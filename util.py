import bpy
import math
import re

from .common import (
    FRAME_EPSILON,
    PMM_FPS,
    find_armature,
    fps_differs,
    iter_fcurves,
    log,
    scene_fps,
)


def addon_loaded(modules):
    try:
        names = list(bpy.context.preferences.addons.keys())
    except Exception:
        return False
    return any(name.rsplit(".", 1)[-1] in modules for name in names)


def target_usable(obj):
    return (
        obj is not None
        and len(obj.users_collection) > 0
        and find_armature(obj) is not None
    )


def current_scale(settings):
    if settings.auto_scale:
        for item in settings.targets:
            if item.enabled and target_usable(item.target):
                size = getattr(item.target, "empty_display_size", 0.0)
                if size > 0.0:
                    return size * 0.2
    return settings.scale


def is_mmd_root(self, obj):
    return getattr(obj, "mmd_type", "") == "ROOT"


def find_root_for(model):
    roots = [
        o
        for o in bpy.data.objects
        if getattr(o, "mmd_type", "") == "ROOT" and target_usable(o)
    ]
    log.debug(
        "auto-match for PMM model '%s' / '%s' among roots: %s",
        model.name,
        model.name_en,
        [(o.name, o.mmd_root.name, o.mmd_root.name_e) for o in roots],
    )
    for obj in roots:
        if model.name and obj.mmd_root.name == model.name:
            return obj
    for obj in roots:
        if model.name_en and obj.mmd_root.name_e == model.name_en:
            return obj
    for obj in roots:
        if obj.name in (model.name, model.name_en):
            return obj
    for obj in roots:
        for name in (obj.mmd_root.name, obj.mmd_root.name_e, obj.name):
            if name and model.name and (name in model.name or model.name in name):
                return obj
    return None


def purge_actions(prefix):
    removed = 0
    for action in list(bpy.data.actions):
        if action.name.startswith(prefix) and action.users == 0:
            bpy.data.actions.remove(action)
            removed += 1
    if removed:
        log.info("purged %d unused action(s) with prefix '%s'", removed, prefix)


def frame_span(frames):
    frames = list(frames)
    if not frames:
        return math.inf, -math.inf
    return min(frames), max(frames)


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


def find_fcurve(owner, data_path, index):
    data = owner.animation_data
    action = data.action if data else None
    if action is None:
        return None
    for fcurve in iter_fcurves(action):
        if fcurve.data_path == data_path and fcurve.array_index == index:
            return fcurve
    return None


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


def retime_factor(scene, settings):
    if not settings.retime_fps or not fps_differs(scene):
        return 1.0
    return scene_fps(scene) / PMM_FPS


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


def redact_paths(text):
    text = re.sub(r"([A-Za-z]:[\\/]+Users[\\/]+)[^\\/\"']+", r"\1<user>", text)
    text = re.sub(r"(/(?:home|Users)/)[^/\"'\s]+", r"\1<user>", text)
    return text
