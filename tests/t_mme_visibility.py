import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import bpy, key_tr, mb, np, setup_scene

triangle = Path(__file__).parent / "fixtures" / "mme" / "triangle" / "triangle.x"
scene, _, _, camera = setup_scene()

shader = """
float4x4 transform : WORLDVIEWPROJECTION;
float4 diffuse : DIFFUSE;
float3 eye : POSITION < string Object = "Camera"; >;
struct Vertex { float4 position : POSITION; float weight : TEXCOORD1; };
Vertex vs(float4 pos : POSITION) {
    Vertex output = (Vertex)0;
    output.position = mul(pos, transform);
    output.weight = WEIGHT;
    return output;
}
float4 ps(Vertex input) : COLOR0 {
    return float4(1, 1, 1, input.weight * diffuse.a);
}
technique Object { pass Surface {
    VertexShader = compile vs_3_0 vs();
    PixelShader = compile ps_3_0 ps();
} }
"""


def build(folder, name, weight):
    x = folder / (name + ".x")
    x.write_text(triangle.read_text().replace("1;1;1;1;;", "1;1;1;0.5;;"))
    fx = x.with_suffix(".fx")
    fx.write_text(shader.replace("WEIGHT", weight))
    root = mb.import_standalone_accessory(bpy.context, str(x), 1.0, True, str(fx))
    passes = mb.mme_effect_passes(root)
    assert len(passes) == 1, (name, passes)
    return root, passes[0]


def values(obj, frame):
    scene.frame_set(frame)
    dg = bpy.context.evaluated_depsgraph_get()
    mb.mme_update_scene(scene, dg, force=True)
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.data
    assert len(mesh.polygons) == 1, len(mesh.polygons)
    weight = mesh.attributes[mb.mme_varying_name("TEXCOORD1", 0)]
    return max(v.value for v in weight.data), evaluated[mb.MME_TR_UNIFORM]


with tempfile.TemporaryDirectory() as temp:
    folder = Path(temp)
    for backend in ("GEONODES", "PYTHON"):
        scene.ninesync_fx.mme_backend = backend
        scene.frame_set(1)
        root, obj = build(folder, "opacity", "0.5 - diffuse.a")
        assert obj[mb.MME_BACKEND_PROP] == ("GEO" if backend == "GEONODES" else backend)
        key_tr(root, [(1, 1.0), (2, 0.5)])
        assert values(obj, 1) == (0.0, 1.0)
        observed = values(obj, 2)
        np.testing.assert_allclose(observed, (0.25, 0.5), atol=1e-6)

        _, obj = build(folder, "view", "abs(normalize(eye).x)")
        camera.location = (0, -40, 0)
        assert values(obj, 1)[0] == 0
        camera.location = (40, 0, 0)
        assert values(obj, 2)[0] == 1
print("Material alpha and view-dependent visibility follow keyed opacity and the scene camera.")
