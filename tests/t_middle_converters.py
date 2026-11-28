"""Headless integration and evidence for the three experimental MME destinations."""

import argparse
import os
from pathlib import Path
import sys
from time import perf_counter

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths

os.environ.setdefault("NINESYNC_FX", str(Path(__file__).resolve().parents[1]))
paths.prepare("ninesync_fx")

from ninesync_fx.fx.errors import FxError
from ninesync_fx.fx.loader import fx_parse_file
from ninesync_fx.runtime.middle.analysis import analyze
from ninesync_fx.runtime.middle.targets import DEFORM, MATERIAL, apply, inspect_bindings, remove, retarget


FIX = Path(__file__).resolve().parent / "fixtures"
TOON = FIX / "middle" / "model_toon.fx"
WAVE = FIX / "middle" / "model_wave.fx"
SCREEN = FIX / "middle" / "screen.fx"
FRAMES = (1, 10, 20)
CONTROL = {1: 0.25, 10: 0.5, 20: 0.9}
BONE = {1: 0.0, 10: 0.2, 20: -0.1}
MORPH = {1: 0.0, 10: 0.35, 20: 0.65}
BASE = np.array([0.8, 0.6, 0.4])
PIXEL = np.array([0.625, 0.5, 0.75])
tests = []


def passed(key, detail):
    tests.append(key)
    print(f"PASS {key} {detail}", flush=True)


def reject(action, expected):
    try:
        action()
    except (FxError, OSError) as error:
        assert expected.lower() in str(error).lower(), str(error)
        return str(error)
    raise AssertionError("Expected rejection: " + expected)


def set_scene():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.render.resolution_x, scene.render.resolution_y = 96, 48
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    scene.render.fps = 25
    scene.render.fps_base = 1.0
    scene.frame_end = 20
    scene.world.color = (0.0, 0.0, 0.0)
    data = bpy.data.cameras.new("Test camera")
    data.type = "ORTHO"
    data.ortho_scale = 3.8
    camera = bpy.data.objects.new("Test camera", data)
    scene.collection.objects.link(camera)
    camera.location = (0.0, 0.0, 6.0)
    scene.camera = camera
    return scene


def add_empty(scene, name, xyz=(0.0, 0.0, 0.0)):
    obj = bpy.data.objects.new(name, None)
    scene.collection.objects.link(obj)
    obj.location = xyz
    return obj


def base_material(name, color):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    tree = mat.node_tree
    for node in list(tree.nodes):
        if node.type == "BSDF_PRINCIPLED":
            tree.nodes.remove(node)
    shade = tree.nodes.new("ShaderNodeEmission")
    shade.inputs["Color"].default_value = (*color, 1.0)
    tree.links.new(shade.outputs[0], tree.nodes.get("Material Output").inputs["Surface"])
    return mat


