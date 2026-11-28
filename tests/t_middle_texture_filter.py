"""Check encoded-space bilinear sampling of an actual PMX texture in both stages."""
import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer, model_object, resolve_textures, snapshot, position_camera, render_image
from t_middle_converters import set_scene
from t_middle_vertex import field
from ninesync_fx.runtime.middle.targets import apply, remove, MATERIAL
from ninesync_fx.runtime.middle.vertex import VERTEX_CORE
from ninesync_fx.runtime.middle.images import ENCODED_SOURCE


def run(args):
    pmx, importer = load_importer(args.mmd_tools)
    scene = set_scene()
    scene.render.resolution_x = scene.render.resolution_y = 128
    scene.render.film_transparent = True
    model = pmx.load(str(args.model))
    resolve_textures(model, args.model, args.model.parent / "textures")
    importer().execute(pmx=model, scale=0.08, types={"MESH", "ARMATURE", "MORPHS"}, fix_bone_order=False)
    obj = model_object(scene, model)
    position_camera(scene, obj)
    source = snapshot(obj)
    image = obj.material_slots[1].material.node_tree.nodes["mmd_base_tex"].image
    original_space = image.colorspace_settings.name
    original_alpha = image.alpha_mode
    width, height = image.size
    texels = np.array(image.pixels[:]).reshape(height, width, 4)
    distance = np.abs(texels[:, 1:, :3]-texels[:, :-1, :3]).sum(axis=2)
    row, col = np.unravel_index(distance.argmax(), distance.shape)
    expected = (texels[row, col, :3]+texels[row, col+1, :3])*0.5
    # Halfway between two texel centers; Y is flipped in HLSL coordinates.
    u, v = (col+1)/width, 1-(row+0.5)/height
    linear = np.where(expected <= 0.04045, expected/12.92, ((expected+0.055)/1.055)**2.4)
    args.output.mkdir(parents=True, exist_ok=True)
    fx = args.output / "encoded-filter.fx"
    fx.write_text('''float4x4 WVP : WORLDVIEWPROJECTION;
texture MaterialTexture : MATERIALTEXTURE;
sampler Base = sampler_state { Texture=<MaterialTexture>; MinFilter=LINEAR; MagFilter=LINEAR; AddressU=CLAMP; AddressV=CLAMP; };
struct Output { float4 position:POSITION; float3 sample:TEXCOORD0; };
Output VS(float4 position:POSITION) { Output o; o.position=mul(position,WVP); o.sample=tex2Dlod(Base,float4(U,V,0,0)).rgb; return o; }
float4 PS(Output i):COLOR { return float4((i.sample+tex2D(Base,float2(U,V)).rgb)*0.5,1); }
technique Model < string MMDPass="object"; > { pass Main { VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 PS(); } }
'''.replace('U,V', f'{u:.12g},{v:.12g}'), encoding="utf-8")
    result = apply(obj, fx, MATERIAL, slot=1, units=0.08)
    core = result.core[VERTEX_CORE]
    instance = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = instance.to_mesh()
    try:
        selected = np.repeat([p.material_index == 1 for p in mesh.polygons], [p.loop_total for p in mesh.polygons])
        values = np.column_stack([field(mesh, core, f"TEXCOORD0.{i}") for i in range(3)])
        vertex_error = float(np.abs(values[selected]-expected).max())
        assert vertex_error < 2e-5, (vertex_error, expected, values[selected][0])
    finally:
        instance.to_mesh_clear()
    rendered = render_image(scene, args.output, "encoded-filter", 1)
    errors = np.abs(rendered[:, :, :3]-linear).max(axis=2)
    matched = (errors < 2e-5) & (rendered[:, :, 3] > 0.99)
    assert matched.sum() > 300, (matched.sum(), expected, linear)
    assert any(i.get(ENCODED_SOURCE) == image for i in bpy.data.images)
    assert image.colorspace_settings.name == original_space and image.alpha_mode == original_alpha
    original_pixels = texels.copy()
    assert not apply(obj, fx, MATERIAL, slot=1, units=0.08).rebuilt
    edited = texels.copy()
    edited[row, col, 0] -= 0.02
    image.pixels.foreach_set(edited.astype(np.float32).ravel())
    image.update()
    change = (image.pixels[(row*width+col)*4]-texels[row, col, 0])*0.5
    assert abs(change) > 0.005
    updated = apply(obj, fx, MATERIAL, slot=1, units=0.08)
    assert updated.rebuilt, "Edited original texels reused a stale encoded view"
    instance = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = instance.to_mesh()
    try:
        value = field(mesh, updated.core[VERTEX_CORE], "TEXCOORD0.0")[selected]
        assert float(np.abs(value-expected[0]-change).max()) < 2e-5
    finally:
        instance.to_mesh_clear()
    image.pixels.foreach_set(original_pixels.astype(np.float32).ravel())
    image.update()
    assert apply(obj, fx, MATERIAL, slot=1, units=0.08).rebuilt
    remove(obj, MATERIAL, slot=1)
    assert not any(i.get(ENCODED_SOURCE) == image for i in bpy.data.images)
    assert np.array_equal(np.array(image.pixels[:]).reshape(texels.shape), original_pixels)
    assert snapshot(obj) == source
    report = dict(source_texture=image.filepath, source_size=[width, height], sample_uv=[u, v],
                  expected_encoded_rgb=expected.tolist(), expected_linear_rgb=linear.tolist(),
                  maximum_vertex_error=vertex_error, shader_pixels_matched=int(matched.sum()),
                  source_image_preserved=True, encoded_copy_removed=True, edited_image_reimport_verified=True)
    (args.output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("PASS REAL_PMX_ENCODED_FILTERING", json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--mmd-tools", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
