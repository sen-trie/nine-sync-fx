import bpy
import math
import numpy as np

from ..common.constants import (
    LIGHT_COLLECTION,
    MME_SWAP3,
    PMM_FPS,
)
from ..common.controls import mme_load_controls
from ..common.log import log
from ..common.scene_utils import (
    child_of_type,
    find_rig_root,
    scene_fps,
)
from ..fx.semantics import (
    FX_FAR,
    FX_NEAR,
    FxSceneState,
    fx_lh_look,
    fx_lh_ortho,
    fx_lh_perspective,
)
from ..fx.types import fx_resolve_type
from .control_values import mme_control_values
from .matrices import mme_np_matrix
from .params import mme_param_values


# Fills the scene state from the render camera: MMD-space eye, direction and view matrix, and a projection
# that follows Blender's sensor fit and aspect.
# Orthographic cameras use an orthographic projection scaled by ortho_scale.
def mme_camera_state(state, scene, depsgraph, unit):
    camera = scene.camera
    if camera is None:
        return
    evaluated = camera.evaluated_get(depsgraph) if depsgraph is not None else camera
    world = mme_np_matrix(evaluated.matrix_world)
    eye = MME_SWAP3 @ world[:3, 3] / unit
    forward = MME_SWAP3 @ (-world[:3, 2])
    up = MME_SWAP3 @ world[:3, 1]
    state.view = fx_lh_look(eye, forward, up)
    state.camera_pos = eye
    state.camera_dir = forward / (np.linalg.norm(forward) or 1.0)
    render = scene.render
    width = render.resolution_x * render.pixel_aspect_x
    height = render.resolution_y * render.pixel_aspect_y
    aspect = width / height if height else 1.0
    data = evaluated.data
    if getattr(data, "type", "PERSP") == "ORTHO":
        scale = data.ortho_scale / unit
        if aspect >= 1.0:
            state.proj = fx_lh_ortho(scale, scale / aspect, FX_NEAR, FX_FAR)
        else:
            state.proj = fx_lh_ortho(scale * aspect, scale, FX_NEAR, FX_FAR)
        state.ortho = True
        return
    fit = getattr(data, "sensor_fit", "AUTO")
    angle = data.angle
    if fit == "VERTICAL":
        fov_y = data.angle_y
    elif fit == "HORIZONTAL" or aspect >= 1.0:
        fov_x = data.angle_x if fit == "HORIZONTAL" else angle
        fov_y = 2.0 * math.atan(math.tan(fov_x * 0.5) / aspect)
    else:
        fov_y = angle
    state.proj = fx_lh_perspective(max(fov_y, 1e-4), aspect, FX_NEAR, FX_FAR)


# Direction and colour of the MMD light rig; the direction is the negated light position with axes swapped.
def mme_light_state(state, depsgraph):
    collection = bpy.data.collections.get(LIGHT_COLLECTION)
    root = find_rig_root(collection, "LIGHT") if collection else None
    light = child_of_type(root, "LIGHT") if root else None
    if light is None:
        return
    evaluated = light.evaluated_get(depsgraph) if depsgraph is not None else light
    loc = evaluated.location
    direction = np.array([-loc.x, -loc.z, -loc.y], dtype=np.float64)
    if np.linalg.norm(direction) > 1e-9:
        state.light_dir = direction
    state.light_color = np.array(list(evaluated.data.color), dtype=np.float64)


# Complete scene state for one frame: viewport, camera, light, time from the frame number, controller values
# and parameter values.
def mme_frame_state(scene, depsgraph, root, runtime, unit):
    state = FxSceneState()
    state.ortho = False
    render = scene.render
    pct = render.resolution_percentage / 100.0
    state.viewport = (render.resolution_x * pct, render.resolution_y * pct)
    mme_camera_state(state, scene, depsgraph, unit)
    mme_light_state(state, depsgraph)
    fps = scene_fps(scene) or PMM_FPS
    state.time = scene.frame_current / fps
    state.elapsed = 1.0 / fps
    controls = mme_load_controls(root)
    program = runtime.program
    for name, desc in controls.items():
        g = program.globals.get(name)
        if g is None:
            continue
        t = fx_resolve_type(g.type, program)
        try:
            state.controls[name] = [float(v) for v in mme_control_values(desc, root, depsgraph, unit, t)]
        except Exception as error:
            log.debug("control %s failed: %s", name, error)
            state.controls[name] = [0.0]
    state.params = mme_param_values(root, program, depsgraph)
    return state


# Copy of a state with a fixed distant camera, so visibility probing does not depend on where the viewport
# camera happens to be.
def mme_prune_state(state):
    probe = FxSceneState()
    probe.__dict__.update(state.__dict__)
    probe.controls = dict(state.controls)
    eye = np.array([0.0, 0.0, -1.0e5])
    probe.view = fx_lh_look(eye, (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))
    probe.proj = fx_lh_perspective(math.radians(30.0), 1.0, FX_NEAR, FX_FAR)
    probe.camera_pos = eye
    probe.camera_dir = np.array([0.0, 0.0, 1.0])
    probe.ortho = False
    return probe
