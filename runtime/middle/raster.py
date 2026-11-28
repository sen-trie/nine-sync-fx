"""Direct homogeneous rasterization for FX outputs that mesh modifiers cannot represent.

Coordinates follow D3D9: z in [0,w], a downward viewport Y axis and integer
pixel centers. The FX parser/interpreter remains responsible for both shaders.
This CPU execution path is intentionally independent of Blender's rasterizer.
"""
from dataclasses import dataclass, replace

import numpy as np

from ...fx.errors import FxUnsupported


FORMATS = {"A8R8G8B8", "R16F", "R32F", "A16B16G16R16F", "A32B32G32R32F"}
COMPARE = {"NEVER": np.zeros_like, "LESS": np.less, "EQUAL": np.equal,
           "LESSEQUAL": np.less_equal, "GREATER": np.greater, "NOTEQUAL": np.not_equal,
           "GREATEREQUAL": np.greater_equal, "ALWAYS": np.ones_like}


def compare(function, source, destination):
    function = function.upper().replace("D3DCMP_", "").replace("LEQUAL", "LESSEQUAL").replace("GEQUAL", "GREATEREQUAL")
    if function not in COMPARE:
        raise FxUnsupported(f"Unimplemented comparison function {function!r}.")
    if function == "ALWAYS":
        return np.ones(np.broadcast(source, destination).shape, dtype=bool)
    if function == "NEVER":
        return np.zeros(np.broadcast(source, destination).shape, dtype=bool)
    return COMPARE[function](source, destination)


def quantize_color(values, format):
    values = np.asarray(values, dtype=np.float32)
    if format == "A8R8G8B8":
        return np.floor(np.clip(values, 0, 1) * 255 + .5) / 255
    if format in ("R16F", "A16B16G16R16F"):
        values = values.astype(np.float16).astype(np.float32)
    elif format not in ("R32F", "A32B32G32R32F"):
        raise FxUnsupported(f"Unimplemented color format {format!r}.")
    if format in ("R16F", "R32F"):
        values = values.copy()
        values[..., 1:3] = 0
        values[..., 3] = 1
    return values


class ColorTarget:
    def __init__(self, width, height, format="A8R8G8B8", mip_levels=1):
        format = format.upper().removeprefix("D3DFMT_")
        if format not in FORMATS or width < 1 or height < 1:
            raise FxUnsupported(f"Invalid color target {width}x{height} / {format}.")
        self.width, self.height, self.format = int(width), int(height), format
        self.mip_levels = mip_levels
        self.pixels = np.zeros((self.height, self.width, 4), dtype=np.float32)
        if format in ("R16F", "R32F"):
            self.pixels[..., 3] = 1

    def clear(self, value):
        # MME passes a packed D3DCOLOR to Device.Clear, even for floating targets.
        value = np.floor(np.clip(np.asarray(value, dtype=np.float64), 0, 1) * 255) / 255
        self.pixels[:] = quantize_color(value, self.format)

    def reduce_mip(self, pixels):
        for axis in (0, 1):
            size = pixels.shape[axis]
            if size == 1:
                continue
            count = size // 2
            indices = np.arange(count)
            first = np.take(pixels, 2 * indices, axis=axis)
            middle = np.take(pixels, 2 * indices + 1, axis=axis)
            if size % 2 == 0:
                pixels = (first + middle) * .5
                continue
            # Native MME impulse probes show three taps for an odd source axis.
            # The first tap transfers part of its area weight to the middle tap.
            shape = [1] * pixels.ndim
            shape[axis] = count
            first_weight = ((count - indices - (indices + .5) / count) / size).reshape(shape)
            last_weight = ((indices + 1) / size).reshape(shape)
            last = np.take(pixels, 2 * indices + 2, axis=axis)
            pixels = first * first_weight + middle * (1 - first_weight - last_weight) + last * last_weight
        return quantize_color(pixels, self.format)

    def copy(self):
        other = ColorTarget(self.width, self.height, self.format, self.mip_levels)
        other.pixels[:] = self.pixels
        return other


class DepthTarget:
    def __init__(self, width, height, bits=24):
        self.width, self.height, self.bits = int(width), int(height), bits
        self.values = np.ones((self.height, self.width), dtype=np.float64)

    def quantize(self, values):
        values = np.clip(values, 0, 1)
        if self.bits == 32:
            return values.astype(np.float32).astype(np.float64)
        maximum = (1 << self.bits) - 1
        return np.floor(values * maximum + .5) / maximum

    def clear(self, value=1):
        self.values[:] = self.quantize(np.asarray(value))

    def copy(self):
        other = DepthTarget(self.width, self.height, self.bits)
        other.values[:] = self.values
        return other


