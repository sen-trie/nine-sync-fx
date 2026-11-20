import bpy

from ..common.constants import MME_STAGE_PROP
from ..common.log import log

MME_STAGES = {
    "particles": (
        "Edit Particle Points",
        "One point per particle (LoopByCount copy), after the per-particle part of the shader. Delete points to drop particles, move them to offset a particle's result, or edit the stored per-particle values",
    ),
}


# The node group the user edits for one stage of one pass. It belongs to the user: it carries a stage key
# instead of the owner tag, so pass clean-up and rebuilds never delete it, and every rebuild links the same group
# back in. Created as a plain pass-through the first time.
def mme_user_stage(owner, stage):
    key = "%s|%s" % (owner, stage)
    for group in bpy.data.node_groups:
        if group.bl_idname == "GeometryNodeTree" and group.get(MME_STAGE_PROP) == key:
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


# Places the user's stage group on the geometry. A stage whose group no longer has a geometry input and output
# (edited that way by hand) is left out with a warning rather than breaking the pass.
def mme_stage(S, geometry, owner, stage):
    group = mme_user_stage(owner, stage)
    node = S.node("GeometryNodeGroup", MME_STAGES[stage][0])
    node.node_tree = group
    node.width = 220
    source = next((s for s in node.inputs if s.type == "GEOMETRY"), None)
    result = next((s for s in node.outputs if s.type == "GEOMETRY"), None)
    if source is None or result is None:
        log.warning("%s group %s has no geometry input or output; skipped", MME_STAGES[stage][0], group.name)
        S.tree.nodes.remove(node)
        return geometry
    S.link(geometry, source)
    return result
