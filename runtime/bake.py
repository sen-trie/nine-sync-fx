import bpy
import time

from ..common.constants import (
    MME_BAKED_PROP,
    MME_GN_MODIFIER,
    MME_REBUILD_DELAY,
)
from ..common.digest import brief_inputs
from ..common.log import log
from ..common.state import _mme_state
from .drivers import mme_needs_handler
from .manifest import (
    mme_bake_decision,
    mme_bake_state,
    mme_bake_storage,
    mme_log_action,
    mme_mark_stale_bakes,
    mme_set_bake_record,
)


def _fail(context, obj, reason, started):
    mme_set_bake_record(obj, state="failed", stale=False, stale_reason=reason[:200])
    if MME_BAKED_PROP in obj:
        del obj[MME_BAKED_PROP]
    try:
        mme_clear_bake(context, obj)
    except Exception as error:
        log.debug("clearing the failed bake of %s did not work: %s", obj.name, error)
    mme_set_bake_record(obj, state="failed", stale=False, stale_reason=reason[:200])
    mme_log_action("FAILED", obj.name, "bake not recorded as valid: %s" % reason, seconds=time.time() - started)
    return "failed"


# Bakes a Geometry Nodes pass over the frame range through the modifier's bake node, unless the pass's recorded bake still
# matches its current inputs and Blender still holds it (then nothing runs). The frame handler is limited to this object
# during the bake. The new signature is recorded only after Blender reports the bake finished and the stored data is
# not known to be missing; a failure leaves the pass marked failed with its old bake cleared.
# Returns "baked", "skipped" or "failed".
def mme_bake_pass(context, obj, start, end, settings, force=False):
    modifier = obj.modifiers.get(MME_GN_MODIFIER)
    if modifier is None or not len(modifier.bakes):
        return "failed"
    started = time.time()
    scene = context.scene
    detail = bool(getattr(settings, "mme_log_detail", False))
    valid, reason, changes, inputs, sig = mme_bake_decision(obj, obj.parent, scene, settings, force)
    analysis = time.time() - started
    backend = obj.get("mme_backend", "")
    if valid:
        mme_set_bake_record(obj, stale=False, stale_reason="")
        mme_log_action("SKIP BAKE", obj.name, reason, backend, analysis)
        return "skipped"
    mme_log_action("REBAKE", obj.name, reason, backend, analysis, changes, detail)
    bake = modifier.bakes[0]
    bake.bake_mode = "ANIMATION"
    bake.use_custom_simulation_frame_range = True
    bake.frame_start = start
    bake.frame_end = end
    if settings.mme_bake_target == "DISK":
        modifier.bake_target = "DISK"
        modifier.bake_directory = settings.mme_bake_dir
        bake.bake_target = "DISK"
    else:
        modifier.bake_target = "PACKED"
        bake.bake_target = "PACKED"
    _mme_state["only"] = {obj.name}
    baking = time.time()
    try:
        with context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
            result = bpy.ops.object.geometry_node_bake_single(
                session_uid=obj.session_uid, modifier_name=modifier.name, bake_id=bake.bake_id
            )
    except Exception as error:
        return _fail(context, obj, "Blender raised %s" % error, started)
    finally:
        _mme_state["only"] = None
    if "FINISHED" not in result:
        return _fail(context, obj, "Blender returned %s" % sorted(result), started)
    ok, detail_text = mme_bake_storage(obj, settings)
    if ok is False:
        return _fail(context, obj, "bake finished but %s" % detail_text, started)
    obj[MME_BAKED_PROP] = True
    obj["mme_handler"] = mme_needs_handler(obj)
    mme_set_bake_record(
        obj,
        state="baked",
        sig=sig,
        inputs=brief_inputs(inputs),
        frames=[int(start), int(end)],
        target=settings.mme_bake_target,
        seconds=round(time.time() - baking, 3),
        stale=False,
        stale_reason="",
    )
    mme_log_action("BAKED", obj.name, "frames %d-%d, %s" % (start, end, detail_text), backend, time.time() - baking)
    return "baked"


# Deletes the bake and gives the pass its frame handler back if it needs one.
def mme_clear_bake(context, obj):
    modifier = obj.modifiers.get(MME_GN_MODIFIER)
    if modifier is None or not len(modifier.bakes):
        return False
    bake = modifier.bakes[0]
    with context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.geometry_node_bake_delete_single(
            session_uid=obj.session_uid, modifier_name=modifier.name, bake_id=bake.bake_id
        )
    if MME_BAKED_PROP in obj:
        del obj[MME_BAKED_PROP]
    if mme_bake_state(obj) != "failed":
        mme_set_bake_record(obj, state="none", stale=False, stale_reason="")
    obj["mme_handler"] = mme_needs_handler(obj)
    return True


# Queues a debounced check of a root's bakes after an edit; nothing is computed in the depsgraph handler itself.
def mme_queue_bake_check(root):
    _mme_state["bake_check"].add(root.name)
    if bpy.app.timers.is_registered(mme_run_bake_checks):
        bpy.app.timers.unregister(mme_run_bake_checks)
    bpy.app.timers.register(mme_run_bake_checks, first_interval=MME_REBUILD_DELAY)


# Timer callback: once edits settle, marks baked passes whose inputs changed as stale and clears those bakes so the pass
# goes back to live evaluation instead of showing obsolete geometry. It never starts a bake.
def mme_run_bake_checks():
    names = list(_mme_state["bake_check"])
    _mme_state["bake_check"].clear()
    try:
        context = bpy.context
        scene = context.scene
    except AttributeError:
        return None
    settings = scene.ninesync_fx
    detail = bool(getattr(settings, "mme_log_detail", False))
    for name in names:
        root = bpy.data.objects.get(name)
        if root is None:
            continue
        try:
            for child in mme_mark_stale_bakes(root, scene, settings, detail):
                mme_log_action("CLEAR", child.name, "stale bake cleared; the pass evaluates live until rebaked")
                mme_clear_bake(context, child)
                mme_set_bake_record(child, state="none", stale=False, stale_reason="")
        except Exception as error:
            log.error("bake check of '%s' failed: %s", name, error)
    return None
