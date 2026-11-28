# Proposed compositor target

FilmGrain is a candidate for a limited vignette adaptation. Its source also reads a noise texture, routes an intermediate image and uses a PMX controller. Expose the supported operation and label its output `BLENDER_EQUIVALENT`. FXAA provides a second capture fixture for diagnosing screen-space sampling. A bloom adapter needs a separately inspected bloom fixture before implementation. [Corpus evidence](COMPATIBILITY_MATRIX.md)

Use an explicit scene, camera and view layer. An opt-in operator should place an owned node group in the chosen compositor chain, retain the previous connections, allow parameter edits and restore the original connections on removal. Check ownership on resync and keep independently authored nodes and links intact.

Test installation, removal and undo with two scenes and an existing compositor tree. Compare frames at a fixed resolution, alpha convention and color transform. Record the source MMD frame alongside the Blender output before assigning a verification status. Temporal noise, feedback and depth-dependent operators need their own fixtures and seek tests.

**Scope:** The current project rules put full-screen and post-process passes out of scope ("never plan or emulate them"). This proposal therefore needs an explicit owner decision before any implementation. The build sweep found 40 post or scene entries in a corpus of 165, the largest blocked group after model shaders.

