import bpy

from ..common.constants import (
    MME_ATTR_CULL,
    MME_ATTR_INST,
    MME_ATTR_PARTICLE,
    MME_ATTR_POINT,
    MME_ATTR_POS,
    MME_BAKE_NODE,
    MME_EDIT_OBJECT_PROP,
    MME_GROUP_LABEL,
    MME_LOOP_PREFIX,
    MME_ORDER_LEVELS,
    MME_ORDER_OBJECT_PROP,
    MME_OWNER_PROP,
    MME_PASS_PREFIX,
    MME_VARYING_PREFIX,
)
from ..common.controls import mme_load_controls
from ..common.log import log
from ..fx.entry import (
    fx_entry,
    fx_run_entry,
)
from ..fx.errors import FxUnsupported
from ..fx.interp.executor import FxExec
from ..fx.types import (
    FxValue,
    fx_is_numeric,
)
from .backend_geo import MmeGeoBackend
from .geo_env import (
    MmeGeoEnv,
    mme_geo_vertex_inputs,
)
from .geo_groups import (
    MmeGeoScene,
    mme_geo_clip_group,
)
from .geo_instanced import (
    mme_instanced_branch,
    mme_ordered_branch,
)
from .geo_split import mme_particle_split
from .geo_stages import mme_stage
from .geo_sprites import (
    mme_store_outputs,
    mme_strip_inputs,
    mme_wrap_pass,
)
from .layout import (
    mme_layout,
    mme_prune_tree,
)
from .socket import MmeSock


MME_ORDER_SWITCH_LABEL = "Transparency Order"


# The driver of a pass's Transparency Order input: the accessory's override when it has one, else the scene's.
def mme_order_spec(scene, root):
    return {
        "expr": "scene if own == 0 else own - 1",
        "vars": [
            {"name": "scene", "type": "SINGLE_PROP", "id_type": "SCENE", "id": scene.name, "path": "ninesync_fx.mme_transparency_order"},
            {"name": "own", "type": "SINGLE_PROP", "id_type": "OBJECT", "id": root.name, "path": MME_ORDER_OBJECT_PROP},
        ],
    }


# The driver of a sprite pass's Enable Instance Editing input: the pass object's own Edit Particle Instances switch. The
# object does not exist yet when the tree is built, so the variable is marked and gets the object's name when it is made.
def mme_edit_spec():
    return {
        "expr": "own",
        "vars": [{"name": "own", "type": "SINGLE_PROP", "id_type": "OBJECT", "id": "", "self": True, "path": MME_EDIT_OBJECT_PROP}],
    }


def mme_order_level(scene, root):
    own = getattr(root, MME_ORDER_OBJECT_PROP, "SCENE")
    if own in MME_ORDER_LEVELS:
        return MME_ORDER_LEVELS[own]
    return MME_ORDER_LEVELS.get(getattr(scene.ninesync_fx, "mme_transparency_order", "MMD"), 1)


# The pass's one Bake node, named so Effects > Bake can find it among any Bake nodes the user adds. It freezes the result
# per frame.
def mme_bake_node(S, geometry):
    bake = S.node("GeometryNodeBake", "Bake (Effects > Bake)")
    bake.name = MME_BAKE_NODE
    bake.bake_items.new("GEOMETRY", "Geometry")
    S.link(geometry, bake.inputs[0])
    return bake.outputs[0]


