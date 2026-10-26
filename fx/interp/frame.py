# One function call: its scope stack, the return value, a 'done' mask for early returns and the active loops.
class FxFrame:
    __slots__ = ("function", "scopes", "ret", "done", "loops")

    def __init__(self, function):
        self.function = function
        self.scopes = [{}]
        self.ret = None
        self.done = 0.0
        self.loops = []


# Break and continue masks of one loop; masks are scalars (0/1) or per-vertex arrays.
class FxLoop:
    __slots__ = ("brk", "cont")

    def __init__(self):
        self.brk = 0.0
        self.cont = 0.0
