"""Per-corner deformation, topology and normal preservation on a real PMX."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer, model_object, resolve_textures, snapshot, position_camera, render_image
from t_middle_converters import set_scene
from t_middle_vertex import field
from ninesync_fx.runtime.middle.targets import apply, remove, MATERIAL
from ninesync_fx.runtime.middle.vertex import stage, locate, VERTEX_CORE

FX = Path(__file__).parent / "fixtures" / "middle" / "corner_deform.fx"
UNITS = 0.08


def evaluated(obj):
    instance = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = instance.to_mesh()
    try:
        vertices = np.array([v.co[:] for v in mesh.vertices])
        corners = np.array([loop.vertex_index for loop in mesh.loops])
        return dict(positions=vertices[corners], normals=np.array([n.vector[:] for n in mesh.corner_normals]),
                    uv=np.array([v.uv[:] for v in mesh.uv_layers[0].data]), vertices=len(vertices),
                    materials=np.array([p.material_index for p in mesh.polygons]),
                    polygons=np.array([p.loop_total for p in mesh.polygons]))
    finally:
        instance.to_mesh_clear()


def run(args):
    pmx, importer = load_importer(args.mmd_tools)
    scene = set_scene()
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = scene.render.resolution_y = 320
    scene.render.film_transparent = True
    model_hash = sha256(args.model.read_bytes()).hexdigest()
    model = pmx.load(str(args.model))
    resolve_textures(model, args.model, args.model.parent / "textures")
    importer().execute(pmx=model, scale=UNITS, types={"MESH", "ARMATURE", "MORPHS"}, fix_bone_order=False)
    obj = model_object(scene, model)
    position_camera(scene, obj)
    args.output.mkdir(parents=True, exist_ok=True)
    original = snapshot(obj)
    originals = tuple(slot.material for slot in obj.material_slots)
    original_attrs = tuple(obj.data.attributes.keys())
    # Prefer the real material boundary with the most shared source vertices.
    by_slot = {}
    for polygon in obj.data.polygons:
        by_slot.setdefault(polygon.material_index, set()).update(polygon.vertices)
    overlap, a, b = max((len(va & vb), a, b) for a, va in by_slot.items()
                        for b, vb in by_slot.items() if a < b)
    slots = (a, b) if overlap else (1, 12)
    rig = obj.find_armature()
    bone = next(b for b in rig.pose.bones if b.name in obj.vertex_groups)
    keys = obj.data.shape_keys
    morph = next((key for key in keys.key_blocks if key != keys.reference_key
                  and any((p.co-q.co).length > 1e-6 for p, q in zip(key.data, keys.reference_key.data))), None)
    assert morph is not None
    for frame, value in ((1, 0.0), (10, 0.08)):
        bone.location.x = value
        bone.keyframe_insert("location", frame=frame)
        morph.value = value * 5
        morph.keyframe_insert("value", frame=frame)
    baseline = {}
    for frame in (1, 10):
        scene.frame_set(frame)
        baseline[frame] = evaluated(obj)
    assert float(np.abs(baseline[1]["positions"]-baseline[10]["positions"]).max()) > 1e-4
    gains = {slots[0]: 0.4, slots[1]: -0.3}
    results = {}
    for slot in slots:
        result = apply(obj, FX, MATERIAL, slot=slot, units=UNITS)
        results[slot] = result
        core = result.core[VERTEX_CORE]
        assert core["ninesync_middle_split_corners"]
        _wrapper, node = locate(obj, result.core["ninesync_middle_token"])
        node.inputs["Gain"].default_value = gains[slot]
    reports = []

    def check(frame):
        scene.frame_set(frame)
        actual = evaluated(obj)
        base = baseline[frame]
        assert actual["vertices"] == len(base["positions"]), (actual["vertices"], len(base["positions"]))
        assert np.array_equal(actual["materials"], base["materials"])
        assert np.array_equal(actual["polygons"], base["polygons"])
        assert np.array_equal(actual["uv"], base["uv"])
        normal_error = float(np.abs(actual["normals"]-base["normals"]).max())
        assert normal_error < 2e-5, normal_error
        expected = base["positions"].copy()
        material = np.repeat(base["materials"], base["polygons"])
        for slot, gain in gains.items():
            selected = material == slot
            expected[selected, 0] += (base["uv"][selected, 0]**2*gain + frame/25*0.1)*UNITS
            core = results[slot].core[VERTEX_CORE]
            instance = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh = instance.to_mesh()
            try:
                values = np.column_stack([field(mesh, core, f"TEXCOORD0.{i}") for i in range(3)])
                assert float(np.abs(values[selected]-base["normals"][selected][:, (0, 2, 1)]).max()) < 2e-5
            finally:
                instance.to_mesh_clear()
        error = float(np.abs(actual["positions"]-expected).max())
        assert error < 2e-6, error
        assert snapshot(obj) == original
        assert tuple(obj.data.attributes.keys()) == original_attrs
        reports.append(dict(frame=frame, maximum_position_error=error, maximum_normal_error=normal_error,
                            source_vertices=len(obj.data.vertices), evaluated_vertices=actual["vertices"]))
        return actual

    first = check(1)
    check(10)
    groups = len(bpy.data.node_groups)
    for slot in slots:
        results[slot] = apply(obj, FX, MATERIAL, slot=slot, units=UNITS, force_rebuild=True)
    assert len(bpy.data.node_groups) == groups
    check(10)
    scene.frame_set(1)
    bpy.ops.file.pack_all()
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "corner-deformation.blend"), relative_remap=False)
    # Remove one stage while the other is still active.
    removed_slot = slots[0]
    remove(obj, MATERIAL, slot=removed_slot)
    remaining = evaluated(obj)
    selected = np.repeat(baseline[1]["materials"], baseline[1]["polygons"]) == removed_slot
    assert float(np.abs(remaining["positions"][selected]-baseline[1]["positions"][selected]).max()) < 1e-6
    remove(obj, MATERIAL, slot=slots[1])
    assert stage(obj) is None and snapshot(obj) == original
    assert tuple(slot.material for slot in obj.material_slots) == originals
    assert np.array_equal(evaluated(obj)["positions"], baseline[1]["positions"])
    bpy.ops.wm.open_mainfile(filepath=str(args.output / "corner-deformation.blend"), load_ui=False)
    obj = model_object(bpy.context.scene, model)
    reopened = evaluated(obj)
    reopen_error = float(np.abs(reopened["positions"]-first["positions"]).max())
    assert reopen_error == 0.0, reopen_error
    assert sha256(args.model.read_bytes()).hexdigest() == model_hash
    report = dict(source=str(args.model), sha256=model_hash, slots=slots, shared_source_vertices=overlap,
                  checks=reports, source_preserved=True, rollback_verified=True, reopen_max_error=reopen_error)
    (args.output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("PASS REAL_PMX_CORNER_DEFORMATION", json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--mmd-tools", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
