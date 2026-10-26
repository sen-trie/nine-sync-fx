import bpy
import numpy as np

from ..common.constants import X_IMPORTER_MODULES
from ..common.log import log
from ..common.scene_utils import addon_loaded
from ..xfile.geometry import merge_x_meshes
from .materials import build_x_material


# Deletes the faces of the given material indices, used to keep only the parts of a mesh that no effect pass
# draws.
def remove_subset_faces(obj, subsets):
    mesh = obj.data
    if not subsets or not len(mesh.polygons):
        return
    indices = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("material_index", indices)
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(mesh)
    doomed = [f for f in bm.faces if f.material_index in subsets]
    if doomed:
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


# Binary and compressed .x files are only importable through the separate DirectX X Importer add-on.
def x_importer_available():
    return addon_loaded(X_IMPORTER_MODULES)


# Mesh object from a parsed .x without the importer add-on: axes swapped (Y/Z), face order reversed to match,
# V flipped for UVs, per-vertex colours as an attribute.
def create_x_object(name, xscene, folder, settings):
    vertices, faces, face_materials, uvs, colors, materials = merge_x_meshes(xscene)
    points = [(x, z, y) for x, y, z in vertices]
    flipped = [tuple(reversed(face)) for face in faces]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(points, [], flipped)
    mesh.update()
    for index, xmat in enumerate(materials):
        try:
            mesh.materials.append(
                build_x_material("%s_%d" % (name, index), xmat, folder, settings)
            )
        except Exception as error:
            log.warning("material %d of %s failed: %s", index, name, error)
            mesh.materials.append(bpy.data.materials.new("%s_%d" % (name, index)))
    if len(mesh.polygons) == len(face_materials):
        mesh.polygons.foreach_set("material_index", face_materials)
    if uvs:
        flat = []
        for face in flipped:
            for vertex in face:
                u, v = uvs[vertex]
                flat.extend((u, 1.0 - v))
        if len(mesh.loops) * 2 == len(flat):
            layer = mesh.uv_layers.new(name="UVMap")
            layer.data.foreach_set("uv", flat)
    if colors is not None:
        try:
            attribute = mesh.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
            attribute.data.foreach_set("color", [c for rgba in colors for c in rgba])
        except Exception as error:
            log.warning("vertex colors skipped for %s: %s", name, error)
    mesh.update()
    return bpy.data.objects.new(name, mesh)


# Fallback for binary .x files: finds whichever import operator the DirectX X Importer registers and returns
# the new objects.
def import_x_with_addon(path):
    before = set(bpy.data.objects)
    group = getattr(bpy.ops, "import_scene", None)
    operator = None
    for candidate in ("x", "directx", "directx_x", "x_file", "dx"):
        if group is not None and hasattr(group, candidate):
            operator = getattr(group, candidate)
            break
    if operator is None:
        raise RuntimeError(
            "binary/compressed .x needs the DirectX X Importer add-on, but its import operator was not found"
        )
    operator(filepath=path)
    return [o for o in bpy.data.objects if o not in before]
