import bpy
import json
import numpy as np
import os
import re
import time

from ..common.animation import iter_fcurves
from ..common.constants import (
    LIGHT_COLLECTION,
    MME_BACKEND_PROP,
    MME_BAKE_STORE_VERSION,
    MME_CORE_VERSION,
    MME_BAKED_PROP,
    MME_GN_MODIFIER,
    MME_MANIFEST_PROP,
    MME_MANIFEST_VERSION,
    MME_PASS_KEY_PROP,
    MME_PRECULL_VERSION,
)
from ..common.controls import (
    mme_control_use_prop,
    mme_load_controls,
    mme_param_meta,
)
from ..common.digest import (
    brief_inputs,
    canonical,
    describe_diff,
    diff_inputs,
    digest,
    file_digest,
)
from ..common.log import log
from ..common.scene_utils import (
    child_of_type,
    find_rig_root,
    scene_fps,
)
from ..fx.params import fx_vs_globals
from ..xfile.textures import resolve_x_texture
from .bake_node import mme_managed_bake
from .bake_paths import mme_disk_frames
from .bake_scope import mme_bake_scope
from .drivers import mme_handler_uniforms

MME_TRANSFORM_PATHS = (
    "location",
    "rotation_euler",
    "rotation_quaternion",
    "rotation_axis_angle",
    "rotation_mode",
    "scale",
    "delta_location",
    "delta_rotation_euler",
    "delta_rotation_quaternion",
    "delta_scale",
)

LOG_PREFIX = "[NineSync FX]"


def _base_path(path):
    match = re.match(r'^(.*\])\[\d+\]$', path)
    return match.group(1) if match else path


def _rna_values(struct):
    out = []
    for prop in struct.bl_rna.properties:
        if prop.identifier == "rna_type" or prop.type in ("POINTER", "COLLECTION"):
            continue
        try:
            value = getattr(struct, prop.identifier)
        except (AttributeError, RuntimeError):
            continue
        out.append([prop.identifier, value if isinstance(value, (bool, int, float, str)) else list(value) if hasattr(value, "__len__") else str(value)])
    return out


# Everything about an ID's animation that changes what a property evaluates to: every key with its handles,
# interpolation and easing, extrapolation, curve modifiers and NLA strips. A value is None when nothing animates the
# listed paths.
def mme_curve_print(idblock, paths=None):
    anim = getattr(idblock, "animation_data", None)
    if anim is None:
        return None
    curves = []
    if anim.action is not None:
        for fcurve in iter_fcurves(anim.action):
            if paths is not None and _base_path(fcurve.data_path) not in paths:
                continue
            points = fcurve.keyframe_points
            buf = np.zeros(len(points) * 2)
            rows = []
            for attr in ("co", "handle_left", "handle_right"):
                points.foreach_get(attr, buf)
                rows.append(np.round(buf, 5).tolist())
            curves.append(
                [
                    fcurve.data_path,
                    fcurve.array_index,
                    fcurve.extrapolation,
                    bool(fcurve.mute),
                    rows,
                    [p.interpolation for p in points],
                    [p.easing for p in points],
                    [p.handle_left_type for p in points],
                    [p.handle_right_type for p in points],
                    [[m.type, _rna_values(m)] for m in fcurve.modifiers],
                ]
            )
    curves.sort(key=lambda c: (c[0], c[1]))
    tracks = []
    if paths is None or anim.nla_tracks:
        for track in anim.nla_tracks:
            strips = [
                [s.action.name if s.action else "", s.frame_start, s.frame_end, s.blend_type, s.influence, s.scale, s.repeat, bool(s.mute)]
                for s in track.strips
            ]
            tracks.append([track.name, bool(track.mute), strips])
    if not curves and not tracks:
        return None
    return {"curves": curves, "nla": tracks}


