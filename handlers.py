import bpy
from . import hooks

from .common.constants import (
    MME_PARAMS_PROP,
    MME_REBUILD_DELAY,
)
from .common.controls import mme_effect_roots
from .common.log import log
from .common.scene_utils import redraw_all
from .common.state import (
    _mme_runtimes,
    _mme_state,
)
from .nodes.library_store import mme_library_sync
from .runtime.build_effect import (
    mme_rebuild_needed,
    mme_run_rebuilds,
)
from .runtime.drivers import mme_revive_drivers
from .runtime.progress import mme_progress_tick
from .runtime.update import mme_update_scene
from .ui.hook_entry import (
    clean_hook,
    post_sync_hook,
    report_hook,
)
from .ui.relink import (
    schedule_relink,
    settings_outdated,
)


# Frame-change handler. A busy flag stops an update from re-entering itself, and during a bake the depsgraph
# is updated so the bake sees the new geometry.
@bpy.app.handlers.persistent
def mme_frame_handler(scene, depsgraph=None):
    if _mme_state.get("progress") is not None:
        mme_progress_tick(scene.frame_current)
    if _mme_state["busy"]:
        return
    _mme_state["busy"] = True
    try:
        updated = mme_update_scene(scene, depsgraph)
        if updated and depsgraph is not None and _mme_state.get("only") is not None:
            depsgraph.update()
    finally:
        _mme_state["busy"] = False


# Registers FX's callbacks with the hook registry; core, if installed, calls them.
def register_hooks():
    hooks.add("post_sync", hooks.FX_OWNER, post_sync_hook)
    hooks.add("clean", hooks.FX_OWNER, clean_hook)
    hooks.add("report", hooks.FX_OWNER, report_hook)


# After a file load: re-registers hooks, clears per-session caches, revives drivers Blender invalidated and
# relinks outdated settings.
@bpy.app.handlers.persistent
def mme_load_handler(*_args):
    register_hooks()
    _mme_runtimes.clear()
    _mme_state["errors"].clear()
    try:
        revived = mme_revive_drivers(
            [c for root in mme_effect_roots(bpy.context.scene) for c in root.children]
        )
        if revived:
            log.info(
                "reset %d effect driver(s) that Blender had marked invalid", revived
            )
    except (AttributeError, RuntimeError):
        pass
    try:
        if settings_outdated(bpy.context.scene):
            schedule_relink()
    except AttributeError:
        pass


# Depsgraph handler that notices edited effect parameters. A rebuild is queued (debounced by a timer) only
# when a structural parameter changed;
# with auto rebuild off the effect is only marked stale. It stays out of the way during playback, renders and
# bakes.
@bpy.app.handlers.persistent
def mme_param_watch(scene, depsgraph=None):
    if _mme_state["busy"] or _mme_state.get("only") is not None or depsgraph is None:
        return
    screen = getattr(bpy.context, "screen", None)
    if screen is not None and screen.is_animation_playing:
        return
    if bpy.app.is_job_running("RENDER"):
        return
    for update in depsgraph.updates:
        obj = update.id.original if isinstance(update.id, bpy.types.Object) else None
        if obj is None or not obj.get(MME_PARAMS_PROP):
            continue
        if obj.name in _mme_state["rebuild"]:
            continue
        if mme_rebuild_needed(obj):
            settings = getattr(scene, "ninesync_fx", None)
            if settings is not None and not settings.mme_auto_rebuild:
                if obj.name not in _mme_state["stale"]:
                    _mme_state["stale"].add(obj.name)
                    redraw_all()
                continue
            _mme_state["rebuild"].add(obj.name)
            if bpy.app.timers.is_registered(mme_run_rebuilds):
                bpy.app.timers.unregister(mme_run_rebuilds)
            bpy.app.timers.register(mme_run_rebuilds, first_interval=MME_REBUILD_DELAY)


# Removes this add-on's handlers by function name, so a reload never leaves duplicates from an older copy.
def remove_handlers():
    for handlers, name in (
        (bpy.app.handlers.frame_change_post, "mme_frame_handler"),
        (bpy.app.handlers.load_post, "mme_load_handler"),
        (bpy.app.handlers.depsgraph_update_post, "mme_param_watch"),
    ):
        for handler in [h for h in handlers if getattr(h, "__name__", "") == name]:
            handlers.remove(handler)


# One-shot timer after registration: update the shared library, then relink if the scene still has outdated
# settings.
def startup_check():
    mme_library_sync()
    try:
        scene = bpy.context.scene
    except AttributeError:
        return None
    if scene is not None and settings_outdated(scene):
        schedule_relink()
    return None
