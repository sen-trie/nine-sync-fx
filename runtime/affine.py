import numpy as np

from ..common.constants import (
    MME_PRUNE_TR,
    MME_SWAP3,
)
from ..fx.semantics import FxSceneState
from ..fx.types import fx_is_numeric
from ..nodes.names import mme_varying_name
from .frame_state import mme_prune_state
from .vs_eval import (
    MmePassData,
    mme_clip_culled,
    mme_loop_values,
    mme_run_vs,
)

MME_AFFINE_BLOCKS = ("p", "n", "uv")
MME_AFFINE_GROUPS = ((), ("uv",), ("p",), ("n",), ("p", "uv"), ("n", "uv"), ("p", "n"), ("p", "n", "uv"))
MME_AFFINE_INSTANCES = 24
MME_AFFINE_VERTICES = 256
MME_AFFINE_TOLERANCE = 1e-5
MME_AFFINE_NOISE = 1e-6
MME_AFFINE_SPAN = 0.05
MME_AFFINE_RCOND = 1e-9


# What the instanced output of a LoopByCount pass needs: the source vertices the vertex shader runs on for each
# particle ('refs'), the constant weights that turn their results into per-particle coefficients
# (coefficient = weights @ values at refs; one matrix per feature group, rows in group order then the constant),
# the group every vertex shader output uses, and the weights for the instance transform in mesh coordinates.
class MmeAffinePlan:
    def __init__(self, refs, weights, channels, position):
        self.refs = refs
        self.weights = weights
        self.channels = channels
        self.position = position


def mme_affine_features(runtime, src):
    return {
        "p": np.asarray(runtime.positions[src], dtype=np.float64),
        "n": np.asarray(runtime.normals[src], dtype=np.float64),
        "uv": np.asarray(runtime.uvs[src], dtype=np.float64)[:, :2],
    }


def mme_feature_matrix(features, group, rows=None):
    blocks = [features[b] if rows is None else features[b][rows] for b in group]
    count = len(next(iter(features.values()))) if rows is None else len(rows)
    return np.hstack(blocks + [np.ones((count, 1))])


# Adds vertices to 'chosen' until they span the affine hull of one feature block: each time the vertex
# farthest from the span of those already chosen (the vertex farthest from the centre when none is). Reusing
# the vertices other blocks chose keeps the reference set small. Returns the list and the block's rank.
def mme_span_vertices(values, chosen):
    centred = values - values.mean(axis=0)
    scale = float(np.abs(centred).max()) if len(values) else 0.0
    if scale <= 0.0:
        return (chosen or [0]), 0
    chosen = list(chosen) or [int(np.argmax(np.linalg.norm(centred, axis=1)))]
    offsets = values - values[chosen[0]]
    basis = []

    def residual(rows):
        rest = rows.copy()
        for axis in basis:
            rest -= np.outer(rest @ axis, axis)
        return rest

    for vertex in chosen[1:]:
        rest = residual(offsets[vertex][None])[0]
        length = float(np.linalg.norm(rest))
        if length > MME_AFFINE_SPAN * scale and len(basis) < values.shape[1]:
            basis.append(rest / length)
    while len(basis) < values.shape[1]:
        lengths = np.linalg.norm(residual(offsets), axis=1)
        best = int(np.argmax(lengths))
        if lengths[best] <= MME_AFFINE_SPAN * scale:
            break
        chosen.append(best)
        basis.append(residual(offsets[best][None])[0] / lengths[best])
    return chosen, len(basis)


def mme_copy_state(state):
    copy = FxSceneState()
    copy.__dict__.update(state.__dict__)
    copy.controls = dict(state.controls)
    return copy


# States the fit is checked in: the build frame's camera and the far probe camera the pruning uses, at every Tr
# probe value, plus the build frame a few seconds later.
def mme_affine_states(state, tr_names):
    states = []
    for base in (mme_copy_state(state), mme_prune_state(state)):
        for value in MME_PRUNE_TR if tr_names else [None]:
            probe = mme_copy_state(base)
            if value is not None:
                for name in tr_names:
                    probe.controls[name] = [value]
            states.append(probe)
    later = mme_copy_state(state)
    later.time = float(getattr(state, "time", 0.0)) + 2.37
    states.append(later)
    return states


