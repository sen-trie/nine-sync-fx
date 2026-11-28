"""Run original Grass HitObject on evaluated real Marisa geometry into R16F."""
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
from time import perf_counter

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer,model_object,snapshot,resolve_textures
from t_middle_converters import set_scene
from ninesync_fx.fx.loader import fx_parse_file
from ninesync_fx.runtime.middle.blender_raster import snapshot_geometry,make_state,texture_loader
from ninesync_fx.runtime.middle.render_execution import FxRenderExecution
from ninesync_fx.runtime.middle.raster import ColorTarget,RasterState
from ninesync_fx.nodes.material_compile import mme_tree_signature


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    pmx,importer=load_importer(args.mmd_tools)
    scene=set_scene();scene.render.fps=30;scene.render.resolution_x=scene.render.resolution_y=512
    model=pmx.load(str(args.model));resolve_textures(model,args.model,args.model.parent/'tex')
    importer().execute(pmx=model,scale=.08,types={'MESH','ARMATURE','MORPHS'},fix_bone_order=False)
    obj=model_object(scene,model)
    scene.camera.location=(0,-45*.08,10*.08);scene.camera.rotation_euler=(math.pi/2,0,0)
    scene.camera.data.type='PERSP';scene.camera.data.sensor_fit='VERTICAL'
    scene.camera.data.lens=scene.camera.data.sensor_height/(2*math.tan(math.radians(30)/2))
    rig=obj.find_armature();bone=rig.pose.bones['\u30bb\u30f3\u30bf\u30fc']
    for frame,x in ((0,0),(10,.08),(20,-.08)):
        bone.location.x=x;bone.keyframe_insert('location',frame=frame)
    from harness_fx import mb
    from ninesync_fx.accessories.standalone_import import import_plain_accessory
    owner=import_plain_accessory(bpy.context,str(args.accessory),.08)
    owner['mmd_bridge_accessory']=True;owner.scale=(.1,)*3
    for frame,scale,x in ((0,.1,0),(10,.15,.04),(20,.1,-.04)):
        owner.scale=(scale,)*3;owner.location.x=x
        owner.keyframe_insert('scale',frame=frame);owner.keyframe_insert('location',frame=frame)
    scene.frame_set(0);bpy.context.view_layer.update()
    before=snapshot(obj);graphs={slot.material.name:mme_tree_signature(slot.material.node_tree) for slot in obj.material_slots if slot.material}
    counts={name:len(getattr(bpy.data,name)) for name in ('objects','meshes','materials','node_groups')}
    hashes={str(path):sha256(path.read_bytes()).hexdigest() for path in (args.model,args.fx,args.accessory)}
    program=fx_parse_file(str(args.fx));reports=[];frames={}
    for frame in (0,10,20,0):
        scene.frame_set(frame);bpy.context.view_layer.update();start=perf_counter()
        stream=snapshot_geometry(scene,obj,.08)
        runtime=FxRenderExecution(program,512,512,texture_loader)
        runtime.targets['']=ColorTarget(512,512,'R16F');runtime.targets[''].clear([0,0,0,1])
        runtime.depths[''].clear(1)
        states=[]
        for slot in sorted(set(stream.subsets)):
            state,images,technique=make_state(scene,obj,stream,int(slot),program,owner=owner)
            # The diagnostic explicitly renders both faces; native comparison
            # will separately establish the inherited material cull policy.
            runtime.execute(stream.subset(slot),state,subset=int(slot),images=images,
                            initial_state=RasterState(cull='NONE'),technique=technique)
            states.append(dict(slot=int(slot),controls=state.controls,stats=runtime.last_stats))
        pixels=runtime.targets[''].pixels
        assert np.count_nonzero(pixels[:,:,0]>.01)>100
        assert np.isfinite(pixels).all() and np.all(pixels[:,:,1:3]==0) and np.all(pixels[:,:,3]==1)
        if frame in frames:assert np.array_equal(pixels,frames[frame]),'Repeated frame changed numeric target'
        frames[frame]=pixels.copy();np.save(args.output/f'hit-{frame}.npy',pixels)
        assert snapshot(obj)==before
        assert graphs=={slot.material.name:mme_tree_signature(slot.material.node_tree) for slot in obj.material_slots if slot.material}
        assert counts=={name:len(getattr(bpy.data,name)) for name in counts}
        reports.append(dict(frame=frame,seconds=perf_counter()-start,positive_pixels=int(np.count_nonzero(pixels[:,:,0]>.01)),
                            minimum=float(pixels[:,:,0].min()),maximum=float(pixels[:,:,0].max()),states=states))
    assert not np.array_equal(frames[0],frames[10]) and not np.array_equal(frames[0],frames[20])
    scene.frame_set(0)
    image=bpy.data.images.new('Original Grass HitRT - direct R16F',512,512,float_buffer=True)
    image.colorspace_settings.name='Non-Color';image.pixels.foreach_set(frames[0][::-1].reshape(-1));image.pack()
    bpy.context.preferences.filepaths.save_version=0
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output/'grass-hit-raster.blend'),relative_remap=False)
    for path,digest in hashes.items():assert sha256(Path(path).read_bytes()).hexdigest()==digest
    (args.output/'model-results.json').write_text(json.dumps(dict(source_hashes=hashes,checks=reports,source_preserved=True,
        native_comparison_pending=True,scope='Actual original HitObject.fx, evaluated Marisa, independent animated real X owner, R16F draw. Full original Grass execution remains pending.'),indent=2))
    print('PASS ORIGINAL_GRASS_HIT_RASTER',len(reports),'real-model states',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for key in ('model','fx','accessory','mmd-tools','output'):parser.add_argument('--'+key,type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
