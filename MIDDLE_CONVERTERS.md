# Existing-model MME converters

The Middle experiment adds three Blender destinations for the original HLSL in MME effects: Material Slot, Mesh Deformation, and Rendered Scene. It uses existing Blender meshes, rigs, and materials. See [validation results](MIDDLE_SOLUTIONS_RESULTS.md) and [remaining work](REMAINING_WORK.md).

## Toon validation on real PMX assets

The six original ToonDX presets and six BeamMan surface shaders were tested on one imported Sanae PMX. The tests covered original material inputs, vertex interpolation and texture sampling. These experiments cover selected object-pass conversions. See [validation results](MIDDLE_SOLUTIONS_RESULTS.md).

The toon demo uses actual PMX files. MMD Tools 4.5.14 imports the original geometry, rig, UVs, morphs, and materials. [pmx_toon.fx](tests/fixtures/middle/pmx_toon.fx) uses the same FX source as the single-slot test and accepts explicitly selected slots. Its `use_texture` flag keeps original color-only materials usable.

| PMX | Imported mesh, rig, and keys | Converted slots | EEVEE render checks |
| --- | --- | --- | --- |
| `sanae_model_fixed.pmx` | 15,155 vertices, 23,919 polygons, 274 imported bones, 34 shape keys | 9, including face, skin, hair, and clothing | Toon and keyed controller changed the rendered pixels. Reopen and restoration both matched their references exactly. |
| `Ghost Dance Stage/Discoball.pmx` | 695 vertices, 1,386 polygons, 5 bones, 10 shape keys | 1 untextured material | Toon and keyed controller changed the rendered pixels. Reopen and restoration both matched their references exactly. |

The character PMX refers to six texture files under `tex` that are present under `textures`. The validation script uses that directory for in-memory path resolution. The discoball's original material uses MMD Tools' bundled shared toon bitmap. The saved Blender files include packed images. Enable MMD Tools when opening them so the FX drivers can still read the original PMX material properties. Source PMX hashes were identical before and after the runs.

### Native MMEffect comparison

MikuMikuDance 9.32 loaded the same `pmx_toon.fx` entrypoint used by the Blender renderer. The native assignments were exported again from the live MMEffect scene. The export confirmed nine character material slots and the discoball's one material slot. Both files use the material diffuse property declared with `Object = "Geometry"`.

MMD used a zero-opacity copy of `tests/fixtures/plain/plainthing.x` named `MME Control.x`. Blender used an Empty with the same name. The PNG comparison shows controller X at 1.00 in both renderers. Changing X to 0.68 in MMD changed 84,693 character pixels and 400,406 discoball pixels by more than two 8-bit levels in at least one channel.

## Ordered scene image destination

The Blender apply/remove/retarget operators also expose **Rendered Scene**. It executes one explicit update using the source camera, producing a packed numeric image and saving exact render resources. Under **Render Target History**, **Keep Targets** continues the last successful update and **Reset Targets** starts each apply with fresh color and depth targets. The choice is saved on the assignment. Scripts can set it with `apply(..., reset_targets=True)`. Applying matches the current frame only. **Ground Shadows** runs declared FX shadow passes before the ordinary object draws, using the PMX material casting flags and the chosen **Ground Shadow Brightness**. Scripts can pass `ground_shadows=True` and `ground_shadow_brightness=1.0` to `apply`. The setting is saved with the Rendered Scene assignment. Native fallback shadows for materials without a matching FX pass remain outside this path. **Self Shadows** can draw a fresh 1024 ? 1024 map using declared `zplot` passes and the PMX caster flags. Mode 1 and Mode 2 use the specified **Self Shadow Distance**. Matching PMX `object_ss` passes can sample the map through an unbound pixel sampler at `register(s0)`. The mode and distance are saved with the assignment. Scripts can use `self_shadow_mode=1` and `self_shadow_distance=8875` with `apply`. See [implementation results and native limits](MIDDLE_SOLUTIONS_RESULTS.md).

For an original offscreen effect, pass actual assignments, including meshes used only by offscreen rules. `grass_fx` below is a `pathlib.Path`; all object variables are existing source-scene Blender objects.

