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
def passes(r):
    return sorted((c for c in r.children if c.get(mb.MME_PASS_KEY_PROP)), key=lambda c: c.name)
def outer_tree(obj):
    return next((m.node_group for m in obj.modifiers if m.type == "NODES" and m.node_group and "NineSync Frame source" in m.node_group.nodes), None)
def evaluate(obj):
    dg = bpy.context.evaluated_depsgraph_get(); ev = obj.evaluated_get(dg); me = ev.to_mesh(); n = len(me.vertices)
    co = np.empty(n * 3, np.float32); me.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return co.reshape(-1, 3)
def stage_groups():
    return {g.get(mb.MME_STAGE_PROP): g.as_pointer() for g in bpy.data.node_groups if g.get(mb.MME_STAGE_PROP)}
def leaks():
    return sorted(g.name for g in bpy.data.node_groups if g.get(mb.MME_OWNER_PROP) and (g.users == 0 or g.use_fake_user))
def kept_previous():
    return [g.name for g in bpy.data.node_groups if g.name.endswith("(your nodes, previous build)")]
def drivers(obj):
    return sorted(d.data_path for d in (obj.animation_data.drivers if obj.animation_data else []))
def rebuild():
    bpy.ops.ninesync_fx.refresh_effects(root_name=r.name, force=True)
    return next(c for c in passes(r) if c.name.split(".")[0] == name)

r = bpy.data.objects.new("root_synth", None); r["mmd_bridge_accessory"] = "synth"; col.objects.link(r)
mb.mme_build_effect(scene, col, settings, A2, r, X2, FX2, 1.0, controls)
key_tr(r, [(0, 1.0), (40, 0.0)])
obj = next(c for c in passes(r) if outer_tree(c) is not None)
name = obj.name.split(".")[0]
tree = outer_tree(obj)
print("pass", obj.name, "sprites", "NineSync Frame instances" in tree.nodes)
check(all(("NineSync Stage " + k) in tree.nodes for k in ("source", "final")), "the source and final frames hold a stage group node")
stage = tree.nodes["NineSync Stage final"].node_tree
check(stage.get(mb.MME_STAGE_PROP, "").endswith("|final") and stage.interface.items_tree.get("MME Pass") is None, "the final stage group is user-owned and has only its Geometry input")
scene.frame_set(17)
base = evaluate(obj)

gin = next(n for n in stage.nodes if n.bl_idname == "NodeGroupInput"); gout = next(n for n in stage.nodes if n.bl_idname == "NodeGroupOutput")
move = stage.nodes.new("GeometryNodeSetPosition"); move.inputs["Offset"].default_value = (0.0, 1.0, 0.0)
stage.links.new(gin.outputs[0], move.inputs["Geometry"]); stage.links.new(move.outputs[0], gout.inputs[0])

frame = tree.nodes["NineSync Frame final"]; end = tree.nodes["NineSync Frame final out"]
before_end = end.inputs[0].links[0].from_socket
rin = tree.nodes.new("GeometryNodeRepeatInput"); rout = tree.nodes.new("GeometryNodeRepeatOutput"); rin.pair_with_output(rout)
rin.inputs["Iterations"].default_value = 2
inner = tree.nodes.new("GeometryNodeSetPosition"); inner.inputs["Offset"].default_value = (0.0, 0.0, 0.25)
for n in (rin, rout, inner):
    n.parent = frame
tree.links.new(before_end, rin.inputs["Geometry"]); tree.links.new(rin.outputs["Geometry"], inner.inputs["Geometry"]); tree.links.new(inner.outputs[0], rout.inputs["Geometry"])
copy = tree.nodes.new("GeometryNodeTransform"); copy.name = "user copy of the pass"; copy.parent = frame
copy.inputs["Translation"].default_value = (0.0, 0.0, 20.0)
tree.links.new(tree.nodes["NineSync Pass"].outputs[0], copy.inputs["Geometry"])
join = tree.nodes.new("GeometryNodeJoinGeometry"); join.name = "user join"; join.parent = frame
tree.links.new(rout.outputs["Geometry"], join.inputs[0]); tree.links.new(copy.outputs[0], join.inputs[0])
tree.links.new(join.outputs[0], end.inputs[0])
note = tree.nodes.new("NodeFrame"); note.name = "user note"; note.label = "outside every frame"; note.location = (0, 2000)
edited = evaluate(obj)
check(len(edited) == 2 * len(base), "the edits take effect (%d -> %d vertices)" % (len(base), len(edited)))
user_nodes = sorted([rin.name, rout.name, inner.name, copy.name, join.name, note.name])
pointer = tree.as_pointer()
groups = stage_groups()
owned = len([g for g in bpy.data.node_groups if g.get(mb.MME_OWNER_PROP) == tree.get(mb.MME_OWNER_PROP)])
driven = drivers(obj)
obj.ninesync_fx_edit_instances = True

