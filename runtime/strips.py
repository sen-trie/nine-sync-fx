import hashlib
import json
import numpy as np
import os
import tempfile
import time

from ..common.constants import (
    MME_ANALYSIS_DTYPE,
    MME_MEMORY_BUDGETS,
    MME_SIMPLIFY_BUDGET,
    MME_SIMPLIFY_TR,
    MME_SIMPLIFY_VARIANTS,
    MME_SPILL_FOLDER,
    MME_SPILL_MIN_BYTES,
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
    mme_budget,
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


# Large row buffers of a strip analysis; under the Low memory budget they are file-backed in the temp folder so the
# operating system can page them out. close() releases and deletes the files.
class MmeStripStore:
    def __init__(self, spill):
        self.spill = spill
        self.files = []

    def array(self, shape, dtype):
        size = int(np.prod(shape)) * np.dtype(dtype).itemsize
        if not self.spill or size < MME_SPILL_MIN_BYTES:
            return np.empty(shape, dtype=dtype)
        folder = os.path.join(tempfile.gettempdir(), MME_SPILL_FOLDER)
        os.makedirs(folder, exist_ok=True)
        handle, path = tempfile.mkstemp(suffix=".buf", dir=folder)
        os.close(handle)
        self.files.append(path)
        return np.memmap(path, dtype=dtype, mode="w+", shape=shape)

    def close(self):
        for path in self.files:
            try:
                os.remove(path)
            except OSError as error:
                log.debug("strip buffer %s not removed: %s", path, error)
        self.files = []


# Strip samples already in the layout the row search scores, streamed: each probe state is evaluated in instance batches
# sized by the memory budget and written straight into float32 row-major buffers, instead of keeping every state's
# float64 results and stacking, cleaning and transposing them afterwards. The values are the same as before: the shader
# runs in float32, positions are cast once from float64, the row tolerances, the effect size and the varying ranges are
# accumulated in float64 from the same numbers, and varyings are scaled at the end in float64 as before. Each varying
# channel has its own buffer, allocated only once the channel varies (a constant channel never changes a score), and
# switched to float64 if float32 cannot hold its values exactly.
class MmeStripSamples:
    def __init__(self, runs, instances, rows, width, store=None):
        self.S, self.I, self.R, self.W = runs, instances, rows, width
        self.lanes = runs * instances * width
        self.store = store or MmeStripStore(False)
        self.P = self.store.array((rows, self.lanes, 3), np.float32)
        self.C = np.empty((rows, self.lanes), dtype=bool)
        self.widths = np.empty((runs, instances, rows), dtype=np.float64) if width > 1 else None
        self.channels = None
        self.vmin = self.vmax = None
        self.lo = np.full(3, np.inf)
        self.hi = np.full(3, -np.inf)

    def put(self, run, i0, i1, world, cull, comps):
        n = i1 - i0
        count = n * self.R * self.W
        lane0 = (run * self.I + i0) * self.W
        lane1 = lane0 + n * self.W
        pos = np.nan_to_num(np.asarray(world, dtype=np.float64)).reshape(n, self.R, self.W, 3)
        cul = np.broadcast_to(np.asarray(cull, dtype=bool), (count,)).reshape(n, self.R, self.W)
        visible = ~cul
        if visible.any():
            seen = pos[visible]
            np.minimum(self.lo, seen.min(0), out=self.lo)
            np.maximum(self.hi, seen.max(0), out=self.hi)
        if self.widths is not None:
            self.widths[run, i0:i1] = np.linalg.norm(pos[:, :, -1] - pos[:, :, 0], axis=-1)
        self.P[:, lane0:lane1] = pos.transpose(1, 0, 2, 3).reshape(self.R, n * self.W, 3)
        del pos
        self.C[:, lane0:lane1] = cul.transpose(1, 0, 2).reshape(self.R, n * self.W)
        if self.channels is None:
            self.channels = [None] * len(comps)
            self.vmin = np.full(len(comps), np.inf)
            self.vmax = np.full(len(comps), -np.inf)
        for k, c in enumerate(comps):
            raw = np.broadcast_to(np.asarray(c), (count,))
            wide = np.nan_to_num(raw.astype(np.float64))
            low, high = wide.min(), wide.max()
            self.vmin[k] = min(self.vmin[k], low)
            self.vmax[k] = max(self.vmax[k], high)
            del wide
            block = raw.reshape(n, self.R, self.W).transpose(1, 0, 2).reshape(self.R, n * self.W)
            held = self.channels[k]
            if held is None or isinstance(held, tuple):
                first = raw[:1]
                if np.isfinite(first).all() and abs(float(first[0])) < 1e20 and np.array_equal(raw, np.broadcast_to(first, (count,))) and (held is None or np.array_equal(held[1], first)):
                    self.channels[k] = ("constant", first.copy())
                    continue
                dtype = np.float32
                if raw.dtype != np.float32 and not np.array_equal(raw.astype(np.float32).astype(np.float64), raw.astype(np.float64), equal_nan=True):
                    dtype = np.float64
                array = self.store.array((self.R, self.lanes), dtype)
                if held is not None:
                    array[:, :lane0] = held[1].astype(dtype)[0]
                self.channels[k] = held = array
            elif held.dtype == np.float32 and raw.dtype != np.float32:
                if not np.array_equal(raw.astype(np.float32).astype(np.float64), raw.astype(np.float64), equal_nan=True):
                    wider = self.store.array((self.R, self.lanes), np.float64)
                    wider[:, :lane0] = held[:, :lane0]
                    self.channels[k] = held = wider
            held[:, lane0:lane1] = block

    def prepared(self, tolerance, block):
        size = float(np.linalg.norm(self.hi - self.lo)) if np.isfinite(self.lo).all() else 0.0
        pos_tol = max(size * tolerance, 1e-9)
        if self.widths is not None:
            row_tol = np.clip(self.widths * 0.5, pos_tol * 0.1, pos_tol)
        else:
            row_tol = np.full((self.S, self.I, self.R), pos_tol)
        self.widths = None
        T = np.repeat(row_tol.transpose(2, 0, 1).reshape(self.R, self.S * self.I).astype(np.float32), self.W, axis=1)
        del row_tol
        V = []
        if self.channels:
            ranges = np.maximum(self.vmax - self.vmin, 1e-6)
            var_tol = max(tolerance * 4.0, 1e-3)
            scale = ranges * var_tol
            step = max(1, block // max(1, self.lanes * 16))
            for k, held in enumerate(self.channels):
                if isinstance(held, tuple):
                    continue
                out = held if held.dtype == np.float32 else self.store.array(held.shape, np.float32)
                for r0 in range(0, self.R, step):
                    chunk = held[r0:r0 + step].astype(np.float64)
                    np.nan_to_num(chunk, copy=False)
                    chunk /= scale[k]
                    out[r0:r0 + step] = chunk
                    del chunk
                V.append(out)
            self.channels = None
        return self.P, T, self.C, V


# Evaluates the vertex shader over the selected strip rows for every probe state and instance, in batches bounded by the
# memory budget, and returns the prepared buffers for mme_simplify_prepared.
def mme_strip_samples(runtime, draw, state, used, rows, layout, instances, tr_names, density, runs=None, tolerance=0.0, store=None):
    vertices = layout["grid"][rows].reshape(-1)
    per = len(vertices)
    width = layout["grid"].shape[1]
    if runs is None:
        runs = mme_strip_runs(runtime, state, tr_names)
    budget = mme_budget(runtime)
    batch = max(1, budget["strips"] // max(1, per))
    out = MmeStripSamples(len(runs), instances, len(rows), width, store)
    for step, (probe, value) in enumerate(runs):
        mme_progress_within(0.65 + 0.15 * (step + 1) / len(runs), "simplifying strips %d/%d" % (step + 1, len(runs)))
        if value is not None:
            for name in tr_names:
                probe.controls[name] = [value]
        for i0 in range(0, instances, batch):
            i1 = min(instances, i0 + batch)
            src = np.tile(used[vertices], i1 - i0)
            inst = np.repeat(np.arange(i0, i1, dtype=np.int64), per)
            data = MmePassData(draw, src, inst, mme_loop_values(draw, inst, density), runtime)
            outputs, _ex = mme_run_vs(runtime, data, probe, MME_ANALYSIS_DTYPE)
            world, cull = mme_clip_world(outputs, probe, data.count)
            comps = []
            for semantic, item in sorted(outputs.items()):
                if semantic == "POSITION0" or not fx_is_numeric(item.t):
                    continue
                comps.extend(item.c)
            out.put(step, i0, i1, world, cull, comps)
            del data, outputs, world, cull, comps
    return out.prepared(tolerance, budget["block"])


# Chooses which strip rows to keep from full sample arrays (runs, instances, rows, columns): prepares the buffers and runs
# mme_simplify_prepared.
def mme_simplify_rows(params, pos, var, cul, tolerance):
    if len(params) <= 2:
        return list(range(len(params)))
    samples, instances, rows, columns = cul.shape
    out = MmeStripSamples(samples, instances, rows, columns)
    flat = var.reshape(samples, instances, rows, columns, -1)
    for s in range(samples):
        comps = [flat[s, ..., k].reshape(-1) for k in range(flat.shape[-1])]
        out.put(s, 0, instances, pos[s].reshape(-1, 3), cul[s].reshape(-1), comps)
    P, T, C, V = out.prepared(tolerance, MME_MEMORY_BUDGETS["BALANCED"]["block"])
    return mme_simplify_prepared(params, P, T, C, V)


# Recursively splits a span at the row that interpolates worst (position and varying error against a tolerance relative
# to the effect's size). Only rows where a lane's cull state changes are forced, so a span between them is uniformly
# culled or visible per lane; forcing whole spans kept every row of Thunder's Wave ring. If the time budget runs out, the
# remaining rows are kept as they are. Interior rows are scored in blocks bounded by the memory budget, one varying
# channel at a time; a row's score depends only on that row, so this matches scoring the whole span at once.
def mme_simplify_prepared(params, P, T, C, V, block=MME_MEMORY_BUDGETS["BALANCED"]["block"]):
    n = len(params)
    if n <= 2:
        return list(range(n))
    lanes = P.shape[1]
    step = max(1, block // max(1, lanes * 4 * 8))
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
        t_all = ((params[a + 1:b] - params[a]) / span_param).astype(np.float32)
        Pa = np.asarray(P[a])
        dP = (np.asarray(P[b]) - Pa)[None]
        Va = [np.asarray(ch[a]) for ch in V]
        dV = [(np.asarray(ch[b]) - va)[None] for ch, va in zip(V, Va)]
        bound = np.minimum(T[a], T[b])[None]
        worst = np.empty(b - a - 1, dtype=np.float32)
        for c0 in range(0, b - a - 1, step):
            c1 = min(b - a - 1, c0 + step)
            r0, r1 = a + 1 + c0, a + 1 + c1
            t = t_all[c0:c1]
            pred = np.multiply(dP, t[:, None, None])
            pred += Pa[None]
            pred -= P[r0:r1]
            np.square(pred, out=pred)
            score = pred.sum(axis=-1)
            del pred
            np.sqrt(score, out=score)
            score /= np.minimum(bound, T[r0:r1])
            if V:
                vmax = None
                for ch, va, dv in zip(V, Va, dV):
                    vpred = np.multiply(dv, t[:, None])
                    vpred += va[None]
                    vpred -= ch[r0:r1]
                    np.abs(vpred, out=vpred)
                    if vmax is None:
                        vmax = vpred
                    else:
                        np.maximum(vmax, vpred, out=vmax)
                        del vpred
                np.maximum(score, vmax, out=score)
                del vmax
            score[C[r0:r1]] = 0.0
            worst[c0:c1] = score.max(axis=1)
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
            store = MmeStripStore(mme_budget(runtime).get("spill", False))
            try:
                P, T, C, V = mme_strip_samples(runtime, draw, state, used, rows, layout, instances, tr_names, density, sample_runs, tolerance, store)
                kept = mme_simplify_prepared(layout["params"][rows], P, T, C, V, mme_budget(runtime)["block"])
                del P, T, C, V
            finally:
                store.close()
            cached[key] = [int(k) for k in kept]
            changed = True
            kept_rows = rows[kept]
        for a, b in zip(kept_rows[:-1], kept_rows[1:]):
            for face in template[int(a)]:
                out.append(tuple(int(grid[b, column[v]]) if row_of[v] == a + 1 else int(v) for v in face))
    if changed:
        mme_strip_cache_save(strip_path, cached)
    return out