def make_model(scene):
    data = bpy.data.meshes.new("Original mesh")
    data.from_pydata([(-1, -0.65, 0), (0, -0.65, 0), (1, -0.65, 0),
                      (-1, 0.65, 0), (0, 0.65, 0), (1, 0.65, 0)],
                     [], [(0, 1, 4, 3), (1, 2, 5, 4)])
    obj = bpy.data.objects.new("Demo model", data)
    scene.collection.objects.link(obj)
    original = [base_material("Keep material", (0.1, 0.2, 0.35)), base_material("Original material", BASE)]
    data.materials.append(original[0])
    data.materials.append(original[1])
    data.polygons[1].material_index = 1
    uv = data.uv_layers.new(name="Original UV")
    for polygon in data.polygons:
        for index, coords in zip(polygon.loop_indices, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            uv.uv[index].vector = coords
    pixels = bpy.data.images.new("Original base image", 2, 2, alpha=True, float_buffer=True)
    pixels.pixels = [0.25, 0.75, 0.5, 1.0] * 2 + [*PIXEL, 1.0] * 2
    pixels.pack()
    node = original[1].node_tree.nodes.new("ShaderNodeTexImage")
    node.name = "mmd_base_tex"
    node.image = pixels
    basis = obj.shape_key_add(name="Basis")
    lift = obj.shape_key_add(name="Lift")
    for source, point in zip(basis.data, lift.data):
        point.co.z = source.co.z + 0.18
    for frame, value in MORPH.items():
        lift.value = value
        lift.keyframe_insert("value", frame=frame)

    arm = bpy.data.armatures.new("Original armature")
    rig = bpy.data.objects.new("Demo Rig", arm)
    scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for name, x in (("Main", 0.0), ("Signal", 0.0)):
        bone = arm.edit_bones.new(name)
        bone.head, bone.tail = (x, 0, 0), (x, 1, 0)
        bone.use_deform = name == "Main"
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.select_set(False)
    for frame, value in BONE.items():
        main = rig.pose.bones["Main"]
        main.location.x = 0.1 + value * 0.2
        main.rotation_mode = "XYZ"
        main.rotation_euler.y = value * 0.25
        main.keyframe_insert("location", frame=frame)
        main.keyframe_insert("rotation_euler", frame=frame)
        signal = rig.pose.bones["Signal"]
        signal.location.z = value
        signal.keyframe_insert("location", frame=frame)
    group = obj.vertex_groups.new(name="Main")
    group.add(range(len(data.vertices)), 1.0, "REPLACE")
    modifier = obj.modifiers.new("Original skinning", "ARMATURE")
    modifier.object = rig
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    # Exercise an object-local override while a second object shares the underlying mesh and materials.
    sibling = bpy.data.objects.new("Linked model", data)
    scene.collection.objects.link(sibling)
    sibling.hide_render = True
    sibling.hide_viewport = True
    return obj, rig, original, sibling


def vertices(scene, obj, frame):
    scene.frame_set(frame)
    deps = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(deps)
    mesh = evaluated.to_mesh()
    try:
        return np.array([tuple(v.co) for v in mesh.vertices])
    finally:
        evaluated.to_mesh_clear()


def assert_near(actual, expected, limit=2e-5):
    error = float(np.max(np.abs(np.array(actual) - np.array(expected))))
    assert error < limit, f"error {error} > {limit}, actual={actual}, expected={expected}"
    return error


def wave_expected(scene, positions, frame, control, amplitude=0.2, frequency=1.25, offset=0.0):
    expected = positions.copy()
    expected[:, 2] += offset + amplitude * control * (1 + BONE[frame] + MORPH[frame]) * np.sin(
        (positions[:, 0] - control) * frequency + frame / scene.render.fps
    )
    return expected


def render(scene, path, frame):
    scene.frame_set(frame)
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "32"
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path), check_existing=False)
    data = np.array(image.pixels[:]).reshape(48, 96, 4)
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "8"
    image.save_render(str(path.with_suffix(".png")), scene=scene)
    bpy.data.images.remove(image)
    return data


def swatch(image):
    return image[32, 61, :3]


def shade(result):
    return next(n for n in result.destination.node_tree.nodes if n.type == "GROUP" and n.node_tree == result.core)


def modifier_port(mod, name):
    key = next(i.identifier for i in mod.node_group.interface.items_tree if i.in_out == "INPUT" and i.name == name)
    return getattr(mod.properties.inputs, key)


def owned_counts():
    groups = [g for g in bpy.data.node_groups if g.get("ninesync_middle_token")]
    material = [m for m in bpy.data.materials if m.get("ninesync_middle_token")]
    drivers = sum(len(g.animation_data.drivers) for g in groups if g.animation_data)
    return len(material), len(groups), drivers, len(bpy.data.meshes), len(bpy.data.objects)


def elapsed(action, repeats):
    start = perf_counter()
    for _ in range(repeats):
        action()
    return 1000 * (perf_counter() - start) / repeats


def test_pack(obj, path, light):
    if not path:
        print("SKIP CORPUS MMDShaderTemplate path not supplied")
        return
    analysis = analyze(path, subset=0)
    signature = owned_counts()
    message = reject(lambda: apply(obj, path, MATERIAL, slot=0, light=light), "TEXCOORD1")
    assert owned_counts() == signature
    print("CORPUS", path.name, analysis.targets, analysis.problems, message)


