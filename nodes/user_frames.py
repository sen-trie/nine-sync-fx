import bpy

from ..common.constants import MME_OWNER_PROP
from ..common.log import log

FRAME_PREFIX = "NineSync Frame "

FRAME_TEXT = {
    "source": "Edit Source Mesh",
    "final": "Edit Final Geometry",
}

_SKIP = {
    "rna_type", "name", "label", "location", "location_absolute", "width", "height", "dimensions", "select", "hide",
    "parent", "inputs", "outputs", "internal_links", "type", "bl_idname", "bl_label", "bl_description", "bl_icon",
    "bl_static_type", "bl_width_default", "bl_width_min", "bl_width_max", "bl_height_default", "bl_height_min",
    "bl_height_max", "is_active_output", "panel_states", "show_options", "show_preview", "show_texture", "warning_propagation",
}
_FIRST = ("node_tree", "data_type", "input_type", "domain", "mode", "operation")
_ZONES = (
    "GeometryNodeRepeatInput", "GeometryNodeRepeatOutput", "GeometryNodeSimulationInput", "GeometryNodeSimulationOutput",
    "GeometryNodeForeachGeometryElementInput", "GeometryNodeForeachGeometryElementOutput",
)
_POOLS = ("node_groups", "objects", "materials", "collections", "images", "textures")


def _end_names(key):
    return "%s%s in" % (FRAME_PREFIX, key), "%s%s out" % (FRAME_PREFIX, key)


