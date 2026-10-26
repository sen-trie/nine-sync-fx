import bpy

from ..common.constants import X_IMPORTER_URL
from .mesh import x_importer_available


# mmd_tools registers its VMD import operator when enabled; that is checked instead of an add-on name.
def fx_mmd_tools_available():
    return hasattr(bpy.types, "MMD_TOOLS_OT_import_vmd")


# Optional add-ons the user may want, with the reason and where to get them.
def fx_missing_dependencies():
    missing = []
    if not x_importer_available():
        missing.append(
            (
                "DirectX X Importer",
                "DirectX X Importer is not installed (only needed for binary or compressed .x accessories)",
                X_IMPORTER_URL,
            )
        )
    return missing
