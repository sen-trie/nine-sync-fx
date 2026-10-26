import bpy
import importlib
import json
import os
import tempfile
import textwrap
import time
import traceback
from . import hooks
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty
from bpy.types import Operator, OperatorFileListElement, Panel, PropertyGroup
from bpy_extras.io_utils import ImportHelper

from .common import (
    LOG_TEXT_NAME,
    MME_BACKEND_PROP,
    MME_BAKED_PROP,
    MME_CORE_FILE,
    MME_DRAW_PROP,
    MME_DRIVEN_PROP,
    MME_GN_MODIFIER,
    MME_GROUP_PROP,
    MME_KEY_PROP,
    MME_PRUNED_PROP,
    MME_SELF_NAMES,
    MME_UNIFORMS_PROP,
    MME_VARYINGS_PROP,
    NINESYNC_ACC_LABEL,
    NINESYNC_CATEGORY,
    NINESYNC_FX_LABEL,
    NINESYNC_NAME,
    REPORT_LOG_LINES,
    REPORT_TEXT_NAME,
    _mme_runtimes,
    _mme_state,
    accessory_collection,
    find_accessory_root,
    fps_differs,
    iter_fcurves,
    log,
    mme_control_source,
    mme_control_use_prop,
    mme_effect_passes,
    mme_load_controls,
    mme_param_meta,
    redraw_all,
    short_text,
)
from .util import (
    is_mmd_root,
    redact_paths,
)
from .pmm import (
    PmmError,
)
from .fx import (
    FxError,
)
from .mme_nodes import (
    MME_WRAP_CHAR,
    MME_WRAP_MARGIN,
    mme_build_standard_groups,
    mme_core_names,
    mme_core_path,
    mme_save_core_library,
)
from .mme import (
    mme_bake_pass,
    mme_clear_bake,
    mme_progress_begin,
    mme_progress_end,
    mme_progress_step,
    mme_rebuild_effect,
    mme_reset_params,
    mme_target_roots,
    mme_update_scene,
)
from .accessories import (
    clean_hook,
    first_armature,
    fx_missing_dependencies,
    fx_mmd_tools_available,
    import_standalone_accessory,
    refresh_accessories,
    report_hook,
)
from .standalone import (
    legacy_group,
    legacy_markers,
    read_standalone_project,
    refresh_links,
    repair_legacy,
    run_standalone_sync,
)


def draw_wrapped(layout, text, width=None):
    if width is None:
        width = 38
        region = getattr(bpy.context, "region", None)
        if region is not None and region.width > 1:
            scale = bpy.context.preferences.system.ui_scale or 1.0
            width = max(16, int((region.width / scale - MME_WRAP_MARGIN) / MME_WRAP_CHAR))
    column = layout.column(align=True)
    column.scale_y = 0.85
    for line in textwrap.wrap(text, width):
        column.label(text=line)


class NineSyncFXAccessory(PropertyGroup):
    key: StringProperty()
    display: StringProperty()
    enabled: BoolProperty(
        name="Sync From PMM",
        description="Update this accessory from the PMM when you press Sync. Turn off to keep it as it is in Blender",
        default=True,
    )
    standalone: BoolProperty(default=False)
    effect_checked: BoolProperty(default=False)
    use_effect: BoolProperty(
        name="Use Effect",
        description="Run this accessory's MME effect (.fx). Turn off to import the plain mesh",
        default=True,
    )
    effect_path: StringProperty(
        name="Effect File",
        description="Effect (.fx) to use instead of the one found next to the .x or in the PMM's .emm file",
        subtype="FILE_PATH",
    )
    effect_note: StringProperty()
    show_controls: BoolProperty(name="Effect Controls", default=False)
    parent_bone: StringProperty(
        name="Parent Bone",
        description="Overrides the parent bone stored in the PMM. Leave empty to use the PMM value",
    )


class NineSyncFXLink(PropertyGroup):
    key: StringProperty()
    pmm_name: StringProperty()
    enabled: BoolProperty(name="Enabled", default=True)
    target: PointerProperty(name="Target", type=bpy.types.Object, poll=is_mmd_root)


