import bpy

from ..common.constants import (
    MME_CONTROL_PREFIX,
    MME_GROUP_PROP,
    MME_OWNER_PROP,
)
from ..common.state import _mme_runtimes


# Removes every child of an accessory root (meshes, pass objects) together with the node groups only they
# used.
def remove_accessory_children(root):
    for child in list(root.children):
        remove_accessory_children_single(child)


# The effect node groups a pass object depends on: its top group and the groups nested in it that carry the
# owner tag. Shared library groups are left alone.
def mme_pass_groups(obj):
    groups = []
    for modifier in getattr(obj, "modifiers", []):
        if modifier.type != "NODES" or modifier.node_group is None:
            continue
        top = modifier.node_group
        if top.get(MME_OWNER_PROP) is None:
            continue
        groups.append(top)
        for node in top.nodes:
            if node.bl_idname == "GeometryNodeGroup" and node.node_tree is not None and node.node_tree.get(MME_OWNER_PROP) is not None:
                groups.append(node.node_tree)
    return groups


# Deletes groups left without users; a group already removed along with its owner is skipped.
def mme_free_groups(groups):
    for group in groups:
        try:
            if group.users == 0:
                bpy.data.node_groups.remove(group)
        except ReferenceError:
            pass


# Drops unused materials and groups whose owner key starts with the prefix, i.e. leftovers of accessories that
# no longer exist.
def mme_purge_owned(prefix):
    removed = 0
    for material in [m for m in bpy.data.materials if str(m.get(MME_OWNER_PROP, "")).startswith(prefix)]:
        if material.users:
            continue
        group = bpy.data.node_groups.get(material.get(MME_GROUP_PROP, ""))
        bpy.data.materials.remove(material)
        if group is not None and group.users == 0:
            bpy.data.node_groups.remove(group)
        removed += 1
    for group in [g for g in bpy.data.node_groups if str(g.get(MME_OWNER_PROP, "")).startswith(prefix)]:
        if group.users == 0:
            bpy.data.node_groups.remove(group)
    return removed


# Forgets everything effect-related on a root and its cached runtime when the effect is detached.
def mme_clear_root_props(root):
    for name in [k for k in root.keys() if k in ("mme_fx", "mme_x", "mme_unit", "mme_controls") or k.startswith(MME_CONTROL_PREFIX)]:
        del root[name]
    _mme_runtimes.pop(root.name, None)


# Removes one child object, then its node groups and its mesh. Materials made for effects stay, since they are
# reused on the next build.
def remove_accessory_children_single(child):
    data = child.data
    groups = mme_pass_groups(child)
    bpy.data.objects.remove(child, do_unlink=True)
    mme_free_groups(groups)
    if data is not None and getattr(data, "users", 1) == 0 and isinstance(data, bpy.types.Mesh):
        try:
            for material in list(data.materials):
                if material is not None and material.users <= 1 and material.get(MME_OWNER_PROP) is None:
                    bpy.data.materials.remove(material)
            bpy.data.meshes.remove(data)
        except (ReferenceError, RuntimeError):
            pass
