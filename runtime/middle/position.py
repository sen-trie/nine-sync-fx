"""Recover object-space positions from a proven final camera projection."""

from ...fx.errors import FxUnsupported
from ...fx.interp.executor import FxExec
from ...fx.interp.intrinsics import fx_intrinsic_values
from ...fx.types import FxValue, fx_is_numeric
from ...nodes.socket import MmeSock


class PositionExec(FxExec):
    def __init__(self, program, backend, env):
        super().__init__(program, backend, env)
        self.projected = {}

    def call_node(self, node):
        if node[1] != "mul" or "mul" in self.program.functions:
            return super().call_node(node)
        values = [self.eval(argument) for argument in node[2]]
        result = fx_intrinsic_values(self, "mul", values)
        if len(values) == 2:
            vector, matrix = values
            if (fx_is_numeric(vector.t) and vector.t[1] == (4,) and matrix.t == ("f", (4, 4))
                    and tuple(self.B.key(c) for c in matrix.c) in self.env.projection_roots):
                self.projected[tuple(self.B.key(c) for c in result.c)] = (
                    FxValue(vector.t, list(vector.c)), list(result.c), matrix)
        return result

    def local_position(self, outputs):
        clip = outputs.get("POSITION0")
        if not fx_is_numeric(getattr(clip, "t", None)) or clip.t[1] != (4,):
            raise FxUnsupported("Vertex position recovery requires a float4 POSITION0 output.")
        key = tuple(self.B.key(c) for c in clip.c)
        projection = self.projected.get(key)
        if projection is not None:
            result = projection[0]
        else:
            # XY-only clip edits retain the original depth and homogeneous W,
            # hence also retain clipping and perspective-correct interpolation.
            # Recover their local displacement with the live inverse camera matrix.
            matches = [value for projected, value in self.projected.items() if projected[2:] == key[2:]]
            if len(matches) != 1:
                raise FxUnsupported("Vertex position is not a proven final projection or depth-preserving XY clip edit.")
            original, projected, matrix = matches[0]
            inverse = self.B.new("FunctionNodeInvertMatrix")
            self.B.links.new(self.B.matrix_of(matrix.c).socket, inverse.inputs[0])
            transform = self.B.new("FunctionNodeTransformDirection")
            self.B.links.new(inverse.outputs[0], transform.inputs["Transform"])
            offset = self.B.vector_of([self.B.sub(clip.c[i], projected[i]) for i in range(2)] + [0.0], force=True)
            self.B.links.new(offset.socket, transform.inputs["Direction"])
            delta = self.B.split_vector(MmeSock(transform.outputs[0]))
            result = FxValue(original.t, [self.B.add(a, b) for a, b in zip(original.c[:3], delta)] + [original.c[3]])
            self.env.warnings.add("Depth-preserving XY clip edits use the live inverse camera projection.")
        if self.B.known(result.c[3]) != 1.0:
            raise FxUnsupported("Recovered object-space position requires a constant homogeneous W of 1.")
        return result


def is_passthrough(env, position):
    original = env.attribute("POSITION0")
    return all(env.backend.key(a) == env.backend.key(b) for a, b in zip(position.c[:3], original[:3]))
