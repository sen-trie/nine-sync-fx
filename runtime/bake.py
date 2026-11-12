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
from .bake_paths import (
    mme_pass_bake_folder,
    mme_pass_bake_template,
)
from .bake_store import (
    mme_in_store,
    mme_store_begin,
    mme_store_discard,
    mme_store_find,
    mme_store_publish,
    mme_store_touch,
)
from .drivers import mme_needs_handler
from .manifest import (
    mme_bake_decision,
    mme_bake_state,
    mme_bake_storage,
    mme_log_action,
    mme_mark_stale_bakes,
    mme_portable_bake_sig,
    mme_set_bake_record,
)


def _fail(context, obj, reason, started, staging=None):
    if staging is not None:
        mme_store_discard(staging)
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


def _set_range(bake, start, end):
    bake.bake_mode = "ANIMATION"
    bake.use_custom_simulation_frame_range = True
    bake.frame_start = start
    bake.frame_end = end


def _point_at(modifier, bake, folder):
    modifier.bake_target = "DISK"
    bake.bake_target = "DISK"
    bake.use_custom_path = True
    bake.directory = folder.replace("\\", "/").rstrip("/") + "/"


def _record_bake(obj, settings, sig, inputs, start, end, seconds, shared):
    obj[MME_BAKED_PROP] = True
    obj["mme_handler"] = mme_needs_handler(obj)
    mme_set_bake_record(
        obj,
        state="baked",
        sig=sig,
        inputs=brief_inputs(inputs),
        frames=[int(start), int(end)],
        target=settings.mme_bake_target,
        seconds=round(seconds, 3),
        shared=bool(shared),
        stale=False,
        stale_reason="",
    )


# Bakes a Geometry Nodes pass over the frame range through the modifier's bake node, unless the pass's recorded bake still
# matches its current inputs and Blender still holds it (then nothing runs). With Share Bakes on, a bake in the shared store
# with the same address (see mme_portable_bake_sig) is attached instead of baking, and a new bake is made in a private
# folder of that store and renamed to its address when it finishes. The frame handler is limited to this object during the
# bake. The new signature is recorded only after Blender reports the bake finished and the stored data is not known to be
# missing; a failure leaves the pass marked failed with its old bake cleared.
# Returns "baked", "skipped", "shared" (attached from the store) or "failed".
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
    bake = modifier.bakes[0]
    shared = settings.mme_bake_target == "DISK" and getattr(settings, "mme_bake_shared", False)
    address = mme_portable_bake_sig(obj, obj.parent, inputs) if shared else None
    if address and not force:
        found = mme_store_find(address, start, end)
        if found:
            _set_range(bake, start, end)
            _point_at(modifier, bake, found)
            ok, detail_text = mme_bake_storage(obj, settings, (start, end))
            if ok is not False:
                mme_store_touch(found)
                _record_bake(obj, settings, sig, inputs, start, end, 0.0, True)
                mme_log_action("SHARED BAKE", obj.name, "reused %s: frames %d-%d, %s" % (address[:10], start, end, detail_text), backend, time.time() - started)
                return "shared"
            log.info("%s shared bake %s is incomplete (%s); baking again", obj.name, address[:10], detail_text)
    mme_log_action("REBAKE", obj.name, reason, backend, analysis, changes, detail)
    _set_range(bake, start, end)
    staging = None
    if settings.mme_bake_target == "DISK":
        modifier.bake_target = "DISK"
        modifier.bake_directory = settings.mme_bake_dir
        bake.bake_target = "DISK"
        if address:
            staging = mme_store_begin(address)
            _point_at(modifier, bake, staging)
        else:
            template = mme_pass_bake_template(settings, obj) if getattr(settings, "mme_bake_pass_folders", False) else None
            bake.use_custom_path = bool(template)
            if template:
                bake.directory = template
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
        return _fail(context, obj, "Blender raised %s" % error, started, staging)
    finally:
        _mme_state["only"] = None
    if "FINISHED" not in result:
        return _fail(context, obj, "Blender returned %s" % sorted(result), started, staging)
    ok, detail_text = mme_bake_storage(obj, settings, (start, end))
    if ok is False:
        return _fail(context, obj, "bake finished but %s" % detail_text, started, staging)
    if staging is not None:
        final = mme_store_publish(staging, address, start, end)
        if final is not None:
            _point_at(modifier, bake, final)
            ok, detail_text = mme_bake_storage(obj, settings, (start, end))
            if ok is False:
                return _fail(context, obj, "stored bake is not readable: %s" % detail_text, started, None)
        else:
            shared = False
    _record_bake(obj, settings, sig, inputs, start, end, time.time() - baking, shared and staging is not None)
    mme_log_action("BAKED", obj.name, "frames %d-%d, %s" % (start, end, detail_text), backend, time.time() - baking)
    return "baked"


# Deletes the bake and gives the pass its frame handler back if it needs one. A bake that lives in the shared store is only
# unlinked: Blender's delete would remove the files other projects read.
def mme_clear_bake(context, obj):
    modifier = obj.modifiers.get(MME_GN_MODIFIER)
    if modifier is None or not len(modifier.bakes):
        return False
    bake = modifier.bakes[0]
    if mme_in_store(mme_pass_bake_folder(obj)):
        bake.use_custom_path = False
        bake.directory = ""
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
