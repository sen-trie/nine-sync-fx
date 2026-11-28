# MME compatibility evidence

Two audits on November 22, 2026. The first covered 8 packages and 11 FX entries. The second added 17 downloaded packages and the owner's local MME library (SimpleMagic Vol. 1–4, Nuke2, DDLL, SvDOF2, ToonDX), for 33 packages. Every accessory pair with a same-name `.x` was imported headless through the normal import operator. MMD reference-image comparisons are still pending for every entry, so nothing here is `RENDER_VERIFIED`.

## Results by group

| Group | Entries | Observed | What the check established |
|---|---:|---|---|
| Self-contained accessories (SimpleMagic `lv*`, Thunder, Beam, Gun, Slash, KiraKira, Sakura, Synth, Triangle) | 53 | BUILT | Every import finished with pass objects and no errors. Offscreen color targets written by geometry draws are emulated. Ice_lv3 realizes 3.96 M vertices and Thunder_lv2/lv3 about 0.58 M, so playback cost needs measuring. |
| Accessory plus full-screen passes (Nuke2) | 1 | BUILT, partial | 11 pass objects. 2 blur passes were skipped, and `ScnMap` is emulated from the smoke draws without its blur. |
| GPGPU particles and trails (ParticleEX2, LineParticle, LSv4_5, SlashLine, SimpleMagic `*_Auto`, `*_loop`) | 14 | BUILT but degraded, or empty | Buffer passes compute particle state each frame from the previous frame. The geometry reads that state, which samples as zero here. Pass objects exist but do not move like the MMD effect. LSv4_5 and LineSystem_Blade build nothing. |
| Capture-driven surfaces (StaticWater, MirrorWater, MaskMirror, HitRain) | 14 | BUILT but degraded | The surface samples mirror, height, scene or depth captures that only MMD renders. The plane builds, but its shading inputs are zero. |
| Controller-driven accessory (AutoSmoke2 AS2X1) | 1 | PARSED | Every face is culled at default values. Its look comes from the same-name `AutoSmoke2.pmx` morphs, which a standalone import does not have. |
| Post effects (AutoLuminous, LightSampling, SoftParticleEngine, DDLL, SvDOF, LightBloom, Hatching, FXAA, FilmGrain, SimpleGlass, f2nd) | 40 | PARSED, no geometry | The `.x` only starts full-screen passes. An import correctly produces no pass objects and lists the skipped passes. |
| Model and material shaders (ToonDX, NewToon, MMDShaderTemplate, AutoLuminous `full_*`, Hatching 0.x, MMDF2ND `material/*`, SvDOF_DepthOut, same-name PMX effects) | 33 | PARSED | No model-material target exists yet. Five of them also declare a post pass and would be partial. |
| Unclassified entries (`ZCreateObject`, `KeepEdge`, `NoEdge`, HDR variants) | 9 | PARSED | Helper effects assigned by hand in MMD. The scanner cannot infer their target. |

The 165 entry effects are counted once each. Include fragments and offscreen companions are listed in the inventory with their role but are not counted. The per-effect stage, prediction, blockers and build evidence are in the [inventory](mme_effect_inventory.json).

## Third batch: 28 BeamMan packages

These were chosen for P1 and P2. All 50 accessory imports finished.

- **Built:** LineBillboard and its samples, Burner, the four Thunder variants, BillBoard, NamePlate, 17 Fireworks variants, MasterSpark and BeamMagnum.
- **Hidden-pass particles (BUILT but degraded, or empty):** Line_v2, WaterParticle, RingParticle and Grass.
- **Grass for MikuMikuMoving** stops at the declaration of `SkinOut`. The package expects MMM to supply the `MMM_SKINNING_OUTPUT` type and `MMM_SkinnedPositionNormal` function. It also uses MMM light arrays and the host's self-shadow function. Supporting this variant requires those inputs and operations.
- **ReflectBeam (MMM)** is driven by a PMX controller's centre bone and is empty without the controller.

### November 23 retest

The four HitEffect variants and BeamMagnum_Thunder previously built zero passes. Headless Blender 5.3 gave the following results after the importer changes.

