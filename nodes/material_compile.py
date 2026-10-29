import bpy
import hashlib

from ..common.constants import (
    MME_GROUP_LABEL,
    MME_GROUP_PROP,
    MME_LOOP_PREFIX,
    MME_OWNER_PROP,
    MME_SIG_PROP,
    MME_VARYING_PREFIX,
)
from ..common.log import log
from ..fx.entry import (
    fx_entry,
    fx_run_entry,
)
from ..fx.errors import FxUnsupported
from ..fx.interp.executor import FxExec
from ..xfile.textures import resolve_x_texture
from .backend_ps import MmePsBackend
from .images import mme_effect_image
from .material_env import (
    MmeNodeEnv,
    mme_blend_mode,
    mme_ps_inputs,
)
from .material_post import (
    mme_material_setup,
    mme_shader_post,
)
from .names import (
    MME_INPUT_NODE_PREFIX,
    mme_varying_name,
)


# Passes with the same vertex shader share vertex-shader output attributes; this text identifies the shader.
def mme_vs_signature(fx_pass):
    return repr(fx_pass.vs)


# Hash of node types, names, labels, unlinked defaults (rounded) and links. Stored after a build; if it no
# longer matches, the user has edited the material.
def mme_tree_signature(tree):
    parts = []
    for node in sorted(tree.nodes, key=lambda n: n.name):
        parts.append((node.bl_idname, node.name, node.label))
        for socket in node.inputs:
            if socket.is_linked or not hasattr(socket, "default_value"):
                continue
            value = socket.default_value
            try:
                value = tuple(round(float(v), 5) for v in value)
            except TypeError:
                try:
                    value = round(float(value), 5)
                except (TypeError, ValueError):
                    value = str(value)
            parts.append((socket.identifier, value))
    for link in tree.links:
        parts.append((link.from_node.name, link.from_socket.identifier, link.to_node.name, link.to_socket.identifier))
    return hashlib.md5(repr(sorted(parts, key=repr)).encode("utf-8")).hexdigest()


# Finds the datablock that belongs to an owner key, so rebuilds reuse a material instead of creating copies.
def mme_owned(collection, owner):
    for item in collection:
        if item.get(MME_OWNER_PROP) == owner:
            return item
    return None


# Clears a group for regeneration but keeps its outputs, so users of the group keep their links.
def mme_reset_group(group, keep_outputs=True):
    for node in list(group.nodes):
        group.nodes.remove(node)
    for item in list(group.interface.items_tree):
        if item.item_type != "SOCKET":
            continue
        if keep_outputs and item.in_out == "OUTPUT":
            continue
        group.interface.remove(item)


# Drops outputs the new build no longer produces.
def mme_group_outputs(group, wanted):
    for item in list(group.interface.items_tree):
        if item.item_type == "SOCKET" and item.in_out == "OUTPUT":
            if (item.name, item.socket_type) not in wanted:
                group.interface.remove(item)


# Display name for a group input in the material's attribute nodes.
def mme_friendly_input(key, item_name):
    if key.startswith(MME_VARYING_PREFIX):
        return "Vertex output %s" % item_name
    if key.startswith(MME_LOOP_PREFIX):
        return "Loop index %s" % key[len(MME_LOOP_PREFIX):]
    return item_name


# Adds an Attribute node for every group input and links it, so the material reads vertex-shader outputs and
# uniforms without manual wiring.
def mme_wire_inputs(tree, group_node, B):
    nodes, links = tree.nodes, tree.links
    for node in [n for n in nodes if n.name.startswith(MME_INPUT_NODE_PREFIX)]:
        nodes.remove(node)
    x, y = group_node.location_absolute
    row = 0
    for key, sock in B.inputs.items():
        identifier = sock.socket.identifier
        target = next((s for s in group_node.inputs if s.identifier == identifier), None)
        if target is None:
            continue
        attr = nodes.new("ShaderNodeAttribute")
        attr.name = MME_INPUT_NODE_PREFIX + key
        attr.attribute_type = B.kinds.get(key, "GEOMETRY")
        attr.attribute_name = key
        attr.label = mme_friendly_input(key, target.name)
        attr.hide = True
        attr.location = (x - 320, y - row * 40)
        links.new(attr.outputs["Fac"], target)
        row += 1


