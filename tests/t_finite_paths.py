import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import importlib
strips = importlib.import_module("ninesync_fx.runtime.strips")
numpy_backend = importlib.import_module("ninesync_fx.fx.interp.numpy_backend")

failed = []
checks = 0


def bits(a):
    a = np.asarray(a)
    if a.dtype.kind != "f":
        return a.dtype.str, a.shape, a.tobytes()
    nan = np.isnan(a)
    return a.dtype.str, a.shape, np.where(nan, 0, a).tobytes(), nan.tobytes()


def check(label, got, want):
    global checks
    checks += 1
    if type(got) is not type(want) or bits(got) != bits(want):
        failed.append(label)


def fmod_reference(a, b):
    return np.fmod(a, b, dtype=np.float32)


rng = np.random.default_rng(7)
fin = np.finfo(np.float32)
specials = np.array([0.0, -0.0, 1.0, -1.0, 255.999, -255.999, 256.0, -256.0, 512.0, -512.0, 1e-45, -1e-45, fin.tiny, -fin.tiny,
                     fin.max, -fin.max, np.inf, -np.inf, np.nan, 2.0 ** 24, 2.0 ** 24 + 2, 2.0 ** 40 + 256, -(2.0 ** 40) - 512, 1.5, -1.5], dtype=np.float32)
dividends = np.concatenate([specials,
                            (rng.standard_normal(400_000) * 10.0 ** rng.integers(-44, 38, 400_000)).astype(np.float32),
                            rng.integers(-2 ** 20, 2 ** 20, 100_000).astype(np.float32)])
divisors = [256.0, -256.0, 1.0, 2.0, 0.5, 0.0078125, 3.0, 7.3, -0.1, 1e-30, 1e30, 1e-45, 0.0, -0.0, np.inf, -np.inf, np.nan, fin.max]
backend = numpy_backend.FxNumpyBackend(np.float32)
with np.errstate(all="ignore"):
    for b in divisors:
        check("mod scalar divisor %r" % b, backend.mod(dividends, np.float32(b)), fmod_reference(dividends, np.float32(b)))
        check("mod python divisor %r" % b, backend.mod(dividends, b), fmod_reference(dividends, b))
    array_b = np.concatenate([specials[::-1], (rng.standard_normal(len(dividends) - len(specials)) * 10.0 ** rng.integers(-44, 38, len(dividends) - len(specials))).astype(np.float32)])
    check("mod array divisor", backend.mod(dividends, array_b), fmod_reference(dividends, array_b))
    check("mod swapped operands", backend.mod(array_b, dividends), fmod_reference(array_b, dividends))
    wide = rng.standard_normal(50_000) * 1e3
    check("mod float64 dividend", backend.mod(wide, 256.0), fmod_reference(wide, 256.0))
    check("mod float64 divisor", backend.mod(dividends[:1000], np.float64(0.1)), fmod_reference(dividends[:1000], np.float64(0.1)))
    ints = rng.integers(-2 ** 40, 2 ** 40, 50_000)
    check("mod int64 dividend", backend.mod(ints, 256.0), fmod_reference(ints, 256.0))
    check("mod int divisor", backend.mod(dividends[:1000], 7), fmod_reference(dividends[:1000], 7))
    check("mod python scalars", backend.mod(-1000.5, 256.0), fmod_reference(-1000.5, 256.0))
    check("mod 0-d arrays", backend.mod(np.float32(-0.0), np.float32(3.0)), fmod_reference(np.float32(-0.0), np.float32(3.0)))
    check("mod strided", backend.mod(dividends[::3], 256.0), fmod_reference(dividends[::3], 256.0))
    check("mod broadcast", backend.mod(dividends[:600].reshape(200, 3), np.array([256.0, -0.5, 3.0], dtype=np.float32)), fmod_reference(dividends[:600].reshape(200, 3), np.array([256.0, -0.5, 3.0], dtype=np.float32)))
    finite = dividends[np.isfinite(dividends)]
    for b in [256.0, -256.0, 1.0, -1.0, 2.0, 4096.0, 2.0 ** 20, 2.0 ** 64, 2.0 ** 127, -(2.0 ** 127), 0.5, 2.0 ** -149, 3.0, 255.0, 257.0]:
        check("mod finite dividends, divisor %r" % b, backend.mod(finite, b), fmod_reference(finite, b))
        check("mod finite dividends, float32 divisor %r" % b, backend.mod(finite, np.float32(b)), fmod_reference(finite, np.float32(b)))
        check("mod finite float64 dividends, divisor %r" % b, backend.mod(wide, b), fmod_reference(wide, b))
    multiples = (np.arange(-4096, 4097, dtype=np.float32) * 256.0).astype(np.float32)
    check("mod exact multiples keep the dividend's zero sign", backend.mod(multiples, 256.0), fmod_reference(multiples, 256.0))
    check("mod subnormal dividends", backend.mod(np.array([1e-45, -1e-45, fin.tiny, -fin.tiny, -0.0], dtype=np.float32), 256.0), fmod_reference(np.array([1e-45, -1e-45, fin.tiny, -fin.tiny, -0.0], dtype=np.float32), 256.0))
    check("mod largest finite dividends", backend.mod(np.array([fin.max, -fin.max, 2.0 ** 127, 3e38], dtype=np.float32), 2.0 ** 127), fmod_reference(np.array([fin.max, -fin.max, 2.0 ** 127, 3e38], dtype=np.float32), 2.0 ** 127))
    backend64 = numpy_backend.FxNumpyBackend(np.float64)
    check("mod float64 backend", backend64.mod(wide, 7.3), np.fmod(wide, 7.3, dtype=np.float64))


