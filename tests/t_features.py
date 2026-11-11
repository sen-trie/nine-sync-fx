import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
scene, col, root, cam = setup_scene()
settings = scene.ninesync_fx
settings.mme_auto_rebuild = True
settings.mme_backend = "GEONODES"
controls = {"Tr": {"kind": "self", "item": "Tr"}, "Si": {"kind": "self", "item": "Si"}}
children, *_ = mb.mme_build_effect(scene, col, settings, Acc(), root, XP, FX, 1.0, controls)
for c in children:
    c.scale = (mb.ACCESSORY_UNIT,) * 3
key_tr(root, [(0, 1.0), (60, 0.0)])
print("params meta:", {k: (v["type"], v["size"], v["default"]) for k, v in mb.mme_param_meta(root).items()})
print("structural:", mb.fx_structural_params(mb.fx_parse_file(FX)))
print("handler needed:", [(c.name, c.get("mme_handler"), json.loads(c.get("mme_driven"))) for c in children])
calls = {"n": 0}
orig = mb.mme_update_pass
def counting(*a, **k):
    calls["n"] += 1
    return orig(*a, **k)
mb.mme_update_pass = counting
ts = []
for f in range(5, 45, 4):
    t = time.time(); scene.frame_set(f); dg = bpy.context.evaluated_depsgraph_get(); ts.append(time.time() - t)
print("GN-only frame_set avg %.1f ms (max %.1f), python VS calls: %d" % (1000 * np.mean(ts), 1000 * max(ts), calls["n"]))
line = [c for c in children if "Line" in c.name][0]
def nverts(o):
    dg = bpy.context.evaluated_depsgraph_get(); ev = o.evaluated_get(dg); m = ev.to_mesh(); n = len(m.vertices); ev.to_mesh_clear(); return n
print("Line verts before:", nverts(line))
prop = mb.mme_param_meta(root)["LineNum"]["prop"]
root[prop] = 5
root.update_tag(); scene.frame_set(scene.frame_current)
print("Line verts after LineNum=5 (live, no rebuild):", nverts(line), "rebuild queued:", sorted(mb._mme_state["rebuild"]))
settings.mme_density = 0.4
bpy.context.view_layer.update()
scene.frame_set(scene.frame_current)
fc = line.animation_data.drivers.find('modifiers["MME"].properties.inputs.%s.value' % [d for d in line.animation_data.drivers if True][0].data_path.split(".")[-2]) if line.animation_data else None
for d in line.animation_data.drivers:
    print("  driver", d.data_path, d.driver.is_valid, d.driver.expression, [(v.targets[0].id, v.targets[0].data_path) for v in d.driver.variables])
print("Line verts at density 0.4:", nverts(line))
settings.mme_density = 1.0
mc = mb.mme_param_meta(root)["MainColor"]["prop"]
mat = line.data.materials[0]
print("material:", mat.name, "group:", mat.get("mme_group"), "top nodes:", [n.bl_idname.replace("ShaderNode", "") for n in mat.node_tree.nodes][:14])
grp = bpy.data.node_groups[mat["mme_group"]]
print("PS group inputs:", [i.name for i in grp.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"])
print("PS frames:", [n.label for n in grp.nodes if n.bl_idname == "NodeFrame"][:12])
root[mc] = [1.0, 0.2, 0.1]
root.update_tag()
bpy.context.view_layer.update()
uprops = [k for k in line.keys() if k.startswith("mmeU_MainColor")]
print("pass MainColor uniforms via drivers:", [(k, round(line.evaluated_get(bpy.context.evaluated_depsgraph_get()).get(k), 3)) for k in uprops])
part = [c for c in children if "Paritcle" in c.name][0]
print("Particle verts:", nverts(part), "pruned:", part.get("mme_pruned"))
pn = mb.mme_param_meta(root)["ParticleNum"]["prop"]
root[pn] = 200
root.update_tag()
bpy.context.view_layer.update()
print("rebuild queued after ParticleNum=200:", sorted(mb._mme_state["rebuild"]))
mb.mme_run_rebuilds()
part = [c for c in root.children if "Paritcle" in c.name][0]
print("Particle verts after rebuild:", nverts(part), "line verts:", nverts([c for c in root.children if "Line" in c.name][0]))
bpy.ops.wm.save_as_mainfile(filepath=(FIX + "/out/features.blend"))
