import bpy
import json
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator

from ...common.controls import (
    mme_control_source,
    mme_ensure_use_props,
    mme_load_controls_raw,
)
from ...common.state import _mme_state
from ...common.scene_utils import redraw_all
from ...runtime.build_effect import mme_rebuild_effect
from ...runtime.controls_resolve import (
    mme_apply_manual_controls,
    mme_control_item,
    mme_manual_armature,
    mme_manual_bones,
    mme_manual_clear,
    mme_manual_control,
    mme_manual_prop,
    mme_manual_set,
)
from ...runtime.update import mme_update_scene
from ..relink import refuse_if_outdated


# Controls that read the same missing source, usually the morphs of one controller model; one pick fills them.
def mme_source_peers(controls, name):
    source = mme_control_source(controls[name])
    return [
        other
        for other, desc in controls.items()
        if other == name or (mme_control_source(desc) == source and (desc.get("kind") == "const" or desc.get("manual")))
    ]


class NINESYNC_FX_OT_assign_control(Operator):
    bl_idname = "ninesync_fx.assign_control"
    bl_label = "Assign Controller"
    bl_description = "Choose the object or bone this unresolved effect controller reads. The assignment is saved with the accessory. An automatic match from the PMM takes priority"
    bl_options = {"UNDO"}

    root_name: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    control: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    clear: BoolProperty(options={"HIDDEN", "SKIP_SAVE"})
    object_name: StringProperty(name="Object", options={"SKIP_SAVE"})
    bone_name: StringProperty(name="Bone", options={"SKIP_SAVE"})
    apply_to_source: BoolProperty(
        name="Use for this controller's other items",
        description="Also assign this object to the other unresolved items of the same controller",
        default=True,
        options={"SKIP_SAVE"},
    )

    def invoke(self, context, _event):
        root = bpy.data.objects.get(self.root_name)
        if root is None:
            return {"CANCELLED"}
        if self.clear:
            return self.execute(context)
        current = root.get(mme_manual_prop(self.control))
        self.object_name = current.name if current is not None else ""
        self.bone_name = mme_manual_bones(root).get(self.control, "")
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, _context):
        layout = self.layout
        root = bpy.data.objects.get(self.root_name)
        desc = mme_load_controls_raw(root).get(self.control, {}) if root is not None else {}
        layout.label(text=desc.get("from") or self.control, icon="LINKED")
        layout.prop_search(self, "object_name", bpy.data, "objects", icon="OBJECT_DATA")
        obj = bpy.data.objects.get(self.object_name)
        armature = mme_manual_armature(obj) if obj is not None else None
        if armature is not None:
            layout.prop_search(self, "bone_name", armature.pose, "bones", icon="BONE_DATA")
        layout.prop(self, "apply_to_source")

    def execute(self, context):
        if refuse_if_outdated(self, context):
            return {"CANCELLED"}
        root = bpy.data.objects.get(self.root_name)
        controls = mme_load_controls_raw(root) if root is not None else {}
        if self.control not in controls:
            self.report({"ERROR"}, "That effect control no longer exists")
            return {"CANCELLED"}
        names = mme_source_peers(controls, self.control) if self.apply_to_source and not self.bone_name else [self.control]
        skipped = 0
        if self.clear:
            for name in names:
                mme_manual_clear(root, name)
        else:
            obj = bpy.data.objects.get(self.object_name)
            if obj is None:
                self.report({"ERROR"}, "Pick an object to assign")
                return {"CANCELLED"}
            if mme_manual_control(obj, mme_control_item(controls[self.control]), self.bone_name) is None:
                wanted = self.bone_name or mme_control_item(controls[self.control])
                self.report({"ERROR"}, "%s has no shape key, bone or transform item named %s" % (obj.name, wanted))
                return {"CANCELLED"}
            for name in names:
                if mme_manual_control(obj, mme_control_item(controls[name]), self.bone_name if name == self.control else "") is None:
                    skipped += 1
                    continue
                mme_manual_set(root, name, obj, self.bone_name if name == self.control else "")
        mme_apply_manual_controls(root, controls)
        root["mme_controls"] = json.dumps(controls)
        mme_ensure_use_props(root, controls)
        if context.scene.ninesync_fx.mme_auto_rebuild:
            mme_rebuild_effect(context.scene, root)
            mme_update_scene(context.scene, context.evaluated_depsgraph_get(), force=True)
        else:
            _mme_state["stale"].add(root.name)
        if skipped:
            self.report({"WARNING"}, "%d item(s) of this controller were not found on the object and stay unresolved" % skipped)
        redraw_all()
        return {"FINISHED"}
