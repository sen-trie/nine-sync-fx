from bpy.types import Operator

from ...common.constants import MME_CORE_FILE
from ...common.scene_utils import redraw_all
from ...fx.errors import FxError
from ...nodes.standard_groups import (
    mme_build_standard_groups,
    mme_save_core_library,
)


class NINESYNC_FX_OT_save_core_library(Operator):
    bl_idname = "ninesync_fx.save_core_library"
    bl_label = "Save Shared Groups to Library"
    bl_description = "Write every shared effect node group in this file into mme_core.blend next to the add-on, so later imports append them instead of building them"

    def execute(self, context):
        try:
            count = mme_save_core_library()
        except (OSError, RuntimeError) as error:
            self.report({"ERROR"}, "Could not write %s: %s" % (MME_CORE_FILE, error))
            return {"CANCELLED"}
        self.report({"INFO"}, "%s now holds %d shared group(s)" % (MME_CORE_FILE, count))
        redraw_all()
        return {"FINISHED"}


class NINESYNC_FX_OT_rebuild_core_library(Operator):
    bl_idname = "ninesync_fx.rebuild_core_library"
    bl_label = "Rebuild Standard Groups"
    bl_description = "Rebuild the standard shared groups (camera, matrices, clip space, common HLSL intrinsics) with this Blender version and save them to the library. Function groups from imported effects are kept"

    def execute(self, context):
        try:
            built = mme_build_standard_groups(force=True)
            count = mme_save_core_library()
        except (OSError, RuntimeError, FxError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, "Rebuilt %d standard group(s); %s holds %d" % (built, MME_CORE_FILE, count))
        redraw_all()
        return {"FINISHED"}
