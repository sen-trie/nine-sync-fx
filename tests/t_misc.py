import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
scene, col, root, cam = setup_scene()
settings = scene.ninesync_fx
class K:
    def __init__(self, frame, opacity):
        self.frame = frame; self.opacity = opacity; self.scale = 1.0; self.visible = True
        self.position = (0.5, 0.0, 0.2); self.rotation = (0.0, 0.0, 0.0); self.parent_model = -1; self.parent_bone = -1
class A:
    name = "Thunder_lv1.x"; path = XP; index = 0
    keys = [K(0, 1.0), K(60, 0.0)]
    parent_model = -1; parent_bone = -1
class P:
    models = []; accessories = [A]; accessory_error = None
bpy.data.objects.remove(root)
for k in ("parent_model", "parent_bone", "model_index", "bone_index"):
    pass
mb.refresh_accessories(settings, P)
entry = settings.accessories[0] if len(settings.accessories) else None
print("entries:", [(e.key, e.display) for e in settings.accessories])
ok = mb.import_accessory(scene, col, P, settings, A, entry, 1.0)
root = [o for o in col.objects if o.get("mmd_bridge_accessory")][0]
print("import ok:", ok, root.name, "note:", entry.effect_note if entry else None)
print("controls:", json.loads(root["mme_controls"]))
passes = mb.mme_effect_passes(root)
line = [p for p in passes if "Line" in p.name][0]
mat = line.data.materials[0]
tree = mat.node_tree
gamma = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeGamma")
boost = tree.nodes.new("ShaderNodeMath"); boost.operation = "MULTIPLY"; boost.inputs[1].default_value = 3.0; boost.label = "user boost"
emission = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeEmission")
tree.links.new(gamma.outputs[0], boost.inputs[0]); tree.links.new(boost.outputs[0], emission.inputs["Strength"])
print("user edited material; signature differs:", mat.get("mme_sig") != mb.mme_tree_signature(tree))
r = bpy.ops.ninesync_fx.refresh_effects(root_name=root.name)
line2 = [p for p in mb.mme_effect_passes(root) if "Line" in p.name][0]
mat2 = line2.data.materials[0]
print("after rebuild same material:", mat2 == mat, "user node kept:", any(n.label == "user boost" for n in mat2.node_tree.nodes), "group linked:", any(n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None for n in mat2.node_tree.nodes), "inputs wired:", sum(1 for n in mat2.node_tree.nodes if n.name.startswith("mmeIn_")))
print("orphan groups:", [g.name for g in bpy.data.node_groups if g.users == 0])
cam.data.type = "ORTHO"; cam.data.ortho_scale = 60.0
settings.mme_backend = "PYTHON"
r = bpy.ops.ninesync_fx.refresh_effects(root_name=root.name)
py = {json.loads(p["mme_draw"])["pass"]: p for p in mb.mme_effect_passes(root)}
def world(o):
    dg = bpy.context.evaluated_depsgraph_get(); ev = o.evaluated_get(dg); m = ev.to_mesh(); n = len(m.vertices)
    co = np.empty(n * 3, np.float32); m.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    cu = np.empty(n, np.float32); m.attributes["mme_cull"].data.foreach_get("value", cu)
    M = np.array(ev.matrix_world); w = co @ M[:3, :3].T + M[:3, 3]; ev.to_mesh_clear(); return w, cu
scene.frame_set(20)
pyw = {k: world(o) for k, o in py.items()}
settings.mme_backend = "GEONODES"
r = bpy.ops.ninesync_fx.refresh_effects(root_name=root.name)
scene.frame_set(21); scene.frame_set(20)
for p in mb.mme_effect_passes(root):
    w, cu = world(p); wp, cp = pyw[json.loads(p["mme_draw"])["pass"]]
    if w.shape == wp.shape:
        vis = (cu < 0.5) & (cp < 0.5)
        print("ortho %-30s err %.2e cull mismatch %d" % (p.name, np.abs(w[vis] - wp[vis]).max() if vis.any() else 0, int(((cu > .5) != (cp > .5)).sum())))
    else:
        print("ortho", p.name, "shape", w.shape, wp.shape)
class L:
    def __getattr__(self, name):
        return lambda *a, **k: L()
    def __setattr__(self, name, value):
        pass
panel = mb.NINESYNC_FX_PT_accessories
entry.show_controls = True
class Ctx: pass
ctx = Ctx(); ctx.scene = scene
fake = type("FakePanel", (), {"layout": L()})()
panel.draw(fake, ctx)
print("panel draw ok")
