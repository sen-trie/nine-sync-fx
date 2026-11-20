import bpy

from ..common.constants import (
    MME_ATTR_INST,
    MME_ATTR_PARTICLE,
    MME_KEY_PROP,
)
from .layout import mme_prune_tree
from .socket import MmeSock

MME_CONST = 0
MME_PARTICLE = 1
MME_VERTEX = 2

MME_PURE_NODES = frozenset(
    (
        "ShaderNodeMath",
        "ShaderNodeVectorMath",
        "ShaderNodeCombineXYZ",
        "ShaderNodeSeparateXYZ",
        "ShaderNodeMix",
        "ShaderNodeMapRange",
        "ShaderNodeClamp",
        "ShaderNodeSeparateColor",
        "ShaderNodeCombineColor",
        "ShaderNodeValue",
        "FunctionNodeSeparateColor",
        "FunctionNodeCombineColor",
        "FunctionNodeSeparateMatrix",
        "FunctionNodeCombineMatrix",
        "FunctionNodeMatrixMultiply",
        "FunctionNodeTransposeMatrix",
        "FunctionNodeInvertMatrix",
        "FunctionNodeTransformPoint",
        "FunctionNodeTransformDirection",
        "FunctionNodeIntegerMath",
        "FunctionNodeBooleanMath",
        "FunctionNodeBitMath",
        "FunctionNodeCompare",
        "FunctionNodeFloatToInt",
        "FunctionNodeInputBool",
        "FunctionNodeInputInt",
        "FunctionNodeInputVector",
        "FunctionNodeInputColor",
        "FunctionNodeInputString",
        "GeometryNodeSwitch",
        "GeometryNodeIndexSwitch",
        "NodeReroute",
        "NodeGroupInput",
        "NodeGroupOutput",
    )
)

MME_UNIFORM_NODES = frozenset(
    (
        "GeometryNodeObjectInfo",
        "GeometryNodeSelfObject",
        "GeometryNodeInputActiveCamera",
        "GeometryNodeInputSceneTime",
        "GeometryNodeCameraInfo",
    )
)

MME_ATTRIBUTE_TYPES = {
    "VALUE": ("FLOAT", "NodeSocketFloat"),
    "INT": ("INT", "NodeSocketInt"),
    "BOOLEAN": ("BOOLEAN", "NodeSocketBool"),
    "VECTOR": ("FLOAT_VECTOR", "NodeSocketVector"),
    "RGBA": ("FLOAT_COLOR", "NodeSocketColor"),
    "ROTATION": ("QUATERNION", "NodeSocketRotation"),
}

MME_SPLIT_MIN_NODES = 4


# One value that the particle part of a split vertex shader hands to the vertex part: it is stored as a point
# attribute on the loop instances, travels through Instance on Points and Realize Instances, and is read back
# as a group input of the vertex part.
class MmeParticleValue:
    def __init__(self, attribute, data_type, label, output, input):
        self.attribute = attribute
        self.data_type = data_type
        self.label = label
        self.output = output
        self.input = input


# A vertex shader split in two: 'tree' runs on the loop instance points, 'values' carry its results to the
# vertex part (the original inner group, now smaller).
class MmeParticleSplit:
    def __init__(self, tree, values, moved):
        self.tree = tree
        self.values = values
        self.moved = moved


# Whether a group only computes from its inputs (and scene-wide single values), recursively; such a group
# gives the same result in any field context.
def mme_group_is_pure(group, seen):
    if group is None:
        return False
    if group.name in seen:
        return seen[group.name]
    seen[group.name] = False
    for item in group.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "INPUT" and getattr(item, "default_input", "VALUE") != "VALUE":
            return False
    pure = all(mme_node_is_pure(node, seen, True) for node in group.nodes)
    seen[group.name] = pure
    return pure


# Nodes whose outputs depend only on their linked inputs. Anything not known to be pure (field inputs such as
# Position or Index, implicit field defaults, unknown node types) counts as per-vertex, so the split can only
# ever be too cautious.
def mme_node_is_pure(node, seen, allow_uniform=False):
    idname = node.bl_idname
    if idname == "NodeFrame":
        return True
    if idname == "GeometryNodeGroup":
        return mme_group_is_pure(node.node_tree, seen)
    if idname == "GeometryNodeImageTexture":
        vector = node.inputs.get("Vector")
        return vector is not None and vector.is_linked
    if allow_uniform and idname in MME_UNIFORM_NODES:
        return True
    return idname in MME_PURE_NODES


