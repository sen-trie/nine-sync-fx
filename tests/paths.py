import os
import sys
import tempfile


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

LAYOUT = {
    "ninesync": ("NINESYNC_CORE", ("nine-sync-core", "ninesync-core", "ninesync")),
    "ninesync_fx": ("NINESYNC_FX", ("nine-sync-fx", "ninesync-fx", "ninesync_fx")),
}


def locate(module, repo=REPO, environ=os.environ):
    env, folders = LAYOUT[module]
    explicit = environ.get(env)
    if explicit:
        return os.path.abspath(explicit)
    if os.path.basename(repo) in folders or os.path.basename(repo) == module:
        return repo
    for folder in folders:
        sibling = os.path.join(os.path.dirname(repo), folder)
        if os.path.isfile(os.path.join(sibling, "__init__.py")):
            return sibling
    return None


def prepare(*modules):
    root = tempfile.mkdtemp(prefix="ninesync_pkgs_")
    found = []
    for module in modules:
        source = locate(module)
        if source is None:
            raise RuntimeError("set %s to the %s folder" % (LAYOUT[module][0], module))
        os.symlink(source, os.path.join(root, module))
        found.append(module)
    sys.path.insert(0, root)
    return root


def prepare_available(*modules):
    root = tempfile.mkdtemp(prefix="ninesync_pkgs_")
    for module in modules:
        source = locate(module)
        if source is not None:
            os.symlink(source, os.path.join(root, module))
    sys.path.insert(0, root)
    return root


def fixtures():
    return os.environ.get("NINESYNC_FIXTURES", os.path.join(HERE, "fixtures"))
