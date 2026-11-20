import os, sys, tempfile; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *

FX_TEXT = r"""
int Copies = 12;
int index;
float Tr : CONTROLOBJECT < string name = "(self)"; string item = "Tr"; >;
float4x4 WVP : WORLDVIEWPROJECTION;
float4x4 W : WORLD;
float3 CamPos : POSITION < string Object = "Camera"; >;
struct VS_OUT { float4 Pos : POSITION; float2 Tex : TEXCOORD0; float3 N : TEXCOORD1; float3 Eye : TEXCOORD2; float A : TEXCOORD3; };
float3 RotY(float a, float3 p) { float c = cos(a); float s = sin(a); return float3(c * p.x - s * p.z, p.y, s * p.x + c * p.z); }
VS_OUT VS(float4 Pos : POSITION, float3 N : NORMAL, float2 Tex : TEXCOORD0)
{
    VS_OUT o = (VS_OUT)0;
    float k = index;
    float3 p = RotY(k * 0.7 + Tr, Pos.xyz) * (0.5 + 0.1 * k);
    p += float3(k * 1.5 - 8, sin(k) * 2, 0);
    o.Pos = mul(float4(p, 1), WVP);
    o.N = normalize(mul(RotY(k * 0.7 + Tr, N), (float3x3)W));
    o.Eye = CamPos - mul(float4(p, 1), W).xyz;
    o.Tex = Tex * 0.5 + k * 0.01;
    o.A = Tr * (0.2 + k / 15.0);
    return o;
}
VS_OUT VS_BENT(float4 Pos : POSITION, float3 N : NORMAL, float2 Tex : TEXCOORD0)
{
    VS_OUT o = VS(Pos, N, Tex);
    o.Pos = mul(float4(Pos.xyz * (1 + 0.3 * Pos.x) + float3(index * 1.5 - 8, -3, 0), 1), WVP);
    return o;
}
float4 PS(VS_OUT IN) : COLOR0 { return float4(IN.Tex, saturate(dot(normalize(IN.N), normalize(IN.Eye))), IN.A); }
technique T < string MMDPass = "object"; string Script = "LoopByCount=Copies; LoopGetIndex=index; Pass=P0; Pass=P1; Pass=P2; LoopEnd=;"; >
{
    pass P0 { AlphaBlendEnable = TRUE; SrcBlend = SRCALPHA; DestBlend = ONE; VertexShader = compile vs_3_0 VS(); PixelShader = compile ps_3_0 PS(); }
    pass P1 { AlphaBlendEnable = TRUE; SrcBlend = SRCALPHA; DestBlend = ONE; VertexShader = compile vs_3_0 VS_BENT(); PixelShader = compile ps_3_0 PS(); }
    pass P2 { AlphaBlendEnable = TRUE; SrcBlend = SRCALPHA; DestBlend = INVSRCALPHA; VertexShader = compile vs_3_0 VS(); PixelShader = compile ps_3_0 PS(); }
}
"""


def cube_x():
    verts, normals, uvs, faces = [], [], [], []
    for axis in range(3):
        for sign in (-1.0, 1.0):
            n = [0.0, 0.0, 0.0]; n[axis] = sign
            u = [0.0, 0.0, 0.0]; u[(axis + 1) % 3] = 1.0
            v = [0.0, 0.0, 0.0]; v[(axis + 2) % 3] = 1.0
            base = len(verts)
            for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                verts.append([n[i] * 0.5 + (u[i] * a + v[i] * b) * 0.5 for i in range(3)])
                normals.append(n)
                uvs.append(((a + 1) / 2, (b + 1) / 2))
            faces += [(base, base + 1, base + 2), (base, base + 2, base + 3)] if sign > 0 else [(base, base + 2, base + 1), (base, base + 3, base + 2)]
    row = lambda values: ";".join("%.6f" % x for x in values)
    text = ["xof 0303txt 0032", "Mesh {", " %d;" % len(verts)]
    text += [" %s;%s" % (row(p), "," if k < len(verts) - 1 else ";") for k, p in enumerate(verts)]
    text += [" %d;" % len(faces)] + [" 3;%d,%d,%d;%s" % (f + ("," if k < len(faces) - 1 else ";",)) for k, f in enumerate(faces)]
    text += [" MeshMaterialList {", "  1;", "  1;", "  0;", "  Material {", "   1.0;1.0;1.0;1.0;;", "   5.0;", "   0.0;0.0;0.0;;", "   0.0;0.0;0.0;;", "  }", " }"]
    text += [" MeshNormals {", "  %d;" % len(normals)] + ["  %s;%s" % (row(n), "," if k < len(normals) - 1 else ";") for k, n in enumerate(normals)]
    text += ["  %d;" % len(faces)] + ["  3;%d,%d,%d;%s" % (f + ("," if k < len(faces) - 1 else ";",)) for k, f in enumerate(faces)] + [" }"]
    text += [" MeshTextureCoords {", "  %d;" % len(uvs)] + ["  %s;%s" % (row(t), "," if k < len(uvs) - 1 else ";") for k, t in enumerate(uvs)] + [" }", "}"]
    return "\n".join(text) + "\n"


