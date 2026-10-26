import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib, paths
paths.prepare("ninesync_fx")
import shim52
import bpy
fxm = importlib.import_module("ninesync_fx"); fxm.MIN_BLENDER = (5, 2, 0)
class P: pass
mods = ["ui", "standalone", "accessories", "mme", "mme_nodes", "fx", "xfile", "util", "pmm", "common"]
M = [importlib.import_module("ninesync_fx." + n) for n in mods]
class Proxy:
    def __getattr__(self, n):
        for m in [fxm] + M:
            if hasattr(m, n): return getattr(m, n)
        raise AttributeError(n)
    def __setattr__(self, n, v):
        for m in M:
            if n in m.__dict__: setattr(m, n, v)
mb = Proxy()
shim52.install(mb)
fxm.register()
bpy.ops.wm.open_mainfile(filepath=os.environ.get("NINESYNC_LEGACY_BLEND", "/tmp/legacy_old.blend"))
scene = bpy.context.scene
raw = scene.bl_system_properties_get().get("mmd_bridge")
print("legacy group present:", raw is not None, "density raw", raw.get("mme_density") if raw else None)
print("effect roots before repair:", len(mb.mme_effect_roots(scene)))
r = bpy.ops.ninesync_fx.repair_legacy()
s = scene.ninesync_fx
print("repaired:", r, "density", round(s.mme_density, 3), "entries", [(e.display, e.standalone) for e in s.accessories])
paths_ = sorted(set(t.data_path for g in bpy.data.node_groups if g.animation_data for d in g.animation_data.drivers for v in d.driver.variables for t in v.targets))
print("driver paths:", paths_)
root = mb.mme_effect_roots(scene)[0]
before = len(mb.mme_effect_passes(root))
r = bpy.ops.ninesync_fx.refresh_effects()
after = len(mb.mme_effect_passes(root))
print("rebuild:", r, "passes before/after", before, after)
for f in (1, 12, 30):
    scene.frame_set(f)
print("frame_set ok; errors:", dict(mb._mme_state["errors"]))
