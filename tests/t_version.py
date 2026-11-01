import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
entry = sys.modules[pkg.__name__ + ".ui.hook_entry"]
root = sys.modules[pkg.__name__]
print("version from ADDON_VERSION:", entry.addon_version() == tuple(root.ADDON_VERSION))
del root.bl_info
print("version without bl_info:", entry.addon_version() == tuple(root.ADDON_VERSION) != (0, 0, 0))
print("FAILED:", "none" if entry.addon_version() == tuple(root.ADDON_VERSION) else "version")
