import math

from ..program import FxFunction
from ..types import FxValue
from .frame import FxFrame


# Intrinsics receive unevaluated nodes so they control evaluation order and masks themselves.
def fx_args(ex, nodes):
    return [ex.eval(n) for n in nodes]


# Factory for the one-argument math functions (sin, abs, ...), executed per component by the backend.
def fx_unary_intrinsic(name):
    def run(ex, nodes):
        (a,) = fx_args(ex, nodes)
        return ex.elementwise(lambda x: ex.B.unary(name, x), FxValue(("f", a.t[1]), a.c))

    return run


def fx_saturate_scalar(B, x):
    return B.min(B.max(x, 0.0), 1.0)


def fx_i_saturate(ex, nodes):
    (a,) = fx_args(ex, nodes)
    return FxValue(("f", a.t[1]), [fx_saturate_scalar(ex.B, x) for x in a.c])


def fx_i_clamp(ex, nodes):
    a, lo, hi = fx_args(ex, nodes)
    B = ex.B
    return ex.elementwise(lambda x, l, h: B.min(B.max(x, l), h), a, lo, hi)


def fx_i_min(ex, nodes):
    a, b = fx_args(ex, nodes)
    return ex.elementwise(ex.B.min, a, b)


def fx_i_max(ex, nodes):
    a, b = fx_args(ex, nodes)
    return ex.elementwise(ex.B.max, a, b)


def fx_i_pow(ex, nodes):
    a, b = fx_args(ex, nodes)
    return ex.elementwise(ex.B.pow, FxValue(("f", a.t[1]), a.c), b)


def fx_i_fmod(ex, nodes):
    a, b = fx_args(ex, nodes)
    return ex.elementwise(ex.B.mod, FxValue(("f", a.t[1]), a.c), b)


def fx_i_atan2(ex, nodes):
    a, b = fx_args(ex, nodes)
    return ex.elementwise(ex.B.atan2, FxValue(("f", a.t[1]), a.c), b)


def fx_i_lerp(ex, nodes):
    a, b, t = fx_args(ex, nodes)
    B = ex.B
    return ex.elementwise(lambda x, y, s: B.add(x, B.mul(B.sub(y, x), s)), FxValue(("f", a.t[1]), a.c), b, t)


def fx_i_step(ex, nodes):
    a, b = fx_args(ex, nodes)
    B = ex.B
    return ex.elementwise(lambda edge, x: B.cmp(">=", x, edge), FxValue(("f", a.t[1]), a.c), b)


def fx_i_smoothstep(ex, nodes):
    lo, hi, x = fx_args(ex, nodes)
    B = ex.B

    def run(a, b, v):
        t = fx_saturate_scalar(B, B.div(B.sub(v, a), B.sub(b, a)))
        return B.mul(B.mul(t, t), B.sub(3.0, B.mul(2.0, t)))

    return ex.elementwise(run, FxValue(("f", lo.t[1]), lo.c), hi, x)


def fx_dot_comps(B, a, b):
    total = None
    for x, y in zip(a, b):
        term = B.mul(x, y)
        total = term if total is None else B.add(total, term)
    return total if total is not None else B.const(0.0)


def fx_i_dot(ex, nodes):
    a, b = fx_args(ex, nodes)
    _dims, ca, cb = ex.broadcast(a, b)
    return FxValue(("f", ()), [fx_dot_comps(ex.B, ca, cb)])


def fx_i_cross(ex, nodes):
    a, b = fx_args(ex, nodes)
    B = ex.B
    ax, ay, az = (a.c + [B.const(0.0)] * 3)[:3]
    bx, by, bz = (b.c + [B.const(0.0)] * 3)[:3]
    return FxValue(
        ("f", (3,)),
        [
            B.sub(B.mul(ay, bz), B.mul(az, by)),
            B.sub(B.mul(az, bx), B.mul(ax, bz)),
            B.sub(B.mul(ax, by), B.mul(ay, bx)),
        ],
    )


def fx_length_comps(B, comps):
    return B.unary("sqrt", fx_dot_comps(B, comps, comps))


