import sys, os, importlib, logging, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
paths.prepare_available("ninesync", "ninesync_fx")
FIX = paths.fixtures()
import bpy
logging.basicConfig(level=logging.WARNING)
core = importlib.import_module("ninesync")
fxm = importlib.import_module("ninesync_fx")
core.MIN_BLENDER = fxm.MIN_BLENDER = (5, 2, 0)
core.register()
fxm.register()
engine = importlib.import_module("ninesync.sync.engine")
engine.import_scene_elements = lambda scene, project, settings: 0
engine.import_audio = lambda *a: False
scene = bpy.context.scene
for o in list(bpy.data.objects): bpy.data.objects.remove(o)
d = tempfile.mkdtemp()
for ext in ("x", "fx"):
    shutil.copy((FIX + "/Thunder/Thunder_lv1.") + ext, os.path.join(d, "Thunder_lv3." + ext))
shutil.copy(FIX + "/plain/plainthing.x", os.path.join(d, "nekomimi.x"))
shutil.copy(FIX + "/plain/plainthing.x", os.path.join(d, "mic.x"))
scene.ninesync_fx.accessory_dir = d
scene.ninesync_fx.mme_density = 0.1
scene.mmd_bridge.filepath = os.environ.get("NINESYNC_TEST_PMM", FIX + "/meiling.pmm")
print("fx installed per core:", importlib.import_module("ninesync.hooks").fx_installed())
print("sync ok:", engine.run_sync(scene), "|", scene.mmd_bridge.status)
roots = sorted(o.name for o in bpy.data.objects if o.get("mmd_bridge_accessory"))
print("accessory roots:", roots)
print("fx entries:", [(e.display, e.enabled) for e in scene.ninesync_fx.accessories])
print("fx filepath mirrored:", os.path.basename(scene.ninesync_fx.filepath))
print("keyed roots:", [o.name for o in bpy.data.objects if o.get("mmd_bridge_accessory") and o.animation_data and o.animation_data.action])
fxm.unregister()
print("FX disabled, core sync ok:", engine.run_sync(scene))
fxm.register()
core.unregister()
print("core disabled, fx standalone import:", bpy.ops.import_scene.ninesync_fx_accessory(filepath=os.path.join(d, "mic.x")))