obj = rebuild()
tree = outer_tree(obj)
check(tree.as_pointer() == pointer, "a forced rebuild reuses the same wrapper tree")
check(obj.ninesync_fx_edit_instances, "a forced rebuild keeps the pass's Edit Particle Instances toggle")
check(all(n in tree.nodes for n in user_nodes), "zone nodes, links from the MME Pass node and nodes outside the frames stay")
check(tree.nodes["user copy of the pass"].inputs[0].links and tree.nodes["user copy of the pass"].inputs[0].links[0].from_node.name == "NineSync Pass", "the link from the MME Pass node is kept")
check(stage_groups() == groups, "the same stage groups are linked back, none made")
check(not kept_previous(), "nothing kept as a previous build")
check(not leaks(), "no orphan or fake-user effect groups left (%s)" % leaks())
check(len([g for g in bpy.data.node_groups if g.get(mb.MME_OWNER_PROP) == tree.get(mb.MME_OWNER_PROP)]) == owned, "the old pass tree was freed")
check(drivers(obj) == driven and len(driven) > 0, "the new pass object has the same drivers (%d)" % len(driven))
scene.frame_set(17)
again = evaluate(obj)
check(again.shape == edited.shape and np.allclose(again, edited, atol=1e-6), "the result is the same after the rebuild")

manifest = mb.mme_read_manifest(obj); manifest["build"]["sig"] = "changed"; mb.mme_write_manifest(obj, manifest)
bpy.ops.ninesync_fx.refresh_effects(root_name=r.name)
obj = next(c for c in passes(r) if c.name.split(".")[0] == name)
tree = outer_tree(obj)
check(tree.as_pointer() == pointer and all(n in tree.nodes for n in user_nodes), "a plain rebuild of a changed pass reuses the wrapper with every user node")
check(stage_groups() == groups and not kept_previous() and not leaks(), "a plain rebuild of a changed pass leaves no leftovers (%s)" % leaks())
check(len([g for g in bpy.data.node_groups if g.get(mb.MME_OWNER_PROP) == tree.get(mb.MME_OWNER_PROP)]) == owned, "a plain rebuild frees the replaced pass tree")
scene.frame_set(17)
check(np.allclose(evaluate(obj), edited, atol=1e-6), "the result is the same after a plain rebuild")

path = os.path.join(tempfile.mkdtemp(prefix="ns_wrap_"), "wrap.blend")
bpy.ops.wm.save_as_mainfile(filepath=path)
bpy.ops.wm.open_mainfile(filepath=path)
scene = bpy.context.scene
r = bpy.data.objects["root_synth"]
obj = rebuild()
tree = outer_tree(obj)
check(tree.as_pointer() != 0 and all(n in tree.nodes for n in user_nodes), "after save and reopen a forced rebuild still keeps every user node")
check(not leaks() and not kept_previous(), "no leftovers after reopen and rebuild")
scene.frame_set(17)
reopened = evaluate(obj)
check(reopened.shape == edited.shape and np.allclose(reopened, edited, atol=1e-6), "the result is the same after reopen and rebuild")

groups = stage_groups()
tree[mb.WRAP_PROP] = 0
obj = rebuild()
tree = outer_tree(obj)
check(tree[mb.WRAP_PROP] == mb.MME_GRAPH_VERSION, "a wrapper of another layout is built new")
check("user copy of the pass" in tree.nodes and tree.nodes["user copy of the pass"].inputs[0].links and tree.nodes["user copy of the pass"].inputs[0].links[0].from_node.name == "NineSync Pass", "the fallback carries links from the MME Pass node")
check(len(kept_previous()) == 1, "the fallback keeps the old tree because the zone could not be carried (%s)" % kept_previous())
stage = tree.nodes["NineSync Stage final"].node_tree
check(stage_groups() == groups and any(n.bl_idname == "GeometryNodeSetPosition" for n in stage.nodes), "the fallback links the same stage group, edit inside, back")
keys = []
for title in ("second scene", "third scene"):
    other = bpy.data.scenes.new(title)
    with bpy.context.temp_override(scene=other, view_layer=other.view_layers[0]):
        bpy.ops.import_scene.ninesync_fx_accessory(filepath=X2, scale=1.0)
    made = next(o for o in other.objects if o.get("mme_params"))
    keys.append((made["mmd_bridge_accessory"], {c.active_material.as_pointer() for c in made.children if c.get(mb.MME_DRAW_PROP) and c.active_material}))
check(keys[0][0] != keys[1][0], "the same file imported into two scenes gets two keys (%s, %s)" % (keys[0][0], keys[1][0]))
check(not (keys[0][1] & keys[1][1]), "the two imports do not share pass materials")
print("FAILED:", "none" if not failed else failed)
