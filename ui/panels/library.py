import bpy
import os
from bpy.types import Panel

from ...common.constants import (
    MME_CORE_FILE,
    MME_KEY_PROP,
    NINESYNC_CATEGORY,
)
from ...common.scene_utils import short_text
from ...nodes.core_library import mme_core_names
from ...nodes.library_store import (
    mme_bundled_path,
    mme_core_path,
    mme_library_fallback,
    mme_user_path,
)
from ..helpers import draw_wrapped
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
        return not settings_outdated(context.scene)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.ninesync_fx
        path = mme_core_path()
        names = mme_core_names()
        col = layout.column(align=True)
        col.label(text=MME_CORE_FILE, icon="FILE_BLEND" if os.path.isfile(path) else "ERROR")
        col.label(text=short_text(path, 60))
        col.label(text="%d shared groups in the library" % len(names))
        local = [g for g in bpy.data.node_groups if g.get(MME_KEY_PROP)]
        col.label(text="%d shared groups in this file (%d not in the library)" % (len(local), len([g for g in local if g.name not in names])))
        reason = mme_library_fallback()
        user = mme_user_path()
        if reason:
            box = layout.box()
            box.alert = True
            draw_wrapped(box, "Using the bundled library until Blender restarts: %s" % reason)
        elif path == mme_bundled_path() and not (user and os.path.isfile(user)):
            draw_wrapped(layout, "Your library is created on the first effect import.")
        layout.operator("ninesync_fx.open_core_library", icon="FILE_FOLDER")
        layout.prop(settings, "mme_core_autosave")
        row = layout.row(align=True)
        row.enabled = not reason
        row.operator("ninesync_fx.save_core_library", text="Save to Library", icon="FILE_TICK")
        row.operator("ninesync_fx.rebuild_core_library", text="Rebuild Standard", icon="FILE_REFRESH")
