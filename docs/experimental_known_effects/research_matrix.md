# Known MMEffect research matrix

Reviewed 2026-11-23. Only o_Tonemap was selected for original-package verification.

| Candidate | Creator and source | Observed scope | Review and priority |
|---|---|---|---|
| o_Tonemap v0.7 | o_tamon, creator's June 2013 release. [Distribution page](https://okoneya.jp/mmd_files/#o_Tonemap) | Full-screen tonal adjustment, a controller accessory, per-channel offsets, monochrome mix and bitmap tone curves. | Original package and README reviewed. Exact recognition for the o_Tonemap entrypoint. The independent Blender control mapping covers strength only. |
| o_Diffusion v0.5 | o_tamon, based on Soboro's Diffusion and an algorithm credited to sayappa. [Distribution page](https://okoneya.jp/mmd_files/#o_Diffusion) | Creator describes selective blur of brighter regions. | Listing reviewed. Package terms, blur behavior and transparency remain unverified. Generic highlight bloom was chosen for the optional stack experiment. |
| Diffusion7 | Soboro. [Creator's effect catalogue video](https://www.nicovideo.jp/watch/sm15369179) | Candidate for a blur and image-mix study. | Distribution version and terms remain unverified. |
| AutoLuminous4 | Soboro. [Creator's AutoLuminous video](https://www.nicovideo.jp/watch/sm16087751) | Candidate for a separate emission-mask and pass-routing experiment. | Version, mask routing, alpha and terms need package-level study. The experiment's global bloom stage provides brightness-based glow. |

Technical references include the [MMEffect reference](https://kamedesuyo.github.io/MME_REFERENCE/) and [Blender 5.3 release](https://www.blender.org/releases/5-3/). Scene Effects integration was verified against the installed Blender RNA and Blender's native Scene Effects node-creation operator.
