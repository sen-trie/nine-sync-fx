import bpy
def install(mb):
    if bpy.app.version >= (5, 3, 0):
        return
    P = mb.MmePsBackend
    def select(self, c, a, b):
        known = self.known(c)
        if known is not None:
            return a if known != 0.0 else b
        if self.key(a) == self.key(b):
            return a
        key = ("select", self.key(c), self.key(a), self.key(b))
        if key in self.cache:
            return self.cache[key]
        node = self.new("ShaderNodeMix"); node.data_type = "FLOAT"; node.clamp_factor = True
        self.feed(mb.node_socket(node, "Factor_Float"), c)
        self.feed(mb.node_socket(node, "A_Float"), b)
        self.feed(mb.node_socket(node, "B_Float"), a)
        return self.remember(key, mb.node_socket(node, "Result_Float", outputs=True))
    def bool_math(self, op, *args):
        if op == "AND":
            return self.math("MULTIPLY", *args, boolish=True)
        if op == "OR":
            return self.math("MAXIMUM", *args, boolish=True)
        return self.math("SUBTRACT", 1.0, args[0], boolish=True)
    def int_math(self, op, a, b):
        if op == "DIVIDE":
            return self.math("TRUNC", self.math("DIVIDE", a, b))
        return self.math("MODULO", a, b)
    P.select = select
    P.bool_math = bool_math
    P.int_math = int_math
    orig = mb.mme_fallback_material
    def fallback(name, runtime, subset, mode="ALPHA", reason="", owner=None):
        return orig(name, runtime, subset, mode, "", owner)
    mb.mme_fallback_material = fallback
