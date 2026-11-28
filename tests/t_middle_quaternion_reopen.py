"""Fresh-process verification of saved native drivers, without importing NineSync."""
import argparse
import json
from pathlib import Path
import sys
import bpy
import numpy as np


def run(args):
    report=json.loads(args.results.read_text());checks=[]
    assert not any(name=='ninesync_fx' or name.startswith('ninesync_fx.') for name in sys.modules)
    bpy.ops.wm.open_mainfile(filepath=str(args.blend),load_ui=False)
    obj=bpy.data.objects[report['object_name']];slot=report['slot']
    material=obj.material_slots[slot].material
    token=material['ninesync_middle_token']
    core=next(node.node_tree for node in material.node_tree.nodes if node.get('ninesync_middle_core_node')==token and node.type=='GROUP')['ninesync_middle_vertex_core']
    descriptors=json.loads(core['ninesync_middle_vertex_map'])
    for frame in (30,0,20,10,30):
        expected=next(row['expected'] for row in report['checks'] if row['label']=='quaternion-seek' and row['frame']==frame)
        bpy.context.scene.frame_set(frame);bpy.context.view_layer.update()
        evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=evaluated.to_mesh()
        try:
            selected=np.repeat([poly.material_index==slot for poly in mesh.polygons],[poly.loop_total for poly in mesh.polygons])
            columns=[]
            for row in range(3):
                for col in range(4):
                    name,channel=descriptors[f'TEXCOORD{row}.{col}'];attribute=mesh.attributes[name]
                    value=np.empty(len(attribute.data)*4,dtype=np.float32);attribute.data.foreach_get('color',value)
                    columns.append(value.reshape(-1,4)[:,channel])
            actual=np.column_stack(columns)[selected]
            error=float(np.max(np.abs(actual-np.array(expected).reshape(12))))
            assert error<2e-5,(frame,error)
            checks.append(dict(frame=frame,maximum_field_error=error))
        finally:evaluated.to_mesh_clear()
    assert not any(name=='ninesync_fx' or name.startswith('ninesync_fx.') for name in sys.modules)
    args.output.write_text(json.dumps(dict(checks=checks,addon_imported=False,status='PASS'),indent=2))
    print('PASS QUATERNION_REOPEN_NO_ADDON',len(checks),'seeks',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('blend','results','output'):parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
