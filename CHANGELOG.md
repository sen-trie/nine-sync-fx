# Changelog

## 0.3.0

- Shared node library: `mme_core.blend` now lives in a user folder that survives updates. New versions of the bundled library replace or merge into it, keeping groups grown from your own effects, with a backup of the previous file.
- Open FX Library .blend button in the Core Node Library panel.
- Translated node groups carry the label `MMEffects` in Shader Nodes and Geometry Nodes (group nodes for the pixel shader and the vertex shader; groups made from HLSL functions and intrinsics read `MMEffects: name(args)`).
- Unresolved effect controllers can be assigned to an object or bone by hand.

## 0.2.0

- Split from the combined NineSync. Runs without NineSync: PMM Bridge.
- Settings moved to `scene.ninesync_fx`; operators renamed to `ninesync_fx.*`; panels renamed `NINESYNC_FX_PT_*`.
- Own PMM read, model links, fps retime (vendored PMM parser) and a Support Report / Clean for accessories.
- Repair Legacy Data operator for files saved with the combined add-on.
- Shared node library is copied to the user extension folder when installed as an extension, so updates do not lose it.
- Hook registry (`post_sync`, `clean`, `report`) when NineSync: PMM Bridge is installed.
