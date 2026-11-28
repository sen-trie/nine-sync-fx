"""Apply, update and remove experimental FX on an explicit Blender mesh."""

from dataclasses import dataclass
import json
from math import isfinite
from uuid import uuid4

import bpy

from ...common.digest import digest
from ...fx.errors import FxUnsupported
from ...nodes.material_compile import mme_tree_signature
from .analysis import DEFORM, MATERIAL, RENDER, analyze
from .compiler import compile_core, validate_base_image, is_mmd_material, has_sphere_map
from .specialists import AUTO, apply_specialist, plan_specialist
from .controllers import binding_diagnostics, retarget as retarget_core, saved_bindings
from .images import discard_unused, views_current
from .material_morphs import signature as morph_signature, diagnostics as morph_diagnostics
from .vertex import VERTEX_CORE, VERTEX_NAMESPACE, stage as vertex_stage, locate as vertex_node, install as install_vertex, uninstall as uninstall_vertex


OWNER = "ninesync_middle_owner"
TOKEN = "ninesync_middle_token"
ORIGINAL = "ninesync_middle_original"
CORE_NODE = "ninesync_middle_core_node"
STAGE_NODE = "ninesync_middle_edit_node"
SOURCE = "ninesync_middle_source"
SIGNATURE = "ninesync_middle_signature"
PROFILE = 24


@dataclass(frozen=True)
class Result:
    destination: object
    core: object
    status: str
    warnings: tuple
    rebuilt: bool


def _require_model(obj, scene):
    if obj is None or obj.type != "MESH" or obj.library or obj.override_library or obj.mode != "OBJECT":
        raise FxUnsupported("Select a local editable mesh in Object mode.")
    if scene.objects.get(obj.name) != obj:
        raise FxUnsupported("The destination mesh is outside the chosen scene.")


def _slot(obj, index):
    if not isinstance(index, int) or not 0 <= index < len(obj.material_slots):
        raise FxUnsupported("Choose one existing material slot by index.")
    return obj.material_slots[index]


def _deformer(obj):
    found = [mod for mod in obj.modifiers if mod.type == "NODES" and mod.node_group
             and mod.node_group.get(TOKEN)]
    if any(mod.node_group.get(OWNER) != obj for mod in found):
        raise FxUnsupported("The experimental modifier belongs to a different object.")
    if len(found) > 1:
        raise FxUnsupported("Multiple copies of the owned modifier exist. Keep one before updating it.")
    return found[0] if found else None


def _core_node(tree, token):
    found = [node for node in tree.nodes if node.get(CORE_NODE) == token]
    if len(found) != 1 or not found[0].node_tree or found[0].node_tree.get(TOKEN) != token:
        raise FxUnsupported("The generated core connection is missing or has been duplicated.")
    return found[0]


def _socket_fields(tree):
    return tuple((item.in_out, item.name, item.socket_type) for item in tree.interface.items_tree
                 if item.item_type == "SOCKET")


def _swap_core(wrapper, node, new):
    old = node.node_tree
    if _socket_fields(old) != _socket_fields(new):
        raise FxUnsupported("The FX input/output interface changed. The existing effect was kept.")
    defaults = {s.name: s.default_value for s in node.inputs if s.type == "VALUE"}
    incoming = [(link.from_socket, link.to_socket.name) for link in wrapper.links if link.to_node == node]
    outgoing = [(link.from_socket.name, link.to_socket) for link in wrapper.links if link.from_node == node]

    def install(group):
        node.node_tree = group
        for name, value in defaults.items():
            node.inputs[name].default_value = value
        for source, name in incoming:
            if not any(link.from_socket == source for link in node.inputs[name].links):
                wrapper.links.new(source, node.inputs[name])
        for name, dest in outgoing:
            if not any(link.to_socket == dest for link in node.outputs[name].links):
                wrapper.links.new(node.outputs[name], dest)

    try:
        install(new)
    except (RuntimeError, KeyError, TypeError):
        install(old)
        raise
    _free(bpy.data.node_groups, old)


def _free(collection, block):
    if block and not block.users:
        collection.remove(block)
        discard_unused()


def _source_image(material, explicit):
    if explicit is not None:
        return explicit
    if not material.node_tree:
        return None
    nodes = material.node_tree.nodes
    base = nodes.get("mmd_base_tex")
    if base and base.type == "TEX_IMAGE":
        return base.image
    images = {node.image for node in nodes if node.type == "TEX_IMAGE" and node.image}
    return next(iter(images)) if len(images) == 1 else None


