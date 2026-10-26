import bpy
import importlib
import json
import os
import time
import traceback
import types
from . import hooks
from mathutils import Matrix

from .common import (
    ACCESSORY_FOLLOW_PREFIX,
    ACCESSORY_OWNED_PATHS,
    ACCESSORY_PREFIX,
    ACCESSORY_UNIT,
    ACTION_PREFIX,
    MME_BACKEND_PROP,
    MME_BAKED_PROP,
    MME_CONTROL_PREFIX,
    MME_DRAW_PROP,
    X_IMPORTER_MODULES,
    X_IMPORTER_URL,
    _mme_runtimes,
    accessory_collection,
    accessory_key,
    find_accessory_root,
    find_armature,
    iter_fcurves,
    log,
    mme_effect_passes,
    mme_effect_roots,
    mme_ensure_use_props,
    mme_load_controls,
    model_key,
    node_input,
    node_socket,
    pose_bone_name,
    short_name,
    short_text,
)
from .util import (
    addon_loaded,
    current_scale,
    frame_span,
    purge_actions,
    restore_points,
    snapshot_outside,
    target_usable,
)
from .pmm import (
    AccessoryKey,
    PmmProject,
)
from .xfile import (
    merge_x_meshes,
    parse_x_bytes,
    resolve_x_texture,
    x_header_mode,
)
from .mme import (
    effect_setup,
    mme_build_effect,
    mme_clear_root_props,
    mme_effect_stamp,
    mme_find_effect,
    mme_key_morph_props,
    mme_link_visibility,
    mme_purge_owned,
    mme_update_scene,
    remove_accessory_children,
    remove_accessory_children_single,
    remove_subset_faces,
)


def x_importer_available():
    return addon_loaded(X_IMPORTER_MODULES)


def refresh_accessories(settings, project):
    wanted = [accessory_key(a) for a in project.accessories]
    for index in reversed(range(len(settings.accessories))):
        entry = settings.accessories[index]
        if entry.key not in wanted and not entry.standalone:
            settings.accessories.remove(index)
    present = {e.key: e for e in settings.accessories}
    for acc in project.accessories:
        key = accessory_key(acc)
        if key not in present:
            entry = settings.accessories.add()
            entry.key = key
            entry.display = acc.name
            entry.enabled = True
            log.info("added accessory slot %s", key)
    if project.accessory_error:
        log.warning("accessory section could not be read: %s", project.accessory_error)


def find_accessory_file(acc, settings):
    pmm_dir = os.path.dirname(bpy.path.abspath(settings.filepath))
    base = acc.path.replace("\\", "/").rsplit("/", 1)[-1] or acc.name
    candidates = [acc.path]
    if settings.accessory_dir:
        candidates.append(os.path.join(bpy.path.abspath(settings.accessory_dir), base))
    candidates.append(os.path.join(pmm_dir, base))
    candidates.append(os.path.join(pmm_dir, "Accessory", base))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def add_color_mix(nodes, links, blend, a_socket, b_socket):
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = blend
    mix.inputs[0].default_value = 1.0
    links.new(a_socket, node_socket(mix, "A_Color"))
    links.new(b_socket, node_socket(mix, "B_Color"))
    return node_socket(mix, "Result_Color", outputs=True)


def add_sphere_map(nodes, links, image, additive, color_socket):
    geometry = nodes.new("ShaderNodeNewGeometry")
    transform = nodes.new("ShaderNodeVectorTransform")
    transform.vector_type = "NORMAL"
    transform.convert_from = "WORLD"
    transform.convert_to = "CAMERA"
    scale = nodes.new("ShaderNodeVectorMath")
    scale.operation = "MULTIPLY_ADD"
    scale.inputs[1].default_value = (0.5, 0.5, 0.0)
    scale.inputs[2].default_value = (0.5, 0.5, 0.0)
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.extension = "EXTEND"
    links.new(geometry.outputs["Normal"], transform.inputs["Vector"])
    links.new(transform.outputs["Vector"], scale.inputs[0])
    links.new(scale.outputs["Vector"], texture.inputs["Vector"])
    return add_color_mix(
        nodes,
        links,
        "ADD" if additive else "MULTIPLY",
        color_socket,
        texture.outputs["Color"],
    )


