import json

from ..common.constants import (
    MME_CORE_VERSION,
    MME_GROUP_LABEL,
    MME_RET_PROP,
)
from ..common.scene_utils import node_socket
from ..fx.constants import FX_GROUP_INTRINSICS
from ..fx.errors import FxError
from ..fx.function_info import (
    fx_comp_label,
    fx_function_info,
    fx_type_name,
)
from ..fx.interp.executor import FxExec
from ..fx.interp.intrinsics import fx_intrinsic_values
from ..fx.semantics import fx_sampler_texture_global
from ..fx.text import MME_LABEL_LIMIT
from ..fx.types import (
    FxValue,
    fx_count,
    fx_is_numeric,
    fx_resolve_type,
)
from .core_library import mme_core_group
from .images import mme_slot_image
from .names import mme_group_name
from .socket import MmeSock


# Environment used while building a shared group: it supplies no globals and turns texture reads into image-
# slot inputs of the group.
class MmeGroupEnv:
    def __init__(self, program):
        self.program = program
        self.rt_lookup = None

    def global_value(self, ex, g):
        return None

    def sample(self, ex, info, u, v, lod):
        g = fx_sampler_texture_global(self.program, info)
        slot = g.name if g is not None else (info.name if info is not None else "texture")
        point = info.point() if info else False
        address = info.address() if info else "WRAP"
        extension = {"CLAMP": "EXTEND", "MIRROR": "MIRROR"}.get(address, "REPEAT")
        return ex.B.image_slot(slot, "Closest" if point else "Linear", extension, u, v)


