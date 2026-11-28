"""Resolve MME control annotations and build native Blender driver variables."""

import json
from dataclasses import dataclass

from ...fx.errors import FxUnsupported

CONTROL_DATA = "ninesync_middle_controls"
CONTROL_REF = "ninesync_middle_ref_"
CONTROL_REST = "ninesync_middle_bone_rest"
CONTROL_RIG = "ninesync_middle_bone_rig"
CONTROL_REST_LIVE = "ninesync_middle_bone_rest_live"
CONTROL_REST_DATA = "ninesync_middle_bone_rest_data"
CONTROL_ABSENT_MORPHS = "ninesync_middle_absent_morphs"
CONTROL_ROTATION_MODE = "ninesync_middle_rotation_mode"
AXES = "XZY"


@dataclass(frozen=True)
class DriverSource:
    id: object
    path: str = ""
    axis: str = ""
    bone: str = ""
    space: str = "WORLD_SPACE"
    scale: float = 1.0
    index: int | None = None
    transform: str = "LOC"
    rotation_mode: str = "XYZ"


@dataclass(frozen=True)
class DriverExpression:
    sources: tuple
    expression: str
    rest: tuple = ()


class AbsentMorph(float):
    """Native MME reads an absent scalar PMX morph as zero, including with an initializer."""


def _bone(armature, item):
    if not armature or not item:
        return None
    matches = [bone for bone in armature.pose.bones if item in (
        bone.name, getattr(getattr(bone, "mmd_bone", None), "name_j", ""),
        getattr(getattr(bone, "mmd_bone", None), "name_e", ""))]
    if len(matches) > 1:
        raise FxUnsupported(f"Controller bone {item!r} is ambiguous in {armature.name!r}.")
    return matches[0] if matches else None


def _bone_matrix(armature, bone, units):
    # Native MME exposes a bone-head world transform, with identity rotation at
    # rest. Blender's pose matrix additionally contains its edit-bone orientation.
    # RNA driver paths index matrix columns before rows.
    # Edit-bone rest matrices contain an orthonormal orientation. Read its
    # transpose live rather than baking an inverse that becomes stale after
    # roll/head/tail edits. The resulting drivers remain native RNA drivers,
    # including in reopened files with no Python update handler installed.
    rest = (armature, bone.name, tuple(v for row in bone.bone.matrix_local for v in row))
    pose = bone.path_from_id("matrix")
    rest_path = bone.bone.path_from_id("matrix_local")
    swap = (0, 2, 1)
    values = []
    for row in range(4):
        for col in range(4):
            if col == 3:
                values.append(float(row == 3))
                continue
            i = swap[col]
            sources = [(f"w{a}", DriverSource(armature, f"matrix_world[{a}]", index=i)) for a in range(3)]
            if row == 3:
                sources += [("t", DriverSource(armature, "matrix_world[3]", index=i))]
                sources += [(f"p{a}", DriverSource(armature, f"{pose}[3]", index=a)) for a in range(3)]
                expression = f"(t+w0*p0+w1*p1+w2*p2)/{units!r}"
            else:
                j = swap[row]
                sources += [(f"p{a}{b}", DriverSource(armature, f"{pose}[{b}]", index=a))
                            for a in range(3) for b in range(3)]
                sources += [(f"r{b}", DriverSource(armature.data, f"{rest_path}[{b}]", index=j)) for b in range(3)]
                terms = [f"w{a}*(p{a}0*r0+p{a}1*r1+p{a}2*r2)" for a in range(3)]
                expression = "+".join(terms)
            values.append(DriverExpression(tuple(sources), expression, rest))
    return values


def _resolve_object(scene, obj, name, bindings, offscreen_owner=None):
    if name.lower() == "(self)":
        return obj
    if name.lower() == "(offscreenowner)":
        if offscreen_owner is None:
            raise FxUnsupported("CONTROLOBJECT (OffscreenOwner) requires an offscreen render pass.")
        if scene.objects.get(offscreen_owner.name) != offscreen_owner:
            raise FxUnsupported("The offscreen owner is outside the executing pass scene.")
        return offscreen_owner
    if name in bindings:
        found = bindings[name]
        if found and scene.objects.get(found.name) == found:
            return found
        raise FxUnsupported(f"Controller {name!r} is outside the destination scene.")
    exact = scene.objects.get(name)
    matches = [candidate for candidate in scene.objects if candidate.get("ninesync_mme_name") == name]
    if exact and exact not in matches:
        matches.append(exact)
    if len(matches) != 1:
        reason = "ambiguous" if matches else "missing"
        raise FxUnsupported(f"Controller {name!r} is {reason}. Supply an explicit scene object mapping.")
    return matches[0]


