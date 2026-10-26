import hashlib
import os
import sys


SHARED = ("hooks.py",)


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def main(argv):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    core = argv[1] if len(argv) > 1 else os.path.join(os.path.dirname(here), "ninesync")
    failed = False
    for name in SHARED:
        a = os.path.join(here, name)
        b = os.path.join(core, name)
        if not os.path.isfile(a) or not os.path.isfile(b):
            print("missing: %s or %s" % (a, b))
            failed = True
        elif digest(a) != digest(b):
            print("DRIFT: %s differs between %s and %s" % (name, here, core))
            failed = True
        else:
            print("ok: %s" % name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
