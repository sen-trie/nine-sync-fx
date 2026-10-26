import json
import os

from ..common.constants import (
    MME_LOOP_PREFIX,
    MME_RUNTIME_VALUES_PROP,
)
from ..common.controls import mme_load_controls
from ..common.state import _mme_runtimes
from ..fx.errors import FxUnsupported
from ..fx.loader import fx_parse_file
from ..fx.params import fx_script_values
from ..fx.techniques import fx_plan_draws
from ..fx.textures import FxTextureStore
from ..fx.types import FxValue
from ..nodes.images import mme_load_pixels
from ..xfile.textures import resolve_x_texture
from .geometry_cache import mme_x_effect_geometry
from .params import mme_built_values


# Everything needed to evaluate one effect: parsed program, merged mesh arrays, texture store, planned draws,
# and the parameter values it was built with.
class MmeRuntime:
    def __init__(self, fx_path, x_path, values=None):
        self.fx_path = fx_path
        self.x_path = x_path
        self.x_folder = os.path.dirname(x_path)
        self.program = fx_parse_file(fx_path)
        mode, geometry = mme_x_effect_geometry(x_path)
        if mode != "txt":
            raise FxUnsupported("binary .x files cannot drive effects")
        (
            self.positions,
            self.normals,
            self.uvs,
            self.faces,
            self.face_subsets,
            self.materials,
        ) = geometry
        self.textures = FxTextureStore(self.program, mme_load_pixels)
        self.values = dict(values or {})
        self.draws = fx_plan_draws(self.program, max(1, len(self.materials)), self.values)
        self.passes = {}
        self.controls_runtime = set()
        self.stamp = mme_effect_stamp(fx_path, x_path, self.program)

    # Identity of the loop counts, which decide the instance count of a build.
    def loop_key(self):
        names = set(fx_script_values(self.program, "loopbycount"))
        return json.dumps({k: v for k, v in sorted(self.values.items()) if k in names})

    def draw_by_order(self, order):
        for draw in self.draws:
            if draw.order == order:
                return draw
        return None

    # Loop index variables for the pixel shader: read from per-vertex attributes written by the geometry
    # stage.
    def loop_overrides_nodes(self, draw, B):
        out = {}
        for var, _count, _name in draw.loops:
            if var:
                sock = B.attribute(MME_LOOP_PREFIX + var)
                B.tag(sock, "LoopGetIndex %s" % var)
                out[var] = FxValue(("i", ()), [sock])
        return out

    # Copies a material's colours and texture path into the scene state before a pass is evaluated; sphere and
    # spa textures are not material textures.
    def material_state(self, state, subset):
        xmat = self.materials[subset] if subset < len(self.materials) else None
        if xmat is None:
            return
        texture = ""
        if xmat.texture:
            first = xmat.texture.split("*")[0].strip()
            if first and not first.lower().endswith((".sph", ".spa")):
                texture = resolve_x_texture(first, self.x_folder) or ""
        diffuse = tuple(float(v) for v in xmat.diffuse)
        state.material = {
            "diffuse": diffuse,
            "ambient": diffuse[:3],
            "specular": tuple(float(v) for v in xmat.specular),
            "power": float(xmat.power),
            "emissive": tuple(float(v) for v in xmat.emissive),
            "texture": texture,
        }


# Text identifying the exact .x/.fx files (path and modified time) an effect was built from.
def mme_effect_stamp(fx_path, x_path, program=None):
    files = list(program.files) if program is not None else [fx_path]
    parts = [x_path, str(os.path.getmtime(x_path))]
    for path in files:
        try:
            parts.append("%s=%s" % (os.path.basename(path), os.path.getmtime(path)))
        except OSError:
            parts.append(path)
    return "|".join(parts)


# Per-session cache of runtimes by root name; a runtime is rebuilt when the files change and is restored from
# the values stored on the root after a file load.
def mme_runtime_for(root):
    fx_path = root.get("mme_fx", "")
    x_path = root.get("mme_x", "")
    if not fx_path or not x_path:
        return None
    key = root.name
    runtime = _mme_runtimes.get(key)
    if runtime is not None and runtime.fx_path == fx_path and runtime.x_path == x_path:
        return runtime
    if not (os.path.isfile(fx_path) and os.path.isfile(x_path)):
        return None
    try:
        values = json.loads(root.get(MME_RUNTIME_VALUES_PROP, "null"))
    except (TypeError, ValueError):
        values = None
    if not isinstance(values, dict):
        values = {k: v for k, v in mme_built_values(root).items() if isinstance(v, list)}
    runtime = MmeRuntime(fx_path, x_path, values)
    controls = mme_load_controls(root)
    runtime.controls_runtime = {name for name, desc in controls.items() if desc.get("kind") != "const"}
    _mme_runtimes[key] = runtime
    return runtime
