"""Feed existing model inputs to the shared FX interpreter and node backends."""

import json
from pathlib import Path

import bpy

from ...fx.entry import fx_entry, fx_run_entry
from ...fx.errors import FxUnsupported
from ...fx.interp.executor import FxExec
from ...fx.semantics import fx_classify_global, fx_sampler_texture_global
from ...fx.textures import FxTextureStore
from ...nodes.images import mme_effect_image
from ...fx.types import FxValue, fx_count, fx_is_numeric, fx_resolve_type
from ...fx.params import fx_vs_globals
from ...fx.techniques import fx_technique_pass_type
from ...nodes.backend_geo import MmeGeoBackend
from ...nodes.backend_ps import MmePsBackend
from ...nodes.socket import MmeSock
from .analysis import DEFORM
from .scene_inputs import world_matrix, world_position, view_matrix, camera_projection
from .controllers import AXES, CONTROL_DATA, CONTROL_REF, CONTROL_ABSENT_MORPHS, AbsentMorph, DriverSource, DriverExpression, resolve, wire, wire_time


MATERIAL_PATHS = {
    "DIFFUSE": ("diffuse_color", "alpha"),
    "AMBIENT": ("diffuse_color",),
    "EMISSIVE": ("ambient_color",),
    "SPECULAR": ("specular_color",),
    "SPECULARPOWER": ("shininess",),
    "EDGECOLOR": ("edge_color",),
}


def is_mmd_material(material):
    mmd = getattr(material, "mmd_material", None)
    return bool(mmd and (mmd.material_id >= 0 or mmd.name_j or mmd.name_e))


def has_sphere_map(material):
    mmd = getattr(material, "mmd_material", None)
    if not mmd or int(mmd.sphere_texture_type) == 0:
        return False
    node = material.node_tree.nodes.get("mmd_sphere_tex") if material.node_tree else None
    # PMX can retain ADD/MULTIPLY mode with texture index -1. An absent map
    # must not select a textured technique. A declared but missing file remains
    # an error rather than being hidden by this presence check.
    return bool((node and getattr(node, "image", None)) or mmd.sphere_texture_rel_path)


def encode_srgb(B, value):
    nonlinear = B.sub(B.mul(1.055, B.pow(B.max(value, 0.0), 1.0 / 2.4)), 0.055)
    return B.select(B.cmp("<=", value, 0.0031308), B.mul(12.92, value), nonlinear)


def decode_srgb(B, value):
    nonlinear = B.pow(B.div(B.add(value, 0.055), 1.055), 2.4)
    return B.select(B.cmp("<=", value, 0.04045), B.div(value, 12.92), nonlinear)


def validate_base_image(image):
    if image is None:
        raise FxUnsupported("The destination material has no unambiguous base image.")
    if image.source not in {"FILE", "GENERATED"}:
        raise FxUnsupported(f"Unsupported base image source: {image.source}.")
    if image.source == "FILE" and not image.packed_file:
        path = bpy.path.abspath(image.filepath, library=image.library)
        if not Path(path).is_file():
            raise FxUnsupported(f"Base image file is missing: {image.filepath}.")


