"""Prepare and verify texture-morph inputs on the real user PMX, with native evidence."""
import argparse
from pathlib import Path
import sys
import json
from hashlib import sha256
import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer, resolve_textures, model_object, snapshot, render_image, position_camera
from t_middle_material_morph import prepare_model, NAMES, OFFSETS
from t_middle_converters import set_scene
from t_middle_vertex import field
from ninesync_fx.runtime.middle.targets import apply, remove, inspect_bindings, MATERIAL
from ninesync_fx.runtime.middle.vertex import VERTEX_CORE
from ninesync_fx.nodes.material_compile import mme_tree_signature
from ninesync_fx.fx.errors import FxUnsupported
from ninesync_fx.runtime.middle.analysis import analyze

FX = Path(__file__).parent/"fixtures/middle/texture_morph.fx"

FACTORS = (
    dict(texture=(.4,.7,1.2,.65), sphere=(1.1,.5,.8,.4), toon=(.6,1.15,.4,.75)),
    dict(texture=(.15,-.08,.1,-.12), sphere=(-.1,.12,.05,.15), toon=(.08,-.1,.12,-.15)),
    dict(texture=(.8,1.05,.9,1.1), sphere=(.9,.8,1.1,.85), toon=(1.1,.85,.9,1.05)),
)
FRAMES = {0:(0,0,0,0), 10:(.5,0,0,0), 20:(1,0,0,0), 30:(0,.5,0,0),
          40:(0,1,0,0), 50:(0,0,1,0), 60:(.2,.1,.5,.7), 70:(1,1,1,1)}
TEXTURES = dict(base=(120,80,200,160), sphere=(60,180,100,190), toon=(200,140,40,220))


def expected(weights, *, slot=1, shadow=True):
    mul = {key:np.ones(4) for key in ("texture","sphere","toon")}
    add = {key:np.zeros(4) for key in mul}
    factors = ((weights[0],weights[3]*.4),(weights[1],weights[3]*.6),(weights[2],))
    for index in (2,0,1):
        if slot != 1 and index != 2:
            continue
        for key, offset in FACTORS[index].items():
            for weight in factors[index]:
                if index == 1:
                    add[key] += weight*np.array(offset)
                else:
                    mul[key] *= 1+(np.array(offset)-1)*weight
    tc = np.r_[np.array(TEXTURES["toon"][:3])/256,1]*mul["toon"]+add["toon"]
    if not shadow:
        tc = np.r_[np.array(TEXTURES["toon"][:3])/256,1]
    return [mul["texture"] if shadow else np.ones(4),add["texture"] if shadow else np.zeros(4),
            mul["sphere"] if shadow else np.ones(4),add["sphere"] if shadow else np.zeros(4),tc,
            *(np.array(TEXTURES[key])/255 for key in ("base","sphere","toon"))]