def build_x_material(name, xmat, folder, settings):
    mat = bpy.data.materials.new(name)
    tree = mat.node_tree
    nodes, links = tree.nodes, tree.links
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        output = next(
            (n for n in nodes if n.type == "OUTPUT_MATERIAL"), None
        ) or nodes.new("ShaderNodeOutputMaterial")
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    r, g, b, a = xmat.diffuse
    base = node_input(bsdf, "Base Color")
    base.default_value = (r, g, b, 1.0)
    alpha = node_input(bsdf, "Alpha")
    alpha.default_value = a
    rough = node_input(bsdf, "Roughness")
    if rough is not None:
        rough.default_value = 0.6
    spec = node_input(bsdf, "Specular IOR Level", "Specular")
    if spec is not None:
        spec.default_value = min(1.0, max(xmat.specular) * 0.5)
    emission = node_input(bsdf, "Emission Color", "Emission")
    if emission is not None:
        emission.default_value = (
            xmat.emissive[0],
            xmat.emissive[1],
            xmat.emissive[2],
            1.0,
        )
    strength = node_input(bsdf, "Emission Strength")
    if strength is not None:
        strength.default_value = 0.25
    parts = [
        p.strip() for p in xmat.texture.replace("\\\\", "\\").split("*") if p.strip()
    ]
    spheres = [p for p in parts if p.lower().endswith((".sph", ".spa"))]
    plain = [p for p in parts if not p.lower().endswith((".sph", ".spa"))]
    color_socket = None
    translucent = a < 0.999
    if plain:
        found = resolve_x_texture(plain[0], folder)
        if found is None:
            log.warning("texture not found for %s: %s", name, plain[0])
        else:
            texture = nodes.new("ShaderNodeTexImage")
            texture.image = bpy.data.images.load(found, check_existing=True)
            color_socket = texture.outputs["Color"]
            links.new(texture.outputs["Alpha"], alpha)
            translucent = True
            if min(r, g, b) < 0.99:
                tint = nodes.new("ShaderNodeRGB")
                tint.outputs[0].default_value = (r, g, b, 1.0)
                color_socket = add_color_mix(
                    nodes, links, "MULTIPLY", color_socket, tint.outputs[0]
                )
    if spheres and settings.sphere_maps:
        if color_socket is None:
            tint = nodes.new("ShaderNodeRGB")
            tint.outputs[0].default_value = (r, g, b, 1.0)
            color_socket = tint.outputs[0]
        for sphere in spheres:
            found = resolve_x_texture(sphere, folder)
            if found is None:
                log.warning("sphere map not found for %s: %s", name, sphere)
                continue
            image = bpy.data.images.load(found, check_existing=True)
            color_socket = add_sphere_map(
                nodes, links, image, sphere.lower().endswith(".spa"), color_socket
            )
            log.info("sphere map (experimental) %s on %s", sphere, name)
    elif spheres:
        log.info("sphere maps disabled; skipping %s on %s", spheres, name)
    if color_socket is not None:
        links.new(color_socket, base)
    if translucent:
        mat.surface_render_method = "BLENDED"
    return mat


def create_x_object(name, xscene, folder, settings):
    vertices, faces, face_materials, uvs, colors, materials = merge_x_meshes(xscene)
    points = [(x, z, y) for x, y, z in vertices]
    flipped = [tuple(reversed(face)) for face in faces]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(points, [], flipped)
    mesh.update()
    for index, xmat in enumerate(materials):
        try:
            mesh.materials.append(
                build_x_material("%s_%d" % (name, index), xmat, folder, settings)
            )
        except Exception as error:
            log.warning("material %d of %s failed: %s", index, name, error)
            mesh.materials.append(bpy.data.materials.new("%s_%d" % (name, index)))
    if len(mesh.polygons) == len(face_materials):
        mesh.polygons.foreach_set("material_index", face_materials)
    if uvs:
        flat = []
        for face in flipped:
            for vertex in face:
                u, v = uvs[vertex]
                flat.extend((u, 1.0 - v))
        if len(mesh.loops) * 2 == len(flat):
            layer = mesh.uv_layers.new(name="UVMap")
            layer.data.foreach_set("uv", flat)
    if colors is not None:
        try:
            attribute = mesh.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
            attribute.data.foreach_set("color", [c for rgba in colors for c in rgba])
        except Exception as error:
            log.warning("vertex colors skipped for %s: %s", name, error)
    mesh.update()
    return bpy.data.objects.new(name, mesh)


