from typing import NamedTuple, Tuple


# Generic block from the text format: type name, optional name, flat list of numbers/strings and child blocks.
class XBlock:
    __slots__ = ("kind", "name", "data", "children")

    def __init__(self, kind, name):
        self.kind = kind
        self.name = name
        self.data = []
        self.children = []


# Material as stored in .x: diffuse RGBA, specular power, specular and emissive RGB and the texture file text.
class XMaterial(NamedTuple):
    name: str
    diffuse: Tuple[float, float, float, float]
    power: float
    specular: Tuple[float, float, float]
    emissive: Tuple[float, float, float]
    texture: str


# One mesh in accessory space. Normals (taken from the per-corner lists) and colours are keyed by vertex
# index.
class XMesh:
    __slots__ = (
        "vertices",
        "faces",
        "uvs",
        "face_materials",
        "materials",
        "colors",
        "normals",
    )

    def __init__(self):
        self.vertices = []
        self.faces = []
        self.uvs = []
        self.face_materials = []
        self.materials = []
        self.colors = {}
        self.normals = {}


# Parse result: meshes, warnings for ignored blocks and the format mode (txt, bin, ...).
class XScene:
    __slots__ = ("meshes", "warnings", "mode")

    def __init__(self):
        self.meshes = []
        self.warnings = []
        self.mode = "txt"


# Malformed or unsupported .x data.
class XError(Exception):
    pass
