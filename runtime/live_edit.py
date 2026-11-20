from ..common.constants import MME_GN_MODIFIER, MME_LIVE_INPUT


def _entry(obj):
    modifier = obj.modifiers.get(MME_GN_MODIFIER)
    group = modifier.node_group if modifier is not None else None
    if group is None:
        return None
    for item in group.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name == MME_LIVE_INPUT:
            return getattr(modifier.properties.inputs, item.identifier, None)
    return None


# Sets the pass input that shows the live nodes instead of the bake. It is a plain modifier value, not a driver, and sits
# after the Bake node, so switching it never changes a bake's inputs.
def mme_set_live_edit(obj, value):
    entry = _entry(obj)
    if entry is not None:
        entry.value = bool(value)


# A bake only records while the Bake node is evaluated, so live editing is off for the bake and put back after it.
def mme_live_suspend(obj):
    entry = _entry(obj)
    if entry is None:
        return False
    was = bool(entry.value)
    entry.value = False
    return was


def mme_live_restore(obj, was):
    entry = _entry(obj)
    if entry is not None:
        entry.value = bool(was)
