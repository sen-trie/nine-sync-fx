import bpy
import hashlib
import json
import numpy as np
import os

from ..common.constants import (
    MME_GATE_ALPHA,
    MME_GATE_EPS,
    MME_GATE_PROBES,
    MME_GATE_VERSION,
    MME_PRUNE_TR,
)
from ..common.digest import file_digest
from ..common.log import log
from ..fx.entry import (
    fx_entry,
    fx_run_entry,
)
from ..fx.interp.executor import FxExec
from ..fx.interp.numpy_backend import FxNumpyBackend
from ..fx.params import fx_vs_globals
from ..fx.semantics import fx_classify_global
from ..fx.textures import FxNumpyEnv
from ..fx.types import (
    FxValue,
    fx_count,
    fx_is_numeric,
)
from .cache_store import (
    mme_cache_count,
    mme_cache_path,
    mme_cache_touch,
    mme_cache_trim,
)
from .frame_state import mme_prune_state
from .manifest import mme_texture_digest
from .precull import (
    mme_precull_load,
    mme_precull_save,
)
from .progress import mme_progress_within
from .variants import (
    mme_condition,
    mme_condition_key,
    mme_condition_state,
    mme_exact_value,
    mme_group_key,
)
from .visibility import (
    mme_evaluate_visibility,
    mme_visibility_runs,
)
from .vs_eval import (
    MmePassData,
    mme_loop_values,
    mme_loop_values_for,
    mme_run_vs,
)


# Decides which vertices can ever be visible (keep) and which can pass the alpha gate (opened). Without a
# cache it evaluates all probe states once.
# With one, results are stored per scene condition (parameters and world matrix) and only new conditions or
# new loop instances are evaluated,
# which is what makes re-imports of heavy effects fast.
def mme_prune_mask(runtime, data, state, tr_names, gates=(), cache_path=None, per=None, instances=1, count_params=()):
    base, runs = mme_visibility_runs(runtime, data, state, tr_names, gates)
    runtime.last_reads = None
    if cache_path is None or per is None:
        keep = np.zeros(data.count, dtype=bool)
        opened = np.zeros(data.count, dtype=bool) if gates else np.ones(data.count, dtype=bool)
        mme_evaluate_visibility(runtime, data, runs, tr_names, gates, keep, opened)
        return keep, opened
    conditions = [mme_condition(probe, value, tr_names, count_params) for probe, value in runs]
    cache = mme_precull_load(cache_path, per)
    had_cache = bool(cache["groups"])
    include = set(cache["reads"]) if cache.get("reads") is not None and cache["groups"] else None
    program = runtime.program
    reads = set(include or ())
    regrouped = {}
    for old_key, group in cache["groups"].items():
        key = mme_group_key(group["conditions"][0], include) if group["conditions"] else old_key
        other = regrouped.get(key)
        if other is None:
            regrouped[key] = group
            continue
        rows = min(other["keep"].shape[0], group["keep"].shape[0])
        regrouped[key] = {
            "keep": other["keep"][:rows] | group["keep"][:rows],
            "opened": other["opened"][:rows] | group["opened"][:rows],
            "conditions": other["conditions"] + group["conditions"],
        }
    cache["groups"] = regrouped
    wanted = {}
    for condition in conditions:
        wanted.setdefault(mme_group_key(condition, include), []).append(condition)
    result_keep = np.zeros((instances, per), dtype=bool)
    result_open = np.zeros((instances, per), dtype=bool)
    known_total = 0
    grown_total = 0
    changed = False
    for gkey, group_conditions in wanted.items():
        group = cache["groups"].pop(gkey, None) or {"keep": np.zeros((0, per), dtype=bool), "opened": np.zeros((0, per), dtype=bool), "conditions": []}
        known = {mme_condition_key(c, include, program) for c in group["conditions"]}
        missing = []
        for condition in group_conditions:
            key = mme_condition_key(condition, include, program)
            if key in known:
                known_total += 1
            else:
                known.add(key)
                missing.append(condition)
        cached_rows = group["keep"].shape[0]
        rows = max(instances, cached_rows)
        keep = np.zeros((rows, per), dtype=bool)
        opened = np.zeros((rows, per), dtype=bool) if gates else np.ones((rows, per), dtype=bool)
        keep[:cached_rows] = group["keep"]
        opened[:cached_rows] = group["opened"]
        if instances > cached_rows and group["conditions"]:
            new = np.arange(cached_rows, instances)
            inst = np.repeat(new, per)
            grow = MmePassData(data.draw, np.tile(data.src[:per], len(new)), inst, mme_loop_values_for(data.draw, inst), runtime)
            flat_keep = np.zeros(len(inst), dtype=bool)
            flat_open = np.zeros(len(inst), dtype=bool) if gates else np.ones(len(inst), dtype=bool)
            old_runs = [(mme_condition_state(base, c), None) for c in group["conditions"]]
            reads |= mme_evaluate_visibility(runtime, grow, old_runs, tr_names, gates, flat_keep, flat_open, "new instances ")
            keep[cached_rows:instances] = flat_keep.reshape(len(new), per)
            opened[cached_rows:instances] = flat_open.reshape(len(new), per)
            grown_total += instances - cached_rows
            changed = True
        if missing:
            new_runs = [(mme_condition_state(base, c), None) for c in missing]
            flat_keep = np.zeros(rows * per, dtype=bool)
            flat_open = np.zeros(rows * per, dtype=bool) if gates else np.ones(rows * per, dtype=bool)
            if rows * per == data.count:
                target = data
            else:
                inst = np.repeat(np.arange(rows), per)
                target = MmePassData(data.draw, np.tile(data.src[:per], rows), inst, mme_loop_values_for(data.draw, inst), runtime)
            reads |= mme_evaluate_visibility(runtime, target, new_runs, tr_names, gates, flat_keep, flat_open)
            keep |= flat_keep.reshape(rows, per)
            opened |= flat_open.reshape(rows, per)
            changed = True
        cache["groups"][gkey] = {"keep": keep, "opened": opened, "conditions": group["conditions"] + missing}
        result_keep |= keep[:instances]
        result_open |= opened[:instances]
    log.info(
        "pre-cull cache (%s): %d of %d visibility state(s) already known%s",
        "file had %d group(s)" % len(cache["groups"]) if had_cache else "no usable cache file",
        known_total,
        len(conditions),
        ", %d new instance(s) evaluated" % grown_total if grown_total else "",
    )
    runtime.last_reads = set(reads) if reads else None
    if changed:
        cache["reads"] = reads
        mme_precull_save(cache_path, cache)
    return result_keep.reshape(-1), result_open.reshape(-1)


