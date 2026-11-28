# MME package taxonomy

The scanner answers three questions independently. Which file does the artist load? What does each pass need from the renderer? Which visual family does the source establish? A triangle mesh can start a full-screen effect, and a single shader pack can include a model shader and an offscreen companion.

## Package modes observed in the first audit

| Mode | Evidence used | Packages |
|---|---|---:|
| A1, local accessory | Same-stem text mesh with faces and a planned main-target geometry draw | 1 |
| A2, procedural accessory | A main-target geometry draw carries a scripted loop | 2 |
| A3, controller | A reachable controller parameter names an inventoried object file | 2 |
| A4, model or material assignment candidate | An unpaired object technique draws geometry and its shader reads a material input | 3 |
| A5, post effect | A pass specifies a buffer draw or the effect declares postprocess order | 2 |
| A6, cooperating effects | An offscreen target routes drawing to another resolved shader file | 1 |

Counts overlap across the eight scanned packages. The package JSON retains unresolved entry points. For example, the developer probe in the Synth folder has no automatic mesh pairing. A mesh referenced as a controller can also participate in drawing. A controller binding to an object already in the MMD scene may need resolution even when the package contains no such file.

## Rendering dependencies

| Code | Evidence |
|---|---|
| D0 | Object geometry, material data, local textures, camera or light inputs |
| D1 | Scripted loops, time inputs, animated textures or self opacity |
| D2 | A potentially read controller parameter addresses another object |
| D3 | Buffer draws, postprocess order or explicit screen inputs |
| D4 | Target routing, external scene drawing or offscreen capture |
| D5 | Explicit previous-frame or history inputs require investigation |
| D6 | Explicit renderer inputs such as a G-buffer or shadow map require investigation |

The scanner captures declared techniques and render states, global semantics, shader entry points and potential reads through helper functions and sampler bindings. The separate draw list follows NineSync's normal object-technique selection, material subsets and default loop counts. Without a mesh it shows the planner's subset-zero view. A declaration on an alternate technique can therefore appear in the package summary without appearing in that draw list. The MMEffect reference describes technique selection, script order and target semantics. [MMEffect reference](https://kamedesuyo.github.io/MME_REFERENCE/)

Visual tags are sparse. Buffer effects receive `POST`, and material-assignment candidates receive `MODEL_MATERIAL`. A loop alone gives no evidence for a specific appearance such as smoke or lightning. D5 and D6 hints use explicit semantic identifiers, including nonstandard names. Implicit feedback across frames and whole-renderer behavior require manual inspection. This corpus contains no verified D5 or D6 case.

## Reading a report

Run `python -m tools.mme DIRECTORY --probe` from the source checkout with NumPy installed. Each directory represents one unpacked package. `--source IDENTIFIER` supplies its provenance. The report goes to standard output. Use a UTF-8 redirect to save it. Per-effect warnings identify missing active includes, texture paths, controller references, unresolved shader entries and unassessed commands. Includes can refer outside the package. Shared resources resolved there appear as relative paths when possible.

The classifier uses the same-name accessory convention. Project-specific material assignments in `.emm` and the contents of PMX, PMD and EMD files are outside this scan. The inventory retains their paths. Preprocessing uses the importer's default macro configuration. After an unresolved include, dependency discovery continues with the macros available so far and parsing stops for that entry.

Predictions range from A (local accessory candidate) through B (conditional), C (material backend needed) and D (scene or post pipeline needed). Unknown entries have a null rating. Package summaries give the most demanding classified entry and the lowest entry confidence. A report can identify a supported local draw alongside a blocked scene draw. Some same-geometry color-target writers can be folded into an accessory material by the current compiler. Independent scene replay and full-screen buffers need a separate target.

`draws_planned` records static planning. With `--probe`, an eligible geometry shader pair can reach `sample_evaluated`: the NumPy backend evaluates one synthetic vertex and pixel at default scene settings and loop index zero, with black texture samples. A failure includes the pass and stage. Node compilation, image loading, animation, playback speed and rendered appearance require separate checks.

The inventory records observed progress as `NOT_DOWNLOADED`, `FILES_PRESENT`, `PARSED`, `BUILT` and `RENDER_VERIFIED`. The last stage requires a matched reference. The scanner always emits `verification: unverified`. Build and render observations live alongside the scan in the audit inventory. [Compatibility matrix](COMPATIBILITY_MATRIX.md) · [Inventory](mme_effect_inventory.json)

## Rules added after the build sweep

The second audit imported every accessory pair in Blender and compared the outcome with the scan. These rules came out of that comparison. Report schema 2 carries the new fields.

- **What geometry reads decides the outcome, not what passes exist.** A main-target geometry pass that samples a texture filled only by buffer passes (GPGPU particle state) or by a scene capture (an offscreen target or a `ScriptExternal` capture) is blocked. The add-on renders neither, so those reads are zero. `fx_unsourced_reads` in `fx/params.py` implements this rule, and the importer and the scanner share it.
- **Targets written by geometry are emulated.** When a geometry draw writes the target, the importer emulates it from that draw. Buffer passes on the same target (Nuke2's blur) are dropped with a warning.
- **`partial`** means the local part converts and only full-screen or scene passes are lost. Nuke2 is the measured example. `skipped_draws` lists the buffer draws an import leaves out. A model shader with a post pass is a C partial.
- **D5 frame feedback** is raised only when a buffer pass reads a target before this frame writes it, so the value is the previous frame's. ParticleEX2, LSv4_5 and SlashLine qualify. A same-frame blur chain such as LightBloom does not.
- **D6 self-shadow** is raised when an `object_ss` technique samples the bare `register(s0)` sampler, which is MMD's shadow map in that pass. The importer uses the object technique, so this is a warning, not a blocker.
- **Roles.** An `.fx` that another file includes has the role `include`. One assigned only through an offscreen `DefaultEffect` is an `offscreen_companion`. Neither is predicted or counted in package totals. An unpaired `.fx` next to a same-name PMX or PMD gets `PAIRED_MODEL` and C, because MME applies it to that model (AutoSmoke2).
- **Controllers** match package files by file name as well as by relative path, as MME does. Mesh textures that the shader never samples no longer produce missing-texture warnings. Mesh texture names split on `*` (texture and sphere map).

A build can still succeed while the effect looks wrong. The inventory records those cases as `BUILT` with a `degraded` list, never as plain successes.

