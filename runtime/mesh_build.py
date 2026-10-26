import bpy
import numpy as np

from ..common.constants import (
    ACCESSORY_UNIT,
    MME_SWAP3,
)
from .matrices import mme_np_matrix


# Builds a mesh from numpy arrays with bulk setters; much faster than adding vertices and faces one by one.
def mme_build_mesh(name, positions, faces):
    mesh = bpy.data.meshes.new(name)
    count = len(positions)
    mesh.vertices.add(count)
    mesh.vertices.foreach_set("co", positions.astype(np.float32).reshape(-1))
    sizes = np.array([len(f) for f in faces], dtype=np.int64)
    flat = np.concatenate([np.asarray(f, dtype=np.int64) for f in faces]) if faces else np.zeros(0, dtype=np.int64)
    starts = np.zeros(len(faces), dtype=np.int64)
    if len(faces):
        starts[1:] = np.cumsum(sizes)[:-1]
    mesh.loops.add(len(flat))
    mesh.loops.foreach_set("vertex_index", flat.astype(np.int32))
    mesh.polygons.add(len(faces))
    mesh.polygons.foreach_set("loop_start", starts.astype(np.int32))
    mesh.update(calc_edges=True)
    return mesh


# Writes a point attribute, replacing one of a different type or domain.
def mme_set_attribute(mesh, name, values, kind="FLOAT"):
    attribute = mesh.attributes.get(name)
    if attribute is None or attribute.data_type != kind or attribute.domain != "POINT":
        if attribute is not None:
            mesh.attributes.remove(attribute)
        attribute = mesh.attributes.new(name=name, type=kind, domain="POINT")
    if kind == "INT":
        attribute.data.foreach_set("value", np.asarray(values, dtype=np.int32))
    elif kind == "FLOAT_VECTOR":
        attribute.data.foreach_set("vector", np.asarray(values, dtype=np.float32).reshape(-1))
    else:
        attribute.data.foreach_set("value", np.asarray(values, dtype=np.float32))


# Reads a point attribute back as a numpy array, or None if it does not exist.
def mme_read_attribute(mesh, name, kind="INT"):
    attribute = mesh.attributes.get(name)
    if attribute is None:
        return None
    out = np.empty(len(attribute.data), dtype=np.int32 if kind == "INT" else np.float32)
    attribute.data.foreach_get("value", out)
    return out


# World matrix of a pass object: the root's matrix with the accessory unit and import scale applied.
def mme_child_matrix(root, unit):
    return mme_np_matrix(root.matrix_world) @ np.diag([ACCESSORY_UNIT * unit] * 3 + [1.0])


# Pass object under the root. It casts no shadow and is invisible to diffuse, glossy, transmission and volume
# rays, so it does not light or shadow the rest of the scene.
def mme_pass_object(label, mesh, collection, root):
    obj = bpy.data.objects.new(label, mesh)
    collection.objects.link(obj)
    obj.parent = root
    obj.visible_shadow = False
    for name in ("visible_diffuse", "visible_glossy", "visible_transmission", "visible_volume_scatter"):
        setattr(obj, name, False)
    return obj


# MMD world positions to the pass object's local space; culled vertices collapse to the origin and non-finite
# values become zero.
def mme_world_to_local(world, culled, child_matrix, unit):
    blender = (world @ MME_SWAP3.T) * unit
    inverse = np.linalg.inv(child_matrix)
    local = blender @ inverse[:3, :3].T + inverse[:3, 3]
    local[culled] = 0.0
    return np.where(np.isfinite(local), local, 0.0)
