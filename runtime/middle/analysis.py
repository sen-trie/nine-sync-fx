"""Read-only routing for the experimental existing-model profile."""

from dataclasses import dataclass
from copy import copy
from pathlib import Path

from ...common.digest import digest, file_digest
from ...fx.annotations import fx_annotation
from ...fx.entry import fx_entry, fx_norm_semantic
from ...fx.errors import FxUnsupported
from ...fx.loader import fx_parse_file, fx_parse_file_uncached
from ...fx.params import fx_vs_globals
from ...fx.semantics import fx_classify_global, fx_is_render_target
from ...fx.techniques import (
    fx_pass_script,
    fx_pick_technique,
    fx_plan_draws,
    fx_technique_pass_type,
    fx_technique_script,
    fx_technique_subset,
    fx_subset_matches,
)

MATERIAL = "MODEL_MATERIAL"
DEFORM = "MESH_DEFORM"
RENDER = "SCENE_RENDER"


@dataclass(frozen=True)
class Analysis:
    path: Path
    program: object
    signature: str
    technique: object
    shader_pass: object
    targets: tuple
    controller_refs: tuple
    local_position: str | None
    problems: tuple
    texture_writers: tuple = ()
    loops: tuple = ()


def _commands(script):
    return [part.strip().lower().replace(" ", "") for part in (script or "").split(";") if part.strip()]


