# Changelog

## 0.4.0

- Fix: effects assigned in MME (the PMM's `.emm`) are now used, including `none` and `Default`. A missing effect is reported instead of guessed, and accessories hidden in MME stay hidden.
- Keys you edit on an accessory are kept on the next sync unless the PMM keys changed.
- Accessories that switch parent bone between keys now follow each key's parent.
- Tr (opacity) fades accessories without an effect.
- Additive accessories get an additive material once NineSync: PMM Bridge reports the flag.

## 0.3.0

- Shared node library: `mme_core.blend` now lives in a user folder that survives updates. New versions of the bundled library replace or merge into it, keeping groups grown from your own effects, with a backup of the previous file.
- Open FX Library .blend button in the Core Node Library panel.
- Translated node groups carry the label `MMEffects` in Shader Nodes and Geometry Nodes (group nodes for the pixel shader and the vertex shader; groups made from HLSL functions and intrinsics read `MMEffects: name(args)`).
- Unresolved effect controllers can be assigned to an object or bone by hand.
- Fix: the DirectX X Importer check always said an importer was installed (Blender answers `hasattr` on `bpy.ops` with True for any name), so the missing-importer notice never appeared. It now lists the registered operators.
- A second enabled copy of this add-on refuses to register instead of colliding with the first.
- GPL-3.0 `LICENSE`, repository links in the manifest and `bl_info`.

## 0.2.0

- Split from the combined NineSync. Runs without NineSync: PMM Bridge.
- Settings moved to `scene.ninesync_fx`; operators renamed to `ninesync_fx.*`; panels renamed `NINESYNC_FX_PT_*`.
- Own PMM read, model links, fps retime (vendored PMM parser) and a Support Report / Clean for accessories.
- Repair Legacy Data operator for files saved with the combined add-on.
- Shared node library is copied to the user extension folder when installed as an extension, so updates do not lose it.
- Hook registry (`post_sync`, `clean`, `report`) when NineSync: PMM Bridge is installed.
