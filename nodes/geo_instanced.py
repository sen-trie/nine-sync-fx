import bpy
import numpy as np

from ..common.constants import (
    MME_ATTR_AFFINE,
    MME_ATTR_CULL,
    MME_ATTR_INST,
    MME_ATTR_NRM,
    MME_ATTR_POINT,
    MME_ATTR_POS,
    MME_ATTR_SRC,
    MME_ATTR_UV,
    MME_LOOP_PREFIX,
)
from .geo_groups import mme_geo_clip_group
from .material_instanced import MME_AFFINE_FEATURES
from .names import mme_affine_name
from .socket import MmeSock

MME_AFFINE_SIZES = {"p": 3, "n": 3, "uv": 2}
MME_ATTR_WEIGHT = "mme_w"
MME_ATTR_COLUMN = "mme_col"


def mme_index_switch(S, index, values, data_type, label):
    node = S.node("GeometryNodeIndexSwitch", label)
    node.data_type = data_type
    while len(node.index_switch_items) < len(values):
        node.index_switch_items.new()
    S.link(index, node.inputs["Index"])
    for k, value in enumerate(values):
        node.inputs[k + 1].default_value = value
    return node.outputs[0]


def mme_store(S, geometry, name, value, data_type, domain):
    store = S.node("GeometryNodeStoreNamedAttribute", name, data_type=data_type, domain=domain)
    store.inputs["Name"].default_value = name
    S.link(geometry, store.inputs["Geometry"])
    if isinstance(value, (bpy.types.NodeSocket, MmeSock)):
        S.link(value, store.inputs["Value"])
    else:
        S.B.feed(store.inputs["Value"], value)
    return store.outputs[0]


def mme_remove(S, geometry, name, wildcard=False):
    remove = S.node("GeometryNodeRemoveAttribute", "drop " + name)
    if wildcard:
        remove.inputs["Pattern Mode"].default_value = "Wildcard"
    remove.inputs["Name"].default_value = name
    S.link(geometry, remove.inputs["Geometry"])
    return remove.outputs[0]


def mme_reader(S, name, data_type):
    node = S.node("GeometryNodeInputNamedAttribute", name, data_type=data_type)
    node.inputs["Name"].default_value = name
    return node.outputs["Attribute"]