def import_x_with_addon(path):
    before = set(bpy.data.objects)
    group = getattr(bpy.ops, "import_scene", None)
    operator = None
    for candidate in ("x", "directx", "directx_x", "x_file", "dx"):
        if group is not None and hasattr(group, candidate):
            operator = getattr(group, candidate)
            break
    if operator is None:
        raise RuntimeError(
            "binary/compressed .x needs the DirectX X Importer add-on, but its import operator was not found"
        )
    operator(filepath=path)
    return [o for o in bpy.data.objects if o not in before]


def first_armature(settings):
    for item in settings.targets:
        if item.enabled and target_usable(item.target):
            return find_armature(item.target)
    return None


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


def bone_rest_inverse(armature, bone_name):
    data_bone = armature.data.bones.get(bone_name)
    if data_bone is None:
        return Matrix.Identity(4)
    rotation = data_bone.matrix_local.to_3x3().normalized()
    return rotation.inverted().to_4x4()


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


def accessory_owned_path(path):
    return path in ACCESSORY_OWNED_PATHS or path.startswith('["%s' % MME_CONTROL_PREFIX)


def key_accessory(root, acc, unit):
    if getattr(acc, "standalone", False):
        for name in ("mme_tr", "mme_si"):
            if name not in root:
                root[name] = 1.0
        mme_link_visibility(root)
        return
    anim = root.animation_data or root.animation_data_create()
    low, high = frame_span(k.frame for k in acc.keys)
    kept = snapshot_outside([root], low, high, keep_all=lambda path: not accessory_owned_path(path))
    anim.action = None
    purge_actions(ACTION_PREFIX + "Acc_")
    root.rotation_mode = "YXZ"
    for key in acc.keys:
        root.location = (
            key.position[0] * unit,
            key.position[2] * unit,
            key.position[1] * unit,
        )
        root.rotation_euler = (-key.rotation[0], -key.rotation[2], -key.rotation[1])
        root.scale = (key.scale, key.scale, key.scale)
        root["mme_tr"] = float(key.opacity)
        root["mme_si"] = float(key.scale)
        root.keyframe_insert("location", frame=key.frame)
        root.keyframe_insert("rotation_euler", frame=key.frame)
        root.keyframe_insert("scale", frame=key.frame)
        root.keyframe_insert('["mme_tr"]', frame=key.frame)
        root.keyframe_insert('["mme_si"]', frame=key.frame)
    action = anim.action
    if action is not None:
        for fcurve in iter_fcurves(action):
            for point in fcurve.keyframe_points:
                point.interpolation = "LINEAR"
    restore_points(kept)
    if anim.action is not None:
        anim.action.name = ACTION_PREFIX + "Acc_" + short_name(acc.name, 40)
    mme_link_visibility(root)
    visible = {bool(k.visible) for k in acc.keys}
    for child in root.children:
        child_anim = child.animation_data
        if child_anim is not None:
            child_anim.action = None
        child.hide_viewport = False
        child.hide_render = False
        if visible == {True}:
            continue
        for key in acc.keys:
            child.hide_viewport = not key.visible
            child.hide_render = not key.visible
            child.keyframe_insert("hide_viewport", frame=key.frame)
            child.keyframe_insert("hide_render", frame=key.frame)
        child_action = child.animation_data.action if child.animation_data else None
        if child_action is not None:
            child_action.name = ACTION_PREFIX + "Acc_vis_" + short_name(acc.name, 30)
            for fcurve in iter_fcurves(child_action):
                for point in fcurve.keyframe_points:
                    point.interpolation = "CONSTANT"


