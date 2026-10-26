import sys, os, importlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
paths.prepare_available("ninesync", "ninesync_fx")
FIX = paths.fixtures()
import bpy
from bpy.props import PointerProperty, EnumProperty
fxm = importlib.import_module("ninesync_fx"); fxm.MIN_BLENDER = (5, 2, 0)
core = importlib.import_module("ninesync"); core.MIN_BLENDER = (5, 2, 0)
fxm.register()
scene = bpy.context.scene
scene["mmd_bridge"] = {"mme_density": 0.4, "mme_backend": 1, "sync_accessories": 0, "mme_precull": 0, "accessories": [{"key": "0:foo.x", "display": "foo.x", "enabled": 0, "use_effect": 1}]}
ng = bpy.data.node_groups.new("legacy_density", "GeometryNodeTree")
node = ng.nodes.new("ShaderNodeMath")
fc = ng.driver_add('nodes["%s"].inputs[0].default_value' % node.name)
var = fc.driver.variables.new(); var.name = "var"; var.type = "SINGLE_PROP"
var.targets[0].id_type = "SCENE"; var.targets[0].id = scene; var.targets[0].data_path = "mmd_bridge.mme_density"
fc.driver.expression = "var"
r = bpy.ops.ninesync_fx.repair_legacy()
s = scene.ninesync_fx
print("repair:", r, "density", s.mme_density, "backend", s.mme_backend, "sync_accessories", s.sync_accessories, "precull", s.mme_precull)
print("entries:", [(e.key, e.display, e.enabled, e.use_effect) for e in s.accessories])
print("driver path:", var.targets[0].data_path)
fxm.unregister()
class Fake(bpy.types.PropertyGroup):
    mme_backend: EnumProperty(items=[("A", "A", ""), ("B", "B", "")])
bpy.utils.register_class(Fake)
bpy.types.Scene.mmd_bridge = PointerProperty(type=Fake)
for m in (core, fxm):
    m.register()
print("legacy panels:", hasattr(bpy.types, "NINESYNC_PT_legacy"), hasattr(bpy.types, "NINESYNC_FX_PT_legacy"))
print("regular panels absent:", not hasattr(bpy.types, "NINESYNC_PT_main"), not hasattr(bpy.types, "NINESYNC_FX_PT_main"), "| ninesync_fx pointer absent:", not hasattr(bpy.types.Scene, "ninesync_fx"))
for m in (fxm, core):
    m.unregister()
del bpy.types.Scene.mmd_bridge
bpy.utils.unregister_class(Fake)
print("legacy panels gone:", not hasattr(bpy.types, "NINESYNC_PT_legacy"), not hasattr(bpy.types, "NINESYNC_FX_PT_legacy"))
for m in (core, fxm):
    m.register()
print("normal again:", hasattr(bpy.types, "NINESYNC_PT_main"), hasattr(bpy.types, "NINESYNC_FX_PT_main"))
nodes = importlib.import_module("ninesync_fx.mme_nodes")
print("core path (not extension):", os.path.basename(nodes.mme_core_path()), os.path.dirname(nodes.mme_core_path()).endswith("ninesync_fx"))
nodes.__package__ = "bl_ext.test_repo.ninesync_fx"
try:
    p = nodes.mme_core_path()
    print("core path (extension):", p, os.path.isfile(p))
except Exception as error:
    print("extension path raised", type(error).__name__, error)