def fx_i_length(ex, nodes):
    (a,) = fx_args(ex, nodes)
    return FxValue(("f", ()), [fx_length_comps(ex.B, a.c)])


def fx_i_distance(ex, nodes):
    a, b = fx_args(ex, nodes)
    diff = ex.binary("-", a, b)
    return FxValue(("f", ()), [fx_length_comps(ex.B, diff.c)])


def fx_safe_normalize(B, comps):
    length = fx_length_comps(B, comps)
    inv = B.select(B.cmp(">", length, 1e-20), B.div(1.0, length), 0.0)
    return [B.mul(x, inv) for x in comps]


def fx_i_normalize(ex, nodes):
    (a,) = fx_args(ex, nodes)
    return FxValue(("f", a.t[1]), fx_safe_normalize(ex.B, a.c))


def fx_i_reflect(ex, nodes):
    i, n = fx_args(ex, nodes)
    B = ex.B
    d = B.mul(2.0, fx_dot_comps(B, i.c, n.c))
    return FxValue(("f", i.t[1]), [B.sub(x, B.mul(d, y)) for x, y in zip(i.c, n.c)])


def fx_i_refract(ex, nodes):
    i, n, eta = fx_args(ex, nodes)
    B = ex.B
    e = eta.c[0]
    cosi = fx_dot_comps(B, i.c, n.c)
    k = B.sub(1.0, B.mul(B.mul(e, e), B.sub(1.0, B.mul(cosi, cosi))))
    root = B.unary("sqrt", B.max(k, 0.0))
    factor = B.add(B.mul(e, cosi), root)
    comps = [B.sub(B.mul(e, x), B.mul(factor, y)) for x, y in zip(i.c, n.c)]
    ok = B.cmp(">=", k, 0.0)
    return FxValue(("f", i.t[1]), [B.select(ok, x, 0.0) for x in comps])


def fx_i_faceforward(ex, nodes):
    n, i, ng = fx_args(ex, nodes)
    B = ex.B
    d = fx_dot_comps(B, i.c, ng.c)
    flip = B.cmp("<", d, 0.0)
    return FxValue(("f", n.t[1]), [B.select(flip, x, B.neg(x)) for x in n.c])


# HLSL mul with row-vector conventions: vector*matrix, matrix*vector, matrix*matrix, with dimensions clipped
# to the smaller shared size.
def fx_i_mul(ex, nodes):
    a, b = fx_args(ex, nodes)
    B = ex.B
    da, db = a.t[1], b.t[1]
    if len(da) == 0 or len(db) == 0:
        return ex.binary("*", a, b)
    if len(da) == 1 and len(db) == 1:
        return FxValue(("f", ()), [fx_dot_comps(B, a.c, b.c)])
    if len(da) == 1 and len(db) == 2:
        rows, cols = db
        k = min(da[0], rows)
        out = []
        for j in range(cols):
            out.append(fx_dot_comps(B, a.c[:k], [b.c[i * cols + j] for i in range(k)]))
        return FxValue(("f", (cols,)), out)
    if len(da) == 2 and len(db) == 1:
        rows, cols = da
        k = min(cols, db[0])
        out = []
        for i in range(rows):
            out.append(fx_dot_comps(B, a.c[i * cols:i * cols + k], b.c[:k]))
        return FxValue(("f", (rows,)), out)
    r1, c1 = da
    r2, c2 = db
    k = min(c1, r2)
    out = []
    for i in range(r1):
        for j in range(c2):
            out.append(fx_dot_comps(B, [a.c[i * c1 + m] for m in range(k)], [b.c[m * c2 + j] for m in range(k)]))
    return FxValue(("f", (r1, c2)), out)


def fx_i_transpose(ex, nodes):
    (a,) = fx_args(ex, nodes)
    if len(a.t[1]) != 2:
        return a
    r, c = a.t[1]
    return FxValue((a.t[0], (c, r)), [a.c[i * c + j] for j in range(c) for i in range(r)])


