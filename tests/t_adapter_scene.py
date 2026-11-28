import os
from pathlib import Path
import sys
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import paths
paths.prepare("ninesync_fx")

import addon_utils
addon = addon_utils.enable("ninesync_fx", default_set=True)
assert addon
from ninesync_fx.experimental_adapters.compositor import CompositorAdapter as Adapter, CORE, OWNER, plan
from ninesync_fx.experimental_adapters.registry import recognize
from ninesync_fx.experimental_adapters.scan import scan


def counts():
    return tuple(len(getattr(bpy.data, key)) for key in ("objects", "materials", "node_groups", "scenes", "images"))


def control(effect, name, value=None):
    identifier = next(s.identifier for s in effect.node_group.interface.items_tree if s.name == name)
    socket = getattr(effect.properties.inputs, identifier)
    if value is not None:
        socket.value = value
    return socket.value


scene = bpy.context.scene
prefs = bpy.context.preferences.addons["ninesync_fx"].preferences
assert not bpy.ops.ninesync_fx.analyze_adapter.poll()
prefs.experimental_mme_adapters = True
assert bpy.ops.ninesync_fx.analyze_adapter.poll()
before = counts()
fixture = ROOT / "tests/fixtures/mme/known/tonal.fx"
t0 = perf_counter()
for _ in range(10):
    assert bpy.ops.ninesync_fx.analyze_adapter(filepath=str(fixture)) == {"FINISHED"}
print("Synthetic scan mean ms", round((perf_counter() - t0) * 100, 2))
assert counts() == before and not scene.compositor_effects
assert bpy.ops.ninesync_fx.adapter_action(action="TONAL") == {"FINISHED"}
early = scene.compositor_effects[0]
early.name = "Existing color adjustment"

