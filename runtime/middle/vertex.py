"""Evaluate material VS outputs on mesh corners before raster interpolation."""

import json

import bpy

from ...fx.entry import fx_entry, fx_run_entry
from ...fx.errors import FxUnsupported
from ...fx.types import fx_count, fx_is_numeric
from ...nodes.backend_geo import MmeGeoBackend
from ...nodes.socket import MmeSock
from .position import PositionExec, is_passthrough

VERTEX_CORE = "ninesync_middle_vertex_core"
VERTEX_OWNER = "ninesync_middle_vertex_owner"
VERTEX_NODE = "ninesync_middle_vertex_node"
VERTEX_NAMESPACE = "ninesync_middle_vertex_namespace"


def stage(obj):
    found = [modifier for modifier in obj.modifiers if modifier.type == "NODES" and modifier.node_group
             and modifier.node_group.get(VERTEX_OWNER)]
    if len(found) > 1 or any(modifier.node_group.get(VERTEX_OWNER) != obj for modifier in found):
        raise FxUnsupported("The material vertex stage is duplicated or belongs to another object.")
    if found and found[0].node_group.users != 1:
        raise FxUnsupported("The material vertex stage is shared. Make it independent before updating.")
    return found[0] if found else None


def locate(obj, token):
    modifier = stage(obj)
    nodes = [node for node in modifier.node_group.nodes if node.get(VERTEX_NODE) == token] if modifier else []
    if len(nodes) != 1 or not nodes[0].node_tree:
        raise FxUnsupported("The material's generated vertex stage is missing or duplicated.")
    if len(nodes[0].inputs["Geometry"].links) != 1:
        raise FxUnsupported("Restore the generated vertex stage's geometry input before updating it.")
    return modifier.node_group, nodes[0]


def install(obj, core, token):
    modifier = stage(obj)
    created = modifier is None
    if created:
        wrapper = bpy.data.node_groups.new("MME FX Vertex Inputs", "GeometryNodeTree")
        wrapper[VERTEX_OWNER] = obj
        wrapper[VERTEX_NAMESPACE] = core[VERTEX_NAMESPACE]
        for direction in ("INPUT", "OUTPUT"):
            wrapper.interface.new_socket("Geometry", in_out=direction, socket_type="NodeSocketGeometry")
        source = wrapper.nodes.new("NodeGroupInput")
        source.location.x = -300
        output = wrapper.nodes.new("NodeGroupOutput")
        wrapper.links.new(source.outputs["Geometry"], output.inputs["Geometry"])
        modifier = obj.modifiers.new("MME FX Vertex Inputs", "NODES")
        modifier.node_group = wrapper
    else:
        wrapper = modifier.node_group
    node = None
    try:
        output = next(node for node in wrapper.nodes if node.type == "GROUP_OUTPUT" and node.is_active_output)
        links = list(output.inputs["Geometry"].links)
        if len(links) != 1:
            raise FxUnsupported("The vertex stage needs one connected geometry output.")
        incoming = links[0].from_socket
        node = wrapper.nodes.new("GeometryNodeGroup")
        node[VERTEX_NODE] = token
        node.label = f"Slot {core['ninesync_middle_slot']}: {core.name.partition('|')[2].strip()}"
        node.node_tree = core
        node.location.x = 280 * (sum(bool(other.get(VERTEX_NODE)) for other in wrapper.nodes) - 1)
        output.location.x = node.location.x + 300
        wrapper.links.new(incoming, node.inputs["Geometry"])
        wrapper.links.new(node.outputs["Geometry"], output.inputs["Geometry"])
        return wrapper, node
    except Exception:
        if node:
            wrapper.nodes.remove(node)
        if created:
            obj.modifiers.remove(modifier)
            bpy.data.node_groups.remove(wrapper)
        raise


def uninstall(obj, token):
    wrapper, node = locate(obj, token)
    incoming = list(node.inputs["Geometry"].links)
    outgoing = list(node.outputs["Geometry"].links)
    if len(incoming) != 1:
        raise FxUnsupported("Restore the generated vertex stage's geometry input before removing it.")
    for link in outgoing:
        wrapper.links.new(incoming[0].from_socket, link.to_socket)
    wrapper.nodes.remove(node)
    if not any(node.get(VERTEX_NODE) for node in wrapper.nodes):
        # Retain artist work if the now-empty wrapper contains extra nodes.
        if len(wrapper.nodes) == 2:
            modifier = stage(obj)
            obj.modifiers.remove(modifier)
            bpy.data.node_groups.remove(wrapper)


