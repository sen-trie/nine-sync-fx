import bpy
import os
import shutil
import subprocess
import tempfile
from bpy.props import BoolProperty, EnumProperty
from bpy.types import Operator

from ...common.constants import MME_CORE_FILE
from ...common.log import log
from ...common.scene_utils import redraw_all
from ...fx.errors import FxError
from ...nodes.library_store import (
    mme_bundled_path,
    mme_core_path,
    mme_library_fallback,
    mme_user_path,
)
from ...nodes.standard_groups import (
    mme_build_standard_groups,
    mme_save_core_library,
)
from ..helpers import draw_wrapped


# The user's library when it exists; otherwise None, because opening means editing and the add-on's own copy is
# only ever opened through a throwaway copy.
def mme_open_target():
    path = mme_user_path()
    if not mme_library_fallback() and path is not None and os.path.isfile(path):
        return path
    return None


def mme_bundled_copy():
    folder = tempfile.mkdtemp(prefix="ninesync_library_")
    path = os.path.join(folder, MME_CORE_FILE)
    shutil.copyfile(mme_bundled_path(), path)
    return path


def mme_start_blender(path):
    if not bpy.app.binary_path:
        raise OSError("this Blender has no program file to start")
    subprocess.Popen(
        [bpy.app.binary_path, path],
        cwd=os.path.dirname(path),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


# Opening a .blend replaces the session, so a second Blender is the default and replacing this one with unsaved
# changes needs a ticked box. There is deliberately no path that discards changes without one.
class NINESYNC_FX_OT_open_core_library(Operator):
    bl_idname = "ninesync_fx.open_core_library"
    bl_label = "Open FX Library .blend"
    bl_description = "Open the shared node library this add-on reads and writes. Asks first: it can start a second Blender so this file stays open"

    mode: EnumProperty(
        name="Open In",
        items=(
            ("WINDOW", "Second Blender", "Start a separate Blender with the library. This file stays open"),
            ("REPLACE", "This Blender", "Close this file and open the library here"),
        ),
        default="WINDOW",
    )
    use_bundled: BoolProperty(
        name="Open a copy of the bundled library",
        description="Your library is not available. Open a temporary copy of the one shipped with the add-on; saving it does not change either library",
        default=False,
        options={"SKIP_SAVE"},
    )
    discard_changes: BoolProperty(
        name="Discard unsaved changes in this file",
        default=False,
        options={"SKIP_SAVE"},
    )

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=460, confirm_text="Open")

    def draw(self, context):
        layout = self.layout
        path = mme_open_target()
        if path is not None:
            draw_wrapped(layout, path, 70)
        else:
            box = layout.box()
            box.alert = True
            draw_wrapped(box, mme_library_fallback() or "Your library was not found at %s." % (mme_user_path() or "the user folder"), 70)
            layout.prop(self, "use_bundled")
        layout.prop(self, "mode", expand=True)
        if self.mode == "REPLACE":
            if bpy.data.is_dirty:
                box = layout.box()
                box.alert = True
                box.label(text="This file has unsaved changes", icon="ERROR")
                box.prop(self, "discard_changes")
            else:
                layout.label(text="This file is closed. It has no unsaved changes", icon="INFO")

    def execute(self, context):
        path = mme_open_target()
        if path is None and not self.use_bundled:
            self.report({"ERROR"}, "%s not found at %s. Tick the bundled copy, or import an effect to create it" % (MME_CORE_FILE, mme_user_path() or mme_core_path()))
            return {"CANCELLED"}
        if self.mode == "REPLACE" and bpy.data.is_dirty and not self.discard_changes:
            self.report({"ERROR"}, "This file has unsaved changes. Save it first, or open the library in a second Blender")
            return {"CANCELLED"}
        try:
            if path is None:
                path = mme_bundled_copy()
            if self.mode == "WINDOW":
                mme_start_blender(path)
            else:
                bpy.ops.wm.open_mainfile(filepath=path)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            log.warning("could not open %s: %s", path, error)
            self.report({"ERROR"}, "Could not open %s: %s" % (path, error))
            return {"CANCELLED"}
        self.report({"INFO"}, "Opened %s" % path)
        return {"FINISHED"}


class NINESYNC_FX_OT_save_core_library(Operator):
    bl_idname = "ninesync_fx.save_core_library"
    bl_label = "Save Shared Groups to Library"
    bl_description = "Write every shared effect node group in this file into your mme_core.blend library, so later imports append them instead of building them"

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
