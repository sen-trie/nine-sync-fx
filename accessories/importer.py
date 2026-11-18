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
from ..runtime.build_effect import mme_build_effect_steps
from ..runtime.cleanup import (
    mme_clear_root_props,
    remove_accessory_children,
    remove_accessory_children_single,
)
from ..runtime.controls_resolve import (
    mme_morph_curves,
    mme_resolve_controls,
)
from ..runtime.effect_files import mme_effect_lookup
from ..runtime.manifest import (
    mme_broken_bindings,
    mme_light_binding,
    mme_pass_by_key,
)
from ..runtime.runtime import mme_effect_stamp
from ..runtime.steps import run_steps
from ..runtime.visibility import (
    mme_capture_visibility,
    mme_restore_visibility,
)
from ..xfile.cache import (
    parse_x_bytes,
    x_header_mode,
)
from .files import find_accessory_file
from .keying import (
    key_accessory,
    link_fade,
)
from .mesh import (
    create_x_object,
    import_x_with_addon,
    remove_subset_faces,
)
from .parenting import (
    accessory_parent_plan,
    apply_accessory_parent,
    parent_influence_curves,
    parent_signature,
)


# Locates and parses the accessory's effect and resolves its controllers. The first time it also decides
# whether effects are used for the entry at all;
# returns (fx_path, program, controls, morph tracks, shown), with no effect when there is none or it cannot be
# read. shown is False when MME hides the accessory (.show = false in the .emm).
def effect_setup(project, settings, acc, entry, path, root):
    choice = mme_effect_lookup(acc, path, settings, entry, project)
    if entry is not None and choice.source != "emm-missing" and entry.effect_note.startswith("Effect not found"):
        entry.effect_note = ""
    if choice.source == "emm-none":
        log.info("accessory '%s': the PMM's .emm assigns no effect", acc.name)
    elif choice.source == "emm-missing":
        log.warning("accessory '%s': the PMM's .emm assigns %s, which was not found", acc.name, choice.value)
    if not choice.shown:
        log.info("accessory '%s' is hidden in MME (.emm show flag); hiding it", acc.name)
    if choice.subsets:
        log.info("accessory '%s': per-material effects in the .emm are not supported; the object's effect is used for every material", acc.name)
    if entry is not None and not entry.effect_checked:
        entry.effect_checked = True
        entry.use_effect = bool(choice.path) or choice.source == "emm-missing"
        if not entry.use_effect and choice.source != "emm-none":
            log.info("accessory '%s': no MME effect (.fx) found; effects are off for it", acc.name)
    elif entry is not None and not entry.use_effect and choice.path and choice.source.startswith("emm"):
        log.info("accessory '%s': the .emm assigns %s, but Use Effect is off for it", acc.name, os.path.basename(choice.path))
    fx_path = choice.path if entry is None or entry.use_effect else None
    if entry is not None and entry.use_effect and choice.source == "emm-missing":
        entry.effect_note = "Effect not found: %s" % short_text(choice.value.replace("\\", "/").rsplit("/", 1)[-1], 50)
    if not fx_path:
        return None, None, {}, {}, choice.shown
    try:
        program = fx_parse_file(fx_path)
        controls, tracks = mme_resolve_controls(program, project, settings, acc, root)
    except (FxError, OSError, UnicodeError) as error:
        log.error("accessory '%s': effect %s could not be read: %s", acc.name, fx_path, error)
        if entry is not None:
            entry.effect_note = "Effect error: %s" % short_text(error, 60)
        return None, None, {}, {}, choice.shown
    return fx_path, program, controls, tracks, choice.shown


# Imports or updates one accessory; see import_accessory_steps. 'report' collects "warnings" and sets "failed" when the
# accessory did not get what was asked for.
def import_accessory(scene, collection, project, settings, acc, entry, unit, report=None):
    return run_steps(import_accessory_steps(scene, collection, project, settings, acc, entry, unit, report))


