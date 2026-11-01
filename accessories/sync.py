from ..common.controls import mme_effect_roots
from ..common.log import log
from ..common.scene_utils import model_key
from .batch import import_accessories
from .entries import refresh_accessories


# Copies what core knows into FX's own settings before a build: the PMM path and the model links. FX never
# imports core, this is the hand-over.
def mirror_context(settings, ctx):
    settings.filepath = ctx.pmm_path
    wanted = [model_key(m) for m in ctx.project.models]
    for index in reversed(range(len(settings.targets))):
        if settings.targets[index].key not in wanted:
            settings.targets.remove(index)
    present = {t.key: t for t in settings.targets}
    for model in ctx.project.models:
        key = model_key(model)
        link = present.get(key)
        if link is None:
            link = settings.targets.add()
            link.key = key
            link.pmm_name = model.name or model.name_en or key
            link.enabled = True
        link.target = ctx.targets.get(key)


# When effects exist: locks the render interface so they can update safely during a render.
def apply_render_defaults(scene):
    if not mme_effect_roots(scene):
        return
    if not scene.render.use_lock_interface:
        scene.render.use_lock_interface = True
        log.info("render interface locking enabled so effects can update safely while rendering")


# The one sync path, called by core through the post_sync hook.
def sync_accessories(ctx):
    scene = ctx.scene
    settings = getattr(scene, "ninesync_fx", None)
    if settings is None:
        return 0
    project = ctx.project
    mirror_context(settings, ctx)
    refresh_accessories(settings, project)
    done = import_accessories(scene, project, settings, ctx.unit)
    apply_render_defaults(scene)
    return done
