"""Explicit filtering of packed RGBA8 render-target levels in material nodes."""

from ...nodes.socket import MmeSock
from .images import buffer_mip_atlas


def _texture_footprint(B, u, v, texture_size, viewport):
    """Recover UV changes from height gradients in the screen basis."""
    geometry = B.new("ShaderNodeNewGeometry")
    normal = MmeSock(geometry.outputs["True Normal"])
    window = B.new("ShaderNodeTexCoord")
    screen_x, screen_y, _ = B.split_vector(MmeSock(window.outputs["Window"]))

    def vector(operation, a, b):
        node = B.new("ShaderNodeVectorMath")
        node.operation = operation
        B.feed(node.inputs[0], a)
        B.feed(node.inputs["Scale" if operation == "SCALE" else 1], b)
        return MmeSock(node.outputs["Value" if operation == "DOT_PRODUCT" else "Vector"])

    def dot(a, b):
        return vector("DOT_PRODUCT", a, b)

    def oriented_area(a, b):
        return dot(normal, vector("CROSS_PRODUCT", a, b))

    def gradient(height):
        bump = B.new("ShaderNodeBump")
        bump.label = "Surface derivative"
        bump.inputs["Distance"].default_value = 1.0
        bump.inputs["Filter Width"].default_value = 1.0
        B.feed(bump.inputs["Normal"], normal)
        B.feed(bump.inputs["Height"], height)
        perturbed = MmeSock(bump.outputs["Normal"])
        return vector("SUBTRACT", normal, vector("SCALE", perturbed, B.div(1.0, dot(normal, perturbed))))

    gu, gv, gx, gy = map(gradient, (u, v, screen_x, screen_y))
    det = oriented_area(gx, gy)
    width, height = viewport
    tw, th = texture_size
    dx = [B.mul(size / width, B.div(oriented_area(grad, gy), det)) for grad, size in ((gu, tw), (gv, th))]
    dy = [B.mul(size / height, B.div(oriented_area(gx, grad), det)) for grad, size in ((gu, tw), (gv, th))]
    return dx, dy, B.cmp(">", B.unary("abs", det), 1e-12)


def _atlas_bilinear(B, atlas, rows, level, u, v, extension):
    atlas_width, atlas_height = atlas.size
    base_width, base_height, _ = rows[0]
    scale = B.pow(2.0, level)
    size = [B.max(1.0, B.unary("floor", B.div(dim, scale))) for dim in (base_width, base_height)]
    offset = 0.0
    for index, (_, _, start) in enumerate(rows[1:], 1):
        offset = B.select(B.cmp(">=", level, index), float(start), offset)

    def address(index, length):
        if extension == "EXTEND":
            return B.min(B.max(index, 0.0), B.sub(length, 1.0))
        if extension == "MIRROR":
            return B.sub(B.math("PINGPONG", B.add(index, .5), length), .5)
        return B.math("FLOORED_MODULO", index, length)

    def axis(coord, length, start, extent):
        texel = B.sub(B.mul(coord, length), .5)
        lo = B.unary("floor", texel)
        queries = [B.div(B.add(B.add(start, address(index, length)), .5), extent)
                   for index in (lo, B.add(lo, 1.0))]
        return queries, B.sub(texel, lo)

    x, fx = axis(u, size[0], 0.0, atlas_width)
    y, fy = axis(v, size[1], offset, atlas_height)
    samples = [[B.image_sample(atlas, "Closest", "EXTEND", i, j) for i in x] for j in y]
    top, bottom = [_lerp(B, a, b, fx) for a, b in samples]
    return _lerp(B, top, bottom, fy)


def _lerp(B, a, b, weight):
    return [B.add(lo, B.mul(weight, B.sub(hi, lo))) for lo, hi in zip(a, b)]


def _anisotropic_footprint(B, dx, dy, texture_size, max_anisotropy):
    ux, vx = dx
    uy, vy = dy

    def square(x):
        return B.mul(x, x)

    # The eigenvectors of the texel-space footprint give its sampling direction.
    a, c = B.add(square(ux), square(uy)), B.add(square(vx), square(vy))
    b = B.add(B.mul(ux, vx), B.mul(uy, vy))
    discriminant = B.unary("sqrt", B.add(square(B.sub(a, c)), B.mul(4.0, square(b))))
    major_squared = B.mul(.5, B.add(B.add(a, c), discriminant))
    major = B.unary("sqrt", major_squared)
    area = B.unary("abs", B.sub(B.mul(ux, vy), B.mul(vx, uy)))
    minor = B.max(B.div(area, B.max(major, 1e-12)), B.div(major, max_anisotropy))
    ratio = B.div(major, B.max(minor, 1e-12))
    # Four samples cover up to 4:1. Blend to the engine over the next unit
    # so rotation across that limit does not switch abruptly.
    confidence = B.min(1.0, B.max(0.0, B.sub(5.0, ratio)))
    first_axis = B.cmp(">=", a, c)
    direction = (B.select(first_axis, B.sub(major_squared, c), b),
                 B.select(first_axis, b, B.sub(major_squared, a)))
    length = B.unary("sqrt", B.add(square(direction[0]), square(direction[1])))
    span = B.max(0.0, B.sub(major, minor))
    distance = B.div(span, B.max(length, 1e-12))
    shift = tuple(B.mul(value, B.div(distance, size)) for value, size in zip(direction, texture_size))
    return minor, shift, confidence


def sample_buffer_mips(B, image, viewport, u, v, extension, max_anisotropy):
    atlas, rows = buffer_mip_atlas(image)
    dx, dy, valid = _texture_footprint(B, u, v, image.size, viewport)
    last = float(len(rows) - 1)
    if max_anisotropy > 1:
        footprint, shift, confidence = _anisotropic_footprint(B, dx, dy, image.size, max_anisotropy)
        level = B.min(last, B.unary("log2", B.max(1.0, footprint)))
        locations = [(B.add(u, B.mul(step, shift[0])), B.add(v, B.mul(step, shift[1])))
                     for step in (-.375, -.125, .125, .375)]
        valid = B.mul(valid, confidence)
    else:
        widths = [B.add(B.mul(a, a), B.mul(b, b)) for a, b in (dx, dy)]
        level = B.min(last, B.mul(.5, B.unary("log2", B.max(1.0, B.max(*widths)))))
        locations = [(u, v)]
    low = B.unary("floor", level)
    high = B.min(last, B.add(low, 1.0))
    taps = []
    for s, t in locations:
        a = _atlas_bilinear(B, atlas, rows, low, s, t, extension)
        b = _atlas_bilinear(B, atlas, rows, high, s, t, extension)
        taps.append(_lerp(B, a, b, B.sub(level, low)))
    explicit = taps[0] if len(taps) == 1 else [
        B.mul(.25, B.add(B.add(a, b), B.add(c, d))) for a, b, c, d in zip(*taps)]
    engine = B.image_sample(image, "Linear", extension, u, v)
    return _lerp(B, engine, explicit, valid)
