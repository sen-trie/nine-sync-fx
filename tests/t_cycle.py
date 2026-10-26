import os
import sys, importlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
paths.prepare_available("ninesync", "ninesync_fx")
FIX = paths.fixtures()
import bpy
core = importlib.import_module("ninesync"); fxm = importlib.import_module("ninesync_fx")
for m in (core, fxm):
    m.MIN_BLENDER = (5, 2, 0)
hooks = importlib.import_module("ninesync.hooks")
for i in range(5):
    core.register(); fxm.register()
    if i % 2:
        fxm.unregister(); fxm.register()
    core.unregister(); fxm.unregister()
reg = hooks.registry()
print("leftover", {k: (list(v) if isinstance(v, dict) else v) for k, v in reg.items()})
print("handlers", [h.__name__ for h in bpy.app.handlers.frame_change_post], [h.__name__ for h in bpy.app.handlers.load_post], [h.__name__ for h in bpy.app.handlers.depsgraph_update_post])
print("props", hasattr(bpy.types.Scene, "mmd_bridge"), hasattr(bpy.types.Scene, "ninesync_fx"), hasattr(bpy.types.Object, "ninesync_visible"))
