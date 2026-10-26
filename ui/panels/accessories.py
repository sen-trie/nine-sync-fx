from bpy.types import Panel

from ...accessories.parenting import first_armature
from ...common.constants import (
    NINESYNC_ACC_LABEL,
    NINESYNC_CATEGORY,
)
from ...common.scene_utils import (
    accessory_collection,
    find_accessory_root,
)
from ..helpers import (
    accessory_visibility_animated,
    accessory_visibility_keyed,
    draw_standalone_block,
)
from ..relink import settings_outdated


class NINESYNC_FX_PT_accessories(Panel):
    bl_label = NINESYNC_ACC_LABEL
    bl_idname = "NINESYNC_FX_PT_accessories"
    bl_order = 1
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = NINESYNC_CATEGORY

    def draw(self, context):
        layout = self.layout
        if settings_outdated(context.scene):
            return
        settings = context.scene.ninesync_fx
        draw_standalone_block(layout, context, settings)
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(settings, "sync_accessories")
        col.prop(settings, "sphere_maps")
        col.prop(settings, "accessory_dir")
        armature = first_armature(settings)
        if not len(settings.accessories):
            layout.label(text="Sync a PMM with NineSync: PMM Bridge, or import an accessory directly", icon="INFO")
            layout.operator("import_scene.ninesync_fx_accessory", text="Import Accessory (.x)", icon="IMPORT")
            return
        collection = accessory_collection(context.scene)
        active = getattr(context, "active_object", None)
        frame = context.scene.frame_current
        box = layout.box()
        for entry in settings.accessories:
            root = find_accessory_root(collection, entry.key) if collection is not None else None
            row = box.row(align=True)
            if entry.standalone:
                row.label(text="", icon="IMPORT")
            else:
                row.prop(entry, "enabled", text="", icon="LINKED" if entry.enabled else "UNLINKED")
            icon = "SHADERFX" if root is not None and root.get("mme_fx") else "MESH_DATA"
            if root is None:
                sub = row.row(align=True)
                sub.enabled = False
                sub.label(text=entry.display, icon=icon)
            else:
                chosen = active is not None and (active == root or getattr(active, "parent", None) == root)
                pick = row.operator("ninesync_fx.select_object", text=entry.display, icon=icon, depress=chosen)
                pick.name = root.name
                shown = root.ninesync_visible
                row.prop(root, "ninesync_visible", text="", icon="HIDE_OFF" if shown else "HIDE_ON", emboss=False)
                keyed = accessory_visibility_keyed(root, frame)
                key = row.operator(
                    "ninesync_fx.key_accessory_visibility",
                    text="",
                    icon="DECORATE_KEYFRAME" if keyed else ("DECORATE_ANIMATE" if accessory_visibility_animated(root) else "DECORATE"),
                    emboss=False,
                )
                key.root_name = root.name
            if armature is not None and not entry.standalone:
                row.prop_search(entry, "parent_bone", armature.pose, "bones", text="", icon="BONE_DATA")
        layout.operator("import_scene.ninesync_fx_accessory", text="Import Accessory (.x)", icon="IMPORT")