# Builds the Geometry Nodes tree for one pass: the vertex shader as an inner group; a top group that makes one
# copy of the mesh per LoopByCount index,
# runs the shader, maps clip space back to object space, collapses culled vertices and stores
# culled/varying/loop values as attributes for the material.
# Returns the tree, its drivers and defaults, and the node count.
def mme_build_geo_tree(label, runtime, draw, state, root, unit, varyings, loop_attrs, scene, empty_at_zero_tr=False, sprites=False, particle_split=True, instanced=None):
    program = runtime.program
    function, args = fx_entry(program, draw.fx_pass.vs)
    if function is None:
        raise FxUnsupported("pass %s has no vertex shader" % draw.fx_pass.name)
    owner = "%s|%s" % (root.get("mmd_bridge_accessory"), label)
    mme_drop_orphan_groups(owner)
    controls = mme_load_controls(root)
    inner = bpy.data.node_groups.new("MME VS " + label, "GeometryNodeTree")
    inner[MME_OWNER_PROP] = owner
    top = None
    outer = None
    split = None
    try:
        needs = {}
        B = MmeGeoBackend(inner)
        runtime.material_state(state, draw.subset)
        env = MmeGeoEnv(program, state, runtime.textures, controls, needs)
        ex = FxExec(program, B, env)
        for var, _count, _name in draw.loops:
            if var:
                key = "l:" + var
                needs[key] = ("loop", var)
                ex.overrides[var] = FxValue(("i", ()), [B.new_input(key, label="LoopGetIndex %s" % var)])
        outputs = fx_run_entry(ex, function, args, mme_geo_vertex_inputs(needs)(ex))
        position = outputs.get("POSITION0")
        if position is not None and fx_is_numeric(position.t):
            clip = list(ex.convert(position, ("f", (4,))).c)
        else:
            clip = [0.0, 0.0, 0.0, 0.0]
        outs = [("Clip %s" % c, clip[k], "NodeSocketFloat") for k, c in enumerate("xyzw")]
        for name in varyings:
            semantic, _sep, index = name[len(MME_VARYING_PREFIX):].rpartition("_")
            value = outputs.get(semantic)
            comp = 0.0
            if value is not None and fx_is_numeric(value.t):
                k = int(index)
                comp = value.c[k] if k < len(value.c) else 0.0
            outs.append((name, comp, "NodeSocketFloat"))
        B.statement("vertex shader outputs")
        B.finish(outs)
        inner_inputs = {key: B.input_identifier(key) for key in list(B.inputs)}
        top = bpy.data.node_groups.new(MME_PASS_PREFIX + label, "GeometryNodeTree")
        top[MME_OWNER_PROP] = owner
        top.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        top.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        S = MmeGeoScene(top, root, unit, scene, state, controls, program)
        T = S.B
        group_in = T.new("NodeGroupInput")
        T.block_of[group_in.name] = None
        geometry = group_in.outputs[0]
        T.statement("The shader's position input, taken from the mesh after your Edit Source Mesh stage (in the pass wrapper)")
        geometry = mme_source_stage(S, geometry)
        if draw.loops:
            T.statement("LoopByCount: one copy of the mesh per loop index")
            density = S.driven(
                "density",
                "Effect Density",
                {"expr": "var", "vars": [{"name": "var", "type": "SINGLE_PROP", "id_type": "SCENE", "id": scene.name, "path": "ninesync_fx.mme_density"}]},
                scene.ninesync_fx.mme_density,
            )
            params = getattr(program, "params", {})
            effective = []
            for var, count, name in draw.loops:
                if name in params:
                    source = S.param_values(name, 1)[0]
                else:
                    source = float(count)
                T.statement("LoopByCount=%s" % name)
                effective.append(T.max(1.0, T.unary("round", T.mul(source, density))))
            total = 1.0
            for value in effective:
                total = T.mul(total, value)
            points = S.node("GeometryNodePoints", "loop instances")
            T.feed(points.inputs["Count"], total)
            index = S.node("GeometryNodeInputIndex")
            store = S.node("GeometryNodeStoreNamedAttribute", "store loop instance", data_type="INT", domain="POINT")
            store.inputs["Name"].default_value = MME_ATTR_INST
            S.link(points.outputs[0], store.inputs["Geometry"])
            S.link(index.outputs[0], store.inputs["Value"])
            inst_attr = S.node("GeometryNodeInputNamedAttribute", "loop instance", data_type="INT")
            inst_attr.inputs["Name"].default_value = MME_ATTR_INST
            remaining = MmeSock(inst_attr.outputs["Attribute"])
            T.statement("LoopGetIndex values")
            for (var, _count, _name), value in reversed(list(zip(draw.loops, effective))):
                if var:
                    S.cache[("loopvar", var)] = T.imod(remaining, value)
                remaining = T.idiv(remaining, value)
            provided = {key: S.provide(needs.get(key, ("none",))) for key in inner_inputs}
            if particle_split:
                split, reason = mme_particle_split(inner, inner_inputs, provided, "MME VS Particles " + label, MME_OWNER_PROP, owner)
                if split is not None:
                    log.info("effect pass %s: %d vertex shader node(s) run once per particle, %d value(s) handed to the per-vertex part", label, split.moved, len(split.values))
                else:
                    log.info("effect pass %s: vertex shader stays per vertex (%s)", label, reason)
            points_geometry = mme_particle_stage(S, store.outputs[0], split, inner_inputs, provided)
            T.statement("Your edits to the particles")
            points_geometry, instanced_points = mme_particles_stage(S, points_geometry, owner, split is not None)
            source_geometry = geometry
            instance = S.node("GeometryNodeInstanceOnPoints")
            S.link(instanced_points, instance.inputs["Points"])
            S.link(geometry, instance.inputs["Instance"])
            realize = S.node("GeometryNodeRealizeInstances")
            S.link(instance.outputs[0], realize.inputs[0])
            geometry = realize.outputs[0]
        else:
            provided = {key: S.provide(needs.get(key, ("none",))) for key in inner_inputs}
        shared = {}
        if split is not None:
            T.statement("Values computed once per particle")
            point_attr = S.node("GeometryNodeInputNamedAttribute", "particle point", data_type="INT")
            point_attr.inputs["Name"].default_value = MME_ATTR_POINT
            for value in split.values:
                reader = S.node("GeometryNodeInputNamedAttribute", value.label, data_type=value.data_type)
                reader.inputs["Name"].default_value = value.attribute
                sample = S.node("GeometryNodeSampleIndex", value.label, data_type=value.data_type, domain="POINT")
                S.link(points_geometry, sample.inputs["Geometry"])
                S.link(reader.outputs["Attribute"], sample.inputs["Value"])
                S.link(point_attr.outputs["Attribute"], sample.inputs["Index"])
                shared[value.input] = sample.outputs["Value"]

        def wire():
            return mme_vs_node(S, inner, inner_inputs, provided, shared)

        T.statement("Vertex shader: %s" % function.name)
        vs = wire()
        T.statement("Evaluate the vertex shader once per vertex")
        geometry, shader = mme_capture_outputs(S, geometry, vs, ["Clip %s" % c for c in "xyzw"] + list(varyings))
        offset = mme_particle_offset(S) if draw.loops else None
        T.statement("Clip space to object space")
        clip_node = S.group_node(
            mme_geo_clip_group(),
            "Clip space to object space",
            {"Clip X": shader["Clip x"], "Clip Y": shader["Clip y"], "Clip Z": shader["Clip z"], "Clip W": shader["Clip w"], "Unit": unit},
        )
        culled = MmeSock(clip_node.outputs["Culled"])
        T.boolish.add(T.key(culled))
        T.statement("Set positions; culled vertices collapse to the origin")
        set_pos = S.node("GeometryNodeSetPosition")
        S.link(geometry, set_pos.inputs["Geometry"])
        S.link(clip_node.outputs["Position"], set_pos.inputs["Position"])
        if offset is not None:
            S.link(offset, set_pos.inputs["Offset"])
        geometry = set_pos.outputs[0]
        T.statement("Store vertex shader outputs for the material")

        def store(name, value, data_type="FLOAT"):
            nonlocal geometry
            node = S.node("GeometryNodeStoreNamedAttribute", name, data_type=data_type, domain="POINT")
            node.inputs["Name"].default_value = name
            S.link(geometry, node.inputs["Geometry"])
            T.feed(node.inputs["Value"], value)
            geometry = node.outputs[0]

        store(MME_ATTR_CULL, culled)
        if varyings:
            geometry = mme_store_outputs(S, geometry, list(varyings), [MmeSock(shader[name]) for name in varyings])
        for name in loop_attrs:
            var = name[len(MME_LOOP_PREFIX):]
            store(name, S.cache.get(("loopvar", var), 0.0))
        T.statement("Drop vertex shader inputs the material never reads")
        geometry = mme_strip_inputs(S, geometry)
        if split is not None:
            geometry = mme_remove_attribute(S, geometry, MME_ATTR_POINT, "drop particle point index")
        if instanced is not None and draw.loops:
            plan, material, instanced_material, order_free = instanced
            branch = mme_instanced_branch(S, source_geometry, instanced_points, plan, wire, varyings, loop_attrs, unit, instanced_material)
            T.statement("Transparency Order (Effects panel, or the accessory's Effect Controls)")
            level = S.driven("order", "Transparency Order", mme_order_spec(scene, root), float(mme_order_level(scene, root)))
            if not order_free:
                ordered = mme_ordered_branch(S, branch, plan, varyings, material)
                sorted_switch = S.node("GeometryNodeSwitch", "Blender Sorted?", input_type="GEOMETRY")
                T.feed(sorted_switch.inputs["Switch"], T.cmp(">", level, 1.5))
                S.link(ordered, sorted_switch.inputs["False"])
                S.link(branch, sorted_switch.inputs["True"])
                branch = sorted_switch.outputs[0]
            choose = S.node("GeometryNodeSwitch", MME_ORDER_SWITCH_LABEL, input_type="GEOMETRY")
            T.feed(choose.inputs["Switch"], T.cmp(">", level, 0.5))
            S.link(geometry, choose.inputs["False"])
            S.link(branch, choose.inputs["True"])
            geometry = choose.outputs[0]
        if empty_at_zero_tr:
            T.statement("Empty geometry while Tr is 0 (proved when the pass was built)")
            tr = S.object_items(root, "tr", "", 1, False)[0]
            off = S.node("FunctionNodeCompare", "Tr <= 0", data_type="FLOAT", operation="LESS_EQUAL")
            T.feed(off.inputs["A"], tr)
            off.inputs["B"].default_value = 0.0
            switch = S.node("GeometryNodeSwitch", "Hide at Tr = 0", input_type="GEOMETRY")
            S.link(off.outputs[0], switch.inputs["Switch"])
            S.link(geometry, switch.inputs["False"])
            geometry = switch.outputs[0]
        T.statement("Output")
        group_out = T.new("NodeGroupOutput")
        T.block_of[group_out.name] = "out"
        S.link(geometry, group_out.inputs[0])
        mme_prune_tree(top, group_out)
        for node in top.nodes:
            if node.bl_idname == "NodeGroupInput":
                for socket in node.outputs:
                    socket.hide = not socket.is_linked
        mme_layout(top, T.block_of, T.blocks)
        outer, mapping, edit_input, live_input = mme_wrap_pass(top, "MME " + label, owner, sprites)
        drivers = [(mapping.get(identifier, identifier), spec) for identifier, spec in S.drivers]
        if edit_input is not None:
            drivers.append((edit_input, mme_edit_spec()))
        defaults = {mapping.get(identifier, identifier): value for identifier, value in S.defaults.items()}
        defaults[live_input] = False
        return outer, drivers, defaults, len(inner.nodes) + len(top.nodes) + len(outer.nodes) + (len(split.tree.nodes) if split is not None else 0)
    except Exception:
        for group in (outer, top, split.tree if split is not None else None):
            if group is not None:
                bpy.data.node_groups.remove(group)
        bpy.data.node_groups.remove(inner)
        raise