# The instanced output of a LoopByCount pass whose vertex shader the build proved affine per particle (see
# runtime/affine.py). The shader runs only on plan.refs, a few source vertices that form one n-gon per particle
# point. Each reference vertex carries fixed weights (times the vertex count); its shader results times those
# weights, averaged over the face, are the instance transform and the coefficients the instanced material
# rebuilds each varying from (instance attributes named by mme_affine_name). Mesh to Points on the faces then
# gives one point per particle that already carries them all, so there is no per-particle lookup. A particle
# with a culled reference vertex is dropped. 'wire' makes a vertex shader node with the pass inputs.
def mme_instanced_branch(S, source, points, plan, wire, varyings, loop_attrs, unit, material):
    T = S.B
    refs = [int(r) for r in plan.refs]
    count = len(refs)
    T.statement("Instanced particles: the shader on %d reference vertices per particle, one face each" % count)
    ring = S.node("GeometryNodeMeshCircle", "one face through the reference vertices", fill_type="NGON")
    ring.inputs["Vertices"].default_value = count
    index = S.node("GeometryNodeInputIndex")
    pick = mme_index_switch(S, index.outputs[0], refs, "INT", "source vertex of each reference")
    geometry = ring.outputs[0]
    for name, data_type in ((MME_ATTR_POS, "FLOAT_VECTOR"), (MME_ATTR_NRM, "FLOAT_VECTOR"), (MME_ATTR_UV, "FLOAT_VECTOR"), (MME_ATTR_SRC, "INT")):
        sample = S.node("GeometryNodeSampleIndex", name, data_type=data_type, domain="POINT")
        S.link(source, sample.inputs["Geometry"])
        S.link(mme_reader(S, name, data_type), sample.inputs["Value"])
        S.link(pick, sample.inputs["Index"])
        geometry = mme_store(S, geometry, name, sample.outputs["Value"], data_type, "POINT")
    made = []

    def weight(geometry, values, data_type):
        name = "%s%d" % (MME_ATTR_WEIGHT, len(made))
        made.append(name)
        switch = mme_index_switch(S, index.outputs[0], values, data_type, "weights " + name)
        return mme_store(S, geometry, name, switch, "FLOAT_VECTOR" if data_type == "VECTOR" else "FLOAT", "POINT"), name

    jobs = []
    for name in varyings:
        group = plan.channels[name]
        matrix = np.asarray(plan.weights[group], dtype=np.float64) * count
        row = 0
        for block in group:
            size = MME_AFFINE_SIZES[block]
            rows = [matrix[row + k] if k < size else np.zeros(count) for k in range(3)]
            row += size
            geometry, attr = weight(geometry, [(float(rows[0][j]), float(rows[1][j]), float(rows[2][j])) for j in range(count)], "VECTOR")
            jobs.append((mme_affine_name(name, block), name, attr, True))
        geometry, attr = weight(geometry, [float(matrix[row][j]) for j in range(count)], "FLOAT")
        jobs.append((mme_affine_name(name, "k"), name, attr, False))
    columns = []
    for k in range(4):
        geometry, attr = weight(geometry, [float(plan.position[k][j]) * count for j in range(count)], "FLOAT")
        columns.append(attr)
    spread = S.node("GeometryNodeInstanceOnPoints", "reference face on every particle")
    S.link(points, spread.inputs["Points"])
    S.link(geometry, spread.inputs["Instance"])
    realize = S.node("GeometryNodeRealizeInstances")
    S.link(spread.outputs[0], realize.inputs[0])
    vs = wire()
    clip = S.group_node(
        mme_geo_clip_group(),
        "Clip space to object space",
        {"Clip X": vs.outputs["Clip x"], "Clip Y": vs.outputs["Clip y"], "Clip Z": vs.outputs["Clip z"], "Clip W": vs.outputs["Clip w"], "Unit": unit},
    )
    T.statement("Shader results times weights at the reference vertices, evaluated once")
    capture = S.node("GeometryNodeCaptureAttribute", "shader results x weights", domain="POINT")
    S.link(realize.outputs[0], capture.inputs[0])
    products = []

    def captured(target, socket, vector):
        item = capture.capture_items.new("VECTOR" if vector else "FLOAT", target)
        S.link(socket, capture.inputs[item.name])
        products.append((target, capture.outputs[item.name], "FLOAT_VECTOR" if vector else "FLOAT"))

    for target, name, attr, vector in jobs:
        if vector:
            node = S.node("ShaderNodeVectorMath", target, operation="SCALE")
            S.link(mme_reader(S, attr, "FLOAT_VECTOR"), node.inputs[0])
            S.link(vs.outputs[name], node.inputs["Scale"])
            captured(target, node.outputs["Vector"], True)
        else:
            node = S.node("ShaderNodeMath", target, operation="MULTIPLY")
            S.link(mme_reader(S, attr, "FLOAT"), node.inputs[0])
            S.link(vs.outputs[name], node.inputs[1])
            captured(target, node.outputs[0], False)
    for k, attr in enumerate(columns):
        node = S.node("ShaderNodeVectorMath", "transform column %d" % (k + 1), operation="SCALE")
        S.link(clip.outputs["Position"], node.inputs[0])
        S.link(mme_reader(S, attr, "FLOAT"), node.inputs["Scale"])
        captured("%s%d" % (MME_ATTR_COLUMN, k), node.outputs["Vector"], True)
    culled = S.node("ShaderNodeMath", "culled as 0/1", operation="ADD")
    S.link(clip.outputs["Culled"], culled.inputs[0])
    culled.inputs[1].default_value = 0.0
    captured(MME_ATTR_CULL, culled.outputs[0], False)
    geometry = capture.outputs[0]
    T.statement("Per particle: the face average of the weighted results")
    for target, socket, data_type in products:
        geometry = mme_store(S, geometry, target, socket, data_type, "FACE")
    for name in loop_attrs:
        geometry = mme_store(S, geometry, name, S.cache.get(("loopvar", name[len(MME_LOOP_PREFIX):]), 0.0), "FLOAT", "FACE")
    for name in (MME_ATTR_POS, MME_ATTR_NRM, MME_ATTR_UV, MME_ATTR_SRC, MME_ATTR_INST, MME_ATTR_POINT):
        geometry = mme_remove(S, geometry, name)
    geometry = mme_remove(S, geometry, MME_ATTR_WEIGHT + "*", True)
    centres = S.node("GeometryNodeMeshToPoints", "one point per particle", mode="FACES")
    S.link(geometry, centres.inputs["Mesh"])
    T.statement("Instances with the source mesh and the instanced material")
    mesh = S.node("GeometryNodeSetMaterial", "instanced material")
    S.link(source, mesh.inputs["Geometry"])
    mesh.inputs["Material"].default_value = material
    instances = S.node("GeometryNodeInstanceOnPoints")
    S.link(centres.outputs[0], instances.inputs["Points"])
    S.link(mesh.outputs[0], instances.inputs["Instance"])
    geometry = instances.outputs[0]
    T.statement("Instance transform from the averaged columns, at the particle point")
    cols = [mme_reader(S, "%s%d" % (MME_ATTR_COLUMN, k), "FLOAT_VECTOR") for k in range(4)]
    offset = S.vec_node("ADD", cols[3], S.node("GeometryNodeInputPosition").outputs[0], "plus the particle point")
    comps = []
    for k, column in enumerate(cols[:3] + [offset]):
        comps += list(S.split(column)) + [1.0 if k == 3 else 0.0]
    transform = S.node("GeometryNodeSetInstanceTransform")
    S.link(geometry, transform.inputs["Instances"])
    S.link(S.combine_matrix(comps, "instance transform"), transform.inputs["Transform"])
    geometry = transform.outputs[0]
    T.statement("Drop particles with a culled reference vertex")
    drop = S.node("GeometryNodeDeleteGeometry", "culled particles", domain="INSTANCE", mode="ALL")
    S.link(geometry, drop.inputs["Geometry"])
    T.feed(drop.inputs["Selection"], T.cmp(">", MmeSock(mme_reader(S, MME_ATTR_CULL, "FLOAT")), 0.0))
    geometry = mme_remove(S, drop.outputs[0], MME_ATTR_CULL)
    return mme_remove(S, geometry, MME_ATTR_COLUMN + "*", True)