# MmeStripSamples.put and .prepared as they were before the finite fast paths and lane dedup, kept as the oracle.
class ReferenceSamples(strips.MmeStripSamples):
    def __init__(self, runs, instances, rows, width, store=None):
        super().__init__(runs, instances, rows, width, store)
        self.widths = np.empty((runs, instances, rows), dtype=np.float64) if width > 1 else None

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


def poison(a, rate, kinds):
    a = a.copy()
    if rate:
        hit = rng.random(a.shape) < rate
        a[hit] = rng.choice(np.array(kinds, dtype=a.dtype), int(hit.sum()))
    return a


def channel(kind, count, rate):
    if kind == "f32":
        return poison(rng.standard_normal(count).astype(np.float32), rate, [np.nan, np.inf, -np.inf])
    if kind == "f64":
        return poison(rng.standard_normal(count) * 1e-3 + 1.0 / 3.0, rate, [np.nan, np.inf, -np.inf])
    if kind == "f32_in_f64":
        return rng.standard_normal(count).astype(np.float32).astype(np.float64)
    if kind == "constant":
        return np.full(count, 0.25, dtype=np.float32)
    if kind == "scalar":
        return np.float32(-0.0)
    if kind == "strided":
        return poison(rng.standard_normal(count * 2).astype(np.float32), rate, [np.nan])[::2]
    if kind == "int":
        return rng.integers(-5, 5, count)
    if kind == "signed_zeros":
        return rng.choice(np.array([0.0, -0.0], dtype=np.float32), count)
    if kind == "huge":
        return poison(np.full(count, 3e38, dtype=np.float32), rate, [np.inf])
    raise ValueError(kind)


def compare_samples(label, new, ref):
    check(label + " lo", new.lo, ref.lo)
    check(label + " hi", new.hi, ref.hi)
    global checks
    checks += 1
    same_range = new.vmin is None and ref.vmin is None or (np.array_equal(new.vmin, ref.vmin, equal_nan=True) and np.array_equal(new.vmax, ref.vmax, equal_nan=True))
    if not same_range:
        failed.append(label + " ranges")
    for k, (a, b) in enumerate(zip(new.channels or [], ref.channels or [])):
        checks += 1
        if isinstance(a, tuple) or isinstance(b, tuple):
            if not (isinstance(a, tuple) and isinstance(b, tuple) and bits(a[1]) == bits(b[1])):
                failed.append("%s channel %d constant" % (label, k))
        elif a.dtype != b.dtype:
            failed.append("%s channel %d dtype" % (label, k))


