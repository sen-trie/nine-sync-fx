# Changelog

## 0.7.0

- Upgrading: every existing bake reads as stale once after this update and is rebaked on the next bake or sync (this is not backward-compatible bake reuse), and the accessory key record becomes version 2. Bakes made by 0.7.0 are shared and reused as before.
- The NineSyncLog text block saved in the .blend now shows the home folder as `~` (for example `~\Projects\song.pmm`), so the account name is not stored. Logs saved by earlier versions are not rewritten and may still contain full paths.
- Not included: library pruning (the second half of the library-size work) is not done yet.
- A sync after the effect files changed only in modified time or path now goes through the incremental builder: unchanged passes keep their objects and bakes. A replaced pass is removed only after its replacement was built, and a failed rebuild or an unreadable effect keeps the working passes.
- Bake validity and the shared-store address now include Core-owned animation: Child Of / parent bones and their ancestors, bones read by transform drivers, constraints, and the scene camera and light rig. Every existing bake reads as stale once, then only real changes rebake. Every sync re-checks bakes.
- Effect drivers whose target object is gone (a recreated light rig) are detected and the pass is rebuilt; the light binding is part of the build signature.
- The post_sync hook reports `{"imported", "failed", "warnings"}` and runs as a generator when Core offers `ctx.cancelled`, so Esc is heard between effect passes; FX starts no progress bar in an integrated sync.
- Frame handler skips Core's internal frame loops (`shared["internal_frames"]`) and tags a pass only when a uniform value changed. The user library is saved once per sync and the bake store trimmed once per bake run, without evicting bakes that run just published.
- Action traversal is slot-aware with or without mmd_tools; the accessory key record is version 2, adopting old records instead of reading them as edits.
- The shared log trim keeps the last 2000 lines instead of clearing the text.
- Preferences are found through the package and its parents; FX registers no preferences class when the bundle sets `NINESYNC_PREFERENCES_OWNER`.

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

- Fix: effects assigned in MME (the PMM's `.emm`) are now used, including `none` and `Default`. A missing effect is reported instead of guessed, and accessories hidden in MME stay hidden.
- Accessories that switch parent bone between keys now follow each key's parent.
- Tr (opacity) fades accessories without an effect.

## 0.3.0

- Shared node library: `mme_core.blend` now lives in a user folder that survives updates.
- Open FX Library .blend button in the Core Node Library panel.
- Unresolved effect controllers can be assigned to an object or bone by hand.

## 0.2.0

- Split from the combined NineSync.
