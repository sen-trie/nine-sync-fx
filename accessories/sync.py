from ..common.constants import MME_BAKED_PROP
from ..common.controls import mme_effect_roots
from ..common.log import log
from ..common.scene_utils import model_key
from ..common.state import _mme_state
from ..runtime.bake import mme_run_bake_checks
from ..runtime.build_effect import (
    mme_library_batch_begin,
    mme_library_batch_end,
)
from ..runtime.steps import run_steps
from .batch import (
    import_accessory_steps_all,
    new_outcome,
)
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


# The one sync path, called by core through the post_sync hook, as a generator: it yields (fraction, text) per accessory and per
# effect pass and returns {"imported", "failed", "warnings"}. While it runs under an integrated sync (ctx.integrated), core owns
# the progress bar, so FX starts none. User-library writes are held back and made once at the end, also when the sync is cancelled.
def sync_accessories_steps(ctx):
    scene = ctx.scene
    settings = getattr(scene, "ninesync_fx", None)
    if settings is None:
        return new_outcome()
    project = ctx.project
    mirror_context(settings, ctx)
    refresh_accessories(settings, project)
    _mme_state["integrated"] = bool(getattr(ctx, "integrated", False))
    mme_library_batch_begin()
    try:
        outcome = yield from import_accessory_steps_all(scene, project, settings, ctx.unit, getattr(ctx, "cancelled", None))
    finally:
        _mme_state["integrated"] = False
        mme_library_batch_end()
    apply_render_defaults(scene)
    queue_bake_checks(scene)
    return outcome


# Core's rigs, lights and animation may have changed under a baked pass although neither effect file did, so every sync re-checks
# the bakes of every effect it touched; a bake whose inputs no longer match goes stale and is cleared.
def queue_bake_checks(scene):
    for root in mme_effect_roots(scene):
        if any(child.get(MME_BAKED_PROP) for child in root.children):
            _mme_state["bake_check"].add(root.name)
    mme_run_bake_checks()


def sync_accessories(ctx):
    return run_steps(sync_accessories_steps(ctx))