def fx_i_determinant(ex, nodes):
    (a,) = fx_args(ex, nodes)
    B = ex.B
    n = a.t[1][0]

    def det(rows, cols):
        if len(rows) == 1:
            return a.c[rows[0] * n + cols[0]]
        total = None
        for k, col in enumerate(cols):
            sub = det(rows[1:], cols[:k] + cols[k + 1:])
            term = B.mul(a.c[rows[0] * n + col], sub)
            if k % 2:
                term = B.neg(term)
            total = term if total is None else B.add(total, term)
        return total

    return FxValue(("f", ()), [det(list(range(n)), list(range(n)))])


def fx_i_all(ex, nodes):
    (a,) = fx_args(ex, nodes)
    B = ex.B
    total = B.const(1.0)
    for x in a.c:
        total = B.mul(total, B.truth(x))
    return FxValue(("b", ()), [total])


def fx_i_any(ex, nodes):
    (a,) = fx_args(ex, nodes)
    B = ex.B
    total = B.const(0.0)
    for x in a.c:
        total = B.max(total, B.truth(x))
    return FxValue(("b", ()), [total])


# clip(x) discards the pixel when any component is negative; recorded in the discard mask under the current
# execution mask.
def fx_i_clip(ex, nodes):
    (a,) = fx_args(ex, nodes)
    B = ex.B
    hit = B.const(0.0)
    for x in a.c:
        hit = B.max(hit, B.cmp("<", x, 0.0))
    active = ex.active()
    known = B.known(active)
    if known is None:
        hit = B.mul(hit, active)
    elif not known:
        return FxValue("void", None)
    ex.discard = ex.or_mask(ex.discard, hit)
    return FxValue("void", None)


# Screen-space derivatives (ddx, ddy, fwidth) have no meaning when shading per vertex or per node, so they
# evaluate to zero.
def fx_i_zero_like(ex, nodes):
    (a,) = fx_args(ex, nodes)
    return FxValue(("f", a.t[1]), [ex.B.const(0.0)] * len(a.c))


def fx_i_radians(ex, nodes):
    (a,) = fx_args(ex, nodes)
    return ex.elementwise(lambda x: ex.B.mul(x, math.pi / 180.0), FxValue(("f", a.t[1]), a.c))


def fx_i_degrees(ex, nodes):
    (a,) = fx_args(ex, nodes)
    return ex.elementwise(lambda x: ex.B.mul(x, 180.0 / math.pi), FxValue(("f", a.t[1]), a.c))


def fx_i_isnan(ex, nodes):
    (a,) = fx_args(ex, nodes)
    return FxValue(("b", a.t[1]), [ex.B.cmp("!=", x, x) for x in a.c])


def fx_i_sincos(ex, nodes):
    a = ex.eval(nodes[0])
    s = ex.elementwise(lambda x: ex.B.unary("sin", x), FxValue(("f", a.t[1]), a.c))
    c = ex.elementwise(lambda x: ex.B.unary("cos", x), FxValue(("f", a.t[1]), a.c))
    ex.assign(nodes[1], s)
    ex.assign(nodes[2], c)
    return FxValue("void", None)


def fx_i_ldexp(ex, nodes):
    a, b = fx_args(ex, nodes)
    B = ex.B
    return ex.elementwise(lambda x, e: B.mul(x, B.unary("exp2", e)), FxValue(("f", a.t[1]), a.c), b)


# HLSL lit(): ambient, diffuse and specular lighting coefficients.
def fx_i_lit(ex, nodes):
    ndotl, ndoth, m = fx_args(ex, nodes)
    B = ex.B
    d = ndotl.c[0]
    h = ndoth.c[0]
    spec = B.select(B.mul(B.cmp(">", d, 0.0), B.cmp(">", h, 0.0)), B.pow(B.max(h, 0.0), m.c[0]), 0.0)
    return FxValue(("f", (4,)), [B.const(1.0), B.max(d, 0.0), spec, B.const(1.0)])


# asfloat is an identity here: values are already carried as floats.
def fx_i_as_is(ex, nodes):
    (a,) = fx_args(ex, nodes)
    return a


