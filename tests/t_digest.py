import importlib.util, os, subprocess, sys, tempfile
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ns_digest", os.path.join(here, "..", "common", "digest.py"))
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)

assert d.digest({"b": 1, "a": [1.0, 2.5]}) == d.digest({"a": [1.0, 2.5], "b": 1}), "key order must not matter"
assert d.digest([0.1 + 0.2]) == d.digest([0.3]), "float noise below 1e-6 must not matter"
assert d.digest([0.3]) != d.digest([0.300002]), "real changes must matter"
assert d.digest({"x": {1, 2}}) == d.digest({"x": {2, 1}})
code = "import importlib.util,sys;s=importlib.util.spec_from_file_location('m',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(m.digest({'a':[1,2,{'b':0.5}]}))"
runs = {subprocess.run([sys.executable, "-c", code, os.path.join(here, "..", "common", "digest.py")], capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": str(seed)}).stdout.strip() for seed in (1, 2, 3)}
assert len(runs) == 1 and next(iter(runs)), "digest must not depend on the process hash seed"

old = d.brief_inputs({"density": 1.0, "param.Spread": [2.5], "big": list(range(500)), "gone": 1})
new = {"density": 0.5, "param.Spread": [2.5], "big": list(range(500)), "added": 2}
changes = d.diff_inputs(old, new)
names = [c[0] for c in changes]
assert names == ["added", "density", "gone"], names
assert isinstance(old["big"], str) and old["big"].startswith("#"), "large values are stored as a digest"
assert not d.diff_inputs(old, {"density": 1.0, "param.Spread": [2.5], "big": list(range(500)), "gone": 1})
assert "density 1.0 -> 0.5" in d.describe_diff(changes)

with tempfile.TemporaryDirectory() as folder:
    path = os.path.join(folder, "a.fx")
    with open(path, "wb") as f: f.write(b"one")
    first = d.file_digest(path)
    with open(path, "wb") as f: f.write(b"two")
    assert d.file_digest(path) != first, "same size but new content must change the digest"
    os.utime(path, ns=(1, 1))
    with open(path, "wb") as f: f.write(b"two")
    assert d.file_digest(os.path.join(folder, "missing")) == "missing"
print("digest checks passed")
