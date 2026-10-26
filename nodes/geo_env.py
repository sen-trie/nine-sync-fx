from ..common.constants import (
    MME_ATTR_NRM,
    MME_ATTR_POS,
    MME_ATTR_SRC,
    MME_ATTR_UV,
)
from ..fx.semantics import (
    fx_classify_global,
    fx_source_is_runtime,
    fx_value_from_list,
)
from ..fx.types import (
    FxValue,
    fx_count,
    fx_is_numeric,
    fx_resolve_type,
)
from .images import mme_node_sample


# Environment for vertex shaders on Geometry Nodes. Runtime values become group inputs recorded in 'needs';
# the scene then supplies them (drivers, camera, bones).
class MmeGeoEnv:
    def __init__(self, program, state, textures, controls, needs):
        self.program = program
        self.state = state
        self.textures = textures
        self.controls = controls
        self.needs = needs
        self.rt_lookup = None

    # Constants fold in; matrices and scalars that change over time become inputs named m:/u: with the global
    # name.
    def global_value(self, ex, g):
        source = fx_classify_global(g, self.program)
        if source is None:
            return None
        t = fx_resolve_type(g.type, self.program)
        if not fx_is_numeric(t):
            return None
        values = self.state.values(source, g)
        runtime = fx_source_is_runtime(source)
        if source[0] == "control" and self.controls.get(g.name, {}).get("kind", "const") == "const":
            runtime = False
        if not runtime:
            return fx_value_from_list(ex, values, t)
        B = ex.B
        if source[0] == "matrix" or len(values) == 16:
            key = "m:" + g.name
            self.needs[key] = ("matrix", source, g.name)
            comps = B.matrix_input(key, g.name)
            return ex.convert(FxValue(("f", (4, 4)), comps), t)
        comps = []
        for k in range(len(values)):
            key = "u:%s:%d" % (g.name, k)
            self.needs[key] = ("scalar", source, g.name, k, len(values))
            comps.append(B.new_input(key))
        dims = () if len(comps) == 1 else (len(comps),)
        return ex.convert(FxValue(("f", dims), comps), t)

    def sample(self, ex, info, u, v, lod):
        return mme_node_sample(self, ex, info, u, v)


# Vertex attribute inputs by semantic; each becomes a named group input, so the scene can read the matching
# mesh attribute.
def mme_geo_vertex_inputs(needs):
    def provide(ex):
        B = ex.B

        def attr(name, k):
            key = "v:%s:%d" % (name, k)
            needs[key] = ("vertex", name, k)
            return B.new_input(key)

        def get(semantic, t):
            if semantic == "POSITION0":
                comps = [attr(MME_ATTR_POS, k) for k in range(3)] + [1.0]
            elif semantic == "NORMAL0":
                comps = [attr(MME_ATTR_NRM, k) for k in range(3)] + [0.0]
            elif semantic == "TEXCOORD0":
                comps = [attr(MME_ATTR_UV, 0), attr(MME_ATTR_UV, 1), 0.0, 1.0]
            elif semantic in ("_INDEX", "INDEX"):
                comps = [attr(MME_ATTR_SRC, 0)]
            elif semantic in ("COLOR0", "COLOR1"):
                comps = [1.0, 1.0, 1.0, 1.0]
            else:
                comps = [0.0, 0.0, 0.0, 1.0]
            if not fx_is_numeric(t):
                return ex.zero(t)
            count = fx_count(t[1])
            comps = (comps + [0.0] * 16)[:count]
            return FxValue(("f", t[1]), comps)

        return get

    return provide
