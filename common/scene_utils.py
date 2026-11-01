import bpy
import re

from .constants import (
    ACCESSORY_COLLECTION,
    ACCESSORY_OWNER_PROP,
    PMM_FPS,
)
from .log import log


# Key for a PMM model that stays stable when two models share a name (index first).
def model_key(model):
    return "%d:%s" % (model.index, model.name)


# Tags every area for redraw; used after changes made outside operators.
def redraw_all():
    wm = bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def find_armature(root):
    for child in root.children_recursive:
        if child.type == "ARMATURE":
            return child
    return None


# Looks collections up by name across the whole file, so reused or renamed collections are found again instead
# of duplicated.
def ensure_collection(scene, name):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        log.info("created collection '%s'", name)
    if collection not in scene.collection.children_recursive:
        scene.collection.children.link(collection)
    return collection


# Moves rather than links, so an object never ends up in both the scene and the accessory collection.
def move_to_collection(obj, collection):
    for owner in list(obj.users_collection):
        if owner != collection:
            owner.objects.unlink(obj)
    if obj.name not in collection.objects:
        collection.objects.link(obj)


def find_rig_root(collection, mmd_type):
    for obj in collection.all_objects:
        if obj.type == "EMPTY" and getattr(obj, "mmd_type", "") == mmd_type:
            return obj
    return None


def child_of_type(root, object_type):
    for child in root.children:
        if child.type == object_type:
            return child
    return None


# mmd_tools renames bones, so the PMM (Japanese) name is matched through mmd_bone.name_j before falling back
# to the plain name.
def pose_bone_name(armature, mmd_name):
    for pose_bone in armature.pose.bones:
        mmd_bone = getattr(pose_bone, "mmd_bone", None)
        if mmd_bone is not None and mmd_bone.name_j == mmd_name:
            return pose_bone.name
    if mmd_name in armature.pose.bones:
        return mmd_name
    return None


# Cuts to a UTF-8 byte limit, not a character count: Blender names are limited in bytes and Japanese names are
# 3 bytes per character.
def short_name(text, limit=48):
    out = ""
    for char in text:
        if len((out + char).encode("utf-8")) > limit:
            break
        out += char
    return out or "accessory"


# Entries from core carry a key; standalone entries do not, so fall back to index and name.
def accessory_key(acc):
    key = getattr(acc, "key", None)
    if key:
        return key
    return "%d:%s" % (acc.index, acc.name)


# Socket names differ between Blender versions, so the first matching name wins.
def node_input(node, *names):
    for name in names:
        found = node.inputs.get(name)
        if found is not None:
            return found
    return None


# Looks a socket up by identifier because names are not unique on some nodes.
def node_socket(node, identifier, outputs=False):
    for socket in node.outputs if outputs else node.inputs:
        if socket.identifier == identifier:
            return socket
    return None


# Roots are tagged with the accessory key in a custom property, so renaming the object does not break the
# link.
def find_accessory_root(collection, key):
    for obj in collection.objects:
        if obj.get("mmd_bridge_accessory") == key:
            return obj
    return None


