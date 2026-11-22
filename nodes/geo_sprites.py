import bpy
import numpy as np

from ..common.log import log
from ..common.constants import (
    MME_ATTR_SPRITE,
    MME_CORE_VERSION,
    MME_BAKE_NODE,
    MME_LIVE_INPUT,
    MME_EDIT_INPUT,
    MME_GRAPH_VERSION,
    MME_OWNER_PROP,
    MME_PARTICLE_MESH_INPUT,
    MME_SHADER_ONLY_ATTRS,
)
from .core_library import mme_core_group
from .user_frames import (
    PASS_NODE,
    mme_capture_frames,
    mme_free_owned,
    mme_keep_previous,
    mme_previous_wrapper,
    mme_restore_frames,
    mme_stage_frame,
    mme_take_stash,
)
from .names import mme_group_name

SPRITE_CORNERS = 4

WRAP_PROP = "mme_wrap"
SPRITES_PROP = "mme_wrap_sprites"
INPUTS_NODE = "NineSync Inputs"
PARAMS_NODE = "NineSync Pass Inputs"
INSTANCES_NODE = "NineSync Particles to Instances"
SWAP_NODE = "NineSync Particle Mesh"


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


# The pass's one Bake node, named so Effects > Bake can find it among any Bake nodes the user adds. It is the last node
# of the wrapper, so a bake holds everything the pass makes, the user's instance edits and the Particle Mesh swap included.
def _bake(outer, x, y, source, live_input):
    bake = _node(outer, "GeometryNodeBake", x, y + 100, "Bake (Effects > Bake)")
    bake.name = MME_BAKE_NODE
    bake.bake_items.new("GEOMETRY", "Geometry")
    outer.links.new(source, bake.inputs[0])
    live_node = _node(outer, "NodeGroupInput", x - 370, y + 255, MME_LIVE_INPUT)
    for socket in live_node.outputs:
        socket.hide = socket.identifier != live_input
    live = next(s for s in live_node.outputs if s.identifier == live_input)
    viewport = _node(outer, "GeometryNodeIsViewport", x - 370, y + 165, "viewport only")
    both = _node(outer, "FunctionNodeBooleanMath", x - 90, y + 285, "Geometry Node Editing, viewport only", operation="AND")
    outer.links.new(live, both.inputs[0])
    outer.links.new(viewport.outputs[0], both.inputs[1])
    for node in (live_node, viewport, both):
        node.width = 230
    show = _node(outer, "GeometryNodeSwitch", x + 190, y + 85, "Live nodes instead of the bake", input_type="GEOMETRY", width=230)
    outer.links.new(both.outputs[0], show.inputs["Switch"])
    outer.links.new(bake.outputs[0], show.inputs["False"])
    outer.links.new(source, show.inputs["True"])
    return show.outputs[0]


# Wraps a finished pass tree in the tree the modifier uses, so each pass is one node group. For sprite passes the wrapper
# also holds the place for the user's instance nodes, Realize Instances and the Particle Mesh swap, all before the pass's
# Bake node. Its inputs repeat the pass's inputs; the returned map takes a pass input identifier to the wrapper's, for the
# drivers and defaults, followed by the identifier of the Enable Instance Editing input (None for a pass without one).
# A rebuild reuses the earlier wrapper and only swaps the pass in it, so everything the user added stays as it was; the
# capture and restore of the frames is the fallback for a wrapper of another layout. A held (stashed) wrapper may still
# be used by the pass it replaces, which is removed once the new pass is in place.
def mme_wrap_pass(top, name, owner, sprites):
    stashed = mme_take_stash(owner)
    previous = stashed or mme_previous_wrapper(owner, name)
    if previous is not None and (stashed is not None or previous.users == 0):
        try:
            reused = _reuse_wrapper(previous, top, owner, sprites)
        except (RuntimeError, TypeError, ValueError, KeyError, StopIteration) as error:
            log.warning("could not reuse the earlier tree of %s: %s", name, error)
            mme_keep_previous(previous)
            return _wrap_pass(top, name, owner, sprites)
        if reused is not None:
            return reused
    kept, lost = mme_capture_frames(previous) if previous is not None else ({}, 0)
    result = _wrap_pass(top, name, owner, sprites)
    if previous is not None:
        if (mme_restore_frames(result[0], kept) if kept else 0) + lost:
            mme_keep_previous(previous)
        else:
            mme_free_owned(previous)
        result[0].name = name
    return result


