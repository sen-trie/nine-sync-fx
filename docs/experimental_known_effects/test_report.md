# Known MMEffect adapter experiment report

## Outcome

**Continue on the experiment branch.** Exact package recognition, explicit apply and native Scene Effects are practical. The o_Tonemap path exposes an independent tonal adaptation with an editable strength control. The rendered comparison shows visible tone-curve differences. Broader named support needs additional version, behavior and provenance checks.

## Branch integrity

| Item | Verified value |
|---|---|
| Worktree | D:/Projects-D/nine-sync/nine-sync-fx |
| Experiment branch | experiment/known-mme-adapter |
| Original source branch and commit | master at 8153583d9794cc3b8623b09b6a44c74f22471bd2 |
| Current-changes baseline | a6b2ab4ee502854202e6b8723fb7e170ed4c2f38 |
| Implementation | 491864965d6cf60329fbd5e755c6edd8f2c91fe8 |
| Report revision | The commit containing this report. Use `git rev-parse HEAD` from the completed branch. |
| Verified release refs | release-prep-0.7.0 and origin/release-prep-0.7.0 each point to c615bfd141747c79008312411122e16ca0c0268c. Master and origin/master each point to 8153583d9794cc3b8623b09b6a44c74f22471bd2. |

The maintainer explicitly selected the existing branch based on the current changes, so implementation stayed in that checkout. The clean baseline was recorded before code changes. Later Nuke2 and Thunder comparisons used an external archive of the baseline. The implementation and evidence commits are local. Check the complete file list with `git diff --name-status a6b2ab4..HEAD` and the final status with `git status --short --branch`.

## Gates

| Gate | Result | Evidence |
|---|---|---|
| P1 | Pass | Bounded local scanner, exact manifests and original fixtures. Classifies accessory, looped particle, material assignment and post controller separately. Unexpected resources or malformed input produce diagnostics. |
| P2 | Pass | Strict registry and six outcomes. Exact matches bind to the selected entrypoint and complete raw package. Structurally similar sources and differently named or changed entries receive diagnostics. |
| P3 | Pass | Native tonal Scene Effect with Exposure, Contrast, Saturation and Strength. Neutral and zero-strength renders equal the baseline. The GUI verified rebuild, undo, redo and editable nodes. |
| P4 | Pass for independent adaptation | Creator archive and README verified, original MMD references captured, opt-in named offer exercised. Manual Tr-to-Strength mapping. The bitmap curve and other original settings remain outside coverage. |
| P5 | Pass | Independent brightness-based bloom. Both stack orders were exercised in Cycles. Transparent test alpha stayed unchanged and the opaque test showed a highlight halo. |
| P6 | Pass | Cold extension install starts with the preference off and both experimental operators unavailable. Analyze leaves the scene alone. Opt-in controls expose the proposal, confirmation and effect lifecycle. Existing imports use their usual operator. |
| P7 | Pass | Complete regression suite, paired MMD and Blender renders, separate GUI evidence, timings and rights review. |

## Implementation inventory

- `experimental_adapters/scan.py` uses the existing FX preprocessor, parser, annotation and draw-analysis code and the existing text .x reader. `fx/mme_inspect.py` moves the capability classifier into a location shared by the existing CLI and the Blender add-on. `fx/preprocessor.py` accepts a reader for bounded experiments while retaining the existing default.
- `experimental_adapters/registry.py` and `registry.json` provide validated identities and recognition policy. The JSON contains package metadata and SHA-256 digests. It contains no shader or preset data.
- `experimental_adapters/compositor.py` builds native Scene Effect wrapper and core groups. `experimental_adapters/ui.py`, `ui/__init__.py` and `ui/preferences.py` expose the default-off standalone UI and operators. Geometry scheduling, generic shader compilation and cache keys have no adapter hooks.
- The two original files in `tests/fixtures/mme/known/`, `.gitattributes`, the three `tests/t_adapter_*.py` scripts, `tools/run_tests.py` and this documentation support reproducibility. The fixture byte format is fixed across Git checkouts. `tools/mme/package.py` routes its original analysis through the shared classifier.
- Arbitrary offscreen render graphs, selective glow masks, PMX material mutation, original presets and release packaging changes are outside this experiment.

## Recognition results

| Input class | Representative cases | Observed result | Named offer |
|---|---|---|---|
| Exact | Synthetic tonal in original and relocated folders, o_Tonemap v0.7 selected through .fx and .x | Verified exact | Yes, explicit apply |
| Exact with identical basename | Two separately registered synthetic entrypoints in different subdirectories | Each resolved to its own entry | Yes, each selected target only |
| Edited and structurally similar | Comment append, parameter change, unrelated shader logic with the same structural features, changed include | Family candidate | None |
| Unknown or different sibling | Empty technique, multipass synthetic bloom, original o_Plain sibling | Unknown or family candidate according to features | None |
| Incomplete | Lost include or texture, absent registered bitmap, malformed FX | Incomplete with diagnostic | None |
| Input rejected by limits | Include cycle or escape, external symbolic link, oversized file, excessive preprocessor condition | Incomplete with diagnostic | None |
| Disabled policy | Review-pending, incompatible, duplicate-key registry | Review blocked or incompatible | None |

