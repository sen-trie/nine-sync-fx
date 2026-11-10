import bpy
import json
import os

from ..common.constants import (
    MME_CONTROL_PREFIX,
    MME_MANUAL_BONES_PROP,
    MME_MANUAL_PREFIX,
    MME_OBJECT_ITEMS,
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
        desc.setdefault("item", item)
        controls[g.name] = desc
    return mme_apply_manual_controls(root, controls), morph_tracks


# The controller item a control asks for; controls saved before 'item' was stored keep it in their label.
def mme_control_item(desc):
    if desc.get("item") is not None:
        return desc["item"]
    text = desc.get("from") or ""
    return text.split(" / ", 1)[1] if " / " in text else ""


def mme_manual_prop(name):
    return mme_prop_name(MME_MANUAL_PREFIX, name)


def mme_manual_bones(root):
    try:
        return json.loads(root.get(MME_MANUAL_BONES_PROP, "{}"))
    except (TypeError, ValueError):
        return {}


# The object is an ID property rather than a stored name, so a user's assignment follows renames and reads as
# empty once the object is deleted.
def mme_manual_set(root, name, obj, bone=""):
    root[mme_manual_prop(name)] = obj
    bones = mme_manual_bones(root)
    if bone:
        bones[name] = bone
    else:
        bones.pop(name, None)
    root[MME_MANUAL_BONES_PROP] = json.dumps(bones)


def mme_manual_clear(root, name):
    prop = mme_manual_prop(name)
    if prop in root:
        del root[prop]
    bones = mme_manual_bones(root)
    if bones.pop(name, None) is not None:
        root[MME_MANUAL_BONES_PROP] = json.dumps(bones)


def mme_manual_armature(obj):
    if obj.type == "ARMATURE":
        return obj
    if obj.parent is not None and obj.parent.type == "ARMATURE":
        return obj.parent
    return find_armature(obj)


# Reads a controller item off the object the user picked, in the same order as a matched model: shape key, then
# bone, then a transform item. A bone the user named wins. None when the object cannot supply the item.
def mme_manual_control(obj, item, bone=""):
    armature = mme_manual_armature(obj)
    if bone:
        if armature is None or armature.pose.bones.get(bone) is None:
            return None
        return {"kind": "bone", "object": armature.name, "bone": bone}
    if not item:
        return {"kind": "model", "object": obj.name}
    for child in [obj] + list(obj.children_recursive):
        keys = child.data.shape_keys if child.type == "MESH" else None
        if keys is not None and item in keys.key_blocks:
            return {"kind": "shapekey", "object": child.name, "key": item}
    found = pose_bone_name(armature, item) if armature is not None else None
    if found is not None:
        return {"kind": "bone", "object": armature.name, "bone": found}
    if item.lower() in MME_OBJECT_ITEMS:
        return {"kind": "object", "object": obj.name, "item": item}
    return None


# Applies manual assignments to controls nothing else resolved. Automatic matches win, so a model that later
# appears in the PMM takes over; the assignment stays stored and returns if the match goes away. An assignment
# that stopped working goes back to the constant with a note saying why.
def mme_apply_manual_controls(root, controls):
    bones = mme_manual_bones(root)
    for name, desc in list(controls.items()):
        if desc.get("kind") != "const" and not desc.get("manual"):
            continue
        shared = {key: desc[key] for key in ("from", "source") if key in desc}
        item = mme_control_item(desc)
        shared["item"] = item
        prop = mme_manual_prop(name)
        if prop not in root:
            if desc.get("manual"):
                controls[name] = {"kind": "const", "values": [0.0], **shared}
            continue
        obj = root.get(prop)
        found = mme_manual_control(obj, item, bones.get(name, "")) if obj is not None else None
        if found is None:
            note = "assigned object was deleted" if obj is None else "%s has no %s" % (obj.name, bones.get(name) or item)
            controls[name] = {"kind": "const", "values": [0.0], "manual": True, "note": note, **shared}
        else:
            controls[name] = {**shared, **found, "manual": True}
    return controls


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


# Controller morph tracks as curves for the accessory's keying (written with its other keys), with each
# property set to its first value so it exists before it is animated; tracks without keys hold 0.
def mme_morph_curves(root, morph_tracks):
    curves = []
    for prop, keys in morph_tracks.items():
        if not keys:
            root[prop] = 0.0
            continue
        ordered = sorted(keys, key=lambda k: k.frame)
        root[prop] = float(ordered[0].weight)
        curves.append(('["%s"]' % prop, [k.frame for k in ordered], [float(k.weight) for k in ordered]))
    return curves
