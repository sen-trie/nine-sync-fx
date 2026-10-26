import os
import sys
import tempfile


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

LAYOUT = {
    "ninesync": ("NINESYNC_CORE", "ninesync"),
    "ninesync_fx": ("NINESYNC_FX", "ninesync-fx"),
}


def locate(module):
    env, folder = LAYOUT[module]
    explicit = os.environ.get(env)
    if explicit:
        return os.path.abspath(explicit)
    own = os.path.basename(REPO)
    if own in (folder, module):
        return REPO
    sibling = os.path.join(os.path.dirname(REPO), folder)
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