# Domain of a value the pass tree hands to the vertex shader: constant when it is a plain number or computed
# from single values only, particle when its only field input is the loop instance attribute, vertex otherwise.
def mme_provided_domain(value, seen):
    if not isinstance(value, (MmeSock, bpy.types.NodeSocket)):
        return MME_CONST
    socket = value.socket if isinstance(value, MmeSock) else value
    tree = socket.id_data
    upstream = {}
    for link in tree.links:
        if not link.is_muted:
            upstream.setdefault(link.to_node.name, []).append(link.from_node)
    domain = MME_CONST
    stack = [socket.node]
    visited = set()
    while stack:
        node = stack.pop()
        if node.name in visited:
            continue
        visited.add(node.name)
        idname = node.bl_idname
        if idname == "GeometryNodeInputNamedAttribute":
            name = node.inputs["Name"]
            if name.is_linked or name.default_value != MME_ATTR_INST:
                return MME_VERTEX
            domain = max(domain, MME_PARTICLE)
        elif idname == "NodeGroupInput":
            continue
        elif not mme_node_is_pure(node, seen, True):
            return MME_VERTEX
        stack.extend(upstream.get(node.name, ()))
    return domain


# Domain of every node of the vertex shader group, from the domains of its group inputs: a pure node takes the
# highest domain among its linked inputs, an impure one is per vertex. Nodes in 'forced' are per vertex.
def mme_node_domains(inner, input_domain, seen, forced):
    incoming = {}
    for link in inner.links:
        if not link.is_muted:
            incoming.setdefault(link.to_node.name, []).append(link)
    domains = {}

    def socket_domain(node, socket):
        if node.bl_idname == "NodeGroupInput":
            return input_domain.get(socket.identifier, MME_CONST)
        return domains.get(node.name, MME_VERTEX)

    for start in inner.nodes:
        if start.bl_idname in ("NodeGroupInput", "NodeFrame") or start.name in domains:
            continue
        stack = [(start, False)]
        while stack:
            node, ready = stack.pop()
            if node.name in domains and not ready:
                continue
            if not ready:
                domains[node.name] = None
                stack.append((node, True))
                for link in incoming.get(node.name, ()):
                    source = link.from_node
                    if source.bl_idname != "NodeGroupInput" and source.name not in domains:
                        stack.append((source, False))
                continue
            if node.name in forced or not mme_node_is_pure(node, seen):
                domains[node.name] = MME_VERTEX
                continue
            domain = MME_CONST
            for link in incoming.get(node.name, ()):
                found = socket_domain(link.from_node, link.from_socket)
                domain = max(domain, MME_VERTEX if found is None else found)
            domains[node.name] = domain
    return domains, socket_domain


# Particle-domain sockets that per-vertex nodes (or the group output) read: what the particle part must hand
# over. Returns the boundary sockets and the nodes that produce an unsupported type, if any. Matrices are not
# handed over on purpose: 16 floats per realized vertex cost more to carry than rebuilding the matrix from its
# few per-particle inputs (measured on Nuke2), so the cut moves up to those inputs.
def mme_split_boundary(inner, domains, socket_domain):
    boundary = {}
    blocked = set()
    for link in inner.links:
        if link.is_muted:
            continue
        target = link.to_node
        consumer = MME_VERTEX if target.bl_idname == "NodeGroupOutput" else domains.get(target.name, MME_VERTEX)
        if consumer != MME_VERTEX or socket_domain(link.from_node, link.from_socket) != MME_PARTICLE:
            continue
        socket = link.from_socket
        if socket.type not in MME_ATTRIBUTE_TYPES or (socket.type == "VECTOR" and getattr(socket, "dimensions", 3) != 3):
            if link.from_node.bl_idname == "NodeGroupInput":
                return None, None
            blocked.add(link.from_node.name)
            continue
        boundary.setdefault((link.from_node.name, socket.identifier), socket)
    return boundary, blocked


# A copy owned by this pass. It must not keep a shared library key, or the library lookup could later hand
# the copy out as the original group.
def mme_private_copy(tree, name, owner_prop, owner):
    copy = tree.copy()
    copy.name = name
    if MME_KEY_PROP in copy:
        del copy[MME_KEY_PROP]
    copy[owner_prop] = owner
    return copy


