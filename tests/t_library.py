import os, shutil, subprocess, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import importlib
store = importlib.import_module("ninesync_fx.nodes.library_store")
std = importlib.import_module("ninesync_fx.nodes.standard_groups")
lib = importlib.import_module("ninesync_fx.nodes.core_library")
ops_lib = importlib.import_module("ninesync_fx.ui.operators.library")
core_state = store._mme_core
V = mb.MME_CORE_VERSION
KEY = mb.MME_KEY_PROP
FAILED = []
WORK = tempfile.mkdtemp(prefix="ninesync lib test ")


def check(label, ok):
    print("%-58s %s" % (label, "ok" if ok else "FAIL"))
    if not ok:
        FAILED.append(label)


def clear_groups():
    for group in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(group)


def make_library(path, spec):
    clear_groups()
    made = {}
    for name, key, nested in spec:
        group = bpy.data.node_groups.new(name, "GeometryNodeTree")
        if key:
            group[KEY] = key
        made[name] = (group, nested)
    for group, nested in made.values():
        for other in nested:
            group.nodes.new("GeometryNodeGroup").node_tree = made[other][0]
    bpy.data.libraries.write(path, {g for g, _n in made.values()}, fake_user=True)
    clear_groups()


def stage(name):
    folder = os.path.join(WORK, name, "user folder")
    os.makedirs(folder)
    store.mme_user_folder = lambda: folder
    store.mme_bundled_path = lambda: os.path.join(WORK, name, "bundled.blend")
    return folder, os.path.join(folder, mb.MME_CORE_FILE), os.path.join(WORK, name, "bundled.blend")


def sync():
    store.mme_library_sync(force=True)
    return core_state.get("fallback")


def keys(path):
    info = store.mme_library_read(path)
    return None if info is None else info.keys()


def ka(n, v=V):
    return "fn:GN:%s:h:v%d" % (n, v)


old = V - 1
newer = V + 1

folder, user, bundled = stage("upgrade")
make_library(bundled, [("B", ka("b"), [])])
make_library(user, [("U", ka("u", old), []), ("B0", ka("b", old), [])])
sync()
check("upgrade: older groups not merged", keys(user) == {ka("b")})
check("upgrade: previous library in .bak", keys(user + ".bak") == {ka("u", old), ka("b", old)})

folder, user, bundled = stage("merge")
make_library(bundled, [("leaf", ka("leaf"), []), ("top", ka("top"), ["leaf"]), ("new", ka("new"), [])])
make_library(user, [("leaf", ka("leaf"), []), ("grown", ka("grown"), ["leaf"]), ("deep", ka("deep"), ["grown"])])
sync()
info = store.mme_library_read(user)
check("merge: bundled and user-only keys present", info.keys() == {ka("leaf"), ka("top"), ka("new"), ka("grown"), ka("deep")})
check("merge: no duplicate of a shared key", len(info.names) == 5)
check("merge: no temporary files left", sorted(os.listdir(folder)) == [mb.MME_CORE_FILE, mb.MME_CORE_FILE + ".bak"])
mme_loaded = store.mme_load_groups(user, ["deep"])
deep = bpy.data.node_groups["deep"]
grown = next(n.node_tree for n in deep.nodes)
leaf = next(n.node_tree for n in grown.nodes)
check("merge: nested chain intact", grown.get(KEY) == ka("grown") and leaf.get(KEY) == ka("leaf"))
clear_groups()
stamp = os.path.getmtime(user)
size = os.path.getsize(user)
sync()
check("merge: second run changes nothing", os.path.getmtime(user) == stamp and os.path.getsize(user) == size)

folder, user, bundled = stage("prefer")
make_library(bundled, [("same", ka("same"), []), ("extra", ka("extra"), [])])
clear_groups()
group = bpy.data.node_groups.new("same", "GeometryNodeTree"); group[KEY] = ka("same"); group["from"] = "user"
bpy.data.libraries.write(user, {group}, fake_user=True)
clear_groups()
group = bpy.data.node_groups.new("same", "GeometryNodeTree"); group[KEY] = ka("same"); group["from"] = "bundled"
extra = bpy.data.node_groups.new("extra", "GeometryNodeTree"); extra[KEY] = ka("extra")
bpy.data.libraries.write(bundled, {group, extra}, fake_user=True)
clear_groups()
sync()
store.mme_load_groups(user, ["same"])
check("duplicate key: bundled wins", bpy.data.node_groups["same"].get("from") == "bundled")
clear_groups()

folder, user, bundled = stage("mixed")
make_library(bundled, [("a", ka("a", old), []), ("b", ka("b"), []), ("b.001", ka("b"), [])])
make_library(user, [("a", ka("a", old), []), ("g", ka("g"), [])])
sync()
info = store.mme_library_read(user)
check("mixed versions: file counts as newest", info.version == V)
check("mixed versions: old key dropped, one copy of each key", info.keys() == {ka("b"), ka("g")} and sorted(info.names) == ["b", "g"])

