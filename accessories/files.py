import bpy
import os


# PMM stores the path of the machine it was saved on, so also look in the configured accessory folder, beside
# the PMM and in its Accessory/ folder.
def find_accessory_file(acc, settings):
    pmm_dir = os.path.dirname(bpy.path.abspath(settings.filepath))
    base = acc.path.replace("\\", "/").rsplit("/", 1)[-1] or acc.name
    candidates = [acc.path]
    if settings.accessory_dir:
        candidates.append(os.path.join(bpy.path.abspath(settings.accessory_dir), base))
    candidates.append(os.path.join(pmm_dir, base))
    candidates.append(os.path.join(pmm_dir, "Accessory", base))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None
