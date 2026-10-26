# Removes every node that does not lead to the output; shaders often compute values they never use, which
# would otherwise bloat the tree.
def mme_prune_tree(tree, output=None):
    if output is None:
        output = next((n for n in tree.nodes if n.bl_idname in ("ShaderNodeOutputMaterial", "NodeGroupOutput")), None)
    if output is None:
        return
    upstream = {}
    for link in tree.links:
        upstream.setdefault(link.to_node.name, set()).add(link.from_node.name)
    keep = set()
    stack = [output.name]
    while stack:
        name = stack.pop()
        if name in keep:
            continue
        keep.add(name)
        stack.extend(upstream.get(name, ()))
    for node in [n for n in tree.nodes if n.name not in keep and n.bl_idname != "NodeFrame"]:
        tree.nodes.remove(node)


MME_NODE_HEIGHTS = {
    "ShaderNodeMath": 150.0,
    "FunctionNodeIntegerMath": 150.0,
    "FunctionNodeBooleanMath": 130.0,
    "FunctionNodeBitMath": 150.0,
    "GeometryNodeSwitch": 150.0,
    "ShaderNodeValue": 90.0,
    "ShaderNodeCombineXYZ": 130.0,
    "ShaderNodeSeparateXYZ": 130.0,
    "ShaderNodeSeparateColor": 150.0,
    "FunctionNodeSeparateColor": 150.0,
    "ShaderNodeTexImage": 280.0,
    "GeometryNodeImageTexture": 260.0,
    "NodeGroupInput": 70.0,
}


# Estimated node size for layout, since real sizes are not known before the tree is drawn.
def mme_node_size(node):
    height = MME_NODE_HEIGHTS.get(node.bl_idname)
    if height is None:
        rows = sum(1 for s in node.inputs if s.enabled and not s.hide)
        rows += sum(1 for s in node.outputs if s.enabled and not s.hide)
        height = 36.0 + 22.0 * rows
    return max(node.width, 140.0), height


# Places nodes without overlaps. Nodes created for the same HLSL statement form a block; blocks are ordered
# left to right by what feeds what,
# and inside a block nodes are layered by depth. Multi-node blocks get a labelled frame.
def mme_layout(tree, block_of, labels):
    nodes = [n for n in tree.nodes if n.bl_idname != "NodeFrame"]
    if not nodes:
        return
    names = {n.name: n for n in nodes}
    feeds = {n.name: set() for n in nodes}
    for link in tree.links:
        if link.from_node.name in names and link.to_node.name in names:
            feeds[link.to_node.name].add(link.from_node.name)
    group_of = {}
    for node in nodes:
        block = block_of.get(node.name)
        group_of[node.name] = "pre" if block is None else block
    block_feeds = {}
    for name, sources in feeds.items():
        mine = group_of[name]
        block_feeds.setdefault(mine, set())
        for source in sources:
            if group_of[source] != mine:
                block_feeds[mine].add(group_of[source])
    level = {}

    def block_level(block, trail=()):
        if block in level:
            return level[block]
        if block in trail:
            return 0
        found = 0
        for source in block_feeds.get(block, ()):
            found = max(found, block_level(source, trail + (block,)) + 1)
        level[block] = found
        return found

    for block in block_feeds:
        block_level(block)
    if "out" in level:
        level["out"] = max(level.values()) + (0 if len(level) == 1 else 1)
    local = {}

    def local_depth(name, trail=()):
        if name in local:
            return local[name]
        found = 0
        for source in feeds[name]:
            if group_of[source] == group_of[name] and source not in trail:
                found = max(found, local_depth(source, trail + (name,)) + 1)
        local[name] = found
        return found

    members = {}
    for node in nodes:
        local_depth(node.name)
        members.setdefault(group_of[node.name], []).append(node)
    sizes = {n.name: mme_node_size(n) for n in nodes}
    shapes = {}
    for block, block_nodes in members.items():
        columns = {}
        for node in block_nodes:
            columns.setdefault(local[node.name], []).append(node)
        widths = []
        heights = []
        for depth in sorted(columns):
            widths.append(max(sizes[n.name][0] for n in columns[depth]))
            heights.append(sum(sizes[n.name][1] + MME_NODE_GAP_Y for n in columns[depth]))
        shapes[block] = (columns, widths, sum(widths) + MME_NODE_GAP_X * (len(widths) - 1), max(heights))
    by_level = {}
    for block in members:
        by_level.setdefault(level.get(block, 0), []).append(block)

    def order(block):
        if block == "pre":
            return -1
        if block == "out":
            return 1 << 30
        return block

    x = 0.0
    frames = []
    where = {}
    for lvl in sorted(by_level):
        blocks = sorted(by_level[lvl], key=order)
        column_width = max(shapes[b][2] for b in blocks)
        y = 0.0
        for block in blocks:
            columns, widths, width, height = shapes[block]
            label = labels[block] if isinstance(block, int) and block < len(labels) else None
            framed = label is not None and len(members[block]) > 1
            top = y - (40.0 if framed else 0.0)
            cx = x
            for depth, column_w in zip(sorted(columns), widths):
                cy = top
                for node in columns[depth]:
                    where[node.name] = (cx, cy)
                    cy -= sizes[node.name][1] + MME_NODE_GAP_Y
                cx += column_w + MME_NODE_GAP_X
            if framed:
                frames.append((label, members[block]))
            elif label is not None and members[block] and not members[block][0].label and members[block][0].bl_idname != "NodeGroupInput":
                members[block][0].label = label
            y = top - height - MME_BLOCK_GAP_Y
        x += column_width + MME_BLOCK_GAP_X
    for label, block_nodes in frames:
        frame = tree.nodes.new("NodeFrame")
        frame.label = label
        frame.label_size = 14
        frame.shrink = True
        for node in block_nodes:
            node.parent = frame
    flat = []
    for node in tree.nodes:
        flat.extend(where.get(node.name, (0.0, 0.0)))
    tree.nodes.foreach_set("location", flat)


MME_NODE_GAP_X = 40.0


MME_NODE_GAP_Y = 16.0


MME_BLOCK_GAP_X = 120.0


MME_BLOCK_GAP_Y = 70.0
