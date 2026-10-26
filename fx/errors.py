# Any problem reading or running an effect; callers catch it to fall back to a simpler material or backend.
class FxError(Exception):
    pass


# The effect is valid HLSL but uses something this interpreter cannot express (data-dependent loop on the node
# backend, bit operations, ...).
# The build catches it and switches to another backend.
class FxUnsupported(FxError):
    pass
