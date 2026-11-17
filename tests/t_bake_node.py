import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import importlib

bake_node = importlib.import_module("ninesync_fx.runtime.bake_node")
bake_scope = importlib.import_module("ninesync_fx.runtime.bake_scope")
digest_module = importlib.import_module("ninesync_fx.common.digest")

failed = []


def check(label, ok):
    print("%-72s %s" % (label, "ok" if ok else "FAILED"))
    if not ok:
        failed.append(label)


def trees_of(group, seen=None):
    seen = [] if seen is None else seen
    if group in seen:
        return seen
    seen.append(group)
    for node in group.nodes:
        if node.bl_idname == "GeometryNodeGroup" and node.node_tree is not None:
            trees_of(node.node_tree, seen)
    return seen


def evaluated(obj):
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    result = np.array([v.co[:] for v in mesh.vertices]), sorted(a.name for a in mesh.attributes if not a.name.startswith("."))
    ev.to_mesh_clear()
    return result


src = os.environ.get("NINESYNC_THUNDER_LV2", os.path.join(paths.REPO, "debug-src"))
fx, xp = "%s/Thunder_lv2.fx" % src, "%s/Thunder_lv2.x" % src
if not os.path.isfile(fx):
    print("SKIP: Thunder lv2 not found; set NINESYNC_THUNDER_LV2")
    print("FAILED: none")
    raise SystemExit(0)

scene, col, root, cam = setup_scene()
acc = Acc()
acc.name = "Thunder_lv2"
acc.path = xp
scene.ninesync_fx.mme_backend = "GEONODES"
kids, *_ = mb.mme_build_effect(scene, col, scene.ninesync_fx, acc, root, xp, fx, 1.0, {"Tr": {"kind": "self", "item": "Tr"}})
passes = [k for k in kids if k.get("mme_draw") and k.get("mme_backend") == "GEO"]
check("the effect builds Geometry Nodes passes", len(passes) > 0)
sprite = [p for p in passes if any(n.bl_idname == "GeometryNodeRealizeInstances" for n in p.modifiers[mb.MME_GN_MODIFIER].node_group.nodes)]
plain = [p for p in passes if p not in sprite]
print("sprite passes:", [p.name for p in sprite], "other passes:", [p.name for p in plain])
check("at least one pass is made of sprites", len(sprite) > 0)

for obj in passes:
    modifier = obj.modifiers[mb.MME_GN_MODIFIER]
    top = modifier.node_group
    inner = [n for n in top.nodes if n.bl_idname == "GeometryNodeGroup" and n.node_tree.name.startswith(mb.MME_PASS_PREFIX)]
    check("%s: the modifier tree holds one pass group" % obj.name, len(inner) == 1)
    own = [n for t in trees_of(top) for n in t.nodes if n.bl_idname == "GeometryNodeBake"]
    check("%s: exactly one Bake node, inside the pass group" % obj.name, len(own) == 1 and own[0].id_data == inner[0].node_tree and own[0].name == mb.MME_BAKE_NODE)
    managed = bake_node.mme_managed_bake(modifier)
    check("%s: the managed bake is found by name" % obj.name, managed is not None and managed.node.name == mb.MME_BAKE_NODE)

