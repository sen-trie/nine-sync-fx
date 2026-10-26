import os

from ..fx.errors import FxUnsupported
from .backend_base import MmeTreeBackend
from .socket import MmeSock


# Shader-node (material) backend. Texture reads become Image Texture nodes; image slots inside shared
# functions are not supported.
class MmePsBackend(MmeTreeBackend):
    separate_color = "ShaderNodeSeparateColor"
    tree_tag = "SH"
    group_idname = "ShaderNodeGroup"
    image_inputs = False

    def image_slot(self, slot, interpolation, extension, u, v):
        raise FxUnsupported("texture inside a shared shader function")

    # Vertex-shader outputs and uniforms reach the material through attributes; the kind (geometry or object)
    # is remembered to wire them up later.
    def attribute(self, name, kind="GEOMETRY"):
        found = self.inputs.get(name)
        if found is None:
            found = self.new_input(name)
            self.kinds[name] = kind
        return found

    def __init__(self, tree):
        super().__init__(tree)
        self.kinds = {}

    # Image Texture sample with v flipped (HLSL texture space starts at the top, Blender's at the bottom);
    # results are cached per image and coordinate.
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
        texture = self.new("ShaderNodeTexImage")
        texture.image = image
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