# A frame with a pass-through link inside it, like the particle-instance frame: the user adds nodes on the link (Blender
# inserts them) and the pass connects to the frame's two ends. Returns the geometry output after the frame.
def mme_stage_frame(outer, key, x, y, source, width=620, height=200):
    frame = outer.nodes.new("NodeFrame")
    frame.name = FRAME_PREFIX + key
    frame.label = FRAME_TEXT[key]
    frame.label_size = 16
    frame.shrink = False
    frame.width = width
    frame.height = height
    frame.location = (x, y)
    ends = []
    for name, offset in zip(_end_names(key), (40, width - 80)):
        node = outer.nodes.new("NodeReroute")
        node.name = name
        node.label = name[len(FRAME_PREFIX):]
        node.socket_idname = "NodeSocketGeometry"
        node.location = (x + offset, y - height // 2)
        node.parent = frame
        ends.append(node)
    outer.links.new(source, ends[0].inputs[0])
    outer.links.new(ends[0].outputs[0], ends[1].inputs[0])
    return ends[1].outputs[0]


def _inside(node, frame):
    if node.parent is not None:
        return node.parent == frame
    x, y = node.location
    fx, fy = frame.location
    return fx <= x <= fx + frame.width and fy - frame.height <= y <= fy


def _relative(node, frame):
    if node.parent == frame:
        return tuple(node.location)
    return (node.location[0] - frame.location[0], node.location[1] - frame.location[1])


def _plain(value):
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if isinstance(value, bpy.types.ID):
        return ("id", value.__class__.__name__, value.name)
    try:
        return [_plain(v) for v in value]
    except TypeError:
        return None


def _capture_node(node, frame):
    props = {}
    for prop in node.bl_rna.properties:
        key = prop.identifier
        if key in _SKIP or prop.is_readonly or prop.type == "COLLECTION":
            continue
        try:
            props[key] = _plain(getattr(node, key))
        except (AttributeError, RuntimeError, TypeError):
            continue
    inputs = {}
    for socket in node.inputs:
        if not socket.is_linked and hasattr(socket, "default_value"):
            inputs[socket.identifier] = _plain(socket.default_value)
    return {
        "idname": node.bl_idname, "name": node.name, "label": node.label, "offset": _relative(node, frame),
        "width": node.width, "mute": node.mute, "props": props, "inputs": inputs,
    }


# The nodes the user added inside the stage frames of an earlier build of a pass, with the links among them and to the two
# ends of each frame. Returns (data, lost): lost counts what cannot be carried over (zone nodes, links to nodes outside).
def mme_capture_frames(tree):
    data = {}
    lost = 0
    for key in FRAME_TEXT:
        frame = tree.nodes.get(FRAME_PREFIX + key)
        if frame is None:
            continue
        ends = _end_names(key)
        members = [
            n for n in tree.nodes
            if n.name != frame.name and n.name not in ends and n.bl_idname != "NodeFrame" and _inside(n, frame)
        ]
        keep = [n for n in members if n.bl_idname not in _ZONES]
        lost += len(members) - len(keep)
        kept = {n.name for n in keep}
        names = kept | set(ends)
        links = []
        for link in tree.links:
            a, b = link.from_node.name, link.to_node.name
            if a in names and b in names:
                if not (a == ends[0] and b == ends[1]):
                    links.append((a, link.from_socket.identifier, b, link.to_socket.identifier))
            elif (a in kept) or (b in kept):
                lost += 1
        if keep:
            data[key] = {"nodes": [_capture_node(n, frame) for n in keep], "links": links}
    return data, lost


def _restore_value(node, key, value):
    if isinstance(value, tuple) and value and value[0] == "id":
        pool = {"NodeTree": "node_groups", "Object": "objects", "Material": "materials", "Collection": "collections", "Image": "images", "Texture": "textures"}.get(value[1])
        value = getattr(bpy.data, pool).get(value[2]) if pool in _POOLS else None
    setattr(node, key, value)


def _restore_node(tree, frame, item):
    node = tree.nodes.new(item["idname"])
    failed = 0
    ordered = [k for k in _FIRST if k in item["props"]] + [k for k in item["props"] if k not in _FIRST]
    for key in ordered:
        try:
            _restore_value(node, key, item["props"][key])
        except (AttributeError, TypeError, ValueError, RuntimeError):
            if key in _FIRST:
                failed += 1
    for socket in node.inputs:
        if socket.identifier in item["inputs"] and hasattr(socket, "default_value"):
            try:
                socket.default_value = item["inputs"][socket.identifier]
            except (AttributeError, TypeError, ValueError, RuntimeError):
                failed += 1
    node.name = item["name"]
    node.label = item["label"]
    node.width = item["width"]
    node.mute = item["mute"]
    node.parent = frame
    node.location = item["offset"]
    return node, failed


# Puts captured nodes back into the same frames of a new tree. Returns how many pieces could not be restored.
def mme_restore_frames(tree, data):
    failed = 0
    for key, saved in data.items():
        frame = tree.nodes.get(FRAME_PREFIX + key)
        if frame is None:
            failed += len(saved["nodes"])
            continue
        ends = _end_names(key)
        made = {name: tree.nodes.get(name) for name in ends}
        for item in saved["nodes"]:
            try:
                node, bad = _restore_node(tree, frame, item)
            except RuntimeError:
                failed += 1
                continue
            failed += bad
            made[item["name"]] = node
        joined = False
        for a, sa, b, sb in saved["links"]:
            na, nb = made.get(a), made.get(b)
            source = next((s for s in na.outputs if s.identifier == sa), None) if na is not None else None
            target = next((s for s in nb.inputs if s.identifier == sb), None) if nb is not None else None
            if source is None or target is None:
                failed += 1
                continue
            tree.links.new(source, target)
            joined = joined or a in ends or b in ends
        if joined:
            for link in list(made[ends[0]].outputs[0].links):
                if link.to_node == made[ends[1]]:
                    tree.links.remove(link)
    return failed


_STASH = {}


# A forced rebuild removes the old pass objects before building, so their frame contents are set aside by owner key first.
def mme_stash_pass(obj):
    for modifier in getattr(obj, "modifiers", []):
        tree = modifier.node_group if modifier.type == "NODES" else None
        if tree is not None and tree.get(MME_OWNER_PROP) and (FRAME_PREFIX + "source") in tree.nodes:
            _STASH[tree[MME_OWNER_PROP]] = mme_capture_frames(tree)


def mme_take_stash(owner):
    return _STASH.pop(owner, None)


def mme_clear_stash():
    _STASH.clear()


# The earlier wrapper tree of a pass, if it still holds stage frames.
def mme_previous_wrapper(owner, name):
    found = [
        g for g in bpy.data.node_groups
        if g.bl_idname == "GeometryNodeTree" and g.get(MME_OWNER_PROP) == owner and g.name.startswith(name)
        and (FRAME_PREFIX + "source") in g.nodes
    ]
    found.sort(key=lambda g: g.name)
    return found[-1] if found else None


# Keeps the old wrapper (renamed, with a fake user) when some of the user's nodes could not be carried over.
def mme_keep_previous(previous):
    previous.name = "%s (your nodes, previous build)" % previous.name
    previous.use_fake_user = True
    if MME_OWNER_PROP in previous:
        del previous[MME_OWNER_PROP]
    log.warning("some stage nodes could not be carried over; the earlier tree is kept as '%s'", previous.name)
