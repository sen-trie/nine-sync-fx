from .annotations import fx_const_value


# Evaluates the Subset annotation ('0,2-4', '1-') against a material index.
def fx_subset_matches(spec, index):
    if spec is None:
        return True
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            lo = int(lo) if lo else 0
            if hi == "":
                if index >= lo:
                    return True
            elif lo <= index <= int(hi):
                return True
        else:
            try:
                if int(part) == index:
                    return True
            except ValueError:
                pass
    return False


# One planned draw: a pass for one material subset, the loop counters active around it, the render target set
# by the script, and whether it draws geometry or a full-screen buffer.
class FxDraw:
    __slots__ = ("technique", "fx_pass", "subset", "loops", "target", "kind", "order")

    def __init__(self, technique, fx_pass, subset, loops, target, kind, order):
        self.technique = technique
        self.fx_pass = fx_pass
        self.subset = subset
        self.loops = loops
        self.target = target
        self.kind = kind
        self.order = order

    @property
    def label(self):
        return "%s.%s" % (self.technique.name or "tech", self.fx_pass.name or "pass")


# MMDPass annotation (object, object_ss, shadow, ...); techniques without one are object passes.
def fx_technique_pass_type(technique, program):
    for key, node in technique.ann.items():
        if key.lower() == "mmdpass":
            value = fx_const_value(program, node)
            if isinstance(value, str):
                return value.lower()
    return "object"


# Subset annotation as text (floats become ints); None if there is none.
def fx_technique_subset(technique, program):
    for key, node in technique.ann.items():
        if key.lower() == "subset":
            value = fx_const_value(program, node)
            if value is None:
                return None
            if isinstance(value, float):
                return str(int(value))
            return str(value)
    return None


# Technique-level Script annotation, which drives loops and render targets.
def fx_technique_script(technique, program):
    for key, node in technique.ann.items():
        if key.lower() == "script":
            value = fx_const_value(program, node)
            if isinstance(value, str):
                return value
    return None


# Pass-level Script annotation (for example Draw=Buffer for a full-screen pass).
def fx_pass_script(fx_pass, program):
    for key, node in fx_pass.ann.items():
        if key.lower() == "script":
            value = fx_const_value(program, node)
            if isinstance(value, str):
                return value
    return None


# Picks the technique for normal drawing: the first one whose MMDPass is object (then object_ss) and whose
# Subset matches.
def fx_pick_technique(program, subset, pass_types=("object", "object_ss")):
    for pass_type in pass_types:
        for technique in program.techniques:
            if fx_technique_pass_type(technique, program) != pass_type:
                continue
            if fx_subset_matches(fx_technique_subset(technique, program), subset):
                return technique
    return None


# Loop counts can be literals, globals with a constant initialiser or runtime parameters; this resolves any of
# them to an int (default 1).
def fx_global_int(program, name, values=None):
    name = name.strip()
    try:
        return int(float(name))
    except ValueError:
        pass
    if values and name in values:
        return max(0, int(values[name][0]))
    g = program.globals.get(name)
    if g is None or g.init is None:
        return 1
    value = fx_const_value(program, g.init)
    if isinstance(value, list):
        value = value[0]
    return int(value) if value is not None else 1


# Turns each technique's script (RenderColorTarget, LoopByCount, LoopGetIndex, LoopEnd, Pass=) into a flat
# list of draws with their enclosing loops.
# Techniques without a script draw every pass once.
def fx_plan_draws(program, subset_count, values=None):
    draws = []
    order = 0
    for subset in range(subset_count):
        technique = fx_pick_technique(program, subset)
        if technique is None:
            continue
        passes = {p.name: p for p in technique.passes}
        script = fx_technique_script(technique, program)
        if script is None:
            for fx_pass in technique.passes:
                kind = "buffer" if (fx_pass_script(fx_pass, program) or "").lower().replace(" ", "").find("draw=buffer") >= 0 else "geometry"
                draws.append(FxDraw(technique, fx_pass, subset, [], "", kind, order))
                order += 1
            continue
        target = ""
        stack = []
        for command in script.split(";"):
            if "=" not in command:
                continue
            key, value = command.split("=", 1)
            key = key.strip().lower()
            value = value.strip()
            if key in ("rendercolortarget", "rendercolortarget0"):
                target = value
            elif key == "loopbycount":
                stack.append([value, fx_global_int(program, value, values), None])
            elif key == "loopgetindex":
                if stack:
                    stack[-1][2] = value
            elif key == "loopend":
                if stack:
                    stack.pop()
            elif key == "pass":
                fx_pass = passes.get(value)
                if fx_pass is None:
                    continue
                pass_script = (fx_pass_script(fx_pass, program) or "").lower().replace(" ", "")
                kind = "buffer" if "draw=buffer" in pass_script else "geometry"
                loops = [(index or "", count, name) for name, count, index in stack]
                draws.append(FxDraw(technique, fx_pass, subset, loops, target, kind, order))
                order += 1
    return draws