class ModelInputs:
    def __init__(self, analysis, backend, obj, scene, material, image, bindings, units, light, pass_context=None):
        self.program = analysis.program
        self.analysis = analysis
        self.backend = backend
        self.obj = obj
        self.scene = scene
        self.material = material
        self.mmd_shading = is_mmd_material(material)
        from .material_morphs import MaterialMorphs
        self.material_morphs = MaterialMorphs(self) if self.mmd_shading else None
        self.image = image
        self.bindings = bindings
        self.units = units
        self.light = light
        self.pass_context = pass_context
        self.stage = "vs"
        self.projections = set()
        self.unavailable = {}
        self.pending = {}
        self.controls = {}
        self.optional_controls = set()
        self.encoded_images = {}
        self.attributes = {}
        self.parameters = {}
        self.warnings = set()
        self.world_shading = False
        self.texture_writers = dict(analysis.texture_writers)
        self.target_order = {name: index for index, (name, _writer) in enumerate(analysis.texture_writers)}
        self.visiting_targets = set()
        self.vertex_fields = False
        self.vertex_pipeline = None
        self.vertex_parameters = {}
        self.recover_position = False
        self.projection_roots = set()

    def driven(self, label, source):
        node = self.backend.new("ShaderNodeValue")
        node.label = label
        self.pending[node.name] = source
        return MmeSock(node.outputs[0])

    def value(self, ex, t, parts):
        return ex.convert(FxValue(("f", (len(parts),)), parts), t)

    def global_value(self, ex, g):
        try:
            return self._global_value(ex, g)
        except FxUnsupported as error:
            if not self.recover_position:
                raise
            t = fx_resolve_type(g.type, self.program)
            if not fx_is_numeric(t) or g.array is not None:
                raise
            return self.unavailable_value(t, f"global {g.name}: {error}")

    def unavailable_value(self, t, description):
        values = []
        for component in range(fx_count(t[1])):
            key = f"Unavailable {description}.{component}"
            self.unavailable[key] = description
            values.append(self.backend.new_input(key, label=key))
        return FxValue(t, values)

    def _global_value(self, ex, g):
        if self.analysis.loops:
            names = {item[0] for item in self.analysis.loops}
            if g.name in names:
                if self.vertex_pipeline:
                    from .instances import loop_fields
                    value = loop_fields(self.vertex_pipeline.env)[0][g.name]
                    result = self.vertex_pipeline.value(ex, "loop_" + g.name, FxValue(("f", ()), value.c), ("f", ()))
                    return ex.convert(result, value.t)
                if self.backend.tree_tag != "GN":
                    raise FxUnsupported("Material instance loops require the vertex interpolation stage.")
                from .instances import loop_fields
                return loop_fields(self)[0][g.name]
        source = fx_classify_global(g, self.program)
        t = fx_resolve_type(g.type, self.program)
        semantic = g.semantic or ""
        if source is None:
            if semantic and t not in ("sampler", "texture"):
                raise FxUnsupported(f"Unsupported global {g.name}: {semantic}.")
            return None
        if not fx_is_numeric(t) or g.array is not None:
            raise FxUnsupported(f"Unsupported type for global {g.name}: {g.type}.")
        count = fx_count(t[1])
        kind = source[0]
        loop_count = g.name in {item[2] for item in self.analysis.loops}
        if kind == "param" and (t[0] == "f" or t[0] == "i" and loop_count) and len(t[1]) <= 1:
            if self.vertex_pipeline and g.name in self.vertex_pipeline.env.vertex_parameters:
                value = self.vertex_pipeline.env.vertex_parameters[g.name]
                if t[0] == "i":
                    floating = ("f", t[1])
                    result = self.vertex_pipeline.value(ex, "uniform_" + g.name, ex.convert(value, floating), floating)
                    return ex.convert(result, t)
                return self.vertex_pipeline.value(ex, "uniform_" + g.name,
                                                 value, t)
            meta = self.program.params[g.name]
            if len(g.name) > 50:
                raise FxUnsupported(f"Runtime parameter name is too long: {g.name}.")
            values = []
            for component in range(count):
                label = g.name if count == 1 else f"{g.name}.{component}"
                value = self.backend.new_input(label, label=label)
                self.parameters[label] = (meta["default"][component], meta["help"] or meta["label"])
                values.append(value)
            result = self.value(ex, t, values)
            self.vertex_parameters[g.name] = result
            return result
        if kind == "param":
            if t[0] in {"i", "b"}:
                return None  # The initial declaration is a compile-time variant in this profile.
            raise FxUnsupported(f"Unsupported runtime parameter type for {g.name}.")
        if kind == "control" and (t[0] == "f" and (len(t[1]) <= 1 or t[1] == (4, 4)) or t == ("b", ())):
            if t == ("b", ()):
                target, _sources = resolve(self.scene, self.obj, source[1], source[2], count,
                                            self.bindings, self.units, base="b",
                                            offscreen_owner=self.pass_context.owner if self.pass_context else None)
                if target is None:
                    self.optional_controls.add(source[1])
                    return FxValue(t, [0.0])
            # Delay scene resolution until the backend has pruned unused outputs.
            values = [self.driven(f"{g.name} [{i}]", None) for i in range(count)]
            self.controls[g.name] = (source[1], source[2], [v.socket.node.name for v in values], t)
            return FxValue(t, values) if len(t[1]) == 2 else self.value(ex, t, values)
        if kind == "viewport":
            percentage = self.driven("Resolution percentage", DriverSource(self.scene, "render.resolution_percentage", scale=0.01))
            parts = [ex.B.mul(percentage, self.driven(f"{g.name} {axis}",
                             DriverSource(self.scene, f"render.resolution_{axis}"))) for axis in "xy"]
            return self.value(ex, t, parts[:count])
        if kind == "time" and t == ("f", ()):
            return ex.scalar(self.driven(g.name, self.scene))
        if kind == "matrix" and self.stage == "vs" and source[1:] == ("WORLDVIEWPROJECTION", "", "camera"):
            if self.recover_position or self.vertex_fields:
                values = camera_projection(self)
                self.projection_roots.add(tuple(self.backend.key(value) for value in values))
                return FxValue(t, values)
            values = []
            for i in range(16):
                key = f"Projection {g.name}.{i}"
                self.projections.add(key)
                values.append(self.backend.new_input(key, label=key))
            return FxValue(t, values)
        if kind == "matrix" and source[3] == "camera" and source[1] in ("WORLD", "VIEW"):
            if source[2] not in ("", "TRANSPOSE"):
                raise FxUnsupported(f"Matrix inverse semantic {semantic} requires an inverse provider.")
            values = world_matrix(self, self.obj) if source[1] == "WORLD" else view_matrix(self)
            if source[2] == "TRANSPOSE":
                values = [values[col * 4 + row] for row in range(4) for col in range(4)]
            return self.value(ex, t, values) if len(t[1]) < 2 else FxValue(t, values)
        if kind == "position" and source[1] == "camera":
            parts = world_position(self, self.scene.camera)
            return self.value(ex, t, (parts + [1.0])[:count])
        if kind == "light_color" and self.light:
            if source[1] == "DIFFUSE":
                return self.value(ex, t, ([0.0, 0.0, 0.0] + [1.0])[:count])
            if self.light.type == "LIGHT":
                parts = [self.driven(f"{g.name} [{i}]", DriverSource(self.light.data, "color", index=i))
                         for i in range(3)]
            else:
                parts = [154.0 / 255.0] * 3
                self.warnings.add("The direction object has no light data. MMD's default light color is used.")
            return self.value(ex, t, (parts + [1.0])[:count])
        if kind == "material" and self.material is not None:
            return self.value(ex, t, self.material_values(source[1], count))
        if kind == "flag" and self.material is not None:
            if source[1] == "use_selfshadow" and fx_technique_pass_type(self.analysis.technique, self.program) == "object_ss":
                raise FxUnsupported("object_ss use_selfshadow needs native shadow-pass state, outside the input-only profile.")
            mmd = getattr(self.material, "mmd_material", None)
            sphere = int(mmd.sphere_texture_type) if mmd else 0
            sphere_present = has_sphere_map(self.material)
            flags = {"use_texture": self.image is not None, "use_spheremap": sphere_present,
                     "use_subtexture": sphere_present and sphere == 3, "spadd": sphere_present and sphere == 2,
                     "use_toon": self.mmd_shading, "parthf": bool(self.scene.camera and self.scene.camera.data.type != "ORTHO"),
                     "transp": False, "opadd": False, "use_selfshadow": False}
            if source[1] in flags:
                return self.value(ex, t, [float(flags[source[1]])])
        if kind == "direction" and source[1] == "light" and self.light:
            if self.backend.tree_tag != "SH" and not (self.vertex_fields or self.recover_position):
                raise FxUnsupported("DIRECTION from an explicit light source is available in the material profile.")
            # Match the generic MMD light rig convention: light direction points away from the light's location.
            B = self.backend
            raw = [self.driven(f"{g.name} [{axis}]", DriverSource(self.light, axis=axis, scale=-1.0))
                   for axis in "XYZ"]
            if self.vertex_fields or self.recover_position:
                vector = B.vector_of([raw[0], raw[2], raw[1]], force=True)
                if not self.world_shading:
                    inverse = B.new("FunctionNodeInvertMatrix")
                    B.links.new(B.matrix_of(world_matrix(self, self.obj)).socket, inverse.inputs[0])
                    transform = B.new("FunctionNodeTransformDirection")
                    B.links.new(inverse.outputs[0], transform.inputs["Transform"])
                    B.links.new(vector.socket, transform.inputs["Direction"])
                    vector = MmeSock(transform.outputs[0])
                normal = B.new("ShaderNodeVectorMath")
                normal.operation = "NORMALIZE"
                B.links.new(vector.socket, normal.inputs[0])
                return self.value(ex, t, B.split_vector(MmeSock(normal.outputs["Vector"]))[:count])
            if self.world_shading:
                node = B.new("ShaderNodeVectorMath")
                node.operation = "NORMALIZE"
                B.links.new(B.vector_of(raw, force=True).socket, node.inputs[0])
                x, y, z = B.split_vector(MmeSock(node.outputs["Vector"]))
                return self.value(ex, t, ([x, z, y] + [0.0])[:count])
            transform = B.new("ShaderNodeVectorTransform")
            transform.vector_type = "VECTOR"
            transform.convert_from, transform.convert_to = "WORLD", "OBJECT"
            B.links.new(B.vector_of(raw, force=True).socket, transform.inputs["Vector"])
            normal = B.new("ShaderNodeVectorMath")
            normal.operation = "NORMALIZE"
            B.links.new(transform.outputs["Vector"], normal.inputs[0])
            x, y, z = B.split_vector(MmeSock(normal.outputs["Vector"]))
            self.warnings.add("Light direction uses the explicit object's position and the model's local axes.")
            return self.value(ex, t, [x, z, y][:count])
        raise FxUnsupported(f"Unsupported global {g.name}: {semantic or source[1]}.")

    def material_values(self, semantic, count):
        if semantic in {"ADDINGTEXTURE", "MULTIPLYINGTEXTURE", "ADDINGSPHERETEXTURE", "MULTIPLYINGSPHERETEXTURE"}:
            if count != 4:
                raise FxUnsupported(f"Material semantic {semantic} requires float4.")
            values = [0.0 if semantic.startswith("ADDING") else 1.0]*4
            # MMD only supplies these accumulators in object_ss. Object passes
            # retain identity coefficients even with nonzero PMX sliders.
            if self.material_morphs and fx_technique_pass_type(self.analysis.technique, self.program) == "object_ss":
                values = self.material_morphs.apply(semantic, values)
            return values
        if semantic == "TOONCOLOR":
            morphs = (self.material_morphs if fx_technique_pass_type(self.analysis.technique, self.program) == "object_ss"
                      else None)
            node = self.material.node_tree.nodes.get("mmd_toon_tex") if self.material.node_tree else None
            if not node or not getattr(node, "image", None):
                mmd = getattr(self.material, "mmd_material", None)
                if self.mmd_shading and not mmd.is_shared_toon_texture and not mmd.toon_texture:
                    values = [1.0]*count
                    return morphs.apply(semantic, values) if morphs else values
                raise FxUnsupported("TOONCOLOR requires the original model's toon image.")
            validate_base_image(node.image)
            # Native MMD's byte-to-TOONCOLOR conversion divides RGB by 256,
            # whereas texture sampling divides by 255. Packed native probes
            # distinguish this from output-byte rounding. Its base alpha is 1.
            rgb = self.sample_image(node.image, 0.0, 1.0, "Closest", "EXTEND")[:3]
            values = ([self.backend.mul(value, 255.0/256.0) for value in rgb] + [1.0])[:count]
            return morphs.apply(semantic, values) if morphs else values
        material = self.material
        mmd = getattr(material, "mmd_material", None)
        is_mmd = self.mmd_shading
        if not is_mmd:
            if semantic != "DIFFUSE" or count not in (3, 4):
                raise FxUnsupported(f"Material semantic {semantic} requires matching MMD material properties.")
            paths = [DriverSource(material, "diffuse_color", index=i) for i in range(count)]
        else:
            names = MATERIAL_PATHS.get(semantic)
            if names is None:
                raise FxUnsupported(f"Unsupported PMX material semantic {semantic}.")
            paths = []
            for attr in names:
                value = getattr(mmd, attr)
                path = f"mmd_material.{attr}"
                if isinstance(value, (int, float)):
                    paths.append(DriverSource(material, path))
                else:
                    paths.extend(DriverSource(material, path, index=i) for i in range(len(value)))
            if count == 4 and semantic in ("AMBIENT", "EMISSIVE", "SPECULAR"):
                paths.append(1.0)
            if not count <= len(paths):
                raise FxUnsupported(f"Material semantic {semantic} has only {len(paths)} component(s).")
        values = [path if isinstance(path, float) else self.driven(f"{semantic} [{i}]", path)
                  for i, path in enumerate(paths[:count])]
        if self.material_morphs:
            values = self.material_morphs.apply(semantic, values)
        if semantic == "SPECULARPOWER":
            values = [self.backend.max(value, 0.1) for value in values]
        return values

    def sample(self, ex, info, u, v, lod):
        texture = fx_sampler_texture_global(self.program, info)
        if texture and self.pass_context and texture.name in self.pass_context.images:
            if texture.semantic not in ("OFFSCREENRENDERTARGET", "RENDERCOLORTARGET", "RENDERTARGET"):
                raise FxUnsupported("An external scene image must match a declared render target.")
            image = self.pass_context.images[texture.name]
            validate_base_image(image)
            allowed={"texture","addressu","addressv","minfilter","magfilter","mipfilter"}
            filters=[info.state(key,"LINEAR") for key in ("minfilter","magfilter")]
            if set(info.states)-allowed or len(set(filters))!=1 or filters[0] not in ("POINT","LINEAR"):
                raise FxUnsupported("Scene targets require matching point/linear filters and supported sampler states.")
            if info.state("addressu", "WRAP") != "CLAMP" or info.state("addressv", "WRAP") != "CLAMP":
                raise FxUnsupported("The scene-target sampling subset requires CLAMP on U/V.")
            if lod is not None or info.state("mipfilter", "NONE") not in ("NONE", "POINT"):
                raise FxUnsupported("Scene-target mip/LOD sampling is not implemented.")
            return self.backend.image_sample(image, "Closest" if info.point() else "Linear", "EXTEND", u, v)
        if texture and texture.name in self.texture_writers:
            return self.sample_target(ex, texture.name, u, v)
        material_sampler = texture and texture.semantic == "MATERIALTEXTURE"
        sampler_zero = info and not info.texture and (info.register or "").lower().replace(" ", "") in ("s0", "")
        image = self.image if material_sampler or sampler_zero else None
        if texture and texture.semantic in ("MATERIALSPHEREMAP", "MATERIALTOONTEXTURE"):
            name = "mmd_sphere_tex" if texture.semantic == "MATERIALSPHEREMAP" else "mmd_toon_tex"
            node = self.material.node_tree.nodes.get(name) if self.material and self.material.node_tree else None
            image = node.image if node and node.type == "TEX_IMAGE" else None
        if image is None and texture and not texture.semantic:
            path = FxTextureStore(self.program, None).resource_path(texture)
            image = mme_effect_image(path) if path else None
        if image is None:
            raise FxUnsupported(f"Unsupported or missing texture source {getattr(info, 'name', None)!r}.")
        validate_base_image(image)
        if lod is not None:
            self.warnings.add("Explicit FX texture LOD uses Blender's material texture filtering.")
        address_u, address_v = (info.state(key, "WRAP") for key in ("addressu", "addressv"))
        modes = {"WRAP": "REPEAT", "CLAMP": "EXTEND", "MIRROR": "MIRROR"}
        if address_u != address_v or address_u not in modes:
            raise FxUnsupported("The material profile requires matching wrap, clamp or mirror modes on U/V.")
        supported = {"texture", "addressu", "addressv", "minfilter", "magfilter", "mipfilter", "filter", "maxanisotropy"}
        if set(info.states) - supported:
            raise FxUnsupported(f"Unsupported sampler states: {', '.join(sorted(set(info.states) - supported))}.")
        filters = [info.state(key, "LINEAR") for key in ("minfilter", "magfilter")]
        if len(set(filters)) != 1 or filters[0] not in ("POINT", "LINEAR", "ANISOTROPIC"):
            raise FxUnsupported("Minification and magnification must both use point or linear filtering.")
        if "filter" in info.states and info.state("filter") not in ("LINEAR", "POINT", "MIN_MAG_MIP_LINEAR", "MIN_MAG_MIP_POINT", "ANISOTROPIC", "NONE"):
            raise FxUnsupported(f"Unsupported sampler filter {info.state('filter')}.")
        if "ANISOTROPIC" in filters or info.state("filter") == "ANISOTROPIC" or "maxanisotropy" in info.states:
            self.warnings.add("Anisotropic sampler settings use Blender's material texture filtering.")
        if "mipfilter" in info.states:
            self.warnings.add("Blender chooses its own mip filtering.")
        interpolation = "Closest" if info.point() or info.state("filter") in ("POINT", "MIN_MAG_MIP_POINT", "NONE") else "Linear"
        return self.sample_image(image, u, v, interpolation, modes[address_u])

    def sample_image(self, image, u, v, interpolation, extension):
        if self.mmd_shading and image.colorspace_settings.name == "sRGB":
            from .images import encoded_image
            if image not in self.encoded_images:
                self.encoded_images[image] = encoded_image(image)
            image = self.encoded_images[image]
        return self.backend.image_sample(image, interpolation, extension, u, v)

    def sample_target(self, ex, name, u, v):
        if name in self.visiting_targets:
            raise FxUnsupported(f"Render-target feedback is unavailable: {name}.")
        if any(self.target_order[name] >= self.target_order[parent] for parent in self.visiting_targets):
            raise FxUnsupported(f"Buffer pass needs a prior render target: {name}.")
        self.visiting_targets.add(name)
        try:
            draw = self.texture_writers[name]
            vs, args = fx_entry(self.program, draw.vs)

            def buffer_input(semantic, t):
                if semantic == "TEXCOORD0":
                    return self.value(ex, t, [u, v, 0.0, 1.0][:fx_count(t[1])])
                parts = []
                for component in range(fx_count(t[1])):
                    key = f"Unavailable buffer {name} {semantic}.{component}"
                    self.unavailable[key] = semantic
                    parts.append(ex.B.new_input(key, label=key))
                return FxValue(t, parts)

            varyings = fx_run_entry(FxExec(self.program, ex.B, self), vs, args, buffer_input) if vs else {}
            position = varyings.get("POSITION0")
            if vs:
                expected = buffer_input("POSITION0", ("f", (4,))).c
                if position is None or not fx_is_numeric(position.t) or len(position.c) != 4:
                    raise FxUnsupported(f"Buffer pass {draw.name} has no full-screen POSITION0 output.")
                if any(ex.B.key(a) != ex.B.key(b) for a, b in zip(position.c, expected)):
                    raise FxUnsupported(f"Buffer pass {draw.name} changes the full-screen raster coverage.")
            ps, args = fx_entry(self.program, draw.ps)
            if ps is None:
                raise FxUnsupported(f"Buffer pass {draw.name} has no pixel shader.")

            def pixel_input(semantic, t):
                return varyings[semantic] if semantic in varyings else buffer_input(semantic, t)

            pixel = FxExec(self.program, ex.B, self)
            output = fx_run_entry(pixel, ps, args, pixel_input)
            if set(output) != {"COLOR0"} or ex.B.known(pixel.discard) != 0.0:
                raise FxUnsupported(f"Buffer pass {draw.name} requires raster output or discard.")
            self.warnings.add("Intermediate texture passes evaluate as shader functions. Blender supplies mip filtering and texture precision.")
            return pixel.convert(output["COLOR0"], ("f", (4,))).c
        finally:
            self.visiting_targets.remove(name)

    def attribute(self, semantic):
        if semantic in self.attributes:
            return self.attributes[semantic]
        screen = bool(self.pass_context and self.pass_context.screen)
        if screen and semantic not in ("POSITION0", "TEXCOORD0"):
            raise FxUnsupported(f"The fullscreen subset has no verified input {semantic}.")
        B = self.backend
        geometry = B.tree_tag == "GN"
        mesh = self.obj.data
        if semantic in ("POSITION0", "NORMAL0"):
            normal = semantic == "NORMAL0"
            if geometry:
                node = B.new("GeometryNodeInputNormal" if normal else "GeometryNodeInputPosition")
                socket = node.outputs[0]
            else:
                node = B.new("ShaderNodeNewGeometry")
                transform = B.new("ShaderNodeVectorTransform")
                transform.vector_type = "NORMAL" if normal else "POINT"
                transform.convert_from, transform.convert_to = "WORLD", "OBJECT"
                B.links.new(node.outputs["Normal" if normal else "Position"], transform.inputs["Vector"])
                socket = transform.outputs[0]
            x, y, z = B.split_vector(MmeSock(socket))
            values = [x, z, y]
            if not normal:
                values = [B.div(value, self.units) for value in values] + [1.0]
            if screen:
                values = [x, y, 0.0, 1.0]
        elif semantic and semantic.startswith("TEXCOORD") and semantic[8:].isdigit():
            index = int(semantic[8:])
            if geometry and not self.vertex_fields:
                # GN positions are point-domain values and Blender's model UVs are face-corner data.
                raise FxUnsupported(f"Geometry profile cannot use face-corner UV input {semantic} for positions.")
            if index >= len(mesh.uv_layers):
                raise FxUnsupported(f"Destination mesh has no UV layer for {semantic}.")
            uv = mesh.uv_layers[index]
            if geometry:
                node = B.new("GeometryNodeInputNamedAttribute")
                node.data_type = "FLOAT_VECTOR"
                node.inputs["Name"].default_value = uv.name
                socket = node.outputs["Attribute"]
            else:
                node = B.new("ShaderNodeUVMap")
                node.uv_map = uv.name
                socket = node.outputs["UV"]
            u, v, _ = B.split_vector(MmeSock(socket))
            values = [u, B.sub(1.0, v)]
            if self.pass_context and self.pass_context.screen:
                # Native Draw=Buffer supplies UVs at integer D3D9 pixel
                # positions. Blender's quad interpolates at pixel centers.
                render = self.scene.render
                scale = render.resolution_percentage / 100
                values = [B.sub(value, .5 / (size * scale)) for value, size in
                          zip(values, (render.resolution_x, render.resolution_y))]
        elif semantic == "COLOR0" and (not geometry or self.vertex_fields):
            color = mesh.color_attributes.active_color
            if color is None:
                raise FxUnsupported("Destination mesh has no active color attribute for COLOR0.")
            if geometry:
                node = B.new("GeometryNodeInputNamedAttribute")
                node.data_type = "FLOAT_COLOR"
                node.inputs["Name"].default_value = color.name
                socket = node.outputs["Attribute"]
            else:
                node = B.new("ShaderNodeVertexColor")
                node.layer_name = color.name
                socket = node.outputs["Color"]
            split = B.new(B.separate_color)
            B.links.new(socket, split.inputs[0])
            alpha = split.outputs["Alpha"] if geometry else node.outputs["Alpha"]
            values = [MmeSock(socket) for socket in split.outputs[:3]] + [MmeSock(alpha)]
        else:
            raise FxUnsupported(f"Unsupported mesh input {semantic}.")
        self.attributes[semantic] = values
        return values

    def mesh_input(self, semantic, t):
        try:
            values = self.attribute(semantic)
        except FxUnsupported as error:
            if not self.recover_position:
                raise
            return self.unavailable_value(t, f"mesh {semantic}: {error}")
        return self.as_input(semantic, t, values)

    @staticmethod
    def as_input(semantic, t, values):
        if not fx_is_numeric(t) or len(t[1]) > 1 or fx_count(t[1]) > len(values):
            raise FxUnsupported(f"Unsupported input width for {semantic}: {t}.")
        return FxValue(("f", (len(values),)), values)

    def finish(self):
        if self.pass_context and self.pass_context.owner:
            self.backend.tree["ninesync_middle_offscreen_owner"] = self.pass_context.owner
        if self.material_morphs:
            self.material_morphs.finish()
        tree = self.backend.tree
        if self.projections & self.backend.inputs.keys():
            raise FxUnsupported("The selected output depends on clip-space projection.")
        for key, semantic in self.unavailable.items():
            if key in self.backend.inputs:
                raise FxUnsupported(f"Pixel varying {semantic} needs a supported pass-through vertex output.")
        for key, (default, help_text) in self.parameters.items():
            socket = self.backend.inputs.get(key)
            if socket is not None:
                item = self.backend.input_items[socket.socket.identifier]
                item.default_value = default
                item.description = help_text
        controls = []
        absent_morphs = []
        for global_name, (name, item, nodes, t) in self.controls.items():
            surviving = [n if tree.nodes.get(n) else None for n in nodes]
            if not any(surviving):
                continue
            target, sources = resolve(self.scene, self.obj, name, item, len(nodes), self.bindings, self.units,
                                      base=t[0], dims=t[1],
                                      offscreen_owner=self.pass_context.owner if self.pass_context else None)
            if any(isinstance(value, AbsentMorph) for value in sources):
                absent_morphs.append((global_name, item))
                self.warnings.add(f"Absent model morph {item!r} reads zero, matching native MMD.")
            for node_name, source in zip(surviving, sources):
                if node_name:
                    self.pending[node_name] = source
            driven = [node_name if isinstance(source, (DriverSource, DriverExpression)) else None
                      for node_name, source in zip(surviving, sources)]
            controls.append((global_name, name, item, len(nodes), driven, t[0], t[1]))
            tree[CONTROL_REF + global_name] = target
        tree[CONTROL_DATA] = json.dumps(controls)
        tree[CONTROL_ABSENT_MORPHS] = json.dumps(absent_morphs)
        tree["ninesync_middle_optional_controls"] = json.dumps(sorted(self.optional_controls))
        tree["ninesync_middle_units"] = self.units
        for name, source in self.pending.items():
            node = tree.nodes.get(name)
            if node is None:
                continue
            if source == self.scene:
                wire_time(node.outputs[0], self.scene)
            else:
                wire(node.outputs[0], source)


