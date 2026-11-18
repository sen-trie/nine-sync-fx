import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import shutil, tempfile, types

FAILED = []
def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        FAILED.append(what)

scene = bpy.context.scene
for o in list(bpy.data.objects): bpy.data.objects.remove(o)
settings = scene.ninesync_fx
hooks = importlib.import_module("ninesync_fx.hooks")
d = tempfile.mkdtemp()
for ext in ("fx", "x"):
    shutil.copy(FIX + "/Synth/synth." + ext, os.path.join(d, "synth." + ext))
settings.accessory_dir = d
settings.mme_backend = "PYTHON"
scene.frame_start, scene.frame_end = 1, 6

def key(frame, x):
    return mb.AccessoryKey(frame, 1, -1, 0, (x, 0.0, 0.0), (0.0, 0.0, 0.0), 1.0, 0, 1.0)
good = types.SimpleNamespace(index=0, name="synth", path=os.path.join(d, "synth.x"), draw_order=0, keys=[key(0, 0.0)])
gone = types.SimpleNamespace(index=1, name="gone", path="C:/nowhere/gone.x", draw_order=1, keys=[key(0, 0.0)])
project = types.SimpleNamespace(models=[], accessories=[good], accessory_error=None)

def ctx(integrated=True, cancelled=None):
    out = types.SimpleNamespace(scene=scene, project=project, pmm_path=os.path.join(d, "t.pmm"), targets={}, unit=1.0, retimed=True, core_version="test", integrated=integrated)
    if integrated:
        out.cancelled = cancelled or (lambda: False)
        out.progress = lambda f, t="": None
    return out

def drain(gen):
    steps = 0
    try:
        while True:
            next(gen); steps += 1
    except StopIteration as done:
        return done.value, steps

def sync(c=None):
    result = hooks.registry()["post_sync"][hooks.FX_OWNER](c or ctx())
    if hasattr(result, "send"):
        return drain(result)[0]
    return result

def passes():
    col = mb.accessory_collection(scene)
    root = next(o for o in col.objects if o.get("mmd_bridge_accessory"))
    return root, {c["mme_pass_key"]: c for c in root.children if c.get("mme_pass_key")}

result = sync()
check(result["imported"] == 1 and result["failed"] == 0, "generator hook returns a result dict: %r" % (result,))
for name in ("synth.x", "synth.fx"):
    os.utime(os.path.join(d, name), (1_600_000_000, 1_600_000_000))
sync()
root, first = passes()
check(len(first) >= 2, "synth builds %d passes" % len(first))
for p in first.values():
    p["marker"] = 1
    mb.mme_set_bake_record(p, state="baked")
    p[mb.MME_BAKED_PROP] = True
pointers = {k: p.as_pointer() for k, p in first.items()}

os.utime(os.path.join(d, "synth.x"), (1_700_000_000, 1_700_000_000))
os.utime(os.path.join(d, "synth.fx"), (1_700_000_000, 1_700_000_000))
sync()
root, now = passes()
check({k: p.as_pointer() for k, p in now.items()} == pointers and all(p.get("marker") for p in now.values()), "S2: mtime-only change keeps every pass object")

moved = tempfile.mkdtemp()
for ext in ("fx", "x"):
    shutil.copy(os.path.join(d, "synth." + ext), os.path.join(moved, "synth." + ext))
settings.accessory_dir = moved
good.path = os.path.join(moved, "synth.x")
sync()
root, now = passes()
check({k: p.as_pointer() for k, p in now.items()} == pointers, "S2: path-only change (copied project) keeps every pass object")
d = moved

fx_file = os.path.join(d, "synth.fx")
original = open(fx_file, encoding="utf-8", errors="replace").read()
open(fx_file, "w", encoding="utf-8").write(original + "\nfloat4 broken( {{{ \n")
result = sync()
root, now = passes()
check(set(now) == set(first) and all(p.get("marker") for p in now.values()), "D6: an invalid effect keeps the working passes (%d)" % len(now))
check(result["failed"] >= 1 and result["warnings"], "S6: the failure is reported: %r" % (result,))
open(fx_file, "w", encoding="utf-8").write(original)
result = sync()
check(result["failed"] == 0, "recovers after the effect is fixed: %r" % (result,))

