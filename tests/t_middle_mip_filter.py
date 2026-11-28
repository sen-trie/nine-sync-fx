import argparse
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
import paths
paths.prepare('ninesync_fx')
from ninesync_fx.fx.errors import FxUnsupported
from ninesync_fx.fx.loader import fx_parse_file
from ninesync_fx.fx.semantics import FxSceneState
from ninesync_fx.fx.textures import FxImage
from ninesync_fx.fx.types import FxSampler
from ninesync_fx.runtime.middle.render_execution import FxRenderExecution,GeometryStream
from ninesync_fx.runtime.middle.raster import Rasterizer,RasterState


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    checks=[]
    def sampler(**states):return FxSampler('Read','T',states,None)
    pixels=np.ones((8,8,4));pixels[...,:3]=(np.indices((8,8)).sum(0)%2)[...,None]
    image=FxImage(pixels,'checker')
    assert [(m.width,m.height) for m in image.mip_chain()]==[(8,8),(4,4),(2,2),(1,1)]
    assert np.array_equal(image.mips[-1].pixels[0,0],[.5,.5,.5,1])
    checks.append('encoded RGBA box mip chain')
    resource = image.d3dx_png_upload().mip_chain()
    material = image.d3dx_png_upload(material_texture=True).mip_chain()
    np.testing.assert_array_equal(np.rint(resource[-1].pixels[0,0]*255), [128,128,128,255])
    np.testing.assert_array_equal(np.rint(material[-1].pixels[0,0]*255), [127,127,127,255])
    checks.append('native PNG resource and material uploads round each reduced level separately')
    # Native vertex capture: unspecified filtering is POINT with MIPFILTER=NONE.
    np.testing.assert_allclose(image.sample_filtered(.0625,.0625,sampler(),lod=1,stage='vs'),[0,0,0,1])
    np.testing.assert_allclose(image.sample_filtered(.0625,.0625,sampler(minfilter='LINEAR',magfilter='LINEAR',mipfilter='LINEAR'),lod=1,stage='vs'),[.5,.5,.5,1])
    checks.append('native vertex defaults differ from pixel defaults; explicit mip states override them')
    odd=FxImage(np.arange(3*5*4,dtype=float).reshape(3,5,4)-30,'odd-signed')
    assert [(m.width,m.height) for m in odd.mip_chain()]==[(5,3),(2,1),(1,1)]
    np.testing.assert_allclose(odd.mips[-1].pixels[0,0],odd.pixels.mean((0,1)),atol=1e-12)
    assert odd.mip_chain() is odd.mips
    checks.append('odd rectangular dimensions preserve signed channel means')
    uv=(.0625,.0625)
    none=image.sample_filtered(*uv,sampler(mipfilter='NONE'),lod=3)
    np.testing.assert_allclose(image.sample_filtered(*uv,sampler(filter='NONE'),lod=3),none)
    linear=image.sample_filtered(*uv,sampler(mipfilter='LINEAR'),lod=3)
    assert np.array_equal(none,[0,0,0,1])
    np.testing.assert_allclose(linear,[.5,.5,.5,1])
    fractional=image.sample_filtered(*uv,sampler(mipfilter='LINEAR'),lod=.25)
    np.testing.assert_allclose(fractional,[.125,.125,.125,1])
    np.testing.assert_allclose(image.sample_filtered(*uv,sampler(mipfilter='POINT'),lod=.6),[.5,.5,.5,1])
    np.testing.assert_allclose(image.sample_filtered(*uv,sampler(),lod=0),none)
    checks.append('NONE, nearest mip, fractional trilinear, default and explicit zero')
    ramp=np.ones((2,2,4));ramp[0,0,:3]=0
    mag=FxImage(ramp,'mag')
    a=mag.sample_filtered(.5,.5,sampler(minfilter='POINT',magfilter='LINEAR'),lod=-2)
    b=mag.sample_filtered(.5,.5,sampler(minfilter='LINEAR',magfilter='POINT'),lod=-2)
    np.testing.assert_allclose(a,[.75,.75,.75,1]);np.testing.assert_allclose(b,[1,1,1,1])
    np.testing.assert_allclose(mag.sample_filtered(.5,.5,sampler(filter='POINT'),lod=0),[1,1,1,1])
    np.testing.assert_allclose(mag.sample_filtered(.5,.5,sampler(filter='LINEAR'),lod=0),[.75,.75,.75,1])
    np.testing.assert_allclose(mag.sample_filtered(.5,.5,sampler(),lod=0),[.75,.75,.75,1])
    checks.append('native Filter fallback and independent min/mag filters')
    for address in ('WRAP','MIRROR','CLAMP'):
        info=sampler(addressu=address,mipfilter='LINEAR')
        actual=image.sample_filtered(np.array([-1.2,.2,1.2]),.4,info,lod=0)
        np.testing.assert_array_equal(actual,image.sample(np.array([-1.2,.2,1.2]),.4,False,address))
    mixed=image.sample_filtered(np.array([uv[0]]*3),uv[1],sampler(),lod=np.array([0,.25,3]))
    np.testing.assert_allclose(mixed[0],[0,.125,.5])
    checks.append('addressing and vector LOD lanes')
    positions=np.array([[-.8,-.8,.5,1],[1.6,-1.6,1,2],[0,3.2,2,4]])
    tex=np.array([[0,0],[1,0],[0,1.]])
    raster=Rasterizer(64,64,derivatives=True)
    for x,y,z,inputs in raster.fragments(positions,{'TEXCOORD0':tex},np.array([[0,1,2]]),RasterState()):
        count=len(x);coords=inputs['VPOS'];actual=inputs['TEXCOORD0']
        screen=(positions[:,:2]/positions[:,3:4]*[.5,-.5]+.5)*64
        plane=np.column_stack([screen,np.ones(3)])
        basis=np.column_stack([coords[:,:2],np.ones(len(coords))]) @ np.linalg.inv(plane)
        expected=(basis/positions[:,3]) @ tex / (basis @ (1/positions[:,3]))[:,None]
        np.testing.assert_allclose(actual,expected,atol=1e-12)
        assert len(actual)==count*4
    checks.append('perspective-correct helper UVs independently reconstructed from screen planes')
    source='''texture T:MATERIALTEXTURE;
sampler Read=sampler_state {Texture=<T>;MinFilter=LINEAR;MagFilter=LINEAR;MIPSTATE};
struct O {float4 p:POSITION;float2 uv:TEXCOORD0;};
O VS(float4 p:POSITION,float2 uv:TEXCOORD0){O o;o.p=p;o.uv=uv;return o;}
float4 PS(O i,float2 p:VPOS):COLOR {return SAMPLE;}
technique Main {pass Draw {ZEnable=false;VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 PS();}}
'''
    geometry=GeometryStream({'POSITION0':np.array([[-1,1,.5,1],[1,1,.5,1],[1,-1,.5,1],[-1,-1,.5,1]]),
                             'TEXCOORD0':np.array([[0,0],[1,0],[1,1],[0,1]])},np.array([[0,1,2],[0,2,3]]))
    state=FxSceneState();state.viewport=(16,16)
    def execute(name,mip,sample,texture=image):
        fx=args.output/(name+'.fx');fx.write_text(source.replace('MIPSTATE',mip).replace('SAMPLE',sample))
        runtime=FxRenderExecution(fx_parse_file(str(fx)),16,16)
        return runtime.execute(geometry,state,images={'T':texture}).pixels.copy()
    sample='tex2D(Read,i.uv*8+float2(.0625,.0625))'
    filtered=execute('implicit','',sample)
    unfiltered=execute('none','MipFilter=NONE;',sample)
    explicit=execute('explicit-zero','','tex2Dlod(Read,float4(i.uv*8+float2(.0625,.0625),0,0))')
    assert np.all(filtered[...,:3]==128/255)
    assert np.array_equal(unfiltered,explicit)
    assert not np.array_equal(filtered,unfiltered)
    checks.append('pixel-shader transformed UV footprint, default linear mip and explicit level-zero bypass')
    # Independent native DDS readout: the bottom-right helper adds a large UV
    # offset. The top row and left column still select authored level 2.
    levels = [FxImage(np.full((256 >> level, 256 >> level, 4), level * (30 / 255)), 'authored')
              for level in range(9)]
    levels[0].mips = levels
    quad = execute('coarse-quad', 'MipFilter=LINEAR;', 'tex2D(Read,p*(4./256)+float2(.5,0)*frac(p.x*.5)*frac(p.y*.5)*4)',
                   levels[0])
    np.testing.assert_array_equal(np.rint(quad[..., :3] * 255), 60)
    checks.append('implicit LOD selects the same level for every pixel in a native coarse quad')
    (args.output/'results.json').write_text(json.dumps({'status':'PASS','checks':checks},indent=2))
    print('PASS NUMERIC_MIP_FILTER',len(checks),'cases',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
