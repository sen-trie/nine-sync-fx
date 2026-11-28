import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_converters import set_scene,add_empty
from ninesync_fx.fx.errors import FxUnsupported
from ninesync_fx.runtime.middle.targets import apply,remove,retarget,RENDER
from ninesync_fx.runtime.middle.raster_targets import CONFIG
from ninesync_fx.runtime.middle.scene_execution import SceneMember


SOURCE = '''texture History:RENDERCOLORTARGET <int Width=16;int Height=16;string Format="A32B32G32R32F";>;
texture Next:RENDERCOLORTARGET <int Width=16;int Height=16;string Format="A32B32G32R32F";>;
texture Mipped:RENDERCOLORTARGET <int Width=16;int Height=16;int MipLevels=0;>;
sampler Previous=sampler_state{Texture=<History>;MinFilter=POINT;MagFilter=POINT;MipFilter=NONE;};
sampler Current=sampler_state{Texture=<Next>;MinFilter=POINT;MagFilter=POINT;MipFilter=NONE;};
float X:CONTROLOBJECT <string name="Pulse.x";string item="X";>;
struct O{float4 p:POSITION;float2 uv:TEXCOORD0;};
O VS(float4 p:POSITION,float2 uv:TEXCOORD0){O o;o.p=p;o.uv=uv;return o;}
float4 Advance(O i):COLOR{return tex2D(Previous,i.uv)+float4(X,0,0,0);}
float4 Copy(O i):COLOR{return tex2D(Current,i.uv);}
technique Main <string MMDPass="object";string Script="RenderColorTarget0=Next;Pass=Advance;RenderColorTarget0=History;Pass=Copy;RenderColorTarget0=;Pass=Copy;";>{
pass Advance <string Script="Draw=Buffer;";>{ZEnable=false;ZWriteEnable=false;VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 Advance();}
pass Copy <string Script="Draw=Buffer;";>{VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 Copy();}}
'''


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    scene=set_scene();scene.render.resolution_x=scene.render.resolution_y=16
    mesh=bpy.data.meshes.new('Source');mesh.from_pydata([(-1,-1,0),(1,-1,0),(0,1,0)],[],[(0,1,2)])
    obj=bpy.data.objects.new('Source',mesh);scene.collection.objects.link(obj)
    material=bpy.data.materials.new('Artist');mesh.materials.append(material)
    control=add_empty(scene,'Actual pulse',(.25,0,0));control['mmd_bridge_accessory']=True
    fx=args.output/'history.fx';fx.write_text(SOURCE)
    members=(SceneMember(obj,'source.pmx',fx,bindings={'Pulse.x':control}),)
    before=(tuple(mesh.vertices[0].co),tuple(m.name for m in obj.data.materials),len(obj.modifiers))
    result=apply(obj,fx,RENDER,scene=scene,units=1,scene_members=members)
    assert result.core.executors[next(iter(result.core.executors))].targets['History'].pixels[0,0,0]==.25
    result=apply(obj,fx,RENDER,scene=scene,units=1)
    assert result.core.executors[next(iter(result.core.executors))].targets['History'].pixels[0,0,0]==.5
    stamp=fx.stat()
    fx.write_text(SOURCE.replace('float4(X,0,0,0)','float4(0,0,0,0)'))
    os.utime(fx,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
    refreshed=apply(obj,fx,RENDER,scene=scene,units=1)
    assert refreshed.core.executors[next(iter(refreshed.core.executors))].targets['History'].pixels[0,0,0]==0
    fx.write_text(SOURCE);os.utime(fx,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
    apply(obj,fx,RENDER,scene=scene,units=1)
    old_image=obj[CONFIG]['image'];old_state=obj[CONFIG]['state'];old_pixels=np.array(old_image.pixels[:])
    bad=args.output/'bad.fx';bad.write_text(SOURCE.replace('ZEnable=false;','ZEnable=false;FogEnable=true;'))
    try:apply(obj,bad,RENDER,scene=scene,units=1,scene_members=(replace(members[0],fx=bad),),reset_targets=True)
    except FxUnsupported as error:assert 'fogenable' in str(error).lower(),str(error)
    else:raise AssertionError('Unsupported pass published output')
    assert obj[CONFIG]['image']==old_image and obj[CONFIG]['state']==old_state
    assert not obj[CONFIG]['reset_targets']
    assert np.array_equal(np.array(old_image.pixels[:]),old_pixels)
    scene.render.resolution_x=scene.render.resolution_y=32
    result=apply(obj,fx,RENDER,scene=scene,units=1)
    assert tuple(result.destination.size)==(32,32)
    assert result.core.executors[next(iter(result.core.executors))].targets['History'].pixels[0,0,0]==.25
    replacement=add_empty(scene,'Rebound pulse',(.125,0,0));replacement['mmd_bridge_accessory']=True
    try:retarget(obj,RENDER,'Missing.x',replacement,scene=scene)
    except FxUnsupported as error:assert 'reads controller' in str(error)
    else:raise AssertionError('An unused controller was reported as retargeted')
    result=retarget(obj,RENDER,'Pulse.x',replacement,scene=scene)
    assert result.core.executors[next(iter(result.core.executors))].targets['History'].pixels[0,0,0]==.125
    image_name=result.destination.name;expected=np.array(result.destination.pixels[:])
    file=args.output/'render-apply.blend'
    bpy.context.preferences.filepaths.save_version=0
    bpy.ops.wm.save_as_mainfile(filepath=str(file))
    bpy.ops.wm.open_mainfile(filepath=str(file),load_ui=False)
    scene=bpy.context.scene;obj=scene.objects['Source']
    assert np.array_equal(np.array(bpy.data.images[image_name].pixels[:]),expected)
    result=apply(obj,fx,RENDER,scene=scene,units=1)
    assert result.core.executors[next(iter(result.core.executors))].targets['History'].pixels[0,0,0]==.25
    result=apply(obj,fx,RENDER,scene=scene,units=1,reset_targets=True)
    assert obj[CONFIG]['reset_targets']
    assert result.core.executors[next(iter(result.core.executors))].targets['History'].pixels[0,0,0]==.125
    result=apply(obj,fx,RENDER,scene=scene,units=1)
    assert result.core.executors[next(iter(result.core.executors))].targets['History'].pixels[0,0,0]==.125
    reset_file=args.output/'render-reset.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(reset_file))
    bpy.ops.wm.open_mainfile(filepath=str(reset_file),load_ui=False)
    scene=bpy.context.scene;obj=scene.objects['Source']
    result=apply(obj,fx,RENDER,scene=scene,units=1)
    assert obj[CONFIG]['reset_targets']
    assert result.core.executors[next(iter(result.core.executors))].targets['History'].pixels[0,0,0]==.125
    result=apply(obj,fx,RENDER,scene=scene,units=1,reset_targets=False)
    assert not obj[CONFIG]['reset_targets']
    assert result.core.executors[next(iter(result.core.executors))].targets['History'].pixels[0,0,0]==.25
    assert before==(tuple(obj.data.vertices[0].co),tuple(m.name for m in obj.data.materials),len(obj.modifiers))
    remove(obj,RENDER,scene=scene)
    assert CONFIG not in obj
    assert not [i for i in bpy.data.images if i.get('ninesync_middle_render_owner')==obj]
    assert not [i for i in bpy.data.texts if i.get('ninesync_middle_render_owner')==obj]
    report=dict(status='PASS',checks=['normal apply and explicit update history','failed apply preserves packed image and exact history',
        'resize resets incompatible history','explicit controller mapping and retarget','save/reopen continues exact feedback state',
        'source slots/mesh/modifiers preserved and removal cleans generated data','same-stamp source edits refresh shader and reset incompatible history',
        'per-update target reset survives reopen and retained mode can resume from that update'])
    (args.output/'results.json').write_text(json.dumps(report,indent=2))
    print('PASS ORDERED_RENDER_APPLY',len(report['checks']),'cases',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
