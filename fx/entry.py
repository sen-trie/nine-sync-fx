from .program import FxStruct
from .types import (
    FxValue,
    fx_resolve_type,
    fx_array_type,
    fx_is_array,
)
from .errors import FxUnsupported


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
    def indexed(semantic, index):
        semantic = fx_norm_semantic(semantic)
        if not semantic:
            raise FxUnsupported("A shader interface array requires a semantic.")
        end = len(semantic)
        while end and semantic[end-1].isdigit():
            end -= 1
        return semantic[:end] + str(int(semantic[end:] or 0) + index)

    def read(type_name, semantic, array=None):
        t = fx_resolve_type(type_name, program)
        if array is None:
            return ex.convert(inputs(fx_norm_semantic(semantic), t), t)
        count = ex.const_int(array)
        if count is None or count < 0:
            raise FxUnsupported("Shader interface array size must be a nonnegative constant.")
        if not isinstance(t, tuple) or len(t) != 2 or len(t[1]) > 1:
            raise FxUnsupported("Shader interface arrays currently require scalar/vector elements.")
        return FxValue(fx_array_type(t, count), [ex.convert(inputs(indexed(semantic, i), t), t) for i in range(count)])

    def output(semantic, value):
        sem = fx_norm_semantic(semantic)
        if not sem:
            return
        if fx_is_array(value.t):
            for i, element in enumerate(value.c):
                if fx_is_array(element.t) or isinstance(element.t, FxStruct) or len(element.t[1]) > 1:
                    raise FxUnsupported("Shader interface arrays currently require scalar/vector elements.")
                outputs[indexed(sem, i)] = element
        else:
            outputs[sem] = value
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
            for name, type_name, semantic, array in pt.members:
                members[name] = read(type_name, semantic, array)
            args.append(FxValue(pt, members))
        else:
            args.append(read(param.type, param.semantic, param.array))
        if ex.B.wants_text:
            ex.tag(args[-1], param.name)
    result, outs = ex.run_function(function, args)
    outputs = {}
    rt = fx_resolve_type(function.ret, program)
    if isinstance(rt, FxStruct):
        for name, type_name, semantic, _array in rt.members:
            output(semantic, result.c[name])
    elif function.semantic and rt != "void":
        output(function.semantic, result)
    for param in function.params:
        if param.name in outs:
            value = outs[param.name]
            if isinstance(value.t, FxStruct):
                for name, type_name, semantic, _array in value.t.members:
                    output(semantic, value.c[name])
            elif param.semantic:
                output(param.semantic, value)
    return outputs
