import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import shutil, tempfile

failed = []


def check(label, ok):
    print("%-70s %s" % (label, "ok" if ok else "FAILED"))
    if not ok:
        failed.append(label)


work = tempfile.mkdtemp(prefix="ninesync_rel_")
here = os.path.join(work, "here")
os.makedirs(os.path.join(here, "fx"))
for name in ("synth.x", "synth.fx"):
    shutil.copyfile(os.path.join(FIX, "Synth", name), os.path.join(here, "fx", name))
other_fx = os.path.join(work, "picked")
os.makedirs(other_fx)
shutil.copyfile(os.path.join(FIX, "Synth", "synth.fx"), os.path.join(other_fx, "picked.fx"))

scene = bpy.context.scene
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); scene.collection.objects.link(cam); scene.camera = cam

unsaved = mb.import_standalone_accessory(bpy.context, os.path.join(here, "fx", "synth.x"), 0.08, True, "")
check("unsaved: effect built", unsaved is not None and bool(unsaved.get("mme_fx")))
check("unsaved: both paths absolute", os.path.isabs(unsaved["mme_fx"]) and os.path.isabs(unsaved["mme_x"]))
bpy.data.objects.remove(unsaved)

blend = os.path.join(here, "scene.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend)
auto = mb.import_standalone_accessory(bpy.context, os.path.join(here, "fx", "synth.x"), 0.08, True, "")
check("saved: .fx stored relative to the .blend", auto["mme_fx"].startswith("//") and "synth.fx" in auto["mme_fx"])
check("saved: .x stored relative to the .blend", auto["mme_x"].startswith("//") and "synth.x" in auto["mme_x"])
check("saved: not marked as hand-picked", not auto.get("mme_fx_chosen"))
auto_name = auto.name

picked = mb.import_standalone_accessory(bpy.context, os.path.join(here, "fx", "synth.x"), 0.08, True, os.path.join(other_fx, "picked.fx"))
check("hand-picked .fx: stored absolute", os.path.isabs(picked["mme_fx"]) and picked["mme_fx"].lower().endswith("picked.fx"))
check("hand-picked .fx: .x still relative", picked["mme_x"].startswith("//"))
check("hand-picked .fx: marked", bool(picked.get("mme_fx_chosen")))
picked_name = picked.name
mb.mme_rebuild_effect(scene, picked, True)
check("hand-picked .fx stays absolute after a rebuild", os.path.isabs(picked["mme_fx"]) and picked["mme_fx_chosen"])
mb.mme_rebuild_effect(scene, bpy.data.objects[auto_name], True)
check("default .fx stays relative after a rebuild", bpy.data.objects[auto_name]["mme_fx"].startswith("//"))

bpy.ops.wm.save_mainfile()
moved = os.path.join(work, "moved")
shutil.copytree(here, moved)
shutil.rmtree(here)
bpy.ops.wm.open_mainfile(filepath=os.path.join(moved, "scene.blend"))
scene = bpy.context.scene
auto = bpy.data.objects[auto_name]
check("moved folder: relative paths resolve to the new place", os.path.isfile(mb.mme_resolved_path(auto["mme_fx"])) and os.path.dirname(mb.mme_resolved_path(auto["mme_fx"])).startswith(moved))
check("moved folder: the runtime loads from the new place", mb.mme_runtime_for(auto) is not None)
check("moved folder: a forced rebuild still works", mb.mme_rebuild_effect(scene, auto, True) is not None and bool(auto.children))
picked = bpy.data.objects[picked_name]
check("moved folder: the hand-picked absolute .fx still resolves", os.path.isfile(mb.mme_resolved_path(picked["mme_fx"])))
check("nothing is missing while the files are there", not mb.mme_missing_files(auto) and not mb.mme_missing_files(picked))
os.remove(mb.mme_resolved_path(auto["mme_fx"]))
lost = mb.mme_missing_files(auto)
check("a deleted .fx is reported with its resolved path", len(lost) == 1 and lost[0][0] == "Effect file" and lost[0][1].endswith("synth.fx"))
check("the other effect is not reported", not mb.mme_missing_files(picked))
check("a rebuild with a missing file does nothing", mb.mme_rebuild_effect(scene, auto, True) == 0)
try:
    result = bpy.ops.ninesync_fx.bake_effects(root_name=auto.name)
    refused = result == {"CANCELLED"}
    message = ""
except RuntimeError as error:
    refused, message = True, str(error)
check("baking is refused and names the file", refused and (not message or "not found" in message))
check("the bake did not start (no bake data was made)", not any(c.get("mme_baked") for c in auto.children))
try:
    bpy.ops.ninesync_fx.bake_effects(root_name=auto.name, clear=True)
    cleared = True
except RuntimeError:
    cleared = False
check("clearing bakes is still allowed", cleared)
with open(os.path.join(moved, "fx", "synth.fx"), "w") as handle:
    handle.write("")
shutil.copyfile(os.path.join(FIX, "Synth", "synth.fx"), os.path.join(moved, "fx", "synth.fx"))
check("restoring the file clears the report", not mb.mme_missing_files(auto))
shutil.rmtree(work, ignore_errors=True)
print("FAILED:", failed or "none")
