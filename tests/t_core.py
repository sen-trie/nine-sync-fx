import sys, os, importlib, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
paths.prepare_available("ninesync", "ninesync_fx")
FIX = paths.fixtures()
import bpy
mode = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "core"
core = importlib.import_module("ninesync")
core.MIN_BLENDER = (5, 2, 0)
mods = [core]
if mode == "both":
    fxm = importlib.import_module("ninesync_fx")
    fxm.MIN_BLENDER = (5, 2, 0)
logging.basicConfig(level=logging.WARNING)
core.register()
if mode == "both":
    fxm.register()
bridge = importlib.import_module("ninesync.bridge")
bridge.mmd_tools_available = lambda: True
importlib.import_module("ninesync.ui").mmd_tools_available = lambda: True
bridge.import_scene_elements = lambda scene, project, settings: 0
bridge.import_audio = lambda *a: False
scene = bpy.context.scene
settings = scene.mmd_bridge
settings.filepath = os.environ.get("NINESYNC_TEST_PMM", FIX + "/meiling.pmm")
if mode == "both":
    fxs = scene.ninesync_fx
    import shutil, tempfile
    d = tempfile.mkdtemp()
    for ext in ("x", "fx"):
        shutil.copy((FIX + "/Thunder/Thunder_lv1.") + ext, os.path.join(d, "Thunder_lv3." + ext))
    shutil.copy((FIX + "/plain/plainthing.x"), os.path.join(d, "nekomimi.x"))
    shutil.copy((FIX + "/plain/plainthing.x"), os.path.join(d, "mic.x"))
    fxs.accessory_dir = d
    fxs.mme_density = 0.1
ok = bridge.run_sync(scene)
print("sync ok:", ok, "|", settings.status)
print("models:", [(t.pmm_name, t.target) for t in settings.targets])
print("pmm_accessory_count:", settings.pmm_accessory_count)
print("collections:", sorted(c.name for c in bpy.data.collections))
print("objects:", sorted(o.name for o in bpy.data.objects)[:12])
print("fx installed per core:", importlib.import_module("ninesync.hooks").fx_installed())
rep = bridge.build_report(bpy.context)
print("report lines:", len(rep.splitlines()))
print([l for l in rep.splitlines() if "FX" in l or "accessor" in l.lower()][:6])
ui = importlib.import_module("ninesync.ui")
print("clean options hook installed:", bool(importlib.import_module("ninesync.hooks").registry()["clean"]))
core_src = [m for m in sys.modules if m.startswith("ninesync.") ]
print("core modules loaded:", sorted(m.split(".")[1] for m in core_src))
print("numpy loaded by core modules:", any("numpy" in str(getattr(sys.modules[m], "__dict__", {}).get("np", "")) for m in core_src))

if mode == "both":
    fxs = scene.ninesync_fx
    print("fx filepath mirrored:", fxs.filepath)
    print("fx entries:", [(e.display, e.enabled, e.effect_note) for e in fxs.accessories])
    print("fx links:", [(l.pmm_name, l.target) for l in fxs.targets])
    print("accessory roots:", sorted(o.name for o in bpy.data.objects if o.get("mmd_bridge_accessory")))
    print("effect passes:", sum(len([c for c in o.children if c.get("mme_draw")]) for o in bpy.data.objects if o.get("mmd_bridge_accessory")))
    bpy.ops.ninesync.clean(fx=True, animation=False, pose=False, uv=False, scene=False, audio=False)
    print("after clean roots:", sorted(o.name for o in bpy.data.objects if o.get("mmd_bridge_accessory")))
    rep = bridge.build_report(bpy.context)
    print([l for l in rep.splitlines() if "FX" in l or "effect" in l][:6])
