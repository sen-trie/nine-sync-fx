"""Live model and camera inputs expressed through the existing scalar node backend."""

from ...fx.errors import FxUnsupported
from .controllers import DriverSource

SWAP = (0, 2, 1)


def world_matrix(env, obj):
    parts = []
    for row in range(4):
        for col in range(4):
            if col == 3:
                parts.append(float(row == 3))
            else:
                # Driver RNA matrix paths index columns before rows.
                column = 3 if row == 3 else SWAP[row]
                value = env.driven(f"{obj.name} world {row},{col}",
                                   DriverSource(obj, f"matrix_world[{column}]", index=SWAP[col]))
                parts.append(env.backend.div(value, env.units) if row == 3 else value)
    return parts


def world_position(env, obj):
    if obj is None:
        raise FxUnsupported("The scene has no camera for the POSITION semantic.")
    return [env.driven(f"{obj.name} position {axis}", DriverSource(obj, axis=axis, scale=1/env.units))
            for axis in "XZY"]


def view_matrix(env):
    camera = env.scene.camera
    if camera is None:
        raise FxUnsupported("The scene has no camera for the VIEW semantic.")
    B = env.backend
    position = world_position(env, camera)
    rows = []
    basis = []
    for row in range(3):
        basis.append([
            B.mul(env.driven(f"Camera view {row},{col}",
                            DriverSource(camera, f"matrix_world[{col}]", index=SWAP[row])),
                  -1.0 if col == 2 else 1.0)
            for col in range(3)])
        rows.extend(basis[-1] + [0.0])
    for col in range(3):
        term = 0.0
        for row in range(3):
            term = B.add(term, B.mul(position[row], basis[row][col]))
        rows.append(B.neg(term))
    return rows + [1.0]


def camera_projection(env):
    """Reuse the generic accessory path's live MMD camera projection backend."""
    from ...nodes.geo_scene import MmeGeoSceneCore
    from ...nodes.socket import MmeSock

    if env.scene.camera is None:
        raise FxUnsupported("The scene has no camera for WORLDVIEWPROJECTION.")
    if env.scene.camera.data.type not in ("PERSP", "ORTHO"):
        raise FxUnsupported("Camera projection requires a perspective or orthographic camera.")
    cached = getattr(env, "camera_wvp", None)
    if cached is not None:
        return cached
    builder = MmeGeoSceneCore(env.backend.tree, None, env.units, env.scene, None, {}, env.program)
    builder.B = env.backend
    camera = builder.camera_impl()
    B = env.backend
    # Preserve Blender's off-axis lens shift while retaining the generic MMD depth convention.
    native = next(node for node in builder.B.nodes if node.bl_idname == "FunctionNodeSeparateMatrix"
                  and node.label == "Blender projection")
    depth = B.new("FunctionNodeSeparateMatrix")
    B.links.new(camera["proj"], depth.inputs[0])
    components = [MmeSock(socket) for socket in depth.outputs[:16]]
    for index in (8, 9):
        components[index] = B.select(camera["ortho"], 0.0, B.neg(MmeSock(native.outputs[index])))
    for index in (12, 13):
        components[index] = B.select(camera["ortho"], MmeSock(native.outputs[index]), 0.0)
    matrix = builder.mat_mul(B.matrix_of(components).socket, camera["view"], B.matrix_of(world_matrix(env, env.obj)).socket)
    split = env.backend.new("FunctionNodeSeparateMatrix")
    env.backend.links.new(matrix, split.inputs[0])
    env.camera_wvp = [MmeSock(socket) for socket in split.outputs[:16]]
    return env.camera_wvp
