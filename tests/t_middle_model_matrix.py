"""Original surface FX on a catalogued PMX, with animation and lifecycle checks."""
import argparse
import faulthandler
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer, model_object, snapshot, position_camera, render_image, resolve_textures
from t_middle_converters import set_scene,add_empty
from ninesync_fx.runtime.middle.targets import apply,remove,inspect_bindings,MATERIAL
from ninesync_fx.nodes.material_compile import mme_tree_signature


def run(args):
    record=json.loads(args.entry.read_text(encoding="utf-8"))
    model_path=Path(record["path"])
    digest=sha256(model_path.read_bytes()).hexdigest()
    pmx,importer=load_importer(args.mmd_tools)
    scene=set_scene()
    scene.render.engine="BLENDER_EEVEE"
    scene.render.resolution_x=scene.render.resolution_y=384
    scene.render.film_transparent=True
    model=pmx.load(str(model_path))
    relinked=resolve_textures(model,model_path,model_path.parent/"tex")
    print("MODEL_IMPORT_BEGIN",record["family"],flush=True)
    faulthandler.dump_traceback_later(45,repeat=True)
    importer().execute(pmx=model,scale=.08,types={"MESH","ARMATURE","MORPHS"},fix_bone_order=False)
    faulthandler.cancel_dump_traceback_later()
    print("MODEL_IMPORT_END",record["family"],flush=True)
    obj=model_object(scene,model)
    position_camera(scene,obj)
    light=add_empty(scene,"Model matrix light",(-.65,-1,.85))
    # Choose a used, visible slot with an existing texture; missing files remain recorded.
    counts={i:sum(p.loop_total for p in obj.data.polygons if p.material_index==i) for i in range(len(obj.material_slots))}
    candidates=[]
    for i,slot in enumerate(obj.material_slots):
        mat=slot.material
        if mat and mat.mmd_material.alpha>0 and counts[i] and mat.node_tree:
            base=mat.node_tree.nodes.get("mmd_base_tex")
            if base and base.image and (base.image.packed_file or Path(bpy.path.abspath(base.image.filepath)).is_file()):
                candidates.append(i)
    if not candidates:
        raise AssertionError("No visible textured slot is available; source asset needs repair")
    slot=max(candidates,key=counts.get)
    rig=obj.find_armature()
    bone=next((b for b in rig.pose.bones if b.name in obj.vertex_groups and not b.constraints),None)
    if bone is None:bone=next(b for b in rig.pose.bones if b.name in obj.vertex_groups)
    bone_name=bone.name
    for frame,offset in ((1,0.0),(10,.03)):
        bone.location.x=offset
        bone.keyframe_insert("location",frame=frame)
    scene.frame_set(1)
    bpy.context.view_layer.update()
    original=snapshot(obj)
    material=obj.material_slots[slot].material
    signature=mme_tree_signature(material.node_tree)
    groups=len(bpy.data.node_groups)
    args.output.mkdir(parents=True,exist_ok=True)
    start=perf_counter()
    result=apply(obj,args.fx,MATERIAL,slot=slot,units=.08,light=light)
    apply_seconds=perf_counter()-start
    assert not inspect_bindings(obj,MATERIAL,slot=slot)
    first=render_image(scene,args.output,"frame-1",1)
    animated=render_image(scene,args.output,"frame-10",10)
    assert (first[:,:,3]>.9).sum()>500
    assert not apply(obj,args.fx,MATERIAL,slot=slot,units=.08,light=light).rebuilt
    apply(obj,args.fx,MATERIAL,slot=slot,units=.08,light=light,force_rebuild=True)
    rebuilt=render_image(scene,args.output,"rebuilt",10)
    assert np.array_equal(animated,rebuilt)
    assert snapshot(obj)==original
    bpy.context.preferences.filepaths.save_version=0
    saved=args.output/"model.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved),relative_remap=False)
    scene.frame_set(1)
    bpy.context.view_layer.update()
    remove(obj,MATERIAL,slot=slot)
    assert snapshot(obj)==original and obj.material_slots[slot].material==material
    assert mme_tree_signature(material.node_tree)==signature
    assert len(bpy.data.node_groups)==groups
    bpy.ops.wm.open_mainfile(filepath=str(saved),load_ui=False)
    scene=bpy.context.scene
    obj=model_object(scene,model)
    light=scene.objects["Model matrix light"]
    assert not apply(obj,args.fx,MATERIAL,slot=slot,units=.08,light=light).rebuilt
    reopened=render_image(scene,args.output,"reopened",10)
    assert np.array_equal(reopened,animated)
    assert sha256(model_path.read_bytes()).hexdigest()==digest
    result=dict(record,execution="PASS",slot=slot,fx=str(args.fx),fx_sha256=sha256(args.fx.read_bytes()).hexdigest(),
                bone=bone_name,textures_relinked=relinked,apply_seconds=apply_seconds,blend=str(saved),
                reopen_error=0.0,rebuild_error=0.0,source_graph_preserved=True,source_hash_preserved=True)
    (args.output/"results.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("PASS MODEL_MATRIX",record["family"],slot,flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--entry",type=Path,required=True)
    parser.add_argument("--fx",type=Path,required=True)
    parser.add_argument("--mmd-tools",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
