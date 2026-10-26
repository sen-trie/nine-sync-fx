# Tests

Headless Blender tests. Each `t_*.py` is a script; run it with the Python that has the `bpy` wheel (`pip install bpy==5.2.3`, the newest wheel when these were written; it lacks a few Blender 5.3 nodes, `shim52.py` stands in for them).

```
cd tests
NINESYNC_FIXTURES=/path/to/fixtures python3 t_standalone.py
```

Environment:

| Variable | Meaning |
|---|---|
| `NINESYNC_FX` | FX package folder (default: this repo) |
| `NINESYNC_CORE` | core package folder (default: sibling `ninesync`) |
| `NINESYNC_FIXTURES` | folder with `Thunder/`, `Synth/`, `Texture/`, `plain/`, `meiling.pmm` (core tests only) |
| `NINESYNC_TEST_PMM` | PMM used by `t_core.py` (needs core) |

`tests/fixtures` ships `Synth/` and `Texture/` only. `Thunder/Thunder_lv1.fx` and `.x` are the third-party SimpleMagic Thunder effect, `plain/plainthing.x` is any plain text `.x` mesh, and `meiling.pmm` is a PMM with three accessories; supply your own copies.

Scripts that use `harness_fx.py` register only FX and print values to compare against earlier runs. Scripts that import both packages: `t_core.py` (`-- core` or `-- both`), `t_panels.py` (`-- core|fx|both`), `t_legacy.py`, `t_cycle.py`, `t_import.py` (`-- ninesync ninesync_fx`).

`manual/legacy_blend_make.py` needs the old combined add-on; it writes a `.blend` that `manual/legacy_blend_check.py` then opens with FX only to test Repair Legacy Data.
