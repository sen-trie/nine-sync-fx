ADDON_VERSION = (0, 2, 0)

bl_info = {
    "name": "NineSync: FX Importer",
    "author": "",
    "version": ADDON_VERSION,
    "blender": (5, 3, 0),
    "location": "View3D > Sidebar > NineSync",
    "description": "⑨-Sync FX: import MikuMikuDance accessories (.x) and run their MME effects (.fx) through Geometry Nodes and shader nodes. Works alone or with NineSync: PMM Bridge",
    "category": "Import-Export",
}

import importlib as _importlib
import sys as _sys

for _name in (
    "common",
    "util",
    "hooks",
    "pmm",
    "xfile",
    "fx",
    "mme_nodes",
    "mme",
    "accessories",
    "standalone",
    "ui",
):
    _module = _sys.modules.get(__name__ + "." + _name)
    if _module is not None:
        _importlib.reload(_module)

import bpy
from bpy.props import BoolProperty, PointerProperty

from . import hooks
from .common import (
    MIN_BLENDER,
    MME_PARAMS_PROP,
    MME_REBUILD_DELAY,
    NINESYNC_NAME,
    _mme_runtimes,
    _mme_state,
    legacy_monolith_active,
    log,
    mme_effect_roots,
    redraw_all,
    setup_logging,
)
from .mme import (
    accessory_visible_get,
    accessory_visible_set,
    mme_progress_tick,
    mme_rebuild_needed,
    mme_revive_drivers,
    mme_run_rebuilds,
    mme_update_scene,
)
from .accessories import clean_hook, post_sync_hook, report_hook
from .ui import (
    CLASSES,
    LEGACY_CLASSES,
    NineSyncFXSettings,
    menu_import_accessory,
    schedule_relink,
    settings_outdated,
)


_state = {"legacy": False}


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


def register_hooks():
    hooks.add("post_sync", hooks.FX_OWNER, post_sync_hook)
    hooks.add("clean", hooks.FX_OWNER, clean_hook)
    hooks.add("report", hooks.FX_OWNER, report_hook)


@bpy.app.handlers.persistent
def mme_load_handler(*_args):
    if _state["legacy"]:
        return
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


def remove_handlers():
    for handlers, name in (
        (bpy.app.handlers.frame_change_post, "mme_frame_handler"),
        (bpy.app.handlers.load_post, "mme_load_handler"),
        (bpy.app.handlers.depsgraph_update_post, "mme_param_watch"),
    ):
        for handler in [h for h in handlers if getattr(h, "__name__", "") == name]:
            handlers.remove(handler)


def register():
    if bpy.app.version < MIN_BLENDER:
        raise RuntimeError(
            "NineSync: FX needs Blender %d.%d or newer (this is %s)"
            % (MIN_BLENDER[0], MIN_BLENDER[1], bpy.app.version_string)
        )
    setup_logging()
    if legacy_monolith_active():
        _state["legacy"] = True
        for cls in LEGACY_CLASSES:
            bpy.utils.register_class(cls)
        log.error(
            "the old combined NineSync is enabled; disable it before using NineSync: FX"
        )
        return
    _state["legacy"] = False
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ninesync_fx = PointerProperty(type=NineSyncFXSettings)
    bpy.types.Object.ninesync_visible = BoolProperty(
        name="Visible",
        description="Show this accessory in the viewport and in renders (sets Hide in Viewport and Disable in Renders on its meshes, like mmd_tools)",
        get=accessory_visible_get,
        set=accessory_visible_set,
    )
    bpy.types.TOPBAR_MT_file_import.remove(menu_import_accessory)
    bpy.types.TOPBAR_MT_file_import.append(menu_import_accessory)
    remove_handlers()
    bpy.app.handlers.frame_change_post.append(mme_frame_handler)
    bpy.app.handlers.load_post.append(mme_load_handler)
    bpy.app.handlers.depsgraph_update_post.append(mme_param_watch)
    register_hooks()
    _mme_runtimes.clear()
    if not bpy.app.timers.is_registered(startup_check):
        bpy.app.timers.register(startup_check, first_interval=1.0)
    log.info("%s FX registered", NINESYNC_NAME)


def startup_check():
    try:
        scene = bpy.context.scene
    except AttributeError:
        return None
    if _state["legacy"]:
        return None
    if scene is not None and settings_outdated(scene):
        schedule_relink()
    return None


def unregister():
    remove_handlers()
    hooks.remove(hooks.FX_OWNER)
    _mme_runtimes.clear()
    if _state["legacy"]:
        for cls in reversed(LEGACY_CLASSES):
            try:
                bpy.utils.unregister_class(cls)
            except (RuntimeError, ValueError):
                pass
        _state["legacy"] = False
        return
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_import_accessory)
    except (ValueError, RuntimeError):
        pass
    if hasattr(bpy.types.Object, "ninesync_visible"):
        del bpy.types.Object.ninesync_visible
    if hasattr(bpy.types.Scene, "ninesync_fx"):
        del bpy.types.Scene.ninesync_fx
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


def reload_in_place():
    unregister()
    register()


if __name__ == "__main__":
    reload_in_place()
