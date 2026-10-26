import bpy
import json
import textwrap
from .. import hooks

from ..accessories.dependencies import (
    fx_missing_dependencies,
    fx_mmd_tools_available,
)
from ..common.animation import iter_fcurves
from ..common.constants import (
    MME_BACKEND_PROP,
    MME_BAKED_PROP,
    MME_DRAW_PROP,
    MME_PRUNED_PROP,
    MME_WRAP_CHAR,
    MME_WRAP_MARGIN,
)
from ..common.controls import (
    mme_control_source,
    mme_control_use_prop,
    mme_effect_passes,
    mme_load_controls,
    mme_param_meta,
)
from ..common.scene_utils import (
    accessory_collection,
    find_accessory_root,
    fps_differs,
)
from ..standalone.legacy_repair import (
    legacy_group,
    legacy_markers,
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


# The hide_viewport F-curve of the first child that has one; visibility keys are stored on the children.
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


# The effect root for the active object, which may be the root itself or one of its pass objects.
def fx_effect_root(obj):
    if obj is None:
        return None
    if obj.get("mme_fx"):
        return obj
    if obj.get(MME_DRAW_PROP) and obj.parent is not None and obj.parent.get("mme_fx"):
        return obj.parent
    return None


# Settings entry of an accessory root, matched by its accessory key.
def fx_entry_for(settings, root):
    key = root.get("mmd_bridge_accessory")
    for entry in settings.accessories:
        if entry.key == key:
            return entry
    return None


# Text pieces describing a pass for the panel: its draw info, backend, loop counts and flags.
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


# Controllers that currently override a parameter (the effect assigns it under an 'if' on a controller);
# switched-off controllers are not counted.
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


def draw_fx_dependency_notices(layout):
    for label, message, url in fx_missing_dependencies():
        box = layout.box()
        box.alert = True
        box.label(text=message, icon="ERROR")
        box.operator("wm.url_open", text="Get %s" % label, icon="URL").url = url
    if not fx_mmd_tools_available():
        draw_wrapped(layout, "mmd_tools is not enabled: parent bone matching and morph names use plain bone names only.")


# True when the file still has settings from the old combined add-on and the repair has not been done.
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