def run(output, corpus):
    output.mkdir(parents=True, exist_ok=True)
    scene = set_scene()
    bpy.context.preferences.filepaths.save_version = 0
    handler_names = tuple(map(len, (bpy.app.handlers.frame_change_post, bpy.app.handlers.depsgraph_update_post)))
    obj, rig, original, sibling = make_model(scene)
    data, keys = obj.data, obj.data.shape_keys
    original_arm = obj.modifiers[0]
    control = add_empty(scene, "MME Control")
    light = add_empty(scene, "Direction source", (0, 0, 2))
    alternative = add_empty(scene, "Other control", (0.45, 0, 0))
    for frame, value in CONTROL.items():
        control.location.x = value
        control.keyframe_insert("location", index=0, frame=frame)
    baseline = {frame: vertices(scene, obj, frame) for frame in FRAMES}
    assert_near(baseline[1][1], (0.1, -0.65, 0.0))
    assert abs(baseline[20][1, 2] - baseline[1][1, 2]) > 0.05
    scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "model-before.blend"))
    before = render(scene, output / "material-before.exr", 1)
    assert_near(swatch(before), BASE)
    generic = analyze(FIX / "Synth" / "synth.fx")
    assert "ACCESSORY" in generic.targets and SCREEN.is_file()
    assert analyze(WAVE).local_position == "TEXCOORD7"
    assert MATERIAL not in analyze(TOON, 0).targets
    assert "SCREEN" in analyze(SCREEN).targets
    assert {ref[1] for ref in analyze(WAVE).controller_refs} == {"MME Control", "Demo Rig", "(self)"}
    passed("A-01", "Parsed subset, existing-mesh output, controller annotations, accessory pair and screen requirements.")

    test_pack(obj, corpus, light)
    count = owned_counts()
    start = perf_counter()
    result = apply(obj, TOON, MATERIAL, slot=1, light=light)
    material_ms = 1000 * (perf_counter() - start)
    material, core = result.destination, result.core
    assert material != original[1] and obj.material_slots[0].material == original[0]
    assert obj.material_slots[1].link == "OBJECT" and obj.data.materials[1] == original[1]
    assert sibling.material_slots[1].material == original[1]
    assert owned_counts()[-2:] == count[-2:]
    assert len(core.nodes) > 10
    node = shade(result)
    assert set(socket.name for socket in node.inputs) == {"Intensity", "Threshold"}
    for frame in (1, 10, 20, 1):
        rgba = render(scene, output / f"material-frame-{frame:02}.exr", frame)
        expected = BASE * PIXEL * 0.8 * CONTROL[frame]
        assert_near(swatch(rgba), expected, 0.007)
        assert_near(rgba[15, 61, :3], BASE * (0.25, 0.75, 0.5) * 0.8 * CONTROL[frame], 0.007)
        assert_near(rgba[32, 39, :3], (0.1, 0.2, 0.35), 0.007)
    passed("M-01", f"Slot 1 only. Linked mesh and slot 0 retain originals. Core has {len(core.nodes)} nodes, Cycles validates two UV texels.")
    passed("C-01", "Empty X drives rendered RGB at frames 1, 10, 20 and reverse to 1.")
    node.inputs["Intensity"].default_value = 0.4
    node.inputs["Threshold"].default_value = 1.2
    rgba = render(scene, output / "material-edited.exr", 20)
    assert_near(swatch(rgba), BASE * PIXEL * 0.4 * 0.9 * 0.25, 0.007)
    node.inputs["Threshold"].default_value = 0.5
    passed("M-02", f"Intensity and directional band change rendered RGB to {swatch(rgba).tolist()}.")
    assert_near(vertices(scene, obj, 20), baseline[20])
    assert obj.data == data and data.shape_keys == keys and rig.pose.bones.get("Main")
    passed("M-03", "Animated skin and shape-key positions match the pre-import mesh.")

    geometry_camera = scene.camera
    geometry_camera.location = (2.2, -3.5, 0.75)
    geometry_camera.rotation_euler = (-geometry_camera.location).to_track_quat("-Z", "Y").to_euler()
    original_view = render(scene, output / "wave-before.exr", 20)
    baseline_steps = iter((1, 10, 20) * 20)
    baseline_ms = elapsed(lambda: vertices(scene, obj, next(baseline_steps)), 60)
    start = perf_counter()
    deformation = apply(obj, WAVE, DEFORM)
    geom_ms = 1000 * (perf_counter() - start)
    mod = deformation.destination
    assert list(obj.modifiers) == [original_arm, mod] and data.materials[1] == original[1]
    assert data.uv_layers[0].name == "Original UV" and [p.material_index for p in data.polygons] == [0, 1]
    amp = modifier_port(mod, "Amplitude")
    freq = modifier_port(mod, "Frequency")
    assert abs(amp.value - 0.2) < 1e-6
    errors = [assert_near(vertices(scene, obj, f), wave_expected(scene, baseline[f], f, CONTROL[f]))
              for f in (*FRAMES, 10, 1)]
    assert max(abs(vertices(scene, obj, 20)[:, 2] - baseline[20][:, 2])) > 0.1
    wave_view = render(scene, output / "wave-after.exr", 20)
    assert float(np.mean(np.abs(wave_view - original_view))) > 0.005
    geometry_camera.location = (0.0, 0.0, 6.0)
    geometry_camera.rotation_euler = (0.0, 0.0, 0.0)
    passed("G-01", f"Live Set Position on the original skinned mesh. Max position error {max(errors):.3g}.")
    passed("G-03", "Default post-armature order. Original mesh, UVs, indices, rig, morph and slots preserved.")
    passed("C-02", "Empty translation changes the GN amplitude and pivot at all keyed frames.")
    passed("C-03", "Wave gain matches the keyed world-space bone signal and original shape key at all keyed frames.")
    amp.value = 0.0
    obj.update_tag()
    assert_near(vertices(scene, obj, 20), baseline[20])
    obj.keyframe_insert(data_path=amp.path_from_id("value"), frame=20)
    amp.value = 0.35
    freq.value = 2.5
    obj.keyframe_insert(data_path=amp.path_from_id("value"), frame=1)
    obj.update_tag()
    assert_near(vertices(scene, obj, 1), wave_expected(scene, baseline[1], 1, CONTROL[1], 0.35, 2.5))
    assert_near(vertices(scene, obj, 20), baseline[20])
    obj.keyframe_delete(data_path=amp.path_from_id("value"), frame=1)
    obj.keyframe_delete(data_path=amp.path_from_id("value"), frame=20)
    amp.value, freq.value = 0.2, 1.25
    obj.update_tag()
    passed("G-02", "Zero and keyed amplitudes, frequency edit and backward scrubbing agree with the numerical reference.")

    stable = owned_counts()
    reject(lambda: apply(obj, SCREEN, MATERIAL, slot=1), "render-target")
    reject(lambda: apply(obj, SCREEN, DEFORM), "render-target")
    invalid = output / "invalid-reimport.fx"
    invalid.write_text(TOON.read_text(encoding="utf-8-sig").replace("tex2D(Base, v.uv)", "MissingOperation(v.uv)"),
                       encoding="utf-8")
    reject(lambda: apply(obj, invalid, MATERIAL, slot=1, light=light), "MissingOperation")
    missing = bpy.data.images.new("Missing test image", 1, 1)
    missing.source = "FILE"
    missing.filepath = str(output / "missing.png")
    reject(lambda: apply(obj, TOON, MATERIAL, slot=1, image=missing, light=light), "Base image file is missing")
    bpy.data.images.remove(missing)
    assert owned_counts() == stable and node.node_tree == core
    passed("M-05", "Screen shader, missing image and compile error leave the prior effect and owned counts intact.")

    impostor = add_empty(scene, "Duplicate imported controller")
    impostor["ninesync_mme_name"] = control.name
    reject(lambda: apply(obj, TOON, MATERIAL, slot=1, light=light, force_rebuild=True,
                         bindings={control.name: None}), "outside")
    # Ignore the stored mapping on a first import and confirm explicit ambiguity.
    other = obj.copy()
    other.data = data
    scene.collection.objects.link(other)
    other.material_slots[1].link = "DATA"
    reject(lambda: apply(other, TOON, MATERIAL, slot=1, light=light), "ambiguous")
    bpy.data.objects.remove(other, do_unlink=True)
    bpy.data.objects.remove(impostor, do_unlink=True)
    control.name = "Renamed controller"
    assert "stored object" in inspect_bindings(obj, MATERIAL, slot=1)[0]
    assert_near(vertices(scene, obj, 20), wave_expected(scene, baseline[20], 20, CONTROL[20]))
    bpy.data.objects.remove(control, do_unlink=True)
    stable = owned_counts()
    assert "missing" in inspect_bindings(obj, DEFORM)[0]
    reject(lambda: apply(obj, TOON, MATERIAL, slot=1, light=light), "outside")
    assert node.node_tree == core and owned_counts() == stable
    retarget(obj, MATERIAL, "MME Control", alternative, slot=1)
    retarget(obj, DEFORM, "MME Control", alternative)
    assert node.node_tree == core
    assert_near(vertices(scene, obj, 10), wave_expected(scene, baseline[10], 10, 0.45))
    passed("C-04", "Ambiguous initial mapping rejected, rename retains the ID, deletion is diagnosed and explicit retarget restores output.")

    outer = material.node_tree
    artist = outer.nodes.new("ShaderNodeMixShader")
    artist.name = "Artist material edit"
    artist.inputs[0].default_value = 1.0
    outer.links.new(node.outputs["Surface"], artist.inputs[2])
    outer.links.new(artist.outputs[0], outer.nodes.get("Material Output").inputs["Surface"])
    edit = next(n.node_tree for n in mod.node_group.nodes if n.type == "GROUP" and n.node_tree.name.startswith("Edit Deformation"))
    artist_geom = edit.nodes.new("GeometryNodeSetPosition")
    artist_geom.name = "Artist geometry edit"
    artist_geom.inputs["Offset"].default_value = (0.0, 0.0, 0.02)
    edit.links.new(next(n for n in edit.nodes if n.type == "GROUP_INPUT").outputs[0], artist_geom.inputs["Geometry"])
    edit.links.new(artist_geom.outputs["Geometry"], next(n for n in edit.nodes if n.type == "GROUP_OUTPUT").inputs["Geometry"])
    before_rebuild = owned_counts()
    updated = apply(obj, TOON, MATERIAL, slot=1, light=light, force_rebuild=True)
    updated_geom = apply(obj, WAVE, DEFORM, force_rebuild=True)
    assert updated.destination == material and updated_geom.destination == mod
    assert artist.inputs[2].links[0].from_node == shade(updated)
    assert abs(shade(updated).inputs["Intensity"].default_value - 0.4) < 1e-6
    assert owned_counts() == before_rebuild
    altered = output / "updated-material.fx"
    source = TOON.read_text(encoding="utf-8-sig")
    altered.write_text(source.replace("? 1.0 : 0.25", "? 0.75 : 0.25"), encoding="utf-8")
    edited_result = apply(obj, altered, MATERIAL, slot=1, light=light)
    assert edited_result.rebuilt and artist.inputs[2].links[0].from_node == shade(edited_result)
    rgba = render(scene, output / "material-reimport.exr", 10)
    assert_near(swatch(rgba), BASE * PIXEL * 0.4 * 0.45 * 0.75, 0.007)
    changed_interface = output / "changed-interface.fx"
    changed_interface.write_text(source.replace("Intensity", "Power"), encoding="utf-8")
    reject(lambda: apply(obj, changed_interface, MATERIAL, slot=1, light=light), "interface changed")
    assert shade(edited_result).node_tree == edited_result.core
    apply(obj, TOON, MATERIAL, slot=1, light=light)
    assert owned_counts() == before_rebuild
    assert_near(vertices(scene, obj, 10), wave_expected(scene, baseline[10], 10, 0.45, offset=0.02))

    estimate = iter((1, 10, 20) * 20)
    animated_ms = elapsed(lambda: vertices(scene, obj, next(estimate)), 60)
    parse_ms = elapsed(lambda: fx_parse_file(str(WAVE)), 30)
    analysis_ms = elapsed(lambda: analyze(WAVE), 30)
    rebuild_ms = elapsed(lambda: apply(obj, WAVE, DEFORM, force_rebuild=True), 5)
    # Restore the declaration name on the explicitly mapped target for unchanged-source cache timings.
    alternative.name = "MME Control"
    warm_ms = elapsed(lambda: apply(obj, WAVE, DEFORM), 20)
    assert not apply(obj, WAVE, DEFORM).rebuilt
    demo = output / "middle-converters.blend"
    scene.frame_set(10)
    render(scene, output / "combined-after.exr", 10)
    scene.render.filepath = "//"
    bpy.ops.wm.save_as_mainfile(filepath=str(demo))
    bpy.ops.wm.open_mainfile(filepath=str(demo), load_ui=False)
    scene, obj = bpy.context.scene, bpy.data.objects["Demo model"]
    material = obj.material_slots[1].material
    mod = obj.modifiers[-1]
    assert material.node_tree.nodes["Artist material edit"].inputs[2].is_linked
    assert bpy.data.node_groups["Edit Deformation"].nodes["Artist geometry edit"]
    assert str(Path(demo)) == bpy.data.filepath
    assert not inspect_bindings(obj, MATERIAL, slot=1)
    assert not inspect_bindings(obj, DEFORM)
    assert_near(vertices(scene, obj, 10), wave_expected(scene, baseline[10], 10, 0.45, offset=0.02))
    opened = render(scene, output / "combined-reopened.exr", 10)
    assert_near(swatch(opened), BASE * PIXEL * 0.4 * 0.45, 0.007)
    light = bpy.data.objects["Direction source"]
    assert not apply(obj, TOON, MATERIAL, slot=1, light=light).rebuilt
    assert not apply(obj, WAVE, DEFORM).rebuilt
    passed("M-04", "Connected wrapper edit and FX values survive rebuild, save and reopen.")
    passed("G-04", "Connected edit stage survives rebuild and reopen with stable owner counts.")
    assert tuple(map(len, (bpy.app.handlers.frame_change_post, bpy.app.handlers.depsgraph_update_post))) == handler_names
    passed("C-05", "Retarget survives save and reopen in native drivers. No Python handlers were added.")

    stage = bpy.data.node_groups["Edit Deformation"]
    remove(obj, MATERIAL, slot=1)
    remove(obj, DEFORM)
    assert stage.use_fake_user and stage.nodes["Artist geometry edit"]
    assert obj.material_slots[1].material == bpy.data.materials["Original material"]
    assert len(obj.modifiers) == 1 and obj.modifiers[0].type == "ARMATURE"
    assert_near(vertices(scene, obj, 20), baseline[20])
    bpy.data.node_groups.remove(stage)
    clean = owned_counts()
    all_groups = len(bpy.data.node_groups)
    for _ in range(3):
        apply(obj, TOON, MATERIAL, slot=1, light=light)
        apply(obj, WAVE, DEFORM)
        remove(obj, MATERIAL, slot=1)
        remove(obj, DEFORM)
        assert owned_counts() == clean, (owned_counts(), clean)
        assert len(bpy.data.node_groups) == all_groups
    assert (0, 0, 0) == clean[:3]
    passed("R-03", f"Three apply/remove cycles leave original counts. Material/group/driver/mesh/object counts {clean}.")
    pre_skin = apply(obj, WAVE, DEFORM, modifier_index=0)
    assert obj.modifiers[0] == pre_skin.destination
    # Mixing local X and Z separates the pre/post positions under the keyed Y rotation.
    assert abs(vertices(scene, obj, 10)[0, 0] - baseline[10][0, 0]) > 0.001
    remove(obj, DEFORM)
    print(f"MEASURE ms material_apply={material_ms:.3f} geometry_apply={geom_ms:.3f} "
          f"original_evaluate_and_read={baseline_ms:.3f} animated_evaluate_and_read={animated_ms:.3f} parser_warm={parse_ms:.3f} analyze_warm={analysis_ms:.3f} "
          f"forced_rebuild={rebuild_ms:.3f} geometry_warm={warm_ms:.3f}", flush=True)
    print(f"PASS COUNT {len(tests)}; saved {demo}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--corpus", type=Path)
    options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    run(options.output.resolve(), options.corpus)
