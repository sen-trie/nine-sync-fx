import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import types

mesh_mod = sys.modules[pkg.__name__ + ".accessories.mesh"]
bundled_name = pkg.__name__ + ".x_importer_bundled"
real_names = mesh_mod.loaded_addon_names
calls = []

def fake_module(name, label):
    module = types.ModuleType(name)
    def import_x(context, filepath, **options):
        calls.append((label, filepath, options))
        return {"FINISHED"}
    module.import_x = import_x
    sys.modules[name] = module
    return module

def drop(*names):
    for name in names:
        sys.modules.pop(name, None)

addon_key = "bl_ext.fake_repo.io_directx_x"
mesh_mod.loaded_addon_names = lambda modules: []
print("neither -> None:", mesh_mod.x_importer_function() is None)

fake_module(addon_key + ".importer", "addon")
sys.modules[addon_key] = types.ModuleType(addon_key)
mesh_mod.loaded_addon_names = lambda modules: [addon_key]
fn = mesh_mod.x_importer_function()
print("addon only -> addon function:", fn is not None and fn.__module__ == addon_key + ".importer")

fake_module(bundled_name, "bundled")
fn = mesh_mod.x_importer_function()
print("bundled + addon -> bundled wins:", fn is not None and fn.__module__ == bundled_name)
print("available:", mesh_mod.x_importer_available())

target = os.path.join(FIX, "Synth", "synth.x")
calls.clear()
mesh_mod.import_x_with_addon(target)
label, path, options = calls[0]
print("call goes to bundled with explicit options:", label == "bundled" and path == target and all(
    k in options for k in ("use_import_collection", "import_animation", "use_apply_transform", "global_scale")))
print("registers no operator:", not hasattr(bpy.ops.import_scene, "directx_x"))

drop(bundled_name)
calls.clear()
mesh_mod.import_x_with_addon(target)
print("bundled gone -> addon used, called once:", [c[0] for c in calls] == ["addon"])

def boom(context, filepath, **options):
    bpy.data.objects.new("half", None)
    bpy.data.collections.new("half_col")
    raise ValueError("bad file")
sys.modules[addon_key + ".importer"].import_x = boom
objects_before = len(bpy.data.objects); collections_before = len(bpy.data.collections)
try:
    mesh_mod.import_x_with_addon(target)
    print("importer error reported as RuntimeError: False")
except RuntimeError as error:
    print("importer error reported as RuntimeError:", "bad file" in str(error))
print("partial import cleaned:", len(bpy.data.objects) == objects_before and len(bpy.data.collections) == collections_before)

drop(addon_key, addon_key + ".importer")
mesh_mod.loaded_addon_names = real_names

real = mesh_mod.x_importer_function()
binary = os.environ.get("NINESYNC_X_BINARY") or target
if real is None:
    print("SKIP real importer comparison: no DirectX X importer add-on enabled in this profile")
else:
    operator = mesh_mod.x_importer_operator()
    if operator is None:
        print("SKIP operator comparison: importer add-on registers no operator")
    else:
        def snapshot(objs):
            return len(objs), sorted(len(o.data.materials) for o in objs if o.type == "MESH"), sorted(len(o.data.vertices) for o in objs if o.type == "MESH")
        by_function = mesh_mod.import_x_with_addon(binary)
        function_result = snapshot(by_function)
        for o in by_function:
            bpy.data.objects.remove(o)
        mesh_mod.x_importer_function = lambda: None
        by_operator = mesh_mod.import_x_with_addon(binary)
        operator_result = snapshot(by_operator)
        print("function vs operator (objects, materials, vertices):", function_result, operator_result, function_result == operator_result)
print("no FX-registered x operator:", not any(hasattr(bpy.ops.import_scene, n) for n in ("x", "directx", "x_file", "dx")) or real is not None)
