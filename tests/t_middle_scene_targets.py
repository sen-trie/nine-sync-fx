"""Two real owners, an animated PMX, actual scene draws and sampled composites."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer,model_object,resolve_textures,snapshot,render_image
from t_middle_converters import set_scene,add_empty
from ninesync_fx.runtime.middle.scene_targets import create,bake,composite,restore,discard,discard_composite
from ninesync_fx.runtime.middle.targets import inspect_bindings,MATERIAL
from ninesync_fx.runtime.middle.targets import apply, retarget
from ninesync_fx.runtime.middle.pass_context import PassContext
from ninesync_fx.fx.errors import FxUnsupported

FIX=Path(__file__).parent/"fixtures/middle"


def prepare(args,pmx):
    records=json.loads((args.output/"model-catalog.json").read_text(encoding="utf-8"))["models"]
    record=next(row for row in records if row["family"]=="schwarz_marisa")
    path=Path(record["path"])
    model=pmx.load(str(path))
    model.name=model.name_e="Marisa-Target"
    resolve_textures(model,path,path.parent/"tex")
    for texture in model.textures:
        p=Path(texture.path);texture.path=str(p if p.is_absolute() else path.parent/p)
    pmx.save(str(args.output/"Marisa-target.pmx"),model)
    (args.output/"scene_target_draw.fx").write_bytes((FIX/"scene_target_draw.fx").read_bytes())
    source=(FIX/"scene_target_owner.fx").read_text()
    for index,name in enumerate(("Owner-A","Owner-B")):
        (args.output/(name+".fx")).write_text("#define SIDE "+str(index)+"\n"+source)
        (args.output/(name+".x")).write_bytes(args.accessory.read_bytes())
    (args.output/"TargetCamera.x").write_bytes(args.accessory.read_bytes())
    (args.output/"TargetCamera.fx").write_text('technique Main < string MMDPass="object"; > {}\n')
    (args.output/"hide.fx").write_text('technique Main < string MMDPass="object"; > {}\ntechnique Shadow < string MMDPass="object_ss"; > {}\n')
    from mmd_tools.core import vmd
    motion=vmd.File();motion.header=vmd.Header();motion.header.model_name=model.name
    motion.boneAnimation=vmd.BoneAnimation()
    center=next(b.name for b in model.bones if b.name=="\u30bb\u30f3\u30bf\u30fc")
    for frame,x in ((0,0),(10,1),(20,-1)):
        key=vmd.BoneFrameKey();key.frame_number=frame;key.location=(x,0,0);key.rotation=(0,0,0,1);key.interp=[20]*64
        motion.boneAnimation[center].append(key)
    motion.save(filepath=str(args.output/"model-motion.vmd"))
    print("PREPARED_SCENE_TARGET_INPUTS",record["family"],flush=True)


def run(args,pmx,importer):
    source=set_scene()
    source.render.fps=30
    model=pmx.load(str(args.output/"Marisa-target.pmx"))
    importer().execute(pmx=model,scale=.08,types={"MESH","ARMATURE","MORPHS"},fix_bone_order=False)
    obj=model_object(source,model)
    source.frame_set(0)
    from mmd_tools.core.vmd.importer import VMDImporter
    VMDImporter(str(args.output/"model-motion.vmd"),scale=.08,frame_margin=0).assign(obj.find_armature())
    import math
    camera=source.camera
    camera.location,camera.rotation_euler=(0,-45*.08,10*.08),(math.pi/2,0,0)
    camera.data.type,camera.data.sensor_fit="PERSP","VERTICAL"
    camera.data.lens=camera.data.sensor_height/(2*math.tan(math.radians(30)/2))
    from ninesync_fx.accessories.standalone_import import import_plain_accessory
    from harness_fx import mb  # Register the existing X importer settings.
    owner_a=import_plain_accessory(bpy.context,str(args.output/"Owner-A.x"),.08)
    owner_b=import_plain_accessory(bpy.context,str(args.output/"Owner-B.x"),.08)
    control=import_plain_accessory(bpy.context,str(args.output/"TargetCamera.x"),.08)
    control.name="Artist camera control"
    # MME reads accessory Si and basis size at ten times the native panel value.
    for owner in (owner_a,owner_b):
        assert owner.type=="EMPTY"
        owner["mmd_bridge_accessory"]=True
        owner.rotation_mode="XYZ"
    owner_b.location.x=4*.08
    owner_b.scale=(2,)*3
    for frame,si,x,offset in ((0,1,0,0),(10,1.5,2,-.5),(20,.75,-1,.5),(30,.5,3,.5)):
        owner_a.scale=(si,)*3;owner_a.keyframe_insert("scale",frame=frame)
        owner_a.location.x=x*.08;owner_a.keyframe_insert("location",frame=frame)
        control.location.x=offset*.08;control.keyframe_insert("location",frame=frame)
    # Controller dependencies are supplied to the pass via the existing source hierarchy.
    source.frame_set(0)
    original=snapshot(obj)
    counts_before={key:len(getattr(bpy.data,key)) for key in ("objects","meshes","materials","node_groups","worlds","scenes","images","cameras")}
    unsupported=args.output/"unsupported-target.fx"
    unsupported.write_text((args.output/"Owner-A.fx").read_text().replace("int Width=256;","float2 ViewPortRatio={1,1}; int Width=256;"))
    try:
        create(source,owner_a,unsupported,"HitRT",camera,[(obj,args.output/"scene_target_draw.fx")])
    except FxUnsupported as error:
        assert "ViewPortRatio" in str(error),str(error)
    else:
        raise AssertionError("An unimplemented target-size annotation was accepted")
    assert counts_before=={key:len(getattr(bpy.data,key)) for key in counts_before}
    targets=[]
    for owner,name in ((owner_a,"Owner-A"),(owner_b,"Owner-B")):
        handle=create(source,owner,args.output/(name+".fx"),"HitRT",camera,
                      [(obj,args.output/"scene_target_draw.fx")],bindings={"TargetCamera.x":control})
        assert handle.scene.objects.get(control.name) == control
        targets.append(handle)
    pass_scene = targets[0].scene
    clone = pass_scene["ninesync_draw_0"]
    slot = clone.data.polygons[0].material_index
    previous = clone.material_slots[slot].material
    try:
        apply(clone, args.output / "scene_target_draw.fx", MATERIAL, scene=pass_scene, slot=slot,
              units=.08, pass_context=PassContext(pass_scene, owner=owner_a, bindings={"TargetCamera.x":control}),
              bindings={"TargetCamera.x":owner_a})
    except FxUnsupported as error:
        assert "conflicts with the specialist's pass mapping" in str(error), error
    else:
        raise AssertionError("Conflicting caller mapping replaced a specialist binding")
    for name in ("(OffscreenOwner)", "TargetCamera.x"):
        try:
            retarget(clone, MATERIAL, name, owner_b, scene=pass_scene, slot=slot)
        except FxUnsupported as error:
            assert "effect specialist supplies" in str(error), error
        else:
            raise AssertionError("Retarget replaced a pass-owned binding")
    assert clone.material_slots[slot].material == previous
    screen=composite(targets,[args.output/"Owner-A.fx",args.output/"Owner-B.fx"])
    # Coverage-changing fullscreen shaders must fail before replacing a core.
    plane=next(o for o in screen.objects if o.type=="MESH")
    previous_material=plane.material_slots[0].material
    previous_groups=set(bpy.data.node_groups)
    coverage=args.output/"unsupported-screen.fx"
    coverage.write_text((args.output/"Owner-A.fx").read_text().replace("o.p=p;","o.p=p*.5;"))
    try:
        apply(plane,coverage,MATERIAL,scene=screen,slot=0,units=1,
              pass_context=PassContext(screen,images={targets[0].name:targets[0].image},screen=True))
    except FxUnsupported as error:
        assert "unchanged fullscreen" in str(error),str(error)
    else:
        raise AssertionError("Changed fullscreen coverage was accepted")
    assert plane.material_slots[0].material==previous_material and set(bpy.data.node_groups)==previous_groups
    grass_result=None
    if args.grass:
        previous_scenes=set(bpy.data.scenes)
        try:
            create(source,owner_a,args.grass,"HitRT",camera,[(obj,args.grass.with_name("HitObject.fx"))])
        except FxUnsupported as error:
            assert "R16F" in str(error),str(error)
            grass_result=dict(path=str(args.grass),sha256=sha256(args.grass.read_bytes()).hexdigest(),error=str(error))
        else:
            raise AssertionError("Unverified original Grass numeric target was accepted")
        assert set(bpy.data.scenes)==previous_scenes
    reports=[]
    captured={}
    target_pixels={}
    source_name=source.name
    for frame in (0,10,20,30,0):
        paths=bake(targets,frame,args.output/"targets")
        pixels=[]
        for handle in targets:
            values=np.array(handle.image.pixels[:]).reshape(256,256,4)
            pixels.append(values)
        if frame==30:
            assert np.array_equal(pixels[1],target_pixels[20][1]), "Animating one owner contaminated the other target"
            assert not np.array_equal(pixels[0],target_pixels[20][0])
        target_pixels[frame]=pixels
        # The second owner stays constant in color, while the model/camera still animate.
        for handle in targets:
            for index in range(handle.scene["ninesync_target_draw_count"]):
                clone=handle.scene[f"ninesync_draw_{index}"]
                for slot in {p.material_index for p in clone.data.polygons}:
                    diagnostics = inspect_bindings(clone,MATERIAL,slot=slot,scene=handle.scene)
                    assert diagnostics and all("uses the stored object 'Artist camera control'" in message
                                               for message in diagnostics), diagnostics
        image=render_image(screen,args.output,f"composite-{frame}",frame)
        if frame in captured:assert np.array_equal(image,captured[frame])
        captured[frame]=image
        assert snapshot(obj)==original
        reports.append(dict(frame=frame,targets=list(paths),pixel_ranges=[(float(p[:,:,:3].min()),float(p[:,:,:3].max())) for p in pixels]))
    bpy.context.preferences.filepaths.save_version=0
    for h in targets:h.image.pack()
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output/"scene-targets.blend"),relative_remap=False)
    # Reopen the real pass scenes and render a non-saved frame through their IDs.
    bpy.ops.wm.open_mainfile(filepath=str(args.output/"scene-targets.blend"),load_ui=False)
    source=bpy.data.scenes[source_name]
    obj=model_object(source,model)
    original=snapshot(obj)
    targets=[restore(scene) for scene in bpy.data.scenes if "ninesync_target_image" in scene]
    targets.sort(key=lambda h:h.owner.name)
    screen=next(scene for scene in bpy.data.scenes if scene.name.startswith("MME target composite"))
    bake(targets,20,args.output/"reopened-targets")
    reopened=render_image(screen,args.output,"composite-reopened",20)
    assert np.array_equal(reopened,captured[20]), "Saved pass scenes changed the composite"
    # Failure in the second target must not publish the first target's new pixels.
    previous=[np.array(h.image.pixels[:]) for h in targets]
    old_fx=targets[1].scene["ninesync_fx_0"]
    bad=args.output/"unsupported-draw.fx"
    bad.write_text((args.output/"scene_target_draw.fx").read_text().replace("pass Draw {","pass Draw { AlphaBlendEnable=true;"))
    targets[1].scene["ninesync_fx_0"]=str(bad)
    try:
        bake(targets,10,args.output/"failed-targets")
    except FxUnsupported:
        pass
    else:
        raise AssertionError("Unsupported second pass was accepted")
    assert all(np.array_equal(np.array(h.image.pixels[:]),old) for h,old in zip(targets,previous))
    targets[1].scene["ninesync_fx_0"]=old_fx
    bake(targets,20,args.output/"restored-targets")
    assert snapshot(obj)==original
    discard_composite(screen)
    for handle in targets:discard(handle)
    assert snapshot(obj)==original
    assert not any("ninesync_scene_target" in scene for scene in bpy.data.scenes)
    counts_after={key:len(getattr(bpy.data,key)) for key in counts_before}
    # Reopen discards factory-startup orphan meshes/materials/cameras. No
    # collection may grow, and no renderer-owned datablock may remain.
    assert all(counts_after[key]<=counts_before[key] for key in counts_before),(counts_before,counts_after)
    assert not any(data.get("ninesync_scene_target") for key in counts_before for data in getattr(bpy.data,key))
    (args.output/"scene-target-results.json").write_text(json.dumps(dict(checks=reports,source_model="schwarz_marisa",source_preserved=True,
        exact_reopen=True,atomic_publication_failure_verified=True,owner_isolation_verified=True,explicit_named_bindings_verified=True,
        changed_screen_coverage_rejected=True,grass=grass_result,
        unsupported_target_annotation_rejected=True,
        counts_before=counts_before,counts_after=counts_after),indent=2))
    print("PASS TWO_OWNER_SCENE_TARGETS",len(reports),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--mmd-tools",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--accessory",type=Path,required=True)
    parser.add_argument("--prepare",action="store_true")
    parser.add_argument("--grass",type=Path)
    args=parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    pmx,importer=load_importer(args.mmd_tools)
    prepare(args,pmx) if args.prepare else run(args,pmx,importer)
