import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import shutil, types

failed = []


def check(label, ok):
    print("%-60s %s" % (label, "ok" if ok else "FAILED"))
    if not ok:
        failed.append(label)


work = tempfile.mkdtemp(prefix="ninesync_emm_")
mmd = os.path.join(work, "mmd")
effects = os.path.join(mmd, "UserFile", "Effects", "E")
os.makedirs(effects)
os.makedirs(os.path.join(mmd, "UserFile", "Accessory"))
os.makedirs(os.path.join(work, "outside"))
os.makedirs(os.path.join(work, "proj"))
synth_x, synth_fx = FIX + "/Synth/synth.x", FIX + "/Synth/synth.fx"
shutil.copy(synth_x, os.path.join(effects, "synth.x"))
shutil.copy(synth_fx, os.path.join(effects, "synth.fx"))
shutil.copy(synth_fx, os.path.join(effects, "other.fx"))
shutil.copy(FIX + "/plain/plainthing.x", os.path.join(mmd, "UserFile", "Accessory", "a.x"))
shutil.copy(FIX + "/plain/plainthing.x", os.path.join(work, "outside", "b.x"))
shutil.copy(FIX + "/plain/plainthing.x", os.path.join(effects, "gone.x"))
shutil.copy(FIX + "/plain/plainthing.x", os.path.join(effects, "c.x"))
shutil.copy(synth_fx, os.path.join(effects, "c.fx"))
pmm = os.path.join(work, "proj", "p.pmm")
open(pmm, "wb").close()
outside_b = os.path.join(work, "outside", "b.x")


def write_emm(text):
    with open(os.path.splitext(pmm)[0] + ".emm", "wb") as handle:
        handle.write(text.replace("\n", "\r\n").encode("cp932"))


write_emm("""[Info]
Version = 3

[Object]
Acs1 = UserFile\\Accessory\\a.x
Pmd2 = X:\\elsewhere\\モデル.pmx
Acs3 = UserFile\\Effects\\E\\synth.x
Acs4 = UserFile\\Effects\\E\\synth.x
Acs5 = %s
Acs6 = UserFile\\Effects\\E\\gone.x

[Effect]
Default = none
Acs1 = none
Pmd2 = none
Acs3 = UserFile\\Effects\\E\\other.fx
Acs3[1] = none
Acs4 = none
Acs4.show = false
Acs5 = UserFile\\Effects\\E\\synth.fx
Acs6 = UserFile\\Effects\\E\\missing.fx

[Effect@RT]
Owner = Acs1
Acs3 = hide
""" % outside_b.replace("/", "\\"))


def acc(index, name, path, draw):
    return types.SimpleNamespace(index=index, name=name, path=path.replace("/", os.sep), draw_order=draw, keys=[mb.AccessoryKey(0, 1, -1, 0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 1.0, 0, 1.0)])


synth_rec = os.path.join(effects, "synth.x")
accs = [
    acc(0, "synth.x", synth_rec, 3),
    acc(1, "a.x", os.path.join(mmd, "UserFile", "Accessory", "a.x"), 0),
    acc(2, "synth.x", synth_rec, 1),
    acc(3, "b.x", outside_b, 2),
    acc(4, "gone.x", os.path.join(effects, "gone.x"), 4),
    acc(5, "c.x", os.path.join(effects, "c.x"), 5),
]
project = types.SimpleNamespace(models=[types.SimpleNamespace(index=0, name="m", path="X:\\elsewhere\\モデル.pmx")], accessories=accs, accessory_error=None)
settings = bpy.context.scene.ninesync_fx
settings.filepath = pmm


def look(a, entry=None):
    return mb.mme_effect_lookup(a, a.path.replace("\\", os.sep), settings, entry, project)


emm = mb.mme_read_emm(pmm)
check("Shift-JIS .emm parsed, Acs and Pmd slots", sorted(emm.objects) == ["acs1", "acs3", "acs4", "acs5", "acs6", "pmd2"])
check("[Effect@tab] entries not read as main assignments", emm.effects["acs3"].endswith("other.fx"))
check("show flag and subset entry read", emm.shown == {"acs4": False} and emm.subsets == {"acs3": {1: "none"}})
check("slots by path; copies paired in draw order, not PMM index", [mb.mme_emm_slot(emm, a, accs) for a in accs] == ["acs4", "acs1", "acs3", "acs5", "acs6", None])
c = look(accs[1])
check("'none' means no effect even with a same-name rule fallback", c.path is None and c.source == "emm-none")
c = look(accs[2])
check("relative effect resolved against the MMD folder", c.path is not None and os.path.samefile(c.path, os.path.join(effects, "other.fx")) and c.source == "emm" and c.subsets)
c = look(accs[0])
check("second copy: none and hidden by .show = false", c.path is None and not c.shown)
c = look(accs[3])
check("object outside the MMD folder, relative effect", c.path is not None and os.path.samefile(c.path, os.path.join(effects, "synth.fx")))
c = look(accs[4])
check("assigned effect missing: no guess, reported", c.path is None and c.source == "emm-missing" and c.value.endswith("missing.fx"))
c = look(accs[5])
check("accessory the .emm does not list: same-name .fx", c.path is not None and c.source == "beside")
alone = acc(-1, "a.x", os.path.join(mmd, "UserFile", "Accessory", "a.x"), 0)
alone.standalone = True
check("import without a PMM never reads the .emm", look(alone).source == "beside")

write_emm("""[Object]
Acs1 = UserFile\\Effects\\E\\c.x

[Effect]
Default = UserFile\\Effects\\E\\other.fx
""")
c = look(accs[5])
check("Default applies to a listed accessory without an entry", c.source == "emm-default" and os.path.basename(c.path) == "other.fx")
os.remove(os.path.splitext(pmm)[0] + ".emm")
check("no .emm: same-name .fx", look(accs[5]).source == "beside")

write_emm("""[Object]
Acs1 = UserFile\\Effects\\E\\synth.x
Acs2 = UserFile\\Effects\\E\\synth.x

[Effect]
Acs1 = none
Acs2 = UserFile\\Effects\\E\\synth.fx
""")
scene = bpy.context.scene
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); scene.collection.objects.link(cam); scene.camera = cam
pair = [acc(0, "synth.x", synth_rec, 1), acc(1, "synth.x", synth_rec, 0)]
ctx = types.SimpleNamespace(scene=scene, project=types.SimpleNamespace(models=[], accessories=pair, accessory_error=None), pmm_path=pmm, targets={}, unit=1.0)
done = mb.sync_accessories(ctx)
col = mb.accessory_collection(scene)
roots = {e.key: mb.find_accessory_root(col, e.key) for e in settings.accessories}
off, on = roots["1:synth.x"], roots["0:synth.x"]
check("sync: both imported", done == 2 and off is not None and on is not None)
check("sync: 'none' accessory built no effect", not mb.mme_effect_passes(off) and not off.get("mme_fx"))
check("sync: assigned accessory built its effect", len(mb.mme_effect_passes(on)) > 0)
print("FAILED: %s" % (", ".join(failed) if failed else "none"))