def _material(B, env, analysis, vertex_tree=None, prefix=None, vertex_slot=0):
    program = analysis.program
    referenced = fx_vs_globals(program, [analysis.shader_pass], stages=("vs", "ps"))
    env.world_shading = any(g.name in referenced and fx_classify_global(g, program) == ("matrix", "WORLD", "", "camera")
                            for g in program.globals.values())
    vs, args = fx_entry(program, analysis.shader_pass.vs)
    if vertex_tree is not None and vs:
        from .vertex import VertexFields
        env.vertex_pipeline = VertexFields(vertex_tree, env, analysis, ModelInputs, prefix, vertex_slot)
        varyings = {}
    else:
        vertex_ex = FxExec(program, B, env)
        varyings = fx_run_entry(vertex_ex, vs, args, env.mesh_input) if vs else {}
        if vs and env.pass_context and env.pass_context.screen:
            if B.known(vertex_ex.discard) != 0.0:
                raise FxUnsupported("The fullscreen subset cannot transfer vertex discard.")
            position = varyings.get("POSITION0")
            expected = env.attribute("POSITION0")
            if (not fx_is_numeric(getattr(position, "t", None)) or position.t[1] != (4,)
                    or any(B.key(a) != B.key(b) for a, b in zip(position.c, expected))):
                raise FxUnsupported("The screen subset requires an unchanged fullscreen POSITION0 output.")
    env.stage = "ps"
    if analysis.local_position and not env.vertex_pipeline:
        env.warnings.add("The material destination leaves vertex positions to the model and its modifiers.")
    ps, args = fx_entry(program, analysis.shader_pass.ps)
    ex = FxExec(program, B, env)

    def read(semantic, t):
        if env.vertex_pipeline:
            return env.vertex_pipeline.read(ex, semantic, t)
        if vs is None:
            return env.mesh_input(semantic, t)
        src = env.attributes.get(semantic)
        value = varyings.get(semantic)
        if (src is not None and value is not None and semantic != "POSITION0"
                and fx_is_numeric(value.t) and fx_is_numeric(t)
                and fx_count(t[1]) <= min(len(src), len(value.c))
                and all(B.key(a) == B.key(b) for a, b in zip(src, value.c))):
            return env.as_input(semantic, t, value.c)
        if value is not None and semantic != "POSITION0" and fx_is_numeric(value.t):
            env.warnings.add("Calculated vertex varyings are evaluated on the shaded surface. Vertex interpolation can differ.")
            return env.as_input(semantic, t, value.c)
        if not fx_is_numeric(t) or len(t[1]) > 1:
            raise FxUnsupported(f"Unsupported pixel input {semantic}: {t}.")
        components = []
        for i in range(fx_count(t[1])):
            key = f"Unsupported varying {semantic}.{i}"
            env.unavailable[key] = semantic
            components.append(B.new_input(key, label=key))
        return FxValue(t, components)

    outputs = fx_run_entry(ex, ps, args, read)
    color = outputs.get("COLOR0")
    if not fx_is_numeric(getattr(color, "t", None)) or color.t[1] != (4,) or set(outputs) != {"COLOR0"}:
        raise FxUnsupported("The material profile requires a single float4 COLOR0 pixel output.")
    r, g, b, alpha = color.c
    if env.mmd_shading or env.pass_context:
        r, g, b = [decode_srgb(B, B.min(B.max(value, 0.0), 1.0)) for value in (r, g, b)]
    alpha = B.min(B.max(alpha, 0.0), 1.0)
    alpha = B.mul(alpha, B.sub(1.0, ex.discard))
    combine = B.new("ShaderNodeCombineColor")
    for socket, value in zip(combine.inputs, (r, g, b)):
        B.feed(socket, value)
    emission = B.new("ShaderNodeEmission")
    B.links.new(combine.outputs[0], emission.inputs["Color"])
    mix = B.new("ShaderNodeMixShader")
    B.feed(mix.inputs[0], alpha)
    B.links.new(B.new("ShaderNodeBsdfTransparent").outputs[0], mix.inputs[1])
    B.links.new(emission.outputs[0], mix.inputs[2])
    B.finish([("Surface", MmeSock(mix.outputs[0]), "NodeSocketShader")])


