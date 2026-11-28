"""Numeric sampling against native register-scoped sampler readouts."""
import argparse
import json
from pathlib import Path
import sys
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import t_middle_converters
from ninesync_fx.fx.loader import fx_parse_file
from ninesync_fx.fx.semantics import FxSceneState
from ninesync_fx.runtime.middle.render_execution import FxRenderExecution,GeometryStream
from ninesync_fx.runtime.middle.blender_raster import encoded_image, texture_loader

def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    geometry=GeometryStream({'POSITION0':np.array([[-1,-1,.5,1],[1,-1,.5,1],[0,1,.5,1]])},np.array([[0,1,2]]))
    cases=['implicit-default','implicit-ps2','register3','reserved','register3-spatial','lod-NONE','lod-LINEAR','lod-default','register1','register2','used-scope',
           'address-WRAP','address-CLAMP','address-v0','address-v1','address-v2']
    rows=[]
    for name in cases:
        program=fx_parse_file(str(args.probes/('pixel-'+name+'.fx')))
        state=FxSceneState();state.viewport=(512,512)
        actual=FxRenderExecution(program,512,512,texture_loader).execute(geometry,state).pixels.copy()
        native=texture_loader(str(args.probes/('native-pixel-'+name+'.png'))).pixels
        error=np.abs(actual[...,:3]-native[...,:3])*255
        interior=float(error[:,2:-2].max());edges=float(error.max())
        assert interior<=1.01,(name,interior)
        assert edges<=1.01,(name,edges)
        if name in ('register3','reserved','register3-spatial','lod-NONE'):assert edges<1e-4,(name,edges)
        rows.append(dict(case=name,maximum_interior_byte_error=interior,maximum_whole_image_byte_error=edges))
    png_upload_checks = check_png_upload(args, geometry)
    (args.output/'results.json').write_text(json.dumps(dict(status='PASS_REGISTER_SCOPED_SAMPLING',checks=rows,
        png_upload_checks=png_upload_checks, png_mip_checks=check_png_mips(args.texture_atlas),
        scope='Native initialized/custom register mip and address defaults; implicit inherited spatial filtering still requires explicit state.'),indent=2))
    print('PASS NATIVE_SAMPLER_REGISTERS',rows,flush=True)

def check_png_upload(args, geometry):
    source = args.probes / 'grass-base.png'
    if not source.is_file():
        return []
    def original_dimensions(path):
        image = bpy.data.images.load(path, check_existing=False)
        try:
            return encoded_image(image)
        finally:
            bpy.data.images.remove(image)

    stored = original_dimensions(str(source))
    native_size = texture_loader(str(source))
    assert (stored.width, stored.height) == (1280, 960)
    assert (native_size.width, native_size.height) == (2048, 1024)
    rows = []
    for kind in ('resource', 'material'):
        for level in range(4):
            name = f'grass-{kind}-lod-{level}'
            program = fx_parse_file(str(args.probes / (name + '.fx')))
            native = texture_loader(str(args.probes / ('native-' + name + '.png'))).pixels
            state = FxSceneState()
            state.viewport = (512, 512)
            accuracy = {}
            for label, loader, image in (('file_dimensions', original_dimensions, stored),
                                         ('upload_dimensions', texture_loader, native_size if kind == 'resource'
                                          else texture_loader(str(source), material_texture=True))):
                material = {'T': image} if kind == 'material' else None
                actual = FxRenderExecution(program, 512, 512, loader).execute(geometry, state, images=material).pixels
                error = np.abs(actual[..., :3] - native[..., :3]) * 255
                accuracy[label] = dict(mean=float(error.mean()), interior_mean=float(error[12:-12, 12:-12].mean()))
            assert accuracy['upload_dimensions']['interior_mean'] < .65, (kind, level, accuracy)
            assert accuracy['upload_dimensions']['mean'] < accuracy['file_dimensions']['mean'] / 2, (kind, level, accuracy)
            rows.append(dict(case=name, **accuracy))
    return rows


def check_png_mips(atlas):
    if atlas is None:
        return []
    rows = []
    for kind in ('material', 'resource'):
        source = texture_loader(str(atlas/'grass-source.png'), material_texture=(kind == 'material'))
        native = texture_loader(str(atlas/f'tiny-atlas-{kind}.png')).pixels
        chain = source.mip_chain()
        for level in range(5, 9):
            mip = chain[level]
            y = (level - 5) * 64
            expected = native[y:y+mip.height, :mip.width, :3]
            error = np.abs(mip.pixels[..., :3] - expected) * 255
            assert error.mean() < .2 and error.max() <= 1.01, (kind, level, error.mean(), error.max())
            rows.append(dict(binding=kind, mip=level, mean_byte_error=float(error.mean()),
                             maximum_byte_error=float(error.max())))
    return rows


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--probes',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--texture-atlas', type=Path)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
