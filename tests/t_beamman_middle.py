"""Inspect and exercise local BeamMan FX against existing-model destinations."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("NINESYNC_FX", str(Path(__file__).resolve().parents[1]))
import paths
paths.prepare("ninesync_fx")

from ninesync_fx.fx.loader import fx_parse_file
from ninesync_fx.fx.annotations import fx_annotation
from ninesync_fx.fx.semantics import fx_classify_global, fx_is_render_target
from ninesync_fx.fx.techniques import fx_technique_pass_type, fx_pass_script
from ninesync_fx.runtime.middle.analysis import analyze
from ninesync_fx.runtime.middle.targets import MATERIAL, DEFORM, apply, remove
from ninesync_fx.runtime.middle.controllers import resolve
from ninesync_fx.fx.types import fx_resolve_type, fx_count, fx_is_numeric


def inspect(path, root):
    raw = path.read_bytes()
    entry = {"path": str(path), "relative": path.relative_to(root).as_posix(),
             "sha256": sha256(raw).hexdigest(), "categories": []}
    # Preserve failed parser candidates for the actual importer trial.
    text = raw.decode("utf-8-sig", errors="replace")
    entry["paired_x"] = str(path.with_suffix(".x")) if path.with_suffix(".x").is_file() else None
    try:
        program = fx_parse_file(str(path))
        entry["techniques"] = [dict(name=t.name, role=fx_technique_pass_type(t, program),
                                   passes=[p.name for p in t.passes]) for t in program.techniques]
        controls = []
        for g in program.globals.values():
            source = fx_classify_global(g, program)
            if source and source[0] == "control":
                controls.append(dict(parameter=g.name, name=source[1], item=source[2], type=g.type))
        entry["controls"] = controls
        entry["render_targets"] = [g.name for g in program.globals.values() if fx_is_render_target(g)]
        object_techs = [t for t in program.techniques if fx_technique_pass_type(t, program) in ("object", "object_ss")]
        geometry_draw = any("draw=buffer" not in (fx_pass_script(p, program) or "").lower().replace(" ","")
                            for t in object_techs for p in t.passes)
        if geometry_draw and not entry["paired_x"]:
            entry["categories"].extend(["MATERIAL", "GEOMETRY"])
        if controls:
            entry["categories"].append("CONTROLLER")
        if entry["paired_x"]:
            entry["accessory"] = True
    except Exception as error:
        entry["parse_error"] = str(error)
        if re.search(r"CONTROLOBJECT", text, re.I):
            entry["categories"].append("CONTROLLER")
        if not entry["paired_x"] and re.search(r'MMDPass\s*=\s*"object', text, re.I):
            entry["categories"].extend(["MATERIAL", "GEOMETRY"])
        if not entry["categories"]:
            entry["categories"].append("GEOMETRY")
            entry["qualification"] = "Conservative vertex-effect candidate. Parsing failed before target analysis."
    return entry


def run_case(args):
    entry = json.loads(args.entry.read_text(encoding="utf-8"))
    from t_middle_pmx import load_importer, model_object, resolve_textures, snapshot, position_camera
    from t_middle_converters import set_scene, add_empty
    pmx, importer = load_importer(args.mmd_tools)
    scene = set_scene()
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = scene.render.resolution_y = 256
    model_file = args.model
    if args.prefer_companion:
        companion = next((Path(entry["path"]).with_suffix(extension) for extension in (".pmx", ".pmd")
                          if Path(entry["path"]).with_suffix(extension).is_file()), None)
        model_file = companion or model_file
    model_hash = sha256(model_file.read_bytes()).hexdigest()
    if model_file.suffix.lower() == ".pmd":
        from mmd_tools.core.pmd.importer import import_pmd_to_pmx
        model = import_pmd_to_pmx(str(model_file))
    else:
        model = pmx.load(str(model_file))
    resolve_textures(model, model_file, model_file.parent / "textures")
    importer().execute(pmx=model, scale=0.08, types={"MESH", "ARMATURE", "MORPHS"},
                       fix_bone_order=False)
    obj = model_object(scene, model)
    position_camera(scene, obj)
    light = add_empty(scene, "BeamMan audit light", (-0.65, -1, 0.85))
    bindings = {model_file.name: obj}
    controller_imports = []
    package_root = args.root if args.root else Path(entry["path"]).parents[0]
    for name in dict.fromkeys(c["name"] for c in entry.get("controls", [])):
        if not name or name.startswith("(") or name in bindings:
            continue
        local = Path(entry["path"]).parent / name
        matches = ([local] if args.prefer_local_controller and local.is_file() else
                   [p for p in package_root.rglob("*") if p.is_file()
                    and p.suffix.lower() in (".pmx", ".pmd", ".x") and p.name.lower() == name.lower()])
        if len(matches) != 1:
            continue
        try:
            from mmd_tools.core.pmd.importer import import_pmd_to_pmx
            before_ids = set(scene.objects)
            if matches[0].suffix.lower() == ".x":
                from harness_fx import mb
                from ninesync_fx.accessories.standalone_import import import_plain_accessory
                root = import_plain_accessory(bpy.context, str(matches[0]), 0.08)
                root["ninesync_mme_name"] = name
                bindings[name] = root
            elif matches[0].suffix.lower() == ".pmd":
                ctrl_model = import_pmd_to_pmx(str(matches[0]))
                importer().execute(pmx=ctrl_model, scale=0.08, types={"MESH","ARMATURE","MORPHS"},
                                   fix_bone_order=False)
            else:
                importer().execute(filepath=str(matches[0]), scale=0.08,
                                   types={"MESH","ARMATURE","MORPHS"}, fix_bone_order=False)
            new_meshes = [o for o in scene.objects if o not in before_ids and o.type == "MESH"]
            if len(new_meshes) == 1 and name not in bindings:
                bindings[name] = new_meshes[0]
            controller_imports.append({"name":name, "path":str(matches[0]),
                                       "sha256":sha256(matches[0].read_bytes()).hexdigest(),
                                       "status":"IMPORTED" if name in bindings else "AMBIGUOUS_MESH"})
        except Exception as error:
            controller_imports.append({"name":name,"path":str(matches[0]),"status":"IMPORT_FAILED",
                                       "error":str(error)})
    initial = snapshot(obj)
    materials = tuple(s.material for s in obj.material_slots)
    counts = (len(bpy.data.materials), len(bpy.data.node_groups), len(bpy.data.meshes))
    result = dict(entry)
    result["source_model"] = str(model_file)
    result["source_model_sha256"] = model_hash
    outcomes = {}
    path = Path(entry["path"])
    trial_slot = 1 if len(obj.material_slots) > 1 else 0
    for key,target in (("MATERIAL", MATERIAL), ("GEOMETRY", DEFORM)):
        if key not in entry["categories"]:
            continue
        try:
            converted = apply(obj, path, target, slot=trial_slot, light=light, units=0.08, bindings=bindings)
            deps = bpy.context.evaluated_depsgraph_get()
            ev = obj.evaluated_get(deps)
            mesh = ev.to_mesh()
            assert len(mesh.vertices) >= len(obj.data.vertices)
            ev.to_mesh_clear()
            if key == "MATERIAL":
                scene.render.image_settings.file_format = "PNG"
                scene.render.filepath = str(args.output.with_suffix(".png"))
                bpy.ops.render.render(write_still=True)
            outcomes[key] = {"status":converted.status, "warnings":converted.warnings}
            remove(obj, target, slot=trial_slot)
        except Exception as error:
            outcomes[key] = {"status":"REJECTED", "error":str(error), "exception":type(error).__name__}
    if "CONTROLLER" in entry["categories"]:
        checks = []
        try:
            program = fx_parse_file(str(path))
            for control in entry.get("controls", []):
                t = fx_resolve_type(control["type"], program)
                try:
                    if not fx_is_numeric(t):
                        raise ValueError("Aggregate control is unavailable in the binding profile.")
                    target, sources = resolve(scene,obj,control["name"],control["item"],
                                              fx_count(t[1]),bindings,0.08,base=t[0],dims=t[1])
                    checks.append(dict(control,status="RESOLVED" if target else "OPTIONAL_ABSENT",
                                       object=target.name if target else None))
                except Exception as error:
                    checks.append(dict(control,status="UNRESOLVED",error=str(error)))
            outcomes["CONTROLLER"] = checks
        except Exception as error:
            outcomes["CONTROLLER"] = {"status":"PARSE_FAILED","error":str(error)}
    assert snapshot(obj) == initial, "Original PMX data changed"
    assert tuple(s.material for s in obj.material_slots) == materials, "Slot restoration failed"
    result["controller_imports"] = controller_imports
    result["outcomes"] = outcomes
    result["pmx_preserved"] = True
    result["datablocks_before"] = counts
    result["datablocks_after"] = (len(bpy.data.materials),len(bpy.data.node_groups),len(bpy.data.meshes))
    result["shader_unchanged"] = sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    assert result["shader_unchanged"]
    result["model_unchanged"] = sha256(model_file.read_bytes()).hexdigest() == model_hash
    assert result["model_unchanged"]
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("BEAMMAN_CASE",entry["relative"],json.dumps(outcomes,ensure_ascii=True),flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path)
    parser.add_argument("--entry",type=Path)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--model",type=Path)
    parser.add_argument("--mmd-tools",type=Path)
    parser.add_argument("--prefer-companion",action="store_true")
    parser.add_argument("--prefer-local-controller",action="store_true")
    args=parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    if args.root and not args.entry:
        entries=[inspect(p,args.root) for p in sorted(args.root.rglob("*")) if p.suffix.lower()==".fx"]
        args.output.write_text(json.dumps(entries,ensure_ascii=False,indent=2),encoding="utf-8")
        print("SCANNED",len(entries),"QUALIFIED",sum(bool(e["categories"]) for e in entries),flush=True)
    else:
        run_case(args)

if __name__=="__main__":
    main()
