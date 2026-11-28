import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_converters import set_scene
from harness_fx import mb
from ninesync_fx.accessories.standalone_import import import_plain_accessory
from ninesync_fx.fx.errors import FxUnsupported
from ninesync_fx.fx.loader import fx_parse_file
from ninesync_fx.fx.interp.executor import FxExec
from ninesync_fx.fx.interp.numpy_backend import FxNumpyBackend
from ninesync_fx.fx.textures import FxTextureStore
from ninesync_fx.runtime.middle.blender_raster import make_state,snapshot_geometry
from ninesync_fx.runtime.middle.render_execution import RasterEnvironment


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    reports=[]
    for case in ('ThunderLineTgt','ThunderLineTgtStatic'):
        directory=args.root/case;fx=directory/(case+'.fx');companion=directory/'tgt.x'
        hashes={str(p):sha256(p.read_bytes()).hexdigest() for p in (fx,companion,fx.with_suffix('.x'))}
        scene=set_scene();scene.render.resolution_x=scene.render.resolution_y=32
        owner=import_plain_accessory(bpy.context,str(fx.with_suffix('.x')),.08)
        obj=next(c for c in owner.children_recursive if c.type=='MESH')
        root=import_plain_accessory(bpy.context,str(companion),.08)
        root['ninesync_mme_name']='tgt.x';root['mmd_bridge_accessory']=True
        program=fx_parse_file(str(fx));stream=snapshot_geometry(scene,obj,.08)
        checks=[]
        for frame,xyz in [(0,(0,0,0)),(10,(1,3,-2)),(20,(-1,-3,2)),(0,(0,0,0))]:
            scene.frame_set(frame);root.location=np.array(xyz)[[0,2,1]]*.08;bpy.context.view_layer.update()
            state,images,technique=make_state(scene,obj,stream,0,program,.08,bindings={'tgt.x':root})
            ex=FxExec(program,FxNumpyBackend(np.float32),RasterEnvironment(program,state,FxTextureStore(program,lambda path:None),{},images))
            value=np.array(ex.global_value('TgtPos').c)
            np.testing.assert_allclose(value,xyz,atol=1e-6)
            checks.append(dict(frame=frame,original_parameter='TgtPos',value=value.tolist()))
        bpy.data.objects.remove(root,do_unlink=True)
        try:make_state(scene,obj,stream,0,program,.08,bindings={'tgt.x':None})
        except FxUnsupported:pass
        else:raise AssertionError('Removed companion still resolved')
        rebound=import_plain_accessory(bpy.context,str(companion),.08);rebound['ninesync_mme_name']='tgt.x'
        state,_,_=make_state(scene,obj,stream,0,program,.08,bindings={'tgt.x':rebound})
        np.testing.assert_allclose(state.controls['TgtPos'],[0,0,0],atol=1e-6)
        assert all(sha256(Path(path).read_bytes()).hexdigest()==digest for path,digest in hashes.items())
        reports.append(dict(effect=str(fx),companion=str(companion),hashes=hashes,checks=checks,remove_and_rebind=True,
                            complete_original_draw_verified=False))
    (args.output/'results.json').write_text(json.dumps(dict(status='PASS',cases=reports),indent=2))
    print('PASS ORIGINAL_TGT_COMPANIONS',len(reports),'original declarations with animation, removal and rebind',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('root','output'):parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
