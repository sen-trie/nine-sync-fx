import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TESTS = os.path.join(REPO, "tests")

THUNDER = "Thunder/Thunder_lv1.fx"

BLENDER = [
    ("compare", (), (THUNDER,)),
    ("bake", (), (THUNDER,)),
    ("misc", (), (THUNDER,)),
    ("ui", (), (THUNDER,)),
    ("clean", (), (THUNDER,)),
    ("features", (), (THUNDER,)),
    ("reuse", (), (THUNDER,)),
    ("controller", (), (THUNDER,)),
    ("user_bug", (), (THUNDER,)),
    ("keyed", (), (THUNDER,)),
    ("standalone", (), (THUNDER,)),
    ("hook", (), ()),
    ("scenes", (), (THUNDER,)),
    ("synth", (), ()),
    ("precull", (), (THUNDER,)),
    ("library", (), ()),
    ("manual_controls", (), (THUNDER,)),
    ("thunder_rings", (), ()),
    ("plain_fixture", (), ()),
    ("x_importer", (), ()),
    ("mixed", (), ()),
    ("version", (), ()),
    ("legacy", (), ()),
    ("emm", (), ()),
    ("accessory_keys", (), ()),
    ("cache", (), ()),
    ("labels", (), (THUNDER,)),
    ("panels", ("--", "fx"), ()),
    ("cycle", (), ("@core",)),
    ("core_fx", (), ("@core", "meiling.pmm", THUNDER)),
]

BAD_LINE = re.compile(r"Traceback \(most recent call last\)|FAILED: (?!none)|\bFAILED\b(?!: none)")


# Log text goes to CI output and reports, so local folders and the user name are replaced before it is shown.
def scrub(text, extra):
    for value, label in extra:
        if value:
            text = text.replace(value, label).replace(value.replace("\\", "/"), label)
    home = os.path.expanduser("~")
    text = text.replace(home, "~").replace(home.replace("\\", "/"), "~")
    return re.sub(r"(?i)[a-z]:[\/]users[\/][^\/\s]+", "~", text)


def missing(requirements, fixtures, core):
    out = []
    for item in requirements:
        if item == "@core":
            if not core:
                out.append("NineSync Core not found (set NINESYNC_CORE)")
        elif not os.path.exists(os.path.join(fixtures, item)):
            out.append("fixture %s not supplied" % item)
    return out


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", default=os.environ.get("BLENDER"))
    parser.add_argument("--fixtures", default=os.environ.get("NINESYNC_FIXTURES", os.path.join(TESTS, "fixtures")))
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args(argv)
    if not args.blender or not os.path.isfile(args.blender):
        print("set --blender or BLENDER to a Blender 5.3+ executable")
        return 2
    sys.path.insert(0, TESTS)
    import paths

    core = paths.locate("ninesync")
    work = tempfile.mkdtemp(prefix="ninesync_run_")
    env = dict(os.environ)
    env["BLENDER_USER_RESOURCES"] = os.path.join(work, "resources")
    env["NINESYNC_FIXTURES"] = args.fixtures
    env.setdefault("NINESYNC_FX", REPO)
    if core:
        env.setdefault("NINESYNC_CORE", core)
    scrub_map = [(work, "<work>"), (args.fixtures, "<fixtures>"), (REPO, "<fx>"), (core, "<core>"), (args.blender, "<blender>")]
    results = []
    version = subprocess.run([args.blender, "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    print("Blender:", version)
    pure = [("paths", None, ())]
    for name, extra, requirements in pure + BLENDER:
        if args.only and name not in args.only:
            continue
        reasons = missing(requirements, args.fixtures, core)
        if reasons:
            results.append((name, "SKIP", "; ".join(reasons)))
            continue
        script = os.path.join(TESTS, "t_%s.py" % name)
        if extra is None:
            command = [sys.executable, "-I", script]
        else:
            command = [args.blender, "-b", "--factory-startup", "--python", script] + list(extra)
        started = time.time()
        try:
            done = subprocess.run(command, capture_output=True, text=True, errors="replace", env=env, timeout=args.timeout, cwd=TESTS)
            output = done.stdout + done.stderr
            bad = [line for line in output.splitlines() if BAD_LINE.search(line)]
            status = "PASS" if done.returncode == 0 and not bad else "FAIL"
        except subprocess.TimeoutExpired as error:
            output = (error.stdout or b"").decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
            status, bad = "FAIL", ["timeout after %ds" % args.timeout]
        detail = "%.0fs" % (time.time() - started)
        if status == "FAIL":
            tail = scrub("\n".join((bad + output.splitlines()[-12:])[:20]), scrub_map)
            detail += "\n" + tail
        results.append((name, status, detail))
    shutil.rmtree(work, ignore_errors=True)
    print()
    for name, status, detail in results:
        print("%-16s %-5s %s" % (name, status, detail))
    skipped = [r for r in results if r[1] == "SKIP"]
    failed = [r for r in results if r[1] == "FAIL"]
    print("\npass %d  fail %d  skip %d" % (len([r for r in results if r[1] == "PASS"]), len(failed), len(skipped)))
    if skipped:
        print("SIGN-OFF INCOMPLETE: skipped tests are pending, not passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
