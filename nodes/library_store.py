import bpy
import contextlib
import glob
import os
import re
import shutil
import time

from ..common.constants import (
    ADDON_PACKAGE,
    MME_CORE_FILE,
    MME_CORE_VERSION,
    MME_KEY_PROP,
    MME_LIBRARY_FOLDER,
    MME_LIBRARY_LOCK_STALE,
)
from ..common.log import log
from ..common.state import _mme_core


def mme_bundled_path():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", MME_CORE_FILE)


# Folder that survives add-on updates: the extension's user folder, or Blender's datafiles folder for a
# development install. None when neither can be created, which leaves the bundled file as a read-only library.
def mme_user_folder():
    try:
        if ADDON_PACKAGE.startswith("bl_ext."):
            return bpy.utils.extension_path_user(ADDON_PACKAGE, path="", create=True)
        return bpy.utils.user_resource("DATAFILES", path=MME_LIBRARY_FOLDER, create=True)
    except (ValueError, RuntimeError, OSError) as error:
        log.warning("no user folder for %s: %s", MME_CORE_FILE, error)
        return None


def mme_user_path():
    folder = mme_user_folder()
    return os.path.join(folder, MME_CORE_FILE) if folder else None


# The library this session reads. Never touches the disk beyond a stat, so panels may call it while drawing.
def mme_core_path():
    if _mme_core.get("fallback"):
        return mme_bundled_path()
    target = mme_user_path()
    if target is not None and os.path.isfile(target):
        return target
    return mme_bundled_path()


def mme_library_fallback():
    return _mme_core.get("fallback")


def mme_key_version(key):
    match = re.search(r":v(\d+)$", str(key or ""))
    return int(match.group(1)) if match else None


class MmeLibraryBusy(OSError):
    pass


class MmeLibraryInfo:
    def __init__(self, names):
        self.names = names
        self.versions = {v for v in (mme_key_version(k) for k in names.values()) if v is not None}
        self.version = max(self.versions) if self.versions else None

    def keys(self, version=None):
        return {k for k in self.names.values() if k and (version is None or mme_key_version(k) == version)}


