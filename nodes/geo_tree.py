import bpy

from ..common.constants import (
    MME_ATTR_CULL,
    MME_ATTR_INST,
    MME_LOOP_PREFIX,
    MME_OWNER_PROP,
    MME_VARYING_PREFIX,
)
from ..common.controls import mme_load_controls
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
from .layout import (
    mme_layout,
    mme_prune_tree,
)
from .socket import MmeSock


# Bake node at the end of the tree so Effects > Bake can freeze the result per frame.
def mme_bake_node(S, geometry):
    bake = S.node("GeometryNodeBake", "Bake (Effects > Bake)")
    bake.bake_items.new("GEOMETRY", "Geometry")
    S.link(geometry, bake.inputs[0])
    return bake.outputs[0]


# Builds the Geometry Nodes tree for one pass: the vertex shader as an inner group; a top group that makes one
# copy of the mesh per LoopByCount index,
# runs the shader, maps clip space back to object space, collapses culled vertices and stores
# culled/varying/loop values as attributes for the material.
# Returns the tree, its drivers and defaults, and the node count.
def mme_build_geo_tree(label, runtime, draw, state, root, unit, varyings, loop_attrs, scene):
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
        top = bpy.data.node_groups.new("MME " + label, "GeometryNodeTree")
        top[MME_OWNER_PROP] = owner
        top.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        top.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        S = MmeGeoScene(top, root, unit, scene, state, controls, program)
        T = S.B
        group_in = T.new("NodeGroupInput")
        T.block_of[group_in.name] = None
        geometry = group_in.outputs[0]
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
            instance = S.node("GeometryNodeInstanceOnPoints")
            S.link(store.outputs[0], instance.inputs["Points"])
            S.link(geometry, instance.inputs["Instance"])
            realize = S.node("GeometryNodeRealizeInstances")
            S.link(instance.outputs[0], realize.inputs[0])
            geometry = realize.outputs[0]
            inst_attr = S.node("GeometryNodeInputNamedAttribute", "loop instance", data_type="INT")
            inst_attr.inputs["Name"].default_value = MME_ATTR_INST
            remaining = MmeSock(inst_attr.outputs["Attribute"])
            T.statement("LoopGetIndex values")
            for (var, _count, _name), value in reversed(list(zip(draw.loops, effective))):
                if var:
                    S.cache[("loopvar", var)] = T.imod(remaining, value)
                remaining = T.idiv(remaining, value)
        T.statement("Vertex shader: %s" % function.name)
        vs = S.node("GeometryNodeGroup", "Vertex shader: %s (%s)" % (function.name, draw.fx_pass.name or "pass"))
        vs.node_tree = inner
        vs.width = 260
        for key, identifier in inner_inputs.items():
            target = next((s for s in vs.inputs if s.identifier == identifier), None)
            if target is None:
                continue
            value = S.provide(needs.get(key, ("none",)))
            if isinstance(value, (MmeSock, bpy.types.NodeSocket)):
                S.link(value, target)
            else:
                T.feed(target, value)
        T.statement("Clip space to object space")
        clip_node = S.group_node(
            mme_geo_clip_group(),
            "Clip space to object space",
            {"Clip X": vs.outputs["Clip x"], "Clip Y": vs.outputs["Clip y"], "Clip Z": vs.outputs["Clip z"], "Clip W": vs.outputs["Clip w"], "Unit": unit},
        )
        culled = MmeSock(clip_node.outputs["Culled"])
        T.boolish.add(T.key(culled))
        T.statement("Set positions; culled vertices collapse to the origin")
        set_pos = S.node("GeometryNodeSetPosition")
        S.link(geometry, set_pos.inputs["Geometry"])
        S.link(clip_node.outputs["Position"], set_pos.inputs["Position"])
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
        for name in varyings:
            value = MmeSock(vs.outputs[name])
            store(name, T.select(T.cmp("==", value, value), value, 0.0))
        for name in loop_attrs:
            var = name[len(MME_LOOP_PREFIX):]
            store(name, S.cache.get(("loopvar", var), 0.0))
        T.statement("Output")
        geometry = mme_bake_node(S, geometry)
        group_out = T.new("NodeGroupOutput")
        T.block_of[group_out.name] = "out"
        S.link(geometry, group_out.inputs[0])
        mme_prune_tree(top, group_out)
        for node in top.nodes:
            if node.bl_idname == "NodeGroupInput":
                for socket in node.outputs:
                    socket.hide = not socket.is_linked
        mme_layout(top, T.block_of, T.blocks)
        return top, S.drivers, S.defaults, len(inner.nodes) + len(top.nodes)
    except Exception:
        if top is not None:
            bpy.data.node_groups.remove(top)
        bpy.data.node_groups.remove(inner)
        raise


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
