from .model import (
    XError,
    XMaterial,
    XMesh,
)
from .parser import UNSUPPORTED_BLOCKS


# Row-vector convention (as in DirectX): the child's local matrix goes on the left of the parent's.
def x_mat_mul(a, b):
    return [
        sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4))
        for r in range(4)
        for c in range(4)
    ]


X_IDENTITY = [
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
]


# Collects named top-level Materials so a mesh can refer to them by { name } reference.
def x_collect_materials(root):
    found = {}

    def walk(block):
        for child in block.children:
            if child.kind == "Material" and child.name:
                found[child.name] = child
            walk(child)

    walk(root)
    return found


# Material layout: 4 diffuse, power, 3 specular, 3 emissive, plus an optional TextureFilename child.
def x_read_material(block, name):
    d = block.data
    if len(d) < 11:
        raise XError("short Material block")
    texture = ""
    for child in block.children:
        if child.kind == "TextureFilename" and child.data:
            texture = str(child.data[0])
    return XMaterial(
        name or "Material",
        (d[0], d[1], d[2], d[3]),
        d[4],
        (d[5], d[6], d[7]),
        (d[8], d[9], d[10]),
        texture,
    )


# Reads one Mesh block: positions (with the frame matrix applied), faces, UVs, material list, per-corner
# normals and vertex colours.
# A material list with a single index applies to every face; missing materials are padded with a grey default.
def x_read_mesh(block, matrix, named_materials):
    d = block.data
    mesh = XMesh()
    pos = 0
    count = int(d[pos])
    pos += 1
    raw = d[pos : pos + count * 3]
    pos += count * 3
    for k in range(count):
        x, y, z = raw[k * 3 : k * 3 + 3]
        m = matrix
        mesh.vertices.append(
            (
                x * m[0] + y * m[4] + z * m[8] + m[12],
                x * m[1] + y * m[5] + z * m[9] + m[13],
                x * m[2] + y * m[6] + z * m[10] + m[14],
            )
        )
    face_count = int(d[pos])
    pos += 1
    for _ in range(face_count):
        n = int(d[pos])
        pos += 1
        mesh.faces.append(tuple(int(v) for v in d[pos : pos + n]))
        pos += n
    for child in block.children:
        cd = child.data
        if child.kind == "MeshTextureCoords" and cd:
            n = int(cd[0])
            mesh.uvs = [(cd[1 + k * 2], cd[2 + k * 2]) for k in range(n)]
        elif child.kind == "MeshMaterialList" and len(cd) >= 2:
            n_mat = int(cd[0])
            n_idx = int(cd[1])
            indexes = [int(v) for v in cd[2 : 2 + n_idx]]
            if n_idx == 1 and face_count > 1:
                indexes = indexes * face_count
            mesh.face_materials = indexes
            for sub in child.children:
                if sub.kind == "Material":
                    mesh.materials.append(x_read_material(sub, sub.name))
                elif sub.kind == "__ref__":
                    target = named_materials.get(sub.name)
                    if target is not None:
                        mesh.materials.append(x_read_material(target, target.name))
            if n_mat and len(mesh.materials) < n_mat:
                while len(mesh.materials) < n_mat:
                    mesh.materials.append(
                        XMaterial(
                            "Material",
                            (0.8, 0.8, 0.8, 1.0),
                            0.0,
                            (0.0, 0.0, 0.0),
                            (0.0, 0.0, 0.0),
                            "",
                        )
                    )
        elif child.kind == "MeshNormals" and cd:
            count = int(cd[0])
            m = matrix
            listed = []
            for k in range(count):
                nx, ny, nz = cd[1 + k * 3 : 4 + k * 3]
                listed.append(
                    (
                        nx * m[0] + ny * m[4] + nz * m[8],
                        nx * m[1] + ny * m[5] + nz * m[9],
                        nx * m[2] + ny * m[6] + nz * m[10],
                    )
                )
            pos = 1 + count * 3
            if pos < len(cd):
                face_count_n = int(cd[pos])
                pos += 1
                for index in range(face_count_n):
                    if pos >= len(cd):
                        break
                    n = int(cd[pos])
                    pos += 1
                    corners = [int(v) for v in cd[pos : pos + n]]
                    pos += n
                    if index < len(mesh.faces):
                        for vertex, normal in zip(mesh.faces[index], corners):
                            if 0 <= normal < len(listed):
                                mesh.normals[vertex] = listed[normal]
        elif child.kind == "MeshVertexColors" and cd:
            n = int(cd[0])
            for k in range(n):
                base = 1 + k * 5
                mesh.colors[int(cd[base])] = (
                    cd[base + 1],
                    cd[base + 2],
                    cd[base + 3],
                    cd[base + 4],
                )
    return mesh


# Depth-first walk that multiplies Frame transforms down to each Mesh so meshes come out in accessory space.
def x_walk(block, matrix, scene, named_materials):
    for child in block.children:
        if child.kind == "Frame":
            local = list(X_IDENTITY)
            for sub in child.children:
                if sub.kind == "FrameTransformMatrix" and len(sub.data) >= 16:
                    local = [float(v) for v in sub.data[:16]]
            x_walk(child, x_mat_mul(local, matrix), scene, named_materials)
        elif child.kind == "Mesh":
            scene.meshes.append(x_read_mesh(child, matrix, named_materials))
            for sub in child.children:
                if sub.kind in UNSUPPORTED_BLOCKS:
                    note = (
                        "%s ignored (skinned/animated .x is not supported)" % sub.kind
                    )
                    if note not in scene.warnings:
                        scene.warnings.append(note)
        elif child.kind in UNSUPPORTED_BLOCKS:
            note = "%s ignored (skinned/animated .x is not supported)" % child.kind
            if note not in scene.warnings:
                scene.warnings.append(note)