@dataclass(frozen=True)
class RasterState:
    depth_test: bool = True
    depth_write: bool = True
    depth_func: str = "LESSEQUAL"
    cull: str = "NONE"
    alpha_test: bool = False
    alpha_func: str = "GREATER"
    alpha_ref: int = 0
    blend: bool = False
    source_blend: str = "SRCALPHA"
    destination_blend: str = "INVSRCALPHA"
    blend_op: str = "ADD"
    separate_alpha: bool = False
    source_alpha: str = "ONE"
    destination_alpha: str = "ZERO"
    alpha_op: str = "ADD"
    color_mask: int = 15
    depth_bias: float = 0
    slope_bias: float = 0
    color_mask1: int = 15
    color_mask2: int = 15
    color_mask3: int = 15
    quantize_blend_source: bool = False

    def apply(self, states):
        fields = {
            "zenable": ("depth_test", bool_value), "zwriteenable": ("depth_write", bool_value),
            "zfunc": ("depth_func", enum_value), "cullmode": ("cull", enum_value),
            "alphatestenable": ("alpha_test", bool_value), "alphafunc": ("alpha_func", enum_value),
            "alpharef": ("alpha_ref", integer_value), "alphablendenable": ("blend", bool_value),
            "srcblend": ("source_blend", enum_value), "destblend": ("destination_blend", enum_value),
            "blendop": ("blend_op", enum_value), "separatealphablendenable": ("separate_alpha", bool_value),
            "srcblendalpha": ("source_alpha", enum_value), "destblendalpha": ("destination_alpha", enum_value),
            "blendopalpha": ("alpha_op", enum_value),
            "colorwriteenable": ("color_mask", integer_value),
            "colorwriteenable1": ("color_mask1", integer_value),
            "colorwriteenable2": ("color_mask2", integer_value),
            "colorwriteenable3": ("color_mask3", integer_value),
            "depthbias": ("depth_bias", float), "slopescaledepthbias": ("slope_bias", float),
        }
        updates = {}
        for key, value in states.items():
            if key.lower() not in fields:
                raise FxUnsupported(f"Unimplemented raster state {key!r}.")
            name, parse = fields[key.lower()]
            updates[name] = parse(value)
        result = replace(self, **updates)
        if result.cull not in ("CW", "CCW", "NONE") or not 0 <= result.alpha_ref <= 255:
            raise FxUnsupported("Invalid culling or alpha reference.")
        if any(not 0 <= mask <= 15 for mask in (result.color_mask, result.color_mask1,
                                               result.color_mask2, result.color_mask3)):
            raise FxUnsupported("Color-write masks must be between 0 and 15.")
        compare(result.depth_func, 0, 0)
        compare(result.alpha_func, 0, 0)
        return result


def bool_value(value):
    text = str(value).upper().strip()
    if text not in ("FALSE", "TRUE", "0", "1"):
        raise FxUnsupported(f"Expected an explicit raster-state boolean, got {value!r}.")
    return text in ("TRUE", "1")


def enum_value(value):
    return str(value).upper().strip().removeprefix("D3DBLEND_").removeprefix("D3DBLENDOP_").removeprefix("D3DCULL_")


def integer_value(value):
    return int(str(value).strip(), 0)


def blend_factor(name, source, destination):
    name = enum_value(name)
    if name == "ZERO": return np.zeros_like(source)
    if name == "ONE": return np.ones_like(source)
    if name == "SRCCOLOR": return source
    if name == "INVSRCCOLOR": return 1-source
    if name == "DESTCOLOR": return destination
    if name == "INVDESTCOLOR": return 1-destination
    if name == "SRCALPHA": return np.broadcast_to(source[:, 3:4], source.shape)
    if name == "INVSRCALPHA": return np.broadcast_to(1-source[:, 3:4], source.shape)
    if name == "DESTALPHA": return np.broadcast_to(destination[:, 3:4], source.shape)
    if name == "INVDESTALPHA": return np.broadcast_to(1-destination[:, 3:4], source.shape)
    if name == "SRCALPHASAT":
        result = np.broadcast_to(np.minimum(source[:, 3:4], 1-destination[:, 3:4]), source.shape).copy()
        result[:, 3] = 1
        return result
    raise FxUnsupported(f"Unimplemented blend factor {name!r}.")