def _driven_expression(idblock, base):
    anim = getattr(idblock, "animation_data", None)
    if anim is None:
        return None
    for fcurve in anim.drivers:
        if _base_path(fcurve.data_path) == base:
            return fcurve.driver.expression
    return None


# Adds one property to an inputs dict: its keyed animation if it has any, the driver expression if it is driven (the
# value then follows the frame, so it is not a stable input), otherwise its current value. A property that cannot be
# read is listed as unverifiable.
def mme_add_source(inputs, unverifiable, name, idblock, path):
    base = _base_path(path)
    printed = mme_curve_print(idblock, {base})
    if printed is not None:
        inputs[name] = {"keyed": printed}
        return
    expression = _driven_expression(idblock, base)
    if expression is not None:
        inputs[name] = {"driven": expression}
        return
    try:
        inputs[name] = idblock.path_resolve(path)
    except (ValueError, TypeError, AttributeError, RuntimeError):
        unverifiable.append(name)


MME_DEPENDENCY_DEPTH = 6

MME_BONE_CHANNELS = ("location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "scale", "rotation_mode")

_CONSTRAINT_SKIP = {"influence", "is_valid", "active", "show_expanded", "error_location", "error_rotation"}


def _plain_value(value):
    if isinstance(value, float):
        return round(value, 6)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, bpy.types.ID):
        return "id:" + value.name
    try:
        return [_plain_value(v) for v in value]
    except TypeError:
        return str(value)


def _struct_values(struct, skip=()):
    out = []
    for prop in struct.bl_rna.properties:
        if prop.identifier in skip or prop.identifier == "rna_type" or prop.type in ("POINTER", "COLLECTION"):
            continue
        try:
            out.append([prop.identifier, _plain_value(getattr(struct, prop.identifier))])
        except (AttributeError, RuntimeError):
            continue
    return out


# The one way an external Blender ID that a pass reads (a parent, a constraint or driver target, the camera, the light) enters
# a dependency set. An object contributes its transform sources with their animation, its parent (and parent bone), its
# constraints and its drivers, and the helper recurses into everything those point at. An armature with named bones
# contributes those bones and their ancestors: pose channels with their animation, rest matrices, bone constraints and drivers.
# Anything that cannot be read, or lies deeper than MME_DEPENDENCY_DEPTH, is listed as unverifiable, which never matches and
# never shares.
def mme_dependency_digest(inputs, unverifiable, role, idblock, bones=None, seen=None, depth=0, drivers=True):
    seen = set() if seen is None else seen
    if idblock is None:
        unverifiable.append(role)
        return
    token = (idblock.as_pointer(), tuple(sorted(bones or ())))
    if token in seen:
        return
    seen.add(token)
    if depth > MME_DEPENDENCY_DEPTH:
        unverifiable.append("%s (too deep)" % role)
        return
    if not isinstance(idblock, bpy.types.Object):
        unverifiable.append(role)
        return
    obj = idblock
    for path in MME_TRANSFORM_PATHS:
        mme_add_source(inputs, unverifiable, "%s.%s" % (role, path), obj, path)
    parent_bone = obj.parent_bone if obj.parent_type == "BONE" else ""
    inputs["%s.parent" % role] = [obj.parent.name if obj.parent else "", obj.parent_type, parent_bone, _plain_value(obj.matrix_parent_inverse)]
    if obj.parent is not None:
        mme_dependency_digest(inputs, unverifiable, "%s^" % role, obj.parent, {parent_bone} if parent_bone else None, seen, depth + 1)
    _dependency_constraints(inputs, unverifiable, role, obj, "", obj.constraints, seen, depth)
    anim = obj.animation_data
    if drivers and anim is not None:
        for fcurve in anim.drivers:
            if not fcurve.data_path.startswith("pose.bones["):
                _dependency_driver(inputs, unverifiable, role, fcurve, seen, depth)
    if obj.type == "ARMATURE" and bones:
        _dependency_bones(inputs, unverifiable, role, obj, bones, seen, depth)


