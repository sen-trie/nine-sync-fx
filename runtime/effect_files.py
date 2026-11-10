import bpy
import os
import re
from typing import NamedTuple, Optional

from ..common.constants import (
    MME_EMM_CACHE_SIZE,
    MME_EMM_HIDE,
    MME_EMM_NO_EFFECT,
)
from ..common.log import log
from ..common.state import _mme_emm
from ..fx.preprocessor import fx_read_text
from ..fx.textures import fx_find_file


MME_EMM_KEY = re.compile(r"^(acs|pmd)(\d+)(?:\[(\d+)\])?(?:\.(show))?$")


# One parsed .emm. [Object] numbers accessories (Acs<N>) and models (Pmd<N>) in one sequence; the main [Effect]
# tab holds the assignments, show flags, per-subset entries and Default. [Effect@<tab>] sections belong to
# offscreen render targets and are not read.
class MmeEmm:
    __slots__ = ("objects", "effects", "shown", "subsets", "default")

    def __init__(self):
        self.objects = {}
        self.effects = {}
        self.shown = {}
        self.subsets = {}
        self.default = None


# How an accessory's effect was decided: source is chosen, emm, emm-default, emm-none, emm-missing or beside;
# value is the .emm text when the .emm decided.
class MmeEffectChoice(NamedTuple):
    path: Optional[str]
    source: str
    value: str = ""
    shown: bool = True
    subsets: bool = False


def mme_parse_emm(text):
    emm = MmeEmm()
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "//")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if "=" not in line or section not in ("object", "effect"):
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        key = key.lower()
        if section == "effect" and key == "default":
            emm.default = value
            continue
        match = MME_EMM_KEY.match(key)
        if match is None:
            continue
        slot = "%s%d" % (match.group(1), int(match.group(2)))
        subset, show = match.group(3), match.group(4)
        if section == "object":
            if subset is None and show is None:
                emm.objects[slot] = value
        elif show:
            if subset is None:
                emm.shown[slot] = value.lower() != "false"
        elif subset is not None:
            emm.subsets.setdefault(slot, {})[int(subset)] = value
        else:
            emm.effects[slot] = value
    return emm


# The .emm MME saves next to the PMM, parsed once per file version.
def mme_read_emm(pmm_path):
    if not pmm_path:
        return None
    path = os.path.splitext(pmm_path)[0] + ".emm"
    try:
        st = os.stat(path)
    except OSError:
        return None
    stamp = (st.st_size, st.st_mtime_ns)
    cached = _mme_emm.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        emm = mme_parse_emm(fx_read_text(path))
    except OSError as error:
        log.warning("could not read %s: %s", path, error)
        return None
    if len(_mme_emm) >= MME_EMM_CACHE_SIZE:
        _mme_emm.clear()
    _mme_emm[path] = (stamp, emm)
    return emm


# PMM and .emm paths are Windows paths from the machine MMD ran on, so they are compared case-insensitively
# with one separator.
def mme_emm_norm(path):
    return (path or "").strip().strip('"').replace("\\", "/").lower()


def mme_emm_absolute(norm):
    return norm.startswith("/") or (len(norm) > 1 and norm[1] == ":")


# An absolute .emm path must equal the file's path; a relative one (based on the MMD folder) must be a
# whole-component suffix of it.
def mme_emm_same_file(full, listed):
    if not full or not listed:
        return False
    if mme_emm_absolute(listed):
        return full == listed
    return full == listed or full.endswith("/" + listed)


# The [Object] slot of an accessory, matched by path. MME numbers accessories in draw order, not by PMM index,
# so copies of one file pair up in (draw order, index) order. A file name that only one slot and one accessory
# have is the last resort.
def mme_emm_slot(emm, acc, accessories):
    wanted = mme_emm_norm(acc.path)
    slots = [slot for _n, slot in sorted((int(s[3:]), s) for s in emm.objects if s.startswith("acs"))]
    hits = [s for s in slots if mme_emm_same_file(wanted, mme_emm_norm(emm.objects[s]))]
    if len(hits) > 1:
        twins = sorted(
            (a for a in accessories if mme_emm_norm(a.path) == wanted),
            key=lambda a: (getattr(a, "draw_order", 0), a.index),
        )
        rank = next((k for k, a in enumerate(twins) if a.index == acc.index), 0)
        return hits[min(rank, len(hits) - 1)]
    if hits:
        return hits[0]
    base = wanted.rsplit("/", 1)[-1]
    named = [s for s in slots if mme_emm_norm(emm.objects[s]).rsplit("/", 1)[-1] == base]
    same = [a for a in accessories if mme_emm_norm(a.path).rsplit("/", 1)[-1] == base]
    return named[0] if len(named) == 1 and len(same) <= 1 else None


