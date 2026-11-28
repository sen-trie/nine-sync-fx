import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer,model_object,resolve_textures,snapshot,position_camera
from t_middle_converters import set_scene,add_empty
from t_middle_instances import read
from ninesync_fx.fx.errors import FxUnsupported
from ninesync_fx.runtime.middle.analysis import analyze
from ninesync_fx.runtime.middle.targets import apply,remove,retarget,MATERIAL,DEFORM,TOKEN,_core_node
from ninesync_fx.runtime.middle.vertex import VERTEX_CORE


UNITS=.08
FIX=Path(__file__).parent/'fixtures/middle/model_instances.fx'


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    source=FIX.read_text().replace('int Copies < string UIName = "Copies"; > = 2;',
        'float Copies:CONTROLOBJECT <string name="InstanceControl";string item="X";>;')
    source=source.replace('int Layers < string UIName = "Layers"; > = 3;',
        'float Layers:CONTROLOBJECT <string name="InstanceControl";string item="Y";>;')
    source=source.replace('result.position = mul(position, WVP);','position.z += Copies*.01; result.position = mul(position, WVP);')
    fx=args.output/'controlled-instances.fx';fx.write_text(source)
    facts=analyze(fx,1)
    assert {row[0] for row in facts.controller_refs}=={'Copies','Layers'},facts.controller_refs
    pmx,importer=load_importer(args.mmd_tools);scene=set_scene()
    model=pmx.load(str(args.model));resolve_textures(model,args.model,args.model.parent/'textures')
    importer().execute(pmx=model,scale=UNITS,types={'MESH','ARMATURE','MORPHS'},fix_bone_order=False)
    obj=model_object(scene,model);position_camera(scene,obj)
    mesh=obj.data;attribute_names=tuple(mesh.attributes.keys());digest=sha256(args.model.read_bytes()).hexdigest()
    annotation=bpy.data.node_groups.new('Original corner correspondence','GeometryNodeTree')
    for direction in ('INPUT','OUTPUT'):annotation.interface.new_socket('Geometry',in_out=direction,socket_type='NodeSocketGeometry')
    entry=annotation.nodes.new('NodeGroupInput');output=annotation.nodes.new('NodeGroupOutput')
    store=annotation.nodes.new('GeometryNodeStoreNamedAttribute');store.data_type='INT';store.domain='CORNER'
    store.inputs['Name'].default_value='diagnostic_source_corner';index=annotation.nodes.new('GeometryNodeInputIndex')
    annotation.links.new(index.outputs[0],store.inputs['Value']);annotation.links.new(entry.outputs['Geometry'],store.inputs['Geometry'])
    annotation.links.new(store.outputs['Geometry'],output.inputs['Geometry'])
    obj.modifiers.new('Artist corner correspondence','NODES').node_group=annotation
    control=add_empty(scene,'Actual loop controller')
    poses={1:(2.8,2.75),5:(0.,2.75),10:(3.2,1.75),20:(-1.,-2.)}
    for frame,(copies,layers) in poses.items():
        control.location=(copies*UNITS,0,layers*UNITS);control.keyframe_insert('location',frame=frame)
    rig=obj.find_armature();bone=next(b for b in rig.pose.bones if b.name in obj.vertex_groups)
    for frame,x in ((1,0),(10,.1),(20,-.05)):
        bone.location.x=x;bone.keyframe_insert('location',frame=frame)
    original=snapshot(obj);baselines={}
    for frame in poses:
        scene.frame_set(frame);baselines[frame]=read(obj)
    reports=[]
    for target in (MATERIAL,DEFORM):
        result=apply(obj,fx,target,slot=1,units=UNITS,bindings={'InstanceControl':control},interpolate_vertices=True)
        def check(frame,values=None):
            scene.frame_set(frame);bpy.context.view_layer.update()
            copies,layers=values or poses[frame];n=max(0,int(copies));m=max(0,int(layers))
            core=result.core[VERTEX_CORE] if target==MATERIAL else None
            actual=read(obj,core);base=baselines[frame]
            selected=actual['materials']==1 if target==MATERIAL else np.ones(len(actual['ids']),dtype=bool)
            chosen_base=base['materials']==1 if target==MATERIAL else np.ones(len(base['ids']),dtype=bool)
            assert int(selected.sum())==int(chosen_base.sum())*n*m,(target,frame,int(selected.sum()),n,m)
            ids=actual['ids'];expected=base['points'][ids].copy()
            if n*m:
                if target==MATERIAL:
                    outer=actual['outer'][selected];inner=np.floor(actual['loop_id'][selected]/10)
                else:
                    iteration=np.repeat(np.arange(n*m),len(base['ids']));outer=np.floor(iteration/m);inner=iteration%m
                expected[selected,0]+=outer*.4*UNITS
                expected[selected,1]+=copies*.01*UNITS
                expected[selected,2]+=(inner*.2+frame/25*.1)*UNITS
            error=float(np.max(np.abs(actual['points']-expected),initial=0))
            normal_error=float(np.max(np.abs(actual['normals']-base['normals'][ids]),initial=0))
            assert error<3e-6,(target,frame,error)
            assert normal_error<2e-5,(target,frame,normal_error)
            np.testing.assert_array_equal(actual['materials'],base['materials'][ids])
            for name,uv in actual['uv'].items():
                np.testing.assert_array_equal(uv,base['uv'][name][ids])
            if len(ids):assert set(actual['uv'])==set(base['uv'])
            current=snapshot(obj)
            current=current[:3]+(tuple(mod for mod in current[3] if not (mod.type=='NODES' and mod.node_group and mod.node_group.get(TOKEN))),)+current[4:]
            assert current==original,(target,frame,'source datablocks changed')
            assert tuple(mesh.attributes.keys())==attribute_names,(target,frame,'source attributes changed')
            reports.append(dict(target=target,frame=frame,copies=n,layers=m,maximum_position_error=error,maximum_normal_error=normal_error))
            return actual
        for frame in (1,5,10,20,1):check(frame)
        result=apply(obj,fx,target,slot=1,units=UNITS,force_rebuild=True,interpolate_vertices=True);check(10)
        replacement=add_empty(scene,'Replacement loop controller',(1.8*UNITS,0,1.75*UNITS))
        retarget(obj,target,'InstanceControl',replacement,slot=1)
        expected=check(10,(1.8,1.75))
        previous=result.destination
        invalid=args.output/'instance-dependent-count.fx'
        invalid.write_text(source.replace('LoopByCount=Layers;', 'LoopByCount=Outer;'))
        try:apply(obj,invalid,target,slot=1,units=UNITS,bindings={'InstanceControl':replacement},interpolate_vertices=True)
        except FxUnsupported as error:assert 'per-instance counter' in str(error),str(error)
        else:raise AssertionError('An instance-dependent uniform count was accepted')
        assert (obj.material_slots[1].material if target==MATERIAL else obj.modifiers.get(previous.name))==previous
        unknown=args.output/'unknown-count.fx'
        unknown.write_text(source.replace('LoopByCount=Layers;', 'LoopByCount=MissingCount;'))
        try:apply(obj,unknown,target,slot=1,units=UNITS,bindings={'InstanceControl':replacement},interpolate_vertices=True)
        except FxUnsupported as error:assert 'unknown effect parameter' in str(error),str(error)
        else:raise AssertionError('An unknown count silently became one copy')
        assert (obj.material_slots[1].material if target==MATERIAL else obj.modifiers.get(previous.name))==previous
        static=args.output/'static-count.fx'
        static.write_text(source.replace('float Layers:CONTROLOBJECT <string name="InstanceControl";string item="Y";>;',
            'float LayerControl:CONTROLOBJECT <string name="InstanceControl";string item="Y";>; static float Layers=LayerControl+.25;'))
        try:apply(obj,static,target,slot=1,units=UNITS,bindings={'InstanceControl':replacement},interpolate_vertices=True)
        except FxUnsupported as error:assert 'not an exposed native MME effect parameter' in str(error),str(error)
        else:raise AssertionError('An unexposed static count was accepted')
        assert (obj.material_slots[1].material if target==MATERIAL else obj.modifiers.get(previous.name))==previous
        try:apply(obj,fx,target,slot=1,units=UNITS,bindings={'InstanceControl':None},interpolate_vertices=True)
        except FxUnsupported:pass
        else:raise AssertionError('A missing loop controller was accepted')
        assert (obj.material_slots[1].material if target==MATERIAL else obj.modifiers.get(previous.name))==previous
        scene.frame_set(10);obj_name=obj.name
        file=args.output/(target+'.blend');bpy.context.preferences.filepaths.save_version=0;bpy.ops.file.pack_all()
        bpy.ops.wm.save_as_mainfile(filepath=str(file),relative_remap=False)
        remove(obj,target,slot=1);assert snapshot(obj)==original
        bpy.ops.wm.open_mainfile(filepath=str(file),load_ui=False)
        scene=bpy.context.scene;obj=scene.objects[obj_name];mesh=obj.data
        actual=read(obj)
        assert np.array_equal(actual['points'],expected['points'])
        remove(obj,target,slot=1);original=snapshot(obj);control=scene.objects['Actual loop controller']
        assert sha256(args.model.read_bytes()).hexdigest()==digest
    (args.output/'results.json').write_text(json.dumps(dict(status='PASS',source_sha256=digest,checks=reports,
        native_valid_direct_counts=True,static_count_rejected=True,unknown_count_rejected=True,fractional_and_negative_counts=True,zero_copies=True,only_in_script_control=True,
        shared_VS_count_control=True,seam_uvs_exact=True,material_boundaries_exact=True,instance_dependent_count_rejected=True,retarget=True,rebuild=True,missing_binding_rollback=True,exact_reopen=True),indent=2))
    print('PASS CONTROLLER_NESTED_INSTANCES',len(reports),'states',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('model','mmd-tools','output'):parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
