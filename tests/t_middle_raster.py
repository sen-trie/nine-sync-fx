"""Numerical raster contract: coverage, real clip W, depth and ordered states."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
import paths
paths.prepare('ninesync_fx')
from ninesync_fx.runtime.middle.raster import ColorTarget,DepthTarget,Rasterizer,RasterState


QUAD=np.array([[-1,-1,.5,1],[1,-1,.5,1],[1,1,.5,1],[-1,1,.5,1]],dtype=float)
FACES=np.array([[0,1,2],[0,2,3]])


def solid(color,discard=0,depth=None):
    return lambda inputs:({0:color},discard,depth)


def draw(positions=QUAD,indices=FACES,state=RasterState(),shader=None,varyings=None,target=None,depth=None):
    target=target or ColorTarget(64,64,'A32B32G32R32F')
    depth=depth or DepthTarget(64,64)
    raster=Rasterizer(64,64,fragment_batch=512)
    raster.draw(positions,varyings or {},indices,shader or solid([1,0,0,1]),{0:target},depth,state)
    return target,depth,raster


def run(args):
    tests=[]
    state=RasterState(blend=True,source_blend='ONE',destination_blend='ONE')
    target,depth,raster=draw(state=state)
    assert np.array_equal(target.pixels,np.broadcast_to([1,0,0,1],(64,64,4)))
    assert raster.stats['passing_fragments']==4096,raster.stats
    tests.append('integer-centers and shared-edge top-left ownership')

    ndc=np.array([[-.8,-.8],[.8,-.8],[0,.8]])
    w=np.array([1,2,4])
    positions=np.column_stack([ndc*w[:,None],w*.5,w])
    uv=np.array([[0,0],[1,0],[0,1]],dtype=float)
    def uv_shader(inputs):
        values=inputs['TEXCOORD0']
        return {0:np.column_stack([values,np.zeros(len(values)),np.ones(len(values))])},0,None
    target,_,_=draw(positions,np.array([[0,1,2]]),shader=uv_shader,varyings={'TEXCOORD0':uv})
    assert np.max(np.abs(target.pixels[32,32,:2]-[.25,.25]))<1e-6,target.pixels[32,32]
    tests.append('modified clip W supplies perspective interpolation')

    crossing=QUAD.copy();crossing[0,2]=-.5
    target,depth,raster=draw(crossing)
    assert 0<raster.stats['passing_fragments']<4096
    assert np.all((depth.values>=0)&(depth.values<=1))
    behind=QUAD.copy();behind[:,2]=-1
    target,_,raster=draw(behind)
    assert raster.stats['passing_fragments']==0 and not target.pixels.any()
    tests.append('homogeneous near clipping and fully clipped primitives')

    target,depth,_=draw(shader=solid([1,0,0,1]))
    far=QUAD.copy();far[:,2]=.8
    draw(far,shader=solid([0,0,1,1]),target=target,depth=depth)
    assert np.array_equal(target.pixels[32,32],[1,0,0,1])
    draw(far,shader=solid([0,0,1,1],depth=.2),target=target,depth=depth)
    assert np.array_equal(target.pixels[32,32],[0,0,1,1])
    assert abs(depth.values[32,32]-.2)<1e-7
    tests.append('Z-only ordering and explicit pixel-shader depth')

    target,depth,_=draw(shader=solid([1,0,0,1]))
    near=QUAD.copy();near[:,2]=.1
    draw(near,state=RasterState(alpha_test=True,alpha_ref=128),shader=solid([0,1,0,.25]),target=target,depth=depth)
    assert np.array_equal(target.pixels[32,32],[1,0,0,1])
    draw(near,state=RasterState(depth_write=False),shader=solid([0,1,0,1]),target=target,depth=depth)
    draw(QUAD,shader=solid([0,0,1,1]),target=target,depth=depth)
    assert np.array_equal(target.pixels[32,32],[0,0,1,1])
    tests.append('alpha rejection and disabled depth writes')

    a,_,_=draw(state=RasterState(cull='CW'))
    b,_,_=draw(state=RasterState(cull='CCW'))
    assert bool(a.pixels.any()) != bool(b.pixels.any())
    tests.append('opposite cull modes')

    target=ColorTarget(64,64,'A32B32G32R32F');target.clear([0,0,0,1])
    state=RasterState(depth_test=False,blend=True)
    draw(state=state,shader=solid([1,0,0,.5]),target=target)
    draw(state=state,shader=solid([0,0,1,.25]),target=target)
    assert np.max(np.abs(target.pixels[32,32]-[.375,0,.25,.625]))<1e-6,target.pixels[32,32]
    tests.append('ordered source-alpha blending')

    target=ColorTarget(64,64,'R16F')
    draw(shader=solid([2.125,-3,7,.3]),target=target)
    assert np.array_equal(target.pixels[32,32],[2.125,0,0,1])
    draw(shader=solid([-.3,1,1,1]),target=target)
    assert target.pixels[32,32,0]==np.float32(np.float16(-.3))
    tests.append('signed HDR R16F and defined missing channels')

    target=ColorTarget(64,64)
    target.clear([.07,.12,.35,1])
    assert np.array_equal(np.rint(target.pixels[0,0]*255),[17,30,89,255])
    state=RasterState(color_mask=1)
    draw(state=state,shader=solid([1,1,1,0]),target=target)
    assert np.array_equal(np.rint(target.pixels[32,32]*255),[255,30,89,255])
    tests.append('D3DCOLOR clear truncation and channel write mask')

    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'raster-results.json').write_text(json.dumps(dict(checks=tests,status='NUMERICAL_PASS',native_comparison_pending=True),indent=2))
    print('PASS DIRECT_RASTER',len(tests),'numerical cases',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
