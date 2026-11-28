"""Two numeric offscreen owners and whole-scene rollback on actual creator geometry."""
import argparse,json,math,sys
from dataclasses import replace
from pathlib import Path
import bpy,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer,model_object,resolve_textures,snapshot
from t_middle_converters import set_scene
from ninesync_fx.runtime.middle.scene_execution import SceneMember,FxSceneExecution
from ninesync_fx.runtime.middle.targets import apply,remove,RENDER
from ninesync_fx.fx.errors import FxUnsupported

DRAW='''float4x4 WVP:WORLDVIEWPROJECTION;
float S:CONTROLOBJECT <string name="(OffscreenOwner)";string item="Si";>;
float4x4 W:CONTROLOBJECT <string name="(OffscreenOwner)";>;
struct O {float4 p:POSITION;}; O VS(float4 p:POSITION){O o;o.p=mul(p,WVP);return o;}
float4 PS(O i):COLOR{return float4(S*.25+W[3].x*.01,0,0,1);}
technique Main <string MMDPass="object";> {pass Draw {CullMode=NONE;
VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 PS();}}'''
OWNER='''texture T:OFFSCREENRENDERTARGET <int Width=32;int Height=32;string Format="R16F";
float4 ClearColor={0,0,0,1};float ClearDepth=1;bool AntiAlias=false;
string DefaultEffect="self=hide;*.pmx=draw.fx;*=hide;";>;
sampler View=sampler_state{Texture=<T>;MinFilter=POINT;MagFilter=POINT;MipFilter=NONE;};
struct O {float4 p:POSITION;float2 uv:TEXCOORD0;};
O VS(float4 p:POSITION,float2 uv:TEXCOORD0){O o;o.p=p;o.uv=uv;return o;}
float4 PS(O i):COLOR{float r=tex2D(View,i.uv).r;return float4(r,0,0,1);}
technique Main <string MMDPass="object";>{pass Draw <string Script="Draw=Buffer;";>{
ZEnable=false;ZWriteEnable=false;AlphaBlendEnable=false;CullMode=NONE;
VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 PS();}}'''



GROUND = '''
float4x4 World : WORLD;
float3 Surface : AMBIENT;
float3 Emissive : EMISSIVE;
float3 LightAmbient : AMBIENT <string Object="Light";>;
float4 VS(float4 p:POSITION, uniform float z):POSITION { p.z=z; return p; }
float4 GroundPS():COLOR {return float4(Surface.r*LightAmbient.g,World[3].y,Emissive.b,.65);}
float4 OverlapPS():COLOR {return float4(1,1,1,.65);}
float4 BodyPS(float2 p:VPOS):COLOR {clip(15.5-p.x);return float4(0,1,0,1);}
technique Shadow <string MMDPass="shadow";>{
pass Cast <string Script="Draw=Buffer;";>{
VertexShader=compile vs_3_0 VS(.25);PixelShader=compile ps_3_0 GroundPS();}
pass Overlap <string Script="Draw=Buffer;";>{
VertexShader=compile vs_3_0 VS(.125);PixelShader=compile ps_3_0 OverlapPS();}}
technique Main <string MMDPass="object";>{
pass Body <string Script="Draw=Buffer;";>{
VertexShader=compile vs_3_0 VS(.75);PixelShader=compile ps_3_0 BodyPS();}}
'''


def shadow_fixture_mesh(scene, name):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(-1,-1,0),(1,-1,0),(0,1,0)], [], [(0,1,2)])
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    material = bpy.data.materials.new(name)
    material.mmd_material.name_j = name
    mesh.materials.append(material)
    return obj, material.mmd_material


