import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import hashlib
import shutil

failed = []


def check(label, ok):
    print("%-68s %s" % (label, "ok" if ok else "FAILED"))
    if not ok:
        failed.append(label)


scene, col, root, cam = setup_scene()
bpy.data.objects.remove(root)
X = FIX + "/Synth/synth.x"
FXF = FIX + "/Synth/synth.fx"
settings = scene.ninesync_fx
settings.mme_bake_target = "DISK"
settings.mme_bake_dir = tempfile.mkdtemp(prefix="nsown_").replace("\\", "/") + "/"
settings.mme_bake_shared = True
scene.frame_start, scene.frame_end = 0, 20


class Acc:
    name = "store_test"; index = 0; keys = [Key(0)]


def project(label, tr_end):
    acc = Acc()
    acc.path = X
    r = bpy.data.objects.new("root_" + label, None); r["mmd_bridge_accessory"] = "synth_" + label; col.objects.link(r)
    settings.mme_backend = "GEONODES"
    kids, *_ = mb.mme_build_effect(scene, col, settings, acc, r, X, FXF, 1.0, {"Tr": {"kind": "self", "item": "Tr"}})
    r["mme_tr"] = 1.0; r.keyframe_insert('["mme_tr"]', frame=0)
    r["mme_tr"] = tr_end; r.keyframe_insert('["mme_tr"]', frame=20)
    return r, sorted(kids, key=lambda k: k.get("mme_backend"))


def signature(obj, frame):
    scene.frame_set(frame)
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    co = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get("co", co)
    count = len(mesh.vertices)
    ev.to_mesh_clear()
    return count, hashlib.md5(np.round(co, 4).tobytes()).hexdigest()


store = mb.mme_store_folder()
shutil.rmtree(store, ignore_errors=True)
root_a, kids_a = project("a", 0.2)
first = [mb.mme_bake_pass(bpy.context, k, 0, 20, settings, False) for k in kids_a]
check("the first project bakes into the shared store", first == ["baked"] * len(kids_a) and len(os.listdir(store)) == len(kids_a))
seen_a = [signature(k, 10) for k in kids_a]
check("the bake is not written beside the .blend", not any(os.path.isdir(os.path.join(settings.mme_bake_dir, n)) for n in ("ninesync_fx",)))

root_b, kids_b = project("b", 0.2)
second = [mb.mme_bake_pass(bpy.context, k, 0, 20, settings, False) for k in kids_b]
check("a second project with the same effect and animation attaches those bakes", second == ["shared"] * len(kids_b))
check("it sees the same geometry", [signature(k, 10) for k in kids_b] == seen_a)
check("no new store folder was made", len(os.listdir(store)) == len(kids_a))

root_c, kids_c = project("c", 0.6)
third = [mb.mme_bake_pass(bpy.context, k, 0, 20, settings, False) for k in kids_c]
check("different Tr animation never shares a bake", third == ["baked"] * len(kids_c) and len(os.listdir(store)) == 2 * len(kids_a))

geo = next(k for k in kids_b if k.get("mme_backend") == "GEO")
folder = mb.mme_pass_bake_folder(geo)
mb.mme_clear_bake(bpy.context, geo)
check("Clear unlinks a shared bake without deleting its files", os.path.isdir(folder) and mb.mme_store_find(os.path.basename(folder), 0, 20) is not None and mb.mme_bake_state(geo) == "none")
check("the pass attaches it again afterwards", mb.mme_bake_pass(bpy.context, geo, 0, 20, settings, False) == "shared")

shutil.rmtree(folder)
valid = mb.mme_bake_decision(geo, root_b, scene, settings, False)[0]
check("a bake removed from the store is noticed", valid is False)
check("and is baked again", mb.mme_bake_pass(bpy.context, geo, 0, 20, settings, False) == "baked")

leftover = os.path.join(store, "_tmp_partial")
os.makedirs(leftover)
check("an unfinished bake folder is never taken for a finished one", mb.mme_store_find("_tmp_partial", 0, 20) is None)
before = len(os.listdir(store))
mb.mme_store_trim(1)
check("trim removes the least recently used bakes to fit the limit", len(os.listdir(store)) < before)
print("FAILED: %s" % (", ".join(failed) if failed else "none"))