class NineSyncFXSettings(PropertyGroup):
    filepath: StringProperty(name="PMM File", subtype="FILE_PATH")
    status: StringProperty(default="Idle")
    auto_scale: BoolProperty(
        name="Auto Scale From Model",
        description="Use the scale the first enabled model was imported with in mmd_tools",
        default=True,
    )
    scale: FloatProperty(
        name="Scale",
        description="Motion scale passed to the mmd_tools VMD importer when Auto Scale is off",
        default=0.08,
        min=0.001,
    )
    sync_accessories: BoolProperty(
        name="Sync Accessories",
        description="Import and animate .x accessories stored in the PMM. Static meshes only",
        default=True,
    )
    sphere_maps: BoolProperty(
        name="Sphere Maps (Experimental)",
        description="Rebuild .sph/.spa sphere maps from accessory textures with a camera-space normal lookup",
        default=True,
    )
    accessory_collection: PointerProperty(type=bpy.types.Collection, options={"HIDDEN"})
    accessory_dir: StringProperty(
        name="Accessory Folder",
        description="Extra folder searched for .x files when the path stored in the PMM does not exist",
        subtype="DIR_PATH",
    )
    accessories: CollectionProperty(type=NineSyncFXAccessory)
    mme_effects: BoolProperty(
        name="MME Effects (.fx)",
        description="Run the MikuMikuEffect file of each accessory: the same-name .fx next to the .x, or the one assigned in the PMM's .emm file. Vertex shaders run every frame, pixel shaders become material nodes",
        default=True,
    )
    mme_density: FloatProperty(
        name="Effect Density",
        description="Share of each LoopByCount loop to draw. 1 matches MMD. Lower values make heavy effects such as Nuke2 faster to scrub. Geometry Nodes passes update live; Python passes on the next sync or Refresh Effects",
        default=1.0,
        min=0.02,
        max=1.0,
        subtype="FACTOR",
        update=lambda self, context: context.scene.update_tag(),
    )
    mme_tolerance: FloatProperty(
        name="Geometry Tolerance",
        description="How far simplified effect strips may drift from the full mesh, as a share of the effect's size. Rows of strip meshes (such as Thunder's 10,000-segment rings and bolts) that the shader output can rebuild by interpolation are merged. 0 keeps every row. Faces that the shader always makes fully transparent are removed regardless. Applies on the next Rebuild",
        default=0.0005,
        min=0.0,
        max=0.05,
        precision=4,
        step=0.01,
    )
    mme_precull: BoolProperty(
        name="Pre-cull Cache",
        description="Remember which effect vertices can never be drawn (per effect file, .x file and pass) in a cache on disk, so rebuilds only analyse new settings. Only vertices that stayed invisible in every evaluated state are skipped",
        default=True,
    )
    mme_auto_rebuild: BoolProperty(
        name="Auto Rebuild",
        description="Rebuild an effect automatically after you edit a parameter that changes its geometry (wrench icon). Turn off for heavy effects: edits then only mark the effect as out of date until you press Rebuild",
        default=True,
    )
    mme_live: BoolProperty(
        name="Live Effect Preview",
        description="Recompute Python-backend effect passes whenever the frame changes in the viewport. Geometry Nodes passes always update. Renders always recompute",
        default=True,
    )
    mme_backend: EnumProperty(
        name="Effect Backend",
        description="How effect vertex shaders run. Applied on the next sync or Refresh Effects",
        items=[
            ("GEONODES", "Geometry Nodes", "Compile vertex shaders to Geometry Nodes: no Python per frame, renders and bakes natively. Passes that cannot compile fall back to Python"),
            ("PYTHON", "Python", "Run vertex shaders in Python (numpy) from a frame-change handler"),
        ],
        default="GEONODES",
    )
    fx_info_tab: EnumProperty(
        name="Effect Info",
        items=[
            ("LINKS", "Linked Objects", "Objects and morphs the effect reads (CONTROLOBJECT)", "LINKED", 0),
            ("PASSES", "Passes", "The effect's draw passes", "NODETREE", 1),
            ("PASS", "Selected Pass", "Details of the selected pass object", "OBJECT_DATA", 2),
        ],
        default="PASSES",
    )
    mme_core_autosave: BoolProperty(
        name="Grow Library Automatically",
        description="After an effect import builds new shared node groups, add them to mme_core.blend so later imports append them instead of building them again",
        default=True,
    )
    mme_bake_target: EnumProperty(
        name="Bake To",
        items=[
            ("PACKED", "Packed in .blend", "Store baked effect geometry inside the .blend file"),
            ("DISK", "Disk", "Store baked effect geometry in the folder below"),
        ],
        default="PACKED",
    )
    mme_bake_dir: StringProperty(
        name="Bake Folder",
        description="Folder for effect bakes stored on disk",
        default="//mme_bake/",
        subtype="DIR_PATH",
    )
    mme_standard_view: BoolProperty(
        name="MMD Colors (Standard View)",
        description="Switch the scene view transform to Standard when effects are synced so effect colors match MMD",
        default=True,
    )
    retime_fps: BoolProperty(
        name="Retime to Scene FPS",
        description="PMM keyframes are 30 fps. Scale every key frame by scene fps / 30 so motion keeps its real speed in a scene with a different frame rate",
        default=True,
    )
    targets: CollectionProperty(type=NineSyncFXLink)


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
    bl_label = "Clear Pre-cull Cache"
    bl_description = "Delete the remembered never-drawn vertices for every effect. The next rebuild analyses each effect from scratch"

    def execute(self, context):
        folder = os.path.join(tempfile.gettempdir(), "ninesync_cache")
        removed = 0
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                if name.startswith("precull_"):
                    try:
                        os.remove(os.path.join(folder, name))
                        removed += 1
                    except OSError:
                        pass
        self.report({"INFO"}, "Removed %d pre-cull cache file(s)" % removed)
        return {"FINISHED"}


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


