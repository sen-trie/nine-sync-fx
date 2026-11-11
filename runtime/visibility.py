import bpy
import numpy as np

from ..common.animation import (
    iter_fcurves,
    remove_fcurve,
)
from ..common.constants import (
    ACCESSORY_VISIBLE_GROUP,
    ACCESSORY_VISIBLE_MODIFIER,
    ACCESSORY_VISIBLE_PROP,
    MME_ANALYSIS_DTYPE,
    MME_GATE_EPS,
    MME_GATE_TR,
    MME_PRUNE_TR,
)
from ..common.log import log
from .frame_state import mme_prune_state
from .progress import mme_progress_within
from .variants import (
    mme_apply_variant,
    mme_condition,
    mme_condition_key,
)
from .vs_eval import (
    MmePassData,
    mme_clip_culled,
    mme_run_vs,
)


# Migrates old files that hid accessories with a property, an F-curve or a modifier to the current scheme
# (hide_viewport and hide_render on the meshes, like mmd_tools),
# converting any keys, and removes the old driver, modifier and node group.
def mme_link_visibility(root):
    curve = None
    anim = root.animation_data
    if anim is not None and anim.action is not None:
        curve = next((f for f in iter_fcurves(anim.action) if f.data_path == '["%s"]' % ACCESSORY_VISIBLE_PROP), None)
    legacy_keys = [(p.co[0], p.co[1] >= 0.5) for p in curve.keyframe_points] if curve is not None else []
    legacy_value = root.get(ACCESSORY_VISIBLE_PROP)
    if curve is not None:
        remove_fcurve(anim.action, curve)
    if ACCESSORY_VISIBLE_PROP in root:
        del root[ACCESSORY_VISIBLE_PROP]
    for child in root.children:
        anim = child.animation_data
        if anim is not None:
            for fcurve in list(anim.drivers):
                if fcurve.data_path in ("hide_viewport", "hide_render") or ACCESSORY_VISIBLE_MODIFIER in fcurve.data_path:
                    anim.drivers.remove(fcurve)
        modifier = child.modifiers.get(ACCESSORY_VISIBLE_MODIFIER)
        if modifier is not None:
            child.modifiers.remove(modifier)
        if legacy_keys:
            for frame, shown in legacy_keys:
                child.hide_viewport = not shown
                child.hide_render = not shown
                child.keyframe_insert("hide_viewport", frame=frame)
                child.keyframe_insert("hide_render", frame=frame)
        elif legacy_value is not None:
            child.hide_viewport = not bool(legacy_value)
            child.hide_render = not bool(legacy_value)
    group = bpy.data.node_groups.get(ACCESSORY_VISIBLE_GROUP)
    if group is not None and group.users == 0:
        bpy.data.node_groups.remove(group)


# Remembers the hide flags and the action of the first child so a rebuild can put them back on the new pass
# objects.
def mme_capture_visibility(root):
    for child in root.children:
        if child.animation_data is not None and child.animation_data.action is not None:
            return child.animation_data.action, child.hide_viewport, child.hide_render
    for child in root.children:
        return None, child.hide_viewport, child.hide_render
    return None, False, False


# The hide flags and action of one pass object, in the form mme_restore_visibility takes.
def mme_capture_pass(child):
    action = child.animation_data.action if child.animation_data is not None else None
    return action, child.hide_viewport, child.hide_render


# Applies what mme_capture_visibility saved to freshly built children.
def mme_restore_visibility(children, captured):
    action, hide_viewport, hide_render = captured
    for child in children:
        child.hide_viewport = hide_viewport
        child.hide_render = hide_render
        if action is not None:
            anim = child.animation_data_create()
            anim.action = action
            if anim.action_suitable_slots:
                anim.action_slot = anim.action_suitable_slots[0]


# Property getter for the 'Visible' checkbox: visible if any mesh child is shown in the viewport.
def accessory_visible_get(self):
    children = [c for c in self.children if c.type == "MESH"] or list(self.children)
    if not children:
        return True
    return any(not c.hide_viewport for c in children)


# Property setter for the 'Visible' checkbox: sets viewport and render visibility on every child, as mmd_tools
# does.
def accessory_visible_set(self, value):
    for child in self.children:
        child.hide_viewport = not value
        child.hide_render = not value


# Builds the list of scene states (opacity samples, parameter extremes, keyed variants) under which the vertex
# shader is probed to find which vertices can ever be visible.
def mme_visibility_runs(runtime, data, state, tr_names, gates):
    samples = (MME_GATE_TR if gates else MME_PRUNE_TR) if tr_names else [None]
    variants, combos = getattr(runtime, "build_variants", ([], []))
    base = mme_prune_state(state)
    runs = []
    for combo in [None] + list(combos):
        for value in samples:
            runs.append((mme_apply_variant(base, params=combo), value))
    for variant in variants:
        runs.append((mme_apply_variant(base, variant), None))
    return base, runs


# Runs the vertex shader for each probe state and marks vertices as kept when they are inside the clip volume
# in any state, and as opened when an alpha gate lets them through.
# Vertices already settled drop out of later runs, and a state equal to an evaluated one on every global that
# one read is skipped: the shader is deterministic, so it would give the same answer. Returns the global names
# the shader read, used to narrow cache keys.
def mme_evaluate_visibility(runtime, data, runs, tr_names, gates, keep, opened, label=""):
    pending = np.arange(data.count)
    subset = data
    reads = set()
    seen = {}
    skipped = 0
    for step, (probe, value) in enumerate(runs):
        mme_progress_within(0.1 + 0.5 * (step + 1) / max(1, len(runs)), "%svisibility %d/%d" % (label, step + 1, len(runs)))
        if not len(pending):
            break
        if value is not None:
            for name in tr_names:
                probe.controls[name] = [value]
        condition = mme_condition(probe, value, tr_names)
        if any(mme_condition_key(condition, read, runtime.program) in keys for read, keys in seen.items()):
            skipped += 1
            continue
        outputs, ex = mme_run_vs(runtime, subset, probe, MME_ANALYSIS_DTYPE)
        read = frozenset(getattr(ex.env, "reads", set()))
        reads |= read
        seen.setdefault(read, set()).add(mme_condition_key(condition, read, runtime.program))
        count = len(pending)
        keep[pending] |= ~mme_clip_culled(outputs, probe, count)
        for semantic, k in gates:
            found = outputs.get(semantic)
            if found is None or k >= len(found.c):
                opened[pending] = True
                continue
            values = np.broadcast_to(np.asarray(found.c[k]), (count,))
            opened[pending] |= ~(np.abs(values) <= MME_GATE_EPS)
        settled = keep[pending] & opened[pending]
        if settled.any():
            pending = pending[~settled]
            subset = data.subset(pending, runtime, subset, np.flatnonzero(~settled))
    if skipped:
        log.debug("%svisibility: %d of %d probe state(s) repeated an evaluated one and were skipped", label, skipped, len(runs))
    return reads
