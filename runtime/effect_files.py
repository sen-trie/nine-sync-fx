import bpy
import os

from ..fx.preprocessor import fx_read_text
from ..fx.textures import fx_find_file


# Reads the .emm next to a PMM: the [Object] and [Effect] sections map accessory slots to effect files.
def mme_read_emm(pmm_path):
    path = os.path.splitext(pmm_path)[0] + ".emm"
    if not os.path.isfile(path):
        return {}, {}
    try:
        text = fx_read_text(path)
    except OSError:
        return {}, {}
    sections = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().lower()
            sections.setdefault(current, {})
            continue
        if current and "=" in line:
            key, value = line.split("=", 1)
            sections[current][key.strip().lower()] = value.strip()
    return sections.get("object", {}), sections.get("effect", {})


# Finds the effect for an accessory: explicit per-accessory override, then the PMM's .emm mapping (by
# accessory file name, then by slot number),
# then a .fx named like the .x file beside it. 'none' in the .emm disables the effect.
def mme_find_effect(acc, x_path, settings, entry):
    if entry is not None and not entry.use_effect:
        return None
    if entry is not None and entry.effect_path:
        override = bpy.path.abspath(entry.effect_path)
        return override if os.path.isfile(override) else None
    pmm_path = bpy.path.abspath(settings.filepath)
    objects, effects = mme_read_emm(pmm_path)
    if effects:
        base = acc.path.replace("\\", "/").rsplit("/", 1)[-1].lower()
        slot = None
        for key, value in objects.items():
            if key.startswith("acc") and value.replace("\\", "/").rsplit("/", 1)[-1].lower() == base:
                slot = key
                break
        if slot is None:
            slot = "acc%d" % (acc.index + 1)
        value = effects.get(slot)
        if value is not None:
            if value.lower() in ("none", ""):
                return None
            for candidate in (value, os.path.join(os.path.dirname(pmm_path), value)):
                if os.path.isfile(candidate):
                    return candidate
            found = fx_find_file(os.path.dirname(x_path), value.replace("\\", "/").rsplit("/", 1)[-1])
            if found:
                return found
    stem = os.path.splitext(os.path.basename(x_path))[0].lower()
    folder = os.path.dirname(x_path)
    try:
        for name in os.listdir(folder):
            lower = name.lower()
            if lower == stem + ".fx":
                return os.path.join(folder, name)
    except OSError:
        pass
    return None
