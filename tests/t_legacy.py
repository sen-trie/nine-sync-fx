import sys, os, importlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
paths.prepare_available("ninesync_fx")
FIX = paths.fixtures()
import bpy
fxm = importlib.import_module("ninesync_fx"); fxm.MIN_BLENDER = (5, 2, 0)
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
nodes = importlib.import_module("ninesync_fx.nodes.library_store")
calls = []
utils = nodes.bpy.utils
class FakeUtils:
    @staticmethod
    def user_resource(kind, path="", create=False):
        calls.append(kind)
        return os.path.join("user", kind, path)
    @staticmethod
    def extension_path_user(package, path="", create=False):
        calls.append(package)
        return os.path.join("user", "extensions", package)
nodes.bpy = type("B", (), {"utils": FakeUtils, "data": bpy.data})
print("user path (not extension):", nodes.mme_user_path())
nodes.ADDON_PACKAGE = "bl_ext.test_repo.ninesync_fx"
print("user path (extension):", nodes.mme_user_path())
print("bundled path:", os.path.basename(os.path.dirname(nodes.mme_bundled_path())))