# Compiles a pass's pixel shader into a material: the shader code goes into a group, the fixed output stage
# around it is built once.
# If the user edited the material (the signature changed), only the group is regenerated. Render-target reads
# re-run the pass that wrote them.
# Returns the material plus the attributes, uniforms and loop attributes it needs.
def mme_compile_material(name, runtime, draw, state, vs_outputs, owner=None):
    program = runtime.program
    mat = mme_owned(bpy.data.materials, owner) if owner else None
    edited = mat is not None and mat.get(MME_SIG_PROP) != mme_tree_signature(mat.node_tree)
    if mat is None:
        mat = bpy.data.materials.new(name)
        if owner:
            mat[MME_OWNER_PROP] = owner
    group = bpy.data.node_groups.get(mat.get(MME_GROUP_PROP, ""))
    if group is None or group.bl_idname != "ShaderNodeTree":
        group = bpy.data.node_groups.new("MME PS " + name, "ShaderNodeTree")
        mat[MME_GROUP_PROP] = group.name
    group[MME_OWNER_PROP] = owner or name
    mme_reset_group(group)
    mme_group_outputs(group, {("Color", "NodeSocketColor"), ("Alpha", "NodeSocketFloat")})
    B = MmePsBackend(group)
    env = MmeNodeEnv(program, state, runtime.textures, runtime.controls_runtime)
    provider = mme_ps_inputs(vs_outputs)
    loops = runtime.loop_overrides_nodes(draw, B)
    visiting = set()

    def run_ps(fx_pass):
        function, args = fx_entry(program, fx_pass.ps)
        if function is None:
            raise FxUnsupported("pass %s has no pixel shader" % fx_pass.name)
        ex = FxExec(program, B, env)
        ex.overrides.update(loops)
        outputs = fx_run_entry(ex, function, args, provider(ex))
        color = outputs.get("COLOR0")
        if color is None:
            return [0.0, 0.0, 0.0, 1.0], ex.discard
        comps = ex.convert(color, ("f", (4,))).c
        return comps, ex.discard

    def rt_lookup(ex, texture_name):
        writers = [
            d
            for d in runtime.draws
            if d.target == texture_name
            and d.kind == "geometry"
            and d.subset == draw.subset
            and d.order < draw.order
            and mme_vs_signature(d.fx_pass) == mme_vs_signature(draw.fx_pass)
            and [(l[0], l[1]) for l in d.loops] == [(l[0], l[1]) for l in draw.loops]
        ]
        if not writers or texture_name in visiting:
            return [0.0, 0.0, 0.0, 0.0]
        visiting.add(texture_name)
        try:
            comps, _discard = run_ps(writers[-1].fx_pass)
        finally:
            visiting.discard(texture_name)
        return list(comps)

    env.rt_lookup = rt_lookup
    try:
        color, discard = run_ps(draw.fx_pass)
        r, g, b, a = (list(color) + [0.0, 0.0, 0.0, 1.0])[:4]
        alpha = B.min(B.max(a, 0.0), 1.0)
        if B.known(discard) is None or B.known(discard) != 0.0:
            alpha = B.mul(alpha, B.sub(1.0, discard))
        B.statement("output color and alpha")
        B.finish([("Color", [r, g, b], "NodeSocketColor"), ("Alpha", alpha, "NodeSocketFloat")])
    except Exception:
        if not edited and mat.users == 0:
            bpy.data.materials.remove(mat)
            bpy.data.node_groups.remove(group)
        raise
    mode = mme_blend_mode(draw.fx_pass.states)
    tree = mat.node_tree
    group_node = next((n for n in tree.nodes if n.bl_idname == "ShaderNodeGroup" and n.node_tree == group), None)
    if not edited or group_node is None:
        for node in list(tree.nodes):
            tree.nodes.remove(node)
        group_node = tree.nodes.new("ShaderNodeGroup")
        group_node.node_tree = group
        group_node.name = "MME Pixel Shader"
        group_node.label = MME_GROUP_LABEL
        group_node.width = 240
        group_node.location = (0, 0)
        mme_shader_post(tree, group_node, mode, 340)
        mme_material_setup(mat, mode, draw.fx_pass.states)
    else:
        log.info("material %s was edited; only its pixel shader group was regenerated", mat.name)
    mme_wire_inputs(tree, group_node, B)
    mat[MME_SIG_PROP] = mme_tree_signature(tree)
    varyings = sorted(k for k in B.inputs if k.startswith(MME_VARYING_PREFIX) and B.kinds.get(k) == "GEOMETRY")
    uniforms = [[k, env.uniforms[k][0], env.uniforms[k][1]] for k in B.inputs if k in env.uniforms]
    loop_attrs = sorted(k for k in B.inputs if k.startswith(MME_LOOP_PREFIX))
    return mat, varyings, uniforms, mode, loop_attrs, len(group.nodes)


