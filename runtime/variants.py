import json
import numpy as np

from ..common.animation import iter_fcurves
from ..common.constants import (
    MME_VARIANT_FRAMES,
    MME_VARIANT_MAX,
    PMM_FPS,
)
from ..common.controls import (
    mme_control_use_prop,
    mme_load_controls,
    mme_param_meta,
)
from ..common.scene_utils import scene_fps
from ..fx.params import fx_structural_params
from ..fx.semantics import (
    FxSceneState,
    fx_classify_global,
)
from ..fx.types import (
    fx_is_numeric,
    fx_resolve_type,
)


# The root's animation channels by data path and array index.
def mme_root_fcurves(root):
    out = {}
    anim = root.animation_data
    if anim is None or anim.action is None:
        return out
    for fcurve in iter_fcurves(anim.action):
        out.setdefault(fcurve.data_path, {})[fcurve.array_index] = fcurve
    return out


# Parameter value at a frame, overlaying keyed channels on the current static value; None if the path is not
# animated.
def mme_animated_value(curves, path, frame, current, size):
    found = curves.get(path)
    if not found:
        return None
    if current is None:
        current = [0.0] * size
    elif not hasattr(current, "__len__"):
        current = [current]
    values = [float(v) for v in current]
    values = (values + [0.0] * size)[:size]
    for index, fcurve in found.items():
        if index < size:
            values[index] = float(fcurve.evaluate(frame))
    return values


# Rounded key coordinates of an animated parameter, so a change in its keys invalidates caches.
def mme_param_key_fingerprint(root, prop):
    curves = mme_root_fcurves(root).get('["%s"]' % prop)
    if not curves:
        return None
    out = []
    for index in sorted(curves):
        points = curves[index].keyframe_points
        coords = np.zeros(len(points) * 2)
        points.foreach_get("co", coords)
        out.append([index] + [round(float(v), 5) for v in coords])
    return {"keyed": out}


# Samples the timeline for animated structural parameters, opacity, controller properties and use-switches.
# Returns per-frame variants to probe,
# extreme-value combinations, and the highest values (the build uses those so the instance count covers the
# whole animation).
def mme_build_variants(scene, root, runtime, tr_names):
    program = runtime.program
    curves = mme_root_fcurves(root)
    if scene is None:
        return [], []
    controls = mme_load_controls(root)
    meta = mme_param_meta(root)
    structural = [n for n in fx_structural_params(program) if n in meta]
    keyed = [n for n in structural if '["%s"]' % meta[n]["prop"] in curves]
    paths = {'["%s"]' % meta[n]["prop"] for n in keyed} | {'["mme_tr"]'}
    for desc in controls.values():
        if desc.get("kind") == "prop":
            paths.add('["%s"]' % desc.get("prop", ""))
        use = mme_control_use_prop(desc)
        if use:
            paths.add('["%s"]' % use)
    start, end = scene.frame_start, scene.frame_end
    frames = set(np.linspace(start, end, min(MME_VARIANT_FRAMES, max(1, end - start + 1))).round().astype(int).tolist())
    for path in paths:
        for fcurve in curves.get(path, {}).values():
            for point in fcurve.keyframe_points:
                frames.add(int(round(point.co[0])))
    frames = sorted(f for f in frames if start <= f <= end)
    if len(frames) > MME_VARIANT_MAX:
        frames = [frames[int(i)] for i in np.linspace(0, len(frames) - 1, MME_VARIANT_MAX).round()]
    fps = scene_fps(scene) or PMM_FPS
    variants = []
    ranges = {}
    for frame in frames:
        params = {}
        for name in keyed:
            info = meta[name]
            values = mme_animated_value(curves, '["%s"]' % info["prop"], frame, root.get(info["prop"]), int(info.get("size", 1)))
            if values is not None:
                params[name] = values
                low, high = ranges.get(name, (values, values))
                ranges[name] = ([min(a, b) for a, b in zip(low, values)], [max(a, b) for a, b in zip(high, values)])
        found = {}
        tr = mme_animated_value(curves, '["mme_tr"]', frame, root.get("mme_tr", 1.0), 1)
        if tr is None:
            tr = [float(root.get("mme_tr", 1.0))]
        for name in tr_names:
            found[name] = tr
        for gname, desc in controls.items():
            use_prop = mme_control_use_prop(desc)
            use = mme_animated_value(curves, '["%s"]' % use_prop, frame, root.get(use_prop, True), 1) if use_prop else None
            use = 1.0 if use is None else float(use[0] >= 0.5)
            if desc.get("kind") == "prop":
                value = mme_animated_value(curves, '["%s"]' % desc.get("prop", ""), frame, root.get(desc.get("prop", ""), 0.0), 1)
                if value is None:
                    value = [float(root.get(desc.get("prop", ""), 0.0))]
                found[gname] = [value[0] * use]
            elif use_prop and desc.get("kind") == "model":
                g = program.globals.get(gname)
                t = fx_resolve_type(g.type, program) if g is not None else ("f", ())
                if fx_is_numeric(t) and t[0] == "b":
                    found[gname] = [use]
        variants.append({"time": frame / fps, "params": params, "controls": found})
    highs = {n: ranges[n][1] for n in ranges}
    combos = []
    if ranges:
        combos.append({n: ranges[n][0] for n in ranges})
        combos.append({n: ranges[n][1] for n in ranges})
        if len(ranges) > 1:
            for n in ranges:
                combos.append({n: ranges[n][0]})
                combos.append({n: ranges[n][1]})
    return variants, combos, highs


