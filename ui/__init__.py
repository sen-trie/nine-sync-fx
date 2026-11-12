from .operators.accessories import (
    NINESYNC_FX_OT_clean,
    NINESYNC_FX_OT_copy_report,
    NINESYNC_FX_OT_import_accessory,
    NINESYNC_FX_OT_key_accessory_visibility,
    NINESYNC_FX_OT_select_object,
)
from .operators.bake import NINESYNC_FX_OT_bake_effects
from .operators.controls import NINESYNC_FX_OT_assign_control
from .operators.effects import (
    NINESYNC_FX_OT_clear_precull,
    NINESYNC_FX_OT_refresh_effects,
    NINESYNC_FX_OT_reset_params,
)
from .operators.library import (
    NINESYNC_FX_OT_open_core_library,
    NINESYNC_FX_OT_rebuild_core_library,
    NINESYNC_FX_OT_save_core_library,
)
from .panels.accessories import NINESYNC_FX_PT_accessories
from .panels.accessory_effects import NINESYNC_FX_PT_effects
from .panels.bake import NINESYNC_FX_PT_bake
from .panels.library import NINESYNC_FX_PT_library
from .panels.main import NINESYNC_FX_PT_main
from .panels.selected import NINESYNC_FX_PT_selected
from .preferences import NineSyncFXPreferences
from .properties import (
    NineSyncFXAccessory,
    NineSyncFXLink,
    NineSyncFXSettings,
)


CLASSES = (
    NineSyncFXPreferences,
    NineSyncFXLink,
    NineSyncFXAccessory,
    NineSyncFXSettings,
    NINESYNC_FX_OT_refresh_effects,
    NINESYNC_FX_OT_key_accessory_visibility,
    NINESYNC_FX_OT_import_accessory,
    NINESYNC_FX_OT_clear_precull,
    NINESYNC_FX_OT_bake_effects,
    NINESYNC_FX_OT_reset_params,
    NINESYNC_FX_OT_select_object,
    NINESYNC_FX_OT_save_core_library,
    NINESYNC_FX_OT_rebuild_core_library,
    NINESYNC_FX_OT_open_core_library,
    NINESYNC_FX_OT_assign_control,
    NINESYNC_FX_OT_clean,
    NINESYNC_FX_OT_copy_report,
    NINESYNC_FX_PT_accessories,
    NINESYNC_FX_PT_main,
    NINESYNC_FX_PT_selected,
    NINESYNC_FX_PT_effects,
    NINESYNC_FX_PT_bake,
    NINESYNC_FX_PT_library,
)
