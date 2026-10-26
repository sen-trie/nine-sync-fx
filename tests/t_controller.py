import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import math
scene, col, root, cam = setup_scene()
settings = scene.ninesync_fx
SRC = "Thunder_ColorController.pmx"
controls = {"Tr": {"kind": "self", "item": "Tr", "source": "(self)", "from": "(self) / Tr"},
            "Si": {"kind": "self", "item": "Si", "source": "(self)", "from": "(self) / Si"},
            "bController": {"kind": "model", "object": "", "source": SRC, "from": SRC}}
vals = {"morph_r": 1.0, "morph_g": 0.0, "morph_b": 0.0, "morph_add_si": 0.0, "morph_a": 0.0}
for g, v in vals.items():
    prop = mb.mme_prop_name(mb.MME_CONTROL_PREFIX, g)
    root[prop] = v
    controls[g] = {"kind": "prop", "prop": prop, "source": SRC, "from": "%s / x" % SRC}
kids, *_ = mb.mme_build_effect(scene, col, settings, Acc(), root, XP, FX, 1.0, controls)
for c in kids: c.scale = (mb.ACCESSORY_UNIT,) * 3
root.scale = (0.15,) * 3
key_tr(root, [(0, 1.0), (60, 0.0)])
cam.location = (0.0, -60.0, 25.0); cam.rotation_euler = (math.radians(68), 0, 0)
scene.view_settings.view_transform = "Standard"
scene.render.engine = "CYCLES"; scene.cycles.samples = 4; scene.cycles.device = "CPU"
scene.render.resolution_x, scene.render.resolution_y = 320, 180
scene.render.film_transparent = True
def look(tag, frame=20):
    scene.frame_set(frame)
    scene.render.filepath = (FIX + "/out/ctl_%s.png") % tag
    bpy.ops.render.render(write_still=True)
    im = bpy.data.images.load(scene.render.filepath); a = np.array(im.pixels[:]).reshape(-1, 4)
    v = a[a[:, 3] > 0.05]
    m = v[:, :3].mean(0) if len(v) else np.zeros(3)
    print("%-22s visible %5d  mean rgb %.3f %.3f %.3f" % (tag, len(v), *m))
    return m
a = look("controller red")
USE = mb.mme_use_prop(SRC)
print("use prop:", USE, root.get(USE))
root[USE] = False
b = look("controller off")
root[USE] = True
c = look("controller back on")
print("red when linked:", a[0] > a[2], "MainColor when off:", b[2] > b[0], "restored:", np.allclose(a, c, atol=0.02))
root[USE] = True
root.keyframe_insert('["%s"]' % USE, frame=0)
root[USE] = False
root.keyframe_insert('["%s"]' % USE, frame=21)
k1 = look("keyed on @20", 20)
k2 = look("keyed off @22", 22)
print("keyframed toggle follows keys:", k1[0] > k1[2], k2[2] > k2[0])
meta = mb.mme_param_meta(root)
root[meta["MainColor"]["prop"]] = (2.0, 0.0, 2.0)
d = look("off + magenta", 22)
print("MainColor edit live:", d[0] > 0.5 and d[2] > 0.5 and d[1] < 0.1)