The nine recognition tests include the real package, one-byte variant, duplicate names, package relocation, missing file and corrupt registry cases. The negative cases yielded zero named offers. Exact hashes establish package identity. Verification of rendering behavior uses separate evidence.

## Blender lifecycle

| Operation | Observed result |
|---|---|
| Analyze and repeat analyze | Identical report with object, material, node group, scene and image counts preserved. |
| Apply | New stage appended to the chosen scene and two native node groups created. Existing color stage remained first. |
| Reapply | Same stage returned for the same adapter, source and scene. Existing values persisted. |
| Rebuild | Changed the generated core. Preserved the wrapper link count, user-added frame and Strength. The rendered image matched the pre-rebuild render. |
| Forced construction error | Stage stack and datablock counts matched the pre-apply state. |
| Undo and redo | The separate GUI process restored and reapplied an explicit core rebuild. |
| Disable and remove | Both enable flags cleared on disable. Removing the effect kept the prior color stage and restored the baseline image for the tonal test. |
| Save and reopen with add-on disabled | Native node groups and effect values were present. The resulting image matched the earlier adapted image. |
| EEVEE rendered viewport | Separate GUI captures show the native controls and a rendering difference with preview toggled. The compositor editor displayed the editable wrapper tree. |

## Rendering and cost

Blender 5.3.0 build 02f0899d78e3 was tested with isolated profiles. The headless fixture uses one orthographic camera, three self-lit swatches, Cycles CPU with four threads and one sample, 256 by 128 pixels and Standard view transform. The tonal case used Exposure 0.65, Contrast 8, Saturation 0.25 and Strength 0.8. Mean absolute change in normalized Blender-loaded RGB PNG samples was 0.05225 for tonal versus baseline. The two bloom orders differed by 0.00608; transparent test alpha matched pixel for pixel. The opaque frame shows the bright swatch halo. Transparent output can conceal glow behind zero alpha.

For the named effect, the same original 640 by 360 RGB chart was displayed inside MMD 9.32 x64 and rendered using an emissive plane in Blender. In MMD the original o_Tonemap v0.7 used its Default bitmap and Tr 0.65 and 1.0, with the viewport and chart otherwise fixed. In Blender the native stage used Contrast 12 and Saturation 1.12, with Strength 0.65 and 1.0. Ten MMD sample channels that shifted by at least 8 of 255 landed between 0.643 and 0.667 of their full shift at Tr 0.65. This supports the blend-control mapping. Display sampling and the tonal curves differ between the engines, so the experiment uses the side-by-side image for appearance and separate measurements for controls.

| Measurement | Observed |
|---|---|
| Synthetic analysis | About 2.7–4.4 ms mean across ten scans in two headless runs. |
| Named Apply | About 19–23 ms including the confirm-time package rescan and the build. The earlier direct graph-build measurement was about 1.3 ms. |
| Graph size | Tonal uses three wrapper nodes plus six core nodes. The user-edit lifecycle fixture adds one wrapper frame. |
| Tiny scene render, median of five warm frames per state | 0.183/0.189 s and 0.198/0.204 s, disabled/enabled across two runs. |
| Nuke2 generic import | Two serial cold imports per revision. Baseline 19.75 and 20.54 s. Prototype 17.24 and 20.66 s. Each produced 11 geometry passes. The timing ranges overlap. |
| Memory and playback | Not quantified. |

Evidence is stored outside the repository in `D:/tmp/ninesync-adapter-evidence`. The renders directory includes `mme-blender-chart-comparison.png`, `comparison.png`, `opaque-bloom-comparison.png`, `interactive-preview-off.png`, `interactive-scene-effects.png`, `interactive-editable-nodes.png` and `scene-effects.blend`. It also includes individual frames. The local MMD/Blender comparison uses original swatches and charts. Creator assets remain in the separate local unpacked package.

## Regression and distribution

The baseline suite initially passed 25 scripts, with two Thunder Lv1 scripts pending fixture discovery. The two scripts then passed against the baseline archive. The prototype suite with original fixtures passed **29 scripts, zero failures, zero skips**. The dedicated Blender adapter render suite was rerun after adding the opaque bloom case. Nuke2 produced 11 geometry passes across both revisions. Thunder Lv2 gate-cache, Thunder Lv1 controller and Tr-zero, synthetic particle stages and the textured accessory smoke import all passed.

A local extension ZIP built, validated and cold-installed into a separate profile. The extension UI and effects remained opt-in during the test. The ZIP and creator packages are outside version control. [Research matrix](research_matrix.md) and [rights review](rights_review.md) contain the creator attribution, original distribution evidence, review scope and remaining questions.
