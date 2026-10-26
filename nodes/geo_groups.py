from ..common.constants import MME_CORE_VERSION
from ..fx.semantics import (
    FX_FAR,
    FX_NEAR,
)
from .core_library import mme_core_group
from .geo_scene import MmeGeoSceneCore
from .names import mme_group_name
from .socket import MmeSock


# The full scene builder: MmeGeoSceneCore plus the shared MMD matrix, camera and light groups. It lives next
# to those groups because their builders use it.
class MmeGeoScene(MmeGeoSceneCore):
    def mmd_world(self, transform, scale=1.0):
        node = self.group_node(mme_geo_matrix_group(), "Blender transform to MMD", {"Transform": transform, "Unit": self.unit, "Scale": scale})
        return node.outputs["Matrix"]

    def camera(self):
        if "camera" in self.cache:
            return self.cache["camera"]
        self.B.statement("Camera in MMD space")
        node = self.group_node(mme_geo_camera_group(), "MMD camera", {"Unit": self.unit})
        out = node.outputs
        ortho = MmeSock(out["Orthographic"])
        self.B.boolish.add(self.B.key(ortho))
        found = {
            "eye": self.split(out["Eye"]),
            "r": self.split(out["Right"]),
            "u": self.split(out["Up"]),
            "f": self.split(out["Forward"]),
            "view": out["View"],
            "proj": out["Projection"],
            "ortho": ortho,
            "xs": MmeSock(out["XS"]),
            "ys": MmeSock(out["YS"]),
            "c22": MmeSock(out["C22"]),
            "c32": MmeSock(out["C32"]),
            "q": FX_FAR / (FX_FAR - FX_NEAR),
        }
        self.cache["camera"] = found
        return found

    def light(self):
        if "light" in self.cache:
            return self.cache["light"]
        B = self.B
        B.statement("Light: MMD direction and color")
        light = self.light_object()
        if light is not None:
            loc = [
                self.driven("light:%d" % k, "Light location %s" % "xyz"[k], {"expr": "var", "vars": [{"name": "var", "type": "SINGLE_PROP", "id_type": "OBJECT", "id": light.name, "path": "location", "index": k}]}, light.location[k])
                for k in range(3)
            ]
            direction = self.combine([B.neg(loc[0]), B.neg(loc[2]), B.neg(loc[1])], "MMD light direction")
            color = [
                self.driven("lightcolor:%d" % k, "Light color %s" % "rgb"[k], {"expr": "var", "vars": [{"name": "var", "type": "SINGLE_PROP", "id_type": "LIGHT", "id": light.data.name, "path": "color", "index": k}]}, light.data.color[k])
                for k in range(3)
            ]
        else:
            direction = self.combine([float(v) for v in self.state.light_dir], "MMD light direction")
            color = [float(v) for v in self.state.light_color]
        node = self.group_node(mme_geo_light_group(), "MMD light", {"Direction": direction})
        out = node.outputs
        found = {
            "d": self.split(out["Direction"]),
            "eye": self.split(out["Eye"]),
            "color": color,
            "view": out["View"],
            "proj": out["Projection"],
        }
        self.cache["light"] = found
        return found


# Scene builder for use inside a shared group, with unit as a group input.
def mme_geo_group_scene(group):
    S = MmeGeoScene(group, None, 1.0, None, None, {}, None)
    S.unit = S.B.new_input("Unit", "NodeSocketFloat", "Unit")
    S.B.input_items[S.unit.socket.identifier].default_value = 1.0
    return S


# Shared group: Blender object transform to an MMD row-vector world matrix.
def mme_geo_matrix_group():
    key = "core:mmd_matrix:GN:v%d" % MME_CORE_VERSION

    def build(group):
        S = mme_geo_group_scene(group)
        transform = S.B.new_input("Transform", "NodeSocketMatrix", "Transform")
        scale = S.B.new_input("Scale", "NodeSocketFloat", "Scale")
        S.B.input_items[scale.socket.identifier].default_value = 1.0
        S.B.statement("unit scale, swap Y/Z (MMD is left-handed, Y-up)")
        matrix = S.mmd_matrix_impl(transform, scale)
        S.B.finish([("Matrix", MmeSock(matrix), "NodeSocketMatrix")])
        group.description = "Blender object transform to an MMD row-vector world matrix"

    return mme_core_group(mme_group_name("Blender to MMD matrix", "GN", key), "GeometryNodeTree", key, build)


# Shared group: scene camera as MMD view/projection matrices and basis vectors.
def mme_geo_camera_group():
    key = "core:camera:GN:v%d" % MME_CORE_VERSION

    def build(group):
        S = mme_geo_group_scene(group)
        cam = S.camera_impl()
        S.B.statement("camera outputs")
        S.B.finish(
            [
                ("View", MmeSock(cam["view"]), "NodeSocketMatrix"),
                ("Projection", MmeSock(cam["proj"]), "NodeSocketMatrix"),
                ("Eye", MmeSock(cam["eye"]), "NodeSocketVector"),
                ("Right", MmeSock(cam["r"]), "NodeSocketVector"),
                ("Up", MmeSock(cam["u"]), "NodeSocketVector"),
                ("Forward", MmeSock(cam["f"]), "NodeSocketVector"),
                ("XS", cam["xs"], "NodeSocketFloat"),
                ("YS", cam["ys"], "NodeSocketFloat"),
                ("C22", cam["c22"], "NodeSocketFloat"),
                ("C32", cam["c32"], "NodeSocketFloat"),
                ("Orthographic", cam["ortho"], "NodeSocketBool"),
            ]
        )
        group.description = "Scene camera as MMD view and projection matrices"

    return mme_core_group(mme_group_name("Camera (MMD)", "GN", key), "GeometryNodeTree", key, build)


