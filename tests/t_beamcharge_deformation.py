"""Original BeamCharge vertex extraction with independent shader execution."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer,model_object,resolve_textures,snapshot,position_camera
from t_middle_converters import set_scene
from t_middle_instances import read
from ninesync_fx.fx.entry import fx_entry,fx_run_entry
from ninesync_fx.fx.errors import FxUnsupported
from ninesync_fx.fx.interp.executor import FxExec
from ninesync_fx.fx.interp.numpy_backend import FxNumpyBackend
from ninesync_fx.fx.textures import FxTextureStore
from ninesync_fx.fx.types import FxValue
from ninesync_fx.runtime.middle.analysis import analyze
from ninesync_fx.runtime.middle.targets import apply,remove,DEFORM,MATERIAL,TOKEN,_core_node
from ninesync_fx.runtime.middle.controllers import resolve
from ninesync_fx.runtime.middle.blender_raster import snapshot_geometry,make_state,texture_loader
from ninesync_fx.runtime.middle.render_execution import RasterEnvironment
from ninesync_fx.runtime.middle.images import MIP_LEVEL,views_current

UNITS=.08

def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    hashes={p.name:sha256(p.read_bytes()).hexdigest() for p in args.root.iterdir() if p.is_file()}
    pmx,importer=load_importer(args.mmd_tools)
    from mmd_tools.core.pmd.importer import import_pmd_to_pmx
    model=import_pmd_to_pmx(str(args.root/'BeamCharge.pmd'))
    scene=set_scene();resolve_textures(model,args.root/'BeamCharge.pmd',args.root/'textures')
    importer().execute(pmx=model,scale=UNITS,types={'MESH','ARMATURE','MORPHS'},fix_bone_order=False)
    obj=model_object(scene,model);position_camera(scene,obj)
    from harness_fx import mb
    from ninesync_fx.accessories.standalone_import import import_plain_accessory
    companion=import_plain_accessory(bpy.context,str(args.root/'BeamCharge_Tgt.x'),UNITS)
    bindings={'BeamCharge_Tgt.x':companion}
    annotation=bpy.data.node_groups.new('Artist corner correspondence','GeometryNodeTree')
    for direction in ('INPUT','OUTPUT'):annotation.interface.new_socket('Geometry',in_out=direction,socket_type='NodeSocketGeometry')
    entry=annotation.nodes.new('NodeGroupInput');output=annotation.nodes.new('NodeGroupOutput')
    store=annotation.nodes.new('GeometryNodeStoreNamedAttribute');store.data_type='INT';store.domain='CORNER'
    store.inputs['Name'].default_value='diagnostic_source_corner';index=annotation.nodes.new('GeometryNodeInputIndex')
    annotation.links.new(index.outputs[0],store.inputs['Value']);annotation.links.new(entry.outputs['Geometry'],store.inputs['Geometry'])
    annotation.links.new(store.outputs['Geometry'],output.inputs['Geometry'])
    obj.modifiers.new('Artist corner correspondence','NODES').node_group=annotation
    for name,values in (('\u8ddd\u96e2',(.1,.2)),('\u30e9\u30a4\u30f3Si',(.05,.08)),('\u56de\u8ee2',(.1,.3)),('\u8ddd\u96e2rnd',(.1,.25))):
        _target,sources=resolve(scene,obj,'(self)',name,1,bindings,UNITS)
        key=sources[0].id.key_blocks[name]
        for frame,value in zip((0,10),values):key.value=value;key.keyframe_insert('value',frame=frame)
    rig=obj.find_armature();bone=rig.pose.bones['\u30bb\u30f3\u30bf\u30fc']
    for frame,x in ((0,0.),(10,.01)):
        bone.location.x=x;bone.keyframe_insert('location',frame=frame)
        companion.location.x=x*.5;companion.keyframe_insert('location',frame=frame)
    fx=args.root/'BeamCharge.fx';facts=analyze(fx,destination=DEFORM)
    assert DEFORM in facts.targets and MATERIAL not in facts.targets
    assert not any(name.startswith('param_') for name,*rest in facts.controller_refs)
    try:apply(obj,fx,MATERIAL,units=UNITS,bindings=bindings)
    except FxUnsupported as error:assert 'pass states' in str(error)
    else:raise AssertionError('Object-pass raster states were silently omitted by material conversion')
    original=snapshot(obj);baselines={};expected={}
    for frame,copies in ((0,3),(10,7)):
        scene.frame_set(frame);baselines[frame]=read(obj)
        stream=snapshot_geometry(scene,obj,UNITS)
        state,images,technique=make_state(scene,obj,stream,0,facts.program,UNITS,bindings=bindings)
        state.params['CloneNum']=[copies]
        env=RasterEnvironment(facts.program,state,FxTextureStore(facts.program,texture_loader),{},images)
        execution=FxExec(facts.program,FxNumpyBackend(),env)
        count=len(stream.geometry.inputs['POSITION0'])
        execution.overrides['index']=FxValue(('i',()),[np.repeat(np.arange(copies),count)])
        def vertex(semantic,t):
            values=np.tile(stream.geometry.inputs[semantic],(copies,1))
            return FxValue(('f',(values.shape[1],)),list(values.T))
        function,arguments=fx_entry(facts.program,technique.passes[0].vs)
        clip=fx_run_entry(execution,function,arguments,vertex)['POSITION0']
        values=np.column_stack([np.broadcast_to(v,count*copies) for v in clip.c])
        local=values @ np.linalg.inv(state.world @ state.view @ state.proj)
        expected[frame]=(local[:,:3]/local[:,3:])[:,[0,2,1]]*UNITS
    result=apply(obj,fx,DEFORM,units=UNITS,bindings=bindings)
    assert any('Vertex-only' in w for w in result.warnings)
    assert not any('Explicit FX texture LOD' in w for w in result.warnings)
    assert not any(node.bl_idname=='GeometryNodeImageTexture' and node.inputs['Image'].default_value.get(MIP_LEVEL)
                   for node in result.core.nodes)
    reports=[]
    for frame,copies in ((0,3),(10,7),(0,3)):
        scene.frame_set(frame)
        node=_core_node(result.destination.node_group,result.core[TOKEN])
        for link in list(node.inputs['CloneNum'].links):result.destination.node_group.links.remove(link)
        node.inputs['CloneNum'].default_value=copies;obj.update_tag()
        bpy.context.view_layer.update();actual=read(obj);base=baselines[frame]
        error=float(np.max(np.abs(actual['points']-expected[frame])))
        assert error<5e-5,(frame,error)
        ids=actual['ids'];np.testing.assert_allclose(actual['normals'],base['normals'][ids],atol=2e-5)
        np.testing.assert_array_equal(actual['materials'],base['materials'][ids])
        for name,uv in actual['uv'].items():np.testing.assert_array_equal(uv,base['uv'][name][ids])
        assert actual['faces']==copies*base['faces']
        reports.append(dict(frame=frame,copies=copies,maximum_position_error=error,faces=actual['faces']))
    destination=result.destination
    result=apply(obj,fx,DEFORM,units=UNITS,force_rebuild=True)
    assert result.destination==destination
    bpy.context.view_layer.update();before=read(obj)['points']
    np.testing.assert_allclose(before,expected[0],atol=5e-5)
    assert views_current([result.core])
    filename=args.output/'beamcharge.blend';obj_name=obj.name
    bpy.context.preferences.filepaths.save_version=0;bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(filename),relative_remap=False)
    remove(obj,DEFORM);assert snapshot(obj)==original
    bpy.ops.wm.open_mainfile(filepath=str(filename),load_ui=False)
    obj=bpy.context.scene.objects[obj_name]
    np.testing.assert_array_equal(read(obj)['points'],before)
    remove(obj,DEFORM)
    for name,digest in hashes.items():assert sha256((args.root/name).read_bytes()).hexdigest()==digest
    (args.output/'results.json').write_text(json.dumps(dict(status='PASS_VERTEX_EXTRACTION',source_hashes=hashes,checks=reports,
        native_full_effect_parity=False,reference='original HLSL through NumPy with native-verified vertex sampler defaults',
        source_normals_uvs_materials_preserved=True,rebuild=True,exact_reopen=True,source_unchanged=True),indent=2))
    print('PASS ORIGINAL_BEAMCHARGE_VERTEX_EXTRACTION',reports,flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--mmd-tools',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
