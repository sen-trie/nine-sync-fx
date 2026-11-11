import numpy as np

from ..common.constants import MME_MEMORY_BUDGETS
from ..common.controls import mme_load_controls
from ..fx.entry import (
    fx_entry,
    fx_run_entry,
)
from ..fx.errors import FxUnsupported
from ..fx.geometry_inputs import (
    fx_clip_culled,
    fx_clip_to_world,
    fx_vertex_inputs,
)
from ..fx.interp.executor import FxExec
from ..fx.interp.numpy_backend import FxNumpyBackend
from ..fx.textures import FxNumpyEnv
from ..fx.types import FxValue


# The vertices of one pass as numpy arrays: source index, loop instance, per-vertex loop values and the
# matching positions, normals and UVs. A subset made while probing keeps the lane numbers of its vertices in
# the full set and shares its memo, so texture samples carry over between probe states; inputs converted to
# the analysis dtype are kept, not converted again for every state.
class MmePassData:
    __slots__ = ("draw", "src", "inst", "loop_values", "positions", "normals", "uvs", "count", "stamp", "lanes", "memo", "converted", "parent")

    def __init__(self, draw, src, inst, loop_values, runtime, lanes=None, memo=None, parent=None):
        self.draw = draw
        self.src = src
        self.inst = inst
        self.loop_values = loop_values
        self.positions = runtime.positions[src]
        self.normals = runtime.normals[src]
        self.uvs = runtime.uvs[src]
        self.count = len(src)
        self.stamp = None
        self.lanes = lanes
        self.memo = memo if memo is not None else {}
        self.converted = {}
        self.parent = parent

    def lane_ids(self, pending):
        return pending if self.lanes is None else self.lanes[pending]

    def subset(self, pending, runtime, previous=None, at=None):
        parent = (previous.lanes, at) if previous is not None and at is not None else None
        return MmePassData(self.draw, self.src[pending], self.inst[pending], {k: v[pending] for k, v in self.loop_values.items()}, runtime, self.lane_ids(pending), self.memo, parent)

    def as_dtype(self, dtype):
        found = self.converted.get(dtype)
        if found is None:
            found = (
                self.positions.astype(dtype, copy=False),
                self.normals.astype(dtype, copy=False),
                self.uvs.astype(dtype, copy=False),
                {var: values.astype(dtype) for var, values in self.loop_values.items()},
            )
            self.converted[dtype] = found
        return found


# Batch sizes of the memory budget chosen for the build that is running.
def mme_budget(runtime):
    return MME_MEMORY_BUDGETS.get(getattr(runtime, "memory_budget", "BALANCED"), MME_MEMORY_BUDGETS["BALANCED"])


# Names of globals bound to the accessory's own opacity (tr), which pruning varies to find vertices that only
# appear at some opacity.
def mme_self_tr_globals(runtime, root):
    found = []
    for name, desc in mme_load_controls(root).items():
        if desc.get("kind") == "self" and desc.get("item", "").lower() == "tr":
            found.append(name)
    return found


# Turns instance numbers into LoopGetIndex values by mixed-radix decomposition over the nested loop counts.
def mme_loop_values(draw, inst, density):
    values = {}
    counts = [mme_loop_count(count, density) for _var, count, _name in draw.loops]
    remaining = inst.copy()
    for (var, _count, _name), count in reversed(list(zip(draw.loops, counts))):
        if var:
            values[var] = (remaining % count).astype(np.float64)
        remaining = remaining // count
    return values


# Loop values when the instance number is the innermost index directly (single loop).
def mme_loop_values_for(draw, inst):
    if not draw.loops:
        return {}
    var = draw.loops[0][0]
    return {var: inst.astype(np.float64)} if var else {}


# Loop count scaled by Effect Density, never below one.
def mme_loop_count(count, density):
    return max(1, int(round(count * max(0.0, min(1.0, density)))))


# Runs a pass's vertex shader on the numpy backend for all vertices at once. dtype lets analysis use float32
# for speed.
def mme_run_vs(runtime, data, state, dtype=np.float64):
    program = runtime.program
    function, args = fx_entry(program, data.draw.fx_pass.vs)
    if function is None:
        raise FxUnsupported("pass %s has no vertex shader" % data.draw.fx_pass.name)
    runtime.material_state(state, data.draw.subset)
    ex = FxExec(program, FxNumpyBackend(dtype), FxNumpyEnv(program, state, runtime.textures, data.memo, data.lanes, data.parent))
    positions, normals, uvs, loops = data.as_dtype(dtype)
    for var, values in loops.items():
        ex.overrides[var] = FxValue(("i", ()), [values])
    provide = fx_vertex_inputs(positions, normals, uvs, data.src)
    with np.errstate(all="ignore"):
        outputs = fx_run_entry(ex, function, args, provide(ex))
    return outputs, ex


# Maps the shader's clip-space position back to world space. Orthographic projections are inverted directly;
# perspective goes through fx_clip_to_world.
# Also returns which vertices are culled.
def mme_clip_world(outputs, state, count):
    position = outputs.get("POSITION0")
    if position is None:
        return np.zeros((count, 3)), np.ones(count, dtype=bool)
    comps = [np.broadcast_to(np.asarray(c, dtype=np.float64), (count,)) for c in (list(position.c) + [np.float64(1.0)] * 4)[:4]]
    view_proj = state.view @ state.proj
    try:
        inverse = np.linalg.inv(view_proj)
    except np.linalg.LinAlgError:
        inverse = np.identity(4)
    if getattr(state, "ortho", False):
        homo = np.stack(comps, axis=-1)
        world = homo @ inverse
        w = np.where(np.abs(world[:, 3:4]) < 1e-20, 1.0, world[:, 3:4])
        culled = (np.abs(comps[3]) < 1e-12) | (comps[2] < 0) | (comps[2] > comps[3])
        return world[:, :3] / w, culled
    with np.errstate(all="ignore"):
        return fx_clip_to_world(comps, inverse, state.proj)


# Only the culling decision of mme_clip_world, without the matrix work.
def mme_clip_culled(outputs, state, count):
    position = outputs.get("POSITION0")
    if position is None:
        return np.ones(count, dtype=bool)
    comps = [np.broadcast_to(np.asarray(c), (count,)) for c in (list(position.c) + [np.float32(1.0)] * 4)[:4]]
    if getattr(state, "ortho", False):
        return mme_clip_world(outputs, state, count)[1]
    return np.broadcast_to(fx_clip_culled(comps, state.proj), (count,))
