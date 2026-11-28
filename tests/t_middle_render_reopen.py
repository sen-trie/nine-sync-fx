import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
import t_middle_converters
from ninesync_fx.runtime.middle.targets import apply,RENDER
from ninesync_fx.runtime.middle.raster_targets import CONFIG


def run(args):
    bpy.ops.wm.open_mainfile(filepath=str(args.file),load_ui=False)
    obj=bpy.context.scene.objects['Source']
    image=obj[CONFIG]['image']
    np.testing.assert_allclose(np.array(image.pixels[:]).reshape(-1,4)[:,0],32/255,atol=1e-7)
    result=apply(obj,args.file.parent/'history.fx',RENDER,units=1)
    runtime=result.core.executors[next(iter(result.core.executors))]
    assert np.all(runtime.targets['History'].pixels[...,0]==.25)
    assert runtime.targets['Mipped'].mip_levels==5
    args.output.write_text(json.dumps(dict(status='PASS',fresh_process=True,packed_image_preserved=True,continued_feedback_value=.25)))
    print('PASS FRESH_PROCESS_RENDER_HISTORY',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('file','output'):parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