def blend_operation(name, a, b):
    if name == "ADD": return a+b
    if name == "SUBTRACT": return a-b
    if name == "REVSUBTRACT": return b-a
    if name == "MIN": return np.minimum(a, b)
    if name == "MAX": return np.maximum(a, b)
    raise FxUnsupported(f"Unimplemented blend operation {name!r}.")


def blend_color(state, source, destination):
    if not state.blend:
        return source
    output = blend_operation(state.blend_op, source*blend_factor(state.source_blend, source, destination),
                             destination*blend_factor(state.destination_blend, source, destination))
    if state.separate_alpha:
        output[:, 3] = blend_operation(state.alpha_op,
            source[:, 3]*blend_factor(state.source_alpha, source, destination)[:, 3],
            destination[:, 3]*blend_factor(state.destination_alpha, source, destination)[:, 3])
    return output


def clipped_triangles(positions, attributes, indices):
    """Sutherland-Hodgman clipping in homogeneous space, carrying all varyings."""
    if not np.isfinite(positions).all():
        raise FxUnsupported("Nonfinite clip-space vertex positions have undefined raster behavior.")
    # Clip the six D3D half-spaces, and exclude the undefined all-zero w vertex.
    planes = np.array([[1,0,0,1],[-1,0,0,1],[0,1,0,1],[0,-1,0,1],[0,0,1,0],[0,0,-1,1],[0,0,0,1]], dtype=float)
    triangles = positions[indices]
    distances = triangles @ planes.T
    distances[..., -1] -= 1e-10
    rejected = np.any(np.all(distances < 0, axis=1), axis=1)
    accepted = np.all(distances >= 0, axis=(1,2))
    for primitive, corners in enumerate(indices):
        if rejected[primitive]: continue
        p = triangles[primitive]
        a = attributes[corners]
        if not accepted[primitive]:
            polygon = [(v.copy(), x.copy()) for v,x in zip(p,a)]
            for plane_index, plane in enumerate(planes):
                output = []
                if not polygon: break
                previous, previous_attribute = polygon[-1]
                previous_distance = previous @ plane - (1e-10 if plane_index==6 else 0)
                for current, current_attribute in polygon:
                    distance = current @ plane - (1e-10 if plane_index==6 else 0)
                    if (distance >= 0) != (previous_distance >= 0):
                        t = previous_distance/(previous_distance-distance)
                        output.append((previous+(current-previous)*t, previous_attribute+(current_attribute-previous_attribute)*t))
                    if distance >= 0: output.append((current,current_attribute))
                    previous,previous_attribute,previous_distance = current,current_attribute,distance
                polygon = output
            for i in range(1,len(polygon)-1):
                chosen = [polygon[0],polygon[i],polygon[i+1]]
                yield np.array([v[0] for v in chosen]),np.array([v[1] for v in chosen]),primitive
        else:
            yield p,a,primitive


def edge(a, b, x, y):
    return (b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0])


def top_left(a, b):
    delta = b-a
    return delta[1] < 0 or delta[1] == 0 and delta[0] > 0


class RasterInputs(dict):
    pass