def draw_effect_controls(layout, entry):
    collection = accessory_collection(bpy.context.scene)
    root = find_accessory_root(collection, entry.key) if collection else None
    if root is None or not root.get("mme_fx"):
        return
    passes = mme_effect_passes(root)
    box = layout.box()
    head = box.row(align=True)
    head.prop(
        entry,
        "show_controls",
        text="Effect Controls",
        icon="TRIA_DOWN" if entry.show_controls else "TRIA_RIGHT",
        emboss=False,
    )
    geo = sum(1 for p in passes if p.get(MME_BACKEND_PROP) == "GEO")
    baked = sum(1 for p in passes if p.get(MME_BAKED_PROP))
    summary = "%d GN, %d Py" % (geo, len(passes) - geo)
    if baked:
        summary += ", %d baked" % baked
    head.label(text=summary)
    if not entry.show_controls:
        return
    col = box.column(align=True)
    col.prop(root, '["mme_tr"]', text="Tr (keyed from PMM)")
    col.prop(root, '["mme_si"]', text="Si (keyed from PMM)")
    meta = mme_param_meta(root)
    if meta:
        col.separator()
        for name, info in sorted(meta.items(), key=lambda item: item[1].get("order", 0)):
            if info.get("prop") in root:
                col.prop(root, '["%s"]' % info["prop"], text=info.get("label") or name)
    row = box.row(align=True)
    reset = row.operator("ninesync_fx.reset_effect_params", text="Reset", icon="LOOP_BACK")
    reset.root_name = root.name
    rebuild = row.operator("ninesync_fx.refresh_effects", text="Rebuild", icon="FILE_REFRESH")
    rebuild.root_name = root.name
    row = box.row(align=True)
    bake = row.operator("ninesync_fx.bake_effects", text="Bake", icon="RENDER_ANIMATION")
    bake.root_name = root.name
    clear = row.operator("ninesync_fx.bake_effects", text="Clear Bake", icon="TRASH")
    clear.root_name = root.name
    clear.clear = True


class NINESYNC_FX_PT_accessories(Panel):
    bl_label = NINESYNC_ACC_LABEL
    bl_idname = "NINESYNC_FX_PT_accessories"
    bl_order = 1
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = NINESYNC_CATEGORY

    def draw(self, context):
        layout = self.layout
        if settings_outdated(context.scene):
            return
        settings = context.scene.ninesync_fx
        draw_standalone_block(layout, context, settings)
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(settings, "sync_accessories")
        col.prop(settings, "sphere_maps")
        col.prop(settings, "accessory_dir")
        armature = first_armature(settings)
        if not len(settings.accessories):
            layout.label(text="Sync a PMM, or import an accessory directly", icon="INFO")
            layout.operator("import_scene.ninesync_fx_accessory", text="Import Accessory (.x)", icon="IMPORT")
            return
        collection = accessory_collection(context.scene)
        active = getattr(context, "active_object", None)
        frame = context.scene.frame_current
        box = layout.box()
        for entry in settings.accessories:
            root = find_accessory_root(collection, entry.key) if collection is not None else None
            row = box.row(align=True)
            if entry.standalone:
                row.label(text="", icon="IMPORT")
            else:
                row.prop(entry, "enabled", text="", icon="LINKED" if entry.enabled else "UNLINKED")
            icon = "SHADERFX" if root is not None and root.get("mme_fx") else "MESH_DATA"
            if root is None:
                sub = row.row(align=True)
                sub.enabled = False
                sub.label(text=entry.display, icon=icon)
            else:
                chosen = active is not None and (active == root or getattr(active, "parent", None) == root)
                pick = row.operator("ninesync_fx.select_object", text=entry.display, icon=icon, depress=chosen)
                pick.name = root.name
                shown = root.ninesync_visible
                row.prop(root, "ninesync_visible", text="", icon="HIDE_OFF" if shown else "HIDE_ON", emboss=False)
                keyed = accessory_visibility_keyed(root, frame)
                key = row.operator(
                    "ninesync_fx.key_accessory_visibility",
                    text="",
                    icon="DECORATE_KEYFRAME" if keyed else ("DECORATE_ANIMATE" if accessory_visibility_animated(root) else "DECORATE"),
                    emboss=False,
                )
                key.root_name = root.name
            if armature is not None and not entry.standalone:
                row.prop_search(entry, "parent_bone", armature.pose, "bones", text="", icon="BONE_DATA")
        layout.operator("import_scene.ninesync_fx_accessory", text="Import Accessory (.x)", icon="IMPORT")


