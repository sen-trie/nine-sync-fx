"""Convert the original local ToonDX presets on a real PMX and test their lifecycle."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer, model_object, resolve_textures, position_camera, render_image, snapshot, add_area
from t_middle_converters import set_scene, add_empty
from ninesync_fx.runtime.middle.targets import apply, remove, MATERIAL, CORE_NODE
from ninesync_fx.runtime.middle.images import BUFFER_ATLAS, BUFFER_SOURCE


def close(actual, expected):
    error=float(np.abs(actual-expected).mean())
    assert error<1e-5,error
    return error


def expect_rejection(obj,path,light,fragment):
    before=obj.material_slots[12].material
    groups=set(bpy.data.node_groups)
    try:
        apply(obj,path,MATERIAL,slot=12,units=0.08,light=light)
    except Exception as error:
        assert fragment in str(error),str(error)
    else:
        raise AssertionError("Unsupported intermediate pass was accepted.")
    assert obj.material_slots[12].material==before
    assert set(bpy.data.node_groups)==groups


def run(args):
    pmx,importer=load_importer(args.mmd_tools)
    scene=set_scene()
    scene.render.engine="BLENDER_EEVEE"
    scene.render.resolution_x=scene.render.resolution_y=800
    scene.render.film_transparent=True
    model=pmx.load(str(args.model))
    resolve_textures(model,args.model,args.model.parent/"textures")
    importer().execute(pmx=model,scale=0.08,types={"MESH","ARMATURE","MORPHS"},fix_bone_order=False)
    obj=model_object(scene,model)
    center,span=position_camera(scene,obj)
    add_area(scene,"Key",center,span,(-0.7,-1,1),16)
    add_area(scene,"Fill",center,span,(1,-0.35,0.5),4)
    source=add_empty(scene,"FX light direction",(-0.65,-1,0.85))
    initial=snapshot(obj)
    initial_object_name=obj.name
    slots=tuple(s.material for s in obj.material_slots)
    mapping={0:"\u9854",1:"\u808c",11:"\u9aea",12:"\u670d",13:"\u670d",
             15:"\u670d",16:"\u670d",17:"\u9aea2",18:"\u305d\u306e\u4ed6"}
    original={name:sha256((args.root/(name+".fx")).read_bytes()).hexdigest() for name in set(mapping.values())}
    out=args.output
    out.mkdir(parents=True,exist_ok=True)
    before=render_image(scene,out,"before",1)
    converted={}
    for index,name in mapping.items():
        result=apply(obj,args.root/(name+".fx"),MATERIAL,slot=index,units=0.08,light=source)
        assert result.status=="PARTIAL"
        assert result.core["ninesync_middle_technique"].startswith("MainTec")
        assert result.core["ninesync_middle_technique"] in ("MainTec5", "MainTec7") or index == 15
        converted[index]=result
    assert snapshot(obj)==initial
    after=render_image(scene,out,"toondx",1)
    assert int((after[:, :, 3] > 0.9).sum()) > 0.9 * int((before[:, :, 3] > 0.9).sum())
    from ninesync_fx.runtime.middle.vertex import VERTEX_CORE, stage
    assert stage(obj) is not None
    assert sum(bool(node.get("ninesync_middle_vertex_node")) for node in stage(obj).node_group.nodes) == len(mapping)
    assert all(converted_material.core.get(VERTEX_CORE) for converted_material in converted.values())
    assert len({node.attribute_name for converted_material in converted.values()
                for node in converted_material.core.nodes if node.type == "ATTRIBUTE"}) <= 8
    deps = bpy.context.evaluated_depsgraph_get()
    semantic_checks = {"AMBIENT": 0, "EMISSIVE": 0}
    for index, converted_material in converted.items():
        original_material = slots[index].mmd_material
        for core in (converted_material.core, converted_material.core[VERTEX_CORE]):
            evaluated = core.evaluated_get(deps)
            for node in evaluated.nodes:
                if node.label.startswith("AMBIENT ["):
                    component = int(node.label.partition("[")[2].partition("]")[0])
                    assert abs(node.outputs[0].default_value-original_material.diffuse_color[component]) < 1e-6
                    semantic_checks["AMBIENT"] += 1
                if node.label.startswith("EMISSIVE ["):
                    component = int(node.label.partition("[")[2].partition("]")[0])
                    assert abs(node.outputs[0].default_value-original_material.ambient_color[component]) < 1e-6
                    semantic_checks["EMISSIVE"] += 1
    assert all(semantic_checks.values()), semantic_checks
    delta=float(np.abs(after-before).mean())
    assert delta>0.005,delta
    originals=set(bpy.data.node_groups)
    for index,name in mapping.items():
        result=apply(obj,args.root/(name+".fx"),MATERIAL,slot=index,units=0.08,light=source)
        assert not result.rebuilt
        assert result.core==converted[index].core
    assert set(bpy.data.node_groups)==originals
    def finite_buffer(result):
        return next(node.image for node in result.core.nodes
                    if node.type == "TEX_IMAGE" and node.image and BUFFER_SOURCE in node.image
                    and not node.image.get(BUFFER_ATLAS))

    def mip_atlas(result):
        return next((node.image for node in result.core.nodes
                     if node.type == "TEX_IMAGE" and node.image and node.image.get(BUFFER_ATLAS)), None)

    original_buffer = finite_buffer(converted[1])
    assert tuple(original_buffer.size) == (800, 800)
    atlas = mip_atlas(converted[1])
    assert atlas and atlas.packed_file
    assert tuple(atlas.size) == (800, sum(800 >> level for level in range(10)))
    scene.render.engine = "CYCLES"
    other_engine = apply(obj,args.root/(mapping[1]+".fx"),MATERIAL,slot=1,units=.08,light=source)
    assert other_engine.rebuilt and mip_atlas(other_engine) is None
    scene.render.engine = "BLENDER_EEVEE"
    returned = apply(obj,args.root/(mapping[1]+".fx"),MATERIAL,slot=1,units=.08,light=source)
    assert returned.rebuilt and mip_atlas(returned).packed_file
    artist_material = obj.material_slots[1].material
    artist_node = artist_material.node_tree.nodes.new("ShaderNodeValue")
    artist_node.label = "Artist edit across buffer refresh"
    artist_node.outputs[0].default_value = .625
    artist_node_name = artist_node.name
    scene.render.resolution_percentage = 57
    resized = apply(obj,args.root/(mapping[1]+".fx"),MATERIAL,slot=1,units=.08,light=source)
    assert resized.rebuilt and tuple(finite_buffer(resized).size) == (456, 456)
    assert mip_atlas(resized).size[0] == 456 and mip_atlas(resized).packed_file
    scene.render.resolution_percentage = 100
    resized = apply(obj,args.root/(mapping[1]+".fx"),MATERIAL,slot=1,units=.08,light=source)
    restored_buffer = finite_buffer(resized)
    texture = restored_buffer[BUFFER_SOURCE]
    texel = texture.pixels[0]
    texture.pixels[0] = 1.0 if texel < .5 else 0.0
    texture.update()
    edited = apply(obj,args.root/(mapping[1]+".fx"),MATERIAL,slot=1,units=.08,light=source)
    assert edited.rebuilt and finite_buffer(edited)[BUFFER_SOURCE] == texture
    texture.pixels[0] = texel
    texture.update()
    restored_result = apply(obj,args.root/(mapping[1]+".fx"),MATERIAL,slot=1,units=.08,light=source)
    assert restored_result.rebuilt and artist_material.node_tree.nodes[artist_node_name] == artist_node
    assert len(bpy.data.node_groups) == len(originals)
    cloth=(args.root/("\u670d.fx")).read_bytes()
    try:
        text=cloth.decode("utf-8-sig")
    except UnicodeDecodeError:
        text=cloth.decode("cp932")
    coverage=out/"invalid-coverage.fx"
    assert "Out.Pos = Pos;" in text
    coverage.write_text(text.replace("Out.Pos = Pos;","Out.Pos = Pos * 0.5;"),encoding="utf-8")
    expect_rejection(obj,coverage,source,"raster coverage")
    feedback=out/"invalid-feedback.fx"
    assert "tex2D(DefObjTexSampler,Tex)" in text
    feedback.write_text(text.replace("tex2D(DefObjTexSampler,Tex)","tex2D(ObjTexSampler,Tex)"),encoding="utf-8")
    expect_rejection(obj,feedback,source,"feedback")
    cleared=out/"invalid-clear.fx"
    assert '"Pass=CreateMipmap;"' in text
    cleared.write_text(text.replace('"Pass=CreateMipmap;"',
                                   '"Pass=CreateMipmap;Clear=Color;"'),encoding="utf-8")
    expect_rejection(obj,cleared,source,"Clearing a written render target")
    for index,name in mapping.items():
        apply(obj,args.root/(name+".fx"),MATERIAL,slot=index,units=0.08,light=source,force_rebuild=True)
    assert len(bpy.data.node_groups)==len(originals)
    rebuilt=render_image(scene,out,"rebuilt",1)
    close(after,rebuilt)
    # Both the model and explicit light move without rebuilding the FX.
    obj.rotation_euler.z=0.12
    source.location.x=1.0
    moving=render_image(scene,out,"transformed",10)
    assert float(np.abs(moving-after).mean())>0.005
    obj.rotation_euler.z=0.0
    source.location.x=-0.65
    render_image(scene,out,"saved-reference",1)
    bpy.ops.file.pack_all()
    bpy.context.preferences.filepaths.save_version=0
    bpy.ops.wm.save_as_mainfile(filepath=str(out/"toondx.blend"),relative_remap=False)
    for index in mapping:
        remove(obj,MATERIAL,slot=index)
    assert snapshot(obj)==initial
    assert tuple(s.material for s in obj.material_slots)==slots
    assert stage(obj) is None
    assert not any(BUFFER_SOURCE in image for image in bpy.data.images)
    restored=render_image(scene,out,"restored",1)
    close(restored,before)
    for name,digest in original.items():
        assert sha256((args.root/(name+".fx")).read_bytes()).hexdigest()==digest
    bpy.ops.wm.open_mainfile(filepath=str(out/"toondx.blend"),load_ui=False)
    reopened_object = next(obj for obj in bpy.context.scene.objects if obj.name == initial_object_name)
    reopened_material = reopened_object.material_slots[1].material
    assert reopened_material.node_tree.nodes[artist_node_name].outputs[0].default_value == .625
    assert any(image.packed_file for image in bpy.data.images if image.get(BUFFER_ATLAS))
    reopened=render_image(bpy.context.scene,out,"reopened",1)
    close(reopened,after)
    report={"source":str(args.root),"model":str(args.model),"fx_sha256":original,
            "slots":mapping,"status":"PARTIAL","mean_render_difference":delta,
            "restored_mean_error":float(np.abs(restored-before).mean()),
            "reopened_mean_error":float(np.abs(reopened-after).mean()),
            "packed_buffer_resize_and_texture_rebuild":True,"invalid_coverage_rejected":True,"feedback_rejected":True,"post_write_clear_rejected":True,
            "material_profile":"object, self-shadow off"}
    (out/"results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("PASS TOONDX",json.dumps(report,ensure_ascii=True),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",required=True,type=Path)
    parser.add_argument("--model",required=True,type=Path)
    parser.add_argument("--mmd-tools",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