def mme_drop_unused_inputs(tree):
    used = set()
    for node in tree.nodes:
        if node.bl_idname == "NodeGroupInput":
            used.update(s.identifier for s in node.outputs if s.is_linked)
    for item in [i for i in tree.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"]:
        if item.identifier not in used:
            tree.interface.remove(item)
    for node in tree.nodes:
        if node.bl_idname == "NodeGroupInput":
            for socket in node.outputs:
                socket.hide = not socket.is_linked


def mme_drop_empty_frames(tree):
    parents = {node.parent.name for node in tree.nodes if node.parent is not None}
    for node in [n for n in tree.nodes if n.bl_idname == "NodeFrame" and n.name not in parents]:
        tree.nodes.remove(node)


def mme_socket_label(socket):
    node = socket.node
    base = node.label or node.name
    return ("%s %s" % (base, socket.name) if len(node.outputs) > 1 else base)[:60]


# Splits the vertex shader group of a LoopByCount pass so that everything depending only on the loop index and
# scene-wide values runs once per loop instance instead of once per realized vertex. 'provided' maps each
# need key to the value the pass tree feeds that input; 'inner_inputs' maps need keys to input identifiers.
# The inner group is modified in place to become the per-vertex part. Returns the split and "", or None (inner
# untouched) and the reason when nothing worth moving was found.
def mme_particle_split(inner, inner_inputs, provided, name, owner_prop, owner):
    seen = {}
    input_domain = {}
    for key, identifier in inner_inputs.items():
        if identifier is not None:
            input_domain[identifier] = mme_provided_domain(provided.get(key, 0.0), seen)
    if MME_PARTICLE not in input_domain.values():
        return None, "no input depends on the loop index alone"
    return mme_split_tree(inner, input_domain, name, owner_prop, owner, seen, MME_SPLIT_MIN_NODES)


# The split itself: 'input_domain' gives each group input's domain. Calls of shader functions that mix a
# per-particle and a per-vertex argument (RotY(angle, position)) stay whole on the per-vertex side: splitting
# them was measured on Nuke2 and lost, because their per-particle halves (cos, sin, ...) hand over more values
# than the arguments they replace, and each value is copied to every realized vertex.
def mme_split_tree(inner, input_domain, name, owner_prop, owner, seen, minimum):
    forced = set()
    for _attempt in range(8):
        domains, socket_domain = mme_node_domains(inner, input_domain, seen, forced)
        boundary, blocked = mme_split_boundary(inner, domains, socket_domain)
        if boundary is None:
            return None, "a loop value has a type that cannot be stored as an attribute"
        if not blocked:
            break
        forced |= blocked
    else:
        return None, "too many values of types that cannot be stored as attributes"
    moved = [n for n in inner.nodes if domains.get(n.name) == MME_PARTICLE and n.bl_idname not in ("NodeReroute", "NodeFrame")]
    weight = sum(max(1, len(n.node_tree.nodes)) if n.bl_idname == "GeometryNodeGroup" and n.node_tree else 1 for n in moved)
    if not boundary or weight < minimum:
        return None, "only %d node(s) depend on the loop index alone" % weight
    particle = mme_private_copy(inner, name, owner_prop, owner)
    for item in [i for i in particle.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "OUTPUT"]:
        particle.interface.remove(item)
    for node in [n for n in particle.nodes if n.bl_idname == "NodeGroupOutput"]:
        particle.nodes.remove(node)
    out = particle.nodes.new("NodeGroupOutput")
    out.location = (max((n.location.x for n in particle.nodes), default=0.0) + 300.0, 0.0)
    values = []
    for k, ((node_name, identifier), socket) in enumerate(boundary.items()):
        data_type, socket_type = MME_ATTRIBUTE_TYPES[socket.type]
        label = mme_socket_label(socket)
        made = particle.interface.new_socket(label, in_out="OUTPUT", socket_type=socket_type)
        source = next(s for s in particle.nodes[node_name].outputs if s.identifier == identifier)
        particle.links.new(source, next(s for s in out.inputs if s.identifier == made.identifier))
        taken = inner.interface.new_socket("Per particle: " + label, in_out="INPUT", socket_type=socket_type)
        reader = inner.nodes.new("NodeGroupInput")
        reader.label = "computed per particle"
        replacement = next(s for s in reader.outputs if s.identifier == taken.identifier)
        targets = [link.to_socket for link in socket.links if not link.is_muted and (link.to_node.bl_idname == "NodeGroupOutput" or domains.get(link.to_node.name, MME_VERTEX) == MME_VERTEX)]
        for target in targets:
            inner.links.new(replacement, target)
        first = targets[0].node if targets else socket.node
        reader.location = (first.location.x - 220.0, first.location.y)
        values.append(MmeParticleValue("%s%d" % (MME_ATTR_PARTICLE, k), data_type, label, made.identifier, taken.identifier))
    mme_prune_tree(particle, out)
    mme_drop_unused_inputs(particle)
    mme_drop_empty_frames(particle)
    mme_prune_tree(inner)
    mme_drop_unused_inputs(inner)
    mme_drop_empty_frames(inner)
    return MmeParticleSplit(particle, values, weight), ""
