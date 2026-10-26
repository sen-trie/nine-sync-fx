import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
scene, col, root, cam = setup_scene()
settings = scene.ninesync_fx
mb.mme_build_effect(scene, col, settings, Acc(), root, XP, FX, 1.0, {"Tr": {"kind": "self", "item": "Tr", "from": "(self) / Tr"}, "morph_r": {"kind": "const", "values": [0.0], "from": "Thunder_ColorController.pmx / 赤"}})
texts = []
class L:
    def __getattr__(self, name):
        if name == "panel":
            return lambda *a, **k: (L(), L())
        def f(*a, **k):
            if name == "label": texts.append(k.get("text", ""))
            if name == "prop" and len(a) > 1: texts.append("[prop %s %s]" % (a[1], k.get("text", "")))
            return L()
        return f
    def __setattr__(self, n, v): pass
class Ctx: pass
for tab in ("LINKS", "PASSES", "PASS"):
  settings.fx_info_tab = tab
  for active in (root, mb.mme_effect_passes(root)[2]):
    ctx = Ctx(); ctx.scene = scene; ctx.active_object = active
    texts.clear()
    mb.NINESYNC_FX_PT_selected.draw(type("P", (), {"layout": L()})(), ctx)
    print("tab=%s active=%s:" % (tab, active.name), " | ".join(t for t in texts if t)[:600])
print("labels:", mb.NINESYNC_FX_PT_main.bl_label, "| category", mb.NINESYNC_FX_PT_main.bl_category, "| bl_info", mb.bl_info["name"])
r = bpy.ops.ninesync_fx.select_object(name=mb.mme_effect_passes(root)[0].name)
print("select op", r, bpy.context.view_layer.objects.active.name)
