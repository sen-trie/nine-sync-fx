import os
import tempfile

from ..common.constants import (
    MME_CACHE_FOLDER,
    MME_CACHE_LIMIT,
    MME_CACHE_LEGACY_FOLDER,
)
from ..common.log import log
from ..nodes.library_store import mme_user_folder


# Pre-cull, strip and geometry caches live beside the user's node library (the extension's user folder), so a
# temp clean-up does not bring back cold builds; the system temp folder is the fallback without one.
def mme_cache_folder():
    base = mme_user_folder()
    if base:
        return os.path.join(base, MME_CACHE_FOLDER)
    return os.path.join(tempfile.gettempdir(), MME_CACHE_LEGACY_FOLDER)


def mme_cache_path(name):
    return os.path.join(mme_cache_folder(), name)


# The temp folder older versions used; only Clear reads it.
def mme_legacy_cache_folder():
    return os.path.join(tempfile.gettempdir(), MME_CACHE_LEGACY_FOLDER)


def mme_cache_entries(folder):
    found = []
    try:
        names = os.listdir(folder)
    except OSError:
        return found
    for name in names:
        path = os.path.join(folder, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if os.path.isfile(path):
            found.append((st.st_mtime, st.st_size, path))
    return found


# A cache hit counts as a use, so the size limit removes what was used least recently.
def mme_cache_touch(path):
    try:
        os.utime(path, None)
    except OSError:
        pass


# Keeps the cache folder under MME_CACHE_LIMIT bytes by deleting the least recently used files.
def mme_cache_trim(limit=MME_CACHE_LIMIT):
    entries = sorted(mme_cache_entries(mme_cache_folder()))
    total = sum(size for _time, size, _path in entries)
    removed = 0
    for _time, size, path in entries:
        if total <= limit:
            break
        try:
            os.remove(path)
        except OSError:
            continue
        total -= size
        removed += 1
    if removed:
        log.info("effect cache: removed %d old file(s) to stay under %d MB", removed, limit // (1024 * 1024))
    return removed


# Deletes every cache file (pre-cull, strip and geometry), here and in the temp folder older versions used.
def mme_cache_clear():
    removed = 0
    folders = {mme_cache_folder(), mme_legacy_cache_folder()}
    for folder in folders:
        for _time, _size, path in mme_cache_entries(folder):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed
