import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup

from ..common.scene_utils import is_mmd_root


class NineSyncFXAccessory(PropertyGroup):
    key: StringProperty()
    display: StringProperty()
    enabled: BoolProperty(
        name="Sync From PMM",
        description="Update this accessory from the PMM when you press Sync. Turn off to keep it as it is in Blender",
        default=True,
    )
    standalone: BoolProperty(default=False)
    effect_checked: BoolProperty(default=False)
    use_effect: BoolProperty(
        name="Use Effect",
        description="Run this accessory's MME effect (.fx). Turn off to import the plain mesh",
        default=True,
    )
    effect_path: StringProperty(
        name="Effect File",
        description="Effect (.fx) to use instead of the one found next to the .x or in the PMM's .emm file",
        subtype="FILE_PATH",
    )
    effect_note: StringProperty()
    show_controls: BoolProperty(name="Effect Controls", default=False)
    parent_bone: StringProperty(
        name="Parent Bone",
        description="Overrides the parent bone stored in the PMM. Leave empty to use the PMM value",
    )


class NineSyncFXLink(PropertyGroup):
    key: StringProperty()
    pmm_name: StringProperty()
    enabled: BoolProperty(name="Enabled", default=True)
    target: PointerProperty(name="Target", type=bpy.types.Object, poll=is_mmd_root)


class NineSyncFXSettings(PropertyGroup):
    filepath: StringProperty(name="PMM File", description="Set by NineSync: PMM Bridge on each sync", subtype="FILE_PATH", options={"HIDDEN"})
    auto_scale: BoolProperty(
        name="Auto Scale From Model",
        description="Use the scale the first enabled model was imported with in mmd_tools",
        default=True,
    )
    scale: FloatProperty(
        name="Scale",
        description="Motion scale passed to the mmd_tools VMD importer when Auto Scale is off",
        default=0.08,
        min=0.001,
    )
    sync_accessories: BoolProperty(
        name="Sync Accessories",
        description="Import and animate .x accessories stored in the PMM. Static meshes only",
        default=True,
    )
    sphere_maps: BoolProperty(
        name="Sphere Maps (Experimental)",
        description="Rebuild .sph/.spa sphere maps from accessory textures with a camera-space normal lookup",
        default=True,
    )
    accessory_collection: PointerProperty(type=bpy.types.Collection, options={"HIDDEN"})
    accessory_dir: StringProperty(
        name="Accessory Folder",
        description="Extra folder searched for .x files when the path stored in the PMM does not exist",
        subtype="DIR_PATH",
    )
    accessories: CollectionProperty(type=NineSyncFXAccessory)
    mme_density: FloatProperty(
        name="Effect Density",
        description="Share of each LoopByCount loop to draw. 1 matches MMD. Lower values make heavy effects such as Nuke2 faster to scrub. Geometry Nodes passes update live; Python passes on the next sync or Refresh Effects",
        default=1.0,
        min=0.02,
        max=1.0,
        subtype="FACTOR",
        update=lambda self, context: context.scene.update_tag(),
    )
    mme_tolerance: FloatProperty(
        name="Geometry Tolerance",
        description="How far simplified effect strips may drift from the full mesh, as a share of the effect's size. Rows of strip meshes (such as Thunder's 10,000-segment rings and bolts) that the shader output can rebuild by interpolation are merged. 0 keeps every row. Faces that the shader always makes fully transparent are removed regardless. Applies on the next Rebuild",
        default=0.0005,
        min=0.0,
        max=0.05,
        precision=4,
        step=0.01,
    )
    mme_precull: BoolProperty(
        name="Pre-cull Cache",
        description="Remember which effect vertices can never be drawn (per effect file, .x file and pass) in a cache on disk, so rebuilds only analyse new settings. Only vertices that stayed invisible in every evaluated state are skipped",
        default=True,
    )
    mme_auto_rebuild: BoolProperty(
        name="Auto Rebuild",
        description="Rebuild an effect automatically after you edit a parameter that changes its geometry (wrench icon). Turn off for heavy effects: edits then only mark the effect as out of date until you press Rebuild",
        default=True,
    )
    mme_live: BoolProperty(
        name="Live Effect Preview",
        description="Recompute Python-backend effect passes whenever the frame changes in the viewport. Geometry Nodes passes always update. Renders always recompute",
        default=True,
    )
    mme_backend: EnumProperty(
        name="Effect Backend",
        description="How effect vertex shaders run. Applied on the next sync or Refresh Effects",
        items=[
            ("GEONODES", "Geometry Nodes", "Compile vertex shaders to Geometry Nodes: no Python per frame, renders and bakes natively. Passes that cannot compile fall back to Python"),
            ("PYTHON", "Python", "Run vertex shaders in Python (numpy) from a frame-change handler"),
        ],
        default="GEONODES",
    )
    fx_info_tab: EnumProperty(
        name="Effect Info",
        items=[
            ("LINKS", "Linked Objects", "Objects and morphs the effect reads (CONTROLOBJECT)", "LINKED", 0),
            ("PASSES", "Passes", "The effect's draw passes", "NODETREE", 1),
            ("PASS", "Selected Pass", "Details of the selected pass object", "OBJECT_DATA", 2),
        ],
        default="PASSES",
    )
    mme_core_autosave: BoolProperty(
        name="Grow Library Automatically",
        description="After an effect import builds new shared node groups, add them to mme_core.blend so later imports append them instead of building them again",
        default=True,
    )
    mme_bake_target: EnumProperty(
        name="Bake To",
        items=[
            ("PACKED", "Packed in .blend", "Store baked effect geometry inside the .blend file"),
            ("DISK", "Disk", "Store baked effect geometry in the folder below"),
        ],
        default="PACKED",
    )
    mme_bake_dir: StringProperty(
        name="Bake Folder",
        description="Folder for effect bakes stored on disk",
        default="//mme_bake/",
        subtype="DIR_PATH",
    )
    retime_fps: BoolProperty(
        name="Retime to Scene FPS",
        description="PMM keyframes are 30 fps. Scale every key frame by scene fps / 30 so motion keeps its real speed in a scene with a different frame rate",
        default=True,
    )
    targets: CollectionProperty(type=NineSyncFXLink)
