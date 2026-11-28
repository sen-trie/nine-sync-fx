"""Live rest edits, actual undo/redo and reopen on an existing creator PMX rig."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer,model_object,resolve_textures,snapshot
from t_middle_converters import set_scene
from t_middle_vertex import field
from t_middle_bone_matrix import matrix
from ninesync_fx.runtime.middle.targets import apply,remove,inspect_bindings,MATERIAL,TOKEN,_core_node
from ninesync_fx.runtime.middle.vertex import VERTEX_CORE

UNITS=.08


def run(args):
    import ninesync_fx
    ninesync_fx.register()
    args.output.mkdir(parents=True,exist_ok=True)
    pmx,importer=load_importer(args.mmd_tools)
    source_hash=sha256(args.model.read_bytes()).hexdigest()
    scene=set_scene();model=pmx.load(str(args.model))
    resolve_textures(model,args.model,args.model.parent/'tex')
    importer().execute(pmx=model,scale=UNITS,types={'MESH','ARMATURE','MORPHS'},fix_bone_order=False)
    obj=model_object(scene,model);rig=obj.find_armature();obj_name=obj.name;rig_name=rig.name
    arm='\u5de6\u8155';center='\u30bb\u30f3\u30bf\u30fc'
    assert arm in rig.pose.bones and center in rig.pose.bones
    original=snapshot(obj);groups=len(bpy.data.node_groups)
    rig['ninesync_mme_name']='Bone controller.pmx'
    bone=rig.pose.bones[arm];bone.rotation_mode='XYZ'
    for frame,angles in ((1,(.2,-.3,.1)),(10,(-.3,.15,.4))):
        bone.rotation_euler=angles;bone.keyframe_insert('rotation_euler',frame=frame)
    rig.location=(.13,-.17,.11);rig.rotation_euler=(.1,.2,-.3);rig.scale=(1.1,.9,1.2)
    source=(Path(__file__).parent/'fixtures/middle/bone_matrix.fx').read_text()
    fx=args.output/'live-rest.fx';fx.write_text(source.replace('ProbeArm',arm).replace('ProbeCenter',center),encoding='utf-8')
    apply(obj,fx,MATERIAL,slot=1,units=UNITS,bindings={'Bone controller.pmx':rig})
    reports=[]

    def current():
        nonlocal obj,rig,scene
        scene=bpy.context.scene;obj=bpy.data.objects[obj_name];rig=bpy.data.objects[rig_name]
        material=obj.material_slots[1].material
        return _core_node(material.node_tree,material[TOKEN]).node_tree[VERTEX_CORE]

    def check(label,frame=10):
        core=current();scene.frame_set(frame);bpy.context.view_layer.update()
        deps=bpy.context.evaluated_depsgraph_get();expected=matrix(rig.evaluated_get(deps),arm)
        evaluated=obj.evaluated_get(deps);mesh=evaluated.to_mesh()
        try:
            selected=np.repeat([p.material_index==1 for p in mesh.polygons],[p.loop_total for p in mesh.polygons])
            actual=np.column_stack([field(mesh,core,f'TEXCOORD{row}.{col}') for row in range(4) for col in range(4)])[selected]
            error=float(np.abs(actual-expected.reshape(16)).max())
            assert error<2e-5,(label,error,actual[0],expected)
            assert not inspect_bindings(obj,MATERIAL,slot=1)
            reports.append(dict(state=label,frame=frame,maximum_error=error,matrix=expected.tolist()))
            return actual.copy()
        finally:evaluated.to_mesh_clear()

    check('initial',1);before=check('initial',10)
    bpy.context.preferences.edit.use_global_undo=True
    bpy.context.view_layer.objects.active=rig;rig.select_set(True)
    bpy.ops.ed.undo_push(message='Converted original rest pose')
    bpy.ops.object.mode_set(mode='EDIT')
    edit=rig.data.edit_bones[arm];edit.roll+=.37;edit.tail.x+=.025;edit.head.z+=.012
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.ed.undo_push(message='Changed creator rig rest pose')
    edited=check('edited-without-reapply')
    assert not np.array_equal(edited,before)
    assert bpy.ops.ed.undo()=={'FINISHED'}
    undone=check('undo-without-reapply');assert np.max(np.abs(undone-before))<2e-5
    assert bpy.ops.ed.redo()=={'FINISHED'}
    redone=check('redo-without-reapply');assert np.max(np.abs(redone-edited))<2e-5
    result=apply(obj,fx,MATERIAL,slot=1,units=UNITS)
    assert not result.rebuilt
    apply(obj,fx,MATERIAL,slot=1,units=UNITS,force_rebuild=True)
    rebuilt=check('forced-rebuild-reference');assert np.array_equal(rebuilt,redone)
    # Driver variables and pose/data paths must follow a native bone rename.
    rig.pose.bones[arm].name='Renamed live rest arm'
    arm='Renamed live rest arm'
    renamed=check('renamed-bone');assert np.array_equal(renamed,rebuilt)
    bpy.context.view_layer.objects.active=rig
    bpy.ops.object.mode_set(mode='EDIT');rig.data.edit_bones[arm].roll-=.19;bpy.ops.object.mode_set(mode='OBJECT')
    final=check('renamed-then-edited-without-reapply')
    bpy.context.preferences.filepaths.save_version=0
    saved=args.output/'live-bone-rest.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved),relative_remap=False)
    bpy.ops.wm.open_mainfile(filepath=str(saved),load_ui=False)
    reopened=check('reopened-without-reapply');assert np.array_equal(reopened,final)
    baseline=snapshot(obj)
    remove(obj,MATERIAL,slot=1)
    assert snapshot(obj)==baseline and len(bpy.data.node_groups)==groups
    assert sha256(args.model.read_bytes()).hexdigest()==source_hash
    (args.output/'results.json').write_text(json.dumps(dict(model=str(args.model),source_sha256=source_hash,
        checks=reports,native_RNA_only=True,rebuild_match=True,undo_redo=True,reopen=True,removal=True),indent=2))
    print('PASS LIVE_BONE_REST',len(reports),'states',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for key in ('model','mmd-tools','output'):parser.add_argument('--'+key,type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