def import_accessory(scene, collection, project, settings, acc, entry, unit):
    path = find_accessory_file(acc, settings)
    if path is None:
        log.warning(
            "accessory '%s': file not found (PMM path %s); set an Accessory Folder",
            acc.name,
            acc.path,
        )
        if entry is not None:
            entry.effect_note = "File not found"
        return False
    key = accessory_key(acc)
    root = find_accessory_root(collection, key)
    if root is None:
        root = bpy.data.objects.new(ACCESSORY_PREFIX + short_name(acc.name), None)
        root.empty_display_type = "PLAIN_AXES"
        root.empty_display_size = 1.0
        root["mmd_bridge_accessory"] = key
        collection.objects.link(root)
        log.info("created accessory root '%s'", root.name)
    fx_path, program, controls, tracks = effect_setup(project, settings, acc, entry, path, root)
    stamp = "%s|%s" % (path, os.path.getmtime(path))
    if fx_path:
        stamp += "|%s|%s|density=%.4f|unit=%.6f|%s" % (
            mme_effect_stamp(fx_path, path, program),
            settings.mme_backend,
            settings.mme_density,
            unit,
            json.dumps(sorted((k, v.get("kind")) for k, v in controls.items())),
        )
    if root.get("mmd_bridge_stamp") != stamp or not root.children:
        remove_accessory_children(root)
        mme_clear_root_props(root)
        with open(path, "rb") as handle:
            raw = handle.read()
        parsed = {}

        def x_scene():
            if "scene" not in parsed:
                parsed["scene"] = parse_x_bytes(raw)
                for note in parsed["scene"].warnings:
                    log.warning("accessory '%s': %s", acc.name, note)
            return parsed["scene"]

        mode = x_header_mode(raw)
        folder = os.path.dirname(path)
        children = None
        if fx_path and mode != "txt":
            log.warning(
                "accessory '%s': effects need a text .x file; %s is %s, so it is imported without its effect",
                acc.name,
                os.path.basename(path),
                mode,
            )
        elif fx_path:
            started = time.time()
            try:
                children, uncovered, _tracks, total = mme_build_effect(
                    scene, collection, settings, acc, root, path, fx_path, unit, controls
                )
                if uncovered:
                    static = create_x_object(
                        short_name(acc.name, 40) + "_mesh", x_scene(), folder, settings
                    )
                    collection.objects.link(static)
                    remove_subset_faces(static, set(range(len(static.data.materials))) - set(uncovered))
                    children.append(static)
                note = "%s: %d of %d pass(es) running" % (
                    os.path.basename(fx_path),
                    len([c for c in children if c.get(MME_DRAW_PROP)]),
                    total,
                )
                log.info(
                    "accessory '%s': effect %s built in %.1fs (%s)",
                    acc.name,
                    fx_path,
                    time.time() - started,
                    note,
                )
                if entry is not None:
                    entry.effect_note = note
            except Exception as error:
                log.error(
                    "accessory '%s': effect %s failed, importing the plain mesh instead: %s\n%s",
                    acc.name,
                    fx_path,
                    error,
                    traceback.format_exc(),
                )
                for child in list(root.children):
                    remove_accessory_children_single(child)
                mme_clear_root_props(root)
                if entry is not None:
                    entry.effect_note = "Effect failed: %s" % short_text(error, 60)
                children = None
        elif entry is not None:
            entry.effect_note = ""
        if children is None:
            if mode == "txt":
                child = create_x_object(
                    short_name(acc.name, 40) + "_mesh", x_scene(), folder, settings
                )
                collection.objects.link(child)
                children = [child]
                log.info(
                    "accessory '%s': %d mesh part(s) from %s",
                    acc.name,
                    len(x_scene().meshes),
                    path,
                )
            else:
                children = import_x_with_addon(path)
                for child in children:
                    for owner in list(child.users_collection):
                        owner.objects.unlink(child)
                    collection.objects.link(child)
        for child in children:
            child.parent = root
        root["mmd_bridge_stamp"] = stamp
    for child in root.children:
        child.scale = (ACCESSORY_UNIT * unit,) * 3
    key_accessory(root, acc, unit)
    if root.get("mme_fx"):
        root["mme_controls"] = json.dumps(controls)
        mme_ensure_use_props(root, controls)
        root["mme_unit"] = unit
        mme_key_morph_props(root, tracks)
        runtime = _mme_runtimes.get(root.name)
        if runtime is not None:
            runtime.controls_runtime = {
                name for name, desc in mme_load_controls(root).items() if desc.get("kind") != "const"
            }
    apply_accessory_parent(root, project, settings, acc, entry)
    return True