def _geometry(B, env, analysis):
    from .position import PositionExec, is_passthrough

    function, args = fx_entry(analysis.program, analysis.shader_pass.vs)
    referenced = fx_vs_globals(analysis.program, [analysis.shader_pass], stages=("vs",))
    env.world_shading = any(g.name in referenced and fx_classify_global(g, analysis.program) == ("matrix", "WORLD", "", "camera")
                            for g in analysis.program.globals.values())
    env.recover_position = analysis.local_position is None
    ex = PositionExec(analysis.program, B, env) if env.recover_position else FxExec(analysis.program, B, env)
    outputs = fx_run_entry(ex, function, args, env.mesh_input)
    position = ex.local_position(outputs) if env.recover_position else outputs.get(analysis.local_position)
    if env.recover_position:
        if is_passthrough(env, position) and not analysis.loops:
            raise FxUnsupported("The selected vertex shader leaves mesh positions unchanged; use material conversion.")
        B.tree["ninesync_middle_recovered_position"] = True
    if not fx_is_numeric(getattr(position, "t", None)) or position.t[1] not in ((3,), (4,)):
        raise FxUnsupported(f"Local position output {analysis.local_position} must be float3 or float4.")
    if position.t[1] == (4,) and B.known(position.c[3]) != 1.0:
        raise FxUnsupported("A float4 local position requires a constant homogeneous W of 1.")
    if B.known(ex.discard) != 0.0:
        raise FxUnsupported("The geometry profile cannot transfer vertex discard.")
    if set(outputs) - {analysis.local_position, "POSITION0"}:
        env.warnings.add("Geometry conversion uses the position output only. Other VS varyings are omitted.")
    x, y, z = position.c[:3]
    node = B.new("GeometryNodeSetPosition")
    mesh = B.new_input("Geometry", "NodeSocketGeometry", "Geometry")
    if analysis.loops:
        from .instances import duplicate
        mesh = duplicate(env, mesh)
    B.links.new(mesh.socket, node.inputs["Geometry"])
    vector = B.vector_of([B.mul(value, env.units) for value in (x, z, y)], force=True)
    B.links.new(vector.socket, node.inputs["Position"])
    B.finish([("Geometry", MmeSock(node.outputs["Geometry"]), "NodeSocketGeometry")])


