"""Feed evaluated artist geometry and existing model/controller data to FX raster execution."""
from dataclasses import dataclass
import math
from pathlib import Path
import re
from types import SimpleNamespace

import bpy
import numpy as np

from ...fx.annotations import fx_annotation
from ...fx.errors import FxUnsupported
from ...fx.interp.numpy_backend import FxNumpyBackend
from ...fx.params import fx_vs_globals
from ...fx.semantics import FxSceneState,fx_classify_global,fx_lh_look
from ...fx.techniques import fx_technique_pass_type,fx_technique_subset,fx_subset_matches
from ...fx.textures import FxImage
from ...fx.types import fx_resolve_type,fx_count
from .compiler import ModelInputs,is_mmd_material,has_sphere_map
from .controllers import DriverSource,DriverExpression,resolve
from .material_morphs import MaterialMorphs,model_root
from .render_execution import GeometryStream
from .targets import ORIGINAL,TOKEN
from .vertex import VERTEX_OWNER
from ...accessories.materials import X_MATERIAL,X_BASE_TEXTURE


SWAP=np.eye(4)[[0,2,1,3]]
# Native MMD 9.32 PROJECTION readout: q=1.0000100135803223 and
# r=-1.0000100135803223 in float32, represented by near=1 / far=100000.
# Keep the generic converter's older projection defaults independent.
MMD_RASTER_NEAR=1.0
MMD_RASTER_FAR=100000.0
CLEAR_SELF_SHADOW = FxImage(np.ones((1,1,4), dtype=np.float32), 'Clear self shadow')


def read_source(source,depsgraph):
    """Read the same native RNA sources used by the node converter's drivers."""
    if isinstance(source,(float,int)):return float(source)
    if isinstance(source,DriverExpression):
        values={name:read_source(value,depsgraph) for name,value in source.sources}
        return float(eval(source.expression,{'__builtins__':{}},values))
    if not isinstance(source,DriverSource):raise FxUnsupported('Unknown numeric controller source.')
    obj=source.id.evaluated_get(depsgraph)
    if source.axis:
        if source.bone:
            matrix=obj.pose.bones[source.bone].matrix
            if source.space=='WORLD_SPACE':matrix=obj.matrix_world @ matrix
        else:
            matrix=(obj.matrix_world if source.space=='WORLD_SPACE' else
                    obj.matrix_basis if source.space=='TRANSFORM_SPACE' else obj.matrix_local)
        if source.transform=='SCALE':value=matrix.to_scale()
        elif source.transform=='ROT':value=matrix.to_euler(getattr(source,'rotation_mode','XYZ'))
        else:value=matrix.translation
        return float(value['XYZ'.index(source.axis)])*source.scale
    try:value=obj.path_resolve(source.path)
    except ValueError:
        # RNA driver array paths index matrix columns. Python's Matrix indexing
        # indexes rows, and path_resolve does not accept a partial matrix path.
        match=re.search(r'\[(\d+)\]$',source.path)
        if not match:raise
        base=obj.path_resolve(source.path[:match.start()]);index=int(match.group(1))
        value=base.col[index] if hasattr(base,'col') else base[index]
    if source.index is not None:value=value[source.index]
    return float(value)*source.scale


def encoded_image(image, *, native_upload=False, material_texture=False):
    if image is None:return None
    width,height=image.size
    pixels=np.empty(width*height*4,dtype=np.float32);image.pixels.foreach_get(pixels)
    texture = FxImage(pixels.reshape(height,width,4)[::-1].copy(),image.name)
    if native_upload and Path(image.filepath).suffix.lower() == '.png' and not image.is_float:
        return texture.d3dx_png_upload(material_texture=material_texture)
    return texture


class NumericMaterial:
    def __init__(self,obj,material,program,technique,depsgraph):
        self.obj,self.material,self.program=obj,material,program
        self.analysis=SimpleNamespace(technique=technique)
        self.mmd_shading=is_mmd_material(material)
        self.backend=FxNumpyBackend(np.float32);self.depsgraph=depsgraph
        self.material_morphs=MaterialMorphs(self) if self.mmd_shading else None
        self.image_cache={}

    def driven(self,name,source):return read_source(source,self.depsgraph)

    def sample_image(self,image,u,v,interpolation,extension):
        if image not in self.image_cache:self.image_cache[image]=encoded_image(image)
        address={'EXTEND':'CLAMP','REPEAT':'WRAP','MIRROR':'MIRROR'}[extension]
        return self.image_cache[image].sample(u,v,interpolation=='Closest',address,np.float32)

    def material_values(self,semantic,count):
        if not self.mmd_shading and self.material.node_tree:
            # The existing plain-X importer stores the source diffuse in its
            # Principled inputs, not Material.diffuse_color's viewport swatch.
            principled=next((node for node in self.material.node_tree.nodes if node.type=='BSDF_PRINCIPLED'),None)
            if principled:
                diffuse=list(principled.inputs['Base Color'].default_value[:3])+[principled.inputs['Alpha'].default_value]
                if semantic=='DIFFUSE':return diffuse[:count]
                source=self.material.get(X_MATERIAL)
                if source:
                    values={'AMBIENT':diffuse[:3]+[1], 'SPECULAR':list(source['specular'])+[1],
                            'SPECULARPOWER':[source['power']], 'EMISSIVE':list(source['emissive'])+[1],
                            'TOONCOLOR':[1]*4,'EDGECOLOR':[0,0,0,1]}
                    if semantic in values:return values[semantic][:count]
        return ModelInputs.material_values(self,semantic,count)


