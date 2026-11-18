import traceback

from ..common.log import log
from ..common.scene_utils import (
    accessory_collection,
    accessory_key,
)
from ..runtime.steps import (
    run_steps,
    scaled,
)
from .importer import import_accessory_steps


def new_outcome():
    return {"imported": 0, "failed": 0, "warnings": []}


# Imports every enabled accessory of the project. One failing accessory is logged, counted as failed and does not stop the
# others; an accessory whose effect failed is counted as failed and not as imported, with the reason in the warnings.
def import_accessories(scene, project, settings, unit, cancelled=None):
    return run_steps(import_accessory_steps_all(scene, project, settings, unit, cancelled))["imported"]


# The same as a generator yielding (fraction, text) per accessory and per effect pass, returning the outcome dict. 'cancelled'
# is core's optional callable; it is also checked between accessories.
def import_accessory_steps_all(scene, project, settings, unit, cancelled=None):
    outcome = new_outcome()
    if not settings.sync_accessories:
        return outcome
    if project.accessory_error:
        log.warning("accessory section could not be read: %s", project.accessory_error)
        outcome["warnings"].append("accessory section could not be read: %s" % project.accessory_error)
        return outcome
    if not project.accessories:
        return outcome
    log.info("accessories: effect density %.2f", settings.mme_density)
    collection = accessory_collection(scene, True)
    entries = {e.key: e for e in settings.accessories}
    todo = []
    for acc in project.accessories:
        entry = entries.get(accessory_key(acc))
        if entry is not None and not entry.enabled:
            log.info("skip accessory '%s': disabled", acc.name)
            continue
        if not acc.keys:
            continue
        todo.append((acc, entry))
    for position, (acc, entry) in enumerate(todo):
        if cancelled is not None and cancelled():
            outcome["warnings"].append("cancelled before accessory '%s'" % acc.name)
            break
        low, high = position / len(todo), (position + 1) / len(todo)
        yield low, acc.name
        report = {"warnings": [], "failed": False}
        try:
            ok = yield from scaled(import_accessory_steps(scene, collection, project, settings, acc, entry, unit, report), low, high)
        except Exception as error:
            log.error(
                "accessory '%s' failed: %s\n%s", acc.name, error, traceback.format_exc()
            )
            report["warnings"].append("accessory '%s' failed: %s" % (acc.name, error))
            report["failed"] = True
            ok = False
        outcome["warnings"].extend(report["warnings"])
        if report["failed"] or not ok:
            outcome["failed"] += 1
        else:
            outcome["imported"] += 1
    return outcome
