"""Editable native Blender Scene Effects, created only after explicit apply."""

from dataclasses import dataclass
from uuid import uuid4

import bpy


OWNER = "ninesync_experimental_adapter"
TOKEN = "ninesync_experimental_id"
CORE = "ninesync_experimental_core"
SCHEMA = 1

CONTROLS = {
    "tonal": (
        ("Exposure", 0.0, -8.0, 8.0),
        ("Contrast", 0.0, -100.0, 100.0),
        ("Saturation", 1.0, 0.0, 2.0),
        ("Strength", 1.0, 0.0, 1.0),
    ),
    "bloom": (
        ("Threshold", 1.0, 0.0, 32.0),
        ("Glow", 1.0, 0.0, 5.0),
        ("Strength", 1.0, 0.0, 1.0),
    ),
}


@dataclass(frozen=True)
class AdapterPlan:
    adapter: str
    label: str
    source: str
    coverage: str
    omissions: str


def plan(adapter="tonal", match=None, facts=None):
    if adapter not in CONTROLS:
        raise ValueError("Unknown experimental Scene Effect")
    if match is not None:
        if match.adapter != adapter or not facts.fingerprint:
            raise ValueError("The selected package has no verified adapter")
        entry = match.entry
        return AdapterPlan(adapter, f'{entry["name"]} {entry["version"]} adaptation',
                           facts.fingerprint, "Independent adaptation", entry["adapter_notes"])
    if adapter == "tonal":
        return AdapterPlan(adapter, "NineSync Tonal", "generic", "Independent tonal adjustment",
                           "Uses the incoming image. No MME lighting, textures or render targets are imported.")
    return AdapterPlan(adapter, "NineSync Bloom", "generic", "Independent full-image bloom",
                       "Highlights are selected by image brightness. Object masks, extra passes and MME alpha modes are omitted.")


