import bpy
import os
import time
import types
from typing import NamedTuple, Tuple

from ..common.constants import (
    ACCESSORY_PREFIX,
    ACCESSORY_UNIT,
)
from ..common.log import log
from ..common.scene_utils import (
    accessory_collection,
    find_accessory_root,
    short_name,
)
from ..runtime.effect_files import mme_find_effect
from ..runtime.update import mme_update_scene
from ..xfile.cache import (
    parse_x_bytes,
    x_header_mode,
)
from .importer import import_accessory
from .mesh import (
    create_x_object,
    import_x_with_addon,
)


# Same fields as the accessory keys core hands over, so the keying code reads both alike.
class AccessoryKey(NamedTuple):
    frame: int
    visible: int
    parent_model: int
    parent_bone: int
    position: Tuple[float, float, float]
    rotation: Tuple[float, float, float]
    scale: float
    shadow: int
    opacity: float = 1.0


# Empty project for an import with no PMM: no models, no other accessories.
def standalone_project():
    return types.SimpleNamespace(models=[], accessories=[], accessory_error=None)


# Unique entry key for an accessory imported by hand, numbered when the same file is imported twice.
def standalone_key(settings, name):
    taken = {e.key for e in settings.accessories}
    key = "x:%s" % name
    number = 2
    while key in taken:
        key = "x:%s#%d" % (name, number)
        number += 1
    return key


# PMM-less accessory description with a single neutral key, so the normal import path can be reused.
def standalone_accessory(path, key):
    acc = types.SimpleNamespace()
    acc.index = -1
    acc.name = os.path.basename(path)
    acc.path = path
    acc.draw_order = 0
    acc.key = key
    acc.standalone = True
    acc.keys = [AccessoryKey(0, 1, -1, 0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 1.0, 0, 1.0)]
    return acc


# Import without an effect: mesh (or importer add-on result) under an empty placed at the 3D cursor.
def import_plain_accessory(context, path, unit):
    with open(path, "rb") as handle:
        raw = handle.read()
    name = os.path.basename(path)
    collection = context.collection or context.scene.collection
    if x_header_mode(raw) == "txt":
        xscene = parse_x_bytes(raw)
        for note in xscene.warnings:
            log.warning("accessory '%s': %s", name, note)
        child = create_x_object(short_name(name, 40) + "_mesh", xscene, os.path.dirname(path), context.scene.ninesync_fx)
        collection.objects.link(child)
        children = [child]
    else:
        children = import_x_with_addon(path)
        for child in children:
            for owner in list(child.users_collection):
                owner.objects.unlink(child)
            collection.objects.link(child)
    root = bpy.data.objects.new(ACCESSORY_PREFIX + short_name(name), None)
    root.empty_display_type = "PLAIN_AXES"
    collection.objects.link(root)
    root.location = context.scene.cursor.location
    for child in children:
        child.parent = root
        child.scale = (ACCESSORY_UNIT * unit,) * 3
    log.info("imported plain accessory '%s' (%d part(s), no MME effect)", name, len(children))
    return root


# File > Import path. With an effect it creates a standalone entry and goes through the normal accessory
# import, then updates the effect once so it is visible straight away;
# without one it is a plain import.
def import_standalone_accessory(context, path, unit, use_effect=True, effect_path=""):
    scene = context.scene
    settings = scene.ninesync_fx
    name = os.path.basename(path)
    probe = standalone_accessory(path, "")
    fx_path = ""
    if use_effect:
        fx_path = effect_path if effect_path and os.path.isfile(effect_path) else (mme_find_effect(probe, path, settings, None) or "")
    if not fx_path:
        return import_plain_accessory(context, path, unit)
    key = standalone_key(settings, name)
    entry = settings.accessories.add()
    entry.key = key
    entry.display = name
    entry.standalone = True
    entry.enabled = False
    entry.effect_checked = True
    entry.use_effect = True
    entry.effect_path = effect_path or ""
    collection = accessory_collection(scene, True)
    acc = standalone_accessory(path, key)
    started = time.time()
    if not import_accessory(scene, collection, standalone_project(), settings, acc, entry, unit):
        return None
    root = find_accessory_root(collection, key)
    if root is not None:
        root.location = scene.cursor.location
        mme_update_scene(scene, context.evaluated_depsgraph_get(), force=True)
    log.info("imported accessory '%s' with effect %s in %.1fs", name, os.path.basename(fx_path), time.time() - started)
    return root
