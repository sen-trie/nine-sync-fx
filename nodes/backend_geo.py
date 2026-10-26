import os

from .backend_base import MmeTreeBackend
from .socket import MmeSock


# Geometry-node backend for vertex shaders. Adds matrix inputs, bit operations and image textures that work on
# geometry.
class MmeGeoBackend(MmeTreeBackend):
    separate_color = "FunctionNodeSeparateColor"
    combine_color = "FunctionNodeCombineColor"
    tree_tag = "GN"
    group_idname = "GeometryNodeGroup"
    image_inputs = True

    def image_slot(self, slot, interpolation, extension, u, v):
        key = ("imgslot", slot, interpolation, extension, self.key(u), self.key(v))
        found = self.cache.get(key)
        if found is not None:
            return found
        source = self.new_input("tex:" + slot, "NodeSocketImage", "tex:" + slot)
        flipped = self.sub(1.0, v)
        combine = self.new("ShaderNodeCombineXYZ")
        self.feed(combine.inputs[0], u)
        self.feed(combine.inputs[1], flipped)
        combine.inputs[2].default_value = 0.0
        texture = self.new("GeometryNodeImageTexture")
        texture.interpolation = interpolation
        texture.extension = extension
        texture.label = slot
        self.links.new(source.socket, texture.inputs["Image"])
        self.links.new(combine.outputs[0], texture.inputs["Vector"])
        separate = self.new(self.separate_color)
        self.links.new(texture.outputs["Color"], separate.inputs[0])
        result = [MmeSock(separate.outputs[k]) for k in range(3)]
        result.append(MmeSock(texture.outputs["Alpha"]))
        self.cache[key] = result
        return result

    def image_sample(self, image, interpolation, extension, u, v):
        key = ("img", image.name, interpolation, extension, self.key(u), self.key(v))
        found = self.cache.get(key)
        if found is not None:
            return found
        flipped = self.sub(1.0, v)
        combine = self.new("ShaderNodeCombineXYZ")
        self.feed(combine.inputs[0], u)
        self.feed(combine.inputs[1], flipped)
        combine.inputs[2].default_value = 0.0
        texture = self.new("GeometryNodeImageTexture")
        texture.inputs["Image"].default_value = image
        texture.interpolation = interpolation
        texture.extension = extension
        texture.label = os.path.basename(image.filepath or image.name)
        self.links.new(combine.outputs[0], texture.inputs["Vector"])
        separate = self.new(self.separate_color)
        self.links.new(texture.outputs["Color"], separate.inputs[0])
        result = [MmeSock(separate.outputs[k]) for k in range(3)]
        result.append(MmeSock(texture.outputs["Alpha"]))
        self.cache[key] = result
        self.keep.extend(result)
        return result

    def bit_math(self, op, a, b=None):
        key = ("bit", op, self.key(a), self.key(b) if b is not None else None)
        found = self.cache.get(key)
        if found is not None:
            return found
        node = self.new("FunctionNodeBitMath")
        node.operation = op
        self.feed(node.inputs["A"], a)
        if b is not None:
            self.feed(node.inputs["Shift" if op in ("SHIFT", "ROTATE") else "B"], b)
        return self.remember(key, node.outputs[0])

    def bitop(self, op, a, b):
        folded = self.fold(lambda x, y: self.np.bitop(op, x, y), a, b)
        if folded is not None:
            return folded
        if op == "&":
            return self.bit_math("AND", a, b)
        if op == "|":
            return self.bit_math("OR", a, b)
        if op == "^":
            return self.bit_math("XOR", a, b)
        if op == "<<":
            return self.bit_math("SHIFT", a, b)
        return self.bit_math("SHIFT", a, self.neg(b))

    def bitnot(self, a):
        folded = self.fold(self.np.bitnot, a)
        if folded is not None:
            return folded
        return self.bit_math("NOT", a)

    # A group input of matrix type, split into its 16 scalars for the interpreter.
    def matrix_input(self, key, label):
        found = self.cache.get(("matrix", key))
        if found is not None:
            return found
        source = self.new_input(key, "NodeSocketMatrix", label)
        separate = self.new("FunctionNodeSeparateMatrix")
        separate.label = label
        self.links.new(source.socket, separate.inputs[0])
        comps = [MmeSock(separate.outputs[k]) for k in range(16)]
        self.cache[("matrix", key)] = comps
        return comps