# Copy of a scene state with a variant's time, parameters and controls applied.
def mme_apply_variant(state, variant=None, params=None):
    probe = FxSceneState()
    probe.__dict__.update(state.__dict__)
    probe.controls = dict(state.controls)
    probe.params = dict(state.params)
    if variant is not None:
        probe.time = variant["time"]
        probe.params.update(variant["params"])
        probe.controls.update(variant["controls"])
    if params:
        probe.params.update(params)
    return probe


# Rounded, JSON-ready description of a probe state; it is the unit the pre-cull cache stores results for.
def mme_condition(probe, value, tr_names, skip_params=()):
    controls = dict(probe.controls)
    if value is not None:
        for name in tr_names:
            controls[name] = [value]
    params = {k: v for k, v in probe.params.items() if k not in skip_params}
    return {
        "time": round(float(probe.time), 5),
        "world": [round(float(x), 4) for x in np.asarray(probe.world, dtype=np.float64).reshape(-1)],
        "params": {k: [round(float(x), 5) for x in v] for k, v in sorted(params.items()) if v is not None},
        "controls": {k: [round(float(x), 5) for x in v] for k, v in sorted(controls.items()) if v is not None},
    }


# Cache key of a condition. With 'include' it keeps only the values the vertex shader actually read (and time
# only if it read time),
# so unrelated animation does not invalidate cached results.
def mme_condition_key(condition, include=None, program=None):
    if include is None:
        return json.dumps(condition, sort_keys=True)
    timed = False
    for name in include:
        g = program.globals.get(name) if program is not None else None
        source = fx_classify_global(g, program) if g is not None else None
        if source is not None and source[0] in ("time", "elapsed"):
            timed = True
    projected = {
        "time": condition["time"] if timed else 0.0,
        "world": condition.get("world"),
        "params": {k: v for k, v in condition["params"].items() if k in include},
        "controls": {k: v for k, v in condition["controls"].items() if k in include},
    }
    return json.dumps(projected, sort_keys=True)


# Rebuilds a scene state from a stored condition.
def mme_condition_state(base, condition):
    probe = mme_apply_variant(base)
    probe.time = condition["time"]
    if condition.get("world") is not None:
        probe.world = np.asarray(condition["world"], dtype=np.float64).reshape(4, 4)
    probe.params.update(condition["params"])
    probe.controls.update(condition["controls"])
    return probe


# Conditions that share parameters and world matrix are stored as one group of rows.
def mme_group_key(condition, include):
    params = condition["params"] if include is None else {k: v for k, v in condition["params"].items() if k in include}
    return json.dumps({"params": params, "world": condition.get("world")}, sort_keys=True)
