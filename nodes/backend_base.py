import math
import numpy as np

from ..fx.errors import FxUnsupported
from ..fx.interp.numpy_backend import FxNumpyBackend
from ..fx.text import MME_LABEL_LIMIT
from .backend_groups import MmeGroupCalls
from .layout import (
    mme_layout,
    mme_prune_tree,
)
from .socket import MmeSock


MME_NODE_UNARY = {
    "abs": "ABSOLUTE",
    "sign": "SIGN",
    "floor": "FLOOR",
    "ceil": "CEIL",
    "trunc": "TRUNC",
    "round": "ROUND",
    "frac": "FRACT",
    "sqrt": "SQRT",
    "rsqrt": "INVERSE_SQRT",
    "exp": "EXPONENT",
    "sin": "SINE",
    "cos": "COSINE",
    "tan": "TANGENT",
    "asin": "ARCSINE",
    "acos": "ARCCOSINE",
    "atan": "ARCTANGENT",
    "sinh": "SINH",
    "cosh": "COSH",
    "tanh": "TANH",
}


MME_MATH_LABELS = {
    "ADD": "+",
    "SUBTRACT": "-",
    "MULTIPLY": "*",
    "DIVIDE": "/",
}


# Backend that emits Blender nodes. A scalar is a float (compile-time constant) or an MmeSock (a node output),
# so constants fold away.
# Identical operations are cached to share nodes, and statements are recorded so the layout can group nodes by
# source line.
class MmeTreeBackend(MmeGroupCalls):
    vectorized = False
    wants_text = True
    separate_color = "ShaderNodeSeparateColor"
    combine_color = "ShaderNodeCombineColor"

    def __init__(self, tree):
        self.tree = tree
        self.nodes = tree.nodes
        self.links = tree.links
        self.np = FxNumpyBackend()
        self.cache = {}
        self.keep = []
        self.block = None
        self.blocks = []
        self.block_of = {}
        self.scopes = []
        self.block_stack = []
        self.inputs = {}
        self.input_items = {}
        self.auto_named = set()
        self.boolish = set()
        self.input_nodes = []
        self.input_owner = {}

    # Starts a new layout block labelled with the HLSL statement that follows; nodes created next belong to
    # that block.
    def statement(self, text):
        if text is None:
            return
        if len(self.scopes) > 1:
            text = "%s: %s" % (self.scopes[-1], text)
        self.block = len(self.blocks)
        self.blocks.append(text)

    # Called on function entry so block labels get the function name and leave() restores the caller's block.
    def enter(self, name):
        self.scopes.append(name)
        self.block_stack.append(self.block)

    def leave(self):
        if self.scopes:
            self.scopes.pop()
        if self.block_stack:
            self.block = self.block_stack.pop()

    def new(self, idname):
        node = self.nodes.new(idname)
        self.block_of[node.name] = self.block
        return node

    def const(self, x):
        return float(x)

    def known(self, x):
        if isinstance(x, MmeSock):
            return None
        return float(x)

    def any_true(self, x):
        raise FxUnsupported("data-dependent loop")

    # Hashable identity of a scalar for the node cache: node and socket for sockets, the value for constants
    # (NaN made comparable).
    def key(self, x):
        if isinstance(x, MmeSock):
            return ("s", x.socket.node.name, x.socket.identifier)
        value = float(x)
        return ("c", "nan" if value != value else value)

    # Gives a node, or a group input that has no name yet, the HLSL variable name it computes; existing labels
    # are never overwritten.
    def tag(self, x, label):
        if not isinstance(x, MmeSock):
            return
        node = x.socket.node
        if node.bl_idname == "NodeGroupInput":
            item = self.input_items.get(x.socket.identifier)
            if item is not None and item.identifier in self.auto_named:
                self.auto_named.discard(item.identifier)
                item.name = label[:MME_LABEL_LIMIT]
            return
        if not node.label:
            node.label = label[:MME_LABEL_LIMIT]

    # Connects a scalar to a socket, or sets the socket's default when the scalar is a constant (converted to
    # the socket's own type).
    def feed(self, socket, value):
        if isinstance(value, MmeSock):
            self.links.new(value.socket, socket)
            return
        value = float(value)
        if socket.type == "BOOLEAN":
            socket.default_value = value != 0.0
        elif socket.type == "INT":
            socket.default_value = int(value)
        elif socket.type in ("VECTOR", "RGBA"):
            socket.default_value = [value] * len(socket.default_value)
        else:
            socket.default_value = value

    # A socket for any scalar; constants get a shared Value node.
    def socket_of(self, x):
        if isinstance(x, MmeSock):
            return x.socket
        key = ("value", self.key(x))
        found = self.cache.get(key)
        if found is not None:
            return found.socket
        node = self.new("ShaderNodeValue")
        node.outputs[0].default_value = float(x)
        node.label = "%g" % float(x)
        result = MmeSock(node.outputs[0])
        self.cache[key] = result
        return result.socket

    # Constant folding through numpy so folded results match the numpy backend exactly (same float semantics).
    def fold(self, fn, *args):
        if any(isinstance(a, MmeSock) for a in args):
            return None
        with np.errstate(all="ignore"):
            return float(fn(*[np.float64(a) for a in args]))

    # Caches a node output under its key, keeps it from being pruned, and tracks 0/1 'boolean' results so
    # selects can skip a conversion.
    def remember(self, key, socket, boolish=False):
        result = MmeSock(socket)
        self.cache[key] = result
        self.keep.append(result)
        if boolish:
            self.boolish.add(self.key(result))
        return result

    def math(self, op, *args, boolish=False):
        key = ("math", op) + tuple(self.key(a) for a in args)
        found = self.cache.get(key)
        if found is not None:
            return found
        node = self.new("ShaderNodeMath")
        node.operation = op
        if op == "COMPARE":
            node.inputs[2].default_value = 0.0
        for index, arg in enumerate(args):
            self.feed(node.inputs[index], arg)
        return self.remember(key, node.outputs[0], boolish)

    def int_math(self, op, a, b):
        key = ("imath", op, self.key(a), self.key(b))
        found = self.cache.get(key)
        if found is not None:
            return found
        node = self.new("FunctionNodeIntegerMath")
        node.operation = op
        self.feed(node.inputs[0], a)
        self.feed(node.inputs[1], b)
        return self.remember(key, node.outputs[0])

    def bool_math(self, op, *args):
        key = ("bmath", op) + tuple(self.key(a) for a in args)
        found = self.cache.get(key)
        if found is not None:
            return found
        node = self.new("FunctionNodeBooleanMath")
        node.operation = op
        for index, arg in enumerate(args):
            self.feed(node.inputs[index], arg)
        return self.remember(key, node.outputs[0], True)

    def ordered(self, a, b):
        return (a, b) if repr(self.key(a)) <= repr(self.key(b)) else (b, a)

    def add(self, a, b):
        folded = self.fold(self.np.add, a, b)
        if folded is not None:
            return folded
        if self.known(a) == 0.0:
            return b
        if self.known(b) == 0.0:
            return a
        return self.math("ADD", *self.ordered(a, b))

    def sub(self, a, b):
        folded = self.fold(self.np.sub, a, b)
        if folded is not None:
            return folded
        if self.known(b) == 0.0:
            return a
        if self.key(a) == self.key(b):
            return 0.0
        return self.math("SUBTRACT", a, b, boolish=self.known(a) == 1.0 and self.key(b) in self.boolish)

    def mul(self, a, b):
        folded = self.fold(self.np.mul, a, b)
        if folded is not None:
            return folded
        if self.known(a) == 1.0:
            return b
        if self.known(b) == 1.0:
            return a
        if self.known(a) == 0.0 or self.known(b) == 0.0:
            return 0.0
        both = self.key(a) in self.boolish and self.key(b) in self.boolish
        return self.math("MULTIPLY", *self.ordered(a, b), boolish=both)

    def div(self, a, b):
        folded = self.fold(self.np.div, a, b)
        if folded is not None:
            return folded
        if self.known(b) == 1.0:
            return a
        if self.known(a) == 0.0:
            return 0.0
        return self.math("DIVIDE", a, b)

    def neg(self, a):
        if not isinstance(a, MmeSock):
            return -float(a)
        return self.math("MULTIPLY", a, -1.0)

    def mod(self, a, b):
        folded = self.fold(self.np.mod, a, b)
        if folded is not None:
            return folded
        return self.math("MODULO", a, b)

    def imod(self, a, b):
        folded = self.fold(self.np.imod, a, b)
        if folded is not None:
            return folded
        return self.int_math("MODULO", a, b)

    def idiv(self, a, b):
        folded = self.fold(self.np.idiv, a, b)
        if folded is not None:
            return folded
        return self.int_math("DIVIDE", a, b)

    def pow(self, a, b):
        folded = self.fold(self.np.pow, a, b)
        if folded is not None:
            return folded
        if self.known(b) == 1.0:
            return a
        if self.known(b) == 0.0:
            return 1.0
        return self.math("POWER", a, b)

    def unary(self, name, a):
        folded = self.fold(lambda x: self.np.unary(name, x), a)
        if folded is not None:
            return folded
        if name == "exp2":
            return self.math("POWER", 2.0, a)
        if name == "log":
            return self.math("LOGARITHM", a, math.e)
        if name == "log2":
            return self.math("LOGARITHM", a, 2.0)
        if name == "log10":
            return self.math("LOGARITHM", a, 10.0)
        op = MME_NODE_UNARY.get(name)
        if op is None:
            raise FxUnsupported("function %s" % name)
        return self.math(op, a)

    def atan2(self, a, b):
        folded = self.fold(self.np.atan2, a, b)
        if folded is not None:
            return folded
        return self.math("ARCTAN2", a, b)

    def min(self, a, b):
        folded = self.fold(self.np.min, a, b)
        if folded is not None:
            return folded
        if self.key(a) == self.key(b):
            return a
        return self.math("MINIMUM", *self.ordered(a, b))

    def max(self, a, b):
        folded = self.fold(self.np.max, a, b)
        if folded is not None:
            return folded
        if self.key(a) == self.key(b):
            return a
        both = self.key(a) in self.boolish and self.key(b) in self.boolish
        return self.math("MAXIMUM", *self.ordered(a, b), boolish=both)

    def cmp(self, op, a, b):
        folded = self.fold(lambda x, y: self.np.cmp(op, x, y), a, b)
        if folded is not None:
            return folded
        if op == "<":
            return self.math("LESS_THAN", a, b, boolish=True)
        if op == ">":
            return self.math("GREATER_THAN", a, b, boolish=True)
        if op == "<=":
            return self.lnot(self.math("GREATER_THAN", a, b, boolish=True))
        if op == ">=":
            return self.lnot(self.math("LESS_THAN", a, b, boolish=True))
        equal = self.math("COMPARE", a, b, boolish=True)
        if op == "==":
            return equal
        return self.lnot(equal)

    def bitop(self, op, a, b):
        folded = self.fold(lambda x, y: self.np.bitop(op, x, y), a, b)
        if folded is not None:
            return folded
        raise FxUnsupported("bit operation in a pixel shader")

    def bitnot(self, a):
        folded = self.fold(self.np.bitnot, a)
        if folded is not None:
            return folded
        raise FxUnsupported("bit operation in a pixel shader")

    def truth(self, a):
        known = self.known(a)
        if known is not None:
            return 1.0 if known != 0.0 else 0.0
        if self.key(a) in self.boolish:
            return a
        return self.lnot(self.math("COMPARE", a, 0.0, boolish=True))

    def land(self, a, b):
        ta, tb = self.truth(a), self.truth(b)
        ka, kb = self.known(ta), self.known(tb)
        if ka is not None:
            return tb if ka else 0.0
        if kb is not None:
            return ta if kb else 0.0
        return self.bool_math("AND", *self.ordered(ta, tb))

    def lor(self, a, b):
        ta, tb = self.truth(a), self.truth(b)
        ka, kb = self.known(ta), self.known(tb)
        if ka is not None:
            return 1.0 if ka else tb
        if kb is not None:
            return 1.0 if kb else ta
        return self.bool_math("OR", *self.ordered(ta, tb))

    def lnot(self, a):
        known = self.known(a)
        if known is not None:
            return 0.0 if known != 0.0 else 1.0
        return self.bool_math("NOT", self.truth(a))

    def select(self, c, a, b):
        known = self.known(c)
        if known is not None:
            return a if known != 0.0 else b
        if self.key(a) == self.key(b):
            return a
        key = ("select", self.key(c), self.key(a), self.key(b))
        found = self.cache.get(key)
        if found is not None:
            return found
        node = self.new("GeometryNodeSwitch")
        node.input_type = "FLOAT"
        self.feed(node.inputs["Switch"], c)
        self.feed(node.inputs["False"], b)
        self.feed(node.inputs["True"], a)
        both = self.key(a) in self.boolish and self.key(b) in self.boolish
        return self.remember(key, node.outputs[0], both)

    # Group input socket by key, created once; auto-named inputs are renamed later by tag() once the HLSL name
    # is known.
    def new_input(self, key, socket_type="NodeSocketFloat", label=None):
        found = self.inputs.get(key)
        if found is not None:
            return found
        item = self.tree.interface.new_socket(label or key, in_out="INPUT", socket_type=socket_type)
        if label is None:
            self.auto_named.add(item.identifier)
        node = self.new("NodeGroupInput")
        self.input_nodes.append(node)
        socket = next(s for s in node.outputs if s.identifier == item.identifier)
        result = MmeSock(socket)
        self.inputs[key] = result
        self.input_items[item.identifier] = item
        self.input_owner[key] = (node.name, item.identifier)
        return result

    def attribute(self, name, kind="GEOMETRY"):
        return self.new_input(name)

    def input_identifier(self, key):
        found = self.inputs.get(key)
        return found.socket.identifier if found is not None else None

    # Creates the group output, removes nodes that do not feed it and group inputs that ended up unused, hides
    # unlinked sockets and lays the nodes out.
    def finish(self, outputs):
        output = self.new("NodeGroupOutput")
        self.block_of[output.name] = "out"
        existing = {}
        for item in self.tree.interface.items_tree:
            if item.item_type == "SOCKET" and item.in_out == "OUTPUT":
                existing.setdefault((item.name, item.socket_type), item)
        for name, value, socket_type in outputs:
            item = existing.pop((name, socket_type), None)
            if item is None:
                item = self.tree.interface.new_socket(name, in_out="OUTPUT", socket_type=socket_type)
            target = next(s for s in output.inputs if s.identifier == item.identifier)
            if isinstance(value, (list, tuple)):
                combine = self.new(self.combine_color if socket_type == "NodeSocketColor" else "ShaderNodeCombineXYZ")
                self.block_of[combine.name] = "out"
                for k, comp in enumerate(value):
                    self.feed(combine.inputs[k], comp)
                self.links.new(combine.outputs[0], target)
            elif isinstance(value, MmeSock):
                self.links.new(value.socket, target)
            else:
                target.default_value = float(value)
                item.default_value = float(value)
        mme_prune_tree(self.tree, output)
        alive = {n.name for n in self.tree.nodes}
        for key in list(self.inputs):
            node_name, identifier = self.input_owner[key]
            if node_name not in alive:
                item = self.input_items.pop(identifier, None)
                if item is not None:
                    self.tree.interface.remove(item)
                del self.inputs[key]
        for node in self.tree.nodes:
            if node.bl_idname == "NodeGroupInput":
                for socket in node.outputs:
                    socket.hide = not socket.is_linked
        mme_layout(self.tree, self.block_of, self.blocks)
        return output
