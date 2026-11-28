# Remaining Middle converter work

- **Rendered Scene.** Match native depth, coverage, and mip response in original Grass. Add verified multisampled resolve and ScriptExternal scene draws for original effects such as WorkingFloorX and TakenFrame. Verify render-target lifetime during redraw, playback, and seeks.
- **Whole-model rendering.** Validate native outlines, ground-shadow fallback, and full-scene self shadows. Compare the remaining high-error pixels on matched object passes.
- **Generated geometry.** Compare complete BeamCharge, ReflectBeam, and Ninja renders with native MME. Verify an original effect that changes connectivity, including winding, normals, UVs, and material slots.
- **Controller bindings.** Test offscreen-owner declarations in their original passes. The original ExcellentShadow accessory resolves 21 previously missing bindings. Continue with full effects and the remaining scene-specific references.
- **Playback and model coverage.** Measure displayed frame times, skipped frames, and process and GPU memory. Investigate the three normal-import stalls before rerunning those model families.

[Validation results](MIDDLE_SOLUTIONS_RESULTS.md)
