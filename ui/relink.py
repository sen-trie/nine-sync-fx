import bpy
import importlib

from ..common.constants import ADDON_PACKAGE
from ..common.log import log
from ..common.scene_utils import redraw_all


STALE_SETTINGS_MESSAGE = "An older copy of NineSync: FX replaced this add-on's settings. They were re-linked; press the button again, and remove the old copy in Preferences > Add-ons"


_relink = {"pending": False}


# After an add-on update the scene pointer can still use an older copy's settings class, which lacks
# properties like mme_backend.
def settings_outdated(scene):
    settings = getattr(scene, "ninesync_fx", None)
    return settings is None or not hasattr(settings, "mme_backend")


# Reloads this add-on in place so the scene points at the current settings class.
def relink_settings():
    _relink["pending"] = False
    try:
        scene = bpy.context.scene
    except AttributeError:
        scene = None
    if scene is not None and not settings_outdated(scene):
        return None
    importlib.import_module(ADDON_PACKAGE).reload_in_place()
    log.warning("settings were registered by an older copy of the add-on; re-linked to this version")
    redraw_all()
    return None


# Runs the relink from a timer instead of inline; only one is queued at a time.
def schedule_relink():
    if _relink["pending"]:
        return
    _relink["pending"] = True
    bpy.app.timers.register(relink_settings, first_interval=0.01)


# Operators call this first: if the settings are stale it schedules the relink, tells the user to press the
# button again and returns True.
def refuse_if_outdated(operator, context):
    if not settings_outdated(context.scene):
        return False
    schedule_relink()
    operator.report({"WARNING"}, STALE_SETTINGS_MESSAGE)
    return True
