import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import importlib
strips = importlib.import_module("ninesync_fx.runtime.strips")

rows = 1001
angle = np.linspace(0.0, 2.0 * np.pi, rows)
ring = np.stack([np.cos(angle), np.sin(angle), np.zeros(rows)], axis=-1)
pos = np.zeros((1, 2, rows, 2, 3))
pos[0, 0, :, 0] = ring * 2.9
pos[0, 0, :, 1] = ring * 3.0
pos[0, 1] = pos[0, 0] * 1.5
var = np.zeros((1, 2, rows, 2, 0))
params = np.linspace(0.0, 1.0, rows)
none = np.zeros((1, 2, rows, 2), dtype=bool)
print("ring without culling, rows kept:", len(strips.mme_simplify_rows(params, pos, var, none, 0.0005)))
start = none.copy(); start[0, 0, :400] = True
kept = strips.mme_simplify_rows(params, pos, var, start, 0.0005)
print("one lane culled up to row 400, rows kept:", len(kept), "boundary kept:", 399 in kept and 400 in kept)
print("culling must not pin the ring:", len(kept) < 200)
gaps = np.zeros((1, 2, rows, 2), dtype=bool); gaps[0, 1, 100:900] = True
kept = strips.mme_simplify_rows(params, pos, var, gaps, 0.0005)
print("both edges of a culled run kept:", all(k in kept for k in (99, 100, 899, 900)))
all_culled = np.ones((1, 2, rows, 2), dtype=bool)
print("all culled, ends only:", strips.mme_simplify_rows(params, pos, var, all_culled, 0.0005) == [0, rows - 1])

src = os.environ.get("NINESYNC_THUNDER_LV2", os.path.join(paths.REPO, "debug-src"))
if os.path.isfile(os.path.join(src, "Thunder_lv2.fx")):
    limits = {"lv2": 40000, "lv2_2": 40000}
    for which, limit in limits.items():
        fx, xp = "%s/Thunder_%s.fx" % (src, which), "%s/Thunder_%s.x" % (src, which)
        scene, col, root, cam = setup_scene()
        acc = Acc(); acc.name = "Thunder_" + which; acc.path = xp
        children, _u, _t, total = mb.mme_build_effect(scene, col, scene.ninesync_fx, acc, root, xp, fx, 1.0, {"Tr": {"kind": "self", "item": "Tr", "from": "(self) / Tr"}})
        dg = bpy.context.evaluated_depsgraph_get()
        count = 0
        for child in children:
            ev = child.evaluated_get(dg); mesh = ev.to_mesh(); count += len(mesh.vertices); ev.to_mesh_clear()
        print(which, "passes", len(children), "of", total, "evaluated vertices under", limit, ":", count < limit, count)
        mb.remove_accessory_children(root)
else:
    print("Thunder lv2 not found; set NINESYNC_THUNDER_LV2 to run the real effect")
