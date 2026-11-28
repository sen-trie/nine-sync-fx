"""Run in Blender's GUI with an isolated profile after t_adapter_scene.py."""

import os
from pathlib import Path
import sys
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ["NINESYNC_ADAPTER_OUTPUT"])
sys.path.insert(0, str(ROOT / "tests"))
import paths
paths.prepare("ninesync_fx")

import addon_utils
assert addon_utils.enable("ninesync_fx", default_set=True)
bpy.context.preferences.addons["ninesync_fx"].preferences.experimental_mme_adapters = True
from ninesync_fx.experimental_adapters.compositor import CORE, TOKEN


def current():
    scene = bpy.context.window.scene
    return scene.compositor_effects[-1]


def context_for(area_type):
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == area_type)
    region = next(r for r in area.regions if r.type == "WINDOW")
    return bpy.context.temp_override(window=window, area=area, region=region)


def rebuild():
    with context_for("VIEW_3D"):
        effect = current()
        group = effect.node_group
        inner = next(n for n in group.nodes if n.get(CORE))
        inner.node_tree.nodes.new("NodeFrame").label = "Undo probe"
        assert bpy.ops.ed.undo_push(message="Before core rebuild") == {"FINISHED"}
        assert bpy.ops.ninesync_fx.adapter_action(
            "EXEC_DEFAULT", True, action="REBUILD", index=len(bpy.context.scene.compositor_effects) - 1,
            token=group[TOKEN]) == {"FINISHED"}
        assert not any(n.label == "Undo probe" for n in inner.node_tree.nodes)
    return undo


def has_edit():
    group = current().node_group
    core = next(n.node_tree for n in group.nodes if n.get(CORE))
    return any(n.label == "Undo probe" for n in core.nodes)


def undo():
    with context_for("VIEW_3D"):
        assert bpy.ops.ed.undo() == {"FINISHED"}
        assert has_edit(), [(g.name, g.users) for g in bpy.data.node_groups if g.get(CORE)]
    return redo


def redo():
    with context_for("VIEW_3D"):
        assert bpy.ops.ed.redo() == {"FINISHED"}
        assert not has_edit()
        area = bpy.context.area
        scene = bpy.context.scene
        scene.render.engine = "BLENDER_EEVEE"
        area.spaces.active.region_3d.view_perspective = "CAMERA"
        area.spaces.active.shading.type = "RENDERED"
        area.spaces.active.shading.use_compositor = "ALWAYS"
        area.spaces.active.show_region_ui = True
        current().show_expanded = True
        scene.compositor_effects.active = current()
    with context_for("PROPERTIES"):
        bpy.context.space_data.context = "COMPOSITOR"
    with context_for("VIEW_3D"):
        current().enable_for_preview = False
    return capture_before


def capture_before():
    with context_for("VIEW_3D"):
        assert bpy.ops.screen.screenshot(filepath=str(OUT / "interactive-preview-off.png")) == {"FINISHED"}
        current().enable_for_preview = True
    return capture_view


def capture_view():
    with context_for("VIEW_3D"):
        assert bpy.ops.screen.screenshot(filepath=str(OUT / "interactive-scene-effects.png")) == {"FINISHED"}
    return capture_nodes


def capture_nodes():
    with context_for("VIEW_3D"):
        area = bpy.context.area
        area.type = "NODE_EDITOR"
        area.ui_type = "CompositorNodeTree"
    return screenshot_nodes


def screenshot_nodes():
    with context_for("NODE_EDITOR"):
        space = bpy.context.space_data
        assert space.edit_tree == current().node_group
        assert bpy.ops.node.view_all() == {"FINISHED"}
    return finish


def finish():
    with context_for("NODE_EDITOR"):
        assert bpy.ops.screen.screenshot(filepath=str(OUT / "interactive-editable-nodes.png")) == {"FINISHED"}
    (OUT / "interactive-status.txt").write_text("PASS: GUI rebuild, undo and redo, EEVEE viewport configured, editable compositor tree displayed.\n")
    quit_test()


def quit_test():
    bpy.context.preferences.view.use_save_prompt = False
    bpy.ops.wm.quit_blender()


step = rebuild


def run_step():
    global step
    try:
        step = step()
        return 2.0 if step else None
    except Exception:
        (OUT / "interactive-status.txt").write_text(traceback.format_exc())
        quit_test()
        return None


bpy.app.timers.register(run_step, first_interval=3.0)
