import numpy as np
import os

from .errors import FxUnsupported
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
    __slots__ = ("pixels", "width", "height", "path", "single", "mips",
                 "mip_levels", "reduce_mip")

    def __init__(self, pixels, path, *, mip_levels=None, reduce_mip=None):
        self.pixels = pixels
        self.height, self.width = pixels.shape[:2]
        self.path = path
        self.single = None
        self.mips = [self]
        self.mip_levels = mip_levels
        self.reduce_mip = reduce_mip

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

        address_u, address_v = (address, address) if isinstance(address, str) else address

        def wrap(i, n, mode):
            if mode in ("CLAMP", "BORDER"):
                return np.clip(i, 0, n - 1)
            if mode == "MIRROR":
                period = np.mod(i, 2 * n)
                return np.where(period >= n, 2 * n - 1 - period, period)
            if n & (n - 1) == 0:
                return np.bitwise_and(i, n - 1)
            return np.mod(i, n)

        if point:
            x = wrap(np.floor(u * w).astype(np.int64), w, address_u)
            y = wrap(np.floor(v * h).astype(np.int64), h, address_v)
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
            xa, xb = wrap(x0, w, address_u), wrap(x0 + 1, w, address_u)
            ya, yb = wrap(y0, h, address_v), wrap(y0 + 1, h, address_v)
            p = self.pixels
            texel = (p[ya, xa] * (1 - tx) + p[ya, xb] * tx) * (1 - ty) + (p[yb, xa] * (1 - tx) + p[yb, xb] * tx) * ty
        texel = np.asarray(texel, dtype=np.float64)
        if texel.ndim == 1:
            return [np.float64(texel[k]) for k in range(4)]
        return [texel[..., k] for k in range(4)]


    def d3dx_png_upload(self, *, material_texture=False):
        """Create the native-sized base and byte-quantized PNG mip chain."""
        rounding = 1 if material_texture else 2

        def reduce_mip(pixels):
            # Source bytes and power-of-two extents give exact quarter-byte
            # sums. Material uploads mostly round half ties down in native
            # captures, while effect resources round them up.
            quarters = np.rint(FxImage._area_mip(pixels) * 1020)
            return (np.floor((quarters + rounding) / 4) / 255).astype(np.float32)

        target_width = 1 << (self.width - 1).bit_length()
        target_height = 1 << (self.height - 1).bit_length()
        if (target_width, target_height) == (self.width, self.height):
            return FxImage(self.pixels, self.path, reduce_mip=reduce_mip)
        pixels = np.asarray(self.pixels, dtype=np.float32)
        for axis, size in ((1, target_width), (0, target_height)):
            original = pixels.shape[axis]
            if original == size:
                continue
            position = (np.arange(size, dtype=np.float32) + .5) * (original / size) - .5
            left = np.floor(position).astype(np.intp)
            shape = [1] * pixels.ndim
            shape[axis] = size
            fraction = (position - left).astype(np.float32).reshape(shape)
            lower = np.take(pixels, left.clip(0, original - 1), axis=axis)
            upper = np.take(pixels, (left + 1).clip(0, original - 1), axis=axis)
            pixels = lower + fraction * (upper - lower)
        # Native upload stores its resampled base in an 8-bit texture. The
        # device's triangle filter and dither still leave small byte residuals.
        pixels = np.floor(pixels * 255 + .5) / 255
        return FxImage(pixels, self.path, reduce_mip=reduce_mip)

    # Area averaging retains odd-size edges and signed numeric channels in encoded space.
    def mip_chain(self):
        if len(self.mips) > 1 or max(self.width, self.height) == 1:
            return self.mips
        pixels = self.pixels
        level_count = self.mip_levels or max(self.width, self.height).bit_length()
        reduce = self.reduce_mip or self._area_mip
        while len(self.mips) < level_count:
            pixels = reduce(pixels)
            self.mips.append(FxImage(pixels, self.path))
        return self.mips

    @staticmethod
    def _area_mip(pixels):
        for axis in (0, 1):
            size = pixels.shape[axis]
            if size == 1:
                continue
            count = size // 2
            scale = size / count
            start = np.arange(count) * scale
            first = np.floor(start).astype(np.int64)
            shape = [1] * pixels.ndim
            shape[axis] = count
            reduced = None
            for offset in range(int(np.ceil(scale)) + 1):
                indices = first + offset
                weights = np.maximum(0, np.minimum(start + scale, indices + 1) - np.maximum(start, indices)) / scale
                values = np.take(pixels, np.minimum(indices, size - 1), axis=axis) * weights.reshape(shape)
                reduced = values if reduced is None else reduced + values
            pixels = reduced
        return pixels

    # Explicit LOD bypasses footprint selection; implicit magnification keeps the MAG filter.
    def sample_filtered(self, u, v, info, lod=None, derivatives=None, dtype=np.float64, *, stage="ps"):
        state = info.state if info else lambda key, default='': default
        default_filter, default_mip = info.defaults(stage) if info else (('POINT', 'NONE') if stage == 'vs' else ('LINEAR', 'LINEAR'))
        # Explicit pixel LOD has no footprint for anisotropic filtering. Native
        # readouts of initialized slots use linear spatial filtering here.
        if default_filter is None and lod is not None and info.native_slot in ("MMD", "MMD_NONZERO", 0, 1, 2):
            default_filter = 'LINEAR'
        mip = state('mipfilter', state('filter', default_mip))
        if mip is None:
            raise FxUnsupported(f'Sampler {info.name!r} has an ambiguous native register; specify its mip filter explicitly.')
        if mip not in ('NONE', 'POINT', 'LINEAR'):
            raise FxUnsupported(f'Unimplemented numeric mip filter {mip!r}.')
        filters = [state(key, state('filter', default_filter)) for key in ('minfilter', 'magfilter')]
        if any(value is None for value in filters):
            raise FxUnsupported(f'Sampler {info.name!r} has an unverified native spatial-filter binding; specify min/mag filters explicitly.')
        if any(value not in ('NONE', 'POINT', 'LINEAR') for value in filters):
            raise FxUnsupported(f'Unimplemented numeric spatial filters {filters!r}.')
        default_address = info.default_address(stage) if info else 'WRAP'
        address = (state('addressu', default_address), state('addressv', default_address))
        if any(mode is None for mode in address):
            raise FxUnsupported(f'Sampler {info.name!r} has ambiguous native addressing; specify U/V address modes explicitly.')
        if any(mode not in ('WRAP', 'MIRROR', 'CLAMP', 'BORDER') for mode in address):
            raise FxUnsupported(f'Unimplemented numeric address modes {address!r}.')
        if lod is None:
            if derivatives is None:
                level = np.asarray(0.)
            else:
                du_dx, dv_dx, du_dy, dv_dy = derivatives
                rho = np.maximum(np.hypot(du_dx * self.width, dv_dx * self.height),
                                 np.hypot(du_dy * self.width, dv_dy * self.height))
                level = np.log2(np.maximum(rho, np.finfo(float).tiny))
            level = level + float(state('mipmaplodbias', '0'))
        else:
            level = np.asarray(lod, dtype=float)
        if not np.isfinite(level).all():
            raise FxUnsupported('Nonfinite texture level of detail.')
        u, v, level = np.broadcast_arrays(u, v, level)
        shape = u.shape
        magnify = level <= 0
        minimum = int(state('maxmiplevel', '0'))
        if minimum < 0:
            raise FxUnsupported('MAXMIPLEVEL must be nonnegative.')
        chain = self.mip_chain() if mip != 'NONE' and (minimum or np.any(level > 0)) else [self]
        level = np.clip(level, min(minimum, len(chain)-1), len(chain)-1) if mip != 'NONE' else np.zeros(shape)
        lower = np.floor(level + .5).astype(int) if mip == 'POINT' else np.floor(level).astype(int)
        upper = np.minimum(lower + 1, len(chain)-1) if mip == 'LINEAR' else lower
        fraction = level - lower if mip == 'LINEAR' else np.zeros(shape)
        result = np.empty((u.size, 4), dtype=dtype)
        uf, vf = u.ravel(), v.ravel()
        for is_mag in (False, True):
            point = filters[int(is_mag)] in ('NONE', 'POINT')
            mask = (magnify == is_mag).ravel()
            for index in np.unique(lower.ravel()[mask]):
                selected = mask & (lower.ravel() == index)
                sampled = np.stack(chain[index].sample(uf[selected], vf[selected], point, address, dtype), axis=-1)
                result[selected] = sampled
            if mip == 'LINEAR':
                for index in np.unique(upper.ravel()[mask]):
                    selected = mask & (upper.ravel() == index)
                    sampled = np.stack(chain[index].sample(uf[selected], vf[selected], point, address, dtype), axis=-1)
                    blend = fraction.ravel()[selected, None]
                    result[selected] = result[selected] * (1-blend) + sampled * blend
        return [result[:, channel].reshape(shape) for channel in range(4)]


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
