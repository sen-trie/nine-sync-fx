import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import shutil, tempfile, types
scene = bpy.context.scene
for o in list(bpy.data.objects): bpy.data.objects.remove(o)
settings = scene.ninesync_fx
hooks = importlib.import_module("ninesync_fx.hooks")
print("pmm operator absent:", not hasattr(bpy.types, "NINESYNC_FX_OT_read_pmm"), not hasattr(bpy.types, "NINESYNC_FX_OT_sync_accessories"))
print("pmm modules absent:", not any(n.endswith((".pmm", ".standalone.ctx", ".standalone.pmm_source")) for n in sys.modules))
d = tempfile.mkdtemp()
shutil.copy(FIX + "/plain/plainthing.x", os.path.join(d, "nekomimi.x"))
settings.accessory_dir = d
def key(frame, x):
    return mb.AccessoryKey(frame, 1, -1, 0, (x, 0.0, 0.0), (0.0, 0.0, 0.0), 1.0, 0, 1.0)
acc = types.SimpleNamespace(index=0, name="nekomimi.x", path="C:/gone/nekomimi.x", draw_order=0, keys=[key(0, 0.0), key(30, 3.0)])
project = types.SimpleNamespace(models=[], accessories=[acc], accessory_error=None)
def run(fps):
    scene.render.fps = fps
    scene.render.fps_base = 1.0
    keys = [key(int(k.frame * fps / 30), k.position[0]) for k in acc.keys]
    acc.keys = keys
    ctx = types.SimpleNamespace(scene=scene, project=project, pmm_path=os.path.join(d, "test.pmm"), targets={}, unit=0.08, retimed=True, core_version="test")
    registered = hooks.registry()["post_sync"]
    print("post_sync hooks:", sorted(registered))
    return sum(fn(ctx) for fn in registered.values())
done = run(30)
col = mb.accessory_collection(scene)
roots = [o for o in col.objects if o.get("mmd_bridge_accessory")] if col else []
print("synced:", done, "roots:", [o.name for o in roots], "filepath mirrored:", os.path.basename(settings.filepath))
bpy.ops.ninesync_fx.clean("EXEC_DEFAULT")
print("entries after clean:", len(settings.accessories))
