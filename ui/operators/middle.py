"""Explicit experimental operators for existing-model FX."""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ...fx.errors import FxError
from ...runtime.middle.analysis import DEFORM, MATERIAL, RENDER, analyze
from ...runtime.middle.specialists import AUTO, plan_specialist
from ...runtime.middle.targets import apply, remove, retarget


_TARGETS = (
    (MATERIAL, "Material Slot", "Convert the pixel shader for one existing material slot"),
    (RENDER, "Rendered Scene", "Execute ordered FX passes through the source camera and save a numeric image"),
    (DEFORM, "Mesh Deformation", "Convert the local vertex position to a Geometry Nodes modifier"),
)


class _MiddleMesh:
    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "MESH" and context.object.mode == "OBJECT"


class NINESYNC_FX_OT_middle_apply(bpy.types.Operator, ImportHelper):
    bl_idname = "ninesync_fx.middle_apply"
    bl_label = "Apply Experimental MME FX"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".fx"
    check_extension = False
    filter_glob: StringProperty(default="*.fx;*.x", options={"HIDDEN"})
    target: EnumProperty(name="Destination", default=AUTO, items=(*_TARGETS,
        (AUTO, "Automatic", "Apply a verified scene specialist or convert the active material slot")))
    package_root: StringProperty(name="Package Folder", subtype="DIR_PATH",
                                 description="Leave blank to use the selected effect's folder")
    slot: IntProperty(name="Material Slot", description="Zero-based slot index. -1 uses the active slot.", default=-1, min=-1)
    units: FloatProperty(name="Blender Units per MMD Unit", default=1.0, min=0.000001, precision=4)
    light: StringProperty(name="Light Direction Source")
    preview: BoolProperty(name="Analyze Only", description="Inspect the selected source without applying an effect")
    force_rebuild: BoolProperty(name="Rebuild Generated Core")
    target_history: EnumProperty(name="Render Target History", default="AS_ASSIGNED", items=(
        ("AS_ASSIGNED", "Use Saved Setting", "Use this assignment's setting, or keep targets for a new assignment"),
        ("KEEP", "Keep Targets", "Carry color and depth targets from the last successful update"),
        ("RESET", "Reset Targets", "Start each update with fresh color and depth targets"),
    ))

    ground_shadows: EnumProperty(name="Ground Shadows", default="AS_ASSIGNED", items=(
        ("AS_ASSIGNED", "Use Saved Setting", "Use this assignment's setting, or disable shadows for a new assignment"),
        ("DISABLED", "Disabled", "Omit ground-shadow drawing"),
        ("ENABLED", "Enabled", "Execute FX ground-shadow passes for eligible PMX materials"),
    ))
    ground_shadow_brightness: FloatProperty(name="Ground Shadow Brightness", default=1.0, min=0.0, max=2.0)
    self_shadow: EnumProperty(name="Self Shadows", default="AS_ASSIGNED", items=(
        ("AS_ASSIGNED", "Use Saved Setting", "Use this assignment's mode, or disable shadows for a new assignment"),
        ("0", "Disabled", "Omit the native self-shadow map"),
        ("1", "Mode 1", "Render the Mode 1 depth map and use it in object_ss passes"),
        ("2", "Mode 2", "Render the Mode 2 depth map and use it in object_ss passes"),
    ))
    self_shadow_distance: FloatProperty(name="Self Shadow Distance", default=8875, min=0, max=10000)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "target")
        if self.target == AUTO:
            layout.prop(self, "package_root")
        if self.target in (AUTO, MATERIAL):
            layout.prop(self, "slot")
        if self.target == RENDER:
            layout.prop(self, "target_history")
            layout.prop(self, "ground_shadows")
            if self.ground_shadows == "ENABLED":
                layout.prop(self, "ground_shadow_brightness")
            layout.prop(self, "self_shadow")
            if self.self_shadow in {"1", "2"}:
                layout.prop(self, "self_shadow_distance")
        layout.prop_search(self, "light", context.scene, "objects")
        layout.prop(self, "units")
        layout.prop(self, "preview")
        layout.prop(self, "force_rebuild")

    def execute(self, context):
        obj = context.object
        slot = (obj.active_material_index if obj else 0) if self.slot == -1 else self.slot
        path = bpy.path.abspath(self.filepath)
        package_root = bpy.path.abspath(self.package_root) if self.package_root else None
        try:
            if self.preview:
                destination = self.target
                if destination == AUTO:
                    specialist = plan_specialist(path, package_root)
                    if specialist is not None:
                        self.report({"INFO"}, f"{specialist.label} | {specialist.coverage} | {specialist.omissions}")
                        return {"CANCELLED"}
                    destination = MATERIAL
                facts = analyze(path, slot if destination == MATERIAL else 0,
                                destination=destination if destination in (MATERIAL, DEFORM) else None)
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
            reset_targets = None
            ground_shadows = None
            self_shadow_mode = None
            if self.target == RENDER:
                if self.target_history != "AS_ASSIGNED":
                    reset_targets = self.target_history == "RESET"
                if self.ground_shadows != "AS_ASSIGNED":
                    ground_shadows = self.ground_shadows == "ENABLED"
                if self.self_shadow != "AS_ASSIGNED":
                    self_shadow_mode = int(self.self_shadow)
            result = apply(obj, path, self.target, scene=context.scene, slot=slot, units=self.units,
                           light=light, force_rebuild=self.force_rebuild, package_root=package_root,
                           reset_targets=reset_targets, ground_shadows=ground_shadows,
                           ground_shadow_brightness=self.ground_shadow_brightness if ground_shadows else None,
                           self_shadow_mode=self_shadow_mode,
                           self_shadow_distance=self.self_shadow_distance if self_shadow_mode else None)
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