# The MMD-order output of an instanced pass: its instances realized back into one mesh. The particles and their
# faces keep the order of the full-shader path, and EEVEE draws a mesh's faces in order as MMD does, so alpha
# blending matches; instances of a blended material are sorted by distance instead. Each varying is rebuilt per
# vertex from its particle's coefficients, so the shader still runs only on the reference vertices, and the mesh
# gets the pass material back.
def mme_ordered_branch(S, instances, plan, varyings, material):
    T = S.B
    T.statement("MMD order: the particles as one mesh in draw order, shader outputs rebuilt per vertex")
    realize = S.node("GeometryNodeRealizeInstances", "particles in draw order")
    S.link(instances, realize.inputs[0])
    geometry = realize.outputs[0]
    for name in varyings:
        value = mme_reader(S, mme_affine_name(name, "k"), "FLOAT")
        for block in plan.channels[name]:
            dot = S.vec_node("DOT_PRODUCT", mme_reader(S, mme_affine_name(name, block), "FLOAT_VECTOR"), mme_reader(S, MME_AFFINE_FEATURES[block], "FLOAT_VECTOR"))
            add = S.node("ShaderNodeMath", "%s x %s" % (name, block), operation="ADD")
            S.link(value, add.inputs[0])
            S.link(dot, add.inputs[1])
            value = add.outputs[0]
        geometry = mme_store(S, geometry, name, value, "FLOAT", "POINT")
    geometry = mme_remove(S, geometry, MME_ATTR_AFFINE + "*", True)
    mesh = S.node("GeometryNodeSetMaterial", "pass material")
    S.link(geometry, mesh.inputs["Geometry"])
    mesh.inputs["Material"].default_value = material
    return mesh.outputs[0]
