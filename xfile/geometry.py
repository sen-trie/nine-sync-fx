import numpy as np

from .model import XMaterial


# Merges all meshes of the file into one list with offset face and material indices, so the importer and
# effects see a single mesh.
# UV and colour arrays are padded to one entry per vertex.
def merge_x_meshes(xscene):
    vertices = []
    faces = []
    face_materials = []
    uvs = []
    colors = []
    materials = []
    has_colors = False
    for mesh in xscene.meshes:
        vertex_offset = len(vertices)
        material_offset = len(materials)
        vertices.extend(mesh.vertices)
        for k in range(len(mesh.vertices)):
            uvs.append(mesh.uvs[k] if k < len(mesh.uvs) else (0.0, 0.0))
            colors.append(mesh.colors.get(k, (1.0, 1.0, 1.0, 1.0)))
        has_colors = has_colors or bool(mesh.colors)
        local_materials = list(mesh.materials) or [
            XMaterial(
                "Material",
                (0.8, 0.8, 0.8, 1.0),
                0.0,
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                "",
            )
        ]
        materials.extend(local_materials)
        for index, face in enumerate(mesh.faces):
            faces.append(tuple(v + vertex_offset for v in face))
            local = (
                mesh.face_materials[index] if index < len(mesh.face_materials) else 0
            )
            face_materials.append(
                material_offset + min(max(local, 0), len(local_materials) - 1)
            )
    return (
        vertices,
        faces,
        face_materials,
        uvs,
        colors if has_colors else None,
        materials,
    )


# Geometry for effect passes: numpy arrays plus a normal per vertex. Vertices without a file normal get an
# area-weighted average of their faces' normals.
def x_effect_geometry(xscene):
    vertices, faces, face_materials, uvs, _colors, materials = merge_x_meshes(xscene)
    normals = []
    for mesh in xscene.meshes:
        for k in range(len(mesh.vertices)):
            normals.append(mesh.normals.get(k))
    positions = np.array(vertices, dtype=np.float64).reshape(-1, 3)
    uv_array = np.array(uvs, dtype=np.float64).reshape(-1, 2)
    normal_array = np.zeros_like(positions)
    missing = np.ones(len(positions), dtype=bool)
    for index, value in enumerate(normals):
        if value is not None:
            normal_array[index] = value
            missing[index] = False
    if missing.any() and faces:
        accum = np.zeros_like(positions)
        for face in faces:
            if len(face) < 3:
                continue
            a, b, c = positions[face[0]], positions[face[1]], positions[face[2]]
            n = np.cross(b - a, c - a)
            for vertex in face:
                accum[vertex] += n
        lengths = np.linalg.norm(accum, axis=1, keepdims=True)
        accum = np.where(lengths > 1e-12, accum / np.maximum(lengths, 1e-12), 0.0)
        normal_array[missing] = accum[missing]
    return positions, normal_array, uv_array, faces, np.array(face_materials, dtype=np.int64), materials
