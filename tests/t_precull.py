import sys, os, glob, tempfile; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
for f in glob.glob(os.path.join(tempfile.gettempdir(), "ninesync_cache", "precull_*")): os.remove(f)
scene, col, root, cam = setup_scene()
scene.frame_start, scene.frame_end = 0, 30
settings = scene.ninesync_fx
controls = {"Tr": {"kind": "self", "item": "Tr"}, "Si": {"kind": "self", "item": "Si"}}
mb.mme_sync_params(root, mb.fx_parse_file(FX))
meta = mb.mme_param_meta(root)
def setp(**kw):
    for name, value in kw.items(): root[meta[name]["prop"]] = value
setp(LineNum=32, WaveNum=8, ParticleNum=85, FlashNum=8)
key_tr(root, [(0, 1.0), (30, 0.0)])
def faces():
    return {c.name.split("_")[-1]: len(c.data.polygons) for c in root.children}
def build(tag):
    for c in list(root.children): mb.remove_accessory_children_single(c)
    mb._mme_runtimes.clear()
    t0 = time.time()
    mb.mme_build_effect(scene, col, settings, Acc(), root, XP, FX, 1.0, controls)
    dt = time.time() - t0
    print("%-34s %5.1fs  %s" % (tag, dt, faces()))
    return faces(), dt
cold, t_cold = build("cold (cache empty)")
warm, t_warm = build("rebuild, nothing changed")
setp(LineNum=40)
grown, t_grown = build("LineNum 32 -> 40")
settings.mme_precull = False
ref_grown, t_ref = build("LineNum 40 without cache")
settings.mme_precull = True
setp(WaveNum=10)
w, t_w = build("WaveNum 8 -> 10")
settings.mme_precull = False
ref_w, _ = build("WaveNum 10 without cache")
print("identical to cache-free builds:", grown == ref_grown, w == ref_w, "| warm == cold:", warm == cold)