def analyze(path, subset=0, material_flags=None, *, pass_type=None, pass_context=None, destination=None):
    if destination not in (None, MATERIAL, DEFORM):
        raise FxUnsupported(f"Unsupported node destination: {destination!r}.")
    vertex_only = destination == DEFORM
    if pass_type not in (None, "object", "object_ss"):
        raise FxUnsupported(f"Unsupported existing-model pass selection: {pass_type!r}.")
    roles = (pass_type,) if pass_type else ("object", "object_ss")
    path = Path(path).resolve(strict=True)
    if path.suffix.lower() != ".fx":
        raise FxUnsupported("Select an .fx source file.")
    program = fx_parse_file(str(path))
    files = tuple((name, file_digest(name)) for name in program.files)
    signature = digest(files)
    # The shared loader uses file stamps. Also check bytes before reusing an experimental conversion.
    if getattr(program, "_middle_source_signature", signature) != signature:
        program = fx_parse_file_uncached(str(path))
        signature = digest(tuple((name, file_digest(name)) for name in program.files))
    program._middle_source_signature = signature
    technique = fx_pick_technique(program, subset, pass_types=roles)
    if material_flags is not None:
        technique = next((candidate for role in roles for candidate in program.techniques
                          if fx_technique_pass_type(candidate, program) == role
                          and fx_subset_matches(fx_technique_subset(candidate, program), subset)
                          and all(fx_annotation(candidate, name, program, value) == value
                                  for name, value in material_flags.items())), None)
    shader_pass = None
    writers, loops = [], ()
    targets, problems = [], []
    if path.with_suffix(".x").is_file() and any(draw.kind == "geometry" for draw in fx_plan_draws(program, subset + 1)):
        targets.append("ACCESSORY")
    if technique is None:
        problems.append(f"No object technique for subset {subset}.")
    else:
        selected_role = fx_technique_pass_type(technique, program)
        if selected_role == "object_ss" and roles != ("object_ss",):
            problems.append("object_ss requires an unverified self-shadow profile.")
        selected = copy(program)
        selected.techniques = [technique]
        draws = [draw for draw in fx_plan_draws(selected, subset + 1) if draw.subset == subset]
        screen = bool(pass_context and pass_context.screen)
        surface = [draw for draw in draws if draw.kind == ("buffer" if screen else "geometry") and not draw.target]
        if len(surface) != 1 or any(draw.loops for draw in draws if draw.kind != "geometry" or draw.target):
            problems.append("Existing-model conversion requires one object draw; buffer/target instance loops need renderer integration.")
        else:
            shader_pass = surface[0].fx_pass
            loops = tuple(surface[0].loops)
            writers = [(draw.target, draw.fx_pass) for draw in draws
                       if draw.kind == "buffer" and draw.target and draw.order < surface[0].order]
            if any(draw.order > surface[0].order or (draw.kind == "buffer" and not draw.target and draw != surface[0])
                   for draw in draws):
                problems.append("Additional screen draws require a render destination.")
        commands = _commands(fx_technique_script(technique, program))
        supported = ("pass=", "rendercolortarget0=", "rendercolortarget=", "renderdepthstenciltarget=",
                     "clearsetcolor=", "clearsetdepth=", "clear=", "loopbycount=", "loopgetindex=", "loopend=")
        if any(not cmd.startswith(supported) for cmd in commands):
            problems.append("Unsupported technique script: " + ", ".join(commands))
        # The shared draw planner does not model clears. Only pre-write clears are safe
        # when replacing full-screen render targets with shader functions.
        target, written = "", set()
        pass_names = {p.name.lower() for p in technique.passes}
        for command in commands:
            key, _, value = command.partition("=")
            if key in ("rendercolortarget", "rendercolortarget0"):
                target = value
            elif key == "pass":
                if value not in pass_names:
                    problems.append("Technique script references an unknown pass: " + value)
                if target:
                    written.add(target)
            elif key == "clear" and value == "color" and target in written:
                problems.append("Clearing a written render target requires temporal pass evaluation.")
        if any(draw.kind == "geometry" and draw.target for draw in draws):
            problems.append("Geometry-rendered targets require a scene render destination.")
        prior_targets = set()
        for name, writer in writers:
            reads = fx_vs_globals(program, [writer], stages=("vs", "ps"))
            unavailable = {g.name for g in program.globals.values()
                           if fx_is_render_target(g) and g.name in reads} - prior_targets
            if unavailable:
                problems.append("Buffer pass needs prior render targets: " + ", ".join(sorted(unavailable)))
            prior_targets.add(name)
        if len(writers) != len({name for name, _writer in writers}):
            problems.append("Repeated render-target writes require temporal pass evaluation.")
        for _name, writer in writers:
            if _commands(fx_pass_script(writer, program)) != ["draw=buffer"]:
                problems.append(f"Buffer pass {writer.name} has an unsupported pass script.")
            if any(key not in ("alphablendenable", "alphatestenable", "zenable", "zwriteenable")
                   or str(value).strip().lower() not in ("false", "0")
                   for key, value in writer.states.items()):
                problems.append(f"Buffer pass {writer.name} has raster state that requires a rendered target.")
        if shader_pass:
            commands = _commands(fx_pass_script(shader_pass, program))
            if commands and commands != ["draw=buffer" if screen else "draw=geometry"]:
                problems.append("Unsupported pass script: " + ", ".join(commands))
            screen_states = {"zenable": {"false", "0"}, "zwriteenable": {"false", "0"},
                             "alphablendenable": {"false", "0"}, "srcblend": {"one"}, "destblend": {"zero"}}
            if screen and any(str(shader_pass.states.get(key, "")).strip().lower() not in ("false", "0")
                              for key in ("zenable", "zwriteenable", "alphablendenable")):
                problems.append("The fullscreen subset requires explicit disabled depth test/write and blending.")
            if not vertex_only and shader_pass.states and not (screen and all(
                    key in screen_states and str(value).strip().lower() in screen_states[key]
                    for key, value in shader_pass.states.items())):
                problems.append("Unsupported pass states: " + ", ".join(sorted(shader_pass.states)))
    selected_passes = [shader_pass] if shader_pass else []
    stages = ("vs",) if vertex_only else ("vs", "ps")
    used = fx_vs_globals(program, selected_passes, stages=stages)
    targets_read = {g.name for g in program.globals.values() if fx_is_render_target(g) and g.name in used}
    unknown_targets = targets_read - {name for name, _writer in writers} - set(pass_context.images if pass_context else ())
    if unknown_targets:
        targets.append("SCREEN")
        problems.append("Screen/render-target dependencies: " + ", ".join(sorted(unknown_targets)))
    if not shader_pass and any("draw=buffer" in _commands(fx_pass_script(p, program))
                               for tech in program.techniques for p in tech.passes):
        targets.append("SCREEN")
        problems.append("Screen/render-target dependencies require an object draw destination.")
    if any(fx_annotation(g, "ScriptExternal", program) is not None
           or "scriptexternal" in str(fx_annotation(g, "Script", program, "")).lower()
           for g in program.globals.values()):
        problems.append("ScriptExternal requires a scene render destination.")
    refs = []
    passes = ([writer for _name, writer in writers] + [shader_pass] if shader_pass
              else [p for tech in program.techniques for p in tech.passes])
    names = fx_vs_globals(program, passes, stages=stages, global_names=(item[2] for item in loops))
    for g in program.globals.values():
        if g.name in names:
            source = fx_classify_global(g, program)
            if source and source[0] == "control":
                refs.append((g.name, source[1], source[2], g.type))
    if refs:
        targets.append("CONTROLLER")
    local_position = None
    if technique and shader_pass and not problems:
        vs, _ = fx_entry(program, shader_pass.vs)
        ps, _ = fx_entry(program, shader_pass.ps)
        if ps and not vertex_only:
            targets.append(MATERIAL)
        local = fx_annotation(technique, "NineSyncLocalPosition", program)
        if vs and fx_technique_subset(technique, program) is None:
            local_position = fx_norm_semantic(local) if isinstance(local, str) else None
            targets.append(DEFORM)
    if technique is not None and not vertex_only:
        from ...fx.execution_plan import fx_plan_operations
        from .raster import RasterState
        try:
            state=RasterState()
            for operation in fx_plan_operations(program,subset,technique=technique):
                if operation.kind=='states':state=state.apply(operation.value)
            targets.append(RENDER)
        except FxUnsupported as error:
            problems.append(str(error))
    return Analysis(path, program, signature, technique, shader_pass, tuple(targets), tuple(refs),
                    local_position, tuple(problems), tuple(writers), loops)
