from .constants import (
    FX_SCALARS,
    FX_SPECIAL_TYPES,
    FX_TYPE_RE,
)


class FxStruct:
    __slots__ = ("name", "members")

    def __init__(self, name):
        self.name = name
        self.members = []


class FxParam:
    __slots__ = ("name", "type", "semantic", "quals", "default", "array")

    def __init__(self, name, type_name, semantic, quals, default, array):
        self.name = name
        self.type = type_name
        self.semantic = semantic
        self.quals = quals
        self.default = default
        self.array = array


class FxFunction:
    __slots__ = ("name", "ret", "semantic", "params", "body")

    def __init__(self, name, ret, semantic, params, body):
        self.name = name
        self.ret = ret
        self.semantic = semantic
        self.params = params
        self.body = body


class FxGlobal:
    __slots__ = ("name", "type", "quals", "semantic", "register", "ann", "init", "array", "order")

    def __init__(self, name, type_name, quals, semantic, register, ann, init, array, order):
        self.name = name
        self.type = type_name
        self.quals = quals
        self.semantic = semantic
        self.register = register
        self.ann = ann
        self.init = init
        self.array = array
        self.order = order


class FxPass:
    __slots__ = ("name", "ann", "states", "vs", "ps")

    def __init__(self, name, ann):
        self.name = name
        self.ann = ann
        self.states = {}
        self.vs = None
        self.ps = None


class FxTechnique:
    __slots__ = ("name", "ann", "passes")

    def __init__(self, name, ann):
        self.name = name
        self.ann = ann
        self.passes = []


# Everything the parser found: structs, typedefs, functions by name (overloads share a list), globals,
# techniques; 'params' is added after parsing.
class FxProgram:
    def __init__(self):
        self.structs = {}
        self.typedefs = {}
        self.functions = {}
        self.globals = {}
        self.techniques = []
        self.folder = ""
        self.files = []

    # The parser needs this to tell declarations from expressions, including user structs and typedefs.
    def is_type(self, name):
        return (
            name in self.structs
            or name in self.typedefs
            or name in FX_SCALARS
            or FX_TYPE_RE.match(name) is not None
            or name.lower() in FX_SPECIAL_TYPES
        )
