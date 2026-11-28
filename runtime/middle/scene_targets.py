"""Explicit, saved scene-target execution for the first offscreen renderer subset.

Pass scenes own their draw copies and FX graphs. Creator meshes/rigs/materials
remain shared, unchanged inputs. Targets are published only after all draws bake.
"""
from array import array
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import bpy

from ...fx.annotations import fx_annotation
from ...fx.errors import FxUnsupported
from ...fx.loader import fx_parse_file
from .pass_context import PassContext
from .targets import apply, remove, MATERIAL, OWNER, ORIGINAL, TOKEN
from .vertex import VERTEX_OWNER

TAG = "ninesync_scene_target"


@contextmanager
def active_scene(scene):
    # Render operators and animation handlers consult the window scene even
    # when a scene name is supplied. Make the pass context current transiently.
    window = bpy.context.window
    previous = window.scene if window else None
    if window:
        window.scene = scene
    try:
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            yield
    finally:
        if window:
            window.scene = previous


@dataclass
class SceneTarget:
    scene: object
    image: object
    name: str

    @property
    def owner(self):
        return self.scene["ninesync_target_owner"]


def _link(scene, obj):
    if scene.objects.get(obj.name) != obj:
        scene.collection.objects.link(obj)


def _dependencies(scene, obj):
    root = obj
    while root.parent:
        root = root.parent
    for item in (root, *root.children_recursive):
        if item.type != "MESH" or getattr(item, "mmd_type", "") == "PLACEHOLDER":
            _link(scene, item)
    for mod in obj.modifiers:
        target = getattr(mod, "object", None)
        if target:
            _link(scene, target)


def _new_scene(name, width, height, clear):
    scene = bpy.data.scenes.new(name)
    scene[TAG] = uuid4().hex
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.dither_intensity = 0
    scene.eevee.taa_render_samples = 1
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.world = bpy.data.worlds.new(name + " clear")
    scene.world[TAG] = scene[TAG]
    scene.world.use_nodes = True
    # D3DCOLOR clear conversion truncates normalized channels to bytes.
    encoded = [int(max(0,min(1,c))*255)/255 for c in clear[:3]]
    rgb = [c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4 for c in encoded]
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (*rgb,1)
    return scene


