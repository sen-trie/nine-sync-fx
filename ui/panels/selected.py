from bpy.types import Panel

from ...common.constants import NINESYNC_CATEGORY
from ..helpers import (
    draw_wrapped,
    fx_effect_root,
)
from ..relink import settings_outdated
from .effect_info import draw_fx_selected


class NINESYNC_FX_PT_selected(Panel):
    bl_label = "Selected Effect"
    bl_idname = "NINESYNC_FX_PT_selected"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = NINESYNC_CATEGORY
    bl_parent_id = "NINESYNC_FX_PT_main"

    @classmethod
    def poll(cls, context):
        return not settings_outdated(context.scene)

    def draw(self, context):
        active = context.active_object
        root = fx_effect_root(active)
        if root is None:
            draw_wrapped(self.layout, "Select an effect accessory (MMDAcc_...) or one of its passes to see its settings here.")
            return
        draw_fx_selected(self.layout, context, root, active)
