import bpy
import os
from bpy.types import Panel

from ...common.constants import (
    MME_CORE_FILE,
    MME_KEY_PROP,
    NINESYNC_CATEGORY,
)
from ...nodes.core_library import (
    mme_core_names,
    mme_core_path,
)
from ..relink import settings_outdated


class NINESYNC_FX_PT_library(Panel):
    bl_label = "Core Node Library"
    bl_idname = "NINESYNC_FX_PT_library"
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
        path = mme_core_path()
        names = mme_core_names()
        col = layout.column(align=True)
        col.label(text=MME_CORE_FILE, icon="FILE_BLEND" if os.path.isfile(path) else "ERROR")
        col.label(text="%d shared groups in the library" % len(names))
        local = [g for g in bpy.data.node_groups if g.get(MME_KEY_PROP)]
        col.label(text="%d shared groups in this file (%d not in the library)" % (len(local), len([g for g in local if g.name not in names])))
        layout.prop(settings, "mme_core_autosave")
        row = layout.row(align=True)
        row.operator("ninesync_fx.save_core_library", text="Save to Library", icon="FILE_TICK")
        row.operator("ninesync_fx.rebuild_core_library", text="Rebuild Standard", icon="FILE_REFRESH")