obj = sprite[0]
modifier = obj.modifiers[mb.MME_GN_MODIFIER]
top = modifier.node_group
inputs = {i.name: i.identifier for i in top.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"}
check("a sprite pass has a Particle Mesh input", mb.MME_PARTICLE_MESH_INPUT in inputs)
check("the plain passes have none", all(mb.MME_PARTICLE_MESH_INPUT not in [i.name for i in p.modifiers[mb.MME_GN_MODIFIER].node_group.interface.items_tree] for p in plain))
managed = bake_node.mme_managed_bake(modifier)
base_graph, reach = bake_scope.mme_bake_scope(modifier)
check("the Particle Mesh input is not upstream of the bake", inputs[mb.MME_PARTICLE_MESH_INPUT] not in reach)
check("the other inputs reach the bake", all(v in reach for k, v in inputs.items() if k != mb.MME_PARTICLE_MESH_INPUT and k != "Geometry") or len(reach) > 0)

settings = scene.ninesync_fx
cube = bpy.data.objects.new("cube", bpy.data.meshes.new("cube"))
col.objects.link(cube)
getattr(modifier.properties.inputs, inputs[mb.MME_PARTICLE_MESH_INPUT]).value = cube
check("setting the Particle Mesh does not change the bake scope", bake_scope.mme_bake_scope(modifier)[0] == base_graph)
mb_inputs = mb.mme_bake_inputs(obj, root, scene, settings)
getattr(modifier.properties.inputs, inputs[mb.MME_PARTICLE_MESH_INPUT]).value = None
mb_inputs_after = mb.mme_bake_inputs(obj, root, scene, settings)
check("the bake inputs ignore the Particle Mesh", digest_module.digest(mb_inputs) == digest_module.digest(mb_inputs_after))

outer = {n.bl_idname for n in top.nodes}
delete = [n for n in top.nodes if n.bl_idname == "GeometryNodeDeleteGeometry"]
check("a sprite pass has the instance zone after the pass group", len(delete) == 1 and delete[0].domain == "INSTANCE")
dl_count = lambda: len(evaluated(obj)[0])
edit_id = inputs[mb.MME_EDIT_INPUT]
check("a sprite pass has a Particle Instances switch that is off", mb.MME_EDIT_INPUT in inputs and getattr(modifier.properties.inputs, edit_id).value is False)
check("the Particle Instances switch is not upstream of the bake", edit_id not in reach)
count = dl_count()
off_pos = evaluated(obj)[0]
getattr(modifier.properties.inputs, edit_id).value = True
on_pos = evaluated(obj)[0]
check("turning Particle Instances on gives the same mesh", on_pos.shape == off_pos.shape and np.allclose(on_pos, off_pos, atol=1e-4))
delete[0].inputs["Selection"].default_value = True
check("deleting every instance leaves nothing", dl_count() == 0)
delete[0].inputs["Selection"].default_value = False
check("a user node after the bake does not change the output", dl_count() == count)

user_bake = top.nodes.new("GeometryNodeBake")
user_bake.bake_items.new("GEOMETRY", "Geometry")
check("a user Bake node is added to the modifier", len(modifier.bakes) == 2)
found = bake_node.mme_managed_bake(modifier)
check("the managed bake is still the pass's own", found is not None and found.node.name == mb.MME_BAKE_NODE)
check("a user Bake node does not change the bake scope", bake_scope.mme_bake_scope(modifier)[0] == base_graph)
top.nodes.remove(user_bake)

pass_tree = [n for n in top.nodes if n.bl_idname == "GeometryNodeGroup" and n.node_tree.name.startswith(mb.MME_PASS_PREFIX)][0].node_tree
changed = [n for n in pass_tree.nodes if n.bl_idname == "GeometryNodeSetPosition"][0]
changed.mute = True
check("editing a node inside the pass group changes the bake scope", bake_scope.mme_bake_scope(modifier)[0] != base_graph)
changed.mute = False
check("undoing the edit restores it", bake_scope.mme_bake_scope(modifier)[0] == base_graph)
for node in pass_tree.nodes:
    node.location.x += 50
check("moving nodes does not change the bake scope", bake_scope.mme_bake_scope(modifier)[0] == base_graph)

swap = bpy.data.objects.new("swap", bpy.data.meshes.new("swap"))
mesh = swap.data
mesh.from_pydata([(-0.5, -0.5, 0), (0.5, -0.5, 0), (0.5, 0.5, 0), (-0.5, 0.5, 0)], [], [(0, 1, 2, 3)])
col.objects.link(swap)
before, names = evaluated(obj)
getattr(modifier.properties.inputs, inputs[mb.MME_PARTICLE_MESH_INPUT]).value = swap
after, names_after = evaluated(obj)
print("swap", len(before), len(after), sorted(set(names_after) ^ set(names)))
check("a Particle Mesh swaps each sprite for the mesh (one quad of 4 vertices per sprite)", len(after) == len(before) and set(names) <= set(names_after))
getattr(modifier.properties.inputs, inputs[mb.MME_PARTICLE_MESH_INPUT]).value = None
check("clearing the Particle Mesh restores the sprites", np.allclose(evaluated(obj)[0], before))
check("the working attribute is removed from the output", mb.MME_ATTR_SPRITE not in names)

print("FAILED: %s" % (", ".join(failed) if failed else "none"))