def accessory_visibility_curves(root):
    for child in root.children:
        anim = child.animation_data
        if anim is None or anim.action is None:
            continue
        for fcurve in iter_fcurves(anim.action):
            if fcurve.data_path == "hide_viewport":
                return fcurve
    return None


def accessory_visibility_animated(root):
    return accessory_visibility_curves(root) is not None


def accessory_visibility_keyed(root, frame):
    fcurve = accessory_visibility_curves(root)
    if fcurve is None:
        return False
    return any(abs(p.co[0] - frame) < 0.5 for p in fcurve.keyframe_points)


def fx_effect_root(obj):
    if obj is None:
        return None
    if obj.get("mme_fx"):
        return obj
    if obj.get(MME_DRAW_PROP) and obj.parent is not None and obj.parent.get("mme_fx"):
        return obj.parent
    return None


def fx_entry_for(settings, root):
    key = root.get("mmd_bridge_accessory")
    for entry in settings.accessories:
        if entry.key == key:
            return entry
    return None


def fx_pass_summary(obj):
    try:
        info = json.loads(obj.get(MME_DRAW_PROP, "{}"))
    except (TypeError, ValueError):
        info = {}
    backend = "Geometry Nodes" if obj.get(MME_BACKEND_PROP) == "GEO" else "Python"
    loops = ", ".join("%s x%d" % (var or "loop", count) for var, count in info.get("loops", []))
    flags = []
    if obj.get(MME_BAKED_PROP):
        flags.append("baked")
    if obj.get("mme_handler", True):
        flags.append("frame handler")
    if obj.get(MME_PRUNED_PROP):
        flags.append("pruned")
    return info, backend, loops, flags


def mme_overriding_sources(root, name):
    try:
        gates = json.loads(root.get("mme_overrides", "{}")).get(name, [])
    except (TypeError, ValueError, AttributeError):
        return []
    if not gates:
        return []
    controls = mme_load_controls(root)
    found = []
    for gname in gates:
        desc = controls.get(gname, {})
        if desc.get("kind", "const") == "const":
            continue
        prop = mme_control_use_prop(desc)
        if prop is not None and not bool(root.get(prop, True)):
            continue
        source = mme_control_source(desc)
        if source and source not in found:
            found.append(source)
    return found


def fx_control_text(desc):
    kind = desc.get("kind")
    source = desc.get("from") or ""
    if kind == "const":
        return "missing: %s (uses %s)" % (source or "target", ", ".join("%g" % v for v in desc.get("values", [0.0])))
    if kind == "self":
        return "this accessory: %s" % (desc.get("item") or "transform")
    if kind == "object":
        return "%s: %s" % (desc.get("object", ""), desc.get("item") or "transform")
    if kind == "prop":
        return "morph %s (keyed)" % source
    if kind == "shapekey":
        return "shape key %s on %s (live)" % (desc.get("key", ""), desc.get("object", ""))
    if kind == "bone":
        return "bone %s on %s" % (desc.get("bone", ""), desc.get("object", ""))
    if kind == "model":
        return "model %s" % desc.get("object", "")
    return kind


