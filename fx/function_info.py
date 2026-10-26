import hashlib

from ..common.constants import MME_CORE_VERSION
from .errors import FxError
from .params import fx_script_values
from .semantics import fx_classify_global
from .types import fx_resolve_type


# Deterministic text for an AST fragment (objects by type and name), used for hashing instead of repr().
def fx_stable(node):
    if isinstance(node, (tuple, list)):
        return "(" + ",".join(fx_stable(item) for item in node) + ")"
    if node is None or isinstance(node, (str, int, float, bool)):
        return repr(node)
    name = getattr(node, "name", None)
    return "<%s:%s>" % (type(node).__name__, name)


# Type as a Blender-facing name (float3, int, float4x4).
def fx_type_name(t):
    base = {"f": "float", "i": "int", "b": "bool"}.get(t[0], "float")
    dims = t[1]
    if not dims:
        return base
    if len(dims) == 1:
        return "%s%d" % (base, dims[0])
    return "%s%dx%d" % (base, dims[0], dims[1])


# Label for one component of a value (v.x, m[1][2]) in node names.
def fx_comp_label(name, t, k):
    dims = t[1]
    if not dims:
        return name
    if len(dims) == 2:
        return "%s[%d][%d]" % (name, k // dims[1], k % dims[1])
    if dims[0] <= 4:
        return "%s.%s" % (name, "xyzw"[k])
    return "%s[%d]" % (name, k)


# The texture a sampler global refers to, as plain text.
def fx_sampler_slot(program, g):
    if g is None or g.init is None or g.init[0] != "sampler_state":
        return None
    tex = g.init[1].get("texture")
    if not tex:
        return None
    return "".join(tex).strip("<>() ")


# True when a global's value cannot change at run time (no semantic, not a loop counter, only constant
# dependencies); memoised.
def fx_global_constant(program, name, seen=None):
    if seen is None:
        cache = program.__dict__.setdefault("_global_constant", {})
        if name not in cache:
            cache[name] = fx_global_constant_walk(program, name, set())
        return cache[name]
    return fx_global_constant_walk(program, name, seen)


# Recursive part of fx_global_constant; 'seen' breaks cycles between globals.
def fx_global_constant_walk(program, name, seen):
    if name in seen:
        return True
    seen.add(name)
    g = program.globals.get(name)
    if g is None:
        return True
    if name in set(fx_script_values(program, "loopgetindex")):
        return False
    if fx_classify_global(g, program) is not None:
        return False
    ids = set()

    def walk(node):
        if isinstance(node, tuple) and node:
            if node[0] == "id" and len(node) > 1 and isinstance(node[1], str):
                ids.add(node[1])
            for item in node[1:]:
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(g.init)
    walk(g.array)
    return all(fx_global_constant_walk(program, other, seen) for other in ids if other in program.globals)


# Decides whether a function can become a shared node group and gives it a content hash. It is pure when it
# reads only constants and
# textures, never discards and calls only pure functions. The hash covers the code, constants and the core
# version, so identical functions in different effects share one group.
def fx_function_info(program, function):
    cache = program.__dict__.setdefault("_fn_info", {})
    found = cache.get(id(function))
    if found is not None:
        return found
    info = {"pure": False, "hash": "", "slots": []}
    cache[id(function)] = info
    params = {p.name for p in function.params}
    local_names, ids, calls, flags = set(), set(), [], set()

    def walk(node):
        if isinstance(node, tuple) and node:
            kind = node[0]
            if kind == "decl":
                for name, _array, _init in node[2]:
                    local_names.add(name)
            elif kind == "id" and len(node) > 1 and isinstance(node[1], str):
                ids.add(node[1])
            elif kind == "call" and len(node) > 1 and isinstance(node[1], str):
                calls.append(node[1])
            elif kind == "discard":
                flags.add("discard")
            for item in node[1:]:
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(function.body)
    for p in function.params:
        walk(p.default)
    pure = "discard" not in flags
    deps = []
    slots = []
    for name in sorted(ids - params - local_names):
        g = program.globals.get(name)
        if g is None:
            continue
        try:
            t = fx_resolve_type(g.type, program)
        except FxError:
            pure = False
            break
        if t == "sampler":
            slot = fx_sampler_slot(program, g)
            if slot is None:
                pure = False
                break
            slots.append(slot)
            deps.append(("sampler", name, fx_stable(g.init)))
        elif t == "texture":
            slots.append(name)
            deps.append(("texture", name))
        elif fx_global_constant(program, name):
            deps.append(("const", name, fx_stable(g.type), fx_stable(g.init), fx_stable(g.array), fx_stable(g.quals)))
        else:
            pure = False
            break
    nested = []
    for cname in calls:
        for other in program.functions.get(cname, []):
            sub = fx_function_info(program, other)
            if not sub["pure"]:
                pure = False
            nested.append(sub["hash"])
            slots.extend(sub["slots"])
    signature = (
        function.name,
        [(p.name, fx_stable(p.type), sorted(p.quals), fx_stable(p.array), fx_stable(p.default)) for p in function.params],
        fx_stable(function.ret),
        fx_stable(function.body),
        deps,
        sorted(nested),
        MME_CORE_VERSION,
    )
    info["pure"] = pure
    info["hash"] = hashlib.md5(fx_stable(signature).encode("utf-8")).hexdigest()
    info["slots"] = sorted(set(slots))
    return info
