from .annotations import fx_annotation
from .entry import fx_entry
from .errors import FxUnsupported
from .params import fx_pass_reads, fx_unsourced_reads, fx_vs_globals
from .semantics import FX_MATERIAL_SEMANTICS, fx_classify_global, fx_is_render_target, fx_sampler_slot
from .techniques import (
    fx_draw_kind, fx_pass_script, fx_plan_draws, fx_script_commands,
    fx_technique_pass_type, fx_technique_script, fx_technique_subset,
)

MATERIAL_INPUTS = FX_MATERIAL_SEMANTICS | {"MATERIALTEXTURE", "MATERIALSPHEREMAP", "MATERIALTOONTEXTURE"}
SCREEN_INPUTS = {"SCENECOLOR", "SCENEDEPTH", "SCENENORMAL", "DEPTHBUFFER"}
HISTORY_INPUTS = {"PREVIOUSFRAME", "PREVIOUSFRAMETEXTURE", "HISTORYTEXTURE"}
RENDERER_INPUTS = {"GBUFFER", "SHADOWMAP"}


def default_sampler(program, reads):
    return any(program.globals[n].type.lower().startswith("sampler") and fx_sampler_slot(program, program.globals[n]) is None
               and (program.globals[n].register or "").lower() == "s0" for n in reads)


def pass_info(program, fx_pass, warnings):
    reads = fx_vs_globals(program, [fx_pass], stages=("vs", "ps")) & program.globals.keys()
    for name in sorted(reads):
        g = program.globals[name]
        if not g.type.lower().startswith("sampler"):
            continue
        slot = fx_sampler_slot(program, g)
        if slot in program.globals:
            reads.add(slot)
        elif slot:
            warnings.append(f"{fx_pass.name}: sampler {name} refers to undeclared texture {slot}")
        elif g.register and g.register.lower() != "s0":
            warnings.append(f"{fx_pass.name}: review external sampler binding {name} ({g.register})")
    info = {
        "name": fx_pass.name, "kind": fx_draw_kind(fx_pass, program),
        "commands": list(fx_script_commands(fx_pass_script(fx_pass, program))),
        "states": fx_pass.states, "reads": sorted(reads),
    }
    for stage in ("vs", "ps"):
        entry = getattr(fx_pass, stage)
        function, _ = fx_entry(program, entry)
        info[stage] = entry[2][1] if entry and entry[2][0] == "call" else None
        if entry and function is None:
            warnings.append(f"{fx_pass.name}: unresolved {stage} entry {info[stage]}")
    return info


def inspect_techniques(program, report, unsupported):
    warnings = report["warnings"]
    techniques = report["techniques"]
    all_reads = set()
    loop = post = routed = shadow = False
    for tech in program.techniques:
        passes = [pass_info(program, p, warnings) for p in tech.passes]
        commands = list(fx_script_commands(fx_technique_script(tech, program)))
        mmd_pass = fx_technique_pass_type(tech, program)
        if mmd_pass == "object_ss" and any(default_sampler(program, p["reads"]) for p in passes):
            shadow = True
        techniques.append({"name": tech.name, "mmd_pass": mmd_pass,
                           "subset": fx_technique_subset(tech, program), "commands": commands, "passes": passes})
        all_commands = commands.copy()
        for p in passes:
            all_reads.update(p["reads"])
            post |= p["kind"] == "buffer"
            all_commands.extend(p["commands"])
        for key, value in all_commands:
            if key == "loopbycount":
                loop = True
            elif key in {"rendercolortarget", "rendercolortarget0"} and value:
                routed = True
            elif key == "scriptexternal" or (key.startswith(("rendercolortarget", "renderdepthstenciltarget")) and value):
                routed = True
                unsupported.add(f"{key}={value}")
            elif key == "pass" and value not in {p["name"] for p in passes}:
                warnings.append(f"{tech.name}: script references missing pass {value}")
            elif key == "draw" and value.lower() not in {"buffer", "geometry"}:
                warnings.append(f"{tech.name}: unassessed draw type {value}")
            elif key not in {"pass", "draw", "loopgetindex", "loopend", "clear", "clearsetcolor", "clearsetdepth",
                             "rendercolortarget", "rendercolortarget0", "renderdepthstenciltarget"}:
                warnings.append(f"{tech.name}: unassessed script command {key}={value}")
    return all_reads, loop, post, routed, shadow