def _dependency_constraints(inputs, unverifiable, role, owner, prefix, constraints, seen, depth):
    for constraint in constraints:
        base = "%s.constraint.%s" % (role, constraint.name)
        inputs[base] = [constraint.type, _struct_values(constraint, _CONSTRAINT_SKIP)]
        mme_add_source(inputs, unverifiable, base + ".influence", owner, '%sconstraints["%s"].influence' % (prefix, constraint.name))
        targets = []
        if hasattr(constraint, "targets"):
            targets.extend((t.target, getattr(t, "subtarget", "")) for t in constraint.targets)
        elif hasattr(constraint, "target"):
            targets.append((constraint.target, getattr(constraint, "subtarget", "")))
        inputs[base + ".targets"] = [[t.name if t is not None else "", sub] for t, sub in targets]
        for target, sub in targets:
            if target is not None:
                mme_dependency_digest(inputs, unverifiable, "%s>%s" % (base, target.name), target, {sub} if sub and target.type == "ARMATURE" else None, seen, depth + 1)


def _dependency_bones(inputs, unverifiable, role, arm, bones, seen, depth):
    closure = []
    for name in sorted(bones):
        pose_bone = arm.pose.bones.get(name)
        if pose_bone is None:
            unverifiable.append("%s.bone.%s" % (role, name))
        while pose_bone is not None:
            if pose_bone.name not in closure:
                closure.append(pose_bone.name)
            pose_bone = pose_bone.parent
    anim = arm.animation_data
    for name in sorted(closure):
        pose_bone = arm.pose.bones[name]
        base = "%s.bone.%s" % (role, name)
        data_bone = pose_bone.bone
        inputs[base] = [
            _plain_value(data_bone.matrix_local),
            [bool(getattr(data_bone, attr, False)) for attr in ("use_connect", "use_inherit_rotation", "use_local_location")],
            str(getattr(data_bone, "inherit_scale", "")),
        ]
        prefix = 'pose.bones["%s"].' % name
        for channel in MME_BONE_CHANNELS:
            mme_add_source(inputs, unverifiable, "%s.%s" % (base, channel), arm, prefix + channel)
        _dependency_constraints(inputs, unverifiable, base, arm, prefix, pose_bone.constraints, seen, depth)
        if anim is not None:
            for fcurve in anim.drivers:
                if fcurve.data_path.startswith(prefix[:-1]):
                    _dependency_driver(inputs, unverifiable, base, fcurve, seen, depth)


def _dependency_driver(inputs, unverifiable, role, fcurve, seen, depth):
    driver = fcurve.driver
    where = "%s.driver.%s" % (role, fcurve.data_path)
    inputs[where] = [driver.type, driver.expression, bool(fcurve.mute)]
    for var in driver.variables:
        driver_variable(inputs, unverifiable, where, var, seen, depth)


# One driver variable: a property read is fingerprinted with its animation, a transform channel through the target object's
# whole dependency set (and bone), anything else is unverifiable.
def driver_variable(inputs, unverifiable, where, var, seen, depth=0):
    count = 2 if var.type in ("LOC_DIFF", "ROTATION_DIFF") else 1
    for index, target in enumerate(list(var.targets)[:count]):
        name = "%s.%s%s" % (where, var.name, "" if index == 0 else ".%d" % index)
        if target.id is None:
            unverifiable.append(name)
        elif var.type == "SINGLE_PROP":
            mme_add_source(inputs, unverifiable, "%s.%s%s" % (name, target.id.name, target.data_path), target.id, target.data_path)
        elif var.type in ("TRANSFORMS", "LOC_DIFF", "ROTATION_DIFF") and isinstance(target.id, bpy.types.Object):
            bone = target.bone_target if target.id.type == "ARMATURE" else ""
            inputs[name + ".mode"] = [target.transform_type, target.transform_space, target.rotation_mode, bone]
            mme_dependency_digest(inputs, unverifiable, "%s.%s" % (name, target.id.name), target.id, {bone} if bone else None, seen, depth + 1)
        else:
            unverifiable.append(name)


