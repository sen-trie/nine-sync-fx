import bpy
import os

from ..common.constants import MME_KEY_PROP
from ..common.log import log
from ..common.state import (
    _mme_core,
    _mme_groups,
)
from .library_store import (
    mme_core_path,
    mme_library_sync,
)


# Names of the groups in the library, read once per file version (loading a library just to list names is
# slow). The modified time is part of the check because the file can be edited in a second Blender.
def mme_core_names():
    path = mme_core_path()
    stamp = (path, os.path.getmtime(path) if os.path.isfile(path) else None)
    if _mme_core["names"] is None or _mme_core["path"] != stamp:
        names = set()
        if os.path.isfile(path):
            try:
                with bpy.data.libraries.load(path, link=False) as (source, _target):
                    names = set(source.node_groups)
            except (OSError, RuntimeError) as error:
                log.warning("core library %s could not be read: %s", path, error)
        _mme_core["names"] = names
        _mme_core["path"] = stamp
    return _mme_core["names"]


# Appends one group from the library. A same-named group in the file is renamed out of the way, and a result
# whose key differs
# (an older version of the group) is thrown away so the caller rebuilds it.
def mme_core_append(name, key):
    mme_library_sync()
    if name not in mme_core_names():
        return None
    blocking = bpy.data.node_groups.get(name)
    if blocking is not None:
        blocking.name = name + " (stale)"
    try:
        with bpy.data.libraries.load(mme_core_path(), link=False) as (_source, target):
            target.node_groups = [name]
    except (OSError, RuntimeError) as error:
        log.warning("core library group %s could not be appended: %s", name, error)
        return None
    loaded = target.node_groups[0] if target.node_groups else None
    if loaded is None:
        return None
    if loaded.get(MME_KEY_PROP) != key:
        bpy.data.node_groups.remove(loaded)
        return None
    for group in bpy.data.node_groups:
        if group.get(MME_KEY_PROP) and group.library is None:
            _mme_groups.setdefault(group[MME_KEY_PROP], group.name)
    return loaded


# Single entry point for shared groups, looked up by content key: already used in this file, then appended
# from the library, then built with 'build'.
# The stats counters show which of the three happened.
def mme_core_group(name, tree_type, key, build):
    found = _mme_groups.get(key)
    group = bpy.data.node_groups.get(found) if found else None
    if group is not None and group.get(MME_KEY_PROP) == key and group.bl_idname == tree_type:
        return group
    for group in bpy.data.node_groups:
        if group.get(MME_KEY_PROP) == key and group.bl_idname == tree_type and group.library is None:
            _mme_groups[key] = group.name
            _mme_core["stats"]["file"] += 1
            return group
    group = None if _mme_core.get("skip_library") else mme_core_append(name, key)
    if group is not None and group.bl_idname == tree_type:
        _mme_groups[key] = group.name
        _mme_core["stats"]["library"] += 1
        return group
    group = bpy.data.node_groups.new(name, tree_type)
    group[MME_KEY_PROP] = key
    try:
        build(group)
    except Exception:
        bpy.data.node_groups.remove(group)
        raise
    _mme_groups[key] = group.name
    _mme_core["stats"]["built"] += 1
    return group


# Zeroes the built/file/library counters reported after an import.
def mme_group_stats_reset():
    _mme_core["stats"] = {"built": 0, "file": 0, "library": 0}
