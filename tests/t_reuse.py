import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
scene, col, root, cam = setup_scene()
settings = scene.ninesync_fx
controls = {"Tr": {"kind": "self", "item": "Tr"}, "Si": {"kind": "self", "item": "Si"}}
def check(tag, r):
    bad = []
    for p in mb.mme_effect_passes(r):
        mat = p.data.materials[0]
        grp = bpy.data.node_groups[mat["mme_group"]]
        outs = [i.name for i in grp.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "OUTPUT"]
        go = next(n for n in grp.nodes if n.bl_idname == "NodeGroupOutput")
        linked = all(s.is_linked for s in go.inputs if s.name in ("Color", "Alpha"))
        gn = next(n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeGroup")
        used = [s.name for s in gn.outputs if s.is_linked]
        if outs != ["Color", "Alpha"] or not linked:
            bad.append((p.name, outs, linked, used))
    print(tag, "OK" if not bad else bad, "| GN groups:", len([g for g in bpy.data.node_groups if g.bl_idname == "GeometryNodeTree"]))
mb.mme_build_effect(scene, col, settings, Acc(), root, XP, FX, 1.0, controls)
check("first build", root)
bpy.ops.ninesync_fx.refresh_effects(root_name=root.name)
check("after rebuild", root)
for c in list(root.children): bpy.data.objects.remove(c)
bpy.data.objects.remove(root)
root = bpy.data.objects.new("MMDAcc_Thunder", None); root["mmd_bridge_accessory"] = "thunder"; col.objects.link(root)
mb.mme_build_effect(scene, col, settings, Acc(), root, XP, FX, 1.0, controls)
check("reimport after manual delete (no purge)", root)
