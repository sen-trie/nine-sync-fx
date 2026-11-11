import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import math
scene, col, root, cam = setup_scene()
settings = scene.ninesync_fx
settings.mme_auto_rebuild = True
controls = {"Tr": {"kind": "self", "item": "Tr"}, "Si": {"kind": "self", "item": "Si"}}
def build(r):
    kids, *_ = mb.mme_build_effect(scene, col, settings, Acc(), r, XP, FX, 1.0, controls)
    for c in kids: c.scale = (mb.ACCESSORY_UNIT,) * 3
    r.scale = (0.15,) * 3
    key_tr(r, [(0, 1.0), (60, 0.0)])
build(root)
cam.location = (0.0, -60.0, 25.0); cam.rotation_euler = (math.radians(68), 0, 0)
scene.view_settings.view_transform = "Standard"
scene.render.engine = "CYCLES"; scene.cycles.samples = 4; scene.cycles.device = "CPU"
scene.render.resolution_x, scene.render.resolution_y = 320, 180
scene.render.film_transparent = True
def lit(tag):
    scene.frame_set(20)
    scene.render.filepath = (FIX + "/out/bug_%s.png") % tag
    bpy.ops.render.render(write_still=True)
    im = bpy.data.images.load(scene.render.filepath); a = np.array(im.pixels[:]).reshape(-1, 4)
    print("%-38s visible pixels: %d" % (tag, int((a[:, 3] > 0.05).sum())))
lit("initial")
meta = mb.mme_param_meta(root)
root[meta["LineNum"]["prop"]] = 5
root[meta["WaveNum"]["prop"]] = 2
root.update_tag(); bpy.context.view_layer.update()
print("rebuild queued:", sorted(mb._mme_state["rebuild"]))
mb.mme_run_rebuilds()
for f in range(0, 40, 3): scene.frame_set(f)
lit("after LineNum=5 WaveNum=2 + scrub")
for c in list(root.children): bpy.data.objects.remove(c)
bpy.data.objects.remove(root)
root = bpy.data.objects.new("MMDAcc_Thunder", None); root["mmd_bridge_accessory"] = "thunder"; col.objects.link(root)
build(root)
lit("reimport without purge")
