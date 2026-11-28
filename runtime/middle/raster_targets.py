import base64
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path

import bpy
import numpy as np

from ...common.digest import file_digest
from ...fx.errors import FxUnsupported
from ...fx.loader import fx_parse_file_uncached
from ...fx.params import fx_vs_globals
from ...fx.semantics import fx_classify_global
from .raster import ColorTarget, DepthTarget
from .scene_execution import FxSceneExecution, SceneMember


RENDER = 'SCENE_RENDER'
CONFIG = 'ninesync_middle_render'
OWNER = 'ninesync_middle_render_owner'


def _member_data(member):
    data = dict(obj=member.obj, name=member.name, main=member.main,
                fx=str(Path(member.fx).resolve()) if member.fx else '',
                bindings=dict(member.bindings or {}))
    if member.owner is not None:
        data['owner'] = member.owner
    return data


def _members(data):
    return tuple(SceneMember(row['obj'], row['name'], row['fx'] or None,
                             row.get('owner'), bool(row['main']), dict(row.get('bindings', {})))
                 for row in (data[key] for key in sorted(data,key=int)))


def _identity(members, units, light):
    return json.dumps(dict(units=units, light=light.name_full if light else None,
        members=[dict(obj=m.obj.name_full, name=m.name, fx=str(m.fx) if m.fx else '',
                      owner=m.control.name_full, main=m.main,
                      bindings={name:obj.name_full for name,obj in (m.bindings or {}).items()})
                 for m in members]), sort_keys=True)


# Every target alias is recorded once, including exact float64 depth values.
def _encode(engine):
    colors = {}; depths = {}; arrays = {}; resources = {}; depth_resources = {}
    def color(target):
        key = colors.setdefault(id(target), 'c'+str(len(colors)))
        if key not in arrays:
            arrays[key] = target.pixels
            resources[key] = dict(width=target.width, height=target.height,
                                  format=target.format, mip_levels=target.mip_levels)
        return key
    def depth(target):
        key = depths.setdefault(id(target), 'd'+str(len(depths)))
        if key not in arrays:
            arrays[key] = target.values
            depth_resources[key] = dict(width=target.width, height=target.height, bits=target.bits)
        return key
    main_color = color(engine.color); main_depth = depth(engine.depth)
    executions = [dict(key=list(key), width=runtime.width, height=runtime.height,
                       colors={name:color(value) for name,value in runtime.targets.items()},
                       depths={name:depth(value) for name,value in runtime.depths.items()},
                       operations=runtime.last_operations, stats=runtime.last_stats)
                  for key,runtime in engine.executors.items()]
    files = {name:file_digest(name) for program in engine.programs.values() for name in program.files}
    metadata = dict(version=1, width=engine.width, height=engine.height,
                    color=main_color, depth=main_depth, colors=resources, depths=depth_resources,
                    executions=executions, files=files, frame=engine.last_frame, trace=engine.last_trace)
    arrays['metadata'] = np.frombuffer(json.dumps(metadata).encode('utf-8'), dtype=np.uint8)
    stream = BytesIO(); np.savez_compressed(stream, **arrays)
    return base64.encodebytes(stream.getvalue()).decode('ascii')


def _restore(engine, text):
    with np.load(BytesIO(base64.b64decode(''.join(text.as_string().split()), validate=True)), allow_pickle=False) as saved:
        metadata = json.loads(saved['metadata'].tobytes())
        if (metadata['version'], metadata['width'], metadata['height']) != (1, engine.width, engine.height):
            return False
        if any(not Path(name).is_file() or file_digest(name)!=signature for name,signature in metadata['files'].items()):
            return False
        colors = {}; depths = {}
        for key,settings in metadata['colors'].items():
            colors[key] = ColorTarget(**settings)
            colors[key].pixels[:] = saved[key]
        for key,settings in metadata['depths'].items():
            depths[key] = DepthTarget(**settings)
            depths[key].values[:] = saved[key]
        engine.color = colors[metadata['color']]; engine.depth = depths[metadata['depth']]
        for row in metadata['executions']:
            key = tuple(row['key'])
            runtime = engine._executor(key, key[-1], row['width'], row['height'])
            runtime.targets = {name:colors[value] for name,value in row['colors'].items()}
            runtime.depths = {name:depths[value] for name,value in row['depths'].items()}
            runtime.last_operations = row['operations']; runtime.last_stats = row['stats']
        engine.last_frame = metadata['frame']; engine.last_trace = metadata['trace']
    return True


def _discard(block, collection, obj):
    if block is not None and block.get(OWNER)==obj:
        block.use_fake_user = False
        if not block.users:
            collection.remove(block)


