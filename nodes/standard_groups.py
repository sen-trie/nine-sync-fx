import bpy
import os

from ..common.constants import (
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
from .core_library import (
    mme_core_names,
    mme_core_path,
)
from .geo_groups import (
    mme_geo_camera_group,
    mme_geo_clip_group,
    mme_geo_light_group,
    mme_geo_matrix_group,
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


# Writes all current-version shared groups, and the groups they nest, to the library file. Groups already in
# the file are kept by appending them first,
# so saving from a file that lacks some of them does not remove them from the library.
def mme_save_core_library(path=None):
    path = path or mme_core_path()
    keep = {}
    for group in bpy.data.node_groups:
        key = group.get(MME_KEY_PROP)
        if key and group.library is None and str(key).endswith(":v%d" % MME_CORE_VERSION):
            keep.setdefault(key, group)
    appended = []
    missing = [n for n in mme_core_names() if bpy.data.node_groups.get(n) is None]
    if missing and os.path.isfile(path):
        with bpy.data.libraries.load(path, link=False) as (_source, target):
            target.node_groups = missing
        appended = [g for g in target.node_groups if g is not None]
        for group in appended:
            key = group.get(MME_KEY_PROP)
            if key:
                keep.setdefault(key, group)
    blocks = set(keep.values())
    for group in list(blocks):
        for node in group.nodes:
            if node.bl_idname in ("GeometryNodeGroup", "ShaderNodeGroup") and node.node_tree is not None:
                blocks.add(node.node_tree)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    bpy.data.libraries.write(path, blocks, fake_user=True, compress=True)
    for group in appended:
        if group.users == 0:
            bpy.data.node_groups.remove(group)
    _mme_core["names"] = None
    return len(keep)
