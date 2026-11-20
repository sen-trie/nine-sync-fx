import os, sys, tempfile; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
FX2 = (FIX + "/Synth/synth.fx"); X2 = (FIX + "/Synth/synth.x")
scene, col, root, cam = setup_scene()
bpy.data.objects.remove(root)
settings = scene.ninesync_fx
failed = []
class A2:
    name = "synth"; path = X2; index = 0; keys = [Key(0)]
controls = {"SelfM": {"kind": "self", "item": ""}, "SelfXYZ": {"kind": "self", "item": "XYZ"}, "SelfRx": {"kind": "self", "item": "Rx"}, "Tr": {"kind": "self", "item": "Tr"}, "Si": {"kind": "self", "item": "Si"}}
def check(ok, text):
    print(("ok   " if ok else "FAIL ") + text)
    if not ok:
        failed.append(text)
def build(key, split):
    settings.mme_particle_split = split
    r = bpy.data.objects.new("root_" + key, None); r["mmd_bridge_accessory"] = key; col.objects.link(r)
    mb.mme_build_effect(scene, col, settings, A2, r, X2, FX2, 1.0, controls)
    key_tr(r, [(0, 1.0), (40, 0.0)])
    return r
def passes(r):
    return {c.name.split("_", 1)[-1].split(".")[0]: c for c in r.children if c.get(mb.MME_PASS_KEY_PROP)}
def looped(r):
    return next(c for name, c in passes(r).items() if name.endswith("P0"))
def evaluate(obj):
    dg = bpy.context.evaluated_depsgraph_get(); ev = obj.evaluated_get(dg); me = ev.to_mesh(); n = len(me.vertices)
    co = np.empty(n * 3, np.float32); me.vertices.foreach_get("co", co)
    attrs = {}
    for a in me.attributes:
        if a.domain == "POINT" and a.data_type in ("FLOAT", "INT") and a.name.startswith("mme"):
            buf = np.empty(n, np.float32 if a.data_type == "FLOAT" else np.int32); a.data.foreach_get("value", buf); attrs[a.name] = buf
    ev.to_mesh_clear()
    return co.reshape(-1, 3), attrs
def stage_group(obj, stage):
    owner = "%s|%s" % (obj.parent.get("mmd_bridge_accessory"), obj.name.split(".")[0])
    return next((g for g in bpy.data.node_groups if g.get(mb.MME_STAGE_PROP) == owner + "|" + stage), None)
def insert(group, idname, **props):
    gin = next(n for n in group.nodes if n.bl_idname == "NodeGroupInput"); gout = next(n for n in group.nodes if n.bl_idname == "NodeGroupOutput")
    source = gout.inputs[0].links[0].from_socket
    node = group.nodes.new(idname)
    for k, v in props.items():
        setattr(node, k, v)
    group.links.new(source, node.inputs[0]); group.links.new(node.outputs[0], gout.inputs[0])
    return node
def outer_tree(obj):
    return next(m.node_group for m in obj.modifiers if m.type == "NODES" and m.node_group and "NineSync Frame source" in m.node_group.nodes)
def frame_insert(obj, key, idname, **props):
    tree = outer_tree(obj); end = tree.nodes["NineSync Frame %s out" % key]
    source = end.inputs[0].links[0].from_socket
    node = tree.nodes.new(idname); node.parent = tree.nodes["NineSync Frame " + key]
    for k, v in props.items():
        setattr(node, k, v)
    tree.links.new(source, node.inputs[0]); tree.links.new(node.outputs[0], end.inputs[0])
    return node
def frame_nodes(obj):
    tree = outer_tree(obj)
    return sorted(n.name for n in tree.nodes if n.parent and n.parent.name.startswith("NineSync Frame ") and n.bl_idname not in ("NodeReroute",))
def stage_count():
    return len([g for g in bpy.data.node_groups if g.get(mb.MME_STAGE_PROP)])

split_root = build("synth_split", True)
plain_root = build("synth_plain", False)
check(any(g.name.startswith("MME VS Particles") for g in bpy.data.node_groups), "the looped pass got a per-particle shader group")
for f in (5, 17, 33):
    scene.frame_set(f)
    for name, a in passes(split_root).items():
        b = passes(plain_root)[name]
        ca, aa = evaluate(a); cb, ab = evaluate(b)
        same = ca.shape == cb.shape and np.array_equal(ca, cb) and set(aa) == set(ab) and all(np.array_equal(aa[k], ab[k]) for k in aa)
        check(same, "f%d %s: split and plain builds match bit for bit (%d vertices)" % (f, name, len(ca)))

