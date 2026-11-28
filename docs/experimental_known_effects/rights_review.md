# Rights review

| Field | o_Tonemap v0.7 |
|---|---|
| Creator and release | o_tamon, 2013-06-29. Archive obtained from the version 0.7 link on the [creator's distribution page](https://okoneya.jp/mmd_files/#o_Tonemap). |
| Evidence | Original UTF-16 readme.txt, including the usage, alpha, control and rights sections. Package entries and SHA-256 values are recorded in the experiment registry. Archive SHA-256 is 28c9a5d1fedb8fe5bfbfcb6d667c74eb27b276023d0a4c16d7c8dc1bb0549d48. |
| Terms | The README grants unrestricted use, modification and redistribution of the effect file and included bitmaps, with a liability disclaimer. The website separately describes its common MMD usage condition and asks readers to link to the distribution page. The README describes use in MMD and credits earlier work in the history. |
| Attribution and scope | NineSync uses the creator name and exact release number to identify compatibility. The Blender implementation uses standard native Exposure, Brightness/Contrast, Hue/Saturation and Mix nodes. No original shader logic, lookup bitmap, preset, mesh or reference artwork is copied into the adapter. |
| Decision | Enable exact recognition and the opt-in, independent adaptation on the experiment branch. Keep the archive and extracted files local. Public release, alternate presets and closer mapping would need separate scope and provenance review. |
| Open items | No creator endorsement or explicit Blender-specific statement was found. The local matched-input chart comparison measures strength behavior and shows visible differences in the tonal curves. |

The app maps documented Tr, the opacity-style effect strength, to Blender Strength by manual user entry (0–1). The README suggests roughly 0.5–0.75 with the default preset. Blender Exposure is measured in stops. Blender Contrast and Saturation use their native compositor units. The original X/Y/Z offsets span −1 to +1 per channel, and the source can use bitmap tone curves and special alpha output. Those controls and assets are outside the current mapping.

The other candidates were researched through catalogue or creator pages. No packages were downloaded for them and they have no registry entries. Existing local Thunder Lv1/Lv2 and Nuke2 packages were used solely for generic-import regression checks. Their licensing was outside the named adapter review.
