import bpy
import time
import traceback
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator

from ...common.controls import mme_effect_passes
from ...common.log import log
from ...common.scene_utils import redraw_all
from ...runtime.bake import (
    mme_bake_pass,
    mme_clear_bake,
)
from ...runtime.progress import (
    mme_progress_begin,
    mme_progress_end,
    mme_progress_step,
    mme_target_roots,
)
from ..relink import refuse_if_outdated


class NINESYNC_FX_OT_bake_effects(Operator):
    bl_idname = "ninesync_fx.bake_effects"
    bl_label = "Bake Effects"
    bl_description = "Bake effect geometry over the scene frame range so playback and rendering read stored geometry instead of running shaders"

    root_name: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    clear: BoolProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        if refuse_if_outdated(self, context):
            return {"CANCELLED"}
        scene = context.scene
        settings = scene.ninesync_fx
        roots = mme_target_roots(scene, self.root_name)
        passes = [p for root in roots for p in mme_effect_passes(root)]
        if not passes:
            self.report({"WARNING"}, "No effect passes to %s" % ("clear" if self.clear else "bake"))
            return {"CANCELLED"}
        if settings.mme_bake_target == "DISK" and not bpy.data.filepath and settings.mme_bake_dir.startswith("//"):
            self.report({"ERROR"}, "Save the .blend first, or pick an absolute Bake Folder")
            return {"CANCELLED"}
        frame = scene.frame_current
        done = 0
        started = time.time()
        if not self.clear:
            mme_progress_begin(context, "NineSync baking", len(passes), scene.frame_start, scene.frame_end)
        try:
            for index, obj in enumerate(passes):
                mme_progress_step(index, obj.name)
                try:
                    if self.clear:
                        done += mme_clear_bake(context, obj)
                    else:
                        done += mme_bake_pass(context, obj, scene.frame_start, scene.frame_end, settings)
                except Exception as error:
                    log.error("bake of '%s' failed: %s\n%s", obj.name, error, traceback.format_exc())
        finally:
            mme_progress_end()
        scene.frame_set(frame)
        if self.clear:
            self.report({"INFO"}, "Cleared %d effect bake(s)" % done)
        else:
            self.report(
                {"INFO"},
                "Baked %d of %d effect pass(es), frames %d-%d, in %.1fs"
                % (done, len(passes), scene.frame_start, scene.frame_end, time.time() - started),
            )
        redraw_all()
        return {"FINISHED"}