def _add_chain(inputs, unverifiable, obj, root):
    mme_dependency_digest(inputs, unverifiable, "pass", obj, None, set(), 0, drivers=False)


def _reads_node(tree, idname, memo):
    pointer = tree.as_pointer()
    if pointer in memo:
        return memo[pointer]
    memo[pointer] = False
    for node in tree.nodes:
        if node.bl_idname == idname or (node.bl_idname == "GeometryNodeGroup" and node.node_tree is not None and _reads_node(node.node_tree, idname, memo)):
            memo[pointer] = True
            break
    return memo[pointer]


# The scene camera as a pass sees it: the object with its rig (parents, constraints, bone animation), the lens data with its
# animation and the render size that sets the aspect.
def _camera_dependency(inputs, unverifiable, scene, seen):
    camera = scene.camera
    render = scene.render
    inputs["camera.render"] = [render.resolution_x, render.resolution_y, render.resolution_percentage, render.pixel_aspect_x, render.pixel_aspect_y]
    if camera is None:
        inputs["camera"] = "none"
        return
    inputs["camera"] = camera.name
    mme_dependency_digest(inputs, unverifiable, "camera", camera, None, seen)
    data = camera.data
    if data is None:
        unverifiable.append("camera.data")
        return
    for path in ("type", "lens", "sensor_width", "sensor_height", "sensor_fit", "shift_x", "shift_y", "clip_start", "clip_end", "ortho_scale"):
        mme_add_source(inputs, unverifiable, "camera.data.%s" % path, data, path)


def light_object():
    collection = bpy.data.collections.get(LIGHT_COLLECTION)
    rig = find_rig_root(collection, "LIGHT") if collection else None
    return child_of_type(rig, "LIGHT") if rig else None


# Identity of the Core light a build binds its uniforms to; a recreated light under another name is a changed input.
def mme_light_binding(light=None):
    light = light if light is not None else light_object()
    if light is None:
        return None
    return [light.name, light.data.name if light.data is not None else ""]


def _light_dependency(inputs, unverifiable, seen):
    light = light_object()
    if light is None:
        inputs["light"] = "none"
        return
    inputs["light"] = mme_light_binding(light)
    mme_dependency_digest(inputs, unverifiable, "light", light, None, seen)
    if light.data is None:
        unverifiable.append("light.data")
    else:
        mme_add_source(inputs, unverifiable, "light.data.color", light.data, "color")


# Driver variables on a pass whose target is gone; such a driver evaluates to 0 until the pass is rebuilt (the light rig
# recreated by a clean and sync, a deleted controller object).
def mme_broken_bindings(obj):
    anim = getattr(obj, "animation_data", None)
    if anim is None:
        return []
    broken = []
    for fcurve in anim.drivers:
        for var in fcurve.driver.variables:
            count = 2 if var.type in ("LOC_DIFF", "ROTATION_DIFF") else 1
            if any(target.id is None for target in list(var.targets)[:count]):
                broken.append("%s.%s" % (fcurve.data_path, var.name))
    return broken


# Stable identity of a draw inside an effect root: root identity, technique, pass, material subset and draw order, plus a
# duplicate index when two draws are otherwise identical. The displayed object name is not part of it.
def mme_pass_key(root, draw, seen):
    ident = [root.get("mmd_bridge_accessory") or root.name, draw.technique.name or "", draw.fx_pass.name or "", draw.subset, draw.order]
    text = canonical(ident)
    count = seen.get(text, 0)
    seen[text] = count + 1
    return digest([ident, count])[:16]


def mme_read_manifest(obj):
    try:
        manifest = json.loads(obj.get(MME_MANIFEST_PROP, "null"))
    except (TypeError, ValueError):
        return None
    if not isinstance(manifest, dict) or manifest.get("v") != MME_MANIFEST_VERSION or manifest.get("analysis") != MME_PRECULL_VERSION:
        return None
    return manifest