def run(args, pmx, importer):
    scene = set_scene()
    scene.render.resolution_x = scene.render.resolution_y = 400
    scene.render.film_transparent = True
    original_hash = sha256(args.model.read_bytes()).hexdigest()
    model = pmx.load(str(args.output/"Sanae-texture-diagnostic.pmx"))
    importer().execute(pmx=model,scale=.08,types={"MESH","ARMATURE","MORPHS"},fix_bone_order=False)
    obj = model_object(scene,model)
    position_camera(scene,obj)
    from mmd_tools.core.model import Model, FnModel
    root = FnModel.find_root_object(obj)
    rig = Model(root)
    rig.morph_slider.bind()
    slider = rig.morph_slider.placeholder(binded=True)
    for frame, weights in FRAMES.items():
        for name, weight in zip(NAMES,weights):
            key = slider.data.shape_keys.key_blocks[name]
            key.value = weight
            key.keyframe_insert("value",frame=frame)
    scene.frame_set(0)
    source = snapshot(obj)
    source_material = obj.material_slots[1].material
    source_signature = mme_tree_signature(source_material.node_tree)
    result = apply(obj,FX,MATERIAL,slot=1,units=.08,pass_type="object_ss")
    reports = []
    buffers = {}

    def check(frame, *, shadow=True, render=True):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        wanted = expected(FRAMES[frame],shadow=shadow)
        instance = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = instance.to_mesh()
        try:
            selected = np.repeat([p.material_index==1 for p in mesh.polygons],[p.loop_total for p in mesh.polygons])
            errors = []
            for i, value in enumerate(wanted):
                actual = np.column_stack([field(mesh,result.core[VERTEX_CORE],f"TEXCOORD{i}.{j}") for j in range(4)])
                error = float(np.abs(actual[selected]-value).max())
                assert error < 3e-6, (frame,i,error,actual[selected][0],value)
                errors.append(error)
        finally:
            instance.to_mesh_clear()
        if render:
            total = sum(wanted)*.05
            encoded = total[:3]+total[3]*.05+.05
            linear = np.where(encoded<=.04045,encoded/12.92,((encoded+.055)/1.055)**2.4)
            pixels = render_image(scene,args.output,f"diagnostic-{frame}",frame)
            matched = int(((np.abs(pixels[:,:,:3]-linear).max(axis=2)<2e-5)&(pixels[:,:,3]>.99)).sum())
            assert matched > 1000, (frame,matched,encoded)
            if frame in buffers:
                assert np.array_equal(buffers[frame],pixels), "Reopening changed texture morph renders"
            buffers[frame] = pixels
        assert not inspect_bindings(obj,MATERIAL,slot=1)
        assert snapshot(obj)==source
        reports.append(dict(frame=frame,shadow=shadow,errors=errors))

    for frame in FRAMES:
        check(frame)
    assert not apply(obj,FX,MATERIAL,slot=1,units=.08,pass_type="object_ss").rebuilt
    # Coefficients remain editable drivers; test a live offset edit and restoration.
    offset = root.mmd_root.material_morphs[NAMES[0]].data[0]
    before = offset.texture_factor[:]
    offset.texture_factor[0] = before[0]+.05
    assert not apply(obj,FX,MATERIAL,slot=1,units=.08,pass_type="object_ss").rebuilt
    scene.frame_set(20)
    bpy.context.view_layer.update()
    instance = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = instance.to_mesh()
    try:
        selected = np.repeat([p.material_index==1 for p in mesh.polygons],[p.loop_total for p in mesh.polygons])
        actual = field(mesh,result.core[VERTEX_CORE],"TEXCOORD0.0")[selected]
        assert np.max(np.abs(actual-before[0]-.05)) < 3e-6
    finally:
        instance.to_mesh_clear()
    offset.texture_factor = before
    # Invalid source bindings must retain the last good graph and datablock counts.
    old_name = offset.name
    offset.name = "missing-texture-morph-binding"
    counts = (len(bpy.data.materials),len(bpy.data.node_groups),len(bpy.data.meshes))
    try:
        apply(obj,FX,MATERIAL,slot=1,units=.08,pass_type="object_ss",force_rebuild=True)
    except FxUnsupported as error:
        assert "binding" in str(error)
    else:
        raise AssertionError("Broken morph binding was accepted")
    assert counts == (len(bpy.data.materials),len(bpy.data.node_groups),len(bpy.data.meshes))
    offset.name = old_name
    result = apply(obj,FX,MATERIAL,slot=1,units=.08,pass_type="object_ss",force_rebuild=True)
    check(60)
    # Ordinary object pass uses identity texture coefficients, matching native MMD.
    result = apply(obj,FX,MATERIAL,slot=1,units=.08,pass_type="object")
    check(20,shadow=False,render=False)
    result = apply(obj,FX,MATERIAL,slot=1,units=.08,pass_type="object_ss")
    check(20)
    # Exercise the same offset scoping through the converter on another real slot.
    face_original = obj.material_slots[0].material
    apply(obj,args.output/"native-0.fx",MATERIAL,slot=0,units=.08,pass_type="object_ss")
    for frame in (20,50,60,70):
        encoded = expected(FRAMES[frame],slot=0)[0][:3]*.5
        linear = np.where(encoded<=.04045,encoded/12.92,((encoded+.055)/1.055)**2.4)
        pixels = render_image(scene,args.output,f"scope-{frame}",frame)
        assert int(((np.abs(pixels[:,:,:3]-linear).max(axis=2)<2e-5)&(pixels[:,:,3]>.99)).sum())>500
    remove(obj,MATERIAL,slot=0)
    assert obj.material_slots[0].material==face_original
    # Shared generic-X semantics retain the documented no-morph defaults.
    from ninesync_fx.fx.semantics import FxSceneState
    state = FxSceneState()
    for name in ("ADDINGTEXTURE","ADDINGSPHERETEXTURE","MULTIPLYINGTEXTURE","MULTIPLYINGSPHERETEXTURE"):
        assert state.values(("material",name),None)==[0.0 if name.startswith("ADDING") else 1.0]*4
    # Unsupported raster state must remain transactional even with explicit pass selection.
    bad = args.output/"unsupported-state.fx"
    bad.write_text(FX.read_text().replace("pass Main {", "pass Main { AlphaBlendEnable=TRUE;"))
    groups = len(bpy.data.node_groups)
    try:
        apply(obj,bad,MATERIAL,slot=1,units=.08,pass_type="object_ss")
    except FxUnsupported as error:
        assert "state" in str(error)
    else:
        raise AssertionError("Unsupported object_ss state was accepted")
    assert len(bpy.data.node_groups)==groups
    check(20)
    shadow_state = args.output/"unsupported-shadow-state.fx"
    shadow_state.write_text('''float4x4 WVP:WORLDVIEWPROJECTION;
bool use_selfshadow;
float4 VS(float4 p:POSITION):POSITION {return mul(p,WVP);}
float4 PS():COLOR {return float4(use_selfshadow,0,0,1);}
technique Model < string MMDPass="object_ss"; > {
 pass Main {VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 PS();}
}''')
    try:
        apply(obj,shadow_state,MATERIAL,slot=1,units=.08,pass_type="object_ss")
    except FxUnsupported as error:
        assert "native shadow-pass state" in str(error)
    else:
        raise AssertionError("An unimplemented shadow flag silently disabled native shading")
    assert len(bpy.data.node_groups)==groups
    # Default routing keeps its shadow-pass rejection; explicit input conversion works.
    only = args.output/"shadow-only.fx"
    ordinary = 'technique Model < string MMDPass="object"; > {\n pass Main { VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 PS(); }\n}\n'
    assert ordinary in FX.read_text()
    only.write_text(FX.read_text().replace(ordinary,""))
    assert any("unverified self-shadow" in problem for problem in analyze(only).problems)
    result = apply(obj,only,MATERIAL,slot=1,units=.08,pass_type="object_ss")
    check(60)
    result = apply(obj,FX,MATERIAL,slot=1,units=.08,pass_type="object_ss")
    saved = args.output/"texture-morphs.blend"
    bpy.ops.file.pack_all()
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(saved),relative_remap=False)
    scene.frame_set(0)
    bpy.context.view_layer.update()
    remove(obj,MATERIAL,slot=1)
    assert obj.material_slots[1].material==source_material
    assert mme_tree_signature(source_material.node_tree)==source_signature
    assert snapshot(obj)==source
    bpy.ops.wm.open_mainfile(filepath=str(saved),load_ui=False)
    scene = bpy.context.scene
    obj = model_object(scene,model)
    source = snapshot(obj)
    result = apply(obj,FX,MATERIAL,slot=1,units=.08,pass_type="object_ss")
    assert not result.rebuilt
    for frame in (0,20,60,70):
        check(frame)
    native = validate_native(args)
    assert sha256(args.model.read_bytes()).hexdigest()==original_hash
    (args.output/"results.json").write_text(json.dumps(dict(checks=reports,source_hash=original_hash,native=native),indent=2))
    print("PASS_REAL_PMX_TEXTURE_MORPHS",len(reports),"states;",len(native),"native probes",flush=True)