folder, user, bundled = stage("downgrade")
make_library(bundled, [("b", ka("b"), [])])
make_library(user, [("n", ka("n", newer), [])])
before = open(user, "rb").read()
reason = sync()
check("downgrade: user file byte-identical", open(user, "rb").read() == before)
check("downgrade: no .bak written", not os.path.exists(user + ".bak"))
check("downgrade: session reads the bundled copy", reason and lib.mme_core_path() == bundled)
try:
    std.mme_save_core_library()
    check("downgrade: save refused", False)
except OSError:
    check("downgrade: save refused", True)
check("downgrade: still untouched after save attempt", open(user, "rb").read() == before)

folder, user, bundled = stage("missing")
make_library(bundled, [("b", ka("b"), [])])
sync()
check("missing: installed from bundled", keys(user) == {ka("b")})

folder, user, bundled = stage("corrupt")
make_library(bundled, [("b", ka("b"), [])])
with open(user, "wb") as handle:
    handle.write(b"BLENDER-v300 broken")
sync()
check("corrupt, no backup: bundled installed", keys(user) == {ka("b")})
check("corrupt: damaged file kept aside", os.path.isfile(user + ".corrupt"))

folder, user, bundled = stage("recover")
make_library(bundled, [("b", ka("b"), [])])
make_library(user + ".bak", [("b", ka("b"), []), ("g", ka("g"), [])])
with open(user, "wb") as handle:
    handle.write(b"junk")
sync()
check("corrupt with backup: grown groups recovered", keys(user) == {ka("b"), ka("g")})
check("corrupt with backup: good .bak not overwritten", keys(user + ".bak") == {ka("b"), ka("g")})

