"""Isolate original surface shaders from complex rig evaluation differences."""
import argparse
import copy
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer,model_object,resolve_textures,snapshot,render_image
from t_middle_converters import set_scene,add_empty
from ninesync_fx.runtime.middle.targets import apply,remove,MATERIAL

UNITS=.08

def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    pmx,importer=load_importer(args.mmd_tools)
    if args.prepare:
        source=pmx.load(str(args.model));resolve_textures(source,args.model,args.model.parent/'textures')
        model=pmx.Model();model.name=model.name_e='NineSync material patch'
        model.comment=model.comment_e='Diagnostic flat geometry; original Sanae skin material and textures.'
        model.textures=copy.deepcopy(source.textures)
        for texture in model.textures:
            path=Path(texture.path);texture.path=str(path if path.is_absolute() else args.model.parent/path)
        material=copy.deepcopy(source.materials[1]);material.name=material.name_e='Sanae skin patch'
        material.vertex_count=6
        for field in ('enabled_toon_edge','enabled_drop_shadow','enabled_self_shadow_map','enabled_self_shadow'):
            if hasattr(material,field):setattr(material,field,False)
        model.materials=[material]
        bone=pmx.Bone();bone.name=bone.name_e='Root';bone.location=[0,0,0];bone.parent=-1
        model.bones=[bone]
        for co,uv in zip([(-8,0,0),(-8,16,0),(8,16,0),(8,0,0)],[(0,1),(0,0),(1,0),(1,1)]):
            vertex=pmx.Vertex();vertex.co=list(co);vertex.uv=list(uv);vertex.normal=[0,0,-1];vertex.edge_scale=0
            vertex.weight=pmx.BoneWeight();vertex.weight.bones=[0];model.vertices.append(vertex)
        model.faces=[(0,2,1),(0,3,2)]
        pmx.save(str(args.output/'patch.pmx'),model)
        links=json.loads(args.aliases.read_text(encoding='utf-8'))
        rows=[]
        for row in links:
            if Path(row['source']).stem=='full_SimpleSoftShadow':continue
            source=Path(row['source']);alias=Path(row['native_alias'])
            assert source.read_bytes()==alias.read_bytes()
            rows.append(dict(index=len(rows),label=row.get('name') or source.stem,source=str(source),native_alias=str(alias),sha256=sha256(source.read_bytes()).hexdigest()))
        (args.output/'sources.json').write_text(json.dumps(rows,ensure_ascii=True,indent=2))
        (args.output/'provenance.json').write_text(json.dumps(dict(model=str(args.model),model_sha256=sha256(args.model.read_bytes()).hexdigest(),
            diagnostic='4 vertices / 2 triangles, original material slot 1 and original texture resources',material=repr(material.__dict__)),ensure_ascii=True,indent=2))
        print('PREPARED',len(rows),'original sources',flush=True);return
    model=pmx.load(str(args.output/'patch.pmx'))
    scene=set_scene();scene.render.engine='BLENDER_EEVEE'
    scene.render.resolution_x=scene.render.resolution_y=512;scene.eevee.taa_render_samples=args.samples
    scene.render.film_transparent=True;scene.world.use_nodes=True
    scene.world.node_tree.nodes['Background'].inputs['Color'].default_value=(0,0,0,1)
    camera=scene.camera;camera.location=(0,-45*UNITS,8*UNITS);camera.rotation_euler=(math.pi/2,0,0)
    camera.data.type='PERSP';camera.data.sensor_fit='VERTICAL'
    if args.raster_align:
        camera.data.shift_x=-.5/512;camera.data.shift_y=.5/512
    camera.data.lens=camera.data.sensor_height/(2*math.tan(math.radians(30)/2))
    importer().execute(pmx=model,scale=UNITS,types={'MESH','ARMATURE','MORPHS'},fix_bone_order=False)
    obj=model_object(scene,model);light=add_empty(scene,'Native light direction',(.5,-.5,1))
    original=snapshot(obj);reports=[]
    render_output=args.output/(('blender-raster-aligned' if args.raster_align else 'blender')+(f'-samples-{args.samples}' if args.samples!=64 else ''))
    render_output.mkdir(exist_ok=True)
    for row in json.loads((args.output/'sources.json').read_text()):
        fx=Path(row['source']);result=apply(obj,fx,MATERIAL,slot=0,units=UNITS,light=light)
        image=render_image(scene,render_output,f"blender-{row['index']:02}",0)
        assert sha256(fx.read_bytes()).hexdigest()==row['sha256']
        reports.append(dict(row,status=result.status,warnings=result.warnings))
        remove(obj,MATERIAL,slot=0);assert snapshot(obj)==original
        print('RENDERED',row['index'],row['label'],flush=True)
    (render_output/'blender-results.json').write_text(json.dumps(reports,ensure_ascii=True,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--model',type=Path,required=True);parser.add_argument('--mmd-tools',type=Path,required=True)
    parser.add_argument('--aliases',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--prepare',action='store_true');parser.add_argument('--raster-align',action='store_true');parser.add_argument('--samples',type=int,default=64)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
