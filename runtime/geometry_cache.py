import hashlib
import json
import numpy as np
import os
import tempfile

from ..common.constants import MME_CORE_VERSION
from ..common.log import log
from ..xfile.cache import parse_x_bytes
from ..xfile.geometry import x_effect_geometry
from ..xfile.model import XMaterial


# Cache location keyed by path, size, modified time and core version, so a changed .x or a new add-on version
# never reads stale data.
def mme_geometry_cache_file(x_path):
    st = os.stat(x_path)
    text = "%s|%d|%d|%d" % (os.path.abspath(x_path), st.st_size, int(st.st_mtime * 1000), MME_CORE_VERSION)
    folder = os.path.join(tempfile.gettempdir(), "ninesync_cache")
    return folder, os.path.join(folder, hashlib.md5(text.encode("utf-8")).hexdigest() + ".npz")


# Merged effect geometry for an .x file, from the on-disk cache when present. Parsing and merging a large .x
# is slow and every session repeats it.
# Returns ('txt', geometry) or (mode, None) for binary files.
def mme_x_effect_geometry(x_path):
    folder, cache = mme_geometry_cache_file(x_path)
    if os.path.isfile(cache):
        try:
            with np.load(cache, allow_pickle=False) as data:
                sizes = data["face_sizes"]
                flat = data["face_flat"].tolist()
                faces = []
                at = 0
                for size in sizes.tolist():
                    faces.append(tuple(flat[at:at + size]))
                    at += size
                materials = [XMaterial(**m) for m in json.loads(str(data["materials"]))]
                return "txt", (
                    data["positions"].copy(),
                    data["normals"].copy(),
                    data["uvs"].copy(),
                    faces,
                    data["face_subsets"].copy(),
                    materials,
                )
        except (OSError, ValueError, KeyError, TypeError) as error:
            log.debug("geometry cache %s unreadable: %s", cache, error)
    with open(x_path, "rb") as handle:
        xscene = parse_x_bytes(handle.read())
    if xscene.mode != "txt":
        return xscene.mode, None
    geometry = x_effect_geometry(xscene)
    positions, normals, uvs, faces, face_subsets, materials = geometry
    try:
        os.makedirs(folder, exist_ok=True)
        np.savez(
            cache,
            positions=positions,
            normals=normals,
            uvs=uvs,
            face_sizes=np.array([len(f) for f in faces], dtype=np.int64),
            face_flat=np.array([v for f in faces for v in f], dtype=np.int64),
            face_subsets=np.asarray(face_subsets, dtype=np.int64),
            materials=np.array(json.dumps([dict(m._asdict()) for m in materials])),
        )
    except OSError as error:
        log.debug("geometry cache %s not written: %s", cache, error)
    return "txt", geometry