def mme_write_manifest(obj, manifest):
    manifest["v"] = MME_MANIFEST_VERSION
    manifest["analysis"] = MME_PRECULL_VERSION
    obj[MME_MANIFEST_PROP] = canonical(manifest)


def mme_texture_digest(runtime):
    return digest(_texture_files(runtime))


def _texture_files(runtime):
    out = {}
    for g in runtime.program.globals.values():
        path = runtime.textures.resource_path(g) if getattr(g, "semantic", None) not in ("MATERIALTEXTURE",) else None
        if path:
            out[os.path.basename(path)] = file_digest(path)
    return out


# Inputs shared by every pass of one effect: file contents (effect and includes, mesh, textures the effect names), quality
# settings, animation samples, controls and the root's opacity and scale. Computed once per build.
def mme_shared_inputs(runtime, root, settings, backend, unit, controls, variants_pack):
    files = [file_digest(path) for path in runtime.program.files]
    return {
        "fx": digest(files),
        "x": file_digest(runtime.x_path),
        "textures": digest(_texture_files(runtime)),
        "density": float(settings.mme_density),
        "tolerance": float(getattr(settings, "mme_tolerance", 0.0)),
        "precull": bool(getattr(settings, "mme_precull", True)),
        "backend": backend,
        "unit": float(unit),
        "variants": digest(variants_pack),
        "controls": controls,
        "tr": float(root.get("mme_tr", 1.0)),
        "si": float(root.get("mme_si", 1.0)),
        "light": mme_light_binding(),
    }


def _material_inputs(runtime, subset):
    xmat = runtime.materials[subset] if subset < len(runtime.materials) else None
    if xmat is None:
        return None
    texture = ""
    if xmat.texture:
        first = xmat.texture.split("*")[0].strip()
        if first and not first.lower().endswith((".sph", ".spa")):
            found = resolve_x_texture(first, runtime.x_folder)
            texture = file_digest(found) if found else "missing"
    return [list(map(float, xmat.diffuse)), list(map(float, xmat.specular)), float(xmat.power), list(map(float, xmat.emissive)), texture]


# What decides whether a pass has to be rebuilt: the shared inputs plus this draw's identity and loops, the effect parameters
# and controls its vertex or pixel shader reads (the pixel shader matters because alpha gates prune geometry), its material
# subset, and, for the Python backend whose vertices are written in world space, the root's transform.
# Returns the inputs and the names the shaders read.
def mme_build_inputs(shared, runtime, draw, root, scene_controls):
    program = runtime.program
    reads = set(fx_vs_globals(program, [draw.fx_pass], ("vs", "ps"))) | {name for _var, _count, name in draw.loops if name}
    params = getattr(program, "params", {})
    meta = mme_param_meta(root)
    inputs = {
        "schema": [MME_MANIFEST_VERSION, MME_PRECULL_VERSION],
        "fx": shared["fx"],
        "x": shared["x"],
        "textures": shared["textures"],
        "draw": [draw.technique.name or "", draw.fx_pass.name or "", draw.subset, draw.order],
        "loops": [[var, str(count), name] for var, count, name in draw.loops],
        "density": shared["density"],
        "tolerance": shared["tolerance"],
        "precull": shared["precull"],
        "backend": shared["backend"],
        "unit": shared["unit"],
        "variants": shared["variants"],
        "tr": shared["tr"],
        "si": shared["si"],
        "light": shared["light"],
        "material": _material_inputs(runtime, draw.subset),
    }
    unverifiable = []
    for name in sorted(reads):
        if name in params and name in meta:
            prop = meta[name]["prop"]
            mme_add_source(inputs, unverifiable, "param.%s" % name, root, '["%s"]' % prop)
        elif name in scene_controls:
            desc = scene_controls[name]
            inputs["control.%s" % name] = desc
            for prop in (desc.get("prop"), mme_control_use_prop(desc)):
                if prop:
                    mme_add_source(inputs, unverifiable, "controlprop.%s" % prop, root, '["%s"]' % prop)
    if shared["backend"] == "PYTHON":
        inputs["world"] = [list(row) for row in root.matrix_basis]
        mme_dependency_digest(inputs, unverifiable, "root", root, None, set(), 0)
    if unverifiable:
        inputs["unverifiable"] = sorted(unverifiable)
    return inputs, reads