def _validate_material(obj, slot, index):
    material = slot.material
    if material is None:
        raise FxUnsupported("The selected slot has no source material.")
    if not material.get(TOKEN):
        return material, None
    if material.get(OWNER) != obj:
        raise FxUnsupported("The selected override belongs to a different object.")
    if material.get("ninesync_middle_slot") != index:
        raise FxUnsupported("The overridden slot moved. Remove its override before applying the new subset.")
    original = material.get(ORIGINAL)
    if original is None:
        raise FxUnsupported("The override's original material is unavailable.")
    return original, material


def _current(obj, target, slot):
    if target == MATERIAL:
        material = _slot(obj, slot).material
        if not material or not material.get(TOKEN) or material.get(OWNER) != obj:
            raise FxUnsupported("The selected slot has no owned experimental material.")
        return material, material.node_tree, material[TOKEN]
    if target == DEFORM:
        mod = _deformer(obj)
        if mod is None:
            raise FxUnsupported("The object has no owned experimental deformer.")
        return mod, mod.node_group, mod.node_group[TOKEN]
    raise FxUnsupported(f"Unknown target {target!r}.")


def _new_material(obj, slot, index, original, core, token):
    result = bpy.data.materials.new(f"{original.name} | MME FX")
    link = slot.link
    try:
        result.diffuse_color = original.diffuse_color
        result.surface_render_method = "DITHERED"
        result[OWNER], result[TOKEN], result[ORIGINAL] = obj, token, original
        result["ninesync_middle_slot"] = index
        result["ninesync_middle_original_link"] = link
        tree = result.node_tree
        tree.nodes.clear()
        node = tree.nodes.new("ShaderNodeGroup")
        node.label = "Generated FX shader"
        node[CORE_NODE] = token
        node.node_tree = core
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        output.location.x = 290
        tree.links.new(node.outputs["Surface"], output.inputs["Surface"])
        slot.link = "OBJECT"
        slot.material = result
    except Exception:
        if slot.link == "OBJECT" and link == "DATA":
            slot.material = None
            slot.link = "DATA"
        if slot.material != original and link == "OBJECT":
            slot.material = original
        _free(bpy.data.materials, result)
        raise
    return result


def _socket(tree, name, direction, socket_type):
    return tree.interface.new_socket(name, in_out=direction, socket_type=socket_type)


def _new_deformer(obj, core, token, modifier_index):
    wrapper = bpy.data.node_groups.new("MME FX Deformer", "GeometryNodeTree")
    stage, mod = None, None
    try:
        wrapper[OWNER], wrapper[TOKEN] = obj, token
        for io, name, kind in sorted(_socket_fields(core), key=lambda port: port[1] != "Geometry"):
            item = _socket(wrapper, name, io, kind)
            source = next(s for s in core.interface.items_tree if s.in_out == io and s.name == name)
            if kind == "NodeSocketFloat":
                item.default_value = source.default_value
                item.description = source.description
                item.force_non_field = True
        stage = bpy.data.node_groups.new("Edit Deformation", "GeometryNodeTree")
        stage[TOKEN] = token
        _socket(stage, "Geometry", "INPUT", "NodeSocketGeometry")
        _socket(stage, "Geometry", "OUTPUT", "NodeSocketGeometry")
        stage_in = stage.nodes.new("NodeGroupInput")
        stage_out = stage.nodes.new("NodeGroupOutput")
        stage_out.location.x = 240
        stage.links.new(stage_in.outputs["Geometry"], stage_out.inputs["Geometry"])
        stage["ninesync_middle_initial_stage"] = mme_tree_signature(stage)

        group_in = wrapper.nodes.new("NodeGroupInput")
        group_in.location.x = -300
        node = wrapper.nodes.new("GeometryNodeGroup")
        node[CORE_NODE] = token
        node.label = "Generated FX deformation"
        node.node_tree = core
        edit = wrapper.nodes.new("GeometryNodeGroup")
        edit[STAGE_NODE] = token
        edit.node_tree = stage
        edit.location.x = 230
        group_out = wrapper.nodes.new("NodeGroupOutput")
        group_out.location.x = 470
        for port in core.interface.items_tree:
            if port.in_out == "INPUT":
                wrapper.links.new(group_in.outputs[port.name], node.inputs[port.name])
        wrapper.links.new(node.outputs["Geometry"], edit.inputs["Geometry"])
        wrapper.links.new(edit.outputs["Geometry"], group_out.inputs["Geometry"])
        mod = obj.modifiers.new("MME FX Deformation", "NODES")
        mod.node_group = wrapper
        if modifier_index is not None:
            obj.modifiers.move(len(obj.modifiers) - 1, modifier_index)
    except Exception:
        if mod is not None:
            obj.modifiers.remove(mod)
        _free(bpy.data.node_groups, wrapper)
        _free(bpy.data.node_groups, stage)
        raise
    return mod