# One accessory collection per scene. Order of preference: the pointer saved on the scene, a collection
# stamped with this scene's name,
# then an unstamped MMD_Accessories from an older file. On create, collections owned by other scenes are
# unlinked so duplicated scenes do not share one.
def accessory_collection(scene, create=False):
    if scene is None:
        return None
    settings = getattr(scene, "ninesync_fx", None)
    tree = [scene.collection] + list(scene.collection.children_recursive)
    pointer = getattr(settings, "accessory_collection", None) if settings is not None else None
    if pointer is not None and pointer in tree:
        return pointer
    found = None
    for collection in tree[1:]:
        if collection.get(ACCESSORY_OWNER_PROP) == scene.name:
            found = collection
            break
    if found is None:
        for collection in tree[1:]:
            if collection.get(ACCESSORY_OWNER_PROP) is None and collection.name.split(".")[0] == ACCESSORY_COLLECTION:
                found = collection
                break
    if found is None and not create:
        return None
    if found is None:
        for collection in tree[1:]:
            owner = collection.get(ACCESSORY_OWNER_PROP)
            if owner and owner != scene.name and bpy.data.scenes.get(owner) is not None:
                for parent in tree:
                    if collection.name in parent.children:
                        parent.children.unlink(collection)
                log.info("unlinked accessory collection '%s' (belongs to scene '%s') from scene '%s'", collection.name, owner, scene.name)
        found = bpy.data.collections.new(ACCESSORY_COLLECTION)
        scene.collection.children.link(found)
        log.info("created accessory collection '%s' for scene '%s'", found.name, scene.name)
    if not create:
        return found
    if found.get(ACCESSORY_OWNER_PROP) != scene.name:
        try:
            found[ACCESSORY_OWNER_PROP] = scene.name
        except (TypeError, AttributeError, RuntimeError):
            pass
    if settings is not None and hasattr(settings, "accessory_collection") and settings.accessory_collection != found:
        try:
            settings.accessory_collection = found
        except (TypeError, AttributeError, RuntimeError):
            pass
    return found


# Scene frame rate including fps_base (29.97 is 30 / 1.001).
def scene_fps(scene):
    render = scene.render
    return render.fps / (render.fps_base or 1.0)


# PMM is always 30 fps; this tells whether keys need retiming.
def fps_differs(scene):
    return abs(scene_fps(scene) - PMM_FPS) > 0.01


# Collapses whitespace and cuts with an ellipsis so long paths and messages fit a UI row.
def short_text(text, limit=80):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# Add-on keys differ between legacy add-ons and extensions (bl_ext.repo.name), so only the last segment is
# compared.
def addon_loaded(modules):
    return bool(loaded_addon_names(modules))


# Full enabled module names (e.g. bl_ext.blender_org.io_directx_x), needed to import a submodule of the add-on.
def loaded_addon_names(modules):
    try:
        names = list(bpy.context.preferences.addons.keys())
    except Exception:
        return []
    return [name for name in names if name.rsplit(".", 1)[-1] in modules]


# A model link is usable only while its root is still in the file and has an armature to follow.
def target_usable(obj):
    return (
        obj is not None
        and len(obj.users_collection) > 0
        and find_armature(obj) is not None
    )


# With auto scale the import scale follows the first usable mmd_tools model (empty display size / 5).
def current_scale(settings):
    if settings.auto_scale:
        for item in settings.targets:
            if item.enabled and target_usable(item.target):
                size = getattr(item.target, "empty_display_size", 0.0)
                if size > 0.0:
                    return size * 0.2
    return settings.scale


def is_mmd_root(self, obj):
    return getattr(obj, "mmd_type", "") == "ROOT"


# Matches a PMM model to an mmd_tools root: Japanese name, English name, object name, then a loose substring
# match.
# The order stops a partial match from beating an exact one.
def find_root_for(model):
    roots = [
        o
        for o in bpy.data.objects
        if getattr(o, "mmd_type", "") == "ROOT" and target_usable(o)
    ]
    log.debug(
        "auto-match for PMM model '%s' / '%s' among roots: %s",
        model.name,
        model.name_en,
        [(o.name, o.mmd_root.name, o.mmd_root.name_e) for o in roots],
    )
    for obj in roots:
        if model.name and obj.mmd_root.name == model.name:
            return obj
    for obj in roots:
        if model.name_en and obj.mmd_root.name_e == model.name_en:
            return obj
    for obj in roots:
        if obj.name in (model.name, model.name_en):
            return obj
    for obj in roots:
        for name in (obj.mmd_root.name, obj.mmd_root.name_e, obj.name):
            if name and model.name and (name in model.name or model.name in name):
                return obj
    return None


# Replaces user names in paths before text goes into the support report.
def redact_paths(text):
    text = re.sub(r"([A-Za-z]:[\\/]+Users[\\/]+)[^\\/\"']+", r"\1<user>", text)
    text = re.sub(r"(/(?:home|Users)/)[^/\"'\s]+", r"\1<user>", text)
    return text