def standalone_key(settings, name):
    taken = {e.key for e in settings.accessories}
    key = "x:%s" % name
    number = 2
    while key in taken:
        key = "x:%s#%d" % (name, number)
        number += 1
    return key


def standalone_accessory(path, key):
    acc = types.SimpleNamespace()
    acc.index = -1
    acc.name = os.path.basename(path)
    acc.path = path
    acc.draw_order = 0
    acc.key = key
    acc.standalone = True
    acc.keys = [AccessoryKey(0, 1, -1, 0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 1.0, 0, 1.0)]
    return acc


def import_plain_accessory(context, path, unit):
    with open(path, "rb") as handle:
        raw = handle.read()
    name = os.path.basename(path)
    collection = context.collection or context.scene.collection
    if x_header_mode(raw) == "txt":
        xscene = parse_x_bytes(raw)
        for note in xscene.warnings:
            log.warning("accessory '%s': %s", name, note)
        child = create_x_object(short_name(name, 40) + "_mesh", xscene, os.path.dirname(path), context.scene.ninesync_fx)
        collection.objects.link(child)
        children = [child]
    else:
        children = import_x_with_addon(path)
        for child in children:
            for owner in list(child.users_collection):
                owner.objects.unlink(child)
            collection.objects.link(child)
    root = bpy.data.objects.new(ACCESSORY_PREFIX + short_name(name), None)
    root.empty_display_type = "PLAIN_AXES"
    collection.objects.link(root)
    root.location = context.scene.cursor.location
    for child in children:
        child.parent = root
        child.scale = (ACCESSORY_UNIT * unit,) * 3
    log.info("imported plain accessory '%s' (%d part(s), no MME effect)", name, len(children))
    return root


def import_standalone_accessory(context, path, unit, use_effect=True, effect_path=""):
    scene = context.scene
    settings = scene.ninesync_fx
    name = os.path.basename(path)
    probe = standalone_accessory(path, "")
    fx_path = ""
    if use_effect and settings.mme_effects:
        fx_path = effect_path if effect_path and os.path.isfile(effect_path) else (mme_find_effect(probe, path, settings, None) or "")
    if not fx_path:
        return import_plain_accessory(context, path, unit)
    key = standalone_key(settings, name)
    entry = settings.accessories.add()
    entry.key = key
    entry.display = name
    entry.standalone = True
    entry.enabled = False
    entry.effect_checked = True
    entry.use_effect = True
    entry.effect_path = effect_path or ""
    collection = accessory_collection(scene, True)
    acc = standalone_accessory(path, key)
    started = time.time()
    if not import_accessory(scene, collection, PmmProject(), settings, acc, entry, unit):
        return None
    root = find_accessory_root(collection, key)
    if root is not None:
        root.location = scene.cursor.location
        mme_update_scene(scene, context.evaluated_depsgraph_get(), force=True)
    log.info("imported accessory '%s' with effect %s in %.1fs", name, os.path.basename(fx_path), time.time() - started)
    return root


def import_accessories(scene, project, settings, unit):
    if not settings.sync_accessories:
        return 0
    if project.accessory_error:
        log.warning("accessory section could not be read: %s", project.accessory_error)
        return 0
    if not project.accessories:
        return 0
    log.info(
        "accessories: MME effects %s, effect density %.2f",
        "on" if settings.mme_effects else "off",
        settings.mme_density,
    )
    collection = accessory_collection(scene, True)
    entries = {e.key: e for e in settings.accessories}
    done = 0
    for acc in project.accessories:
        entry = entries.get(accessory_key(acc))
        if entry is not None and not entry.enabled:
            log.info("skip accessory '%s': disabled", acc.name)
            continue
        if not acc.keys:
            continue
        try:
            if import_accessory(scene, collection, project, settings, acc, entry, unit):
                done += 1
        except Exception as error:
            log.error(
                "accessory '%s' failed: %s\n%s", acc.name, error, traceback.format_exc()
            )
    return done


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


def fx_mmd_tools_available():
    return hasattr(bpy.types, "MMD_TOOLS_OT_import_vmd")


