ADDON_VERSION = (0, 7, 2)

bl_info = {
    "name": "NineSync: FX Importer",
    "author": "sentrie",
    "version": ADDON_VERSION,
    "blender": (5, 3, 0),
    "location": "View3D > Sidebar > NineSync",
    "description": "⑨-Sync FX: import MikuMikuDance accessories (.x) and run their MME effects (.fx) through Geometry Nodes and shader nodes. Works alone or with NineSync: PMM Bridge",
    "doc_url": "https://github.com/sen-trie/nine-sync-fx",
    "tracker_url": "https://github.com/sen-trie/nine-sync-fx/issues",
    "category": "Import-Export",
}

import sys as _sys

for _name in [_n for _n in _sys.modules if _n.startswith(__name__ + ".")]:
    del _sys.modules[_name]

import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty

from . import hooks
from .i18n import translations
from .common.constants import (
    MIN_BLENDER,
    NINESYNC_NAME,
)
from .common.log import (
    log,
    setup_logging,
)
from .common.state import _mme_runtimes
from .handlers import (
    mme_frame_handler,
    mme_load_handler,
    mme_param_watch,
    register_hooks,
    remove_handlers,
    startup_check,
)
from .runtime.live_edit import mme_set_live_edit
from .runtime.visibility import (
    accessory_visible_get,
    accessory_visible_set,
)
from .ui import CLASSES
from .ui.preferences import NineSyncFXPreferences
from .ui.properties import (
    MME_TRANSPARENCY_OVERRIDE_ITEMS,
    NineSyncFXSettings,
)
from .ui.operators.accessories import menu_import_accessory


_install = {"registered": False, "classes": []}


# In the combined build the bundle registers the one preferences class on its own id (Core's and FX's properties together) and
# marks its root package NINESYNC_PREFERENCES_OWNER; FX then adds no second class for the same id.
def preferences_owned_elsewhere():
    parent = __name__.rsplit(".", 1)[0] if "." in __name__ else ""
    return bool(getattr(_sys.modules.get(parent), "NINESYNC_PREFERENCES_OWNER", False))


# A second copy of this add-on (a legacy zip next to the extension, or a renamed folder) would register the
# same classes and properties. The later copy refuses to start and leaves the first one untouched.
def conflicting_copy():
    return not _install["registered"] and hasattr(bpy.types.Scene, "ninesync_fx")


# The translation dictionary goes in first and comes out on every failed or refused enable, so a refused copy leaves nothing
# registered. The owner is this package's own name: standalone and bundled installs have different ones.
def register():
    translations.register(__package__)
    try:
        _register_all()
    except BaseException:
        translations.unregister(__package__)
        raise


def _register_all():
    if conflicting_copy():
        raise RuntimeError(
            "NineSync: FX Importer is already enabled from another install; disable that copy first. Nothing was changed."
        )
    if bpy.app.version < MIN_BLENDER:
        raise RuntimeError(
            "NineSync: FX needs Blender %d.%d or newer (this is %s)"
            % (MIN_BLENDER[0], MIN_BLENDER[1], bpy.app.version_string)
        )
    setup_logging()
    _install["classes"] = [cls for cls in CLASSES if not (cls is NineSyncFXPreferences and preferences_owned_elsewhere())]
    for cls in _install["classes"]:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ninesync_fx = PointerProperty(type=NineSyncFXSettings)
    bpy.types.Object.ninesync_visible = BoolProperty(
        name="Visible",
        description="Show this accessory in the viewport and in renders (sets Hide in Viewport and Disable in Renders on its meshes, like mmd_tools)",
        get=accessory_visible_get,
        set=accessory_visible_set,
    )
    bpy.types.Object.ninesync_fx_transparency_order = EnumProperty(
        name="Transparency Order",
        description="Transparency Order for this accessory's effect, overriding the one in the Effects panel",
        items=MME_TRANSPARENCY_OVERRIDE_ITEMS,
        default="SCENE",
        update=lambda self, context: context.scene.update_tag(),
    )
    bpy.types.Object.ninesync_fx_edit_instances = BoolProperty(
        name="Edit Particle Instances",
        description="Part of this pass, so leave it enabled when rendering. Turns each particle into an instance so the nodes in the pass's frame can edit them; the edits are part of the bake (slower on big effects)",
        default=False,
        update=lambda self, context: context.scene.update_tag(),
    )
    bpy.types.Object.ninesync_fx_edit_live = BoolProperty(
        name="Enable Geometry Node Editing",
        description="Show this pass's live nodes in the viewport instead of its bake, so edits to its node groups show at once. Ignored in renders: they keep using the bake. Turn it off to see the bake again",
        default=False,
        update=lambda self, context: mme_set_live_edit(self, self.ninesync_fx_edit_live),
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
    _install["registered"] = True
    log.info("%s FX registered", NINESYNC_NAME)


def unregister():
    if not _install["registered"]:
        return
    _install["registered"] = False
    remove_handlers()
    hooks.remove(hooks.FX_OWNER)
    _mme_runtimes.clear()
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_import_accessory)
    except (ValueError, RuntimeError):
        pass
    if hasattr(bpy.types.Object, "ninesync_fx_transparency_order"):
        del bpy.types.Object.ninesync_fx_transparency_order
    if hasattr(bpy.types.Object, "ninesync_fx_edit_live"):
        del bpy.types.Object.ninesync_fx_edit_live
    if hasattr(bpy.types.Object, "ninesync_fx_edit_instances"):
        del bpy.types.Object.ninesync_fx_edit_instances
    if hasattr(bpy.types.Object, "ninesync_visible"):
        del bpy.types.Object.ninesync_visible
    if hasattr(bpy.types.Scene, "ninesync_fx"):
        del bpy.types.Scene.ninesync_fx
    for cls in reversed(_install["classes"]):
        bpy.utils.unregister_class(cls)
    _install["classes"] = []
    translations.unregister(__package__)


def reload_in_place():
    unregister()
    register()


if __name__ == "__main__":
    reload_in_place()