# Shared group: MMD light direction as view and projection matrices.
def mme_geo_light_group():
    key = "core:light:GN:v%d" % MME_CORE_VERSION

    def build(group):
        S = MmeGeoScene(group, None, 1.0, None, None, {}, None)
        direction = S.B.new_input("Direction", "NodeSocketVector", "Direction")
        S.B.statement("light view and projection")
        light = S.light_impl(direction)
        S.B.finish(
            [
                ("View", MmeSock(light["view"]), "NodeSocketMatrix"),
                ("Projection", MmeSock(light["proj"]), "NodeSocketMatrix"),
                ("Direction", MmeSock(light["d"]), "NodeSocketVector"),
                ("Eye", MmeSock(light["eye"]), "NodeSocketVector"),
            ]
        )
        group.description = "MMD light direction as light view and projection matrices"

    return mme_core_group(mme_group_name("Light (MMD)", "GN", key), "GeometryNodeTree", key, build)


# Shared group: clip-space vertex-shader output back to object space with a 'culled' flag (the node version of
# fx_clip_to_world).
def mme_geo_clip_group():
    key = "core:clip:GN:v%d" % MME_CORE_VERSION

    def build(group):
        S = mme_geo_group_scene(group)
        T = S.B
        x, y, z, w = [T.new_input("Clip " + c, "NodeSocketFloat", "Clip " + c) for c in "XYZW"]
        unit = S.unit
        cam = S.camera()
        T.statement("Clip space to MMD world space")
        ortho = cam["ortho"]
        vx = T.div(x, cam["xs"])
        vy = T.div(y, cam["ys"])
        vz = T.select(ortho, T.div(T.sub(z, T.mul(w, cam["c32"])), cam["c22"]), w)
        vw = T.select(ortho, w, 1.0)
        terms = [
            T.vm("SCALE", "right * x", T.vector_of(cam["r"]), scale=vx),
            T.vm("SCALE", "up * y", T.vector_of(cam["u"]), scale=vy),
            T.vm("SCALE", "forward * z", T.vector_of(cam["f"]), scale=vz),
            T.vm("SCALE", "eye * w", T.vector_of(cam["eye"]), scale=vw),
        ]
        total = T.vm("ADD", "view to world", T.vm("ADD", "right + up", terms[0], terms[1]), T.vm("ADD", "forward + eye", terms[2], terms[3]))
        world_vector = T.vm("SCALE", "divide by w", total, scale=T.div(1.0, vw))
        world = T.split_vector(world_vector)
        T.statement("Culling (outside the MMD view volume)")
        abs_w = T.unary("abs", w)
        tiny = T.cmp("<=", abs_w, 1e-12)
        natural = T.add(T.mul(cam["q"], w), -FX_NEAR * cam["q"])
        front = T.cmp(">", w, 1e-12)
        near_out = T.cmp("<", z, T.mul(-1e-6, T.max(1.0, abs_w)))
        far_out = T.cmp(">", z, T.add(T.mul(w, 1.0 + 1e-9), 1e-6))
        outside = T.land(front, T.lor(near_out, far_out))
        behind = T.land(T.cmp("<", w, -1e-12), T.cmp(">", T.unary("abs", T.sub(z, natural)), T.mul(1e-4, T.add(abs_w, 1.0))))
        persp = T.lor(tiny, T.lor(outside, behind))
        ortho_cull = T.lor(T.cmp("<", abs_w, 1e-12), T.lor(T.cmp("<", z, 0.0), T.cmp(">", z, w)))
        culled = T.select(ortho, ortho_cull, persp)
        for value in world:
            culled = T.lor(culled, T.lnot(T.cmp("==", value, value)))
        T.statement("MMD world to object space")
        blender = T.vm("SCALE", "MMD units to Blender", T.vector_of([world[0], world[2], world[1]]), scale=unit).socket
        to_local = S.node("FunctionNodeTransformPoint")
        S.link(blender, to_local.inputs["Vector"])
        S.link(S.invert(S.self_info().outputs["Transform"]), to_local.inputs["Transform"])
        T.statement("culled vertices collapse to the origin")
        choose = S.node("GeometryNodeSwitch", "culled ? origin : position", input_type="VECTOR")
        T.feed(choose.inputs["Switch"], culled)
        S.link(to_local.outputs[0], choose.inputs["False"])
        choose.inputs["True"].default_value = (0.0, 0.0, 0.0)
        T.finish([("Position", MmeSock(choose.outputs[0]), "NodeSocketVector"), ("Culled", culled, "NodeSocketBool")])
        group.description = "MMD clip-space vertex position to this object's local space, with MMD culling"

    return mme_core_group(mme_group_name("Clip to object (MMD)", "GN", key), "GeometryNodeTree", key, build)
