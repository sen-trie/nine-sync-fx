import hashlib

from .mesh import (
    X_IDENTITY,
    x_collect_materials,
    x_walk,
)
from .model import (
    XError,
    XScene,
)
from .parser import (
    UNSUPPORTED_BLOCKS,
    x_parse_tree,
)
from .tokenizer import x_decode


# Reads the format word of the 16-byte header (txt, bin, tzip, bzip). Only 'txt' is parsed; the importer falls
# back to the .x add-on for the rest.
def x_header_mode(raw):
    if len(raw) < 16 or raw[:4] != b"xof ":
        raise XError("not a DirectX .x file")
    return raw[8:12].decode("ascii", errors="replace").strip()


_x_cache = {}


# Parsing a large .x is slow and the same file is read for every sync, so the last result is kept keyed by
# content hash.
def parse_x_bytes(raw):
    key = (len(raw), hashlib.md5(raw).hexdigest())
    found = _x_cache.get(key)
    if found is not None:
        return found
    scene = parse_x_uncached(raw)
    _x_cache.clear()
    _x_cache[key] = scene
    return scene


# Walks the frame tree into flat meshes with all frame transforms applied. Skin and animation blocks are
# skipped with a warning instead of failing.
def parse_x_uncached(raw):
    scene = XScene()
    scene.mode = x_header_mode(raw)
    if scene.mode != "txt":
        return scene
    body = x_decode(raw[16:])
    root = x_parse_tree(body)
    named = x_collect_materials(root)
    x_walk(root, list(X_IDENTITY), scene, named)
    for block in root.children:
        if block.kind in UNSUPPORTED_BLOCKS:
            note = "%s ignored (skinned/animated .x is not supported)" % block.kind
            if note not in scene.warnings:
                scene.warnings.append(note)
    return scene