# Alpha the pixel shader would produce for given vertex-shader outputs, evaluated on numpy; discarded pixels
# count as zero and NaN as opaque.
def mme_ps_numpy_alpha(runtime, draw, state, inputs, loop_values, count):
    program = runtime.program
    function, args = fx_entry(program, draw.fx_pass.ps)
    if function is None:
        return None
    runtime.material_state(state, draw.subset)
    ex = FxExec(program, FxNumpyBackend(), FxNumpyEnv(program, state, runtime.textures))
    for var, values in loop_values.items():
        ex.overrides[var] = FxValue(("i", ()), [values])

    def provide(ex):
        def get(semantic, t):
            if semantic in inputs:
                comps = list(inputs[semantic])
            elif semantic == "VFACE":
                comps = [np.ones(count)]
            else:
                comps = [np.zeros(count)] * 4
            if not fx_is_numeric(t):
                return ex.zero(t)
            n = fx_count(t[1])
            comps = (comps + [np.zeros(count)] * 16)[:n]
            return FxValue(("f", t[1]), comps)

        return get

    with np.errstate(all="ignore"):
        outputs = fx_run_entry(ex, function, args, provide(ex))
        color = outputs.get("COLOR0")
        if color is None:
            return None
        comps = ex.convert(color, ("f", (4,))).c
        alpha = np.clip(np.broadcast_to(np.asarray(comps[3], dtype=np.float64), (count,)), 0.0, 1.0)
        discard = np.broadcast_to(np.asarray(ex.discard, dtype=np.float64), (count,))
        alpha = np.where(discard > 0, 0.0, alpha)
    return np.nan_to_num(alpha, nan=1.0)