@dataclass(frozen=True)
class SelfShadowCamera:
    mode: int
    view: np.ndarray
    projection: np.ndarray
    ray: np.ndarray


def self_shadow_matrices(eye, forward, ray, mode, distance):
    axis = np.linalg.norm(ray)
    if not axis or np.linalg.norm(np.cross(forward, ray)) <= axis * 1e-8:
        raise FxUnsupported('Self shadows need a light direction separate from the camera viewing axis.')
    alignment = min(1.0, abs(np.dot(forward, ray) / (np.linalg.norm(forward) * axis)))
    view = fx_lh_look(eye - 50 * ray, ray, forward)
    extent = (10000 - distance) * 1e-5
    angled = max(0.0, alignment - .8)
    scale = extent * (1 + mode * max(0.0, 1 - 10 * angled))
    edge = max(1 - alignment, 1 - 9 * angled)
    warp = scale * edge * (.5 if mode == 1 and alignment <= .9 else 1)
    projection = np.array([[scale, 0, 0, 0], [0, scale, .5 * warp, warp],
                           [0, 0, .15 * extent, 0], [0, -edge, 0, 1]])
    return SelfShadowCamera(mode, view, projection, ray)


def _world_light_ray(evaluated_light):
    # NineSync's explicit MMD light proxy points away from its world location.
    return -np.array(evaluated_light.matrix_world.translation)[[0,2,1]]


def make_self_shadow_camera(scene, units, light, mode, distance):
    import mathutils

    if scene.camera is None:
        raise FxUnsupported('Self shadows need a source camera.')
    depsgraph = bpy.context.evaluated_depsgraph_get()
    camera = scene.camera.evaluated_get(depsgraph)
    matrix = camera.matrix_world
    eye = np.array(matrix.translation)[[0,2,1]] / units
    forward = np.array(matrix.to_quaternion() @ mathutils.Vector((0,0,-1)))[[0,2,1]]
    ray = _world_light_ray(light.evaluated_get(depsgraph)) if light else FxSceneState().light_dir
    return self_shadow_matrices(eye, forward, ray, mode, distance)


class BlenderRasterState(FxSceneState):
    ground_shadow_brightness = None
    self_shadow_camera = None
    self_shadow_image = None

    def light_matrices(self):
        camera = self.self_shadow_camera
        return (camera.view, camera.projection) if camera else super().light_matrices()

    def project_ground_shadow(self, world_ray, brightness):
        if not world_ray[1]:
            raise FxUnsupported('Ground shadows need a light direction with a vertical component.')
        # Native MME projects along the world light ray onto Y=0.1 MMD units.
        projector = np.eye(4)
        projector[1] = [-world_ray[0]/world_ray[1], 0, -world_ray[2]/world_ray[1], 0]
        projector[3, 1] = .1
        self.world = self.world @ projector
        self.ground_shadow_brightness = brightness

    def values(self,source,g):
        if self.self_shadow_camera is not None and source == ('position', 'light'):
            return [*(-self.self_shadow_camera.ray), 1.0]
        if self.ground_shadow_brightness is not None:
            if source == ('light_color', 'AMBIENT'):
                # The shadow pass scales the native byte light by brightness / 256.
                color = np.asarray(self.light_color) * (255/256 * self.ground_shadow_brightness)
                return [*color, 1.0]
            if source == ('material', 'AMBIENT'):
                return [1.0, 1.0, 1.0, 1.0]
            if source == ('material', 'EMISSIVE'):
                return [0.0, 0.0, 0.0, 1.0]
        if source[0]=='material':
            return list(self.numeric_material.material_values(source[1],fx_count(fx_resolve_type(g.type,self.program)[1])))
        return super().values(source,g)


@dataclass
class EvaluatedStream:
    geometry: GeometryStream
    materials: tuple
    subsets: object
    world: object
    data_units: float

    def subset(self,index):return GeometryStream(self.geometry.inputs,self.geometry.triangles[self.subsets==index])


