import bpy

from ..common.constants import ACCESSORY_FADE_PROP
from ..common.log import log
from ..common.scene_utils import (
    node_input,
    node_socket,
)
from ..xfile.textures import resolve_x_texture


# Colour mix node whose factor is fixed at 1, so the blend type alone decides the result.
def add_color_mix(nodes, links, blend, a_socket, b_socket):
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = blend
    mix.inputs[0].default_value = 1.0
    links.new(a_socket, node_socket(mix, "A_Color"))
    links.new(b_socket, node_socket(mix, "B_Color"))
    return node_socket(mix, "Result_Color", outputs=True)


# MMD sphere map: view-space normal remapped to 0..1 as texture coordinates, then added (.spa) or multiplied
# (.sph) onto the colour.
def add_sphere_map(nodes, links, image, additive, color_socket):
    geometry = nodes.new("ShaderNodeNewGeometry")
    transform = nodes.new("ShaderNodeVectorTransform")
    transform.vector_type = "NORMAL"
    transform.convert_from = "WORLD"
    transform.convert_to = "CAMERA"
    scale = nodes.new("ShaderNodeVectorMath")
    scale.operation = "MULTIPLY_ADD"
    scale.inputs[1].default_value = (0.5, 0.5, 0.0)
    scale.inputs[2].default_value = (0.5, 0.5, 0.0)
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.extension = "EXTEND"
    links.new(geometry.outputs["Normal"], transform.inputs["Vector"])
    links.new(transform.outputs["Vector"], scale.inputs[0])
    links.new(scale.outputs["Vector"], texture.inputs["Vector"])
    return add_color_mix(
        nodes,
        links,
        "ADD" if additive else "MULTIPLY",
        color_socket,
        texture.outputs["Color"],
    )


# MMD's Tr fades the whole accessory: each mesh carries 1 - Tr as an object property (mme_fade, driven from the
# root), read through an Object attribute so materials need no driver and a mesh without it stays opaque.
def add_fade(nodes, links, alpha):
    fade = nodes.new("ShaderNodeAttribute")
    fade.attribute_type = "OBJECT"
    fade.attribute_name = ACCESSORY_FADE_PROP
    fade.label = "Accessory fade (1 - Tr)"
    keep = nodes.new("ShaderNodeMath")
    keep.operation = "SUBTRACT"
    keep.inputs[0].default_value = 1.0
    links.new(fade.outputs["Fac"], keep.inputs[1])
    scaled = nodes.new("ShaderNodeMath")
    scaled.operation = "MULTIPLY"
    scaled.use_clamp = True
    if alpha.is_linked:
        links.new(alpha.links[0].from_socket, scaled.inputs[0])
    else:
        scaled.inputs[0].default_value = alpha.default_value
    links.new(keep.outputs[0], scaled.inputs[1])
    links.new(scaled.outputs[0], alpha)
    return scaled.outputs[0]


# MMD's additive (加算) accessory: colour times alpha added onto what is behind, as emission plus a
# transparent shader (the same blend effect passes use for ADD_ALPHA).
def make_additive(mat, nodes, links, color, alpha):
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Strength"].default_value = 1.0
    scaled = add_color_mix(nodes, links, "MULTIPLY", color, alpha)
    links.new(scaled, emission.inputs["Color"])
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    shader = nodes.new("ShaderNodeAddShader")
    links.new(transparent.outputs[0], shader.inputs[0])
    links.new(emission.outputs[0], shader.inputs[1])
    output = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None) or nodes.new("ShaderNodeOutputMaterial")
    links.new(shader.outputs[0], output.inputs["Surface"])
    mat.surface_render_method = "BLENDED"


# Principled material from a .x material: diffuse becomes base colour and alpha; specular level comes from the
# material's specular, roughness is fixed and emission is weak.
# Texture and sphere entries are split on '*' (MMD stores several), sphere maps only when enabled, and any
# alpha or texture switches the material to blended.
def build_x_material(name, xmat, folder, settings, additive=False):
    mat = bpy.data.materials.new(name)
    tree = mat.node_tree
    nodes, links = tree.nodes, tree.links
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        output = next(
            (n for n in nodes if n.type == "OUTPUT_MATERIAL"), None
        ) or nodes.new("ShaderNodeOutputMaterial")
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    r, g, b, a = xmat.diffuse
    base = node_input(bsdf, "Base Color")
    base.default_value = (r, g, b, 1.0)
    alpha = node_input(bsdf, "Alpha")
    alpha.default_value = a
    rough = node_input(bsdf, "Roughness")
    if rough is not None:
        rough.default_value = 0.6
    spec = node_input(bsdf, "Specular IOR Level", "Specular")
    if spec is not None:
        spec.default_value = min(1.0, max(xmat.specular) * 0.5)
    emission = node_input(bsdf, "Emission Color", "Emission")
    if emission is not None:
        emission.default_value = (
            xmat.emissive[0],
            xmat.emissive[1],
            xmat.emissive[2],
            1.0,
        )
    strength = node_input(bsdf, "Emission Strength")
    if strength is not None:
        strength.default_value = 0.25
    parts = [
        p.strip() for p in xmat.texture.replace("\\\\", "\\").split("*") if p.strip()
    ]
    spheres = [p for p in parts if p.lower().endswith((".sph", ".spa"))]
    plain = [p for p in parts if not p.lower().endswith((".sph", ".spa"))]
    color_socket = None
    translucent = a < 0.999
    if plain:
        found = resolve_x_texture(plain[0], folder)
        if found is None:
            log.warning("texture not found for %s: %s", name, plain[0])
        else:
            texture = nodes.new("ShaderNodeTexImage")
            texture.image = bpy.data.images.load(found, check_existing=True)
            color_socket = texture.outputs["Color"]
            links.new(texture.outputs["Alpha"], alpha)
            translucent = True
            if min(r, g, b) < 0.99:
                tint = nodes.new("ShaderNodeRGB")
                tint.outputs[0].default_value = (r, g, b, 1.0)
                color_socket = add_color_mix(
                    nodes, links, "MULTIPLY", color_socket, tint.outputs[0]
                )
    if spheres and settings.sphere_maps:
        if color_socket is None:
            tint = nodes.new("ShaderNodeRGB")
            tint.outputs[0].default_value = (r, g, b, 1.0)
            color_socket = tint.outputs[0]
        for sphere in spheres:
            found = resolve_x_texture(sphere, folder)
            if found is None:
                log.warning("sphere map not found for %s: %s", name, sphere)
                continue
            image = bpy.data.images.load(found, check_existing=True)
            color_socket = add_sphere_map(
                nodes, links, image, sphere.lower().endswith(".spa"), color_socket
            )
            log.info("sphere map (experimental) %s on %s", sphere, name)
    elif spheres:
        log.info("sphere maps disabled; skipping %s on %s", spheres, name)
    if color_socket is not None:
        links.new(color_socket, base)
    faded = add_fade(nodes, links, alpha)
    if translucent:
        mat.surface_render_method = "BLENDED"
    if additive:
        if color_socket is None:
            tint = nodes.new("ShaderNodeRGB")
            tint.outputs[0].default_value = (r, g, b, 1.0)
            color_socket = tint.outputs[0]
        make_additive(mat, nodes, links, color_socket, faded)
    return mat