# The vertex shader group node with the pass's inputs: scene values and vertex data from 'provided', the
# per-particle values of a split shader from 'shared' (input identifier to the Sample Index reading them).
def mme_vs_node(S, inner, inner_inputs, provided, shared):
    vs = S.node("GeometryNodeGroup", MME_GROUP_LABEL)
    vs.node_tree = inner
    vs.width = 260
    for key, identifier in inner_inputs.items():
        target = next((s for s in vs.inputs if s.identifier == identifier), None)
        if target is None:
            continue
        value = provided[key]
        if isinstance(value, (MmeSock, bpy.types.NodeSocket)):
            S.link(value, target)
        else:
            S.B.feed(target, value)
    for identifier, socket in shared.items():
        S.link(socket, next(s for s in vs.inputs if s.identifier == identifier))
    return vs


# Runs the particle part of a split vertex shader on the loop instance points and stores each value it hands
# to the vertex part as a point attribute there. The vertex part reads them with Sample Index by loop instance,
# so the values are never copied onto the realized mesh. Returns the points unchanged when nothing was split.
def mme_particle_stage(S, points, split, inner_inputs, provided):
    if split is None:
        return points
    S.B.statement("Vertex shader, per particle: values that depend only on the loop index")
    node = S.node("GeometryNodeGroup", MME_GROUP_LABEL)
    node.node_tree = split.tree
    node.width = 260
    for key, identifier in inner_inputs.items():
        target = next((s for s in node.inputs if s.identifier == identifier), None)
        if target is None:
            continue
        value = provided[key]
        if isinstance(value, (MmeSock, bpy.types.NodeSocket)):
            S.link(value, target)
        else:
            S.B.feed(target, value)
    for value in split.values:
        store = S.node("GeometryNodeStoreNamedAttribute", value.label, data_type=value.data_type, domain="POINT")
        store.inputs["Name"].default_value = value.attribute
        S.link(points, store.inputs["Geometry"])
        S.link(next(s for s in node.outputs if s.identifier == value.output), store.inputs["Value"])
        points = store.outputs[0]
    return points