```python
from ninesync_fx.runtime.middle.targets import apply, remove, RENDER
from ninesync_fx.runtime.middle.scene_execution import SceneMember

result = apply(grass_mesh, grass_fx, RENDER, scene=scene, units=0.08,
    scene_members=[
        SceneMember(character_mesh, "Marisa-target.pmx", main=False),
        SceneMember(grass_mesh, grass_fx.with_suffix(".x").name, grass_fx, grass_owner),
    ])
image = result.destination
trace = result.core.last_trace
```

Each `SceneMember` accepts `bindings={"tgt.x": actual_controller_root}` for explicit real-object mappings. Reapply can reuse the saved assignments. `remove(grass_mesh, RENDER)` removes generated output/state and preserves source meshes, slots and artist graphs. Invalid declarations, states and bindings leave the previous published result intact. A Rendered Scene destination publishes an image; Material Slot and Mesh Deformation continue to expose their existing node-editing workflows.

## Try the converters

In a Blender 5.3 source install of this branch, select an existing mesh in Object Mode. Use F3 and choose **Apply Experimental MME FX to Model**. Pick **Material Slot** or **Mesh Deformation**, then select an FX file. **Analyze Only** reports candidates and controller references. For the material fixture, choose slot 1 and a light-direction source whose location points toward the intended lit side. For the geometry fixture, supply a matching rig and named controllers or open the saved demonstration. The units field is Blender units per MMD unit. The default is 1. Use the import scale of the PMX mesh when matching MMD units.

The Shader Editor exposes the material's outer graph and its FX inputs. The geometry effect exposes keyable inputs on the modifier and an **Edit Deformation** group in its wrapper. The F3 commands **Retarget Experimental MME Controller** and **Remove Experimental MME FX from Model** work on the selected mesh. For material removal or retargeting, make the affected slot active.

Imported MMD materials also get one **MME FX Vertex Inputs** modifier shared across their converted slots. Keep this modifier after skinning and other deformation. Its Geometry Nodes graph exposes the generated vertex groups and their keyable parameters. A parameter used by both VS and PS has one input in this graph; the pixel shader reads its exported value. Parameters used only by PS remain in the Shader Editor. Rebuild preserves the surrounding graphs and values when the interfaces match. The Python API enables this stage automatically for MMD materials; `interpolate_vertices=True` explicitly enables it for other materials.

The Python entry points are in [runtime/middle/targets.py](runtime/middle/targets.py). `apply` accepts an explicit slot, scene, source image, light-source object, modifier index, and a map from FX names to scene objects. `inspect_bindings` reports stored mappings and missing sources. `retarget` preserves the compiled graph and changes the driver variables. To rebuild artist-edited wrappers, the generated cores must keep the same socket interface. A changed interface or reassigned slot leaves the last working effect in place. Removal restores the original slot link and material or removes the owned modifier. An edited geometry stage is retained as a reusable group.

The FX fixtures use independently authored source. See [model_toon.fx](tests/fixtures/middle/model_toon.fx), [model_wave.fx](tests/fixtures/middle/model_wave.fx), and the rejected [screen.fx](tests/fixtures/middle/screen.fx).

## Implementation

