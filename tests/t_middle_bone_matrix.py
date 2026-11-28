"""Native-calibrated bone controllers on an animated real PMX-derived model."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from math import radians
import sys

import bpy
from mathutils import Euler
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer, model_object, snapshot, position_camera, resolve_textures
from t_middle_converters import set_scene
from t_middle_vertex import field
from ninesync_fx.runtime.middle.targets import apply, remove, retarget, inspect_bindings, MATERIAL
from ninesync_fx.runtime.middle.vertex import VERTEX_CORE
from ninesync_fx.runtime.middle.controllers import resolve
from ninesync_fx.nodes.material_compile import mme_tree_signature
from ninesync_fx.fx.errors import FxUnsupported

FX = Path(__file__).parent / "fixtures/middle/bone_matrix.fx"
UNITS = 0.08
SWAP = (0, 2, 1, 3)


def prepare(pmx, args):
    """Create labeled native diagnostics without modifying the creator's files."""
    args.output.mkdir(parents=True, exist_ok=True)
    model = pmx.load(str(args.source))
    resolve_textures(model, args.source, args.source.parent / "textures")
    for texture in model.textures:
        path = Path(texture.path)
        texture.path = str(path if path.is_absolute() else args.source.parent / path)
    model.name = model.name_e = "Sanae-Bone-Probe"
    aliases = {"\u30bb\u30f3\u30bf\u30fc": "ProbeCenter", "\u5de6\u8155": "ProbeArm"}
    for bone in model.bones:
        if bone.name in aliases:
            bone.name = bone.name_e = aliases[bone.name]
    assert all(any(b.name == name for b in model.bones) for name in aliases.values())
    pmx.save(str(args.output / "Sanae-bone-diagnostic.pmx"), model)
    from mmd_tools.core import vmd
    motion = vmd.File()
    motion.header = vmd.Header()
    motion.header.model_name = model.name
    motion.boneAnimation = vmd.BoneAnimation()
    states = [(0,(0,0,0),(0,0,0),(0,0,0)), (10,(1,2,-1),(10,20,15),(25,-20,35)),
              (20,(-2,1,2),(-15,35,-10),(-10,30,-25))]
    for frame, center, rotation, arm in states:
        for name, location, angles in [("ProbeCenter", center, rotation), ("ProbeArm", (0,0,0), arm)]:
            key = vmd.BoneFrameKey()
            key.frame_number, key.location = frame, location
            q = Euler(tuple(radians(a) for a in angles), "XYZ").to_quaternion()
            key.rotation = (q.x,q.y,q.z,q.w)
            key.interp = [20]*64
            motion.boneAnimation[name].append(key)
    motion.save(filepath=str(args.output / "bone-motion.vmd"))
    template = FX.with_name("bone_matrix_native.fx").read_text()
    for channel in range(5):
        (args.output / f"native-{channel}.fx").write_text(template.replace(
            "#define BONE_CHANNEL 0", f"#define BONE_CHANNEL {channel}"))


def matrix(rig, name):
    bone = rig.pose.bones[name]
    result = np.array(rig.matrix_world @ bone.matrix @ bone.bone.matrix_local.to_3x3().inverted().to_4x4())
    result = result[np.ix_(SWAP, SWAP)].T
    result[3, :3] /= UNITS
    return result


