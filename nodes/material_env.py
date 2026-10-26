from ..common.constants import MME_UNIFORM_PREFIX
from ..fx.semantics import (
    fx_classify_global,
    fx_source_is_runtime,
    fx_value_from_list,
)
from ..fx.types import (
    FxValue,
    fx_is_numeric,
    fx_resolve_type,
)
from .images import mme_node_sample
from .names import (
    mme_prop_name,
    mme_varying_name,
)


# Environment for pixel shaders. Per-frame scene values become object uniforms, which the drivers keep up to
# date; constants are baked in.
class MmeNodeEnv:
    def __init__(self, program, state, textures, controls_runtime):
        self.program = program
        self.state = state
        self.textures = textures
        self.controls_runtime = controls_runtime
        self.uniforms = {}
        self.rt_lookup = None

    # Values that change over time become attributes named by mme_prop_name (recorded in 'uniforms'); controls
    # that are not animated are treated as constants.
    def global_value(self, ex, g):
        source = fx_classify_global(g, self.program)
        if source is None:
            return None
        t = fx_resolve_type(g.type, self.program)
        if not fx_is_numeric(t):
            return None
        values = self.state.values(source, g)
        runtime = fx_source_is_runtime(source)
        if source[0] == "control" and g.name not in self.controls_runtime:
            runtime = False
        if not runtime:
            return fx_value_from_list(ex, values, t)
        comps = []
        for index in range(len(values)):
            prop = mme_prop_name(MME_UNIFORM_PREFIX, g.name, index)
            self.uniforms[prop] = (g.name, index)
            comps.append(ex.B.attribute(prop, "OBJECT"))
        dims = (4, 4) if len(comps) == 16 else (() if len(comps) == 1 else (len(comps),))
        return ex.convert(FxValue(("f", dims), comps), t)

    def sample(self, ex, info, u, v, lod):
        return mme_node_sample(self, ex, info, u, v)


# Pixel-shader inputs: vertex-shader outputs come in as attributes written by the geometry tree; VFACE is
# always front-facing.
def mme_ps_inputs(vs_outputs):
    def provide(ex):
        def get(semantic, t):
            if semantic in vs_outputs:
                count = vs_outputs[semantic]
                comps = [ex.B.attribute(mme_varying_name(semantic, k)) for k in range(count)]
            elif semantic == "VFACE":
                comps = [1.0]
            else:
                comps = [0.0, 0.0, 0.0, 0.0]
            dims = () if len(comps) == 1 else (len(comps),)
            return FxValue(("f", dims), comps)

        return get

    return provide


# Maps the pass's DirectX blend states to the six modes the material setup knows. Anything unrecognised falls
# back to normal alpha blending.
def mme_blend_mode(states):
    def state(name, default):
        value = states.get(name)
        if value is None:
            return default
        return str(value).replace(" ", "").upper()

    if state("alphablendenable", "TRUE") in ("FALSE", "0"):
        return "OPAQUE"
    src = state("srcblend", "SRCALPHA")
    dst = state("destblend", "INVSRCALPHA")
    op = state("blendop", "ADD")
    if op not in ("ADD", "1"):
        return "ALPHA"
    if dst == "ONE":
        return "ADD_ALPHA" if src == "SRCALPHA" else "ADD"
    if src == "ZERO" and dst == "SRCCOLOR" or src == "DESTCOLOR" and dst == "ZERO":
        return "MULTIPLY"
    if src == "ONE" and dst == "INVSRCALPHA":
        return "PREMULTIPLIED"
    if src == "ONE" and dst == "ZERO":
        return "OPAQUE"
    return "ALPHA"


MME_MODE_LABELS = {
    "OPAQUE": "Opaque",
    "ALPHA": "Alpha blend (SRCALPHA, INVSRCALPHA)",
    "ADD": "Additive (ONE, ONE)",
    "ADD_ALPHA": "Additive (SRCALPHA, ONE)",
    "PREMULTIPLIED": "Premultiplied (ONE, INVSRCALPHA)",
    "MULTIPLY": "Multiply",
}
