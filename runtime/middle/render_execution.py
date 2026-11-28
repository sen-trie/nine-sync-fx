"""Execute complete ordered FX draws through the shared NumPy interpreter."""
from dataclasses import dataclass

import numpy as np

from ...fx.annotations import fx_annotation
from ...fx.entry import fx_entry,fx_run_entry
from ...fx.errors import FxUnsupported
from ...fx.execution_plan import fx_plan_operations
from ...fx.interp.executor import FxExec
from ...fx.interp.numpy_backend import FxNumpyBackend
from ...fx.semantics import fx_classify_global,fx_is_render_target,fx_sampler_texture_global,fx_value_from_list
from ...fx.textures import FxNumpyEnv,FxTextureStore,FxImage
from ...fx.types import FxValue,fx_resolve_type,fx_count,fx_is_numeric
from .raster import ColorTarget,DepthTarget,RasterState,Rasterizer


def columns(value,count):
    if not fx_is_numeric(value.t):raise FxUnsupported('Raster streams require numeric shader outputs.')
    return np.column_stack([np.broadcast_to(np.asarray(c,dtype=np.float32),(count,)) for c in value.c])


@dataclass
class GeometryStream:
    inputs: dict
    triangles: object

    def validate(self):
        positions=self.inputs.get('POSITION0')
        if positions is None or positions.ndim!=2 or positions.shape[1]!=4:
            raise FxUnsupported('Geometry streams require an N x 4 POSITION0 input.')
        count=len(positions)
        if any(value.ndim!=2 or len(value)!=count for value in self.inputs.values()):
            raise FxUnsupported('Every geometry input must have the same vertex count.')
        indices=np.asarray(self.triangles)
        if indices.ndim!=2 or indices.shape[1]!=3 or indices.dtype.kind not in 'iu':
            raise FxUnsupported('Geometry streams require integer triangle indices.')
        if indices.size and (indices.min()<0 or indices.max()>=count):
            raise FxUnsupported('A triangle references a missing stream vertex.')


class RasterEnvironment(FxNumpyEnv):
    def __init__(self,program,state,textures,targets,images=None):
        super().__init__(program,state,textures)
        self.targets,self.images=targets,images or {}
        self.bound_targets=set()

    def global_value(self,ex,g):
        source=self.source(g)
        if source and source[0]=='param' and source[1] not in self.state.params:
            return None # Keep the original declaration's initializer.
        if source and source[0]=='control' and g.name not in self.state.controls:
            raise FxUnsupported(f'Unbound raster controller {g.name!r}: {source[1:]!r}.')
        return super().global_value(ex,g)

    def sample(self,ex,info,u,v,lod):
        texture=fx_sampler_texture_global(self.program,info)
        if texture and texture.name in self.targets:
            if texture.name in self.bound_targets:
                raise FxUnsupported(f'Sampling an actively written target is undefined: {texture.name}. Use distinct history targets.')
            image=FxImage(self.targets[texture.name].pixels,texture.name)
        elif texture and texture.name in self.images:
            image=self.images[texture.name]
        else:
            if texture and fx_is_render_target(texture):
                raise FxUnsupported(f'Render target {texture.name!r} is not bound to this execution context.')
            return super().sample(ex,info,u,v,lod)
        if lod is not None:
            level=ex.B.known(lod)
            if level!=0:raise FxUnsupported('Nonzero target mip levels need verified mip generation.')
        return image.sample(u,v,info.point(),info.address(),dtype=np.float32)


