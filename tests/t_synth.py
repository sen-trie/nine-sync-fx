import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
FX2 = (FIX + "/Synth/synth.fx"); X2 = (FIX + "/Synth/synth.x")
scene, col, root, cam = setup_scene()
bpy.data.objects.remove(root)
lc = mb.ensure_collection(scene, mb.LIGHT_COLLECTION)
rig = bpy.data.objects.new("LightRig", None); lc.objects.link(rig)
ld = bpy.data.lights.new("sun", "SUN"); light = bpy.data.objects.new("sun", ld); lc.objects.link(light); light.parent = rig
light.location = (0.3, 0.6, -0.8); ld.color = (0.9, 0.7, 0.5)
mb.find_rig_root = lambda collection, kind: rig if kind == "LIGHT" else None
settings = scene.ninesync_fx
class A2:
    name = "synth"; path = X2; index = 0; keys = [Key(0)]
controls = {"SelfM": {"kind": "self", "item": ""}, "SelfXYZ": {"kind": "self", "item": "XYZ"}, "SelfRx": {"kind": "self", "item": "Rx"}, "Tr": {"kind": "self", "item": "Tr"}, "Si": {"kind": "self", "item": "Si"}}
roots = {}
for backend in ("GEONODES", "PYTHON"):
    r = bpy.data.objects.new("root_" + backend, None); r["mmd_bridge_accessory"] = "synth_" + backend; col.objects.link(r)
    r.location = (1.0, 2.0, 0.5); r.rotation_euler = (0.3, 0.1, 0.2)
    settings.mme_backend = backend
    kids, *_ = mb.mme_build_effect(scene, col, settings, A2, r, X2, FX2, 1.0, controls)
    for c in kids: c.scale = (mb.ACCESSORY_UNIT,) * 3
    key_tr(r, [(0, 1.0), (40, 0.0)])
    r.keyframe_insert("location", frame=0); r.location = (2.0, 1.0, 1.5); r.keyframe_insert("location", frame=40)
    roots[backend] = kids
    print(backend, [(k.name, k.get("mme_backend"), k.get("mme_handler")) for k in kids])
print("params:", {k: v["label"] for k, v in mb.mme_param_meta(bpy.data.objects["root_GEONODES"]).items()})
def ev_data(obj):
    dg = bpy.context.evaluated_depsgraph_get(); ev = obj.evaluated_get(dg); me = ev.to_mesh(); n = len(me.vertices)
    co = np.empty(n * 3, np.float32); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    M = np.array(ev.matrix_world); world = co @ M[:3, :3].T + M[:3, 3]
    attrs = {}
    for a in me.attributes:
        if a.domain == "POINT" and a.data_type in ("FLOAT", "INT") and not a.name.startswith("."):
            buf = np.empty(n, np.float32 if a.data_type == "FLOAT" else np.int32); a.data.foreach_get("value", buf); attrs[a.name] = buf
    ev.to_mesh_clear(); return world, attrs
cam.data.lens = 35
for f in (5, 17, 33):
    scene.frame_set(f)
    for g, p in zip(roots["GEONODES"], roots["PYTHON"]):
        wg, ag = ev_data(g); wp, ap = ev_data(p)
        ig = ag.get("mme_inst", np.zeros(len(wg), np.int32)); ip = ap.get("mme_inst", np.zeros(len(wp), np.int32))
        kg = {(int(s), int(i)): k for k, (s, i) in enumerate(zip(ag["mme_src"], ig))}
        pairs = [(kg[(int(s), int(i))], k) for k, (s, i) in enumerate(zip(ap["mme_src"], ip)) if (int(s), int(i)) in kg]
        a = np.array([x for x, _ in pairs]); b = np.array([y for _, y in pairs])
        cg, cp = ag["mme_cull"][a] > 0.5, ap["mme_cull"][b] > 0.5
        vis = ~cg & ~cp
        err = np.abs(wg[a][vis] - wp[b][vis]).max() if vis.any() else 0.0
        vary = sorted(n for n in ap if n.startswith("mmeV_"))
        verr = {n: float(np.abs(ag[n][a][vis] - ap[n][b][vis]).max()) for n in vary if n in ag and vis.any()}
        worst = max(verr.items(), key=lambda kv: kv[1]) if verr else None
        print("f%d %-24s n=%d/%d culled g/p=%d/%d mismatch=%d pos_err=%.2e worst_varying=%s" % (f, g.name, len(wg), len(wp), int(cg.sum()), int(cp.sum()), int((cg != cp).sum()), err, worst))
