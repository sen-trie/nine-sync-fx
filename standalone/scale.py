from ..common.animation import retime_keys


# Rescales every accessory key from the PMM's 30 fps to the scene rate.
def retime_accessories(project, factor):
    if abs(factor - 1.0) < 1e-6:
        return
    for acc in project.accessories:
        acc.keys = retime_keys(acc.keys, factor)
