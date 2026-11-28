import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness_fx import bpy, mb, np, setup_scene
from mathutils import Vector


scene, collection, _, camera = setup_scene()
fixture = Path(__file__).parent / "fixtures" / "mme" / "triangle" / "triangle.x"
result = bpy.ops.import_scene.ninesync_fx_accessory(filepath=str(fixture), scale=1.0)
assert result == {"FINISHED"}, result
entry = scene.ninesync_fx.accessories[0]
root = mb.find_accessory_root(collection, entry.key)
children = mb.mme_effect_passes(root)
assert len(children) == 1, children
mesh = children[0].evaluated_get(bpy.context.evaluated_depsgraph_get())
assert len(mesh.data.polygons) == 1 and mesh.data.materials[0].node_tree is not None
points = [mesh.matrix_world @ v.co for v in mesh.data.vertices]
center = sum(points, Vector()) / len(points)
normal = (points[1] - points[0]).cross(points[2] - points[0]).normalized()
camera.location = center + normal * 4
camera.rotation_euler = (-normal).to_track_quat("-Z", "Y").to_euler()
camera.data.type = "ORTHO"
camera.data.ortho_scale = max((p - center).length for p in points) * 2.5
scene.render.engine = "CYCLES"
scene.cycles.samples = 1
scene.cycles.device = "CPU"
scene.render.resolution_percentage = 100
scene.render.resolution_x = scene.render.resolution_y = 64
scene.render.film_transparent = True
scene.view_settings.view_transform = "Standard"
with tempfile.TemporaryDirectory() as tmp:
    scene.render.filepath = str(Path(tmp) / "triangle.png")
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(scene.render.filepath)
    pixels = np.array(image.pixels[:]).reshape(-1, 4)
    bpy.data.images.remove(image)
    inside = pixels[pixels[:, 3] > 0.99, :3]
    assert len(inside) > 100, len(inside)
    assert np.any(inside[:, 0] > inside[:, 1]) and np.any(inside[:, 1] > inside[:, 0]), inside.mean(axis=0)
    print(f"triangle: one material pass, {len(inside)} opaque pixels, both source texture colours present")