folder = tempfile.mkdtemp(prefix="ns_inst_")
XF = os.path.join(folder, "cube.x"); FXF = os.path.join(folder, "cube.fx")
open(XF, "w").write(cube_x()); open(FXF, "w").write(FX_TEXT)
scene, col, root, cam = setup_scene()
bpy.data.objects.remove(root)
scene.render.resolution_x, scene.render.resolution_y = 192, 108
settings = scene.ninesync_fx
failed = []
class A:
    name = "cube"; path = XF; index = 0; keys = [Key(0)]
def check(ok, text):
    print(("ok   " if ok else "FAIL ") + text)
    if not ok:
        failed.append(text)
r = bpy.data.objects.new("root_cube", None); r["mmd_bridge_accessory"] = "cube"; col.objects.link(r)
mb.mme_build_effect(scene, col, settings, A, r, XF, FXF, 1.0, {"Tr": {"kind": "self", "item": "Tr"}})
key_tr(r, [(0, 1.0), (40, 0.2)])
passes = {c.name.split(".")[0].rsplit("_", 1)[-1]: c for c in r.children if c.get(mb.MME_PASS_KEY_PROP)}
check(sorted(passes) == ["P0", "P1", "P2"], "three passes built (%s)" % sorted(passes))
def has_branch(obj):
    return any(n.label == "Transparency Order" for g in all_trees(obj.modifiers[0].node_group) for n in g.nodes)
check(has_branch(passes["P0"]), "the affine additive pass has a Transparency Order switch")
check(not has_branch(passes["P1"]), "the pass that bends its copies has none")
check(has_branch(passes["P2"]), "the affine alpha-blended pass has one")
check(passes["P0"].get(mb.MME_ORDER_PASS_PROP) and not passes["P1"].get(mb.MME_ORDER_PASS_PROP), "eligible passes are marked for the Effect Controls override")
def instances(obj):
    dg = bpy.context.evaluated_depsgraph_get(); geometry = obj.evaluated_get(dg).evaluated_geometry()
    found = geometry.instances_pointcloud()
    return len(found.points) if found else 0
def realized(obj):
    dg = bpy.context.evaluated_depsgraph_get(); ev = obj.evaluated_get(dg); me = ev.to_mesh(); n = len(me.vertices)
    out = {"co": np.empty(n * 3), "attrs": sorted(a.name for a in me.attributes), "material": me.materials[0].name if len(me.materials) and me.materials[0] else ""}
    me.vertices.foreach_get("co", out["co"]); out["co"] = out["co"].reshape(-1, 3)
    for a in me.attributes:
        if a.domain == "POINT" and a.data_type == "FLOAT" and a.name.startswith(("mmeV_", "mmeL_", "mme_cull")):
            buf = np.empty(n); a.data.foreach_get("value", buf); out[a.name] = buf
    ev.to_mesh_clear()
    return out
