import bpy
import numpy as np
import os

from ..common.constants import MME_IMAGE_PREFIX
from ..fx.errors import FxError
from ..fx.semantics import (
    fx_is_render_target,
    fx_sampler_texture_global,
)
from ..fx.textures import FxImage


# Image for a texture slot of a shared group, or None for render targets and anything unresolvable.
def mme_slot_image(env, slot):
    program = getattr(env, "program", None)
    g = program.globals.get(slot) if program is not None else None
    if g is None or fx_is_render_target(g) or getattr(env, "textures", None) is None:
        return None
    path = env.textures.resource_path(g, env.state.material.get("texture", ""))
    if not path:
        return None
    try:
        return mme_effect_image(path)
    except RuntimeError:
        return None


# Loads an effect texture once per file (matched by normalised path), as Non-Color data so the shader does its
# own gamma handling.
def mme_effect_image(path):
    norm = os.path.normcase(os.path.abspath(path))
    for image in bpy.data.images:
        if image.get("mme_path") == norm:
            return image
    image = bpy.data.images.load(path, check_existing=False)
    image.name = MME_IMAGE_PREFIX + os.path.basename(path)
    image["mme_path"] = norm
    image.colorspace_settings.name = "Non-Color"
    image.alpha_mode = "CHANNEL_PACKED"
    return image


# Reads a loaded image back as float RGBA with the rows flipped to HLSL order, for the numpy backend.
def mme_load_pixels(path):
    image = mme_effect_image(path)
    width, height = image.size
    if width == 0 or height == 0:
        raise FxError("image has no pixels: %s" % path)
    buffer = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(buffer)
    data = buffer.reshape(height, width, 4)[::-1].astype(np.float64)
    return FxImage(data, path)


# Resolves a sampler to a file. The second value is True for render targets, which are handled separately.
# Without a texture global, sampler s0 means the material's own texture.
def mme_texture_path(env, info):
    g = fx_sampler_texture_global(env.program, info)
    if fx_is_render_target(g):
        return None, True
    material_texture = env.state.material.get("texture", "")
    if g is None and info is not None and info.texture is None:
        path = material_texture if (info.register or "").lower().replace(" ", "") in ("s0", "") else None
    else:
        path = env.textures.resource_path(g, material_texture)
    return path, False


# Texture read for node backends: render targets go through rt_lookup (re-running the pass that wrote them),
# missing textures read as transparent black.
def mme_node_sample(env, ex, info, u, v):
    path, target = mme_texture_path(env, info)
    if target:
        if env.rt_lookup is not None:
            g = fx_sampler_texture_global(env.program, info)
            return env.rt_lookup(ex, g.name)
        return [0.0, 0.0, 0.0, 0.0]
    if not path:
        return [0.0, 0.0, 0.0, 0.0]
    try:
        image = mme_effect_image(path)
    except RuntimeError:
        return [0.0, 0.0, 0.0, 0.0]
    point = info.point() if info else False
    address = info.address() if info else "WRAP"
    extension = {"CLAMP": "EXTEND", "MIRROR": "MIRROR"}.get(address, "REPEAT")
    return ex.B.image_sample(image, "Closest" if point else "Linear", extension, u, v)
