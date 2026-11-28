"""Adapt the shared FX draw planner's loops to an existing evaluated mesh."""

from ...fx.interp.executor import FxExec
from ...fx.types import FxValue, fx_resolve_type
from ...fx.errors import FxUnsupported
from ...nodes.socket import MmeSock


def loop_fields(env):
    if hasattr(env, "instance_fields"):
        return env.instance_fields
    B = env.backend
    counts, variables = [], {}
    name = "nsfx_" + B.tree["ninesync_middle_token"] + "_instance"
    attr = B.new("GeometryNodeInputNamedAttribute")
    attr.data_type = "INT"
    attr.inputs["Name"].default_value = name
    remaining = MmeSock(attr.outputs["Attribute"])
    execution = FxExec(env.program, B, env)
    env._building_instance_counts = True
    try:
        for variable, count, parameter in env.analysis.loops:
            declaration = env.program.globals.get(parameter)
            if declaration is None:
                try:
                    source = float(parameter)
                except ValueError:
                    raise FxUnsupported(f"LoopByCount {parameter!r} references an unknown effect parameter.") from None
            else:
                if "static" in declaration.quals:
                    raise FxUnsupported(f"LoopByCount {parameter!r} is static and is not an exposed native MME effect parameter.")
                value = execution.global_value(parameter)
                if value.t not in (("f", ()), ("i", ()), ("b", ())):
                    raise FxUnsupported(f"LoopByCount {parameter!r} requires a uniform numeric scalar.")
                source = value.c[0]
            counts.append(B.unary("floor", B.max(source, 0.0)))
    finally:
        env._building_instance_counts = False
    for (variable, _count, _parameter), count in reversed(list(zip(env.analysis.loops, counts))):
        if variable:
            global_value = env.program.globals.get(variable)
            t = fx_resolve_type(global_value.type, env.program) if global_value else ("i", ())
            if t not in (("i", ()), ("f", ())):
                raise FxUnsupported("LoopGetIndex requires a numeric scalar global.")
            # Guard zero-count arithmetic; a zero loop still emits no geometry.
            variables[variable] = FxValue(t, [B.imod(remaining, B.max(count, 1.0))])
        remaining = B.idiv(remaining, B.max(count, 1.0))
    total = 1.0
    for count in counts:
        total = B.mul(total, count)
    env.instance_fields = variables, total, name
    return env.instance_fields


def duplicate(env, geometry):
    B = env.backend
    _variables, count, name = loop_fields(env)
    points = B.new("GeometryNodePoints")
    points.label = "One copy per MME loop iteration"
    B.feed(points.inputs["Count"], count)
    index = B.new("GeometryNodeInputIndex")
    store = B.new("GeometryNodeStoreNamedAttribute")
    store.domain, store.data_type = "POINT", "INT"
    store.inputs["Name"].default_value = name
    B.links.new(points.outputs[0], store.inputs["Geometry"])
    B.links.new(index.outputs[0], store.inputs["Value"])
    instance = B.new("GeometryNodeInstanceOnPoints")
    B.links.new(store.outputs["Geometry"], instance.inputs["Points"])
    B.links.new(geometry.socket, instance.inputs["Instance"])
    realize = B.new("GeometryNodeRealizeInstances")
    B.links.new(instance.outputs[0], realize.inputs[0])
    B.tree["ninesync_middle_instance_loops"] = True
    env.warnings.add("Instance loops duplicate the evaluated mesh before vertex shading; large counts increase memory use.")
    return MmeSock(realize.outputs[0])
