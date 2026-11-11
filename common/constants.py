import numpy as np


MIN_BLENDER = (5, 3, 0)


NINESYNC_NAME = "NineSync"


NINESYNC_CATEGORY = "NineSync"


NINESYNC_MAIN_LABEL = NINESYNC_NAME + ": PMM Bridge"


NINESYNC_FX_LABEL = NINESYNC_NAME + ": FX Importer"


NINESYNC_ACC_LABEL = NINESYNC_NAME + ": Accessories (.x)"


ACTION_PREFIX = "MMDBridge_"


CAMERA_COLLECTION = "MMD_Camera"


LIGHT_COLLECTION = "MMD_Sun"


FOLLOW_PREFIX = "MMDBridgeFollow"


UNIT_SCALE = 1.0


TEMP_PREFIX = "mmd_bridge_"


TEMP_DIR_NAME = "mmd_bridge"


STALE_TEMP_SECONDS = 300


FRAME_EPSILON = 1e-4


CLIP_TRACK_NAME = "MMDBridge"


UV_KEY_PREFIX = "MMDUV_"


UV_NODE_PREFIX = "MMDBridgeUV"


UV_ATTRIBUTE_PREFIX = "MMDBridgeUV_"


UV_SLIDER_LIMIT = 10.0


PMM_FPS = 30.0


AUDIO_STATE_SIZE = 28


AUDIO_PATH_LENGTH = 256


AUDIO_STRIP_NAME = "MMDBridge_Audio"


AUDIO_EXTENSIONS = (".wav", ".mp3", ".ogg", ".flac", ".aac", ".m4a")


REPORT_TEXT_NAME = "NineSyncReport"


REPORT_LOG_LINES = 300


ADDON_PACKAGE = (__package__ or __name__).split(".")[0] if not (__package__ or "").startswith("bl_ext.") else ".".join((__package__ or "").split(".")[:3])


ACCESSORY_COLLECTION = "MMD_Accessories"


ACCESSORY_OWNER_PROP = "ninesync_scene"


ACCESSORY_PREFIX = "MMDAcc_"


ACCESSORY_FOLLOW_PREFIX = "MMDBridgeAcc"


ACCESSORY_VISIBLE_PROP = "mme_visible"


ACCESSORY_VISIBLE_MODIFIER = "NineSync Visible"


ACCESSORY_VISIBLE_GROUP = "NineSync Visibility"


ACCESSORY_OWNED_PATHS = ("location", "rotation_euler", "scale", '["mme_tr"]', '["mme_si"]')


ACCESSORY_UNIT = 10.0


X_IMPORTER_MODULES = ("io_directx_x", "io_scene_x")


X_IMPORTER_BUNDLED_MODULE = "x_importer_bundled"


# Spelled out because the operator path relied on the operator's defaults, which differ from import_x's
# (axis_forward is "Z" on the operator and "-Z" on the function).
X_IMPORT_OPTIONS = {
    "use_apply_transform": True,
    "global_scale": 1.0,
    "axis_forward": "Z",
    "axis_up": "Y",
    "import_normals": True,
    "import_uvs": True,
    "import_materials": True,
    "import_textures": True,
    "use_diffuse_alpha": True,
    "rest_from_frame1": False,
    "flatten_reflections": True,
    "smooth_shade_from_faces": False,
    "split_submeshes": True,
    "triangulate_quads": True,
    "weld_duplicate_verts": True,
    "import_armature": True,
    "import_weights": True,
    "import_animation": True,
    "anim_fps": 0.0,
    "set_frame_range": False,
    "use_import_collection": True,
    "import_type": "standard",
}


X_IMPORTER_URL ="https://extensions.blender.org/add-ons/io-directx-x/"


MMD_TOOLS_URL = "https://extensions.blender.org/add-ons/mmd-tools/"


MME_DRAW_PROP = "mme_draw"


MME_VARYINGS_PROP = "mme_varyings"


MME_UNIFORMS_PROP = "mme_uniforms"


MME_ATTR_SRC = "mme_src"


MME_ATTR_INST = "mme_inst"


MME_ATTR_CULL = "mme_cull"


MME_VARYING_PREFIX = "mmeV_"


MME_LOOP_PREFIX = "mmeL_"


MME_UNIFORM_PREFIX = "mmeU_"


MME_CONTROL_PREFIX = "mmeC_"


MME_IGNORED_PROP = "mme_ignored_sources"


MME_USE_PREFIX = "mmeUse_"


MME_IMAGE_PREFIX = "MME_"


MME_GAMMA = 2.2


MME_PRUNE_TR = (1.0, 0.8, 0.6, 0.4, 0.2, 0.0)