# Decides whether a LoopByCount pass can be drawn as instances: the clip-space position must be one affine
# function of the source position per particle (the node tree's clip-to-object mapping is affine in clip space
# for perspective cameras), a particle's vertices must be culled all together, and each output gets the
# smallest feature group (position, normal, UV) it is an affine function of per particle, or None. All of it
# is checked numerically on sample particles and probe states, rebuilding through exactly the reference
# vertices and weights the node tree will use. Returns (plan, "") or (None, reason).
def mme_affine_plan(runtime, draw, state, src_one, density, tr_names, instances):
    if not draw.loops:
        return None, "not a LoopByCount pass"
    features = mme_affine_features(runtime, src_one)
    count = len(src_one)
    chosen = []
    ranks = {}
    for block in MME_AFFINE_BLOCKS:
        chosen, ranks[block] = mme_span_vertices(features[block], chosen)
    if ranks["p"] < 3:
        return None, "the mesh is flat"
    refs = np.array(sorted(set(chosen)), dtype=np.int64)
    weights = {group: np.linalg.pinv(mme_feature_matrix(features, group, refs), rcond=MME_AFFINE_RCOND) for group in MME_AFFINE_GROUPS}
    mesh_features = {"p": features["p"] @ MME_SWAP3.T}
    transform = np.linalg.pinv(mme_feature_matrix(mesh_features, ("p",), refs), rcond=MME_AFFINE_RCOND)
    rng = np.random.default_rng(4321)
    if count <= MME_AFFINE_VERTICES:
        sub = np.arange(count)
    else:
        sub = np.unique(np.concatenate([refs, rng.choice(count, MME_AFFINE_VERTICES, replace=False)]))
    at_refs = np.searchsorted(sub, refs)
    picked = np.unique(np.linspace(0, max(0, instances - 1), min(instances, MME_AFFINE_INSTANCES)).round().astype(np.int64))
    per = len(sub)
    src = np.tile(src_one[sub], len(picked))
    inst = np.repeat(picked, per)
    data = MmePassData(draw, src, inst, mme_loop_values(draw, inst, density), runtime)
    sub_features = {b: features[b][sub] for b in MME_AFFINE_BLOCKS}
    predict = {group: mme_feature_matrix(sub_features, group) @ weights[group] for group in MME_AFFINE_GROUPS}
    passing = None
    live_any = False
    for probe in mme_affine_states(state, tr_names):
        outputs, _ex = mme_run_vs(runtime, data, probe)
        culled = mme_clip_culled(outputs, probe, data.count)
        culled = np.asarray(culled, dtype=bool).reshape(len(picked), per)
        partial = culled.any(axis=1) & ~culled.all(axis=1)
        if partial.any():
            return None, "particles are partly culled"
        live = ~culled.all(axis=1)
        if not live.any():
            continue
        live_any = True
        position = outputs.get("POSITION0")
        clip = np.stack([np.broadcast_to(np.asarray(c, dtype=np.float64), (data.count,)) for c in (list(position.c) + [1.0] * 4)[:4]])
        if not mme_affine_fits(clip.reshape(4, len(picked), per)[:, live], predict[("p",)], at_refs):
            return None, "the vertex shader does not move each particle by one affine transform"
        channels = {}
        for semantic, value in outputs.items():
            if semantic == "POSITION0" or not fx_is_numeric(value.t):
                continue
            for k, comp in enumerate(value.c):
                values = np.broadcast_to(np.asarray(comp, dtype=np.float64), (data.count,)).reshape(len(picked), per)[live]
                channels[mme_varying_name(semantic, k)] = {g for g in MME_AFFINE_GROUPS if mme_affine_fits(values[None], predict[g], at_refs)}
        if passing is None:
            passing = channels
        else:
            passing = {name: passing.get(name, set()) & groups for name, groups in channels.items()}
    if not live_any:
        return None, "no particle is ever drawn in the probe states"
    chosen = {name: next((g for g in MME_AFFINE_GROUPS if g in groups), None) for name, groups in sorted((passing or {}).items())}
    return MmeAffinePlan(refs, weights, chosen, transform), ""


# The outputs in 'names' that the plan cannot rebuild, for the log; empty when instancing is possible.
def mme_affine_missing(plan, names):
    return [name for name in names if plan.channels.get(name) is None]


# True when values (channels x particles x vertices) are reproduced, within tolerance, from their values at
# the reference vertices through 'predict' (vertices x refs).
def mme_affine_fits(values, predict, at_refs):
    rebuilt = values[:, :, at_refs] @ predict.T
    allowed = MME_AFFINE_TOLERANCE * np.ptp(values, axis=2) + MME_AFFINE_NOISE * np.abs(values).max(axis=2) + 1e-12
    error = np.abs(rebuilt - values).max(axis=2)
    return bool(np.isfinite(values).all() and (error <= allowed).all())
