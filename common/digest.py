import hashlib
import json
import os

DIGEST_LIMIT = 80
FILE_MEMO_BYTES = 8 << 20

_file_digests = {}


def _plain(value):
    if isinstance(value, float):
        return round(value, 6) + 0.0
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return "id:" + name
    if hasattr(value, "__len__") and hasattr(value, "__getitem__"):
        return [_plain(v) for v in value]
    return str(value)


# Deterministic text for a value: sorted keys, floats rounded to 6 places, no process-dependent hashing, so the same inputs
# give the same digest in every session.
def canonical(value):
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()[:32]


# Content digest of a file. Files up to FILE_MEMO_BYTES are always read, so a rewrite that keeps size and modified time
# is still noticed; larger ones (textures) are remembered per (path, size, mtime). Returns "missing" when it cannot be
# read, which never equals a real digest.
def file_digest(path):
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return "missing"
    memo = st.st_size > FILE_MEMO_BYTES
    key = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    found = _file_digests.get(key) if memo else None
    if found is None:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            return "missing"
        found = h.hexdigest()[:32]
        if memo:
            if len(_file_digests) > 512:
                _file_digests.clear()
            _file_digests[key] = found
    return found


# Short form of an input for the stored manifest and the logs: small values stay readable, big ones become a digest.
def brief(value):
    text = canonical(value)
    return _plain(value) if len(text) <= DIGEST_LIMIT else "#" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


# Named inputs to the form stored in a manifest: name -> brief value.
def brief_inputs(inputs):
    return {name: brief(value) for name, value in inputs.items()}


# Names whose stored brief form differs from the current inputs, as [(name, old, new)]; a name only on one side counts.
def diff_inputs(old, new):
    out = []
    for name in sorted(set(old) | set(new)):
        before = old.get(name, "<none>")
        after = brief(new[name]) if name in new else "<none>"
        if canonical(before) != canonical(after):
            out.append((name, before, after))
    return out


# One line per diff, capped, for logs.
def describe_diff(changes, limit=4):
    parts = ["%s %s -> %s" % (name, canonical(before), canonical(after)) for name, before, after in changes[:limit]]
    if len(changes) > limit:
        parts.append("+%d more" % (len(changes) - limit))
    return "; ".join(parts)
