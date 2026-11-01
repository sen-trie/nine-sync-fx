import bpy
import json
import os

from ...common.constants import (
    MME_DRAW_PROP,
    MME_DRIVEN_PROP,
    MME_GN_MODIFIER,
    MME_GROUP_PROP,
    MME_SELF_NAMES,
    MME_UNIFORMS_PROP,
    MME_UNSUPPORTED_PROP,
    MME_VARYINGS_PROP,
)
from ...common.controls import (
    mme_control_source,
    mme_control_use_prop,
    mme_effect_passes,
    mme_load_controls,
    mme_param_meta,
)
from ...common.state import _mme_state
from ..helpers import (
    draw_wrapped,
    fx_entry_for,
    fx_pass_summary,
    mme_overriding_sources,
)


# One-line description of a resolved controller for the Controls tab.
def fx_control_text(desc):
    kind = desc.get("kind")
    source = desc.get("from") or ""
    if kind == "const":
        text = "missing: %s (uses %s)" % (source or "target", ", ".join("%g" % v for v in desc.get("values", [0.0])))
        return "%s, %s" % (text, desc["note"]) if desc.get("note") else text
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
    layout.label(text="Keyed from the PMM", icon="KEYINGSET")
    sub = layout.column(align=True)
    sub.prop(root, '["mme_tr"]', text="Tr (opacity)")
    sub.prop(root, '["mme_si"]', text="Si (scale)")
    meta = mme_param_meta(root)
    header = layout.row(align=True)
    header.label(text="Effect Parameters", icon="PROPERTIES")
    reset = header.operator("ninesync_fx.reset_effect_params", text="", icon="LOOP_BACK", emboss=False)
    reset.root_name = root.name
    try:
        structural = set(json.loads(root.get("mme_structural", "[]")))
    except (TypeError, ValueError):
        structural = set()
    if not meta:
        layout.label(text="This effect exposes no parameters")
    overriders = set()
    for name, info in sorted(meta.items(), key=lambda item: item[1].get("order", 0)):
        if info.get("prop") not in root:
            continue
        by = mme_overriding_sources(root, name)
        overriders.update(by)
        row = layout.row(align=True)
        row.active = not by
        row.prop(root, '["%s"]' % info["prop"], text=info.get("label") or name)
        row.label(text="", icon="LINKED" if by else ("MODIFIER" if name in structural else "BLANK1"))
    if overriders:
        hint = layout.row()
        hint.active = False
        hint.label(text="Greyed: set by %s" % ", ".join(sorted(overriders)), icon="LINKED")
    if structural & set(meta):
        draw_wrapped(layout, "Wrench parameters change the geometry and can be keyframed: the build covers every keyed value, and editing the keys rebuilds the effect.")
    header, body = layout.panel("ninesync_fx_details", default_closed=False)
    header.label(text="Effect Details", icon="INFO")
    if body is not None:
        draw_fx_details(body, settings, root, active)
    entry = fx_entry_for(settings, root)
    if entry is not None:
        layout.prop(entry, "effect_path", text="Effect File")


def draw_fx_details(layout, settings, root, active):
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
                if missing or desc.get("manual"):
                    assign = right.operator("ninesync_fx.assign_control", text="", icon="EYEDROPPER")
                    assign.root_name = root.name
                    assign.control = name
                if desc.get("manual"):
                    clear = right.operator("ninesync_fx.assign_control", text="", icon="X")
                    clear.root_name = root.name
                    clear.control = name
                    clear.clear = True
            box.separator(factor=0.5)
    elif tab == "PASSES":
        try:
            unsupported = json.loads(root.get(MME_UNSUPPORTED_PROP, "[]"))
        except (TypeError, ValueError):
            unsupported = []
        if unsupported:
            draw_wrapped(box, "Full-screen passes are not supported and were skipped: %s" % ", ".join(unsupported))
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
