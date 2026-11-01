import bpy


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


# Converts a value stored by the old add-on to the type of the new property (enum indexes become identifiers);
# None if it does not fit.
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


# Reads the old mmd_bridge property group, which Blender 5 keeps in system properties for unregistered groups.
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


# Keys that show a file was saved with an older NineSync.
def legacy_markers(raw):
    try:
        return [k for k in raw.keys() if k.startswith("mme_") or k in LEGACY_MARKERS]
    except AttributeError:
        return []


# Old setting names worth copying into the new settings.
def legacy_keys(raw):
    try:
        return [k for k in raw.keys() if k.startswith("mme_") or k in LEGACY_SETTING_NAMES or k in ("accessories", "accessory_collection")]
    except AttributeError:
        return []


# Every datablock that can carry drivers, including the node trees of materials, worlds and groups.
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


# Copies old settings and accessory entries into the new settings and repoints effect drivers from
# mmd_bridge.mme_* to ninesync_fx.*. Returns (settings copied, drivers repointed).
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
