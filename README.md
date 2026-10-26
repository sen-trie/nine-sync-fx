# NineSync: FX Importer

Imports MikuMikuDance accessories (`.x`) and runs their MME effects (`.fx`): vertex shaders become Geometry Nodes (or run in numpy), pixel shaders become material nodes. Works on its own, or driven by **NineSync: PMM Bridge** (`ninesync`).

## Requirements

- Blender 5.3 or newer
- Optional: mmd_tools (parent bones, morph names), DirectX X Importer (binary or compressed `.x` only)

## Ways to use it

1. **Manual import.** File > Import > MMD Accessory (.x). A same-name `.fx` next to the `.x` is used, or pick one. Tr and Si and every effect parameter appear as custom properties on the accessory root.
2. **Its own PMM sync.** In the Accessories panel, set a PMM file, press Read PMM, match its models to mmd_tools models, press Sync Accessories. Uses a vendored copy of the PMM parser and retimes accessory keys to the scene fps.
3. **With NineSync: PMM Bridge.** Press Sync Now there. Core calls this add-on through the hook registry; the PMM field here is replaced by a note.

## Panels (sidebar tab NineSync)

- Accessories (.x), order 1
- FX Importer, order 2, with Selected Effect, Accessory Effects, Bake and Core Node Library

## Names

| Thing | Value |
|---|---|
| Scene settings | `scene.ninesync_fx` |
| Operators | `ninesync_fx.*`, `import_scene.ninesync_fx_accessory` |
| Panels | `NINESYNC_FX_PT_*` |
| Logger | `NineSync.FX` (shared text block `NineSyncLog`) |

Scene data from the old combined add-on keeps working: collection names, `MMDBridge_` action prefix, `mmd_bridge_accessory` and `mme_*` object properties are unchanged. For `.blend` files saved with the old add-on, press Repair Legacy Data in the Accessories panel, then Rebuild All Effects.

## Layout

```
__init__.py        register, unregister, bl_info
handlers.py        frame, load and depsgraph handlers, hook registration
hooks.py           hook registry client (identical in ninesync)
pmm.py             vendored copy of ninesync/pmm.py (identical)
common/            constants, state, logging, scene and animation helpers (some copied from ninesync), controller helpers
xfile/             .x parser, merge, texture lookup (pure Python)
fx/                HLSL .fx front end (preprocessor, lexer, parser, types, techniques, semantics)
fx/interp/         interpreter (FxExec and its mixins), numpy backend, intrinsics
nodes/             node library, shader and Geometry Nodes backends, materials, layout, shared groups
runtime/           effect build, pruning, strip simplification, per-frame update, bake, visibility, progress
accessories/       .x import, materials, parenting, keying, the single sync path
standalone/        own PMM read, model links, retime, legacy repair
ui/                properties, operators, panels, relink, hook entry points
data/mme_core.blend  shared node groups
tools/check_vendored.py   fails if pmm.py or hooks.py drift from core
tests/
```

Packages import only from the ones above them: `common`, `xfile`, `fx`, `nodes`, `runtime`, `accessories`, `standalone`, `ui`, `handlers`, `__init__` (`hooks.py` and `pmm.py` only need `common`).

## Compatibility

| FX | core | hook API |
|---|---|---|
| 0.2.x | 0.2.x or none | 1 |
