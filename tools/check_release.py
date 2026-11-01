import hashlib
import os
import re
import subprocess
import sys
import tomllib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED_EXCLUDES = ("/tests/", "/tools/", "/docs/", "/.github/", "/CLAUDE.md")
PATH_LEAK = re.compile(r"(?i)\b[a-z]:[\/]+users[\/]")


def read(name):
    with open(os.path.join(REPO, name), encoding="utf8") as handle:
        return handle.read()


# Extra terms to keep out of tracked files (a user name, a private folder name) come from the environment, so
# they are never written into the repository by this script.
def private_terms():
    return [t.strip() for t in os.environ.get("NINESYNC_PRIVATE_TERMS", "").split(",") if t.strip()]


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf8").split("\0") if p]


def main():
    problems = []
    manifest = tomllib.loads(read("blender_manifest.toml"))
    init = read("__init__.py")
    match = re.search(r"^ADDON_VERSION\s*=\s*\((\d+),\s*(\d+),\s*(\d+)\)", init, re.M)
    version = ".".join(match.groups()) if match else None
    if version != manifest.get("version"):
        problems.append("ADDON_VERSION %s differs from manifest version %s" % (version, manifest.get("version")))
    if not re.search(r'"author":\s*"[^"]+"', init):
        problems.append("bl_info author is empty")
    for key in ("doc_url", "tracker_url"):
        if key not in init:
            problems.append("bl_info lacks %s" % key)
    if manifest.get("license") != ["SPDX:GPL-3.0-or-later"]:
        problems.append("manifest license is not SPDX:GPL-3.0-or-later")
    if not manifest.get("website"):
        problems.append("manifest website is empty")
    text = read("LICENSE") if os.path.isfile(os.path.join(REPO, "LICENSE")) else ""
    if "GNU GENERAL PUBLIC LICENSE" not in text or "Version 3, 29 June 2007" not in text or "END OF TERMS AND CONDITIONS" not in text:
        problems.append("LICENSE is missing or is not the full GPL-3.0 text")
    excludes = manifest.get("build", {}).get("paths_exclude_pattern", [])
    for item in REQUIRED_EXCLUDES:
        if item not in excludes:
            problems.append("manifest build excludes lack %s" % item)
    if not re.search(r"^## %s\s*$" % re.escape(manifest.get("version", "")), read("CHANGELOG.md"), re.M):
        problems.append("CHANGELOG.md has no entry for %s" % manifest.get("version"))
    if not os.path.isfile(os.path.join(REPO, "data", "mme_core.blend")):
        problems.append("data/mme_core.blend is missing")
    core = os.environ.get("NINESYNC_CORE")
    if core and os.path.isfile(os.path.join(core, "hooks.py")):
        digests = [hashlib.sha256(open(os.path.join(p, "hooks.py"), "rb").read()).hexdigest() for p in (REPO, core)]
        if digests[0] != digests[1]:
            problems.append("hooks.py differs from the Core copy")
    elif core:
        problems.append("NINESYNC_CORE has no hooks.py")
    terms = private_terms()
    for name in tracked_files():
        path = os.path.join(REPO, name)
        try:
            with open(path, encoding="utf8") as handle:
                body = handle.read()
        except (UnicodeDecodeError, OSError):
            continue
        if PATH_LEAK.search(body) or any(t.lower() in body.lower() for t in terms):
            problems.append("private path or term in tracked file %s" % name)
    for problem in problems:
        print("FAIL:", problem)
    print("release checks: %s" % ("FAILED" if problems else "ok (version %s)" % version))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
