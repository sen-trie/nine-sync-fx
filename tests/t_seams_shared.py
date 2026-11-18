import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import hashlib, shutil

failed = []
def check(label, ok):
    print("%-72s %s" % (label, "ok" if ok else "FAILED"))
    if not ok:
        failed.append(label)

scene, col, root, cam = setup_scene()
bpy.data.objects.remove(root)
X = FIX + "/Synth/synth.x"
FXF = FIX + "/Synth/synth.fx"
settings = scene.ninesync_fx
settings.mme_backend = "GEONODES"
settings.mme_bake_target = "DISK"
settings.mme_bake_dir = tempfile.mkdtemp(prefix="nsseam_").replace("\\", "/") + "/"
settings.mme_bake_shared = True
scene.frame_start, scene.frame_end = 0, 12

class Acc:
    name = "seam"; index = 0; keys = [Key(0)]; path = X

lc = mb.ensure_collection(scene, mb.LIGHT_COLLECTION)
current_rig = {}
def make_rig(tag, loc):
    rig = bpy.data.objects.new("LightRig" + tag, None); lc.objects.link(rig)
    data = bpy.data.lights.new("sun" + tag, "SUN"); light = bpy.data.objects.new("sun" + tag, data); lc.objects.link(light)
    light.parent = rig; light.location = loc; data.color = (0.9, 0.7, 0.5)
    current_rig["rig"] = rig
    return rig, light
for name, module in list(sys.modules.items()):
    if name.startswith("ninesync_fx") and hasattr(module, "find_rig_root"):
        module.find_rig_root = lambda collection, kind: current_rig.get("rig") if kind == "LIGHT" else None

controls = {"Tr": {"kind": "self", "item": "Tr"}}
def build(label, same=False):
    r = bpy.data.objects.new("root_" + ("x" if same else label), None); r["mmd_bridge_accessory"] = "seam_" + ("x" if same else label); col.objects.link(r)
    kids, *_ = mb.mme_build_effect(scene, col, settings, Acc, r, X, FXF, 1.0, controls)
    for k in kids: k.scale = (mb.ACCESSORY_UNIT,) * 3
    return r, sorted(kids, key=lambda k: k["mme_pass_key"])

def evaluated(obj, frame):
    scene.frame_set(frame)
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    co = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return np.round(co, 4)

def passes(r):
    return {c["mme_pass_key"]: c for c in r.children if c.get("mme_pass_key")}

def bake_all(kids):
    return [mb.mme_bake_pass(bpy.context, k, 0, 12, settings, False) for k in kids]

store = mb.mme_store_folder()
shutil.rmtree(store, ignore_errors=True)

def drop_project(r):
    for o in list(r.children) + [r] + [o for o in bpy.data.objects if o.type == "ARMATURE"]:
        bpy.data.objects.remove(o)
    for a in list(bpy.data.armatures):
        bpy.data.armatures.remove(a)

def arm_project(label, x_end):
    r, kids = build(label, same=True)
    data = bpy.data.armatures.new("arm_x")
    arm = bpy.data.objects.new("arm_x", data)
    scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    bone = data.edit_bones.new("hand"); bone.head = (0, 0, 0); bone.tail = (0, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    pb = arm.pose.bones["hand"]
    for frame, x in ((0, 0.0), (12, x_end)):
        pb.location.x = x
        pb.keyframe_insert("location", index=0, frame=frame)
    con = r.constraints.new("CHILD_OF"); con.target = arm; con.subtarget = "hand"
    scene.frame_set(1)
    bpy.context.view_layer.update()
    return r, kids

r_a, k_a = arm_project("a", 2.0)
check("project A bakes", bake_all(k_a) == ["baked"] * len(k_a))
drop_project(r_a)
r_b, k_b = arm_project("b", 2.0)
check("same effect and same bone motion attaches the stored bake", bake_all(k_b) == ["shared"] * len(k_b))
drop_project(r_b)
r_c, k_c = arm_project("c", 5.0)
check("same effect with different bone motion bakes its own, never attaches", bake_all(k_c) == ["baked"] * len(k_c))
check("each motion has its own store folders", len(os.listdir(store)) == 2 * len(k_c))
drop_project(r_c)

r_d, k_d = build("d")
bake_all(k_d)
ptr = {k["mme_pass_key"]: k.as_pointer() for k in k_d}
check("a no-op rebuild keeps the pass objects and their bakes", (mb.mme_rebuild_effect(scene, r_d), all(passes(r_d)[k].as_pointer() == v and mb.mme_bake_state(passes(r_d)[k]) != "none" for k, v in ptr.items()))[1])
check("and re-baking skips every pass", all(mb.mme_bake_pass(bpy.context, p, 0, 12, settings, False) == "skipped" for p in passes(r_d).values()))
reads = {p["mme_pass_key"]: set(json.loads(p["mme_manifest"])["build"]["inputs"]) for p in k_d}
backend = {p["mme_pass_key"]: p.get("mme_backend") for p in k_d}
dependent = {k for k, names in reads.items() if "param.Spread" in names}
prop = mb.mme_param_meta(r_d)["Spread"]["prop"]
r_d[prop] = float(r_d[prop]) + 1.0
mb.mme_rebuild_effect(scene, r_d)
now = passes(r_d)
rebuilt_keys = {k for k in ptr if now[k].as_pointer() != ptr[k]}
check("a one-parameter change rebuilds only the passes that read it", rebuilt_keys == dependent and bool(dependent) and dependent != set(reads))
again = {k: mb.mme_bake_pass(bpy.context, now[k], 0, 12, settings, False) for k in now}
check("the changed pass is baked again", all(again[k] == "baked" for k in dependent))
check("an unchanged geometry-nodes pass keeps its bake (Python-backend passes depend on the whole root)", all(again[k] == "skipped" for k in reads if k not in dependent and backend[k] == "GEO"))

old_rig, old_light = make_rig("1", (0.3, 0.6, -0.8))
r_l, k_l = build("light")
order = lambda ps: sorted(ps, key=lambda p: p.name.rsplit("_", 1)[-1])
before = [evaluated(p, 3) for p in order(k_l)]
bpy.data.objects.remove(old_light); bpy.data.objects.remove(old_rig)
check("the pass notices the deleted light rig", any(mb.mme_broken_bindings(p) for p in k_l))
new_rig, new_light = make_rig("2", (-0.7, 0.2, -0.5))
mb.mme_rebuild_effect(scene, r_l)
rebuilt = passes(r_l)
check("the rebuild left no broken driver targets", not any(mb.mme_broken_bindings(p) for p in rebuilt.values()))
r_f, k_f = build("fresh", same=False)
fresh_list = [evaluated(p, 3) for p in order(k_f)]
after_list = [evaluated(p, 3) for p in order(rebuilt.values())]
check("evaluated output follows the recreated light, as a fresh build does", len(after_list) == len(fresh_list) and all(a.shape == b.shape and np.allclose(a, b) for a, b in zip(after_list, fresh_list)))
check("and differs from the output under the old light", any(a.shape != b.shape or not np.allclose(a, b) for a, b in zip(before, after_list)))

print("FAILED: %s" % (", ".join(failed) if failed else "none"))