def apply(obj, path, target, *, scene=None, slot=0, bindings=None, units=1.0, image=None, light=None,
          modifier_index=None, force_rebuild=False, interpolate_vertices=None, pass_type=None, pass_context=None,
          scene_members=None, offscreen_owner=None, package_root=None, reset_targets=None,
          ground_shadows=None, ground_shadow_brightness=None, self_shadow_mode=None, self_shadow_distance=None):
    """Apply one target. Material slots and optional modifier positions are zero-based."""
    scene = scene if scene is not None else bpy.context.scene
    if target == AUTO:
        adapter_plan = plan_specialist(path, package_root)
        if adapter_plan is not None:
            if pass_context is not None or scene_members is not None or offscreen_owner is not None or bindings:
                raise FxUnsupported("The compositor specialist does not accept mesh inputs or offscreen owners.")
            effect, changed = apply_specialist(scene, adapter_plan, force_rebuild)
            return Result(effect, effect.node_group, "SPECIALIST", (adapter_plan.omissions,), changed)
        target = MATERIAL
    _require_model(obj, scene)
    if pass_context:
        pass_context.validate(scene)
        if pass_context.screen:
            if target != MATERIAL:
                raise FxUnsupported("A screen pass requires a material destination on the renderer quad.")
            interpolate_vertices = False
    if not isfinite(units) or units <= 0.0:
        raise FxUnsupported("Blender units per MMD unit must be positive and finite.")
    if light is not None and (light == obj or scene.objects.get(light.name) != light):
        raise FxUnsupported("The light direction source must be another object in the destination scene.")
    if target == RENDER:
        if pass_context is not None:
            raise FxUnsupported('Supply scene members for ordered rendering instead of a node pass context.')
        from .raster_targets import apply_render
        return apply_render(obj,path,scene=scene,units=units,light=light,members=scene_members,
                            owner=offscreen_owner,bindings=bindings,force_rebuild=force_rebuild,
                            reset_targets=reset_targets,ground_shadows=ground_shadows,
                            ground_shadow_brightness=ground_shadow_brightness,
                            self_shadow_mode=self_shadow_mode,self_shadow_distance=self_shadow_distance)
    original = selected_slot = existing = wrapper = None
    if target == MATERIAL:
        selected_slot = _slot(obj, slot)
        original, existing = _validate_material(obj, selected_slot, slot)
        wrapper = existing.node_tree if existing else None
        image = _source_image(original, image)
    elif target == DEFORM:
        used_slots = {polygon.material_index for polygon in obj.data.polygons}
        sources = {obj.material_slots[index].material for index in used_slots
                   if index < len(obj.material_slots) and obj.material_slots[index].material}
        sources = {material.get(ORIGINAL, material) for material in sources}
        if len(sources) == 1:
            original = next(iter(sources))
            image = _source_image(original, image)
        existing = _deformer(obj)
        wrapper = existing.node_group if existing else None
        if existing and modifier_index is not None and obj.modifiers.find(existing.name) != modifier_index:
            raise FxUnsupported("Move an existing FX modifier explicitly in the modifier stack.")
        if modifier_index is not None and not 0 <= modifier_index <= len(obj.modifiers):
            raise FxUnsupported("Modifier position is outside the stack.")
        vertex_modifier = vertex_stage(obj)
        if vertex_modifier and modifier_index is not None and modifier_index > obj.modifiers.find(vertex_modifier.name):
            raise FxUnsupported("Place deformation before the material vertex-input stage.")
    else:
        raise FxUnsupported(f"Unknown target {target!r}.")

    flags = None
    sphere_mode = 0
    if original is not None:
        mmd = getattr(original, "mmd_material", None)
        sphere_mode = int(mmd.sphere_texture_type) if mmd else 0
        flags = {"UseTexture": image is not None,
                 "UseSphereMap": has_sphere_map(original),
                 "UseToon": is_mmd_material(original)}
    facts = analyze(path, slot if target == MATERIAL else 0, material_flags=flags,
                    pass_type=pass_type, pass_context=pass_context, destination=target)
    if target == DEFORM:
        for material_slot in obj.material_slots:
            material = material_slot.material
            if not material or not material.get(TOKEN) or material.get(OWNER) != obj:
                continue
            shader = _core_node(material.node_tree, material[TOKEN]).node_tree
            vertex = shader.get(VERTEX_CORE)
            if (vertex and (vertex.get("ninesync_middle_vertex_deforms") or vertex.get("ninesync_middle_instance_loops"))
                    and shader.get(SOURCE) == str(facts.path)):
                raise FxUnsupported("The same FX already supplies material vertex deformation. Remove that copy before adding standalone deformation.")
    if target not in facts.targets:
        reason = " ".join(facts.problems) or (
            "No whole-mesh object VS with a recoverable or annotated local position." if target == DEFORM
            else "No single supported object pixel shader.")
        raise FxUnsupported(reason)
    owner = existing if target == MATERIAL else wrapper
    token = owner[TOKEN] if owner else uuid4().hex
    node = _core_node(wrapper, token) if existing else None
    old = node.node_tree if node else None
    old_vertex = old.get(VERTEX_CORE) if old else None
    vertex_wrapper = vertex_group_node = None
    if old_vertex:
        vertex_wrapper, vertex_group_node = vertex_node(obj, token)
        if vertex_group_node.node_tree != old_vertex:
            raise FxUnsupported("The material's vertex core connection changed.")
        if old_vertex.users > 2:
            raise FxUnsupported("The material's vertex core is shared. Make it independent before updating.")
        stage = vertex_stage(obj)
        if obj.modifiers.find(stage.name) != len(obj.modifiers) - 1:
            raise FxUnsupported("Place the material vertex-input stage after the model's other modifiers.")
    if existing and ((target == MATERIAL and (existing.users - int(existing.use_fake_user) > 1))
                     or (target == DEFORM and wrapper.users > 1)):
        raise FxUnsupported("The experimental output is shared. Make its destination independent before updating.")
    used_bindings = saved_bindings(old) if old else {}
    if old_vertex:
        used_bindings.update(saved_bindings(old_vertex))
    if old:
        for name in json.loads(old.get("ninesync_middle_pass_binding_names", "[]")):
            used_bindings.pop(name, None)
    if bindings:
        used_bindings.update(bindings)
    if pass_context:
        for name, mapped in pass_context.bindings.items():
            if bindings and name in bindings and bindings[name] != mapped:
                raise FxUnsupported(f"Controller {name!r} conflicts with the specialist's pass mapping.")
            used_bindings[name] = mapped
    colors = obj.data.color_attributes.active_color
    source_images = []
    if original and original.node_tree:
        for texture in original.node_tree.nodes:
            if texture.type == "TEX_IMAGE" and texture.image:
                source_images.append((texture.name, texture.image, texture.image.colorspace_settings.name))
    interpolation = target == MATERIAL and (is_mmd_material(original) if interpolate_vertices is None else interpolate_vertices)
    if facts.loops and target == MATERIAL and not interpolation:
        raise FxUnsupported("Material instance loops require the vertex interpolation stage.")
    signature = digest([PROFILE, bpy.app.version, facts.signature, facts.technique.name, pass_type, flags, sphere_mode, source_images,
                        pass_context.signature if pass_context else None,
                        morph_signature(obj, original), interpolation,
                        scene.render.engine if target == MATERIAL and facts.texture_writers else None,
                        target, slot if target == MATERIAL else 0,
                        units, tuple(layer.name for layer in obj.data.uv_layers), colors.name if colors else ""])
    if old and old.get(SIGNATURE) == signature and not force_rebuild and not bindings:
        messages = binding_diagnostics(old, scene, include_stored=False)
        if old_vertex:
            messages += binding_diagnostics(old_vertex, scene, include_stored=False)
        if (old.get("ninesync_middle_scene") == scene
                and old.get("ninesync_middle_image") == image
                and old.get("ninesync_middle_light") == light
                and old.get("ninesync_middle_camera") == scene.camera and not messages
                and views_current([old] + ([old_vertex] if old_vertex else []), scene)):
            if old.get("ninesync_middle_samples_base"):
                validate_base_image(image)
            warnings = tuple(filter(None, old.get("ninesync_middle_warnings", "").splitlines()))
            return Result(existing, old, "PARTIAL" if warnings else "SUPPORTED", warnings, False)

    kind = "ShaderNodeTree" if target == MATERIAL else "GeometryNodeTree"
    core = bpy.data.node_groups.new(f"MME FX Core | {facts.path.stem}", kind)
    core[TOKEN] = token
    vertex = None
    inserted = swapped = False
    try:
        if interpolation:
            vertex = bpy.data.node_groups.new(f"MME FX Vertices | {facts.path.stem}", "GeometryNodeTree")
            vertex[TOKEN] = token
            vertex["ninesync_middle_slot"] = slot
            stage = vertex_stage(obj)
            if stage and obj.modifiers.find(stage.name) != len(obj.modifiers) - 1:
                raise FxUnsupported("Place the material vertex-input stage after the model's other modifiers.")
            vertex[VERTEX_NAMESPACE] = stage.node_group[VERTEX_NAMESPACE] if stage else token
        warnings = compile_core(core, target, facts, obj, scene, original, image, used_bindings, units, light,
                                vertex_tree=vertex, attribute_prefix="nsfx_" + vertex[VERTEX_NAMESPACE] + "_" if vertex else None,
                                vertex_slot=slot, pass_context=pass_context)
        if (vertex and not vertex.get("ninesync_middle_vertex_attributes")
                and not vertex.get("ninesync_middle_vertex_deforms") and not vertex.get("ninesync_middle_instance_loops")):
            _free(bpy.data.node_groups, vertex)
            vertex = None
        if vertex and (vertex.get("ninesync_middle_vertex_deforms") or vertex.get("ninesync_middle_instance_loops")):
            deformer = _deformer(obj)
            if deformer and _core_node(deformer.node_group, deformer.node_group[TOKEN]).node_tree.get(SOURCE) == str(facts.path):
                raise FxUnsupported("The same FX already supplies standalone deformation. Remove that copy before applying the full material FX.")
        if old and _socket_fields(old) != _socket_fields(core):
            raise FxUnsupported("The FX input/output interface changed. The existing effect was kept.")
        if old_vertex and vertex and _socket_fields(old_vertex) != _socket_fields(vertex):
            raise FxUnsupported("The FX vertex interface changed. The existing effect was kept.")
        if vertex:
            core[VERTEX_CORE] = vertex
            vertex[SOURCE], vertex[SIGNATURE] = str(facts.path), signature
        core[SOURCE], core[SIGNATURE] = str(facts.path), signature
        core["ninesync_middle_scene"] = scene
        core["ninesync_middle_technique"] = facts.technique.name
        core["ninesync_middle_pass"] = pass_type or "auto"
        if scene.camera:
            core["ninesync_middle_camera"] = scene.camera
        if image:
            core["ninesync_middle_image"] = image
            core["ninesync_middle_samples_base"] = any(
                (n.type == "TEX_IMAGE" and n.image == image) or
                (n.bl_idname == "GeometryNodeImageTexture" and n.inputs["Image"].default_value == image)
                for tree in [core] + ([vertex] if vertex else []) for n in tree.nodes)
        if light:
            core["ninesync_middle_light"] = light
        core["ninesync_middle_warnings"] = "\n".join(warnings)
        if vertex:
            if old_vertex:
                _swap_core(vertex_wrapper, vertex_group_node, vertex)
                swapped = True
            else:
                install_vertex(obj, vertex, token)
                inserted = True
        if old:
            _swap_core(wrapper, node, core)
        elif target == MATERIAL:
            existing = _new_material(obj, selected_slot, slot, original, core, token)
        else:
            existing = _new_deformer(obj, core, token, modifier_index)
            stage = vertex_stage(obj)
            if stage and modifier_index is None:
                obj.modifiers.move(obj.modifiers.find(existing.name), obj.modifiers.find(stage.name))
        if old_vertex and not vertex:
            uninstall_vertex(obj, token)
        if old_vertex:
            _free(bpy.data.node_groups, old_vertex)
    except Exception:
        if inserted:
            uninstall_vertex(obj, token)
        if swapped:
            vertex_group_node.node_tree = old_vertex
        _free(bpy.data.node_groups, core)
        _free(bpy.data.node_groups, vertex)
        raise
    return Result(existing, core, "PARTIAL" if warnings else "SUPPORTED", warnings, True)


