"""Render the toon converter on a user-supplied PMX with its original materials."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter

import bpy
from mathutils import Vector
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t_middle_converters import MATERIAL, add_empty, apply, remove, set_scene


TOON = Path(__file__).resolve().parent / "fixtures" / "middle" / "pmx_toon.fx"


def load_importer(tool_dir):
    root = tool_dir.parent.parent
    pyver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    sys.path[:0] = [str(tool_dir.parent), str(root / ".local" / "lib" / pyver / "site-packages")]
    assert bpy.ops.preferences.addon_enable(module="mmd_tools") == {"FINISHED"}
    import mmd_tools
    from mmd_tools.core import pmx
    from mmd_tools.core.pmx.importer import PMXImporter

    shared_toon = tool_dir / "externals" / "MikuMikuDance"
    if (shared_toon / "toon01.bmp").is_file():
        bpy.context.preferences.addons["mmd_tools"].preferences.shared_toon_folder = str(shared_toon)
    print("MMD Tools", mmd_tools.MMD_TOOLS_VERSION, flush=True)
    return pmx, PMXImporter


def resolve_textures(model, model_file, texture_dir):
    relinked = []
    for texture in model.textures:
        path = Path(texture.path)
        path = path if path.is_absolute() else model_file.parent / path
        if path.is_file():
            continue
        candidate = texture_dir / path.name if texture_dir else None
        if candidate is not None and candidate.is_file():
            texture.path = str(candidate)
            relinked.append((str(path), str(candidate)))
        else:
            print("Unresolved original texture:", path, flush=True)
    return relinked


def model_object(scene, model):
    candidates = [obj for obj in scene.objects if obj.type == "MESH"
                  and len(obj.data.polygons) == len(model.faces)]
    assert len(candidates) == 1, "Could not identify the imported PMX mesh."
    return candidates[0]


def position_camera(scene, obj):
    coords = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", coords)
    matrix = np.array(obj.matrix_world)
    points = coords.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
    low = points.min(axis=0)
    high = points.max(axis=0)
    center = Vector((low + high) * 0.5)
    distance = float(np.linalg.norm(high - low))
    facing = Vector((0.28, -1, 0.10)).normalized()
    rotation = (-facing).to_track_quat("-Z", "Y")
    camera = scene.camera
    camera.rotation_euler = rotation.to_euler()
    right, up = (rotation @ Vector(axis) for axis in [(1, 0, 0), (0, 1, 0)])
    x, y = points @ np.array(right), points @ np.array(up)
    center += right * (float(x.min() + x.max()) * 0.5 - right.dot(center))
    center += up * (float(y.min() + y.max()) * 0.5 - up.dot(center))
    camera.location = center + facing * distance * 2.4
    camera.data.ortho_scale = float(max(x.max() - x.min(), y.max() - y.min()) * 1.13)
    camera.data.clip_end = max(distance * 12, 10)
    return center, distance


def add_area(scene, name, center, span, position, energy):
    light = bpy.data.lights.new(name, "AREA")
    light.energy = energy * span ** 2
    light.shape = "DISK"
    light.size = span * 0.7
    obj = bpy.data.objects.new(name, light)
    scene.collection.objects.link(obj)
    obj.location = center + Vector(position) * span
    obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()


def render_image(scene, output, stem, frame):
    scene.frame_set(frame)
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "32"
    scene.render.filepath = str(output / (stem + ".exr"))
    window = bpy.context.window
    previous = window.scene if window else None
    if window:
        window.scene = scene
    try:
        scene.frame_set(frame)
        bpy.ops.render.render(write_still=True, scene=scene.name)
    finally:
        if window:
            window.scene = previous
    image = bpy.data.images.load(scene.render.filepath, check_existing=False)
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    width, height = image.size
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "8"
    image.save_render(str(output / (stem + ".png")), scene=scene)
    bpy.data.images.remove(image)
    assert (output / (stem + ".png")).read_bytes()[:8] == bytes((137, 80, 78, 71, 13, 10, 26, 10))
    return pixels.reshape(height, width, 4)


def snapshot(obj):
    rig = obj.find_armature()
    data = obj.data
    polygon_indices = np.empty(len(data.polygons), dtype=np.int32)
    data.polygons.foreach_get("material_index", polygon_indices)
    return (
        data, rig.data if rig else None, tuple(rig.data.bones.keys()) if rig else (),
        tuple(mod for mod in obj.modifiers if not (mod.type == "NODES" and mod.node_group
                  and mod.node_group.get("ninesync_middle_vertex_owner"))), tuple(data.materials),
        data.shape_keys, tuple(data.shape_keys.key_blocks.keys()) if data.shape_keys else (),
        tuple(data.uv_layers.keys()), polygon_indices.tobytes(),
    )


def run(args):
    pmx, PMXImporter = load_importer(args.mmd_tools.resolve())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = args.model.resolve(strict=True)
    model_hash = sha256(path.read_bytes()).hexdigest()
    model = pmx.load(str(path))
    relinked = resolve_textures(model, path, args.texture_dir)
    for old, new in relinked:
        print("Real texture resolved:", old, "->", new, flush=True)
    scene = set_scene()
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = scene.render.resolution_y = args.resolution
    scene.world.color = (0.065, 0.065, 0.065)
    scene.render.film_transparent = True
    bpy.context.preferences.filepaths.save_version = 0
    PMXImporter().execute(pmx=model, scale=args.scale, types={"MESH", "ARMATURE", "MORPHS", "DISPLAY"},
                          fix_bone_order=False)
    obj = model_object(scene, model)
    baseline = snapshot(obj)
    slots = tuple(slot.material for slot in obj.material_slots)
    indices = tuple(dict.fromkeys(args.slots))
    assert all(0 <= i < len(slots) and slots[i] for i in indices)
    center, span = position_camera(scene, obj)
    add_area(scene, "Key light", center, span, (-0.7, -1, 1), 16)
    add_area(scene, "Fill light", center, span, (1, -0.35, 0.5), 4)
    source = add_empty(scene, "FX light direction", (-0.65, -1, 0.85))
    control = add_empty(scene, "MME Control.x", (args.scale, 0.0, 0.0))
    for frame, value in ((1, 1.0), (12, 0.68)):
        control.location.x = args.scale * value
        control.keyframe_insert("location", index=0, frame=frame)
    scene.frame_end = 12

    before = render_image(scene, output, "before", 1)
    start = perf_counter()
    results = {}
    for i in indices:
        results[i] = apply(obj, TOON, MATERIAL, slot=i, light=source, units=args.scale)
    seconds = perf_counter() - start
    assert snapshot(obj) == baseline
    assert all((slot.material != slots[i]) == (i in results) for i, slot in enumerate(obj.material_slots))
    for i, result in results.items():
        assert not apply(obj, TOON, MATERIAL, slot=i, light=source, units=args.scale).rebuilt
        assert result.destination == obj.material_slots[i].material
        assert any("mmd_material.diffuse_color[0]" == target.data_path
                   for curve in result.core.animation_data.drivers for variable in curve.driver.variables
                   for target in variable.targets)

    after = render_image(scene, output, "toon", 1)
    driven = render_image(scene, output, "toon-controller-frame12", 12)
    visible = (before[:, :, 3] > 0.9) & (after[:, :, 3] > 0.9) & (driven[:, :, 3] > 0.9)
    assert visible.sum() > 2500, "Too few opaque model pixels for comparison."
    look_delta = float(np.abs(after[visible, :3] - before[visible, :3]).mean())
    driver_delta = float(np.abs(driven[visible, :3] - after[visible, :3]).mean())
    assert look_delta > 0.015 and driver_delta > 0.004, (look_delta, driver_delta)
    bpy.ops.file.pack_all()
    scene.frame_set(1)
    scene.render.filepath = "//"
    for selected in list(bpy.context.selected_objects):
        selected.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.region_3d.view_perspective = "CAMERA"
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "toon.blend"), relative_remap=False)
    for i in indices:
        remove(obj, MATERIAL, slot=i)
    assert snapshot(obj) == baseline
    assert tuple(slot.material for slot in obj.material_slots) == slots
    restored = render_image(scene, output, "restored", 1)
    restoration_delta = float(np.abs(restored - before).mean())
    assert restoration_delta < 1e-5, restoration_delta
    assert sha256(path.read_bytes()).hexdigest() == model_hash
    result = {
        "model": str(path), "name": model.name, "source_sha256": model_hash,
        "fx": str(TOON), "fx_sha256": sha256(TOON.read_bytes()).hexdigest(),
        "fx_include_sha256": sha256(TOON.with_name("model_toon.fx").read_bytes()).hexdigest(),
        "vertices": len(obj.data.vertices), "polygons": len(obj.data.polygons),
        "bones": len(baseline[2]), "shape_keys": len(baseline[6]),
        "converted_slots": {str(i): slots[i].name for i in indices},
        "textures_relinked": relinked, "apply_seconds": seconds,
        "toon_mean_rgb_delta": look_delta, "controller_mean_rgb_delta": driver_delta,
        "restore_mean_rgba_delta": restoration_delta,
    }
    bpy.ops.wm.open_mainfile(filepath=str(output / "toon.blend"), load_ui=False)
    reopened = render_image(bpy.context.scene, output, "toon-reopened", 1)
    reopen_delta = float(np.abs(reopened - after).mean())
    assert reopen_delta < 1e-5, reopen_delta
    result["reopen_mean_rgba_delta"] = reopen_delta
    (output / "evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS REAL-PMX-TOON", json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mmd-tools", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--texture-dir", type=Path)
    parser.add_argument("--slots", required=True, nargs="+", type=int)
    parser.add_argument("--scale", default=0.08, type=float)
    parser.add_argument("--resolution", default=800, type=int)
    run(parser.parse_args(sys.argv[sys.argv.index("--") + 1:]))
