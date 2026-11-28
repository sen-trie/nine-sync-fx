"""Render the same bone-row probe as native MMD on every visible real PMX slot."""
import argparse
from collections import Counter
import math
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer, model_object, render_image
from t_middle_converters import set_scene
from ninesync_fx.runtime.middle.targets import apply, inspect_bindings, MATERIAL


def run(args):
    pmx, importer = load_importer(args.mmd_tools)
    scene = set_scene()
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = scene.render.resolution_y = 512
    scene.world.use_nodes = True
    scene.world.node_tree.nodes.get("Background").inputs["Color"].default_value = (0,0,0,1)
    scene.frame_set(0)
    model = pmx.load(str(args.output / "Sanae-bone-diagnostic.pmx"))
    importer().execute(pmx=model, scale=.08, types={"MESH", "ARMATURE", "MORPHS"}, fix_bone_order=False)
    obj = model_object(scene, model)
    from mmd_tools.core.vmd.importer import VMDImporter
    VMDImporter(str(args.output / "bone-motion.vmd"), scale=.08, frame_margin=0).assign(obj.find_armature())
    camera = scene.camera
    camera.location = (0,-45*.08,10*.08)
    camera.rotation_euler = (math.pi/2,0,0)
    camera.data.type, camera.data.sensor_fit = "PERSP", "VERTICAL"
    camera.data.lens = camera.data.sensor_height/(2*math.tan(math.radians(30)/2))
    slots = [slot for slot in range(len(obj.material_slots)) if slot not in (2,14)]
    for slot in slots:
        apply(obj, args.output / "native-0.fx", MATERIAL, slot=slot, units=.08)
        assert not inspect_bindings(obj, MATERIAL, slot=slot)
    for frame in (0,10,20):
        render_image(scene, args.output, f"blender-native-{frame}", frame)
        colors = []
        for path in (args.output / f"native-0-{frame}.png", args.output / f"blender-native-{frame}.png"):
            image = bpy.data.images.load(str(path), check_existing=False)
            rgb = np.rint(np.array(image.pixels[:]).reshape(-1,4)[:,:3]*255).astype(int)
            observed, count = Counter(map(tuple, rgb[np.any(rgb != 0, axis=1)])).most_common(1)[0]
            assert count > 10000
            colors.append(np.array(observed))
            bpy.data.images.remove(image)
        assert np.max(np.abs(colors[0]-colors[1])) <= 1, (frame, colors)
    bpy.ops.file.pack_all()
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "bone-matrix-native-comparison.blend"), relative_remap=False)
    print("PASS NATIVE_BONE_RENDER_COMPARISON", len(slots), "slots; three keyed poses; solid-color error <= 1/255", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mmd-tools", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
