import bpy
import os

from ..common.animation import iter_fcurves
from ..common.constants import (
    MME_CONTROL_PREFIX,
    MME_SELF_NAMES,
)
from ..common.scene_utils import (
    accessory_collection,
    accessory_key,
    find_accessory_root,
    find_armature,
    model_key,
    pose_bone_name,
)
from ..fx.semantics import fx_classify_global
from ..nodes.names import mme_prop_name


# Maps the effect's CONTROLOBJECT globals to things in the scene: itself, another accessory, a PMM model
# (bone, morph or whole model), or a scene object found by name.
# Morph controls become custom properties; returns the controls plus the PMM morph tracks to key onto them.
def mme_resolve_controls(program, project, settings, acc, root):
    controls = {}
    scene = settings.id_data if isinstance(getattr(settings, "id_data", None), bpy.types.Scene) else bpy.context.scene
    by_path = {}
    for model in project.models:
        base = model.path.replace("\\", "/").rsplit("/", 1)[-1].lower()
        by_path[base] = model
        if model.name:
            by_path.setdefault(model.name.lower(), model)
    accessories = {}
    for other in project.accessories:
        accessories[other.name.lower()] = other
        accessories[other.path.replace("\\", "/").rsplit("/", 1)[-1].lower()] = other
    items = {t.key: t for t in settings.targets}
    collection = accessory_collection(scene)
    morph_tracks = {}
    for g in program.globals.values():
        source = fx_classify_global(g, program)
        if source is None or source[0] != "control":
            continue
        name, item = source[1], source[2]
        lower = name.lower()
        desc = {"kind": "const", "values": [0.0]}
        if lower in MME_SELF_NAMES:
            desc = {"kind": "self", "item": item}
        elif lower.endswith((".x", ".vac")):
            other = accessories.get(lower)
            if other is not None and collection is not None:
                other_root = find_accessory_root(collection, accessory_key(other))
                if other_root is not None and other_root != root:
                    desc = {"kind": "object", "object": other_root.name, "item": item}
                elif other_root is root:
                    desc = {"kind": "self", "item": item}
        else:
            model = by_path.get(lower)
            if model is None:
                stem = os.path.splitext(lower)[0]
                model = next((m for k, m in by_path.items() if os.path.splitext(k)[0] == stem), None)
            if model is None:
                found = mme_scene_control(name, item)
                if found is not None:
                    desc = found
            if model is not None:
                item_obj = items.get(model_key(model))
                target = item_obj.target if item_obj is not None else None
                if item and item in model.morph_names:
                    prop = mme_prop_name(MME_CONTROL_PREFIX, g.name)
                    morph_tracks[prop] = model.morphs[model.morph_names.index(item)]
                    desc = {"kind": "prop", "prop": prop}
                elif item and item in model.bone_names and target is not None:
                    armature = find_armature(target)
                    bone = pose_bone_name(armature, item) if armature is not None else None
                    if bone is not None:
                        desc = {"kind": "bone", "object": armature.name, "bone": bone}
                elif not item:
                    desc = {"kind": "model", "object": target.name if target is not None else ""}
        desc["from"] = "%s%s" % (name, (" / %s" % item) if item else "")
        desc["source"] = name
        controls[g.name] = desc
    return controls, morph_tracks


# mmd_tools root matching a name, by exact match then by substring, ignoring folders and extensions.
def mme_scene_model(name):
    lower = name.lower()
    stem = os.path.splitext(lower.replace("\\", "/").rsplit("/", 1)[-1])[0]
    roots = [o for o in bpy.data.objects if getattr(o, "mmd_type", "") == "ROOT"]

    def names(obj):
        mmd_root = getattr(obj, "mmd_root", None)
        found = [obj.name]
        if mmd_root is not None:
            found += [mmd_root.name, mmd_root.name_e]
        return [n.lower() for n in found if n]

    for obj in roots:
        if stem in names(obj) or lower in names(obj):
            return obj
    for obj in roots:
        if any(stem and (stem in n or n in stem) for n in names(obj)):
            return obj
    return None


# Controller for a model that was not matched to the PMM: shape key first, then bone, or the whole model when
# no item is given.
def mme_scene_control(name, item):
    root = mme_scene_model(name)
    if root is None:
        return None
    if not item:
        return {"kind": "model", "object": root.name}
    for child in root.children_recursive:
        keys = child.data.shape_keys if child.type == "MESH" else None
        if keys is not None and item in keys.key_blocks:
            return {"kind": "shapekey", "object": child.name, "key": item}
    armature = find_armature(root)
    bone = pose_bone_name(armature, item) if armature is not None else None
    if bone is not None:
        return {"kind": "bone", "object": armature.name, "bone": bone}
    return None


# Keys the morph weights from the PMM onto the control properties with linear interpolation.
def mme_key_morph_props(root, morph_tracks):
    anim = root.animation_data or root.animation_data_create()
    for prop, keys in morph_tracks.items():
        if not keys:
            root[prop] = 0.0
            continue
        for key in keys:
            root[prop] = float(key.weight)
            root.keyframe_insert('["%s"]' % prop, frame=key.frame)
    action = anim.action
    if action is not None:
        for fcurve in iter_fcurves(action):
            if fcurve.data_path.startswith('["%s' % MME_CONTROL_PREFIX):
                for point in fcurve.keyframe_points:
                    point.interpolation = "LINEAR"
