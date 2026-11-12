import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import AddonPreferences

from ..common.constants import ADDON_PACKAGE, MME_BAKE_STORE_LIMIT, MME_CACHE_LIMIT


# Both the standalone and the bundled add-on read the same two settings, so pointing them at one folder makes them share
# a cache; the default (empty) keeps each in its own user folder.
class NineSyncFXPreferences(AddonPreferences):
    bl_idname = ADDON_PACKAGE

    cache_dir: StringProperty(
        name="Cache Folder",
        description="Where parsed .x geometry, pre-cull results and strip results are kept. Leave empty for this add-on's own user folder; set the same folder in the standalone and the bundled add-on to share it",
        default="",
        subtype="DIR_PATH",
    )
    cache_limit_mb: IntProperty(
        name="Cache Limit (MB)",
        description="The least recently used cache files are deleted when the folder grows past this size",
        default=MME_CACHE_LIMIT >> 20,
        min=16,
        soft_max=16384,
    )

    bake_limit_mb: IntProperty(
        name="Shared Bake Limit (MB)",
        description="The least recently used shared bakes are deleted when the shared bake store grows past this size",
        default=MME_BAKE_STORE_LIMIT >> 20,
        min=64,
        soft_max=131072,
    )

    def draw(self, context):
        self.layout.prop(self, "cache_dir")
        self.layout.prop(self, "cache_limit_mb")
        self.layout.prop(self, "bake_limit_mb")

