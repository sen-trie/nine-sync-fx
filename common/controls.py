import bpy
import hashlib
import json

from .constants import (
    MME_DRAW_PROP,
    MME_IGNORED_PROP,
    MME_PARAMS_PROP,
    MME_SELF_NAMES,
    MME_USE_PREFIX,
)
from .scene_utils import accessory_collection


# Controls are stored on the effect root as JSON in a custom property; a damaged value reads as no controls.
def mme_load_controls_raw(root):
    try:
        return json.loads(root.get("mme_controls", "{}"))
    except (TypeError, ValueError):
        return {}


# The object or bone a controller control reads from, with the legacy 'from' text as fallback.
def mme_control_source(desc):
    return (desc.get("source") or (desc.get("from") or "").split(" / ")[0]).strip()


# Only controls that read another object can be switched off; (self) and offscreen-owner controls always
# apply.
def mme_control_toggleable(desc):
    return desc.get("kind") in ("prop", "model", "bone", "object", "shapekey") and mme_control_source(desc).lower() not in MME_SELF_NAMES


# Property name for the per-source 'use' switch. Names over 63 bytes are hashed because Blender caps custom
# property names.
def mme_use_prop(source):
    text = MME_USE_PREFIX + source
    if len(text.encode("utf-8")) > 63:
        text = MME_USE_PREFIX + hashlib.md5(source.encode("utf-8")).hexdigest()[:12]
    return text


# Name of the use-switch property for a control, or None when the control cannot be switched off.
def mme_control_use_prop(desc):
    if not mme_control_toggleable(desc):
        return None
    return mme_use_prop(mme_control_source(desc))


# Creates the 'use this controller' switches on the root. Also migrates the older ignore list into the new
# per-source switches and deletes it.
def mme_ensure_use_props(root, controls):
    legacy = root.get(MME_IGNORED_PROP)
    ignored = set()
    if legacy is not None:
        try:
            ignored = {str(v).lower() for v in json.loads(legacy)}
        except (TypeError, ValueError):
            ignored = set()
        del root[MME_IGNORED_PROP]
    for desc in controls.values():
        prop = mme_control_use_prop(desc)
        if prop is None or prop in root:
            continue
        source = mme_control_source(desc)
        root[prop] = source.lower() not in ignored
        try:
            root.id_properties_ui(prop).update(
                description="Use %s in this effect. Untick (or keyframe it off) to run the effect as if the controller were not loaded in MMD, so it falls back to its own parameters" % source
            )
        except (TypeError, AttributeError):
            pass
        root.update_tag()


# Resolved controls of an effect root (see mme_resolve_controls for the shapes).
def mme_load_controls(root):
    return mme_load_controls_raw(root)


# Effect parameters (name, type, range) as saved at build time.
def mme_param_meta(root):
    try:
        return json.loads(root.get(MME_PARAMS_PROP, "{}"))
    except (TypeError, ValueError):
        return {}


# Driver variable spec that reads a morph control's shape key value; None when the object or key is gone.
def mme_shapekey_spec(desc):
    obj = bpy.data.objects.get(desc.get("object", ""))
    keys = obj.data.shape_keys if obj is not None and obj.type == "MESH" else None
    if keys is None or desc.get("key", "") not in keys.key_blocks:
        return None
    return {
        "expr": "var",
        "vars": [{"name": "var", "type": "SINGLE_PROP", "id_type": "KEY", "id": keys.name, "path": 'key_blocks["%s"].value' % desc.get("key", "")}],
    }


# Effect accessories are the tagged roots in the accessory collection.
def mme_effect_roots(scene):
    collection = accessory_collection(scene)
    if collection is None:
        return []
    return [
        obj
        for obj in collection.objects
        if obj.get("mme_fx") and obj.get("mmd_bridge_accessory") is not None
    ]


# Passes are the root's children that carry a draw description.
def mme_effect_passes(root):
    return [c for c in root.children if c.get(MME_DRAW_PROP)]
