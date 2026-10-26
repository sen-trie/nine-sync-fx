from .annotations import (
    FX_COLOR_NAME,
    fx_annotation,
    fx_const_value,
)
from .entry import fx_entry
from .errors import FxError
from .semantics import (
    FX_FLAG_DEFAULTS,
    fx_classify_global,
)
from .techniques import fx_technique_script
from .types import (
    fx_count,
    fx_is_numeric,
    fx_resolve_type,
)


# Collects the values of a given key (for example loopbycount) over all technique scripts, memoised on the
# program.
def fx_script_values(program, wanted):
    cache = program.__dict__.setdefault("_script_values", {})
    if wanted in cache:
        return list(cache[wanted])
    found = fx_script_values_uncached(program, wanted)
    cache[wanted] = tuple(found)
    return found


# Splits every technique script on ';' and collects the right-hand sides of the wanted key.
def fx_script_values_uncached(program, wanted):
    found = []
    for technique in program.techniques:
        script = fx_technique_script(technique, program) or ""
        for command in script.split(";"):
            if "=" not in command:
                continue
            key, value = command.split("=", 1)
            if key.strip().lower() == wanted:
                found.append(value.strip())
    return found


# Finds the user parameters an effect exposes: plain float/int/bool scalars and small vectors without a
# semantic. Static/const, loop counters,
# the flag globals, arrays and UIVisible=false are skipped. Returns the metadata (default, label, widget,
# range, colour) stored on the effect root.
def fx_find_params(program):
    loop_vars = set(fx_script_values(program, "loopgetindex"))
    params = {}
    for g in sorted(program.globals.values(), key=lambda item: item.order):
        if g.semantic or g.array is not None:
            continue
        quals = {str(q).lower() for q in (g.quals or [])}
        if quals & {"static", "const"}:
            continue
        if g.name in loop_vars or g.name in FX_FLAG_DEFAULTS or g.name == "use_texture":
            continue
        try:
            t = fx_resolve_type(g.type, program)
        except FxError:
            continue
        if not fx_is_numeric(t) or len(t[1]) > 1 or (t[1] and t[1][0] > 4):
            continue
        has_ui = any(str(k).lower().startswith("ui") for k in g.ann)
        if g.init is None and not has_ui:
            continue
        visible = fx_annotation(g, "UIVisible", program, None)
        if isinstance(visible, float) and not visible:
            continue
        size = fx_count(t[1])
        default = fx_const_value(program, g.init) if g.init is not None else None
        if isinstance(default, str):
            continue
        if default is None:
            default = [0.0]
        if not isinstance(default, list):
            default = [default]
        if len(default) == 1 and size > 1:
            default = default * size
        default = ([float(v) for v in default] + [0.0] * size)[:size]
        if t[0] == "i":
            default = [float(int(v)) for v in default]
        elif t[0] == "b":
            default = [1.0 if v else 0.0 for v in default]
        meta = {
            "type": t[0],
            "size": size,
            "default": default,
            "label": str(fx_annotation(g, "UIName", program, "") or g.name),
            "widget": str(fx_annotation(g, "UIWidget", program, "") or "").lower(),
            "help": str(fx_annotation(g, "UIHelp", program, "") or ""),
            "order": g.order,
        }
        meta["color"] = t[0] == "f" and size in (3, 4) and (
            meta["widget"] == "color" or any(FX_COLOR_NAME.search(text) for text in (g.name, meta["label"]))
        )
        for key in ("UIMin", "UIMax"):
            value = fx_annotation(g, key, program, None)
            if isinstance(value, list):
                value = value[0] if value else None
            if isinstance(value, float):
                meta[key[2:].lower()] = value
        params[g.name] = meta
    return params


# Names of globals the vertex shader entry points can read, found by walking the function bodies, called
# functions and global initialisers.
# Used to know which parameters change geometry.
def fx_vs_globals(program, passes=None):
    names = set()
    seen = set()
    stack = []
    if passes is None:
        passes = [fx_pass for technique in program.techniques for fx_pass in technique.passes]
    for technique in [None]:
        for fx_pass in passes:
            if fx_pass.vs is None:
                continue
            function, args = fx_entry(program, fx_pass.vs)
            if function is not None and id(function) not in seen:
                seen.add(id(function))
                stack.append(function.body)
            stack.append(args)

    def walk(node):
        if isinstance(node, tuple):
            if len(node) > 1 and node[0] == "id" and isinstance(node[1], str):
                names.add(node[1])
            if len(node) > 1 and node[0] == "call" and isinstance(node[1], str):
                for function in program.functions.get(node[1], []):
                    if id(function) not in seen:
                        seen.add(id(function))
                        stack.append(function.body)
            for item in node:
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    visited = set()
    while stack:
        walk(stack.pop())
        for name in list(names - visited):
            visited.add(name)
            g = program.globals.get(name)
            if g is not None and g.init is not None:
                stack.append(g.init)
    return names


# Finds parameters that the effect overwrites under an 'if' on a controller value, so the UI can tell the user
# which controller takes over which parameter.
def fx_overridden_params(program):
    params = set(getattr(program, "params", {}))
    controls = set()
    for g in program.globals.values():
        source = fx_classify_global(g, program)
        if source is not None and source[0] == "control":
            controls.add(g.name)
    found = {}

    def ids(node, out):
        if isinstance(node, tuple):
            if len(node) > 1 and node[0] == "id" and isinstance(node[1], str):
                out.add(node[1])
            for item in node:
                ids(item, out)
        elif isinstance(node, list):
            for item in node:
                ids(item, out)
        return out

    def target(node):
        while isinstance(node, tuple) and len(node) > 1:
            if node[0] == "id":
                return node[1]
            node = next((item for item in node[1:] if isinstance(item, tuple)), None)
        return None

    def assigned(node, out):
        if isinstance(node, tuple):
            if len(node) > 3 and node[0] == "assign":
                name = target(node[2])
                if name in params:
                    out.add(name)
            for item in node:
                assigned(item, out)
        elif isinstance(node, list):
            for item in node:
                assigned(item, out)
        return out

    def walk(node):
        if isinstance(node, tuple):
            if node and node[0] == "if" and len(node) == 4:
                gates = ids(node[1], set()) & controls
                if gates:
                    for name in assigned(node[2], set()):
                        found.setdefault(name, set()).update(gates)
            for item in node:
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for functions in program.functions.values():
        for function in functions:
            walk(function.body)
    return {name: sorted(gates) for name, gates in found.items()}


# Parameters that change the mesh itself (read by the vertex shader or used as loop counts), so editing them
# needs a rebuild rather than a driver update.
def fx_structural_params(program):
    params = getattr(program, "params", {})
    used = fx_vs_globals(program) | set(fx_script_values(program, "loopbycount"))
    return sorted(name for name in params if name in used)