# The shader's position input taken again from the vertices that arrive after the user's Edit Source Mesh stage, which sits in
# the pass wrapper (an exact axis swap, so an untouched mesh gives the stored values bit for bit).
def mme_source_stage(S, geometry):
    position = S.node("GeometryNodeInputPosition")
    store = S.node("GeometryNodeStoreNamedAttribute", "shader position from the edited mesh", data_type="FLOAT_VECTOR", domain="POINT")
    store.inputs["Name"].default_value = MME_ATTR_POS
    S.link(geometry, store.inputs["Geometry"])
    S.link(S.swap(position.outputs[0]), store.inputs["Value"])
    return store.outputs[0]


# The user's Edit Particles stage on the loop instance points. With a split shader, each point's index after the
# edits is stored so every copy reads the per-particle values of its own point (deleted, duplicated or edited
# points stay consistent), and the per-particle values are dropped from the points that get instanced so they are
# never copied to the realized mesh. Returns the edited points and the points to instance.
def mme_particles_stage(S, points, owner, split):
    points = mme_stage(S, points, owner, "particles")
    if not split:
        return points, points
    index = S.node("GeometryNodeInputIndex")
    store = S.node("GeometryNodeStoreNamedAttribute", "particle point index", data_type="INT", domain="POINT")
    store.inputs["Name"].default_value = MME_ATTR_POINT
    S.link(points, store.inputs["Geometry"])
    S.link(index.outputs[0], store.inputs["Value"])
    points = store.outputs[0]
    return points, mme_remove_attribute(S, points, MME_ATTR_PARTICLE + "*", "keep per-particle values off the copies", True)


