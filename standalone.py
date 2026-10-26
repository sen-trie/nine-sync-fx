import bpy
import time
import traceback
import types

from .common import (
    PMM_FPS,
    log,
    model_key,
    redraw_all,
    scene_fps,
)
from .util import (
    current_scale,
    find_root_for,
    retime_factor,
    retime_keys,
    target_usable,
)
from .pmm import (
    PmmError,
    parse_pmm_file,
)
from .accessories import (
    sync_accessories,
)


def refresh_links(settings, project):
    wanted = [model_key(m) for m in project.models]
    for index in reversed(range(len(settings.targets))):
        if settings.targets[index].key not in wanted:
            settings.targets.remove(index)
    present = {t.key: t for t in settings.targets}
    for model in project.models:
        key = model_key(model)
        link = present.get(key)
        if link is None:
            link = settings.targets.add()
            link.key = key
            link.pmm_name = model.name or model.name_en or key
            link.enabled = True
        if link.target is not None and not target_usable(link.target):
            link.target = None
        if link.target is None:
            found = find_root_for(model)
            if found is not None:
                log.info("auto-matched '%s' -> %s", link.pmm_name, found.name)
                link.target = found


def retime_accessories(project, factor):
    if abs(factor - 1.0) < 1e-6:
        return
    for acc in project.accessories:
        acc.keys = retime_keys(acc.keys, factor)


def read_standalone_project(settings):
    path = bpy.path.abspath(settings.filepath)
    return path, parse_pmm_file(path)


def standalone_context(scene, project, path, settings):
    targets = {}
    for link in settings.targets:
        if link.enabled and link.target is not None:
            targets[link.key] = link.target
    return types.SimpleNamespace(
        scene=scene,
        project=project,
        pmm_path=path,
        targets=targets,
        unit=current_scale(settings),
        retimed=False,
        integrated=False,
        core_version=None,
    )


def standalone_sync(scene):
    settings = scene.ninesync_fx
    path, project = read_standalone_project(settings)
    refresh_links(settings, project)
    factor = retime_factor(scene, settings)
    if factor != 1.0:
        retime_accessories(project, factor)
        log.info(
            "retimed accessory keys by x%.4f for a %.3f fps scene (PMM is %d fps)",
            factor,
            scene_fps(scene),
            PMM_FPS,
        )
    ctx = standalone_context(scene, project, path, settings)
    done = sync_accessories(ctx)
    scene.frame_set(scene.frame_current)
    return done, len(project.accessories)


def run_standalone_sync(scene):
    settings = scene.ninesync_fx
    try:
        done, total = standalone_sync(scene)
    except (PmmError, OSError) as error:
        log.error("PMM read failed: %s", error)
        settings.status = "PMM read failed: %s" % error
        redraw_all()
        return False
    except Exception as error:
        log.error("accessory sync failed: %s\n%s", error, traceback.format_exc())
        settings.status = "Sync failed: %s" % error
        redraw_all()
        return False
    settings.status = "Synced %d of %d accessory(ies) at %s" % (done, total, time.strftime("%H:%M:%S"))
    redraw_all()
    return True


LEGACY_SETTING_NAMES = (
    "sync_accessories",
    "sphere_maps",
    "accessory_dir",
    "fx_info_tab",
    "retime_fps",
    "auto_scale",
    "scale",
    "filepath",
)


def legacy_value(settings, name, value):
    prop = settings.bl_rna.properties.get(name)
    if prop is None:
        return None
    if prop.type == "ENUM" and isinstance(value, int):
        items = list(prop.enum_items)
        if 0 <= value < len(items):
            return items[value].identifier
        return None
    if prop.type == "BOOLEAN":
        return bool(value)
    if prop.type == "FLOAT":
        return float(value)
    if prop.type == "INT":
        return int(value)
    if prop.type == "STRING":
        return str(value)
    return None


def legacy_group(scene):
    getter = getattr(scene, "bl_system_properties_get", None)
    if getter is not None:
        try:
            group = getter()
        except (RuntimeError, TypeError):
            group = None
        if group is not None:
            raw = group.get("mmd_bridge")
            if raw is not None:
                return raw
    return scene.get("mmd_bridge")


LEGACY_MARKERS = ("sync_accessories", "sphere_maps", "accessory_dir", "fx_info_tab", "accessories", "accessory_collection")


def legacy_markers(raw):
    try:
        return [k for k in raw.keys() if k.startswith("mme_") or k in LEGACY_MARKERS]
    except AttributeError:
        return []


def legacy_keys(raw):
    try:
        return [k for k in raw.keys() if k.startswith("mme_") or k in LEGACY_SETTING_NAMES or k in ("accessories", "accessory_collection")]
    except AttributeError:
        return []


def driver_owners():
    collections = (
        bpy.data.objects,
        bpy.data.node_groups,
        bpy.data.materials,
        bpy.data.meshes,
        bpy.data.scenes,
        bpy.data.shape_keys,
        bpy.data.cameras,
        bpy.data.lights,
        bpy.data.worlds,
        bpy.data.collections,
    )
    for collection in collections:
        for item in collection:
            yield item
            tree = getattr(item, "node_tree", None)
            if tree is not None:
                yield tree


def repair_legacy(scene):
    settings = scene.ninesync_fx
    raw = legacy_group(scene)
    copied = 0
    if raw is not None:
        for key in legacy_keys(raw):
            if key in ("accessories", "accessory_collection"):
                continue
            value = legacy_value(settings, key, raw[key])
            if value is None:
                continue
            try:
                setattr(settings, key, value)
                copied += 1
            except (TypeError, ValueError, AttributeError):
                continue
        present = {e.key for e in settings.accessories}
        for item in raw.get("accessories", []):
            key = item.get("key", "")
            if not key or key in present:
                continue
            entry = settings.accessories.add()
            entry.key = key
            for name in ("display", "enabled", "standalone", "effect_checked", "use_effect", "effect_path", "effect_note", "parent_bone"):
                if name in item:
                    legacy_set = legacy_value(entry, name, item[name])
                    if legacy_set is not None:
                        setattr(entry, name, legacy_set)
            copied += 1
    repointed = 0
    for owner in driver_owners():
        anim = getattr(owner, "animation_data", None)
        if anim is None:
            continue
        for driver in anim.drivers:
            for var in driver.driver.variables:
                for target in var.targets:
                    if target.data_path.startswith("mmd_bridge.mme_"):
                        target.data_path = "ninesync_fx." + target.data_path.split(".", 1)[1]
                        repointed += 1
    return copied, repointed
