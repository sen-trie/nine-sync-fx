import bpy
import json
import numpy as np
import time

from ..common.constants import (
    MME_ATTR_CULL,
    MME_ATTR_INST,
    MME_ATTR_NRM,
    MME_ATTR_POS,
    MME_ATTR_SRC,
    MME_ATTR_UV,
    MME_BACKEND_PROP,
    MME_DRAW_PROP,
    MME_GATE_MARGIN,
    MME_GN_MODIFIER,
    MME_LOOP_PREFIX,
    MME_PRUNED_PROP,
    MME_SWAP3,
    MME_UNIFORMS_PROP,
    MME_VARYINGS_PROP,
)
from ..common.log import log
from ..fx.errors import FxError
from ..fx.params import fx_vs_globals
from ..fx.types import fx_is_numeric
from ..nodes.drivers import mme_set_drivers
from ..nodes.geo_tree import (
    mme_bake_only_tree,
    mme_build_geo_tree,
)
from ..nodes.material_compile import (
    mme_compile_material,
    mme_fallback_material,
)
from ..nodes.names import mme_varying_name
from .drivers import (
    mme_drive_uniforms,
    mme_needs_handler,
)
from .matrices import mme_world_row
from .mesh_build import (
    mme_build_mesh,
    mme_child_matrix,
    mme_pass_object,
    mme_set_attribute,
    mme_world_to_local,
)
from .precull import mme_precull_file
from .progress import mme_progress_within
from .pruning import (
    mme_alpha_gates,
    mme_compact,
    mme_grow_faces,
    mme_keep_faces,
    mme_prune_mask,
)
from .strips import mme_simplify_strip
from .update import mme_write_outputs
from .vs_eval import (
    MmePassData,
    mme_clip_world,
    mme_loop_count,
    mme_loop_values,
    mme_run_vs,
    mme_self_tr_globals,
)


# Faces of the draw's material subset, vertices renumbered (face order reversed), loop counts under the
# current density and the instance count.
def mme_pass_geometry(runtime, draw, density):
    subset_faces = [f for f, s in zip(runtime.faces, runtime.face_subsets) if s == draw.subset]
    if not subset_faces:
        return None
    used = np.unique(np.concatenate([np.asarray(f, dtype=np.int64) for f in subset_faces]))
    remap = {int(v): i for i, v in enumerate(used)}
    local_faces = [tuple(remap[int(v)] for v in reversed(f)) for f in subset_faces]
    counts = [mme_loop_count(count, density) for _var, count, _name in draw.loops]
    instances = int(np.prod(counts)) if counts else 1
    return used, local_faces, counts, instances


# Pixel shader material for a pass; if the shader cannot be converted, a plain material is used and the vertex
# outputs fall back to the first UV set.
def mme_pass_material(runtime, draw, state, label, owner, vs_outputs):
    runtime.material_state(state, draw.subset)
    try:
        mat, varyings, uniforms, mode, loop_attrs, node_count = mme_compile_material(
            label, runtime, draw, state, vs_outputs, owner
        )
        return mat, varyings, uniforms, loop_attrs, "%s, %d shader nodes" % (mode.lower(), node_count)
    except FxError as error:
        log.warning("effect pass %s: pixel shader not converted (%s); using a plain material", label, error)
        mat = mme_fallback_material(label, runtime, draw.subset, reason=str(error), owner=owner)
        varyings = [mme_varying_name("TEXCOORD0", 0), mme_varying_name("TEXCOORD0", 1)]
        return mat, varyings, [], [], "fallback material"


# Attaches the material and stores what later stages need on the pass object (draw description, varyings,
# uniforms, backend, vertex-shader parameters), then adds the uniform drivers.
def mme_finish_pass(obj, runtime, draw, root, scene, counts, density, mat, varyings, uniforms, backend, pruned):
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    obj[MME_DRAW_PROP] = json.dumps(
        {
            "order": draw.order,
            "technique": draw.technique.name,
            "pass": draw.fx_pass.name,
            "subset": draw.subset,
            "loops": [[var, count] for var, count in zip([l[0] for l in draw.loops], counts)],
            "density": density,
        }
    )
    obj[MME_VARYINGS_PROP] = json.dumps(varyings)
    obj[MME_UNIFORMS_PROP] = json.dumps(uniforms)
    obj[MME_BACKEND_PROP] = backend
    obj[MME_PRUNED_PROP] = bool(pruned)
    params = getattr(runtime.program, "params", {})
    used = fx_vs_globals(runtime.program, [draw.fx_pass]) | {name for _var, _count, name in draw.loops}
    obj["mme_vs_params"] = json.dumps(sorted(name for name in params if name in used))
    for prop, gname, index in uniforms:
        obj[prop] = 0.0
    mme_drive_uniforms(obj, runtime.program, uniforms, root, scene)
    obj["mme_handler"] = mme_needs_handler(obj)


