"""Original ToonDX/BeamMan surfaces with texture morphs and creator textures."""
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer, model_object, render_image, snapshot
from t_middle_converters import set_scene, add_empty
from t_middle_texture_morph import NAMES, FRAMES
from ninesync_fx.runtime.middle.targets import apply,remove,inspect_bindings,MATERIAL
from ninesync_fx.nodes.material_compile import mme_tree_signature

TOON_SLOTS = {0:"\u9854",1:"\u808c",11:"\u9aea",12:"\u670d",13:"\u670d",15:"\u670d",16:"\u670d",17:"\u9aea2",18:"\u305d\u306e\u4ed6"}
TOON_SLOTS.update({slot:"\u9854" for slot in range(3,11)})
SURFACE_FRAMES = (0,20,40,60,70)


def run(args):
    pmx, importer = load_importer(args.mmd_tools)
    scene = set_scene()
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = scene.render.resolution_y = 512
    if args.native_pixel_centers:
        scene.eevee.taa_render_samples = 1
    scene.render.film_transparent = True
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (0,0,0,1)
    model_path = args.model or args.output / "Sanae-texture-surfaces.pmx"
    model = pmx.load(str(model_path))
    importer().execute(pmx=model,scale=.08,types={"MESH","ARMATURE","MORPHS"},fix_bone_order=False)
    obj = model_object(scene,model)
    from mmd_tools.core.model import Model,FnModel
    rig = Model(FnModel.find_root_object(obj))
    rig.morph_slider.bind()
    slider = rig.morph_slider.placeholder(binded=True)
    for frame,weights in FRAMES.items():
        for name,weight in zip(NAMES,weights):
            key = slider.data.shape_keys.key_blocks[name]
            key.value = weight
            key.keyframe_insert("value",frame=frame)
    camera = scene.camera
    camera.location,camera.rotation_euler = (0,-45*.08,10*.08),(math.pi/2,0,0)
    camera.data.type,camera.data.sensor_fit = "PERSP","VERTICAL"
    camera.data.lens = camera.data.sensor_height/(2*math.tan(math.radians(30)/2))
    if args.native_pixel_centers:
        camera.data.shift_x = -.5 / scene.render.resolution_x
        camera.data.shift_y = .5 / scene.render.resolution_y
    light = add_empty(scene,"Texture morph light",(.5,-.5,1))
    scene.frame_set(0)
    bpy.context.view_layer.update()
    before = snapshot(obj)
    signatures = {material:mme_tree_signature(material.node_tree) for material in obj.data.materials if material}
    beam_names = {"NewToon","NewToon2","full_NoneTexFilter","full_SimpleSoftShadow","full_BeamManCustom"}
    beam = [p for p in sorted(args.beamman.rglob("*.fx")) if p.stem in beam_names]
    assert len(beam)==6
    sources = [("ToonDX",{slot:args.toondx/(name+".fx") for slot,name in TOON_SLOTS.items()})]
    sources += [(f"BeamMan-{index:02}-{path.stem}",{slot:path for slot in TOON_SLOTS}) for index,path in enumerate(beam)]
    reports = []
    for name,mapping in sources:
        folder = args.output/"surfaces"/name
        folder.mkdir(parents=True,exist_ok=True)
        hashes = {str(path):sha256(path.read_bytes()).hexdigest() for path in set(mapping.values())}
        for slot,path in mapping.items():
            apply(obj,path,MATERIAL,slot=slot,units=.08,light=light)
            assert not inspect_bindings(obj,MATERIAL,slot=slot)
        images = {frame:render_image(scene,folder,f"frame-{frame}",frame) for frame in SURFACE_FRAMES}
        for slot,path in mapping.items():
            assert not apply(obj,path,MATERIAL,slot=slot,units=.08,light=light).rebuilt
        # Rebuild a real original shader, keeping the final graph and pixels stable.
        apply(obj,mapping[1],MATERIAL,slot=1,units=.08,light=light,force_rebuild=True)
        rebuilt = render_image(scene,folder,"rebuilt",60)
        assert np.array_equal(rebuilt,images[60])
        bpy.ops.file.pack_all()
        bpy.context.preferences.filepaths.save_version = 0
        saved = folder/"texture-morphs.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(saved),relative_remap=False)
        scene.frame_set(0)
        bpy.context.view_layer.update()
        for slot in mapping:
            remove(obj,MATERIAL,slot=slot)
        assert snapshot(obj)==before
        assert all(mme_tree_signature(material.node_tree)==signature for material,signature in signatures.items())
        assert all(sha256(Path(path).read_bytes()).hexdigest()==value for path,value in hashes.items())
        reports.append(dict(name=name,slots=len(mapping),files=hashes,blend=str(saved),
                            render_samples=scene.eevee.taa_render_samples,
                            camera_shift=[camera.data.shift_x,camera.data.shift_y],
                            native_pixel_centers=args.native_pixel_centers,
                            morph_mean_differences={frame:float(np.abs(image-images[0]).mean()) for frame,image in images.items()}))
        print("PASS ORIGINAL_TEXTURE_MORPH_SURFACE",name,flush=True)
    # Reopen every saved scene, reimport warm, and compare all selected frames.
    for record in reports:
        bpy.ops.wm.open_mainfile(filepath=record["blend"],load_ui=False)
        scene = bpy.context.scene
        obj = model_object(scene,model)
        folder = Path(record["blend"]).parent
        errors = []
        for frame in SURFACE_FRAMES:
            reference = bpy.data.images.load(str(folder/f"frame-{frame}.exr"),check_existing=False)
            wanted = np.array(reference.pixels[:]).reshape(512,512,4)
            bpy.data.images.remove(reference)
            actual = render_image(scene,folder,f"reopened-{frame}",frame)
            error = float(np.max(np.abs(wanted-actual)))
            assert error==0,(record["name"],frame,error)
            errors.append(error)
        record["reopen_maximum_errors"] = errors
    (args.output/"surface-results.json").write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding="utf-8")
    print("PASS ALL_ORIGINAL_TEXTURE_MORPH_SURFACES",len(reports),"scenes",flush=True)


if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mmd-tools",type=Path,required=True)
    parser.add_argument("--toondx",type=Path,required=True)
    parser.add_argument("--beamman",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--native-pixel-centers",action="store_true",
                        help="Use one Eevee sample and Direct3D 9 pixel centers for native captures with antialiasing off.")
    parser.add_argument("--model",type=Path,help="Read the prepared PMX from its original location to preserve relative texture paths.")
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
