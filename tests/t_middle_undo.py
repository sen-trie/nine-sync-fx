"""Verify Blender Undo and Redo through the experimental operator entry point."""

from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t_middle_converters import MATERIAL, TOON, add_empty, make_model, set_scene
import ninesync_fx


def run():
    ninesync_fx.register()
    bpy.context.preferences.edit.use_global_undo = True
    scene = set_scene()
    mesh, _rig, original, _sibling = make_model(scene)
    name = mesh.name
    original_name = original[1].name
    add_empty(scene, "MME Control", (0.5, 0, 0))
    light = add_empty(scene, "Direction source", (0, 0, 2))
    assert bpy.ops.ed.undo_push(message="Original model") == {"FINISHED"}
    assert bpy.ops.ninesync_fx.middle_apply(filepath=str(TOON), target=MATERIAL, slot=1,
                                           light=light.name) == {"FINISHED"}
    active_name = bpy.data.objects[name].material_slots[1].material.name
    assert active_name != original_name
    assert bpy.ops.ed.undo_push(message="Model with experimental material") == {"FINISHED"}
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert bpy.data.objects[name].material_slots[1].material.name == original_name
    assert bpy.ops.ed.redo() == {"FINISHED"}
    assert bpy.data.objects[name].material_slots[1].material.name == active_name
    print("PASS U-01 apply operator, undo and redo restore original and FX slots", flush=True)


if __name__ == "__main__":
    run()
