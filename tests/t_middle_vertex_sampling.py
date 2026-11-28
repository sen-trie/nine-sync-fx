"""Explicit vertex mip views, stage defaults and artist texture edits."""
import argparse
import json
from pathlib import Path
import sys
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_converters import set_scene
from ninesync_fx.fx.errors import FxUnsupported
from ninesync_fx.runtime.middle.targets import apply,remove,DEFORM
from ninesync_fx.runtime.middle.images import views_current

SOURCE="""float4x4 WVP:WORLDVIEWPROJECTION;
texture T:MATERIALTEXTURE;
sampler S=sampler_state {Texture=<T>;STATES};
float4 VS(float4 p:POSITION):POSITION {p.x+=tex2Dlod(S,float4(.0625,.0625,0,LEVEL)).r;return mul(p,WVP);}
float4 PS():COLOR{return 1;}
technique Main {pass Draw {VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 PS();}}
"""

def read(obj):
    evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=evaluated.to_mesh()
    try:return np.array([v.co[:] for v in mesh.vertices])
    finally:evaluated.to_mesh_clear()

def run(args):
    args.output.mkdir(parents=True,exist_ok=True);scene=set_scene()
    mesh=bpy.data.meshes.new('Artist source');mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
    obj=bpy.data.objects.new('Vertex sampling',mesh);scene.collection.objects.link(obj)
    material=bpy.data.materials.new('Artist material');mesh.materials.append(material)
    image=bpy.data.images.new('Artist texture',8,8,float_buffer=True)
    image.colorspace_settings.name='Non-Color';pixels=np.ones((8,8,4),dtype=np.float32)
    row,column=np.indices((8,8));pixels[...,:3]=((7-row+column)%2)[...,None];image.pixels.foreach_set(pixels.ravel());image.update()
    baseline=read(obj);reports=[]
    cases=[('default',1,'',0),('linear',1,'MinFilter=LINEAR;MagFilter=LINEAR;MipFilter=LINEAR;',.5),
           ('fractional',.25,'MinFilter=LINEAR;MagFilter=LINEAR;MipFilter=LINEAR;',.125),
           ('nearest',.6,'MinFilter=POINT;MagFilter=POINT;MipFilter=POINT;',.5),
           ('none',3,'MinFilter=POINT;MagFilter=POINT;MipFilter=NONE;',0),
           ('aggregate-none',3,'Filter=NONE;',0)]
    for name,level,states,expected in cases:
        fx=args.output/(name+'.fx');fx.write_text(SOURCE.replace('STATES',states).replace('LEVEL',str(level)))
        result=apply(obj,fx,DEFORM,image=image)
        actual=read(obj)-baseline;np.testing.assert_allclose(actual[:,0],expected,atol=2e-6)
        np.testing.assert_allclose(actual[:,1:],0,atol=2e-6)
        reports.append(dict(case=name,expected_displacement=expected))
    fx=args.output/'linear.fx';result=apply(obj,fx,DEFORM,image=image)
    assert views_current([result.core]);old=result.core
    pixels[...,:3]=.25;image.pixels.foreach_set(pixels.ravel());image.update()
    assert not views_current([old])
    result=apply(obj,fx,DEFORM,image=image);assert result.rebuilt
    np.testing.assert_allclose((read(obj)-baseline)[:,0],.25,atol=2e-6)
    invalid=args.output/'dynamic.fx';invalid.write_text('float clock:TIME;'+SOURCE.replace('STATES','').replace('LEVEL','clock'))
    old=result.core
    try:apply(obj,invalid,DEFORM,image=image)
    except FxUnsupported as error:assert 'finite uniform constant' in str(error),str(error)
    else:raise AssertionError('Unsupported dynamic vertex LOD was accepted')
    assert old in bpy.data.node_groups[:]
    expected=read(obj);obj_name=obj.name
    bpy.context.preferences.filepaths.save_version=0;bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output/'sampling.blend'),relative_remap=False)
    remove(obj,DEFORM);np.testing.assert_array_equal(read(obj),baseline)
    bpy.ops.wm.open_mainfile(filepath=str(args.output/'sampling.blend'),load_ui=False)
    obj=bpy.context.scene.objects[obj_name];np.testing.assert_array_equal(read(obj),expected)
    remove(obj,DEFORM)
    (args.output/'results.json').write_text(json.dumps(dict(status='PASS',checks=reports,artist_edit_rebuild=True,
        dynamic_lod_rollback=True,exact_reopen=True,remove_restores_mesh=True),indent=2))
    print('PASS VERTEX_SAMPLING',len(reports),'cases',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
