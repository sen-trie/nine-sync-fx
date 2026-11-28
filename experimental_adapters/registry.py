"""Exact recognition policy. Registry errors disable all source-specific offers."""

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re

from .scan import digest


@dataclass(frozen=True)
class Match:
    kind: str
    reasons: tuple
    entry: dict | None = None

    @property
    def adapter(self):
        return self.entry["adapter"] if self.kind == "VERIFIED_EXACT" else None


FIELDS = {"entry", "name", "version", "author", "source", "review",
          "files", "role", "features", "adapter", "adapter_notes", "status"}
DIGEST = re.compile("[0-9a-f]{64}")
REGISTRY = Path(__file__).with_name("registry.json")


def unique_items(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate registry field: {key}")
        result[key] = value
    return result


def valid_path(name):
    path = PurePosixPath(name)
    return (path.as_posix() == name and name != "." and not path.is_absolute()
            and ".." not in path.parts and not any(c in name for c in "\\:\x00"))


def load_registry(path=REGISTRY):
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_items)
        if (set(data) != {"schema", "entries"} or type(data["schema"]) is not int or
                data["schema"] != 1 or not isinstance(data["entries"], list)):
            raise ValueError("Expected registry schema 1 and an entries list")
        entries = {}
        for entry in data["entries"]:
            if not isinstance(entry, dict) or set(entry) != FIELDS:
                raise ValueError("Unexpected registry entry fields")
            strings = FIELDS - {"features", "files"}
            if any(not isinstance(entry[key], str) or not entry[key].strip() for key in strings):
                raise ValueError("Entry strings must be nonempty")
            files, features = entry["files"], entry["features"]
            if (not isinstance(files, dict) or entry["entry"] not in files or
                    any(not valid_path(name) or not isinstance(value, str) or not DIGEST.fullmatch(value)
                        for name, value in files.items()) or
                    len({name.casefold() for name in files}) != len(files)):
                raise ValueError("Invalid registry file manifest")
            if (not isinstance(features, list) or any(not isinstance(item, str) for item in features) or
                    sorted(set(features)) != features):
                raise ValueError("Features must be unique and sorted")
            if entry["status"] not in {"enabled", "incompatible", "review"} or entry["adapter"] != "tonal":
                raise ValueError("Invalid adapter or review status")
            key = digest([1, entry["entry"], files])
            if key in entries:
                raise ValueError("Duplicate entrypoint and package fingerprint")
            entries[key] = entry
        return entries
    except (OSError, ValueError, TypeError) as error:
        raise ValueError(f"Cannot load experimental effect registry: {error}") from error


def recognize(facts, entries=None):
    if facts.errors or facts.missing:
        return Match("INCOMPLETE", tuple(facts.errors + facts.missing))
    try:
        entries = load_registry() if entries is None else entries
    except ValueError as error:
        return Match("BLOCKED_FOR_REVIEW", (str(error),))
    entry = entries.get(facts.fingerprint)
    if entry:
        reasons = (f"Entrypoint {facts.entry} and {len(facts.files)} package file hashes match", entry["review"])
        if entry["status"] == "review":
            return Match("BLOCKED_FOR_REVIEW", reasons, entry)
        if (entry["status"] == "incompatible" or facts.role != entry["role"] or
                facts.features != tuple(entry["features"])):
            return Match("INCOMPATIBLE", (*reasons, "Reported render features differ from the reviewed adapter scope"), entry)
        return Match("VERIFIED_EXACT", reasons, entry)
    for known in entries.values():
        if (known["entry"] == facts.entry and
                facts.files.get(facts.entry) == known["files"][facts.entry]):
            absent = sorted(known["files"].keys() - facts.files.keys())
            if absent:
                return Match("INCOMPLETE", tuple(f"Missing package file: {name}" for name in absent))

    candidates = [e for e in entries.values()
                  if e["role"] == facts.role and tuple(e["features"]) == facts.features]
    if candidates:
        example = next((e for e in candidates if e["entry"] == facts.entry), candidates[0])
        changed = sorted(name for name in facts.files.keys() | example["files"].keys()
                         if facts.files.get(name) != example["files"].get(name))
        differences = ", ".join(changed[:5]) or "entrypoint differs"
        return Match("FAMILY_CANDIDATE", (f"Render features overlap a registered entry. Package identity differs ({differences}).",))
    return Match("UNKNOWN", ("No exact package and entrypoint match.",))
