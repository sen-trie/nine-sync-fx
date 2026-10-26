import bpy
import math

from .constants import (
    FRAME_EPSILON,
    PMM_FPS,
)
from .log import log
from .scene_utils import (
    fps_differs,
    scene_fps,
)


# Blender 5 keeps fcurves in layers/strips/channelbags; older versions expose action.fcurves directly.
def iter_fcurves(action):
    fcurves = getattr(action, "fcurves", None)
    if fcurves is not None:
        return list(fcurves)
    found = []
    for layer in action.layers:
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                found.extend(bag.fcurves)
    return found


# Counterpart of iter_fcurves for the layered action layout.
def remove_fcurve(action, fcurve):
    fcurves = getattr(action, "fcurves", None)
    if fcurves is not None:
        fcurves.remove(fcurve)
        return True
    for layer in action.layers:
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                if any(f == fcurve for f in bag.fcurves):
                    bag.fcurves.remove(fcurve)
                    return True
    return False


# Removes unused actions with this prefix (the MMDBridge_ actions written by syncs).
def purge_actions(prefix):
    removed = 0
    for action in list(bpy.data.actions):
        if action.name.startswith(prefix) and action.users == 0:
            bpy.data.actions.remove(action)
            removed += 1
    if removed:
        log.info("purged %d unused action(s) with prefix '%s'", removed, prefix)


# Returns an empty (inf, -inf) span for no frames so min/max comparisons stay valid.
def frame_span(frames):
    frames = list(frames)
    if not frames:
        return math.inf, -math.inf
    return min(frames), max(frames)


# A sync rewrites the keys inside the PMM frame range. This saves every key outside [low, high] (all keys of
# paths that keep_all selects)
# so hand-made animation survives; constraint curves are skipped because they are rebuilt.
def snapshot_outside(owners, low, high, sources=None, keep_all=None):
    saved = []
    for owner in owners:
        key = owner.as_pointer()
        if sources is not None and key in sources:
            action = sources[key]
        else:
            data = owner.animation_data
            action = data.action if data else None
        if action is None:
            continue
        for fcurve in iter_fcurves(action):
            if fcurve.data_path.startswith("constraints["):
                continue
            points = []
            everything = keep_all is not None and keep_all(fcurve.data_path)
            for point in fcurve.keyframe_points:
                frame = point.co[0]
                if everything or frame < low - FRAME_EPSILON or frame > high + FRAME_EPSILON:
                    points.append(
                        (
                            frame,
                            point.co[1],
                            point.interpolation,
                            point.easing,
                            tuple(point.handle_left),
                            tuple(point.handle_right),
                            point.handle_left_type,
                            point.handle_right_type,
                            point.type,
                        )
                    )
            if points:
                group = fcurve.group.name if fcurve.group else ""
                saved.append(
                    (owner, fcurve.data_path, fcurve.array_index, group, points)
                )
    return saved


# Looks up one channel on the owner's current action.
def find_fcurve(owner, data_path, index):
    data = owner.animation_data
    action = data.action if data else None
    if action is None:
        return None
    for fcurve in iter_fcurves(action):
        if fcurve.data_path == data_path and fcurve.array_index == index:
            return fcurve
    return None


# Inserts a key so Blender creates the channel. Index 0 retries with -1 (the whole property) so non-array
# properties can be keyed too.
def create_fcurve(owner, data_path, index, group, frame):
    for attempt in ((index, -1) if index == 0 else (index,)):
        try:
            if group:
                owner.keyframe_insert(
                    data_path=data_path, index=attempt, frame=frame, group=group
                )
            else:
                owner.keyframe_insert(data_path=data_path, index=attempt, frame=frame)
        except (TypeError, RuntimeError, ValueError, KeyError):
            continue
        found = find_fcurve(owner, data_path, index)
        if found is not None:
            return found
    return None


# Writes back what snapshot_outside saved, creating the channel if the sync removed it. FAST skips per-key re-
# sorting; fcurve.update() sorts once.
def restore_points(saved):
    restored = 0
    for owner, data_path, index, group, points in saved:
        fcurve = find_fcurve(owner, data_path, index)
        if fcurve is None:
            fcurve = create_fcurve(owner, data_path, index, group, points[0][0])
        if fcurve is None:
            log.warning(
                "could not restore %d key(s) on '%s' %s[%d]",
                len(points),
                owner.name,
                data_path,
                index,
            )
            continue
        for (
            frame,
            value,
            interpolation,
            easing,
            left,
            right,
            left_type,
            right_type,
            key_type,
        ) in points:
            point = fcurve.keyframe_points.insert(
                frame, value, options={"FAST"}, keyframe_type=key_type
            )
            point.interpolation = interpolation
            point.easing = easing
            point.handle_left_type = left_type
            point.handle_right_type = right_type
            point.handle_left = left
            point.handle_right = right
        fcurve.update()
        restored += len(points)
    return restored


# Factor from PMM 30 fps to the scene rate, or 1 when retiming is off or the rates match.
def retime_factor(scene, settings):
    if not settings.retime_fps or not fps_differs(scene):
        return 1.0
    return scene_fps(scene) / PMM_FPS


# Rescales key frames. With hold_cuts, keys that were one frame apart stay one frame apart so a hard cut
# (visibility toggle) cannot collapse into the same frame.
def retime_keys(keys, factor, hold_cuts=False):
    scaled = {}
    previous_old = None
    previous_new = None
    for key in sorted(keys, key=lambda k: k.frame):
        new = int(round(key.frame * factor))
        if hold_cuts and previous_old is not None and key.frame - previous_old <= 1:
            new = previous_new + 1
        scaled[new] = key._replace(frame=new)
        previous_old, previous_new = key.frame, new
    return [scaled[frame] for frame in sorted(scaled)]