def compile_core(tree, target, analysis, obj, scene, material, image, bindings, units, light,
                 vertex_tree=None, attribute_prefix=None, vertex_slot=0, pass_context=None):
    B = MmeGeoBackend(tree) if target == DEFORM else MmePsBackend(tree)
    # A staged rebuild must be discardable, including helpers emitted for projected outputs that are pruned.
    B.use_shared_groups = False
    env = ModelInputs(analysis, B, obj, scene, material, image, bindings, units, light, pass_context)
    if target == DEFORM:
        _geometry(B, env, analysis)
    else:
        _material(B, env, analysis, vertex_tree, attribute_prefix, vertex_slot)
    env.finish()
    if env.vertex_pipeline:
        env.warnings.update(env.vertex_pipeline.finish())
    selected_role = fx_technique_pass_type(analysis.technique, analysis.program)
    if selected_role == "object_ss":
        env.warnings.add("Explicit object_ss inputs are converted without native self-shadow rendering.")
    extra_roles = {fx_technique_pass_type(technique, analysis.program)
                   for technique in analysis.program.techniques} - {selected_role}
    if extra_roles:
        env.warnings.add("Additional MME pass roles need renderer integration: " + ", ".join(sorted(extra_roles)) + ".")
    return tuple(sorted(env.warnings))
