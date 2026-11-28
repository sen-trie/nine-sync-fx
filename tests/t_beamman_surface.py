"""Render real BeamMan surface shaders on an imported PMX and check their lifecycle."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer, model_object, resolve_textures, snapshot, position_camera, render_image
from t_middle_converters import set_scene, add_empty
from ninesync_fx.runtime.middle.targets import apply, remove, MATERIAL


def check_material_power(scene, obj, output):
    """Compare the real PMX against the observed native MMD diagnostic RGB."""
    output.mkdir(parents=True, exist_ok=True)
    original = obj.material_slots[1].material
    assert original.mmd_material.shininess == 0.0
    path = Path(__file__).parent / "fixtures" / "middle" / "material_power.fx"
    result = apply(obj, path, MATERIAL, slot=1, units=0.08)
    try:
        first = render_image(scene, output, "native-power-reference", 1)
        rgb = np.clip(first[:, :, :3], 0.0, 1.0)
        encoded = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * rgb ** (1.0 / 2.4) - 0.055)
        pixels = np.rint(encoded * 255).astype(np.int16)
        # MMD 9.32/ps_3_0 returned (2,207,0) over 16,343 skin pixels.
        native = np.array((2, 207, 0))
        matched = int(((np.abs(pixels - native).max(axis=2) <= 1) & (first[:, :, 3] > 0.99)).sum())
        assert matched > 1000, matched
        original.mmd_material.shininess = 0.2
        original.update_tag()
        changed = render_image(scene, output, "power-point-two", 2)
        assert float(np.abs(first-changed).mean()) > 0.001
        warm = apply(obj, path, MATERIAL, slot=1, units=0.08)
        assert not warm.rebuilt and warm.core == result.core
        original.mmd_material.shininess = 0.0
        original.update_tag()
        restored = render_image(scene, output, "power-restored", 1)
        assert float(np.abs(first-restored).mean()) < 1e-5
        (output / "material-power.json").write_text(json.dumps({
            "original_shininess": 0.0, "native_reference_rgb": native.tolist(),
            "converted_pixels_within_one_level": matched,
            "live_change_verified": True, "unchanged_reimport_reuses_core": True,
            "restored_mean_error": float(np.abs(first-restored).mean()),
        }, indent=2), encoding="utf-8")
        print("PASS NATIVE_MATERIAL_POWER", matched, flush=True)
    finally:
        original.mmd_material.shininess = 0.0
        remove(obj, MATERIAL, slot=1)
    return int((first[:, :, 3] > 0.9).sum())


def run(args):
    pmx, importer = load_importer(args.mmd_tools)
    if args.reopen:
        results = json.loads((args.output / "results.json").read_text(encoding="utf-8"))
        for result in results:
            path = Path(result["blend"])
            bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
            scene = bpy.context.scene
            reference = bpy.data.images.load(str(path.parent / "rebuilt.exr"), check_existing=False)
            pixels = np.array(reference.pixels[:], dtype=np.float32).reshape(512, 512, 4)
            bpy.data.images.remove(reference)
            image = render_image(scene, path.parent, "reopened", scene.frame_current)
            error = float(np.abs(image - pixels).mean())
            assert error < 1e-5, (result["file"], error)
            result["reopen_mean_error"] = error
            print("PASS REOPEN", result["file"], error, flush=True)
        (args.output / "results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
        return
    scene = set_scene()
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = scene.render.resolution_y = 512
    scene.render.film_transparent = True
    model = pmx.load(str(args.model))
    resolve_textures(model, args.model, args.model.parent / "textures")
    importer().execute(pmx=model, scale=0.08, types={"MESH", "ARMATURE", "MORPHS"}, fix_bone_order=False)
    obj = model_object(scene, model)
    position_camera(scene, obj)
    light = add_empty(scene, "Light direction", (-0.65, -1, 0.85))
    initial = snapshot(obj)
    slots = tuple(s.material for s in obj.material_slots)
    selected = tuple(index for index, slot in enumerate(obj.material_slots)
                     if slot.material and slot.material.mmd_material.alpha > 0.0)
    opaque_reference = check_material_power(scene, obj, args.output / "material-power")
    names = {"NewToon", "NewToon2", "full_NoneTexFilter", "full_SimpleSoftShadow", "full_BeamManCustom"}
    files = [p for p in sorted(args.root.rglob("*.fx")) if p.stem in names]
    assert len(files) == 6
    results = []
    for index, path in enumerate(files):
        out = args.output / f"{index:02}-{path.stem}"
        out.mkdir(parents=True, exist_ok=True)
        raw_hash = sha256(path.read_bytes()).hexdigest()
        built = []
        try:
            cores = []
            for slot in selected:
                result = apply(obj, path, MATERIAL, slot=slot, units=0.08, light=light)
                built.append(slot)
                cores.append(result)
            original_nodes = len(bpy.data.node_groups)
            for slot, result in zip(selected, cores):
                warm = apply(obj, path, MATERIAL, slot=slot, units=0.08, light=light)
                assert not warm.rebuilt and warm.core == result.core
            assert snapshot(obj) == initial
            first = render_image(scene, out, "light-left", 1)
            assert int((first[:, :, 3] > 0.9).sum()) > opaque_reference * 0.9
            light.location.x = 1.1
            obj.location.x = 0.04
            obj.rotation_euler.z = 0.15
            light.update_tag()
            changed = render_image(scene, out, "light-right", 2)
            world_inputs = 0
            for converted in cores:
                vertex = converted.core.get("ninesync_middle_vertex_core")
                for core in [converted.core] + ([vertex] if vertex else []):
                    if core.animation_data:
                        for curve in core.animation_data.drivers:
                            assert curve.is_valid, curve.data_path
                    evaluated_core = core.evaluated_get(bpy.context.evaluated_depsgraph_get())
                    for node in evaluated_core.nodes:
                        prefix = obj.name + " world "
                        if node.label.startswith(prefix):
                            row, col = map(int, node.label[len(prefix):].split(","))
                            axis = (0, 2, 1)
                            value = obj.matrix_world[axis[col]][3 if row == 3 else axis[row]]
                            assert abs(node.outputs[0].default_value - value) < 1e-5, (node.label, node.outputs[0].default_value, value)
                            world_inputs += 1
            assert world_inputs > 0
            difference = float(np.abs(first-changed).mean())
            assert difference > 0.0001, difference
            for slot in selected:
                apply(obj, path, MATERIAL, slot=slot, units=0.08, light=light, force_rebuild=True)
            assert len(bpy.data.node_groups) == original_nodes
            rebuilt = render_image(scene, out, "rebuilt", 2)
            assert float(np.abs(changed-rebuilt).mean()) < 0.00001
            bpy.context.preferences.filepaths.save_version = 0
            bpy.ops.wm.save_as_mainfile(filepath=str(out / "surface.blend"), relative_remap=False)
            results.append({"file":str(path), "sha256":raw_hash, "status":"PARTIAL",
                            "slots":list(selected), "light_mean_difference":difference,
                            "warnings":sorted({warning for core in cores for warning in core.warnings}),
                            "blend":str(out / "surface.blend")})
            print("PASS REAL_SURFACE",path, difference,flush=True)
        finally:
            for slot in built:
                remove(obj, MATERIAL, slot=slot)
            light.location.x = -0.65
            obj.location.x = 0.0
            obj.rotation_euler.z = 0.0
            light.update_tag()
            assert snapshot(obj) == initial
            assert tuple(s.material for s in obj.material_slots) == slots
            assert sha256(path.read_bytes()).hexdigest() == raw_hash
        (args.output / "results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reopen",action="store_true")
    parser.add_argument("--root",required=True,type=Path)
    parser.add_argument("--model",required=True,type=Path)
    parser.add_argument("--mmd-tools",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
