import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *

FX2 = (FIX + "/Synth/synth.fx"); X2 = (FIX + "/Synth/synth.x")
scene, col, root, cam = setup_scene()
bpy.data.objects.remove(root)
settings = scene.ninesync_fx
settings.mme_core_autosave = False
failures = []


def check(label, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + label + ((" -- " + str(detail)) if detail and not condition else ""))
    if not condition:
        failures.append(label)


import shutil, tempfile
temp = tempfile.mkdtemp(prefix="ninesync_labels_")
text = open(FX2, encoding="utf-8", errors="replace").read()
text = text.replace("float4 PS(VS_OUT IN) : COLOR0 { return IN.Col; }", "float Wave(float t) { return sin(t * 6.0) * 0.5 + 0.5; }\nfloat4 PS(VS_OUT IN) : COLOR0 { return IN.Col * Wave(IN.Col.x + Time); }")
assert "Wave(IN.Col.x" in text
FX2 = os.path.join(temp, "synth.fx")
with open(FX2, "w", encoding="utf-8") as handle:
    handle.write(text)
X2 = os.path.join(temp, "synth.x")
shutil.copy(os.path.join(FIX, "Synth", "synth.x"), X2)


class A2:
    name = "synth"; path = X2; index = 0; keys = [Key(0)]


r = bpy.data.objects.new("root_labels", None); r["mmd_bridge_accessory"] = "synth_labels"; col.objects.link(r)
settings.mme_backend = "GEONODES"
kids, *_ = mb.mme_build_effect(scene, col, settings, A2, r, X2, FX2, 1.0, {})
passes = [k for k in kids if k.get("mme_draw")]
check("the effect builds passes", len(passes) > 0)
group_nodes = []
for obj in passes:
    for mod in obj.modifiers:
        if mod.type == "NODES" and mod.node_group is not None:
            group_nodes += [(mod.node_group, n) for n in mod.node_group.nodes if n.bl_idname == "GeometryNodeGroup" and n.node_tree is not None and n.node_tree.name.startswith("MME VS")]
    for slot in obj.material_slots:
        if slot.material is not None and slot.material.node_tree is not None:
            group_nodes += [(slot.material.node_tree, n) for n in slot.material.node_tree.nodes if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None and n.node_tree.name.startswith("MME PS")]
vertex = [n for tree, n in group_nodes if n.bl_idname == "GeometryNodeGroup"]
pixel = [n for tree, n in group_nodes if n.bl_idname == "ShaderNodeGroup"]
check("Geometry Nodes vertex-shader groups are labelled MMEffects", vertex and all(n.label == "MMEffects" for n in vertex), [n.label for n in vertex])
check("Shader Nodes pixel-shader groups are labelled MMEffects", pixel and all(n.label == "MMEffects" for n in pixel), [n.label for n in pixel])
calls = []
for tree in list(bpy.data.node_groups) + [m.node_tree for m in bpy.data.materials if m.node_tree is not None]:
    for node in tree.nodes:
        if node.bl_idname in ("GeometryNodeGroup", "ShaderNodeGroup") and node.node_tree is not None and node.node_tree.name.startswith(("MME fn", "MME in")):
            calls.append(node)
check("a translated HLSL function appears as a group node", len(calls) > 0)
check("translated HLSL function and intrinsic groups carry the MMEffects label", all(n.label.startswith("MMEffects") for n in calls), [n.label for n in calls][:6])
print("checked %d vertex, %d pixel and %d function/intrinsic group nodes" % (len(vertex), len(pixel), len(calls)))
print("FAILED: %s" % failures if failures else "ALL OK")
raise SystemExit(1 if failures else 0)
