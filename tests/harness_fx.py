import importlib.util, sys, os, time, json, logging
import bpy, numpy as np
import paths
paths.prepare("ninesync_fx")
FIX = paths.fixtures()
os.makedirs(FIX + "/out", exist_ok=True)
pkg = importlib.import_module("ninesync_fx")
def package_modules():
    prefix = pkg.__name__ + "."
    return [m for n, m in list(sys.modules.items()) if n.startswith(prefix) and m is not None] + [pkg]




class Proxy:
    def __getattr__(self, name):
        for module in package_modules():
            if hasattr(module, name):
                return getattr(module, name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        hit = False
        for module in package_modules():
            if name in module.__dict__:
                setattr(module, name, value)
                hit = True
        if not hit:
            raise AttributeError(name)


mb = Proxy()
if hasattr(mb, "MIN_BLENDER"):
    mb.MIN_BLENDER = (5, 2, 0)
logging.basicConfig(level=logging.INFO, format="%(message)s")
import shim52; shim52.install(mb)
import tempfile
_library_folder = tempfile.mkdtemp(prefix="ninesync_user_")
mb.mme_user_folder = lambda: _library_folder
mb.register()
bpy.context.scene.cycles.transparent_max_bounces = 128
FX = (FIX + "/Thunder/Thunder_lv1.fx")
XP = (FIX + "/Thunder/Thunder_lv1.x")

class Key:
    def __init__(self, frame, opacity=1.0, scale=1.0):
        self.frame = frame; self.opacity = opacity; self.scale = scale; self.visible = True
class Acc:
    def __init__(self):
        self.name = "Thunder_lv1"; self.keys = [Key(0)]; self.path = XP; self.index = 0

def setup_scene():
    scene = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (3.0, -40.0, 12.0)
    cam.rotation_euler = (1.35, 0.0, 0.08)
    scene.camera = cam
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.frame_start = 0
    scene.frame_end = 60
    col = mb.ensure_collection(scene, mb.ACCESSORY_COLLECTION)
    root = bpy.data.objects.new("MMDAcc_Thunder", None)
    root["mmd_bridge_accessory"] = "thunder"
    col.objects.link(root)
    root.location = (0.5, 0.2, 0.0)
    root.scale = (1.0, 1.0, 1.0)
    return scene, col, root, cam

def key_tr(root, frames_values):
    for f, v in frames_values:
        root["mme_tr"] = v
        root.keyframe_insert('["mme_tr"]', frame=f)
