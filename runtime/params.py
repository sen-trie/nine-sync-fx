import json

from ..common.constants import (
    MME_PARAMS_PROP,
    MME_PARAM_BUILT_PROP,
    MME_PARAM_PREFIX,
)
from ..common.controls import mme_param_meta
from ..nodes.names import mme_prop_name
from .variants import mme_param_key_fingerprint


# Current parameter values from the evaluated root, with the effect's own defaults for any that are missing.
def mme_param_values(root, program, depsgraph=None):
    params = getattr(program, "params", {})
    source = root.evaluated_get(depsgraph) if (root is not None and depsgraph is not None) else root
    out = {}
    for name, info in params.items():
        prop = mme_prop_name(MME_PARAM_PREFIX, name)
        value = None
        if source is not None:
            value = source.get(prop, root.get(prop) if root is not None else None)
        if value is None:
            out[name] = list(info["default"])
        elif hasattr(value, "__len__"):
            out[name] = [float(v) for v in value]
        else:
            out[name] = [float(value)]
    return out


# Structural parameter values recorded when the effect was last built.
def mme_built_values(root):
    try:
        return json.loads(root.get(MME_PARAM_BUILT_PROP, "{}"))
    except (TypeError, ValueError):
        return {}


# Current values of the structural parameters, or the key fingerprint for keyed ones; compared with the built
# values to know if a rebuild is due.
def mme_param_signature(root):
    try:
        names = json.loads(root.get("mme_structural", "[]"))
    except (TypeError, ValueError):
        names = []
    meta = mme_param_meta(root)
    out = {}
    for name in names:
        info = meta.get(name)
        if info is None:
            continue
        keyed = mme_param_key_fingerprint(root, info["prop"])
        if keyed is not None:
            out[name] = keyed
            continue
        value = root.get(info["prop"])
        if value is None:
            out[name] = list(info["default"])
        elif hasattr(value, "__len__"):
            out[name] = [float(v) for v in value]
        else:
            out[name] = [float(value)]
    return out


# Creates or retypes the parameter properties on the root (keeping values that still fit), applies their UI
# settings, removes properties the effect no longer has and stores the metadata.
def mme_sync_params(root, program):
    params = getattr(program, "params", {})
    meta = {}
    for name, info in params.items():
        prop = mme_prop_name(MME_PARAM_PREFIX, name)
        info = dict(info)
        info["prop"] = prop
        meta[name] = info
        size = info["size"]
        kind = info["type"]
        default = info["default"]
        if kind == "i":
            cast = int
        elif kind == "b":
            cast = bool
        else:
            cast = float
        typed = [cast(v) for v in default]
        current = root.get(prop)
        fits = current is not None and (
            (size == 1 and not hasattr(current, "__len__") and type(current) is type(typed[0]))
            or (size > 1 and hasattr(current, "__len__") and len(current) == size)
        )
        if not fits:
            root[prop] = typed[0] if size == 1 else typed
        ui = root.id_properties_ui(prop)
        options = {"description": info["help"] or "MME effect parameter %s" % name}
        if kind != "b":
            options["default"] = typed[0] if size == 1 else typed
            if "min" in info:
                options["soft_min"] = cast(info["min"])
            if "max" in info:
                options["soft_max"] = cast(info["max"])
            if info.get("color"):
                options["subtype"] = "COLOR_GAMMA"
                options.setdefault("soft_min", 0.0)
        ui.update(**options)
    wanted = {m["prop"] for m in meta.values()}
    for key in [k for k in root.keys() if k.startswith(MME_PARAM_PREFIX) and k not in wanted]:
        del root[key]
    root[MME_PARAMS_PROP] = json.dumps(meta)
    return meta


# Restores every parameter to the effect's default, in place for vectors so animation keeps its channel.
def mme_reset_params(root):
    for name, info in mme_param_meta(root).items():
        prop = info.get("prop") or mme_prop_name(MME_PARAM_PREFIX, name)
        cast = {"i": int, "b": bool}.get(info["type"], float)
        typed = [cast(v) for v in info["default"]]
        if prop in root and info["size"] > 1:
            for k, value in enumerate(typed):
                root[prop][k] = value
        else:
            root[prop] = typed[0] if info["size"] == 1 else typed