def encoded_pixels(path):
    image = bpy.data.images.load(str(path),check_existing=False)
    pixels = np.rint(np.array(image.pixels[:]).reshape(image.size[1],image.size[0],4)*255)
    bpy.data.images.remove(image)
    return pixels


def validate_native(args):
    mask = np.all(encoded_pixels(args.output/"probe-first.png")[:,:,:3]==[51,89,153],axis=2)
    assert mask.sum()>2000
    checks = []
    for frame, weights in FRAMES.items():
        tm,ta,sm,sa,tc,base,sphere,toon = expected(weights)
        values = [tm[:3]*.5,ta[:3]*.5+.25,sm[:3]*.5,sa[:3]*.5+.25,
                  np.array([tm[3]*.5,ta[3]*.5+.25,sm[3]*.5]),
                  np.array([sa[3]*.5+.25,base[3],sphere[3]]),base[:3],sphere[:3],toon[:3],tc[:3],
                  np.array([toon[3],tc[3],0])]
        for channel, wanted in enumerate(values):
            path = args.output/f"native-{channel}-{frame}.png"
            pixels = encoded_pixels(path)[:,:,:3][mask]
            error = float(np.max(np.abs(pixels-np.clip(wanted,0,1)*255)))
            assert error <= 1.01, (frame,channel,error,wanted,np.median(pixels,axis=0))
            checks.append(dict(frame=frame,channel=channel,maximum_byte_error=error,pixels=int(mask.sum()),
                               sha256=sha256(path.read_bytes()).hexdigest()))
    for frame in (0,20,70):
        path = args.output/f"native-no-shadow-0-{frame}.png"
        error = float(np.max(np.abs(encoded_pixels(path)[:,:,:3][mask]-127.5)))
        assert error<=.5
        checks.append(dict(kind="ordinary_texture_identity",frame=frame,maximum_byte_error=error,
                           sha256=sha256(path.read_bytes()).hexdigest()))
    # Per-slot offsets must not leak onto the face; all-material offsets do.
    face_mask = np.all(encoded_pixels(args.output/"native-scope-0-20.png")[:,:,:3]==127,axis=2)
    assert face_mask.sum()>500
    for frame in (20,50,60,70):
        tm,ta,sm,sa,*_ = expected(FRAMES[frame],slot=0)
        for channel, wanted in enumerate((tm[:3]*.5,ta[:3]*.5+.25,sm[:3]*.5,sa[:3]*.5+.25)):
            path = args.output/f"native-scope-{channel}-{frame}.png"
            error = float(np.max(np.abs(encoded_pixels(path)[:,:,:3][face_mask]-np.clip(wanted,0,1)*255)))
            assert error<=1.01, ("scope",frame,channel,error)
            checks.append(dict(frame=frame,channel=channel,slot=0,maximum_byte_error=error,pixels=int(face_mask.sum()),
                               sha256=sha256(path.read_bytes()).hexdigest()))
    # Packed fractional-byte probes independently establish the /256 toon base.
    for frame in (0,30,70):
        tc = expected(FRAMES[frame])[4]
        for channel in range(11,14):
            path = args.output/f"native-pack-{channel}-{frame}.png"
            observed = np.median(encoded_pixels(path)[:,:,:2][mask],axis=0)
            decoded = (observed[0]+observed[1]/255)/255
            assert abs(decoded-tc[channel-11]) < 2e-5, ("packed",frame,channel,decoded,tc)
            checks.append(dict(kind="packed_toon",frame=frame,channel=channel,
                               maximum_byte_error=float(abs(decoded-tc[channel-11])*255),
                               maximum_component_error=float(abs(decoded-tc[channel-11])),
                               sha256=sha256(path.read_bytes()).hexdigest()))
    # Native ordinary object passes keep toon alpha at its unmorphed default.
    for frame in (0,20,50,60,70):
        path = args.output/f"native-toon-alpha-{frame}.png"
        pixels = encoded_pixels(path)[:,:,:3][mask]
        error = float(np.max(np.abs(pixels-[127.5,0,0])))
        assert error<=.5
        checks.append(dict(kind="ordinary_toon_alpha",frame=frame,maximum_byte_error=error,
                           sha256=sha256(path.read_bytes()).hexdigest()))
        path = args.output/f"native-toon-alpha-ss-{frame}.png"
        wanted = [expected(FRAMES[frame])[4][3]*127.5,0,0]
        error = float(np.max(np.abs(encoded_pixels(path)[:,:,:3][mask]-wanted)))
        assert error<=.54, ("object_ss alpha",frame,error,wanted)
        checks.append(dict(kind="object_ss_toon_alpha",frame=frame,maximum_byte_error=error,
                           sha256=sha256(path.read_bytes()).hexdigest()))
    return checks