def draw_fx_selected(layout, context, root, active):
    settings = context.scene.ninesync_fx
    stale = root.name in _mme_state["stale"]
    row = layout.row(align=True)
    row.scale_y = 1.2
    rebuild_cell = row.row(align=True)
    rebuild_cell.alert = stale
    rebuild = rebuild_cell.operator("ninesync_fx.refresh_effects", text="Rebuild (out of date)" if stale else "Rebuild", icon="FILE_REFRESH")
    rebuild.root_name = root.name
    bake = row.operator("ninesync_fx.bake_effects", text="Bake", icon="RENDER_ANIMATION")
    bake.root_name = root.name
    clear = row.operator("ninesync_fx.bake_effects", text="Clear Bake", icon="TRASH")
    clear.root_name = root.name
    clear.clear = True
    col = layout.column(align=True)
    col.label(text="Accessory: %s" % root.get("mme_acc_name", root.name), icon="EMPTY_AXIS")
    col.label(text="Effect: %s" % os.path.basename(root.get("mme_fx", "")), icon="SHADERFX")
    col.label(text="Mesh: %s" % os.path.basename(root.get("mme_x", "")), icon="MESH_DATA")
    header, body = layout.panel("ninesync_fx_keyed", default_closed=False)
    header.label(text="Keyed from the PMM", icon="KEYINGSET")
    if body is not None:
        sub = body.column(align=True)
        sub.prop(root, '["mme_tr"]', text="Tr (opacity)")
        sub.prop(root, '["mme_si"]', text="Si (scale)")
    meta = mme_param_meta(root)
    header, body = layout.panel("ninesync_fx_params", default_closed=False)
    header.label(text="Effect Parameters", icon="PROPERTIES")
    reset = header.operator("ninesync_fx.reset_effect_params", text="", icon="LOOP_BACK", emboss=False)
    reset.root_name = root.name
    if body is not None:
        try:
            structural = set(json.loads(root.get("mme_structural", "[]")))
        except (TypeError, ValueError):
            structural = set()
        if not meta:
            body.label(text="This effect exposes no parameters")
        overriders = set()
        for name, info in sorted(meta.items(), key=lambda item: item[1].get("order", 0)):
            if info.get("prop") not in root:
                continue
            by = mme_overriding_sources(root, name)
            overriders.update(by)
            row = body.row(align=True)
            row.active = not by
            row.prop(root, '["%s"]' % info["prop"], text=info.get("label") or name)
            row.label(text="", icon="LINKED" if by else ("MODIFIER" if name in structural else "BLANK1"))
        if overriders:
            hint = body.row()
            hint.active = False
            hint.label(text="Greyed: set by %s" % ", ".join(sorted(overriders)), icon="LINKED")
        if structural & set(meta):
            draw_wrapped(body, "Wrench parameters change the geometry and can be keyframed: the build covers every keyed value, and editing the keys rebuilds the effect.")
    layout.row().prop(settings, "fx_info_tab", expand=True)
    box = layout.box()
    tab = settings.fx_info_tab
    if tab == "LINKS":
        controls = mme_load_controls(root)
        if not controls:
            box.label(text="This effect reads no other objects")
        groups = {}
        for name, desc in controls.items():
            groups.setdefault(mme_control_source(desc), []).append((name, desc))
        for source, entries in groups.items():
            col = box.column(align=True)
            header = col.row(align=True)
            prop = next((mme_control_use_prop(d) for _n, d in entries if mme_control_use_prop(d)), None)
            if source.lower() in MME_SELF_NAMES:
                header.label(text="This accessory", icon="EMPTY_AXIS")
            elif prop is not None and prop in root:
                header.prop(root, '["%s"]' % prop, text=source, toggle=True)
            else:
                header.label(text=source, icon="ERROR")
            off = prop is not None and not bool(root.get(prop, True))
            for name, desc in entries:
                split = col.split(factor=0.35, align=True)
                missing = desc.get("kind") == "const"
                split.label(text=name, icon="ERROR" if missing else ("HIDE_ON" if off else "LINKED"))
                right = split.row()
                right.alert = missing
                right.enabled = not off
                right.label(text=fx_control_text(desc))
            box.separator(factor=0.5)
    elif tab == "PASSES":
        for obj in mme_effect_passes(root):
            info, backend, loops, flags = fx_pass_summary(obj)
            row = box.row(align=True)
            select = row.operator(
                "ninesync_fx.select_object",
                text="%s / %s" % (info.get("technique", "?"), info.get("pass", "?")),
                icon="RADIOBUT_ON" if obj == active else "RADIOBUT_OFF",
                emboss=False,
            )
            select.name = obj.name
            row.label(text=backend, icon="GEOMETRY_NODES" if backend == "Geometry Nodes" else "SCRIPT")
            detail = ([loops] if loops else []) + flags
            if detail:
                sub = box.row()
                sub.scale_y = 0.75
                sub.separator(factor=3.0)
                sub.label(text=", ".join(detail))
    else:
        if active is None or not active.get(MME_DRAW_PROP):
            box.label(text="Pick a pass in the Passes tab", icon="INFO")
        else:
            info, backend, loops, flags = fx_pass_summary(active)
            try:
                varyings = json.loads(active.get(MME_VARYINGS_PROP, "[]"))
                uniforms = json.loads(active.get(MME_UNIFORMS_PROP, "[]"))
                driven = json.loads(active.get(MME_DRIVEN_PROP, "[]"))
            except (TypeError, ValueError):
                varyings, uniforms, driven = [], [], []
            col = box.column(align=True)
            col.label(text="%s / %s" % (info.get("technique", "?"), info.get("pass", active.name)), icon="OBJECT_DATA")
            col.label(text="Subset %s, %s backend" % (info.get("subset", "?"), backend))
            if loops:
                col.label(text="Loops: %s" % loops)
            if flags:
                col.label(text="State: %s" % ", ".join(flags))
            col.label(text="%d vertex outputs to the material" % len(varyings))
            col.label(text="%d material inputs from the scene (%d by drivers)" % (len(uniforms), len(driven)))
            if active.data is not None and len(active.data.materials) and active.data.materials[0] is not None:
                mat = active.data.materials[0]
                col.label(text=mat.name, icon="MATERIAL")
                group = bpy.data.node_groups.get(mat.get(MME_GROUP_PROP, ""))
                if group is not None:
                    col.label(text=group.name, icon="NODE_MATERIAL")
            modifier = active.modifiers.get(MME_GN_MODIFIER)
            if modifier is not None and modifier.node_group is not None:
                col.label(text=modifier.node_group.name, icon="GEOMETRY_NODES")
    entry = fx_entry_for(settings, root)
    if entry is not None:
        layout.prop(entry, "effect_path", text="Effect File")


