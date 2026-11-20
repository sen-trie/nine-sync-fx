from bpy.types import Panel

from ...common.constants import NINESYNC_CATEGORY
from ..relink import settings_outdated


class NINESYNC_FX_PT_settings(Panel):
    bl_label = "Settings"
    bl_idname = "NINESYNC_FX_PT_settings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = NINESYNC_CATEGORY
    bl_parent_id = "NINESYNC_FX_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return not settings_outdated(context.scene)

    def draw(self, context):
        settings = context.scene.ninesync_fx
        col = self.layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(settings, "mme_backend")
        col.prop(settings, "mme_density", slider=True)
        col.prop(settings, "mme_tolerance")
        col.prop(settings, "mme_memory_budget")
        col.prop(settings, "mme_auto_rebuild")
        col.prop(settings, "mme_log_detail")
        precull = col.row(align=True)
        precull.prop(settings, "mme_precull")
        precull.operator("ninesync_fx.clear_precull", text="", icon="TRASH")
        if settings.mme_backend != "PYTHON":
            col.prop(settings, "mme_particle_split")
            col.prop(settings, "mme_transparency_order")
        if settings.mme_backend == "PYTHON":
            col.prop(settings, "mme_live")
