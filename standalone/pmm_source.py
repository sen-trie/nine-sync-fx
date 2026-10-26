import bpy

from ..pmm import parse_pmm_file


# Reads the PMM through the vendored parser.
def read_standalone_project(settings):
    path = bpy.path.abspath(settings.filepath)
    return path, parse_pmm_file(path)