def apply_render(obj, path, *, scene, units, light=None, members=None, owner=None, bindings=None,
                 force_rebuild=False, reset_targets=None, ground_shadows=None, ground_shadow_brightness=None,
                 self_shadow_mode=None, self_shadow_distance=None):
    from .targets import Result
    previous = obj.get(CONFIG)
    if reset_targets is None:
        reset_targets = bool(previous.get('reset_targets', False)) if previous else False
    if ground_shadows is None:
        ground_shadows = bool(previous.get('ground_shadows', False)) if previous else False
    if ground_shadow_brightness is None:
        ground_shadow_brightness = float(previous.get('ground_shadow_brightness', 1.0)) if previous else 1.0
    if self_shadow_mode is None:
        self_shadow_mode = int(previous.get('self_shadow_mode', 0)) if previous else 0
    if self_shadow_distance is None:
        self_shadow_distance = float(previous.get('self_shadow_distance', 8875)) if previous else 8875
    old_image = previous.get('image') if previous else None
    old_state = previous.get('state') if previous else None
    path = Path(path).resolve(strict=True)
    if members is None:
        if previous:
            members = list(_members(previous['members']))
            members = [replace(m, fx=path, owner=owner if owner is not None else m.owner,
                               bindings=bindings if bindings is not None else m.bindings) if m.obj==obj else m for m in members]
        else:
            members = [SceneMember(obj, path.with_suffix('.x').name if path.with_suffix('.x').is_file() else obj.name,
                                   path, owner, True, bindings)]
    members = tuple(members)
    selected = [m for m in members if m.obj==obj and m.main]
    if len(selected)!=1 or not selected[0].fx or Path(selected[0].fx).resolve()!=path:
        raise FxUnsupported('The render assignment must include the selected mesh once with this FX as a main draw.')
    engine = FxSceneExecution(scene, members, units=units, light=light,
                              ground_shadow_brightness=ground_shadow_brightness if ground_shadows else None,
                              self_shadow_mode=self_shadow_mode,self_shadow_distance=self_shadow_distance)
    identity = _identity(members, units, light)
    if not reset_targets and not force_rebuild and previous and previous.get('scene')==scene and previous['identity']==identity:
        state = previous.get('state')
        if state is not None and state.get(OWNER)==obj:
            _restore(engine, state)
    engine.render(scene.frame_current)
    image = None; state = None
    try:
        image = engine.image('MME rendered scene | '+obj.name)
        image[OWNER] = obj
        state = bpy.data.texts.new('MME render state | '+obj.name)
        state[OWNER] = obj
        state.from_string(_encode(engine))
        data = dict(scene=scene, image=image, state=state, units=units, identity=identity,
                    reset_targets=reset_targets, ground_shadows=ground_shadows,
                    ground_shadow_brightness=ground_shadow_brightness, self_shadow_mode=self_shadow_mode,
                    self_shadow_distance=self_shadow_distance,
                    members={str(i):_member_data(m) for i,m in enumerate(members)})
        if light is not None:
            data['light'] = light
        obj[CONFIG] = data
    except Exception:
        _discard(image, bpy.data.images, obj); _discard(state, bpy.data.texts, obj)
        raise
    if previous:
        _discard(old_image, bpy.data.images, obj)
        _discard(old_state, bpy.data.texts, obj)
    update = ('Rendered one explicit scene update with fresh targets.' if reset_targets else
              'Rendered one explicit scene update with saved target history when available.')
    return Result(image, engine, 'EXECUTED', (update,), True)


def remove_render(obj):
    previous = obj.get(CONFIG)
    if previous is None:
        raise FxUnsupported('The selected mesh has no owned rendered scene.')
    image = previous.get('image'); state = previous.get('state')
    del obj[CONFIG]
    _discard(image, bpy.data.images, obj); _discard(state, bpy.data.texts, obj)


def retarget_render(obj, name, replacement, scene):
    previous = obj.get(CONFIG)
    if previous is None or replacement is None or scene.objects.get(replacement.name)!=replacement:
        raise FxUnsupported('Choose an existing scene object for the rendered scene controller.')
    paths={m.fx for m in _members(previous['members']) if m.fx}
    state=previous.get('state')
    if state is not None:
        with np.load(BytesIO(base64.b64decode(''.join(state.as_string().split()),validate=True)),allow_pickle=False) as saved:
            metadata=json.loads(saved['metadata'].tobytes())
            paths.update(row['key'][-1] for row in metadata['executions'])
    found=False
    for path in paths:
        program=fx_parse_file_uncached(str(path))
        used=fx_vs_globals(program,stages=('vs','ps'))
        for g in program.globals.values():
            source=fx_classify_global(g,program)
            if g.name in used and source and source[0]=='control' and source[1]==name:
                found=True
    if not found or name.startswith('('):
        raise FxUnsupported(f'No retargetable executed shader input reads controller {name!r}.')
    members = tuple(replace(m, bindings={**(m.bindings or {}), name:replacement}) for m in _members(previous['members']))
    own = next(m for m in members if m.obj==obj and m.main)
    return apply_render(obj, own.fx, scene=scene, units=previous['units'], light=previous.get('light'), members=members)