# MME writes paths inside the MMD folder relative to it, and the PMM does not name that folder. Every file the
# PMM and the .emm both list gives it as the difference of the two paths, from the path as found on this
# machine first, then as recorded.
def mme_emm_roots(emm, files):
    roots = []
    for listed in emm.objects.values():
        rel = mme_emm_norm(listed)
        if not rel or mme_emm_absolute(rel):
            continue
        for full in files:
            text = (full or "").strip().strip('"')
            norm = mme_emm_norm(text)
            if len(norm) == len(text) and norm.endswith("/" + rel):
                root = text[: len(text) - len(rel)]
                if root not in roots:
                    roots.append(root)
    return roots


def mme_ancestors(path):
    out = []
    folder = os.path.dirname(path) if path else ""
    while folder and folder not in out:
        out.append(folder)
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return out


# An [Effect] value as a file: absolute as written; relative to the MMD folder, the PMM folder or one of its
# or the .x file's parent folders; last, by name beside the .x, where effects usually ship.
def mme_emm_effect_file(value, roots, pmm_path, x_path):
    text = value.strip().strip('"')
    if mme_emm_absolute(mme_emm_norm(text)):
        candidates = [text]
    else:
        rel = text.replace("\\", "/")
        bases = list(roots) + mme_ancestors(pmm_path) + mme_ancestors(x_path)
        candidates = [os.path.normpath(os.path.join(base, rel)) for base in bases]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return fx_find_file(os.path.dirname(x_path), text.replace("\\", "/").rsplit("/", 1)[-1])


# The .fx MME loads by itself for an accessory: the same name beside the .x, exact first, then in any case.
def mme_same_name_effect(x_path):
    stem = os.path.splitext(x_path)[0]
    if os.path.isfile(stem + ".fx"):
        return stem + ".fx"
    folder = os.path.dirname(x_path)
    wanted = os.path.basename(stem).lower() + ".fx"
    try:
        for name in os.listdir(folder):
            if name.lower() == wanted:
                return os.path.join(folder, name)
    except OSError:
        pass
    return None


# What MME draws an accessory with: a file chosen in the panel; else the PMM's .emm, whose [Effect] entry
# ('none' is no effect) or Default applies to the accessory's slot; else the same-name .fx, which is also what
# MME loads for an accessory the .emm does not list. Imports without a PMM never read an .emm.
def mme_effect_lookup(acc, x_path, settings, entry=None, project=None):
    if entry is not None and entry.effect_path:
        override = bpy.path.abspath(entry.effect_path)
        return MmeEffectChoice(override if os.path.isfile(override) else None, "chosen", entry.effect_path)
    if not getattr(acc, "standalone", False):
        pmm_path = bpy.path.abspath(settings.filepath) if settings.filepath else ""
        emm = mme_read_emm(pmm_path)
        accessories = list(getattr(project, "accessories", None) or [acc])
        slot = mme_emm_slot(emm, acc, accessories) if emm is not None else None
        if slot is not None:
            value = emm.effects.get(slot)
            source = "emm"
            if value is None and emm.default and emm.default.lower() not in MME_EMM_NO_EFFECT:
                value, source = emm.default, "emm-default"
            shown = emm.shown.get(slot, True) and (value or "").lower() != MME_EMM_HIDE
            subsets = bool(emm.subsets.get(slot))
            if value is not None:
                if value.lower() in MME_EMM_NO_EFFECT:
                    return MmeEffectChoice(None, "emm-none", value, shown, subsets)
                files = [x_path, acc.path] + [a.path for a in accessories] + [m.path for m in getattr(project, "models", None) or []]
                found = mme_emm_effect_file(value, mme_emm_roots(emm, files), pmm_path, x_path)
                return MmeEffectChoice(found, source if found else "emm-missing", value, shown, subsets)
            return MmeEffectChoice(mme_same_name_effect(x_path), "beside", "", shown, subsets)
    return MmeEffectChoice(mme_same_name_effect(x_path), "beside")


def mme_find_effect(acc, x_path, settings, entry, project=None):
    if entry is not None and not entry.use_effect:
        return None
    return mme_effect_lookup(acc, x_path, settings, entry, project).path
