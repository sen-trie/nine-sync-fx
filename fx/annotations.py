import re

from .errors import FxError
from .interp.executor import FxExec
from .interp.numpy_backend import FxNumpyBackend
from .types import fx_is_numeric


# Heuristic for colour parameters that have no UIWidget: names or labels containing colour, tint, rgb or a Col
# suffix.
FX_COLOR_NAME = re.compile(r"(?i)colou?r|tint|rgb|^col(?![a-z])|(?<=[a-z0-9_])Col(?![a-z])")


# Annotations and initialisers are evaluated once with a dummy environment and cached per AST node.
def fx_const_value(program, node):
    if node is None:
        return None
    cache = program.__dict__.setdefault("_const_values", {})
    key = id(node)
    hit = cache.get(key)
    if hit is not None and hit[0] is node:
        return hit[1]
    value = fx_const_value_uncached(program, node)
    cache[key] = (node, value)
    return value


# Runs the numpy interpreter with an environment that knows no globals; anything that depends on scene data
# fails and yields None.
def fx_const_value_uncached(program, node):
    ex = FxExec(program, FxNumpyBackend(), FxConstEnv())
    try:
        value = ex.eval(node)
    except FxError:
        return None
    if value.t == "string":
        return value.c
    if fx_is_numeric(value.t):
        out = [float(x) for x in value.c]
        return out[0] if len(out) == 1 else out
    return None


# Environment with no globals and black textures, enough to evaluate constant expressions.
class FxConstEnv:
    def global_value(self, ex, g):
        return None

    def sample(self, ex, info, u, v, lod):
        return [ex.B.const(0.0)] * 4


# Annotation names are case-insensitive in effect files.
def fx_annotation(g, name, program, default=None):
    for key, node in g.ann.items():
        if key.lower() == name.lower():
            value = fx_const_value(program, node)
            return default if value is None else value
    return default
