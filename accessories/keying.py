import bpy
import hashlib
import json
from array import array

from ..common.animation import (
    frame_span,
    iter_fcurves,
    purge_actions,
    restore_points,
    snapshot_outside,
)
from ..common.constants import (
    ACCESSORY_FADE_PROP,
    ACCESSORY_KEY_RECORD,
    ACCESSORY_OWNED_PATHS,
    ACTION_PREFIX,
    MME_CONTROL_PREFIX,
    MME_DRAW_PROP,
)
from ..common.log import log
from ..common.scene_utils import short_name
from ..runtime.visibility import mme_link_visibility


# Channels the sync writes itself (transform, opacity, scale and the control properties); everything else on
# the root is the user's.
def accessory_owned_path(path):
    return path in ACCESSORY_OWNED_PATHS or path.startswith('["%s' % MME_CONTROL_PREFIX)


# Hash of the PMM side of an accessory's animation: its keys, the controller morph curves and whether MME
# shows it.
def accessory_keys_fingerprint(keys, shown, extra=()):
    sha = hashlib.sha256()
    for key in sorted(keys, key=lambda k: k.frame):
        sha.update(repr((
            int(key.frame),
            int(bool(key.visible)),
            [round(float(v), 6) for v in key.position],
            [round(float(v), 6) for v in key.rotation],
            round(float(key.scale), 6),
            round(float(key.opacity), 6),
            int(key.parent_model),
            int(key.parent_bone),
        )).encode("ascii"))
    for path, frames, values in sorted(extra):
        sha.update(repr((path, [int(f) for f in frames], [round(float(v), 6) for v in values])).encode("utf-8"))
    sha.update(b"shown" if shown else b"hidden")
    return sha.hexdigest()[:24]


# Hash of what an action animates, compared with the value saved after a sync to tell an edit made in Blender
# from an untouched action (the same test core uses for model clips).
def action_digest(action):
    if action is None:
        return ""
    sha = hashlib.sha256()
    curves = sorted(iter_fcurves(action), key=lambda c: (c.data_path, c.array_index))
    sha.update(str(len(curves)).encode("ascii"))
    for fcurve in curves:
        points = fcurve.keyframe_points
        count = len(points)
        sha.update(("%s|%d|%d|" % (fcurve.data_path, fcurve.array_index, count)).encode("utf-8", "replace"))
        for prop in ("co", "handle_left", "handle_right"):
            buffer = array("f", bytes(8 * count))
            points.foreach_get(prop, buffer)
            sha.update(buffer.tobytes())
        modes = array("i", bytes(4 * count))
        points.foreach_get("interpolation", modes)
        sha.update(modes.tobytes())
    return sha.hexdigest()[:24]


def owned_action(obj):
    anim = obj.animation_data
    return anim.action if anim is not None else None


# State of the visibility the children carry: their shared action, or the static hide flags.
def visibility_state(root):
    for child in root.children:
        action = owned_action(child)
        if action is not None:
            return {"action": action.name, "digest": action_digest(action)}
    return {"action": "", "hidden": sorted({bool(c.hide_viewport or c.hide_render) for c in root.children})}


# Whether the keys written by the last sync are still as written: "missing" (no record or no action),
# "edited" (other action, other content or other visibility) or "ok".
def compare_record(root, record):
    action = owned_action(root)
    if record is None or action is None:
        return "missing"
    if action.name != record.get("action") or action_digest(action) != record.get("digest"):
        return "edited"
    if visibility_state(root) != record.get("visible"):
        return "edited"
    return "ok"


# The decision table core uses for model clips: new PMM keys replace an edit (with a warning), changed import
# settings never do, and an unchanged PMM keeps everything. Returns (write, message, level).
def keying_decision(root, record, source, settings):
    state = compare_record(root, record)
    if state == "missing":
        return True, "", None
    if record.get("source") != source:
        if state == "edited":
            return True, "keys edited in Blender were replaced because the PMM keys changed", "warning"
        return True, "", None
    if record.get("settings") != settings:
        if state == "edited":
            return False, "import scale or parent bones changed, but the keys were edited in Blender, so they are kept; delete the accessory's action to re-key it", "warning"
        return True, "", None
    if state == "edited":
        return False, "PMM unchanged; your edits in Blender are kept", "info"
    return False, "", None


# Writes channels in one go (keyframe_points.add plus foreach_set) instead of a keyframe_insert per key.
def write_curves(owner, action, curves, interpolation, group=""):
    anim = owner.animation_data or owner.animation_data_create()
    anim.action = action
    mode = bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items[interpolation].value
    for path, index, frames, values in curves:
        fcurve = action.fcurve_ensure_for_datablock(owner, path, index=index, group_name=group)
        points = fcurve.keyframe_points
        if len(points):
            points.clear()
        points.add(len(frames))
        flat = []
        for frame, value in zip(frames, values):
            flat.extend((float(frame), float(value)))
        points.foreach_set("co", flat)
        points.foreach_set("interpolation", [mode] * len(frames))
        fcurve.update()


