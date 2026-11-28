"""Verify the saved rest-input drivers in a fresh process without NineSync."""
import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np


def run(args):
    assert not hasattr(bpy.types.Scene,'ninesync_fx'), 'This proof must start without the add-on'
    report=json.loads((args.output/'results.json').read_text())
    bpy.ops.wm.open_mainfile(filepath=str(args.output/'live-bone-rest.blend'),load_ui=False)
    assert not hasattr(bpy.types.Scene,'ninesync_fx')
    scene=bpy.context.scene;scene.frame_set(10);bpy.context.view_layer.update()
    obj=next(obj for obj in scene.objects if obj.type=='MESH' and obj.find_armature())
    core=next(tree for tree in bpy.data.node_groups if 'ninesync_middle_vertex_map' in tree)
    mapping=json.loads(core['ninesync_middle_vertex_map'])
    evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=evaluated.to_mesh()
    try:
        columns=[]
        for row in range(4):
            for col in range(4):
                name,channel=mapping[f'TEXCOORD{row}.{col}'];attribute=mesh.attributes[name]
                data=np.empty(len(attribute.data)*4,dtype=np.float32);attribute.data.foreach_get('color',data)
                columns.append(data.reshape(-1,4)[:,channel])
        chosen=np.repeat([p.material_index==1 for p in mesh.polygons],[p.loop_total for p in mesh.polygons])
        expected=np.array(report['checks'][-1]['matrix']).reshape(16)
        error=float(np.abs(np.column_stack(columns)[chosen]-expected).max())
        assert error<2e-5,error
    finally:evaluated.to_mesh_clear()
    (args.output/'standalone-reopen.json').write_text(json.dumps(dict(maximum_error=error,addon_registered=False),indent=2))
    print('PASS STANDALONE_BONE_REST_REOPEN',error,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
