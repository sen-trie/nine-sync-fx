"""Read existing MMD Tools material-morph bindings without modifying their owners."""

import json

from ...common.digest import digest
from ...fx.errors import FxUnsupported
from .controllers import DriverSource

MORPH_SOURCE = "ninesync_middle_morph_source"
MORPH_OBJECT = "ninesync_middle_morph_object"
MORPH_SCHEMA = "ninesync_middle_morph_schema"


def model_root(obj):
    while obj is not None and getattr(obj, "mmd_type", None) != "ROOT":
        obj = obj.parent
    return obj


def bindings(obj, material):
    root = model_root(obj)
    if root is None or material is None or not material.node_tree:
        return ()
    found = []
    for morph in root.mmd_root.material_morphs:
        for index, offset in enumerate(morph.data):
            if offset.material and offset.material != material.name:
                continue
            if not offset.name:
                continue  # An unbound MMD Tools morph is inactive in its original material too.
            node = material.node_tree.nodes.get(offset.name)
            kind = "MMDMorphAdd" if offset.offset_type == "ADD" else "MMDMorphMul"
            if not node or node.type != "GROUP" or not node.node_tree or node.node_tree.name != kind:
                raise FxUnsupported(f"Material morph {morph.name!r} has a missing or changed MMD Tools binding.")
            if "Fac" not in node.inputs or node.inputs["Fac"].is_linked:
                raise FxUnsupported(f"Material morph {morph.name!r} needs its native MMD Tools weight driver.")
            # Match MMD Tools' native material ordering: all-material multiply,
            # per-material multiply, all-material add, per-material add.
            order = (offset.offset_type == "ADD", bool(offset.material))
            found.append((order, root, morph, index, offset, node))
    return tuple(sorted(found, key=lambda item: item[0]))


def signature(obj, material):
    return digest([(root, morph.name, index, offset.offset_type, offset.material,
                    node.name, node.node_tree,
                    [(keys, key.path_from_id("value"), factors)
                     for keys, key, factors in contributions(root, morph.name)])
                   for _order, root, morph, index, offset, node in bindings(obj, material)])


def contributions(root, name):
    sliders = [obj for obj in root.children if obj.type == "MESH" and getattr(obj, "mmd_type", "") == "PLACEHOLDER"
               and obj.data.shape_keys]
    if len(sliders) != 1:
        raise FxUnsupported("Material morphs require one existing MMD Tools morph-slider owner.")
    keys = sliders[0].data.shape_keys
    if name not in keys.key_blocks:
        raise FxUnsupported(f"Material morph {name!r} has no existing MMD Tools slider.")
    result = [(keys, keys.key_blocks[name], ())]
    groups = root.mmd_root.group_morphs

    def walk(group, factors, active):
        if group.name in active:
            raise FxUnsupported("Cyclic MMD group morphs require a valid source morph graph.")
        active = active | {group.name}
        for index, item in enumerate(group.data):
            # Indices distinguish repeated entries pointing at the same morph.
            path = factors + (f"{group.path_from_id()}.data[{index}].factor",)
            if item.morph_type == "material_morphs" and item.name == name:
                yield path
            elif item.morph_type == "group_morphs":
                child = groups.get(item.name)
                if child is None:
                    raise FxUnsupported(f"Group morph {group.name!r} references a missing group.")
                yield from walk(child, path, active)

    for group in groups:
        paths = tuple(walk(group, (), set()))
        if paths and group.name not in keys.key_blocks:
            raise FxUnsupported(f"Group morph {group.name!r} has no existing MMD Tools slider.")
        result.extend((keys, keys.key_blocks[group.name], path) for path in paths)
    return tuple(result)