class NINESYNC_FX_PT_main(Panel):
    bl_label = NINESYNC_FX_LABEL
    bl_idname = "NINESYNC_FX_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = NINESYNC_CATEGORY
    bl_order = 2

    def draw(self, context):
        layout = self.layout
        if settings_outdated(context.scene):
            draw_wrapped(layout, STALE_SETTINGS_MESSAGE)
            return
        settings = context.scene.ninesync_fx
        layout.prop(settings, "mme_effects")
        if not settings.mme_effects:
            draw_wrapped(layout, "Turn this on and sync to import accessory effects.")
            return
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(settings, "mme_backend")
        col.prop(settings, "mme_density", slider=True)
        col.prop(settings, "mme_tolerance")
        col.prop(settings, "mme_auto_rebuild")
        precull = col.row(align=True)
        precull.prop(settings, "mme_precull")
        precull.operator("ninesync_fx.clear_precull", text="", icon="TRASH")
        if settings.mme_backend == "PYTHON":
            col.prop(settings, "mme_live")
        col.prop(settings, "mme_standard_view")
        row = layout.row()
        row.scale_y = 1.3
        row.alert = bool(_mme_state["stale"])
        row.operator("ninesync_fx.refresh_effects", text="Rebuild All Effects", icon="SHADERFX")


class NINESYNC_FX_PT_selected(Panel):
    bl_label = "Selected Effect"
    bl_idname = "NINESYNC_FX_PT_selected"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = NINESYNC_CATEGORY
    bl_parent_id = "NINESYNC_FX_PT_main"

    @classmethod
    def poll(cls, context):
        return not settings_outdated(context.scene) and context.scene.ninesync_fx.mme_effects

    def draw(self, context):
        active = context.active_object
        root = fx_effect_root(active)
        if root is None:
            draw_wrapped(self.layout, "Select an effect accessory (MMDAcc_...) or one of its passes to see its settings here.")
            return
        draw_fx_selected(self.layout, context, root, active)


class NINESYNC_FX_PT_effects(Panel):
    bl_label = "Accessory Effects"
    bl_idname = "NINESYNC_FX_PT_effects"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = NINESYNC_CATEGORY
    bl_parent_id = "NINESYNC_FX_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return not settings_outdated(context.scene) and context.scene.ninesync_fx.mme_effects

    def draw(self, context):
        layout = self.layout
        settings = context.scene.ninesync_fx
        if not len(settings.accessories):
            draw_wrapped(layout, "Sync a PMM, or use File > Import > MMD Accessory (.x).")
            return
        collection = accessory_collection(context.scene)
        active = getattr(context, "active_object", None)
        for entry in settings.accessories:
            root = find_accessory_root(collection, entry.key) if collection is not None else None
            row = layout.row(align=True)
            row.prop(entry, "use_effect", text="", icon="SHADERFX")
            name = row.row(align=True)
            name.enabled = entry.use_effect
            if root is not None:
                chosen = active is not None and (active == root or getattr(active, "parent", None) == root)
                pick = name.operator("ninesync_fx.select_object", text=entry.display, depress=chosen)
                pick.name = root.name
            else:
                name.label(text=entry.display)
            path = row.row(align=True)
            path.enabled = entry.use_effect
            path.scale_x = 1.4
            path.prop(entry, "effect_path", text="")
            if entry.use_effect and entry.effect_note.startswith(("Effect failed", "Effect error", "File not found")):
                note = layout.row()
                note.scale_y = 0.8
                note.alert = True
                note.label(text=entry.effect_note, icon="ERROR")


