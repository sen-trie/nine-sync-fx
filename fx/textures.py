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
    __slots__ = ("pixels", "width", "height", "path", "single")

    def __init__(self, pixels, path):
        self.pixels = pixels
        self.height, self.width = pixels.shape[:2]
        self.path = path
        self.single = None

    # Vectorised over u/v arrays; bilinear uses texel centres (the -0.5 offset) like the GPU does. Coordinates
    # are always worked out in float64. A float32 caller gets point samples from a float32 copy of the
    # pixels, the same values its next operation would have cast them to.
    def sample(self, u, v, point, address, dtype=np.float64):
        u = np.array(u, dtype=np.float64)
        v = np.array(v, dtype=np.float64)
        np.nan_to_num(u, copy=False)
        np.nan_to_num(v, copy=False)
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
            if n & (n - 1) == 0:
                return np.bitwise_and(i, n - 1)
            return np.mod(i, n)

        if point:
            x = wrap(np.floor(u * w).astype(np.int64), w)
            y = wrap(np.floor(v * h).astype(np.int64), h)
            if dtype == np.float32:
                if self.single is None:
                    self.single = self.pixels.astype(np.float32)
                texel = self.single[y, x]
                if texel.ndim == 1:
                    return [np.float32(texel[k]) for k in range(4)]
                return [texel[..., k] for k in range(4)]
            texel = self.pixels[y, x]
        else:
            fx = u * w - 0.5
            fy = v * h - 0.5
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
# globals were read. With a memo (one per pass analysis) texture samples are reused across probe states; lanes
# names the analysed vertices of this run within that pass, parent the previous run's lanes and where this
# run's vertices sit in them.
class FxNumpyEnv:
    def __init__(self, program, state, textures, memo=None, lanes=None, parent=None):
        self.program = program
        self.state = state
        self.textures = textures
        self.sources = {}
        self.reads = set()
        self.memo = memo
        self.lanes = lanes
        self.parent = parent

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
        point = info.point() if info else False
        address = info.address() if info else "WRAP"
        dtype = getattr(ex.B, "dtype", np.float64)
        if self.memo is None:
            return image.sample(u, v, point, address, dtype)
        key = (path, point, address, dtype, tuple(ex.trace))
        u = np.asarray(u)
        v = np.asarray(v)
        found = fx_memo_lookup(self.memo.get(key), self.lanes, u, v, self.parent)
        if found is None:
            found = image.sample(u, v, point, address, dtype)
        for array in [u, v] + list(found):
            if isinstance(array, np.ndarray) and array.ndim:
                array.flags.writeable = False
        self.memo[key] = (self.lanes, u, v, found)
        return found


# Lanes of an earlier run that the current lanes occupy, or None when they are not a subset. None lanes mean
# every vertex of the pass.
def fx_memo_positions(old, new, parent=None):
    if old is new:
        return slice(None)
    if parent is not None and parent[0] is old:
        return parent[1]
    if new is None:
        return slice(None) if old is None else None
    if old is None:
        return new
    if len(new) > len(old):
        return None
    at = np.searchsorted(old, new)
    if len(at) and (at[-1] >= len(old) or not np.array_equal(old[np.minimum(at, len(old) - 1)], new)):
        return None
    return at


def fx_memo_take(value, at):
    if not isinstance(value, np.ndarray) or value.ndim == 0 or isinstance(at, slice):
        return value
    return value[at]


# A sample is a pure function of the image, filter, address mode and coordinates, so an earlier result at the
# same place in the shader is reused when the coordinates of the vertices still analysed are identical.
def fx_memo_lookup(entry, lanes, u, v, parent=None):
    if entry is None:
        return None
    old_lanes, old_u, old_v, result = entry
    at = fx_memo_positions(old_lanes, lanes, parent)
    if at is None:
        return None
    for old, new in ((old_u, u), (old_v, v)):
        picked = fx_memo_take(old, at)
        if np.shape(picked) != np.shape(new) or not np.array_equal(picked, new, equal_nan=True):
            return None
    return [fx_memo_take(comp, at) for comp in result]


# Pillow is imported on use so the add-on loads without it.
def fx_pil_loader(path):
    from PIL import Image

    with Image.open(path) as handle:
        rgba = handle.convert("RGBA")
        data = np.asarray(rgba, dtype=np.float64) / 255.0
    return FxImage(data, path)
