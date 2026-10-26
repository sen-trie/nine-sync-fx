import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import math, types
scene = bpy.context.scene
for o in list(bpy.data.objects): bpy.data.objects.remove(o)
settings = scene.ninesync_fx
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); scene.collection.objects.link(cam); scene.camera = cam
fake_root = bpy.data.objects.new("ColorCtl", None); scene.collection.objects.link(fake_root)
me = bpy.data.meshes.new("ctlmesh"); me.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
ctl = bpy.data.objects.new("ColorCtl_mesh", me); scene.collection.objects.link(ctl); ctl.parent = fake_root
ctl.shape_key_add(name="Basis")
for n in ("赤", "緑", "青", "加算倍率", "透明度"):
    ctl.shape_key_add(name=n)
for kb in ctl.data.shape_keys.key_blocks: kb.value = 0.0
ctl.data.shape_keys.key_blocks["赤"].value = 1.0
ctl.hide_render = True
mb.mme_scene_model = lambda name: fake_root if "colorcontroller" in name.lower() else None
r = bpy.ops.import_scene.ninesync_fx_accessory(filepath=XP, scale=0.08)
print("import result", r)
entry = settings.accessories[0]
col = bpy.data.collections.get(mb.ACCESSORY_COLLECTION)
root = mb.find_accessory_root(col, entry.key)
print("entry", entry.key, "standalone", entry.standalone, "use_effect", entry.use_effect, "root", root.name if root else None, "passes", len(mb.mme_effect_passes(root)))
ctrls = mb.mme_load_controls(root)
print("controller links:", sorted((k, v["kind"]) for k, v in ctrls.items() if v.get("kind") != "self"))
print("overridden MainColor by:", mb.mme_overriding_sources(root, "MainColor"))
key_tr(root, [(0, 1.0), (60, 0.0)])
root.location = (0, 0, 0)
cam.location = (0.0, -4.8, 2.0); cam.rotation_euler = (math.radians(68), 0, 0)
scene.view_settings.view_transform = "Standard"
scene.render.engine = "CYCLES"; scene.cycles.samples = 4; scene.cycles.device = "CPU"
scene.render.resolution_x, scene.render.resolution_y = 320, 180
scene.render.film_transparent = True
def look(tag):
    scene.frame_set(20)
    scene.render.filepath = (FIX + "/out/sa_%s.png") % tag
    bpy.ops.render.render(write_still=True)
    im = bpy.data.images.load(scene.render.filepath); a = np.array(im.pixels[:]).reshape(-1, 4)
    v = a[a[:, :3].max(1) > 0.05]
    m = v[:, :3].mean(0) if len(v) else np.zeros(3)
    print("%-26s lit %5d  mean rgb %.3f %.3f %.3f" % (tag, len(v), *m)); return m, len(v)
red, _ = look("shape key red")
kb = ctl.data.shape_keys.key_blocks
kb["赤"].value = 0.0; kb["青"].value = 1.0
blue, _ = look("shape key blue (live)")
print("live shape key link:", red[0] > red[2], blue[2] > blue[0])
use = mb.mme_use_prop("Thunder_ColorController.pmx")
root[use] = False
print("overridden after untick:", mb.mme_overriding_sources(root, "MainColor"))
root.ninesync_visible = False
passes = mb.mme_effect_passes(root)
print("eye off -> passes hidden (viewport, render):", all(p.hide_viewport and p.hide_render for p in passes), "eye reads", root.ninesync_visible)
_, n = look("eye off")
root.ninesync_visible = True
_, n2 = look("eye on")
print("render respects eye:", n == 0, n2 > 0)
proj = types.SimpleNamespace(models=[], accessories=[], accessory_error=None)
mb.refresh_accessories(settings, proj)
print("standalone entry kept after PMM refresh:", len(settings.accessories) == 1)
before = set(bpy.data.objects.keys())
r = bpy.ops.import_scene.ninesync_fx_accessory(filepath=(FIX + "/plain/plainthing.x"), scale=0.08)
new = set(bpy.data.objects.keys()) - before
print("plain import:", r, sorted(new), "entries", len(settings.accessories), "in accessory collection:", any(bpy.data.objects[n].name in col.objects for n in new))
acc = mb.standalone_accessory((FIX + "/plain/plainthing.x"), "x:test")
e2 = settings.accessories.add(); e2.key = "x:test"; e2.display = "plainthing.x"
mb.effect_setup(proj, settings, acc, e2, acc.path, root)
print("no .fx -> use_effect default:", e2.use_effect)
