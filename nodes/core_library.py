import bpy
import os
import shutil

from ..common.constants import (
    ADDON_PACKAGE,
    MME_CORE_FILE,
    MME_KEY_PROP,
)
from ..common.log import log
from ..common.state import (
    _mme_core,
    _mme_groups,
)


# Where the shared node-group library lives. Installed as an extension, the add-on folder is replaced on
# update, so the file is copied once into the user extension folder
# and written there; otherwise the bundled data/ copy is used directly.
def mme_core_path():
    bundled = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", MME_CORE_FILE)
    package = ADDON_PACKAGE
    if not package.startswith("bl_ext."):
        return bundled
    try:
        folder = bpy.utils.extension_path_user(package, path="", create=True)
    except (ValueError, RuntimeError, OSError):
        return bundled
    target = os.path.join(folder, MME_CORE_FILE)
    if not os.path.isfile(target) and os.path.isfile(bundled):
        try:
            shutil.copyfile(bundled, target)
        except OSError:
            return bundled
    return target


# Names of the groups in the library, read once per path (loading a library just to list names is slow).
def mme_core_names():
    path = mme_core_path()
    if _mme_core["names"] is None or _mme_core["path"] != path:
        names = set()
        if os.path.isfile(path):
            try:
                with bpy.data.libraries.load(path, link=False) as (source, _target):
                    names = set(source.node_groups)
            except (OSError, RuntimeError) as error:
                log.warning("core library %s could not be read: %s", path, error)
        _mme_core["names"] = names
        _mme_core["path"] = path
    return _mme_core["names"]


# Appends one group from the library. A same-named group in the file is renamed out of the way, and a result
# whose key differs
# (an older version of the group) is thrown away so the caller rebuilds it.
def mme_core_append(name, key):
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