def inspect_globals(program, all_reads, report, resource, controller, routing, unsupported, dependencies):
    material = post = routed = False
    for g in program.globals.values():
        semantic = g.semantic
        used = g.name in all_reads
        if semantic:
            report["semantics"][g.name] = semantic
        if g.type.lower().startswith("texture"):
            name = fx_annotation(g, "ResourceName", program)
            if isinstance(name, str) and name:
                resource(name)
        if semantic == "STANDARDSGLOBAL":
            order = str(fx_annotation(g, "ScriptOrder", program, "")).lower()
            post |= order == "postprocess"
        if semantic == "CONTROLOBJECT":
            name = str(fx_annotation(g, "name", program, ""))
            item = str(fx_annotation(g, "item", program, ""))
            controller(g.name, name, item, used)
            if used and name and name.lower() != "(self)":
                dependencies.add("D2")
            elif used and item.lower() == "tr":
                dependencies.add("D1")
        if semantic == "OFFSCREENRENDERTARGET" or (used and fx_is_render_target(g)):
            routed = True
            if not semantic.startswith("RENDERCOLORTARGET"):
                unsupported.add(f"{g.name}: {semantic}")
            rules = fx_annotation(g, "DefaultEffect", program, "")
            for rule in str(rules).split(";"):
                if "=" in rule:
                    selector, effect = rule.split("=", 1)
                    routing(g.name, selector.strip(), effect.strip())
        if not used:
            continue
        material |= semantic in MATERIAL_INPUTS and str(fx_annotation(g, "Object", program, "")).lower() != "light"
        if semantic in {"TIME", "ELAPSEDTIME", "ANIMATEDTEXTURE"}:
            dependencies.add("D1")
        for identifiers, code in ((SCREEN_INPUTS, "D3"), (HISTORY_INPUTS, "D5"), (RENDERER_INPUTS, "D6")):
            if semantic in identifiers:
                dependencies.add(code)
                unsupported.add(f"{g.name}: {semantic}")
        if semantic and not fx_is_render_target(g) and fx_classify_global(g, program) is None:
            if semantic not in MATERIAL_INPUTS | {"ANIMATEDTEXTURE"}:
                report["warnings"].append(f"{g.name}: review semantic {semantic}")
    return material, post, routed


