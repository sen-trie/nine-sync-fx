import bpy
import os

from ..common.constants import (
    MME_CORE_FILE,
    MME_CORE_VERSION,
    MME_KEY_PROP,
)
from ..common.state import (
    _mme_core,
    _mme_groups,
)
from ..fx.interp.executor import FxExec
from ..fx.program import FxProgram
from .backend_geo import MmeGeoBackend
from .backend_groups import MmeGroupEnv
from .backend_ps import MmePsBackend
from .geo_groups import (
    mme_geo_camera_group,
    mme_geo_clip_group,
    mme_geo_light_group,
    mme_geo_matrix_group,
)
from .library_store import (
    mme_group_closure,
    mme_key_version,
    mme_library_fallback,
    mme_library_lock,
    mme_library_read,
    mme_library_swap,
    mme_library_sync,
    mme_library_validate,
    mme_merge_files,
    mme_remove_quietly,
    mme_temp_path,
    mme_user_path,
)


# Overloads of mul that are worth shipping as ready-made groups for both node trees.
MME_STANDARD_INTRINSICS = [
    ("mul", [("f", (4,)), ("f", (4, 4))]),
    ("mul", [("f", (3,)), ("f", (4, 4))]),
    ("mul", [("f", (3,)), ("f", (3, 3))]),
    ("mul", [("f", (4, 4)), ("f", (4,))]),
    ("mul", [("f", (4, 4)), ("f", (4, 4))]),
    ("mul", [("f", (3, 3)), ("f", (3, 3))]),
]


# Builds every standard group (matrix multiplies for both trees, plus the geometry matrix, camera, light and
# clip groups) so they can be saved to the library.
# force discards existing ones first and skips the library, giving a clean rebuild.
def mme_build_standard_groups(force=False):
    if force:
        for group in list(bpy.data.node_groups):
            key = str(group.get(MME_KEY_PROP, ""))
            if key.startswith(("core:", "in:")):
                del group[MME_KEY_PROP]
                if group.users == 0:
                    bpy.data.node_groups.remove(group)
                else:
                    group.name = group.name + " (old)"
        _mme_groups.clear()
    before = _mme_core["stats"]["built"]
    _mme_core["skip_library"] = force
    try:
        program = FxProgram()
        program.params = {}
        for backend, idname in ((MmeGeoBackend, "GeometryNodeTree"), (MmePsBackend, "ShaderNodeTree")):
            scratch = bpy.data.node_groups.new("MME scratch", idname)
            try:
                B = backend(scratch)
                ex = FxExec(program, B, MmeGroupEnv(program))
                for name, types in MME_STANDARD_INTRINSICS:
                    B.intrinsic_group_tree(ex, name, types)
            finally:
                bpy.data.node_groups.remove(scratch)
        mme_geo_matrix_group()
        mme_geo_camera_group()
        mme_geo_light_group()
        mme_geo_clip_group()
    finally:
        _mme_core["skip_library"] = False
    return _mme_core["stats"]["built"] - before


# Writes all current-version shared groups, and the groups they nest, into the user library. The session's
# groups go to a scratch file first and are merged with the library on disk, so saving from a file that lacks
# some groups keeps them, and the result only replaces the library once it validates. Nothing here writes to
# the add-on folder: without a writable user library, this raises.
def mme_save_core_library(path=None):
    mme_library_sync()
    reason = mme_library_fallback()
    if reason:
        raise OSError("the library is read-only this session: %s" % reason)
    target = path or mme_user_path()
    if target is None:
        raise OSError("no user folder is available for %s" % MME_CORE_FILE)
    keep = {}
    for group in sorted(bpy.data.node_groups, key=lambda g: g.name):
        key = group.get(MME_KEY_PROP)
        if key and group.library is None and mme_key_version(key) == MME_CORE_VERSION:
            keep.setdefault(key, group)
    folder = os.path.dirname(target)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with mme_library_lock(target):
        previous = mme_library_read(target)
        if previous is not None and previous.version is not None and previous.version > MME_CORE_VERSION:
            raise OSError("the user library is newer than this add-on")
        session = mme_temp_path(target, "session")
        merged = mme_temp_path(target, "save")
        try:
            if keep:
                bpy.data.libraries.write(session, mme_group_closure(keep.values()), fake_user=True, compress=True)
            first, second = (session, target if previous is not None else None) if keep else (target, None)
            if not os.path.isfile(first):
                return 0
            count = mme_merge_files(first, second, MME_CORE_VERSION, merged)
            required = set(keep)
            if previous is not None:
                required |= previous.keys(MME_CORE_VERSION)
            mme_library_validate(merged, required, MME_CORE_VERSION)
            mme_library_swap(merged, target, previous)
        finally:
            mme_remove_quietly(session)
            mme_remove_quietly(merged)
    _mme_core["names"] = None
    return count
