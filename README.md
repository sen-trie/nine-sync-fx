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
- Each effect pass is a Geometry Nodes group with places for your own nodes. **Edit Source Mesh** (the mesh one particle is made of) and **Edit Final Geometry** (the finished mesh) are frames in the pass's top-level node tree: add nodes on the link inside the frame. Rebuilding the effect keeps your nodes in them. A pass's bake holds everything the pass makes, your nodes included, and editing them marks the bake out of date. Effects made of particle sprites also have an **Edit Particle Instances** checkbox in the Pass Details section of the Selected Effect panel, per pass; with it on, each particle becomes an instance you can edit in the frame in the pass's node tree. It is part of the effect, so leave it on when rendering. **Enable Geometry Node Editing** (same section, per pass) shows the live nodes instead of the bake in the viewport so your edits show at once; renders ignore it and keep using the bake. Editing nodes in a baked pass keeps its bake and marks it out of date until you press Bake Changed. Rebuild is only needed when the effect itself changes (its .fx or .x file, or a setting that changes how it is built): it keeps your nodes, but zone nodes (Repeat, Simulation) inside the frames are not carried over, so build those in a group node instead.
- **Transparency Order** (Advanced, and per accessory in its Effect Controls) applies to effects such as Nuke2 whose vertex shader moves each copy of a mesh rigidly; the shader then runs on a few vertices per copy. **MMD Order** (default) keeps the image the same as MMD; **Blender Sorted** draws the copies as instances, which is fastest; EEVEE sorts see-through copies by distance, so overlapping smoke blends differently there, while Cycles renders it the same as the other levels; **Full Shader** runs the shader on every vertex.
- For older `.blend` projects, **Rebuild All Effects** is available in the add-on panels.

## Reporting issues

Report bugs and request features in the [NineSync release tracker](https://github.com/sen-trie/nine-sync-release/issues/new/choose), which covers both the combined extension and the standalone add-ons.

## License

GPL-3.0-or-later. See `LICENSE`.