def resolve(scene, obj, name, item, size, bindings, units, *, base="f", dims=None, offscreen_owner=None):
    if not name:
        raise FxUnsupported("CONTROLOBJECT requires a name annotation.")
    try:
        target = _resolve_object(scene, obj, name, bindings, offscreen_owner)
    except FxUnsupported:
        # A missing optional object has a defined false presence flag. Explicit
        # stale mappings, ambiguous names and pass-specific owners remain errors.
        matches = [candidate for candidate in scene.objects
                   if name in (candidate.name, candidate.get("ninesync_mme_name"))]
        if base == "b" and not item and not name.startswith("(") and name not in bindings and not matches:
            return None, [0.0]
        raise
    lower = item.lower()
    if base == "b" and size == 1 and not item:
        return target, [DriverExpression((("hidden", DriverSource(target, "hide_render")),), "1 - hidden")]
    accessory = name.lower().endswith(".x") or target.get("mmd_bridge_accessory") is not None
    if dims is not None and tuple(dims) == (4, 4) and not item:
        if not accessory:
            # MMD applies model placement through bones; the PMD/PMX object's
            # CONTROLOBJECT world matrix itself remains identity.
            return target, [float(row == col) for row in range(4) for col in range(4)]
        swap = (0, 2, 1)
        values = []
        for row in range(4):
            for col in range(4):
                if col == 3:
                    values.append(float(row == 3))
                else:
                    column = 3 if row == 3 else swap[row]
                    values.append(DriverSource(target, f"matrix_world[{column}]", index=swap[col],
                                               scale=1 / units if row == 3 else 10.0))
        return target, values
    if not item and size in (3, 4):
        values = [DriverSource(target, axis=a, scale=1 / units) for a in AXES]
        return target, values + ([1.0] if size == 4 else [])
    if size == 1 and (lower == "si" or (not item and base == "f")):
        if not accessory:
            return target, [1.0]
        if "mme_si" in target:
            return target, [DriverSource(target, '["mme_si"]', scale=10.0)]
        return target, [DriverSource(target, axis="X", transform="SCALE", space="LOCAL_SPACE", scale=10.0)]
    if size == 1 and lower == "tr" and accessory:
        if "mme_tr" in target:
            return target, [DriverSource(target, '["mme_tr"]')]
        return target, [1.0]
    rotations = {"rx": (0,), "ry": (1,), "rz": (2,), "rxyz": (0, 1, 2)}
    if accessory and lower in rotations and size == len(rotations[lower]):
        if target.rotation_mode in {"XYZ", "YXZ"}:
            # Imported MMD accessory keys use YXZ. Keep their authored angles,
            # including whole turns, rather than decomposing away that history.
            return target, [DriverSource(target, "rotation_euler", index=(0, 2, 1)[a], scale=-1.0,
                                         rotation_mode=target.rotation_mode) for a in rotations[lower]]
        if target.rotation_mode == "QUATERNION":
            # A quaternion has no authored Euler turn count. Use Blender's
            # deterministic YXZ decomposition, the same order as PMM imports.
            # Native transform driver variables keep this live without handlers.
            return target, [DriverSource(target, axis=AXES[a], space="TRANSFORM_SPACE", transform="ROT",
                                         scale=-1.0, rotation_mode="YXZ") for a in rotations[lower]]
        raise FxUnsupported("Accessory rotation bindings require XYZ/YXZ Euler or quaternion rotation mode.")
    axis = {"x": 0, "y": 1, "z": 2}.get(item.lower())
    if size == 1 and axis is not None:
        return target, [DriverSource(target, axis=AXES[axis], space="LOCAL_SPACE", scale=1 / units)]
    if size == 3 and item.lower() == "xyz":
        return target, [DriverSource(target, axis=a, space="LOCAL_SPACE", scale=1 / units) for a in AXES]
    keys = getattr(target.data, "shape_keys", None)
    key = keys.key_blocks.get(item) if keys and item else None
    if size == 1 and key:
        return target, [DriverSource(keys, path=key.path_from_id("value"))]
    if size == 1 and base == "f" and item:
        # Non-vertex PMX morphs live on MMD Tools' existing unified slider owner.
        from .material_morphs import model_root
        root = model_root(target)
        owners = [(child, getattr(child.data, "shape_keys", None)) for child in root.children
                  if child.type == "MESH" and getattr(child, "mmd_type", "") == "PLACEHOLDER"] if root else []
        matches = [keys for _owner, keys in owners if keys and not keys.reference_key.mute and item in keys.key_blocks]
        if len(matches) > 1:
            raise FxUnsupported(f"CONTROLOBJECT {name!r} morph {item!r} has ambiguous MMD Tools sliders.")
        if matches:
            return target, [DriverSource(matches[0], path=matches[0].key_blocks[item].path_from_id("value"))]
    armature = target if target.type == "ARMATURE" else target.find_armature()
    bone = _bone(armature, item)
    if dims is not None and tuple(dims) == (4, 4) and bone:
        return target, _bone_matrix(armature, bone, units)
    if size in (3, 4) and bone:
        axes = [DriverSource(armature, axis=a, bone=bone.name, scale=1 / units) for a in AXES]
        return target, axes + ([1.0] if size == 4 else [])
    if size == 1 and base == "f" and item and target.type == "MESH" and root is not None:
        return target, [AbsentMorph(0.0)]
    raise FxUnsupported(f"CONTROLOBJECT {name!r} item {item!r} has no supported binding for {size} component(s).")


