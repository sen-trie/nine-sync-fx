from ..common.controls import mme_effect_roots
from ..common.log import log
from ..common.scene_utils import model_key
from .batch import import_accessories
from .entries import refresh_accessories


# Copies what core (or the standalone context) knows into FX's own settings before a build: the PMM path and
# the model links. FX never imports core, this is the hand-over.
def mirror_context(settings, ctx):
    integrated = bool(getattr(ctx, "integrated", False))
    if integrated:
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
        if integrated:
            link.target = ctx.targets.get(key)


# When effects exist: locks the render interface so they can update safely during a render, and optionally
# switches to the Standard view transform so colours match MMD.
def apply_render_defaults(scene, settings):
    if not (settings.mme_effects and mme_effect_roots(scene)):
        return
    if not scene.render.use_lock_interface:
        scene.render.use_lock_interface = True
        log.info("render interface locking enabled so effects can update safely while rendering")
    if settings.mme_standard_view and scene.view_settings.view_transform != "Standard":
        try:
            scene.view_settings.view_transform = "Standard"
            log.info("view transform set to Standard so effect colors match MMD")
        except TypeError:
            pass


# The one sync path. Core calls it through the post_sync hook, standalone mode builds its own context and
# calls it too.
def sync_accessories(ctx):
    scene = ctx.scene
    settings = getattr(scene, "ninesync_fx", None)
    if settings is None:
        return 0
    project = ctx.project
    mirror_context(settings, ctx)
    refresh_accessories(settings, project)
    done = import_accessories(scene, project, settings, ctx.unit)
    apply_render_defaults(scene, settings)
    return done
