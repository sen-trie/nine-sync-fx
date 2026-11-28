"""Native absent-morph zero and live positive morphs on a real PMX model."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer,model_object,resolve_textures,snapshot,position_camera,render_image
from t_middle_converters import set_scene
from ninesync_fx.runtime.middle.targets import apply,remove,inspect_bindings,MATERIAL
from ninesync_fx.runtime.middle.controllers import resolve,AbsentMorph


def decode(values):
    values=np.array(values)
    return np.where(values<=.04045,values/12.92,((values+.055)/1.055)**2.4)


def run(args):
    pmx,importer=load_importer(args.mmd_tools)
    args.output.mkdir(parents=True,exist_ok=True)
    scene=set_scene();scene.render.engine='BLENDER_EEVEE';scene.render.resolution_x=scene.render.resolution_y=384
    scene.world.use_nodes=True
    scene.world.node_tree.nodes['Background'].inputs['Color'].default_value=(0,0,0,1)
    digest=sha256(args.model.read_bytes()).hexdigest();model=pmx.load(str(args.model))
    resolve_textures(model,args.model,args.model.parent/'tex')
    importer().execute(pmx=model,scale=.08,types={'MESH','ARMATURE','MORPHS'},fix_bone_order=False)
    obj=model_object(scene,model);position_camera(scene,obj);baseline=snapshot(obj)
    groups=len(bpy.data.node_groups);used=sorted({p.material_index for p in obj.data.polygons})
    key=obj.data.shape_keys.key_blocks['\u307e\u3070\u305f\u304d']
    for frame,value in ((0,0),(10,.25),(20,1)):
        key.value=value;key.keyframe_insert('value',frame=frame)
    fx=args.native/'native-missing-initialized.fx'
    for slot in used:
        result=apply(obj,fx,MATERIAL,slot=slot,units=.08)
        assert any('reads zero' in message for message in result.warnings)
        assert not inspect_bindings(obj,MATERIAL,slot=slot)
    reports=[];reference=None
    for frame,weight in ((0,0),(10,.25),(20,1)):
        image=render_image(scene,args.output,f'frame-{frame}',frame)
        wanted=(.25,.25+.5*weight,.25)
        # EEVEE's working render buffer is half precision even with EXR32 output.
        count=int((np.max(np.abs(image[:,:,:3]-decode(wanted)),axis=2)<2e-4).sum())
        assert count>2000,count
        native=bpy.data.images.load(str(args.native/f'native-{frame}.png'),check_existing=False)
        native.colorspace_settings.name='Non-Color';pixels=np.array(native.pixels[:]).reshape(-1,4)
        native_count=int((np.max(np.abs(pixels[:,:3]-np.rint(np.array(wanted)*255)/255),axis=1)<1/255).sum())
        assert native_count>10000,native_count
        bpy.data.images.remove(native)
        reports.append(dict(frame=frame,weight=weight,encoded_rgb=wanted,blender_pixels=count,native_pixels=native_count))
        if frame==20:reference=image
    # The native probe also overrides an explicit .37 initializer with zero.
    native=bpy.data.images.load(str(args.native/'native-initialized-10.png'),check_existing=False)
    native.colorspace_settings.name='Non-Color';pixels=np.array(native.pixels[:]).reshape(-1,4)
    assert ((np.abs(pixels[:,0]-64/255)<1e-6)&(np.abs(pixels[:,1]-96/255)<1e-6)).sum()>10000
    bpy.data.images.remove(native)
    slot=used[0];missing='__ninesync_absent_morph__'
    assert not apply(obj,fx,MATERIAL,slot=slot,units=.08).rebuilt
    new_key=obj.shape_key_add(name=missing);new_key.value=.6
    assert any('now present' in s for s in inspect_bindings(obj,MATERIAL,slot=slot))
    assert apply(obj,fx,MATERIAL,slot=slot,units=.08).rebuilt
    target,sources=resolve(scene,obj,'(self)',missing,1,{},.08)
    assert target==obj and not isinstance(sources[0],AbsentMorph)
    obj.shape_key_remove(new_key)
    assert inspect_bindings(obj,MATERIAL,slot=slot)
    assert apply(obj,fx,MATERIAL,slot=slot,units=.08).rebuilt
    assert isinstance(resolve(scene,obj,'(self)',missing,1,{},.08)[1][0],AbsentMorph)
    assert snapshot(obj)==baseline
    bpy.context.preferences.filepaths.save_version=0
    saved=args.output/'missing-morphs.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved),relative_remap=False)
    bpy.ops.wm.open_mainfile(filepath=str(saved),load_ui=False)
    scene=bpy.context.scene;obj=model_object(scene,model);baseline=snapshot(obj)
    reopened=render_image(scene,args.output,'reopened',20);assert np.array_equal(reference,reopened)
    for slot in used:remove(obj,MATERIAL,slot=slot)
    assert snapshot(obj)==baseline and len(bpy.data.node_groups)==groups
    assert sha256(args.model.read_bytes()).hexdigest()==digest
    (args.output/'results.json').write_text(json.dumps(dict(model=str(args.model),source_sha256=digest,
        fx_sha256=sha256(fx.read_bytes()).hexdigest(),checks=reports,initialized_missing_zero=True,
        newly_present_rebuild=True,removed_morph_rebuild=True,exact_reopen=True,source_preserved=True),indent=2))
    print('PASS NATIVE_MISSING_MORPH',len(reports),'weights; add/remove/reimport/reopen',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for key in ('model','mmd-tools','output','native'):parser.add_argument('--'+key,type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
