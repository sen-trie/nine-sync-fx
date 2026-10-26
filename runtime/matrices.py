import numpy as np

from ..common.constants import MME_SWAP


# Blender matrix to numpy.
def mme_np_matrix(matrix):
    return np.array([list(row) for row in matrix], dtype=np.float64)


# Blender world matrix to MMD's row-vector world matrix: unit scale and the Y/Z swap.
def mme_world_row(blender_world, unit):
    inv_unit = np.diag([1.0 / unit, 1.0 / unit, 1.0 / unit, 1.0])
    col = inv_unit @ MME_SWAP @ blender_world @ MME_SWAP
    return col.T