def create(source_scene, owner, owner_fx, name, camera, draws, *, units=.08, controls=()):
    """Create one A8R8G8B8 target with explicit model->FX draw assignments.

    DefaultEffect text is retained as provenance; participating objects are an
    explicit mapping for this subset. No scene-wide wildcard import is inferred.
    """
    if source_scene.objects.get(owner.name) != owner or owner.type != "EMPTY":
        raise FxUnsupported("This offscreen subset needs an existing accessory owner Empty in the source scene.")
    if camera is None or camera.type != "CAMERA":
        raise FxUnsupported("A scene target requires an explicit camera.")
    program = fx_parse_file(str(owner_fx))
    target = program.globals.get(name)
    if not target or target.semantic != "OFFSCREENRENDERTARGET":
        raise FxUnsupported(f"{name!r} is not an OFFSCREENRENDERTARGET declaration.")
    fmt = str(fx_annotation(target,"Format",program,"A8R8G8B8")).upper().removeprefix("D3DFMT_")
    if fmt != "A8R8G8B8":
        raise FxUnsupported(f"Scene-target format {fmt!r} needs a verified numeric render path.")
    allowed = {"width", "height", "format", "clearcolor", "cleardepth", "antialias", "defaulteffect",
               "uiname", "uihelp", "uiwidget", "description"}
    unknown = {key for key in target.ann if key.lower() not in allowed}
    if unknown:
        raise FxUnsupported("Unsupported scene-target annotations: " + ", ".join(sorted(unknown)))
    width,height = (int(fx_annotation(target,key,program,256)) for key in ("Width","Height"))
    clear = tuple(fx_annotation(target,"ClearColor",program,[0,0,0,1]))
    if not 16 <= width <= 4096 or not 16 <= height <= 4096 or len(clear)!=4 or clear[3]!=1:
        raise FxUnsupported("The first scene-target subset requires 16..4096 dimensions and opaque clear alpha.")
    if float(fx_annotation(target,"ClearDepth",program,1)) != 1:
        raise FxUnsupported("Custom scene-target clear depth is not implemented.")
    if fx_annotation(target,"AntiAlias",program,False):
        raise FxUnsupported("Multisampled scene targets need a verified resolve path.")
    if not draws:
        raise FxUnsupported("Supply at least one actual model draw assignment.")
    scene = _new_scene(f"MME target {owner.name} | {name}",width,height,clear)
    scene.render.fps = source_scene.render.fps
    scene.render.fps_base = source_scene.render.fps_base
    image = bpy.data.images.new(f"MME target {owner.name} | {name}",width,height,alpha=True,float_buffer=True)
    image.colorspace_settings.name = "Non-Color"
    image.alpha_mode = "STRAIGHT"
    image[TAG] = scene[TAG]
    scene["ninesync_target_owner"] = owner
    scene["ninesync_target_image"] = image
    scene["ninesync_target_name"] = name
    scene["ninesync_target_fx"] = str(Path(owner_fx).resolve())
    scene["ninesync_target_source_scene"] = source_scene
    scene["ninesync_target_default_effect"] = str(fx_annotation(target,"DefaultEffect",program,""))
    scene["ninesync_target_units"] = units
    scene["ninesync_target_draw_count"] = 0
    _link(scene,camera)
    scene.camera = camera
    _link(scene,owner)
    _dependencies(scene,owner)
    for control in controls:
        if source_scene.objects.get(control.name)!=control or control.type!="EMPTY":
            discard(SceneTarget(scene,image,name))
            raise FxUnsupported("Explicit pass controls must be existing source-scene Empties.")
        _link(scene,control)
        _dependencies(scene,control)
    handle = SceneTarget(scene,image,name)
    try:
        for index,(source,fx) in enumerate(draws):
            if source.type != "MESH" or source_scene.objects.get(source.name)!=source:
                raise FxUnsupported("Scene-target draws must reference meshes in the source scene.")
            clone = source.copy()
            clone.name = f"MME draw {owner.name} | {source.name}"
            clone[TAG] = scene[TAG]
            _link(scene,clone)
            _dependencies(scene,source)
            # Strip only this adapter's main-pass stages, retaining artist modifiers.
            for mod in list(clone.modifiers):
                if mod.type=="NODES" and mod.node_group and (mod.node_group.get(TOKEN) or mod.node_group.get(VERTEX_OWNER)):
                    clone.modifiers.remove(mod)
            for slot in clone.material_slots:
                original = slot.material.get(ORIGINAL,slot.material) if slot.material else None
                slot.link = "OBJECT"
                slot.material = original
            scene[f"ninesync_draw_{index}"] = clone
            scene[f"ninesync_source_{index}"] = source
            scene[f"ninesync_fx_{index}"] = str(Path(fx).resolve())
            scene["ninesync_target_draw_count"] = index+1
            used = {p.material_index for p in clone.data.polygons}
            for slot in sorted(used):
                apply(clone,fx,MATERIAL,scene=scene,slot=slot,units=units,
                      pass_context=PassContext(scene,owner=owner))
        return handle
    except Exception:
        discard(handle)
        raise


def restore(scene):
    if not scene.get(TAG) or "ninesync_target_image" not in scene:
        raise FxUnsupported("This is not an owned scene-target pass.")
    return SceneTarget(scene,scene["ninesync_target_image"],scene["ninesync_target_name"])