class MaterialMorphs:
    def __init__(self, env):
        self.env = env
        self.rows = bindings(env.obj, env.material)
        self.weights = {}
        self.sources = {}
        self.used = False

    def apply(self, semantic, values):
        # MME's AMBIENT is the PMX diffuse RGB; EMISSIVE is PMX ambient RGB.
        fields = {
            # Native MMD keeps DIFFUSE RGB unchanged; its alpha still morphs.
            "DIFFUSE": [(None, None)]*3 + [("diffuse_color", 3)],
            "AMBIENT": [("diffuse_color", i) for i in range(3)],
            "EMISSIVE": [("ambient_color", i) for i in range(3)],
            "SPECULAR": [("specular_color", i) for i in range(3)],
            "SPECULARPOWER": [("shininess", None)],
            "EDGECOLOR": [("edge_color", i) for i in range(4)],
            "TOONCOLOR": [("toon_texture_factor", i) for i in range(4)],
            "ADDINGTEXTURE": [("texture_factor", i) for i in range(4)],
            "MULTIPLYINGTEXTURE": [("texture_factor", i) for i in range(4)],
            "ADDINGSPHERETEXTURE": [("sphere_texture_factor", i) for i in range(4)],
            "MULTIPLYINGSPHERETEXTURE": [("sphere_texture_factor", i) for i in range(4)],
        }.get(semantic)
        if not fields or not self.rows:
            return values
        self.used = True
        B, env = self.env.backend, self.env
        for _order, root, morph, index, offset, node in self.rows:
            # Native texture coefficient semantics expose separate accumulators.
            # Applying ADD offsets to a multiply accumulator would double-count
            # them when the original shader evaluates sample*mul+add.
            if semantic.startswith(("ADDING", "MULTIPLYING")):
                if (offset.offset_type == "ADD") != semantic.startswith("ADDING"):
                    continue
            if morph.name not in self.weights:
                terms = []
                for keys, key, factors in contributions(root, morph.name):
                    value = self.source(keys, key.path_from_id("value"), None)
                    enabled = B.sub(1.0, self.source(keys, keys.reference_key.path_from_id("mute"), None))
                    value = B.mul(value, enabled)
                    for factor_path in factors:
                        value = B.mul(value, self.source(root, factor_path, None))
                    terms.append(value)
                self.weights[morph.name] = terms
            path = offset.path_from_id()
            result = list(values)
            for component, (field, channel) in enumerate(fields[:len(values)]):
                if field is None:
                    continue
                source = self.source(root, path + "." + field, channel)
                # MMD visits each direct/group contribution separately. Combining
                # weights before a multiply loses their cross term.
                for weight in self.weights[morph.name]:
                    term = B.mul(weight, source) if offset.offset_type == "ADD" else B.add(1.0, B.mul(weight, B.sub(source, 1.0)))
                    result[component] = B.add(result[component], term) if offset.offset_type == "ADD" else B.mul(result[component], term)
            values = result
        return values

    def source(self, owner, path, index):
        key = owner, path, index
        if key not in self.sources:
            self.sources[key] = self.env.driven("Material morph " + path,
                                                DriverSource(owner, path, index=index))
        return self.sources[key]

    def finish(self):
        if not self.used:
            return
        tree = self.env.backend.tree
        tree[MORPH_SOURCE] = self.env.material
        tree[MORPH_OBJECT] = self.env.obj
        tree[MORPH_SCHEMA] = signature(self.env.obj, self.env.material)
        tree["ninesync_middle_material_morphs"] = json.dumps([morph.name for _o, _r, morph, *_ in self.rows])


def diagnostics(tree):
    if MORPH_SCHEMA not in tree:
        return ()
    obj, material = tree.get(MORPH_OBJECT), tree.get(MORPH_SOURCE)
    if obj is None or material is None:
        return ("The source model/material for material morphs is missing.",)
    try:
        current = signature(obj, material)
    except FxUnsupported as error:
        return (str(error),)
    if current != tree[MORPH_SCHEMA]:
        return ("MMD Tools material-morph bindings changed; reapply the FX to rebuild its inputs.",)
    return ()