class NINESYNC_FX_PT_bake(Panel):
    bl_label = "Bake"
    bl_idname = "NINESYNC_FX_PT_bake"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = NINESYNC_CATEGORY
    bl_parent_id = "NINESYNC_FX_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return not settings_outdated(context.scene) and context.scene.ninesync_fx.mme_effects

    def draw(self, context):
        layout = self.layout
        settings = context.scene.ninesync_fx
        layout.label(text="Scene frames %d - %d" % (context.scene.frame_start, context.scene.frame_end))
        layout.prop(settings, "mme_bake_target", text="Store")
        if settings.mme_bake_target == "DISK":
            layout.prop(settings, "mme_bake_dir", text="")
        row = layout.row(align=True)
        row.operator("ninesync_fx.bake_effects", text="Bake All", icon="RENDER_ANIMATION")
        clear = row.operator("ninesync_fx.bake_effects", text="Clear All", icon="TRASH")
        clear.clear = True


class NINESYNC_FX_PT_library(Panel):
    bl_label = "Core Node Library"
    bl_idname = "NINESYNC_FX_PT_library"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = NINESYNC_CATEGORY
    bl_parent_id = "NINESYNC_FX_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return not settings_outdated(context.scene) and context.scene.ninesync_fx.mme_effects

    def draw(self, context):
        layout = self.layout
        settings = context.scene.ninesync_fx
        path = mme_core_path()
        names = mme_core_names()
        col = layout.column(align=True)
        col.label(text=MME_CORE_FILE, icon="FILE_BLEND" if os.path.isfile(path) else "ERROR")
        col.label(text="%d shared groups in the library" % len(names))
        local = [g for g in bpy.data.node_groups if g.get(MME_KEY_PROP)]
        col.label(text="%d shared groups in this file (%d not in the library)" % (len(local), len([g for g in local if g.name not in names])))
        layout.prop(settings, "mme_core_autosave")
        row = layout.row(align=True)
        row.operator("ninesync_fx.save_core_library", text="Save to Library", icon="FILE_TICK")
        row.operator("ninesync_fx.rebuild_core_library", text="Rebuild Standard", icon="FILE_REFRESH")


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


STALE_SETTINGS_MESSAGE = "An older copy of NineSync: FX replaced this add-on's settings. They were re-linked; press the button again, and remove the old copy in Preferences > Add-ons"


_relink = {"pending": False}


def settings_outdated(scene):
    settings = getattr(scene, "ninesync_fx", None)
    return settings is None or not hasattr(settings, "mme_backend")


def relink_settings():
    _relink["pending"] = False
    try:
        scene = bpy.context.scene
    except AttributeError:
        scene = None
    if scene is not None and not settings_outdated(scene):
        return None
    importlib.import_module(__package__).reload_in_place()
    log.warning("settings were registered by an older copy of the add-on; re-linked to this version")
    redraw_all()
    return None


def schedule_relink():
    if _relink["pending"]:
        return
    _relink["pending"] = True
    bpy.app.timers.register(relink_settings, first_interval=0.01)


def refuse_if_outdated(operator, context):
    if not settings_outdated(context.scene):
        return False
    schedule_relink()
    operator.report({"WARNING"}, STALE_SETTINGS_MESSAGE)
    return True


def draw_fx_dependency_notices(layout):
    for label, message, url in fx_missing_dependencies():
        box = layout.box()
        box.alert = True
        box.label(text=message, icon="ERROR")
        box.operator("wm.url_open", text="Get %s" % label, icon="URL").url = url
    if not fx_mmd_tools_available():
        draw_wrapped(layout, "mmd_tools is not enabled: parent bone matching and morph names use plain bone names only.")


def legacy_data_present(scene):
    if scene.get("ninesync_fx_legacy_repaired"):
        return False
    raw = legacy_group(scene)
    return raw is not None and bool(legacy_markers(raw))


def draw_standalone_block(layout, context, settings):
    draw_fx_dependency_notices(layout)
    if legacy_data_present(context.scene):
        box = layout.box()
        draw_wrapped(box, "This file was saved with the old combined NineSync.")
        box.operator("ninesync_fx.repair_legacy", icon="RECOVER_LAST")
    if hooks.core_installed():
        draw_wrapped(layout, "Synced by NineSync: PMM Bridge. Press Sync Now there.")
        return
    box = layout.box()
    box.prop(settings, "filepath", text="")
    row = box.row(align=True)
    row.operator("ninesync_fx.read_pmm", text="Read PMM", icon="FILE_REFRESH")
    row.operator("ninesync_fx.sync_accessories", text="Sync Accessories", icon="IMPORT")
    box.label(text=settings.status, icon="INFO")
    if fps_differs(context.scene):
        box.prop(settings, "retime_fps")
    box.prop(settings, "auto_scale")
    if not settings.auto_scale:
        box.prop(settings, "scale")
    if len(settings.targets):
        header, body = layout.panel("ninesync_fx_links", default_closed=True)
        header.label(text="Models (%d)" % len(settings.targets))
        if body is not None:
            for link in settings.targets:
                row = body.row(align=True)
                row.prop(link, "enabled", text="")
                row.label(text=link.pmm_name)
                row.prop(link, "target", text="")
    tools = layout.row(align=True)
    tools.operator("ninesync_fx.clean", icon="TRASH")
    tools.operator("ninesync_fx.copy_report", icon="COPYDOWN")


