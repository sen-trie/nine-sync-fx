import math
import numpy as np

from .annotations import fx_annotation
from .types import (
    FxSampler,
    FxValue,
    fx_count,
    fx_is_numeric,
)


# Left-handed row-vector projection as MMD/DirectX use it (depth 0..1).
def fx_lh_perspective(fov_y, aspect, near, far):
    ys = 1.0 / math.tan(fov_y * 0.5)
    xs = ys / aspect
    q = far / (far - near)
    return np.array(
        [[xs, 0, 0, 0], [0, ys, 0, 0], [0, 0, q, 1], [0, 0, -near * q, 0]],
        dtype=np.float64,
    )


# Left-handed view matrix for row vectors; falls back to a fixed axis when forward and up are parallel.
def fx_lh_look(eye, forward, up):
    f = np.asarray(forward, dtype=np.float64)
    f = f / (np.linalg.norm(f) or 1.0)
    u = np.asarray(up, dtype=np.float64)
    r = np.cross(u, f)
    if np.linalg.norm(r) < 1e-9:
        r = np.cross(np.array([0.0, 0.0, 1.0]), f)
    r = r / (np.linalg.norm(r) or 1.0)
    u = np.cross(f, r)
    e = np.asarray(eye, dtype=np.float64)
    return np.array(
        [
            [r[0], u[0], f[0], 0],
            [r[1], u[1], f[1], 0],
            [r[2], u[2], f[2], 0],
            [-r.dot(e), -u.dot(e), -f.dot(e), 1],
        ],
        dtype=np.float64,
    )


# Left-handed orthographic projection, the light-space matrix for shadow effects.
def fx_lh_ortho(width, height, near, far):
    return np.array(
        [
            [2.0 / width, 0, 0, 0],
            [0, 2.0 / height, 0, 0],
            [0, 0, 1.0 / (far - near), 0],
            [0, 0, -near / (far - near), 1],
        ],
        dtype=np.float64,
    )


FX_NEAR = 1e-3


FX_FAR = 1e7


FX_MATRIX_BASES = ("WORLDVIEWPROJECTION", "VIEWPROJECTION", "WORLDVIEW", "PROJECTION", "WORLD", "VIEW")


FX_MATERIAL_SEMANTICS = {
    "DIFFUSE",
    "AMBIENT",
    "EMISSIVE",
    "SPECULAR",
    "SPECULARPOWER",
    "TOONCOLOR",
    "EDGECOLOR",
    "GROUNDSHADOWCOLOR",
}


FX_FLAG_DEFAULTS = {
    "parthf": 1.0,
    "transp": 0.0,
    "spadd": 0.0,
    "opadd": 0.0,
    "use_toon": 0.0,
    "use_spheremap": 0.0,
    "use_subtexture": 0.0,
    "use_selfshadow": 0.0,
}


# Splits WORLDVIEWPROJECTIONINVERSETRANSPOSE style semantics into the base matrix and the modifier.
def fx_split_matrix_semantic(semantic):
    for base in FX_MATRIX_BASES:
        if semantic.startswith(base):
            suffix = semantic[len(base):]
            if suffix in ("", "INVERSE", "TRANSPOSE", "INVERSETRANSPOSE"):
                return base, suffix
    return None


# Decides what a global is fed from: a scene matrix, camera/light vector, material colour, time, controller
# object, user parameter or flag.
# None means the effect owns the value. Cached per global.
def fx_classify_global(g, program):
    cache = program.__dict__.setdefault("_classified", {})
    hit = cache.get(g.name)
    if hit is not None and hit[0] is g:
        return hit[1]
    found = fx_classify_global_uncached(g, program)
    cache[g.name] = (g, found)
    return found


# Rules for what feeds a global: its semantic (matrix, position, direction, material, time, viewport,
# controller), then the built-in flag names, then user parameters.
def fx_classify_global_uncached(g, program):
    semantic = g.semantic
    obj = str(fx_annotation(g, "Object", program, "") or "").lower()
    if semantic:
        matrix = fx_split_matrix_semantic(semantic)
        if matrix is not None:
            return ("matrix", matrix[0], matrix[1], "light" if obj == "light" else "camera")
        if semantic == "POSITION":
            return ("position", "light" if obj == "light" else "camera")
        if semantic == "DIRECTION":
            return ("direction", "light" if obj == "light" else "camera")
        if semantic in FX_MATERIAL_SEMANTICS:
            if obj == "light":
                return ("light_color", semantic)
            return ("material", semantic)
        if semantic == "TIME":
            return ("time",)
        if semantic == "ELAPSEDTIME":
            return ("elapsed",)
        if semantic in ("VIEWPORTPIXELSIZE", "VIEWPORTSIZE"):
            return ("viewport",)
        if semantic == "CONTROLOBJECT":
            name = fx_annotation(g, "name", program, "") or ""
            item = fx_annotation(g, "item", program, "") or ""
            return ("control", str(name), str(item))
        if semantic in ("MOUSEPOSITION", "LEFTMOUSEDOWN", "MIDDLEMOUSEDOWN", "RIGHTMOUSEDOWN"):
            return ("zero",)
        return None
    if g.init is None and g.name in FX_FLAG_DEFAULTS:
        return ("flag", g.name)
    if g.init is None and g.name == "use_texture":
        return ("flag", g.name)
    if g.name in getattr(program, "params", {}):
        return ("param", g.name)
    return None


# True for sources that change per frame or per edit and therefore cannot be baked into a constant.
def fx_source_is_runtime(source):
    if source is None:
        return False
    return source[0] in ("matrix", "position", "direction", "light_color", "time", "elapsed", "viewport", "control", "param")


