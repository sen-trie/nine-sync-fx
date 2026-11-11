import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
FX2 = (FIX + "/Synth/synth.fx"); X2 = (FIX + "/Synth/synth.x")
scene, col, root, cam = setup_scene()
bpy.data.objects.remove(root)
settings = scene.ninesync_fx
settings.mme_log_detail = True
assert settings.mme_auto_rebuild is False, "auto rebuild must default to off"
class A2:
    name = "synth"; path = X2; index = 0; keys = [Key(0)]
controls = {"Tr": {"kind": "self", "item": "Tr"}, "Si": {"kind": "self", "item": "Si"}}
scene.frame_start, scene.frame_end = 1, 6

def passes(r):
    return {c["mme_pass_key"]: c for c in r.children if c.get("mme_pass_key")}

def report(r):
    return mb._mme_runtimes[r.name].build_report

def build(backend):
    r = bpy.data.objects.new("root_" + backend, None); r["mmd_bridge_accessory"] = "inc_" + backend; col.objects.link(r)
    settings.mme_backend = backend
    kids, *_ = mb.mme_build_effect(scene, col, settings, A2, r, X2, FX2, 1.0, controls)
    for c in kids: c.scale = (mb.ACCESSORY_UNIT,) * 3
    return r

for backend in ("GEONODES", "PYTHON"):
    r = build(backend)
    first = passes(r)
    assert len(first) >= 2, (backend, "expected two passes", len(first))
    assert all(mb.mme_read_manifest(p) for p in first.values()), "every pass carries a manifest"
    pointers = {k: p.as_pointer() for k, p in first.items()}
    modifiers = {k: p.modifiers["MME"].node_group.name for k, p in first.items()}

    mb.mme_rebuild_effect(scene, r)
    rep = report(r)
    assert not rep["built"] and len(rep["reused"]) == len(first), (backend, "no-op rebuild rebuilt passes", len(rep["built"]))
    now = passes(r)
    assert {k: p.as_pointer() for k, p in now.items()} == pointers, "pass objects were replaced by a no-op rebuild"
    assert {k: p.modifiers["MME"].node_group.name for k, p in now.items()} == modifiers, "node groups changed on a no-op rebuild"
    print(backend, "no-op rebuild reused", len(rep["reused"]), "passes, objects and node groups identical")

    victim_key = sorted(first)[0]
    first[victim_key].modifiers.remove(first[victim_key].modifiers["MME"])
    mb.mme_rebuild_effect(scene, r)
    rep = report(r)
    assert len(rep["built"]) == 1 and len(rep["reused"]) == len(first) - 1, (backend, "only the damaged pass should be rebuilt", len(rep["built"]))
    now = passes(r)
    for key, ptr in pointers.items():
        if key != victim_key:
            assert now[key].as_pointer() == ptr, "sibling object was replaced"
    print(backend, "damaged pass rebuilt alone, siblings kept their objects")

    settings.mme_density = 0.5
    mb.mme_rebuild_effect(scene, r)
    rep = report(r)
    assert len(rep["built"]) == len(first) and not rep["reused"], (backend, "density is shared: every pass must rebuild")
    settings.mme_density = 1.0
    mb.mme_rebuild_effect(scene, r)
    mb.mme_rebuild_effect(scene, r, force=True)
    rep = report(r)
    assert len(rep["built"]) == len(first) and not rep["reused"], "force rebuilds every pass"
    print(backend, "density change and force rebuild regenerated all passes")

    meta = mb.mme_param_meta(r)
    reads = {}
    for p in passes(r).values():
        reads[p["mme_pass_key"]] = set(json.loads(p["mme_manifest"])["build"]["inputs"])
    prop = meta["Spread"]["prop"]
    r[prop] = float(r[prop]) + 1.0
    mb.mme_rebuild_effect(scene, r)
    rep = report(r)
    dependent = {k for k, names in reads.items() if "param.Spread" in names}
    rebuilt = {o["mme_pass_key"] for o, _own in rep["built"]}
    assert rebuilt == dependent, (backend, "rebuilt", len(rebuilt), "expected dependents", len(dependent))
    print(backend, "Spread edit rebuilt exactly its", len(dependent), "dependent pass(es) of", len(reads))

for backend in ("GEONODES", "PYTHON"):
    r = bpy.data.objects["root_" + backend]
    ps = list(passes(r).values())
    calls = {"baked": 0, "skipped": 0, "failed": 0}
    for p in ps:
        calls[mb.mme_bake_pass(bpy.context, p, scene.frame_start, scene.frame_end, settings)] += 1
    print(backend, "first bake", calls)
    assert calls["failed"] == 0, calls
    again = {"baked": 0, "skipped": 0, "failed": 0}
    for p in ps:
        again[mb.mme_bake_pass(bpy.context, p, scene.frame_start, scene.frame_end, settings)] += 1
    assert again == {"baked": 0, "skipped": len(ps), "failed": 0}, ("no-op bake must skip every pass", again)
    prop = mb.mme_param_meta(r)["Spread"]["prop"]
    r[prop] = float(r[prop]) + 0.5
    stale = mb.mme_mark_stale_bakes(r, scene, settings)
    live = [p for p in ps if "param.Spread" in json.loads(p["mme_manifest"])["build"]["inputs"] or p.get("mme_backend") == "PYTHON"]
    assert {p.name for p in stale} == {p.name for p in live}, (backend, "stale set", [p.name for p in stale], "expected", [p.name for p in live])
    redo = {"baked": 0, "skipped": 0, "failed": 0}
    for p in ps:
        redo[mb.mme_bake_pass(bpy.context, p, scene.frame_start, scene.frame_end, settings)] += 1
    assert redo["baked"] == len(live) and redo["skipped"] == len(ps) - len(live), (backend, redo)
    print(backend, "after a Spread edit rebaked", redo["baked"], "and reused", redo["skipped"])
    scene.frame_end = 8
    changed = {"baked": 0, "skipped": 0, "failed": 0}
    for p in ps:
        changed[mb.mme_bake_pass(bpy.context, p, scene.frame_start, scene.frame_end, settings)] += 1
    assert changed["skipped"] == 0, ("frame range change must rebake all", changed)
    scene.frame_end = 6
    print(backend, "frame range change rebaked all passes")

print("incremental build and bake checks passed")
