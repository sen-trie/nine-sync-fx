"""Verify vertex interpolation, animation and lifecycle on the supplied real PMX."""
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
from ninesync_fx.runtime.middle.targets import apply, remove, retarget, MATERIAL, DEFORM
from ninesync_fx.runtime.middle.vertex import VERTEX_CORE, stage, locate

FX = Path(__file__).parent / "fixtures" / "middle" / "vertex_interpolation.fx"


def field(mesh, core, descriptor):
    name, channel = json.loads(core["ninesync_middle_vertex_map"])[descriptor]
    attribute = mesh.attributes[name]
    assert attribute.domain == "CORNER" and attribute.data_type == "FLOAT_COLOR"
    values = np.empty(len(attribute.data) * 4, dtype=np.float32)
    attribute.data.foreach_get("color", values)
    return values.reshape(-1, 4)[:, channel]


def check(scene, obj, core, frame, gain, shift, light, slot=1):
    scene.frame_set(frame)
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        indices = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", indices)
        selected = np.zeros(len(mesh.loops), dtype=bool)
        for poly in mesh.polygons:
            if poly.material_index == slot:
                selected[poly.loop_start:poly.loop_start+poly.loop_total] = True
        uv = np.array([loop.uv[:] for loop in obj.data.uv_layers[0].data])
        xyz = np.empty(len(mesh.vertices)*3, dtype=np.float32)
        mesh.vertices.foreach_get("co", xyz)
        xyz = xyz.reshape(-1, 3)
        time = frame * scene.render.fps_base / scene.render.fps
        ray = np.array(obj.matrix_world.inverted().to_3x3()) @ -np.array(light.location)
        ray /= np.linalg.norm(ray)
        expected = uv[:, 0] ** 2 * gain + shift * 0.1 + time * 0.01 + ray[0] * 0.02
        actual = field(mesh, core, "TEXCOORD1.0")
        error = float(np.abs(actual[selected]-expected[selected]).max())
        assert error < 2e-5, error
        spatial = (xyz[indices, 0] / 0.08) ** 2 - 25.0
        spatial_error = float(np.abs(field(mesh, core, "TEXCOORD2.0")[selected]-spatial[selected]).max())
        assert spatial_error < 5e-4, spatial_error
        assert spatial[selected].min() < 0.0 and spatial[selected].max() > 1.0
        uniform = field(mesh, core, "uniform_Gain.0")[selected]
        assert float(np.abs(uniform-gain).max()) < 1e-6
        return {"frame": frame, "corner_error": error, "position_error": spatial_error,
                "corners": int(selected.sum()), "mean_spatial": float(spatial[selected].mean())}
    finally:
        evaluated.to_mesh_clear()


