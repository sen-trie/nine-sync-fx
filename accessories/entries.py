from ..common.log import log
from ..common.scene_utils import accessory_key


# Keeps the settings list in step with the PMM: new accessories are added enabled, vanished ones removed,
# standalone entries are never touched.
def refresh_accessories(settings, project):
    wanted = [accessory_key(a) for a in project.accessories]
    for index in reversed(range(len(settings.accessories))):
        entry = settings.accessories[index]
        if entry.key not in wanted and not entry.standalone:
            settings.accessories.remove(index)
    present = {e.key: e for e in settings.accessories}
    for acc in project.accessories:
        key = accessory_key(acc)
        if key not in present:
            entry = settings.accessories.add()
            entry.key = key
            entry.display = acc.name
            entry.enabled = True
            log.info("added accessory slot %s", key)
    if project.accessory_error:
        log.warning("accessory section could not be read: %s", project.accessory_error)