def inspect_bindings(obj, target, *, slot=0, scene=None):
    if target == RENDER:
        from .raster_targets import CONFIG
        data=obj.get(CONFIG)
        if data is None:return ('The selected mesh has no owned rendered scene.',)
        result=[];source_scene=scene or bpy.context.scene
        for row in data['members'].values():
            source=row['obj']
            if source is None or source_scene.objects.get(source.name)!=source:
                result.append(f"Rendered scene member {row['name']!r} is missing.")
            for name,source in row.get('bindings',{}).items():
                if source is None or source_scene.objects.get(source.name)!=source:
                    result.append(f'Controller {name!r} is missing from the rendered scene.')
        return tuple(result)
    _destination, wrapper, token = _current(obj, target, slot)
    core = _core_node(wrapper, token).node_tree
    result = binding_diagnostics(core, scene or bpy.context.scene)
    result += morph_diagnostics(core)
    if core.get(VERTEX_CORE):
        result += binding_diagnostics(core[VERTEX_CORE], scene or bpy.context.scene)
        result += morph_diagnostics(core[VERTEX_CORE])
        try:
            _vertex_wrapper, vertex_group_node = vertex_node(obj, token)
            if vertex_group_node.node_tree != core[VERTEX_CORE]:
                result += ("The material's vertex core connection changed.",)
        except FxUnsupported as error:
            result += (str(error),)
    return result