obj = looped(split_root)
scene.frame_set(17)
base_co, base_attrs = evaluate(obj)
visible = base_attrs["mme_cull"] < 0.5
copies = int(base_attrs["mme_inst"].max()) + 1
check(stage_group(obj, "particles") is not None and "NineSync Frame source" in outer_tree(obj).nodes and "NineSync Frame final" in outer_tree(obj).nodes, "the pass has its source and final frames and a particle points group")
offset = insert(stage_group(obj, "particles"), "GeometryNodeSetPosition")
offset.inputs["Offset"].default_value = (0.0, 0.0, 1.0)
moved_co, _ = evaluate(obj)
check(np.allclose(moved_co[visible] - base_co[visible], (0.0, 0.0, 1.0), atol=1e-5), "moving the particle points offsets the shader result")
delete = insert(stage_group(obj, "particles"), "GeometryNodeDeleteGeometry", domain="POINT")
index = stage_group(obj, "particles").nodes.new("GeometryNodeInputIndex")
compare = stage_group(obj, "particles").nodes.new("FunctionNodeCompare"); compare.data_type = "INT"; compare.operation = "GREATER_EQUAL"
compare.inputs["B"].default_value = 1
stage_group(obj, "particles").links.new(index.outputs[0], compare.inputs["A"]); stage_group(obj, "particles").links.new(compare.outputs[0], delete.inputs["Selection"])
kept_co, kept_attrs = evaluate(obj)
first = base_attrs["mme_inst"] == 0
check(len(kept_co) * copies == len(base_co) and np.allclose(kept_co[kept_attrs["mme_cull"] < 0.5], base_co[first & visible] + (0.0, 0.0, 1.0), atol=1e-5), "deleting particle points keeps the others' per-particle values")
final = frame_insert(obj, "final", "GeometryNodeSetPosition")
final.inputs["Offset"].default_value = (0.0, 2.0, 0.0)
final_co, _ = evaluate(obj)
check(np.allclose(final_co - kept_co, (0.0, 2.0, 0.0), atol=1e-5), "the final stage edits the finished geometry")
source = frame_insert(obj, "source", "GeometryNodeTransform")
source.inputs["Translation"].default_value = (0.3, 0.0, 0.0)
source_co, _ = evaluate(obj)
check(not np.allclose(source_co, final_co, atol=1e-6), "moving the source mesh changes what the shader reads")
edited = source_co.copy()
groups_before = {"particles": stage_group(obj, "particles").as_pointer()}
nodes_before = frame_nodes(obj)
count = stage_count()

settings.mme_particle_split = True
bpy.ops.ninesync_fx.refresh_effects(root_name=split_root.name, force=True)
check(any(g.name.startswith("MME VS Particles") and g.users for g in bpy.data.node_groups), "the rebuilt pass is split")
obj = looped(split_root)
check(all(stage_group(obj, s).as_pointer() == groups_before[s] for s in groups_before), "a forced rebuild links the same particle points group back in")
check(frame_nodes(obj) == nodes_before and len(nodes_before) >= 2, "a forced rebuild carries the nodes in both frames over (%s vs %s)" % (nodes_before, frame_nodes(obj)))
check(abs(outer_tree(obj).nodes[nodes_before[0]].location.x) < 1e6, "carried nodes keep a location")
check(stage_count() == count, "a forced rebuild makes no new stage groups (%d)" % stage_count())
scene.frame_set(17)
again, _ = evaluate(obj)
check(again.shape == edited.shape and np.allclose(again, edited, atol=1e-6), "edits give the same result after the rebuild")

path = os.path.join(tempfile.mkdtemp(prefix="ns_stage_"), "stages.blend")
bpy.ops.wm.save_as_mainfile(filepath=path)
bpy.ops.wm.open_mainfile(filepath=path)
scene = bpy.context.scene
split_root = bpy.data.objects["root_synth_split"]
bpy.ops.ninesync_fx.refresh_effects(root_name=split_root.name, force=True)
obj = looped(split_root)
scene.frame_set(17)
reopened, _ = evaluate(obj)
check(reopened.shape == edited.shape and np.allclose(reopened, edited, atol=1e-6), "edits survive save, reopen and a forced rebuild")
check(stage_count() == count, "no duplicate stage groups after reopening (%d)" % stage_count())
print("FAILED:", "none" if not failed else failed)
