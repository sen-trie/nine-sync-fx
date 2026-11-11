import sys, os, importlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
paths.prepare_available("ninesync_fx")
FIX = paths.fixtures()
import bpy
fxm = importlib.import_module("ninesync_fx"); fxm.MIN_BLENDER = (5, 2, 0)
fxm.register()
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