# Everything about shared node groups for the tree backends: HLSL functions and intrinsics as reusable groups,
# vector/matrix packing,
# and native node versions of common intrinsics.
class MmeGroupCalls:
    group_mode = False

    # How a group exposes a value: float3 as one vector socket, float4x4 as one matrix socket (Geometry Nodes
    # only), anything else as separate float sockets.
    def iface_kind(self, t):
        if t == ("f", (3,)):
            return "VECTOR"
        if t == ("f", (4, 4)) and self.tree_tag == "GN":
            return "MATRIX"
        return "FLOATS"

    # Packs three scalars into a vector socket, or a plain tuple while all are constants. When the scalars are
    # just the outputs of one Separate XYZ,
    # the original vector is reused instead of adding a Combine node.
    def vector_of(self, comps, force=False):
        comps = list(comps)
        if not force and all(not isinstance(c, MmeSock) for c in comps):
            return tuple(float(c) for c in comps)
        if all(isinstance(c, MmeSock) for c in comps):
            node = comps[0].socket.node
            if (
                node.bl_idname == "ShaderNodeSeparateXYZ"
                and all(c.socket.node == node for c in comps)
                and [c.socket.identifier for c in comps] == [o.identifier for o in node.outputs[:3]]
                and node.inputs[0].is_linked
            ):
                return MmeSock(node.inputs[0].links[0].from_socket)
        key = ("vec3",) + tuple(self.key(c) for c in comps)
        found = self.cache.get(key)
        if found is not None:
            return found
        node = self.new("ShaderNodeCombineXYZ")
        for k, comp in enumerate(comps):
            self.feed(node.inputs[k], comp)
        result = MmeSock(node.outputs[0])
        self.cache[key] = result
        return result

    def split_vector(self, value):
        if not isinstance(value, MmeSock):
            return [float(v) for v in value]
        key = ("split3", self.key(value))
        found = self.cache.get(key)
        if found is not None:
            return list(found)
        node = self.new("ShaderNodeSeparateXYZ")
        self.links.new(value.socket, node.inputs[0])
        comps = [MmeSock(node.outputs[k]) for k in range(3)]
        self.cache[key] = comps
        return list(comps)

    # Same shortcut as vector_of for 16 scalars and Combine/Separate Matrix.
    def matrix_of(self, comps):
        comps = list(comps)
        if all(isinstance(c, MmeSock) for c in comps):
            node = comps[0].socket.node
            if (
                node.bl_idname == "FunctionNodeSeparateMatrix"
                and all(c.socket.node == node for c in comps)
                and [c.socket.identifier for c in comps] == [o.identifier for o in node.outputs[:16]]
                and node.inputs[0].is_linked
            ):
                return MmeSock(node.inputs[0].links[0].from_socket)
        key = ("mat4",) + tuple(self.key(c) for c in comps)
        found = self.cache.get(key)
        if found is not None:
            return found
        node = self.new("FunctionNodeCombineMatrix")
        for k, comp in enumerate(comps):
            self.feed(node.inputs[k], comp)
        result = MmeSock(node.outputs[0])
        self.cache[key] = result
        return result

    def split_matrix(self, value):
        key = ("split16", self.key(value))
        found = self.cache.get(key)
        if found is not None:
            return list(found)
        node = self.new("FunctionNodeSeparateMatrix")
        self.links.new(value.socket, node.inputs[0])
        comps = [MmeSock(node.outputs[k]) for k in range(16)]
        self.cache[key] = comps
        return list(comps)

    def feed_value(self, socket, value):
        if isinstance(value, MmeSock):
            self.links.new(value.socket, socket)
        elif isinstance(value, tuple):
            socket.default_value = value
        else:
            self.feed(socket, value)

    def group_inputs(self, label, t):
        kind = self.iface_kind(t)
        if kind == "VECTOR":
            return FxValue(t, self.split_vector(self.new_input(label, "NodeSocketVector", label)))
        if kind == "MATRIX":
            return FxValue(t, self.split_matrix(self.new_input(label, "NodeSocketMatrix", label)))
        return FxValue(t, [self.new_input(fx_comp_label(label, t, k), "NodeSocketFloat", fx_comp_label(label, t, k)) for k in range(fx_count(t[1]))])

    def group_outputs(self, label, value):
        t = ("f", value.t[1])
        kind = self.iface_kind(t)
        if kind == "VECTOR":
            return [(label, self.vector_of(value.c, force=True), "NodeSocketVector")]
        if kind == "MATRIX":
            return [(label, self.matrix_of(value.c), "NodeSocketMatrix")]
        return [(fx_comp_label(label, t, k), c, "NodeSocketFloat") for k, c in enumerate(value.c)]

    # Builds a user HLSL function as a shared group, keyed by its content hash so the same function in another
    # effect reuses the group.
    # The return type is stored on the group so callers can unpack the outputs.
    def function_group(self, ex, function, ptypes, rt, info):
        key = "fn:%s:%s:%s:v%d" % (self.tree_tag, function.name, info["hash"], MME_CORE_VERSION)
        name = mme_group_name("fn %s" % function.name, self.tree_tag, key)
        program = ex.program
        backend = type(self)

        def build(group):
            sub = backend(group)
            sub.group_mode = True
            sub_ex = FxExec(program, sub, MmeGroupEnv(program))
            values = [sub.group_inputs(p.name, pt) for p, pt in zip(function.params, ptypes)]
            result, _outs = sub_ex.run_function(function, values)
            result = sub_ex.convert(result, rt)
            sub.statement("return")
            sub.finish(sub.group_outputs("return", result))
            group[MME_RET_PROP] = json.dumps([rt[0], list(rt[1])])
            group.description = "HLSL function %s from an MME effect" % function.name

        return mme_core_group(name, self.tree.bl_idname, key, build)

    # Builds an intrinsic overload (for example mul(float4, float4x4)) as a shared group, preferring the
    # native matrix nodes, then vector maths, then the generic implementation.
    def intrinsic_group_tree(self, ex, name, types):
        signature = "%s(%s)" % (name, ",".join(fx_type_name(t) for t in types))
        key = "in:%s:%s:v%d" % (self.tree_tag, signature, MME_CORE_VERSION)
        program = ex.program
        backend = type(self)

        def build(group):
            sub = backend(group)
            sub.group_mode = True
            sub_ex = FxExec(program, sub, MmeGroupEnv(program))
            labels = ["abc"[i] if i < 3 else "arg%d" % i for i in range(len(types))]
            sub.statement(signature)
            result = sub.matrix_intrinsic(name, types, labels)
            if result is None and name == "mul":
                result = sub.vector_mul(types, labels)
            if result is None:
                values = [sub.group_inputs(label, t) for label, t in zip(labels, types)]
                result = fx_intrinsic_values(sub_ex, name, values)
            rt = ("f", result.t[1])
            group[MME_RET_PROP] = json.dumps([rt[0], list(rt[1])])
            sub.finish(sub.group_outputs("result", FxValue(rt, result.c)))
            group.description = "HLSL intrinsic %s" % signature

        return mme_core_group(mme_group_name(signature, self.tree_tag, key), self.tree.bl_idname, key, build)

    # mul with a 4x4 matrix on Geometry Nodes maps onto the Matrix Multiply node. A vector becomes the first
    # column of a matrix and
    # the matrix is transposed when needed, since HLSL here uses row vectors and Blender uses column vectors.
    def matrix_intrinsic(self, name, types, labels):
        if self.tree_tag != "GN" or name != "mul":
            return None
        m4, v4 = ("f", (4, 4)), ("f", (4,))
        if types == [v4, m4] or types == [m4, v4]:
            vector_first = types[0] == v4
            v = self.group_inputs(labels[0] if vector_first else labels[1], v4)
            m = self.new_input(labels[1] if vector_first else labels[0], "NodeSocketMatrix", labels[1] if vector_first else labels[0])
            column = self.new("FunctionNodeCombineMatrix")
            column.label = "vector as a matrix column"
            for k in range(16):
                self.feed(column.inputs[k], v.c[k] if k < 4 else 0.0)
            source = m.socket
            if not vector_first:
                transpose = self.new("FunctionNodeTransposeMatrix")
                self.links.new(m.socket, transpose.inputs[0])
                source = transpose.outputs[0]
            multiply = self.new("FunctionNodeMatrixMultiply")
            self.links.new(source, multiply.inputs[0])
            self.links.new(column.outputs[0], multiply.inputs[1])
            out = self.split_matrix(MmeSock(multiply.outputs[0]))
            return FxValue(v4, out[:4])
        if types == [m4, m4]:
            a = self.new_input(labels[0], "NodeSocketMatrix", labels[0])
            b = self.new_input(labels[1], "NodeSocketMatrix", labels[1])
            multiply = self.new("FunctionNodeMatrixMultiply")
            self.links.new(b.socket, multiply.inputs[0])
            self.links.new(a.socket, multiply.inputs[1])
            return FxValue(m4, self.split_matrix(MmeSock(multiply.outputs[0])))
        return None

    def vm(self, op, label, a, b=None, scale=None, output="Vector"):
        inputs = [("Vector", a)]
        if b is not None:
            inputs.append(("Vector_001", b))
        if scale is not None:
            inputs.append(("Scale", scale))
        return self.native_node("ShaderNodeVectorMath", label, {"operation": op}, inputs, output)

    # Row vector times matrix as scaled row sums; the w component is computed separately with a dot product
    # when the matrix has four columns.
    def vec_mat(self, v, m, dims):
        rows, cols = dims
        k = min(len(v), rows)
        acc = None
        for i in range(k):
            row = self.vector_of(m[i * cols:i * cols + 3])
            term = self.vm("SCALE", "v[%d] * row %d" % (i, i), row, scale=v[i])
            acc = term if acc is None else self.vm("ADD", "sum of rows", acc, term)
        out = self.split_vector(acc)
        if cols == 4:
            column = self.vector_of([m[i * cols + 3] for i in range(3)])
            w = self.vm("DOT_PRODUCT", "w = dot(v.xyz, column w)", self.vector_of(v[:3]), column, output="Value")
            if k == 4:
                w = self.add(w, self.mul(v[3], m[3 * cols + 3]))
            out.append(w)
        return out

    # Matrix times column vector as one dot product per row.
    def mat_vec(self, m, dims, v):
        rows, cols = dims
        k = min(cols, len(v))
        vector = self.vector_of(v[:3])
        out = []
        for i in range(rows):
            value = self.vm("DOT_PRODUCT", "dot(row %d, v)" % i, self.vector_of(m[i * cols:i * cols + 3]), vector, output="Value")
            if k == 4:
                value = self.add(value, self.mul(m[i * cols + 3], v[3]))
            out.append(value)
        return out

    # mul() built from vector-maths nodes for the shader tree, where there is no matrix socket. Only 3- and
    # 4-wide shapes are handled; others return None.
    def vector_mul(self, types, labels):
        (da, db) = (types[0][1], types[1][1])
        ok = False
        if len(da) == 1 and len(db) == 2:
            ok = db[1] in (3, 4) and min(da[0], db[0]) in (3, 4)
        elif len(da) == 2 and len(db) == 1:
            ok = min(da[1], db[0]) in (3, 4)
        elif len(da) == 2 and len(db) == 2:
            ok = db[1] in (3, 4) and min(da[1], db[0]) in (3, 4)
        if not ok:
            return None
        a, b = [self.group_inputs(label, t) for label, t in zip(labels, types)]
        if len(da) == 1:
            return FxValue(("f", (db[1],)), self.vec_mat(a.c, b.c, db))
        if len(db) == 1:
            return FxValue(("f", (da[0],)), self.mat_vec(a.c, da, b.c))
        out = []
        for i in range(da[0]):
            out.extend(self.vec_mat(a.c[i * da[1]:(i + 1) * da[1]], b.c, db))
        return FxValue(("f", (da[0], db[1])), out)

    # Instantiates a shared group: feeds each argument to its whole-vector, whole-matrix or per-component
    # inputs and unpacks the result.
    # Identical calls (same group, same input sockets) are cached and reused.
    def place_group(self, group, names, values, out_name, label, slots=(), env=None):
        try:
            ret = json.loads(group.get(MME_RET_PROP, '["f", []]'))
        except (TypeError, ValueError):
            ret = ["f", []]
        rt = (ret[0], tuple(ret[1]))
        cache_key = ("call", group.name) + tuple(self.key(c) for v in values for c in v.c)
        found = self.cache.get(cache_key)
        if found is not None:
            return FxValue(rt, list(found))
        node = self.new(self.group_idname)
        node.node_tree = group
        node.label = ("%s: %s" % (MME_GROUP_LABEL, label))[:MME_LABEL_LIMIT]
        inputs = {s.name: s for s in node.inputs}
        for name, value in zip(names, values):
            whole = inputs.get(name)
            if whole is not None and whole.type == "VECTOR":
                self.feed_value(whole, self.vector_of(value.c))
                continue
            if whole is not None and whole.type == "MATRIX":
                self.feed_value(whole, self.matrix_of(value.c))
                continue
            for k, comp in enumerate(value.c):
                target = inputs.get(fx_comp_label(name, value.t, k))
                if target is not None:
                    self.feed(target, comp)
        for slot in slots:
            target = inputs.get("tex:" + slot)
            if target is not None:
                target.default_value = mme_slot_image(env, slot)
        outputs = {s.name: s for s in node.outputs}
        whole = outputs.get(out_name)
        if whole is not None and whole.type == "VECTOR":
            comps = self.split_vector(MmeSock(whole))
        elif whole is not None and whole.type == "MATRIX":
            comps = self.split_matrix(MmeSock(whole))
        else:
            comps = []
            for k in range(fx_count(rt[1])):
                socket = outputs.get(fx_comp_label(out_name, rt, k))
                comps.append(MmeSock(socket) if socket is not None else 0.0)
        self.cache[cache_key] = comps
        return FxValue(rt, comps)

    # Decides whether a user function call can be a group call: the function must be pure, have no out
    # parameters or arrays,
    # and at least one argument must be non-constant. Otherwise the function is inlined.
    def call_group(self, ex, function, args):
        info = fx_function_info(ex.program, function)
        if not info["pure"] or (info["slots"] and not self.image_inputs):
            return None
        params = function.params
        if len(args) != len(params):
            return None
        if any("out" in p.quals or "inout" in p.quals for p in params):
            return None
        try:
            rt = fx_resolve_type(function.ret, ex.program)
            ptypes = [fx_resolve_type(p.type, ex.program) for p in params]
        except FxError:
            return None
        if not fx_is_numeric(rt):
            return None
        for p, pt, a in zip(params, ptypes, args):
            if p.array is not None or not fx_is_numeric(pt) or not fx_is_numeric(a.t):
                return None
        values = [ex.convert(a, pt) for a, pt in zip(args, ptypes)]
        if all(self.known(c) is not None for v in values for c in v.c):
            return None
        group = self.function_group(ex, function, ptypes, rt, info)
        label = "%s(%s)" % (function.name, ", ".join(p.name for p in params))
        result = self.place_group(group, [p.name for p in params], values, "return", label, info["slots"], ex.env)
        return ex.convert(result, rt)

    # Creates (or reuses) a plain node with given properties and inputs; the cache key includes the properties
    # so different operations do not collide.
    def native_node(self, idname, label, props, inputs, output):
        key = ("native", idname, tuple(sorted(props.items()))) + tuple((n, self.key(v) if not isinstance(v, tuple) else v) for n, v in inputs)
        found = self.cache.get(key)
        if found is not None:
            return found
        node = self.new(idname)
        node.label = label
        for prop, value in props.items():
            setattr(node, prop, value)
        for identifier, value in inputs:
            self.feed_value(node_socket(node, identifier), value)
        result = MmeSock(node_socket(node, output, outputs=True))
        self.cache[key] = result
        return result

    # Maps an intrinsic onto a single native node (vector maths, clamp, map range or mix) when the argument
    # shapes allow it, so most shaders do not need a group per call.
    def native_intrinsic(self, ex, name, args):
        if not args or not all(fx_is_numeric(a.t) for a in args):
            return None
        if all(self.known(c) is not None for a in args for c in a.c):
            return None
        v3 = ("f", (3,))
        v4 = ("f", (4,))
        types = [("f", a.t[1]) for a in args]
        if name in ("dot", "length", "normalize") and all(t == v4 for t in types):
            a = args[0]
            b = args[1] if name == "dot" else args[0]
            d = self.vm("DOT_PRODUCT", "dot(xyz)", self.vector_of(a.c[:3]), self.vector_of(b.c[:3]), output="Value")
            d = self.add(d, self.mul(a.c[3], b.c[3]))
            if name == "dot":
                return FxValue(("f", ()), [d])
            length = self.unary("sqrt", d)
            if name == "length":
                return FxValue(("f", ()), [length])
            inv = self.select(self.cmp(">", length, 1e-20), self.div(1.0, length), 0.0)
            xyz = self.split_vector(self.vm("SCALE", "normalize xyz", self.vector_of(a.c[:3]), scale=inv))
            return FxValue(v4, xyz + [self.mul(a.c[3], inv)])
        if name in ("cross", "normalize", "length", "dot", "distance", "reflect") and all(t == v3 for t in types):
            vectors = [self.vector_of(a.c) for a in args]
            if name == "normalize":
                out = self.native_node("ShaderNodeVectorMath", "normalize", {"operation": "NORMALIZE"}, [("Vector", vectors[0])], "Vector")
                return FxValue(v3, self.split_vector(out))
            if name == "length":
                out = self.native_node("ShaderNodeVectorMath", "length", {"operation": "LENGTH"}, [("Vector", vectors[0])], "Value")
                return FxValue(("f", ()), [out])
            if name == "cross":
                out = self.native_node("ShaderNodeVectorMath", "cross", {"operation": "CROSS_PRODUCT"}, [("Vector", vectors[0]), ("Vector_001", vectors[1])], "Vector")
                return FxValue(v3, self.split_vector(out))
            if name == "dot":
                out = self.native_node("ShaderNodeVectorMath", "dot", {"operation": "DOT_PRODUCT"}, [("Vector", vectors[0]), ("Vector_001", vectors[1])], "Value")
                return FxValue(("f", ()), [out])
            if name == "distance":
                out = self.native_node("ShaderNodeVectorMath", "distance", {"operation": "DISTANCE"}, [("Vector", vectors[0]), ("Vector_001", vectors[1])], "Value")
                return FxValue(("f", ()), [out])
            d = self.native_node("ShaderNodeVectorMath", "dot(n, i)", {"operation": "DOT_PRODUCT"}, [("Vector", vectors[1]), ("Vector_001", vectors[0])], "Value")
            scaled = self.native_node("ShaderNodeVectorMath", "2 * dot(n, i) * n", {"operation": "SCALE"}, [("Vector", vectors[1]), ("Scale", self.mul(2.0, d))], "Vector")
            out = self.native_node("ShaderNodeVectorMath", "reflect", {"operation": "SUBTRACT"}, [("Vector", vectors[0]), ("Vector_001", scaled)], "Vector")
            return FxValue(v3, self.split_vector(out))
        if name == "lerp" and len(args) == 3:
            a, b, t = args
            if types[0] == v3 and types[1] == v3 and types[2] in (v3, ("f", ())):
                uniform = types[2] == ("f", ())
                factor = t.c[0] if uniform else self.vector_of(t.c)
                out = self.native_node(
                    "ShaderNodeMix",
                    "lerp",
                    {"data_type": "VECTOR", "factor_mode": "UNIFORM" if uniform else "NON_UNIFORM", "clamp_factor": False},
                    [("Factor_Float" if uniform else "Factor_Vector", factor), ("A_Vector", self.vector_of(a.c)), ("B_Vector", self.vector_of(b.c))],
                    "Result_Vector",
                )
                return FxValue(v3, self.split_vector(out))

            def mix(x, y, s):
                folded = self.fold(lambda p, q, r: p + (q - p) * r, x, y, s)
                if folded is not None:
                    return folded
                return self.native_node("ShaderNodeMix", "lerp", {"data_type": "FLOAT", "clamp_factor": False}, [("Factor_Float", s), ("A_Float", x), ("B_Float", y)], "Result_Float")

            return ex.elementwise(mix, FxValue(("f", a.t[1]), a.c), b, t)
        if name == "smoothstep" and len(args) == 3:

            def step(lo, hi, x):
                if self.known(lo) is not None and self.known(hi) is not None and self.known(x) is not None:
                    return self.np_smoothstep(lo, hi, x)
                return self.native_node(
                    "ShaderNodeMapRange",
                    "smoothstep",
                    {"data_type": "FLOAT", "interpolation_type": "SMOOTHSTEP", "clamp": True},
                    [("From Min", lo), ("From Max", hi), ("Value", x), ("To Min", 0.0), ("To Max", 1.0)],
                    "Result",
                )

            return ex.elementwise(step, FxValue(("f", args[0].t[1]), args[0].c), args[1], args[2])
        if name in ("saturate", "clamp"):
            lo_hi = [0.0, 1.0] if name == "saturate" else None

            def clamp(x, lo, hi):
                folded = self.fold(lambda p, q, r: min(max(p, q), r), x, lo, hi)
                if folded is not None:
                    return folded
                return self.native_node("ShaderNodeClamp", name, {"clamp_type": "MINMAX"}, [("Value", x), ("Min", lo), ("Max", hi)], "Result")

            if lo_hi is not None:
                a = args[0]
                return FxValue(("f", a.t[1]), [clamp(c, 0.0, 1.0) for c in a.c])
            if len(args) != 3:
                return None
            return ex.elementwise(clamp, args[0], args[1], args[2])
        return None

    def np_smoothstep(self, lo, hi, x):
        t = min(max((float(x) - float(lo)) / (float(hi) - float(lo)), 0.0), 1.0) if float(hi) != float(lo) else 0.0
        return t * t * (3.0 - 2.0 * t)

    def intrinsic_group(self, ex, name, args):
        if name not in FX_GROUP_INTRINSICS or not args:
            return None
        if not all(fx_is_numeric(a.t) for a in args):
            return None
        if any(all(self.known(c) is not None for c in a.c) for a in args):
            return None
        if name == "mul" and not any(len(a.t[1]) == 2 for a in args):
            return None
        if name in ("dot", "length", "normalize", "distance") and max(fx_count(a.t[1]) for a in args) < 3:
            return None
        types = [("f", a.t[1]) for a in args]
        values = [FxValue(t, list(a.c)) for t, a in zip(types, args)]
        group = self.intrinsic_group_tree(ex, name, types)
        names = ["abc"[i] if i < 3 else "arg%d" % i for i in range(len(args))]
        return self.place_group(group, names, values, "result", name, (), ex.env)
