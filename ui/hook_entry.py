import importlib
import json
import os
import traceback
from .. import hooks

from ..accessories.clean import clean_accessory_objects
from ..accessories.dependencies import fx_mmd_tools_available
from ..accessories.mesh import x_importer_available
from ..accessories.sync import (
    sync_accessories,
    sync_accessories_steps,
)
from ..common.constants import (
    ADDON_PACKAGE,
    MME_BACKEND_PROP,
    MME_BAKED_PROP,
    MME_DRAW_PROP,
)
from ..common.controls import (
    mme_effect_passes,
    mme_effect_roots,
)
from ..common.log import log
from ..common.scene_utils import current_scale


# Core's post_sync hook. A core that offers ctx.cancelled drives it as a generator, one step per tick, so Esc is heard between
# effect passes and the result dict reaches the sync summary; an older core gets the work done in one call and the imported
# count as an int (API 1 as shipped). Failures never escape: they are logged and returned as a failed count with a warning.
def post_sync_hook(ctx):
    if hasattr(ctx, "cancelled"):
        return guarded_steps(ctx)
    try:
        result = sync_accessories(ctx)
    except Exception as error:
        log.error("FX sync failed: %s\n%s", error, traceback.format_exc())
        return 0
    for warning in result["warnings"]:
        log.warning("FX sync: %s", warning)
    return result["imported"]


def guarded_steps(ctx):
    try:
        return (yield from sync_accessories_steps(ctx))
    except Exception as error:
        log.error("FX sync failed: %s\n%s", error, traceback.format_exc())
        return {"imported": 0, "failed": 1, "warnings": ["FX sync failed: %s" % error]}


# Core's clean hook: remove accessories and the standalone entries, which make no sense without their objects.
def clean_hook(scene, options):
    counts = {"accessories": 0}
    clean_accessory_objects(counts)
    settings = getattr(scene, "ninesync_fx", None)
    if settings is not None:
        for index in reversed(range(len(settings.accessories))):
            if settings.accessories[index].standalone:
                settings.accessories.remove(index)
    return counts


# Adds FX's section to core's support report: versions, optional add-ons, settings, and each effect pass with
# its backend and flags.
def report_hook(context):
    scene = context.scene
    settings = scene.ninesync_fx
    lines = ["---- NineSync: FX ----"]
    lines.append(
        "add-on %s | DirectX X Importer available: %s | mmd_tools available: %s | core installed: %s"
        % (
            ".".join(str(v) for v in addon_version()),
            x_importer_available(),
            fx_mmd_tools_available(),
            hooks.core_installed(),
        )
    )
    lines.append(
        "accessories: sync=%s sphere_maps=%s retime=%s scale=%.4f"
        % (settings.sync_accessories, settings.sphere_maps, settings.retime_fps, current_scale(settings))
    )
    lines.append(
        "effects: backend=%s density=%.2f tolerance=%.4f live=%s bake_to=%s"
        % (settings.mme_backend, settings.mme_density, settings.mme_tolerance, settings.mme_live, settings.mme_bake_target)
    )
    for root in mme_effect_roots(scene):
        passes = mme_effect_passes(root)
        lines.append(
            "  effect %s: %s"
            % (
                os.path.basename(root.get("mme_fx", "")),
                ", ".join(
                    "%s[%s%s%s]"
                    % (
                        json.loads(p.get(MME_DRAW_PROP, "{}")).get("pass", "?"),
                        p.get(MME_BACKEND_PROP, "PYTHON"),
                        ",baked" if p.get(MME_BAKED_PROP) else "",
                        ",handler" if p.get("mme_handler", True) else "",
                    )
                    for p in passes
                ),
            )
        )
    for entry in settings.accessories:
        lines.append(
            "accessory '%s' enabled=%s standalone=%s parent_override=%s effect=%s %s"
            % (
                entry.display,
                entry.enabled,
                entry.standalone,
                entry.parent_bone or "none",
                entry.use_effect,
                entry.effect_note or "",
            )
        )
    return lines


# Blender strips bl_info from an enabled extension's module, so the version comes from ADDON_VERSION in the
# root package, with bl_info only as a fallback.
def addon_version():
    try:
        root = importlib.import_module(ADDON_PACKAGE)
        version = getattr(root, "ADDON_VERSION", None) or root.bl_info["version"]
        return tuple(version)
    except Exception:
        return (0, 0, 0)
