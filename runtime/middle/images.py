"""Isolated encoded texture views for the DX9 shader arithmetic profile."""

from array import array
from copy import copy
from hashlib import sha256
import json
from pathlib import Path

import bpy
import numpy as np

from ...fx.errors import FxUnsupported
from ...fx.textures import FxImage

ENCODED_SOURCE = "ninesync_middle_encoded_source"
ENCODED_DIGEST = "ninesync_middle_encoded_digest"
MIP_SOURCE = "ninesync_middle_mip_source"
MIP_DIGEST = "ninesync_middle_mip_digest"
MIP_LEVEL = "ninesync_middle_mip_level"
BUFFER_SOURCE = "ninesync_middle_buffer_source"
BUFFER_KEY = "ninesync_middle_buffer_key"
BUFFER_UPLOAD = "ninesync_middle_buffer_upload"
BUFFER_VERSION = 2
BUFFER_ATLAS = "ninesync_middle_buffer_mips"
BUFFER_VIEWPORT = "ninesync_middle_buffer_viewport"


def _pixels(image):
    pixels = array("f", [0.0]) * len(image.pixels)
    image.pixels.foreach_get(pixels)
    return pixels


def _signature(image, pixels=None):
    pixels = _pixels(image) if pixels is None else pixels
    return sha256(pixels).hexdigest() + repr(tuple(image.size))


def encoded_image(image):
    # Blender's file image pixel API exposes the original encoded texels. A
    # separate Non-Color view makes interpolation happen before sRGB decoding,
    # matching MME's ordinary DX9 sampler rather than encoding a filtered result.
    pixels = _pixels(image)
    signature = _signature(image, pixels)
    for candidate in bpy.data.images:
        if candidate.get(ENCODED_SOURCE) == image and candidate.get(ENCODED_DIGEST) == signature:
            return candidate
    result = bpy.data.images.new("MME Encoded | " + image.name, *image.size,
                                 alpha=True, float_buffer=image.is_float)
    result.colorspace_settings.name = "Non-Color"
    result.alpha_mode = "CHANNEL_PACKED"
    result.pixels.foreach_set(pixels)
    result.update()
    # Generated buffers otherwise regenerate their blank initial state on reopen.
    result.file_format = "OPEN_EXR" if image.is_float else "PNG"
    result.pack()
    result[ENCODED_SOURCE] = image
    result[ENCODED_DIGEST] = signature
    return result


def mip_image(image, level, *, encoded):
    """Packed explicit mip view; the source image remains artist-owned."""
    pixels = _pixels(image)
    signature = _signature(image, pixels)
    for candidate in bpy.data.images:
        if (candidate.get(MIP_SOURCE) == image and candidate.get(MIP_DIGEST) == signature
                and candidate.get(MIP_LEVEL) == level and candidate.get("ninesync_middle_mip_encoded") == encoded):
            return candidate
    width, height = image.size
    chain = FxImage(np.asarray(pixels).reshape(height, width, 4), image.name).mip_chain()
    mip = chain[min(level, len(chain)-1)]
    result = bpy.data.images.new(f"MME Mip {level} | {image.name}", mip.width, mip.height,
                                 alpha=True, float_buffer=True)
    result.colorspace_settings.name = "Non-Color" if encoded else image.colorspace_settings.name
    result.alpha_mode = "CHANNEL_PACKED"
    result.pixels.foreach_set(mip.pixels.astype(np.float32).ravel())
    result.update()
    result.file_format = "OPEN_EXR"
    result.pack()
    result[MIP_SOURCE], result[MIP_DIGEST], result[MIP_LEVEL] = image, signature, level
    result["ninesync_middle_mip_encoded"] = encoded
    return result


