# Changelog

## 0.2.0

- Split from the combined NineSync. Runs without NineSync: PMM Bridge.
- Settings moved to `scene.ninesync_fx`; operators renamed to `ninesync_fx.*`; panels renamed `NINESYNC_FX_PT_*`.
- Own PMM read, model links, fps retime (vendored PMM parser) and a Support Report / Clean for accessories.
- Repair Legacy Data operator for files saved with the combined add-on.
- Shared node library is copied to the user extension folder when installed as an extension, so updates do not lose it.
- Hook registry (`post_sync`, `clean`, `report`) when NineSync: PMM Bridge is installed.