folder, user, bundled = stage("truncated")
make_library(bundled, [("b", ka("b"), [])])
make_library(user, [("g", ka("g"), []), ("b", ka("b"), [])])
raw = open(user, "rb").read()
with open(user, "wb") as handle:
    handle.write(raw[: len(raw) // 2])
sync()
info = store.mme_library_read(user)
check("interrupted write: result is readable", info is not None and ka("b") in info.keys())

folder, user, bundled = stage("empty")
make_library(bundled, [("b", ka("b"), [])])
make_library(user, [("nokey", None, [])])
sync()
check("empty library: treated as no version", keys(user) == {ka("b")})
check("empty library: not worth a backup", not os.path.exists(user + ".bak"))

folder, user, bundled = stage("failure")
make_library(bundled, [("b", ka("b"), []), ("c", ka("c"), [])])
make_library(user, [("g", ka("g"), [])])
before = open(user, "rb").read()
real = store.mme_merge_files
store.mme_merge_files = lambda *a: (_ for _ in ()).throw(RuntimeError("merge exploded"))
reason = sync()
store.mme_merge_files = real
check("merge failure: user file untouched", open(user, "rb").read() == before)
check("merge failure: bundled used this session", "exploded" in (reason or "") and lib.mme_core_path() == bundled)
check("merge failure: lock released", not os.path.exists(user + ".lock"))
check("merge failure: next session retries and works", sync() is None and ka("g") in keys(user) and ka("b") in keys(user))

folder, user, bundled = stage("replace-fails")
make_library(bundled, [("b", ka("b"), []), ("c", ka("c"), [])])
make_library(user, [("g", ka("g"), []), ("b", ka("b"), [])])
before = open(user, "rb").read()
real_replace = store.os.replace
def refuse(a, b):
    if b == user:
        raise PermissionError("in use")
    return real_replace(a, b)
store.os.replace = refuse
reason = sync()
store.os.replace = real_replace
check("swap failure: user file intact, fallback set", open(user, "rb").read() == before and "in use" in (reason or ""))

folder, user, bundled = stage("validate")
make_library(bundled, [("b", ka("b"), []), ("c", ka("c"), [])])
make_library(user, [("g", ka("g"), []), ("b", ka("b"), [])])
before = open(user, "rb").read()
real = store.mme_merge_files
def drops(first, second, version, out):
    make_library(out, [("b", ka("b"), [])])
    return 1
store.mme_merge_files = drops
reason = sync()
store.mme_merge_files = real
check("bad merge result rejected", open(user, "rb").read() == before and "lost" in (reason or ""))

folder, user, bundled = stage("lock")
make_library(bundled, [("b", ka("b"), [])])
make_library(user, [("b", ka("b"), [])])
open(user + ".lock", "w").close()
group = bpy.data.node_groups.new("session", "GeometryNodeTree"); group[KEY] = ka("session")
sync()
try:
    std.mme_save_core_library()
    check("busy library: save refused", False)
except OSError as error:
    check("busy library: save refused", "another Blender" in str(error))
past = time.time() - mb.MME_LIBRARY_LOCK_STALE - 5
os.utime(user + ".lock", (past, past))
check("stale lock: save goes through", std.mme_save_core_library() == 2 and ka("session") in keys(user))
clear_groups()

folder, user, bundled = stage("save")
make_library(bundled, [("b", ka("b"), [])])
sync()
bundled_before = open(bundled, "rb").read()
inner = bpy.data.node_groups.new("inner", "GeometryNodeTree"); inner[KEY] = ka("inner")
outer = bpy.data.node_groups.new("outer", "GeometryNodeTree"); outer[KEY] = ka("outer")
outer.nodes.new("GeometryNodeGroup").node_tree = inner
aside = bpy.data.node_groups.new("b", "GeometryNodeTree")
count = std.mme_save_core_library()
check("save: keeps library groups the session lacks", keys(user) == {ka("b"), ka("inner"), ka("outer")} and count == 3)
check("save: never writes the bundled copy", open(bundled, "rb").read() == bundled_before)
check("save: session group names restored", bpy.data.node_groups.get("b") == aside and "outer" in bpy.data.node_groups)
check("save: session has no leftovers", len(bpy.data.node_groups) == 3)
check("save: previous library backed up", keys(user + ".bak") == {ka("b")})
stamp = os.path.getmtime(user)
second = std.mme_save_core_library()
check("save: second run keeps every key", second == 3 and keys(user) == {ka("b"), ka("inner"), ka("outer")})
clear_groups()

folder, user, bundled = stage("rebuild")
shutil.copyfile(os.path.join(paths.REPO, "data", mb.MME_CORE_FILE), bundled)
sync()
real_info = store.mme_library_read(user)
check("shipped library: mixed v1/v2 handled", real_info is not None and real_info.version == V)
built = std.mme_build_standard_groups(force=True)
count = std.mme_save_core_library()
reread = store.mme_library_read(user)
check("rebuild standard: saved to the user library", built > 0 and count >= built and reread.version == V)
for name in ["core:camera:GN:v%d" % V, "core:clip:GN:v%d" % V]:
    check("rebuild standard: holds " + name, name in reread.keys())
clear_groups()
mb.mme_group_stats_reset()
mb._mme_groups.clear()
g = mb.mme_geo_clip_group()
check("appended from the user library, nested groups included", mb._mme_core["stats"]["library"] >= 1 and mb._mme_core["stats"]["built"] == 0)
clear_groups()

print("-- open operator")
folder, user, bundled = stage("open with spaces")
make_library(bundled, [("b", ka("b"), [])])
sync()
calls = []
real_popen = subprocess.Popen
ops_lib.subprocess.Popen = lambda argv, **kw: calls.append((argv, kw))
wm = bpy.ops.ninesync_fx.open_core_library
r = wm(mode="WINDOW")
check("second Blender: argv is one path, no shell", r == {"FINISHED"} and calls[0][0][1] == user and " " in user and not calls[0][1].get("shell"))
os.remove(user)
calls.clear()
try:
    r = wm(mode="WINDOW")
except RuntimeError as error:
    r = {"CANCELLED"}
check("missing library: refused, nothing created", r == {"CANCELLED"} and not calls and not os.path.exists(user))
r = wm(mode="WINDOW", use_bundled=True)
copied = calls[0][0][1] if calls else ""
check("bundled fallback: opens a temporary copy", r == {"FINISHED"} and copied != bundled and os.path.isfile(copied))
ops_lib.subprocess.Popen = real_popen
sync()
bpy.data.node_groups.new("dirty", "GeometryNodeTree")
scratch = os.path.join(WORK, "session work.blend")
bpy.ops.wm.save_as_mainfile(filepath=scratch)
bpy.data.objects.new("unsaved", None).use_fake_user = True
bpy.context.scene.frame_current += 1
print("is_dirty", bpy.data.is_dirty)
ops_lib.bpy = types.SimpleNamespace(app=bpy.app, ops=bpy.ops, data=types.SimpleNamespace(is_dirty=True))
try:
    r = wm(mode="REPLACE")
except RuntimeError:
    r = {"CANCELLED"}
check("this Blender with unsaved work: refused", r == {"CANCELLED"} and bpy.data.filepath == scratch)
ops_lib.bpy = bpy
r = wm(mode="REPLACE", discard_changes=True)
check("this Blender after explicit tick: opened", r == {"FINISHED"} and os.path.normcase(bpy.data.filepath) == os.path.normcase(user))

print("FAILED:", FAILED if FAILED else "none")
shutil.rmtree(WORK, ignore_errors=True)