# Cache file of a pass's alpha gates (kind "gates") or Tr = 0 proof (kind "empty", with the pass data's digest as
# extra). Both searches are deterministic functions of what this key holds (the random probes use fixed seeds): the
# effect, mesh and texture contents, the pass, the probe state's exact bits, the output layout, the opacity globals, the
# instance count and the gate constants, with the Blender and numpy versions that evaluate it.
def mme_gate_file(runtime, draw, probe, vs_outputs, tr_names, instances, kind="gates", extra=()):
    stamp = tuple(file_digest(name) for name in runtime.program.files)
    xstamp = file_digest(runtime.x_path)
    if "missing" in stamp or xstamp == "missing":
        return None
    text = repr((
        MME_GATE_VERSION,
        tuple(bpy.app.version),
        np.__version__,
        stamp,
        xstamp,
        mme_texture_digest(runtime),
        draw.technique.name,
        draw.fx_pass.name,
        draw.subset,
        draw.order,
        repr(draw.loops),
        list(vs_outputs.items()),
        list(tr_names),
        int(instances),
        MME_GATE_PROBES,
        MME_GATE_EPS,
        MME_GATE_ALPHA,
        MME_PRUNE_TR,
        mme_exact_value(probe.__dict__),
        kind,
        extra,
    ))
    return mme_cache_path(kind + "_" + hashlib.md5(text.encode("utf-8")).hexdigest() + ".json")


# Digest of what pass data is built from apart from the pass and instance count: the used vertices with their inputs,
# and the loop density. Hashing the data itself would cost more than the searches it keys, since it holds every instance.
def mme_source_digest(runtime, used, density):
    h = hashlib.sha1(repr(float(density)).encode("utf-8"))
    for array in (np.asarray(used, dtype=np.int64), runtime.positions[used], runtime.normals[used], runtime.uvs[used]):
        array = np.ascontiguousarray(array)
        h.update(array.dtype.str.encode("ascii"))
        h.update(repr(array.shape).encode("ascii"))
        h.update(array.tobytes())
    return h.hexdigest()


def mme_gate_list(value):
    return [(str(semantic), int(k)) for semantic, k in value]


def mme_gate_text(value):
    if not isinstance(value, str):
        raise TypeError("not a string")
    return value