# Factory for tex2D, tex2Dlod, tex2Dproj and tex1D; the mode decides how the coordinate is turned into u, v
# and level of detail.
def fx_texture_call(mode):
    def run(ex, nodes):
        sampler = ex.eval(nodes[0])
        coord = ex.eval(nodes[1])
        B = ex.B
        info = sampler.c
        comps = coord.c
        u = comps[0]
        v = comps[1] if len(comps) > 1 else B.const(0.0)
        lod = None
        if mode == "lod":
            lod = comps[3] if len(comps) > 3 else B.const(0.0)
        elif mode == "proj" and len(comps) > 3:
            u = B.div(u, comps[3])
            v = B.div(v, comps[3])
        elif mode == "1d":
            v = B.const(0.5)
        return ex.sample(info, u, v, lod)

    return run


# Name to implementation table. Native single-argument functions are added below by a loop.
FX_INTRINSICS = {
    "saturate": fx_i_saturate,
    "clamp": fx_i_clamp,
    "min": fx_i_min,
    "max": fx_i_max,
    "pow": fx_i_pow,
    "fmod": fx_i_fmod,
    "atan2": fx_i_atan2,
    "lerp": fx_i_lerp,
    "step": fx_i_step,
    "smoothstep": fx_i_smoothstep,
    "dot": fx_i_dot,
    "cross": fx_i_cross,
    "length": fx_i_length,
    "distance": fx_i_distance,
    "normalize": fx_i_normalize,
    "reflect": fx_i_reflect,
    "refract": fx_i_refract,
    "faceforward": fx_i_faceforward,
    "mul": fx_i_mul,
    "transpose": fx_i_transpose,
    "determinant": fx_i_determinant,
    "all": fx_i_all,
    "any": fx_i_any,
    "clip": fx_i_clip,
    "ddx": fx_i_zero_like,
    "ddy": fx_i_zero_like,
    "ddx_coarse": fx_i_zero_like,
    "ddy_coarse": fx_i_zero_like,
    "ddx_fine": fx_i_zero_like,
    "ddy_fine": fx_i_zero_like,
    "fwidth": fx_i_zero_like,
    "radians": fx_i_radians,
    "degrees": fx_i_degrees,
    "isnan": fx_i_isnan,
    "sincos": fx_i_sincos,
    "ldexp": fx_i_ldexp,
    "lit": fx_i_lit,
    "asfloat": fx_i_as_is,
    "asint": fx_i_as_is,
    "asuint": fx_i_as_is,
    "tex2D": fx_texture_call("plain"),
    "tex2Dlod": fx_texture_call("lod"),
    "tex2Dbias": fx_texture_call("plain"),
    "tex2Dgrad": fx_texture_call("plain"),
    "tex2Dproj": fx_texture_call("proj"),
    "tex1D": fx_texture_call("1d"),
    "tex1Dlod": fx_texture_call("lod"),
    "tex3D": fx_texture_call("plain"),
    "tex3Dlod": fx_texture_call("lod"),
    "texCUBE": fx_texture_call("plain"),
    "texCUBElod": fx_texture_call("lod"),
}


for _name in (
    "abs",
    "sign",
    "floor",
    "ceil",
    "trunc",
    "round",
    "frac",
    "sqrt",
    "rsqrt",
    "exp",
    "exp2",
    "log",
    "log2",
    "log10",
    "sin",
    "cos",
    "tan",
    "asin",
    "acos",
    "atan",
    "sinh",
    "cosh",
    "tanh",
):
    FX_INTRINSICS[_name] = fx_unary_intrinsic(_name)


FX_DUMMY_FUNCTION = FxFunction("__intrinsic", "float", None, [], ("block", []))


# Calls an intrinsic with already-evaluated values by binding them to temporary names in a dummy frame; needed
# when node backends fall back to the generic implementation.
def fx_intrinsic_values(ex, name, values):
    pushed = False
    if not ex.frames:
        ex.frames.append(FxFrame(FX_DUMMY_FUNCTION))
        pushed = True
    frame = ex.frames[-1]
    frame.scopes.append({"__a%d" % i: v for i, v in enumerate(values)})
    try:
        return FX_INTRINSICS[name](ex, [("id", "__a%d" % i) for i in range(len(values))])
    finally:
        frame.scopes.pop()
        if pushed:
            ex.frames.pop()