def inspect_program(program, mesh, report, resource, controller, routing, probe):
    dependencies = {"D0"}
    unsupported = set()
    blockers = set()
    targets = set()
    modes = set()
    warnings = report["warnings"]
    reads, loop, post, routed, shadow = inspect_techniques(program, report, unsupported)
    material, post_order, external_target = inspect_globals(
        program, reads, report, resource, controller, routing, unsupported, dependencies)
    subsets = max(1, mesh["subsets"]) if mesh else 1
    draws = fx_plan_draws(program, subsets)
    report["draws"] = [
        {"technique": d.technique.name, "pass": d.fx_pass.name, "subset": d.subset,
         "kind": d.kind, "target": d.target, "loops": d.loops}
        for d in draws
    ]

    if routed or external_target:
        dependencies.add("D4")
    if dependencies & {"D3", "D5", "D6"} or ("D4" in dependencies and unsupported):
        targets.add("SCENE_RENDER")
    if post or post_order:
        dependencies.add("D3")
        targets.add("FULLSCREEN_POST")
        modes.add("A5")
        unsupported.add("full-screen output")
    geometry = [d for d in draws if d.kind == "geometry" and not d.target]
    if loop:
        dependencies.add("D1")
    if shadow:
        dependencies.add("D6")
        warnings.append("Self-shadow technique samples MMD's shadow map (register s0); the object technique is used instead")
    buffer_targets = {d.target for d in draws if d.kind == "buffer" and d.target}
    first_write = {}
    for tech in report["techniques"]:
        current = ""
        for key, value in tech["commands"]:
            if key in {"rendercolortarget", "rendercolortarget0"}:
                current = value
            elif key == "scriptexternal" and current:
                first_write[current] = -1
    for d in draws:
        if d.target:
            first_write.setdefault(d.target, d.order)
    for d in draws:
        if d.kind != "buffer":
            continue
        stale = sorted(n for n in fx_pass_reads(program, d.fx_pass) & buffer_targets if first_write[n] >= d.order)
        if stale:
            dependencies.add("D5")
            unsupported.add(f"{d.label}: reads {', '.join(stale)} before this frame writes it (frame feedback)")
    drawn_targets = {d.target for d in draws if d.kind == "geometry" and d.target}
    for d, name, source in fx_unsourced_reads(program, draws):
        blockers.add(f"{d.label}: geometry reads {name}, computed by a buffer pass" if source == "buffer"
                     else f"{d.label}: output samples scene capture {name}")
    for d in geometry:
        for name in sorted(fx_pass_reads(program, d.fx_pass) & buffer_targets & drawn_targets):
            warnings.append(f"{d.label}: {name} is emulated from its geometry writers without its buffer passes")
    if mesh and geometry and mesh["faces"]:
        modes.add("A2" if any(d.loops for d in geometry) else "A1")
        targets.add("ACCESSORY_GEOMETRY")
        for d in geometry:
            if d.fx_pass.vs is None:
                blockers.add(f"{d.label}: fixed-function geometry pass")
    elif not mesh and material and any(t["mmd_pass"] in {"object", "object_ss"} and
                                       any(p["kind"] == "geometry" for p in t["passes"]) for t in report["techniques"]):
        modes.add("A4")
        targets.add("MODEL_MATERIAL")
        unsupported.add("model material assignment")
    if mesh and mesh["format"] not in {None, "txt"} and geometry:
        blockers.add("binary/compressed .x effect geometry")
    if mesh and mesh["format"] == "txt" and not mesh["faces"] and geometry:
        warnings.append("Geometry pass has no drawable mesh faces")
    if "D4" in dependencies and "SCENE_RENDER" not in targets:
        warnings.append("Color target routing: check matching earlier geometry writers and texture sampling")
        if not geometry:
            targets.add("SCENE_RENDER")
    for name in reads:
        if program.globals[name].semantic == "ANIMATEDTEXTURE":
            unsupported.add(f"{name}: animated texture samples return black")
    unsupported |= blockers
    report.update(dependencies=sorted(dependencies), target_kinds=sorted(targets or {"UNKNOWN"}),
                  package_modes=sorted(modes), unsupported=sorted(unsupported))
    buildable = bool(geometry and mesh and mesh["faces"] and not blockers)
    skipped = [d.label for d in draws if d.kind == "buffer"]
    report["reads_material_texture"] = any(program.globals[n].semantic == "MATERIALTEXTURE" for n in reads)
    local = buildable or "MODEL_MATERIAL" in targets
    report["partial"] = local and bool(targets & {"SCENE_RENDER", "FULLSCREEN_POST"})
    report["skipped_draws"] = skipped
    if report["partial"]:
        warnings.append("Partial: local shading can convert; full-screen and scene passes would be skipped")
    if buildable:
        report["dry_run"] = "draws_planned"
        if probe:
            try:
                probe(program, geometry)
                report["dry_run"] = "sample_evaluated"
            except FxUnsupported as error:
                report["dry_run"] = "failed"
                warnings.append(str(error))
        if any(d["loops"] for d in report["draws"]):
            warnings.append("Looped geometry: measure realized vertex count and playback")
    if targets & {"SCENE_RENDER", "FULLSCREEN_POST"} and not local:
        prediction = "D"
    elif "MODEL_MATERIAL" in targets:
        prediction = "C"
    elif "ACCESSORY_GEOMETRY" in targets:
        prediction = "B" if warnings or unsupported or dependencies - {"D0"} else "A"
    else:
        prediction = None
    report["compatibility_prediction"] = prediction
    report["confidence"] = "low" if not targets else ("medium" if warnings or "MODEL_MATERIAL" in targets else "high")
    report["features"] = {"loopbycount": loop, "buffer_draw": post, "postprocess_order": post_order,
                          "render_targets": routed or external_target, "material_inputs": material}
