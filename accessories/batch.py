import traceback

from ..common.log import log
from ..common.scene_utils import (
    accessory_collection,
    accessory_key,
)
from .importer import import_accessory


# Imports every enabled accessory of the project. One failing accessory is logged and does not stop the
# others.
def import_accessories(scene, project, settings, unit):
    if not settings.sync_accessories:
        return 0
    if project.accessory_error:
        log.warning("accessory section could not be read: %s", project.accessory_error)
        return 0
    if not project.accessories:
        return 0
    log.info("accessories: effect density %.2f", settings.mme_density)
    collection = accessory_collection(scene, True)
    entries = {e.key: e for e in settings.accessories}
    done = 0
    for acc in project.accessories:
        entry = entries.get(accessory_key(acc))
        if entry is not None and not entry.enabled:
            log.info("skip accessory '%s': disabled", acc.name)
            continue
        if not acc.keys:
            continue
        try:
            if import_accessory(scene, collection, project, settings, acc, entry, unit):
                done += 1
        except Exception as error:
            log.error(
                "accessory '%s' failed: %s\n%s", acc.name, error, traceback.format_exc()
            )
    return done
