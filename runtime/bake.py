import bpy

from ..common.constants import (
    MME_BAKED_PROP,
    MME_GN_MODIFIER,
)
from ..common.state import _mme_state
from .drivers import mme_needs_handler


# Bakes a Geometry Nodes pass over the frame range through the modifier's bake node. The frame handler is
# limited to this object during the bake.
def mme_bake_pass(context, obj, start, end, settings):
    modifier = obj.modifiers.get(MME_GN_MODIFIER)
    if modifier is None or not len(modifier.bakes):
        return False
    bake = modifier.bakes[0]
    bake.bake_mode = "ANIMATION"
    bake.use_custom_simulation_frame_range = True
    bake.frame_start = start
    bake.frame_end = end
    if settings.mme_bake_target == "DISK":
        modifier.bake_target = "DISK"
        modifier.bake_directory = settings.mme_bake_dir
        bake.bake_target = "DISK"
    else:
        modifier.bake_target = "PACKED"
        bake.bake_target = "PACKED"
    _mme_state["only"] = {obj.name}
    try:
        with context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
            result = bpy.ops.object.geometry_node_bake_single(
                session_uid=obj.session_uid, modifier_name=modifier.name, bake_id=bake.bake_id
            )
    finally:
        _mme_state["only"] = None
    if "FINISHED" not in result:
        return False
    obj[MME_BAKED_PROP] = True
    obj["mme_handler"] = mme_needs_handler(obj)
    return True


# Deletes the bake and gives the pass its frame handler back if it needs one.
def mme_clear_bake(context, obj):
    modifier = obj.modifiers.get(MME_GN_MODIFIER)
    if modifier is None or not len(modifier.bakes):
        return False
    bake = modifier.bakes[0]
    with context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.geometry_node_bake_delete_single(
            session_uid=obj.session_uid, modifier_name=modifier.name, bake_id=bake.bake_id
        )
    if MME_BAKED_PROP in obj:
        del obj[MME_BAKED_PROP]
    obj["mme_handler"] = mme_needs_handler(obj)
    return True
