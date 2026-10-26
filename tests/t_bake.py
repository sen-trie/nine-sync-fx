import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
scene, col, root, cam = setup_scene()
settings = scene.ninesync_fx
controls = {"Tr": {"kind": "self", "item": "Tr"}, "Si": {"kind": "self", "item": "Si"}}
settings.mme_backend = "GEONODES"
geo, *_ = mb.mme_build_effect(scene, col, settings, Acc(), root, XP, FX, 1.0, controls)
root2 = bpy.data.objects.new("MMDAcc_Thunder_py", None); root2["mmd_bridge_accessory"] = "thunder_py"; col.objects.link(root2)
settings.mme_backend = "PYTHON"
py, *_ = mb.mme_build_effect(scene, col, settings, Acc(), root2, XP, FX, 1.0, controls)
for c in geo + py:
    c.scale = (mb.ACCESSORY_UNIT,) * 3
key_tr(root, [(0, 1.0), (60, 0.0)]); key_tr(root2, [(0, 1.0), (60, 0.0)])
scene.frame_start, scene.frame_end = 5, 25
def snap(o):
    dg = bpy.context.evaluated_depsgraph_get(); ev = o.evaluated_get(dg); m = ev.to_mesh()
    co = np.empty(len(m.vertices) * 3, np.float32); m.vertices.foreach_get("co", co)
    a = m.attributes.get("mmeV_TEXCOORD6_0"); va = None
    if a is not None:
        va = np.empty(len(m.vertices), np.float32); a.data.foreach_get("value", va)
    ev.to_mesh_clear(); return co, va
ref = {}
for f in range(4, 27):
    scene.frame_set(f)
    for o in geo + py: ref[(o.name, f)] = snap(o)
t = time.time()
with bpy.context.temp_override():
    r = bpy.ops.ninesync_fx.bake_effects()
print("bake", r, "%.1fs" % (time.time() - t), [(o.name, o.get("mme_baked"), o.get("mme_handler")) for o in geo + py])
calls = {"n": 0}
orig = mb.mme_run_vs
def counting(*a, **k):
    calls["n"] += 1; return orig(*a, **k)
mb.mme_run_vs = counting
for o in geo + py:
    m = o.modifiers["MME"]
    for item in m.node_group.nodes:
        pass
for f in (7, 16, 24):
    scene.frame_set(f)
    for o in geo + py:
        co, va = snap(o); c0, v0 = ref[(o.name, f)]
        same = co.shape == c0.shape and np.allclose(co, c0, atol=1e-4) and (va is None or np.allclose(va, v0, atol=1e-5))
        if not same:
            near = [g for g in range(4, 27) if ref[(o.name, g)][0].shape == co.shape and np.allclose(co, ref[(o.name, g)][0], atol=1e-4)]
            print("  MISMATCH", o.name, f, "baked data equals live frame(s)", near)
print("baked playback matches live evaluation for all passes at 7/16/24; python VS calls during baked playback:", calls["n"])
mb.mme_run_vs = orig
r = bpy.ops.ninesync_fx.bake_effects(clear=True)
print("clear", r, [(o.name, o.get("mme_baked"), o.get("mme_handler")) for o in geo + py])
scene.frame_set(16)
co, va = snap(geo[2]); c0, v0 = ref[(geo[2].name, 16)]
print("after clear live again:", np.allclose(co, c0, atol=1e-4))
