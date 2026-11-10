import hashlib
import json
import numpy as np
import os

from ..common.constants import (
    MME_PRECULL_GROUPS,
    MME_PRECULL_VERSION,
)
from ..common.log import log
from ..fx.loader import fx_files_stamp
from .cache_store import (
    mme_cache_path,
    mme_cache_touch,
    mme_cache_trim,
)


# Cache file for one pass, keyed by the effect and .x file stamps, pass identity, used vertices, gates and
# instance count, so any change selects a new file.
def mme_precull_file(runtime, draw, used, gates, extra):
    stamp = fx_files_stamp(runtime.program.files) or ()
    try:
        st = os.stat(runtime.x_path)
        xstamp = (os.path.abspath(runtime.x_path), st.st_size, st.st_mtime_ns)
    except OSError:
        return None
    text = repr((
        MME_PRECULL_VERSION,
        stamp,
        xstamp,
        draw.technique.name,
        draw.fx_pass.name,
        draw.subset,
        draw.order,
        len(used),
        int(used[:64].sum()) if len(used) else 0,
        sorted(gates),
        extra,
    ))
    return mme_cache_path("precull_" + hashlib.md5(text.encode("utf-8")).hexdigest() + ".npz")


# Loads cached visibility groups; a file that is damaged or has a different vertex count counts as empty.
def mme_precull_load(path, per):
    empty = {"groups": {}, "reads": None}
    if path is None or not os.path.isfile(path):
        return empty
    try:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
            groups = {}
            for index, item in enumerate(meta.get("groups", [])):
                keep = data["keep_%d" % index].astype(bool)
                opened = data["opened_%d" % index].astype(bool)
                if keep.ndim != 2 or keep.shape[1] != per or opened.shape != keep.shape:
                    return empty
                groups[item["key"]] = {"keep": keep, "opened": opened, "conditions": item["conditions"]}
    except (OSError, ValueError, KeyError):
        return empty
    mme_cache_touch(path)
    return {"groups": groups, "reads": meta.get("reads")}


# Writes atomically (temp file then replace) and keeps only the most recent groups, so a crash cannot leave a
# half-written cache and the file stays bounded.
def mme_precull_save(path, cache):
    if path is None:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        items = list(cache["groups"].items())[-MME_PRECULL_GROUPS:]
        arrays = {}
        meta = {"groups": [], "reads": sorted(cache["reads"]) if cache.get("reads") is not None else None}
        for index, (key, group) in enumerate(items):
            arrays["keep_%d" % index] = group["keep"]
            arrays["opened_%d" % index] = group["opened"]
            meta["groups"].append({"key": key, "conditions": group["conditions"]})
        temp = path + ".tmp.npz"
        np.savez_compressed(temp, meta=np.array(json.dumps(meta)), **arrays)
        os.replace(temp, path)
        mme_cache_trim()
    except OSError as error:
        log.debug("pre-cull cache not written: %s", error)
