"""Isolated encoded texture views for the DX9 shader arithmetic profile."""

from array import array
from hashlib import sha256

import bpy

ENCODED_SOURCE = "ninesync_middle_encoded_source"
ENCODED_DIGEST = "ninesync_middle_encoded_digest"


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


def views_current(trees):
    signatures = {}
    for tree in trees:
        for node in tree.nodes:
            image = node.image if node.type == "TEX_IMAGE" else (
                node.inputs["Image"].default_value if node.bl_idname == "GeometryNodeImageTexture" else None)
            source = image.get(ENCODED_SOURCE) if image else None
            if image and ENCODED_SOURCE in image and not source:
                return False
            if source:
                if source not in signatures:
                    signatures[source] = _signature(source)
                if image.get(ENCODED_DIGEST) != signatures[source]:
                    return False
    return True


def discard_unused():
    for image in list(bpy.data.images):
        if ENCODED_SOURCE in image and image.users == 0:
            bpy.data.images.remove(image)