def fx_missing_dependencies():
    missing = []
    if not x_importer_available():
        missing.append(
            (
                "DirectX X Importer",
                "DirectX X Importer is not installed (only needed for binary or compressed .x accessories)",
                X_IMPORTER_URL,
            )
        )
    return missing


def mirror_context(settings, ctx):
    integrated = bool(getattr(ctx, "integrated", False))
    if integrated:
        settings.filepath = ctx.pmm_path
    wanted = [model_key(m) for m in ctx.project.models]
    for index in reversed(range(len(settings.targets))):
        if settings.targets[index].key not in wanted:
            settings.targets.remove(index)
    present = {t.key: t for t in settings.targets}
    for model in ctx.project.models:
        key = model_key(model)
        link = present.get(key)
        if link is None:
            link = settings.targets.add()
            link.key = key
            link.pmm_name = model.name or model.name_en or key
            link.enabled = True
        if integrated:
            link.target = ctx.targets.get(key)


def apply_render_defaults(scene, settings):
    if not (settings.mme_effects and mme_effect_roots(scene)):
        return
    if not scene.render.use_lock_interface:
        scene.render.use_lock_interface = True
        log.info("render interface locking enabled so effects can update safely while rendering")
    if settings.mme_standard_view and scene.view_settings.view_transform != "Standard":
        try:
            scene.view_settings.view_transform = "Standard"
            log.info("view transform set to Standard so effect colors match MMD")
        except TypeError:
            pass


def sync_accessories(ctx):
    scene = ctx.scene
    settings = getattr(scene, "ninesync_fx", None)
    if settings is None:
        return 0
    project = ctx.project
    mirror_context(settings, ctx)
    refresh_accessories(settings, project)
    done = import_accessories(scene, project, settings, ctx.unit)
    apply_render_defaults(scene, settings)
    return done


def post_sync_hook(ctx):
    return sync_accessories(ctx)


def clean_hook(scene, options):
    counts = {"accessories": 0}
    clean_accessory_objects(counts)
    settings = getattr(scene, "ninesync_fx", None)
    if settings is not None:
        for index in reversed(range(len(settings.accessories))):
            if settings.accessories[index].standalone:
                settings.accessories.remove(index)
    return counts


def report_hook(context):
    scene = context.scene
    settings = scene.ninesync_fx
    lines = ["---- NineSync: FX ----"]
    lines.append(
        "add-on %s | DirectX X Importer available: %s | mmd_tools available: %s | core installed: %s"
        % (
            ".".join(str(v) for v in addon_version()),
            x_importer_available(),
            fx_mmd_tools_available(),
            hooks.core_installed(),
        )
    )
    lines.append(
        "accessories: sync=%s sphere_maps=%s retime=%s scale=%.4f"
        % (settings.sync_accessories, settings.sphere_maps, settings.retime_fps, current_scale(settings))
    )
    lines.append(
        "effects: on=%s backend=%s density=%.2f tolerance=%.4f live=%s bake_to=%s"
        % (settings.mme_effects, settings.mme_backend, settings.mme_density, settings.mme_tolerance, settings.mme_live, settings.mme_bake_target)
    )
    for root in mme_effect_roots(scene):
        passes = mme_effect_passes(root)
        lines.append(
            "  effect %s: %s"
            % (
                os.path.basename(root.get("mme_fx", "")),
                ", ".join(
                    "%s[%s%s%s]"
                    % (
                        json.loads(p.get(MME_DRAW_PROP, "{}")).get("pass", "?"),
                        p.get(MME_BACKEND_PROP, "PYTHON"),
                        ",baked" if p.get(MME_BAKED_PROP) else "",
                        ",handler" if p.get("mme_handler", True) else "",
                    )
                    for p in passes
                ),
            )
        )
    for entry in settings.accessories:
        lines.append(
            "accessory '%s' enabled=%s standalone=%s parent_override=%s effect=%s %s"
            % (
                entry.display,
                entry.enabled,
                entry.standalone,
                entry.parent_bone or "none",
                entry.use_effect,
                entry.effect_note or "",
            )
        )
    return lines


def addon_version():
    try:
        return tuple(importlib.import_module(__package__).bl_info["version"])
    except Exception:
        return (0, 0, 0)
