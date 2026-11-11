import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import importlib
strips = importlib.import_module("ninesync_fx.runtime.strips")
constants = importlib.import_module("ninesync_fx.common.constants")
strips.MME_SPILL_MIN_BYTES = 0


def reference(params, pos, var, cul, tolerance):
    n = len(params)
    if n <= 2:
        return list(range(n))
    pos = np.nan_to_num(pos.astype(np.float64))
    var = np.nan_to_num(var.astype(np.float64))
    visible = ~cul
    size = float(np.linalg.norm(pos[visible].max(0) - pos[visible].min(0))) if visible.any() else 0.0
    pos_tol = max(size * tolerance, 1e-9)
    if pos.shape[3] > 1:
        row_tol = np.clip(np.linalg.norm(pos[:, :, :, -1] - pos[:, :, :, 0], axis=-1) * 0.5, pos_tol * 0.1, pos_tol)
    else:
        row_tol = np.full(pos.shape[:3], pos_tol)
    samples, instances, rows, columns = cul.shape
    lanes = samples * instances * columns
    P = np.ascontiguousarray(pos.transpose(2, 0, 1, 3, 4).reshape(rows, lanes, 3), dtype=np.float32)
    T = np.ascontiguousarray(np.repeat(row_tol.transpose(2, 0, 1).reshape(rows, samples * instances), columns, axis=1), dtype=np.float32)
    C = np.ascontiguousarray(cul.transpose(2, 0, 1, 3).reshape(rows, lanes))
    channels = var.shape[-1]
    V = None
    if channels:
        span = var.reshape(-1, channels)
        ranges = np.maximum(span.max(0) - span.min(0), 1e-6)
        V = np.ascontiguousarray((var / (ranges * max(tolerance * 4.0, 1e-3))).transpose(2, 0, 1, 3, 4).reshape(rows, lanes, channels), dtype=np.float32)
    params = np.asarray(params, dtype=np.float64)
    changes = np.nonzero((C[1:] != C[:-1]).any(axis=1))[0]
    keep = {0, n - 1} | {int(r) for r in changes} | {int(r) + 1 for r in changes}
    fixed = sorted(keep)
    stack = list(zip(fixed[:-1], fixed[1:]))
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        t = ((params[a + 1:b] - params[a]) / max(params[b] - params[a], 1e-12)).astype(np.float32)
        pred = np.multiply((P[b] - P[a])[None], t[:, None, None]); pred += P[a][None]; pred -= P[a + 1:b]
        np.square(pred, out=pred); score = pred.sum(axis=-1); np.sqrt(score, out=score)
        score /= np.minimum(np.minimum(T[a], T[b])[None], T[a + 1:b])
        if V is not None:
            vpred = np.multiply((V[b] - V[a])[None], t[:, None, None]); vpred += V[a][None]; vpred -= V[a + 1:b]
            np.abs(vpred, out=vpred); np.maximum(score, vpred.max(axis=-1), out=score)
        score[C[a + 1:b]] = 0.0
        worst = score.max(axis=1)
        k = int(np.argmax(worst))
        if worst[k] > 1.0:
            keep.add(a + 1 + k); stack.append((a, a + 1 + k)); stack.append((a + 1 + k, b))
    return sorted(keep)


def streamed(params, pos, var, cul, tolerance, budget):
    samples, instances, rows, columns = cul.shape
    store = strips.MmeStripStore(budget["spill"])
    out = strips.MmeStripSamples(samples, instances, rows, columns, store)
    batch = max(1, budget["strips"] // (rows * columns))
    for s in range(samples):
        for i0 in range(0, instances, batch):
            i1 = min(instances, i0 + batch)
            comps = [var[s, i0:i1, ..., k].reshape(-1) for k in range(var.shape[-1])]
            out.put(s, i0, i1, pos[s, i0:i1].reshape(-1, 3), cul[s, i0:i1].reshape(-1), comps)
    P, T, C, V = out.prepared(tolerance, budget["block"])
    try:
        return strips.mme_simplify_prepared(params, P, T, C, V, budget["block"])
    finally:
        del P, T, C, V
        store.close()


rng = np.random.default_rng(7)
failed = []
cases = 0
for trial in range(12):
    samples, instances, rows, columns = int(rng.integers(1, 4)), int(rng.integers(1, 6)), int(rng.integers(3, 400)), 2
    t = np.linspace(0, 1, rows)
    centre = np.stack([np.cos(t * 6), np.sin(t * 4), t * 2], -1)
    pos = centre[None, None, :, None, :] + rng.normal(0, 0.002 * (trial % 3), (samples, instances, rows, columns, 3))
    pos[..., 1, 0] += 0.05
    channels = int(rng.integers(0, 6))
    var = rng.normal(0, 1, (samples, instances, rows, columns, channels)).astype(np.float32).astype(np.float64)
    if channels:
        var[..., 0] = 1.0
    if channels > 2:
        var[..., 1] = np.round(t * 10)[None, None, :, None]
    if trial == 5 and channels:
        var[0, 0, 3, 0, -1] = np.nan
    if trial == 7 and channels:
        var[..., -1] = 0.1
    cul = rng.random((samples, instances, rows, columns)) < (0.02 if trial % 2 else 0.0)
    params = np.cumsum(rng.random(rows) + 0.1)
    for tol in (0.0005, 0.01):
        want = reference(params, pos, var, cul, tol)
        for label, budget in constants.MME_MEMORY_BUDGETS.items():
            tiny = dict(budget, strips=max(1, rows * columns), block=4096) if label == "LOW" else budget
            got = streamed(params, pos, var, cul, tol, tiny)
            cases += 1
            if got != want:
                failed.append((trial, tol, label, len(got), len(want)))
print("streamed strip analysis matches the reference on", cases - len(failed), "of", cases, "cases")
print("FAILED:", failed or "none")
