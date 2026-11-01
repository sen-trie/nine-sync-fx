import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
from ninesync_fx.xfile import parse_x_bytes
from ninesync_fx.xfile.geometry import merge_x_meshes
import paths
path = os.path.join(paths.HERE, "fixtures", "plain", "plainthing.x")
with open(path, "rb") as handle:
    scene = parse_x_bytes(handle.read())
vertices, faces, face_materials, uvs, colors, materials = merge_x_meshes(scene)
failed = []
def check(label, got, want):
    ok = got == want
    print("%-28s %s" % (label, "ok" if ok else "FAILED got %r want %r" % (got, want)))
    if not ok:
        failed.append(label)
check("vertices", len(vertices), 8)
check("faces", len(faces), 6)
check("materials", len(materials), 1)
check("uvs", len(uvs), 8)
check("material indices", len(face_materials), 6)
for o in list(bpy.data.objects): bpy.data.objects.remove(o)
result = bpy.ops.import_scene.ninesync_fx_accessory(filepath=path, scale=1.0)
check("operator result", result, {"FINISHED"})
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
check("mesh objects", len(meshes), 1)
if meshes:
    check("mesh vertices", len(meshes[0].data.vertices), 8)
    check("mesh polygons", len(meshes[0].data.polygons), 6)
print("FAILED:", ", ".join(failed) if failed else "none")
