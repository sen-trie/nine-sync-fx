"""Opt-in analyzer and explicit Scene Effect actions."""

from pathlib import Path

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ..common.constants import ADDON_PACKAGE, NINESYNC_CATEGORY
from ..ui.helpers import draw_wrapped
from .compositor import CompositorAdapter, OWNER, TOKEN, plan
from .registry import recognize
from .scan import scan


_analysis = None


def enabled(context):
    addon = context.preferences.addons.get(ADDON_PACKAGE)
    return bool(addon and getattr(addon.preferences, "experimental_mme_adapters", False))


def has_scene_effects(context):
    return any(effect.node_group and effect.node_group.get(OWNER)
               for effect in context.scene.compositor_effects)


def _current(context):
    if _analysis and _analysis[0] == context.scene.session_uid:
        return _analysis
    return None


class NINESYNC_FX_OT_analyze_adapter(bpy.types.Operator, ImportHelper):
    bl_idname = "ninesync_fx.analyze_adapter"
    bl_label = "Analyze Local Effect"
    filter_glob: StringProperty(default="*.fx;*.x", options={"HIDDEN"})
    package_root: StringProperty(name="Package Root", subtype="DIR_PATH",
                                 description="Leave blank to scan the selected file's folder")

    @classmethod
    def poll(cls, context):
        return enabled(context)

    def draw(self, context):
        self.layout.prop(self, "package_root")

    def execute(self, context):
        global _analysis
        selected = Path(bpy.path.abspath(self.filepath))
        root = Path(bpy.path.abspath(self.package_root)) if self.package_root else selected.parent
        facts = scan(selected, root)
        _analysis = (context.scene.session_uid, selected, root, facts, recognize(facts))
        return {"FINISHED"}