class NINESYNC_FX_OT_read_pmm(Operator):
    bl_idname = "ninesync_fx.read_pmm"
    bl_label = "Read PMM"
    bl_description = "Read the PMM's models and accessories and match its models to mmd_tools models in the scene"

    def execute(self, context):
        if refuse_if_outdated(self, context):
            return {"CANCELLED"}
        settings = context.scene.ninesync_fx
        try:
            path, project = read_standalone_project(settings)
        except (OSError, PmmError) as error:
            self.report({"ERROR"}, str(error))
            log.error("scan failed: %s", error)
            return {"CANCELLED"}
        refresh_links(settings, project)
        refresh_accessories(settings, project)
        self.report({"INFO"}, "Found %d model(s), %d accessory(ies)" % (len(project.models), len(project.accessories)))
        redraw_all()
        return {"FINISHED"}


class NINESYNC_FX_OT_sync_accessories(Operator):
    bl_idname = "ninesync_fx.sync_accessories"
    bl_label = "Sync Accessories"
    bl_description = "Import the PMM's accessories with their animation and effects"

    def execute(self, context):
        if refuse_if_outdated(self, context):
            return {"CANCELLED"}
        settings = context.scene.ninesync_fx
        if not os.path.isfile(bpy.path.abspath(settings.filepath)):
            self.report({"ERROR"}, "PMM file not found")
            return {"CANCELLED"}
        if not run_standalone_sync(context.scene):
            self.report({"ERROR"}, settings.status)
            return {"CANCELLED"}
        self.report({"INFO"}, settings.status)
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


class NINESYNC_FX_OT_repair_legacy(Operator):
    bl_idname = "ninesync_fx.repair_legacy"
    bl_label = "Repair Legacy Data"
    bl_description = "Copy effect settings from a file saved with the old combined NineSync into this add-on and re-point the effect drivers. Press Rebuild All Effects afterwards"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if refuse_if_outdated(self, context):
            return {"CANCELLED"}
        copied, repointed = repair_legacy(context.scene)
        context.scene["ninesync_fx_legacy_repaired"] = True
        self.report({"INFO"}, "Restored %d setting(s), re-pointed %d driver(s)" % (copied, repointed))
        redraw_all()
        return {"FINISHED"}


class NINESYNC_FX_PT_legacy(Panel):
    bl_label = NINESYNC_FX_LABEL
    bl_idname = "NINESYNC_FX_PT_legacy"
    bl_order = 2
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = NINESYNC_CATEGORY

    def draw(self, context):
        box = self.layout.box()
        box.alert = True
        box.label(text="Old combined NineSync is enabled", icon="ERROR")
        draw_wrapped(box, "Disable the old combined NineSync in Preferences > Add-ons, then restart Blender.")


CLASSES = (
    NineSyncFXLink,
    NineSyncFXAccessory,
    NineSyncFXSettings,
    NINESYNC_FX_OT_refresh_effects,
    NINESYNC_FX_OT_key_accessory_visibility,
    NINESYNC_FX_OT_import_accessory,
    NINESYNC_FX_OT_clear_precull,
    NINESYNC_FX_OT_bake_effects,
    NINESYNC_FX_OT_reset_params,
    NINESYNC_FX_OT_select_object,
    NINESYNC_FX_OT_save_core_library,
    NINESYNC_FX_OT_rebuild_core_library,
    NINESYNC_FX_OT_read_pmm,
    NINESYNC_FX_OT_sync_accessories,
    NINESYNC_FX_OT_clean,
    NINESYNC_FX_OT_copy_report,
    NINESYNC_FX_OT_repair_legacy,
    NINESYNC_FX_PT_accessories,
    NINESYNC_FX_PT_main,
    NINESYNC_FX_PT_selected,
    NINESYNC_FX_PT_effects,
    NINESYNC_FX_PT_bake,
    NINESYNC_FX_PT_library,
)


LEGACY_CLASSES = (NINESYNC_FX_PT_legacy,)
