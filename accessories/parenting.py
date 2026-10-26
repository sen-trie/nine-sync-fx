from mathutils import Matrix

from ..common.constants import ACCESSORY_FOLLOW_PREFIX
from ..common.log import log
from ..common.scene_utils import (
    find_armature,
    model_key,
    pose_bone_name,
    target_usable,
)


# Armature of the first enabled model link; used when only a bone override is given.
def first_armature(settings):
    for item in settings.targets:
        if item.enabled and target_usable(item.target):
            return find_armature(item.target)
    return None


# Which armature bone an accessory follows: the bone override if set, otherwise the model and bone from the
# PMM's first key.
# Returns None, after logging why, when the link or bone does not exist.
def resolve_accessory_parent(project, settings, acc, entry):
    first = acc.keys[0]
    override = entry.parent_bone if entry is not None else ""
    by_index = {m.index: m for m in project.models}
    items = {t.key: t for t in settings.targets}
    armature = None
    model = by_index.get(first.parent_model) if first.parent_model >= 0 else None
    if model is not None:
        item = items.get(model_key(model))
        if item is not None and item.target is not None:
            armature = find_armature(item.target)
    if override:
        armature = armature or first_armature(settings)
        if armature is not None and override in armature.pose.bones:
            return armature, override
        log.warning("accessory '%s': override bone '%s' not found", acc.name, override)
        return None
    if model is None or first.parent_bone < 0:
        return None
    if armature is None:
        log.warning(
            "accessory '%s' follows model %d which has no Blender target",
            acc.name,
            first.parent_model,
        )
        return None
    if first.parent_bone >= len(model.bone_names):
        log.warning(
            "accessory '%s': bone index %d out of range", acc.name, first.parent_bone
        )
        return None
    bone = pose_bone_name(armature, model.bone_names[first.parent_bone])
    if bone is None:
        log.warning(
            "accessory '%s': bone '%s' not found on '%s'",
            acc.name,
            model.bone_names[first.parent_bone],
            armature.name,
        )
        return None
    return armature, bone


# Inverse of the bone's rest rotation, used as the Child Of inverse matrix so the bone's rest orientation is
# not applied to the accessory.
def bone_rest_inverse(armature, bone_name):
    data_bone = armature.data.bones.get(bone_name)
    if data_bone is None:
        return Matrix.Identity(4)
    rotation = data_bone.matrix_local.to_3x3().normalized()
    return rotation.inverted().to_4x4()


# Replaces the follow constraint with a Child Of constraint to the resolved bone (position and rotation only,
# no scale). The PMM can change parent between keys, but only the first is used.
def apply_accessory_parent(root, project, settings, acc, entry):
    for constraint in [
        c for c in root.constraints if c.name.startswith(ACCESSORY_FOLLOW_PREFIX)
    ]:
        root.constraints.remove(constraint)
    parents = {(k.parent_model, k.parent_bone) for k in acc.keys}
    if len(parents) > 1:
        log.warning(
            "accessory '%s' changes parent between keyframes; only the first parent is used",
            acc.name,
        )
    resolved = resolve_accessory_parent(project, settings, acc, entry)
    if resolved is None:
        log.info("accessory '%s' has no parent bone", acc.name)
        return
    armature, bone = resolved
    constraint = root.constraints.new(type="CHILD_OF")
    constraint.name = ACCESSORY_FOLLOW_PREFIX
    constraint.target = armature
    constraint.subtarget = bone
    constraint.use_scale_x = False
    constraint.use_scale_y = False
    constraint.use_scale_z = False
    constraint.set_inverse_pending = False
    constraint.inverse_matrix = bone_rest_inverse(armature, bone)
    constraint.influence = 1.0
    log.info("accessory '%s' follows %s / %s", acc.name, armature.name, bone)
