# NineSync: FX Importer

Import MikuMikuDance `.x` accessories and apply supported MME `.fx` effects in Blender. Use it on its own or alongside [NineSync: PMM Bridge](https://github.com/sen-trie/nine-sync-core) to bring accessories and effects into a synced MMD project.

## Requirements

- Blender 5.3+

For syncing accessories with MMD models, use PMM Bridge with [mmd_tools](https://extensions.blender.org/add-ons/mmd-tools/).

## Installation

1. Download the FX Importer extension ZIP from [Releases](https://github.com/sen-trie/nine-sync-fx/releases).
2. In Blender, open **Edit > Preferences > Get Extensions**, select **Install from Disk**, and choose the ZIP.

## Getting started

To import an accessory, go to **File > Import > MMD Accessory (.x)** and select your file. If a matching `.fx` file is in the same folder, NineSync can use it automatically; you can also select an effect yourself.

Open the **NineSync** tab in the 3D Viewport sidebar (**N**) to manage accessories, effects and baking.

When used with **PMM Bridge**, click **Sync Now** in PMM Bridge to import or update accessories along with the project.

## Notes

- **EEVEE** is recommended for rendering effects. **Cycles** can also be used, but some effects may require manual shader and transparency settings adjustments.
- Full-screen and post-processing MME effects are not supported.
- For older `.blend` projects, **Repair Legacy Data** and **Rebuild All Effects** are available in the add-on panels.

## Reporting issues

Report bugs and request features in the [NineSync release tracker](https://github.com/sen-trie/nine-sync-release/issues/new/choose), which covers both the combined extension and the standalone add-ons.

## License

GPL-3.0-or-later. See `LICENSE`.