# The lanes of an instance as raw bytes of every prepared buffer, so the stored and the reference lanes compare as sets.
def lane_groups(P, T, C, V, width):
    P, C = np.asarray(P), np.asarray(C)
    out = []
    for g in range(P.shape[1] // width):
        a, b = g * width, (g + 1) * width
        out.append(b"|".join([np.ascontiguousarray(P[:, a:b]).tobytes(), np.ascontiguousarray(T[:, a:b]).tobytes(), np.ascontiguousarray(C[:, a:b]).tobytes()]
                             + [np.ascontiguousarray(np.asarray(ch)[:, a:b]).tobytes() for ch in V]))
    return out


def duplicate_instances(world, cull, comps, n, rows, width):
    world = world.copy()
    w = world.reshape(n, rows * width, 3)
    copies = [(j, int(rng.integers(0, j)), rng.choice(["full", "full", "position", "near", "recull"])) for j in range(1, n) if rng.random() < 0.6]
    full = [(j, src) for j, src, kind in copies if kind != "position"]
    for j, src, kind in copies:
        w[j] = w[src]
        if kind == "near" and w.dtype == np.float64:
            w[j] += w[src] * 1e-13
    if not isinstance(cull, bool):
        cull = cull.copy()
        c = cull.reshape(n, -1)
        for j, src, kind in copies:
            if kind != "position":
                c[j] = c[src][::-1] if kind == "recull" else c[src]
    out = []
    for comp in comps:
        if np.ndim(comp) and comp.flags.c_contiguous:
            comp = comp.copy()
            v = comp.reshape(n, -1)
            for j, src in full:
                v[j] = v[src]
        out.append(comp)
    return world, cull, out


kinds_all = ["f32", "f64", "f32_in_f64", "constant", "scalar", "strided", "int", "signed_zeros", "huge"]
cases = 0
unique_inputs = all_inputs = 0
with np.errstate(all="ignore"):
    for trial in range(18):
        runs = int(rng.integers(1, 4))
        instances = int(rng.integers(1, 7))
        rows = int(rng.integers(3, 24))
        width = int(rng.choice([1, 2, 5]))
        spill = bool(trial % 3 == 2)
        pos_rate = [0.0, 0.0, 0.02, 0.3][trial % 4]
        var_rate = [0.0, 0.05, 0.0, 0.2][(trial // 2) % 4]
        kinds = list(rng.choice(kinds_all, int(rng.integers(0, 5))))
        cull_mode = trial % 3
        batch = int(rng.integers(1, instances + 1))
        store_new, store_ref = strips.MmeStripStore(spill), strips.MmeStripStore(spill)
        if spill:
            strips.MME_SPILL_MIN_BYTES = 0
        new = strips.MmeStripSamples(runs, instances, rows, width, store_new)
        ref = ReferenceSamples(runs, instances, rows, width, store_ref)
        inputs = []
        repeats = trial % 2 == 1
        for s in range(runs):
            if repeats and s and rng.random() < 0.6:
                inputs.extend((s, i0, i1, world.copy(), np.copy(cull) if not isinstance(cull, bool) else cull, [np.copy(c) for c in comps]) for _, i0, i1, world, cull, comps in [x for x in inputs if x[0] == s - 1])
                continue
            for i0 in range(0, instances, batch):
                i1 = min(instances, i0 + batch)
                count = (i1 - i0) * rows * width
                world = poison(rng.standard_normal((count, 3)) * 50.0, pos_rate, [np.nan, np.inf, -np.inf])
                if trial % 5 == 4:
                    world = world.astype(np.float32)
                cull = False if cull_mode == 0 else (rng.random(count) < 0.3 if cull_mode == 1 else np.ones(count, dtype=bool))
                comps = [channel(kind, count, var_rate) for kind in kinds]
                if repeats and i1 - i0 > 1:
                    world, cull, comps = duplicate_instances(world, cull, comps, i1 - i0, rows, width)
                inputs.append((s, i0, i1, world, cull, comps))
        written = len(inputs) and sum(i1 - i0 for _, i0, i1, _, _, _ in inputs)
        for s, i0, i1, world, cull, comps in inputs:
            before = world.copy()
            new.put(s, i0, i1, world, cull, comps)
            ref.put(s, i0, i1, world.copy(), np.copy(cull), [np.copy(c) for c in comps])
            checks_before = bits(before)
            if bits(world) != checks_before:
                failed.append("trial %d put changed its input" % trial)
        label = "trial %d (runs %d instances %d rows %d width %d spill %s pos %.2f var %.2f %s)" % (trial, runs, instances, rows, width, spill, pos_rate, var_rate, ",".join(kinds) or "no channels")
        compare_samples(label + " after put", new, ref)
        for tolerance, block in ((0.0005, 1), (0.01, 1 << 30)):
            snapshot_new = (np.asarray(new.P).copy(), new.widths.copy() if new.widths is not None else None, [c if isinstance(c, tuple) else np.asarray(c).copy() for c in new.channels] if new.channels else new.channels)
            snapshot_ref = (np.asarray(ref.P).copy(), ref.widths.copy() if ref.widths is not None else None, [c if isinstance(c, tuple) else np.asarray(c).copy() for c in ref.channels] if ref.channels else ref.channels)
            P1, T1, C1, V1 = new.prepared(tolerance, block)
            P2, T2, C2, V2 = ref.prepared(tolerance, block)
            checks += 1
            if len(V1) != len(V2) or any(np.asarray(a).dtype != np.asarray(b).dtype for a, b in zip(V1, V2)):
                failed.append(label + " prepared channels")
            stored, reference = lane_groups(P1, T1, C1, V1, width), lane_groups(P2, T2, C2, V2, width)
            checks += 2
            if set(stored) != set(reference):
                failed.append(label + " prepared lane set")
            if len(reference) != written:
                failed.append(label + " reference lane count")
            unique_inputs += len(stored)
            all_inputs += len(reference)
            params = np.sort(rng.random(rows))
            check(label + " kept rows", np.asarray(strips.mme_simplify_prepared(params, P1, T1, C1, V1, block)), np.asarray(strips.mme_simplify_prepared(params, P2, T2, C2, V2, block)))
            new.widths, new.channels = snapshot_new[1], snapshot_new[2]
            ref.widths, ref.channels = snapshot_ref[1], snapshot_ref[2]
            if snapshot_new[2] is not None:
                new.channels = [c if isinstance(c, tuple) else c.copy() for c in snapshot_new[2]]
                ref.channels = [c if isinstance(c, tuple) else c.copy() for c in snapshot_ref[2]]
            cases += 1
        store_new.close(); store_ref.close()

def near_case(label, mode):
    global checks
    rows, width = 6, 2
    base = rng.uniform(-1.0, 1.0, (rows, 1, 3))
    first = np.concatenate([base, base + np.array([0.02, 0.0, 0.0])], axis=1)
    second = first.copy()
    cull_a = np.zeros((rows, width), dtype=bool)
    cull_a[2, 0] = True
    cull_b = cull_a.copy()
    channel_a = rng.standard_normal((rows, width)).astype(np.float32)
    channel_b = channel_a.copy()
    if mode == "width":
        second += 0.9 * (second.astype(np.float32).astype(np.float64) - second)
    elif mode == "cull":
        cull_b[2, 0], cull_b[3, 1] = False, True
    else:
        channel_b[[1, 4]] = channel_b[[4, 1]]
    world = np.concatenate([first, second]).reshape(-1, 3)
    cull = np.concatenate([cull_a, cull_b]).reshape(-1)
    comps = [np.concatenate([channel_a, channel_b]).reshape(-1)]
    new = strips.MmeStripSamples(1, 2, rows, width)
    ref = ReferenceSamples(1, 2, rows, width)
    new.put(0, 0, 2, world, cull, comps)
    ref.put(0, 0, 2, world.copy(), cull.copy(), [c.copy() for c in comps])
    checks += 2
    if not np.array_equal(first.astype(np.float32), second.astype(np.float32)):
        failed.append(label + " setup: float32 positions differ")
    P1, T1, C1, V1 = new.prepared(0.01, 1 << 30)
    P2, T2, C2, V2 = ref.prepared(0.01, 1 << 30)
    stored, reference = lane_groups(P1, T1, C1, V1, width), lane_groups(P2, T2, C2, V2, width)
    if len(set(reference)) != 2 or set(stored) != set(reference):
        failed.append(label + " lane set")


with np.errstate(all="ignore"):
    near_case("float64 widths under equal float32 positions", "width")
    near_case("same-count cull change", "cull")
    near_case("varyings in another order", "channel")

print("finite fast paths match the original on", checks - len(failed), "of", checks, "checks (%d strip cases, %d of %d instance lanes stored)" % (cases, unique_inputs, all_inputs))
if unique_inputs >= all_inputs:
    failed.append("no repeated instance was dropped")
print("FAILED:", failed or "none")