def _interface(tree, adapter):
    tree.interface.new_socket(name="Image", in_out="INPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    for name, default, low, high in CONTROLS[adapter]:
        socket = tree.interface.new_socket(name=name, in_out="INPUT", socket_type="NodeSocketFloat")
        socket.min_value, socket.max_value, socket.default_value = low, high, default


def _node(tree, kind, x, y=0):
    node = tree.nodes.new(kind)
    node.location = (x, y)
    return node


def _core(adapter):
    tree = bpy.data.node_groups.new(f"NineSync {adapter.title()} Core", "CompositorNodeTree")
    try:
        tree[CORE] = SCHEMA
        _interface(tree, adapter)
        source = _node(tree, "NodeGroupInput", 0)
        image = source.outputs["Image"]
        if adapter == "tonal":
            stages = (("CompositorNodeExposure", "Exposure"),
                      ("CompositorNodeBrightContrast", "Contrast"),
                      ("CompositorNodeHueSat", "Saturation"))
            for i, (kind, control) in enumerate(stages, 1):
                stage = _node(tree, kind, i * 200)
                tree.links.new(image, stage.inputs["Image"])
                tree.links.new(source.outputs[control], stage.inputs[control])
                image = stage.outputs["Image"]
        else:
            stage = _node(tree, "CompositorNodeGlare", 250)
            stage.inputs["Type"].default_value = "Fog Glow"
            tree.links.new(image, stage.inputs["Image"])
            tree.links.new(source.outputs["Threshold"], stage.inputs["Threshold"])
            tree.links.new(source.outputs["Glow"], stage.inputs["Strength"])
            image = stage.outputs["Image"]

        mix = _node(tree, "ShaderNodeMix", 850)
        mix.data_type = "RGBA"
        inputs = {socket.identifier: socket for socket in mix.inputs}
        tree.links.new(source.outputs["Strength"], inputs["Factor_Float"])
        tree.links.new(source.outputs["Image"], inputs["A_Color"])
        tree.links.new(image, inputs["B_Color"])
        output = _node(tree, "NodeGroupOutput", 1050)
        tree.links.new(next(s for s in mix.outputs if s.identifier == "Result_Color"), output.inputs["Image"])
        return tree
    except Exception:
        bpy.data.node_groups.remove(tree)
        raise


def _discard(group):
    if group is not None and group.users == 0:
        bpy.data.node_groups.remove(group)


def _wrapper(adapter_plan, key):
    tree = bpy.data.node_groups.new(adapter_plan.label, "CompositorNodeTree")
    core = None
    try:
        core = _core(adapter_plan.adapter)
        tree[OWNER] = adapter_plan.adapter
        tree[TOKEN] = uuid4().hex
        tree["source_key"] = key
        tree["adapter_schema"] = SCHEMA
        tree.allow_usage_in_scene_compositor_effect = True
        _interface(tree, adapter_plan.adapter)
        source = _node(tree, "NodeGroupInput", 0)
        inner = _node(tree, "CompositorNodeGroup", 260)
        inner[CORE] = True
        inner.node_tree = core
        output = _node(tree, "NodeGroupOutput", 570)
        for socket in inner.inputs:
            tree.links.new(source.outputs[socket.name], socket)
        tree.links.new(inner.outputs["Image"], output.inputs["Image"])
        return tree
    except Exception:
        _discard(tree)
        _discard(core)
        raise


class CompositorAdapter:
    @staticmethod
    def apply(scene, adapter_plan):
        key = f"{adapter_plan.adapter}:{adapter_plan.source}"
        effects = scene.compositor_effects
        for effect in effects:
            group = effect.node_group
            if group and group.get(OWNER) == adapter_plan.adapter and group.get("source_key") == key:
                return effect

        group = _wrapper(adapter_plan, key)
        core = next(n.node_tree for n in group.nodes if n.get(CORE))
        effect = None
        try:
            effect = effects.new(adapter_plan.label)
            effect.node_group = group
            return effect
        except Exception:
            if effect is not None:
                effects.remove(effect)
            _discard(group)
            _discard(core)
            raise

    @staticmethod
    def disable(effect):
        _owned(effect)
        effect.enable_for_render = False
        effect.enable_for_preview = False

    @staticmethod
    def remove(scene, effect):
        group = _owned(effect)
        cores = {node.node_tree for node in group.nodes
                 if node.get(CORE) and node.type == "GROUP" and node.node_tree and node.node_tree.get(CORE)}
        scene.compositor_effects.remove(effect)
        _discard(group)
        for core in cores:
            _discard(core)

    @staticmethod
    def rebuild(effect):
        group = _owned(effect)
        if group.users != 1:
            raise ValueError("This wrapper is shared. Make it single-user before rebuilding.")
        nodes = [n for n in group.nodes if n.get(CORE) and n.type == "GROUP"]
        if len(nodes) != 1 or not nodes[0].node_tree or not nodes[0].node_tree.get(CORE):
            raise ValueError("The generated core was replaced or cannot be located.")
        node = nodes[0]
        original = node.node_tree
        replacement = _core(group[OWNER])
        try:
            _replace_core(group, node, replacement)
        except Exception:
            _discard(replacement)
            raise
        _discard(original)


def _owned(effect):
    group = effect.node_group
    if not group or group.get(OWNER) not in CONTROLS or not group.get(TOKEN):
        raise ValueError("Select a NineSync experimental Scene Effect.")
    return group


def _replace_core(tree, node, replacement):
    original = node.node_tree
    signature = lambda group: [(s.identifier, s.in_out, s.socket_type)
                               for s in group.interface.items_tree if s.item_type == "SOCKET"]
    if signature(original) != signature(replacement):
        raise ValueError("The core socket interface changed. Existing connections were preserved.")

    incoming = [(link.from_socket, link.to_socket.identifier) for link in tree.links if link.to_node == node]
    outgoing = [(link.from_socket.identifier, link.to_socket) for link in tree.links if link.from_node == node]
    values = {s.identifier: (tuple(s.default_value) if s.type == "RGBA" else s.default_value)
              for s in node.inputs}

    def install(group):
        node.node_tree = group
        inputs = {s.identifier: s for s in node.inputs}
        outputs = {s.identifier: s for s in node.outputs}
        for identifier, value in values.items():
            inputs[identifier].default_value = value
        for source, identifier in incoming:
            if not any(link.from_socket == source for link in inputs[identifier].links):
                tree.links.new(source, inputs[identifier])
        for identifier, target in outgoing:
            if not any(link.from_socket == outputs[identifier] for link in target.links):
                tree.links.new(outputs[identifier], target)

    try:
        install(replacement)
    except Exception:
        install(original)
        raise