def rebuild_tree(names):
    t = bpy.data.node_groups.new("rebuild varyings", "GeometryNodeTree")
    t.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    t.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N, L = t.nodes, t.links
    gin = N.new("NodeGroupInput"); gout = N.new("NodeGroupOutput")
    real = N.new("GeometryNodeRealizeInstances"); L.new(gin.outputs[0], real.inputs[0]); geo = real.outputs[0]
    def attr(name, kind):
        node = N.new("GeometryNodeInputNamedAttribute"); node.data_type = kind; node.inputs["Name"].default_value = name; return node.outputs["Attribute"]
    for name in names:
        rest = name[len("mmeV_"):]
        value = attr("mmeA_%s_k" % rest, "FLOAT")
        for block, feature in (("p", "mme_p"), ("n", "mme_n"), ("uv", "mme_uv")):
            dot = N.new("ShaderNodeVectorMath"); dot.operation = "DOT_PRODUCT"
            L.new(attr("mmeA_%s_%s" % (rest, block), "FLOAT_VECTOR"), dot.inputs[0]); L.new(attr(feature, "FLOAT_VECTOR"), dot.inputs[1])
            add = N.new("ShaderNodeMath"); L.new(value, add.inputs[0]); L.new(dot.outputs["Value"], add.inputs[1]); value = add.outputs[0]
        store = N.new("GeometryNodeStoreNamedAttribute"); store.data_type = "FLOAT"; store.domain = "POINT"; store.inputs["Name"].default_value = name
        L.new(geo, store.inputs[0]); L.new(value, store.inputs["Value"]); geo = store.outputs[0]
    L.new(geo, gout.inputs[0])
    return t
def kept(a, obj):
    drawn = ~(a["mme_cull"].reshape(12, -1) > 0.5).all(axis=1)
    return np.repeat(drawn, len(obj.data.vertices))
def close(a, b, keep, names):
    return len(b["co"]) == int(keep.sum()) and all(np.allclose(a[k][keep], b[k], rtol=1e-4, atol=1e-4 * max(1.0, np.abs(a[k]).max())) for k in ["co"] + names)
for f in (5, 20, 35):
    settings.mme_transparency_order = "MMD"; scene.frame_set(f)
    counts = {k: instances(o) for k, o in passes.items()}
    check(counts["P0"] == 12 and counts["P1"] == 0 and counts["P2"] == 0, "f%d MMD Order: only the additive affine pass is instanced %s" % (f, counts))
    settings.mme_transparency_order = "SORTED"; scene.frame_set(f)
    counts = {k: instances(o) for k, o in passes.items()}
    check(counts["P0"] == 12 and counts["P1"] == 0 and counts["P2"] == 12, "f%d Blender Sorted: the alpha-blended pass is instanced too %s" % (f, counts))
    settings.mme_transparency_order = "FULL"; scene.frame_set(f)
    check(all(instances(o) == 0 for o in passes.values()), "f%d Full Shader: nothing is instanced" % f)
    obj = passes["P2"]
    names = json.loads(obj.get("mme_varyings", "[]"))
    a = realized(obj)
    settings.mme_transparency_order = "MMD"; scene.frame_set(f)
    b = realized(obj)
    keep = kept(a, obj)
    check(close(a, b, keep, names), "f%d P2 MMD Order: the ordered mesh matches the full shader (%d of %d vertices)" % (f, len(b["co"]), len(a["co"])))
    check(not any(n.startswith("mmeA_") for n in b["attrs"]) and b["material"] == a["material"] and not b["material"].endswith("(instanced)"), "f%d P2 MMD Order: pass material, no coefficient attributes (%s)" % (f, b["material"]))
    for key in ("P0", "P2"):
        obj = passes[key]
        names = json.loads(obj.get("mme_varyings", "[]"))
        settings.mme_transparency_order = "FULL"; scene.frame_set(f)
        a = realized(obj)
        settings.mme_transparency_order = "SORTED"
        modifier = obj.modifiers.new("rebuild", "NODES"); modifier.node_group = rebuild_tree(names)
        scene.frame_set(f)
        b = realized(obj)
        obj.modifiers.remove(modifier)
        check(close(a, b, kept(a, obj), names), "f%d %s: instances plus the rebuilt varyings match the full shader (%d of %d vertices)" % (f, key, len(b["co"]), len(a["co"])))