# A cached value read through read, or None when the file is missing, unreadable or holds something read rejects.
def mme_gate_load(path, read=mme_gate_list):
    if path is None or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        value = read(data["value"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    mme_cache_touch(path)
    return value


# Atomic write (temp file then replace), then the cache folder's size limit.
def mme_gate_save(path, value):
    if path is None:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump({"value": value}, handle)
        os.replace(temp, path)
        mme_cache_trim()
    except OSError as error:
        log.debug("alpha gate cache not written: %s", error)


# Finds vertex-shader outputs that act as alpha gates: setting one component to (almost) zero makes the pixel
# shader fully transparent for all random probes
# while the unmodified inputs are visible somewhere. Vertices where such a gate is closed never show, so they
# can be dropped. Returns [] when anything fails. With cached set, a finished search is kept in the cache folder
# (see mme_gate_file) and read back by later builds.
def mme_alpha_gates(runtime, draw, state, vs_outputs, tr_names, instances, cached=False):
    if draw.fx_pass.ps is None or not vs_outputs:
        return []
    path = mme_gate_file(runtime, draw, mme_prune_state(state), vs_outputs, tr_names, instances) if cached else None
    found = mme_gate_load(path)
    if path is not None:
        mme_cache_count("gates", found is not None)
    if found is not None:
        return found
    gates = mme_alpha_gates_search(runtime, draw, state, vs_outputs, tr_names, instances)
    if gates is not None:
        mme_gate_save(path, gates)
    return gates or []


# A gate that changes with the view cannot justify permanently removing geometry.
# Compare the gated VS outputs themselves: ordinary camera-facing positions and
# lighting varyings do not affect this decision. This runs inside the gate cache.
def mme_view_changes_gate(runtime, draw, state, gates, tr_names, instances):
    program = runtime.program
    for name in fx_vs_globals(program, [draw.fx_pass]):
        g = program.globals.get(name)
        source = fx_classify_global(g, program) if g is not None else None
        if source in (("position", "camera"), ("direction", "camera")):
            break
        if source and source[0] == "matrix" and "VIEW" in source[1] and source[3] == "camera":
            break
    else:
        return False
    faces = [f for f, subset in zip(runtime.faces, runtime.face_subsets) if subset == draw.subset]
    vertices = np.unique(np.concatenate(faces))
    src = vertices[np.linspace(0, len(vertices) - 1, min(len(vertices), 64), dtype=int)]
    inst = np.linspace(0, instances - 1, len(src), dtype=int)
    data = MmePassData(draw, src, inst, mme_loop_values(draw, inst, 1.0), runtime)
    probes = [mme_prune_state(state, direction) for direction in ((0, 0, 1), (1, 0, 0), (1, 2, -1))]
    for tr in ((1.0, 0.5, 0.0) if tr_names else (None,)):
        reference = None
        for probe in probes:
            for name in tr_names:
                probe.controls[name] = [tr]
            outputs, _ = mme_run_vs(runtime, data, probe)
            values = [outputs[sem].c[k] for sem, k in gates]
            if reference is None:
                reference = values
            elif any(not np.array_equal(a, b, equal_nan=True) for a, b in zip(reference, values)):
                return True
    return False


# The gate search of mme_alpha_gates; None when the pixel shader could not be evaluated.
def mme_alpha_gates_search(runtime, draw, state, vs_outputs, tr_names, instances):
    count = MME_GATE_PROBES
    rng = np.random.default_rng(1234)
    base = {}
    for semantic, n in vs_outputs.items():
        base[semantic] = [rng.uniform(0.0, 1.0, count) for _ in range(n)]
    inst = rng.integers(0, max(1, instances), count)
    loops = mme_loop_values(draw, inst, 1.0)
    probe = mme_prune_state(state)
    samples = MME_PRUNE_TR if tr_names else [None]
    candidates = [(sem, k) for sem, n in vs_outputs.items() for k in range(n)]
    closed = {c: True for c in candidates}
    lit = False
    try:
        for step, value in enumerate(samples):
            mme_progress_within(0.1 * (step + 1) / len(samples), "finding alpha gates %d/%d" % (step + 1, len(samples)))
            if value is not None:
                for name in tr_names:
                    probe.controls[name] = [value]
            alpha = mme_ps_numpy_alpha(runtime, draw, probe, base, loops, count)
            if alpha is None:
                return None
            if (alpha > MME_GATE_ALPHA).any():
                lit = True
            for semantic, k in candidates:
                if not closed[(semantic, k)]:
                    continue
                inputs = dict(base)
                comps = list(base[semantic])
                comps[k] = np.full(count, MME_GATE_EPS)
                inputs[semantic] = comps
                gated = mme_ps_numpy_alpha(runtime, draw, probe, inputs, loops, count)
                if gated is None or (gated > MME_GATE_ALPHA).any():
                    closed[(semantic, k)] = False
    except Exception as error:
        log.debug("alpha gate probe for %s failed: %s", draw.fx_pass.name, error)
        return None
    if not lit:
        return []
    gates = [c for c in candidates if closed[c]]
    if gates:
        try:
            if mme_view_changes_gate(runtime, draw, state, gates, tr_names, instances):
                return []
        except Exception as error:
            log.debug("view probe for %s failed: %s", draw.fx_pass.name, error)
            return []
    return gates


# Adds rings of neighbouring faces around the kept ones so a gate that opens slightly away from a vertex does
# not leave visible holes.
def mme_grow_faces(all_faces, kept, rings):
    if rings <= 0 or len(kept) == len(all_faces):
        return kept
    chosen = set(kept)
    by_vertex = {}
    for face in all_faces:
        for v in face:
            by_vertex.setdefault(v, []).append(face)
    frontier = set(kept)
    for _ in range(rings):
        grown = set()
        for face in frontier:
            for v in face:
                for other in by_vertex.get(v, ()):
                    if other not in chosen:
                        grown.add(other)
        if not grown:
            break
        chosen |= grown
        frontier = grown
    return [f for f in all_faces if f in chosen]


# Faces that touch at least one kept vertex (and one open vertex when opened is given).
def mme_keep_faces(local_faces, keep, offset=0, opened=None):
    if opened is None:
        return [f for f in local_faces if any(keep[v + offset] for v in f)]
    return [f for f in local_faces if any(keep[v + offset] for v in f) and any(opened[v + offset] for v in f)]


# Drops unused vertices and renumbers the faces; returns the mask of the vertices that stay.
def mme_compact(src, faces):
    alive = np.zeros(len(src), dtype=bool)
    for face in faces:
        alive[list(face)] = True
    new_index = -np.ones(len(src), dtype=np.int64)
    new_index[alive] = np.arange(int(alive.sum()))
    return alive, [tuple(int(new_index[v]) for v in face) for face in faces]


# Whether the blend state makes a pixel of alpha 0 contribute nothing: standard source-alpha blending, added with ONE or
# inverse source alpha. Opaque, multiply and other states ignore alpha, so alpha 0 proves nothing there.
def mme_alpha_scaled(states):
    def state(name, default):
        value = states.get(name)
        return default if value is None else str(value).replace(" ", "").upper()

    return (
        state("alphablendenable", "TRUE") not in ("FALSE", "0")
        and state("srcblend", "SRCALPHA") == "SRCALPHA"
        and state("destblend", "INVSRCALPHA") in ("INVSRCALPHA", "ONE")
        and state("blendop", "ADD") in ("ADD", "1")
    )


# Decides whether a pass that reads the accessory's opacity draws nothing at Tr = 0, by two tests: every vertex is clipped
# when the vertex shader runs with Tr = 0, or the pixel shader returns alpha 0 for random inputs (several seeds) under a
# blend state where alpha 0 draws nothing. Returns the reason, or "" when neither holds (or anything fails), so only
# passes that pass a test ever get the Tr = 0 switch. source, the (used vertices, density) that data was built from
# (used tiled over the instances), turns on a cache kept like the alpha gates.
def mme_empty_at_zero_tr(runtime, draw, state, data, vs_outputs, tr_names, instances, source=None):
    if not tr_names:
        return ""
    probe = mme_prune_state(state)
    for name in tr_names:
        probe.controls[name] = [0.0]
    path = mme_gate_file(runtime, draw, probe, vs_outputs, tr_names, instances, "empty", mme_source_digest(runtime, *source)) if source is not None else None
    found = mme_gate_load(path, mme_gate_text)
    if path is not None:
        mme_cache_count("empty", found is not None)
    if found is not None:
        return found
    reason = mme_empty_at_zero_tr_search(runtime, draw, probe, data, vs_outputs, tr_names, instances)
    if reason is not None:
        mme_gate_save(path, reason)
    return reason or ""


# The proof of mme_empty_at_zero_tr for a probe state with Tr = 0; None when it could not be evaluated.
def mme_empty_at_zero_tr_search(runtime, draw, probe, data, vs_outputs, tr_names, instances):
    try:
        keep = np.zeros(data.count, dtype=bool)
        opened = np.ones(data.count, dtype=bool)
        mme_evaluate_visibility(runtime, data, [(probe, 0.0)], tr_names, (), keep, opened)
        if not keep.any():
            return "every vertex is clipped at Tr = 0"
        if draw.fx_pass.ps is None or not mme_alpha_scaled(draw.fx_pass.states) or not vs_outputs:
            return ""
        count = MME_GATE_PROBES
        for seed in (11, 23, 37):
            rng = np.random.default_rng(seed)
            base = {sem: [rng.uniform(0.0, 1.0, count) for _ in range(n)] for sem, n in vs_outputs.items()}
            inst = rng.integers(0, max(1, instances), count)
            alpha = mme_ps_numpy_alpha(runtime, draw, probe, base, mme_loop_values(draw, inst, 1.0), count)
            if alpha is None or (alpha > MME_GATE_ALPHA).any():
                return ""
        return "alpha is 0 at Tr = 0"
    except Exception as error:
        log.debug("Tr = 0 proof for %s failed: %s", draw.fx_pass.name, error)
        return None
