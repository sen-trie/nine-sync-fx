import os
from pathlib import Path

from fx.errors import FxError
from fx.loader import fx_parse_source
from fx.preprocessor import FxPreprocessor
from fx.textures import fx_find_file
from xfile.cache import parse_x_bytes
from xfile.model import XError

from fx.mme_inspect import inspect_program

from .probe import probe_geometry


TEXTURES = {".png", ".bmp", ".dds", ".jpg", ".jpeg", ".tga", ".spa", ".sph", ".gif", ".tif", ".tiff", ".exr", ".hdr"}
EXTENSIONS = {"x", "fx", "fxsub", "fxh", "hlsl", "pmx", "pmd", "emd", "emm"}
INPUT_ERRORS = (FxError, XError, OSError, ValueError, TypeError, IndexError, ArithmeticError, RecursionError)


def relative(path, root):
    try:
        return Path(os.path.relpath(path, root)).as_posix()
    except ValueError:
        return str(path)


def inventory(root):
    files = {}
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(names):
            path = Path(folder) / name
            ext = path.suffix.lower()
            if name.casefold().startswith(("readme", "read me", "license", "利用規約", "りどみ", "説明書", "使い方", "はじめに")):
                kind = "documentation"
            elif ext in TEXTURES:
                kind = "texture"
            elif ext[1:] in EXTENSIONS:
                kind = ext[1:]
            else:
                continue
            files.setdefault(kind, []).append(path.relative_to(root).as_posix())
    return files


def texture_ref(root, folder, name, mesh=False):
    path = fx_find_file(folder, name)
    if mesh and not path:
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        path = fx_find_file(folder, base) or fx_find_file(folder / "textures", base)
    valid = path and Path(path).is_file() and Path(path).suffix.lower() in TEXTURES
    return {"name": name, "path": relative(path, root) if valid else None}


def inspect_mesh(root, name):
    path = root / name
    info = {"path": name, "format": None, "faces": 0, "subsets": 0, "textures": [], "warnings": []}
    try:
        scene = parse_x_bytes(path.read_bytes())
        info.update(format=scene.mode, faces=sum(len(m.faces) for m in scene.meshes),
                    subsets=sum(max(1, len(m.materials)) for m in scene.meshes), warnings=list(scene.warnings))
        parts = {p.strip() for mesh in scene.meshes for mat in mesh.materials if mat.texture
                 for p in mat.texture.replace("\\\\", "\\").split("*")}
        for texture in sorted(parts - {""}):
            ref = texture_ref(root, path.parent, texture, mesh=True)
            info["textures"].append(ref)
            if ref["path"] is None:
                info["warnings"].append(f"Missing mesh texture: {texture}")
    except INPUT_ERRORS as error:
        info["warnings"].append(str(error))
    return info


def inspect_effect(root, name, mesh, files, names, probe):
    path = root / name
    report = {"path": name, "mesh": mesh["path"] if mesh else None, "stage": "FILES_PRESENT",
              "includes": [], "missing_includes": [], "resources": [], "controllers": [], "offscreen_effects": [],
              "semantics": {}, "techniques": [], "draws": [], "features": {},
              "dependencies": [], "target_kinds": ["UNKNOWN"], "package_modes": [], "unsupported": [],
              "compatibility_prediction": None, "confidence": "low", "verification": "unverified",
              "dry_run": "not_run", "partial": False, "skipped_draws": [], "reads_material_texture": False, "warnings": list(mesh["warnings"]) if mesh else []}

    def resource(value):
        ref = texture_ref(root, path.parent, value)
        report["resources"].append(ref)
        if ref["path"] is None:
            report["warnings"].append(f"Missing texture: {value}")

    def controller(parameter, value, item, used):
        target = (path.parent / value.replace("\\", "/")).resolve()
        resolved = files.get(relative(target, root).casefold()) or names.get(target.name.casefold())
        report["controllers"].append({"parameter": parameter, "name": value, "item": item,
                                      "read": used, "package_file": resolved})
        if used and not resolved and target.suffix.lower() in {".pmx", ".pmd", ".x"}:
            report["warnings"].append(f"External controller binding required: {value} ({parameter})")

    def routing(target, selector, effect):
        resolved = None
        if effect.lower().endswith(".fx"):
            resolved = files.get(relative(path.parent / effect.replace("\\", "/"), root).casefold())
            if not resolved:
                report["warnings"].append(f"Missing offscreen effect: {effect}")
        report["offscreen_effects"].append({"target": target, "selector": selector, "effect": effect, "path": resolved})

    missing_includes = []
    pre = FxPreprocessor(str(path.parent), missing_includes=missing_includes)
    try:
        source = pre.run_file(path)
        if not missing_includes:
            program = fx_parse_source(source, str(path.parent), pre.files)
            report["stage"] = "PARSED"
            inspect_program(program, mesh, report, resource, controller, routing, probe_geometry if probe else None)
    except INPUT_ERRORS as error:
        report["warnings"].append(str(error))
        if report["stage"] == "PARSED":
            report["dry_run"] = "failed"
    if not report.get("reads_material_texture"):
        report["warnings"] = [w for w in report["warnings"] if not w.startswith("Missing mesh texture")]
    report["includes"] = sorted({relative(p, root) for p in pre.files[1:]})
    for folder, include in sorted(set(missing_includes)):
        report["missing_includes"].append({"folder": relative(folder, root), "name": include})
        report["warnings"].append(f"Missing include: {include}")
    return report


