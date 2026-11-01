import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import json
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

print("import:", bpy.ops.import_scene.ninesync_fx_accessory(filepath=XP, scale=0.08))
entry = settings.accessories[0]
col = bpy.data.collections.get(mb.ACCESSORY_COLLECTION)
root = mb.find_accessory_root(col, entry.key)
controls = mb.mme_load_controls(root)
missing = sorted(k for k, v in controls.items() if v.get("kind") == "const")
print("unresolved before:", missing)
print("item stored:", controls["morph_r"].get("item") == "赤")
assign = bpy.ops.ninesync_fx.assign_control


def attempt(**kw):
    try:
        return assign(root_name=root.name, **kw)
    except RuntimeError as error:
        return "refused: " + str(error).strip().splitlines()[-1][:90]


print("no object:", attempt(control="morph_r"))
print("object without the item:", attempt(control="morph_r", object_name="cam"))
print("nothing stored by refused calls:", mb.mme_manual_prop("morph_r") not in root)
print("assign:", attempt(control="morph_r", object_name=holder.name))
controls = mb.mme_load_controls(root)
print("kinds after:", sorted((k, v["kind"]) for k, v in controls.items() if k.startswith("morph")))
print("透明度 is not on the object, so it stays missing:", controls["morph_a"]["kind"], mb.mme_manual_prop("morph_a") not in root)
dg = bpy.context.evaluated_depsgraph_get()
print("reads the shape key:", mb.mme_control_values(controls["morph_r"], root, dg, 1.0, ("f", ())) == [0.75])
print("passes after rebuild:", len(mb.mme_effect_passes(root)))
holder.name = "RenamedPicker"; mesh.name = "RenamedPicker_mesh"
print("rebuild after rename:", bpy.ops.ninesync_fx.refresh_effects(root_name=root.name))
controls = mb.mme_load_controls(root)
print("follows rename:", controls["morph_r"]["object"], controls["morph_r"]["manual"])
print("reads after rename:", mb.mme_control_values(controls["morph_r"], root, bpy.context.evaluated_depsgraph_get(), 1.0, ("f", ())) == [0.75])
print("bone without bone name on rig is refused:", attempt(control="morph_a", object_name=arm.name))
print("named bone:", attempt(control="morph_a", object_name=arm.name, bone_name="Hand"))
controls = mb.mme_load_controls(root)
print("bone control:", controls["morph_a"]["kind"], controls["morph_a"]["bone"], controls["morph_a"]["object"])
print("bad bone refused:", attempt(control="morph_a", object_name=arm.name, bone_name="Nope"))
path = os.path.join(tempfile.mkdtemp(), "saved.blend")
bpy.ops.wm.save_as_mainfile(filepath=path)
root_name = root.name
bpy.ops.wm.open_mainfile(filepath=path)
root = bpy.data.objects[root_name]
controls = mb.mme_load_controls(root)
print("persists through save and load:", controls["morph_r"]["kind"], controls["morph_a"]["kind"], root[mb.mme_manual_prop("morph_r")].name)
scene = bpy.context.scene
col = bpy.data.collections.get(mb.ACCESSORY_COLLECTION)
project = __import__("types").SimpleNamespace(models=[], accessories=[], accessory_error=None)
program = mb.fx_parse_file(root["mme_fx"])
resolved, _tracks = mb.mme_resolve_controls(program, project, scene.ninesync_fx, None, root)
print("sync path keeps it:", resolved["morph_r"]["kind"], resolved["morph_r"].get("manual"))
bpy.data.objects.remove(bpy.data.objects["RenamedPicker_mesh"])
bpy.data.objects.remove(bpy.data.objects["RenamedPicker"])
print("rebuild after delete:", bpy.ops.ninesync_fx.refresh_effects(root_name=root.name))
controls = mb.mme_load_controls(root)
print("deleted target falls back:", controls["morph_r"]["kind"], "-", controls["morph_r"].get("note"))
print("clear:", attempt(control="morph_r", clear=True))
controls = mb.mme_load_controls(root)
print("cleared:", controls["morph_r"]["kind"], "manual" in controls["morph_r"], mb.mme_manual_prop("morph_r") in root)
print("clearing morph_r also clears its unresolved peers:", controls["morph_g"]["kind"], controls["morph_a"]["kind"])
