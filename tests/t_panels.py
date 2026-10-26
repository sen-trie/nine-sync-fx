import sys, os, importlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
paths.prepare_available("ninesync", "ninesync_fx")
FIX = paths.fixtures()
import bpy
core = importlib.import_module("ninesync"); core.MIN_BLENDER = (5, 2, 0)
fxm = importlib.import_module("ninesync_fx"); fxm.MIN_BLENDER = (5, 2, 0)
mode = sys.argv[sys.argv.index("--") + 1]
if mode in ("core", "both"): core.register()
if mode in ("fx", "both"): fxm.register()
cui = importlib.import_module("ninesync.ui")
cui.mmd_tools_available = lambda: True
out = []
class L:
    def __getattr__(self, name):
        if name == "panel":
            return lambda *a, **k: (L(), L())
        def f(*a, **k):
            if name in ("label", "operator", "prop", "prop_search"):
                out.append("%s:%s" % (name, k.get("text", a[-1] if a else "")))
            return L()
        return f
    def __setattr__(self, n, v): pass
class Ctx: pass
ctx = Ctx(); ctx.scene = bpy.context.scene; ctx.active_object = None
if mode in ("core", "both"):
    ctx.scene.mmd_bridge.pmm_accessory_count = 3
    for name in ("MMDBRIDGE_PT_main", "MMDBRIDGE_PT_audio", "MMDBRIDGE_PT_camera", "MMDBRIDGE_PT_readout", "MMDBRIDGE_PT_options"):
        out.clear()
        getattr(cui, name).draw(type("P", (), {"layout": L()})(), ctx)
        print(name, "->", [o for o in out if "accessor" in o.lower() or "FX" in o or "Get " in o][:4], len(out))
if mode in ("fx", "both"):
    fui = importlib.import_module("ninesync_fx.ui")
    for name in ("NINESYNC_FX_PT_accessories", "NINESYNC_FX_PT_main", "NINESYNC_FX_PT_effects", "NINESYNC_FX_PT_bake", "NINESYNC_FX_PT_library", "NINESYNC_FX_PT_selected"):
        out.clear()
        getattr(fui, name).draw(type("P", (), {"layout": L()})(), ctx)
        print(name, "->", [o for o in out][:7], len(out))
