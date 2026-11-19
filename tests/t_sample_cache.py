import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import hashlib, importlib, shutil, tempfile, types
import paths
cache_mod = importlib.import_module("ninesync_fx.runtime.sample_cache")
strips = importlib.import_module("ninesync_fx.runtime.strips")

failed = []
checks = 0


def check(label, ok):
    global checks
    checks += 1
    if not ok:
        failed.append(label)


rng = np.random.default_rng(11)
real_key = cache_mod.mme_exact_state_key
cache_mod.mme_exact_state_key = lambda probe, reads, program: (probe["state"], tuple(sorted(probe[r] for r in reads)))


def batch(n, per, seed, comps=2):
    r = np.random.default_rng(seed)
    world = r.standard_normal((n * per, 3))
    cull = r.random(n * per) < 0.3
    return world, cull, [r.standard_normal(n * per).astype(np.float32) for _ in range(comps)] + [np.float32(0.5)]


def collect(cache, pass_key, probe, reads, batches, per, budget):
    entry = []
    for i0, i1, world, cull, comps in batches:
        cache.add(entry, i0, i1, world, cull, comps, per)
    cache.commit(pass_key, frozenset(reads), probe, None, entry, budget)


def replay(cache, found):
    return [(i0, i1, w.copy(), c.copy(), [x.copy() for x in comps]) for i0, i1, w, c, comps in cache.batches(found[1])]


def same_batches(a, b, per):
    if len(a) != len(b):
        return False
    for (i0, i1, w, c, comps), (j0, j1, w2, c2, comps2) in zip(a, b):
        count = (i1 - i0) * per
        if (i0, i1) != (j0, j1) or w.tobytes() != np.asarray(w2, dtype=np.float64).reshape(count, 3).tobytes():
            return False
        if c.tobytes() != np.broadcast_to(np.asarray(c2, dtype=bool), (count,)).tobytes():
            return False
        for x, y in zip(comps, comps2):
            y = np.broadcast_to(np.asarray(y), (count,))
            if x.dtype != y.dtype or x.tobytes() != y.tobytes():
                return False
    return True


per = 50
cache = cache_mod.MmeSampleCache()
w1, c1, v1 = batch(3, per, 1)
w2, c2, v2 = batch(2, per, 2)
state_a = [(0, 3, w1, c1, v1), (3, 5, w2, c2, v2)]
collect(cache, "pass", {"state": "a", "Tr": 1.0, "time": 0.0}, ["Tr"], state_a, per, 1 << 30)
found = cache.find("pass", {"state": "a", "Tr": 1.0, "time": 5.0}, None)
check("hit when only an unread value differs", found is not None)
check("replay gives the same bits in the same batches", found is not None and same_batches(replay(cache, found), state_a, per))
check("replay keeps scalar varyings' dtype", found is not None and replay(cache, found)[0][4][-1].dtype == np.float32)
check("miss when a read value differs", cache.find("pass", {"state": "a", "Tr": 0.5, "time": 0.0}, None) is None)
check("miss under another pass key", cache.find("other", {"state": "a", "Tr": 1.0, "time": 0.0}, None) is None)
one = cache.bytes
collect(cache, "pass", {"state": "b", "Tr": 1.0}, ["Tr"], [(0, 3, w1.copy(), c1.copy(), [x.copy() for x in v1]), (3, 5, w2.copy(), c2.copy(), [x.copy() for x in v2])], per, 1 << 30)
check("identical instances are stored once", cache.bytes == one)
w3 = w1.copy()
w3[7, 1] = np.nextafter(w3[7, 1], np.inf)
collect(cache, "pass", {"state": "c", "Tr": 1.0}, ["Tr"], [(0, 3, w3, c1.copy(), [x.copy() for x in v1])], per, 1 << 30)
found_c = cache.find("pass", {"state": "c", "Tr": 1.0}, None)
check("one-ulp position change is a separate instance", cache.bytes > one and found_c is not None and replay(cache, found_c)[0][2].tobytes() == w3.tobytes())
v4 = [x.copy() for x in v1]
v4[0][[0, 1]] = v4[0][[1, 0]]
collect(cache, "pass", {"state": "d", "Tr": 1.0}, ["Tr"], [(0, 3, w1.copy(), c1.copy(), v4)], per, 1 << 30)
found_d = cache.find("pass", {"state": "d", "Tr": 1.0}, None)
check("a fingerprint collision is told apart bit for bit", found_d is not None and replay(cache, found_d)[0][4][0].tobytes() == np.asarray(v4[0]).tobytes())
before = cache.bytes
collect(cache, "pass", {"state": "a", "Tr": 1.0}, ["Tr"], state_a, per, 1 << 30)
check("storing a state again replaces its entry", cache.bytes == before and len(cache.entries) == 4)
check("reads are counted once per entry", cache.reads["pass"][frozenset(["Tr"])] == 4)
collect(cache, "pass", {"state": "e", "Tr": 2.0}, [], [(0, 2, *batch(2, per, 9))], per, one)
check("eviction keeps the byte budget", cache.bytes <= one)
check("eviction drops the oldest entries first", cache.find("pass", {"state": "b", "Tr": 1.0}, None) is None)
check("blob bookkeeping matches the bytes", cache.bytes == sum(b.nbytes for group in cache.blobs.values() for b in group))
entry = []
cache.add(entry, 0, 1, *batch(1, per, 21), per)
held = cache.bytes
cache.release(entry)
check("a released uncommitted entry frees its instances", cache.bytes < held)
cache.clear()
check("clear empties the cache", cache.bytes == 0 and not cache.entries and not cache.blobs and not cache.reads)
cache_mod.mme_exact_state_key = real_key