MME_SWAP = np.array(
    [[1.0, 0, 0, 0], [0, 0, 1.0, 0], [0, 1.0, 0, 0], [0, 0, 0, 1.0]], dtype=np.float64
)


MME_SWAP3 = MME_SWAP[:3, :3]


MME_SELF_NAMES = ("(self)", "(offscreenowner)")


MME_PARAM_PREFIX = "mmeP_"


MME_PARAMS_PROP = "mme_params"


MME_PARAM_BUILT_PROP = "mme_param_built"


MME_GN_MODIFIER = "MME"


MME_BACKEND_PROP = "mme_backend"


MME_PRUNED_PROP = "mme_pruned"


MME_BAKED_PROP = "mme_baked"


MME_MANIFEST_PROP = "mme_manifest"


MME_PASS_KEY_PROP = "mme_pass_key"


MME_MANIFEST_VERSION = 1


MME_DRIVEN_PROP = "mme_driven"


MME_OWNER_PROP = "mme_owner"


MME_GROUP_PROP = "mme_group"


MME_SIG_PROP = "mme_sig"


MME_ATTR_POS = "mme_p"


MME_ATTR_NRM = "mme_n"


MME_ATTR_UV = "mme_uv"


# Mesh attributes only the vertex shader reads; Geometry Nodes passes drop them before the Bake node. The source index
# and loop instance stay: they identify each output vertex, which backend comparisons rely on.
MME_SHADER_ONLY_ATTRS = (MME_ATTR_POS, MME_ATTR_NRM, MME_ATTR_UV)


MME_REBUILD_DELAY = 0.6


MME_CORE_VERSION = 2


MME_KEY_PROP = "mme_key"


MME_RET_PROP = "mme_ret"


MME_CORE_FILE = "mme_core.blend"


MME_LIBRARY_FOLDER = "ninesync_fx"


MME_LIBRARY_LOCK_STALE = 120.0


MME_MANUAL_PREFIX = "mme_manual_"


MME_MANUAL_BONES_PROP = "mme_manual_bones"


MME_UNSUPPORTED_PROP = "mme_unsupported"


MME_OBJECT_ITEMS = ("x", "y", "z", "xyz", "rx", "ry", "rz", "rxyz", "si", "tr")


MME_SIMPLIFY_TR = (1.0, 0.9, 0.75, 0.6, 0.45, 0.3, 0.15, 0.0)


MME_GATE_TR = tuple(k / 32.0 for k in range(32, -1, -1))


MME_GATE_MARGIN = 2


MME_GATE_EPS = 1e-5


MME_GATE_ALPHA = 1.0 / 512.0


MME_GATE_PROBES = 96


MME_VARIANT_FRAMES = 24


MME_VARIANT_MAX = 48


MME_SIMPLIFY_VARIANTS = 8


MME_SIMPLIFY_BUDGET = 15.0


MME_STRIP_CACHE_VERSION = 2


MME_ANALYSIS_DTYPE = np.float32


# Memory budgets for build analysis: vertices per shader batch in pre-cull and in strip analysis, bytes per scoring
# block, and whether large strip buffers are file-backed. They bound the temporary arrays of the analysis; Blender's
# own memory and the built meshes come on top. Every budget gives the same result.
MME_MEMORY_BUDGETS = {
    "LOW": {"precull": 262144, "strips": 65536, "block": 64 << 20, "spill": True},
    "BALANCED": {"precull": 4194304, "strips": 262144, "block": 256 << 20, "spill": False},
    "HIGH": {"precull": 1 << 40, "strips": 1048576, "block": 1024 << 20, "spill": False},
}


MME_SPILL_FOLDER = "ninesync_fx_buffers"


MME_SPILL_MIN_BYTES = 32 << 20


MME_PRECULL_VERSION = 2


MME_PRECULL_GROUPS = 24


MME_STRIP_CACHE_ENTRIES = 64


MME_PROGRESS_WIDTH = 24


MME_WRAP_MARGIN = 40


MME_WRAP_CHAR = 6.2


MME_PROGRESS_REDRAW = 0.25


MME_RUNTIME_VALUES_PROP = "mme_runtime_values"


MME_GROUP_LABEL = "MMEffects"



MME_EMM_NO_EFFECT = ("none", "hide", "")


MME_EMM_HIDE = "hide"


MME_EMM_CACHE_SIZE = 8


ACCESSORY_KEY_RECORD = "mme_key_record"


ACCESSORY_FADE_PROP = "mme_fade"


MME_CACHE_FOLDER = "cache"


MME_CACHE_LEGACY_FOLDER = "ninesync_cache"


MME_CACHE_LIMIT = 512 * 1024 * 1024
