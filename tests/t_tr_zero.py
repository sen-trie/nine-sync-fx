import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import tempfile

failed = []


def check(label, ok):
    print("%-68s %s" % (label, "ok" if ok else "FAILED"))
    if not ok:
        failed.append(label)


scene, col, root, cam = setup_scene()
bpy.data.objects.remove(root)


class Acc:
    name = "tr_test"; index = 0; keys = [Key(0)]


def build(label, x, fx):
    acc = Acc()
    acc.path = x
    r = bpy.data.objects.new(label, None); r["mmd_bridge_accessory"] = label; col.objects.link(r)
    scene.ninesync_fx.mme_backend = "GEONODES"
    kids, *_ = mb.mme_build_effect(scene, col, scene.ninesync_fx, acc, r, x, fx, 1.0, {"Tr": {"kind": "self", "item": "Tr"}})
    return r, kids


def switch_of(obj):
    modifier = obj.modifiers.get(mb.MME_GN_MODIFIER)
    group = modifier.node_group if modifier else None
    return bool(group and any(n.label == "Hide at Tr = 0" for n in group.nodes))


def evaluated(obj, frame=None):
    if frame is not None:
        scene.frame_set(frame)
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    count = len(mesh.vertices)
    ev.to_mesh_clear()
    return count


def settle(*tags):
    for tag in tags:
        tag.update_tag()
    scene.frame_set(scene.frame_current + 1)
    scene.frame_set(scene.frame_current - 1)
    bpy.context.evaluated_depsgraph_get().update()


synth_root, synth_kids = build("tr_synth", FIX + "/Synth/synth.x", FIX + "/Synth/synth.fx")
check("Synth only scales colour by Tr, so no pass gets the Tr = 0 switch", not any(switch_of(k) for k in synth_kids))

if not os.path.isfile(XP) or not os.path.isfile(FX):
    print("SKIP: Thunder fixture not found; the positive half needs it")
    print("FAILED: %s" % (", ".join(failed) if failed else "none"))
    raise SystemExit(0)

root, kids = build("tr_thunder", XP, FX)
geo = [k for k in kids if k.get("mme_backend") == "GEO" and switch_of(k)]
check("a Thunder pass whose alpha follows Tr gets the switch", len(geo) > 0)
if geo:
    target = geo[0]
    counts = {}
    for tr in (1.0, 0.0, 1.0):
        root["mme_tr"] = tr
        settle(root, target)
        counts.setdefault(tr, []).append(evaluated(target))
    check("geometry is empty at Tr = 0 and returns at Tr = 1", counts[0.0] == [0] and counts[1.0][0] > 0 and counts[1.0][0] == counts[1.0][1])
    root["mme_tr"] = 1.0
    root.keyframe_insert('["mme_tr"]', frame=0)
    root["mme_tr"] = 0.0
    root.keyframe_insert('["mme_tr"]', frame=20)
    settings = scene.ninesync_fx
    folder = tempfile.mkdtemp(prefix="nsbake_")
    settings.mme_bake_target = "DISK"
    settings.mme_bake_dir = folder.replace("\\", "/") + "/"
    settings.mme_bake_pass_folders = True
    scene.frame_start, scene.frame_end = 0, 30
    check("the bake finishes", mb.mme_bake_pass(bpy.context, target, 0, 30, settings, True) == "baked")
    found, total, _ = mb.mme_disk_frames(target, 0, 30)
    blobs = os.listdir(os.path.join(mb.mme_pass_bake_folder(target), "blobs"))
    check("every frame has a record, frames at Tr = 0 store no geometry", found == total and len(blobs) < total)
    check("baked frames 25 is empty and frame 5 is not", evaluated(target, 25) == 0 and evaluated(target, 5) > 0)
print("FAILED: %s" % (", ".join(failed) if failed else "none"))
