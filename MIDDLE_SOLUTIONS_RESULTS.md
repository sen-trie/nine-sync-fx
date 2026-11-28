# Middle converter validation

| Area | Verified evidence | Scope |
| --- | --- | --- |
| ToonDX and BeamMan surfaces | 23 native/Blender whole-model frame pairs averaged 0.62–1.18 RGB levels on shared foreground after matching pixel centers, sampling, and object-pass settings. | Native outlines and shadows were disabled for those comparisons. |
| Material inputs and rig controls | 126 native texture, sphere, and toon morph probes and 15 native bone-matrix probes. The six tested ToonDX presets and six BeamMan surfaces exercised the original PMX textures and morphs. | Geometry, rigs, and source shaders were preserved. These are selected surfaces. |
| Scene execution | Original Grass ran its nine main passes plus offscreen draws at frames 0, 10, 20, and 0. With antialiasing disabled, frame-zero foreground/interior error was 1.07/0.76 RGB levels. The packed output reopened exactly in Blender. | Native mip response, depth, and coverage differences remain. |
| Shadows and history | Original NewToon's 33 exposed ground-shadow pixels matched native MME in two tested configurations. Reset Targets matched eight native still exports in one pulse diagnostic. Self-shadow map diagnostics reached 93.1% and 91.3% coverage overlap for modes 1 and 2. | Coverage is for the map diagnostic. Full-scene self-shadow parity and history behavior across original effects remain open. |
| Deformation and scripted copies | BeamCharge's tested positions agreed with independent execution of its original FX within 0.000001 Blender units. Both Ninja variants issued three repeated object draws in isolated raster tests. | Complete native visual comparisons and general connectivity changes remain open. |
| Original controller bindings | The 51-case BeamMan audit recorded 358 declarations. With original sibling accessories, 293 resolved, 13 were optional absent, and 52 remained unresolved in that run. A follow-up resolved all 21 previously missing ExcellentShadow.x declarations across seven original effects. | Companion resolution verifies binding. It leaves full effect rendering to separate tests. |
| Model coverage | The selected-slot regression passed on 26 of 29 Touhou model families. | Three MMD Tools imports timed out. Displayed frame skipping and GPU memory remain unmeasured. |

## Reproduction

The test scripts take original asset paths as arguments when a creator model or effect is needed. Numerical fixtures are checked in. Start with [converter lifecycle](tests/t_middle_converters.py), [ordered passes](tests/t_middle_render_execution.py), [rendered scene apply](tests/t_middle_render_apply.py), [Grass](tests/t_middle_grass_scene.py), and [BeamMan audit](tests/t_beamman_middle.py). See [remaining work](REMAINING_WORK.md) for the open cases.
