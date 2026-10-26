import bpy
import json
import numpy as np
import traceback

from ..common.constants import (
    MME_ATTR_CULL,
    MME_ATTR_INST,
    MME_ATTR_SRC,
    MME_BACKEND_PROP,
    MME_BAKED_PROP,
    MME_DRAW_PROP,
    MME_VARYINGS_PROP,
    MME_VARYING_PREFIX,
)
from ..common.controls import mme_effect_roots
from ..common.log import log
from ..common.state import _mme_state
from ..fx.semantics import fx_classify_global
from ..fx.types import fx_is_numeric
from .drivers import mme_handler_uniforms
from .frame_state import mme_frame_state
from .matrices import (
    mme_np_matrix,
    mme_world_row,
)
from .mesh_build import (
    mme_read_attribute,
    mme_set_attribute,
    mme_world_to_local,
)
from .runtime import mme_runtime_for
from .vs_eval import (
    MmePassData,
    mme_clip_world,
    mme_loop_values,
    mme_run_vs,
)


# Stores the vertex-shader outputs as point attributes for the material, replacing NaN and infinities with
# finite values.
def mme_write_outputs(obj, mesh, outputs, count, varyings):
    for name in varyings:
        body = name[len(MME_VARYING_PREFIX):]
        semantic, _sep, index = body.rpartition("_")
        value = outputs.get(semantic)
        if value is None or not fx_is_numeric(value.t):
            arr = np.zeros(count)
        else:
            k = int(index)
            comp = value.c[k] if k < len(value.c) else np.float64(0.0)
            arr = np.broadcast_to(np.asarray(comp, dtype=np.float64), (count,))
        mme_set_attribute(mesh, name, np.nan_to_num(arr, nan=0.0, posinf=1e6, neginf=-1e6))


# Rebuilds the vertex data of a Python-backend pass from its mesh attributes and draw description; cached
# until the mesh or description changes.
def mme_pass_data(runtime, obj):
    try:
        info = json.loads(obj.get(MME_DRAW_PROP, "{}"))
    except (TypeError, ValueError):
        return None
    mesh = obj.data
    stamp = (mesh.name, len(mesh.vertices), obj.get(MME_DRAW_PROP))
    cached = runtime.passes.get(obj.name)
    if cached is not None and cached.stamp == stamp:
        return cached
    draw = runtime.draw_by_order(info.get("order", -1))
    if draw is None or draw.fx_pass.name != info.get("pass") or draw.technique.name != info.get("technique"):
        return None
    src = mme_read_attribute(mesh, MME_ATTR_SRC)
    inst = mme_read_attribute(mesh, MME_ATTR_INST)
    if src is None or inst is None or len(src) != len(mesh.vertices):
        return None
    src = src.astype(np.int64)
    inst = inst.astype(np.int64)
    if len(src) and src.max() >= len(runtime.positions):
        return None
    data = MmePassData(draw, src, inst, mme_loop_values(draw, inst, info.get("density", 1.0)), runtime)
    data.stamp = stamp
    runtime.passes[obj.name] = data
    return data


# Per-frame update: Python-backend passes re-run the vertex shader and rewrite positions and attributes;
# uniforms without drivers are set directly.
def mme_update_pass(runtime, obj, root, state, unit, depsgraph=None):
    evaluated = obj.evaluated_get(depsgraph) if depsgraph is not None else obj
    child = mme_np_matrix(evaluated.matrix_world)
    state.world = mme_world_row(child, unit)
    mesh = obj.data
    if obj.get(MME_BACKEND_PROP, "PYTHON") == "PYTHON" and not obj.get(MME_BAKED_PROP):
        data = mme_pass_data(runtime, obj)
        if data is None:
            return False
        outputs, _ex = mme_run_vs(runtime, data, state)
        world, culled = mme_clip_world(outputs, state, data.count)
        local = mme_world_to_local(world, culled, child, unit)
        mesh.vertices.foreach_set("co", local.astype(np.float32).reshape(-1))
        mme_set_attribute(mesh, MME_ATTR_CULL, culled.astype(np.float32))
        try:
            varyings = json.loads(obj.get(MME_VARYINGS_PROP, "[]"))
        except (TypeError, ValueError):
            varyings = []
        mme_write_outputs(obj, mesh, outputs, data.count, varyings)
    uniforms = mme_handler_uniforms(obj)
    program = runtime.program
    for prop, gname, index in uniforms:
        g = program.globals.get(gname)
        if g is None:
            continue
        source = fx_classify_global(g, program)
        if source is None:
            continue
        values = state.values(source, g)
        obj[prop] = float(values[index]) if index < len(values) else 0.0
    mesh.update()
    obj.update_tag()
    return True


# Frame handler body. Updates visible passes of every effect root, only while rendering, in live mode or
# during a bake. Each error is logged once.
def mme_update_scene(scene, depsgraph=None, force=False):
    settings = getattr(scene, "ninesync_fx", None)
    if settings is None or not getattr(settings, "mme_effects", False):
        return 0
    rendering = False
    try:
        rendering = bpy.app.is_job_running("RENDER")
    except (AttributeError, TypeError):
        pass
    if not (force or rendering or settings.mme_live or _mme_state.get("only") is not None):
        return 0
    updated = 0
    only = _mme_state.get("only")
    for root in mme_effect_roots(scene):
        children = [c for c in root.children if c.get(MME_DRAW_PROP) and c.get("mme_handler", True)]
        if only is not None:
            children = [c for c in children if c.name in only]
        if not children:
            continue
        if rendering:
            children = [c for c in children if not c.hide_render]
        else:
            children = [c for c in children if not c.hide_viewport and not c.hide_get()]
        if not children:
            continue
        try:
            runtime = mme_runtime_for(root)
        except Exception as error:
            key = (root.name, str(error))
            if key not in _mme_state["errors"]:
                _mme_state["errors"].add(key)
                log.error("effect for '%s' could not be loaded: %s", root.name, error)
            continue
        if runtime is None:
            continue
        unit = float(root.get("mme_unit", 1.0)) or 1.0
        state = mme_frame_state(scene, depsgraph, root, runtime, unit)
        for child in children:
            try:
                if mme_update_pass(runtime, child, root, state, unit, depsgraph):
                    updated += 1
            except Exception as error:
                key = (child.name, str(error))
                if key not in _mme_state["errors"]:
                    _mme_state["errors"].add(key)
                    log.error("effect pass '%s' failed: %s\n%s", child.name, error, traceback.format_exc())
    return updated