# An .fx another file includes, or that an offscreen target assigns, is not something an artist loads; a
# same-name PMX or PMD is the model MME applies an unpaired .fx to.
def assign_roles(effects, assets):
    included = {i.casefold() for e in effects for i in e["includes"]}
    companions = {o["path"].casefold() for e in effects for o in e["offscreen_effects"] if o["path"]}
    for e in effects:
        key = e["path"].casefold()
        e["role"] = "include" if key in included else "offscreen_companion" if key in companions else "entry"
        e["paired_model"] = None
        if e["role"] != "entry":
            e.update(target_kinds=[e["role"].upper()], package_modes=[], compatibility_prediction=None, partial=False)
            e["warnings"] = [w for w in e["warnings"] if not w.startswith(("Missing include", "Partial"))]
            continue
        if e["mesh"] is None:
            for ext in (".pmx", ".pmd"):
                model = assets.get(Path(e["path"]).with_suffix(ext).as_posix().casefold())
                if model:
                    e["paired_model"] = model
                    e["target_kinds"] = sorted((set(e["target_kinds"]) - {"UNKNOWN"}) | {"PAIRED_MODEL"})
                    e["compatibility_prediction"] = e["compatibility_prediction"] or "C"
                    e["warnings"].append(f"Drawn on the same-name model {model}; NineSync has no PMX geometry path")
                    break


def scan_package(root, source=None, probe=False):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"Expected an unpacked package directory: {root}")
    files = inventory(root)
    assets = {name.casefold(): name for ext in ("x", "pmx", "pmd", "fx") for name in files.get(ext, [])}
    names = {}
    for name in sorted(assets.values()):
        names.setdefault(Path(name).name.casefold(), name)
    meshes = {name.casefold(): inspect_mesh(root, name) for name in files.get("x", [])}
    effects = [inspect_effect(root, name, meshes.get(Path(name).with_suffix(".x").as_posix().casefold()), assets, names, probe)
               for name in files.get("fx", [])]
    assign_roles(effects, assets)
    effects_main = [e for e in effects if e["role"] == "entry"]
    controllers = sorted({c["package_file"] for e in effects_main for c in e["controllers"] if c["read"] and c["package_file"]})
    targets = {t for e in effects_main for t in e["target_kinds"]}
    modes = {m for e in effects_main for m in e["package_modes"]}
    if controllers:
        targets.add("CONTROLLER")
        modes.add("A3")
    if any(rule["path"] for e in effects for rule in e["offscreen_effects"]):
        modes.add("A6")
    if len(targets - {"UNKNOWN"}) > 1:
        targets.add("MULTI_TARGET")
    ratings = [e["compatibility_prediction"] for e in effects_main if e["compatibility_prediction"]]
    confidence = min((e["confidence"] for e in effects_main), key=("low", "medium", "high").index, default="low")
    families = set()
    if "FULLSCREEN_POST" in targets:
        families.add("POST")
    if "MODEL_MATERIAL" in targets:
        families.add("MODEL_MATERIAL")
    return {"schema_version": 2, "package": root.name, "source": source or root.name, "files": files, "meshes": list(meshes.values()),
            "effects": effects, "controller_files": controllers, "package_modes": sorted(modes),
            "target_kinds": sorted(targets or {"UNKNOWN"}), "dependencies": sorted({d for e in effects_main for d in e["dependencies"]}),
            "visual_families": sorted(families), "compatibility_prediction": max(ratings, default=None),
            "confidence": confidence, "verification": "unverified", "license_review": "pending"}
