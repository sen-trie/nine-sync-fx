"""Compare exported native MMD probes and render the same real ToonDX scene."""
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer, render_image
from ninesync_fx.runtime.middle.targets import apply, inspect_bindings, MATERIAL, OWNER, TOKEN, _core_node
from ninesync_fx.runtime.middle.material_morphs import model_root
from ninesync_fx.runtime.middle.controllers import resolve, wire, CONTROL_DATA


def image_pixels(path):
    # PNG byte values remain encoded in Blender's file image pixel API.
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        return np.rint(np.array(image.pixels[:]).reshape(image.size[1], image.size[0], 4)*255).astype(float)
    finally:
        bpy.data.images.remove(image)


def run(args):
    load_importer(args.mmd_tools)
    output = args.output
    data = json.loads((output / "results.json").read_text(encoding="utf-8"))
    pixels = image_pixels(output / "native-power-0.png")[:, :, :3]
    mask = np.max(np.abs(pixels-[25,255,0]), axis=2) <= 1
    assert mask.sum() > 2000
    checks = []
    fields = ("diffuse", "ambient_diffuse", "ambient", "specular")
    for frame in (0,10,20,30):
        expected = next(item["expected_inputs"] for item in data["checks"]
                        if item["frame"] == frame and item["offset_delta"] == 0)
        for channel in (range(5) if frame < 30 else (4,)):
            path = output / (f"native-channel-{channel}-{frame}.png" if channel<4 else f"native-power-{frame}.png")
            want = expected[fields[channel]][:3] if channel<4 else [expected["power"][0], expected["diffuse"][3], 0]
            actual = image_pixels(path)[:, :, :3][mask]
            error = float(np.max(np.abs(actual-np.clip(want,0,1)*255)))
            assert error <= 1.0, (frame,channel,error)
            checks.append(dict(frame=frame, channel=channel, maximum_byte_error=error,
                               pixels=int(mask.sum()), sha256=sha256(path.read_bytes()).hexdigest()))
    base_checks = []
    for channel in (0,1):
        path = output / f"native-base-{channel}.png"
        actual = image_pixels(path)[:, :, :3][mask]
        error = float(np.abs(actual-np.array([0.65,0.55,0.45])*255).max())
        assert error <= 1.0, (channel,error)
        base_checks.append(dict(channel=channel, maximum_byte_error=error,
                                pixels=int(mask.sum()), sha256=sha256(path.read_bytes()).hexdigest()))
    group_checks = []
    for frame in (0,20,30):
        item = next(item for item in data["checks"] if item["frame"] == frame and item["offset_delta"] == 0)
        want = np.array([item["weights"][3],0,0])*255
        path = output / f"native-group-{frame}.png"
        actual = image_pixels(path)[:, :, :3][mask]
        error = float(np.abs(actual-want).max())
        assert error <= 1.0, (frame,error)
        group_checks.append(dict(frame=frame, maximum_byte_error=error, pixels=int(mask.sum()),
                                 sha256=sha256(path.read_bytes()).hexdigest()))
    bpy.ops.wm.open_mainfile(filepath=str(output / "material-morphs.blend"), load_ui=False)
    obj = next(mat[OWNER] for mat in bpy.data.materials if mat.get(OWNER))
    assert not inspect_bindings(obj, MATERIAL, slot=1)
    material = obj.material_slots[1].material
    core = _core_node(material.node_tree, material[TOKEN]).node_tree
    record = next(row for row in json.loads(core[CONTROL_DATA]) if row[0] == "GroupWeight")
    socket = core.nodes[next(name for name in record[4] if name)].outputs[0]
    leaf_checks = []
    for frame in (0,20,30):
        item = next(item for item in data["checks"] if item["frame"] == frame and item["offset_delta"] == 0)
        desired = np.array([item["weights"][0]*0.5,item["weights"][3]*0.5,0])*255
        path = output / f"native-leaf-{frame}.png"
        error = float(np.abs(image_pixels(path)[:,:,:3][mask]-desired).max())
        assert error <= 1.0, (frame,error)
        source = resolve(bpy.context.scene,obj,"(self)","FX Tint Mul",1,{},0.08)[1][0]
        wire(socket,source)
        image = render_image(bpy.context.scene,output,f"blender-leaf-controller-{frame}",frame)
        rgb = np.array(item["expected_encoded_rgb"])+(item["weights"][0]-item["weights"][3])*0.01
        linear = np.where(rgb<=0.04045,rgb/12.92,((rgb+0.055)/1.055)**2.4)
        matched = int(((np.abs(image[:,:,:3]-linear).max(2)<2e-5)&(image[:,:,3]>0.99)).sum())
        assert matched > 1000, (frame,matched)
        leaf_checks.append(dict(frame=frame,maximum_byte_error=error,pixels=int(mask.sum()),
                                shader_pixels_matched=matched,sha256=sha256(path.read_bytes()).hexdigest()))
    wire(socket,resolve(bpy.context.scene,obj,"(self)","FX Tint Group",1,{},0.08)[1][0])
    root = model_root(obj)
    slider = next(child for child in root.children if child.type == "MESH" and child.mmd_type == "PLACEHOLDER")
    bpy.data.objects.remove(slider, do_unlink=True)
    messages = inspect_bindings(obj, MATERIAL, slot=1)
    assert any(message.startswith("GroupWeight:") and "unavailable" in message for message in messages), messages
    # The deletion above is confined to this transient test scene. Reopen the
    # unmodified saved ToonDX scene before rendering or saving the deliverable.
    bpy.ops.wm.open_mainfile(filepath=str(output / "original-toondx-morphs.blend"), load_ui=False)
    scene = bpy.context.scene
    scene.render.resolution_x = scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    camera = scene.camera
    camera.location = (0,-45*0.08,10*0.08)
    camera.rotation_euler = (math.pi/2,0,0)
    camera.data.type = "PERSP"
    camera.data.sensor_fit = "VERTICAL"
    camera.data.lens = camera.data.sensor_height/(2*math.tan(math.radians(30)/2))
    light = scene.objects["ToonDX light"]
    # MMD light direction (-0.5,-1,+0.5) in the shared XZY/negative-position convention.
    light.location = (0.5,-0.5,1)
    obj = next(mat[OWNER] for mat in bpy.data.materials if mat.get(OWNER))
    for slot, name in data["original_toondx_slots"].items():
        apply(obj, args.toondx/(name+".fx"), MATERIAL, slot=int(slot), units=0.08, light=light, force_rebuild=True)
    for frame in (0,20,30):
        render_image(scene, output, f"blender-matched-toondx-{frame}", frame)
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "matched-toondx-morphs.blend"), relative_remap=False)
    report = dict(native_model=str(output / "Sanae-material-morph-diagnostic.pmx"),
                  native_model_sha256=sha256((output / "Sanae-material-morph-diagnostic.pmx").read_bytes()).hexdigest(),
                  checks=checks, baseline_color_checks=base_checks, group_controller_checks=group_checks,
                  leaf_controller_checks=leaf_checks,
                  deleted_slider_controller_diagnostics_verified=True,
                  camera=dict(position=[0,10,0],rotation_degrees=[0,0,0],distance=45,fov=30,resolution=[512,512]),
                  native_light=dict(direction=[-0.5,-1,0.5],color=[154,154,154]),
                  scope="Native material input probes; full renderer parity remains partial.")
    (output / "native-validation.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print("PASS NATIVE_MATERIAL_MORPH_INPUTS", len(checks), "plus", len(base_checks), "base-color probes",
          "plus",len(group_checks),"group controller probes",
          "plus",len(leaf_checks),"leaf controller probes",
          "pixels", int(mask.sum()), "maximum error", max(item["maximum_byte_error"] for item in checks), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--mmd-tools",type=Path,required=True)
    parser.add_argument("--toondx",type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
