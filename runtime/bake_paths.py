import bpy
import os

from ..common.constants import (
    MME_GN_MODIFIER,
)


def mme_blend_name():
    return os.path.splitext(os.path.basename(bpy.data.filepath))[0] if bpy.data.filepath else ""


def mme_resolve_bake_path(path):
    if not path:
        return ""
    if "{blend_name}" in path:
        name = mme_blend_name()
        if not name:
            return ""
        path = path.replace("{blend_name}", name)
    return os.path.normpath(bpy.path.abspath(path))


# The folder that holds this pass's frames: the bake's own path when it has one, otherwise Blender's default of the
# modifier folder plus the bake id.
def mme_pass_bake_folder(obj):
    modifier = obj.modifiers.get(MME_GN_MODIFIER)
    if modifier is None or not len(modifier.bakes):
        return ""
    bake = modifier.bakes[0]
    if bake.use_custom_path:
        return mme_resolve_bake_path(bake.directory)
    base = mme_resolve_bake_path(modifier.bake_directory)
    return os.path.join(base, str(bake.bake_id)) if base else ""


# Counts the frames of the range that have a stored meta record in the pass's own folder. Blender writes one
# <frame>_<sub>.json per frame; a bake that silently fell back to packed storage leaves none.
def mme_disk_frames(obj, start, end):
    folder = mme_pass_bake_folder(obj)
    meta = os.path.join(folder, "meta") if folder else ""
    if not meta or not os.path.isdir(meta):
        return 0, end - start + 1, folder
    present = {name.split("_", 1)[0] for name in os.listdir(meta) if name.endswith(".json")}
    found = sum(1 for frame in range(start, end + 1) if "%05d" % frame in present)
    return found, end - start + 1, folder