| Module | Responsibility |
| --- | --- |
| `runtime/middle/__init__.py` | Houses the experimental destination modules. |
| `runtime/middle/analysis.py` | Uses the shared FX loader, technique selector and draw planner for structural candidates, render dependencies and selected-subset controller requirements. Stores a digest of the preprocessed source files. |
| `runtime/middle/compiler.py` | Feeds mesh attributes, material properties, time and controllers to `FxExec` and `fx_run_entry`. Emits source-derived Shader Nodes through `MmePsBackend` and a source-derived position field through `MmeGeoBackend`. |
| `runtime/middle/vertex.py` | Evaluates original material VS expressions on mesh corners, packs exported values into shared per-model attributes, and manages the common geometry stage without changing source mesh data. |
| `runtime/middle/position.py`, `scene_inputs.py` | Proves the final camera projection and recovers its original local-position input. Reuses the generic live camera backend for camera-dependent vertex calculations, including perspective, orthographic and shifted lenses. |
| `runtime/middle/controllers.py` | Resolves `CONTROLOBJECT` references to scene IDs and targets native Blender drivers at object transforms, pose bones and shape keys. Checks recorded bindings and supports retargeting. |
| `runtime/middle/instances.py` | Converts the shared draw planner's nested loop descriptors into evaluated mesh copies and per-instance shader counters. |
| `runtime/middle/images.py` | Creates isolated Non-Color views of original encoded texels, preserving source images and removing unused generated views. |
| `runtime/middle/material_morphs.py` | Reads existing MMD Tools bindings, separate direct/group contributions and original offset data into native drivers and scalar VS/PS expressions; validates source structure. |
| `runtime/middle/targets.py` | Builds new cores before assigning them. Owns object-local material overrides and GN wrappers on the original mesh. Manages reimport, rollback, stable artist links, and removal. |
| `nodes/backend_groups.py` | Allows the experiment to inline helper functions using the shared backend. This keeps temporary shader compilation and discarded projection helpers out of the global group store. The accessory backend still uses shared groups. |
| `ui/operators/middle.py`, `ui/__init__.py` | Register named experimental commands for analysis, apply, remove, and retarget with Blender undo. |

## Compatibility

| Feature | Status | Verified behavior or limitation |
| --- | --- | --- |
| One `MMDPass=object` material draw | Supported | Explicit slot and subset selection, isolated from objects sharing the same source mesh. The real FX PS output supplies color and alpha to an editable Blender material. |
| Base image and UVs | Supported | Original MMD Tools material base image or one explicit image, model UVs, point or linear filtering. The test resolves two different texels. |
| Toon lighting and material semantics | Partial | Original ToonDX and BeamMan sources render on the real PMX. Bound material morphs and separate texture/sphere add/multiply inputs feed both stages. Native checks verify ordinary-pass identity, explicit object_ss coefficients and toon RGB/alpha conversion. Sampler reads stay raw; FX code applies coefficients. Exact renderer parity remains incomplete. See [texture-morph results](MIDDLE_SOLUTIONS_RESULTS.md). |
| Explicit object_ss input profile | Partial, Python API opt-in | `apply(..., pass_type="object_ss")` selects tested model inputs without native shadow rendering. Default shadow-only rejection remains; used native shadow flags, depth maps and unsupported pass states remain rejected. |
| PS varying inputs | Supported for tested float scalar/vector outputs | MMD materials evaluate their original VS in Geometry Nodes and store values on mesh corners before interpolation into the PS. UV seams, signed values, values above one, shared parameters, controllers, live WORLD/VIEW and camera projection inputs and bone animation were checked. Unprovided light projections and unavailable used sources remain errors. Ordinary materials retain the earlier surface-evaluation path unless interpolation is explicitly enabled. |
| Intermediate texture passes | Partial | Texture-only RGBA8 writers such as the four buffered ToonDX presets render into packed intermediate images. Reapply refreshes changed source pixels or render dimensions. Until reapply, a render-size change uses live inline coordinates. Blender supplies final mip and anisotropic filtering. The numeric renderer independently supports explicit and implicit RGBA8 target mip reads. The material path requires full raster coverage, a single COLOR0 output, a single write per target, and a verified order of dependencies. |
| Existing-mesh position deformation | Partial, including original source | The adapter can prove the final `mul(local_position, WORLDVIEWPROJECTION)` and recover its object-space input with homogeneous W=1. Original BeamMan FireBall runs without source annotations on its supplied PMX, including camera-dependent texture displacement. Explicit `NineSyncLocalPosition` outputs remain supported. Original rig, morphs, slots and mesh data survive. See [position-recovery results](MIDDLE_SOLUTIONS_RESULTS.md). |
| Material deformation at shared vertices | Partial | Evaluated corner splitting permits different positions across material/UV seams while preserving source data and corner normals. Tested on two real Sanae slots sharing 54 source vertices, with bone and morph animation. Splitting increases evaluated vertex count. |
| Clip-space edits | Partial | XY edits that leave the proven projected Z and W unchanged use a live inverse camera matrix. Arbitrary depth/W edits and other unproven projection forms remain rejected. |
| Topology and instance loops | Partial | One geometry draw with nested `LoopByCount`/`LoopGetIndex` duplicates the animated mesh or selected material faces. Counts can be keyed, including zero. Other topology generation, multiple geometry draws and looped render targets remain unsupported. |
| Live controller bindings | Supported for the tested subset | Existing position, bone-position and shape-key bindings plus accessory world matrices, Euler and quaternion rotation, Si/Tr tracks, default offset/scale and boolean render visibility. Bound non-vertex morphs can read MMD Tools' existing unified sliders; native probes verify group and material-morph values. Missing optional bool references fold to false and invalidate when the object is subsequently loaded. Typed mappings survive retarget/rebuild/reopen. |
| Bone matrix controllers | Supported for the tested subset | Named float4x4 inputs follow evaluated pose-bone and armature transforms with native rest-orientation removal, row/axis conversion and source-unit translation. Original MMD names survive Blender renames; rest orientation refreshes through live drivers. Native probes, constraints, retargeting and exact render reopening pass. See [bone results](MIDDLE_SOLUTIONS_RESULTS.md). |
| Other MME interaction | Unsupported | Unbound custom morph routing, collisions, and missing required scene objects. Collection/view-layer visibility is not represented by the render flag. |
| Screen and custom draw requirements | Unsupported beyond the buffer subset | The material and mesh destinations reject unsupported surface states and arbitrary scene scripts. Rendered Scene supports selected ordered passes. ScriptExternal and multisampled target resolves remain open. |

