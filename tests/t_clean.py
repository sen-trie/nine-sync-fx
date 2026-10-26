import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
scene, col, root, cam = setup_scene()
settings = scene.ninesync_fx
mb.mme_build_effect(scene, col, settings, Acc(), root, XP, FX, 1.0, {"Tr": {"kind": "self", "item": "Tr"}})
class C: pass
ctx = C(); ctx.scene = scene
print("\n".join(mb.report_hook(bpy.context)).split("effects:")[1].split("\n")[:3])
counts = {"clips": 0, "reset": 0, "uv_keys": 0, "rigs": 0, "accessories": 0, "audio": 0}
mb.clean_accessory_objects(counts)
print("cleaned", counts["accessories"], "left: materials", [m.name for m in bpy.data.materials if m.get("mme_owner")], "groups", [g.name for g in bpy.data.node_groups if g.get("mme_owner")], "meshes", len(bpy.data.meshes))
mb.unregister()
print("unregistered ok, handlers:", [h.__name__ for h in bpy.app.handlers.depsgraph_update_post])
