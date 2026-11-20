import bpy
import json
import os
from bpy.app.translations import pgettext_iface as iface_, pgettext_rpt as rpt_

from ...common.constants import (
    MME_DRAW_PROP,
    MME_BACKEND_PROP,
    MME_BAKED_PROP,
    MME_DRIVEN_PROP,
    MME_EDIT_OBJECT_PROP,
    MME_EDIT_PASS_PROP,
    MME_LIVE_OBJECT_PROP,
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
from ...runtime.effect_files import mme_missing_files
from ...runtime.bake_node import mme_managed_bake
from ...runtime.manifest import mme_bake_state
from ..helpers import (
    draw_wrapped,
    fx_pass_summary,
    mme_overriding_sources,
)
from ...nodes.material_post import MME_EMISSION_LABEL, MME_GAMMA_LABEL


# One-line description of a resolved controller for the Controls tab.
def fx_control_text(desc):
    kind = desc.get("kind")
    source = desc.get("from") or ""
    if kind == "const":
        text = iface_("missing: %s (uses %s)") % (source or iface_("target"), ", ".join("%g" % v for v in desc.get("values", [0.0])))
        return "%s, %s" % (text, desc["note"]) if desc.get("note") else text
    if kind == "self":
        return iface_("this accessory: %s") % (desc.get("item") or iface_("transform"))
    if kind == "object":
        return "%s: %s" % (desc.get("object", ""), desc.get("item") or iface_("transform"))
    if kind == "prop":
        return iface_("morph %s (keyed)") % source
    if kind == "shapekey":
        return iface_("shape key %s on %s (live)") % (desc.get("key", ""), desc.get("object", ""))
    if kind == "bone":
        return iface_("bone %s on %s") % (desc.get("bone", ""), desc.get("object", ""))
    if kind == "model":
        return iface_("model %s") % desc.get("object", "")
    return kind


_status_icons = {}


# Coloured dots for the bake status, the same ones Core uses for its model sync status (strip colours: red, yellow, green).
def _status_icon(state):
    if not _status_icons:
        try:
            available = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())
        except Exception:
            available = set()
        for key, number, fallback in (("NONE", "01", "ERROR"), ("PARTIAL", "03", "ERROR"), ("ALL", "04", "CHECKMARK")):
            name = "STRIP_COLOR_" + number
            _status_icons[key] = name if name in available else fallback
    return _status_icons[state]


# A box above the effect's details: red when no pass is baked, green when every pass that can be baked is, yellow in between
# (some passes baked, or a bake kept while its pass was edited).
def draw_bake_status(layout, root):
    total = baked = edited = 0
    for obj in mme_effect_passes(root):
        if mme_managed_bake(obj.modifiers.get(MME_GN_MODIFIER)) is None:
            continue
        total += 1
        state = mme_bake_state(obj)
        if state == "baked" and obj.get(MME_BAKED_PROP):
            baked += 1
        elif state == "stale" and obj.get(MME_BAKED_PROP):
            edited += 1
    if not total:
        return
    box = layout.box()
    if baked == total:
        box.label(text=iface_("Baked: all %d passes") % total, icon=_status_icon("ALL"))
    elif baked + edited == 0:
        box.label(text="Not baked", icon=_status_icon("NONE"))
    else:
        text = iface_("Partly baked: %d of %d passes") % (baked + edited, total)
        if edited:
            text += iface_(", %d edited since") % edited
        box.label(text=text, icon=_status_icon("PARTIAL"))


def draw_fx_selected(layout, context, root, active):
    settings = context.scene.ninesync_fx
    stale = root.name in _mme_state["stale"]
    row = layout.row(align=True)
    row.scale_y = 1.2
    rebuild_cell = row.row(align=True)
    rebuild_cell.alert = stale
    rebuild = rebuild_cell.operator("ninesync_fx.refresh_effects", text=iface_("Rebuild (out of date)") if stale else iface_("Rebuild"), icon="FILE_REFRESH")
    rebuild.root_name = root.name
    bake = row.operator("ninesync_fx.bake_effects", text="Bake Changed", icon="RENDER_ANIMATION")
    bake.root_name = root.name
    clear = row.operator("ninesync_fx.bake_effects", text="Clear Bake", icon="TRASH")
    clear.root_name = root.name
    clear.clear = True
    missing = mme_missing_files(root)
    if missing:
        box = layout.box()
        box.alert = True
        box.label(text="Files not found, rebuild and bake are off", icon="ERROR")
        for what, path in missing:
            draw_wrapped(box, "%s: %s" % (iface_(what), path))
    draw_bake_status(layout, root)
    layout.label(text=iface_("Effect: %s") % os.path.basename(root.get("mme_fx", "")), icon="SHADERFX")
    if getattr(settings, "filepath", ""):
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
        hint.label(text=iface_("Greyed: set by %s") % ", ".join(sorted(overriders)), icon="LINKED")
    pass_header, pass_body = layout.panel("ninesync_fx_pass", default_closed=False)
    is_pass = active is not None and active.get(MME_DRAW_PROP) and active.parent == root
    pass_header.label(text=iface_("Pass Details: %s") % active.name.split("_")[-1] if is_pass else iface_("Pass Details"), icon="MODIFIER")
    if pass_body is not None:
        if is_pass:
            draw_pass_details(pass_body, active)
        else:
            pass_body.label(text="Select one of this effect's passes")
    header, body = layout.panel("ninesync_fx_details", default_closed=False)
    header.label(text="Effect Details", icon="INFO")
    if body is not None:
        draw_fx_details(body, settings, root, active)


