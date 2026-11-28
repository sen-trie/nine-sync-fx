"""Original Grass multipass and DefaultEffect scene draw on actual Marisa."""
import argparse,json,math,sys
from pathlib import Path
from hashlib import sha256
from time import perf_counter
import bpy,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer,model_object,resolve_textures,snapshot
from t_middle_converters import set_scene
from ninesync_fx.runtime.middle.scene_execution import SceneMember,FxSceneExecution
from ninesync_fx.nodes.material_compile import mme_tree_signature


def run(args):
    import faulthandler
    args.output.mkdir(parents=True,exist_ok=True)
    pmx,importer=load_importer(args.mmd_tools)
    scene=set_scene();scene.render.resolution_x=scene.render.resolution_y=256;scene.render.fps=30
    scene.camera.location=(0,-45*.08,10*.08);scene.camera.rotation_euler=(math.pi/2,0,0)
    scene.camera.data.type='PERSP';scene.camera.data.sensor_fit='VERTICAL'
    scene.camera.data.lens=scene.camera.data.sensor_height/(2*math.tan(math.radians(30)/2))
    model=pmx.load(str(args.model));resolve_textures(model,args.model,args.model.parent/'tex')
    importer().execute(pmx=model,scale=.08,types={'MESH','ARMATURE','MORPHS'},fix_bone_order=False)
    obj=model_object(scene,model);rig=obj.find_armature()
    for frame,x in ((0,0),(10,.08),(20,-.08)):
        rig.pose.bones['\u30bb\u30f3\u30bf\u30fc'].location.x=x
        rig.pose.bones['\u30bb\u30f3\u30bf\u30fc'].keyframe_insert('location',frame=frame)
    if args.sdef:
        from mmd_tools.core.sdef import FnSDEF
        assert FnSDEF.bind(obj,bulk_update=True,use_skip=False)
    from harness_fx import mb
    from ninesync_fx.accessories.standalone_import import import_plain_accessory
    owner=import_plain_accessory(bpy.context,str(args.grass.with_suffix('.x')),.08)
    owner['mmd_bridge_accessory']=True;owner.scale=(.1,)*3
    grass=next(child for child in owner.children_recursive if child.type=='MESH')
    scene.frame_set(0);bpy.context.view_layer.update()
    originals={source:snapshot(source) for source in (obj,grass)}
    graphs={material:mme_tree_signature(material.node_tree) for source in (obj,grass) for material in source.data.materials if material and material.node_tree}
    counts={name:len(getattr(bpy.data,name)) for name in ('objects','meshes','materials','node_groups')}
    hashes={str(path):sha256(path.read_bytes()).hexdigest() for path in (args.model,args.grass,args.grass.with_suffix('.x'),args.grass.parent/'HitObject.fx')}
    engine=FxSceneExecution(scene,[SceneMember(obj,args.model.name,main=False),SceneMember(grass,'grass.x',args.grass,owner)])
    if args.sampler_mip != 'ORIGINAL':
        from copy import deepcopy
        from ninesync_fx.fx.loader import fx_parse_file
        program=deepcopy(fx_parse_file(str(args.grass)))
        program.globals['ObjTexSampler'].init[1]['mipfilter']=[args.sampler_mip]
        engine.programs[args.grass.resolve()]=program
    reports=[];reference=None
    for update,frame in enumerate(args.frames):
        faulthandler.dump_traceback_later(90,repeat=True)
        start=perf_counter()
        if args.application_path:
            from ninesync_fx.runtime.middle.targets import apply,RENDER
            scene.frame_set(frame)
            result=apply(grass,args.grass,RENDER,scene=scene,units=.08,scene_members=engine.members)
            engine=result.core;pixels=engine.color.pixels.copy()
            scene.frame_set(0)
        else:pixels=engine.render(frame)
        elapsed=perf_counter()-start
        assert scene.frame_current==0 and np.isfinite(pixels).all()
        assert np.count_nonzero(pixels[:,:,:3])>100
        main=next(runtime for key,runtime in engine.executors.items() if key[0]=='main' and len(key)==3)
        assert len(main.last_stats)==9
        assert np.ptp(main.targets['HeightTex1'].pixels[:,:,0])>.1,'Original height pass was not executed'
        assert np.all(main.targets['NormalTex'].pixels[:,:,1]==-1),'Signed normal target was clamped'
        assert any(row['offscreen_owner']==owner.name for row in engine.last_trace)
        controls=[row['controls'] for row in engine.last_trace if row['offscreen_owner']]
        assert controls and all('OwnerScale' in row and 'MirrorWorldMatrix' in row for row in controls)
        assert all(np.max(np.abs(np.array(value['OwnerScale'])-1))<2e-6 for value in controls)
        update_path=args.output/f'update-{update:02d}-frame-{frame}'
        update_path.mkdir()
        for name,target in main.targets.items():
            np.save(args.output/f'{name or "main"}-{frame}.npy',target.pixels)
            np.save(update_path/f'{name or "main"}.npy',target.pixels)
        for key,runtime in engine.executors.items():
            for name,target in runtime.depths.items():
                tag='main' if len(key)==3 else 'offscreen-'+str(key[4])
                np.save(update_path/f'{tag}-depth-{name or "default"}.npy',target.values)
        if frame==0:
            if reference is not None:assert np.array_equal(reference,pixels),'Original Grass did not reset at time zero'
            reference=pixels.copy()
        for source,before in originals.items():assert snapshot(source)==before
        assert graphs=={material:mme_tree_signature(material.node_tree) for material in graphs}
        assert counts=={name:len(getattr(bpy.data,name)) for name in counts}
        reports.append(dict(update=update,frame=frame,seconds=elapsed,passes=main.last_stats,trace=engine.last_trace))
        print('PASS ORIGINAL_GRASS_SCENE_FRAME',frame,'seconds',round(elapsed,3),flush=True)
    # Float publication must survive a saved Blender file without quantization.
    published=engine.image('Original Grass - executed FX scene')
    pixels=np.array(published.pixels[:],dtype=np.float32)
    image_name=published.name;file=args.output/'original-grass-scene.blend'
    bpy.context.preferences.filepaths.save_version=0;bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(file),relative_remap=False)
    bpy.ops.wm.open_mainfile(filepath=str(file),load_ui=False)
    assert np.array_equal(pixels,np.array(bpy.data.images[image_name].pixels[:],dtype=np.float32))
    scene=bpy.context.scene
    for path,digest in hashes.items():assert sha256(Path(path).read_bytes()).hexdigest()==digest
    (args.output/'results.json').write_text(json.dumps(dict(checks=reports,source_hashes=hashes,
        actual_original_FX=True,actual_original_X=True,source_preserved=True,packed_image_exact_reopen=True,
        sdef=args.sdef,sampler_mip=args.sampler_mip,application_path=args.application_path,
        camera=dict(location=list(scene.camera.location),rotation=list(scene.camera.rotation_euler),
                    lens=scene.camera.data.lens,sensor_height=scene.camera.data.sensor_height,
                    sensor_fit=scene.camera.data.sensor_fit,type=scene.camera.data.type),
        resolution=[scene.render.resolution_x,scene.render.resolution_y],frames=args.frames,
        blender_version=bpy.app.version_string,native_comparison_pending=True,feedback_policy='One explicit render update per call; native paint/seek lifetime remains unverified.'),indent=2))
    faulthandler.cancel_dump_traceback_later()
    print('PASS ORIGINAL_GRASS_SCENE',len(reports),'updates',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('model','grass','mmd-tools','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--frames',type=int,nargs='+',default=[0,10,20,0]);parser.add_argument('--sdef',action='store_true');parser.add_argument('--sampler-mip',choices=('ORIGINAL','NONE','LINEAR'),default='ORIGINAL')
    parser.add_argument('--application-path',action='store_true')
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
