import hashlib
import json
import numpy as np
import os
import time

from ..common.constants import (
    MME_ANALYSIS_DTYPE,
    MME_SIMPLIFY_BUDGET,
    MME_SIMPLIFY_TR,
    MME_SIMPLIFY_VARIANTS,
    MME_STRIP_CACHE_ENTRIES,
    MME_STRIP_CACHE_VERSION,
)
from ..common.log import log
from ..fx.types import fx_is_numeric
from .cache_store import (
    mme_cache_touch,
    mme_cache_trim,
)
from .progress import mme_progress_within
from .variants import (
    mme_apply_variant,
    mme_condition,
    mme_condition_key,
)
from .vs_eval import (
    MmePassData,
    mme_clip_world,
    mme_loop_values,
    mme_run_vs,
)


# Detects meshes laid out as strips: a grid whose UV rows form a ladder (every row the same width, faces only
# between neighbouring rows).
# Returns the grid description, or None when the mesh is not one; beams and ribbons in effects are typically
# built this way.
def mme_strip_layout(uvs, local_faces):
    count = len(uvs)
    if count < 16 or not local_faces:
        return None
    for axis in (1, 0):
        values = np.round(uvs[:, axis], 7)
        params, rows_of = np.unique(values, return_inverse=True)
        if len(params) < 8:
            continue
        sizes = np.bincount(rows_of)
        if sizes.min() != sizes.max() or sizes[0] > 8:
            continue
        width = int(sizes[0])
        cols = np.round(uvs[:, 1 - axis], 7)
        order = np.lexsort((cols, rows_of))
        grid = order.reshape(len(params), width)
        col_values = cols[grid]
        if not np.allclose(col_values, col_values[0][None, :]):
            continue
        column = np.empty(count, dtype=np.int64)
        column[grid.reshape(-1)] = np.tile(np.arange(width), len(params))
        ok = True
        lower = []
        for face in local_faces:
            rows = {int(rows_of[v]) for v in face}
            if len(rows) != 2 or max(rows) - min(rows) != 1:
                ok = False
                break
            lower.append(min(rows))
        if not ok:
            continue
        return {"params": params, "grid": grid, "row": rows_of, "column": column, "lower": np.array(lower, dtype=np.int64)}
    return None


# Probe states used to judge how much a strip can be simplified: opacity samples, parameter extremes and a
# spread of keyed variants.
def mme_strip_runs(runtime, state, tr_names):
    variants, combos = getattr(runtime, "build_variants", ([], []))
    runs = [(mme_apply_variant(state), value) for value in (MME_SIMPLIFY_TR if tr_names else [None])]
    for combo in combos:
        runs.append((mme_apply_variant(state, params=combo), None))
    if variants:
        picks = np.linspace(0, len(variants) - 1, min(len(variants), MME_SIMPLIFY_VARIANTS)).round().astype(int)
        for index in sorted(set(picks.tolist())):
            runs.append((mme_apply_variant(state, variants[index]), None))
    return runs


# Evaluates the vertex shader over the selected strip rows for every probe state and instance; returns world
# positions, varyings and culling per sample.
def mme_strip_samples(runtime, draw, state, used, rows, layout, instances, tr_names, density, runs=None):
    vertices = layout["grid"][rows].reshape(-1)
    per = len(vertices)
    src = np.tile(used[vertices], instances)
    inst = np.repeat(np.arange(instances, dtype=np.int64), per)
    data = MmePassData(draw, src, inst, mme_loop_values(draw, inst, density), runtime)
    if runs is None:
        runs = mme_strip_runs(runtime, state, tr_names)
    positions, varyings, culled = [], [], []
    for step, (probe, value) in enumerate(runs):
        mme_progress_within(0.65 + 0.15 * (step + 1) / len(runs), "simplifying strips %d/%d" % (step + 1, len(runs)))
        if value is not None:
            for name in tr_names:
                probe.controls[name] = [value]
        outputs, _ex = mme_run_vs(runtime, data, probe, MME_ANALYSIS_DTYPE)
        world, cull = mme_clip_world(outputs, probe, data.count)
        comps = []
        for semantic, out in sorted(outputs.items()):
            if semantic == "POSITION0" or not fx_is_numeric(out.t):
                continue
            for c in out.c:
                comps.append(np.broadcast_to(np.asarray(c, dtype=np.float64), (data.count,)))
        positions.append(world)
        varyings.append(np.stack(comps, axis=-1) if comps else np.zeros((data.count, 0)))
        culled.append(cull)
    width = layout["grid"].shape[1]
    shape = (len(runs), instances, len(rows), width)
    pos = np.nan_to_num(np.stack(positions).reshape(shape + (3,)))
    var = np.nan_to_num(np.stack(varyings).reshape(shape + (-1,)))
    cul = np.stack(culled).reshape(shape)
    return pos, var, cul


