# Tests

Headless Blender tests. Each `t_*.py` is a script; run it with the Python that has the `bpy` wheel (`pip install bpy==5.2.3`, the newest wheel when these were written; it lacks a few Blender 5.3 nodes, `shim52.py` stands in for them).

```
cd tests
NINESYNC_FIXTURES=/path/to/fixtures python3 t_standalone.py
```

`python tools/run_tests.py --blender <blender 5.3+> [--fixtures DIR]` runs the whole list one test at a time in a throw-away Blender profile. It marks a test FAIL on a traceback or a `FAILED:` line, and SKIP, with the reason, when a fixture is missing. Skipped tests are pending, not passed. Output has local paths and the user name scrubbed. `python tools/check_release.py` is the static check (versions, licence, exclusions, hooks parity with `NINESYNC_CORE`, private paths; extra terms to refuse come from `NINESYNC_PRIVATE_TERMS`). Most `t_*.py` scripts print values and only fail loudly on an error, so PASS means "ran without error", not that every printed value was compared.

`plain/plainthing.x` is a synthetic cube made by `tools/make_plain_fixture.py`. The Thunder files are third-party (SimpleMagic) and are not in the repository: copy `Thunder_lv1.fx/.x` from your own MMD effect folder into `<fixtures>/Thunder/` and keep that folder outside the repository.

Environment:

| Variable | Meaning |
|---|---|
| `NINESYNC_FX` | FX package folder (default: this repo; `nine-sync-fx`, `ninesync-fx` and `ninesync_fx` are recognised) |
| `NINESYNC_CORE` | core package folder (default: sibling `nine-sync-core`, `ninesync-core` or `ninesync`) |
| `NINESYNC_FIXTURES` | folder with `Thunder/`, `Synth/`, `Texture/`, `plain/`, `meiling.pmm` (core tests only) |
| `NINESYNC_TEST_PMM` | PMM used by `t_core.py` (needs core) |
| `NINESYNC_THUNDER_LV2` | folder with `Thunder_lv2.fx/.x` and `Thunder_lv2_2.fx/.x` for `t_thunder_rings.py` (default `debug-src`; put `Texture/` next to it for real textures) |

`tests/fixtures` ships `Synth/` and `Texture/` only. `Thunder/Thunder_lv1.fx` and `.x` are the third-party SimpleMagic Thunder effect, `plain/plainthing.x` is any plain text `.x` mesh, and `meiling.pmm` is a PMM with three accessories; supply your own copies.

Scripts that use `harness_fx.py` register only FX and print values to compare against earlier runs. Scripts that import both packages: `t_core.py` (`-- core` or `-- both`), `t_panels.py` (`-- core|fx|both`), `t_cycle.py`, `t_import.py` (`-- ninesync ninesync_fx`).

`harness_fx.py` points the user library, and with it the effect cache folder, at a temp folder. `t_emm.py` covers `.emm` effect assignments, `t_accessory_keys.py` accessory key edits, parent switching, Tr and additive materials, and `t_cache.py` the cache folder; each prints `FAILED: none` on success. `t_library.py` covers library upgrade, merge, downgrade, nested groups, backup, failure fallback and the open operator; it prints `FAILED: none` on success. `t_manual_controls.py` covers controller assignment. `t_thunder_rings.py` covers the strip culling fix; its real-effect half runs when the lv2 files are found.
