import numpy as np

from .types import (
    FxValue,
    fx_count,
    fx_is_numeric,
)


# Builds the vertex-shader input provider from mesh arrays. Unknown semantics read as zero (colour as one), so
# shaders asking for tangents or weights still run.
# Constant columns are made in the dtype of the positions: numpy is several times slower on a float64 array narrowed
# in every operation, and the backend casts each operand to its dtype anyway, so the values the shader computes are
# the same.
def fx_vertex_inputs(positions, normals, uvs, indices, colors=None):
    dtype = positions.dtype if positions.dtype.kind == "f" else np.float64
    columns = {}

    def constant(value, n):
        found = columns.get(value)
        if found is None:
            found = columns[value] = np.full(n, value, dtype=dtype)
        return found

    def provide(ex):
        B = ex.B

        def get(semantic, t):
            n = len(positions)
            if semantic == "POSITION0":
                comps = [positions[:, 0], positions[:, 1], positions[:, 2], constant(1.0, n)]
            elif semantic == "NORMAL0":
                comps = [normals[:, 0], normals[:, 1], normals[:, 2], constant(0.0, n)]
            elif semantic == "TEXCOORD0":
                comps = [uvs[:, 0], uvs[:, 1], constant(0.0, n), constant(1.0, n)]
            elif semantic in ("_INDEX", "INDEX"):
                comps = [indices.astype(np.float64)]
            elif semantic == "COLOR0" and colors is not None:
                comps = [colors[:, k] for k in range(4)]
            elif semantic in ("COLOR0", "COLOR1"):
                comps = [constant(1.0, n)] * 4
            else:
                comps = [constant(0.0, n), constant(0.0, n), constant(0.0, n), constant(1.0, n)]
            if not fx_is_numeric(t):
                return ex.zero(t)
            count = fx_count(t[1])
            comps = (comps + [np.zeros(n)] * 16)[:count]
            return FxValue(("f", t[1]), comps)

        return get

    return provide


# Maps vertex-shader clip output back to world space with the inverse view-projection, for effects that move
# vertices in clip space.
# z is replaced by the depth implied by w; vertices outside the clip volume or behind the camera are flagged
# so the caller can drop them.
def fx_clip_to_world(clip, view_proj_inverse, proj):
    x, y, z, w = clip
    a = proj[2, 2]
    b = proj[3, 2]
    w = np.asarray(w, dtype=np.float64)
    z = np.asarray(z, dtype=np.float64)
    tiny = np.abs(w) <= 1e-12
    natural = a * w + b
    front = w > 1e-12
    outside = front & ((z < -1e-6 * np.maximum(1.0, np.abs(w))) | (z > w * (1 + 1e-9) + 1e-6))
    behind = (w < -1e-12) & (np.abs(z - natural) > 1e-4 * (np.abs(w) + 1.0))
    culled = tiny | outside | behind
    homo = np.stack([np.asarray(x, dtype=np.float64) * np.ones_like(w), np.asarray(y, dtype=np.float64) * np.ones_like(w), natural, w], axis=-1)
    world = homo @ view_proj_inverse
    ww = world[:, 3:4]
    ww = np.where(np.abs(ww) < 1e-20, 1.0, ww)
    pos = world[:, :3] / ww
    pos = np.where(np.isfinite(pos), pos, 0.0)
    culled = culled | ~np.isfinite(world).all(axis=1)
    return pos, culled


# Same culling test as fx_clip_to_world without the matrix inverse, plus non-finite values.
def fx_clip_culled(clip, proj):
    x, y, z, w = clip
    a = proj[2, 2]
    b = proj[3, 2]
    w = np.asarray(w)
    z = np.asarray(z)
    tiny = np.abs(w) <= 1e-12
    front = w > 1e-12
    outside = front & ((z < -1e-6 * np.maximum(1.0, np.abs(w))) | (z > w * (1 + 1e-9) + 1e-6))
    behind = (w < -1e-12) & (np.abs(z - (a * w + b)) > 1e-4 * (np.abs(w) + 1.0))
    finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(w)
    return tiny | outside | behind | ~finite
