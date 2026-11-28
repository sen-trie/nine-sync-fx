"""Route known packages through the existing scene effect adapter."""
from pathlib import Path

from ...experimental_adapters.compositor import CompositorAdapter, plan
from ...experimental_adapters.registry import load_registry, recognize
from ...experimental_adapters.scan import scan
from ...fx.errors import FxUnsupported


AUTO = "AUTO"


def plan_specialist(path, package_root=None):
    selected = Path(path).resolve(strict=True)
    root = Path(package_root).resolve(strict=True) if package_root else selected.parent
    try:
        entry = selected.with_suffix(".fx") if selected.suffix.lower() == ".x" else selected
        entrypoint = entry.relative_to(root).as_posix().casefold()
    except ValueError as error:
        raise FxUnsupported("The selected effect must be inside its package folder.") from error

    entries = load_registry()
    if not any(entrypoint == entry["entry"].casefold() for entry in entries.values()):
        return None

    facts = scan(selected, root)
    match = recognize(facts, entries)
    if not match.adapter:
        reasons = " ".join(match.reasons)
        raise FxUnsupported(f"The specialist for {selected.name!r} requires a verified package. {reasons}")
    return plan(match.adapter, match, facts)


def apply_specialist(scene, adapter_plan, force_rebuild):
    existing = {effect.as_pointer() for effect in scene.compositor_effects}
    effect = CompositorAdapter.apply(scene, adapter_plan)
    created = effect.as_pointer() not in existing
    if force_rebuild and not created:
        CompositorAdapter.rebuild(effect)
    return effect, created or force_rebuild