def mme_sig(inputs):
    return digest(inputs)


def mme_log_action(action, label, reason, backend="", seconds=None, changes=None, detail=False):
    extra = []
    if backend:
        extra.append(backend)
    if seconds is not None:
        extra.append("%.2fs" % seconds)
    text = "%s %s %s: %s%s" % (LOG_PREFIX, action, label, reason, " [%s]" % ", ".join(extra) if extra else "")
    log.info(text)
    if detail and changes:
        for name, before, after in changes:
            log.info("%s     %s: %s -> %s", LOG_PREFIX, name, canonical(before), canonical(after))


# Decides what to do with one planned draw given the pass object that already carries its key (or None): returns the action
# name, the reason and the changed inputs. SKIP BUILD only when the stored build signature equals the current one and the
# object is intact.
def mme_build_decision(existing, sig, inputs, force):
    if force:
        return "BUILD", "forced rebuild of all passes", []
    if existing is None:
        return "BUILD", "new pass", []
    manifest = mme_read_manifest(existing)
    if manifest is None:
        return "BUILD", "no current-version manifest (older file or analysis version changed)", []
    build = manifest.get("build") or {}
    if existing.modifiers.get(MME_GN_MODIFIER) is None or existing.data is None:
        return "BUILD", "pass object lost its Geometry Nodes modifier or mesh", []
    broken = mme_broken_bindings(existing)
    if broken:
        return "BUILD", "driver target missing (%s); rebinding" % ", ".join(broken[:3]), []
    if build.get("sig") == sig:
        return "SKIP BUILD", "build signature unchanged; reused object and node tree", []
    changes = diff_inputs(build.get("inputs", {}), inputs)
    category = "shared dependency" if any(name in ("fx", "x", "textures", "density", "tolerance", "precull", "backend", "unit", "variants", "schema") for name, _b, _a in changes) else "pass dependency"
    return "BUILD", "%s: %s" % (category, describe_diff(changes) if changes else "signature changed"), changes


def mme_record_build(obj, key, sig, inputs, backend):
    obj[MME_PASS_KEY_PROP] = key
    manifest = mme_read_manifest(obj) or {}
    manifest["key"] = key
    manifest["build"] = {"sig": sig, "inputs": brief_inputs(inputs), "backend": backend, "time": int(time.time())}
    manifest["bake"] = {"state": "none"}
    mme_write_manifest(obj, manifest)


def mme_pass_by_key(root):
    out = {}
    for child in root.children:
        key = child.get(MME_PASS_KEY_PROP)
        if key and child.type == "MESH":
            out.setdefault(key, child)
    return out


# The root properties that can reach a pass without a driver of its own: effect parameters, controller properties and
# use-switches, opacity and scale.
def _root_wide(inputs, unverifiable, root):
    meta = mme_param_meta(root)
    props = {info["prop"] for info in meta.values() if info.get("prop")}
    for desc in mme_load_controls(root).values():
        props.add(desc.get("prop") or "")
        props.add(mme_control_use_prop(desc) or "")
    props |= {"mme_tr", "mme_si"}
    for prop in sorted(p for p in props if p and p in root):
        mme_add_source(inputs, unverifiable, "root.%s" % prop, root, '["%s"]' % prop)