def _variable(driver, name, source):
    var = driver.variables.new()
    var.name = name
    target = var.targets[0]
    if source.axis:
        var.type = "TRANSFORMS"
        target.id = source.id
        target.transform_type = source.transform + "_" + source.axis
        target.transform_space = source.space
        if source.transform == "ROT":
            target.rotation_mode = source.rotation_mode
        if source.bone:
            target.bone_target = source.bone
    else:
        var.type = "SINGLE_PROP"
        target.id_type = {"Material": "MATERIAL", "Scene": "SCENE", "Key": "KEY",
                          "Object": "OBJECT", "PointLight": "LIGHT", "SunLight": "LIGHT",
                          "AreaLight": "LIGHT", "SpotLight": "LIGHT", "Armature": "ARMATURE"}[source.id.bl_rna.identifier]
        target.id = source.id
        target.data_path = source.path if source.index is None else f"{source.path}[{source.index}]"


def wire(socket, source):
    socket.driver_remove("default_value")
    for key in (CONTROL_REST, CONTROL_RIG, CONTROL_REST_LIVE, CONTROL_REST_DATA, CONTROL_ROTATION_MODE):
        if key in socket.node:
            del socket.node[key]
    if isinstance(source, (int, float)):
        socket.default_value = source
        return
    driver = socket.driver_add("default_value").driver
    driver.type = "SCRIPTED"
    if isinstance(source, DriverExpression):
        for name, value in source.sources:
            _variable(driver, name, value)
        driver.expression = source.expression
        if source.rest:
            armature, bone, matrix = source.rest
            socket.node[CONTROL_RIG] = armature
            socket.node[CONTROL_REST] = json.dumps((bone, matrix))
            socket.node[CONTROL_REST_LIVE] = True
            socket.node[CONTROL_REST_DATA] = armature.data
    else:
        _variable(driver, "value", source)
        driver.expression = "value" if source.scale == 1.0 else f"value * {source.scale!r}"
        if source.path == "rotation_euler" or source.transform == "ROT":
            socket.node[CONTROL_ROTATION_MODE] = source.id.rotation_mode


def wire_time(socket, scene):
    driver = socket.driver_add("default_value").driver
    driver.type = "SCRIPTED"
    _variable(driver, "fps", DriverSource(scene, "render.fps"))
    _variable(driver, "base", DriverSource(scene, "render.fps_base"))
    driver.expression = "frame * base / fps"


def saved_bindings(core):
    bindings = {}
    for global_name, name, *_ in json.loads(core.get(CONTROL_DATA, "[]")):
        key = CONTROL_REF + global_name
        # Retain a missing ID as an explicit failure and avoid silently attaching to a replacement.
        bindings[name] = core.get(key)
    return bindings


