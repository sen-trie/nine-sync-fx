import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import tempfile

scene = bpy.context.scene
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
settings = scene.ninesync_fx
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); scene.collection.objects.link(cam); scene.camera = cam
holder = bpy.data.objects.new("MyPicker", None); scene.collection.objects.link(holder)
me = bpy.data.meshes.new("pickmesh"); me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
mesh = bpy.data.objects.new("MyPicker_mesh", me); scene.collection.objects.link(mesh); mesh.parent = holder
mesh.shape_key_add(name="Basis")
morphs = ("赤", "緑", "青", "加算倍率", "透明度")
for name in morphs[:4]:
    mesh.shape_key_add(name=name)
blocks = me.shape_keys.key_blocks
blocks["赤"].value = 0.75
arm = bpy.data.objects.new("MyRig", bpy.data.armatures.new("rig")); scene.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="EDIT")
bone = arm.data.edit_bones.new("Hand"); bone.head = (0, 0, 0); bone.tail = (0, 0, 1)
bpy.ops.object.mode_set(mode="OBJECT")

assert bpy.ops.import_scene.ninesync_fx_accessory(filepath=XP, scale=0.08) == {"FINISHED"}
entry = settings.accessories[0]
col = bpy.data.collections.get(mb.ACCESSORY_COLLECTION)
root = mb.find_accessory_root(col, entry.key)
controls = mb.mme_load_controls(root)
assert controls["morph_r"]["kind"] == "const" and controls["morph_r"]["item"] == "\u8d64"
assign = bpy.ops.ninesync_fx.assign_control


def refused(**kwargs):
    try:
        return assign(root_name=root.name, **kwargs) == {"CANCELLED"}
    except RuntimeError:
        return True


assert refused(control="morph_r")
assert refused(control="morph_r", object_name="cam")
assert mb.mme_manual_prop("morph_r") not in root
assert assign(root_name=root.name, control="morph_r", object_name=holder.name) == {"FINISHED"}
controls = mb.mme_load_controls(root)
assert controls["morph_r"]["kind"] == "shapekey"
assert controls["morph_a"]["kind"] == "const" and mb.mme_manual_prop("morph_a") not in root
assert mb.mme_effect_passes(root)

holder.name = "RenamedPicker"
mesh.name = "RenamedPicker_mesh"
assert bpy.ops.ninesync_fx.refresh_effects(root_name=root.name) == {"FINISHED"}
controls = mb.mme_load_controls(root)
assert controls["morph_r"]["object"] in (holder.name, mesh.name) and controls["morph_r"]["manual"]
depsgraph = bpy.context.evaluated_depsgraph_get()
assert mb.mme_control_values(controls["morph_r"], root, depsgraph, 1.0, ("f", ())) == [0.75]
assert refused(control="morph_a", object_name=arm.name)
assert assign(root_name=root.name, control="morph_a", object_name=arm.name, bone_name="Hand") == {"FINISHED"}
bone_control = mb.mme_load_controls(root)["morph_a"]
assert (bone_control["kind"], bone_control["object"], bone_control["bone"]) == ("bone", arm.name, "Hand")
assert refused(control="morph_a", object_name=arm.name, bone_name="Nope")

with tempfile.TemporaryDirectory() as folder:
    path = os.path.join(folder, "saved.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    root_name = root.name
    bpy.ops.wm.open_mainfile(filepath=path)
    root = bpy.data.objects[root_name]
    controls = mb.mme_load_controls(root)
    assert (controls["morph_r"]["kind"], controls["morph_a"]["kind"]) == ("shapekey", "bone")

bpy.data.objects.remove(bpy.data.objects["RenamedPicker_mesh"])
bpy.data.objects.remove(bpy.data.objects["RenamedPicker"])
assert bpy.ops.ninesync_fx.refresh_effects(root_name=root.name) == {"FINISHED"}
assert mb.mme_load_controls(root)["morph_r"]["kind"] == "const"
assert assign(root_name=root.name, control="morph_r", clear=True) == {"FINISHED"}
assert mb.mme_manual_prop("morph_r") not in root
assert mb.mme_load_controls(root)["morph_r"]["kind"] == "const"