# The scene values the numpy backend feeds to semantic globals: matrices, camera, light, time, controls,
# parameters, material.
# Defaults match MMD's default camera and light.
class FxSceneState:
    def __init__(self):
        self.world = np.identity(4)
        self.view = fx_lh_look((0.0, 10.0, -45.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))
        self.proj = fx_lh_perspective(math.radians(30.0), 16.0 / 9.0, FX_NEAR, FX_FAR)
        self.camera_pos = np.array([0.0, 10.0, -45.0])
        self.camera_dir = np.array([0.0, 0.0, 1.0])
        self.light_dir = np.array([-0.5, -1.0, 0.5])
        self.light_color = np.array([154.0, 154.0, 154.0]) / 255.0
        self.time = 0.0
        self.elapsed = 1.0 / 30.0
        self.viewport = (1920.0, 1080.0)
        self.controls = {}
        self.params = {}
        self.flags = dict(FX_FLAG_DEFAULTS)
        self.material = {
            "diffuse": (1.0, 1.0, 1.0, 1.0),
            "ambient": (1.0, 1.0, 1.0),
            "specular": (0.0, 0.0, 0.0),
            "power": 0.0,
            "emissive": (0.0, 0.0, 0.0),
            "texture": "",
        }

    # MME shadow effects expect a light view/projection; this builds a fixed orthographic one 1000 units back
    # along the light direction.
    def light_matrices(self):
        direction = self.light_dir / (np.linalg.norm(self.light_dir) or 1.0)
        eye = -direction * 1000.0
        view = fx_lh_look(eye, direction, (0.0, 1.0, 0.0))
        proj = fx_lh_ortho(400.0, 400.0, 1.0, 4000.0)
        return view, proj

    def matrix(self, base, suffix, target):
        if target == "light":
            view, proj = self.light_matrices()
        else:
            view, proj = self.view, self.proj
        world = self.world
        if base == "WORLD":
            m = world
        elif base == "VIEW":
            m = view
        elif base == "PROJECTION":
            m = proj
        elif base == "WORLDVIEW":
            m = world @ view
        elif base == "VIEWPROJECTION":
            m = view @ proj
        else:
            m = world @ view @ proj
        if "INVERSE" in suffix:
            try:
                m = np.linalg.inv(m)
            except np.linalg.LinAlgError:
                m = np.identity(4)
        if "TRANSPOSE" in suffix:
            m = m.T
        return [float(x) for x in np.asarray(m).reshape(-1)]

    # Flat component list for a classified global.
    def values(self, source, g):
        kind = source[0]
        if kind == "matrix":
            return self.matrix(source[1], source[2], source[3])
        if kind == "position":
            if source[1] == "light":
                d = self.light_dir / (np.linalg.norm(self.light_dir) or 1.0)
                return [float(x) for x in (-d * 1000.0)] + [1.0]
            return [float(x) for x in self.camera_pos] + [1.0]
        if kind == "direction":
            d = self.light_dir if source[1] == "light" else self.camera_dir
            d = d / (np.linalg.norm(d) or 1.0)
            return [float(x) for x in d] + [0.0]
        if kind == "light_color":
            if source[1] == "DIFFUSE":
                return [0.0, 0.0, 0.0, 1.0]
            return [float(x) for x in self.light_color] + [1.0]
        if kind == "material":
            m = self.material
            sem = source[1]
            if sem == "DIFFUSE":
                return list(m["diffuse"])
            if sem == "AMBIENT":
                return list(m["ambient"]) + [1.0]
            if sem == "EMISSIVE":
                return list(m["emissive"]) + [1.0]
            if sem == "SPECULAR":
                return list(m["specular"]) + [1.0]
            if sem == "SPECULARPOWER":
                return [float(m["power"])]
            if sem == "TOONCOLOR":
                return [1.0, 1.0, 1.0, 1.0]
            if sem == "EDGECOLOR":
                return [0.0, 0.0, 0.0, 1.0]
            return [0.0, 0.0, 0.0, 0.5]
        if kind == "time":
            return [float(self.time)]
        if kind == "elapsed":
            return [float(self.elapsed)]
        if kind == "viewport":
            return [float(self.viewport[0]), float(self.viewport[1])]
        if kind == "control":
            found = self.controls.get(g.name)
            if found is None:
                return [0.0]
            return list(found)
        if kind == "flag":
            if source[1] == "use_texture":
                return [1.0 if self.material.get("texture") else 0.0]
            return [float(self.flags.get(source[1], 0.0))]
        if kind == "param":
            return list(self.params.get(source[1], [0.0]))
        return [0.0]


# Turns the flat floats of a scene value into a value of the declared type (a 16-float matrix can fill a
# smaller matrix type).
def fx_value_from_list(ex, values, t):
    comps = [ex.B.const(float(v)) for v in values]
    if not fx_is_numeric(t):
        return None
    n = fx_count(t[1])
    if len(t[1]) == 2 and len(comps) == 16 and t[1] != (4, 4):
        full = FxValue(("f", (4, 4)), comps)
        return ex.convert(full, t)
    if len(comps) == 1 and n > 1:
        comps = comps * n
    comps = (comps + [ex.B.const(0.0)] * n)[:n]
    return ex.convert(FxValue(("f", t[1]), comps), t)


# The texture global a sampler refers to, if it has one.
def fx_sampler_texture_global(program, info):
    if info is None or not isinstance(info, FxSampler):
        return None
    if info.texture:
        return program.globals.get(info.texture)
    return None


# Render and depth targets only exist inside MMD's own multi-pass rendering, so they sample as zero here.
def fx_is_render_target(g):
    return g is not None and g.semantic is not None and g.semantic.startswith(("RENDERCOLORTARGET", "OFFSCREENRENDERTARGET", "RENDERDEPTHSTENCILTARGET"))
