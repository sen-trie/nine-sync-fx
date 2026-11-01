from bpy.types import Operator

from ...common.scene_utils import redraw_all
from ...standalone.legacy_repair import repair_legacy
from ..relink import refuse_if_outdated


class NINESYNC_FX_OT_repair_legacy(Operator):
    bl_idname = "ninesync_fx.repair_legacy"
    bl_label = "Repair Legacy Data"
    bl_description = "Copy effect settings from a file saved with an older NineSync version into this add-on and re-point the effect drivers. Press Rebuild All Effects afterwards"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if refuse_if_outdated(self, context):
            return {"CANCELLED"}
        copied, repointed = repair_legacy(context.scene)
        context.scene["ninesync_fx_legacy_repaired"] = True
        self.report({"INFO"}, "Restored %d setting(s), re-pointed %d driver(s)" % (copied, repointed))
        redraw_all()
        return {"FINISHED"}