folder = tempfile.mkdtemp(prefix="ninesync_samples_")
fx_file = os.path.join(folder, "a.fx")
with open(fx_file, "w") as f:
    f.write("float4 x = 1;\n")
runtime = types.SimpleNamespace(program=types.SimpleNamespace(files=[fx_file], globals={}), textures=None,
                                positions=rng.standard_normal((40, 3)), normals=rng.standard_normal((40, 3)), uvs=rng.random((40, 2)))
draw = types.SimpleNamespace(technique=types.SimpleNamespace(name="T"), fx_pass=types.SimpleNamespace(name="P"), subset=0, order=0, loops=[("i", "N", "N")])
src, rows = np.arange(10, 30), np.arange(0, 10)
base = cache_mod.mme_sample_pass_key(runtime, draw, src, rows, 4, 1.0, 2)
check("pass key is stable", base == cache_mod.mme_sample_pass_key(runtime, draw, src.copy(), rows.copy(), 4, 1.0, 2))
variants = {
    "instances": lambda: cache_mod.mme_sample_pass_key(runtime, draw, src, rows, 5, 1.0, 2),
    "density": lambda: cache_mod.mme_sample_pass_key(runtime, draw, src, rows, 4, 0.5, 2),
    "batch": lambda: cache_mod.mme_sample_pass_key(runtime, draw, src, rows, 4, 1.0, 3),
    "rows": lambda: cache_mod.mme_sample_pass_key(runtime, draw, src, rows + 1, 4, 1.0, 2),
    "vertices": lambda: cache_mod.mme_sample_pass_key(runtime, draw, src[::-1], rows, 4, 1.0, 2),
    "pass": lambda: cache_mod.mme_sample_pass_key(runtime, types.SimpleNamespace(**dict(vars(draw), fx_pass=types.SimpleNamespace(name="Q"))), src, rows, 4, 1.0, 2),
}
for name, make in variants.items():
    check("pass key changes with %s" % name, make() != base)
moved = runtime.positions.copy()
runtime.positions[12, 0] = np.nextafter(runtime.positions[12, 0], np.inf)
check("pass key changes with a vertex position", cache_mod.mme_sample_pass_key(runtime, draw, src, rows, 4, 1.0, 2) != base)
runtime.positions = moved
with open(fx_file, "w") as f:
    f.write("float4 x = 2;\n")
os.utime(fx_file, ns=(1, 2))
check("pass key changes with the effect file", cache_mod.mme_sample_pass_key(runtime, draw, src, rows, 4, 1.0, 2) != base)
shutil.rmtree(folder, ignore_errors=True)

lv2 = os.environ.get("NINESYNC_THUNDER_LV2", os.path.join(paths.REPO, "debug-src"))
X, FXF = os.path.join(lv2, "Thunder_lv2.x"), os.path.join(lv2, "Thunder_lv2.fx")
if os.path.isfile(X) and os.path.isfile(FXF):
    cache = cache_mod.mme_sample_cache
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

    def meshes():
        out = {}
        for c in sorted(root.children, key=lambda c: c.name):
            if c.type != "MESH":
                continue
            co = np.zeros(len(c.data.vertices) * 3, dtype=np.float32)
            c.data.vertices.foreach_get("co", co)
            loops = np.zeros(len(c.data.loops), dtype=np.int32)
            c.data.loops.foreach_get("vertex_index", loops)
            out[c.name] = hashlib.sha1(co.tobytes() + loops.tobytes()).hexdigest()
        return out

    def strip_files():
        for f in os.listdir(mb.mme_cache_folder()):
            if f.endswith("_strip.json"):
                os.remove(os.path.join(mb.mme_cache_folder(), f))

    cache.clear()
    mb.mme_build_effect(scene, col, settings, A, root, X, FXF, 1.0, controls)
    root["mme_tr"] = 1.0; root.keyframe_insert('["mme_tr"]', frame=1)
    root["mme_tr"] = 0.0; root.keyframe_insert('["mme_tr"]', frame=120)
    hits = cache.hits
    strip_files()
    mb.mme_rebuild_effect(scene, root, True)
    warm, replayed = meshes(), cache.hits - hits
    cache.clear()
    strip_files()
    mb.mme_rebuild_effect(scene, root, True)
    cold = meshes()
    check("real effect: the cold-cache rebuild sampled again", cache.bytes > 0 and cache.entries)
    print("Thunder lv2 Tr rebuild: %d probe state(s) replayed, %.0f MB kept" % (replayed, cache.bytes / 1048576.0))
    check("real effect: states replayed after a Tr edit", replayed > 0)
    check("real effect: replayed rebuild matches a cold-cache rebuild", warm == cold and len(warm) > 0)
    settings.mme_memory_budget = "LOW"
    cache.clear()
    strip_files()
    mb.mme_rebuild_effect(scene, root, True)
    check("real effect: the Low budget keeps no samples", cache.bytes == 0 and not cache.entries)
    check("real effect: Low budget rebuild matches", meshes() == cold)
else:
    print("Thunder lv2 not found; set NINESYNC_THUNDER_LV2 to run the real effect")

print("sample cache: %d of %d checks passed" % (checks - len(failed), checks))
print("FAILED:", failed or "none")
