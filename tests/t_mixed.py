import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib, tempfile
import bpy
import paths
paths.prepare("ninesync_fx")
root = tempfile.mkdtemp(prefix="ninesync_dup_")
os.symlink(paths.locate("ninesync_fx"), os.path.join(root, "ninesync_fx_copy"))
sys.path.insert(0, root)
first = importlib.import_module("ninesync_fx")
second = importlib.import_module("ninesync_fx_copy")
for m in (first, second):
    m.MIN_BLENDER = (5, 2, 0)
failed = []
def check(label, ok):
    print("%-52s %s" % (label, "ok" if ok else "FAILED"))
    if not ok:
        failed.append(label)
first.register()
check("first copy registers", hasattr(bpy.types.Scene, "ninesync_fx"))
settings_type = bpy.types.Scene.bl_rna.properties["ninesync_fx"].fixed_type
try:
    second.register()
    refused = False
except RuntimeError as error:
    refused = "another install" in str(error)
check("second copy refuses with a clear message", refused)
check("first copy's property untouched", bpy.types.Scene.bl_rna.properties["ninesync_fx"].fixed_type == settings_type)
second.unregister()
check("refused copy's unregister removes nothing", hasattr(bpy.types.Scene, "ninesync_fx"))
hooks = importlib.import_module("ninesync_fx.hooks")
check("hook still owned by first copy", "ninesync_fx" in hooks.registry()["post_sync"])
first.unregister()
check("first copy unregisters cleanly", not hasattr(bpy.types.Scene, "ninesync_fx") and not hooks.registry()["post_sync"])
second.register()
check("second copy registers once the first is gone", hasattr(bpy.types.Scene, "ninesync_fx"))
second.unregister()
first.register(); first.unregister(); first.register(); first.unregister()
check("same copy re-registers after unregister", not hasattr(bpy.types.Scene, "ninesync_fx"))
print("FAILED:", ", ".join(failed) if failed else "none")
