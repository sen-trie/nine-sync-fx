import numpy as np
import os

from .annotations import fx_annotation
from .semantics import (
    fx_classify_global,
    fx_is_render_target,
    fx_sampler_texture_global,
    fx_value_from_list,
)
from .types import (
    fx_is_numeric,
    fx_resolve_type,
)


# Float RGBA pixels with HLSL-style point/bilinear sampling and wrap, clamp or mirror addressing.
class FxImage:
    __slots__ = ("pixels", "width", "height", "path")

    def __init__(self, pixels, path):
        self.pixels = pixels
        self.height, self.width = pixels.shape[:2]
        self.path = path

    # Vectorised over u/v arrays; bilinear uses texel centres (the -0.5 offset) like the GPU does.
    def sample(self, u, v, point, address):
        u = np.asarray(u, dtype=np.float64)
        v = np.asarray(v, dtype=np.float64)
        shape = np.broadcast(u, v).shape
        u = np.broadcast_to(u, shape)
        v = np.broadcast_to(v, shape)
        w, h = self.width, self.height

        def wrap(i, n):
            if address == "CLAMP":
                return np.clip(i, 0, n - 1)
            if address == "MIRROR":
                period = np.mod(i, 2 * n)
                return np.where(period >= n, 2 * n - 1 - period, period)
            return np.mod(i, n)

        if point:
            x = wrap(np.floor(np.nan_to_num(u) * w).astype(np.int64), w)
            y = wrap(np.floor(np.nan_to_num(v) * h).astype(np.int64), h)
            texel = self.pixels[y, x]
        else:
            fx = np.nan_to_num(u) * w - 0.5
            fy = np.nan_to_num(v) * h - 0.5
            x0 = np.floor(fx)
            y0 = np.floor(fy)
            tx = (fx - x0)[..., None]
            ty = (fy - y0)[..., None]
            x0 = x0.astype(np.int64)
            y0 = y0.astype(np.int64)
            xa, xb = wrap(x0, w), wrap(x0 + 1, w)
            ya, yb = wrap(y0, h), wrap(y0 + 1, h)
            p = self.pixels
            texel = (p[ya, xa] * (1 - tx) + p[ya, xb] * tx) * (1 - ty) + (p[yb, xa] * (1 - tx) + p[yb, xb] * tx) * ty
        texel = np.asarray(texel, dtype=np.float64)
        if texel.ndim == 1:
            return [np.float64(texel[k]) for k in range(4)]
        return [texel[..., k] for k in range(4)]


# Resolves effect textures to image files (ResourceName annotation) and caches loaded images, including
# failures.
class FxTextureStore:
    def __init__(self, program, loader):
        self.program = program
        self.loader = loader
        self.images = {}

    def texture_global(self, info):
        if info is None:
            return None
        name = info.texture
        if not name:
            return None
        return self.program.globals.get(name)

    # Material, toon, sphere and render-target textures are supplied by MMD at draw time, so only plain
    # ResourceName textures resolve to files.
    def resource_path(self, g, material_texture=""):
        if g is None:
            return material_texture or None
        if g.semantic in ("MATERIALTEXTURE",):
            return material_texture or None
        if g.semantic and g.semantic not in ("",):
            if g.semantic.startswith(("RENDERCOLORTARGET", "RENDERDEPTHSTENCILTARGET", "OFFSCREENRENDERTARGET", "ANIMATEDTEXTURE", "MATERIALSPHEREMAP", "MATERIALTOONTEXTURE")):
                return None
        resource = fx_annotation(g, "ResourceName", self.program, None)
        if not isinstance(resource, str) or not resource:
            return None
        return fx_find_file(self.program.folder, resource)

    def image(self, path):
        if path is None:
            return None
        if path not in self.images:
            try:
                self.images[path] = self.loader(path)
            except Exception:
                self.images[path] = None
        return self.images[path]


# Case-insensitive lookup, then a match ignoring the extension (effects often name a different image format
# than the file on disk).
def fx_find_file(folder, name):
    name = name.replace("\\", "/")
    candidate = os.path.join(folder, name)
    if os.path.isfile(candidate):
        return candidate
    parent = os.path.dirname(candidate)
    base = os.path.basename(candidate).lower()
    stem = os.path.splitext(base)[0]
    if os.path.isdir(parent):
        entries = os.listdir(parent)
        for entry in entries:
            if entry.lower() == base:
                return os.path.join(parent, entry)
        for entry in entries:
            if os.path.splitext(entry)[0].lower() == stem:
                return os.path.join(parent, entry)
    return None


# Environment for the numpy backend: globals come from FxSceneState, textures from the store. It records which
# globals were read.
class FxNumpyEnv:
    def __init__(self, program, state, textures):
        self.program = program
        self.state = state
        self.textures = textures
        self.sources = {}
        self.reads = set()

    def source(self, g):
        if g.name not in self.sources:
            self.sources[g.name] = fx_classify_global(g, self.program)
        return self.sources[g.name]

    def global_value(self, ex, g):
        source = self.source(g)
        if source is None:
            return None
        self.reads.add(g.name)
        t = fx_resolve_type(g.type, self.program)
        if not fx_is_numeric(t):
            return None
        return fx_value_from_list(ex, self.state.values(source, g), t)

    # Unresolvable textures and render targets sample as transparent black rather than failing the whole pass.
    def sample(self, ex, info, u, v, lod):
        g = fx_sampler_texture_global(self.program, info)
        if fx_is_render_target(g):
            return [ex.B.const(0.0)] * 4
        material_texture = self.state.material.get("texture", "")
        if g is None and info is not None and info.texture is None:
            path = material_texture if (info.register or "").lower().replace(" ", "") in ("s0", "") else None
        else:
            path = self.textures.resource_path(g, material_texture)
        image = self.textures.image(path)
        if image is None:
            return [ex.B.const(0.0)] * 4
        return image.sample(u, v, info.point() if info else False, info.address() if info else "WRAP")


# Pillow is imported on use so the add-on loads without it.
def fx_pil_loader(path):
    from PIL import Image

    with Image.open(path) as handle:
        rgba = handle.convert("RGBA")
        data = np.asarray(rgba, dtype=np.float64) / 255.0
    return FxImage(data, path)