def snapshot_geometry(scene,obj,units=.08):
    """Evaluate a temporary draw copy; never disable or rewrite source modifiers.

    The stream is per corner, preserving UV seams, split normals and material
    indices after arbitrary artist modifiers/topology. It is removed immediately
    after extraction; the original object/data/rig/material graph stays intact.
    """
    clone=obj.copy();clone.name='MME raster input copy'
    scene.collection.objects.link(clone)
    try:
        for modifier in list(clone.modifiers):
            if modifier.type=='NODES' and modifier.node_group and (modifier.node_group.get(TOKEN) or modifier.node_group.get(VERTEX_OWNER)):
                clone.modifiers.remove(modifier)
        depsgraph=bpy.context.evaluated_depsgraph_get();depsgraph.update()
        evaluated=clone.evaluated_get(depsgraph);mesh=evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            data_units=units if model_root(obj) else 1.0
            points=np.empty(len(mesh.vertices)*3,dtype=np.float32);mesh.vertices.foreach_get('co',points)
            points=points.reshape(-1,3)[:,[0,2,1]]/data_units
            corners=np.empty(len(mesh.loops),dtype=np.int32);mesh.loops.foreach_get('vertex_index',corners)
            inputs={'POSITION0':np.column_stack([points[corners],np.ones(len(corners),dtype=np.float32)])}
            normals=np.empty(len(mesh.corner_normals)*3,dtype=np.float32);mesh.corner_normals.foreach_get('vector',normals)
            inputs['NORMAL0']=normals.reshape(-1,3)[:,[0,2,1]]
            for index,layer in enumerate(mesh.uv_layers):
                uv=np.empty(len(mesh.loops)*2,dtype=np.float32);layer.data.foreach_get('uv',uv);uv=uv.reshape(-1,2);uv[:,1]=1-uv[:,1]
                inputs['TEXCOORD'+str(index)]=uv
            color=mesh.color_attributes.active_color
            if color:
                pixels=np.empty(len(color.data)*4,dtype=np.float32);color.data.foreach_get('color',pixels);pixels=pixels.reshape(-1,4)
                inputs['COLOR0']=pixels[corners] if color.domain=='POINT' else pixels
            # Reversing Blender's handedness restores the original MMD winding.
            triangles=np.array([triangle.loops for triangle in mesh.loop_triangles],dtype=np.int32)[:,[0,2,1]]
            subsets=np.array([triangle.material_index for triangle in mesh.loop_triangles],dtype=np.int32)
            materials=tuple(slot.material.get(ORIGINAL,slot.material) if slot.material else None for slot in obj.material_slots)
            return EvaluatedStream(GeometryStream(inputs,triangles),materials,subsets,np.array(evaluated.matrix_world),data_units)
        finally:evaluated.to_mesh_clear()
    finally:bpy.data.objects.remove(clone,do_unlink=True)