def retarget(obj, target, name, replacement, *, slot=0, scene=None):
    scene = scene or bpy.context.scene
    _require_model(obj, scene)
    if target == RENDER:
        from .raster_targets import retarget_render
        return retarget_render(obj,name,replacement,scene)
    if name.lower() == "(offscreenowner)":
        raise FxUnsupported("The effect specialist supplies (OffscreenOwner). Update the pass context to change it.")
    destination, wrapper, token = _current(obj, target, slot)
    if (target == DEFORM and wrapper.users > 1) or (target == MATERIAL and destination.users > 1):
        raise FxUnsupported("The experimental output is shared.")
    core = _core_node(wrapper, token).node_tree
    if core.users > 1:
        raise FxUnsupported("The experimental core is shared.")
    cores = [core] + ([core[VERTEX_CORE]] if core.get(VERTEX_CORE) else [])
    if core.get(VERTEX_CORE):
        _vertex_wrapper, vertex_group_node = vertex_node(obj, token)
        if vertex_group_node.node_tree != core[VERTEX_CORE] or core[VERTEX_CORE].users > 2:
            raise FxUnsupported("The material's vertex core is shared or its connection changed.")
    if name in json.loads(core.get("ninesync_middle_pass_binding_names", "[]")):
        raise FxUnsupported(f"The effect specialist supplies {name!r}. Update the pass mapping to change it.")
    affected = [group for group in cores if name in saved_bindings(group)]
    if not affected:
        raise FxUnsupported(f"No generated input reads controller {name!r}.")
    for group in affected:
        retarget_core(group, scene, obj, name, replacement, validate_only=True)
    for group in affected:
        retarget_core(group, scene, obj, name, replacement)