# Imports or updates one accessory as a generator that yields (fraction, text) between effect passes. A stamp (files, effect,
# backend, density, scale, controller kinds, light binding) says whether anything needs rebuilding; if not, only keys,
# parent and controls are refreshed. When an effect is already built on the root, a changed stamp goes through the
# same incremental builder as Refresh Effects: passes whose own signature is unchanged keep their objects and bakes, a replaced
# pass is removed only once its replacement exists, and a failure keeps what was working. Without a built effect the effect
# build is tried first and any failure falls back to the plain mesh, so a bad effect never loses the accessory.
def import_accessory_steps(scene, collection, project, settings, acc, entry, unit, report=None):
    report = {"warnings": [], "failed": False} if report is None else report
    path = find_accessory_file(acc, settings)
    if path is None:
        log.warning(
            "accessory '%s': file not found (PMM path %s); set an Accessory Folder",
            acc.name,
            acc.path,
        )
        if entry is not None:
            entry.effect_note = "File not found"
        report["warnings"].append("accessory '%s': file not found" % acc.name)
        report["failed"] = True
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
    fx_path, program, controls, tracks, shown = effect_setup(project, settings, acc, entry, path, root)
    additive = bool(getattr(acc, "additive", False))
    stamp = "%s|%s" % (path, os.path.getmtime(path))
    if additive:
        stamp += "|additive"
    if fx_path:
        stamp += "|%s|%s|density=%.4f|unit=%.6f|%s|light=%s" % (
            mme_effect_stamp(fx_path, path, program),
            settings.mme_backend,
            settings.mme_density,
            unit,
            json.dumps(sorted((k, v.get("kind")) for k, v in controls.items())),
            json.dumps(mme_light_binding()),
        )
    old_passes = mme_pass_by_key(root) if root.get("mme_fx") else {}
    broken = [name for old in old_passes.values() for name in mme_broken_bindings(old)]
    if broken:
        log.info("accessory '%s': %d effect driver target(s) are gone (%s); rebinding", acc.name, len(broken), broken[0])
    keep = bool(old_passes) and not fx_path and entry is not None and entry.effect_note.startswith("Effect error")
    withheld = False
    if keep and root.get("mmd_bridge_stamp") != stamp:
        report["warnings"].append("accessory '%s': the effect could not be read; the previous build is kept" % acc.name)
        report["failed"] = True
    elif not keep and (root.get("mmd_bridge_stamp") != stamp or not root.children or broken):
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
        incremental = bool(old_passes) and bool(fx_path) and mode == "txt"
        captured = mme_capture_visibility(root) if root.children else None
        if incremental:
            kept_children = set(old_passes.values())
            for child in [c for c in root.children if c not in kept_children]:
                remove_accessory_children_single(child)
        else:
            remove_accessory_children(root)
            mme_clear_root_props(root)
        children = None
        built = {}
        untouched = set()
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
                children, uncovered, _tracks, total = yield from mme_build_effect_steps(
                    scene, collection, settings, acc, root, path, fx_path, unit, controls, old_passes if incremental else None
                )
                effect_report = getattr(_mme_runtimes.get(root.name), "build_report", {})
                built = {child: own for child, own in effect_report.get("built", [])}
                untouched = set(effect_report.get("reused", [])) | set(effect_report.get("kept", []))
                if uncovered:
                    static = create_x_object(
                        short_name(acc.name, 40) + "_mesh", x_scene(), folder, settings, additive
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
                if effect_report.get("kept"):
                    withheld = True
                    note += "; %d pass(es) kept from the previous build after an error" % len(effect_report["kept"])
                if effect_report.get("kept") or (effect_report.get("failed") and not incremental):
                    report["failed"] = True
                    report["warnings"].append(
                        "accessory '%s': %d effect pass(es) failed (%s)" % (acc.name, effect_report.get("failed", 0), "; ".join(effect_report.get("errors", [])[:2]))
                    )
                if entry is not None:
                    entry.effect_note = note
            except Exception as error:
                log.error(
                    "accessory '%s': effect %s failed%s: %s\n%s",
                    acc.name,
                    fx_path,
                    "; the previous build is kept" if incremental else ", importing the plain mesh instead",
                    error,
                    traceback.format_exc(),
                )
                report["failed"] = True
                report["warnings"].append("accessory '%s': effect failed: %s" % (acc.name, short_text(error, 80)))
                if entry is not None:
                    entry.effect_note = "Effect failed: %s" % short_text(error, 60)
                if incremental:
                    withheld = True
                    children = [c for c in root.children]
                else:
                    for child in list(root.children):
                        remove_accessory_children_single(child)
                    mme_clear_root_props(root)
                    children = None
        elif entry is not None and not entry.effect_note.startswith("Effect not found"):
            entry.effect_note = ""
        if children is None:
            if mode == "txt":
                child = create_x_object(
                    short_name(acc.name, 40) + "_mesh", x_scene(), folder, settings, additive
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
        if captured is not None:
            for child in children:
                if child in untouched:
                    continue
                mme_restore_visibility([child], built.get(child) or captured)
        if not withheld:
            root["mmd_bridge_stamp"] = stamp
    for child in root.children:
        child.scale = (ACCESSORY_UNIT * unit,) * 3
    plan = accessory_parent_plan(project, settings, acc, entry)
    apply_accessory_parent(root, acc, plan)
    extra = mme_morph_curves(root, tracks) if root.get("mme_fx") else []
    key_accessory(root, acc, unit, shown, parent_signature(plan), parent_influence_curves(plan, acc.keys), extra)
    link_fade(root)
    if root.get("mme_fx"):
        root["mme_controls"] = json.dumps(controls)
        mme_ensure_use_props(root, controls)
        root["mme_unit"] = unit
        runtime = _mme_runtimes.get(root.name)
        if runtime is not None:
            runtime.controls_runtime = {
                name for name, desc in mme_load_controls(root).items() if desc.get("kind") != "const"
            }
    return True