def binding_diagnostics(core, scene, *, include_stored=True):
    messages = []
    for global_name, item in json.loads(core.get(CONTROL_ABSENT_MORPHS, "[]")):
        target = core.get(CONTROL_REF + global_name)
        if not target or scene.objects.get(target.name) != target:
            continue
        try:
            _target, sources = resolve(scene, target, "(self)", item, 1, {}, core["ninesync_middle_units"])
            if not isinstance(sources[0], AbsentMorph):
                messages.append(f"{global_name}: previously absent morph {item!r} is now present; rebuild the effect.")
        except FxUnsupported as error:
            messages.append(f"{global_name}: {error}")
    for name in json.loads(core.get("ninesync_middle_optional_controls", "[]")):
        if any(name in (candidate.name, candidate.get("ninesync_mme_name")) for candidate in scene.objects):
            messages.append(f"Optional controller {name!r} is now present; rebuild the effect.")
    curves = {curve.data_path: curve for curve in core.animation_data.drivers} if core.animation_data else {}
    for global_name, name, item, _size, nodes, *_ in json.loads(core.get(CONTROL_DATA, "[]")):
        target = core.get(CONTROL_REF + global_name)
        if not target or scene.objects.get(target.name) != target:
            messages.append(f"{global_name}: controller {name!r} is missing from the scene.")
            continue
        contextual_owner = name.lower() == "(offscreenowner)" and core.get("ninesync_middle_offscreen_owner") == target
        if include_stored and name.lower() != "(self)" and not contextual_owner and name not in (target.name, target.get("ninesync_mme_name")):
            messages.append(f"{global_name}: {name!r} uses the stored object {target.name!r}.")
        for node_name in filter(None, nodes):
            node = core.nodes.get(node_name)
            if node and CONTROL_ROTATION_MODE in node and target.rotation_mode != node[CONTROL_ROTATION_MODE]:
                messages.append(f"{global_name}: controller rotation mode changed; rebuild the effect.")
                break
            if node and CONTROL_REST in node:
                rig = node.get(CONTROL_RIG)
                if node.get(CONTROL_REST_LIVE) and rig and rig.data != node.get(CONTROL_REST_DATA):
                    messages.append(f"{global_name}: controller armature data changed; rebuild the effect.")
                    break
                bone_name, matrix = json.loads(node[CONTROL_REST])
                bone = rig.data.bones.get(bone_name) if rig and rig.type == "ARMATURE" else None
                if bone is None and rig and rig.type == "ARMATURE":
                    try:
                        renamed = _bone(rig, item)
                    except FxUnsupported as error:
                        messages.append(f"{global_name}: {error}")
                        break
                    bone = renamed.bone if renamed else None
                if bone is None:
                    messages.append(f"{global_name}: stored controller bone {item!r} is unavailable.")
                    break
                if bone and not node.get(CONTROL_REST_LIVE) and any(abs(a-b) > 1e-6 for a, b in zip(
                        matrix, (v for row in bone.matrix_local for v in row))):
                    messages.append(f"{global_name}: controller bone {item!r} rest pose changed; rebuild the effect.")
                    break
            curve = curves.get(node.outputs[0].path_from_id("default_value")) if node else None
            variables = curve.driver.variables if curve else ()
            if not variables or not all(_valid_variable(variable.targets[0], scene) for variable in variables):
                messages.append(f"{global_name}: stored controller item {item!r} is unavailable.")
                break
    return tuple(messages)


def _valid_variable(target, scene=None):
    if not target.id:
        return False
    if scene is not None and target.id.bl_rna.identifier == "Object":
        if scene.objects.get(target.id.name) != target.id:
            return False
    if target.bone_target:
        return target.id.pose.bones.get(target.bone_target) is not None
    if target.data_path:
        try:
            target.id.path_resolve(target.data_path)
        except ValueError:
            return False
    if scene is not None and target.id.bl_rna.identifier == "Key":
        if not any(getattr(obj.data, "shape_keys", None) == target.id for obj in scene.objects):
            return False
    if scene is not None and target.id.bl_rna.identifier == "Armature":
        if not any(obj.type == "ARMATURE" and obj.data == target.id for obj in scene.objects):
            return False
    return True


def revive_invalid_controls(scene=None):
    """Recover owned native drivers after Blender fixes renamed RNA paths.

    Blender can leave a driver invalid after auto-renaming a bone path. Only
    retry invalid generated drivers whose every source currently resolves;
    missing artist data must continue to produce a binding diagnostic.
    """
    import bpy
    revived = 0
    for tree in bpy.data.node_groups:
        if tree.get(CONTROL_DATA, "[]") == "[]" or not tree.animation_data:
            continue
        for curve in tree.animation_data.drivers:
            driver = curve.driver
            if driver.is_valid or not driver.variables:
                continue
            targets = [variable.targets[0] for variable in driver.variables]
            if not all(_valid_variable(target, scene) for target in targets):
                continue
            driver.expression = driver.expression
            tree.update_tag()
            revived += 1
    return revived


def retarget(core, scene, obj, name, replacement, *, validate_only=False):
    if name.lower() == "(self)":
        raise FxUnsupported("(self) always resolves to the destination object.")
    plans = []
    for global_name, ref, item, size, nodes, *metadata in json.loads(core.get(CONTROL_DATA, "[]")):
        if ref == name:
            base, dims = metadata if metadata else ("f", None)
            target, sources = resolve(scene, obj, ref, item, size, {name: replacement}, core["ninesync_middle_units"],
                                      base=base, dims=dims)
            plans.append((global_name, target, [(core.nodes[n].outputs[0], src)
                                                 for n, src in zip(nodes, sources) if n]))
    if not plans:
        raise FxUnsupported(f"No generated input reads controller {name!r}.")
    if validate_only:
        return
    for global_name, target, sockets in plans:
        for socket, source in sockets:
            wire(socket, source)
        core[CONTROL_REF + global_name] = target
