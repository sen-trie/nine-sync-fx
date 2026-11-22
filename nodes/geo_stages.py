import bpy

from ..common.constants import MME_STAGE_PROP
from ..common.log import log

MME_STAGES = {
    "particles": (
        "Edit Particle Points",
        "One point per particle (LoopByCount copy), after the per-particle part of the shader. Delete points to drop particles, move them to offset a particle's result, or edit the stored per-particle values",
    ),
    "source": (
        "Edit Source Mesh",
        "The accessory mesh before the vertex shader reads it. Nodes here survive rebuilds",
    ),
    "instances": (
        "Edit Particle Instances",
        "One instance per particle sprite (needs Edit Particle Instances on the pass). Nodes here survive rebuilds",
    ),
    "final": (
        "Edit Final Geometry",
        "The finished pass before its Bake node. Nodes here survive rebuilds",
    ),
}

_DROPPED_INPUT = "MME Pass"


# Groups from an earlier development build have a second "MME Pass" input that duplicated Geometry. It is removed when
# nothing inside the group uses it.
def _drop_old_input(group):
    item = next(
        (i for i in group.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT" and i.name == _DROPPED_INPUT),
        None,
    )
    if item is None:
        return
    for node in group.nodes:
        if node.bl_idname == "NodeGroupInput" and any(s.identifier == item.identifier and s.is_linked for s in node.outputs):
            return
    group.interface.remove(item)


# The node group the user edits for one stage of one pass. It belongs to the user: it carries a stage key
# instead of the owner tag, so pass clean-up and rebuilds never delete it, and every rebuild links the same group
# back in. Created as a plain pass-through the first time.
def mme_user_stage(owner, stage):
    key = "%s|%s" % (owner, stage)
    for group in bpy.data.node_groups:
        if group.bl_idname == "GeometryNodeTree" and group.get(MME_STAGE_PROP) == key:
            _drop_old_input(group)
            return group
    title, description = MME_STAGES[stage]
    group = bpy.data.node_groups.new("%s: %s" % (title, owner.split("|", 1)[-1]), "GeometryNodeTree")
    group[MME_STAGE_PROP] = key
    group.description = description
    group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin = group.nodes.new("NodeGroupInput")
    gin.location = (-300.0, 0.0)
    gout = group.nodes.new("NodeGroupOutput")
    gout.location = (300.0, 0.0)
    group.links.new(gin.outputs[0], gout.inputs[0])
    return group


# The geometry input and output of a stage group node, or None when the user edited them away.
def mme_stage_sockets(node):
    source = next((s for s in node.inputs if s.type == "GEOMETRY"), None)
    result = next((s for s in node.outputs if s.type == "GEOMETRY"), None)
    if source is None or result is None:
        log.warning("%s group %s has no geometry input or output; skipped", node.label, node.node_tree.name if node.node_tree else "")
        return None
    return source, result


# Places the user's stage group on the geometry. A stage whose group no longer has a geometry input and output
# (edited that way by hand) is left out with a warning rather than breaking the pass.
def mme_stage(S, geometry, owner, stage):
    group = mme_user_stage(owner, stage)
    node = S.node("GeometryNodeGroup", MME_STAGES[stage][0])
    node.node_tree = group
    node.width = 220
    sockets = mme_stage_sockets(node)
    if sockets is None:
        S.tree.nodes.remove(node)
        return geometry
    S.link(geometry, sockets[0])
    return sockets[1]