# The switches that belong to one pass: Edit Particle Instances (only for a pass made of particle sprites, the only
# kind that can be split into instances) and Enable Geometry Node Editing (viewport only), then its output material.
def draw_pass_details(layout, obj):
    if obj.get(MME_EDIT_PASS_PROP):
        layout.prop(obj, MME_EDIT_OBJECT_PROP, text="Edit Particle Instances")
    if obj.get(MME_BACKEND_PROP) == "GEO":
        live = layout.row()
        live.scale_y = 1.2
        live.prop(obj, MME_LIVE_OBJECT_PROP, toggle=True, text="Enable Geometry Node Editing", icon="NODETREE")
        if getattr(obj, MME_LIVE_OBJECT_PROP, False):
            layout.label(text="Viewport only: renders still use the bake", icon="INFO")
        if mme_bake_state(obj) == "stale":
            layout.label(text="Edits are not baked yet: Bake Changed", icon="ERROR")
    draw_pass_material(layout, obj)


# Gamma and emission strength of a pass's output stage, edited on the node sockets themselves so the material
# stays the single source of truth (a rebuild restores the defaults). The diffuse-ray toggle is the object's own
# visibility flag (Cycles), so the pass can light nearby surfaces when the scene has diffuse bounces.
def draw_pass_material(layout, obj):
    if obj.data is None:
        return
    info, _backend, _loops, _flags = fx_pass_summary(obj)
    mats = [m for m in dict.fromkeys(obj.data.materials) if m is not None and m.node_tree is not None]
    shown = []
    for mat in mats:
        nodes = mat.node_tree.nodes
        gamma = next((n for n in nodes if n.type == "GAMMA" and n.label == MME_GAMMA_LABEL), None)
        emission = next((n for n in nodes if n.type == "EMISSION" and n.label == MME_EMISSION_LABEL), None)
        if gamma is not None or emission is not None:
            shown.append((mat, gamma, emission))
    if not shown:
        return
    col = layout.column(align=True)
    col.label(text="Pass Material", icon="MATERIAL")
    col.separator(factor=0.4)
    for mat, gamma, emission in shown:
        if len(shown) > 1:
            col.label(text=mat.name)
        if gamma is not None:
            col.prop(gamma.inputs["Gamma"], "default_value", text="Gamma")
        if emission is not None:
            col.prop(emission.inputs["Strength"], "default_value", text="Emission")
    col.separator(factor=0.4)
    col.prop(obj, "visible_diffuse", text="Produce Diffuse Rays")


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
            draw_wrapped(box, iface_("Full-screen passes are not supported and were skipped: %s") % ", ".join(unsupported))
        for obj in mme_effect_passes(root):
            info, backend, loops, flags = fx_pass_summary(obj)
            row = box.row(align=True)
            select = row.operator(
                "ninesync_fx.select_object",
                text="%s / %s" % (info.get("technique", "?"), info.get("pass", "?")),
                translate=False,
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
            col.label(text="%s / %s" % (info.get("technique", "?"), info.get("pass", active.name)), icon="OBJECT_DATA", translate=False)
            col.label(text=iface_("Subset %s, %s backend") % (info.get("subset", "?"), backend))
            if loops:
                col.label(text=iface_("Loops: %s") % loops)
            if flags:
                col.label(text=iface_("State: %s") % ", ".join(flags))
            col.label(text=iface_("%d vertex outputs to the material") % len(varyings))
            col.label(text=iface_("%d material inputs from the scene (%d by drivers)") % (len(uniforms), len(driven)))
            if active.data is not None and len(active.data.materials) and active.data.materials[0] is not None:
                mat = active.data.materials[0]
                col.label(text=mat.name, icon="MATERIAL")
                group = bpy.data.node_groups.get(mat.get(MME_GROUP_PROP, ""))
                if group is not None:
                    col.label(text=group.name, icon="NODE_MATERIAL")
            modifier = active.modifiers.get(MME_GN_MODIFIER)
            if modifier is not None and modifier.node_group is not None:
                col.label(text=modifier.node_group.name, icon="GEOMETRY_NODES")
