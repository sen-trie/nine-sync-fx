import bpy
import os
import traceback
from bpy.props import BoolProperty, CollectionProperty, FloatProperty, StringProperty
from bpy.types import Operator, OperatorFileListElement
from bpy_extras.io_utils import ImportHelper
from ... import hooks

from ...accessories.standalone_import import import_standalone_accessory
from ...common.animation import iter_fcurves
from ...common.constants import (
    NINESYNC_NAME,
    REPORT_LOG_LINES,
    REPORT_TEXT_NAME,
)
from ...common.log import (
    LOG_TEXT_NAME,
    log,
)
from ...common.scene_utils import (
    redact_paths,
    redraw_all,
    short_text,
)
from ..helpers import accessory_visibility_keyed
from ..hook_entry import (
    clean_hook,
    report_hook,
)
from ..relink import refuse_if_outdated


class NINESYNC_FX_OT_key_accessory_visibility(Operator):
    bl_idname = "ninesync_fx.key_accessory_visibility"
    bl_label = "Keyframe Visibility"
    bl_description = "Insert a visibility keyframe (Hide in Viewport and Disable in Renders) on this accessory's meshes at the current frame. Click on a keyed frame to remove the key"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    root_name: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        root = bpy.data.objects.get(self.root_name)
        if root is None:
            return {"CANCELLED"}
        frame = context.scene.frame_current
        remove = accessory_visibility_keyed(root, frame)
        for child in root.children:
            for path in ("hide_viewport", "hide_render"):
                if remove:
                    try:
                        child.keyframe_delete(path, frame=frame)
                    except RuntimeError:
                        pass
                else:
                    child.keyframe_insert(path, frame=frame)
            anim = child.animation_data
            if anim is not None and anim.action is not None:
                for fcurve in iter_fcurves(anim.action):
                    if fcurve.data_path in ("hide_viewport", "hide_render"):
                        for point in fcurve.keyframe_points:
                            point.interpolation = "CONSTANT"
        redraw_all()
        return {"FINISHED"}


class NINESYNC_FX_OT_import_accessory(Operator, ImportHelper):
    bl_idname = "import_scene.ninesync_fx_accessory"
    bl_label = "Import MMD Accessory"
    bl_description = "Import MikuMikuDance accessories (.x) without a PMM. Accessories with an MME effect (.fx) are built and listed in the NineSync panels"
    bl_options = {"REGISTER", "UNDO", "PRESET"}

    filename_ext = ".x"
    filter_glob: StringProperty(default="*.x", options={"HIDDEN"})
    files: CollectionProperty(type=OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})
    scale: FloatProperty(
        name="Scale",
        description="Size multiplier, matching mmd_tools' import scale (0.08 by default)",
        default=0.08,
        min=0.0001,
        soft_max=10.0,
    )
    use_effect: BoolProperty(
        name="MME Effect",
        description="Build the accessory's MME effect (.fx) when one is found next to it",
        default=True,
    )
    effect_path: StringProperty(
        name="Effect File",
        description="Effect (.fx) to use instead of the one next to the .x. Leave empty to look it up",
        subtype="FILE_PATH",
    )

    def invoke(self, context, event):
        shared = hooks.registry()["shared"].get("import_scale")
        if shared:
            self.scale = shared
        return super().invoke(context, event)

    def execute(self, context):
        if refuse_if_outdated(self, context):
            return {"CANCELLED"}
        names = [f.name for f in self.files if f.name] or [os.path.basename(self.filepath)]
        folder = self.directory or os.path.dirname(self.filepath)
        imported = 0
        for name in names:
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                self.report({"WARNING"}, "Not found: %s" % path)
                continue
            try:
                root = import_standalone_accessory(context, path, self.scale, self.use_effect, bpy.path.abspath(self.effect_path) if self.effect_path else "")
            except Exception as error:
                log.error("import of '%s' failed: %s\n%s", path, error, traceback.format_exc())
                self.report({"ERROR"}, "%s: %s" % (name, short_text(error, 80)))
                continue
            if root is not None:
                imported += 1
        self.report({"INFO"}, "Imported %d accessory file(s)" % imported)
        redraw_all()
        return {"FINISHED"} if imported else {"CANCELLED"}


def menu_import_accessory(self, context):
    self.layout.operator(NINESYNC_FX_OT_import_accessory.bl_idname, text="MMD Accessory (.x)", icon="EMPTY_AXIS")


class NINESYNC_FX_OT_select_object(Operator):
    bl_idname = "ninesync_fx.select_object"
    bl_label = "Select"
    bl_description = "Select this object and make it active"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    name: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        obj = bpy.data.objects.get(self.name)
        if obj is None:
            return {"CANCELLED"}
        for other in context.selected_objects:
            other.select_set(False)
        obj.hide_select = False
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


class NINESYNC_FX_OT_clean(Operator):
    bl_idname = "ninesync_fx.clean"
    bl_label = "Clean Accessories"
    bl_description = "Delete imported accessory objects, their effects and their animation. Your own data is never touched"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        if refuse_if_outdated(self, context):
            return {"CANCELLED"}
        counts = clean_hook(context.scene, {"fx": True})
        self.report({"INFO"}, "Removed %d accessory(ies)" % counts["accessories"])
        redraw_all()
        return {"FINISHED"}


class NINESYNC_FX_OT_copy_report(Operator):
    bl_idname = "ninesync_fx.copy_report"
    bl_label = "Support Report"
    bl_description = "Copy a support report with versions, effect settings and the recent log to the clipboard. User folders are redacted, but check it before posting"

    def execute(self, context):
        lines = [NINESYNC_NAME + " FX support report"] + report_hook(context)
        block = bpy.data.texts.get(LOG_TEXT_NAME)
        lines.append("")
        lines.append("---- last %d log lines ----" % REPORT_LOG_LINES)
        if block is not None:
            lines.extend(block.as_string().splitlines()[-REPORT_LOG_LINES:])
        text = redact_paths("\n".join(lines))
        context.window_manager.clipboard = text
        report = bpy.data.texts.get(REPORT_TEXT_NAME) or bpy.data.texts.new(REPORT_TEXT_NAME)
        report.clear()
        report.write(text)
        self.report({"INFO"}, "Support report copied (%d lines) and saved to the %s text" % (len(text.splitlines()), REPORT_TEXT_NAME))
        return {"FINISHED"}
