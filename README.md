# NineSync: FX Importer

Imports MikuMikuDance accessories (`.x`) and runs their MME effects (`.fx`): vertex shaders become Geometry Nodes (or run in numpy), pixel shaders become material nodes. Works on its own, or driven by **NineSync: PMM Bridge** (`ninesync`).

## Requirements

- Blender 5.3 or newer
- Optional: mmd_tools (parent bones, morph names), DirectX X Importer (binary or compressed `.x` only)

## Ways to use it

1. **Manual import.** File > Import > MMD Accessory (.x). A same-name `.fx` next to the `.x` is used, or pick one. Tr and Si and every effect parameter appear as custom properties on the accessory root.
2. **With NineSync: PMM Bridge.** Press Sync Now there. Core parses the PMM, retimes it and calls this add-on through the hook registry; this add-on imports the accessories with their animation, parenting and effects. FX reads no PMM itself.

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

Scene data from older NineSync versions keeps working: collection names, `MMDBridge_` action prefix, `mmd_bridge_accessory` and `mme_*` object properties are unchanged. For `.blend` files saved with an older version, press Repair Legacy Data in the Accessories panel, then Rebuild All Effects.

## Shared node library

Effects reuse node groups from `mme_core.blend`. The add-on ships a read-only copy in `data/` and works from your own copy, which survives updates: the extension's user folder, or `<Blender config>/datafiles/ninesync_fx` for a development install. Core Node Library shows the path, and **Open FX Library .blend** opens it, in a second Blender by default so the current file stays open.

On startup your copy is compared with the bundled one by the `:v<N>` version in the group keys:

| Bundled compared with yours | What happens |
|---|---|
| Newer | Your library is replaced; older groups are obsolete. The previous file stays as `mme_core.blend.bak` |
| Same version, bundled has new groups | Merged. Bundled wins a duplicate key, and groups only you have are kept, nested groups included |
| Same version, nothing new | Nothing is written |
| Older | Your library is left alone. If it is newer than this add-on can read, the bundled copy is used read-only for the session |

Every write goes through a temporary file that must validate before an atomic replace. If an update fails, your file is left as it was and the bundled copy is used until Blender restarts. A damaged library is kept as `.corrupt` and restored from `.bak` when that is good.

## Rendering

EEVEE is the assumed renderer. In Cycles, stacked effect layers can render black: raise Light Paths > Max Bounces > Transparent, 128 is plenty. The add-on leaves your view transform alone, so use Standard if effect colors should match MMD.

## Not supported

Full-screen and post-process passes (`Draw=Buffer`, and effects that work on the rendered image) are out of scope: this add-on converts effects drawn on geometry, and has no planned way to run those. They are skipped, and the Passes tab and the log name what was skipped.

## Layout

```
__init__.py        register, unregister, bl_info
handlers.py        frame, load and depsgraph handlers, hook registration
hooks.py           hook registry client (identical in ninesync)
common/            constants, state, logging, scene and animation helpers (some copied from ninesync), controller helpers
xfile/             .x parser, merge, texture lookup (pure Python)
fx/                HLSL .fx front end (preprocessor, lexer, parser, types, techniques, semantics)
fx/interp/         interpreter (FxExec and its mixins), numpy backend, intrinsics
nodes/             node library and its on-disk store, shader and Geometry Nodes backends, materials, layout, shared groups
runtime/           effect build, pruning, strip simplification, per-frame update, bake, visibility, progress
accessories/       .x import, materials, parenting, keying, the single sync path
standalone/        legacy repair
ui/                properties, operators, panels, relink, hook entry points
data/mme_core.blend  shared node groups
tools/check_vendored.py   fails if hooks.py drifts from core
tests/
```

Packages import only from the ones above them: `common`, `xfile`, `fx`, `nodes`, `runtime`, `accessories`, `standalone`, `ui`, `handlers`, `__init__` (`hooks.py` only needs `common`).

## Compatibility

| FX | core | hook API |
|---|---|---|
| 0.2.x | 0.2.x or none | 1 |
