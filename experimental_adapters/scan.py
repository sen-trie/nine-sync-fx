"""Read-only recognition of a selected entry in a bounded, unpacked package."""

from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path, PureWindowsPath
import posixpath
import re
import stat

from ..fx.errors import FxError
from ..fx.loader import fx_parse_source
from ..fx.mme_inspect import inspect_program
from ..fx.preprocessor import FxPreprocessor, fx_decode_text, fx_strip_comments
from ..xfile.cache import parse_x_bytes
from ..xfile.model import XError


SOURCE_LIMIT = 256 * 1024
FILE_LIMIT = 16 * 1024 * 1024
PACKAGE_LIMIT = 64 * 1024 * 1024
ENTRY_LIMIT = 512


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":")).encode("ascii")).hexdigest()


@dataclass
class EffectFacts:
    entry: str = ""
    files: dict = field(default_factory=dict)
    closure: str | None = None
    fingerprint: str | None = None
    role: str = "UNKNOWN"
    features: tuple = ()
    requirements: tuple = ()
    parameters: tuple = ()
    missing: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class Package:
    def __init__(self, root):
        self.root = root
        self.names = {}
        self.hashes = {}
        pending = [root]
        count = total = 0
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    count += 1
                    if count > ENTRY_LIMIT:
                        raise ValueError("Package contains too many files or directories")
                    info = entry.stat(follow_symlinks=False)
                    path = Path(entry.path)
                    name = path.relative_to(root).as_posix()
                    if (stat.S_ISLNK(info.st_mode) or
                            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                        raise ValueError(f"Linked package entry is unsupported: {name}")
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(path)
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError(f"Expected a regular package file: {name}")
                    key = name.casefold()
                    if key in self.names:
                        raise ValueError(f"Ambiguous case-insensitive package path: {key}")
                    self.names[key] = name
                    data = self.read(name, min(FILE_LIMIT, PACKAGE_LIMIT - total))
                    total += len(data)
                    self.hashes[name] = sha256(data).hexdigest()

    def read(self, name, limit):
        path = self.root / name
        if path.resolve() != path or not path.is_file():
            raise ValueError(f"Expected an in-package regular file: {name}")
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"Size limit exceeded: {name}")
        if name in self.hashes and sha256(data).hexdigest() != self.hashes[name]:
            raise ValueError(f"Package changed during analysis: {name}")
        return data

    def find(self, folder, name):
        windows = PureWindowsPath(name)
        if windows.anchor or ":" in name or "\x00" in name or not name:
            raise ValueError(f"Expected relative dependency: {name!r}")
        candidate = posixpath.normpath(posixpath.join(folder, name.replace("\\", "/")))
        if candidate == ".." or candidate.startswith("../"):
            raise ValueError(f"Dependency escapes package: {name!r}")
        return self.names.get(candidate.casefold())


class BoundedPreprocessor(FxPreprocessor):
    def __init__(self, package, entry, missing):
        self.package = package
        self.folder = posixpath.dirname(entry)
        self.source_bytes = self.expansions = self.output_chars = 0
        self.active = []
        super().__init__(str(package.root / self.folder), missing_includes=missing, read_text=self.read)

    def read(self, path):
        name = Path(path).relative_to(self.package.root).as_posix()
        if name in self.active:
            raise FxError(f"Recursive include: {name}")
        self.active.append(name)
        data = self.package.read(name, SOURCE_LIMIT - self.source_bytes)
        self.source_bytes += len(data)
        source = fx_decode_text(data)
        stripped = fx_strip_comments(source).replace("\\\r\n", "").replace("\\\n", "")
        for line in re.findall(r"(?m)^\s*#\s*include\b([^\r\n]*)", stripped):
            if not re.fullmatch(r'\s*(?:"[^"]+"|<[^>]+>)\s*', line):
                raise FxError(f"Malformed include in {name}: {line.strip()}")
        return source

    def resolve_include(self, name, base_dir):
        folder = Path(base_dir).relative_to(self.package.root).as_posix()
        found = self.package.find(folder, name) or self.package.find(self.folder, name)
        return str(self.package.root / found) if found else None

    def run_text(self, text, base_dir):
        try:
            return super().run_text(text, base_dir)
        finally:
            self.active.pop()

    def expand(self, text, hidden=frozenset()):
        self.expansions += 1
        if self.expansions > 20000:
            raise FxError("Preprocessor expansion limit exceeded")
        result = super().expand(text, hidden)
        self.output_chars += len(result)
        if self.output_chars > SOURCE_LIMIT * 2:
            raise FxError("Preprocessor output limit exceeded")
        return result

    def eval_condition(self, expression):
        expanded = self.expand(expression)
        if len(expanded) > 256 or re.search(r"\*\s*\*|<\s*<|>\s*>", expanded):
            raise FxError("Unsupported preprocessor condition")
        return super().eval_condition(expression)