# Everything that decides the geometry and attributes reaching the pass's Bake node over the baked range: the build
# signature, the frame range and frame rate, the bake target, the transform chain, the modifier's undriven inputs, and
# the exact sources of every driver (variable targets with their animation). A pass on the Python backend, or one with
# uniforms that only the frame handler sets, depends on the whole set of root properties instead. A dependency that
# cannot be read makes the result unverifiable, which never matches.
def mme_bake_inputs(obj, root, scene, settings):
    manifest = mme_read_manifest(obj)
    unverifiable = []
    inputs = {
        "build": ((manifest or {}).get("build") or {}).get("sig"),
        "frames": [int(scene.frame_start), int(scene.frame_end)],
        "fps": [scene_fps(scene), float(scene.render.fps_base)],
        "target": settings.mme_bake_target,
    }
    if manifest is None or inputs["build"] is None:
        unverifiable.append("build manifest")
    if settings.mme_bake_target == "DISK":
        if getattr(settings, "mme_bake_shared", False):
            inputs["layout"] = "shared"
        else:
            inputs["directory"] = digest(settings.mme_bake_dir)
            inputs["layout"] = "pass" if getattr(settings, "mme_bake_pass_folders", False) else "modifier"
    _add_chain(inputs, unverifiable, obj, root)
    modifier = obj.modifiers.get(MME_GN_MODIFIER)
    anim = obj.animation_data
    driven = [fc.data_path for fc in anim.drivers] if anim is not None else []
    group = modifier.node_group if modifier is not None else None
    graph, reach = mme_bake_scope(modifier)
    if graph is not None:
        inputs["graph"] = graph
    if group is not None:
        held = modifier.properties.inputs
        for item in group.interface.items_tree:
            key = getattr(item, "identifier", None)
            if item.item_type != "SOCKET" or item.in_out != "INPUT" or not key:
                continue
            if reach is not None and key not in reach:
                continue
            if any(re.search(r"(?<!\w)%s(?!\w)" % re.escape(key), path) for path in driven):
                continue
            entry = getattr(held, key, None)
            value = getattr(entry, "value", None)
            if value is None:
                continue
            if isinstance(value, bpy.types.ID):
                value = value.name
            elif not isinstance(value, (bool, int, float, str)):
                try:
                    value = [round(float(v), 6) for v in value]
                except TypeError:
                    unverifiable.append("modifier.%s" % key)
                    continue
            inputs["modifier.%s" % key] = value
    wide = obj.get(MME_BACKEND_PROP, "PYTHON") == "PYTHON" or bool(mme_handler_uniforms(obj))
    if wide:
        _root_wide(inputs, unverifiable, root)
    seen = set()
    if anim is not None:
        for fcurve in anim.drivers:
            inputs["driver.%s" % fcurve.data_path] = fcurve.driver.expression
            for var in fcurve.driver.variables:
                driver_variable(inputs, unverifiable, "driver.%s" % fcurve.data_path, var, seen)
    if wide or (group is not None and _reads_node(group, "GeometryNodeInputActiveCamera", {})):
        _camera_dependency(inputs, unverifiable, scene, seen)
    if wide:
        _light_dependency(inputs, unverifiable, seen)
    if unverifiable:
        inputs["unverifiable"] = sorted(set(unverifiable))
    return inputs


# Address of a bake in the shared store: the bake inputs with everything that is only a place or a name left out (bake folder,
# layout, the pass and root object names), plus the Blender, add-on and store versions, so the same effect baked over the same
# frames from the same animation gets the same address in any project and in either add-on. None when an input cannot be
# verified, which never shares.
def mme_portable_bake_sig(obj, root, inputs):
    if inputs.get("unverifiable"):
        return None
    text = canonical({name: value for name, value in inputs.items() if name not in ("directory", "layout", "target")})
    for name, token in sorted(((obj.name, "<pass>"), (root.name, "<root>")), key=lambda item: -len(item[0])):
        text = text.replace(name, token)
    return digest([text, list(bpy.app.version), MME_CORE_VERSION, MME_BAKE_STORE_VERSION])


