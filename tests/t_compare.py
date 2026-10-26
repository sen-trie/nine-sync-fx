import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
scene, col, root, cam = setup_scene()
root2 = bpy.data.objects.new("MMDAcc_Thunder_py", None)
root2["mmd_bridge_accessory"] = "thunder_py"
col.objects.link(root2)
root2.location = root.location
settings = scene.ninesync_fx
controls = {"Tr": {"kind": "self", "item": "Tr"}, "Si": {"kind": "self", "item": "Si"}}
def build(r, backend):
    settings.mme_backend = backend
    t = time.time()
    children, *_ = mb.mme_build_effect(scene, col, settings, Acc(), r, XP, FX, 1.0, controls)
    for c in children:
        c.scale = (mb.ACCESSORY_UNIT,) * 3
    print(backend, "build %.2fs" % (time.time() - t), [(c.name, c.get("mme_backend"), len(c.data.vertices)) for c in children])
    return children
geo = build(root, "GEONODES")
py = build(root2, "PYTHON")
key_tr(root, [(0, 1.0), (60, 0.0)])
key_tr(root2, [(0, 1.0), (60, 0.0)])
def ev_data(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    n = len(me.vertices)
    co = np.empty(n * 3, np.float32); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    M = np.array(ev.matrix_world)
    world = co @ M[:3, :3].T + M[:3, 3]
    attrs = {}
    for a in me.attributes:
        if a.domain == "POINT" and a.data_type in ("FLOAT", "INT") and not a.name.startswith("."):
            buf = np.empty(n, np.float32 if a.data_type == "FLOAT" else np.int32); a.data.foreach_get("value", buf); attrs[a.name] = buf
    ev.to_mesh_clear()
    return world, attrs
for f in (12, 31, 44, 50):
    t = time.time(); scene.frame_set(f); dt = time.time() - t
    trv = root.evaluated_get(bpy.context.evaluated_depsgraph_get()).get("mme_tr"); print("frame", f, "frame_set %.3fs" % dt, "Tr*16 f64=%.9f f32=%.9f" % (trv * 16, float(np.float32(trv) * np.float32(16))))
    for g, p in zip(geo, py):
        wg, ag = ev_data(g); wp, ap = ev_data(p)
        ig = ag.get("mme_inst", np.zeros(len(wg), np.int32)); ip = ap.get("mme_inst", np.zeros(len(wp), np.int32))
        kg = {(int(s), int(i)): k for k, (s, i) in enumerate(zip(ag["mme_src"], ig))}
        pairs = [(kg[(int(s), int(i))], k) for k, (s, i) in enumerate(zip(ap["mme_src"], ip)) if (int(s), int(i)) in kg]
        a = np.array([x for x, _ in pairs]); b = np.array([y for _, y in pairs])
        cg, cp = ag["mme_cull"][a] > 0.5, ap["mme_cull"][b] > 0.5
        vis = ~cg & ~cp
        err = np.abs(wg[a][vis] - wp[b][vis]).max() if vis.any() else 0.0
        scale = np.abs(wp[b][vis]).max() if vis.any() else 1.0
        vary = [n for n in ap if n.startswith("mmeV_")]
        verr = max((np.nanmax(np.abs(ag[n][a][vis] - ap[n][b][vis])) if vis.any() and n in ag else -1) for n in vary) if vary else 0
        print("  %-34s n=%d/%d matched=%d cull_mismatch=%d pos_err=%.2e (scale %.1f) varying_err=%.2e" % (g.name, len(wg), len(wp), len(pairs), int((cg != cp).sum()), err, scale, verr))
