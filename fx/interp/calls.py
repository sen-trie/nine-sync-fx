from ..constants import (
    FX_GROUP_INTRINSICS,
    FX_NATIVE_INTRINSICS,
)
from ..errors import (
    FxError,
    FxUnsupported,
)
from ..types import (
    FxSampler,
    FxValue,
    fx_is_numeric,
    fx_resolve_type,
)
from .intrinsics import (
    FX_INTRINSICS,
    fx_intrinsic_values,
)


# Function and intrinsic calls and texture sampling.
class FxCalls:
    # Chooses an overload: fits by argument count (defaults fill the rest), then prefers the one whose
    # parameter types match the argument types.
    def find_function(self, name, args):
        candidates = self.program.functions.get(name)
        if not candidates:
            return None
        fitting = [f for f in candidates if len(f.params) >= len(args) and all(
            p.default is not None for p in f.params[len(args):]
        )]
        if not fitting:
            return None
        if len(fitting) == 1:
            return fitting[0]
        best = None
        best_score = -1
        for f in fitting:
            score = 0
            for p, a in zip(f.params, args):
                try:
                    pt = fx_resolve_type(p.type, self.program)
                except FxError:
                    continue
                if fx_is_numeric(pt) and fx_is_numeric(a.t) and pt[1] == a.t[1]:
                    score += 2
                elif pt == a.t:
                    score += 2
            if score > best_score:
                best, best_score = f, score
        return best

    # User functions run inline (or as a shared node group when the backend supports it). Intrinsics may
    # become native nodes or shared groups on node backends,
    # otherwise they run through the generic implementation.
    def eval_call(self, node):
        name = node[1]
        arg_nodes = node[2]
        if name in self.program.functions:
            args = [self.eval(a) for a in arg_nodes]
            function = self.find_function(name, args)
            if function is not None:
                if self.B.wants_text:
                    grouped = self.B.call_group(self, function, args)
                    if grouped is not None:
                        return grouped
                result, outs = self.run_function(function, args)
                for param, arg_node in zip(function.params, arg_nodes):
                    if param.name in outs:
                        self.assign(arg_node, outs[param.name])
                return result
        intrinsic = FX_INTRINSICS.get(name)
        if intrinsic is None:
            raise FxUnsupported("unknown function %s" % name)
        if self.B.wants_text and (name in FX_GROUP_INTRINSICS or name in FX_NATIVE_INTRINSICS):
            args = [self.eval(a) for a in arg_nodes]
            native = self.B.native_intrinsic(self, name, args) if name in FX_NATIVE_INTRINSICS else None
            if native is not None:
                return native
            grouped = self.B.intrinsic_group(self, name, args) if name in FX_GROUP_INTRINSICS else None
            if grouped is not None:
                return grouped
            return fx_intrinsic_values(self, name, args)
        return intrinsic(self, arg_nodes)

    # DX10 style texture.Sample(sampler, uv) calls; only the sample family is supported.
    def eval_mcall(self, node):
        _kind, obj, name, args = node
        lower = name.lower()
        if lower in ("sample", "samplelevel", "samplebias", "samplegrad"):
            texture = self.eval(obj)
            sampler = self.eval(args[0])
            uv = self.eval(args[1])
            info = sampler.c
            if isinstance(info, FxSampler) and texture.t == "texture":
                info = FxSampler(info.name, texture.c, info.states, info.register)
            lod = None
            if lower == "samplelevel" and len(args) > 2:
                lod = self.eval(args[2]).c[0]
            return self.sample(info, uv.c[0], uv.c[1] if len(uv.c) > 1 else self.B.const(0.0), lod)
        raise FxUnsupported("method %s" % name)

    def sample(self, info, u, v, lod=None):
        comps = self.env.sample(self, info, u, v, lod)
        return FxValue(("f", (4,)), comps)

    # Applies a scalar function across components, broadcasting scalars against vectors.
    def elementwise(self, fn, *values):
        if len(values) == 1:
            value = values[0]
            return FxValue(("f" if value.t[0] != "i" else "i", value.t[1]), [fn(x) for x in value.c])
        dims = values[0].t[1]
        comps = [values[0].c]
        result_dims = dims
        current = values[0]
        for other in values[1:]:
            result_dims, ca, cb = self.broadcast(current, other)
            current = FxValue(("f", result_dims), ca)
            comps = [c if len(c) == len(ca) else (c * len(ca) if len(c) == 1 else c[: len(ca)]) for c in comps]
            comps.append(cb)
        size = max(len(c) for c in comps)
        comps = [c * size if len(c) == 1 else c[:size] for c in comps]
        base = "f" if any(v.t[0] == "f" for v in values) else "i"
        return FxValue((base, result_dims), [fn(*parts) for parts in zip(*comps)])
