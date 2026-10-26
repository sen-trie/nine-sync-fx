import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
a = bpy.context.scene
for o in list(bpy.data.objects): bpy.data.objects.remove(o)
for c in list(bpy.data.collections): bpy.data.collections.remove(c)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); a.collection.objects.link(cam); a.camera = cam
r = bpy.ops.import_scene.ninesync_fx_accessory(filepath=XP, scale=0.08)
b = bpy.data.scenes.new("SceneB")
b.collection.objects.link(cam); b.camera = cam
win = bpy.context.window
with bpy.context.temp_override(scene=b):
    r2 = bpy.ops.import_scene.ninesync_fx_accessory(filepath=XP, scale=0.08)
ca, cb = mb.accessory_collection(a), mb.accessory_collection(b)
print("collections:", ca.name if ca else None, cb.name if cb else None, "distinct:", ca != cb)
print("A objects:", sorted(o.name for o in ca.objects)[:3], len(ca.objects), "| B objects:", len(cb.objects))
print("A links B's collection:", cb.name in [c.name for c in a.collection.children_recursive], "| B links A's:", ca.name in [c.name for c in b.collection.children_recursive])
print("entries A/B:", [e.key for e in a.ninesync_fx.accessories], [e.key for e in b.ninesync_fx.accessories])
print("effect roots A/B:", [r.name for r in mb.mme_effect_roots(a)], [r.name for r in mb.mme_effect_roots(b)])
legacy_scene = bpy.data.scenes.new("Legacy")
shared = bpy.data.collections.new("MMD_Accessories"); legacy_scene.collection.children.link(shared)
print("legacy untagged adopted:", mb.accessory_collection(legacy_scene, True) == shared, shared.get("ninesync_scene"))
