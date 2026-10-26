import sys, os, importlib, logging
which = sys.argv[sys.argv.index("--")+1:]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
paths.prepare_available("ninesync", "ninesync_fx")
FIX = paths.fixtures()
import bpy
bpy.context.preferences.addons
mods = {}
for name in which:
    m = importlib.import_module(name)
    m.bl_info
    mods[name] = m
import bpy
for n, m in mods.items():
    import importlib as il
    common = il.import_module(n + ".common")
    common.MIN_BLENDER = (5, 2, 0)
    m.MIN_BLENDER = (5, 2, 0)
for n, m in mods.items():
    m.register()
    print("registered", n)
print("Scene.mmd_bridge:", hasattr(bpy.types.Scene, "mmd_bridge"), "Scene.ninesync_fx:", hasattr(bpy.types.Scene, "ninesync_fx"))
hooks = importlib.import_module(which[0] + ".hooks")
reg = hooks.registry()
print({k: (list(v) if isinstance(v, dict) else v) for k, v in reg.items()})
for n, m in reversed(list(mods.items())):
    m.unregister()
    print("unregistered", n)
print("after:", hasattr(bpy.types.Scene, "mmd_bridge"), hasattr(bpy.types.Scene, "ninesync_fx"))
