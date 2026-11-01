from bpy.types import Panel

from ...common.constants import NINESYNC_CATEGORY
from ...common.scene_utils import (
    accessory_collection,
    find_accessory_root,
)
from ..helpers import draw_wrapped
from ..relink import settings_outdated


class NINESYNC_FX_PT_effects(Panel):
    bl_label = "Accessory Effects"
    bl_idname = "NINESYNC_FX_PT_effects"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = NINESYNC_CATEGORY
    bl_parent_id = "NINESYNC_FX_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return not settings_outdated(context.scene)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.ninesync_fx
        if not len(settings.accessories):
            draw_wrapped(layout, "Sync a PMM with NineSync: PMM Bridge, or use File > Import > MMD Accessory (.x).")
            return
        collection = accessory_collection(context.scene)
        active = getattr(context, "active_object", None)
        for entry in settings.accessories:
            root = find_accessory_root(collection, entry.key) if collection is not None else None
            row = layout.row(align=True)
            row.prop(entry, "use_effect", text="", icon="SHADERFX")
            name = row.row(align=True)
            name.enabled = entry.use_effect
            if root is not None:
                chosen = active is not None and (active == root or getattr(active, "parent", None) == root)
                pick = name.operator("ninesync_fx.select_object", text=entry.display, depress=chosen)
                pick.name = root.name
            else:
                name.label(text=entry.display)
            path = row.row(align=True)
            path.enabled = entry.use_effect
            path.scale_x = 1.4
            path.prop(entry, "effect_path", text="")
            if entry.use_effect and entry.effect_note.startswith(("Effect failed", "Effect error", "File not found")):
                note = layout.row()
                note.scale_y = 0.8
                note.alert = True
                note.label(text=entry.effect_note, icon="ERROR")