The MME reference distinguishes scalar accessory panel values, named morphs, and vector/matrix bone placements. This experiment uses Blender local transforms for panel translation, world positions for pose-bone signals, and the XZY axis conversion used by the PMX importer. MMDX12's separate surface and world effect targets informed the destination split. [MMEffect semantics](https://kamedesuyo.github.io/MME_REFERENCE/sections/section2.html), [MMDX12 porting guide](https://mmdx.codingbot.kr/en/docs/shader-packs/porting-mme/), [MMD Tools PMX importer](https://github.com/MMD-Blender/blender_mmd_tools/blob/main/mmd_tools/core/pmx/importer.py), [MMD Tools material properties](https://github.com/MMD-Blender/blender_mmd_tools/blob/main/mmd_tools/properties/material.py)

The unmodified [MMDShaderTemplate](https://github.com/Manashiku/MMDShaderTemplate) `material.fx` at `456d599795fdd5dc166e97d6dff1fc03beee7336` parsed and reached material compilation. The prototype rejected its required `TEXCOORD1` on the single-UV target without changing the model. Its source also uses four-component additional coordinates, world/view math, sphere and toon maps, and shadow sampling. No third-party effect or model files are included.

## Compared with generic FX

| Feature | Generic accessory path | Material target | Geometry target | Controller service |
| --- | --- | --- | --- | --- |
| Inputs | `.x` geometry, `.fx`, textures and optionally PMM data | `.fx`, existing material slot | Recoverable original or annotated `.fx`, existing animated mesh | FX declarations and Blender scene IDs |
| Output geometry | Creates generated pass geometry | Reuses original mesh | Sets positions in a modifier on the source mesh | Feeds M and G |
| Original PMX data | Accessory pipeline owns a separate object/pass stack | Original material and data slot retained, object-local override | Original mesh, skinning, UVs and shape keys retained | Reads animation without replacing actions |
| Editable output | Generated passes and user node frames | Material wrapper and exposed FX inputs | GN modifier sockets and edit stage | Native driver targets and explicit retarget |
| Update | Existing incremental build | Changed source replaces the inner core | Same modifier and edit stage survive | Retarget without recompiling |
| Unsupported renderer dependencies | Generic draw planning may skip unsupported screen passes | Rejects the destination and keeps the prior material | Rejects the destination and keeps the prior stack | Reports missing or ambiguous binding |

Frame 10 evaluated mesh coordinates and polygon counts produce matching SHA-256 digests for each paired ordinary import.

| Accessory | Passes | Vertices | Matching hash prefix | Archived release-prep | Experiment |
| --- | ---: | ---: | --- | ---: | ---: |
| Synth | 2 | 245 | `eedd233e72fd9d18` | 1.520 s | 1.547 s |
| Thunder Lv1 | 4 | 990 | `0367fe5cf752ce4c` | 4.192 s | 4.321 s |
| Nuke2 | 11 | 661,162 | `b907ebb7883f356e` | 13.099 s | 13.160 s |

One fresh process was used per revision and sample. These are regression observations. The existing-model timings below cover different inputs and cannot estimate a speedup over the accessory path.

| Measurement | Observed |
| --- | ---: |
| Shared warm FX parse | 0.035 ms |
| Experimental warm analysis including content check | 0.499 ms |
| Apply material on the 2-slot mesh | 71.473 ms |
| Apply geometry modifier | 50.212 ms |
| Evaluate original mesh and read 6 positions | 0.201 ms/frame |
| Evaluate animation, GN and drivers and read 6 positions | 0.496 ms/frame |
| Force GN core rebuild | 46.638 ms |
| Warm unchanged GN apply | 0.681 ms |

The frame values are means over 60 calls in the fixture. The saved material and deformation cores contain 53 and 23 nodes respectively. The two cores use 12 native drivers. The observed maximum position error was `1.87e-08` Blender units. Three apply/remove cycles restored the material/group/driver/mesh/object counts to `(0, 0, 0, 1, 6)` after setting aside the intentionally preserved artist stage. These tests do not quantify memory usage or full-size model playback.

## Executed verification

Windows, Blender 5.3.0 `02f0899d78e3`, isolated resource directories, source add-on, Cycles CPU, four render threads, 96×48, one sample. A separate headless EEVEE run reopened the saved combined scene. The real PMX runs used MMD Tools 4.5.14 and headless EEVEE at 800?800.

| Checks | Result | Evidence |
| --- | --- | --- |
| A-01 | Pass | Structural dispatch, subset, screen requirements and controller inventory. |
| M-01, M-02, M-03 | Pass | Rendered two UV texels with controlled light and animated inputs. Original geometry tracks still match the baseline. |
| M-04, M-05 | Pass | Connected artist edit survives a source edit, rebuild and reopen. Rejected compile, interface, missing-image and RT changes leave the last good material. |
| G-01, G-02, G-03, G-04 | Pass | Numerical frame and keyed amplitude checks, explicit pre/post-armature placement, original rig and shape keys, connected edit stage preserved. |
| C-01, C-02, C-03 | Pass | Material and GN values follow keyed Empty, bone and shape keys, including backward scrubbing. |
| C-04, C-05 | Pass | Missing, renamed and ambiguous controls, explicit retarget, persistence on reopen, and no extra handler registrations. |
| R-01, R-02 | Pass | Generic Synth, Thunder and Nuke2 imports tested directly against the archived base. |
| R-03 | Pass | Three repeated apply/remove cycles and stable datablock counts. |
| REAL-PMX-TOON | Pass on both assets | Applied to original materials and checked rendered change, native controller animation, stable reimport, exact restoration and exact reopen. |
| U-01 | Pass | Blender undo and redo through the experimental apply operator. |
| EE-01 | Pass | Headless EEVEE readback from the reopened native scene at frame 10. RGB `[0.0899658, 0.0539856, 0.0539856]` for target `[0.09, 0.054, 0.054]`. |
| Existing regression suite | 42 pass, 0 fail, 1 skip | `core_fx` requires unavailable `meiling.pmm`. The targeted generic suite also passed 6 scripts on each revision. |

Run from the experimental checkout. Set `$fixtures` to the regression fixture directory containing `Thunder` and `$nuke2` to the Nuke2 accessory path when available. The material/deformation tests can run without the optional real shader corpus by omitting `--corpus`.

```powershell
$blender = 'D:/SteamLibrary/steamapps/common/Blender/blender.exe'
$mmdTools = Join-Path $env:APPDATA 'Blender Foundation/Blender/5.3/extensions/blender_org/mmd_tools'
$corpus = Join-Path $results 'MMDShaderTemplate/material.fx'
$env:BLENDER_USER_RESOURCES = Join-Path $results 'middle-tests-resources'

& $blender -b --factory-startup --python-exit-code 1 --python tests/t_middle_converters.py -- --output (Join-Path $results 'demos') --corpus $corpus
& $blender -b --factory-startup --python-exit-code 1 --python tests/t_middle_undo.py

$env:NINESYNC_FX = (Get-Location).Path
$env:NINESYNC_FIXTURES = $fixtures
python -u tools/run_tests.py --blender $blender
& $blender -b --factory-startup --python-exit-code 1 --python tests/t_middle_accessory.py -- tests/fixtures/Synth/synth.x --passes 2
& $blender -b --factory-startup --python-exit-code 1 --python tests/t_middle_accessory.py -- (Join-Path $fixtures 'Thunder/Thunder_lv1.x') --passes 4
& $blender -b --factory-startup --python-exit-code 1 --python tests/t_middle_accessory.py -- $nuke2 --passes 11
```

For the actual PMX renders, run the following in Blender 5.3 with a separate resources directory.

```powershell
$env:BLENDER_USER_RESOURCES = Join-Path $realResults 'repro-profile'
& $blender -b --factory-startup --python-exit-code 1 --python tests/t_middle_pmx.py -- --mmd-tools $mmdTools --model (Join-Path $bridge 'sanae_model_fixed.pmx') --texture-dir (Join-Path $bridge 'textures') --slots 0 1 11 12 13 15 16 17 18 --output (Join-Path $realResults 'sanae')
& $blender -b --factory-startup --python-exit-code 1 --python tests/t_middle_pmx.py -- --mmd-tools $mmdTools --model (Join-Path $bridge 'Ghost Dance Stage/Discoball.pmx') --slots 0 --output (Join-Path $realResults 'discoball')
```

The `sanae` and `discoball` results each contain `toon.blend`, `before.png`, `toon.png`, `toon-controller-frame12.png`, and `evidence.json` with the original PMX hash and measured render comparisons.

The EEVEE check reopened the saved demo and validated the same top texture swatch at frame 10.

```powershell
$check = @'
import bpy
from pathlib import Path

scene = bpy.context.scene
scene.render.engine = 'BLENDER_EEVEE'
scene.render.image_settings.file_format = 'OPEN_EXR'
scene.render.image_settings.color_depth = '32'
scene.render.filepath = str(Path(bpy.data.filepath).with_name('eevee-check.exr'))
scene.frame_set(10)
bpy.ops.render.render(write_still=True)
image = bpy.data.images.load(scene.render.filepath)
offset = 4 * (32 * 96 + 61)
rgb = tuple(image.pixels[offset:offset + 3])
print('EEVEE swatch', rgb)
assert all(abs(got - want) < 0.012 for got, want in zip(rgb, (0.09, 0.054, 0.054)))
'@
& $blender -b --factory-startup (Join-Path $results 'demos/middle-converters.blend') --python-exit-code 1 --python-expr $check
```

For baseline comparisons, set `NINESYNC_FX` to an extract of `git archive c615bfd` and run the same accessory script from the experimental checkout. Use the same source fixture paths for both runs. Logs and native artifacts are under the results directory. The key files are `demos/model-before.blend`, `demos/middle-converters.blend` and the rendered frame files from the numerical integration run. The EXRs carry the linear pixel readbacks checked by the scripts.

## Production recommendation

Keep the three destinations experimental. The strongest production candidate is the typed controller layer behind an explicit destination and a binding review, since it already works on ordinary Blender animation without a frame handler. The material target needs a broader legal shader corpus, broader renderer tests and MMD reference comparisons, and validation of MMD Tools' material morph node behavior. The geometry target needs broader position-space validation and a strategy for recognizing safe local deformation outputs. Renderer-dependent passes remain outside the existing-model profile.
