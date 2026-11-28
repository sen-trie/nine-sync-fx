"""Explicit scene FX execution with owner-scoped DefaultEffect offscreen passes."""
from dataclasses import dataclass
from fnmatch import fnmatchcase
from math import isfinite
from pathlib import Path

import numpy as np

from ...fx.annotations import fx_annotation
from ...fx.errors import FxUnsupported
from ...common.digest import file_digest
from ...fx.loader import fx_parse_file,fx_parse_file_uncached
from ...fx.techniques import fx_technique_pass_type
from ...fx.textures import FxImage
from .blender_raster import snapshot_geometry,make_state,make_self_shadow_camera,texture_loader
from .compiler import is_mmd_material
from .raster import ColorTarget,DepthTarget,RasterState
from .render_execution import FxRenderExecution


# Native ground shadow blending uses the 8-bit source color and alpha.
GROUND_STATE = RasterState(depth_func='LESS', blend=True, separate_alpha=True, quantize_blend_source=True)
SELF_SHADOW_SIZE = 1024


@dataclass(frozen=True)
class SceneMember:
    obj: object
    name: str
    fx: object = None
    owner: object = None
    main: bool = True
    bindings: object = None

    @property
    def control(self):return self.owner if self.owner is not None else self.obj


def default_effect(program,declaration,member,owner):
    """Resolve the first matching native DefaultEffect rule, with explicit names."""
    text=fx_annotation(declaration,'DefaultEffect',program,'')
    for clause in text.split(';'):
        if not clause.strip():continue
        if '=' not in clause:raise FxUnsupported(f'Invalid DefaultEffect rule {clause!r}.')
        pattern,value=(part.strip() for part in clause.split('=',1))
        match=member.control==owner.control if pattern.lower()=='self' else fnmatchcase(member.name.casefold(),pattern.casefold())
        if not match:continue
        if value.casefold()=='hide':return None
        if value.casefold() in ('none',''):raise FxUnsupported('DefaultEffect native-material drawing is not implemented; supply an explicit FX.')
        path=Path(value.replace('\\','/'))
        path=path if path.is_absolute() else Path(program.folder)/path
        if not path.is_file():raise FxUnsupported(f'DefaultEffect shader is missing: {path}.')
        return path.resolve()
    raise FxUnsupported(f'No DefaultEffect rule matches scene member {member.name!r}.')


