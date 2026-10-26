from bpy.types import Panel

from ...common.constants import (
    NINESYNC_CATEGORY,
    NINESYNC_FX_LABEL,
)
from ...common.state import _mme_state
from ..helpers import draw_wrapped
from ..relink import (
    STALE_SETTINGS_MESSAGE,
    settings_outdated,
)


class NINESYNC_FX_PT_main(Panel):
    bl_label = NINESYNC_FX_LABEL
    bl_idname = "NINESYNC_FX_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = NINESYNC_CATEGORY
    bl_order = 2

    def draw(self, context):
        layout = self.layout
        if settings_outdated(context.scene):
            draw_wrapped(layout, STALE_SETTINGS_MESSAGE)
            return
        settings = context.scene.ninesync_fx
        layout.prop(settings, "mme_effects")
        if not settings.mme_effects:
            draw_wrapped(layout, "Turn this on and sync to import accessory effects.")
            return
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(settings, "mme_backend")
        col.prop(settings, "mme_density", slider=True)
        col.prop(settings, "mme_tolerance")
        col.prop(settings, "mme_auto_rebuild")
        precull = col.row(align=True)
        precull.prop(settings, "mme_precull")
        precull.operator("ninesync_fx.clear_precull", text="", icon="TRASH")
        if settings.mme_backend == "PYTHON":
            col.prop(settings, "mme_live")
        col.prop(settings, "mme_standard_view")
        row = layout.row()
        row.scale_y = 1.3
        row.alert = bool(_mme_state["stale"])
        row.operator("ninesync_fx.refresh_effects", text="Rebuild All Effects", icon="SHADERFX")
