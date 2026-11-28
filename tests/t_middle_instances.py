"""Verify real animated PMX duplication driven by MME nested instance loops."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer, model_object, resolve_textures, snapshot, position_camera
from t_middle_converters import set_scene
from t_middle_vertex import field
from ninesync_fx.runtime.middle.targets import apply, remove, MATERIAL, DEFORM, _core_node, TOKEN
from ninesync_fx.runtime.middle.vertex import stage, locate, VERTEX_CORE

FX = Path(__file__).parent / "fixtures" / "middle" / "model_instances.fx"
UNITS = 0.08


def read(obj, core=None):
    instance = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = instance.to_mesh()
    try:
        if not mesh.loops:
            result = dict(ids=np.empty(0,dtype=int),points=np.empty((0,3)),normals=np.empty((0,3)),
                          materials=np.empty(0,dtype=int),vertices=len(mesh.vertices),faces=len(mesh.polygons),uv={})
            if core:result['loop_id']=result['outer']=np.empty(0)
            return result
        ids = np.array([v.value for v in mesh.attributes["diagnostic_source_corner"].data])
        points = np.array([v.co[:] for v in mesh.vertices])
        indices = np.array([v.vertex_index for v in mesh.loops])
        result = dict(ids=ids, points=points[indices], normals=np.array([n.vector[:] for n in mesh.corner_normals]),
                      materials=np.repeat([p.material_index for p in mesh.polygons], [p.loop_total for p in mesh.polygons]),
                      vertices=len(mesh.vertices), faces=len(mesh.polygons))
        result['uv']={}
        for layer in mesh.uv_layers:
            values=np.empty(len(mesh.loops)*2,dtype=np.float32)
            layer.data.foreach_get('uv',values)
            result['uv'][layer.name]=values.reshape(-1,2)
        if core and np.any(result["materials"] == 1):
            result["loop_id"] = field(mesh, core, "TEXCOORD0.0")
            result["outer"] = field(mesh, core, "loop_Outer.0")
        elif core:
            result["loop_id"] = result["outer"] = np.zeros(len(ids))
        return result
    finally:
        instance.to_mesh_clear()


def run(args):
    pmx, importer = load_importer(args.mmd_tools)
    scene = set_scene()
    model = pmx.load(str(args.model))
    resolve_textures(model, args.model, args.model.parent / "textures")
    importer().execute(pmx=model, scale=UNITS, types={"MESH", "ARMATURE", "MORPHS"}, fix_bone_order=False)
    obj = model_object(scene, model)
    position_camera(scene, obj)
    source = snapshot(obj)
    digest = sha256(args.model.read_bytes()).hexdigest()
    original_attributes = tuple(obj.data.attributes.keys())
    # Add correspondence only to evaluated geometry, leaving the real mesh intact.
    annotation = bpy.data.node_groups.new("Evaluated corner correspondence", "GeometryNodeTree")
    for direction in ("INPUT", "OUTPUT"):
        annotation.interface.new_socket("Geometry", in_out=direction, socket_type="NodeSocketGeometry")
    entry, output = annotation.nodes.new("NodeGroupInput"), annotation.nodes.new("NodeGroupOutput")
    store = annotation.nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type, store.domain = "INT", "CORNER"
    store.inputs["Name"].default_value = "diagnostic_source_corner"
    index = annotation.nodes.new("GeometryNodeInputIndex")
    annotation.links.new(index.outputs[0], store.inputs["Value"])
    annotation.links.new(entry.outputs["Geometry"], store.inputs["Geometry"])
    annotation.links.new(store.outputs["Geometry"], output.inputs["Geometry"])
    modifier = obj.modifiers.new("Evaluated corner correspondence", "NODES")
    modifier.node_group = annotation
    source = snapshot(obj)
    rig = obj.find_armature()
    bone = next(b for b in rig.pose.bones if b.name in obj.vertex_groups)
    for frame, offset in ((1, 0.0), (10, 0.1)):
        bone.location.x = offset
        bone.keyframe_insert("location", frame=frame)
    baseline = {}
    for frame in (1, 10):
        scene.frame_set(frame)
        baseline[frame] = read(obj)
    result = apply(obj, FX, MATERIAL, slot=1, units=UNITS)
    core = result.core[VERTEX_CORE]
    assert core["ninesync_middle_instance_loops"]
    wrapper, node = locate(obj, result.core[TOKEN])
    reports = []

    def check(frame, copies, layers):
        scene.frame_set(frame)
        actual, base = read(obj, core), baseline[frame]
        chosen = actual["materials"] == 1
        selected_base = base["materials"] == 1
        n = int(selected_base.sum())
        assert chosen.sum() == copies * layers * n, (chosen.sum(), copies, layers, n)
        assert (~chosen).sum() == (~selected_base).sum()
        ids = actual["ids"]
        expected = base["points"][ids].copy()
        expected_normal = base["normals"][ids]
        loop_id = actual["loop_id"][chosen]
        outer = actual["outer"][chosen]
        if copies * layers:
            assert set(np.unique(loop_id)) == {a + 10*b for a in range(copies) for b in range(layers)}
            assert np.array_equal(outer, np.remainder(loop_id, 10))
            expected[chosen, 0] += outer * 0.4 * UNITS
            expected[chosen, 2] += (np.floor(loop_id/10)*0.2 + frame/25*0.1) * UNITS
        error = float(np.abs(actual["points"]-expected).max())
        normals = float(np.abs(actual["normals"]-expected_normal).max())
        assert error < 2e-6, error
        assert normals < 2e-5, normals
        assert snapshot(obj) == source
        assert tuple(obj.data.attributes.keys()) == original_attributes
        reports.append(dict(frame=frame, copies=copies, layers=layers, corners=len(ids),
                            maximum_position_error=error, maximum_normal_error=normals))
        return actual

    check(1, 2, 3)
    check(10, 2, 3)
    node.inputs["Copies"].default_value = 3
    node.inputs["Layers"].default_value = 2
    check(10, 3, 2)
    for frame, count in ((1, 0), (10, 3)):
        node.inputs["Copies"].default_value = count
        node.inputs["Copies"].keyframe_insert("default_value", frame=frame)
    check(1, 0, 2)
    first = check(10, 3, 2)
    groups = len(bpy.data.node_groups)
    result = apply(obj, FX, MATERIAL, slot=1, units=UNITS, force_rebuild=True)
    core = result.core[VERTEX_CORE]
    assert len(bpy.data.node_groups) == groups
    check(10, 3, 2)
    args.output.mkdir(parents=True, exist_ok=True)
    bpy.ops.file.pack_all()
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "instance-loops.blend"), relative_remap=False)
    remove(obj, MATERIAL, slot=1)
    assert stage(obj) is None and snapshot(obj) == source
    assert np.array_equal(read(obj)["points"], baseline[10]["points"])
    geometry = apply(obj, FX, DEFORM, units=UNITS)
    data = read(obj)
    assert data["vertices"] == len(obj.data.vertices)*6
    assert data["faces"] == len(obj.data.polygons)*6
    assert geometry.core["ninesync_middle_instance_loops"]
    iteration = np.repeat(np.arange(6), len(baseline[10]["ids"]))
    expected = baseline[10]["points"][data["ids"]].copy()
    expected[:, 0] += np.floor(iteration/3) * 0.4 * UNITS
    expected[:, 2] += (np.remainder(iteration, 3) * 0.2 + 10/25*0.1) * UNITS
    geometry_error = float(np.abs(data["points"]-expected).max())
    assert geometry_error < 2e-6, geometry_error
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "geometry-loops.blend"), relative_remap=False)
    remove(obj, DEFORM)
    assert np.array_equal(read(obj)["points"], baseline[10]["points"])
    bpy.ops.wm.open_mainfile(filepath=str(args.output / "instance-loops.blend"), load_ui=False)
    scene = bpy.context.scene
    obj = model_object(scene, model)
    material = obj.material_slots[1].material
    core = _core_node(material.node_tree, material[TOKEN]).node_tree[VERTEX_CORE]
    reopened = read(obj, core)
    assert np.array_equal(reopened["points"], first["points"])
    bpy.ops.wm.open_mainfile(filepath=str(args.output / "geometry-loops.blend"), load_ui=False)
    obj = model_object(bpy.context.scene, model)
    assert np.array_equal(read(obj)["points"], data["points"])
    assert sha256(args.model.read_bytes()).hexdigest() == digest
    report = dict(source=str(args.model), sha256=digest, checks=reports,
                  standalone_vertices=data["vertices"], standalone_faces=data["faces"],
                  standalone_maximum_error=geometry_error, standalone_reopen_verified=True,
                  rollback_verified=True, reopen_verified=True, source_preserved=True)
    (args.output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("PASS REAL_PMX_INSTANCE_LOOPS", json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--mmd-tools", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