def _input_specs(top, sprites):
    sockets = [i for i in top.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"]
    sockets.sort(key=lambda i: int(i.identifier.rsplit("_", 1)[-1]))
    specs = [(i.name, i.socket_type, i) for i in sockets]
    specs.append((MME_LIVE_INPUT, "NodeSocketBool", None))
    if sprites:
        specs.append((MME_EDIT_INPUT, "NodeSocketBool", None))
        specs.append((MME_PARTICLE_MESH_INPUT, "NodeSocketObject", None))
    return sockets, specs


def _copy_socket(item, made):
    for attribute in ("default_value", "min_value", "max_value", "description", "subtype"):
        if hasattr(item, attribute) and hasattr(made, attribute):
            try:
                setattr(made, attribute, getattr(item, attribute))
            except (TypeError, ValueError, AttributeError):
                continue


# Makes the wrapper's inputs match the new pass's, keeping every input whose name and type stay (and so its identifier,
# its links and the user's own Group Input nodes) and putting them in the pass's order.
def _sync_interface(outer, specs):
    old = [i for i in outer.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"]
    used = set()
    ordered = []
    for name, socket_type, source in specs:
        match = next((i for i in old if i.identifier not in used and i.name == name and i.socket_type == socket_type), None)
        if match is None:
            match = outer.interface.new_socket(name, in_out="INPUT", socket_type=socket_type)
        used.add(match.identifier)
        if source is not None:
            _copy_socket(source, match)
        ordered.append(match)
    for item in old:
        if item.identifier not in used:
            outer.interface.remove(item)
    outputs = len([i for i in outer.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "OUTPUT"])
    for position, item in enumerate(ordered):
        outer.interface.move(item, outputs + position)
    return [i.identifier for i in ordered]


# Reuses an earlier wrapper of the same layout: points its MME Pass node at the new pass, matches the inputs and links
# the pass back in. Returns None when the wrapper cannot be reused (another layout or sprite kind, scaffold removed).
def _reuse_wrapper(outer, top, owner, sprites):
    if outer.get(WRAP_PROP) != MME_GRAPH_VERSION or bool(outer.get(SPRITES_PROP)) != bool(sprites):
        return None
    group, gin, params = outer.nodes.get(PASS_NODE), outer.nodes.get(INPUTS_NODE), outer.nodes.get(PARAMS_NODE)
    if group is None or group.bl_idname != "GeometryNodeGroup":
        return None
    if any(n is None or n.bl_idname != "NodeGroupInput" for n in (gin, params)):
        return None
    if sprites and (outer.nodes.get(INSTANCES_NODE) is None or outer.nodes.get(SWAP_NODE) is None):
        return None
    sockets, specs = _input_specs(top, sprites)
    geometry_in = next((i.identifier for i in sockets if i.socket_type == "NodeSocketGeometry"), None)
    source = next((link.from_socket for s in group.inputs if s.type == "GEOMETRY" for link in s.links), None)
    targets = [link.to_socket for s in group.outputs for link in s.links]
    old_top = group.node_tree
    identifiers = _sync_interface(outer, specs)
    mapping = {item.identifier: identifiers[k] for k, item in enumerate(sockets)}
    live_input = identifiers[len(sockets)]
    edit_input = identifiers[len(sockets) + 1] if sprites else None
    group.node_tree = top
    group.label = "MME Pass: " + outer.name[len("MME "):]
    for socket in group.inputs:
        for link in list(socket.links):
            outer.links.remove(link)
    for socket in group.outputs:
        for link in list(socket.links):
            outer.links.remove(link)
    link = outer.links.new
    for old, new in mapping.items():
        target = next(s for s in group.inputs if s.identifier == old)
        if old == geometry_in and source is not None:
            link(source, target)
        else:
            link(next(s for s in params.outputs if s.identifier == new), target)
    for target in targets:
        link(group.outputs[0], target)
    _hide_unlinked(gin, params)
    if sprites:
        outer.nodes[INSTANCES_NODE].node_tree = mme_geo_instances_group()
        outer.nodes[SWAP_NODE].node_tree = mme_geo_swap_group()
    outer[MME_OWNER_PROP] = owner
    if old_top is not None and old_top != top:
        mme_free_owned(old_top)
    return outer, mapping, edit_input, live_input


def _deselect(tree):
    for node in tree.nodes:
        node.select = False


def _hide_unlinked(*nodes):
    for node in nodes:
        for socket in node.outputs:
            socket.hide = not socket.is_linked


def _wrap_pass(top, name, owner, sprites):
    outer = bpy.data.node_groups.new(name, "GeometryNodeTree")
    outer[MME_OWNER_PROP] = owner
    outer[WRAP_PROP] = MME_GRAPH_VERSION
    outer[SPRITES_PROP] = bool(sprites)
    try:
        sockets, _specs = _input_specs(top, sprites)
        mapping = {}
        for item in sockets:
            made = outer.interface.new_socket(item.name, in_out="INPUT", socket_type=item.socket_type)
            _copy_socket(item, made)
            mapping[item.identifier] = made.identifier
        outer.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        live = outer.interface.new_socket(MME_LIVE_INPUT, in_out="INPUT", socket_type="NodeSocketBool")
        live.default_value = False
        live.description = "Shows the live nodes instead of the bake so edits to the pass show at once. Ignored in renders, which keep using the bake"
        live_input = live.identifier
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
        gin = _node(outer, "NodeGroupInput", -1275, y - 30)
        gin.name = INPUTS_NODE
        params = _node(outer, "NodeGroupInput", -815, y - 130)
        params.name = PARAMS_NODE
        bake_x = 2120 if sprites else 305
        gout = _node(outer, "NodeGroupOutput", bake_x + 460, y + 65)
        group = _node(outer, "GeometryNodeGroup", -600, y, "MME Pass: " + name[len("MME "):], node_tree=top, width=300)
        group.name = PASS_NODE
        geometry_in = next((i.identifier for i in sockets if i.socket_type == "NodeSocketGeometry"), None)
        for old, new in mapping.items():
            target = next(s for s in group.inputs if s.identifier == old)
            if old == geometry_in:
                source = mme_stage_frame(outer, "source", -990, y + 15, next(s for s in gin.outputs if s.identifier == new), owner)
            else:
                source = next(s for s in params.outputs if s.identifier == new)
            link(source, target)
        _hide_unlinked(gin, params)
        if not sprites:
            finished = mme_stage_frame(outer, "final", -160, y, group.outputs[0], owner)
            link(_bake(outer, bake_x, y, finished, live_input), gout.inputs[0])
            _deselect(outer)
            return outer, mapping, None, live_input
        instances = _node(outer, "GeometryNodeGroup", -200, -100, "Particles to Instances", node_tree=mme_geo_instances_group(), width=220)
        instances.name = INSTANCES_NODE
        edited = mme_stage_frame(outer, "instances", 200, -100, instances.outputs[0], owner)
        real = _node(outer, "GeometryNodeRealizeInstances", 585, -80, "Realize Instances")
        drop = _node(outer, "GeometryNodeRemoveAttribute", 840, 140, "drop " + MME_ATTR_SPRITE)
        drop.inputs["Name"].default_value = MME_ATTR_SPRITE
        edit_node = _node(outer, "NodeGroupInput", 845, 330, MME_EDIT_INPUT)
        for socket in edit_node.outputs:
            socket.hide = socket.identifier != edit_input
        edit_switch = _node(outer, "GeometryNodeSwitch", 1065, 300, "Enable Instance Editing?", input_type="GEOMETRY")
        swap = _node(outer, "GeometryNodeGroup", 1255, 295, "Particle Mesh (optional swap)", node_tree=mme_geo_swap_group(), width=260)
        swap.name = SWAP_NODE
        mesh_node = _node(outer, "NodeGroupInput", 1080, 135, MME_PARTICLE_MESH_INPUT)
        for socket in mesh_node.outputs:
            socket.hide = socket.identifier != mesh_input
        link(next(s for s in edit_node.outputs if s.identifier == edit_input), edit_switch.inputs["Switch"])
        link(group.outputs[0], instances.inputs[0])
        link(group.outputs[0], edit_switch.inputs["False"])
        link(edited, real.inputs[0])
        link(real.outputs[0], drop.inputs[0])
        link(drop.outputs[0], edit_switch.inputs["True"])
        link(edit_switch.outputs[0], swap.inputs["Geometry"])
        link(next(s for s in mesh_node.outputs if s.identifier == mesh_input), swap.inputs[MME_PARTICLE_MESH_INPUT])
        finished = mme_stage_frame(outer, "final", 1655, y, swap.outputs[0], owner)
        link(_bake(outer, bake_x, y, finished, live_input), gout.inputs[0])
        _deselect(outer)
        return outer, mapping, edit_input, live_input
    except Exception:
        bpy.data.node_groups.remove(outer)
        raise