project.accessories = [good, gone]
result = sync()
check(result["imported"] == 1 and result["failed"] == 1, "S6: one failing accessory among good ones: %r" % (result,))
project.accessories = [gone]
result = sync()
check(result["imported"] == 0 and result["failed"] == 1, "S6: every accessory failing is not success: %r" % (result,))
project.accessories = [good]

check(mb._mme_state.get("progress") is None and not mb._mme_state.get("integrated"), "integrated sync leaves no FX progress state")
gen = hooks.registry()["post_sync"][hooks.FX_OWNER](ctx())
next(gen)
gen.close()
check(not mb._mme_state.get("integrated"), "closing the hook mid-way releases the integrated flag")
root, now = passes()
check(len(now) == len(first), "cancel keeps the built passes")
sync()

legacy = hooks.registry()["post_sync"][hooks.FX_OWNER](ctx(integrated=False))
check(isinstance(legacy, int), "a core without ctx.cancelled gets an int")

arm_data = bpy.data.armatures.new("arm")
arm = bpy.data.objects.new("arm", arm_data)
scene.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="EDIT")
bone = arm_data.edit_bones.new("hand"); bone.head = (0, 0, 0); bone.tail = (0, 0, 1)
bpy.ops.object.mode_set(mode="OBJECT")
pb = arm.pose.bones["hand"]
for frame, x in ((1, 1.0), (50, 1.0)):
    pb.location.x = x
    pb.keyframe_insert("location", index=0, frame=frame)
con = root.constraints.new("CHILD_OF"); con.target = arm; con.subtarget = "hand"
obj = next(iter(passes()[1].values()))
if not hasattr(bpy.types.NodesModifier.bl_rna.properties, "get") or "properties" not in bpy.types.NodesModifier.bl_rna.properties:
    obj.modifiers[mb.MME_GN_MODIFIER].node_group = None
def bake_sig():
    inputs = mb.mme_bake_inputs(obj, root, scene, settings)
    return importlib.import_module("ninesync_fx.common.digest").digest(inputs), mb.mme_portable_bake_sig(obj, root, inputs), inputs
a, pa, inputs = bake_sig()
check(not inputs.get("unverifiable"), "bone dependency is verifiable: %r" % inputs.get("unverifiable"))
for fc in mb.iter_fcurves(arm.animation_data.action):
    if fc.data_path.endswith("location") and fc.array_index == 0:
        fc.keyframe_points[1].co[1] = 5.0
b, pb_, _ = bake_sig()
check(a != b and pa != pb_, "S3: a changed parent-bone key changes the local and the portable signature")
arm.pose.bones["hand"].scale.x = 1.0
c, pc, _ = bake_sig()
check(c == b, "an unrelated no-change read is stable")

target = bpy.data.objects.new("lamp_target", None)
scene.collection.objects.link(target)
obj["probe"] = 0.0
fc = obj.driver_add('["probe"]')
var = fc.driver.variables.new(); var.type = "SINGLE_PROP"; var.targets[0].id = target; var.targets[0].data_path = "location[0]"
fc.driver.expression = "var"
check(not mb.mme_broken_bindings(obj), "driver with a live target is not broken")
bpy.data.objects.remove(target)
check(bool(mb.mme_broken_bindings(obj)), "S5: a driver whose target was deleted is detected")
obj.animation_data.drivers.remove(fc)

lines = 2996
text = bpy.data.texts.new(mb.LOG_TEXT_NAME) if bpy.data.texts.get(mb.LOG_TEXT_NAME) is None else bpy.data.texts[mb.LOG_TEXT_NAME]
text.clear()
text.write("\n".join("core line %d" % i for i in range(lines)) + "\n")
for i in range(10):
    mb.log.info("fx line %d", i)
bodies = [l.body for l in text.lines]
check(any(b.startswith("core line") for b in bodies) and len(bodies) < 3000, "S10: the log trim keeps earlier lines (%d left, %d core)" % (len(bodies), sum(b.startswith("core line") for b in bodies)))

print("FAILED:", FAILED or "none")
