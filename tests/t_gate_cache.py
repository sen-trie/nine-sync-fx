import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import glob, importlib, shutil, tempfile
import paths
pruning = importlib.import_module("ninesync_fx.runtime.pruning")
build_pass = importlib.import_module("ninesync_fx.runtime.build_pass")
cache_store = importlib.import_module("ninesync_fx.runtime.cache_store")

failed = []
checks = 0


def check(label, ok):
    global checks
    checks += 1
    if not ok:
        failed.append(label)


lv2 = os.environ.get("NINESYNC_THUNDER_LV2", os.path.join(paths.REPO, "debug-src"))
if not (os.path.isfile(os.path.join(lv2, "Thunder_lv2.fx")) and os.path.isfile(os.path.join(lv2, "Thunder_lv2.x"))):
    print("Thunder lv2 not found; set NINESYNC_THUNDER_LV2 to run the alpha gate cache test")
    print("FAILED: none")
    raise SystemExit(0)

work = tempfile.mkdtemp(prefix="ninesync_gates_")
os.makedirs(os.path.join(work, "fx"))
FXF = os.path.join(work, "fx", "Thunder_lv2.fx")
X = os.path.join(work, "fx", "Thunder_lv2.x")
shutil.copyfile(os.path.join(lv2, "Thunder_lv2.fx"), FXF)
shutil.copyfile(os.path.join(lv2, "Thunder_lv2.x"), X)

calls = []
original = build_pass.mme_alpha_gates


def checked(runtime, draw, state, vs_outputs, tr_names, instances, cached=False):
    before = list(cache_store.CACHE_STATS.get("gates", [0, 0]))
    got = original(runtime, draw, state, vs_outputs, tr_names, instances, cached)
    after = cache_store.CACHE_STATS.get("gates", [0, 0])
    fresh = pruning.mme_alpha_gates_search(runtime, draw, state, vs_outputs, tr_names, instances) or []
    hit = None if after == before else after[0] > before[0]
    calls.append((draw.fx_pass.name, hit, sorted(got) == sorted(fresh), len(got)))
    return got


empty_original = build_pass.mme_empty_at_zero_tr


def empty_checked(runtime, draw, state, data, vs_outputs, tr_names, instances, source=None):
    before = list(cache_store.CACHE_STATS.get("empty", [0, 0]))
    got = empty_original(runtime, draw, state, data, vs_outputs, tr_names, instances, source)
    after = cache_store.CACHE_STATS.get("empty", [0, 0])
    probe = pruning.mme_prune_state(state)
    for name in tr_names:
        probe.controls[name] = [0.0]
    fresh = pruning.mme_empty_at_zero_tr_search(runtime, draw, probe, data, vs_outputs, tr_names, instances) or ""
    hit = None if after == before else after[0] > before[0]
    calls.append((draw.fx_pass.name + " Tr=0", hit, got == fresh, 0))
    if got:
        proofs.append(draw.fx_pass.name)
    return got


proofs = []
build_pass.mme_alpha_gates = checked
build_pass.mme_empty_at_zero_tr = empty_checked

scene, col, root0, cam = setup_scene()
bpy.data.objects.remove(root0)
settings = scene.ninesync_fx
settings.mme_backend = "GEONODES"
scene.frame_start, scene.frame_end = 1, 120


class A:
    pass
A.name = "Thunder_lv2"; A.path = X; A.index = 0; A.keys = [Key(0)]
controls = {"Tr": {"kind": "self", "item": "Tr"}, "Si": {"kind": "self", "item": "Si"}}
root = bpy.data.objects.new("root_lv2", None); root["mmd_bridge_accessory"] = "Thunder_lv2"; col.objects.link(root)
def cache_files(pattern="*"):
    return glob.glob(os.path.join(mb.mme_cache_folder(), "gates_" + pattern)) + glob.glob(os.path.join(mb.mme_cache_folder(), "empty_" + pattern))


for f in cache_files():
    os.remove(f)


def step(label, action, expect=None):
    del calls[:]
    action()
    gated = [c for c in calls if c[1] is not None]
    check("%s: every pass's gates and Tr = 0 proof equal an uncached search" % label, calls and all(c[2] for c in calls))
    if expect == "miss":
        check("%s: the cache missed" % label, gated and not any(c[1] for c in gated))
    elif expect == "hit":
        check("%s: the cache hit" % label, gated and all(c[1] for c in gated))
    print("%-28s %s" % (label, ", ".join("%s %s%s" % (name, {True: "hit", False: "miss", None: "-"}[hit], " (%d gates)" % n if n else "") for name, hit, _same, n in calls)))


def rebuild():
    mb.mme_rebuild_effect(scene, root, True)


step("cold build", lambda: mb.mme_build_effect(scene, col, settings, A, root, X, FXF, 1.0, controls), "miss")
check("cold build: some pass has a Tr = 0 proof", len(proofs) > 0)
check("cold build: Tr = 0 proofs were looked up", any(name.endswith("Tr=0") and hit is not None for name, hit, _same, _n in calls))
step("rebuild, nothing changed", rebuild, "hit")
meta = mb.mme_param_meta(root)


def change_param():
    root[meta["MainColor"]["prop"]] = [0.5, 0.25, 1.0]
    rebuild()


step("MainColor changed", change_param)


def move_camera():
    cam.location.x += 3.0
    rebuild()


step("camera moved", move_camera)


def key_tr():
    root["mme_tr"] = 1.0; root.keyframe_insert('["mme_tr"]', frame=1)
    root["mme_tr"] = 0.25; root.keyframe_insert('["mme_tr"]', frame=120)
    scene.frame_set(60)
    rebuild()


step("Tr keyed", key_tr)


def edit_shader():
    with open(FXF, "a", encoding="latin-1") as handle:
        handle.write("\n// edited\n")
    rebuild()


step("shader file edited", edit_shader, "miss")


def add_texture():
    os.makedirs(os.path.join(work, "Texture"), exist_ok=True)
    image = bpy.data.images.new("gate_probe", 4, 4)
    image.pixels = [0.5] * 64
    image.filepath_raw = os.path.join(work, "Texture", "Line.png")
    image.file_format = "PNG"
    image.save()
    rebuild()


step("texture added", add_texture, "miss")


def damage():
    for f in cache_files():
        with open(f, "w") as handle:
            handle.write("{not json")
    rebuild()


step("cache files damaged", damage, "miss")
step("rebuild after repair", rebuild, "hit")
check("gate files are in the cache folder", len(glob.glob(os.path.join(mb.mme_cache_folder(), "gates_*.json"))) > 0)
check("Tr = 0 proof files are in the cache folder", len(glob.glob(os.path.join(mb.mme_cache_folder(), "empty_*.json"))) > 0)
build_pass.mme_alpha_gates = original
build_pass.mme_empty_at_zero_tr = empty_original
shutil.rmtree(work, ignore_errors=True)
print("alpha gate cache: %d of %d checks passed" % (checks - len(failed), checks))
print("FAILED:", failed or "none")
