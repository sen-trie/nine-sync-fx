import bpy
import json
import os
import time
import traceback
import types

from ..common.constants import (
    ACCESSORY_UNIT,
    MME_BACKEND_PROP,
    MME_CORE_FILE,
    MME_DRAW_PROP,
    MME_PARAM_BUILT_PROP,
    MME_PRUNED_PROP,
    MME_RUNTIME_VALUES_PROP,
    MME_UNSUPPORTED_PROP,
)
from ..common.controls import (
    mme_ensure_use_props,
    mme_load_controls,
    mme_load_controls_raw,
)
from ..common.log import log
from ..common.scene_utils import (
    accessory_collection,
    redraw_all,
    short_name,
)
from ..common.state import (
    _mme_core,
    _mme_runtimes,
    _mme_state,
)
from ..fx.errors import FxError
from ..fx.loader import fx_parse_file
from ..fx.params import (
    fx_overridden_params,
    fx_structural_params,
)
from ..nodes.standard_groups import mme_save_core_library
from .build_pass import mme_build_pass
from .cleanup import remove_accessory_children_single
from .controls_resolve import mme_apply_manual_controls
from .frame_state import mme_frame_state
from .manifest import (
    mme_build_decision,
    mme_build_inputs,
    mme_log_action,
    mme_pass_by_key,
    mme_pass_key,
    mme_record_build,
    mme_shared_inputs,
    mme_sig,
)
from .params import (
    mme_built_values,
    mme_param_signature,
    mme_param_values,
    mme_sync_params,
)
from .progress import (
    mme_progress_begin,
    mme_progress_end,
    mme_progress_step,
)
from .runtime import MmeRuntime
from .variants import mme_build_variants
from .visibility import (
    mme_capture_pass,
    mme_capture_visibility,
    mme_link_visibility,
    mme_restore_visibility,
)
from .vs_eval import mme_self_tr_globals


