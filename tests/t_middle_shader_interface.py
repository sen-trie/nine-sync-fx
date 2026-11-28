"""Shared shader interface arrays and repeated pass names against native MMD."""
import argparse,json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import paths
paths.prepare('ninesync_fx')
from ninesync_fx.fx.loader import fx_parse_file
from ninesync_fx.fx.semantics import FxSceneState
from ninesync_fx.fx.techniques import fx_plan_draws
from ninesync_fx.runtime.middle.render_execution import GeometryStream,FxRenderExecution


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    fx=Path(__file__).parent/'fixtures/middle/shader_interface.fx';program=fx_parse_file(str(fx))
    geometry=GeometryStream({'POSITION0':np.array([[0,0,.5,1]]*3,dtype=np.float32)},np.array([[0,1,2]]))
    runtime=FxRenderExecution(program,256,256);state=FxSceneState();state.viewport=(256,256)
    pixels=runtime.execute(geometry,state).pixels
    assert np.all(pixels[:,:,2:]==1)
    assert np.allclose(pixels[:,:,0],np.floor(np.arange(256)/256*255+.5)[None,:]/255)
    assert np.allclose(pixels[:,:,1],np.floor(np.arange(256)/256*255+.5)[:,None]/255)
    assert fx_plan_draws(program,1)[0].fx_pass.ps[2][1]=='First'
    np.save(args.output/'interface.npy',pixels)
    # No technique script means every declared pass executes, even if names repeat.
    path=args.output/'implicit-passes.fx';path.write_text(fx.read_text().replace('string Script="Pass=Same;";',''))
    implicit=FxRenderExecution(fx_parse_file(str(path)),256,256);result=implicit.execute(geometry,state)
    assert len(implicit.last_stats)==2 and np.all(result.pixels==[1,0,0,1])
    # Native image is decoded raw; one byte accounts for sub-ULP native UV interpolation.
    import bpy
    image=bpy.data.images.load(str(args.native));image.colorspace_settings.name='Non-Color'
    native=np.array(image.pixels[:],dtype=np.float32).reshape(256,256,4)[::-1];bpy.data.images.remove(image)
    difference=np.abs(np.rint(native*255)-np.rint(pixels*255))
    assert difference.max()<=1
    (args.output/'results.json').write_text(json.dumps(dict(status='PASS',native_max_byte_error=float(difference.max()),
        native_mean_byte_error=float(difference.mean()),arrays=True,vertex_declaration_padding=True,
        explicit_duplicate_lookup='first',implicit_duplicate_draws=2),indent=2))
    print('PASS NATIVE_SHADER_INTERFACE',float(difference.max()),'maximum byte difference',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--native',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
