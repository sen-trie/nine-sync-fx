# Changelog

## 0.7.0

- The interface, tooltips and messages can now be translated through Blender's language settings. No language is complete yet; a Japanese draft awaits review. Generated node trees keep their English names.
- Back-end improvements to syncing and performance: faster syncing, effect builds, playback, and baking. New Transparency Order settings for balancing speed and accuracy.
- Effect file paths are saved relative to the .blend.
- A warning shows when an effect's files are missing.
- Edit Source Mesh and Edit Final Geometry frames in every effect pass, an Edit Particle Instances checkbox for sprite effects. Bakes now include GN edits.

## 0.6.0

- Each pass is one node group with a `NineSync Bake` node inside it, and baking, restoring and clean-up touch only that node.
- Exposed particle passes in geometry nodes.

## 0.5.2

- Store bakes on disk: baked effects can be kept in a shared on-disk store, so other projects with the same effect and animation reuse them.

## 0.5.1

- Lower peak build memory. Baked effects use less memory per frame, vertex shader inputs are no longer stored with the bake.
- Build Memory: Low trades build time for less RAM.

## 0.5.0

- Incremental rebuilds: Rebuild Changed Effects rebuilds only passes that were changed.
- Incremental bakes: Bake Changed skips passes whose stored bake still matches, and rebakes only the rest.

## 0.4.0

- Fix: effects assigned in MME (the PMM's `.emm`) are now used, including `none` and `Default`. A missing effect is reported, and accessories hidden in MME stay hidden.
- Accessories that switch parent bone between keys now follow each key's parent.
- Tr (opacity) fades accessories without an effect.

## 0.3.0

- Shared node library: `mme_core.blend` now lives in a user folder that survives updates.
- Open FX Library .blend button in the Node Library panel.
- Unresolved effect controllers can be assigned to an object or bone by hand.

## 0.2.0

- Split from the combined NineSync.
