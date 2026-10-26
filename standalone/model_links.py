from ..common.log import log
from ..common.scene_utils import (
    find_root_for,
    model_key,
    target_usable,
)


# FX's copy of core's target refresh: links follow the PMM's model list, stale targets are dropped and
# unmatched models are auto-matched to mmd_tools roots.
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
