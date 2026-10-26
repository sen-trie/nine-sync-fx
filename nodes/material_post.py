from ..common.constants import (
    MME_ATTR_CULL,
    MME_GAMMA,
)
from .material_env import MME_MODE_LABELS


# Helper to add a Math node with constant or linked inputs.
def mme_math_node(nodes, op, label, a=None, b=None, clamp=False):
    node = nodes.new("ShaderNodeMath")
    node.operation = op
    node.label = label
    node.use_clamp = clamp
    for index, value in enumerate((a, b)):
        if value is None:
            continue
        if isinstance(value, (int, float)):
            node.inputs[index].default_value = float(value)
        else:
            node.id_data.links.new(value, node.inputs[index])
    return node


# Adds the fixed output stage after the pixel-shader group, depending on blend mode: culled-vertex alpha,
# MMD's 2.2 gamma, then emission mixed or added with transparency.
def mme_shader_post(tree, group_node, mode, x0):
    nodes, links = tree.nodes, tree.links
    color = group_node.outputs["Color"]
    alpha = group_node.outputs["Alpha"]
    frame = nodes.new("NodeFrame")
    frame.label = "MMD blending: %s" % MME_MODE_LABELS.get(mode, mode)
    frame.label_size = 14
    placed = []

    def put(node, x, y):
        node.location = (x0 + x, y)
        placed.append(node)
        return node

    cull = put(nodes.new("ShaderNodeAttribute"), 0, -260)
    cull.attribute_name = MME_ATTR_CULL
    cull.label = "Culled vertex (mme_cull)"
    visible = put(mme_math_node(nodes, "SUBTRACT", "Visible = 1 - culled", 1.0, cull.outputs["Fac"]), 220, -260)
    final_alpha = put(mme_math_node(nodes, "MULTIPLY", "Alpha", alpha, visible.outputs[0], clamp=True), 420, -120)
    if mode == "ADD_ALPHA":
        scaled = put(nodes.new("ShaderNodeMix"), 420, 80)
        scaled.data_type = "RGBA"
        scaled.blend_type = "MULTIPLY"
        scaled.label = "Color * Alpha"
        scaled.inputs["Factor"].default_value = 1.0
        links.new(color, scaled.inputs["A"])
        links.new(final_alpha.outputs[0], scaled.inputs["B"])
        color = scaled.outputs["Result"]
    elif mode == "ADD":
        scaled = put(nodes.new("ShaderNodeMix"), 420, 80)
        scaled.data_type = "RGBA"
        scaled.blend_type = "MULTIPLY"
        scaled.label = "Color * Visible"
        scaled.inputs["Factor"].default_value = 1.0
        links.new(color, scaled.inputs["A"])
        links.new(visible.outputs[0], scaled.inputs["B"])
        color = scaled.outputs["Result"]
    gamma = put(nodes.new("ShaderNodeGamma"), 640, 80)
    gamma.label = "MMD gamma"
    gamma.inputs["Gamma"].default_value = MME_GAMMA
    links.new(color, gamma.inputs["Color"])
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (x0 + 1300, 0)
    if mode == "MULTIPLY":
        transparent = put(nodes.new("ShaderNodeBsdfTransparent"), 860, 0)
        links.new(gamma.outputs[0], transparent.inputs["Color"])
        links.new(transparent.outputs[0], output.inputs["Surface"])
    else:
        emission = put(nodes.new("ShaderNodeEmission"), 860, 80)
        emission.inputs["Strength"].default_value = 1.0
        links.new(gamma.outputs[0], emission.inputs["Color"])
        if mode == "OPAQUE":
            links.new(emission.outputs[0], output.inputs["Surface"])
        else:
            transparent = put(nodes.new("ShaderNodeBsdfTransparent"), 860, -80)
            if mode in ("ADD", "ADD_ALPHA"):
                shader = put(nodes.new("ShaderNodeAddShader"), 1080, 0)
                links.new(transparent.outputs[0], shader.inputs[0])
                links.new(emission.outputs[0], shader.inputs[1])
            elif mode == "PREMULTIPLIED":
                mix = put(nodes.new("ShaderNodeMixShader"), 1080, -60)
                links.new(final_alpha.outputs[0], mix.inputs[0])
                black = put(nodes.new("ShaderNodeEmission"), 860, -200)
                black.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
                links.new(transparent.outputs[0], mix.inputs[1])
                links.new(black.outputs[0], mix.inputs[2])
                shader = put(nodes.new("ShaderNodeAddShader"), 1200, 0)
                links.new(mix.outputs[0], shader.inputs[0])
                links.new(emission.outputs[0], shader.inputs[1])
                output.location = (x0 + 1420, 0)
            else:
                shader = put(nodes.new("ShaderNodeMixShader"), 1080, 0)
                links.new(final_alpha.outputs[0], shader.inputs[0])
                links.new(transparent.outputs[0], shader.inputs[1])
                links.new(emission.outputs[0], shader.inputs[2])
            links.new(shader.outputs[0], output.inputs["Surface"])
    for node in placed:
        where = tuple(node.location)
        node.parent = frame
        node.location_absolute = where


# Material settings that go with the blend mode: blended or dithered transparency, back-face culling from the
# pass's cull state, viewport colour.
def mme_material_setup(mat, mode, states):
    blended = mode not in ("OPAQUE",)
    mat.surface_render_method = "BLENDED" if blended else "DITHERED"
    cull = str(states.get("cullmode", "")).replace(" ", "").upper()
    mat.use_backface_culling = cull in ("CCW", "3")
    mat.use_transparency_overlap = True
    mat.show_transparent_back = True
    mat.diffuse_color = (1.0, 0.55, 0.2, 0.3)
