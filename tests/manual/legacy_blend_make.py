import os, sys
sys.path.insert(0, os.environ["NINESYNC_OLD_HARNESS_DIR"])
from harness import *
scene = bpy.context.scene
for o in list(bpy.data.objects): bpy.data.objects.remove(o)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); scene.collection.objects.link(cam); scene.camera = cam
scene.mmd_bridge.mme_density = 0.5
r = bpy.ops.import_scene.ninesync_accessory(filepath=XP, scale=0.08)
col = bpy.data.collections.get(mb.ACCESSORY_COLLECTION)
root = [o for o in col.objects if o.get("mmd_bridge_accessory")][0]
print("monolith passes:", len(mb.mme_effect_passes(root)), "density", scene.mmd_bridge.mme_density, "root", root.name)
drv = [(g.name, d.driver.variables[0].targets[0].data_path) for g in bpy.data.node_groups if g.animation_data for d in g.animation_data.drivers if d.driver.variables]
print("drivers:", sorted(set(p for _n, p in drv))[:5])
bpy.ops.wm.save_as_mainfile(filepath=os.environ.get("NINESYNC_LEGACY_BLEND", "/tmp/legacy_old.blend"))
print("saved")