def bake(targets, frame, directory):
    """Render real geometry, then publish encoded target pixels atomically.

    This explicit frame operation also works after reopening a saved file. It
    does not install a hidden per-frame handler or claim feedback support.
    """
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    pending=[]
    source_frames={h.scene["ninesync_target_source_scene"]:h.scene["ninesync_target_source_scene"].frame_current for h in targets}
    try:
        for handle in targets:
            scene = handle.scene
            PassContext(scene,owner=handle.owner).validate(scene)
            with active_scene(scene):
                scene.frame_set(frame)
                # Revalidate source graphs before drawing, keeping failures transactional.
                for index in range(scene["ninesync_target_draw_count"]):
                    clone,fx = scene[f"ninesync_draw_{index}"],scene[f"ninesync_fx_{index}"]
                    for slot in sorted({p.material_index for p in clone.data.polygons}):
                        apply(clone,fx,MATERIAL,scene=scene,slot=slot,units=scene["ninesync_target_units"],
                              pass_context=PassContext(scene,owner=handle.owner))
                scene.render.filepath=str(directory/f"{scene[TAG]}-{frame}.png")
                bpy.ops.render.render(write_still=True,scene=scene.name)
            temporary=bpy.data.images.load(scene.render.filepath,check_existing=False)
            try:
                pixels=array("f",[0])*len(temporary.pixels)
                temporary.pixels.foreach_get(pixels)
                pending.append((handle,pixels,scene.render.filepath))
            finally:
                bpy.data.images.remove(temporary)
        for handle,pixels,path in pending:
            handle.image.pixels.foreach_set(pixels)
            handle.image.update()
            handle.image["ninesync_target_frame"]=frame
            handle.image["ninesync_target_capture"]=path
        return tuple(path for _handle,_pixels,path in pending)
    finally:
        for scene,old_frame in source_frames.items():
            scene.frame_set(old_frame)


def composite(targets, owner_effects, *, width=512, height=512):
    """Compile original fullscreen buffer VS/PS through the material backend."""
    scene=_new_scene("MME target composite",width,height,(0,0,0,1))
    if targets:
        scene.render.fps = targets[0].scene.render.fps
        scene.render.fps_base = targets[0].scene.render.fps_base
    camera_data=bpy.data.cameras.new("MME composite camera")
    camera_data.type="ORTHO"
    camera_data.ortho_scale=2
    camera=bpy.data.objects.new("MME composite camera",camera_data)
    camera.location=(0,0,3)
    camera[TAG]=scene[TAG]
    _link(scene,camera)
    scene.camera=camera
    try:
        for index,(handle,fx) in enumerate(zip(targets,owner_effects,strict=True)):
            mesh=bpy.data.meshes.new(f"MME screen {index}")
            mesh.from_pydata([(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)],[],[(0,1,2,3)])
            uv=mesh.uv_layers.new()
            for item,value in zip(uv.data,((0,0),(1,0),(1,1),(0,1))):item.uv=value
            plane=bpy.data.objects.new(f"MME screen {index}",mesh)
            plane[TAG]=scene[TAG]
            plane.location.z=index*.001
            _link(scene,plane)
            material=bpy.data.materials.new(f"MME screen source {index}")
            material.use_nodes=True
            material[TAG]=scene[TAG]
            mesh.materials.append(material)
            apply(plane,fx,MATERIAL,scene=scene,slot=0,units=1,
                  pass_context=PassContext(scene,images={handle.name:handle.image},screen=True))
        return scene
    except Exception:
        discard_composite(scene)
        raise


def discard_composite(scene):
    world=scene.world
    for obj in list(scene.objects):
        if obj.get(TAG)!=scene.get(TAG):continue
        if obj.type=="MESH":
            original=obj.data.materials[0]
            remove(obj,MATERIAL,slot=0,scene=scene)
            mesh=obj.data
            bpy.data.objects.remove(obj,do_unlink=True)
            bpy.data.meshes.remove(mesh)
            if not original.users:bpy.data.materials.remove(original)
        elif obj.type=="CAMERA":
            data=obj.data
            bpy.data.objects.remove(obj,do_unlink=True)
            bpy.data.cameras.remove(data)
    bpy.data.scenes.remove(scene)
    if world and not world.users:bpy.data.worlds.remove(world)


def discard(handle):
    scene=handle.scene
    world=scene.world
    for obj in list(scene.objects):
        if obj.get(TAG)!=scene.get(TAG):continue
        for index,slot in enumerate(obj.material_slots):
            if slot.material and slot.material.get(OWNER)==obj:remove(obj,MATERIAL,scene=scene,slot=index)
        bpy.data.objects.remove(obj,do_unlink=True)
    image=handle.image
    bpy.data.scenes.remove(scene)
    if not image.users:bpy.data.images.remove(image)
    if world and not world.users:bpy.data.worlds.remove(world)