# Builds a whole effect on a root: syncs parameters and controls, samples keyed variants, then reconciles every
# geometry draw without a render target with the pass objects in 'existing' (by logical pass key). A pass whose
# build signature is unchanged keeps its object, node tree and bake; a changed one is replaced alone; a draw that
# is gone loses its object. Without 'existing' (or with force) every pass is built.
# Passes that fail are skipped. Shared groups are saved to the library if new ones were built and autosave is
# on. The runtime's build_report lists reused, built, removed and failed passes.
# Returns the pass objects, the uncovered material subsets and the number of draws.
def mme_build_effect(scene, collection, settings, acc, root, x_path, fx_path, unit, controls, existing=None, force=False):
    _mme_runtimes.pop(root.name, None)
    program = fx_parse_file(fx_path)
    mme_sync_params(root, program)
    values = mme_param_values(root, program)
    root["mme_controls"] = json.dumps(controls)
    mme_ensure_use_props(root, controls)
    variants, combos, highs = mme_build_variants(scene, root, types.SimpleNamespace(program=program), mme_self_tr_globals(None, root))
    for name, high in highs.items():
        if name in values:
            values[name] = [max(a, b) for a, b in zip(values[name], high)] + values[name][len(high):]
    runtime = MmeRuntime(fx_path, x_path, values)
    runtime.build_variants = (variants, combos)
    root[MME_RUNTIME_VALUES_PROP] = json.dumps(values)
    root["mme_fx"] = fx_path
    root["mme_x"] = x_path
    root["mme_unit"] = unit
    runtime.controls_runtime = {name for name, desc in mme_load_controls(root).items() if desc.get("kind") != "const"}
    if acc is not None:
        root["mme_acc_name"] = acc.name
        root["mme_tr"] = float(acc.keys[0].opacity) if acc.keys else 1.0
        root["mme_si"] = float(acc.keys[0].scale) if acc.keys else 1.0
    else:
        for name in ("mme_tr", "mme_si"):
            if name not in root:
                root[name] = 1.0
    for name, desc in controls.items():
        if desc.get("kind") == "prop" and desc["prop"] not in root:
            root[desc["prop"]] = 0.0
    depsgraph = bpy.context.evaluated_depsgraph_get()
    state = mme_frame_state(scene, depsgraph, root, runtime, unit)
    density = settings.mme_density
    backend = settings.mme_backend
    built_before = _mme_core["stats"]["built"]
    library_before = _mme_core["stats"]["library"]
    file_before = _mme_core["stats"]["file"]
    children = []
    report = {"reused": [], "built": [], "removed": 0, "failed": 0, "empty": 0}
    runtime.build_report = report
    existing = {} if existing is None else existing
    scene_controls = mme_load_controls(root)
    shared = mme_shared_inputs(runtime, root, settings, backend, unit, scene_controls, [variants, combos, highs])
    try:
        empties = json.loads(root.get("mme_empty_passes", "{}"))
    except (TypeError, ValueError):
        empties = {}
    empties_now = {}
    seen = {}
    produced = set()
    analysis_seconds = 0.0
    detail = bool(getattr(settings, "mme_log_detail", False))
    effect_started = time.time()
    base = short_name(root.get("mme_acc_name", acc.name if acc is not None else root.name), 24)
    emitted = [d for d in runtime.draws if d.target == "" and d.kind == "geometry"]
    owns = mme_progress_begin(bpy.context, "NineSync building %s" % base, len(emitted))
    try:
        for index, draw in enumerate(emitted):
            label = "%s_%s_%s" % (base, draw.technique.name or "tech", draw.fx_pass.name or str(draw.order))
            if owns:
                mme_progress_step(index, draw.fx_pass.name or label)
            key = mme_pass_key(root, draw, seen)
            if draw.fx_pass.vs is None:
                log.info("effect pass %s uses fixed-function drawing; skipped", label)
                continue
            analysis_started = time.time()
            inputs, _reads = mme_build_inputs(shared, runtime, draw, root, scene_controls)
            sig = mme_sig(inputs)
            old = existing.get(key)
            action, reason, changes = mme_build_decision(old, sig, inputs, force)
            if action == "BUILD" and old is None and not force and empties.get(key) == sig:
                action, reason = "SKIP BUILD", "build signature unchanged; pass is known to draw nothing"
            analysis_seconds += time.time() - analysis_started
            if action == "SKIP BUILD":
                if old is not None:
                    children.append(old)
                    report["reused"].append(old)
                    produced.add(key)
                else:
                    empties_now[key] = sig
                    report["empty"] += 1
                mme_log_action(action, label, reason, old.get(MME_BACKEND_PROP, "") if old is not None else "", changes=changes, detail=detail)
                continue
            own = None
            if old is not None:
                own = mme_capture_pass(old)
                remove_accessory_children_single(old)
                existing.pop(key, None)
            started = time.time()
            try:
                obj = mme_build_pass(runtime, draw, state, root, collection, unit, density, label, backend, scene)
            except FxError as error:
                log.warning("effect pass %s skipped: %s", label, error)
                mme_log_action("FAILED", label, str(error), backend)
                report["failed"] += 1
                continue
            if obj is None:
                empties_now[key] = sig
                report["empty"] += 1
                mme_log_action("BUILD", label, "%s; draws nothing visible" % reason, backend, time.time() - started, changes, detail)
                continue
            mme_record_build(obj, key, sig, inputs, obj.get(MME_BACKEND_PROP, backend))
            children.append(obj)
            produced.add(key)
            report["built"].append((obj, own))
            mme_log_action("BUILD", label, reason, obj.get(MME_BACKEND_PROP, backend), time.time() - started, changes, detail)
        for key, old in list(existing.items()):
            if key not in produced:
                mme_log_action("REMOVE", old.name, "draw is no longer planned or draws nothing", old.get(MME_BACKEND_PROP, ""))
                remove_accessory_children_single(old)
                report["removed"] += 1
    finally:
        if owns:
            mme_progress_end()
    root["mme_empty_passes"] = json.dumps(empties_now)
    root["mme_structural"] = json.dumps(fx_structural_params(runtime.program))
    root["mme_overrides"] = json.dumps(fx_overridden_params(runtime.program))
    root.update_tag()
    root[MME_PARAM_BUILT_PROP] = json.dumps(mme_param_signature(root))
    stats = dict(_mme_core["stats"])
    log.info(
        "shared node groups: %d built, %d appended from %s, %d reused from this file",
        stats["built"] - built_before,
        stats["library"] - library_before,
        MME_CORE_FILE,
        stats["file"] - file_before,
    )
    if stats["built"] > built_before and settings.mme_core_autosave:
        try:
            count = mme_save_core_library()
            log.info("%s updated: %d shared group(s)", MME_CORE_FILE, count)
        except (OSError, RuntimeError) as error:
            log.warning("%s could not be updated: %s", MME_CORE_FILE, error)
    covered = {d.subset for d in runtime.draws}
    uncovered = [s for s in range(len(runtime.materials)) if s not in covered]
    offscreen = sorted({d.target for d in runtime.draws if d.target})
    skipped = [d.label for d in runtime.draws if d.kind == "buffer"]
    if offscreen:
        log.info(
            "effect '%s': offscreen targets %s are emulated by drawing their source shading directly",
            os.path.basename(fx_path),
            offscreen,
        )
    root[MME_UNSUPPORTED_PROP] = json.dumps(skipped)
    if skipped:
        log.info(
            "effect '%s': skipped %d full-screen pass(es), which this add-on does not support: %s",
            os.path.basename(fx_path),
            len(skipped),
            skipped,
        )
    stage = {name: sum(t.get(name, 0.0) for t in runtime.timing.values()) for name in ("analysis", "alpha", "precull", "strips", "graph")}
    log.info(
        "[NineSync FX] effect %s: %d draw(s); build reused=%d rebuilt=%d removed=%d failed=%d empty=%d; elapsed=%.1fs (dependency analysis %.2fs, pass analysis %.2fs, alpha gates %.2fs, pre-cull %.2fs, strips %.2fs, graph/material %.2fs)",
        base,
        len(emitted),
        len(report["reused"]),
        len(report["built"]),
        report["removed"],
        report["failed"],
        report["empty"],
        time.time() - effect_started,
        analysis_seconds,
        stage["analysis"],
        stage["alpha"],
        stage["precull"],
        stage["strips"],
        stage["graph"],
    )
    _mme_runtimes[root.name] = runtime
    return children, uncovered, None, len(emitted)


