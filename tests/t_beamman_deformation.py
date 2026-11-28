"""Run original BeamMan FireBall FX/PMX through recovered live deformation."""
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer, model_object, snapshot, render_image
from t_middle_converters import set_scene
from ninesync_fx.fx.entry import fx_entry, fx_run_entry
from ninesync_fx.fx.interp.executor import FxExec
from ninesync_fx.fx.interp.numpy_backend import FxNumpyBackend
from ninesync_fx.fx.loader import fx_parse_file
from ninesync_fx.fx.semantics import FX_NEAR, FX_FAR, FxSceneState, fx_classify_global, fx_lh_look, fx_value_from_list
from ninesync_fx.fx.textures import FxImage
from ninesync_fx.fx.types import FxValue, fx_resolve_type
from ninesync_fx.runtime.middle.targets import apply, remove, DEFORM, MATERIAL
from ninesync_fx.runtime.middle.vertex import VERTEX_CORE, stage

UNITS = 0.08
FRAMES = (0, 10, 30)


def mesh_values(scene, obj, frame):
    scene.frame_set(frame)
    deps = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(deps)
    mesh = evaluated.to_mesh()
    try:
        positions = np.empty(len(mesh.vertices)*3, dtype=np.float32)
        normals = np.empty_like(positions)
        mesh.vertices.foreach_get("co", positions)
        mesh.vertices.foreach_get("normal", normals)
        return positions.reshape(-1, 3).copy(), normals.reshape(-1, 3).copy()
    finally:
        evaluated.to_mesh_clear()


def reference(scene, obj, program, positions, normals, image):
    """Execute the unchanged HLSL with the independent NumPy backend."""
    state = FxSceneState()
    swap = np.eye(4)[:, (0, 2, 1, 3)]
    state.world = swap @ np.array(obj.matrix_world).T @ swap
    state.world[3, :3] /= UNITS
    camera = scene.camera
    eye = np.array(camera.matrix_world.translation)[[0, 2, 1]] / UNITS
    forward = np.array(camera.matrix_world.to_3x3() @ Vector((0, 0, -1)))[[0, 2, 1]]
    up = np.array(camera.matrix_world.to_3x3() @ Vector((0, 1, 0)))[[0, 2, 1]]
    state.view = fx_lh_look(eye, forward, up)
    projection = np.array(camera.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),
        x=scene.render.resolution_x, y=scene.render.resolution_y,
        scale_x=scene.render.pixel_aspect_x, scale_y=scene.render.pixel_aspect_y))
    orthographic = camera.data.type == "ORTHO"
    xs, ys = projection[0, 0], projection[1, 1]
    if orthographic:
        xs, ys = xs * UNITS, ys * UNITS
    q = FX_FAR / (FX_FAR - FX_NEAR)
    state.proj = np.array(((xs, 0, 0, 0), (0, ys, 0, 0),
        (0 if orthographic else -projection[0, 2], 0 if orthographic else -projection[1, 2],
         1/(FX_FAR-FX_NEAR) if orthographic else q, 0 if orthographic else 1),
        (projection[0, 3] if orthographic else 0, projection[1, 3] if orthographic else 0,
         -FX_NEAR/(FX_FAR-FX_NEAR) if orthographic else -FX_NEAR*q, 1 if orthographic else 0)))
    state.time = scene.frame_current * scene.render.fps_base / scene.render.fps
    width, height = image.size
    pixels = np.array(image.pixels[:], dtype=np.float64).reshape(height, width, 4)[::-1].copy()
    # Blender's file image pixel API exposes the original encoded texels.
    texture = FxImage(pixels, image.filepath)

    class Inputs:
        def global_value(self, ex, global_value):
            source = fx_classify_global(global_value, program)
            if source is None or source[0] == "param":
                return None
            return fx_value_from_list(ex, state.values(source, global_value), fx_resolve_type(global_value.type, program))

        def sample(self, ex, sampler, u, v, lod):
            sampled = texture.sample(u, v, False, "WRAP")
            return sampled

    def inputs(semantic, type_value):
        if semantic == "POSITION0":
            values = list((positions[:, (0, 2, 1)] / UNITS).T) + [np.ones(len(positions))]
        elif semantic == "NORMAL0":
            values = list(normals[:, (0, 2, 1)].T)
        else:
            raise AssertionError(semantic)
        return FxValue(("f", (len(values),)), values)

    technique = next(technique for technique in program.techniques if technique.name == "MainTec0")
    function, args = fx_entry(program, technique.passes[0].vs)
    output = fx_run_entry(FxExec(program, FxNumpyBackend(), Inputs()), function, args, inputs)["POSITION0"]
    clip = np.stack([np.broadcast_to(component, len(positions)) for component in output.c], axis=1)
    local = clip @ np.linalg.inv(state.world @ state.view @ state.proj)
    local = local[:, :3] / local[:, 3:]
    return local[:, (0, 2, 1)] * UNITS


