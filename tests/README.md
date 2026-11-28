# Tests

Run the FX suite from the repository root with Blender 5.3 or newer.

```sh
python tools/run_tests.py --blender "/path/to/blender" [--fixtures DIR]
```

The runner uses a temporary Blender profile. Use `--only mme_visibility xparse` to select scripts by name. The runner reports a failure for an assertion, a traceback, or a `FAILED:` line. It lists any tests skipped for missing required fixtures. Run `python tools/check_release.py` to verify release metadata and hook parity.

| Environment variable | Use |
|---|---|
| `NINESYNC_FX` | Override the FX package folder. The runner defaults to this repository. |
| `NINESYNC_FIXTURES` | Override the fixture folder. Start with a copy of `tests/fixtures` so it includes the shipped assets. |
| `NINESYNC_THUNDER_LV2` | Point to local `Thunder_lv2.fx` and `.x` files to exercise the gate cache. The X parser test also compares any text meshes it finds there. |
| `NINESYNC_X_BINARY` | Provide a binary X file for the optional importer comparison when a DirectX X add-on is enabled. |

The manual controller and Tr-zero tests require third-party Thunder fixtures. Copy `Thunder_lv1.fx` and `Thunder_lv1.x` into `Thunder/` in your external fixture folder. The gate-cache test checks the local Lv2 fixture when available.

The suite checks rendered output, keyed opacity, view-dependent geometry, MME package diagnostics, X parsing and importer fallback, PMM sync hooks and accessory keys, node edits, strip and gate caches, and shared bakes.
