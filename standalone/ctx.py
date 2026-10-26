import time
import traceback
import types

from ..accessories.sync import sync_accessories
from ..common.animation import retime_factor
from ..common.constants import PMM_FPS
from ..common.log import log
from ..common.scene_utils import (
    current_scale,
    redraw_all,
    scene_fps,
)
from ..pmm import PmmError
from .model_links import refresh_links
from .pmm_source import read_standalone_project
from .scale import retime_accessories


# Context like the one core passes to the hook, built from FX's own settings; integrated=False tells the sync
# to use FX's own PMM path and links.
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


# Standalone sync: read the PMM, refresh the model links, retime keys if the scene frame rate differs, then
# run the shared sync and re-evaluate the current frame.
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


# Operator-facing wrapper: errors are logged and shown in the status text instead of raised.
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