class FxSceneExecution:
    """One explicit render-update step, preserving effect resources between calls.

    The registry is the participating MMD scene, not every hidden diagnostic or
    Blender helper. Offscreen rules select its actual geometry and actual owner.
    A caller decides when a render update occurs; timeline replay/feedback policy
    is deliberately not inferred from frame numbers.
    """
    def __init__(self,scene,members,*,units=.08,light=None,ground_shadow_brightness=None,
                 self_shadow_mode=0,self_shadow_distance=8875):
        self.scene=scene;self.members=tuple(members);self.units=units;self.light=light
        if ground_shadow_brightness is not None and (not isfinite(ground_shadow_brightness)
                                                      or not 0 <= ground_shadow_brightness <= 2):
            raise FxUnsupported('Ground shadow brightness must be between 0 and 2.')
        self.ground_shadow_brightness = ground_shadow_brightness
        if self_shadow_mode not in (0, 1, 2):
            raise FxUnsupported('Self shadow mode must be 0, 1 or 2.')
        if not isfinite(self_shadow_distance) or not 0 <= self_shadow_distance <= 10000:
            raise FxUnsupported('Self shadow distance must be between 0 and 10000.')
        self.self_shadow_mode = self_shadow_mode
        self.self_shadow_distance = self_shadow_distance
        self.last_shadow_map = None
        if not self.members:raise FxUnsupported('A scene execution needs actual participating meshes.')
        for member in self.members:
            if member.main and member.fx is None:
                raise FxUnsupported('Main scene members need an explicit FX; mark offscreen-only members with main=False.')
            if member.obj is None or member.obj.type!='MESH' or scene.objects.get(member.obj.name)!=member.obj:
                raise FxUnsupported('Every scene member must be an existing mesh in the source scene.')
            if scene.objects.get(member.control.name)!=member.control:
                raise FxUnsupported('A scene member owner is outside the source scene.')
        for member in self.members:
            for name,source in (member.bindings or {}).items():
                if source is None or scene.objects.get(source.name)!=source:
                    raise FxUnsupported(f'Controller {name!r} is outside the executing scene.')
        self.width=int(scene.render.resolution_x*scene.render.resolution_percentage/100)
        self.height=int(scene.render.resolution_y*scene.render.resolution_percentage/100)
        self.color=ColorTarget(self.width,self.height);self.depth=DepthTarget(self.width,self.height)
        self.executors={};self.programs={};self.last_trace=[];self.last_frame=None

    def _program(self, fx):
        path = Path(fx).resolve(strict=True)
        if path not in self.programs:
            program=fx_parse_file(str(path))
            signature=tuple((name,file_digest(name)) for name in program.files)
            if getattr(program,'_raster_source_signature',None)!=signature:
                program=fx_parse_file_uncached(str(path))
                signature=tuple((name,file_digest(name)) for name in program.files)
            program._raster_source_signature=signature
            self.programs[path]=program
        return self.programs[path]

    def _executor(self,key,fx,width,height):
        if key not in self.executors:
            self.executors[key]=FxRenderExecution(self._program(fx),width,height,texture_loader)
        runtime=self.executors[key]
        if (runtime.width,runtime.height)!=(width,height):raise FxUnsupported('Render dimensions changed; recreate the scene execution.')
        return runtime

    def _geometry_for(self, obj):
        if obj not in self._geometry:
            self._geometry[obj] = snapshot_geometry(self.scene, obj, self.units)
        return self._geometry[obj]

    def _trace_draw(self, key, member, slot, state, runtime, offscreen_owner=None):
        self._trace.append(dict(key=list(key),member=member.name,slot=int(slot),
                                offscreen_owner=offscreen_owner.name if offscreen_owner else None,
                                controls=state.controls,control_bindings=state.control_bindings,passes=runtime.last_stats))

    def _draw(self,key,member,fx,color,depth,offscreen_owner=None,*,ground_coverage=None,
              self_shadow_camera=None,self_shadow_image=None):
        ground_shadow = ground_coverage is not None
        if ground_shadow:
            program = self._program(fx)
            if not any(fx_technique_pass_type(t, program) == 'shadow' for t in program.techniques):
                return
        runtime=self._executor(key,fx,color.width,color.height)
        self._offscreen(key,member,runtime)
        geometry = self._geometry_for(member.obj)
        runtime.targets['']=color;runtime.depths['']=depth
        pass_types = ('shadow',) if ground_shadow else ('object', 'object_ss')
        brightness = self.ground_shadow_brightness if ground_shadow else None
        for slot in sorted(set(geometry.subsets)):
            material = geometry.materials[slot]
            use_shadow_map = self_shadow_image is not None and is_mmd_material(material)
            slot_pass_types = ('object_ss', 'object') if use_shadow_map else pass_types
            if ground_shadow:
                if not is_mmd_material(material) or not material.mmd_material.enabled_drop_shadow:
                    continue
            resolved = make_state(self.scene,member.obj,geometry,int(slot),runtime.program,self.units,
                owner=offscreen_owner,bindings=member.bindings,light=self.light,self_object=member.control,
                pass_types=slot_pass_types,ground_shadow_brightness=brightness,
                self_shadow_camera=self_shadow_camera if use_shadow_map else None,
                self_shadow_image=self_shadow_image if use_shadow_map else None)
            if resolved is None:
                continue
            state, images, technique = resolved
            runtime.execute(geometry.subset(slot),state,subset=int(slot),images=images,
                            initial_state=GROUND_STATE if ground_shadow else RasterState(cull='NONE'),
                            technique=technique,written_pixels=ground_coverage)
            trace_key = ('ground', *key[1:]) if ground_shadow else key
            self._trace_draw(trace_key,member,slot,state,runtime,offscreen_owner)

    def _draw_self_shadow_map(self, main, camera):
        size = SELF_SHADOW_SIZE
        target = ColorTarget(size, size, 'R32F')
        target.clear((1,0,0,1))
        depth = DepthTarget(size, size)
        for key, member in main:
            program = self._program(member.fx)
            if not any(fx_technique_pass_type(t, program) == 'zplot' for t in program.techniques):
                continue
            geometry = self._geometry_for(member.obj)
            runtime = None
            for slot in sorted(set(geometry.subsets)):
                material = geometry.materials[slot]
                if not is_mmd_material(material) or not material.mmd_material.enabled_self_shadow_map:
                    continue
                resolved = make_state(self.scene,member.obj,geometry,int(slot),program,self.units,
                    bindings=member.bindings,light=self.light,self_object=member.control,
                    pass_types=('zplot',),self_shadow_camera=camera)
                if resolved is None:
                    continue
                state, images, technique = resolved
                state.viewport = (size, size)
                if runtime is None:
                    runtime = FxRenderExecution(program,size,size,texture_loader)
                    runtime.targets[''] = target
                    runtime.depths[''] = depth
                initial = RasterState(cull='NONE' if material.mmd_material.is_double_sided else 'CCW')
                runtime.execute(geometry.subset(slot),state,subset=int(slot),images=images,
                                initial_state=initial,technique=technique)
                self._trace_draw(('zplot', *key[1:]),member,slot,state,runtime)
        return FxImage(target.pixels, 'MME self shadow map')

    def _offscreen(self,key,member,runtime):
        if key in self._drawn_targets:return
        cycle=(member.control,Path(runtime.program.folder),str(runtime.program.path) if hasattr(runtime.program,'path') else key[-1])
        if cycle in self._stack:raise FxUnsupported('Recursive offscreen DefaultEffect cycle.')
        self._stack.add(cycle)
        try:
            for name,g in runtime.program.globals.items():
                if g.semantic!='OFFSCREENRENDERTARGET':continue
                target=runtime.targets[name]
                target.clear(fx_annotation(g,'ClearColor',runtime.program,[0,0,0,1]))
                depth=DepthTarget(target.width,target.height)
                depth.clear(float(fx_annotation(g,'ClearDepth',runtime.program,1)))
                for index,source in enumerate(self.members):
                    fx=default_effect(runtime.program,g,source,member)
                    if fx is None:continue
                    child=(*key,'offscreen',name,index,str(fx))
                    self._draw(child,source,fx,target,depth,member.control)
            self._drawn_targets.add(key)
        finally:self._stack.remove(cycle)

    def render(self,frame,*,clear=(0,0,0,1)):
        # Capture old resources by identity because main/offscreen aliases share
        # targets. A failure in a later owner restores every earlier write.
        old_keys=set(self.executors);saved={};depths={}
        old_maps={key:(dict(runtime.targets),dict(runtime.depths)) for key,runtime in self.executors.items()}
        for runtime in self.executors.values():
            for target in runtime.targets.values():saved.setdefault(id(target),(target,target.pixels.copy()))
            for depth in runtime.depths.values():depths.setdefault(id(depth),(depth,depth.values.copy()))
        saved[id(self.color)]=(self.color,self.color.pixels.copy());depths[id(self.depth)]=(self.depth,self.depth.values.copy())
        old_frame=self.scene.frame_current
        self._geometry={};self._trace=[];self._stack=set();self._drawn_targets=set()
        try:
            self.scene.frame_set(frame)
            self.color.clear(clear);self.depth.clear(1)
            main = [(('main', index, str(Path(member.fx).resolve())), member)
                    for index, member in enumerate(self.members) if member.main and member.fx is not None]
            shadow_camera = shadow_image = None
            if self.self_shadow_mode:
                shadow_camera = make_self_shadow_camera(self.scene, self.units, self.light,
                                                        self.self_shadow_mode, self.self_shadow_distance)
                shadow_image = self._draw_self_shadow_map(main, shadow_camera)
            if self.ground_shadow_brightness is not None:
                ground_depth = DepthTarget(self.width, self.height)
                # An overlapping caster must not darken a ground pixel twice.
                ground_coverage = np.zeros((self.height, self.width), dtype=bool)
                for key, member in main:
                    self._draw(key, member, member.fx, self.color, ground_depth, ground_coverage=ground_coverage)
            for key, member in main:
                self._draw(key, member, member.fx, self.color, self.depth,
                           self_shadow_camera=shadow_camera,self_shadow_image=shadow_image)
            self.last_shadow_map = shadow_image.pixels[...,0] if shadow_image is not None else None
            self.last_frame=frame;self.last_trace=self._trace
            return self.color.pixels.copy()
        except Exception:
            for key,(colors,depth_targets) in old_maps.items():
                self.executors[key].targets=colors;self.executors[key].depths=depth_targets
            for target,pixels in saved.values():target.pixels[:]=pixels
            for depth,values in depths.values():depth.values[:]=values
            for key in set(self.executors)-old_keys:del self.executors[key]
            raise
        finally:
            self._geometry={};self.scene.frame_set(old_frame)

    def image(self,name='MME scene execution'):
        """Publish the last successful frame as an owned numeric Blender image."""
        import bpy
        if self.last_frame is None:raise FxUnsupported('Render a successful scene frame before publishing it.')
        image=bpy.data.images.new(name,self.width,self.height,alpha=True,float_buffer=True)
        try:
            image.use_fake_user=True
            image.colorspace_settings.name='Non-Color';image.file_format='OPEN_EXR'
            image.pixels.foreach_set(self.color.pixels[::-1].reshape(-1));image.pack()
            image['ninesync_raster_frame']=self.last_frame
            return image
        except Exception:
            bpy.data.images.remove(image)
            raise