def check_ground(scene, output):
    fx = output/'ground-pass.fx'
    fx.write_text(GROUND)
    obj, material = shadow_fixture_mesh(scene, 'Ground caster')
    material.enabled_drop_shadow = True

    result = apply(obj, fx, RENDER, scene=scene, ground_shadows=True, ground_shadow_brightness=.2)
    pixels = np.rint(result.core.color.pixels*255).astype(int)
    assert [row['key'][0] for row in result.core.last_trace] == ['ground','main']
    np.testing.assert_array_equal(pixels[16,8], [0,255,0,255])
    np.testing.assert_array_equal(pixels[16,24], [20,17,0,166])

    light_data = bpy.data.lights.new('Ground pass light', 'SUN')
    light_data.color = (32/255,96/255,224/255)
    light = bpy.data.objects.new('Ground pass light', light_data)
    scene.collection.objects.link(light)
    light.location = (.5,-.5,1)
    colored = apply(obj, fx, RENDER, scene=scene, light=light)
    white = colored.core.render(0, clear=(1,1,1,0))
    np.testing.assert_array_equal(np.rint(white[16,24]*255), [101,106,89,166])

    material.enabled_drop_shadow = False
    result = apply(obj, fx, RENDER, scene=scene)
    assert [row['key'][0] for row in result.core.last_trace] == ['main']
    np.testing.assert_array_equal(np.rint(result.core.color.pixels[16,24]*255), [0,0,0,255])
    remove(obj, RENDER, scene=scene)



SELF_SHADOW = '''
sampler ShadowSampler : register(s0);
bool parthf;
float4 VS(float4 p:POSITION):POSITION {p.z=.5;return p;}
float4 DepthPS():COLOR {return float4(.375,0,0,1);}
float4 PlainPS():COLOR {return float4(0,0,1,1);}
float4 SamplePS():COLOR {return float4(tex2D(ShadowSampler,float2(.5,.5)).r,parthf?.5:0,0,1);}
technique Depth <string MMDPass="zplot";>{
pass Cast <string Script="Draw=Buffer;";>{
VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 DepthPS();}}
technique Plain <string MMDPass="object";>{
pass Body <string Script="Draw=Buffer;";>{
VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 PlainPS();}}
technique Receives <string MMDPass="object_ss";>{
pass Body <string Script="Draw=Buffer;";>{
VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 SamplePS();}}
'''


def check_self_shadows(scene, output):
    fx = output/'self-shadow.fx'
    fx.write_text(SELF_SHADOW)
    obj, material = shadow_fixture_mesh(scene, 'Self-shadow caster and receiver')
    material.enabled_self_shadow_map = True
    material.enabled_self_shadow = True
    for mode, expected in ((0,[0,0,255,255]), (1,[96,0,0,255]), (2,[96,128,0,255])):
        result = apply(obj, fx, RENDER, scene=scene, self_shadow_mode=mode)
        pixels = np.rint(result.core.color.pixels*255).astype(int)
        np.testing.assert_array_equal(pixels[16,16], expected)
        if mode:
            assert [row['key'][0] for row in result.core.last_trace] == ['zplot','main']
            assert np.all(result.core.last_shadow_map==.375)
        else:
            assert result.core.last_shadow_map is None

    material.enabled_self_shadow = False
    no_receiver = apply(obj, fx, RENDER, scene=scene)
    np.testing.assert_array_equal(np.rint(no_receiver.core.color.pixels[16,16]*255), [255,128,0,255])
    assert np.all(no_receiver.core.last_shadow_map==.375)
    material.enabled_self_shadow = True
    material.enabled_self_shadow_map = False
    no_caster = apply(obj, fx, RENDER, scene=scene, self_shadow_mode=1)
    np.testing.assert_array_equal(np.rint(no_caster.core.color.pixels[16,16]*255), [255,0,0,255])
    assert np.all(no_caster.core.last_shadow_map==1)
    remove(obj, RENDER, scene=scene)


