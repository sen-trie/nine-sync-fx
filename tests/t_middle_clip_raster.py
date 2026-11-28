"""Exact shared-geometry diagnostic for perspective/orthographic clip Z/W."""
import argparse
from hashlib import sha256
import json
import math
import struct
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_converters import set_scene
from harness_fx import mb
from ninesync_fx.accessories.standalone_import import import_plain_accessory
from ninesync_fx.fx.loader import fx_parse_file
from ninesync_fx.runtime.middle.blender_raster import snapshot_geometry,make_state,texture_loader
from ninesync_fx.runtime.middle.render_execution import FxRenderExecution

FIX=Path(__file__).parent/'fixtures/middle'


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'clip_raster.x').write_bytes((FIX/'clip_raster.x').read_bytes())
    bmp=struct.pack('<2sIHHI',b'BM',58,0,0,54)+struct.pack('<IiiHHIIiiII',40,1,1,1,24,0,4,2835,2835,0,0)+bytes([255,255,255,0])
    (args.output/'clip_white.bmp').write_bytes(bmp)
    scene=set_scene();scene.render.resolution_x=scene.render.resolution_y=256
    scene.camera.location=(0,-4*.08,0);scene.camera.rotation_euler=(math.pi/2,0,0)
    scene.camera.data.sensor_fit='VERTICAL';scene.camera.data.lens=scene.camera.data.sensor_height/(2*math.tan(math.radians(30)))
    scene.camera.data.ortho_scale=4*.08
    root=import_plain_accessory(bpy.context,str(args.output/'clip_raster.x'),.08)
    root.scale=(.1,)*3;root['mmd_bridge_accessory']=True
    scene.frame_set(0);bpy.context.view_layer.update()
    obj=next(o for o in root.children_recursive if o.type=='MESH')
    before=(obj.data,tuple(s.material for s in obj.material_slots),tuple(obj.modifiers))
    geometry=snapshot_geometry(scene,obj)
    reports=[]
    for ortho in (False,True):
        scene.camera.data.type='ORTHO' if ortho else 'PERSP'
        bpy.context.view_layer.update()
        for mode in range(3):
            name=f'{"ortho" if ortho else "perspective"}-{mode}'
            source='#define CLIP_MODE '+str(mode)+'\n#define ORTHOGRAPHIC '+str(int(ortho))+'\n'+(FIX/'clip_raster.fx').read_text()
            fx=args.output/(name+'.fx');fx.write_text(source)
            program=fx_parse_file(str(fx));runtime=FxRenderExecution(program,256,256,texture_loader)
            runtime.targets[''].clear([0,0,0,1])
            for slot in sorted(set(geometry.subsets)):
                state,images,technique=make_state(scene,obj,geometry,int(slot),program)
                runtime.execute(geometry.subset(slot),state,subset=int(slot),images=images,technique=technique)
            pixels=runtime.targets[''].pixels
            assert (pixels[:,:,:3].max(axis=2)>0).sum()>100
            np.save(args.output/(name+'.npy'),pixels)
            reports.append(dict(name=name,source_sha256=sha256(fx.read_bytes()).hexdigest(),passes=runtime.last_stats))
            assert (obj.data,tuple(s.material for s in obj.material_slots),tuple(obj.modifiers))==before
    bpy.context.preferences.filepaths.save_version=0
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output/'clip-diagnostic.blend'),relative_remap=False)
    (args.output/'clip-results.json').write_text(json.dumps(dict(checks=reports,native_comparison_pending=True),indent=2))
    print('PASS CLIP_RASTER_INPUTS',len(reports),'camera/edit combinations',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