class FxRenderExecution:
    """Persistent render resources, with atomic publication per execution.

    Mesh streams are evaluated inputs, not replacements for artist meshes.
    The returned main target is directly rasterized; no inverse projection or
    Blender surface interpolation is used for arbitrary shader clip outputs.
    """
    def __init__(self,program,width,height,texture_loader=None):
        self.program=program;self.width=int(width);self.height=int(height)
        self.targets={'':ColorTarget(width,height)}
        self.depths={'':DepthTarget(width,height)}
        self.textures=FxTextureStore(program,texture_loader or self._missing_image)
        self.last_operations=[];self.last_stats=[]
        for name,g in program.globals.items():
            if not fx_is_render_target(g):continue
            ratio=fx_annotation(g,'ViewPortRatio',program,None)
            if ratio is not None:
                ratio=[ratio,ratio] if np.ndim(ratio)==0 else ratio
                w,h=max(1,int(width*ratio[0])),max(1,int(height*ratio[1]))
            else:
                w=int(fx_annotation(g,'Width',program,width));h=int(fx_annotation(g,'Height',program,height))
            default_format='D24S8' if 'DEPTH' in (g.semantic or '') else 'A8R8G8B8'
            format=str(fx_annotation(g,'Format',program,default_format)).upper().removeprefix('D3DFMT_')
            if 'DEPTH' in (g.semantic or ''):
                if format not in ('D16','D24S8','D24X8','D32'):
                    raise FxUnsupported(f'Unimplemented depth target format {format!r}.')
                self.depths[name]=DepthTarget(w,h,16 if format=='D16' else 32 if format=='D32' else 24)
            else:
                if fx_annotation(g,'AntiAlias',program,False):
                    raise FxUnsupported('Multisampled targets require a verified resolve path.')
                self.targets[name]=ColorTarget(w,h,format)

    @staticmethod
    def _missing_image(path):
        raise FxUnsupported(f'No raster texture loader was supplied for {path!r}.')

    def _run_shader(self,compiled,env,inputs,overrides):
        function,args=fx_entry(self.program,compiled)
        if function is None:raise FxUnsupported('A raster pass requires an explicit shader entry point.')
        backend=FxNumpyBackend(np.float32);ex=FxExec(self.program,backend,env)
        for name,value in overrides.items():
            g=self.program.globals[name]
            ex.overrides[name]=fx_value_from_list(ex,[value],fx_resolve_type(g.type,self.program))
        def read(semantic,t):
            if semantic not in inputs:raise FxUnsupported(f'Raster shader input {semantic!r} is unavailable.')
            array=inputs[semantic]
            if array.shape[1]<fx_count(t[1]):raise FxUnsupported(f'Raster input {semantic} has insufficient components.')
            return FxValue(('f',(array.shape[1],)),[array[:,i] for i in range(array.shape[1])])
        return fx_run_entry(ex,function,args,read),ex.discard

    def execute(self,geometry,state,subset=0,images=None,initial_state=RasterState(),external=None,technique=None):
        geometry.validate()
        colors={name:target.copy() for name,target in self.targets.items()}
        depths={name:target.copy() for name,target in self.depths.items()}
        env=RasterEnvironment(self.program,state,self.textures,colors,images)
        target_names={0:''};depth_name='';clear_color=[0,0,0,0];clear_depth=1
        raster_state=initial_state;trace=[];stats=[]
        values={**state.params,**state.controls}
        for operation in fx_plan_operations(self.program,subset,values,technique=technique):
            trace.append(dict(kind=operation.kind,value=operation.value if operation.kind!='states' else dict(operation.value),
                              pass_name=operation.draw.fx_pass.name if operation.draw else None,
                              loop_values=dict(operation.loop_values)))
            if operation.kind=='states':raster_state=raster_state.apply(operation.value)
            elif operation.kind=='color_target':
                slot,name=operation.value
                if name not in colors:raise FxUnsupported(f'Unknown color target {name!r}.')
                if slot and not name:target_names.pop(slot,None)
                else:target_names[slot]=name
            elif operation.kind=='depth_target':
                depth_name=operation.value
                if depth_name not in depths:raise FxUnsupported(f'Unknown depth target {depth_name!r}.')
            elif operation.kind in ('clearsetcolor','clearsetdepth'):
                backend=FxNumpyBackend(np.float32);ex=FxExec(self.program,backend,env)
                value=ex.global_value(operation.value)
                if operation.kind=='clearsetcolor':
                    if fx_count(value.t[1])!=4:raise FxUnsupported('ClearSetColor requires a float4 global.')
                    clear_color=[float(v) for v in value.c]
                else:clear_depth=float(value.c[0])
            elif operation.kind=='clear_color':
                for name in target_names.values():colors[name].clear(clear_color)
            elif operation.kind=='clear_depth':depths[depth_name].clear(clear_depth)
            elif operation.kind=='external':
                if external is None:raise FxUnsupported('ScriptExternal needs an actual scene-draw callback.')
                external(operation.value,{slot:colors[name] for slot,name in target_names.items()},depths[depth_name])
            elif operation.kind=='draw':
                draw=operation.draw;target=colors[target_names[0]]
                overrides=dict(operation.loop_values)
                if draw.kind=='buffer':
                    # Integer-position native buffer UVs are produced by the
                    # viewport mapping; shader half-texel offsets remain live.
                    inputs={'POSITION0':np.array([[-1,1,0,1],[1,1,0,1],[1,-1,0,1],[-1,-1,0,1]],dtype=np.float32),
                            'TEXCOORD0':np.array([[0,0],[1,0],[1,1],[0,1]],dtype=np.float32)}
                    stream=GeometryStream(inputs,np.array([[0,1,2],[0,2,3]]))
                else:stream=geometry
                env.bound_targets=set(target_names.values())
                vertex,discard=self._run_shader(draw.fx_pass.vs,env,stream.inputs,overrides)
                if np.any(discard):raise FxUnsupported('A vertex-stage discard has no defined D3D9 raster meaning.')
                if 'POSITION0' not in vertex:raise FxUnsupported('The vertex shader must output POSITION0.')
                count=len(stream.inputs['POSITION0'])
                positions=columns(vertex.pop('POSITION0'),count)
                if positions.shape[1]!=4:raise FxUnsupported('Raster POSITION0 must have four components.')
                varyings={name:columns(value,count) for name,value in vertex.items()}
                def shade(inputs):
                    outputs,discard=self._run_shader(draw.fx_pass.ps,env,inputs,overrides)
                    n=len(next(iter(inputs.values())))
                    result={int(name[5:]):columns(value,n) for name,value in outputs.items() if name.startswith('COLOR') and name[5:].isdigit()}
                    depth_value=outputs.get('DEPTH',outputs.get('DEPTH0'))
                    return result,discard,depth_value.c[0] if depth_value is not None else None
                raster=Rasterizer(target.width,target.height)
                raster.draw(positions,varyings,stream.triangles,shade,{slot:colors[name] for slot,name in target_names.items()},
                            depths[depth_name],raster_state)
                stats.append(dict(pass_name=draw.fx_pass.name,kind=draw.kind,target=target_names[0],**raster.stats))
        # Commit only when every shader/state/resource operation succeeds.
        for name,copy in colors.items():self.targets[name].pixels[:]=copy.pixels
        for name,copy in depths.items():self.depths[name].values[:]=copy.values
        self.last_operations,self.last_stats=trace,stats
        return self.targets['']