def run(args):
    slot = args.slot
    pmx, importer = load_importer(args.mmd_tools)
    scene = set_scene()
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = scene.render.resolution_y = 320
    scene.render.film_transparent = True
    model_hash = sha256(args.model.read_bytes()).hexdigest()
    model = pmx.load(str(args.model))
    resolve_textures(model, args.model, args.model.parent / "textures")
    importer().execute(pmx=model, scale=0.08, types={"MESH", "ARMATURE", "MORPHS"}, fix_bone_order=False)
    obj = model_object(scene, model)
    position_camera(scene, obj)
    original = snapshot(obj)
    original_attributes = tuple(obj.data.attributes.keys())
    source = obj.material_slots[slot].material
    control = add_empty(scene, "Vertex Control", (0.08, 0, 0))
    light = add_empty(scene, "Vertex light", (0.6, -1.0, 0.8))
    for frame, x in ((1, 0.08), (10, 0.16)):
        control.location.x = x
        control.keyframe_insert("location", index=0, frame=frame)
    # Animate a real weighted PMX bone; the corner position expression must run after skinning.
    rig = obj.find_armature()
    names = {group.name for group in obj.vertex_groups if any(
        assignment.group == group.index for vertex in obj.data.vertices[:100] for assignment in vertex.groups)}
    bone = next(bone for bone in rig.pose.bones if bone.name in names)
    for frame, x in ((1, 0.0), (10, 0.12)):
        bone.location.x = x
        bone.keyframe_insert("location", frame=frame)
        obj.rotation_euler.z = x
        obj.keyframe_insert("rotation_euler", frame=frame)
    scene.frame_set(1)
    args.output.mkdir(parents=True, exist_ok=True)
    # Recover camera/light-dependent positions without requiring one material for the mesh.
    def coordinates():
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            values = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
            mesh.vertices.foreach_get("co", values)
            return values.reshape(-1, 3).copy()
        finally:
            evaluated.to_mesh_clear()
    base_positions = coordinates()
    geometry = apply(obj, FX.with_name("camera_deform.fx"), DEFORM, units=0.08, light=light)
    ray = np.array(obj.matrix_world.inverted().to_3x3()) @ -np.array(light.location)
    ray /= np.linalg.norm(ray)
    expected_offset = np.array((scene.camera.matrix_world.translation.x * 0.02, 0, ray[2] * 0.03 * 0.08))
    assert float(np.abs(coordinates()-base_positions-expected_offset).max()) < 1e-6
    remove(obj, DEFORM)
    assert snapshot(obj) == original
    before = render_image(scene, args.output, "before", 1)
    result = apply(obj, FX, MATERIAL, slot=slot, units=0.08, light=light)
    vertex = result.core[VERTEX_CORE]
    wrapper, node = locate(obj, result.core["ninesync_middle_token"])
    assert len([mod for mod in obj.modifiers if mod == stage(obj)]) == 1
    assert tuple(obj.data.attributes.keys()) == original_attributes
    assert not any(item.name == "Gain" for item in result.core.interface.items_tree)
    for frame, gain in ((1, 0.8), (10, 0.4)):
        node.inputs["Gain"].default_value = gain
        node.inputs["Gain"].keyframe_insert("default_value", frame=frame)
    reports = [check(scene, obj, vertex, 1, 0.8, 1.0, light, slot), check(scene, obj, vertex, 10, 0.4, 2.0, light, slot)]
    first = render_image(scene, args.output, "converted", 1)
    moved = render_image(scene, args.output, "animated", 10)
    for image, gain in ((first, 0.8), (moved, 0.4)):
        encoded_green = gain * 0.25
        linear_green = ((encoded_green + 0.055) / 1.055) ** 2.4
        matched = ((np.abs(image[:, :, 1] - linear_green) < 1e-4) & (image[:, :, 3] > 0.99)).sum()
        assert int(matched) > 500, int(matched)
    assert float(np.abs(moved-first).mean()) > 0.001
    assert snapshot(obj) == original
    groups = len(bpy.data.node_groups)
    scene.frame_set(1)
    misplaced = obj.modifiers.new("Post vertex stage", "NODES")
    try:
        apply(obj, FX, MATERIAL, slot=slot, units=0.08, light=light)
    except Exception as error:
        assert "after" in str(error), str(error)
    else:
        raise AssertionError("A misplaced vertex stage was accepted.")
    finally:
        obj.modifiers.remove(misplaced)
    assert len(bpy.data.node_groups) == groups
    invalid = args.output / "invalid-clip-read.fx"
    invalid.write_text(FX.read_text().replace("input.value * 0.5", "input.position.x"), encoding="utf-8")
    try:
        apply(obj, invalid, MATERIAL, slot=slot, units=0.08, light=light)
    except Exception as error:
        assert "POSITION0" in str(error), str(error)
    else:
        raise AssertionError("A clip-space input was accepted.")
    assert len(bpy.data.node_groups) == groups and obj.material_slots[slot].material == result.destination
    assert locate(obj, result.core["ninesync_middle_token"])[1].node_tree == vertex
    rebuilt = apply(obj, FX, MATERIAL, slot=slot, units=0.08, light=light, force_rebuild=True)
    assert len(bpy.data.node_groups) == groups
    vertex = rebuilt.core[VERTEX_CORE]
    reports.append(check(scene, obj, vertex, 10, 0.4, 2.0, light, slot))
    warm = apply(obj, FX, MATERIAL, slot=slot, units=0.08, light=light)
    assert not warm.rebuilt
    replacement = add_empty(scene, "Replacement vertex control", (0.24, 0, 0))
    retarget(obj, MATERIAL, "Vertex Control", replacement, slot=slot)
    reports.append(check(scene, obj, vertex, 1, 0.8, 3.0, light, slot))
    retarget(obj, MATERIAL, "Vertex Control", control, slot=slot)
    reference = render_image(scene, args.output, "saved-reference", 1)
    assert float(np.abs(reference-first).mean()) < 1e-5
    bpy.ops.file.pack_all()
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "vertex-inputs.blend"), relative_remap=False)
    remove(obj, MATERIAL, slot=slot)
    assert obj.material_slots[slot].material == source and snapshot(obj) == original
    assert stage(obj) is None and tuple(obj.data.attributes.keys()) == original_attributes
    restored = render_image(scene, args.output, "restored", 1)
    assert float(np.abs(restored-before).mean()) < 1e-5
    bpy.ops.wm.open_mainfile(filepath=str(args.output / "vertex-inputs.blend"), load_ui=False)
    reopened = render_image(bpy.context.scene, args.output, "reopened", 1)
    assert float(np.abs(reopened-reference).mean()) < 1e-5
    assert sha256(args.model.read_bytes()).hexdigest() == model_hash
    (args.output / "results.json").write_text(json.dumps({"checks": reports, "model_sha256": model_hash,
        "source_data_preserved": True, "restore_mean_error": float(np.abs(restored-before).mean()),
        "reopen_mean_error": float(np.abs(reopened-reference).mean())}, indent=2), encoding="utf-8")
    print("PASS REAL_PMX_VERTEX_INPUTS", json.dumps(reports), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot",type=int,default=1)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--mmd-tools", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