# Rebuilds an existing effect in place from the values stored on its root. Only passes whose build signature changed
# are rebuilt (all of them with force); the others keep their objects, node trees, visibility, keys and bakes. Pass
# objects from before manifests existed are replaced once.
def mme_rebuild_effect(scene, root, force=False):
    _mme_state["stale"].discard(root.name)
    settings = scene.ninesync_fx
    collection = next((c for c in root.users_collection), None) or accessory_collection(scene, True)
    fx_path, x_path = root.get("mme_fx", ""), root.get("mme_x", "")
    if collection is None or not (os.path.isfile(fx_path) and os.path.isfile(x_path)):
        return 0
    unit = float(root.get("mme_unit", 1.0)) or 1.0
    passes = [c for c in root.children if c.get(MME_DRAW_PROP)]
    mme_link_visibility(root)
    captured = mme_capture_visibility(root)
    keyed = {} if force else mme_pass_by_key(root)
    for child in passes:
        if child not in keyed.values():
            remove_accessory_children_single(child)
    started = time.time()
    children, _uncovered, _tracks, total = mme_build_effect(
        scene, collection, settings, None, root, x_path, fx_path, unit, mme_apply_manual_controls(root, mme_load_controls_raw(root)), keyed, force
    )
    report = getattr(_mme_runtimes.get(root.name), "build_report", {"built": [], "reused": []})
    for child, own in report["built"]:
        child.scale = (ACCESSORY_UNIT * unit,) * 3
        mme_restore_visibility([child], own or captured)
    root.update_tag()
    log.info(
        "effect '%s' rebuilt in %.1fs: %d of %d pass(es) present, %d rebuilt, %d reused",
        root.name,
        time.time() - started,
        len(children),
        total,
        len(report["built"]),
        len(report["reused"]),
    )
    return len(children)


# A rebuild is needed only if a structural parameter changed and a pass that is pruned or on the Python
# backend reads it;
# Geometry Nodes passes pick up other changes through drivers.
def mme_rebuild_needed(root):
    current = mme_param_signature(root)
    built = mme_built_values(root)
    if current == built:
        return False
    changed = {k for k in set(current) | set(built) if current.get(k) != built.get(k)}
    for child in root.children:
        if not child.get(MME_DRAW_PROP):
            continue
        if not (child.get(MME_PRUNED_PROP) or child.get(MME_BACKEND_PROP, "PYTHON") == "PYTHON"):
            continue
        try:
            reads = set(json.loads(child.get("mme_vs_params", "null")) or current)
        except (TypeError, ValueError):
            reads = set(current)
        if changed & reads:
            return True
    root[MME_PARAM_BUILT_PROP] = json.dumps(current)
    return False


# Timer callback that runs the queued rebuilds after parameter edits settle.
def mme_run_rebuilds():
    names = list(_mme_state["rebuild"])
    _mme_state["rebuild"].clear()
    try:
        scene = bpy.context.scene
    except AttributeError:
        return None
    for name in names:
        root = bpy.data.objects.get(name)
        if root is None or not root.get("mme_fx"):
            continue
        try:
            if mme_rebuild_needed(root):
                mme_rebuild_effect(scene, root)
        except Exception as error:
            log.error("effect '%s' rebuild failed: %s\n%s", name, error, traceback.format_exc())
    redraw_all()
    return None
