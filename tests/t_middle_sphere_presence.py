"""Real PMX absent/present sphere flags against exported native MMD probes."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer,model_object,snapshot,render_image,position_camera
from t_middle_converters import set_scene
from ninesync_fx.runtime.middle.targets import apply,remove,MATERIAL
from ninesync_fx.runtime.middle.compiler import has_sphere_map


def run(args):
    pmx,importer=load_importer(args.mmd_tools)
    model=pmx.load(str(args.model));digest=sha256(args.model.read_bytes()).hexdigest()
    scene=set_scene();scene.render.engine='BLENDER_EEVEE';scene.render.resolution_x=scene.render.resolution_y=512
    importer().execute(pmx=model,scale=.08,types={'MESH','ARMATURE','MORPHS'},fix_bone_order=False)
    obj=model_object(scene,model);position_camera(scene,obj);before=snapshot(obj)
    groups=len(bpy.data.node_groups);originals={i:obj.material_slots[i].material for i in (9,30)}
    assert model.materials[9].sphere_texture==-1 and model.materials[9].sphere_texture_mode==2
    assert not has_sphere_map(originals[9]) and has_sphere_map(originals[30])
    for slot in (9,30):apply(obj,args.fx,MATERIAL,slot=slot,units=.08)
    args.output.mkdir(parents=True,exist_ok=True)
    image=render_image(scene,args.output,'sphere-presence',0)
    checks=[]
    for color in ((0,0,1),(1,0,1)):
        count=int((np.max(np.abs(image[:,:,:3]-color),axis=2)<1e-5).sum())
        assert count>100,count
        checks.append(dict(linear_rgb=color,blender_matching_pixels=count))
    if args.native:
        native=bpy.data.images.load(str(args.native),check_existing=False);native.colorspace_settings.name='Non-Color'
        values=np.array(native.pixels[:]).reshape(-1,4)
        for check in checks:
            count=int((np.max(np.abs(values[:,:3]-check['linear_rgb']),axis=1)<1e-5).sum())
            assert count>1000,count
            check['native_matching_pixels']=count
        bpy.data.images.remove(native)
    bpy.context.preferences.filepaths.save_version=0
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output/'sphere-presence.blend'),relative_remap=False)
    for slot in (9,30):remove(obj,MATERIAL,slot=slot)
    assert snapshot(obj)==before and len(bpy.data.node_groups)==groups
    assert all(obj.material_slots[i].material==material for i,material in originals.items())
    assert sha256(args.model.read_bytes()).hexdigest()==digest
    (args.output/'results.json').write_text(json.dumps(dict(checks=checks,source=str(args.model),source_sha256=digest,
        fx_sha256=sha256(args.fx.read_bytes()).hexdigest(),source_preserved=True,rollback_verified=True),indent=2))
    print('PASS SPHERE_PRESENCE',checks,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for key in ('model','fx','mmd-tools','output'):parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--native',type=Path)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
