from bpy.types import Panel

from ...common.constants import NINESYNC_CATEGORY
from ..relink import settings_outdated


class NINESYNC_FX_PT_bake(Panel):
    bl_label = "Bake"
    bl_idname = "NINESYNC_FX_PT_bake"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = NINESYNC_CATEGORY
    bl_parent_id = "NINESYNC_FX_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return not settings_outdated(context.scene) and context.scene.ninesync_fx.mme_effects

    def draw(self, context):
        layout = self.layout
        settings = context.scene.ninesync_fx
        layout.label(text="Scene frames %d - %d" % (context.scene.frame_start, context.scene.frame_end))
        layout.prop(settings, "mme_bake_target", text="Store")
        if settings.mme_bake_target == "DISK":
            layout.prop(settings, "mme_bake_dir", text="")
        row = layout.row(align=True)
        row.operator("ninesync_fx.bake_effects", text="Bake All", icon="RENDER_ANIMATION")
        clear = row.operator("ninesync_fx.bake_effects", text="Clear All", icon="TRASH")
        clear.clear = True
