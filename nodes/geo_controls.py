import bpy
import numpy as np

from ..common.constants import (
    ACCESSORY_UNIT,
    MME_ATTR_SRC,
    MME_PARAM_PREFIX,
    PMM_FPS,
)
from ..common.controls import (
    mme_control_source,
    mme_control_use_prop,
    mme_param_meta,
    mme_shapekey_spec,
)
from ..common.scene_utils import scene_fps
from ..fx.types import (
    fx_is_numeric,
    fx_resolve_type,
)
from .names import mme_prop_name
from .socket import MmeSock


# Controller objects, bones and parameters as node inputs: each need from the vertex shader is fed by a driver
# or an object-info node.
class MmeGeoControls:
    def prop_spec(self, obj, path, index=None, expr="var"):
        var = {"name": "var", "type": "SINGLE_PROP", "id_type": "OBJECT", "id": obj.name, "path": path}
        if index is not None:
            var["index"] = index
        return {"expr": expr, "vars": [var]}

    def object_items(self, obj, item, gname, n, is_bool):
        lower = (item or "").lower()
        if obj is None:
            return [0.0] * n
        suffix = "" if obj == self.root else " (%s)" % obj.name
        if lower == "tr":
            return [self.driven("tr:" + obj.name, "Tr" + suffix, self.prop_spec(obj, '["mme_tr"]'), obj.get("mme_tr", 1.0))]
        if lower == "si":
            return [self.driven("si:" + obj.name, "Si" + suffix, self.prop_spec(obj, '["mme_si"]', expr="var * %r" % ACCESSORY_UNIT), float(obj.get("mme_si", 1.0)) * ACCESSORY_UNIT)]
        if lower in ("x", "y", "z", "xyz", ""):
            if lower == "" and is_bool:
                return [1.0]
            pos = self.split(self.scale(self.swap(self.object_info(obj).outputs["Location"]), 1.0 / self.unit))
            if lower == "xyz":
                return pos
            if lower == "":
                return pos + [1.0]
            return [pos["xyz".index(lower)]]
        if lower in ("rx", "ry", "rz", "rxyz"):
            axes = {"rx": [0], "ry": [2], "rz": [1], "rxyz": [0, 2, 1]}[lower]
            return [
                self.driven("rot:%s:%d" % (obj.name, k), "Rotation %s (%s)" % ("xyz"[k], obj.name), self.prop_spec(obj, "rotation_euler", k, "-var"), -obj.rotation_euler[k])
                for k in axes
            ]
        return [0.0]

    def bone_spec(self, armature, bone, kind):
        return {
            "expr": "var",
            "vars": [
                {
                    "name": "var",
                    "type": "TRANSFORMS",
                    "id": armature.name,
                    "bone": bone,
                    "transform_type": kind,
                    "space": "WORLD_SPACE",
                    "rotation_mode": "QUATERNION",
                }
            ],
        }

    def bone_location(self, armature, bone):
        key = ("bone", armature.name, bone)
        if key in self.cache:
            return self.cache[key]
        loc = [self.driven("bone:%s:%s:%s" % (armature.name, bone, t), "%s %s" % (bone, t), self.bone_spec(armature, bone, t)) for t in ("LOC_X", "LOC_Y", "LOC_Z")]
        self.cache[key] = loc
        return loc

    def bone_matrix(self, armature, bone):
        loc = self.combine(self.bone_location(armature, bone), "bone location")
        quat = self.node("FunctionNodeQuaternionToRotation")
        for name, kind in zip(("W", "X", "Y", "Z"), ("ROT_W", "ROT_X", "ROT_Y", "ROT_Z")):
            self.B.feed(quat.inputs[name], self.driven("bone:%s:%s:%s" % (armature.name, bone, kind), "%s %s" % (bone, kind), self.bone_spec(armature, bone, kind), 1.0 if kind == "ROT_W" else 0.0))
        scale = self.combine([self.driven("bone:%s:%s:%s" % (armature.name, bone, kind), "%s %s" % (bone, kind), self.bone_spec(armature, bone, kind), 1.0) for kind in ("SCALE_X", "SCALE_Y", "SCALE_Z")], "bone scale")
        combine = self.node("FunctionNodeCombineTransform")
        self.link(loc, combine.inputs["Translation"])
        self.link(quat.outputs[0], combine.inputs["Rotation"])
        self.link(scale, combine.inputs["Scale"])
        return combine.outputs[0]

    def control_matrix(self, gname):
        desc = self.controls.get(gname, {})
        kind = desc.get("kind")
        scale = ACCESSORY_UNIT * self.unit
        if kind == "self":
            return self.mmd_world(self.object_info(self.root).outputs["Transform"], scale)
        if kind in ("object", "model"):
            obj = bpy.data.objects.get(desc.get("object", ""))
            return self.mmd_world(self.object_info(obj).outputs["Transform"], scale)
        if kind == "bone":
            armature = bpy.data.objects.get(desc.get("object", ""))
            if armature is not None:
                return self.mmd_world(self.bone_matrix(armature, desc.get("bone", "")))
        return self.const_matrix(np.identity(4), "identity")

    def control_values(self, gname, n):
        values = self.control_values_raw(gname, n)
        desc = self.controls.get(gname, {})
        prop = mme_control_use_prop(desc)
        if prop is None:
            return values
        source = mme_control_source(desc)
        use = self.driven("use:" + prop, "Use " + source, self.prop_spec(self.root, '["%s"]' % prop), float(bool(self.root.get(prop, True))))
        return [self.B.mul(v, use) for v in values]

    def control_values_raw(self, gname, n):
        desc = self.controls.get(gname, {})
        kind = desc.get("kind")
        if kind == "shapekey":
            spec = mme_shapekey_spec(desc)
            if spec is None:
                return [0.0]
            obj = bpy.data.objects.get(desc.get("object", ""))
            value = obj.data.shape_keys.key_blocks[desc.get("key", "")].value
            return [self.driven("sk:%s:%s" % (desc.get("object", ""), desc.get("key", "")), desc.get("key", ""), spec, float(value))]
        g = self.program.globals.get(gname)
        t = fx_resolve_type(g.type, self.program) if g is not None else ("f", ())
        is_bool = fx_is_numeric(t) and t[0] == "b"
        if kind == "self":
            return self.object_items(self.root, desc.get("item", ""), gname, n, is_bool)
        if kind == "object":
            return self.object_items(bpy.data.objects.get(desc.get("object", "")), desc.get("item", ""), gname, n, is_bool)
        if kind == "prop":
            prop = desc.get("prop", "")
            return [self.driven("prop:" + prop, prop, self.prop_spec(self.root, '["%s"]' % prop), self.root.get(prop, 0.0))]
        if kind == "bone":
            armature = bpy.data.objects.get(desc.get("object", ""))
            if armature is None:
                return [0.0]
            x, y, z = self.bone_location(armature, desc.get("bone", ""))
            B = self.B
            return [B.div(x, self.unit), B.div(z, self.unit), B.div(y, self.unit), 1.0]
        if kind == "model":
            if is_bool:
                return [1.0]
            return self.object_items(bpy.data.objects.get(desc.get("object", "")), "", gname, n, is_bool)
        return [0.0]

    def param_values(self, gname, n):
        meta = mme_param_meta(self.root).get(gname, {})
        prop = meta.get("prop") or mme_prop_name(MME_PARAM_PREFIX, gname)
        size = int(meta.get("size", 1))
        default = list(self.root.get(prop, meta.get("default", [0.0]))) if size > 1 else [self.root.get(prop, (meta.get("default") or [0.0])[0])]
        out = []
        for k in range(size):
            path = '["%s"]' % prop
            label = meta.get("label") or gname
            if size > 1:
                label = "%s.%s" % (label, "xyzw"[k] if k < 4 else str(k))
            out.append(self.driven("param:%s:%d" % (gname, k), label, self.prop_spec(self.root, path, k if size > 1 else None), float(default[k]) if k < len(default) else 0.0))
        return out

    def scene_spec(self, expr, names):
        paths = {"fps": "render.fps", "base": "render.fps_base", "rx": "render.resolution_x", "ry": "render.resolution_y", "pct": "render.resolution_percentage"}
        return {"expr": expr, "vars": [{"name": n, "type": "SINGLE_PROP", "id_type": "SCENE", "id": self.scene.name, "path": paths[n]} for n in names]}

    def scalar_values(self, source, gname, n):
        kind = source[0]
        key = ("scalar", gname)
        if key in self.cache:
            return self.cache[key]
        if kind == "position":
            if source[1] == "light":
                values = self.light()["eye"] + [1.0]
            else:
                values = self.camera()["eye"] + [1.0]
        elif kind == "direction":
            values = (self.light()["d"] if source[1] == "light" else self.camera()["f"]) + [0.0]
        elif kind == "light_color":
            values = [0.0, 0.0, 0.0, 1.0] if source[1] == "DIFFUSE" else list(self.light()["color"]) + [1.0]
        elif kind == "time":
            time_node = self.node("GeometryNodeInputSceneTime")
            values = [MmeSock(time_node.outputs["Seconds"])]
        elif kind == "elapsed":
            fps = scene_fps(self.scene) or PMM_FPS
            values = [self.driven("elapsed", "ElapsedTime", self.scene_spec("base / fps", ("fps", "base")), 1.0 / fps)]
        elif kind == "viewport":
            r = self.scene.render
            values = [
                self.driven("viewport:0", "Viewport width", self.scene_spec("rx * pct / 100", ("rx", "pct")), r.resolution_x * r.resolution_percentage / 100.0),
                self.driven("viewport:1", "Viewport height", self.scene_spec("ry * pct / 100", ("ry", "pct")), r.resolution_y * r.resolution_percentage / 100.0),
            ]
        elif kind == "control":
            values = self.control_values(gname, n)
        elif kind == "param":
            values = self.param_values(gname, n)
        else:
            values = [0.0] * n
        values = (list(values) + [0.0] * n)[:n]
        self.cache[key] = values
        return values

    # Supplies one 'need' of the vertex shader (matrix, scalar, vertex attribute or loop index) as a node
    # socket or constant.
    def provide(self, need):
        kind = need[0]
        if kind == "matrix":
            source, gname = need[1], need[2]
            self.B.statement("%s" % gname)
            if source[0] == "matrix":
                return self.transform_matrix(source)
            return self.control_matrix(gname)
        if kind == "scalar":
            source, gname, k, n = need[1], need[2], need[3], need[4]
            self.B.statement("%s" % gname)
            return self.scalar_values(source, gname, n)[k]
        if kind == "vertex":
            name, k = need[1], need[2]
            key = ("vattr", name)
            if key not in self.cache:
                self.B.statement("Vertex data from the .x file")
                if name == MME_ATTR_SRC:
                    node = self.node("GeometryNodeInputNamedAttribute", "source vertex index", data_type="INT")
                    node.inputs["Name"].default_value = name
                    self.cache[key] = [MmeSock(node.outputs["Attribute"])]
                else:
                    node = self.node("GeometryNodeInputNamedAttribute", name, data_type="FLOAT_VECTOR")
                    node.inputs["Name"].default_value = name
                    self.cache[key] = self.split(node.outputs["Attribute"])
            return self.cache[key][k]
        if kind == "loop":
            return self.cache.get(("loopvar", need[1]), 0.0)
        return 0.0
