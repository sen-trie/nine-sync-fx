"""Evaluate the normal accessory route for controller-bearing BeamMan sources."""
import json
import os
from pathlib import Path
import sys
from time import perf_counter

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
entry_file, output = map(Path,sys.argv[sys.argv.index("--")+1:])
entry=json.loads(entry_file.read_text(encoding="utf-8"))
os.environ.setdefault("NINESYNC_FX",str(Path(__file__).resolve().parents[1]))
from harness_fx import mb, setup_scene

scene,collection,_root,_camera=setup_scene()
start=perf_counter()
operator=bpy.ops.import_scene.ninesync_fx_accessory(filepath=entry["paired_x"],scale=1.0)
assert operator == {"FINISHED"}, str(operator)
root=mb.find_accessory_root(collection,scene.ninesync_fx.accessories[0].key)
passes=mb.mme_effect_passes(root)
frames=[]
for frame in (0,10,30,10):
    scene.frame_set(frame)
    deps=bpy.context.evaluated_depsgraph_get()
    vertices=faces=0
    for obj in passes:
        evaluated=obj.evaluated_get(deps)
        mesh=evaluated.to_mesh()
        try:
            values=np.empty(len(mesh.vertices)*3,dtype=np.float32)
            mesh.vertices.foreach_get("co",values)
            assert np.isfinite(values).all()
            vertices+=len(mesh.vertices)
            faces+=len(mesh.polygons)
        finally:
            evaluated.to_mesh_clear()
    frames.append(dict(frame=frame,vertices=vertices,faces=faces))
report={"relative":entry["relative"],"path":entry["path"],"accessory":entry["paired_x"],
        "status":"BUILT_WITH_GEOMETRY" if any(f["faces"] for f in frames) else "BUILT_EMPTY",
        "passes":len(passes),"frames":frames,"seconds":perf_counter()-start,
        "qualification":"Evaluated topology and finite positions. Rendering and MMD parity unverified."}
output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print("ACCESSORY_RESULT",json.dumps(report,ensure_ascii=True),flush=True)