def run(args):
    pmx, importer = load_importer(args.mmd_tools)
    scene = set_scene()
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = scene.render.resolution_y = 512
    scene.render.fps = 30
    scene.render.film_transparent = True
    camera = scene.camera
    camera.data.type = "PERSP"
    camera.data.sensor_fit = "VERTICAL"
    camera.data.lens = camera.data.sensor_height / (2 * math.tan(math.radians(30)/2))
    camera.data.clip_start = 0.001
    camera.data.clip_end = 1000
    model_file, fx_file = args.root / "FireBall.pmx", args.root / "FireBall.fx"
    hashes = {path.name: sha256(path.read_bytes()).hexdigest() for path in
              (model_file, fx_file, args.root / "FireBall.png", args.root / "Pallet.png")}
    model = pmx.load(str(model_file))
    importer().execute(pmx=model, scale=UNITS, types={"MESH", "ARMATURE", "MORPHS"}, fix_bone_order=False)
    obj = model_object(scene, model)
    source = obj.material_slots[0].material
    source_image = source.node_tree.nodes["mmd_base_tex"].image
    baseline_snapshot = snapshot(obj)
    for frame, amount in ((0, 0.0), (10, 0.1), (30, 0.0)):
        camera.location = (amount, -2.8, amount * 0.5)
        camera.rotation_euler = (-camera.location).to_track_quat("-Z", "Y").to_euler()
        camera.keyframe_insert("location", frame=frame)
        camera.keyframe_insert("rotation_euler", frame=frame)
        obj.rotation_euler.z = amount
        obj.keyframe_insert("rotation_euler", frame=frame)
        keys = obj.data.shape_keys
        if keys and len(keys.key_blocks) > 1:
            keys.key_blocks[1].value = amount
            keys.key_blocks[1].keyframe_insert("value", frame=frame)
    baseline = {frame: mesh_values(scene, obj, frame) for frame in FRAMES}
    scene.frame_set(0)
    program = fx_parse_file(str(fx_file))
    expected = {}
    for frame in FRAMES:
        scene.frame_set(frame)
        expected[frame] = reference(scene, obj, program, *baseline[frame], source_image)
    result = apply(obj, fx_file, DEFORM, units=UNITS)
    assert result.core["ninesync_middle_recovered_position"]
    reports = []
    actual = {}
    for frame in FRAMES:
        values, _ = mesh_values(scene, obj, frame)
        actual[frame] = values
        error = float(np.abs(values-expected[frame]).max())
        displacement = float(np.linalg.norm(values-baseline[frame][0], axis=1).max())
        assert error < 1e-4, (frame, error)
        assert displacement > 0.005, displacement
        reports.append({"frame": frame, "reference_max_error": error, "maximum_displacement": displacement})
    assert float(np.abs((actual[10]-baseline[10][0])-(actual[0]-baseline[0][0])).max()) > 0.005
    for camera_type in ("PERSP", "ORTHO"):
        camera.data.type = camera_type
        camera.data.ortho_scale = 1.4
        camera.data.shift_x, camera.data.shift_y = 0.07, -0.03
        values, _ = mesh_values(scene, obj, 0)
        shifted = reference(scene, obj, program, *baseline[0], source_image)
        error = float(np.abs(values-shifted).max())
        assert error < 1e-4, (camera_type, error)
        reports.append({"camera": camera_type, "shifted_projection_max_error": error})
    camera.data.type = "PERSP"
    camera.data.shift_x = camera.data.shift_y = 0.0
    original_core = result.core
    assert not apply(obj, fx_file, DEFORM, units=UNITS).rebuilt
    args.output.mkdir(parents=True, exist_ok=True)
    invalid = args.output / "invalid-final-clip.fx"
    text = fx_file.read_bytes().decode("cp932")
    assert "Out.Pos = mul(Pos,WorldViewProjMatrix);" in text
    invalid.write_text(text.replace("Out.Pos = mul(Pos,WorldViewProjMatrix);",
        "Out.Pos = mul(Pos,WorldViewProjMatrix) + float4(0,0,0.1,0);"), encoding="utf-8")
    groups = len(bpy.data.node_groups)
    try:
        apply(obj, invalid, DEFORM, units=UNITS)
    except Exception as error:
        assert "proven final" in str(error), str(error)
    else:
        raise AssertionError("A changed clip-space position was accepted.")
    assert len(bpy.data.node_groups) == groups
    assert original_core in bpy.data.node_groups[:]
    clip_xy = args.output / "clip-xy.fx"
    clip_xy.write_text(text.replace("Out.Pos = mul(Pos,WorldViewProjMatrix);",
        "Out.Pos = mul(Pos,WorldViewProjMatrix); Out.Pos.xy += float2(0.12,-0.08);"), encoding="utf-8")
    clip_program = fx_parse_file(str(clip_xy))
    xy_result = apply(obj, clip_xy, DEFORM, units=UNITS)
    assert any("XY clip" in warning for warning in xy_result.warnings)
    for camera_type in ("PERSP", "ORTHO"):
        camera.data.type = camera_type
        camera.data.shift_x, camera.data.shift_y = 0.05, -0.02
        scene.frame_set(10)
        wanted = reference(scene, obj, clip_program, *baseline[10], source_image)
        values, _ = mesh_values(scene, obj, 10)
        error = float(np.abs(values-wanted).max())
        assert error < 1e-4, (camera_type, error)
        reports.append({"camera": camera_type, "clip_xy_max_error": error})
    camera.data.type = "PERSP"
    camera.data.shift_x = camera.data.shift_y = 0.0
    rebuilt = apply(obj, fx_file, DEFORM, units=UNITS, force_rebuild=True)
    assert len(bpy.data.node_groups) == groups
    assert float(np.abs(mesh_values(scene, obj, 0)[0]-actual[0]).max()) < 1e-6
    bpy.ops.file.pack_all()
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "deformation.blend"), relative_remap=False)
    # The full material adapter must not silently apply the same displacement twice.
    try:
        apply(obj, fx_file, MATERIAL, units=UNITS)
    except Exception as error:
        assert "standalone deformation" in str(error), str(error)
    else:
        raise AssertionError("The same standalone/full FX deformation was duplicated.")
    assert len(bpy.data.node_groups) == groups
    # An artist edit belongs outside the generated core and must survive its rebuild.
    edit = next(node.node_tree for node in rebuilt.destination.node_group.nodes
                if node.get("ninesync_middle_edit_node"))
    entry = next(node for node in edit.nodes if node.type == "GROUP_INPUT")
    output = next(node for node in edit.nodes if node.type == "GROUP_OUTPUT")
    offset = edit.nodes.new("GeometryNodeSetPosition")
    offset.inputs["Offset"].default_value = (0, 0, 0.01)
    edit.links.new(entry.outputs["Geometry"], offset.inputs["Geometry"])
    edit.links.new(offset.outputs["Geometry"], output.inputs["Geometry"])
    apply(obj, fx_file, DEFORM, units=UNITS, force_rebuild=True)
    edited, _ = mesh_values(scene, obj, 0)
    assert float(np.abs(edited-actual[0]-np.array((0, 0, 0.01))).max()) < 1e-6
    remove(obj, DEFORM)
    assert edit.use_fake_user
    bpy.data.node_groups.remove(edit)
    assert snapshot(obj) == baseline_snapshot
    assert float(np.abs(mesh_values(scene, obj, 0)[0]-baseline[0][0]).max()) == 0
    material = apply(obj, fx_file, MATERIAL, units=UNITS)
    assert material.core[VERTEX_CORE]["ninesync_middle_vertex_deforms"]
    material_groups = len(bpy.data.node_groups)
    try:
        apply(obj, fx_file, DEFORM, units=UNITS)
    except Exception as error:
        assert "material vertex deformation" in str(error), str(error)
    else:
        raise AssertionError("The full/standalone FX deformation was duplicated.")
    assert len(bpy.data.node_groups) == material_groups
    material_positions = mesh_values(scene, obj, 0)[0]
    assert float(np.abs(material_positions-actual[0]).max()) < 1e-6
    first = render_image(scene, args.output, "fireball", 0)
    animated = render_image(scene, args.output, "fireball-animated", 10)
    assert float(np.abs(first-animated).mean()) > 0.01
    bpy.ops.file.pack_all()
    bpy.context.preferences.filepaths.save_version = 0
    scene.frame_set(0)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "fireball.blend"), relative_remap=False)
    remove(obj, MATERIAL)
    assert stage(obj) is None and snapshot(obj) == baseline_snapshot
    assert obj.material_slots[0].material == source
    bpy.ops.wm.open_mainfile(filepath=str(args.output / "fireball.blend"), load_ui=False)
    reopened = render_image(bpy.context.scene, args.output, "fireball-reopened", 0)
    assert float(np.abs(reopened-first).mean()) < 1e-5
    bpy.ops.wm.open_mainfile(filepath=str(args.output / "deformation.blend"), load_ui=False)
    reopened_obj = model_object(bpy.context.scene, model)
    standalone_error = float(np.abs(mesh_values(bpy.context.scene, reopened_obj, 0)[0]-actual[0]).max())
    assert standalone_error < 1e-6
    for name, digest in hashes.items():
        assert sha256((args.root / name).read_bytes()).hexdigest() == digest
    (args.output / "results.json").write_text(json.dumps({"source": str(fx_file), "hashes": hashes,
        "vertices": len(model.vertices), "checks": reports, "standalone_matches_full_material": True,
        "rollback_verified": True, "duplicate_deformation_rejected": True,
        "artist_stage_preserved": True, "standalone_reopen_max_error": standalone_error,
        "reopen_mean_error": float(np.abs(reopened-first).mean()), "status": "PARTIAL"}, indent=2), encoding="utf-8")
    print("PASS ORIGINAL_FIREBALL", json.dumps(reports), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--mmd-tools", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