# PMM keys as curves: location and rotation with the Y/Z swap, uniform scale, opacity (mme_tr) and scale
# (mme_si), and the parent influence curves of a parent that changes between keys.
def root_curves(keys, unit):
    frames = [k.frame for k in keys]
    curves = []
    for index, source in enumerate((0, 2, 1)):
        curves.append(("location", index, frames, [k.position[source] * unit for k in keys]))
    for index, source in enumerate((0, 2, 1)):
        curves.append(("rotation_euler", index, frames, [-k.rotation[source] for k in keys]))
    for index in range(3):
        curves.append(("scale", index, frames, [k.scale for k in keys]))
    return curves, [
        ('["mme_tr"]', 0, frames, [float(k.opacity) for k in keys]),
        ('["mme_si"]', 0, frames, [float(k.scale) for k in keys]),
    ]


def write_root_keys(root, acc, keys, unit, influence, extra):
    anim = root.animation_data or root.animation_data_create()
    low, high = frame_span([k.frame for k in keys] + [f for _path, frames, _values in extra for f in frames])
    kept = snapshot_outside([root], low, high, keep_all=lambda path: not accessory_owned_path(path))
    anim.action = None
    purge_actions(ACTION_PREFIX + "Acc_")
    first = keys[0]
    root.rotation_mode = "YXZ"
    root.location = (first.position[0] * unit, first.position[2] * unit, first.position[1] * unit)
    root.rotation_euler = (-first.rotation[0], -first.rotation[2], -first.rotation[1])
    root.scale = (first.scale,) * 3
    root["mme_tr"] = float(first.opacity)
    root["mme_si"] = float(first.scale)
    action = bpy.data.actions.new(ACTION_PREFIX + "Acc_" + short_name(acc.name, 40))
    transforms, props = root_curves(keys, unit)
    write_curves(root, action, transforms, "LINEAR", "Object Transforms")
    write_curves(root, action, props + [(path, 0, frames, values) for path, frames, values in extra], "LINEAR")
    write_curves(root, action, [(path, 0, frames, values) for path, frames, values in influence], "CONSTANT")
    restore_points(kept)


# Visibility goes to the children as hide flags, like mmd_tools: one shared constant action when the PMM keys
# hide the accessory at times, static flags otherwise.
def write_visibility(root, acc, keys, shown):
    hidden = not shown
    toggles = shown and {bool(k.visible) for k in keys} != {True}
    action = None
    if toggles:
        action = bpy.data.actions.new(ACTION_PREFIX + "Acc_vis_" + short_name(acc.name, 30))
    for child in root.children:
        anim = child.animation_data
        if anim is not None:
            anim.action = None
        child.hide_viewport = hidden
        child.hide_render = hidden
        if action is not None:
            frames = [k.frame for k in keys]
            values = [0.0 if k.visible else 1.0 for k in keys]
            write_curves(child, action, [("hide_viewport", 0, frames, values), ("hide_render", 0, frames, values)], "CONSTANT")


def accessory_record(root):
    try:
        record = json.loads(root.get(ACCESSORY_KEY_RECORD) or "null")
    except (TypeError, ValueError):
        return None
    return record if isinstance(record, dict) else None


# Writes the PMM's keys to the root and the visibility to the children, unless the last sync's keys are still
# in place unchanged, or were edited in Blender and the PMM keys did not change (see keying_decision).
# Hand-made keys outside the PMM range and on other channels are preserved when keys are rewritten.
def key_accessory(root, acc, unit, shown=True, parent_signature=None, influence=(), extra=()):
    if getattr(acc, "standalone", False):
        for name in ("mme_tr", "mme_si"):
            if name not in root:
                root[name] = 1.0
        mme_link_visibility(root)
        return False
    mme_link_visibility(root)
    keys = list({k.frame: k for k in sorted(acc.keys, key=lambda k: k.frame)}.values())
    source = accessory_keys_fingerprint(keys, shown, extra)
    settings = json.dumps([round(float(unit), 6), parent_signature])
    record = accessory_record(root)
    write, message, level = keying_decision(root, record, source, settings)
    if message:
        getattr(log, level)("accessory '%s': %s", acc.name, message)
    if not write:
        return False
    write_root_keys(root, acc, keys, unit, influence, extra)
    write_visibility(root, acc, keys, shown)
    root[ACCESSORY_KEY_RECORD] = json.dumps({
        "source": source,
        "settings": settings,
        "action": owned_action(root).name,
        "digest": action_digest(owned_action(root)),
        "visible": visibility_state(root),
    })
    return True


# Passes the root's opacity (mme_tr) to the plain meshes as 1 - Tr (mme_fade), which their materials read. A
# simple-expression driver, so it runs with Python auto-execution off. Effect passes read Tr themselves.
def link_fade(root):
    path = '["%s"]' % ACCESSORY_FADE_PROP
    for child in root.children:
        if child.type != "MESH" or child.get(MME_DRAW_PROP):
            continue
        if ACCESSORY_FADE_PROP not in child:
            child[ACCESSORY_FADE_PROP] = 0.0
        anim = child.animation_data
        if anim is not None and anim.drivers.find(path) is not None:
            continue
        driver = child.driver_add(path).driver
        driver.type = "SCRIPTED"
        variable = driver.variables.new()
        variable.name = "tr"
        variable.targets[0].id = root
        variable.targets[0].data_path = '["mme_tr"]'
        driver.expression = "1 - tr"
