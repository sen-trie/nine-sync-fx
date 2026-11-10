import bpy
import traceback
from bpy.props import StringProperty
from bpy.types import Operator

from ...common.log import log
from ...common.scene_utils import (
    redraw_all,
    short_text,
)
from ...common.state import (
    _mme_runtimes,
    _mme_state,
)
from ...runtime.build_effect import mme_rebuild_effect
from ...runtime.cache_store import mme_cache_clear
from ...runtime.params import mme_reset_params
from ...runtime.progress import mme_target_roots
from ...runtime.update import mme_update_scene
from ..relink import refuse_if_outdated


class NINESYNC_FX_OT_refresh_effects(Operator):
    bl_idname = "ninesync_fx.refresh_effects"
    bl_label = "Refresh Effects"
    bl_description = "Reload effect files and rebuild every effect pass with the current backend, density and parameters. Bakes are cleared"

    root_name: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        if refuse_if_outdated(self, context):
            return {"CANCELLED"}
        _mme_runtimes.clear()
        _mme_state["errors"].clear()
        built = 0
        for root in mme_target_roots(context.scene, self.root_name):
            try:
                built += mme_rebuild_effect(context.scene, root)
            except Exception as error:
                log.error("effect '%s' rebuild failed: %s\n%s", root.name, error, traceback.format_exc())
                self.report({"WARNING"}, "%s: %s" % (root.name, short_text(error, 80)))
        mme_update_scene(context.scene, context.evaluated_depsgraph_get(), force=True)
        self.report({"INFO"}, "Rebuilt %d effect pass(es)" % built)
        redraw_all()
        return {"FINISHED"}


class NINESYNC_FX_OT_clear_precull(Operator):
    bl_idname = "ninesync_fx.clear_precull"
    bl_label = "Clear Effect Caches"
    bl_description = "Delete the effect caches: never-drawn vertices, simplified strips and parsed .x geometry. The next rebuild analyses each effect from scratch"

    def execute(self, context):
        removed = mme_cache_clear()
        self.report({"INFO"}, "Removed %d effect cache file(s)" % removed)
        return {"FINISHED"}


class NINESYNC_FX_OT_reset_params(Operator):
    bl_idname = "ninesync_fx.reset_effect_params"
    bl_label = "Reset Parameters"
    bl_description = "Set this effect's parameters back to the values written in the .fx file"

    root_name: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        root = bpy.data.objects.get(self.root_name)
        if root is None:
            return {"CANCELLED"}
        mme_reset_params(root)
        root.update_tag()
        redraw_all()
        return {"FINISHED"}
