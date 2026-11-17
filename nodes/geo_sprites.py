import bpy
import numpy as np

from ..common.constants import (
    MME_ATTR_SPRITE,
    MME_CORE_VERSION,
    MME_EDIT_INPUT,
    MME_OWNER_PROP,
    MME_PARTICLE_MESH_INPUT,
    MME_SHADER_ONLY_ATTRS,
)
from .core_library import mme_core_group
from .names import mme_group_name

SPRITE_CORNERS = 4


# True when the pass mesh is made of sprites: four vertices per quad in order, the particle number (the source z) shared by
# the four, and every face inside one quad. Only such a mesh can be split into one instance per particle.
def mme_is_sprite_mesh(positions, faces):
    count = len(positions)
    if count == 0 or count % SPRITE_CORNERS:
        return False
    z = np.asarray(positions, dtype=np.float64)[:, 2].reshape(-1, SPRITE_CORNERS)
    if not np.allclose(z, z[:, :1]):
        return False
    return all(len({int(v) // SPRITE_CORNERS for v in face}) == 1 for face in faces)


def _node(tree, idname, x, y, label="", parent=None, **props):
    node = tree.nodes.new(idname)
    node.location = (x, y)
    if label:
        node.label = label
    for key, value in props.items():
        setattr(node, key, value)
    if parent is not None:
        node.parent = parent
    return node


def _item(tree, name, socket_type, io):
    return tree.interface.new_socket(name, in_out=io, socket_type=socket_type)


def _sprite_fields(tree, make):
    link = tree.links.new
    index = make("GeometryNodeInputIndex", 0, 0)
    rest = make("FunctionNodeIntegerMath", 160, 0, "corner of its sprite", operation="MODULO")
    rest.inputs[1].default_value = SPRITE_CORNERS
    link(index.outputs[0], rest.inputs[0])
    start = make("FunctionNodeIntegerMath", 320, 0, "first corner of this sprite", operation="SUBTRACT")
    link(index.outputs[0], start.inputs[0])
    link(rest.outputs[0], start.inputs[1])
    corner = []
    for k in range(SPRITE_CORNERS):
        shifted = make("FunctionNodeIntegerMath", 480, -150 * k, "corner %d" % k, operation="ADD")
        shifted.inputs[1].default_value = k
        link(start.outputs[0], shifted.inputs[0])
        position = make("GeometryNodeInputPosition", 480, -150 * k - 50)
        at = make("GeometryNodeFieldAtIndex", 640, -150 * k, "corner %d" % k, data_type="FLOAT_VECTOR", domain="POINT")
        link(shifted.outputs[0], at.inputs["Index"])
        link(position.outputs[0], at.inputs["Value"])
        corner.append(at)
    total = corner[0].outputs[0]
    for k in range(1, SPRITE_CORNERS):
        add = make("ShaderNodeVectorMath", 820 + 140 * k, -100 * k, operation="ADD")
        link(total, add.inputs[0])
        link(corner[k].outputs[0], add.inputs[1])
        total = add.outputs[0]
    centre = make("ShaderNodeVectorMath", 1400, -250, "sprite centre", operation="SCALE")
    centre.inputs["Scale"].default_value = 1.0 / SPRITE_CORNERS
    link(total, centre.inputs[0])
    return rest, corner, centre


def _first_corner(tree, make, rest):
    pick = make("FunctionNodeCompare", 320, 150, "first corner of each sprite", data_type="INT", operation="EQUAL")
    tree.links.new(rest.outputs[0], pick.inputs["A"])
    pick.inputs["B"].default_value = 0
    return pick


def _build_instances(group):
    link = group.links.new
    _item(group, "Geometry", "NodeSocketGeometry", "INPUT")
    _item(group, "Geometry", "NodeSocketGeometry", "OUTPUT")
    gin = _node(group, "NodeGroupInput", -400, 0)
    gout = _node(group, "NodeGroupOutput", 2400, 0)

    def make(idname, x, y, label="", **props):
        return _node(group, idname, x - 400, y - 600, label, **props)

    rest, corner, centre = _sprite_fields(group, make)
    index = _node(group, "GeometryNodeInputIndex", -200, 300)
    tag = _node(group, "FunctionNodeIntegerMath", 0, 300, "sprite number", operation="DIVIDE_FLOOR")
    tag.inputs[1].default_value = SPRITE_CORNERS
    link(index.outputs[0], tag.inputs[0])
    store = _node(group, "GeometryNodeStoreNamedAttribute", 200, 300, "tag sprite number", data_type="INT", domain="POINT")
    store.inputs["Name"].default_value = MME_ATTR_SPRITE
    link(gin.outputs[0], store.inputs["Geometry"])
    link(tag.outputs[0], store.inputs["Value"])
    pick = _first_corner(group, make, rest)
    points = _node(group, "GeometryNodeMeshToPoints", 1200, 300, "sprite centres", mode="VERTICES")
    link(store.outputs[0], points.inputs["Mesh"])
    link(pick.outputs[0], points.inputs["Selection"])
    link(centre.outputs[0], points.inputs["Position"])
    back = _node(group, "ShaderNodeVectorMath", 1200, -100, "-centre", operation="SCALE")
    back.inputs["Scale"].default_value = -1.0
    link(centre.outputs[0], back.inputs[0])
    local = _node(group, "GeometryNodeSetPosition", 1400, 100, "move sprite to its own origin")
    link(store.outputs[0], local.inputs["Geometry"])
    link(back.outputs[0], local.inputs["Offset"])
    group_id = _node(group, "GeometryNodeInputNamedAttribute", 1400, -100, "sprite number", data_type="INT")
    group_id.inputs["Name"].default_value = MME_ATTR_SPRITE
    split = _node(group, "GeometryNodeSplitToInstances", 1600, 100, "one instance per sprite", domain="FACE")
    link(local.outputs[0], split.inputs["Geometry"])
    link(group_id.outputs["Attribute"], split.inputs["Group ID"])
    where = _node(group, "GeometryNodeSampleIndex", 1800, -100, "centre of this instance", data_type="FLOAT_VECTOR", domain="POINT")
    position = _node(group, "GeometryNodeInputPosition", 1600, -250)
    link(points.outputs[0], where.inputs["Geometry"])
    link(position.outputs[0], where.inputs["Value"])
    link(split.outputs["Group ID"], where.inputs["Index"])
    move = _node(group, "GeometryNodeTranslateInstances", 2000, 100, "put instance at the sprite centre")
    link(split.outputs["Instances"], move.inputs["Instances"])
    link(where.outputs[0], move.inputs["Translation"])
    link(move.outputs[0], gout.inputs[0])
    group.description = "One instance per sprite, holding its four vertices around its own origin and placed at the sprite's centre"


# Shared group: replaces the pass mesh by one instance per sprite, so Instance-domain nodes (Delete Geometry, Transform
# Instances, ...) see where each particle really is. Realize Instances afterwards gives back the same mesh. It costs far
# more than the pass itself on big effects, which is why the wrapper only runs it when Particle Instances is on.
def mme_geo_instances_group():
    key = "core:sprite_instances:GN:v%d" % MME_CORE_VERSION
    return mme_core_group(mme_group_name("Particles to Instances", "GN", key), "GeometryNodeTree", key, _build_instances)


def _build_swap(group):
    link = group.links.new
    _item(group, "Geometry", "NodeSocketGeometry", "INPUT")
    _item(group, MME_PARTICLE_MESH_INPUT, "NodeSocketObject", "INPUT")
    _item(group, "Geometry", "NodeSocketGeometry", "OUTPUT")
    gin = _node(group, "NodeGroupInput", -400, 0)
    gout = _node(group, "NodeGroupOutput", 2200, 0)

    def make(idname, x, y, label="", **props):
        return _node(group, idname, x - 400, y - 300, label, **props)

    rest, corner, centre = _sprite_fields(group, make)
    side = make("ShaderNodeVectorMath", 320, -600, "diagonal", operation="DISTANCE")
    link(corner[0].outputs[0], side.inputs[0])
    link(corner[2].outputs[0], side.inputs[1])
    length = make("ShaderNodeMath", 500, -600, "side length", operation="MULTIPLY")
    length.inputs[1].default_value = 0.70710678
    link(side.outputs["Value"], length.inputs[0])
    normal = make("GeometryNodeInputNormal", 500, -700)
    size = make("GeometryNodeStoreNamedAttribute", 1200, 300, "sprite size", data_type="FLOAT", domain="POINT")
    size.inputs["Name"].default_value = "mme_size"
    direction = make("GeometryNodeStoreNamedAttribute", 1400, 300, "sprite normal", data_type="FLOAT_VECTOR", domain="POINT")
    direction.inputs["Name"].default_value = "mme_nrm"
    link(gin.outputs["Geometry"], size.inputs["Geometry"])
    link(length.outputs[0], size.inputs["Value"])
    link(size.outputs[0], direction.inputs["Geometry"])
    link(normal.outputs["Normal"], direction.inputs["Value"])
    pick = _first_corner(group, make, rest)
    points = make("GeometryNodeMeshToPoints", 1600, 300, "one point per sprite", mode="VERTICES")
    link(direction.outputs[0], points.inputs["Mesh"])
    link(pick.outputs[0], points.inputs["Selection"])
    link(centre.outputs[0], points.inputs["Position"])
    mesh = make("GeometryNodeObjectInfo", 1600, 100, MME_PARTICLE_MESH_INPUT)
    link(gin.outputs[MME_PARTICLE_MESH_INPUT], mesh.inputs["Object"])
    facing = make("GeometryNodeInputNamedAttribute", 1600, -100, "", data_type="FLOAT_VECTOR")
    facing.inputs["Name"].default_value = "mme_nrm"
    align = make("FunctionNodeAlignRotationToVector", 1780, -100, axis="Z")
    link(facing.outputs["Attribute"], align.inputs["Vector"])
    width = make("GeometryNodeInputNamedAttribute", 1600, -250, "", data_type="FLOAT")
    width.inputs["Name"].default_value = "mme_size"
    scale = make("ShaderNodeCombineXYZ", 1780, -250)
    for k in range(3):
        link(width.outputs["Attribute"], scale.inputs[k])
    place = make("GeometryNodeInstanceOnPoints", 1960, 300)
    link(points.outputs[0], place.inputs["Points"])
    link(mesh.outputs["Geometry"], place.inputs["Instance"])
    link(align.outputs[0], place.inputs["Rotation"])
    link(scale.outputs[0], place.inputs["Scale"])
    real = make("GeometryNodeRealizeInstances", 2140, 300)
    link(place.outputs[0], real.inputs[0])
    drop_a = make("GeometryNodeRemoveAttribute", 2320, 300, "drop mme_size")
    drop_a.inputs["Name"].default_value = "mme_size"
    drop_b = make("GeometryNodeRemoveAttribute", 2500, 300, "drop mme_nrm")
    drop_b.inputs["Name"].default_value = "mme_nrm"
    link(real.outputs[0], drop_a.inputs[0])
    link(drop_a.outputs[0], drop_b.inputs[0])
    count = make("GeometryNodeAttributeDomainSize", 1780, 100, component="MESH")
    link(mesh.outputs["Geometry"], count.inputs[0])
    has = make("FunctionNodeCompare", 1960, 100, "has Particle Mesh", data_type="INT", operation="GREATER_THAN")
    link(count.outputs["Point Count"], has.inputs["A"])
    has.inputs["B"].default_value = 0
    use = make("GeometryNodeSwitch", 2700, 300, "use Particle Mesh?", input_type="GEOMETRY")
    link(has.outputs[0], use.inputs["Switch"])
    link(drop_b.outputs[0], use.inputs["True"])
    link(gin.outputs["Geometry"], use.inputs["False"])
    link(use.outputs[0], gout.inputs[0])
    gout.location = (2900, 0)
    group.description = "Replaces each sprite of a particle pass by a copy of the Particle Mesh object"


# Shared group: replaces each sprite by an object's mesh, at the sprite's centre and size, facing its normal.
def mme_geo_swap_group():
    key = "core:particle_mesh:GN:v%d:aligned" % MME_CORE_VERSION
    return mme_core_group(mme_group_name("Particle Mesh", "GN", key), "GeometryNodeTree", key, _build_swap)


def _build_strip(group):
    link = group.links.new
    _item(group, "Geometry", "NodeSocketGeometry", "INPUT")
    _item(group, "Geometry", "NodeSocketGeometry", "OUTPUT")
    previous = _node(group, "NodeGroupInput", 0, 0).outputs[0]
    for k, name in enumerate(MME_SHADER_ONLY_ATTRS):
        drop = _node(group, "GeometryNodeRemoveAttribute", 220 + 240 * k, 0, "drop " + name)
        drop.inputs["Name"].default_value = name
        link(previous, drop.inputs["Geometry"])
        previous = drop.outputs[0]
    out = _node(group, "NodeGroupOutput", 220 + 240 * len(MME_SHADER_ONLY_ATTRS), 0)
    link(previous, out.inputs[0])
    group.description = "Drops the vertex shader inputs the material never reads"


# Shared group: the Remove Named Attribute nodes for the vertex shader inputs, in one node.
def mme_geo_strip_group():
    key = "core:strip_inputs:GN:v%d:%s" % (MME_CORE_VERSION, ",".join(MME_SHADER_ONLY_ATTRS))
    return mme_core_group(mme_group_name("Strip Unused Vertex Attributes", "GN", key), "GeometryNodeTree", key, _build_strip)


def _build_store(group, count):
    link = group.links.new
    _item(group, "Geometry", "NodeSocketGeometry", "INPUT")
    _item(group, "Names", "NodeSocketString", "INPUT")
    for k in range(count):
        _item(group, "Value %d" % k, "NodeSocketFloat", "INPUT")
    _item(group, "Geometry", "NodeSocketGeometry", "OUTPUT")
    gin = _node(group, "NodeGroupInput", 0, 0)
    split = _node(group, "FunctionNodeSplitString", 220, 200)
    split.inputs["Separator"].default_value = ";"
    link(gin.outputs["Names"], split.inputs["String"])
    length = _node(group, "GeometryNodeListLength", 440, 200, data_type="STRING")
    link(split.outputs[0], length.inputs[0])
    enter = _node(group, "GeometryNodeRepeatInput", 700, 0)
    leave = _node(group, "GeometryNodeRepeatOutput", 1500, 0)
    enter.pair_with_output(leave)
    link(length.outputs[0], enter.inputs["Iterations"])
    link(gin.outputs["Geometry"], enter.inputs[1])
    pick = _node(group, "GeometryNodeIndexSwitch", 940, -150, data_type="FLOAT")
    while len(pick.index_switch_items) < count:
        pick.index_switch_items.new()
    link(enter.outputs["Iteration"], pick.inputs["Index"])
    for k in range(count):
        link(gin.outputs["Value %d" % k], pick.inputs[str(k)])
    same = _node(group, "ShaderNodeMath", 1120, -150, "NaN check", operation="COMPARE")
    same.inputs[2].default_value = 0.0
    zero = _node(group, "GeometryNodeSwitch", 1280, -150, "NaN -> 0", input_type="FLOAT")
    link(pick.outputs[0], same.inputs[0])
    link(pick.outputs[0], same.inputs[1])
    link(same.outputs[0], zero.inputs["Switch"])
    link(pick.outputs[0], zero.inputs["True"])
    name = _node(group, "GeometryNodeListGetItem", 940, 150, socket_type="STRING")
    link(split.outputs[0], name.inputs["List"])
    link(enter.outputs["Iteration"], name.inputs["Index"])
    store = _node(group, "GeometryNodeStoreNamedAttribute", 1300, 50, data_type="FLOAT", domain="POINT")
    link(enter.outputs[1], store.inputs["Geometry"])
    link(name.outputs[0], store.inputs["Name"])
    link(zero.outputs[0], store.inputs["Value"])
    link(store.outputs[0], leave.inputs[0])
    out = _node(group, "NodeGroupOutput", 1760, 0)
    link(leave.outputs[0], out.inputs[0])
    group.description = "Stores the vertex shader outputs as attributes, replacing NaN by 0"


# Shared group: stores 'count' vertex shader outputs under the names in a ';' separated string, each NaN-checked.
def mme_geo_store_group(count):
    key = "core:store_outputs:GN:v%d:%d" % (MME_CORE_VERSION, count)
    return mme_core_group(mme_group_name("Store Vertex Outputs (%d)" % count, "GN", key), "GeometryNodeTree", key, lambda group: _build_store(group, count))


# Stores the NaN-checked vertex shader outputs through the shared group: names is a list of attribute names, values the
# sockets that hold them.
def mme_store_outputs(S, geometry, names, values):
    node = S.node("GeometryNodeGroup", "Store Vertex Outputs")
    node.node_tree = mme_geo_store_group(len(names))
    node.width = 240
    text = S.node("FunctionNodeInputString", "Output names (; separated)")
    text.string = ";".join(names)
    S.link(geometry, node.inputs["Geometry"])
    S.link(text.outputs[0], node.inputs["Names"])
    for k, value in enumerate(values):
        S.B.feed(node.inputs["Value %d" % k], value)
    return node.outputs[0]


# Strips the vertex shader inputs through the shared group.
def mme_strip_inputs(S, geometry):
    node = S.node("GeometryNodeGroup", "Strip Unused Vertex Attributes")
    node.node_tree = mme_geo_strip_group()
    S.link(geometry, node.inputs[0])
    return node.outputs[0]


# Wraps a finished pass tree in the tree the modifier uses, so each pass is one node group. For sprite passes the wrapper
# also holds the place for the user's instance nodes (Delete Geometry, Transform Instances, ...), Realize Instances and the
# Particle Mesh swap, all after the pass's Bake node. Its inputs repeat the pass's inputs; the returned map takes a pass
# input identifier to the wrapper's, for the drivers and defaults.
def mme_wrap_pass(top, name, owner, sprites):
    outer = bpy.data.node_groups.new(name, "GeometryNodeTree")
    outer[MME_OWNER_PROP] = owner
    try:
        sockets = [i for i in top.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"]
        sockets.sort(key=lambda i: int(i.identifier.rsplit("_", 1)[-1]))
        mapping = {}
        for item in sockets:
            made = outer.interface.new_socket(item.name, in_out="INPUT", socket_type=item.socket_type)
            for attribute in ("default_value", "min_value", "max_value", "description", "subtype"):
                if hasattr(item, attribute) and hasattr(made, attribute):
                    try:
                        setattr(made, attribute, getattr(item, attribute))
                    except (TypeError, ValueError, AttributeError):
                        continue
            mapping[item.identifier] = made.identifier
        outer.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        mesh_input = None
        edit_input = None
        if sprites:
            edit = outer.interface.new_socket(MME_EDIT_INPUT, in_out="INPUT", socket_type="NodeSocketBool")
            edit.default_value = False
            edit.description = "Turns each particle into an instance so the nodes in the frame can edit them (slower on big effects)"
            edit_input = edit.identifier
            mesh_input = outer.interface.new_socket(MME_PARTICLE_MESH_INPUT, in_out="INPUT", socket_type="NodeSocketObject").identifier
        link = outer.links.new
        y = 300 if sprites else 0
        gin = _node(outer, "NodeGroupInput", -1000, y)
        gout = _node(outer, "NodeGroupOutput", 2200 if sprites else 100, y)
        group = _node(outer, "GeometryNodeGroup", -600, y, "MME Pass: " + name[len("MME "):], node_tree=top, width=300)
        for old, new in mapping.items():
            source = next(s for s in gin.outputs if s.identifier == new)
            target = next(s for s in group.inputs if s.identifier == old)
            link(source, target)
        for socket in gin.outputs:
            socket.hide = not socket.is_linked
        if not sprites:
            link(group.outputs[0], gout.inputs[0])
            return outer, mapping
        frame = _node(outer, "NodeFrame", -200, 100, 'Add your nodes (Ensure "Enable Instance Editing" is checked)', shrink=False)
        frame.width = 1220
        frame.label_size = 16
        frame.height = 520
        instances = _node(outer, "GeometryNodeGroup", -170, 60, "Particles to Instances", frame, node_tree=mme_geo_instances_group(), width=220)
        delete = _node(outer, "GeometryNodeDeleteGeometry", 340, -45, "Your nodes here: particle instances", frame, domain="INSTANCE", mode="ALL")
        delete.inputs["Selection"].default_value = False
        real = _node(outer, "GeometryNodeRealizeInstances", 840, -240, "Realize Instances", frame)
        drop = _node(outer, "GeometryNodeRemoveAttribute", 1100, -100, "drop " + MME_ATTR_SPRITE)
        drop.inputs["Name"].default_value = MME_ATTR_SPRITE
        edit_node = _node(outer, "NodeGroupInput", 1300, 440, MME_EDIT_INPUT)
        for socket in edit_node.outputs:
            socket.hide = socket.identifier != edit_input
        edit_switch = _node(outer, "GeometryNodeSwitch", 1400, 300, "Enable Instance Editing?", input_type="GEOMETRY")
        swap = _node(outer, "GeometryNodeGroup", 1700, 300, "Particle Mesh (optional swap)", node_tree=mme_geo_swap_group(), width=260)
        mesh_node = _node(outer, "NodeGroupInput", 1450, 120, MME_PARTICLE_MESH_INPUT)
        for socket in mesh_node.outputs:
            socket.hide = socket.identifier != mesh_input
        link(next(s for s in edit_node.outputs if s.identifier == edit_input), edit_switch.inputs["Switch"])
        link(group.outputs[0], instances.inputs[0])
        link(group.outputs[0], edit_switch.inputs["False"])
        link(instances.outputs[0], delete.inputs["Geometry"])
        link(delete.outputs[0], real.inputs[0])
        link(real.outputs[0], drop.inputs[0])
        link(drop.outputs[0], edit_switch.inputs["True"])
        link(edit_switch.outputs[0], swap.inputs["Geometry"])
        link(next(s for s in mesh_node.outputs if s.identifier == mesh_input), swap.inputs[MME_PARTICLE_MESH_INPUT])
        link(swap.outputs[0], gout.inputs[0])
        return outer, mapping
    except Exception:
        bpy.data.node_groups.remove(outer)
        raise
