# Proposed model-material target

Start with a single local pixel shader on one material slot of an existing mmd_tools mesh. MMDShaderTemplate demonstrates why declarations and draw selection matter: its object technique can be inspected separately from its edge, ground and self-shadow variants. The controlled-surface fixture provides a smaller case for testing a named controller after local shading works. [Corpus evidence](COMPATIBILITY_MATRIX.md)

The assignment should store the object, chosen slot, original material, shader source and generated material. Compile a generated material against the mesh's existing UVs and material inputs. Preserve the original datablock and the object's slot-link mode. Copies of a shared mesh or material must be independently assignable. Resync only a slot still owned by the assignment so an artist's replacement survives.

First test basic diffuse, alpha, UV, texture and light direction. Follow with toon and sphere textures and animated material morphs. The corpus shows texture-add and texture-multiply morph semantics in a real shader. Cross-object controls should reuse the existing reference resolver. Report missing bindings and external passes before applying a partial conversion.

Acceptance requires assigning different slots, previewing, resyncing, saving and reopening the file, restoring the original slots and undoing the operation. Check the animated mesh and armature at several frames against the original object. Add a deformation stage only after verifying the intended order with skinning and shape keys.

## Feasibility review (November 22, 2026)

The review found the target feasible with the existing pixel-shader compiler, at an estimated 600 to 700 lines plus a test. No pass objects are needed. The plan is to run the vertex shader inside the same shader-node group as the pixel shader. The interpreter does not depend on a backend, and `MmePsBackend` shares its base with the Geometry Nodes backend. The VS reads object position, normal and UV from shader nodes and feeds its outputs straight to the PS. It then runs per pixel instead of per vertex, which gives the same result for linear varyings.

What can be reused:

- `mme_compile_material` (`nodes/material_compile.py`) needs only a duck-typed runtime and a varying provider.
- Edit preservation from 0.7.3 (`owner`, `mme_owned`, `MME_SIG_PROP`) works with `owner = "model|<object>|<slot>"`.
- The `CONTROLOBJECT` resolver in `runtime/controls_resolve.py`.

Prerequisites that also affect accessories today:

1. `fx_pick_technique` ignores the `UseTexture`, `UseSphereMap` and `UseToon` annotations, so ToonDX and `full_*` shaders always get their first variant.
2. `MATERIALTOONTEXTURE` and `MATERIALSPHEREMAP` resolve to nothing, and `TOONCOLOR` is fixed white.
3. A bare `register(s0)` sampler in an `object_ss` technique is MMD's shadow map, but it resolves as the material texture.

Open questions before implementation:

- Verify the mmd_tools material property names and texture nodes against a real install. Neither repo has a reference.
- Decide whether MMD's world matrix for a skinned PMX matches the Blender object transform.
- Camera and light uniforms reach only effect-pass children today, through the frame handler (`runtime/update.py`). Model targets need either view-layer attributes or their own loop.
- Material morphs need drivers rather than baked constants.

Checked against real shaders: ToonDX `その他.fx` and `髪2.fx` and AutoLuminous `full_saturate.fx` parse and evaluate in all 16 object and object_ss techniques. `髪2.fx` differs from `その他.fx` only in its self-shadow technique, so it needs prerequisite 3.

