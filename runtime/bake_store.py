import json
import os
import shutil
import time
import uuid

from ..common.constants import (
    MME_BAKE_STORE_FOLDER,
    MME_BAKE_STORE_VERSION,
)
from ..common.log import log
from .cache_store import (
    mme_bake_limit,
    mme_cache_folder,
)

COMPLETE = "complete.json"
STALE_TEMP_SECONDS = 24 * 3600


def mme_store_folder():
    return os.path.join(mme_cache_folder(), MME_BAKE_STORE_FOLDER)


def mme_in_store(path):
    if not path:
        return False
    root = os.path.abspath(mme_store_folder())
    try:
        return os.path.commonpath([os.path.abspath(path), root]) == root
    except ValueError:
        return False


def _frames_present(folder, start, end):
    meta = os.path.join(folder, "meta")
    try:
        present = {name.split("_", 1)[0] for name in os.listdir(meta) if name.endswith(".json")}
    except OSError:
        return 0
    return sum(1 for frame in range(start, end + 1) if "%05d" % frame in present)


# The folder of a finished shared bake that covers the frame range, or None. A folder counts only when its marker was
# written (the bake finished before it was renamed into place) and every frame still has its record.
def mme_store_find(key, start, end):
    folder = os.path.join(mme_store_folder(), key)
    try:
        with open(os.path.join(folder, COMPLETE), "r", encoding="utf-8") as handle:
            marker = json.load(handle)
    except (OSError, ValueError):
        return None
    if marker.get("v") != MME_BAKE_STORE_VERSION or marker.get("frames") != [int(start), int(end)]:
        return None
    if _frames_present(folder, int(start), int(end)) < int(end) - int(start) + 1:
        return None
    return folder


def mme_store_touch(folder):
    try:
        os.utime(os.path.join(folder, COMPLETE), None)
    except OSError:
        pass


# A private folder to bake into; mme_store_publish renames it to the key once the bake has finished, so a bake that is
# interrupted, or that another Blender is running for the same key, never shows up as a finished one.
def mme_store_begin(key):
    folder = os.path.join(mme_store_folder(), "_tmp_%s_%d_%s" % (key, os.getpid(), uuid.uuid4().hex[:8]))
    os.makedirs(folder, exist_ok=True)
    return folder


def mme_store_discard(folder):
    shutil.rmtree(folder, ignore_errors=True)


# Marks the finished bake and renames it to its key. When another process published the same key first, that copy is kept
# and this one is dropped. Returns the final folder, or None when it could not be stored.
def mme_store_publish(folder, key, start, end):
    final = os.path.join(mme_store_folder(), key)
    try:
        with open(os.path.join(folder, COMPLETE), "w", encoding="utf-8") as handle:
            json.dump({"v": MME_BAKE_STORE_VERSION, "key": key, "frames": [int(start), int(end)], "created": int(time.time())}, handle)
        try:
            os.rename(folder, final)
        except OSError:
            if mme_store_find(key, start, end) is not None:
                mme_store_discard(folder)
                return final
            shutil.rmtree(final, ignore_errors=True)
            os.rename(folder, final)
    except OSError as error:
        log.warning("shared bake %s could not be stored: %s", key, error)
        return None
    mme_store_trim()
    return final


def _tree_size(folder):
    total = 0
    for base, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


# Deletes the least recently used shared bakes until the store fits its limit, and temp folders left by interrupted bakes.
# A project that still points at a deleted bake finds its frames missing and rebakes.
def mme_store_trim(limit=None):
    limit = mme_bake_limit() if limit is None else limit
    root = mme_store_folder()
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    entries = []
    now = time.time()
    for name in names:
        folder = os.path.join(root, name)
        if not os.path.isdir(folder):
            continue
        if name.startswith("_tmp_"):
            try:
                if now - os.stat(folder).st_mtime > STALE_TEMP_SECONDS:
                    mme_store_discard(folder)
            except OSError:
                pass
            continue
        try:
            used = os.stat(os.path.join(folder, COMPLETE)).st_mtime
        except OSError:
            used = os.stat(folder).st_mtime
        entries.append((used, name, folder))
    sizes = {folder: _tree_size(folder) for _used, _name, folder in entries}
    total = sum(sizes.values())
    removed = 0
    for _used, _name, folder in sorted(entries):
        if total <= limit:
            break
        mme_store_discard(folder)
        total -= sizes[folder]
        removed += 1
    if removed:
        log.info("shared bakes: removed %d old bake(s) to stay under %d MB", removed, limit // (1024 * 1024))
    return removed


def mme_store_clear():
    root = mme_store_folder()
    removed = 0
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for name in names:
        folder = os.path.join(root, name)
        if os.path.isdir(folder):
            mme_store_discard(folder)
            removed += 1
    return removed


def mme_store_size():
    root = mme_store_folder()
    return _tree_size(root) if os.path.isdir(root) else 0