def scan(selected, root=None):
    facts = EffectFacts()
    try:
        selected = Path(os.path.abspath(selected))
        root = Path(root or selected.parent).resolve(strict=True)
        facts.entry = selected.relative_to(root).as_posix()
        if selected.suffix.lower() not in {".fx", ".x"}:
            raise ValueError("Select an .fx shader or its paired .x file")
        package = Package(root)
        facts.files = dict(sorted(package.hashes.items()))
        if facts.entry.casefold() not in package.names:
            facts.missing.append(facts.entry)
            return facts
        if selected.suffix.lower() == ".x":
            shader = selected.with_suffix(".fx").relative_to(root).as_posix()
            facts.entry = package.names.get(shader.casefold(), shader)
            if facts.entry not in package.hashes:
                facts.missing.append(facts.entry)
                return facts
        else:
            facts.entry = package.names[facts.entry.casefold()]
        inspect_entry(package, facts)
        if not facts.missing:
            facts.fingerprint = digest([1, facts.entry, facts.files])
    except (FxError, XError, OSError, ValueError, IndexError, TypeError, ArithmeticError, RecursionError) as error:
        facts.errors.append(str(error))
    return facts


def inspect_entry(package, facts):
    path = package.root / facts.entry
    folder, _ = posixpath.split(facts.entry)
    includes = []
    pre = BoundedPreprocessor(package, facts.entry, includes)
    source = pre.run_file(path)
    facts.missing.extend(sorted({f"Include: {name}" for _, name in includes}))
    if includes:
        return

    program = fx_parse_source(source, str(path.parent), pre.files)
    closure = {Path(name).relative_to(package.root).as_posix() for name in program.files}

    def dependency(name, kind):
        found = package.find(folder, name)
        if found:
            closure.add(found)
        else:
            facts.missing.append(f"{kind}: {name}")
        return found

    def controller(parameter, name, item, used):
        if used and name.lower().endswith((".x", ".pmx", ".pmd")):
            dependency(name, f"Controller for {parameter}")

    def routing(target, selector, effect):
        if effect.lower().endswith(".fx"):
            dependency(effect, f"Offscreen effect for {target}")

    mesh = None
    mesh_name = package.find(folder, path.with_suffix(".x").name)
    if mesh_name:
        closure.add(mesh_name)
        data = package.read(mesh_name, SOURCE_LIMIT)
        if data[8:12] == b"txt ":
            scene = parse_x_bytes(data)
            mesh = {"format": scene.mode, "faces": sum(len(m.faces) for m in scene.meshes),
                    "subsets": sum(max(1, len(m.materials)) for m in scene.meshes)}
        else:
            facts.warnings.append("Non-text .x geometry could not be classified")

    report = {"warnings": facts.warnings, "techniques": [], "semantics": {},
              "dry_run": "not_run"}
    inspect_program(program, mesh, report, lambda name: dependency(name, "Texture"), controller, routing, None)
    targets = set(report["target_kinds"])
    if "FULLSCREEN_POST" in targets:
        facts.role = ("MIXED" if "ACCESSORY_GEOMETRY" in targets else
                      "POST_CONTROLLER" if mesh_name else "FULLSCREEN_POST")
    elif "ACCESSORY_GEOMETRY" in targets:
        facts.role = "PARTICLE" if "A2" in report["package_modes"] else "ACCESSORY_GEOMETRY"
    elif "MODEL_MATERIAL" in targets:
        facts.role = "MODEL_MATERIAL"
    else:
        facts.role = next(iter(targets)) if len(targets) == 1 else "UNKNOWN"
        if mesh_name and facts.role == "UNKNOWN":
            facts.warnings.append("Paired .x could be a controller or an accessory")
    if mesh and report["reads_material_texture"]:
        for part in scene.meshes:
            for material in part.materials:
                for name in (material.texture or "").replace("\\\\", "\\").split("*"):
                    if name.strip():
                        dependency(name.strip(), "Mesh texture")
    facts.features = tuple(sorted(key for key, value in report["features"].items() if value))
    facts.requirements = tuple(report["unsupported"])
    facts.parameters = tuple(sorted((key, value.type) for key, value in program.globals.items()))
    facts.missing[:] = sorted(set(facts.missing))
    if not facts.missing:
        facts.closure = digest([1, facts.entry, {name: package.hashes[name] for name in closure}])
