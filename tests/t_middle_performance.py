"""Fresh-process headless baseline; deliberately does not report viewport FPS."""
import argparse
import ctypes
from ctypes import wintypes
from hashlib import sha256
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer,model_object,resolve_textures,position_camera
from t_middle_converters import set_scene,add_empty
from ninesync_fx.runtime.middle.targets import apply,MATERIAL,DEFORM

FIX=Path(__file__).parent/'fixtures/middle'


def memory():
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(key,ctypes.c_size_t) for key in
            ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
             'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
    value=Counters();value.cb=ctypes.sizeof(value)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    psapi=ctypes.WinDLL('psapi',use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(value),value.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return dict(working_set_bytes=value.WorkingSetSize,peak_working_set_bytes=value.PeakWorkingSetSize)


def stats(values):
    return dict(median_ms=statistics.median(values)*1000,p95_ms=float(np.percentile(values,95))*1000,
                samples_ms=[value*1000 for value in values])


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    pmx,importer=load_importer(args.mmd_tools)
    scene=set_scene();scene.render.engine='BLENDER_EEVEE';scene.render.fps=30
    scene.render.resolution_x=scene.render.resolution_y=384;scene.eevee.taa_render_samples=16
    scene.render.film_transparent=True
    catalog=json.loads(args.catalog.read_text(encoding='utf-8'))['models']
    families=['schwarz_marisa','Keiki','Arlvit-Sakuya'] if args.multi else ['schwarz_marisa']
    models=[];hashes={};counts=[]
    for index,family in enumerate(families):
        record=next(row for row in catalog if row['family']==family)
        path=Path(record['path']);hashes[str(path)]=sha256(path.read_bytes()).hexdigest()
        model=pmx.load(str(path));resolve_textures(model,path,path.parent/'tex')
        importer().execute(pmx=model,scale=.08,types={'MESH','ARMATURE','MORPHS'},fix_bone_order=False)
        obj=model_object(scene,model)
        root=obj
        while root.parent:root=root.parent
        root.location.x=index*1.8
        rig=obj.find_armature()
        # Animate a weighted source bone without adding physics or changing rest data.
        bone=next((b for b in rig.pose.bones if b.name in obj.vertex_groups and not b.constraints),None)
        if bone is None:bone=next(b for b in rig.pose.bones if b.name in obj.vertex_groups)
        for frame,x in ((1,0),(60,.04)):
            bone.location.x=x;bone.keyframe_insert('location',frame=frame)
        models.append(obj)
        counts.append(dict(family=family,source_vertices=len(obj.data.vertices),source_faces=len(obj.data.polygons)))
    position_camera(scene,models[0])
    if args.multi:
        scene.camera.location.x+=1.8;scene.camera.data.ortho_scale*=2.5
    light=add_empty(scene,'Performance light',(-.65,-1,.85))
    stage_seconds=[]
    for obj in models:
        slot=max(range(len(obj.material_slots)),key=lambda i:sum(p.loop_total for p in obj.data.polygons if p.material_index==i))
        start=perf_counter()
        if args.mode=='material':apply(obj,args.surface,MATERIAL,slot=slot,units=.08,light=light)
        elif args.mode=='seams':apply(obj,FIX/'corner_deform.fx',MATERIAL,slot=slot,units=.08)
        elif args.mode.startswith('copies'):
            # Each configuration has its own independent diagnostic source.
            # A modifier wrapper links its inputs into the core; changing a
            # linked core-node default would leave the actual count unchanged.
            fixture=args.output/'instances.fx'
            fixture.write_text((FIX/'model_instances.fx').read_text().replace('= 2;',f'= {args.mode[-1]};').replace('= 3;','= 1;'))
            apply(obj,fixture,DEFORM,units=.08)
        stage_seconds.append(perf_counter()-start)
    for frame in range(1,11):
        scene.frame_set(frame);bpy.context.view_layer.update()
    memory_after_warmup=memory()
    times=[]
    for frame in range(11,61):
        start=perf_counter();scene.frame_set(frame);bpy.context.view_layer.update();times.append(perf_counter()-start)
    evaluation_memory=memory()
    materialization=[]
    for frame in range(11,61):
        scene.frame_set(frame);bpy.context.view_layer.update()
        start=perf_counter()
        for obj in models:
            evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());evaluated.to_mesh();evaluated.to_mesh_clear()
        materialization.append(perf_counter()-start)
    for obj,row in zip(models,counts):
        evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=evaluated.to_mesh()
        try:row.update(evaluated_vertices=len(mesh.vertices),evaluated_faces=len(mesh.polygons))
        finally:evaluated.to_mesh_clear()
        if args.mode.startswith('copies'):
            copies=int(args.mode[-1])
            assert row['evaluated_vertices']==row['source_vertices']*copies,row
            assert row['evaluated_faces']==row['source_faces']*copies,row
    # Capture fixed poses for later optimization comparisons; rendering is timed separately.
    renders=[]
    for frame in (1,30,60):
        scene.frame_set(frame);scene.render.filepath=str(args.output/f'frame-{frame}.png')
        scene.render.image_settings.file_format='PNG'
        start=perf_counter();bpy.ops.render.render(write_still=True);renders.append(dict(frame=frame,seconds=perf_counter()-start,path=scene.render.filepath))
    for path,digest in hashes.items():assert sha256(Path(path).read_bytes()).hexdigest()==digest
    report=dict(mode=args.mode,multi=args.multi,models=counts,blender=bpy.app.version_string,
        frame_range=[11,60],warmup_frames=10,fps=30,resolution=[384,384],render_engine='EEVEE',taa_render_samples=16,
        dependency_graph=stats(times),mesh_materialization=stats(materialization),conversion_seconds=stage_seconds,
        memory_after_warmup=memory_after_warmup,memory_after_evaluation=evaluation_memory,memory_after_render=memory(),
        rendered_poses=renders,source_hashes=hashes,source_preserved=True,
        displayed_frame_time_ms=None,skipped_display_frames=None,
        scope='Headless dependency-graph evaluation and mesh materialization. Displayed viewport playback and skipped frames are unmeasured.')
    (args.output/'results.json').write_text(json.dumps(report,indent=2))
    print('PASS PERFORMANCE_BASELINE',args.mode,args.multi,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--mmd-tools',type=Path,required=True)
    parser.add_argument('--surface',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mode',choices=['source','material','seams','copies1','copies2','copies6'],required=True)
    parser.add_argument('--multi',action='store_true')
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
