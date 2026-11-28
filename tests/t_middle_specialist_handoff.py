"""Run the original o_Tonemap package through automatic apply and Blender lifecycle."""
import argparse
from pathlib import Path
import shutil
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t_middle_converters import MATERIAL, TOON, add_empty, make_model, set_scene
import ninesync_fx
from ninesync_fx.experimental_adapters.compositor import CORE, OWNER, TOKEN
from ninesync_fx.fx.errors import FxUnsupported
from ninesync_fx.runtime.middle.specialists import AUTO, plan_specialist
from ninesync_fx.runtime.middle.targets import apply, remove


def effects(scene):
    return [effect for effect in scene.compositor_effects if effect.node_group and effect.node_group.get(OWNER)]


def run(args):
    args.output.mkdir(parents=True)
    ninesync_fx.register()
    bpy.context.preferences.edit.use_global_undo = True
    scene = set_scene()
    obj, _rig, materials, _sibling = make_model(scene)
    object_name = obj.name
    add_empty(scene, "MME Control", (.5, 0, 0))
    light = add_empty(scene, "Direction source", (0, 0, 2))
    ordinary = apply(obj, TOON, AUTO, slot=1, light=light)
    assert ordinary.destination == obj.material_slots[1].material
    remove(obj, MATERIAL, slot=1)
    assert obj.material_slots[1].material == materials[1]
    material_name = materials[1].name
    fx = args.package / "o_Tonemap.fx"
    assert "o_Tonemap" in plan_specialist(fx).label
    assert plan_specialist(fx.with_suffix(".x")) == plan_specialist(fx)
    assert not effects(scene)
    foreign = bpy.data.node_groups.new("Artist scene effect", "CompositorNodeTree")
    foreign.allow_usage_in_scene_compositor_effect = True
    scene.compositor_effects.new("Artist").node_group = foreign
    assert bpy.ops.ed.undo_push(message="Before specialist handoff") == {"FINISHED"}
    assert bpy.ops.ninesync_fx.middle_apply(filepath=str(fx)) == {"FINISHED"}
    assert len(effects(scene)) == 1
    assert bpy.ops.ed.undo_push(message="After specialist handoff") == {"FINISHED"}
    assert bpy.ops.ed.undo() == {"FINISHED"}
    scene = bpy.context.scene
    assert not effects(scene) and len(scene.compositor_effects) == 1
    assert bpy.ops.ed.redo() == {"FINISHED"}
    scene = bpy.context.scene
    effect, = effects(scene)
    group = effect.node_group
    token = group[TOKEN]
    identifier = next(socket.identifier for socket in group.interface.items_tree if socket.name == "Strength")
    getattr(effect.properties.inputs, identifier).value = .42
    artist = group.nodes.new("NodeFrame")
    artist.label = "Artist wrapper edit"
    core_node = next(node for node in group.nodes if node.get(CORE))
    core = core_node.node_tree
    warm = apply(None, fx.with_suffix(".x"), AUTO, scene=scene)
    assert not warm.rebuilt and warm.destination == effect
    assert core_node.node_tree == core
    rebuilt = apply(None, fx, AUTO, scene=scene, force_rebuild=True)
    assert rebuilt.rebuilt and rebuilt.destination == effect and core_node.node_tree != core
    assert artist in group.nodes[:] and abs(getattr(effect.properties.inputs, identifier).value - .42) < 1e-6
    invalid = args.output / "Changed"
    shutil.copytree(args.package, invalid)
    readme = invalid / "readme.txt"
    readme.write_bytes(readme.read_bytes() + b"\nChanged diagnostic package.\n")
    try:
        apply(None, invalid / fx.name, AUTO, scene=scene)
    except FxUnsupported as error:
        assert "requires a verified package" in str(error), error
    else:
        raise AssertionError("Changed package was routed to a known specialist")
    assert len(effects(scene)) == 1 and effects(scene)[0].node_group == group
    assert bpy.data.objects[object_name].material_slots[1].material.name == material_name
    saved = args.output / "specialist.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    bpy.ops.wm.open_mainfile(filepath=str(saved), load_ui=False)
    scene = bpy.context.scene
    effect, = effects(scene)
    assert effect.node_group[TOKEN] == token
    assert any(node.label == "Artist wrapper edit" for node in effect.node_group.nodes)
    assert abs(getattr(effect.properties.inputs, identifier).value - .42) < 1e-6
    assert not apply(None, fx, AUTO, scene=scene).rebuilt
    assert bpy.ops.ninesync_fx.adapter_action(action="REMOVE", index=1, token=token) == {"FINISHED"}
    assert not effects(scene) and len(scene.compositor_effects) == 1
    assert scene.compositor_effects[0].node_group.name == "Artist scene effect"
    assert bpy.data.objects[object_name].material_slots[1].material.name == material_name
    print("PASS AUTOMATIC SPECIALIST APPLY REAPPLY UNDO REDO REOPEN REMOVE", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args(sys.argv[sys.argv.index("--") + 1:]))
