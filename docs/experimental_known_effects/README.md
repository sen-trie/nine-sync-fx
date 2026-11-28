# Run the Scene Effects experiment

Enable **Experimental MME Scene Effects** in the standalone NineSync FX add-on preferences. Open **Experimental Scene Effects** in the 3D View sidebar under **NineSync**. Select **Analyze Local Effect** and choose an unpacked .fx or its paired .x. Package Root can be set when the selected entry lives in a subfolder. Use a dedicated unpacked folder for each package.

Review the package role, match, requirements and proposed changes before applying. Generic tonal and bloom stages are available independently of package identity. The named o_Tonemap offer is available for its original 0.7 package and entrypoint. Each apply dialog specifies the scene and the proposed stage. The stage is appended to the native Scene Effects stack. Adjust, reorder and inspect it in Blender's Compositor properties and compositor editor. For EEVEE viewport use, enable compositor processing in rendered viewport shading.

Changing controls edits the native effect values immediately. A repeated apply returns the existing stage for the same adapter and source in the scene. **Disable** clears both preview and render toggles. **Rebuild Generated Core** replaces the inner generated graph after confirmation and keeps the outer group's controls, wiring and edits. A changed core interface or shared outer group requires user intervention. **Remove** detaches the selected stage and frees its groups when they have no other users.

## Recognition format

Schema 1 registry entries record the entrypoint, name, version, creator, evidence source, review status, role, features, adapter and raw file hashes. Files use exact, case-preserving, package-relative POSIX paths. Case-insensitive references are resolved to the actual inventory name. Ambiguous paths are rejected.

Each file digest is SHA-256 over its original bytes. For both the package and closure digests, serialize the array `[1, entrypoint, {relative-path: lowercase SHA-256}]` as JSON with sorted object keys, ASCII escaping and separators `,` and `:`, with no whitespace or trailing newline. Encode the JSON as ASCII and hash it with SHA-256. The package digest covers every regular file. The closure covers the selected shader, active transitive includes, selected paired .x and discovered direct resources or companions. Offscreen companion shaders and runtime model bindings are reported for inspection. Transparency, render masks and arbitrary offscreen passes are outside the adapters' scope.

Exact registry matches require both the full package digest and the reviewed role and features. An edited source, renamed file, changed preset or extra file generally causes a different package digest. A missing file produces a diagnostic when the scanner can resolve a dependency or identify the registered entrypoint's original shader bytes. Family candidates use render structure only. The selected .x maps to its same-stem .fx, and a distinct shader in a multi-effect package has its own identity.

Scanning is local and read-only. It bounds package entries, individual files, aggregate bytes, source expansion and include nesting. Linked entries, absolute dependencies and escaping paths are rejected. Rejecting linked entries is a conservative choice for the prototype. The scanner expects the selected package directory to be stable during analysis.

## Reproduce the tests

Use Blender 5.3 with an isolated profile. PowerShell example from the repository root:

```powershell
$blender = 'D:\SteamLibrary\steamapps\common\Blender\blender.exe'
$evidence = Join-Path $env:TEMP 'ninesync-adapter-evidence'
$env:NINESYNC_ADAPTER_OUTPUT = Join-Path $evidence 'renders'
$env:BLENDER_USER_RESOURCES = Join-Path $evidence 'headless'
# Optional original creator archive, unpacked into a dedicated directory.
$env:NINESYNC_TONEMAP = 'D:\path\to\o_Tonemap_v0_7'
python tools/run_tests.py --blender $blender
& $blender -b --factory-startup --python-exit-code 1 --python tests/t_adapter_scene.py
$env:BLENDER_USER_RESOURCES = Join-Path $evidence 'interactive'
& $blender -b --factory-startup --python-expr "import bpy; bpy.context.preferences.view.show_splash=False; bpy.ops.wm.save_userpref()"
& $blender (Join-Path $env:NINESYNC_ADAPTER_OUTPUT 'scene-effects.blend') --python tests/t_adapter_interactive.py
```

With NINESYNC_TONEMAP unset, the test uses the original synthetic tonal fixture. The interactive test opens a separate visible Blender session, checks rebuild undo and redo, and captures the viewport and compositor editor. It exits after testing.
