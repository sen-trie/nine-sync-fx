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
        row = layout.row()
        row.scale_y = 1.3
        row.alert = bool(_mme_state["stale"])
        row.operator("ninesync_fx.refresh_effects", text="Rebuild Changed Effects", icon="SHADERFX")
        force = layout.operator("ninesync_fx.refresh_effects", text="Force Rebuild All", icon="FILE_REFRESH")
        force.force = True
