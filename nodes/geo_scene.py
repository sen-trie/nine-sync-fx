import bpy
import numpy as np

from ..common.constants import (
    LIGHT_COLLECTION,
    MME_SWAP,
)
from ..common.scene_utils import (
    child_of_type,
    find_rig_root,
)
from ..fx.semantics import (
    FX_FAR,
    FX_NEAR,
    FxSceneState,
)
from .backend_geo import MmeGeoBackend
from .geo_controls import MmeGeoControls
from .socket import MmeSock


# Flattens a matrix into a list of 16 plain floats.
def mme_np_comps(matrix):
    return [float(x) for x in np.asarray(matrix, dtype=np.float64).reshape(-1)]


# Builds the scene side of a vertex-shader tree: Blender objects, lights and bones turned into the matrices
# and values the shader expects,
# with drivers that keep them current. Group-based helpers are added by MmeGeoScene.
class MmeGeoSceneCore(MmeGeoControls):
    def __init__(self, tree, root, unit, scene, state, controls, program):
        self.tree = tree
        self.B = MmeGeoBackend(tree)
        self.root = root
        self.unit = unit
        self.scene = scene
        self.state = state
        self.controls = controls
        self.program = program
        self.cache = {}
        self.drivers = []
        self.defaults = {}

    def node(self, idname, label=None, **props):
        node = self.B.new(idname)
        for key, value in props.items():
            setattr(node, key, value)
        if label:
            node.label = label
        return node

    def link(self, a, b):
        self.tree.links.new(a.socket if isinstance(a, MmeSock) else a, b)

    def sock(self, socket):
        return MmeSock(socket)

    # A float group input that a driver feeds from a property; the driver spec and default are recorded for
    # the modifier setup.
    def driven(self, key, label, spec, default=0.0):
        found = self.B.inputs.get(key)
        if found is not None:
            return found
        sock = self.B.new_input(key, "NodeSocketFloat", label)
        item = self.B.input_items[sock.socket.identifier]
        item.default_value = float(default)
        self.defaults[sock.socket.identifier] = float(default)
        self.drivers.append((sock.socket.identifier, spec))
        return sock

    def vec_node(self, op, a, b=None, label=None):
        node = self.node("ShaderNodeVectorMath", label, operation=op)
        for index, value in enumerate((a, b)):
            if value is None:
                continue
            if isinstance(value, (tuple, list)):
                node.inputs[index].default_value = value
            else:
                self.link(value, node.inputs[index])
        out = node.outputs["Value"] if op in ("DOT_PRODUCT", "LENGTH", "DISTANCE") else node.outputs["Vector"]
        return out

    def scale(self, vec, factor):
        node = self.node("ShaderNodeVectorMath", operation="SCALE")
        self.link(vec, node.inputs[0])
        self.B.feed(node.inputs["Scale"], factor)
        return node.outputs["Vector"]

    def split(self, vec):
        node = self.node("ShaderNodeSeparateXYZ")
        self.link(vec, node.inputs[0])
        return [MmeSock(node.outputs[k]) for k in range(3)]

    def combine(self, comps, label=None):
        node = self.node("ShaderNodeCombineXYZ", label)
        for k, comp in enumerate(comps):
            self.B.feed(node.inputs[k], comp)
        return node.outputs[0]

    def swap(self, vec):
        x, y, z = self.split(vec)
        return self.combine([x, z, y], "Blender to MMD axes")

    def const_matrix(self, matrix, label):
        key = ("cmat", tuple(round(v, 9) for v in np.asarray(matrix).reshape(-1)))
        if key in self.cache:
            return self.cache[key]
        node = self.node("FunctionNodeCombineMatrix", label)
        m = np.asarray(matrix, dtype=np.float64)
        for c in range(4):
            for r in range(4):
                node.inputs[c * 4 + r].default_value = float(m[r][c])
        self.cache[key] = node.outputs[0]
        return node.outputs[0]

    def combine_matrix(self, row_comps, label):
        node = self.node("FunctionNodeCombineMatrix", label)
        for k, comp in enumerate(row_comps):
            self.B.feed(node.inputs[k], comp)
        return node.outputs[0]

    def mat_mul(self, *matrices):
        result = matrices[0]
        for other in matrices[1:]:
            node = self.node("FunctionNodeMatrixMultiply")
            self.link(result, node.inputs[0])
            self.link(other, node.inputs[1])
            result = node.outputs[0]
        return result

    def invert(self, matrix):
        node = self.node("FunctionNodeInvertMatrix")
        self.link(matrix, node.inputs[0])
        return node.outputs[0]

    def transpose(self, matrix):
        node = self.node("FunctionNodeTransposeMatrix")
        self.link(matrix, node.inputs[0])
        return node.outputs[0]

    def object_info(self, obj):
        key = ("info", obj.name if obj is not None else "")
        if key in self.cache:
            return self.cache[key]
        node = self.node("GeometryNodeObjectInfo", "Object: %s" % (obj.name if obj else "-"), transform_space="ORIGINAL")
        node.inputs["Object"].default_value = obj
        self.cache[key] = node
        return node

    def self_info(self):
        if "self" in self.cache:
            return self.cache["self"]
        self_node = self.node("GeometryNodeSelfObject")
        info = self.node("GeometryNodeObjectInfo", "This pass object", transform_space="ORIGINAL")
        self.link(self_node.outputs[0], info.inputs["Object"])
        self.cache["self"] = info
        return info

    def inv_unit(self):
        return self.B.div(1.0, self.unit)

    # Places a node group and connects named inputs from sockets or constants.
    def group_node(self, group, label, inputs):
        node = self.node(self.B.group_idname, label)
        node.node_tree = group
        for name, value in inputs.items():
            target = node.inputs.get(name)
            if target is None:
                continue
            if isinstance(value, (MmeSock, bpy.types.NodeSocket)):
                self.link(value, target)
            else:
                self.B.feed(target, value)
        return node

    # Blender object transform to MMD's matrix: unit scale and the Y/Z swap between Blender's right-handed
    # Z-up and MMD's left-handed Y-up.
    def mmd_matrix_impl(self, transform, scale):
        iu = self.inv_unit()
        pre = self.combine_matrix([iu, 0.0, 0.0, 0.0, 0.0, 0.0, iu, 0.0, 0.0, iu, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0], "MMD units and axes")
        scaled = self.combine_matrix([scale, 0.0, 0.0, 0.0, 0.0, scale, 0.0, 0.0, 0.0, 0.0, scale, 0.0, 0.0, 0.0, 0.0, 1.0], "accessory scale")
        return self.mat_mul(pre, transform, scaled, self.const_matrix(MME_SWAP, "swap Y/Z"))


    def world(self):
        if "world" not in self.cache:
            self.B.statement("WorldMatrix: this pass object in MMD space")
            self.cache["world"] = self.mmd_world(self.self_info().outputs["Transform"])
        return self.cache["world"]

    def look(self, eye, f, up, label):
        r = self.vec_node("NORMALIZE", self.vec_node("CROSS_PRODUCT", up, f))
        u = self.vec_node("CROSS_PRODUCT", f, r)
        rs, us, fs = self.split(r), self.split(u), self.split(f)
        dots = [self.B.neg(MmeSock(self.vec_node("DOT_PRODUCT", v, eye))) for v in (r, u, f)]
        comps = []
        for k in range(3):
            comps.extend([rs[k], us[k], fs[k], 0.0])
        comps.extend(dots + [1.0])
        return {"r": rs, "u": us, "f": fs, "rv": r, "uv": u, "matrix": self.combine_matrix(comps, label)}

    # Reads the active camera inside the tree: the view matrix is built from its eye, forward and up in MMD
    # axes, the projection from Blender's projection matrix with MMD's near/far.
    # Orthographic cameras get their own terms, chosen by a select on Is Orthographic.
    def camera_impl(self):
        B = self.B
        B.statement("Camera: view matrix in MMD space")
        active = self.node("GeometryNodeInputActiveCamera")
        info = self.node("GeometryNodeObjectInfo", "Scene camera", transform_space="ORIGINAL")
        self.link(active.outputs[0], info.inputs["Object"])
        transform = info.outputs["Transform"]
        eye = self.scale(self.swap(info.outputs["Location"]), self.inv_unit())
        forward = self.node("FunctionNodeTransformDirection")
        forward.inputs["Direction"].default_value = (0.0, 0.0, -1.0)
        self.link(transform, forward.inputs["Transform"])
        up = self.node("FunctionNodeTransformDirection")
        up.inputs["Direction"].default_value = (0.0, 1.0, 0.0)
        self.link(transform, up.inputs["Transform"])
        f = self.vec_node("NORMALIZE", self.swap(forward.outputs[0]))
        view = self.look(eye, f, self.swap(up.outputs[0]), "ViewMatrix")
        B.statement("Camera: projection matrix (MMD, near %g, far %g)" % (FX_NEAR, FX_FAR))
        cam_info = self.node("GeometryNodeCameraInfo")
        self.link(active.outputs[0], cam_info.inputs["Camera"])
        proj = self.node("FunctionNodeSeparateMatrix", "Blender projection")
        self.link(cam_info.outputs["Projection Matrix"], proj.inputs[0])
        ortho = MmeSock(cam_info.outputs["Is Orthographic"])
        B.boolish.add(B.key(ortho))
        p00, p11 = MmeSock(proj.outputs[0]), MmeSock(proj.outputs[5])
        xs = B.select(ortho, B.mul(p00, self.unit), p00)
        ys = B.select(ortho, B.mul(p11, self.unit), p11)
        q = FX_FAR / (FX_FAR - FX_NEAR)
        c22 = B.select(ortho, 1.0 / (FX_FAR - FX_NEAR), q)
        c23 = B.select(ortho, 0.0, 1.0)
        c32 = B.select(ortho, -FX_NEAR / (FX_FAR - FX_NEAR), -FX_NEAR * q)
        c33 = B.select(ortho, 1.0, 0.0)
        comps = [xs, 0.0, 0.0, 0.0, 0.0, ys, 0.0, 0.0, 0.0, 0.0, c22, c23, 0.0, 0.0, c32, c33]
        return {
            "eye": eye,
            "f": f,
            "r": view["rv"],
            "u": view["uv"],
            "view": view["matrix"],
            "proj": self.combine_matrix(comps, "ProjectionMatrix"),
            "ortho": ortho,
            "xs": xs,
            "ys": ys,
            "c22": c22,
            "c32": c32,
        }


    def light_object(self):
        collection = bpy.data.collections.get(LIGHT_COLLECTION)
        rig = find_rig_root(collection, "LIGHT") if collection else None
        return child_of_type(rig, "LIGHT") if rig else None

    # View and projection of MMD's light from a direction input.
    def light_impl(self, direction):
        d = self.vec_node("NORMALIZE", direction)
        eye = self.scale(d, -1000.0)
        view = self.look(eye, d, (0.0, 1.0, 0.0), "Light ViewMatrix")
        _view, proj = FxSceneState().light_matrices()
        return {"d": d, "eye": eye, "view": view["matrix"], "proj": self.const_matrix(np.asarray(proj).T, "Light ProjectionMatrix")}


    def transform_matrix(self, source):
        base, suffix, target = source[1], source[2], source[3]
        if target == "light":
            parts = self.light()
        else:
            parts = self.camera()
        view, proj = parts["view"], parts["proj"]
        if base == "WORLD":
            m = self.world()
        elif base == "VIEW":
            m = view
        elif base == "PROJECTION":
            m = proj
        elif base == "WORLDVIEW":
            m = self.mat_mul(view, self.world())
        elif base == "VIEWPROJECTION":
            m = self.mat_mul(proj, view)
        else:
            m = self.mat_mul(proj, view, self.world())
        if "INVERSE" in suffix:
            m = self.invert(m)
        if "TRANSPOSE" in suffix:
            m = self.transpose(m)
        return m