def run(args):
    args.output.mkdir(parents=True,exist_ok=True);(args.output/'draw.fx').write_text(DRAW)
    fx=args.output/'owner.fx';fx.write_text(OWNER)
    bad=args.output/'bad-owner.fx';bad.write_text(OWNER.replace('ZEnable=false;','ZEnable=false;FogEnable=true;'))
    pmx,importer=load_importer(args.mmd_tools);scene=set_scene();scene.render.resolution_x=scene.render.resolution_y=32
    scene.camera.location=(0,-45*.08,10*.08);scene.camera.rotation_euler=(math.pi/2,0,0)
    scene.camera.data.sensor_fit='VERTICAL';scene.camera.data.lens=scene.camera.data.sensor_height/(2*math.tan(math.radians(30)/2))
    model=pmx.load(str(args.model));resolve_textures(model,args.model,args.model.parent/'tex')
    importer().execute(pmx=model,scale=.08,types={'MESH','ARMATURE','MORPHS'},fix_bone_order=False);obj=model_object(scene,model)
    from harness_fx import mb
    from ninesync_fx.accessories.standalone_import import import_plain_accessory
    roots=[import_plain_accessory(bpy.context,str(args.accessory),.08) for _ in range(2)]
    members=[SceneMember(obj,args.model.name,main=False)]
    for index,root in enumerate(roots):
        root['mmd_bridge_accessory']=True;root.scale=((.1,.2)[index],)*3
        mesh=next(child for child in root.children_recursive if child.type=='MESH')
        members.append(SceneMember(mesh,f'owner-{index}.x',fx,root))
    before=snapshot(obj);initial_frame=scene.frame_current;engine=FxSceneExecution(scene,members);checks=[]
    def targets():
        return [engine.executors[('main',index,str(fx.resolve()))].targets['T'].pixels.copy() for index in (1,2)]
    engine.render(0);initial=targets()
    for image,value in zip(initial,(.25,.5)):
        nonzero=image[:,:,0]>0;assert nonzero.sum()>5 and np.all(image[:,:,0][nonzero]==value)
    checks.append('independent R16F owners with actual PMX geometry')
    roots[0].scale=(.15,)*3;roots[0].location.x=.08;engine.render(0);changed=targets()
    assert not np.array_equal(initial[0],changed[0]) and np.array_equal(initial[1],changed[1])
    assert np.all(changed[0][:,:,0][changed[0][:,:,0]>0]==np.float16(.385))
    checks.append('only the animated owner changes scale/matrix-dependent pixels')
    saved={key:({name:value.pixels.copy() for name,value in runtime.targets.items()},
                {name:value.values.copy() for name,value in runtime.depths.items()}) for key,runtime in engine.executors.items()}
    canvas=engine.color.pixels.copy();trace=engine.last_trace
    roots[0].scale=(.17,)*3
    engine.members=tuple(replace(member,fx=bad) if i==2 else member for i,member in enumerate(members))
    try:engine.render(10)
    except FxUnsupported as error:assert 'fogenable' in str(error).lower(),str(error)
    else:raise AssertionError('The intentionally unsupported later owner succeeded')
    assert set(engine.executors)==set(saved) and np.array_equal(canvas,engine.color.pixels)
    assert engine.last_trace==trace and engine.last_frame==0 and scene.frame_current==initial_frame
    for key,(colors,depths) in saved.items():
        runtime=engine.executors[key]
        assert all(np.array_equal(pixels,runtime.targets[name].pixels) for name,pixels in colors.items())
        assert all(np.array_equal(values,runtime.depths[name].values) for name,values in depths.items())
    assert snapshot(obj)==before
    checks.append('later-owner failure restores all prior color/depth resources and frame')
    check_ground(scene,args.output)
    checks.append('ground parameters, single coverage, PMX casting flag and separate primary depth')
    check_self_shadows(scene,args.output)
    checks.append('both self-shadow modes, s0 depth binding, separate caster and receiver flags, fresh shadow map')
    (args.output/'results.json').write_text(json.dumps(dict(status='PASS',checks=checks,source_preserved=True),indent=2))
    print('PASS NUMERIC_SCENE_OWNERS',len(checks),'cases',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('model','accessory','mmd-tools','output'):parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
