import bpy

from ..common.digest import digest
from .bake_node import mme_find_bake

_LAYOUT_ONLY = {
    "rna_type",
    "name",
    "label",
    "location",
    "location_absolute",
    "width",
    "height",
    "dimensions",
    "select",
    "hide",
    "show_options",
    "show_preview",
    "show_texture",
    "color",
    "use_custom_color",
    "color_tag",
    "parent",
    "inputs",
    "outputs",
    "internal_links",
    "node_tree",
    "warning_propagation",
    "panel_states",
    "shrink",
    "label_size",
    "text",
    "is_active_output",
    "bl_idname",
    "bl_label",
    "bl_description",
    "bl_icon",
    "bl_static_type",
    "bl_width_default",
    "bl_width_min",
    "bl_width_max",
    "bl_height_default",
    "bl_height_min",
    "bl_height_max",
    "type",
}


def _value(raw):
    if isinstance(raw, (bool, int, float, str)) or raw is None:
        return raw
    if isinstance(raw, bpy.types.ID):
        return "id:" + raw.name
    try:
        return [_value(v) for v in raw]
    except TypeError:
        return str(raw)


def _node_state(node):
    state = [node.bl_idname]
    for prop in node.bl_rna.properties:
        key = prop.identifier
        if key in _LAYOUT_ONLY or prop.type in ("POINTER", "COLLECTION"):
            continue
        try:
            state.append((key, _value(getattr(node, key))))
        except (AttributeError, RuntimeError, TypeError):
            continue
    for socket in node.inputs:
        if socket.is_linked or not hasattr(socket, "default_value"):
            continue
        state.append((socket.identifier, _value(socket.default_value)))
    return state


# A digest of what a node tree computes: node types, settings, unlinked input values, links and nested groups by content.
# Names, labels, positions and colours are left out, so tidying a tree does not change it and editing a node does.
def mme_tree_digest(tree, memo=None):
    memo = {} if memo is None else memo
    pointer = tree.as_pointer()
    if pointer in memo:
        return memo[pointer]
    memo[pointer] = "cycle"
    index = {node.as_pointer(): number for number, node in enumerate(tree.nodes)}
    nodes = []
    for node in tree.nodes:
        entry = _node_state(node)
        if node.bl_idname == "GeometryNodeGroup" and node.node_tree is not None:
            entry.append(("tree", mme_tree_digest(node.node_tree, memo)))
        nodes.append(entry)
    links = sorted(
        (index[l.from_node.as_pointer()], l.from_socket.identifier, index[l.to_node.as_pointer()], l.to_socket.identifier, bool(l.is_muted))
        for l in tree.links
    )
    sockets = [(i.in_out, i.name, i.socket_type, getattr(i, "identifier", "")) for i in tree.interface.items_tree if i.item_type == "SOCKET"]
    memo[pointer] = digest([nodes, links, sockets])
    return memo[pointer]


def _reach(tree, starts):
    found = set()
    seen = set()
    stack = list(starts)
    while stack:
        socket = stack.pop()
        for link in socket.links:
            node = link.from_node
            if node.bl_idname == "NodeGroupInput":
                found.add(link.from_socket.identifier)
                continue
            key = node.as_pointer()
            if key in seen:
                continue
            seen.add(key)
            stack.extend(node.inputs)
    return found


# What the pass's managed Bake node depends on: the digest of the tree that holds it (nested groups included) and the
# identifiers of the modifier inputs that reach it, so an input that only feeds nodes after the Bake node never changes
# the bake. Returns (None, None) when the modifier has no managed Bake node.
def mme_bake_scope(modifier):
    top = modifier.node_group if modifier is not None else None
    found = mme_find_bake(top)
    if found is None:
        return None, None
    tree, bake, path = found
    reach = _reach(tree, list(bake.inputs))
    trees = [top] + [node.node_tree for node in path[:-1]]
    for parent, group in zip(reversed(trees), reversed(path)):
        starts = [socket for socket in group.inputs if socket.identifier in reach]
        reach = _reach(parent, starts)
    return mme_tree_digest(tree), reach