def viewport_size(scene):
    render = scene.render
    return tuple(max(1, size * render.resolution_percentage // 100)
                 for size in (render.resolution_x, render.resolution_y))


def buffer_image(analysis, name, writer, texture_name, source, viewport):
    """Persist the base of a full-coverage, texture-only FX buffer pass."""
    from ...fx.program import FxTechnique
    from ...fx.semantics import FxSceneState, fx_is_render_target
    from .blender_raster import encoded_image as numeric_image
    from .raster import DepthTarget, RasterState
    from .render_execution import FxRenderExecution, GeometryStream

    signature = _signature(source)
    upload_kind = Path(source.filepath).suffix.lower(), source.is_float
    key = json.dumps([BUFFER_VERSION, analysis.signature, name, signature, viewport, upload_kind])
    for candidate in bpy.data.images:
        if (not candidate.get(BUFFER_ATLAS) and candidate.get(BUFFER_SOURCE) == source
                and candidate.get(BUFFER_KEY) == key):
            return candidate

    program = copy(analysis.program)
    program.globals = {key: g for key, g in program.globals.items()
                       if not fx_is_render_target(g) or key == name}
    program.__dict__.pop("_const_values", None)
    technique = FxTechnique(writer.name, {"Script": ("str", f"RenderColorTarget0={name};Pass={writer.name};")})
    technique.passes = [writer]
    state = FxSceneState()
    state.viewport = viewport
    geometry = GeometryStream({"POSITION0": np.empty((0, 4))}, np.empty((0, 3), dtype=np.int32))
    runtime = FxRenderExecution(program, *viewport)
    target = runtime.targets[name]
    runtime.depths[""] = DepthTarget(target.width, target.height)
    runtime.execute(geometry, state, images={texture_name: numeric_image(source, native_upload=True, material_texture=True)},
                    technique=technique, initial_state=RasterState(depth_test=False, depth_write=False))
    if runtime.last_stats[0]["passing_fragments"] != target.width * target.height:
        raise FxUnsupported(f"Buffer pass {writer.name} changes the full-screen raster coverage.")
    result = bpy.data.images.new("MME Buffer | " + name, target.width, target.height, alpha=True)
    try:
        result.colorspace_settings.name = "Non-Color"
        result.alpha_mode = "CHANNEL_PACKED"
        result.pixels.foreach_set(target.pixels[::-1].ravel())
        result.update()
        result.file_format = "PNG"
        result.pack()
        result[BUFFER_SOURCE] = source
        result[BUFFER_KEY] = key
        result[BUFFER_VIEWPORT] = viewport
        result[ENCODED_DIGEST] = signature
        result[BUFFER_UPLOAD] = repr(upload_kind)
    except Exception:
        bpy.data.images.remove(result)
        raise
    return result


def buffer_mip_atlas(image):
    """Arrange level images in contiguous rows with independent texel addressing."""
    from .raster import ColorTarget

    width, height = image.size
    levels = max(width, height).bit_length()
    rows = []
    offset = 0
    for level in range(levels):
        w, h = max(1, width >> level), max(1, height >> level)
        rows.append((w, h, offset))
        offset += h
    source = image[BUFFER_SOURCE]
    for candidate in bpy.data.images:
        if (candidate.get(BUFFER_ATLAS) and candidate.get(BUFFER_SOURCE) == source
                and candidate.get(BUFFER_KEY) == image[BUFFER_KEY]):
            return candidate, rows

    target = ColorTarget(width, height, mip_levels=levels)
    data = np.asarray(_pixels(image)).reshape(height, width, 4)[::-1]
    chain = FxImage(data, image.name, mip_levels=levels, reduce_mip=target.reduce_mip).mip_chain()
    pixels = np.zeros((offset, width, 4), dtype=np.float32)
    for level, (w, h, start) in zip(chain, rows):
        pixels[start:start+h, :w] = level.pixels
    atlas = bpy.data.images.new("MME Buffer Mips | " + image.name, width, offset, alpha=True)
    try:
        atlas.colorspace_settings.name = "Non-Color"
        atlas.alpha_mode = "CHANNEL_PACKED"
        atlas.pixels.foreach_set(pixels[::-1].ravel())
        atlas.update()
        atlas.file_format = "PNG"
        atlas.pack()
        for key in (BUFFER_SOURCE, BUFFER_KEY, BUFFER_VIEWPORT, ENCODED_DIGEST, BUFFER_UPLOAD):
            atlas[key] = image[key]
        atlas[BUFFER_ATLAS] = True
    except Exception:
        bpy.data.images.remove(atlas)
        raise
    return atlas, rows


def views_current(trees, scene=None):
    signatures = {}
    for tree in trees:
        for node in tree.nodes:
            image = node.image if node.type == "TEX_IMAGE" else (
                node.inputs["Image"].default_value if node.bl_idname == "GeometryNodeImageTexture" else None)
            if image and BUFFER_SOURCE in image:
                source_key, digest_key = BUFFER_SOURCE, ENCODED_DIGEST
                source = image.get(BUFFER_SOURCE)
                if (not source or scene is not None and tuple(image[BUFFER_VIEWPORT]) != viewport_size(scene)
                        or image[BUFFER_UPLOAD] != repr((Path(source.filepath).suffix.lower(), source.is_float))):
                    return False
            else:
                source_key, digest_key = (MIP_SOURCE, MIP_DIGEST) if image and MIP_SOURCE in image else (ENCODED_SOURCE, ENCODED_DIGEST)
            source = image.get(source_key) if image else None
            if image and source_key in image and not source:
                return False
            if source:
                if source not in signatures:
                    signatures[source] = _signature(source)
                if image.get(digest_key) != signatures[source]:
                    return False
    return True


def discard_unused():
    for image in list(bpy.data.images):
        if (ENCODED_SOURCE in image or MIP_SOURCE in image or BUFFER_SOURCE in image) and image.users == 0:
            bpy.data.images.remove(image)
