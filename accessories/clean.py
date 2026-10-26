import bpy

from ..common.animation import purge_actions
from ..common.constants import ACTION_PREFIX
from ..common.scene_utils import accessory_collection
from ..common.state import _mme_runtimes
from ..runtime.cleanup import (
    mme_purge_owned,
    remove_accessory_children,
)


# Removes all accessory roots with their pass objects and owned materials and groups, the emptied collection
# and the sync's actions.
def clean_accessory_objects(counts):
    _mme_runtimes.clear()
    collection = accessory_collection(bpy.context.scene)
    if collection is not None:
        for obj in [
            o for o in collection.objects if o.get("mmd_bridge_accessory") is not None
        ]:
            key = obj.get("mmd_bridge_accessory")
            remove_accessory_children(obj)
            bpy.data.objects.remove(obj, do_unlink=True)
            mme_purge_owned("%s|" % key)
            counts["accessories"] += 1
        if not collection.objects and not collection.children:
            bpy.data.collections.remove(collection)
    purge_actions(ACTION_PREFIX + "Acc_")
