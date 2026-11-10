import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import shutil, types

failed = []


def check(label, ok):
    print("%-64s %s" % (label, "ok" if ok else "FAILED"))
    if not ok:
        failed.append(label)


scene = bpy.context.scene
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
settings = scene.ninesync_fx
work = tempfile.mkdtemp(prefix="ninesync_keys_")
plain = os.path.join(work, "thing.x")
shutil.copy(FIX + "/plain/plainthing.x", plain)
pmm = os.path.join(work, "p.pmm")
open(pmm, "wb").close()


def key(frame, x=0.0, opacity=1.0, parent=(-1, 0), visible=1):
    return mb.AccessoryKey(frame, visible, parent[0], parent[1], (x, 0.0, 0.0), (0.0, 0.0, 0.0), 1.0, 0, opacity)


arm_data = bpy.data.armatures.new("rig")
rig_root = bpy.data.objects.new("model_root", None)
rig = bpy.data.objects.new("rig", arm_data)
scene.collection.objects.link(rig_root)
scene.collection.objects.link(rig)
rig.parent = rig_root
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
for name, head in (("頭", (0.0, 0.0, 10.0)), ("右手首", (5.0, 0.0, 5.0))):
    bone = arm_data.edit_bones.new(name)
    bone.head = head
    bone.tail = (head[0], head[1], head[2] + 1.0)
bpy.ops.object.mode_set(mode="OBJECT")
model = types.SimpleNamespace(index=0, name="m", name_en="", path="", bone_names=["頭", "右手首"])


def sync(keys, unit=1.0, name="thing.x", additive=None):
    acc = types.SimpleNamespace(index=0, name=name, path=plain, draw_order=0, keys=keys)
    if additive is not None:
        acc.additive = additive
    project = types.SimpleNamespace(models=[model], accessories=[acc], accessory_error=None)
    ctx = types.SimpleNamespace(scene=scene, project=project, pmm_path=pmm, targets={mb.model_key(model): rig_root}, unit=unit)
    mb.sync_accessories(ctx)
    col = mb.accessory_collection(scene)
    return mb.find_accessory_root(col, "0:" + name)


def curve(root, path, index=0):
    return next((f for f in mb.iter_fcurves(root.animation_data.action) if f.data_path == path and f.array_index == index), None)


keys = [key(0, 1.0), key(10, 2.0, 0.5)]
root = sync(keys)
loc = curve(root, "location", 0)
check("keys written in bulk, linear", [tuple(p.co) for p in loc.keyframe_points] == [(0.0, 1.0), (10.0, 2.0)] and all(p.interpolation == "LINEAR" for p in loc.keyframe_points))
loc.keyframe_points[0].co[1] = 7.0
loc.update()
root = sync(keys)
check("F6: edit survives a sync with unchanged PMM keys", abs(curve(root, "location", 0).keyframe_points[0].co[1] - 7.0) < 1e-6)
root = sync(keys, unit=2.0)
check("F6: edit survives an import scale change", abs(curve(root, "location", 0).keyframe_points[0].co[1] - 7.0) < 1e-6)
keys = [key(0, 1.0), key(10, 3.0, 0.5)]
root = sync(keys, unit=2.0)
check("F6: changed PMM keys replace the edit", [p.co[1] for p in curve(root, "location", 0).keyframe_points] == [2.0, 6.0])
root = sync(keys, unit=1.0)
check("F6: unedited keys follow an import scale change", [p.co[1] for p in curve(root, "location", 0).keyframe_points] == [1.0, 3.0])
action = root.animation_data.action
root = sync(keys)
check("unchanged sync keeps the same action", root.animation_data.action == action)

child = next(c for c in root.children if c.type == "MESH")
scene.frame_set(10)
check("F5: Tr reaches the mesh as mme_fade", abs(child.get("mme_fade", -1) - 0.5) < 1e-5)
mat = child.data.materials[0]
fade = [n for n in mat.node_tree.nodes if n.type == "ATTRIBUTE" and n.attribute_name == "mme_fade" and n.attribute_type == "OBJECT"]
bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
check("F5: material alpha reads mme_fade", bool(fade) and bsdf.inputs["Alpha"].is_linked)
check("F5: driver is a simple expression", child.animation_data.drivers[0].driver.is_simple_expression)

switch = [key(0, parent=(0, 0)), key(5), key(10, parent=(0, 1))]
root = sync(switch, name="mic.x")
names = sorted(c.name for c in root.constraints)
check("F3: one Child Of per parent", names == ["MMDBridgeAcc_0_0", "MMDBridgeAcc_0_1"])
values = []
for frame in (0, 5, 10):
    scene.frame_set(frame)
    values.append(tuple(round(root.constraints[n].influence, 3) for n in names))
check("F3: influence follows the parent of each key", values == [(1.0, 0.0), (0.0, 0.0), (0.0, 1.0)])
scene.frame_set(10)
at = root.matrix_world.translation
check("F3: at frame 10 the accessory is on the second bone", abs(at.x - 5.0) < 1e-3 and abs(at.z - 5.0) < 1e-3)
single = sync([key(0, parent=(0, 0)), key(10, parent=(0, 0))], name="nekomimi.x")
check("F3: a fixed parent stays one plain Child Of", [c.name for c in single.constraints] == ["MMDBridgeAcc"] and not any("constraints" in f.data_path for f in mb.iter_fcurves(single.animation_data.action)))

add_root = sync([key(0)], name="glow.x", additive=True)
add_mat = next(c for c in add_root.children if c.type == "MESH").data.materials[0]
check("F4: additive accessory gets emission + transparent add", any(n.type == "ADD_SHADER" for n in add_mat.node_tree.nodes) and add_mat.surface_render_method == "BLENDED")
plain_root = sync([key(0)], name="glow.x", additive=False)
plain_mat = next(c for c in plain_root.children if c.type == "MESH").data.materials[0]
check("F4: turning additive off rebuilds a normal material", not any(n.type == "ADD_SHADER" for n in plain_mat.node_tree.nodes))
print("FAILED: %s" % (", ".join(failed) if failed else "none"))