def remove(obj, target, *, slot=0, scene=None):
    _require_model(obj, scene or bpy.context.scene)
    if target == RENDER:
        from .raster_targets import remove_render
        return remove_render(obj)
    destination, wrapper, token = _current(obj, target, slot)
    core = _core_node(wrapper, token).node_tree
    vertex = core.get(VERTEX_CORE)
    if target == MATERIAL:
        ref = _slot(obj, slot)
        original = destination.get(ORIGINAL)
        link = destination["ninesync_middle_original_link"]
        if original is None:
            raise FxUnsupported("The override's original material is unavailable.")
        if vertex:
            _vertex_wrapper, _vertex_node = vertex_node(obj, token)
            if _vertex_node.node_tree != vertex:
                raise FxUnsupported("The material's vertex core connection changed.")
        if link == "DATA":
            if obj.data.materials[slot] != original:
                raise FxUnsupported("The underlying data slot changed. Restore its source material before removing.")
            if ref.link == "OBJECT":
                ref.material = None
                ref.link = "DATA"
        else:
            ref.material = original
        if vertex:
            uninstall_vertex(obj, token)
        _free(bpy.data.materials, destination)
    else:
        stage = next((node.node_tree for node in wrapper.nodes if node.get(STAGE_NODE) == token), None)
        if stage and stage.get(TOKEN) == token and mme_tree_signature(stage) != stage.get("ninesync_middle_initial_stage"):
            stage.use_fake_user = True
        obj.modifiers.remove(destination)
        _free(bpy.data.node_groups, wrapper)
        if stage and stage.get(TOKEN) == token:
            _free(bpy.data.node_groups, stage)
    _free(bpy.data.node_groups, core)
    _free(bpy.data.node_groups, vertex)
