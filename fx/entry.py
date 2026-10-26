from .program import FxStruct
from .types import (
    FxValue,
    fx_resolve_type,
)


# Overloads are matched by argument count only.
def fx_find_function(program, name, argc):
    for function in program.functions.get(name, []):
        uniform = [p for p in function.params if "uniform" in p.quals]
        if len(uniform) >= argc or len(function.params) >= argc:
            return function
    return None


# Resolves 'compile vs_3_0 Name(args)' to the function and its compile-time arguments.
def fx_entry(program, compiled):
    if compiled is None:
        return None, []
    _tag, profile, call = compiled
    if call[0] != "call":
        return None, []
    function = fx_find_function(program, call[1], len(call[2]))
    return function, call[2]


# Compile-time arguments bind to the uniform parameters; with no 'uniform' keyword they bind to the trailing
# parameters.
def fx_bind_entry(function, compile_args):
    uniform = [i for i, p in enumerate(function.params) if "uniform" in p.quals]
    if not uniform and compile_args:
        uniform = list(range(len(function.params) - len(compile_args), len(function.params)))
    bound = {}
    for slot, arg in zip(uniform, compile_args):
        bound[slot] = arg
    return bound


# Canonical semantic names: SV_ prefix removed, bare COLOR/TEXCOORD/... get index 0 and TARGET becomes COLOR0,
# so DX9 and DX10 style shaders look alike.
def fx_norm_semantic(semantic):
    if not semantic:
        return None
    s = semantic.upper()
    if s.startswith("SV_"):
        s = s[3:]
    if s in ("COLOR", "TEXCOORD", "POSITION", "NORMAL", "TANGENT", "BINORMAL", "BLENDWEIGHT", "BLENDINDICES", "TARGET"):
        s = s + "0"
    if s == "TARGET0":
        s = "COLOR0"
    return s


# Runs a shader entry point: arguments come from the 'inputs' callback by semantic (struct members
# individually), results are returned as {semantic: value},
# which merges the return value and out parameters.
def fx_run_entry(ex, function, compile_args, inputs):
    program = ex.program
    bound = fx_bind_entry(function, compile_args)
    args = []
    for index, param in enumerate(function.params):
        pt = fx_resolve_type(param.type, program)
        if index in bound:
            args.append(ex.convert(ex.eval(bound[index]), pt))
            continue
        if "out" in param.quals and "inout" not in param.quals:
            args.append(None)
            continue
        if isinstance(pt, FxStruct):
            members = {}
            for name, type_name, semantic, _array in pt.members:
                mt = fx_resolve_type(type_name, program)
                members[name] = ex.convert(inputs(fx_norm_semantic(semantic), mt), mt)
            args.append(FxValue(pt, members))
        else:
            args.append(ex.convert(inputs(fx_norm_semantic(param.semantic), pt), pt))
        if ex.B.wants_text:
            ex.tag(args[-1], param.name)
    result, outs = ex.run_function(function, args)
    outputs = {}
    rt = fx_resolve_type(function.ret, program)
    if isinstance(rt, FxStruct):
        for name, type_name, semantic, _array in rt.members:
            sem = fx_norm_semantic(semantic)
            if sem:
                outputs[sem] = result.c[name]
    elif function.semantic and rt != "void":
        outputs[fx_norm_semantic(function.semantic)] = result
    for param in function.params:
        if param.name in outs:
            value = outs[param.name]
            if isinstance(value.t, FxStruct):
                for name, type_name, semantic, _array in value.t.members:
                    sem = fx_norm_semantic(semantic)
                    if sem:
                        outputs[sem] = value.c[name]
            elif param.semantic:
                outputs[fx_norm_semantic(param.semantic)] = value
    return outputs
