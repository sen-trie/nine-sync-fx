import bpy
import json

from ..common.constants import (
    ACCESSORY_UNIT,
    MME_BACKEND_PROP,
    MME_BAKED_PROP,
    MME_DRIVEN_PROP,
    MME_UNIFORMS_PROP,
)
from ..common.controls import (
    mme_control_use_prop,
    mme_load_controls,
    mme_param_meta,
    mme_shapekey_spec,
)
from ..fx.semantics import fx_classify_global
from ..fx.types import (
    fx_is_numeric,
    fx_resolve_type,
)
from ..nodes.drivers import mme_add_driver


# Driver description for one component of a shader uniform: scene time, viewport size, an effect parameter, a
# controller property, shape key, use-switch, opacity or scale.
# Returns None when the value cannot be driven; the per-frame handler then sets it.
def mme_uniform_driver(program, gname, index, root, controls, scene):
    g = program.globals.get(gname)
    if g is None:
        return None
    source = fx_classify_global(g, program)
    if source is None:
        return None
    kind = source[0]

    def scene_var(name, path):
        return {"name": name, "type": "SINGLE_PROP", "id_type": "SCENE", "id": scene.name, "path": path}

    def prop_var(obj, path):
        return {"name": "var", "type": "SINGLE_PROP", "id_type": "OBJECT", "id": obj.name, "path": path}

    if kind == "time":
        return {"expr": "frame * base / fps", "vars": [scene_var("fps", "render.fps"), scene_var("base", "render.fps_base")]}
    if kind == "elapsed":
        return {"expr": "base / fps", "vars": [scene_var("fps", "render.fps"), scene_var("base", "render.fps_base")]}
    if kind == "viewport":
        axis = "render.resolution_x" if index == 0 else "render.resolution_y"
        return {"expr": "res * pct / 100", "vars": [scene_var("res", axis), scene_var("pct", "render.resolution_percentage")]}
    if kind == "param":
        meta = mme_param_meta(root).get(gname)
        if meta is None:
            return None
        path = '["%s"]' % meta["prop"]
        if meta["size"] > 1:
            path = '%s[%d]' % (path, index)
        return {"expr": "var", "vars": [prop_var(root, path)]}
    if kind == "control":
        desc = controls.get(gname, {})
        use_prop = mme_control_use_prop(desc)
        if desc.get("kind") == "prop":
            spec = {"expr": "var", "vars": [prop_var(root, '["%s"]' % desc.get("prop", ""))]}
            if use_prop is not None:
                spec = {"expr": "var * use", "vars": spec["vars"] + [dict(prop_var(root, '["%s"]' % use_prop), name="use")]}
            return spec
        if desc.get("kind") == "shapekey":
            spec = mme_shapekey_spec(desc)
            if spec is not None and use_prop is not None:
                spec = {"expr": "var * use", "vars": spec["vars"] + [dict(prop_var(root, '["%s"]' % use_prop), name="use")]}
            return spec
        if desc.get("kind") == "model" and use_prop is not None:
            t = fx_resolve_type(g.type, program)
            if fx_is_numeric(t) and t[0] == "b":
                return {"expr": "use", "vars": [dict(prop_var(root, '["%s"]' % use_prop), name="use")]}
        if desc.get("kind") in ("self", "object"):
            obj = root if desc.get("kind") == "self" else bpy.data.objects.get(desc.get("object", ""))
            item = desc.get("item", "").lower()
            if obj is None:
                return None
            if item == "tr":
                return {"expr": "var", "vars": [prop_var(obj, '["mme_tr"]')]}
            if item == "si":
                return {"expr": "var * %r" % ACCESSORY_UNIT, "vars": [prop_var(obj, '["mme_si"]')]}
    return None


# Adds drivers for all uniforms that can have one and records which ones are driven.
def mme_drive_uniforms(obj, program, uniforms, root, scene):
    controls = mme_load_controls(root)
    driven = []
    for prop, gname, index in uniforms:
        spec = mme_uniform_driver(program, gname, index, root, controls, scene)
        if spec is None:
            continue
        mme_add_driver(obj, '["%s"]' % prop, spec)
        driven.append(prop)
    obj[MME_DRIVEN_PROP] = json.dumps(driven)


# Uniforms that still need the per-frame handler because no driver could be made for them.
def mme_handler_uniforms(obj):
    try:
        uniforms = json.loads(obj.get(MME_UNIFORMS_PROP, "[]"))
        driven = set(json.loads(obj.get(MME_DRIVEN_PROP, "[]")))
    except (TypeError, ValueError):
        return []
    return [u for u in uniforms if u[0] not in driven]


# A pass needs the frame handler if it runs on the Python backend (and is not baked) or has uniforms without
# drivers; otherwise Blender updates it alone.
def mme_needs_handler(obj):
    if obj.get(MME_BACKEND_PROP, "PYTHON") == "PYTHON" and not obj.get(MME_BAKED_PROP):
        return True
    return bool(mme_handler_uniforms(obj))


# After a file load Blender can leave drivers marked invalid; re-assigning the expression makes it re-validate
# them.
def mme_revive_drivers(objects):
    revived = 0
    for obj in objects:
        anim = obj.animation_data
        if anim is None:
            continue
        for fcurve in anim.drivers:
            driver = fcurve.driver
            if not driver.is_valid:
                driver.expression = driver.expression
                revived += 1
        if revived:
            obj.update_tag()
    return revived
