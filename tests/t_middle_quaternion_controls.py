"""Real creator PMX, actual PointLight X, native-calibrated quaternion/YXZ controls."""
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Euler

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer,model_object,resolve_textures,snapshot
from t_middle_converters import set_scene
from t_middle_vertex import field
from ninesync_fx.runtime.middle.targets import apply,remove,retarget,inspect_bindings,MATERIAL,TOKEN,_core_node
from ninesync_fx.runtime.middle.vertex import VERTEX_CORE
from ninesync_fx.runtime.middle.controllers import resolve,AbsentMorph
from ninesync_fx.runtime.middle.blender_raster import read_source

UNITS=.08
POSES={0:(20,-35,50),10:(30,170,-15),20:(30,190,-15),30:(-40,385,75)}
OPACITY={0:.25,10:0,20:1,30:.7}
CANONICAL_NATIVE={0:'0',10:'10',20:'40',30:'50'}


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    hashes={str(path):sha256(path.read_bytes()).hexdigest() for path in (args.model,args.accessory)}
    native={row['name']:row for row in json.loads(args.native_values.read_text())}
    pmx,importer=load_importer(args.mmd_tools)
    scene=set_scene();model=pmx.load(str(args.model));resolve_textures(model,args.model,args.model.parent/'tex')
    importer().execute(pmx=model,scale=UNITS,types={'MESH','ARMATURE','MORPHS'},fix_bone_order=False)
    obj=model_object(scene,model);obj_name=obj.name;original=snapshot(obj)
    slots=tuple(slot.material for slot in obj.material_slots);groups=len(bpy.data.node_groups)
    from harness_fx import mb
    from ninesync_fx.accessories.standalone_import import import_plain_accessory
    root=import_plain_accessory(bpy.context,str(args.accessory),UNITS)
    root['mmd_bridge_accessory']=True;root['ninesync_mme_name']='Owner-A.x'
    root.rotation_mode='QUATERNION';root.scale=(.1,)*3;root_name=root.name
    for frame,angles in POSES.items():
        root.rotation_quaternion=Euler(tuple(-math.radians(angles[k]) for k in (0,2,1)),'YXZ').to_quaternion()
        root['mme_tr']=OPACITY[frame]
        root.keyframe_insert('rotation_quaternion',frame=frame);root.keyframe_insert('["mme_tr"]',frame=frame)
    fx=Path(__file__).parent/'fixtures/middle/quaternion_controls.fx'
    result=apply(obj,fx,MATERIAL,slot=args.slot,units=UNITS)
    reports=[];snapshots={}

    def core():
        mat=obj.material_slots[args.slot].material
        return _core_node(mat.node_tree,mat[TOKEN]).node_tree[VERTEX_CORE]

    def check(label,frame=None,controller=None,native_frame=None,self_tr=0):
        controller=controller or root
        if frame is not None:scene.frame_set(frame)
        bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
        evaluated_controller=controller.evaluated_get(deps)
        if controller.rotation_mode=='QUATERNION':
            angles=evaluated_controller.rotation_quaternion.normalized().to_euler('YXZ')
        else:angles=evaluated_controller.rotation_euler
        expected_angles=np.array([-angles[i] for i in (0,2,1)])
        swap=np.eye(4)[:,(0,2,1,3)]
        world=(np.diag([1/UNITS]*3+[1.0])@swap@np.array(evaluated_controller.matrix_world)@np.diag([10*UNITS]*3+[1.0])@swap).T
        expected=np.array([np.array([.1,.2,.3,1])@world,
                           [*expected_angles,evaluated_controller['mme_tr']],
                           [*expected_angles,self_tr]])
        sources=resolve(scene,obj,'Owner-A.x','Rxyz',3,{'Owner-A.x':controller},UNITS)[1]
        numeric=np.array([read_source(s,deps) for s in sources])
        assert np.max(np.abs(numeric-expected_angles))<2e-6,(label,numeric,expected_angles)
        diagnostics=inspect_bindings(obj,MATERIAL,slot=args.slot)
        assert all('uses the stored object' in message for message in diagnostics),diagnostics
        geometry=obj.evaluated_get(deps);mesh=geometry.to_mesh()
        try:
            selected=np.repeat([p.material_index==args.slot for p in mesh.polygons],[p.loop_total for p in mesh.polygons])
            actual=np.column_stack([field(mesh,core(),f'TEXCOORD{row}.{col}') for row in range(3) for col in range(4)])[selected]
            error=float(np.max(np.abs(actual-expected.reshape(12))))
            assert error<2e-5,(label,error,actual[0],expected)
        finally:geometry.to_mesh_clear()
        record=dict(label=label,frame=scene.frame_current,maximum_field_error=error,angles=expected_angles.tolist(),
                    expected=expected.tolist(),mode=controller.rotation_mode,diagnostics=diagnostics)
        if native_frame is not None:
            record['native_angle_error']=float(np.max(np.abs(expected_angles-native[str(native_frame)]['angles'])))
            record['native_matrix_error']=float(np.max(np.abs(world[:3,:3]-native[str(native_frame)]['matrix'])))
            assert record['native_angle_error']<2e-4,record
            assert record['native_matrix_error']<2.1e-5,record
        assert snapshot(obj)==original
        reports.append(record);print('ROTATION_STATE',label,'PASS',flush=True)
        return actual.copy()

    # Sequential and nonsequential seeks must be independent of visited frames.
    for frame in (0,10,20,30,10,0,30,20):
        actual=check('quaternion-seek',frame,native_frame=CANONICAL_NATIVE[frame])
        if frame in snapshots:assert np.array_equal(actual,snapshots[frame])
        snapshots[frame]=actual
    before=check('positive-q',20,native_frame='40')
    root.rotation_quaternion=tuple(-v for v in root.rotation_quaternion);root.update_tag()
    negative=check('negative-q',native_frame='40');assert np.array_equal(before,negative)
    check('interpolated-Blender-quaternion',15)
    # A parent changes world transforms but must not alter local panel angles.
    parent=root.copy();parent.animation_data_clear();parent.rotation_mode='XYZ';parent.rotation_euler=(.2,.1,-.3)
    parent.location=(.4,-.3,.2);parent.scale=(1,)*3;scene.collection.objects.link(parent)
    root.parent=parent;check('parented-local-quaternion',10)
    root.parent=None;bpy.data.objects.remove(parent,do_unlink=True)
    # Mode changes invalidate warm reuse instead of silently keeping stale inputs.
    root.rotation_mode='YXZ';root.rotation_euler=tuple(-math.radians(POSES[30][k]) for k in (0,2,1));root.update_tag()
    assert any('rotation mode changed' in m for m in inspect_bindings(obj,MATERIAL,slot=args.slot))
    assert apply(obj,fx,MATERIAL,slot=args.slot,units=UNITS).rebuilt
    check('YXZ-authored-385-degrees',native_frame='30')
    root.rotation_mode='QUATERNION';root.update_tag()
    assert apply(obj,fx,MATERIAL,slot=args.slot,units=UNITS).rebuilt
    final=check('quaternion-restored',20,native_frame='40')
    assert not apply(obj,fx,MATERIAL,slot=args.slot,units=UNITS).rebuilt
    apply(obj,fx,MATERIAL,slot=args.slot,units=UNITS,force_rebuild=True)
    assert np.array_equal(final,check('forced-rebuild',20,native_frame='40'))
    replacement=import_plain_accessory(bpy.context,str(args.accessory),UNITS)
    replacement['mmd_bridge_accessory']=True;replacement['mme_tr']=.4;replacement.scale=(.1,)*3
    replacement.rotation_mode='QUATERNION';replacement.rotation_quaternion=Euler(tuple(-math.radians(POSES[0][k]) for k in (0,2,1)),'YXZ').to_quaternion()
    retarget(obj,MATERIAL,'Owner-A.x',replacement,slot=args.slot)
    check('retarget-quaternion',controller=replacement,native_frame='0')
    replacement.rotation_mode='XZY'
    try:retarget(obj,MATERIAL,'Owner-A.x',replacement,slot=args.slot)
    except Exception as error:assert 'rotation mode' in str(error)
    else:raise AssertionError('Unsupported rotation mode was accepted')
    replacement.rotation_mode='QUATERNION'
    retarget(obj,MATERIAL,'Owner-A.x',root,slot=args.slot);bpy.data.objects.remove(replacement,do_unlink=True)
    check('retarget-restored',20,native_frame='40')
    # PMX Tr is a scalar morph name, not accessory opacity or material alpha.
    target,sources=resolve(scene,obj,'(self)','Tr',1,{},UNITS)
    assert target==obj and isinstance(sources[0],AbsentMorph) and sources[0]==0
    obj['mme_tr']=.9
    assert isinstance(resolve(scene,obj,'(self)','Tr',1,{},UNITS)[1][0],AbsentMorph)
    key=obj.shape_key_add(name='Tr');key.value=.63;bpy.context.view_layer.update()
    actual=read_source(resolve(scene,obj,'(self)','Tr',1,{},UNITS)[1][0],bpy.context.evaluated_depsgraph_get())
    assert abs(actual-.63)<1e-6
    without_tr=original;original=snapshot(obj)
    assert any('now present' in m for m in inspect_bindings(obj,MATERIAL,slot=args.slot))
    assert apply(obj,fx,MATERIAL,slot=args.slot,units=UNITS).rebuilt
    for weight in (0,.25,1):
        key.value=weight;bpy.context.view_layer.update()
        check('present-PMX-Tr-morph',self_tr=weight)
    obj.shape_key_remove(key);original=without_tr;del obj['mme_tr'];assert snapshot(obj)==original
    assert inspect_bindings(obj,MATERIAL,slot=args.slot)
    assert apply(obj,fx,MATERIAL,slot=args.slot,units=UNITS).rebuilt
    check('removed-PMX-Tr-morph',20,native_frame='40')
    assert sha256(args.model.read_bytes()).hexdigest()==hashes[str(args.model)]
    saved=args.output/'quaternion-controllers.blend';scene.frame_set(20)
    bpy.context.preferences.filepaths.save_version=0;bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(saved),relative_remap=False)
    bpy.ops.wm.open_mainfile(filepath=str(saved),load_ui=False)
    scene=bpy.context.scene;obj=bpy.data.objects[obj_name];root=bpy.data.objects[root_name];original=snapshot(obj)
    assert np.array_equal(final,check('reopened',20,native_frame='40'))
    remove(obj,MATERIAL,slot=args.slot)
    assert snapshot(obj)==original and len(bpy.data.node_groups)==groups
    for path,digest in hashes.items():assert sha256(Path(path).read_bytes()).hexdigest()==digest
    report=dict(source_hashes=hashes,model=str(args.model),object_name=obj_name,slot=args.slot,checks=reports,
                native_values=str(args.native_values),native_values_sha256=sha256(args.native_values.read_bytes()).hexdigest(),
                source_preserved=True,quaternion_sign_equivalence=True,seeks=True,reopen=True,removal=True,
                policy='Authored XYZ/YXZ Euler angles retain turns. Quaternion input uses canonical YXZ decomposition; turns are not encoded by a quaternion.')
    (args.output/'results.json').write_text(json.dumps(report,indent=2))
    print('PASS REAL_QUATERNION_CONTROLLERS',len(reports),'states',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('model','accessory','mmd-tools','native-values','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--slot',type=int,default=7)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