# Whether Blender still holds the bake this pass recorded. Returns (True, ...) when confirmed, (False, reason) when it is
# known to be missing or stored somewhere else, and (None, ...) when this Blender exposes nothing to ask. A disk bake is
# checked frame by frame in the pass's own folder: Blender 5.3 reports a finished bake even when it could not resolve
# the folder and fell back to packed storage.
def mme_bake_storage(obj, settings, frames=None):
    modifier = obj.modifiers.get(MME_GN_MODIFIER)
    bake = mme_managed_bake(modifier)
    if bake is None:
        return False, "no bake node"
    for name in ("is_baked", "is_bake_valid", "has_baked_data"):
        if hasattr(bake, name):
            ok = bool(getattr(bake, name))
            return ok, "Blender reports %s=%s" % (name, ok)
    record = (mme_read_manifest(obj) or {}).get("bake") or {}
    recorded = record.get("target")
    if recorded and recorded != getattr(modifier, "bake_target", recorded):
        return False, "bake target is no longer %s" % recorded
    if getattr(modifier, "bake_target", "") == "DISK":
        start, end = frames or record.get("frames") or (None, None)
        if start is None:
            return False, "no frame range recorded"
        found, total, folder = mme_disk_frames(obj, int(start), int(end))
        if found < total:
            return False, "only %d of %d frames are stored in %s" % (found, total, folder or "the bake folder")
        return True, "all %d frames stored on disk" % total
    return None, "packed bake not queryable through the API; recorded state trusted"


def mme_bake_state(obj):
    return ((mme_read_manifest(obj) or {}).get("bake") or {}).get("state", "none")


def mme_set_bake_record(obj, **fields):
    manifest = mme_read_manifest(obj)
    if manifest is None:
        return
    bake = manifest.get("bake") or {}
    bake.update(fields)
    manifest["bake"] = bake
    mme_write_manifest(obj, manifest)


# Compares a pass's recorded bake with the current inputs. Returns (valid, reason, changes).
def mme_bake_decision(obj, root, scene, settings, force):
    inputs = mme_bake_inputs(obj, root, scene, settings)
    sig = digest(inputs)
    if force:
        return False, "forced rebake of all passes", [], inputs, sig
    manifest = mme_read_manifest(obj)
    record = (manifest or {}).get("bake") or {}
    if record.get("state") != "baked" or not obj.get(MME_BAKED_PROP):
        return False, "no valid bake (state %s)" % record.get("state", "none"), [], inputs, sig
    if inputs.get("unverifiable"):
        return False, "dependency cannot be verified: %s" % ", ".join(inputs["unverifiable"][:3]), [], inputs, sig
    if record.get("sig") != sig:
        changes = diff_inputs(record.get("inputs", {}), inputs)
        return False, describe_diff(changes) if changes else "signature changed", changes, inputs, sig
    ok, detail = mme_bake_storage(obj, settings)
    if ok is False:
        return False, "stored bake not available: %s" % detail, [], inputs, sig
    return True, "signature matched, frames %d-%d, %s" % (scene.frame_start, scene.frame_end, detail), [], inputs, sig


# Marks baked passes of a root whose inputs no longer match as stale, without touching the bake. Returns their objects.
def mme_mark_stale_bakes(root, scene, settings, detail=False):
    stale = []
    for child in root.children:
        if child.get(MME_PASS_KEY_PROP) is None or not child.get(MME_BAKED_PROP) or mme_bake_state(child) != "baked":
            continue
        valid, reason, changes, _inputs, _sig = mme_bake_decision(child, root, scene, settings, False)
        if valid:
            continue
        mme_set_bake_record(child, state="stale", stale_reason=reason[:200])
        mme_log_action("STALE", child.name, reason, child.get(MME_BACKEND_PROP, ""), changes=changes, detail=detail)
        stale.append(child)
    return stale