def prepare(args, pmx):
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    model = prepare_model(pmx, pmx.load(str(args.model)))
    model.name = model.name_e = "Sanae-Texture-Probe"
    resolve_textures(model, args.model, args.model.parent / "textures")
    for texture in model.textures:
        path = Path(texture.path)
        texture.path = str(path if path.is_absolute() else args.model.parent / path)
    for key, rgba in TEXTURES.items():
        image = bpy.data.images.new("Probe "+key, 2,2,alpha=True)
        image.colorspace_settings.name = "Non-Color"
        image.pixels[:] = list(np.array(rgba)/255)*4
        image.filepath_raw, image.file_format = str(out/(key+".png")), "PNG"
        image.save()
        bpy.data.images.remove(image)
        texture = pmx.Texture()
        texture.path = str(out/(key+".png"))
        model.textures.append(texture)
    material = model.materials[1]
    material.texture = len(model.textures)-3
    material.sphere_texture, material.sphere_texture_mode = len(model.textures)-2, 1
    material.is_shared_toon_texture, material.toon_texture = False, len(model.textures)-1
    # Keep non-texture fields neutral to isolate these coefficients.
    for morph, factors, spec in zip(model.morphs[-4:-1], FACTORS, OFFSETS):
        offset = morph.offsets[0]
        neutral = 0.0 if spec["kind"] else 1.0
        offset.diffuse_offset = [neutral]*4
        offset.ambient_offset = offset.specular_offset = [neutral]*3
        offset.shininess_offset, offset.edge_size_offset = neutral, neutral
        offset.edge_color_offset = [neutral]*4
        offset.texture_factor = factors["texture"]
        offset.sphere_texture_factor, offset.toon_texture_factor = factors["sphere"], factors["toon"]
    pmx.save(str(out/"Sanae-texture-diagnostic.pmx"), model)
    # Surface validation keeps every original creator material and texture.
    original = pmx.load(str(args.model))
    resolve_textures(original,args.model,args.model.parent/"textures")
    for texture in original.textures:
        path = Path(texture.path)
        texture.path = str(path if path.is_absolute() else args.model.parent/path)
    model.materials, model.textures = original.materials, original.textures
    pmx.save(str(out/"Sanae-texture-surfaces.pmx"),model)
    from mmd_tools.core import vmd
    motion = vmd.File()
    motion.header = vmd.Header()
    motion.header.model_name = model.name
    motion.shapeKeyAnimation = vmd.ShapeKeyAnimation()
    for frame, weights in FRAMES.items():
        for name, weight in zip(NAMES, weights):
            key = vmd.ShapeKeyFrameKey()
            key.frame_number, key.weight = frame, weight
            motion.shapeKeyAnimation[name].append(key)
    motion.save(filepath=str(out/"texture-motion.vmd"))
    source = (Path(__file__).parent/"fixtures/middle/texture_morph_native.fx").read_text()
    for channel in range(11):
        (out/f"native-{channel}.fx").write_text(source.replace("#define CHANNEL 0", f"#define CHANNEL {channel}"))
    packed = FX.with_name("texture_morph_precision.fx").read_text()
    for channel, component in enumerate("rgb",11):
        (out/f"native-pack-{channel}.fx").write_text(packed.replace("COMPONENT",component))
    (out/"native-toon-alpha.fx").write_text(FX.with_name("texture_morph_alpha.fx").read_text())
    (out/"native-toon-alpha-ss.fx").write_text(FX.with_name("texture_morph_alpha_ss.fx").read_text())
    print("PREPARED_REAL_PMX_TEXTURE_MORPHS",out,flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model",type=Path,required=True)
    parser.add_argument("--mmd-tools",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--prepare",action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    pmx, importer = load_importer(args.mmd_tools)
    if args.prepare:
        prepare(args,pmx)
    else:
        run(args,pmx,importer)
