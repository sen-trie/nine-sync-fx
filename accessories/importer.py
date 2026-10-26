import bpy
import json
import os
import time
import traceback

from ..common.constants import (
    ACCESSORY_PREFIX,
    ACCESSORY_UNIT,
    MME_DRAW_PROP,
)
from ..common.controls import (
    mme_ensure_use_props,
    mme_load_controls,
)
from ..common.log import log
from ..common.scene_utils import (
    accessory_key,
    find_accessory_root,
    short_name,
    short_text,
)
from ..common.state import _mme_runtimes
from ..fx.errors import FxError
from ..fx.loader import fx_parse_file
from ..runtime.build_effect import mme_build_effect
from ..runtime.cleanup import (
    mme_clear_root_props,
    remove_accessory_children,
    remove_accessory_children_single,
)
from ..runtime.controls_resolve import (
    mme_key_morph_props,
    mme_resolve_controls,
)
from ..runtime.effect_files import mme_find_effect
from ..runtime.runtime import mme_effect_stamp
from ..xfile.cache import (
    parse_x_bytes,
    x_header_mode,
)
from .files import find_accessory_file
from .keying import key_accessory
from .mesh import (
    create_x_object,
    import_x_with_addon,
    remove_subset_faces,
)
from .parenting import apply_accessory_parent


# Locates and parses the accessory's effect and resolves its controllers. The first time it also decides
# whether effects are used for the entry at all;
# returns (fx_path, program, controls, morph tracks), or Nones when there is no effect or it cannot be read.
def effect_setup(project, settings, acc, entry, path, root):
    if not settings.mme_effects:
        return None, None, {}, {}
    if entry is not None and not entry.effect_checked:
        entry.effect_checked = True
        entry.use_effect = True
        entry.use_effect = bool(mme_find_effect(acc, path, settings, entry))
        if not entry.use_effect:
            log.info("accessory '%s': no MME effect (.fx) found; effects are off for it", acc.name)
    fx_path = mme_find_effect(acc, path, settings, entry)
    if not fx_path:
        return None, None, {}, {}
    try:
        program = fx_parse_file(fx_path)
        controls, tracks = mme_resolve_controls(program, project, settings, acc, root)
    except (FxError, OSError, UnicodeError) as error:
        log.error("accessory '%s': effect %s could not be read: %s", acc.name, fx_path, error)
        if entry is not None:
            entry.effect_note = "Effect error: %s" % short_text(error, 60)
        return None, None, {}, {}
    return fx_path, program, controls, tracks


# Imports or updates one accessory. A stamp (files, effect, backend, density, scale, controller kinds) says
# whether anything needs rebuilding; if not, only keys,
# parent and controls are refreshed. The effect build is tried first and any failure falls back to the plain
# mesh, so a bad effect never loses the accessory.
def import_accessory(scene, collection, project, settings, acc, entry, unit):
    path = find_accessory_file(acc, settings)
    if path is None:
        log.warning(
            "accessory '%s': file not found (PMM path %s); set an Accessory Folder",
            acc.name,
            acc.path,
        )
        if entry is not None:
            entry.effect_note = "File not found"
        return False
    key = accessory_key(acc)
    root = find_accessory_root(collection, key)
    if root is None:
        root = bpy.data.objects.new(ACCESSORY_PREFIX + short_name(acc.name), None)
        root.empty_display_type = "PLAIN_AXES"
        root.empty_display_size = 1.0
        root["mmd_bridge_accessory"] = key
        collection.objects.link(root)
        log.info("created accessory root '%s'", root.name)
    fx_path, program, controls, tracks = effect_setup(project, settings, acc, entry, path, root)
    stamp = "%s|%s" % (path, os.path.getmtime(path))
    if fx_path:
        stamp += "|%s|%s|density=%.4f|unit=%.6f|%s" % (
            mme_effect_stamp(fx_path, path, program),
            settings.mme_backend,
            settings.mme_density,
            unit,
            json.dumps(sorted((k, v.get("kind")) for k, v in controls.items())),
        )
    if root.get("mmd_bridge_stamp") != stamp or not root.children:
        remove_accessory_children(root)
        mme_clear_root_props(root)
        with open(path, "rb") as handle:
            raw = handle.read()
        parsed = {}

        def x_scene():
            if "scene" not in parsed:
                parsed["scene"] = parse_x_bytes(raw)
                for note in parsed["scene"].warnings:
                    log.warning("accessory '%s': %s", acc.name, note)
            return parsed["scene"]

        mode = x_header_mode(raw)
        folder = os.path.dirname(path)
        children = None
        if fx_path and mode != "txt":
            log.warning(
                "accessory '%s': effects need a text .x file; %s is %s, so it is imported without its effect",
                acc.name,
                os.path.basename(path),
                mode,
            )
        elif fx_path:
            started = time.time()
            try:
                children, uncovered, _tracks, total = mme_build_effect(
                    scene, collection, settings, acc, root, path, fx_path, unit, controls
                )
                if uncovered:
                    static = create_x_object(
                        short_name(acc.name, 40) + "_mesh", x_scene(), folder, settings
                    )
                    collection.objects.link(static)
                    remove_subset_faces(static, set(range(len(static.data.materials))) - set(uncovered))
                    children.append(static)
                note = "%s: %d of %d pass(es) running" % (
                    os.path.basename(fx_path),
                    len([c for c in children if c.get(MME_DRAW_PROP)]),
                    total,
                )
                log.info(
                    "accessory '%s': effect %s built in %.1fs (%s)",
                    acc.name,
                    fx_path,
                    time.time() - started,
                    note,
                )
                if entry is not None:
                    entry.effect_note = note
            except Exception as error:
                log.error(
                    "accessory '%s': effect %s failed, importing the plain mesh instead: %s\n%s",
                    acc.name,
                    fx_path,
                    error,
                    traceback.format_exc(),
                )
                for child in list(root.children):
                    remove_accessory_children_single(child)
                mme_clear_root_props(root)
                if entry is not None:
                    entry.effect_note = "Effect failed: %s" % short_text(error, 60)
                children = None
        elif entry is not None:
            entry.effect_note = ""
        if children is None:
            if mode == "txt":
                child = create_x_object(
                    short_name(acc.name, 40) + "_mesh", x_scene(), folder, settings
                )
                collection.objects.link(child)
                children = [child]
                log.info(
                    "accessory '%s': %d mesh part(s) from %s",
                    acc.name,
                    len(x_scene().meshes),
                    path,
                )
            else:
                children = import_x_with_addon(path)
                for child in children:
                    for owner in list(child.users_collection):
                        owner.objects.unlink(child)
                    collection.objects.link(child)
        for child in children:
            child.parent = root
        root["mmd_bridge_stamp"] = stamp
    for child in root.children:
        child.scale = (ACCESSORY_UNIT * unit,) * 3
    key_accessory(root, acc, unit)
    if root.get("mme_fx"):
        root["mme_controls"] = json.dumps(controls)
        mme_ensure_use_props(root, controls)
        root["mme_unit"] = unit
        mme_key_morph_props(root, tracks)
        runtime = _mme_runtimes.get(root.name)
        if runtime is not None:
            runtime.controls_runtime = {
                name for name, desc in mme_load_controls(root).items() if desc.get("kind") != "const"
            }
    apply_accessory_parent(root, project, settings, acc, entry)
    return True
