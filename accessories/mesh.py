import importlib

import bpy
import numpy as np

from ..common.constants import X_IMPORT_OPTIONS, X_IMPORTER_BUNDLED_MODULE, X_IMPORTER_MODULES
from ..common.log import log
from ..common.scene_utils import loaded_addon_names
from ..runtime.mesh_build import mme_build_mesh
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


# Binary and compressed .x files need the DirectX X importer. Order: a copy bundled by the combined build, then
# an enabled importer add-on's import_x function (no operator needed, so nothing is registered twice), and
# last an operator probe for importers that expose no function (returned as None here). The probe lists the
# registered operators with dir(): hasattr on bpy.ops answers True for any name.
def x_importer_function():
    try:
        bundled = importlib.import_module(
            "..%s" % X_IMPORTER_BUNDLED_MODULE, __package__
        )
        function = getattr(bundled, "import_x", None)
        if callable(function):
            return function
    except ModuleNotFoundError as error:
        if error.name is None or not error.name.endswith(X_IMPORTER_BUNDLED_MODULE):
            log.warning("bundled .x importer failed to load: %s", error)
    except Exception as error:
        log.warning("bundled .x importer failed to load: %s", error)
    for name in loaded_addon_names(X_IMPORTER_MODULES):
        try:
            function = getattr(importlib.import_module(name + ".importer"), "import_x", None)
        except Exception as error:
            log.warning("importer add-on %s has no usable importer module: %s", name, error)
            continue
        if callable(function):
            return function
    return None


def x_importer_operator():
    group = getattr(bpy.ops, "import_scene", None)
    if group is None:
        return None
    registered = set(dir(group))
    for candidate in ("x", "directx", "directx_x", "x_file", "dx"):
        if candidate in registered:
            return getattr(group, candidate)
    return None


def x_importer_available():
    return x_importer_function() is not None or x_importer_operator() is not None


# Mesh object from a parsed .x without the importer add-on: axes swapped (Y/Z), face order reversed to match,
# V flipped for UVs, per-vertex colours as an attribute. Built with bulk setters.
def create_x_object(name, xscene, folder, settings, additive=False):
    vertices, faces, face_materials, uvs, colors, materials = merge_x_meshes(xscene)
    points = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)[:, (0, 2, 1)]
    flipped = [tuple(reversed(face)) for face in faces]
    mesh = mme_build_mesh(name, points, flipped)
    for index, xmat in enumerate(materials):
        try:
            mesh.materials.append(
                build_x_material("%s_%d" % (name, index), xmat, folder, settings, additive)
            )
        except Exception as error:
            log.warning("material %d of %s failed: %s", index, name, error)
            mesh.materials.append(bpy.data.materials.new("%s_%d" % (name, index)))
    if len(mesh.polygons) == len(face_materials):
        mesh.polygons.foreach_set("material_index", face_materials)
    if uvs and flipped:
        loops = np.concatenate([np.asarray(f, dtype=np.int64) for f in flipped])
        coords = np.asarray(uvs, dtype=np.float64).reshape(-1, 2)[loops]
        coords[:, 1] = 1.0 - coords[:, 1]
        if len(mesh.loops) == len(coords):
            layer = mesh.uv_layers.new(name="UVMap")
            layer.data.foreach_set("uv", coords.astype(np.float32).reshape(-1))
    if colors is not None:
        try:
            attribute = mesh.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
            attribute.data.foreach_set("color", np.asarray(colors, dtype=np.float32).reshape(-1))
        except Exception as error:
            log.warning("vertex colors skipped for %s: %s", name, error)
    mesh.update()
    return bpy.data.objects.new(name, mesh)


# Binary .x import through whichever importer x_importer_function finds, else the operator probe. Returns the
# new objects; on failure it removes what the importer created and raises RuntimeError for the caller to report.
def import_x_with_addon(path):
    function = x_importer_function()
    operator = None if function is not None else x_importer_operator()
    if function is None and operator is None:
        raise RuntimeError(
            "binary/compressed .x needs the DirectX X Importer, but no importer function or import operator was found"
        )
    before = set(bpy.data.objects)
    before_collections = set(bpy.data.collections)
    try:
        if function is not None:
            function(bpy.context, filepath=path, **X_IMPORT_OPTIONS)
        else:
            operator(filepath=path)
    except Exception as error:
        for obj in [o for o in bpy.data.objects if o not in before]:
            bpy.data.objects.remove(obj)
        for collection in [c for c in bpy.data.collections if c not in before_collections]:
            bpy.data.collections.remove(collection)
        raise RuntimeError("DirectX X importer failed on %s: %s" % (path, error)) from error
    return [o for o in bpy.data.objects if o not in before]