# Plain material (diffuse colour times texture) used when the pixel shader cannot be converted; it carries a
# warning node with the reason.
def mme_fallback_material(name, runtime, subset, mode="ALPHA", reason="", owner=None):
    xmat = runtime.materials[subset] if subset < len(runtime.materials) else None
    mat = mme_owned(bpy.data.materials, owner) if owner else None
    if mat is None:
        mat = bpy.data.materials.new(name)
        if owner:
            mat[MME_OWNER_PROP] = owner
    group = bpy.data.node_groups.get(mat.get(MME_GROUP_PROP, ""))
    if group is None or group.bl_idname != "ShaderNodeTree":
        group = bpy.data.node_groups.new("MME PS " + name, "ShaderNodeTree")
        mat[MME_GROUP_PROP] = group.name
    group[MME_OWNER_PROP] = owner or name
    mme_reset_group(group)
    mme_group_outputs(group, {("Color", "NodeSocketColor"), ("Alpha", "NodeSocketFloat")})
    B = MmePsBackend(group)
    B.statement("fallback: material diffuse x texture")
    color = [1.0, 1.0, 1.0, 1.0]
    if xmat is not None:
        color = list(xmat.diffuse)
        texture = xmat.texture.split("*")[0].strip() if xmat.texture else ""
        found = resolve_x_texture(texture, runtime.x_folder) if texture else None
        if found:
            sampled = B.image_sample(mme_effect_image(found), "Linear", "REPEAT", B.attribute(mme_varying_name("TEXCOORD0", 0)), B.attribute(mme_varying_name("TEXCOORD0", 1)))
            color = [B.mul(c, s) for c, s in zip(color, sampled)]
    B.finish([("Color", color[:3], "NodeSocketColor"), ("Alpha", color[3], "NodeSocketFloat")])
    if reason:
        warning = group.nodes.new("GeometryNodeWarning")
        warning.warning_type = "WARNING"
        warning.inputs["Message"].default_value = ("Pixel shader not converted: %s" % reason)[:200]
        warning.location = (0, 200)
    tree = mat.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    group_node = tree.nodes.new("ShaderNodeGroup")
    group_node.node_tree = group
    group_node.name = "MME Pixel Shader"
    group_node.label = MME_GROUP_LABEL
    group_node.width = 240
    mme_shader_post(tree, group_node, mode, 340)
    mme_wire_inputs(tree, group_node, B)
    mme_material_setup(mat, mode, {})
    mat[MME_SIG_PROP] = mme_tree_signature(tree)
    return mat
