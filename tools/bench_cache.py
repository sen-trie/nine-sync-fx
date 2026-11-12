import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TESTS = os.path.join(REPO, "tests")
CHILD_ENV = "NINESYNC_BENCH_CHILD"


def peak_memory_mb():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return counters.PeakWorkingSetSize / (1 << 20)
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1 << 20) if sys.platform == "darwin" else peak / 1024


def folder_bytes(folder):
    total = 0
    for base, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


def child_main(spec):
    sys.path.insert(0, TESTS)
    from harness_fx import bpy, mb

    user = spec["user"]
    mb.mme_user_folder = lambda: user
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    camera = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(camera)
    scene.camera = camera
    mb.CACHE_STATS.clear()
    started = time.time()
    root = mb.import_standalone_accessory(bpy.context, spec["x"], 0.08, True, spec["fx"])
    build_seconds = time.time() - started
    result = {"build_s": round(build_seconds, 3), "passes": [], "error": None}
    if root is None:
        result["error"] = "import returned nothing"
    else:
        passes = sorted((c for c in root.children if c.get(mb.MME_PASS_KEY_PROP)), key=lambda c: c.name)
        for child in passes:
            group = child.modifiers[mb.MME_GN_MODIFIER].node_group if mb.MME_GN_MODIFIER in child.modifiers else None
            result["passes"].append(
                {"name": child.name, "backend": child.get("mme_backend"), "tr_switch": bool(group and any(n.label == "Hide at Tr = 0" for n in group.nodes))}
            )
        result["cache"] = {kind: {"hits": hit, "misses": miss} for kind, (hit, miss) in mb.CACHE_STATS.items()}
        if spec["bake"] and passes:
            settings = scene.ninesync_fx
            root["mme_tr"] = 1.0
            root.keyframe_insert('["mme_tr"]', frame=0)
            root["mme_tr"] = 0.0
            root.keyframe_insert('["mme_tr"]', frame=spec["frames"] // 2)
            settings.mme_bake_target = "DISK"
            settings.mme_bake_dir = os.path.join(spec["work"], "own_bake").replace("\\", "/") + "/"
            settings.mme_bake_shared = True
            scene.frame_start, scene.frame_end = 0, spec["frames"]
            outcomes = {}
            started = time.time()
            for child in passes:
                try:
                    outcome = mb.mme_bake_pass(bpy.context, child, 0, spec["frames"], settings, False)
                except Exception as error:
                    outcome = "error: %s" % error
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
            result["bake_s"] = round(time.time() - started, 3)
            result["bake"] = outcomes
    result["peak_mb"] = round(peak_memory_mb(), 1)
    result["cache_bytes"] = folder_bytes(os.path.join(user, "cache")) if os.path.isdir(os.path.join(user, "cache")) else 0
    store = os.path.join(user, "cache", "bakes")
    result["store_bytes"] = folder_bytes(store) if os.path.isdir(store) else 0
    with open(spec["out"], "w", encoding="utf-8") as handle:
        json.dump(result, handle)


def run_child(blender, spec, timeout):
    work = tempfile.mkdtemp(prefix="nsbench_run_")
    spec = dict(spec, work=work, out=os.path.join(work, "result.json"))
    spec_path = os.path.join(work, "spec.json")
    with open(spec_path, "w", encoding="utf-8") as handle:
        json.dump(spec, handle)
    env = dict(os.environ, BLENDER_USER_RESOURCES=os.path.join(work, "profile"), **{CHILD_ENV: spec_path})
    started = time.time()
    try:
        subprocess.run([blender, "-b", "--factory-startup", "--python", os.path.abspath(__file__)], env=env, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"error": "timed out after %ds" % timeout}
    if not os.path.isfile(spec["out"]):
        return {"error": "no result written"}
    with open(spec["out"], "r", encoding="utf-8") as handle:
        result = json.load(handle)
    result["wall_s"] = round(time.time() - started, 1)
    shutil.rmtree(work, ignore_errors=True)
    return result


def copy_effect(x, fx, target):
    folder = os.path.dirname(fx)
    copies = {folder: os.path.join(target, "effect")}
    texture = os.path.join(os.path.dirname(folder), "Texture")
    if os.path.isdir(texture):
        copies[texture] = os.path.join(target, "Texture")
    for source, destination in copies.items():
        shutil.copytree(source, destination)
        for base, _dirs, files in os.walk(destination):
            for name in files:
                os.utime(os.path.join(base, name), (1600000000, 1600000000))
    return os.path.join(copies[folder], os.path.basename(x)), os.path.join(copies[folder], os.path.basename(fx))


def hit_rate(cache):
    hits = sum(entry["hits"] for entry in cache.values())
    total = hits + sum(entry["misses"] for entry in cache.values())
    return "%d/%d" % (hits, total) if total else "-"


def table(rows):
    header = ["effect", "run", "build s", "bake s", "bake", "peak MB", "cache MB", "bakes MB", "cache hits", "switch"]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def parent_main(args):
    effects = []
    for item in args.effect:
        x, fx = item.split("=", 1) if "=" in item else (None, item)
        x = x or os.path.splitext(fx)[0] + ".x"
        effects.append((os.path.splitext(os.path.basename(fx))[0], x, fx))
    rows = []
    raw = {}
    for label, x, fx in effects:
        user = tempfile.mkdtemp(prefix="nsbench_user_")
        copied = tempfile.mkdtemp(prefix="nsbench_copy_")
        cx, cfx = copy_effect(x, fx, copied)
        runs = [("cold", x, fx), ("warm", x, fx), ("copied files", cx, cfx)]
        raw[label] = {}
        for name, rx, rfx in runs:
            spec = {"x": rx, "fx": rfx, "user": user, "bake": not args.no_bake, "frames": args.frames}
            result = run_child(args.blender, spec, args.timeout)
            raw[label][name] = result
            if result.get("error"):
                rows.append([label, name, "-", "-", result["error"], "-", "-", "-", "-", "-"])
                continue
            switches = "%d/%d" % (sum(1 for p in result["passes"] if p["tr_switch"]), len(result["passes"]))
            rows.append(
                [
                    label,
                    name,
                    result["build_s"],
                    result.get("bake_s", "-"),
                    ", ".join("%s %d" % item for item in sorted(result.get("bake", {}).items())) or "-",
                    result["peak_mb"],
                    round(result["cache_bytes"] / (1 << 20), 2),
                    round(result["store_bytes"] / (1 << 20), 2),
                    hit_rate(result.get("cache", {})),
                    switches,
                ]
            )
            print(" ".join(str(cell) for cell in rows[-1]), flush=True)
        shutil.rmtree(user, ignore_errors=True)
        shutil.rmtree(copied, ignore_errors=True)
    text = table(rows)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        with open(os.path.splitext(args.out)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump(raw, handle, indent=1)


if __name__ == "__main__":
    if os.environ.get(CHILD_ENV):
        with open(os.environ[CHILD_ENV], "r", encoding="utf-8") as stream:
            child_main(json.load(stream))
    else:
        parser = argparse.ArgumentParser(description="Cold versus cached effect import and bake in separate Blender processes.")
        parser.add_argument("--blender", required=True)
        parser.add_argument("--effect", action="append", required=True, help="path to an .fx (its .x beside it), or X=FX")
        parser.add_argument("--frames", type=int, default=30)
        parser.add_argument("--no-bake", action="store_true")
        parser.add_argument("--timeout", type=int, default=1800)
        parser.add_argument("--out")
        parse = parser.parse_args()
        parent_main(parse)
