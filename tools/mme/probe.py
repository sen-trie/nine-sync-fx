import numpy as np

from fx.entry import fx_entry, fx_run_entry
from fx.errors import FxUnsupported
from fx.geometry_inputs import fx_vertex_inputs
from fx.interp.executor import FxExec
from fx.interp.numpy_backend import FxNumpyBackend
from fx.semantics import FxSceneState
from fx.textures import FxNumpyEnv, FxTextureStore


def probe_geometry(program, draws):
    state = FxSceneState()
    state.params = {name: meta["default"] for name, meta in program.params.items()}
    env = FxNumpyEnv(program, state, FxTextureStore(program, lambda path: None))
    inputs = fx_vertex_inputs(np.array([[0., 0., 0.]]), np.array([[0., 0., 1.]]),
                              np.array([[0.5, 0.5]]), np.array([0]))
    tested = set()
    for draw in draws:
        if draw.fx_pass in tested:
            continue
        tested.add(draw.fx_pass)
        varyings = {}
        for stage in ("vs", "ps"):
            function, args = fx_entry(program, getattr(draw.fx_pass, stage))
            if function is None:
                raise FxUnsupported(f"{draw.label}: no resolved {stage} entry")
            ex = FxExec(program, FxNumpyBackend(), env)
            for counter, _, _ in draw.loops:
                if counter:
                    ex.overrides[counter] = ex.scalar(0, "i")
            provider = inputs(ex) if stage == "vs" else lambda semantic, t: varyings.get(semantic, ex.zero(t))
            try:
                with np.errstate(all="ignore"):
                    outputs = fx_run_entry(ex, function, args, provider)
            except Exception as error:
                raise FxUnsupported(f"{draw.label} {stage} {function.name}: {error}") from error
            required = "POSITION0" if stage == "vs" else "COLOR0"
            if required not in outputs:
                raise FxUnsupported(f"{draw.label} {stage}: missing {required} output")
            if stage == "vs":
                varyings = outputs