# Chooses which strip rows to keep: recursively splits a span at the row that interpolates worst (position and
# varying error against a tolerance relative to the effect's size).
# Only rows where a lane's cull state changes are forced, so a span between them is uniformly culled or visible
# per lane; forcing whole spans kept every row of Thunder's Wave ring. If the time budget runs out, the
# remaining rows are kept as they are.
def mme_simplify_rows(params, pos, var, cul, tolerance):
    n = len(params)
    if n <= 2:
        return list(range(n))
    visible = ~cul
    if visible.any():
        seen = pos[visible]
        size = float(np.linalg.norm(seen.max(0) - seen.min(0)))
    else:
        size = 0.0
    pos_tol = max(size * tolerance, 1e-9)
    if pos.shape[3] > 1:
        width = np.linalg.norm(pos[:, :, :, -1] - pos[:, :, :, 0], axis=-1)
        row_tol = np.clip(width * 0.5, pos_tol * 0.1, pos_tol)
    else:
        row_tol = np.full(pos.shape[:3], pos_tol)
    samples, instances, rows, columns = cul.shape
    lanes = samples * instances * columns
    P = np.ascontiguousarray(pos.transpose(2, 0, 1, 3, 4).reshape(rows, lanes, 3), dtype=np.float32)
    T = np.ascontiguousarray(np.repeat(row_tol.transpose(2, 0, 1).reshape(rows, samples * instances), columns, axis=1), dtype=np.float32)
    C = np.ascontiguousarray(cul.transpose(2, 0, 1, 3).reshape(rows, lanes))
    channels = var.shape[-1]
    if channels:
        span = var.reshape(-1, channels)
        ranges = np.maximum(span.max(0) - span.min(0), 1e-6)
        var_tol = max(tolerance * 4.0, 1e-3)
        V = np.ascontiguousarray((var / (ranges * var_tol)).transpose(2, 0, 1, 3, 4).reshape(rows, lanes, channels), dtype=np.float32)
    else:
        V = None
    params = np.asarray(params, dtype=np.float64)
    changes = np.nonzero((C[1:] != C[:-1]).any(axis=1))[0]
    keep = {0, n - 1} | {int(r) for r in changes} | {int(r) + 1 for r in changes}
    fixed = sorted(keep)
    stack = list(zip(fixed[:-1], fixed[1:]))
    deadline = time.time() + MME_SIMPLIFY_BUDGET
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        if time.time() > deadline:
            keep.update(range(a, b + 1))
            continue
        span_param = max(params[b] - params[a], 1e-12)
        t = ((params[a + 1:b] - params[a]) / span_param).astype(np.float32)
        pred = np.multiply((P[b] - P[a])[None], t[:, None, None])
        pred += P[a][None]
        pred -= P[a + 1:b]
        np.square(pred, out=pred)
        score = pred.sum(axis=-1)
        np.sqrt(score, out=score)
        score /= np.minimum(np.minimum(T[a], T[b])[None], T[a + 1:b])
        if V is not None:
            vpred = np.multiply((V[b] - V[a])[None], t[:, None, None])
            vpred += V[a][None]
            vpred -= V[a + 1:b]
            np.abs(vpred, out=vpred)
            np.maximum(score, vpred.max(axis=-1), out=score)
        score[C[a + 1:b]] = 0.0
        worst = score.max(axis=1)
        k = int(np.argmax(worst))
        if worst[k] > 1.0:
            split = a + 1 + k
            keep.add(split)
            stack.append((a, split))
            stack.append((split, b))
    return sorted(keep)


# Loads cached row choices; any read problem means an empty cache.
def mme_strip_cache_load(path):
    if path is None or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        mme_cache_touch(path)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# Atomic write of the row choices, bounded to the newest entries.
def mme_strip_cache_save(path, data):
    if path is None:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if len(data) > MME_STRIP_CACHE_ENTRIES:
            data = dict(list(data.items())[-MME_STRIP_CACHE_ENTRIES:])
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(temp, path)
        mme_cache_trim()
    except OSError as error:
        log.debug("strip cache not written: %s", error)


# Merges strip rows that interpolate within the tolerance: rows are simplified per run of connected rows and
# faces are re-stitched between the kept rows.
# Results are cached under a key built from the probe conditions, camera and tolerance.
def mme_simplify_strip(runtime, draw, state, used, local_faces, instances, tr_names, density, tolerance, cache_path=None):
    if tolerance <= 0 or not local_faces:
        return local_faces
    layout = mme_strip_layout(runtime.uvs[used], local_faces)
    if layout is None:
        return local_faces
    lower = layout["lower"]
    grid = layout["grid"]
    template = {}
    for face, r in zip(local_faces, lower):
        template.setdefault(int(r), []).append(face)
    pairs = sorted(template)
    runs, current = [], [pairs[0]]
    for r in pairs[1:]:
        if r == current[-1] + 1:
            current.append(r)
        else:
            runs.append(current)
            current = [r]
    runs.append(current)
    column = layout["column"]
    row_of = layout["row"]
    out = []
    strip_path = cache_path[:-4] + "_strip.json" if cache_path else None
    cached = mme_strip_cache_load(strip_path)
    changed = False
    sample_runs = mme_strip_runs(runtime, state, tr_names)
    include = getattr(runtime, "last_reads", None)
    signature = [mme_condition_key(mme_condition(probe, value, tr_names), include, runtime.program) for probe, value in sample_runs]
    camera = [np.round(np.asarray(m, dtype=np.float64), 4).tolist() for m in (state.view, state.proj, state.world)]
    for run in runs:
        rows = np.arange(run[0], run[-1] + 2)
        if len(rows) <= 3:
            for r in run:
                out.extend(template[r])
            continue
        key = hashlib.md5(json.dumps([MME_STRIP_CACHE_VERSION, signature, camera, round(tolerance, 7), instances, density, int(rows[0]), int(rows[-1])], sort_keys=True).encode("utf-8")).hexdigest()
        if key in cached:
            kept_rows = rows[np.asarray(cached[key], dtype=np.int64)]
        else:
            pos, var, cul = mme_strip_samples(runtime, draw, state, used, rows, layout, instances, tr_names, density, sample_runs)
            kept = mme_simplify_rows(layout["params"][rows], pos, var, cul, tolerance)
            cached[key] = [int(k) for k in kept]
            changed = True
            kept_rows = rows[kept]
        for a, b in zip(kept_rows[:-1], kept_rows[1:]):
            for face in template[int(a)]:
                out.append(tuple(int(grid[b, column[v]]) if row_of[v] == a + 1 else int(v) for v in face))
    if changed:
        mme_strip_cache_save(strip_path, cached)
    return out
