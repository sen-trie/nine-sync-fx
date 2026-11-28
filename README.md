# NineSync: FX Importer

Import MikuMikuDance `.x` accessories and apply supported MME `.fx` effects in Blender. Use it on its own or alongside [NineSync: PMM Bridge](https://github.com/sen-trie/nine-sync-core) to bring accessories and effects into a synced MMD project.

This repository is part of the [combined NineSync extension](https://github.com/sen-trie/nine-sync-release).

## Requirements

- Blender 5.3+

For syncing accessories with MMD models, use PMM Bridge with [mmd_tools](https://extensions.blender.org/add-ons/mmd-tools/).

## Installation

1. Download the FX Importer extension ZIP from [Releases](https://github.com/sen-trie/nine-sync-fx/releases).
2. In Blender, open **Edit > Preferences > Get Extensions**, select **Install from Disk**, and choose the ZIP.

## Getting started

1. Go to **File > Import > MMD Accessory (.x)** and select your file. If a matching `.fx` file is in the same folder, NineSync can use it automatically; you can also select an effect yourself.
2. In the **3D Viewport**, press **N** and open the **NineSync** tab to manage accessories, effects and baking.
3. When used with **PMM Bridge**, click **Sync Now** in PMM Bridge to import or update accessories along with the project.

## Inspecting an MME package

To inspect an unpacked MME package from a source checkout, run the following from the repository root with Python and NumPy. The command prints JSON with the shaders, planned passes, controller references, missing resources and inferred target types.

```sh
python -m tools.mme "/path/to/effect-folder"
```

Add `--probe` to evaluate a sample vertex and pixel for eligible accessory shaders. The [compatibility matrix](tests/corpus/COMPATIBILITY_MATRIX.md) lists the fixtures exercised in Blender and the [taxonomy](tests/corpus/MME_EFFECT_TAXONOMY.md) explains the predictions.

## Reporting issues

Report bugs and request features in the [NineSync release tracker](https://github.com/sen-trie/nine-sync-release/issues/new/choose), which covers both the combined extension and the standalone add-ons.

## License

GPL-3.0-or-later. See `LICENSE`.
