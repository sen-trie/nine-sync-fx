from .constants import (
    FX_SCALARS,
    FX_TYPE_RE,
)
from .errors import FxError
from .program import FxStruct


# Type model: a numeric type is (base, dims) with base f/i/b and dims () scalar, (n,) vector or (r, c) matrix;
# structs are FxStruct objects;
# textures, samplers, strings and void are plain strings. Typedefs are followed first.
def fx_resolve_type(name, program):
    if isinstance(name, FxStruct):
        return name
    name = program.typedefs.get(name, name)
    struct = program.structs.get(name)
    if struct is not None:
        return struct
    match = FX_TYPE_RE.match(name)
    if match:
        base = FX_SCALARS[match.group(1)]
        rows, cols = match.group(2), match.group(3)
        if rows is None:
            return (base, ())
        if cols is None:
            return (base, (int(rows),))
        return (base, (int(rows), int(cols)))
    lower = name.lower()
    if lower == "vector":
        return ("f", (4,))
    if lower == "matrix":
        return ("f", (4, 4))
    if lower.startswith("texture"):
        return "texture"
    if lower.startswith("sampler"):
        return "sampler"
    if lower == "string":
        return "string"
    if lower == "void":
        return "void"
    raise FxError("unknown type %s" % name)


# Number of scalar components of a numeric type; matrices are stored flat, row by row.
def fx_count(dims):
    if not dims:
        return 1
    if len(dims) == 1:
        return dims[0]
    return dims[0] * dims[1]


def fx_is_numeric(t):
    return isinstance(t, tuple) and len(t) == 2 and isinstance(t[1], tuple)


# A typed value: t is the type, c the components (a list of backend scalars, a dict for structs, a list for
# arrays).
class FxValue:
    __slots__ = ("t", "c")

    def __init__(self, t, c):
        self.t = t
        self.c = c


def fx_array_type(elem, size):
    return ("array", elem, size)


def fx_is_array(t):
    return isinstance(t, tuple) and len(t) == 3 and t[0] == "array"


# A sampler with its state block; helpers turn the HLSL filter and address states into the modes the importer
# supports.
class FxSampler:
    __slots__ = ("name", "texture", "states", "register")

    def __init__(self, name, texture, states, register):
        self.name = name
        self.texture = texture
        self.states = states
        self.register = register

    def state(self, key, default=""):
        value = self.states.get(key)
        if value is None:
            return default
        return (" ".join(value) if isinstance(value, list) else str(value)).upper()

    def point(self):
        return any(
            self.state(k, "LINEAR") in ("NONE", "POINT")
            for k in ("magfilter", "minfilter")
        )

    # Border is treated as clamp; only clamp, mirror and wrap exist on the Blender side.
    def address(self):
        value = self.state("addressu", "WRAP")
        if value in ("CLAMP", "BORDER"):
            return "CLAMP"
        if value == "MIRROR":
            return "MIRROR"
        return "WRAP"
