"""Actual shared FX parsing/interpreting through complete ordered raster draws."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
import paths
paths.prepare('ninesync_fx')
from ninesync_fx.fx.loader import fx_parse_file
from ninesync_fx.fx.textures import FxImage
from ninesync_fx.fx.semantics import FxSceneState
from ninesync_fx.fx.errors import FxUnsupported
from ninesync_fx.runtime.middle.render_execution import FxRenderExecution,GeometryStream
from ninesync_fx.runtime.middle.raster import ColorTarget


COMMON='''
float4x4 WVP:WORLDVIEWPROJECTION;
struct O { float4 p:POSITION; float2 uv:TEXCOORD0; };
O VS(float4 p:POSITION,float2 uv:TEXCOORD0) { O o;o.p=p;o.uv=uv;return o; }
float4 Red(O i):COLOR { return float4(1,0,0,1); }
float4 Blue(O i):COLOR { return float4(0,0,1,1); }
'''
PASSES='''
pass R { ZEnable=false; AlphaBlendEnable=true; SrcBlend=ONE; DestBlend=ONE;
 VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 Red(); }
pass B { VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 Blue(); }
'''


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    geometry=GeometryStream({'POSITION0':np.array([[-.8,-.8,.5,1],[.8,-.8,.5,1],[0,.8,.5,1]],dtype=np.float32),
                             'TEXCOORD0':np.array([[0,0],[1,0],[.5,1]],dtype=np.float32)},np.array([[0,1,2]]))
    state=FxSceneState();state.viewport=(64,64)
    reports=[]
    def effect(name,text):
        path=args.output/(name+'.fx');path.write_text(text)
        return FxRenderExecution(fx_parse_file(str(path)),64,64)

    # A shader pass can follow another geometry pass; raster states persist.
    runtime=effect('two-draws',COMMON+'technique Main {'+PASSES+'}')
    target=runtime.execute(geometry,state)
    assert np.array_equal(np.rint(target.pixels[32,32]*255),[255,0,255,255])
    assert [r['pass_name'] for r in runtime.last_stats]==['R','B']
    reports.append('multiple geometry draws and inherited pass states')

    text=COMMON+'''float4 ClearColor={.07,.12,.35,1};float ClearDepth=1;
    technique Main < string Script="Pass=R;ClearSetColor=ClearColor;Clear=Color;ClearSetDepth=ClearDepth;Clear=Depth;Pass=B;"; > {'''+PASSES+'}'
    runtime=effect('interleaved-clear',text)
    target=runtime.execute(geometry,state)
    assert np.array_equal(np.rint(target.pixels[32,32]*255),[17,30,255,255])
    assert np.array_equal(np.rint(target.pixels[0,0]*255),[17,30,89,255])
    reports.append('color and depth clears retain execution order')

    text=COMMON+'''int Copies=3; int Index;
    float4 Count(O i):COLOR {return float4(.1*(Index+1),0,0,0);}
    technique Main < string Script="LoopByCount=Copies;LoopGetIndex=Index;Pass=C;LoopEnd=;"; > {
     pass C { ZEnable=false;AlphaBlendEnable=true;SrcBlend=ONE;DestBlend=ONE;
      VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 Count(); }
    }'''
    runtime=effect('loop-draws',text);runtime.targets['']=ColorTarget(64,64,'A32B32G32R32F')
    target=runtime.execute(geometry,state)
    assert abs(target.pixels[32,32,0]-.6)<1e-6,target.pixels[32,32]
    assert len(runtime.last_stats)==3
    reports.append('concrete loop values and repeated geometry')

    text=COMMON+'''texture Data:RENDERCOLORTARGET <int Width=64;int Height=64;string Format="R16F";>;
    sampler Read=sampler_state{Texture=<Data>;MinFilter=POINT;MagFilter=POINT;MipFilter=NONE;AddressU=CLAMP;AddressV=CLAMP;};
    float4 Numeric(O i):COLOR {return float4(2.125,5,6,1);}
    float4 ReadBack(O i):COLOR {return float4(tex2D(Read,i.uv).r*.25,0,0,1);}
    technique Main <string Script="RenderColorTarget0=Data;Pass=Write;RenderColorTarget0=;Pass=Read;";> {
     pass Write { ZEnable=false;VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 Numeric(); }
     pass Read < string Script="Draw=Buffer;"; > {VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 ReadBack();}
    }'''
    runtime=effect('numeric-target',text);target=runtime.execute(geometry,state)
    assert runtime.targets['Data'].pixels[32,32,0]==2.125
    assert abs(target.pixels[32,32,0]-2.125*.25)<1/255
    assert [r['kind'] for r in runtime.last_stats]==['geometry','buffer']
    reports.append('signed/HDR target and subsequent buffer sampling')

    # An undefined target alias must not publish earlier writes from this attempt.
    previous={name:target.pixels.copy() for name,target in runtime.targets.items()}
    bad=effect('illegal-feedback',text.replace('float4(2.125,5,6,1)','tex2D(Read,i.uv)'))
    for name,pixels in previous.items():bad.targets[name].pixels[:]=pixels
    try:bad.execute(geometry,state)
    except FxUnsupported as error:assert 'actively written target' in str(error),str(error)
    else:raise AssertionError('Undefined same-target feedback was accepted')
    assert all(np.array_equal(target.pixels,previous[name]) for name,target in bad.targets.items())
    reports.append('failed draw transaction preserves all previous targets')
    # A second write must rebuild mip data even within the same execution.
    mip_source=COMMON+'''texture Data:RENDERCOLORTARGET <int Width=8;int Height=8;int MipLevels=0;>;
    texture DataDepth:RENDERDEPTHSTENCILTARGET <int Width=8;int Height=8;>;
    sampler Read=sampler_state{Texture=<Data>;MinFilter=POINT;MagFilter=POINT;MipFilter=LINEAR;AddressU=CLAMP;AddressV=CLAMP;};
    float4 Checker(float2 p:VPOS):COLOR {return float4(frac((p.x+p.y)*.5)*2,0,0,1);}
    float4 ReadBack(O i):COLOR {return tex2Dlod(Read,float4(.5,.5,0,2.5));}
    technique Main <string Script="RenderColorTarget0=Data;RenderDepthStencilTarget=DataDepth;Pass=Write;RenderColorTarget0=;RenderDepthStencilTarget=;Pass=Read;RenderColorTarget0=Data;RenderDepthStencilTarget=DataDepth;Pass=Replace;RenderColorTarget0=;RenderDepthStencilTarget=;Pass=Read;";> {
     pass Write <string Script="Draw=Buffer;";> {ZEnable=false; VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 Checker();}
     pass Replace <string Script="Draw=Buffer;";> {AlphaBlendEnable=false;VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 Blue();}
     pass Read <string Script="Draw=Buffer;";> {AlphaBlendEnable=true; SrcBlend=ONE; DestBlend=ONE;
       VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 ReadBack();}
    }'''
    runtime=effect('rewritten-target-mips',mip_source)
    assert runtime.targets['Data'].mip_levels==4
    target=runtime.execute(geometry,state)
    np.testing.assert_array_equal(np.rint(target.pixels[32,32]*255),[128,0,255,255])
    reports.append('explicit fractional target LOD and mip refresh after a second write')
    odd=ColorTarget(5,3)
    odd.pixels[...,:3]=np.eye(3)[:,None,:]
    odd.pixels[...,3]=1
    reduced=odd.reduce_mip(odd.pixels)
    np.testing.assert_allclose(reduced[0]*255, [[42,127,85,255]]*2, atol=1.01)
    reports.append('odd target dimensions use the native measured impulse weights')
    phase=COMMON+'''texture Target:RENDERCOLORTARGET <int Width=4;int Height=4;>;
    texture Seed; sampler PointRead=sampler_state{Texture=<Seed>;MinFilter=POINT;MagFilter=POINT;MipFilter=NONE;AddressU=WRAP;AddressV=WRAP;};
    sampler TargetRead=sampler_state{Texture=<Target>;MinFilter=POINT;MagFilter=POINT;MipFilter=NONE;AddressU=CLAMP;AddressV=CLAMP;};
    O OffsetVS(float4 p:POSITION,float2 uv:TEXCOORD0) {O o;o.p=p;o.uv=uv+.125;return o;}
    float4 Compare(O i,float2 p:VPOS):COLOR {
        return float4(tex2D(PointRead,i.uv).r,tex2D(PointRead,(p+.5)/4).r,tex2D(PointRead,float2(.5,.5)).r,1);
    }
    float4 ReadTarget(float2 p:VPOS):COLOR {return tex2Dlod(TargetRead,float4((p+.5)/64,0,0));}
    technique Main <string Script="RenderColorTarget0=Target;Pass=Write;RenderColorTarget0=;Pass=Read;";> {
      pass Write <string Script="Draw=Buffer;";> {ZEnable=false;VertexShader=compile vs_3_0 OffsetVS();PixelShader=compile ps_3_0 Compare();}
      pass Read <string Script="Draw=Buffer;";> {VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 ReadTarget();}
    }'''
    checker=np.ones((8,8,4),dtype=np.float32)
    checker[...,0]=np.arange(8)%2
    runtime=effect('fullscreen-point-phase',phase)
    target=runtime.execute(geometry,state,images={'Seed':FxImage(checker,'checker')})
    np.testing.assert_array_equal(target.pixels,np.broadcast_to([0,1,0,1],target.pixels.shape))
    reports.append('fullscreen point boundary differs from an analytic pixel coordinate')
    masked_targets = COMMON + '''texture A:RENDERCOLORTARGET;
    texture B:RENDERCOLORTARGET;
    texture C:RENDERCOLORTARGET;
    float4 ClearColor = {0,1,0,1};
    struct MRT {
        float4 a:COLOR0; float4 b:COLOR1; float4 c:COLOR2; float4 d:COLOR3;
    };
    MRT Write(O i) {
        MRT o;
        o.a=float4(64.0,96.0,112.0,0)/255.0;
        o.b=o.a; o.c=o.a; o.d=o.a;
        return o;
    }
    technique Main <string Script="RenderColorTarget1=A;RenderColorTarget2=B;RenderColorTarget3=C;Pass=Masked;Pass=Inherited;";> {
      pass Masked <string Script="ClearSetColor=ClearColor;Clear=Color;Draw=Geometry;";> {
        ZEnable=false; ColorWriteEnable=1; ColorWriteEnable1=6; ColorWriteEnable3=0;
        VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 Write();
      }
      pass Inherited {
        AlphaBlendEnable=true; SrcBlend=ONE; DestBlend=ONE;
        VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 Write();
      }
    }'''
    runtime = effect('target-write-masks', masked_targets)
    runtime.execute(geometry, state)
    expected = {'': [128,255,0,255], 'A': [0,192,224,255],
                'B': [128,192,224,0], 'C': [0,255,0,255]}
    for name, color in expected.items():
        pixels = runtime.targets[name].pixels
        np.testing.assert_array_equal(np.rint(pixels[32,32]*255), color, err_msg=name)
        np.testing.assert_array_equal(np.rint(pixels[0,0]*255), [0,255,0,255], err_msg=name)
    reports.append('independent target masks persist across passes and leave clears unrestricted')

    topology=COMMON+'''float4 UV(O i):COLOR{return float4(i.uv,0,1);}
    technique Main {pass Draw {ZEnable=false;VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 UV();}}'''
    stream=GeometryStream({'POSITION0':np.array([[-1,-1,.5,1],[1,-1,.5,1],[1,1,.5,1],[-1,1,.5,1]]),
                           'TEXCOORD0':np.array([[0,0],[1,0],[0,0],[0,0.]])},np.array([[0,1,2],[0,2,3]]))
    runtime=effect('topology',topology)
    first=runtime.execute(stream,state).pixels.copy()
    original=stream.triangles.copy()
    changed=GeometryStream(stream.inputs,np.array([[0,1,3],[1,2,3]]))
    second=runtime.execute(changed,state).pixels.copy()
    fresh=effect('topology-fresh',topology).execute(changed,state).pixels
    assert len(stream.triangles)==len(changed.triangles) and np.array_equal(stream.triangles,original)
    assert not np.array_equal(first,second) and np.array_equal(second,fresh)
    reports.append('equal face counts with changed connectivity reuse no stale indices and preserve UV corners')
    (args.output/'execution-results.json').write_text(json.dumps(dict(checks=reports,status='NUMERICAL_PASS',native_comparison_pending=True),indent=2))
    print('PASS ORDERED_FX_EXECUTION',len(reports),'cases',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