class VertexFields:
    def __init__(self, tree, shader_env, analysis, input_type, prefix, slot):
        self.shader_env = shader_env
        self.prefix = prefix
        self.exports = {}
        self.reads = {}
        self.descriptors = {}
        self.slot = slot
        self.packed = {}
        B = MmeGeoBackend(tree)
        B.use_shared_groups = False
        self.env = input_type(analysis, B, shader_env.obj, shader_env.scene,
                              shader_env.material, shader_env.image, shader_env.bindings,
                              shader_env.units, shader_env.light, shader_env.pass_context)
        self.env.vertex_fields = True
        self.env.encoded_images = shader_env.encoded_images
        self.env.recover_position = True
        self.env.world_shading = shader_env.world_shading
        function, args = fx_entry(analysis.program, analysis.shader_pass.vs)
        execution = PositionExec(analysis.program, B, self.env)
        self.outputs = fx_run_entry(execution, function, args, self.env.mesh_input)
        if B.known(execution.discard) != 0.0:
            raise FxUnsupported("Vertex discard requires native clipping support.")
        position = execution.local_position(self.outputs)
        self.position = None if is_passthrough(self.env, position) else position
        self.split_corners = False
        if analysis.loops:
            self.split_corners = len({polygon.material_index for polygon in self.env.obj.data.polygons}) > 1
        if self.position is not None:
            self.split_corners = len({polygon.material_index for polygon in self.env.obj.data.polygons}) > 1
            # A raster vertex can have a different position at each material/UV seam.
            # Split the evaluated mesh, after skinning, when point-domain evaluation
            # would otherwise average those distinct corner inputs.
            pending = [value.socket.node for value in self.position.c if isinstance(value, MmeSock)]
            seen = set()
            while pending:
                node = pending.pop()
                if node.name in seen:
                    continue
                seen.add(node.name)
                if node.bl_idname == "GeometryNodeInputNamedAttribute":
                    attribute = self.env.obj.data.attributes.get(node.inputs["Name"].default_value)
                    if attribute and attribute.domain == "CORNER":
                        self.split_corners = True
                pending.extend(link.from_node for socket in node.inputs for link in socket.links)

    def value(self, ex, key, value, requested):
        if (not fx_is_numeric(value.t) or not fx_is_numeric(requested) or len(requested[1]) > 1
                or value.t[0] != "f" or requested[0] != "f"):
            raise FxUnsupported(f"Vertex interpolant {key} needs a numeric scalar or vector.")
        count = fx_count(requested[1])
        if count > len(value.c):
            raise FxUnsupported(f"Vertex interpolant {key} has too few components.")
        result = []
        for component, source in enumerate(value.c[:count]):
            descriptor = f"{key}.{component}"
            if descriptor not in self.exports:
                index = len(self.exports)
                name = f"{self.prefix}v{index // 4}"
                if name not in self.packed:
                    node = self.shader_env.backend.new("ShaderNodeAttribute")
                    node.attribute_name = name
                    node.label = f"Interpolated vertex inputs {index // 4}"
                    split = self.shader_env.backend.new("ShaderNodeSeparateColor")
                    self.shader_env.backend.links.new(node.outputs["Color"], split.inputs[0])
                    self.packed[name] = [MmeSock(output) for output in split.outputs[:3]] + [MmeSock(node.outputs["Alpha"])]
                self.exports[descriptor] = source
                self.descriptors[descriptor] = (name, index % 4)
            name, channel = self.descriptors[descriptor]
            output = self.packed[name][channel]
            self.reads.setdefault(descriptor, []).append((output.socket.node.name, output.socket.identifier))
            result.append(output)
        return self.shader_env.value(ex, requested, result)

    def read(self, ex, semantic, requested):
        if semantic == "POSITION0" or semantic not in self.outputs:
            if not fx_is_numeric(requested) or len(requested[1]) > 1:
                raise FxUnsupported(f"Vertex shader has no available interpolant {semantic}.")
            values = []
            for component in range(fx_count(requested[1])):
                key = f"Unavailable vertex interpolant {semantic}.{component}"
                self.shader_env.unavailable[key] = semantic
                values.append(self.shader_env.backend.new_input(key, label=key))
            return self.shader_env.value(ex, requested, values)
        return self.value(ex, semantic, self.outputs[semantic], requested)

    def finish(self):
        # Shader pruning determines which VS outputs actually reach the final surface.
        def used(descriptor):
            for name, identifier in self.reads[descriptor]:
                node = self.shader_env.backend.nodes.get(name)
                if node and any(output.identifier == identifier and output.is_linked for output in node.outputs):
                    return True
            return False
        active = {descriptor: source for descriptor, source in self.exports.items() if used(descriptor)}
        if not active and self.position is None and not self.env.analysis.loops:
            return ()
        B = self.env.backend
        geometry = B.new_input("Geometry", "NodeSocketGeometry", "Geometry")
        original_normal = None
        if self.split_corners:
            normals = [node for node in B.nodes if node.bl_idname == "GeometryNodeInputNormal"]
            capture = B.new("GeometryNodeCaptureAttribute")
            capture.domain = "CORNER"
            capture.capture_items.new("VECTOR", "Source normal")
            capture.label = "Preserve smooth normals before splitting raster vertices"
            B.links.new(geometry.socket, capture.inputs["Geometry"])
            normal = B.new("GeometryNodeInputNormal")
            B.links.new(normal.outputs[0], capture.inputs["Source normal"])
            original_normal = capture.outputs["Source normal"]
            for node in normals:
                for link in list(node.outputs[0].links):
                    B.links.new(original_normal, link.to_socket)
            split = B.new("GeometryNodeSplitEdges")
            split.label = "Independent positions at material and UV seams"
            B.links.new(capture.outputs["Geometry"], split.inputs["Mesh"])
            geometry = MmeSock(split.outputs["Mesh"])
            B.tree["ninesync_middle_split_corners"] = True
        index = B.new("GeometryNodeInputMaterialIndex")
        selection = B.cmp("==", MmeSock(index.outputs[0]), float(self.slot))
        remainder = None
        if self.env.analysis.loops:
            from .instances import duplicate
            separate = B.new("GeometryNodeSeparateGeometry")
            separate.domain = "FACE"
            B.links.new(geometry.socket, separate.inputs["Geometry"])
            B.feed(separate.inputs["Selection"], selection)
            remainder = separate.outputs["Inverted"]
            geometry = duplicate(self.env, MmeSock(separate.outputs["Selection"]))
        packed = {}
        for descriptor, value in active.items():
            name, channel = self.descriptors[descriptor]
            packed.setdefault(name, [0.0] * 4)[channel] = value
        for name, values in packed.items():
            store = B.new("GeometryNodeStoreNamedAttribute")
            store.domain = "CORNER"
            store.data_type = "FLOAT_COLOR"
            store.inputs["Name"].default_value = name
            store.label = "Interpolated VS values"
            B.links.new(geometry.socket, store.inputs["Geometry"])
            B.feed(store.inputs["Selection"], selection)
            combine = B.new("FunctionNodeCombineColor")
            for component, value in enumerate(values):
                B.feed(combine.inputs[component], value)
            B.links.new(combine.outputs[0], store.inputs["Value"])
            geometry = MmeSock(store.outputs["Geometry"])
        if self.position is not None:
            position = B.vector_of([B.mul(value, self.env.units) for value in
                                    (self.position.c[0], self.position.c[2], self.position.c[1])], force=True)
            setter = B.new("GeometryNodeSetPosition")
            B.links.new(geometry.socket, setter.inputs["Geometry"])
            B.feed(setter.inputs["Selection"], selection)
            B.links.new(position.socket, setter.inputs["Position"])
            geometry = MmeSock(setter.outputs["Geometry"])
            B.tree["ninesync_middle_vertex_deforms"] = True
        if remainder is not None:
            join = B.new("GeometryNodeJoinGeometry")
            B.links.new(geometry.socket, join.inputs["Geometry"])
            B.links.new(remainder, join.inputs["Geometry"])
            geometry = MmeSock(join.outputs["Geometry"])
        if original_normal is not None:
            restore = B.new("GeometryNodeSetMeshNormal")
            restore.mode, restore.domain = "FREE", "CORNER"
            restore.label = "Retain source corner normals"
            B.links.new(geometry.socket, restore.inputs["Mesh"])
            B.links.new(original_normal, restore.inputs["Custom Normal"])
            geometry = MmeSock(restore.outputs["Mesh"])
        B.finish([("Geometry", geometry, "NodeSocketGeometry")])
        self.env.finish()
        B.tree["ninesync_middle_vertex_attributes"] = len(active)
        B.tree["ninesync_middle_vertex_map"] = json.dumps({descriptor: self.descriptors[descriptor] for descriptor in active})
        return tuple(sorted(self.env.warnings))