class Rasterizer:
    def __init__(self, width, height, fragment_batch=262144, subpixel_bits=8, derivatives=False, *, buffer=False):
        self.width,self.height = int(width),int(height)
        self.derivatives = derivatives
        self.buffer = buffer
        self.fragment_batch,self.subpixel_bits = fragment_batch,subpixel_bits
        self.stats = dict(input_triangles=0,clipped_triangles=0,shaded_fragments=0,passing_fragments=0)

    def _buffer_gradient_delta(self, clip, screen, values, area):
        # Native fullscreen UV readouts show a reduced reciprocal of the
        # power-of-two triangle area. Keep analytic pixel inputs and constant
        # varyings exact, and use the same plane for derivative helper lanes.
        if (not self.buffer or self.width & (self.width - 1) or self.height & (self.height - 1)
                or not np.all(clip[:, 3] == 1)
                or not np.all((screen == 0) | (screen == [self.width, self.height]))):
            return None
        gradients = np.linalg.solve(np.column_stack([screen, np.ones(3)]), values)[:2]
        inverse = np.float32(1 / area)
        ratio = float(np.nextafter(inverse, np.float32(0))) * area
        return gradients * (ratio - 1)

    def fragments(self, positions, varyings, indices, state):
        names = list(varyings)
        widths = [varyings[name].shape[1] for name in names]
        attributes = np.concatenate([varyings[name] for name in names], axis=1) if names else np.empty((len(positions),0))
        pending = []
        count = 0
        self.stats['input_triangles'] += len(indices)
        for clip, values, primitive in clipped_triangles(positions,attributes,indices):
            screen = clip[:, :2]/clip[:, 3:4]
            screen = (screen*np.array([.5,-.5])+.5)*np.array([self.width,self.height])
            grid = np.rint(screen*(1<<self.subpixel_bits)).astype(np.int64)
            area = edge(grid[0],grid[1],grid[2,0],grid[2,1])
            if not area or state.cull == ('CW' if area>0 else 'CCW'): continue
            face = 1.0 if area>0 else -1.0
            self.stats['clipped_triangles'] += 1
            if area<0:
                order=[0,2,1];clip,values,screen,grid=clip[order],values[order],screen[order],grid[order]
                area=-area
            lo=np.maximum(np.ceil(grid.min(axis=0)/(1<<self.subpixel_bits)).astype(int),0)
            hi=np.minimum(np.floor(grid.max(axis=0)/(1<<self.subpixel_bits)).astype(int),[self.width-1,self.height-1])
            if np.any(hi<lo): continue
            x,y=np.meshgrid(np.arange(lo[0],hi[0]+1),np.arange(lo[1],hi[1]+1));x,y=x.ravel(),y.ravel()
            qx,qy=x*(1<<self.subpixel_bits),y*(1<<self.subpixel_bits)
            edges=[edge(grid[a],grid[b],qx,qy) for a,b in ((1,2),(2,0),(0,1))]
            inside=np.ones(len(x),dtype=bool)
            for e,(a,b) in zip(edges,((1,2),(2,0),(0,1))): inside &= (e>0)|((e==0)&top_left(grid[a],grid[b]))
            x,y=x[inside],y[inside]
            if not len(x): continue
            # Unsapped screen planes supply interpolation; fixed subpixels decide coverage.
            float_area=edge(screen[0],screen[1],screen[2,0],screen[2,1])
            if abs(float_area)<1e-20: continue
            weights=np.column_stack([edge(screen[a],screen[b],x,y)/float_area for a,b in ((1,2),(2,0),(0,1))])
            depth=weights @ (clip[:,2]/clip[:,3])
            perspective=weights/clip[:,3]
            perspective/=perspective.sum(axis=1,keepdims=True)
            interpolated=perspective @ values
            delta = self._buffer_gradient_delta(clip, screen, values, float_area)
            if delta is not None:
                interpolated += np.column_stack([x + .5, y + .5]) @ delta
            vpos=np.column_stack([x,y,depth,weights @ (1/clip[:,3])])
            if self.derivatives:
                samples=[interpolated];locations=[vpos]
                dx = np.where(x % 2 == 0, 1, -1)
                dy = np.where(y % 2 == 0, 1, -1)
                for offset_x, offset_y in ((dx, 0), (0, dy), (dx, dy)):
                    hx, hy = x + offset_x, y + offset_y
                    helper=np.column_stack([edge(screen[a],screen[b],hx,hy)/float_area for a,b in ((1,2),(2,0),(0,1))])
                    reciprocal=helper @ (1/clip[:,3])
                    interpolated_helper = (helper / clip[:, 3]) @ values / reciprocal[:, None]
                    if delta is not None:
                        interpolated_helper += np.column_stack([hx + .5, hy + .5]) @ delta
                    samples.append(interpolated_helper)
                    locations.append(np.column_stack([hx,hy,helper @ (clip[:,2]/clip[:,3]),reciprocal]))
                interpolated=np.stack(samples,axis=1)
                vpos=np.stack(locations,axis=1)
            if state.depth_bias or state.slope_bias:
                slopes=np.linalg.solve(np.column_stack([screen,np.ones(3)]),clip[:,2]/clip[:,3])
                depth+=state.depth_bias+state.slope_bias*np.abs(slopes[:2]).max()
            pending.append((x,y,depth,interpolated,vpos,np.full(len(x),face)))
            count+=len(x)
            if count>=self.fragment_batch:
                yield self._pack(pending,names,widths);pending=[];count=0
        if pending: yield self._pack(pending,names,widths)

    @staticmethod
    def _pack(parts,names,widths):
        x,y,z,values,vpos,face=[np.concatenate([p[i] for p in parts]) for i in range(6)]
        count=len(x)
        helpers=values.ndim==3
        if helpers:
            lanes = values.shape[1]
            values = values.transpose(1, 0, 2).reshape(lanes * count, values.shape[-1])
            vpos = vpos.transpose(1, 0, 2).reshape(lanes * count, vpos.shape[-1])
            face = np.tile(face, lanes)
        channels=np.split(values,np.cumsum(widths)[:-1],axis=1) if names else []
        varyings=RasterInputs(zip(names,channels));varyings['VPOS']=vpos;varyings['VFACE']=face[:,None]
        varyings.fragment_count=count
        varyings.derivative_signs=(np.where(x%2==0,1,-1),np.where(y%2==0,1,-1)) if helpers else None
        # This also supplies the raster position for SV_Position-normalized
        # entry structs. Legacy DX9 shaders commonly declare an unused Pos.
        varyings['POSITION0']=vpos
        return x,y,z,varyings

    def draw(self, positions, varyings, indices, shader, targets, depth, state=RasterState(), *, written_pixels=None):
        if not targets: raise FxUnsupported('A raster draw needs a bound color target.')
        if any((t.width,t.height)!=(self.width,self.height) for t in targets.values()):
            raise FxUnsupported('All bound color targets must match the raster viewport.')
        if state.depth_test and depth is not None and (depth.width<self.width or depth.height<self.height):
            raise FxUnsupported('The depth target must cover the raster viewport.')
        if state.depth_test and depth is None: raise FxUnsupported('Depth testing needs a bound depth target.')
        masks = (state.color_mask, state.color_mask1, state.color_mask2, state.color_mask3)
        for x,y,z,inputs in self.fragments(positions,varyings,indices,state):
            self.stats['shaded_fragments']+=len(x)
            colors,discard,pixel_depth=shader(inputs)
            colors={slot:np.broadcast_to(value,(len(x),4)) for slot,value in colors.items()}
            alive=np.broadcast_to(np.asarray(discard)==0,(len(x),)).copy()
            if state.alpha_test:
                if 0 not in colors: raise FxUnsupported('Alpha testing requires COLOR0.')
                alive &= compare(state.alpha_func,colors[0][:,3],state.alpha_ref/255)
            if pixel_depth is not None: z=np.broadcast_to(pixel_depth,(len(x),))
            if depth is not None: z=depth.quantize(z)
            alive &= np.isfinite(z)
            selected=np.flatnonzero(alive)
            pixel=y*self.width+x
            order=selected[np.argsort(pixel[selected],kind='stable')]
            starts=np.r_[0,np.flatnonzero(np.diff(pixel[order]))+1]
            if not len(order): continue
            ends=np.r_[starts[1:],len(order)]
            # One unique fragment per pixel per iteration preserves primitive
            # order for blend/depth interactions without a Python pixel loop.
            for layer in range(int((ends-starts).max())):
                active=starts+layer;active=active[active<ends]
                lane=order[active]
                if state.depth_test: lane=lane[compare(state.depth_func,z[lane],depth.values[y[lane],x[lane]])]
                if written_pixels is not None:
                    lane = lane[~written_pixels[y[lane], x[lane]]]
                if not len(lane): continue
                self.stats['passing_fragments']+=len(lane)
                for slot,target in targets.items():
                    if slot not in colors or not masks[slot]:
                        continue
                    mask = masks[slot]
                    source=colors[slot][lane]
                    if not np.isfinite(source).all(): raise FxUnsupported('Nonfinite pixel colors have undefined raster behavior.')
                    if state.quantize_blend_source and state.blend and target.format == "A8R8G8B8":
                        source = quantize_color(source, target.format)
                    destination=target.pixels[y[lane],x[lane]]
                    result=quantize_color(blend_color(state,source,destination),target.format)
                    for channel in range(4):
                        if mask & (1<<channel): target.pixels[y[lane],x[lane],channel]=result[:,channel]
                if state.depth_test and state.depth_write: depth.values[y[lane],x[lane]]=z[lane]
                if written_pixels is not None:
                    written_pixels[y[lane], x[lane]] = True
