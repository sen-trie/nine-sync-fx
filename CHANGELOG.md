# Changelog

## 0.5.1

- Lower peak build memory. Baked effects use less memory per frame, vertex shader inputs are no longer stored with the bake.
- Build Memory: Low trades build time for less RAM.

## 0.5.0

- Incremental rebuilds: Rebuild Changed Effects rebuilds only passes that were changed.
- Incremental bakes: Bake Changed skips passes whose stored bake still matches, and rebakes only the rest.

## 0.4.0

- Fix: effects assigned in MME (the PMM's `.emm`) are now used, including `none` and `Default`. A missing effect is reported instead of guessed, and accessories hidden in MME stay hidden.
- Accessories that switch parent bone between keys now follow each key's parent.
- Tr (opacity) fades accessories without an effect.

## 0.3.0

- Shared node library: `mme_core.blend` now lives in a user folder that survives updates.
- Open FX Library .blend button in the Core Node Library panel.
- Unresolved effect controllers can be assigned to an object or bone by hand.

## 0.2.0

- Split from the combined NineSync.