for obj in tuple(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
scene.collection.objects.link(camera)
camera.location.z = 5
camera.data.type = "ORTHO"
camera.data.ortho_scale = 5
scene.camera = camera
for i, (color, strength) in enumerate((((0.2, 0.06, 0.04, 1), 1),
                                       ((0.3, 0.5, 0.8, 1), 1),
                                       ((1, 0.4, 0.05, 1), 5))):
    bpy.ops.mesh.primitive_plane_add(size=1.05, location=(i * 1.45 - 1.45, 0, 0))
    material = bpy.data.materials.new(f"Patch {i}")
    nodes = material.node_tree.nodes
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = strength
    material.node_tree.links.new(emission.outputs["Emission"], nodes.get("Material Output").inputs["Surface"])
    bpy.context.object.data.materials.append(material)
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 1
scene.render.resolution_x = 256
scene.render.resolution_y = 128
scene.render.resolution_percentage = 100
scene.render.film_transparent = True
scene.view_settings.view_transform = "Standard"
scene.render.image_settings.color_mode = "RGBA"
scene.render.threads_mode = "FIXED"
scene.render.threads = 4

temp_output = None
if "NINESYNC_ADAPTER_OUTPUT" not in os.environ:
    temp_output = TemporaryDirectory(prefix="ninesync_adapter_")
out = Path(temp_output.name if temp_output else os.environ["NINESYNC_ADAPTER_OUTPUT"])
out.mkdir(parents=True, exist_ok=True)


def render(stem):
    scene.render.filepath = str(out / f"{stem}.png")
    start = perf_counter()
    assert bpy.ops.render.render(write_still=True) == {"FINISHED"}
    elapsed = perf_counter() - start
    image = bpy.data.images.load(scene.render.filepath)
    pixels = np.array(image.pixels[:], dtype=np.float32).reshape(-1, 4)
    bpy.data.images.remove(image)
    print(f"Render {stem} seconds {elapsed:.3f}")
    return pixels


baseline = render("before")
creator = os.getenv("NINESYNC_TONEMAP")
selected = Path(creator) / "o_Tonemap.x" if creator else fixture
facts = scan(selected)
assert recognize(facts).adapter == "tonal"
assert bpy.ops.ninesync_fx.analyze_adapter(filepath=str(selected)) == {"FINISHED"}
t0 = perf_counter()
assert bpy.ops.ninesync_fx.adapter_action(action="VERIFIED", fingerprint=facts.fingerprint) == {"FINISHED"}
tonal = scene.compositor_effects[-1]
adapter_plan = plan("tonal", recognize(facts), facts)
print("Tonal apply ms", round((perf_counter() - t0) * 1000, 2))
assert scene.compositor_effects[0] == early
assert Adapter.apply(scene, adapter_plan) == tonal and len(scene.compositor_effects) == 2
assert np.array_equal(baseline, render("neutral"))
control(tonal, "Strength", 0.0)
control(tonal, "Exposure", 0.65)
control(tonal, "Contrast", 8.0)
control(tonal, "Saturation", 0.25)
assert np.array_equal(baseline, render("bypass"))
control(tonal, "Strength", 0.8)
toned = render("tonal")
mae = np.abs(baseline[:, :3] - toned[:, :3]).mean()
print("Tonal RGB MAE", float(mae), "alpha change", float(np.abs(baseline[:, 3] - toned[:, 3]).max()))
assert mae > 0.005
assert np.array_equal(baseline[:, 3], toned[:, 3])

wrapper = tonal.node_group
inner = next(n for n in wrapper.nodes if n.get(CORE))
original = inner.node_tree
original.nodes.new("NodeFrame").label = "Edit to replace"
wrapper.nodes.new("NodeFrame").label = "User wrapper edit"
links = len(wrapper.links)
Adapter.rebuild(tonal)
assert tonal.node_group == wrapper and len(wrapper.links) == links
assert any(n.label == "User wrapper edit" for n in wrapper.nodes)
assert all(n.label != "Edit to replace" for n in inner.node_tree.nodes)
assert abs(control(tonal, "Strength") - 0.8) < 1e-5
assert np.array_equal(toned, render("rebuilt"))
assert "Render Layers" not in wrapper.nodes

before = counts()
with patch("ninesync_fx.experimental_adapters.compositor._interface", side_effect=RuntimeError("test failure")):
    try:
        Adapter.apply(scene, plan("bloom"))
        raise AssertionError("Failure was not raised")
    except RuntimeError as error:
        assert str(error) == "test failure"
assert counts() == before and len(scene.compositor_effects) == 2

bloom = Adapter.apply(scene, plan("bloom"))
control(bloom, "Threshold", 0.45)
glow_after = render("tonal-then-bloom")
assert bpy.ops.scene.move_compositor_effect_to_index(name=bloom.name, index=1) == {"FINISHED"}
assert [e.name for e in scene.compositor_effects] == [early.name, bloom.name, tonal.name]
glow_before = render("bloom-then-tonal")
order_mae = np.abs(glow_after[:, :3] - glow_before[:, :3]).mean()
print("Stack order RGB MAE", float(order_mae), "bloom alpha change", float(np.abs(glow_after[:, 3] - toned[:, 3]).max()))
assert order_mae > 0.001

scene.render.film_transparent = False
bloom.enable_for_render = False
opaque_tonal = render("opaque-tonal")
bloom.enable_for_render = True
opaque_bloom = render("opaque-bloom-then-tonal")
assert np.all(opaque_bloom[:, 3] == 1)
assert np.abs(opaque_bloom[:, :3] - opaque_tonal[:, :3]).mean() > 0.002
scene.render.film_transparent = True

Adapter.disable(bloom)
assert not bloom.enable_for_render and not bloom.enable_for_preview
assert np.array_equal(toned, render("bloom-disabled"))
Adapter.remove(scene, bloom)
assert len(scene.compositor_effects) == 2
core = inner.node_tree
print("Tonal wrapper/core nodes", len(wrapper.nodes), len(core.nodes))
scene.compositor_effects.active = tonal

def warm_median(effect, enabled):
    effect.enable_for_render = enabled
    durations = []
    for _ in range(5):
        start = perf_counter()
        bpy.ops.render.render()
        durations.append(perf_counter() - start)
    return median(durations)

print("Median render seconds, adapter off/on", round(warm_median(tonal, False), 3),
      round(warm_median(tonal, True), 3))

blend = out / "scene-effects.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(blend))
addon_utils.disable("ninesync_fx", default_set=True)
bpy.ops.wm.open_mainfile(filepath=str(blend))
scene = bpy.context.scene
assert "ninesync_fx" not in bpy.context.preferences.addons
tonal = scene.compositor_effects[1]
assert tonal.node_group[OWNER] == "tonal" and abs(control(tonal, "Strength") - 0.8) < 1e-5
assert np.array_equal(toned, render("reopened-without-addon"))
Adapter.remove(scene, tonal)
assert len(scene.compositor_effects) == 1 and scene.compositor_effects[0].name == "Existing color adjustment"
assert np.array_equal(baseline, render("removed"))
print("Scene Effect lifecycle checks passed")
