"""Explicit experimental operators for existing-model FX."""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ...fx.errors import FxError
from ...runtime.middle.analysis import DEFORM, MATERIAL, analyze
from ...runtime.middle.targets import apply, remove, retarget


_TARGETS = (
    (MATERIAL, "Material Slot", "Convert the pixel shader for one existing material slot"),
    (DEFORM, "Mesh Deformation", "Convert the local vertex position to a Geometry Nodes modifier"),
)


class _MiddleMesh:
    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "MESH" and context.object.mode == "OBJECT"


class NINESYNC_FX_OT_middle_apply(bpy.types.Operator, ImportHelper, _MiddleMesh):
    bl_idname = "ninesync_fx.middle_apply"
    bl_label = "Apply Experimental MME FX to Model"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".fx"
    filter_glob: StringProperty(default="*.fx", options={"HIDDEN"})
    target: EnumProperty(name="Destination", items=_TARGETS)
    slot: IntProperty(name="Material Slot", description="Zero-based slot index. -1 uses the active slot.", default=-1, min=-1)
    units: FloatProperty(name="Blender Units per MMD Unit", default=1.0, min=0.000001, precision=4)
    light: StringProperty(name="Light Direction Source")
    preview: BoolProperty(name="Analyze Only", description="Inspect the selected source without applying an effect")
    force_rebuild: BoolProperty(name="Rebuild Generated Core")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "target")
        if self.target == MATERIAL:
            layout.prop(self, "slot")
        layout.prop_search(self, "light", context.scene, "objects")
        layout.prop(self, "units")
        layout.prop(self, "preview")
        layout.prop(self, "force_rebuild")

    def execute(self, context):
        obj = context.object
        slot = obj.active_material_index if self.slot == -1 else self.slot
        try:
            if self.preview:
                facts = analyze(bpy.path.abspath(self.filepath), slot if self.target == MATERIAL else 0)
                found = "Candidates: " + ", ".join(facts.targets) if facts.targets else "No target candidates"
                if facts.controller_refs:
                    found += " | Controllers " + ", ".join(f"{name} / {item}" for _, name, item, _ in facts.controller_refs)
                self.report({"INFO"}, " | ".join((found, *facts.problems)))
                # A read-only preview does not need an undo step.
                return {"CANCELLED"}
            light = context.scene.objects.get(self.light)
            if self.light and light is None:
                self.report({"ERROR"}, f"Light direction source {self.light!r} is outside this scene.")
                return {"CANCELLED"}
            result = apply(obj, bpy.path.abspath(self.filepath), self.target, scene=context.scene, slot=slot,
                           units=self.units, light=light, force_rebuild=self.force_rebuild)
        except (FxError, OSError, RuntimeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        for warning in result.warnings:
            self.report({"WARNING"}, warning)
        self.report({"INFO"}, f"{result.status.title()} | " + ("Applied" if result.rebuilt else "Up to date"))
        return {"FINISHED"}


class NINESYNC_FX_OT_middle_remove(bpy.types.Operator, _MiddleMesh):
    bl_idname = "ninesync_fx.middle_remove"
    bl_label = "Remove Experimental MME FX from Model"
    bl_options = {"REGISTER", "UNDO"}

    target: EnumProperty(name="Destination", items=_TARGETS)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.object
        try:
            remove(obj, self.target, scene=context.scene, slot=obj.active_material_index)
        except (FxError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class NINESYNC_FX_OT_middle_retarget(bpy.types.Operator, _MiddleMesh):
    bl_idname = "ninesync_fx.middle_retarget"
    bl_label = "Retarget Experimental MME Controller"
    bl_options = {"REGISTER", "UNDO"}

    target: EnumProperty(name="Destination", items=_TARGETS)
    name: StringProperty(name="FX Controller Name")
    replacement: StringProperty(name="Replacement Object")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "target")
        self.layout.prop(self, "name")
        self.layout.prop_search(self, "replacement", context.scene, "objects")

    def execute(self, context):
        obj = context.object
        try:
            retarget(obj, self.target, self.name, context.scene.objects.get(self.replacement),
                     scene=context.scene, slot=obj.active_material_index)
        except (FxError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}
