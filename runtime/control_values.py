import bpy
import numpy as np

from ..common.constants import (
    ACCESSORY_UNIT,
    MME_SWAP3,
)
from ..common.controls import mme_control_use_prop
from ..fx.types import fx_is_numeric
from .matrices import (
    mme_np_matrix,
    mme_world_row,
)


# Reads a custom property from the evaluated object so animation on it is applied.
def mme_eval_prop(obj, depsgraph, name, default=0.0):
    source = obj.evaluated_get(depsgraph) if depsgraph is not None else obj
    try:
        return float(source.get(name, obj.get(name, default)))
    except (TypeError, ValueError):
        return default


# Value of an MME controller item for an object: x/y/z and xyz position in MMD space, rx/ry/rz rotation, si
# scale, tr opacity;
# with no item the world matrix, a visibility flag for bool globals, or the position as float4.
def mme_object_items(obj, depsgraph, unit, item, t):
    evaluated = obj.evaluated_get(depsgraph) if depsgraph is not None else obj
    world = mme_np_matrix(evaluated.matrix_world)
    pos = MME_SWAP3 @ world[:3, 3] / unit
    euler = evaluated.rotation_euler
    rotation = (-euler.x, -euler.z, -euler.y)
    lower = item.lower()
    if lower == "x":
        return [pos[0]]
    if lower == "y":
        return [pos[1]]
    if lower == "z":
        return [pos[2]]
    if lower == "xyz":
        return list(pos)
    if lower == "rx":
        return [rotation[0]]
    if lower == "ry":
        return [rotation[1]]
    if lower == "rz":
        return [rotation[2]]
    if lower == "rxyz":
        return list(rotation)
    if lower == "si":
        return [mme_eval_prop(obj, depsgraph, "mme_si", 1.0) * ACCESSORY_UNIT]
    if lower == "tr":
        return [mme_eval_prop(obj, depsgraph, "mme_tr", 1.0)]
    if lower:
        return [0.0]
    if fx_is_numeric(t) and len(t[1]) == 2:
        child_scale = np.diag([ACCESSORY_UNIT * unit] * 3 + [1.0])
        return list(mme_world_row(world @ child_scale, unit).reshape(-1))
    if fx_is_numeric(t) and t[0] == "b":
        return [1.0 if obj.visible_get() or not obj.hide_render else 0.0]
    return list(pos) + [1.0]


# Controller values multiplied by the effect's 'use this controller' switch, so switching it off reads as
# zero.
def mme_control_values(desc, root, depsgraph, unit, t):
    values = mme_control_values_raw(desc, root, depsgraph, unit, t)
    prop = mme_control_use_prop(desc)
    if prop is None:
        return values
    use = float(bool(mme_eval_prop(root, depsgraph, prop, 1.0)))
    return [float(v) * use for v in values]


# Controller value by kind: constant, self, custom property (morph track), other object, bone (as matrix or
# position), whole model or shape key.
def mme_control_values_raw(desc, root, depsgraph, unit, t):
    kind = desc.get("kind")
    if kind == "shapekey":
        obj = bpy.data.objects.get(desc.get("object", ""))
        keys = obj.data.shape_keys if obj is not None and obj.type == "MESH" else None
        block = keys.key_blocks.get(desc.get("key", "")) if keys is not None else None
        return [float(block.value) if block is not None else 0.0]
    if kind == "const":
        return list(desc.get("values", [0.0]))
    if kind == "self":
        return mme_object_items(root, depsgraph, unit, desc.get("item", ""), t)
    if kind == "prop":
        return [mme_eval_prop(root, depsgraph, desc.get("prop", ""), 0.0)]
    if kind == "object":
        other = bpy.data.objects.get(desc.get("object", ""))
        if other is None:
            return [0.0]
        return mme_object_items(other, depsgraph, unit, desc.get("item", ""), t)
    if kind == "bone":
        armature = bpy.data.objects.get(desc.get("object", ""))
        if armature is None or armature.type != "ARMATURE":
            return [0.0]
        evaluated = armature.evaluated_get(depsgraph) if depsgraph is not None else armature
        bone = evaluated.pose.bones.get(desc.get("bone", ""))
        if bone is None:
            return [0.0]
        world = mme_np_matrix(evaluated.matrix_world) @ mme_np_matrix(bone.matrix)
        if fx_is_numeric(t) and len(t[1]) == 2:
            return list(mme_world_row(world, unit).reshape(-1))
        pos = MME_SWAP3 @ world[:3, 3] / unit
        return list(pos) + [1.0]
    if kind == "model":
        if fx_is_numeric(t) and t[0] == "b":
            return [1.0]
        target = bpy.data.objects.get(desc.get("object", ""))
        if target is None:
            return [0.0]
        return mme_object_items(target, depsgraph, unit, "", t)
    return [0.0]
