"""Animated controller drivers using the original PointLight X and Sanae PMX."""
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
from ninesync_fx.runtime.middle.targets import apply, remove, retarget, inspect_bindings, MATERIAL
from ninesync_fx.runtime.middle.vertex import VERTEX_CORE
from ninesync_fx.runtime.middle.controllers import resolve, wire

FX = Path(__file__).parent / "fixtures" / "middle" / "accessory_controls.fx"
UNITS = 0.08


def run(args):
    pmx, importer = load_importer(args.mmd_tools)
    scene = set_scene()
    model = pmx.load(str(args.model))
    resolve_textures(model, args.model, args.model.parent / "textures")
    importer().execute(pmx=model, scale=UNITS, types={"MESH", "ARMATURE", "MORPHS"}, fix_bone_order=False)
    obj = model_object(scene, model)
    position_camera(scene, obj)
    source = snapshot(obj)
    hashes = {str(p): sha256(p.read_bytes()).hexdigest() for p in (args.model, args.accessory)}
    # The production generic X importer supplies the actual accessory root/data,
    # including its Si/Tr tracks, rather than substituting a dummy controller.
    from harness_fx import mb
    before = set(scene.objects)
    assert bpy.ops.import_scene.ninesync_fx_accessory(filepath=str(args.accessory), scale=UNITS) == {"FINISHED"}
    roots = [o for o in scene.objects if o not in before and o.parent not in set(scene.objects)-before]
    assert len(roots) == 1, roots
    root = roots[0]
    root["ninesync_mme_name"] = args.accessory.name
    assert root is not None
    root.rotation_mode = "XYZ"
    for frame, position, rotation, size, opacity, hidden in (
        (1, (0.08, 0.16, 0.24), (0.1, 0.2, -0.3), 1.2, 0.6, False),
        (10, (-0.24, 0.32, 0.4), (-0.2, 0.4, 0.3), 0.7, 0.2, True)):
        root.location, root.rotation_euler, root.scale = position, rotation, (size,)*3
        root["mme_si"], root["mme_tr"], root.hide_render = size, opacity, hidden
        for path in ("location", "rotation_euler", "scale", '["mme_si"]', '["mme_tr"]', "hide_render"):
            root.keyframe_insert(path, frame=frame)
    scene.frame_set(1)
    result = apply(obj, FX, MATERIAL, slot=1, units=UNITS)
    assert not inspect_bindings(obj, MATERIAL, slot=1)
    reports = []

    def check(frame, controller):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        target = controller.evaluated_get(bpy.context.evaluated_depsgraph_get())
        world = np.array(target.matrix_world)
        swap = np.eye(4)[:, (0, 2, 1, 3)]
        inv_unit = np.diag([1/UNITS]*3 + [1.0])
        mmd = (inv_unit @ swap @ world @ np.diag([10*UNITS]*3+[1.0]) @ swap).T
        expected = [np.array((0.1, 0.2, 0.3, 1.0)) @ mmd,
                    [-target.rotation_euler.x, -target.rotation_euler.z, -target.rotation_euler.y,
                     target["mme_si"] * 10],
                    [world[0, 3]/UNITS, world[2, 3]/UNITS, world[1, 3]/UNITS, target["mme_tr"]],
                    [float(not target.hide_render), target["mme_si"] * 10, 0.0, 1.0]]
        core = result.core[VERTEX_CORE]
        instance = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = instance.to_mesh()
        try:
            selected = np.repeat([p.material_index == 1 for p in mesh.polygons],
                                 [p.loop_total for p in mesh.polygons])
            errors = []
            for i, values in enumerate(expected):
                actual = np.column_stack([field(mesh, core, f"TEXCOORD{i}.{j}") for j in range(4)])
                error = float(np.abs(actual[selected]-values).max())
                assert error < 2e-5, (i, error, actual[selected][0], values)
                errors.append(error)
        finally:
            instance.to_mesh_clear()
        assert snapshot(obj) == source
        reports.append(dict(frame=frame, errors=errors))

    check(1, root)
    check(10, root)
    from ninesync_fx.accessories.standalone_import import import_plain_accessory
    replacement = import_plain_accessory(bpy.context, str(args.accessory), UNITS)
    replacement.rotation_mode = "XYZ"
    replacement.location, replacement.rotation_euler = (0.4, -0.16, 0.08), (0.3, -0.2, 0.1)
    replacement.scale = (1.1,)*3
    replacement["mme_si"], replacement["mme_tr"] = 1.1, 0.9
    replacement.update_tag()
    retarget(obj, MATERIAL, args.accessory.name, replacement, slot=1)
    check(10, replacement)
    retarget(obj, MATERIAL, args.accessory.name, root, slot=1)
    check(10, root)
    # Exercise the same accessor sources in a pixel-stage driver tree as well.
    tree = bpy.data.node_groups.new("Controller driver diagnostic", "ShaderNodeTree")
    sources = resolve(scene, obj, root.name, "Rxyz", 3, {}, UNITS)[1]
    for item in sources:
        node = tree.nodes.new("ShaderNodeValue")
        wire(node.outputs[0], item)
    assert all(curve.driver.is_valid for curve in tree.animation_data.drivers)
    bpy.data.node_groups.remove(tree)
    # Explicit mappings must still report stale/missing objects, including bools.
    try:
        resolve(scene, obj, "bad.x", "", 1, {"bad.x": None}, UNITS, base="b")
    except Exception as error:
        assert "outside" in str(error)
    else:
        raise AssertionError("An explicit stale mapping became an optional false flag")
    groups = len(bpy.data.node_groups)
    result = apply(obj, FX, MATERIAL, slot=1, units=UNITS, force_rebuild=True)
    assert len(bpy.data.node_groups) == groups
    check(10, root)
    assert not apply(obj, FX, MATERIAL, slot=1, units=UNITS).rebuilt
    # A newly present optional reference must invalidate the warm build.
    optional = bpy.data.objects.new("Optional-controller.pmx", None)
    scene.collection.objects.link(optional)
    assert any("now present" in message for message in inspect_bindings(obj, MATERIAL, slot=1))
    bpy.data.objects.remove(optional, do_unlink=True)
    args.output.mkdir(parents=True, exist_ok=True)
    scene.frame_set(1)
    bpy.ops.file.pack_all()
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "accessory-controllers.blend"), relative_remap=False)
    remove(obj, MATERIAL, slot=1)
    assert snapshot(obj) == source
    bpy.ops.wm.open_mainfile(filepath=str(args.output / "accessory-controllers.blend"), load_ui=False)
    scene = bpy.context.scene
    obj = model_object(scene, model)
    source = snapshot(obj)  # Datablock addresses change when Blender reopens a file.
    root = next(o for o in scene.objects if o.get("ninesync_mme_name") == args.accessory.name)
    from ninesync_fx.runtime.middle.targets import _core_node, TOKEN
    material = obj.material_slots[1].material
    result = type("SavedResult", (), {"core": _core_node(material.node_tree, material[TOKEN]).node_tree})()
    assert not inspect_bindings(obj, MATERIAL, slot=1)
    check(10, root)
    for path, digest in hashes.items():
        assert sha256(Path(path).read_bytes()).hexdigest() == digest
    report = dict(hashes=hashes, checks=reports, original_pmx_preserved=True, original_x_imported=True,
                  rebuild_verified=True, retarget_verified=True, optional_presence_verified=True, reopen_verified=True)
    (args.output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("PASS REAL_ACCESSORY_CONTROLLERS", json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--accessory", type=Path, required=True)
    parser.add_argument("--mmd-tools", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