def make_state(scene,obj,stream,slot,program,units=.08,owner=None,bindings=None,light=None,
               pass_types=('object','object_ss'),self_object=None,*,ground_shadow_brightness=None,
               self_shadow_camera=None,self_shadow_image=None):
    depsgraph=bpy.context.evaluated_depsgraph_get()
    state=BlenderRasterState();state.program=program;state.control_bindings={}
    material=stream.materials[slot]
    nodes=material.node_tree.nodes if material and material.node_tree else ()
    base=nodes.get('mmd_base_tex') if nodes else None
    if not base:
        candidates=[node for node in nodes if node.get(X_BASE_TEXTURE)]
        if len(candidates)>1:raise FxUnsupported('The X material has multiple tagged base textures.')
        base=candidates[0] if candidates else None
    image=base.image if base else None
    flags=dict(UseTexture=image is not None,UseSphereMap=has_sphere_map(material),UseToon=is_mmd_material(material))
    technique=next((t for role in pass_types for t in program.techniques
                    if fx_technique_pass_type(t,program)==role and fx_subset_matches(fx_technique_subset(t,program),slot)
                    and all(fx_annotation(t,key,program,value)==value for key,value in flags.items())),None)
    if technique is None:
        if ground_shadow_brightness is not None or pass_types == ('zplot',):
            return None
        raise FxUnsupported(f'No matching raster technique for material {slot}.')
    state.numeric_material=NumericMaterial(obj,material,program,technique,depsgraph)
    world=SWAP @ stream.world @ SWAP
    world[:3,:3]*=stream.data_units/units;world[:3,3]/=units
    state.world=world.T
    camera=scene.camera
    if camera is None:raise FxUnsupported('Raster execution needs a source camera.')
    camera=camera.evaluated_get(depsgraph)
    view=np.diag([1,1,-1,1]) @ np.linalg.inv(np.array(camera.matrix_world)) @ SWAP
    view[:3,3]/=units;state.view=view.T
    width=scene.render.resolution_x*scene.render.resolution_percentage/100
    height=scene.render.resolution_y*scene.render.resolution_percentage/100
    projection=np.array(camera.calc_matrix_camera(depsgraph,x=int(width),y=int(height)))
    near,far=MMD_RASTER_NEAR,MMD_RASTER_FAR
    state.proj=np.zeros((4,4));state.proj[0,0]=projection[0,0];state.proj[1,1]=projection[1,1]
    if camera.data.type=='ORTHO':
        state.proj[0,0]*=units;state.proj[1,1]*=units
        state.proj[2,2]=1/(far-near);state.proj[3,2]=-near/(far-near);state.proj[3,3]=1
        state.proj[3,:2]=projection[:2,3]
    elif camera.data.type=='PERSP':
        state.proj[2,2]=far/(far-near);state.proj[3,2]=-near*far/(far-near);state.proj[2,3]=1
        state.proj[2,:2]=-projection[:2,2]
    else:raise FxUnsupported('Raster execution needs a perspective or orthographic camera.')
    state.camera_pos=np.array(camera.matrix_world.translation)[[0,2,1]]/units
    state.camera_dir=np.array((camera.matrix_world.to_quaternion() @ __import__('mathutils').Vector((0,0,-1))))[[0,2,1]]
    state.time=scene.frame_current*scene.render.fps_base/scene.render.fps
    state.elapsed=scene.render.fps_base/scene.render.fps;state.viewport=(width,height)
    mmd=getattr(material,'mmd_material',None)
    state.material['texture']=bpy.path.abspath(image.filepath) if image else ''
    state.flags.update(use_toon=flags['UseToon'],use_spheremap=flags['UseSphereMap'],
                       spadd=flags['UseSphereMap'] and int(mmd.sphere_texture_type)==2,
                       use_subtexture=flags['UseSphereMap'] and int(mmd.sphere_texture_type)==3,
                       parthf=camera.data.type=='PERSP',use_selfshadow=False)
    referenced=fx_vs_globals(program,technique.passes,stages=('vs','ps'))
    world_shading=any(g.name in referenced and fx_classify_global(g,program)==('matrix','WORLD','','camera') for g in program.globals.values())
    ray = state.light_dir
    if light:
        evaluated_light = light.evaluated_get(depsgraph)
        ray=_world_light_ray(evaluated_light)
        state.light_dir=ray if world_shading else np.linalg.inv(state.world[:3,:3].T) @ ray
        if light.type=='LIGHT':state.light_color=np.array(evaluated_light.data.color)
    if ground_shadow_brightness is not None:
        state.project_ground_shadow(ray, ground_shadow_brightness)
    role = fx_technique_pass_type(technique, program)
    if self_shadow_camera is not None and role in ('zplot', 'object_ss'):
        state.self_shadow_camera = self_shadow_camera
        state.flags['parthf'] = self_shadow_camera.mode == 2
        if role == 'object_ss':
            receives = mmd.enabled_self_shadow
            state.self_shadow_image = self_shadow_image if receives else CLEAR_SELF_SHADOW
            state.flags['use_selfshadow'] = receives
    for g in program.globals.values():
        source=fx_classify_global(g,program)
        if g.name not in referenced or not source or source[0]!='control':continue
        t=fx_resolve_type(g.type,program)
        _target,sources=resolve(scene,self_object if self_object is not None else obj,source[1],source[2],fx_count(t[1]),bindings or {},units,base=t[0],dims=t[1],offscreen_owner=owner)
        state.controls[g.name]=[read_source(value,depsgraph) for value in sources]
        state.control_bindings[g.name]=dict(requested_object=source[1],requested_item=source[2],
                                           object=_target.name if _target is not None else None)
    images={}
    material_image = encoded_image(image, native_upload=True, material_texture=True)
    state.material['image'] = material_image
    for g in program.globals.values():
        node_name={'MATERIALTEXTURE':'mmd_base_tex','MATERIALSPHEREMAP':'mmd_sphere_tex','MATERIALTOONTEXTURE':'mmd_toon_tex'}.get(g.semantic)
        node=nodes.get(node_name) if node_name and nodes else None
        if g.semantic=='MATERIALTEXTURE' and material_image:
            images[g.name]=material_image
        elif node and node.image:images[g.name]=encoded_image(node.image, native_upload=True)
    return state,images,technique


def texture_loader(path, *, material_texture=False):
    image=bpy.data.images.load(path,check_existing=False)
    try:return encoded_image(image, native_upload=True, material_texture=material_texture)
    finally:bpy.data.images.remove(image)
