import numpy as np


# Backend that computes with numpy arrays, one entry per vertex or pixel. It is the reference implementation
# and the fallback when nodes cannot express the shader.
# 'known' is only non-None for plain scalars, which lets the interpreter fold constants.
class FxNumpyBackend:
    vectorized = True
    wants_text = False

    def __init__(self, dtype=np.float64):
        self.dtype = dtype

    def statement(self, text):
        pass

    def const(self, x):
        return self.dtype(x)

    def known(self, x):
        if np.ndim(x) == 0:
            return float(x)
        return None

    def any_true(self, x):
        return bool(np.any(np.asarray(x) != 0))

    def add(self, a, b):
        return np.add(a, b, dtype=self.dtype)

    def sub(self, a, b):
        return np.subtract(a, b, dtype=self.dtype)

    def mul(self, a, b):
        return np.multiply(a, b, dtype=self.dtype)

    def div(self, a, b):
        return np.divide(a, b, dtype=self.dtype)

    def neg(self, a):
        return np.negative(a, dtype=self.dtype)

    def mod(self, a, b):
        return np.fmod(a, b, dtype=self.dtype)

    def idiv(self, a, b):
        return np.trunc(np.divide(a, b, dtype=self.dtype))

    def pow(self, a, b):
        return np.power(a, b, dtype=self.dtype)

    def unary(self, name, a):
        return FX_NP_UNARY[name](np.asarray(a, dtype=self.dtype))

    def atan2(self, a, b):
        return np.arctan2(a, b, dtype=self.dtype)

    def min(self, a, b):
        return np.minimum(a, b, dtype=self.dtype)

    def max(self, a, b):
        return np.maximum(a, b, dtype=self.dtype)

    def cmp(self, op, a, b):
        return FX_NP_CMP[op](a, b).astype(self.dtype)

    def bitop(self, op, a, b):
        x = np.asarray(a).astype(np.int64)
        y = np.asarray(b).astype(np.int64)
        if op == "&":
            r = x & y
        elif op == "|":
            r = x | y
        elif op == "^":
            r = x ^ y
        elif op == "<<":
            r = x << np.clip(y, 0, 62)
        else:
            r = x >> np.clip(y, 0, 62)
        return r.astype(self.dtype)

    def bitnot(self, a):
        return (~np.asarray(a).astype(np.int64)).astype(self.dtype)

    def select(self, c, a, b):
        return np.where(np.asarray(c) != 0, a, b)

    def truth(self, a):
        return (np.asarray(a) != 0).astype(self.dtype)

    def land(self, a, b):
        return np.multiply(self.truth(a), self.truth(b))

    def lor(self, a, b):
        return np.maximum(self.truth(a), self.truth(b))

    def lnot(self, a):
        return 1.0 - self.truth(a)

    def imod(self, a, b):
        return np.fmod(a, b)

    def enter(self, name):
        pass

    def leave(self):
        pass

    def tag(self, x, label):
        pass


FX_NP_UNARY = {
    "abs": np.abs,
    "sign": np.sign,
    "floor": np.floor,
    "ceil": np.ceil,
    "trunc": np.trunc,
    "round": np.round,
    "frac": lambda x: x - np.floor(x),
    "sqrt": lambda x: np.sqrt(x),
    "rsqrt": lambda x: 1.0 / np.sqrt(x),
    "exp": np.exp,
    "exp2": np.exp2,
    "log": np.log,
    "log2": np.log2,
    "log10": np.log10,
    "sin": np.sin,
    "cos": np.cos,
    "tan": np.tan,
    "asin": lambda x: np.arcsin(np.clip(x, -1, 1)),
    "acos": lambda x: np.arccos(np.clip(x, -1, 1)),
    "atan": np.arctan,
    "sinh": np.sinh,
    "cosh": np.cosh,
    "tanh": np.tanh,
}


FX_NP_CMP = {
    "<": np.less,
    "<=": np.less_equal,
    ">": np.greater,
    ">=": np.greater_equal,
    "==": np.equal,
    "!=": np.not_equal,
}