# Reads names and keys in a throwaway data block, so inspecting a file never adds to the open .blend. A file
# that cannot load, or has a group node pointing at nothing, reads as None.
def mme_library_read(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with bpy.data.temp_data() as temp:
            with temp.libraries.load(path, link=False) as (source, target):
                target.node_groups = list(source.node_groups)
            names = {}
            for group in temp.node_groups:
                names[group.name] = group.get(MME_KEY_PROP)
                for node in group.nodes:
                    if node.bl_idname in ("GeometryNodeGroup", "ShaderNodeGroup") and node.node_tree is None:
                        log.warning("%s: group %s has a broken nested group", path, group.name)
                        return None
            return MmeLibraryInfo(names)
    except (OSError, RuntimeError, ReferenceError) as error:
        log.warning("%s could not be read: %s", path, error)
        return None


# What to do with the user library. Files are compared by the version in their keys, never by timestamp; a
# mixed file counts as its newest version.
def mme_library_action(bundled, user):
    if bundled is None or bundled.version is None:
        return "keep"
    if user is None or user.version is None or bundled.version > user.version:
        return "replace"
    if bundled.version < user.version:
        return "keep"
    if bundled.keys(bundled.version) - user.keys(bundled.version):
        return "merge"
    return "keep"


# Cross-process guard around every change to the library. Two Blenders that both append new groups would
# otherwise lose whichever swap came first. A lock left by a crash expires.
@contextlib.contextmanager
def mme_library_lock(target):
    lock = target + ".lock"
    with contextlib.suppress(OSError):
        if time.time() - os.path.getmtime(lock) > MME_LIBRARY_LOCK_STALE:
            os.remove(lock)
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise MmeLibraryBusy("%s is being updated by another Blender" % MME_CORE_FILE) from None
    os.close(handle)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(lock)


def mme_temp_path(target, tag):
    return "%s.%s-%d.tmp.blend" % (target, tag, os.getpid())


def mme_remove_quietly(path):
    with contextlib.suppress(OSError):
        os.remove(path)


# Swaps a finished file in. The old library reaches .bak through its own temporary file, and only a library
# worth keeping does, so a corrupt or empty one can never push out a good backup.
def mme_library_swap(temp, target, previous):
    if previous is not None and previous.version is not None:
        backup = target + ".bak"
        staged = backup + ".tmp"
        shutil.copyfile(target, staged)
        os.replace(staged, backup)
    os.replace(temp, target)


def mme_group_closure(groups):
    found = set()
    pending = list(groups)
    while pending:
        group = pending.pop()
        if group in found:
            continue
        found.add(group)
        for node in group.nodes:
            if node.bl_idname in ("GeometryNodeGroup", "ShaderNodeGroup") and node.node_tree is not None:
                pending.append(node.node_tree)
    return found


def mme_plain_name(name):
    return re.sub(r"\.\d{3}$", "", name)


# Current-version names to take from a file, one per key: first name in sorted order, so a file holding
# 'X' and 'X.001' with one key always gives the same result.
def mme_wanted_names(info, version, skip=()):
    chosen = {}
    for name in sorted(info.names):
        key = info.names[name]
        if key and mme_key_version(key) == version and key not in skip and key not in chosen:
            chosen[key] = name
    return chosen


def mme_load_groups(path, names):
    with bpy.data.libraries.load(path, link=False) as (_source, target):
        target.node_groups = sorted(names)
    return [g for g in target.node_groups if g is not None]


# Writes first's current-version groups plus second's groups with new keys. The key decides identity, so first
# wins a duplicate and nested references are repointed at the survivor. Node trees can only be relinked in
# bpy.data, not in temp_data, so session groups with colliding names step aside for this one synchronous call
# and get their names back afterwards. Returns the number of keys written.
def mme_merge_files(first, second, version, out):
    info_a = mme_library_read(first)
    if info_a is None:
        raise OSError("%s could not be read" % first)
    info_b = mme_library_read(second) if second else None
    wanted_a = mme_wanted_names(info_a, version)
    wanted_b = mme_wanted_names(info_b, version, wanted_a) if info_b is not None else {}
    colliding = set(info_a.names) | (set(info_b.names) if info_b is not None else set())
    colliding |= {mme_plain_name(n) for n in colliding}
    aside = []
    for group in bpy.data.node_groups:
        if group.name in colliding:
            aside.append((group, group.name))
    before = set(bpy.data.node_groups)
    try:
        for index, (group, _name) in enumerate(aside):
            group.name = "MME aside %d" % index
        loaded = mme_load_groups(first, wanted_a.values())
        if wanted_b:
            loaded += mme_load_groups(second, wanted_b.values())
        survivors = {}
        fresh = [g for g in bpy.data.node_groups if g not in before]
        fresh.sort(key=lambda g: (g not in loaded, loaded.index(g) if g in loaded else 0, g.name))
        for group in fresh:
            key = group.get(MME_KEY_PROP)
            if not key:
                continue
            kept = survivors.get(key)
            if kept is None:
                survivors[key] = group
            else:
                group.user_remap(kept)
        current = [g for key, g in survivors.items() if mme_key_version(key) == version]
        blocks = mme_group_closure(current)
        for group in sorted(blocks, key=lambda g: g.name):
            plain = mme_plain_name(group.name)
            holder = bpy.data.node_groups.get(plain)
            if plain != group.name and (holder is None or holder not in blocks):
                if holder is not None:
                    holder.name = plain + " (dropped)"
                group.name = plain
        bpy.data.libraries.write(out, blocks, fake_user=True, compress=True)
        return len(current)
    finally:
        spent = [g for g in bpy.data.node_groups if g not in before]
        if spent:
            bpy.data.batch_remove(spent)
        for group, name in aside:
            group.name = name


# A finished file must hold every key it promised, at the expected version, with nested groups intact.
def mme_library_validate(path, keys, version):
    info = mme_library_read(path)
    if info is None or info.version != version:
        raise OSError("the new %s did not validate" % MME_CORE_FILE)
    missing = set(keys) - info.keys(version)
    if missing:
        raise OSError("the new %s lost %d group(s)" % (MME_CORE_FILE, len(missing)))
    return info


def mme_library_install(bundled, target, previous, version):
    temp = mme_temp_path(target, "install")
    try:
        shutil.copyfile(bundled, temp)
        mme_library_validate(temp, (), version)
        mme_library_swap(temp, target, previous)
    finally:
        mme_remove_quietly(temp)


# Same-version update: the bundled groups win duplicates, and groups only the user has, such as ones grown
# from Thunder that are expensive to rebuild, stay.
def mme_library_merge(bundled, target, previous, version):
    temp = mme_temp_path(target, "merge")
    try:
        count = mme_merge_files(bundled, target, version, temp)
        mme_library_validate(temp, previous.keys(version), version)
        mme_library_swap(temp, target, previous)
        return count
    finally:
        mme_remove_quietly(temp)


# Unreadable or empty user library: move a damaged file out of the way, then go back to .bak if that is good.
def mme_library_recover(target):
    if os.path.isfile(target) and mme_library_read(target) is None:
        os.replace(target, target + ".corrupt")
        log.warning("%s was unreadable; kept as %s.corrupt", target, MME_CORE_FILE)
    backup = target + ".bak"
    info = mme_library_read(backup)
    if os.path.isfile(target) or info is None or info.version is None:
        return mme_library_read(target)
    temp = mme_temp_path(target, "restore")
    try:
        shutil.copyfile(backup, temp)
        os.replace(temp, target)
    finally:
        mme_remove_quietly(temp)
    log.info("%s restored from its backup", MME_CORE_FILE)
    return mme_library_read(target)


def mme_library_clear_leftovers(target):
    for path in glob.glob(glob.escape(target) + ".*.tmp.blend"):
        mme_remove_quietly(path)


def mme_library_set_fallback(reason):
    _mme_core["fallback"] = reason
    _mme_core["names"] = None
    log.warning("%s: using the bundled copy for this session: %s", MME_CORE_FILE, reason)


def mme_library_apply(bundled_path, target):
    bundled = mme_library_read(bundled_path)
    user = mme_library_read(target)
    if user is None or user.version is None:
        user = mme_library_recover(target) or user
    action = mme_library_action(bundled, user)
    if action == "replace":
        mme_library_install(bundled_path, target, user, bundled.version)
        log.info("%s installed from the bundled copy (version %s, was %s)", MME_CORE_FILE, bundled.version, user.version if user else None)
    elif action == "merge":
        count = mme_library_merge(bundled_path, target, user, bundled.version)
        log.info("%s merged with the bundled copy: %d shared group(s)", MME_CORE_FILE, count)
    if user is not None and user.version is not None and user.version > MME_CORE_VERSION:
        mme_library_set_fallback("the user library is newer (version %d) than this add-on (version %d) and was left untouched" % (user.version, MME_CORE_VERSION))


# Brings the user library up to date, once per session. Any failure leaves the user's file alone and falls back
# to the bundled copy until Blender restarts. Called from a timer and before the first library use, because
# bpy.data is not available during register().
def mme_library_sync(force=False):
    if _mme_core.get("synced") and not force:
        return
    _mme_core["synced"] = True
    _mme_core["fallback"] = None
    _mme_core["names"] = None
    target = mme_user_path()
    if target is None:
        mme_library_set_fallback("no user folder is available")
        return
    try:
        with mme_library_lock(target):
            mme_library_clear_leftovers(target)
            mme_library_apply(mme_bundled_path(), target)
    except MmeLibraryBusy as error:
        if os.path.isfile(target):
            log.info("%s", error)
        else:
            mme_library_set_fallback(str(error))
    except (OSError, RuntimeError) as error:
        mme_library_set_fallback(str(error))
