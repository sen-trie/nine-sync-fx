import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import shutil, tempfile
scene = bpy.context.scene
for o in list(bpy.data.objects): bpy.data.objects.remove(o)
settings = scene.ninesync_fx
d = tempfile.mkdtemp()
for ext in ("x", "fx"):
    shutil.copy((FIX + "/Thunder/Thunder_lv1.") + ext, os.path.join(d, "Thunder_lv3." + ext))
shutil.copy((FIX + "/plain/plainthing.x"), os.path.join(d, "nekomimi.x"))
shutil.copy((FIX + "/plain/plainthing.x"), os.path.join(d, "mic.x"))
settings.filepath = os.environ.get("NINESYNC_TEST_PMM", FIX + "/meiling.pmm")
settings.accessory_dir = d
settings.mme_density = 0.1
print("core installed:", importlib.import_module("ninesync_fx.hooks").core_installed())
r = bpy.ops.ninesync_fx.read_pmm()
print("read:", r, [e.display for e in settings.accessories], [(l.pmm_name, l.target) for l in settings.targets])
def frames(root):
    act = root.animation_data.action
    for fc in mb.iter_fcurves(act):
        if fc.data_path == "location" and fc.array_index == 0:
            return [int(p.co[0]) for p in fc.keyframe_points]
res = {}
for fps in (30, 60):
    scene.render.fps = fps
    scene.render.fps_base = 1.0
    r = bpy.ops.ninesync_fx.sync_accessories()
    col = mb.accessory_collection(scene)
    roots = sorted((o for o in col.objects if o.get("mmd_bridge_accessory")), key=lambda o: o.name)
    res[fps] = {o.get("mmd_bridge_accessory"): frames(o) for o in roots}
    print("fps", fps, r, settings.status, "roots", [o.name for o in roots])
    bpy.ops.ninesync_fx.clean("EXEC_DEFAULT")
for key in res[30]:
    a, b = res[30][key], res[60][key]
    print(key, "keys@30:", a[:6], "keys@60:", b[:6], "doubled:", b == [int(round(f * 2)) for f in a])
print("entries after clean:", len(settings.accessories))
