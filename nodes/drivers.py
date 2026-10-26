import bpy


# Writes default values to the modifier inputs, then adds a driver per recorded input.
def mme_set_drivers(obj, mod, drivers, defaults):
    inputs = mod.properties.inputs
    for identifier, value in defaults.items():
        item = getattr(inputs, identifier, None)
        if item is not None:
            item.value = value
    for identifier, spec in drivers:
        mme_add_driver(obj, 'modifiers["%s"].properties.inputs.%s.value' % (mod.name, identifier), spec)


# Adds a scripted driver from a plain spec of variables and an expression. Existing drivers on the path are
# removed first so rebuilds do not stack them.
def mme_add_driver(owner, path, spec, index=-1):
    try:
        owner.driver_remove(path, index)
    except TypeError:
        pass
    fcurve = owner.driver_add(path, index) if index >= 0 else owner.driver_add(path)
    driver = fcurve.driver
    driver.type = "SCRIPTED"
    for var_spec in spec["vars"]:
        var = driver.variables.new()
        var.name = var_spec["name"]
        var.type = var_spec["type"]
        target = var.targets[0]
        if var_spec["type"] == "SINGLE_PROP":
            target.id_type = var_spec["id_type"]
            if var_spec["id_type"] == "SCENE":
                target.id = bpy.data.scenes.get(var_spec["id"])
            elif var_spec["id_type"] == "LIGHT":
                target.id = bpy.data.lights.get(var_spec["id"])
            elif var_spec["id_type"] == "KEY":
                target.id = bpy.data.shape_keys.get(var_spec["id"])
            else:
                target.id = bpy.data.objects.get(var_spec["id"])
                if target.id is not None:
                    target.id.update_tag()
            path_text = var_spec["path"]
            if "index" in var_spec:
                path_text = "%s[%d]" % (path_text, var_spec["index"])
            target.data_path = path_text
        else:
            target.id = bpy.data.objects.get(var_spec["id"])
            target.bone_target = var_spec.get("bone", "")
            target.transform_type = var_spec["transform_type"]
            target.transform_space = var_spec.get("space", "WORLD_SPACE")
            target.rotation_mode = var_spec.get("rotation_mode", "AUTO")
    driver.expression = spec["expr"]
    return fcurve
