# Changelog

## Unreleased

- Removed FX's own PMM support: the vendored `pmm.py`, `ninesync_fx.read_pmm`, `ninesync_fx.sync_accessories`, the PMM file field, model-link list and `status` setting. PMM accessory sync now comes only from NineSync: PMM Bridge through the `post_sync` hook. Standalone `.x` import and `.fx` conversion are unchanged.
- Internal: the large modules were split into packages (`common`, `xfile`, `fx`, `nodes`, `runtime`, `accessories`, `standalone`, `ui`) with one-way imports; `mme_core.blend` moved to `data/`. Behaviour is unchanged.
- Functions specific to this add-on now carry short comments explaining why they work the way they do.

## 0.2.0

- Split from the combined NineSync. Runs without NineSync: PMM Bridge.
- Settings moved to `scene.ninesync_fx`; operators renamed to `ninesync_fx.*`; panels renamed `NINESYNC_FX_PT_*`.
- Own PMM read, model links, fps retime (vendored PMM parser) and a Support Report / Clean for accessories.
- Repair Legacy Data operator for files saved with the combined add-on.
- Shared node library is copied to the user extension folder when installed as an extension, so updates do not lose it.
- Hook registry (`post_sync`, `clean`, `report`) when NineSync: PMM Bridge is installed.