| Accessory | Reproduction | Evaluated result |
|---|---|---|
| Hit and Hit front-display | Import with Tr at 1, then key it to 0.5 and 0 | Four passes per accessory. All positions collapse at 1, all four passes expand at 0.5 (2,280 vertices total), and geometry is empty at 0. |
| Hit_EX and Hit_EX front-display | Same animation | Four passes per accessory, with 804 vertices total at Tr 0.5 and empty geometry at 0. |
| BeamMagnum_Thunder | Move the camera from the barrel axis to a side view after import | Both passes retain 247 faces. Maximum vertex alpha supplied to each pixel shader changes from 0 to 1. |

A 128 by 128 Cycles smoke render of Hit produced 2,950 pixels with an RGB channel above 0.01 at Tr 0.5 and zero at both endpoints. The original BeamMagnum_Thunder still rendered black from the side. Its pixel shader takes all its color from `Body_thunder.png`, a 90-frame APNG bound through the unsupported `ANIMATEDTEXTURE` semantic. In a temporary copy, binding frame zero as an ordinary PNG produced side-view pixels with the same camera and shader math. The regular BeamMagnum also reads this texture as an overlay on a static beam. The scanner now lists the unsupported texture on both entries.

The culler now samples accessory opacity for passes that read material diffuse color. When a gated vertex shader reads camera inputs, a small sample of vertices is checked from three directions. If a gated output changes, the pass keeps its geometry for the live shader. These samples cover the reported beam and do not exhaust every viewing angle or animation state.

### Import timings

Median build times in seconds across three paired runs in Blender 5.3. Each cold run used a fresh effect cache. A second Blender process reused that cache for the warm run.

| Effect | Cold before | Cold after | Warm before | Warm after |
|---|---:|---:|---:|---:|
| Nuke2 | 11.390 | 11.843 | 6.551 | 6.606 |
| Thunder_lv1 | 3.911 | 4.047 | 1.616 | 1.641 |

Nuke2 built 11 passes and Thunder_lv1 built four throughout. Warm imports reused all recorded cache entries. These timings cover import and compilation.

The Blender suite passed 44 scripts, with one failure and zero skips. The new visibility regression passed in a separate run on both the Geometry Nodes and Python backends. The untouched snapshot reproduced the sole suite failure, Thunder_lv2's expected reuse of strip samples after editing Tr. The first strip pass evicted eight matching entries from the 256 MiB session cache before the next pass looked them up. The warm and cold meshes matched.

## What this changed

- The importer now records `mme_unsourced` on the effect root and shows it in the Effect Info panel. A build whose inputs come only from buffer passes or scene captures is no longer reported as a plain success (`fx/params.py`, `fx_unsourced_reads`).
- A texture name with a leading separator, such as `"\mask.png"` in StaticWater, now resolves beside the effect instead of at the drive root (`fx/textures.py`, `fx_find_file`).
- The analyzer was corrected against the build results. See the [taxonomy](MME_EFFECT_TAXONOMY.md) for the rules.

## Provenance and terms

BeamMan packages come from `BeamManP/BeamMan-MME-Archive` releases (tags `BeamMan`, `BeamMan2` and `BeamMan4`, commit `e9eebf1b`). Each archive's MD5 matched the archive ledger. The archive's terms (2023-11-12) allow redistribution and modification unless a bundled text says otherwise. None of the bundled texts did. Simple-Glass has no licence file. LightBloom and Hatching are MIT, but LightBloom's dirt texture is CC BY-NC-SA 3.0. MMDF2ND is MIT and asks for credit. The local library is the owner's and was used read-only. None of these files are committed or redistributed. Each package's source URL, revision and SHA-256 are in the inventory.

## Reproducing

```sh
python -I tests/t_mme_analyzer.py
python tools/run_tests.py --blender "/path/to/blender" --only mme_triangle mme_visibility strip_stream
python -m tools.mme DIRECTORY --probe --source IDENTIFIER
```

The build observations came from Blender 5.3.0 (`02f0899d78e3`) in a factory-startup profile with a temporary user library. Each `.x` was imported at scale 1.0 and then evaluated at frames 10 and 40. Extract BeamMan archives with CP932 filename decoding.
