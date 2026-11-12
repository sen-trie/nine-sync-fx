import bpy
import os
import shutil

from ..common.constants import (
    MME_BAKE_LAYOUT_FOLDER,
    MME_GN_MODIFIER,
    MME_PASS_KEY_PROP,
)
from ..common.log import log


# Blender 5.3 resolves only {blend_name} in a bake directory (object, modifier, node and scene names make it fall back to
# a packed bake), so the per-pass part is written literally: the pass key already encodes the effect instance, technique,
# pass, subset and order, and does not change when an object is renamed. {blend_name} keeps two .blend files that share a
# bake folder (a Save As copy) from writing into each other's bakes; an unsaved file has no name to resolve, so its
# passes go straight under the bake folder.
def mme_pass_bake_template(settings, obj):
    key = obj.get(MME_PASS_KEY_PROP)
    if not key:
        return None
    base = settings.mme_bake_dir.replace("\\", "/").rstrip("/")
    if not bpy.data.filepath:
        return "%s/%s/%s/" % (base, MME_BAKE_LAYOUT_FOLDER, key)
    return "%s/{blend_name}/%s/%s/" % (base, MME_BAKE_LAYOUT_FOLDER, key)


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


# The folder that holds this pass's frames: its own folder when per-pass folders are in use, otherwise Blender's default
# of the modifier folder plus the bake id.
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


# Deletes the stored frames of one pass, only when they sit in that pass's own folder of the per-pass layout, so no other
# pass, effect or file can lose its bake.
def mme_drop_pass_bake(obj):
    modifier = obj.modifiers.get(MME_GN_MODIFIER)
    key = obj.get(MME_PASS_KEY_PROP)
    if modifier is None or not len(modifier.bakes) or not key or not modifier.bakes[0].use_custom_path:
        return False
    folder = mme_resolve_bake_path(modifier.bakes[0].directory)
    if not folder or os.path.basename(folder) != key or os.path.basename(os.path.dirname(folder)) != MME_BAKE_LAYOUT_FOLDER:
        return False
    if not os.path.isdir(folder):
        return False
    try:
        shutil.rmtree(folder)
    except OSError as error:
        log.warning("could not remove the bake folder of %s: %s", obj.name, error)
        return False
    log.info("[NineSync FX] REMOVE BAKE %s: deleted its own bake folder", obj.name)
    return True