# How far the user moved each particle's point: the realized vertex minus its source vertex, zero for an
# untouched point. Added after the shader so a moved point moves that particle's result.
def mme_particle_offset(S):
    position = S.node("GeometryNodeInputPosition")
    source = S.node("GeometryNodeInputNamedAttribute", "source vertex", data_type="FLOAT_VECTOR")
    source.inputs["Name"].default_value = MME_ATTR_POS
    return S.vec_node("SUBTRACT", position.outputs[0], S.swap(source.outputs["Attribute"]), "particle point offset")


def mme_remove_attribute(S, geometry, name, label, wildcard=False):
    remove = S.node("GeometryNodeRemoveAttribute", label)
    mode = remove.inputs.get("Pattern Mode")
    if wildcard and mode is not None:
        mode.default_value = "Wildcard"
    elif wildcard:
        remove.pattern_mode = "WILDCARD"
    remove.inputs["Name"].default_value = name
    S.link(geometry, remove.inputs["Geometry"])
    return remove.outputs[0]


# Geometry Nodes evaluates a field again for every node that reads it, so without this the whole vertex shader
# ran once per consumer (Set Position, the cull flag and every stored varying). One Capture Attribute evaluates
# all outputs in a single pass and shares their common work.
def mme_capture_outputs(S, geometry, vs, names):
    capture = S.node("GeometryNodeCaptureAttribute", "vertex shader outputs, evaluated once", domain="POINT")
    S.link(geometry, capture.inputs[0])
    captured = {}
    for name in names:
        item = capture.capture_items.new("FLOAT", name)
        S.link(vs.outputs[name], capture.inputs[item.name])
        captured[name] = capture.outputs[item.name]
    return capture.outputs[0], captured


# Removes unused geometry groups of the same owner before a rebuild; the second pass catches groups that only
# became unused after the first.
def mme_drop_orphan_groups(owner):
    for group in [g for g in bpy.data.node_groups if g.get(MME_OWNER_PROP) == owner and g.bl_idname == "GeometryNodeTree"]:
        if group.users == 0 and not group.use_fake_user:
            bpy.data.node_groups.remove(group)
    for group in [g for g in bpy.data.node_groups if g.get(MME_OWNER_PROP) == owner and g.bl_idname == "GeometryNodeTree"]:
        if group.users == 0 and not group.use_fake_user:
            bpy.data.node_groups.remove(group)


# Tree for a baked pass: just the bake node plus a scene-time frame stamp that forces re-evaluation each
# frame.
def mme_bake_only_tree(label, root):
    mme_drop_orphan_groups("%s|%s" % (root.get("mmd_bridge_accessory"), label))
    top = bpy.data.node_groups.new("MME " + label, "GeometryNodeTree")
    top[MME_OWNER_PROP] = "%s|%s" % (root.get("mmd_bridge_accessory"), label)
    top.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    top.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    S = MmeGeoScene(top, root, 1.0, bpy.context.scene, None, {}, None)
    group_in = S.node("NodeGroupInput")
    group_in.location = (0, 0)
    time_node = S.node("GeometryNodeInputSceneTime", "re-evaluate every frame")
    time_node.location = (0, -160)
    stamp = S.node("GeometryNodeStoreNamedAttribute", "frame stamp", data_type="FLOAT", domain="POINT")
    stamp.inputs["Name"].default_value = "mme_frame"
    stamp.location = (220, 0)
    S.link(group_in.outputs[0], stamp.inputs["Geometry"])
    S.link(time_node.outputs["Frame"], stamp.inputs["Value"])
    geometry = mme_bake_node(S, stamp.outputs[0])
    S.tree.nodes[-1].location = (440, 0)
    group_out = S.node("NodeGroupOutput")
    group_out.location = (680, 0)
    S.link(geometry, group_out.inputs[0])
    return top
