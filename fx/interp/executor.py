from ..constants import FX_CALL_DEPTH
from ..errors import (
    FxError,
    FxUnsupported,
)
from ..program import FxStruct
from ..types import (
    FxSampler,
    FxValue,
    fx_array_type,
    fx_count,
    fx_is_array,
    fx_is_numeric,
    fx_resolve_type,
)
from .calls import FxCalls
from .expressions import FxExpressions
from .frame import FxFrame
from .statements import FxStatements


# Interpreter for shader code, written against an abstract backend: numpy arrays, shader nodes or geometry
# nodes all use the same code.
# Control flow is turned into data flow: 'mask' holds the condition under which code runs, so both branches
# execute and results are blended with merge().
class FxExec(FxStatements, FxExpressions, FxCalls):
    def __init__(self, program, backend, env):
        self.program = program
        self.B = backend
        self.env = env
        self.cache = {}
        self.overrides = {}
        self.frames = []
        self.mask = 1.0
        self.discard = 0.0
        self.depth = 0
        self.evaluating = set()

    def zero(self, t):
        B = self.B
        if fx_is_numeric(t):
            return FxValue(t, [B.const(0.0)] * fx_count(t[1]))
        if isinstance(t, FxStruct):
            members = {}
            for name, type_name, _sem, array in t.members:
                mt = fx_resolve_type(type_name, self.program)
                if array is not None:
                    size = int(self.const_int(array))
                    members[name] = FxValue(fx_array_type(mt, size), [self.zero(mt) for _ in range(size)])
                else:
                    members[name] = self.zero(mt)
            return FxValue(t, members)
        if fx_is_array(t):
            return FxValue(t, [self.zero(t[1]) for _ in range(t[2])])
        return FxValue(t, None)

    def const_int(self, expr):
        value = self.eval(expr)
        known = self.B.known(value.c[0])
        if known is None:
            raise FxUnsupported("array size is not constant")
        return int(known)

    def scalar(self, x, base="f"):
        return FxValue((base, ()), [x])

    # Combines the branch mask with the early-return and the break/continue masks of the current function into
    # one execution mask, folding constants where it can.
    def active(self):
        B = self.B
        mask = self.mask
        if not self.frames:
            return mask
        frame = self.frames[-1]
        exclusions = [frame.done]
        for loop in frame.loops:
            exclusions.append(loop.brk)
            exclusions.append(loop.cont)
        for ex in exclusions:
            known = B.known(ex)
            if known is not None:
                if known == 0.0:
                    continue
                return 0.0
            inv = B.sub(1.0, ex)
            known_mask = B.known(mask)
            if known_mask is not None:
                mask = inv if known_mask != 0.0 else 0.0
            else:
                mask = B.mul(mask, inv)
        return mask

    def inactive(self):
        known = self.B.known(self.active())
        return known is not None and known == 0.0

    def lookup(self, name):
        if self.frames:
            for scope in reversed(self.frames[-1].scopes):
                if name in scope:
                    return scope, name
        return None, name

    # Looks a global up in overrides, then the cache, then asks the environment (scene values, parameters,
    # textures) and finally the declared default.
    # 'evaluating' catches globals that depend on themselves.
    def global_value(self, name):
        if name in self.overrides:
            return self.overrides[name]
        if name in self.cache:
            return self.cache[name]
        g = self.program.globals.get(name)
        if g is None:
            raise FxError("unknown identifier %s" % name)
        if name in self.evaluating:
            raise FxError("recursive global %s" % name)
        self.evaluating.add(name)
        saved_frames, saved_mask = self.frames, self.mask
        self.frames, self.mask = [], 1.0
        try:
            value = self.env.global_value(self, g)
            if value is None:
                value = self.default_global(g)
        finally:
            self.frames, self.mask = saved_frames, saved_mask
            self.evaluating.discard(name)
        self.cache[name] = value
        if self.B.wants_text:
            self.tag(value, name)
        return value

    # Value of a global the environment does not supply: samplers and textures become descriptors, others
    # evaluate their initialiser.
    def default_global(self, g):
        t = fx_resolve_type(g.type, self.program)
        if t == "sampler":
            texture = None
            states = {}
            if g.init is not None and g.init[0] == "sampler_state":
                states = g.init[1]
                tex = states.get("texture")
                if tex:
                    texture = "".join(tex).strip("<>() ")
            return FxValue("sampler", FxSampler(g.name, texture, states, g.register))
        if t == "texture":
            return FxValue("texture", g.name)
        if t == "string":
            return FxValue("string", self.eval(g.init).c if g.init is not None else "")
        if g.array is not None and not fx_is_array(t):
            size = self.const_int(g.array) if g.array is not None else None
            at = fx_array_type(t, size)
            if g.init is not None:
                return self.convert(self.eval_init(g.init, at), at)
            return self.zero(at)
        if g.init is None:
            return self.zero(t)
        if g.init[0] == "init":
            return self.eval_init(g.init, t)
        if g.init[0] == "compile":
            return FxValue("void", None)
        return self.convert(self.eval(g.init), t)

    # Fills a type from an HLSL initialiser list {..}; scalars are broadcast and missing parts are zero.
    def eval_init(self, node, t):
        if fx_is_numeric(t):
            comps = []
            for item in self.flatten_init(node):
                comps.extend(item.c if isinstance(item.c, list) else [item.c])
            n = fx_count(t[1])
            if len(comps) == 1 and n > 1:
                comps = comps * n
            comps = (comps + [self.B.const(0.0)] * n)[:n]
            return self.convert(FxValue(("f", t[1]), comps), t)
        if fx_is_array(t):
            items = node[1] if node[0] == "init" else [node]
            elems = []
            flat = None
            if fx_is_numeric(t[1]) and items and not (isinstance(items[0], tuple) and items[0][0] == "init"):
                flat = []
                for item in items:
                    flat.extend(self.eval(item).c)
                per = fx_count(t[1][1])
                if len(flat) >= per * t[2] and len(items) != t[2]:
                    for k in range(t[2]):
                        elems.append(self.convert(FxValue(("f", t[1][1]), flat[k * per:(k + 1) * per]), t[1]))
                    return FxValue(t, elems)
            for k in range(t[2]):
                if k < len(items):
                    item = items[k]
                    if isinstance(item, tuple) and item[0] == "init":
                        elems.append(self.eval_init(item, t[1]))
                    else:
                        elems.append(self.convert(self.eval(item), t[1]))
                else:
                    elems.append(self.zero(t[1]))
            return FxValue(t, elems)
        if isinstance(t, FxStruct):
            items = node[1] if node[0] == "init" else [node]
            value = self.zero(t)
            flat = []
            for item in items:
                if isinstance(item, tuple) and item[0] == "init":
                    flat.append(item)
                else:
                    flat.append(self.eval(item))
            k = 0
            for name, type_name, _sem, _array in t.members:
                mt = value.c[name].t
                if k >= len(flat):
                    break
                item = flat[k]
                if isinstance(item, tuple):
                    value.c[name] = self.eval_init(item, mt)
                    k += 1
                elif fx_is_numeric(mt) and fx_is_numeric(item.t) and fx_count(item.t[1]) < fx_count(mt[1]):
                    comps = []
                    while k < len(flat) and len(comps) < fx_count(mt[1]):
                        comps.extend(flat[k].c)
                        k += 1
                    value.c[name] = self.convert(FxValue(("f", mt[1]), comps[: fx_count(mt[1])]), mt)
                else:
                    value.c[name] = self.convert(item, mt)
                    k += 1
            return value
        return self.eval(node)

    def flatten_init(self, node):
        if isinstance(node, tuple) and node[0] == "init":
            out = []
            for item in node[1]:
                out.extend(self.flatten_init(item))
            return out
        return [self.eval(node)]

    # Implicit HLSL conversions: scalar broadcast, truncation, zero padding, matrix resize, and float to
    # int/bool with truncation.
    def convert(self, value, t):
        B = self.B
        if t is None or t == "void":
            return value
        if fx_is_numeric(t):
            if not fx_is_numeric(value.t):
                if isinstance(value.t, FxStruct):
                    raise FxError("cannot convert struct to %s" % (t,))
                raise FxError("cannot convert %s to %s" % (value.t, t))
            base, dims = t
            sbase, sdims = value.t
            comps = value.c
            n = fx_count(dims)
            if len(dims) == 2 and len(sdims) == 2 and sdims != dims:
                r, c = dims
                sr, sc = sdims
                out = []
                for i in range(r):
                    for j in range(c):
                        out.append(comps[i * sc + j] if i < sr and j < sc else B.const(0.0))
                comps = out
            elif len(comps) == 1 and n > 1:
                comps = comps * n
            elif len(comps) >= n:
                comps = comps[:n]
            else:
                comps = comps + [B.const(0.0)] * (n - len(comps))
            if base != sbase:
                if base == "i":
                    comps = [B.unary("trunc", x) for x in comps]
                elif base == "b":
                    comps = [B.truth(x) for x in comps]
            return FxValue(t, list(comps))
        if isinstance(t, FxStruct):
            if isinstance(value.t, FxStruct):
                return FxValue(t, dict(value.c))
            if fx_is_numeric(value.t):
                out = {}
                for name, type_name, _sem, array in t.members:
                    mt = fx_resolve_type(type_name, self.program)
                    if array is not None:
                        size = int(self.const_int(array))
                        out[name] = FxValue(fx_array_type(mt, size), [self.convert(value, mt) for _ in range(size)])
                    elif fx_is_numeric(mt):
                        out[name] = self.convert(FxValue(value.t, [value.c[0]]), mt)
                    else:
                        out[name] = self.convert(value, mt)
                return FxValue(t, out)
        if fx_is_array(t):
            if fx_is_array(value.t):
                return FxValue(t, [self.convert(v, t[1]) for v in value.c][: t[2]])
        return value

    # Calls a function in a fresh frame with the mask reset. Out and inout parameters are returned so the
    # caller can write them back.
    def run_function(self, function, args):
        if self.depth > FX_CALL_DEPTH:
            raise FxUnsupported("call depth exceeded")
        frame = FxFrame(function)
        scope = frame.scopes[0]
        for param, arg in zip(function.params, args):
            pt = fx_resolve_type(param.type, self.program)
            if param.array is not None and not fx_is_array(pt):
                pt = fx_array_type(pt, self.const_int(param.array))
            if arg is None:
                scope[param.name] = self.zero(pt) if param.default is None else self.convert(self.eval(param.default), pt)
            else:
                scope[param.name] = self.convert(arg, pt)
        saved_mask = self.mask
        self.mask = 1.0
        self.frames.append(frame)
        self.depth += 1
        self.B.enter(function.name)
        try:
            self.exec_block(function.body[1])
        finally:
            self.B.leave()
            self.depth -= 1
            self.frames.pop()
            self.mask = saved_mask
        rt = fx_resolve_type(function.ret, self.program)
        result = frame.ret if frame.ret is not None else (self.zero(rt) if rt != "void" else FxValue("void", None))
        outs = {}
        for param in function.params:
            if "out" in param.quals or "inout" in param.quals:
                outs[param.name] = scope[param.name]
        return result, outs






































