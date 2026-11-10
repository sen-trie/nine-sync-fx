import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import time

failed = []


def check(label, ok):
    print("%-60s %s" % (label, "ok" if ok else "FAILED"))
    if not ok:
        failed.append(label)


folder = mb.mme_cache_folder()
check("cache folder sits beside the user library", os.path.dirname(folder) == mb.mme_user_folder())
scene = bpy.context.scene
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); scene.collection.objects.link(cam); scene.camera = cam
root = mb.import_standalone_accessory(bpy.context, FIX + "/Synth/synth.x", 0.08, True, "")
names = os.listdir(folder) if os.path.isdir(folder) else []
check("an effect build writes pre-cull and geometry caches there", any(n.startswith("precull_") for n in names) and any(not n.startswith("precull_") for n in names))
legacy = mb.mme_legacy_cache_folder()
os.makedirs(legacy, exist_ok=True)
stray = os.path.join(legacy, "precull_test_stray.npz")
open(stray, "wb").close()
for index in range(4):
    path = os.path.join(folder, "trim_%d.bin" % index)
    with open(path, "wb") as handle:
        handle.write(b"x" * 1000)
    os.utime(path, (time.time() - 1000 + index, time.time() - 1000 + index))
before = sum(os.path.getsize(os.path.join(folder, n)) for n in os.listdir(folder))
mb.mme_cache_trim(before - 1500)
left = sorted(n for n in os.listdir(folder) if n.startswith("trim_"))
check("trim removes the least recently used files first", left == ["trim_2.bin", "trim_3.bin"])
result = bpy.ops.ninesync_fx.clear_precull()
check("Clear removes every cache file, old temp folder included", result == {"FINISHED"} and not os.listdir(folder) and not os.path.exists(stray))
print("FAILED: %s" % (", ".join(failed) if failed else "none"))
