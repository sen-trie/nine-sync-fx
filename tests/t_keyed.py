import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import math
scene, col, root, cam = setup_scene()
settings = scene.ninesync_fx
controls = {"Tr": {"kind": "self", "item": "Tr"}, "Si": {"kind": "self", "item": "Si"}}
meta_probe = mb.fx_parse_file(FX)
mb.mme_sync_params(root, meta_probe)
meta = mb.mme_param_meta(root)
PN = meta["ParticleNum"]["prop"]
root[PN] = 16
root.keyframe_insert('["%s"]' % PN, frame=0)
root[PN] = 64
root.keyframe_insert('["%s"]' % PN, frame=30)
draws = []
orig_draw = mb.mme_progress_draw
def spy(force=False):
    p = mb._mme_state.get("progress")
    if p: draws.append((p["index"], round(p["within"], 2), p["detail"]))
    return orig_draw(force)
mb.mme_progress_draw = spy
scene.frame_set(0)
kids, *_ = mb.mme_build_effect(scene, col, settings, Acc(), root, XP, FX, 1.0, controls)
print("progress updates during build:", len(draws), "sample:", draws[:2], draws[-1:], "ended:", mb._mme_state["progress"] is None)
for c in kids: c.scale = (mb.ACCESSORY_UNIT,) * 3
mb.mme_link_visibility(root)
key_tr(root, [(0, 1.0), (60, 0.0)])
particle = [c for c in root.children if "Paritcle" in c.name][0]
print("particle source faces (built while frame 0 has ParticleNum=16):", len(particle.data.polygons))
dg = bpy.context.evaluated_depsgraph_get()
def faces(obj, f):
    scene.frame_set(f)
    e = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()); m = e.to_mesh(); n = len(m.polygons); e.to_mesh_clear(); return n
print("evaluated particle faces f0/f15/f30:", faces(particle, 0), faces(particle, 15), faces(particle, 30))
needed = []
for f in range(0, 31, 5):
    scene.frame_set(f)
    needed.append(mb.mme_rebuild_needed(root))
print("rebuild needed while scrubbing keyed ParticleNum:", any(needed))
kp = [fc for fc in mb.iter_fcurves(root.animation_data.action) if fc.data_path == '["%s"]' % PN][0]
kp.keyframe_points[1].co[1] = 128
print("rebuild needed after editing a key:", mb.mme_rebuild_needed(root))
settings.mme_auto_rebuild = False
mb._mme_state["rebuild"].clear(); mb._mme_state["stale"].clear()
root.update_tag(); bpy.context.view_layer.update()
mb.mme_param_watch(scene, bpy.context.evaluated_depsgraph_get())
print("deferred: queued", sorted(mb._mme_state["rebuild"]), "stale", sorted(mb._mme_state["stale"]))
mb.mme_rebuild_effect(scene, root)
print("after manual rebuild stale:", sorted(mb._mme_state["stale"]), "particle faces now:", len([c for c in root.children if "Paritcle" in c.name][0].data.polygons))
line = [c for c in root.children if "Line" in c.name][0]
scene.frame_set(0)
bpy.ops.ninesync_fx.key_accessory_visibility(root_name=root.name)
scene.frame_set(25)
root.ninesync_visible = False
bpy.ops.ninesync_fx.key_accessory_visibility(root_name=root.name)
scene.frame_set(20)
a = (line.hide_viewport, line.hide_render)
scene.frame_set(26)
b = (line.hide_viewport, line.hide_render)
print("keyed visibility: hidden f20", a, "f26", b, "eye reads", root.ninesync_visible)
MC = meta["MainColor"]["prop"]
root[MC] = (1.0, 0.0, 0.0)
root.keyframe_insert('["%s"]' % MC, frame=10)
acc = Acc()
acc.name = "Thunder_lv1.x"
acc.keys = [mb.AccessoryKey(0, 1, -1, 0, (0, 0, 0), (0, 0, 0), 1.0, 0, 1.0), mb.AccessoryKey(40, 1, -1, 0, (0, 5, 0), (0, 0, 0), 1.0, 0, 0.5)]
mb.key_accessory(root, acc, 1.0)
paths = sorted({fc.data_path for fc in mb.iter_fcurves(root.animation_data.action)})
print("after PMM re-key, root curves:", paths)
print("user keys kept: MainColor", '["%s"]' % MC in paths, "ParticleNum", '["%s"]' % PN in paths)
