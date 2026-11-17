from ..common.constants import (
    MME_BAKE_NODE,
    MME_BAKE_NODE_LEGACY_LABEL,
    MME_GN_MODIFIER,
)


def _is_managed(node):
    return node is not None and (node.name == MME_BAKE_NODE or node.label == MME_BAKE_NODE_LEGACY_LABEL)


# The modifier's entry for the Bake node the pass owns, or None when the tree has none. Bake nodes the user adds are
# listed by Blender too and are never returned.
def mme_managed_bake(modifier):
    if modifier is None:
        return None
    for bake in modifier.bakes:
        if _is_managed(getattr(bake, "node", None)):
            return bake
    return None


def mme_pass_modifier(obj):
    return obj.modifiers.get(MME_GN_MODIFIER)


# The Bake node of a pass and the chain of group nodes leading to it from the modifier's tree (empty when it sits in the
# top tree itself).
def mme_find_bake(tree, seen=None):
    seen = set() if seen is None else seen
    if tree is None or tree.as_pointer() in seen:
        return None
    seen.add(tree.as_pointer())
    for node in tree.nodes:
        if node.bl_idname == "GeometryNodeBake" and _is_managed(node):
            return tree, node, []
    for node in tree.nodes:
        if node.bl_idname == "GeometryNodeGroup" and node.node_tree is not None:
            found = mme_find_bake(node.node_tree, seen)
            if found is not None:
                return found[0], found[1], [node] + found[2]
    return None