settings.mme_transparency_order = "MMD"
for own, p0, p2 in (("SORTED", 12, 12), ("FULL", 0, 0), ("MMD", 12, 0), ("SCENE", 12, 0)):
    setattr(r, mb.MME_ORDER_OBJECT_PROP, own); scene.frame_set(20)
    got = (instances(passes["P0"]), instances(passes["P2"]))
    check(got == (p0, p2), "accessory override %s: P0, P2 instances %s" % (own, got))
settings.mme_transparency_order = "SORTED"; scene.frame_set(20)
check(instances(passes["P2"]) == 12, "Scene Default follows the scene setting again")
inst_mat = [m for m in bpy.data.materials if m.get("mme_owner", "").endswith("|instanced")]
check(len(inst_mat) == 2, "one instanced material per instanced pass (%d)" % len(inst_mat))
check(all(any(n.bl_idname == "ShaderNodeAttribute" and n.attribute_type == "INSTANCER" for n in m.node_tree.nodes) for m in inst_mat), "instanced materials read instance attributes")

def render():
    scene.frame_set(20); scene.render.engine = "BLENDER_EEVEE"
    scene.render.filepath = os.path.join(folder, "r"); bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(scene.render.filepath + ".png", check_existing=False); px = np.empty(len(image.pixels), np.float32); image.pixels.foreach_get(px)
    bpy.data.images.remove(image)
    return px.reshape(-1, 4)[:, :3]
for key in ("P0", "P2"):
    for o in passes.values():
        o.hide_render = o is not passes[key]
    settings.mme_transparency_order = "FULL"; off = render()
    settings.mme_transparency_order = "MMD"; on = render()
    check(off.max() > 0.05 and np.abs(off - on).max() <= 3 / 255, "%s renders the same in MMD Order as in Full Shader (max diff %.4f)" % (key, np.abs(off - on).max()))

settings.mme_transparency_order = "MMD"
obj = passes["P0"]
rebuild = obj.modifiers.new("rebuild", "NODES"); rebuild.node_group = rebuild_tree(json.loads(obj.get("mme_varyings", "[]")))
scene.frame_start, scene.frame_end = 5, 25
live = {}
for f in (7, 16):
    scene.frame_set(f); live[f] = realized(obj)
obj.modifiers.remove(rebuild)
with bpy.context.temp_override():
    result = bpy.ops.ninesync_fx.bake_effects()
check(result == {"FINISHED"} and obj.get("mme_baked"), "the instanced pass bakes (%s)" % result)
rebuild = obj.modifiers.new("rebuild", "NODES"); rebuild.node_group = rebuild_tree(json.loads(obj.get("mme_varyings", "[]")))
for f in (7, 16):
    scene.frame_set(f); baked = realized(obj)
    check(instances(obj) == 0 and baked["co"].shape == live[f]["co"].shape and all(np.allclose(baked[k], live[f][k], atol=1e-5) for k in live[f] if k not in ("attrs", "material")), "f%d: baked instances play back like the live ones" % f)
obj.modifiers.remove(rebuild)
with bpy.context.temp_override():
    bpy.ops.ninesync_fx.bake_effects(clear=True)
scene.frame_set(16)
check(instances(obj) == 12, "clearing the bake brings the live instances back")

names_before = sorted(m.name for m in bpy.data.materials)
bpy.ops.ninesync_fx.refresh_effects(root_name=r.name, force=True)
check(sorted(m.name for m in bpy.data.materials) == names_before, "a forced rebuild reuses the instanced materials (no new datablocks)")
passes = {c.name.split(".")[0].rsplit("_", 1)[-1]: c for c in r.children if c.get(mb.MME_PASS_KEY_PROP)}
settings.mme_transparency_order = "MMD"; scene.frame_set(20)
check(instances(passes["P0"]) == 12, "the rebuilt pass is still instanced")
print("FAILED:", "none" if not failed else failed)
