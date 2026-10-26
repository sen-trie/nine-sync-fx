from ..common.animation import (
    frame_span,
    iter_fcurves,
    purge_actions,
    restore_points,
    snapshot_outside,
)
from ..common.constants import (
    ACCESSORY_OWNED_PATHS,
    ACTION_PREFIX,
    MME_CONTROL_PREFIX,
)
from ..common.scene_utils import short_name
from ..runtime.visibility import mme_link_visibility


# Channels the sync writes itself (transform, opacity, scale and the control properties); everything else on
# the root is the user's.
def accessory_owned_path(path):
    return path in ACCESSORY_OWNED_PATHS or path.startswith('["%s' % MME_CONTROL_PREFIX)


# Writes the PMM's keys to the root: location and rotation with the Y/Z swap, scale, opacity (mme_tr) and
# scale (mme_si), all linear.
# Hand-made keys outside the PMM range and on other channels are preserved. Visibility keys go to the children
# as constant hide flags.
def key_accessory(root, acc, unit):
    if getattr(acc, "standalone", False):
        for name in ("mme_tr", "mme_si"):
            if name not in root:
                root[name] = 1.0
        mme_link_visibility(root)
        return
    anim = root.animation_data or root.animation_data_create()
    low, high = frame_span(k.frame for k in acc.keys)
    kept = snapshot_outside([root], low, high, keep_all=lambda path: not accessory_owned_path(path))
    anim.action = None
    purge_actions(ACTION_PREFIX + "Acc_")
    root.rotation_mode = "YXZ"
    for key in acc.keys:
        root.location = (
            key.position[0] * unit,
            key.position[2] * unit,
            key.position[1] * unit,
        )
        root.rotation_euler = (-key.rotation[0], -key.rotation[2], -key.rotation[1])
        root.scale = (key.scale, key.scale, key.scale)
        root["mme_tr"] = float(key.opacity)
        root["mme_si"] = float(key.scale)
        root.keyframe_insert("location", frame=key.frame)
        root.keyframe_insert("rotation_euler", frame=key.frame)
        root.keyframe_insert("scale", frame=key.frame)
        root.keyframe_insert('["mme_tr"]', frame=key.frame)
        root.keyframe_insert('["mme_si"]', frame=key.frame)
    action = anim.action
    if action is not None:
        for fcurve in iter_fcurves(action):
            for point in fcurve.keyframe_points:
                point.interpolation = "LINEAR"
    restore_points(kept)
    if anim.action is not None:
        anim.action.name = ACTION_PREFIX + "Acc_" + short_name(acc.name, 40)
    mme_link_visibility(root)
    visible = {bool(k.visible) for k in acc.keys}
    for child in root.children:
        child_anim = child.animation_data
        if child_anim is not None:
            child_anim.action = None
        child.hide_viewport = False
        child.hide_render = False
        if visible == {True}:
            continue
        for key in acc.keys:
            child.hide_viewport = not key.visible
            child.hide_render = not key.visible
            child.keyframe_insert("hide_viewport", frame=key.frame)
            child.keyframe_insert("hide_render", frame=key.frame)
        child_action = child.animation_data.action if child.animation_data else None
        if child_action is not None:
            child_action.name = ACTION_PREFIX + "Acc_vis_" + short_name(acc.name, 30)
            for fcurve in iter_fcurves(child_action):
                for point in fcurve.keyframe_points:
                    point.interpolation = "CONSTANT"