# Builds one pass object. Probes the vertex shader, finds alpha gates, prunes vertices and faces that never
# show, simplifies strips, then builds either
# Geometry Nodes (the vertex shader as nodes) or, for 'PYTHON' and for shaders nodes cannot express, a mesh
# evaluated by numpy each frame.
def mme_build_pass(runtime, draw, state, root, collection, unit, density, label, backend="GEONODES", scene=None):
    scene = scene or bpy.context.scene
    geometry = mme_pass_geometry(runtime, draw, density)
    if geometry is None:
        return None
    used, local_faces, counts, instances = geometry
    per = len(used)
    src = np.tile(used, instances)
    inst = np.repeat(np.arange(instances, dtype=np.int64), per)
    data = MmePassData(draw, src, inst, mme_loop_values(draw, inst, density), runtime)
    state.world = mme_world_row(mme_child_matrix(root, unit), unit)
    tr_names = mme_self_tr_globals(runtime, root)
    probe_one = MmePassData(draw, used[:1], np.zeros(1, dtype=np.int64), mme_loop_values(draw, np.zeros(1, dtype=np.int64), density), runtime)
    probe_outputs, _ex = mme_run_vs(runtime, probe_one, state)
    probe_varyings = {k: len(v.c) for k, v in probe_outputs.items() if k != "POSITION0" and fx_is_numeric(v.t)}
    reduce_started = time.time()
    mme_progress_within(0.05, "finding alpha gates")
    gates = mme_alpha_gates(runtime, draw, state, probe_varyings, tr_names, instances)
    cache_path = None
    count_params = ()
    if getattr(scene.ninesync_fx, "mme_precull", True):
        single = len(draw.loops) <= 1
        if single:
            vs_reads = fx_vs_globals(runtime.program, [draw.fx_pass])
            count_params = tuple(sorted(name for _var, _count, name in draw.loops if name and name not in vs_reads))
        cache_path = mme_precull_file(runtime, draw, used, gates, None if single else instances)
    keep, opened = mme_prune_mask(runtime, data, state, tr_names, gates, cache_path, per, instances, count_params)
    owner = "%s|%s" % (root.get("mmd_bridge_accessory"), label)
    keep_src = keep.reshape(instances, per).any(axis=0)
    open_src = opened.reshape(instances, per).any(axis=0)
    before = len(local_faces)
    local_faces = mme_keep_faces(local_faces, keep_src)
    culled_faces = len(local_faces)
    if gates:
        local_faces = mme_grow_faces(local_faces, mme_keep_faces(local_faces, keep_src, opened=open_src), MME_GATE_MARGIN)
    gated_faces = len(local_faces)
    tolerance = float(getattr(scene.ninesync_fx, "mme_tolerance", 0.0))
    mme_progress_within(0.65, "simplifying strips")
    try:
        local_faces = mme_simplify_strip(runtime, draw, state, used, local_faces, instances, tr_names, density, tolerance, cache_path)
    except Exception as error:
        log.debug("strip simplification for %s failed: %s", label, error)
    if before != len(local_faces):
        log.info(
            "effect pass %s: %d of %d faces kept (%d never drawn, %d always transparent%s, %d merged along strips) in %.1fs",
            label,
            len(local_faces),
            before,
            before - culled_faces,
            culled_faces - gated_faces,
            " via " + ", ".join("%s.%s" % (sem, "xyzw"[k] if k < 4 else k) for sem, k in gates) if gates else "",
            gated_faces - len(local_faces),
            time.time() - reduce_started,
        )
    if backend == "GEONODES":
        faces = local_faces
        if not faces:
            log.info("effect pass %s draws nothing visible; skipped", label)
            return None
        alive, faces = mme_compact(used, faces)
        src_one = used[alive]
        pruned = len(src_one) < per
        one = MmePassData(draw, src_one, np.zeros(len(src_one), dtype=np.int64), mme_loop_values(draw, np.zeros(len(src_one), dtype=np.int64), density), runtime)
        outputs, _ex = mme_run_vs(runtime, one, state)
        vs_outputs = {k: len(v.c) for k, v in outputs.items() if k != "POSITION0" and fx_is_numeric(v.t)}
        mme_progress_within(0.8, "material")
        mat, varyings, uniforms, loop_attrs, note = mme_pass_material(runtime, draw, state, label, owner, vs_outputs)
        mme_progress_within(0.9, "node tree")
        try:
            started = time.time()
            tree, drivers, defaults, node_count = mme_build_geo_tree(label, runtime, draw, state, root, unit, varyings, loop_attrs, scene)
        except FxError as error:
            log.warning("effect pass %s: vertex shader cannot run in Geometry Nodes (%s); using the Python backend", label, error)
            return mme_build_pass(runtime, draw, state, root, collection, unit, density, label, "PYTHON", scene)
        positions = runtime.positions[src_one]
        mesh = mme_build_mesh(label, positions @ MME_SWAP3.T, faces)
        mme_set_attribute(mesh, MME_ATTR_SRC, src_one, "INT")
        mme_set_attribute(mesh, MME_ATTR_POS, positions, "FLOAT_VECTOR")
        mme_set_attribute(mesh, MME_ATTR_NRM, runtime.normals[src_one], "FLOAT_VECTOR")
        uv3 = np.zeros((len(src_one), 3))
        uv3[:, :2] = runtime.uvs[src_one]
        mme_set_attribute(mesh, MME_ATTR_UV, uv3, "FLOAT_VECTOR")
        obj = mme_pass_object(label, mesh, collection, root)
        modifier = obj.modifiers.new(MME_GN_MODIFIER, "NODES")
        modifier.node_group = tree
        mme_set_drivers(obj, modifier, drivers, defaults)
        mme_finish_pass(obj, runtime, draw, root, scene, counts, density, mat, varyings, uniforms, "GEO", pruned)
        log.info(
            "effect pass %s: Geometry Nodes, %d source vertices x %d loop instance(s), %d faces each, %d nodes (%.2fs), %s",
            label,
            len(src_one),
            instances,
            len(faces),
            node_count,
            time.time() - started,
            note,
        )
        return obj
    faces = []
    for k in range(instances):
        offset = k * per
        for face in local_faces:
            faces.append(tuple(v + offset for v in face))
    faces = [f for f in faces if any(keep[v] for v in f) and any(opened[v] for v in f)]
    if not faces:
        log.info("effect pass %s draws nothing visible; skipped", label)
        return None
    alive, faces = mme_compact(src, faces)
    pruned = not alive.all()
    src = src[alive]
    inst = inst[alive]
    data = MmePassData(draw, src, inst, mme_loop_values(draw, inst, density), runtime)
    outputs, _ex = mme_run_vs(runtime, data, state)
    vs_outputs = {k: len(v.c) for k, v in outputs.items() if k != "POSITION0" and fx_is_numeric(v.t)}
    world, culled = mme_clip_world(outputs, state, data.count)
    local = mme_world_to_local(world, culled, mme_child_matrix(root, unit), unit)
    mesh = mme_build_mesh(label, local, faces)
    mme_set_attribute(mesh, MME_ATTR_SRC, src, "INT")
    mme_set_attribute(mesh, MME_ATTR_INST, inst, "INT")
    mme_set_attribute(mesh, MME_ATTR_CULL, culled.astype(np.float32))
    for var, values in data.loop_values.items():
        mme_set_attribute(mesh, MME_LOOP_PREFIX + var, values)
    mat, varyings, uniforms, _loop_attrs, note = mme_pass_material(runtime, draw, state, label, owner, vs_outputs)
    obj = mme_pass_object(label, mesh, collection, root)
    modifier = obj.modifiers.new(MME_GN_MODIFIER, "NODES")
    modifier.node_group = mme_bake_only_tree(label, root)
    mme_finish_pass(obj, runtime, draw, root, scene, counts, density, mat, varyings, uniforms, "PYTHON", pruned)
    mme_write_outputs(obj, mesh, outputs, data.count, varyings)
    log.info(
        "effect pass %s: Python, %d vertices, %d faces, %d instance(s), %s",
        label,
        len(src),
        len(faces),
        instances,
        note,
    )
    return obj