def run(args):
    pmx, importer = load_importer(args.mmd_tools)
    if args.prepare:
        prepare(pmx, args)
        print("PREPARED_NATIVE_BONE_DIAGNOSTICS", args.output, flush=True)
        return
    scene = set_scene()
    scene.render.resolution_x = scene.render.resolution_y = 320
    scene.render.film_transparent = True
    digest = sha256(args.source.read_bytes()).hexdigest()
    model = pmx.load(str(args.output / "Sanae-bone-diagnostic.pmx"))
    importer().execute(pmx=model, scale=UNITS, types={"MESH", "ARMATURE", "MORPHS"}, fix_bone_order=False)
    obj = model_object(scene, model)
    rig = obj.find_armature()
    rig["ninesync_mme_name"] = "Bone controller.pmx"
    scene.frame_set(0)
    from mmd_tools.core.vmd.importer import VMDImporter
    VMDImporter(str(args.output / "bone-motion.vmd"), scale=UNITS, frame_margin=0).assign(rig)
    position_camera(scene, obj)
    source = snapshot(obj)
    original_material = obj.material_slots[1].material
    signature = mme_tree_signature(original_material.node_tree)
    result = apply(obj, FX, MATERIAL, slot=1, units=UNITS, bindings={"Bone controller.pmx": rig})
    reports = []
    rendered = {}

    def check(frame, other, *, render=False):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        deps = bpy.context.evaluated_depsgraph_get()
        evaluated_rig = rig.evaluated_get(deps)
        expected = matrix(evaluated_rig, "ProbeArm")
        center = np.array((evaluated_rig.matrix_world @ evaluated_rig.pose.bones["ProbeCenter"].matrix).translation)[[0, 2, 1]] / UNITS
        other_matrix = matrix(other.evaluated_get(deps), "ProbeArm")
        values = [*expected, center, other_matrix[3]]
        instance = obj.evaluated_get(deps)
        mesh = instance.to_mesh()
        try:
            selected = np.repeat([p.material_index == 1 for p in mesh.polygons], [p.loop_total for p in mesh.polygons])
            core = result.core[VERTEX_CORE]
            errors = []
            for row, value in enumerate(values):
                actual = np.column_stack([field(mesh, core, f"TEXCOORD{row}.{c}") for c in range(len(value))])
                error = float(np.max(np.abs(actual[selected] - value)))
                assert error < 8e-6, (frame, row, error, actual[selected][0], value)
                errors.append(error)
        finally:
            instance.to_mesh_clear()
        if render:
            weights = np.array([1,2,3,4])
            wanted = np.array([np.dot(expected[0]+expected[1],weights)*.02+expected[1,1]*.1+.3,
                               np.dot(expected[2]+expected[3],weights)*.005+expected[2,2]*.01+.3,
                               np.dot(center,weights[:3])*.005+np.dot(other_matrix[3],weights)*.005
                               +center[1]*.01+other_matrix[3,2]*.01+.2, 1])
            wanted[:3] = np.where(wanted[:3] <= .04045, wanted[:3]/12.92, ((wanted[:3]+.055)/1.055)**2.4)
            scene.render.image_settings.file_format = "OPEN_EXR"
            scene.render.image_settings.color_mode = "RGBA"
            scene.render.image_settings.color_depth = "32"
            scene.render.filepath = str(args.output / f"blender-{frame}.exr")
            bpy.ops.render.render(write_still=True)
            image = bpy.data.images.load(scene.render.filepath, check_existing=False)
            pixels = np.array(image.pixels[:]).reshape(-1, 4)
            matching = int((np.max(np.abs(pixels-wanted), axis=1) < 2e-5).sum())
            bpy.data.images.remove(image)
            assert matching > 500, (frame, matching, wanted)
            if frame in rendered:
                error = float(np.max(np.abs(pixels-rendered[frame])))
                assert error == 0, ("Reopening changed the shader render", frame, error)
            rendered[frame] = pixels
        assert not inspect_bindings(obj, MATERIAL, slot=1), inspect_bindings(obj, MATERIAL, slot=1)
        assert snapshot(obj) == source
        reports.append(dict(frame=frame, errors=errors, matrix=expected.tolist(), center=center.tolist(), render=render))

    for frame in (0, 10, 20):
        check(frame, rig, render=True)
    # Armature object placement and evaluated constraints must also remain live.
    rig.location = (0.13, -0.08, 0.21)
    rig.rotation_euler = (0.1, -0.2, 0.3)
    rig.scale = (1.1, 0.9, 1.05)
    check(20, rig)
    rig.location = rig.rotation_euler = (0, 0, 0)
    rig.scale = (1, 1, 1)
    constraint_target = bpy.data.objects.new("Bone constraint target", None)
    scene.collection.objects.link(constraint_target)
    constraint_target.rotation_euler = (0.1, 0.2, -0.3)
    constraint = rig.pose.bones["ProbeArm"].constraints.new("COPY_ROTATION")
    constraint.target = constraint_target
    check(10, rig)
    constraint_target.rotation_euler = (-0.2, 0.1, 0.3)
    check(20, rig)
    rig.pose.bones["ProbeArm"].constraints.remove(constraint)
    bpy.data.objects.remove(constraint_target, do_unlink=True)
    replacement = rig.copy()
    replacement.data = rig.data.copy()
    scene.collection.objects.link(replacement)
    replacement.location = (0.2, -0.12, 0.1)
    retarget(obj, MATERIAL, "Bone controller.pmx", replacement, slot=1)
    check(10, replacement)
    retarget(obj, MATERIAL, "Bone controller.pmx", rig, slot=1)
    check(10, rig)
    replacement_data = replacement.data
    bpy.data.objects.remove(replacement, do_unlink=True)
    bpy.data.armatures.remove(replacement_data)
    assert not apply(obj, FX, MATERIAL, slot=1, units=UNITS).rebuilt
    # Original MMD names survive a user rename of Blender's pose bone.
    rig.pose.bones["ProbeArm"].name = "Renamed arm"
    target, values = resolve(scene, obj, "(self)", "ProbeArm", 16, {}, UNITS, dims=(4,4))
    assert target == obj and len(values) == 16
    assert not inspect_bindings(obj, MATERIAL, slot=1)
    # Rest input drivers follow a renamed bone and update without rebuilding.
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    original_roll = rig.data.edit_bones["Renamed arm"].roll
    rig.data.edit_bones["Renamed arm"].roll += .1
    bpy.ops.object.mode_set(mode="OBJECT")
    assert not inspect_bindings(obj, MATERIAL, slot=1)
    bpy.ops.object.mode_set(mode="EDIT")
    rig.data.edit_bones["Renamed arm"].roll = original_roll
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.pose.bones["Renamed arm"].name = "ProbeArm"
    # A broken source must reject rebuilding without replacing working output.
    rig.pose.bones["ProbeArm"].mmd_bone.name_j = "ProbeArm"
    rig.pose.bones["ProbeCenter"].mmd_bone.name_j = "ProbeArm"
    count = len(bpy.data.node_groups)
    try:
        apply(obj, FX, MATERIAL, slot=1, units=UNITS, force_rebuild=True)
    except FxUnsupported as error:
        assert "ambiguous" in str(error)
    else:
        raise AssertionError("Ambiguous original bone name was accepted")
    assert len(bpy.data.node_groups) == count
    rig.pose.bones["ProbeCenter"].mmd_bone.name_j = "ProbeCenter"
    result = apply(obj, FX, MATERIAL, slot=1, units=UNITS, force_rebuild=True)
    check(20, rig)
    # Edit-mode rest changes update native RNA drivers before any rebuild.
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    before = rig.data.bones["ProbeArm"].matrix_local.copy()
    bpy.ops.object.mode_set(mode="EDIT")
    rig.data.edit_bones["ProbeArm"].roll += .1
    bpy.ops.object.mode_set(mode="OBJECT")
    assert not inspect_bindings(obj, MATERIAL, slot=1)
    check(20, rig)
    result = apply(obj, FX, MATERIAL, slot=1, units=UNITS)
    assert not result.rebuilt
    check(20, rig)
    bpy.ops.object.mode_set(mode="EDIT")
    rig.data.edit_bones["ProbeArm"].matrix = before
    bpy.ops.object.mode_set(mode="OBJECT")
    result = apply(obj, FX, MATERIAL, slot=1, units=UNITS)
    check(20, rig)
    # Establish the reopening reference after the test's deliberate rest edits.
    assert np.max(np.abs(np.array(rig.data.bones["ProbeArm"].matrix_local)-np.array(before))) < 1e-6
    rendered.clear()
    for frame in (0, 10, 20):
        check(frame, rig, render=True)
    scene.frame_set(10)
    bpy.ops.file.pack_all()
    bpy.context.preferences.filepaths.save_version = 0
    saved = args.output / "bone-controllers.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), relative_remap=False)
    remove(obj, MATERIAL, slot=1)
    assert snapshot(obj) == source
    assert mme_tree_signature(original_material.node_tree) == signature
    bpy.ops.wm.open_mainfile(filepath=str(saved), load_ui=False)
    scene = bpy.context.scene
    obj = model_object(scene, model)
    rig = obj.find_armature()
    source = snapshot(obj)
    result = apply(obj, FX, MATERIAL, slot=1, units=UNITS)
    assert not result.rebuilt
    for frame in (0, 10, 20):
        check(frame, rig, render=True)
    # A retained Object ID cannot hide removal of the actual source rig.
    collections = tuple(rig.users_collection)
    for collection in collections:
        collection.objects.unlink(rig)
    assert any("unavailable" in s for s in inspect_bindings(obj, MATERIAL, slot=1))
    for collection in collections:
        collection.objects.link(rig)
    check(20, rig)
    # Validate native outputs independently from Blender driver evaluation.
    native = []
    for record in reports[:3]:
        bone = np.array(record["matrix"])
        expected = [*(bone[:3,:3]*.25+.5), bone[3,:3]/40+.5, np.array(record["center"])/40+.5]
        for channel, color in enumerate(expected):
            path = args.output / f"native-{channel}-{record['frame']}.png"
            image = bpy.data.images.load(str(path), check_existing=False)
            image.colorspace_settings.name = "Non-Color"
            pixels = np.array(image.pixels[:]).reshape(-1,4)
            rgb = np.rint(pixels[:,:3]*255).astype(np.int32)
            nonblack = rgb[np.any(rgb != 0, axis=1)]
            observed, count = Counter(map(tuple, nonblack)).most_common(1)[0]
            error = float(np.max(np.abs(np.array(observed)-color*255)))
            assert count > 10000 and error < .6, (record["frame"], channel, observed, color*255, error)
            bpy.data.images.remove(image)
            native.append(dict(frame=record["frame"], channel=channel, pixels=count, max_byte_error=error,
                               observed=list(map(int,observed)), image_hash=sha256(path.read_bytes()).hexdigest()))
    assert sha256(args.source.read_bytes()).hexdigest() == digest
    (args.output / "results.json").write_text(json.dumps(dict(checks=reports, native=native, source_hash=digest), indent=2))
    print("PASS BONE_MATRIX_CONTROLLERS", len(reports), "Blender states;", len(native), "native MMD probes", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--mmd-tools", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