class NINESYNC_FX_OT_adapter_action(bpy.types.Operator):
    bl_idname = "ninesync_fx.adapter_action"
    bl_label = "Change Experimental Scene Effect"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(items=[(item, item.title(), "") for item in
                                ("TONAL", "BLOOM", "VERIFIED", "REBUILD", "DISABLE", "REMOVE")])
    token: StringProperty(options={"HIDDEN"})
    index: IntProperty(default=-1, options={"HIDDEN"})
    scene_uid: StringProperty(options={"HIDDEN"})
    fingerprint: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return enabled(context) or has_scene_effects(context)

    def invoke(self, context, event):
        action = self.action
        if action in {"VERIFIED", "TONAL", "BLOOM"} and not enabled(context):
            return {"CANCELLED"}
        if action == "REBUILD":
            title, message = "Rebuild Generated Core", "Core edits will be replaced. The wrapper and its controls will be preserved."
        elif action == "REMOVE":
            title, message = "Remove Scene Effect", "Remove this effect from the current scene's stack?"
        elif action == "DISABLE":
            return self.execute(context)
        else:
            current = _current(context)
            if action == "VERIFIED" and (not current or not current[4].adapter):
                self.report({"ERROR"}, "Analyze an exact package first.")
                return {"CANCELLED"}
            adapter_plan = (plan(current[4].adapter, current[4], current[3]) if action == "VERIFIED"
                            else plan(action.lower()))
            self.fingerprint = adapter_plan.source
            title = f"Apply {adapter_plan.label}"
            message = f"Append to {context.scene.name}'s Scene Effects. {adapter_plan.omissions}"
        self.scene_uid = str(context.scene.session_uid)
        return context.window_manager.invoke_confirm(self, event, title=title, message=message)

    def execute(self, context):
        scene = context.scene
        action = self.action
        if action in {"VERIFIED", "TONAL", "BLOOM"} and not enabled(context):
            return {"CANCELLED"}
        try:
            if self.scene_uid and self.scene_uid != str(scene.session_uid):
                raise ValueError("The target scene changed. Run the action again in the intended scene.")
            if action == "VERIFIED":
                current = _current(context)
                if not current:
                    raise ValueError("Analyze the package again before applying.")
                facts = scan(current[1], current[2])
                match = recognize(facts)
                if not match.adapter or not self.fingerprint or facts.fingerprint != self.fingerprint:
                    raise ValueError("The package changed or is no longer verified. Analyze it again.")
                CompositorAdapter.apply(scene, plan(match.adapter, match, facts))
            elif action in {"TONAL", "BLOOM"}:
                CompositorAdapter.apply(scene, plan(action.lower()))
            else:
                effect = scene.compositor_effects[self.index] if 0 <= self.index < len(scene.compositor_effects) else None
                if not effect or not effect.node_group or effect.node_group.get(TOKEN) != self.token:
                    raise ValueError("The effect moved or changed. Select the action again.")
                if action == "REMOVE":
                    CompositorAdapter.remove(scene, effect)
                elif action == "REBUILD":
                    CompositorAdapter.rebuild(effect)
                else:
                    CompositorAdapter.disable(effect)
        except (ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class NINESYNC_FX_PT_experimental_adapters(bpy.types.Panel):
    bl_idname = "NINESYNC_FX_PT_experimental_adapters"
    bl_label = "Experimental Scene Effects"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = NINESYNC_CATEGORY
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return enabled(context) or has_scene_effects(context)

    def draw(self, context):
        layout = self.layout
        show_analyzer = enabled(context)
        if show_analyzer:
            layout.operator("ninesync_fx.analyze_adapter", icon="FILE_FOLDER")
        current = _current(context) if show_analyzer else None
        if current:
            _, _, _, facts, match = current
            report = layout.box()
            draw_wrapped(report, facts.entry or "Select an unpacked .fx or .x file.")
            draw_wrapped(report, facts.role.replace("_", " ").title())
            draw_wrapped(report, match.kind.replace("_", " ").title())
            for reason in (*match.reasons, *facts.warnings, *facts.requirements):
                draw_wrapped(report, reason)
            if facts.role in {"ACCESSORY_GEOMETRY", "PARTICLE", "MIXED"}:
                report.operator("import_scene.ninesync_fx_accessory", text="Open Generic Accessory Import")
            if match.adapter:
                entry = match.entry
                verified = plan(match.adapter, match, facts)
                draw_wrapped(report, f'{entry["name"]} {entry["version"]} by {entry["author"]}')
                if entry["source"].startswith("https://"):
                    report.operator("wm.url_open", text="Creator Source", icon="URL").url = entry["source"]
                draw_wrapped(report, verified.coverage)
                draw_wrapped(report, verified.omissions)
                report.operator("ninesync_fx.adapter_action", text=f"Apply {verified.label}").action = "VERIFIED"

        if show_analyzer:
            draw_wrapped(layout, f"Append an editable Scene Effect to {context.scene.name}.")
            for adapter in ("tonal", "bloom"):
                generic = plan(adapter)
                layout.operator("ninesync_fx.adapter_action", text=f"Apply {generic.label}").action = adapter.upper()

        for index, effect in enumerate(context.scene.compositor_effects):
            group = effect.node_group
            if not group or not group.get(OWNER):
                continue
            box = layout.box()
            box.label(text=effect.name)
            box.prop(effect, "enable_for_render", text="Render")
            box.prop(effect, "enable_for_preview", text="Preview")
            for socket in group.interface.items_tree:
                if socket.item_type == "SOCKET" and socket.in_out == "INPUT" and socket.socket_type == "NodeSocketFloat":
                    box.prop(getattr(effect.properties.inputs, socket.identifier), "value", text=socket.name)
            for action, label in (("REBUILD", "Rebuild Generated Core"), ("DISABLE", "Disable"), ("REMOVE", "Remove")):
                op = box.operator("ninesync_fx.adapter_action", text=label)
                op.action, op.index, op.token = action, index, group[TOKEN]


CLASSES = (NINESYNC_FX_OT_analyze_adapter, NINESYNC_FX_OT_adapter_action,
           NINESYNC_FX_PT_experimental_adapters)
