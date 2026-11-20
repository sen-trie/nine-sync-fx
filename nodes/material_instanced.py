import bpy

from ..common.constants import (
    MME_ATTR_NRM,
    MME_ATTR_POS,
    MME_ATTR_UV,
    MME_LOOP_PREFIX,
    MME_OWNER_PROP,
)
from .material_compile import mme_owned
from .names import (
    MME_INPUT_NODE_PREFIX,
    mme_affine_name,
)

MME_INSTANCED_SUFFIX = "|instanced"
MME_AFFINE_FEATURES = {"p": MME_ATTR_POS, "n": MME_ATTR_NRM, "uv": MME_ATTR_UV}
MME_ORDER_FREE_MODES = ("OPAQUE", "ADD", "ADD_ALPHA", "MULTIPLY")


# The material of a pass's instanced output: a fresh copy of the pass material on every build (so edits to the
# pass material carry over), sharing its pixel shader group. Each varying the plan covers is rebuilt from the
# instance's coefficients and the vertex's own position, normal or UV; loop indices are read from the instance.
# A separate material because EEVEE allows a material only 14 Geometry attributes, which the realized varyings
# already come close to.
def mme_instanced_material(mat, plan, owner):
    key = owner + MME_INSTANCED_SUFFIX
    copy = mat.copy()
    old = mme_owned(bpy.data.materials, key)
    if old is not None:
        old.user_remap(copy)
        bpy.data.materials.remove(old)
    copy.name = mat.name + " (instanced)"
    copy[MME_OWNER_PROP] = key
    tree = copy.node_tree
    nodes, links = tree.nodes, tree.links
    features = {}
    inputs = [n for n in nodes if n.bl_idname == "ShaderNodeAttribute" and n.name.startswith(MME_INPUT_NODE_PREFIX)]
    left = min((n.location[0] for n in inputs), default=0.0) - 700
    top = max((n.location[1] for n in inputs), default=0.0)

    def feature(block):
        if block not in features:
            node = nodes.new("ShaderNodeAttribute")
            node.location = (left, top + 60 * (len(features) + 1))
            node.name = "%sfeature_%s" % (MME_INPUT_NODE_PREFIX, block)
            node.attribute_type = "GEOMETRY"
            node.attribute_name = MME_AFFINE_FEATURES[block]
            node.label = "Vertex %s" % {"p": "position", "n": "normal", "uv": "UV"}[block]
            node.hide = True
            features[block] = node.outputs["Vector"]
        return features[block]

    def place(node, x, y):
        node.location = (x, y)
        node.hide = True
        return node

    def instancer(name, socket, label, x, y):
        node = place(nodes.new("ShaderNodeAttribute"), x, y)
        node.name = MME_INPUT_NODE_PREFIX + name
        node.attribute_type = "INSTANCER"
        node.attribute_name = name
        node.label = label
        return node.outputs[socket]

    for node in inputs:
        name = node.attribute_name
        if name.startswith(MME_LOOP_PREFIX):
            node.attribute_type = "INSTANCER"
            continue
        group = plan.channels.get(name)
        if group is None:
            continue
        x, y = node.location
        targets = [link.to_socket for link in node.outputs["Fac"].links]
        value = instancer(mme_affine_name(name, "k"), "Fac", "%s constant" % node.label, x - 460, y)
        for step, block in enumerate(group):
            dot = place(nodes.new("ShaderNodeVectorMath"), x - 300, y - 30 * step)
            dot.operation = "DOT_PRODUCT"
            links.new(instancer(mme_affine_name(name, block), "Vector", "%s x %s" % (node.label, block), x - 460, y - 30 * (step + 1)), dot.inputs[0])
            links.new(feature(block), dot.inputs[1])
            add = place(nodes.new("ShaderNodeMath"), x - 140, y - 30 * step)
            add.operation = "ADD"
            links.new(value, add.inputs[0])
            links.new(dot.outputs["Value"], add.inputs[1])
            value = add.outputs[0]
        for target in targets:
            links.new(value, target)
        nodes.remove(node)
    return copy
