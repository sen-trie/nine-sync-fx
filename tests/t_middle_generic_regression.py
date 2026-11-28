import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0,str(Path(os.environ['NINESYNC_FX'])/'tests'))
from harness_fx import mb


parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
for obj in list(bpy.data.objects):bpy.data.objects.remove(obj)
scene=bpy.context.scene
camera=bpy.data.objects.new('camera',bpy.data.cameras.new('camera'));scene.collection.objects.link(camera)
camera.location=(0,-4.8,2);camera.rotation_euler=(1.1868239,0,0);scene.camera=camera
path=Path(os.environ['NINESYNC_FX'])/'tests/fixtures/Synth/synth.x'
assert bpy.ops.import_scene.ninesync_fx_accessory(filepath=str(path),scale=.08)=={'FINISHED'}
scene.frame_set(10);bpy.context.view_layer.update()
passes=[obj for obj in scene.objects if obj.type=='MESH' and obj.get('mme_pass_key') is not None]
if not passes:passes=[obj for obj in scene.objects if obj.type=='MESH']
hash=sha256();vertices=0;faces=0
for obj in sorted(passes,key=lambda obj:obj.name):
    evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=evaluated.to_mesh()
    try:
        points=np.empty(len(mesh.vertices)*3,dtype=np.float32);mesh.vertices.foreach_get('co',points)
        indices=np.empty(len(mesh.loops),dtype=np.int32);mesh.loops.foreach_get('vertex_index',indices)
        vertices+=len(mesh.vertices);faces+=len(mesh.polygons);hash.update(points.tobytes());hash.update(indices.tobytes())
    finally:evaluated.to_mesh_clear()
assert len(passes)==2 and vertices==245,(len(passes),vertices)
report=dict(passes=len(passes),vertices=vertices,faces=faces,sha256=hash.hexdigest(),hash_format='float32 local positions followed by int32 loop indices, pass name order, frame 10')
args.output.write_text(json.dumps(report,indent=2));print('PASS GENERIC_SYNTH',json.dumps(report),flush=True)
