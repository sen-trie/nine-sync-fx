import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

failed = []


def check(label, got, want):
    ok = got == want
    print("%-44s %s" % (label, "ok" if ok else "FAILED got %r want %r" % (got, want)))
    if not ok:
        failed.append(label)


def tree(*names):
    root = tempfile.mkdtemp(prefix="ninesync_paths_")
    for name in names:
        os.makedirs(os.path.join(root, name))
        open(os.path.join(root, name, "__init__.py"), "w").close()
    return root


for fx_name in ("nine-sync-fx", "ninesync-fx"):
    for core_name in ("nine-sync-core", "ninesync-core", "ninesync"):
        root = tree(fx_name, core_name)
        repo = os.path.join(root, fx_name)
        label = "%s + %s" % (fx_name, core_name)
        check("fx self " + label, paths.locate("ninesync_fx", repo, {}), repo)
        check("core sibling " + label, paths.locate("ninesync", repo, {}), os.path.join(root, core_name))

root = tree("nine-sync-fx")
repo = os.path.join(root, "nine-sync-fx")
check("core absent", paths.locate("ninesync", repo, {}), None)

root = tree("nine-sync-fx", "nine-sync-core")
repo = os.path.join(root, "nine-sync-fx")
other = tempfile.mkdtemp(prefix="ninesync_override_")
check("NINESYNC_FX override", paths.locate("ninesync_fx", repo, {"NINESYNC_FX": other}), os.path.abspath(other))
check("NINESYNC_CORE override", paths.locate("ninesync", repo, {"NINESYNC_CORE": other}), os.path.abspath(other))

renamed = tempfile.mkdtemp(prefix="checkout_")
check("unknown repo folder, no sibling", paths.locate("ninesync_fx", renamed, {}), None)

print("FAILED:", ", ".join(failed) if failed else "none")
sys.exit(1 if failed else 0)
