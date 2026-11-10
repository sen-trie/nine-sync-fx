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


def model_armature(project, settings, model_index):
    model = {m.index: m for m in project.models}.get(model_index) if model_index >= 0 else None
    if model is None:
        return None, None
    item = {t.key: t for t in settings.targets}.get(model_key(model))
    if item is None or item.target is None:
        return model, None
    return model, find_armature(item.target)


# The armature bone one PMM parent (model index, bone index) stands for. Returns None, after logging why, when
# the link or bone does not exist.
def resolve_parent_pair(project, settings, acc, pair):
    model_index, bone_index = pair
    model, armature = model_armature(project, settings, model_index)
    if model is None or bone_index < 0:
        return None
    if armature is None:
        log.warning("accessory '%s' follows model %d which has no Blender target", acc.name, model_index)
        return None
    if bone_index >= len(model.bone_names):
        log.warning("accessory '%s': bone index %d out of range", acc.name, bone_index)
        return None
    bone = pose_bone_name(armature, model.bone_names[bone_index])
    if bone is None:
        log.warning("accessory '%s': bone '%s' not found on '%s'", acc.name, model.bone_names[bone_index], armature.name)
        return None
    return armature, bone


# Which bones an accessory follows. A bone override is one fixed parent on the first key's model (or the first
# linked model). Otherwise every distinct parent the keys use: a single one stays a plain Child Of, and a
# parent that changes between keys becomes one Child Of per parent with keyed influence, as MMD switches
# parents per key. Returns {"pairs": {pair: (armature, bone) or None}, "keyed": bool}.
def accessory_parent_plan(project, settings, acc, entry):
    override = entry.parent_bone if entry is not None else ""
    first = acc.keys[0]
    if override:
        _model, armature = model_armature(project, settings, first.parent_model)
        armature = armature or first_armature(settings)
        if armature is not None and override in armature.pose.bones:
            return {"pairs": {(first.parent_model, first.parent_bone): (armature, override)}, "keyed": False}
        log.warning("accessory '%s': override bone '%s' not found", acc.name, override)
        return {"pairs": {}, "keyed": False}
    order = []
    for key in sorted(acc.keys, key=lambda k: k.frame):
        pair = (key.parent_model, key.parent_bone) if key.parent_model >= 0 and key.parent_bone >= 0 else None
        if pair not in order:
            order.append(pair)
    pairs = {pair: resolve_parent_pair(project, settings, acc, pair) for pair in order if pair is not None}
    return {"pairs": pairs, "keyed": len(order) > 1}


# Text that changes when the plan follows other bones, so the key record can tell a relinked model from an
# unchanged one.
def parent_signature(plan):
    return [
        [list(pair), None if found is None else [found[0].name, found[1]]]
        for pair, found in sorted(plan["pairs"].items())
    ] + [plan["keyed"]]


def parent_constraint_name(plan, pair):
    if not plan["keyed"]:
        return ACCESSORY_FOLLOW_PREFIX
    return "%s_%d_%d" % (ACCESSORY_FOLLOW_PREFIX, pair[0], pair[1])


# Influence curves for a parent that changes between keys: 1 on the keys that use a parent and 0 on the
# others, held constant. Returned as (data path, frames, values) for the keying code.
def parent_influence_curves(plan, keys):
    if not plan["keyed"]:
        return []
    curves = []
    ordered = sorted(keys, key=lambda k: k.frame)
    for pair, found in plan["pairs"].items():
        if found is None:
            continue
        path = 'constraints["%s"].influence' % parent_constraint_name(plan, pair)
        values = [1.0 if (k.parent_model, k.parent_bone) == pair else 0.0 for k in ordered]
        curves.append((path, [k.frame for k in ordered], values))
    return curves


# Inverse of the bone's rest rotation, used as the Child Of inverse matrix so the bone's rest orientation is
# not applied to the accessory.
def bone_rest_inverse(armature, bone_name):
    data_bone = armature.data.bones.get(bone_name)
    if data_bone is None:
        return Matrix.Identity(4)
    rotation = data_bone.matrix_local.to_3x3().normalized()
    return rotation.inverted().to_4x4()


# Replaces the follow constraints with Child Of constraints to the planned bones (position and rotation only,
# no scale). Keyed influence lives in the root's action and is written with the other keys.
def apply_accessory_parent(root, acc, plan):
    for constraint in [c for c in root.constraints if c.name.startswith(ACCESSORY_FOLLOW_PREFIX)]:
        root.constraints.remove(constraint)
    made = 0
    for pair, found in plan["pairs"].items():
        if found is None:
            continue
        armature, bone = found
        constraint = root.constraints.new(type="CHILD_OF")
        constraint.name = parent_constraint_name(plan, pair)
        constraint.target = armature
        constraint.subtarget = bone
        constraint.use_scale_x = False
        constraint.use_scale_y = False
        constraint.use_scale_z = False
        constraint.set_inverse_pending = False
        constraint.inverse_matrix = bone_rest_inverse(armature, bone)
        constraint.influence = 1.0
        made += 1
        log.info("accessory '%s' follows %s / %s", acc.name, armature.name, bone)
    if not made:
        log.info("accessory '%s' has no parent bone", acc.name)
    elif plan["keyed"]:
        log.info("accessory '%s' switches between %d parent bone(s) by key", acc.name, made)
