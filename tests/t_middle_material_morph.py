"""Animated MMD Tools material morphs on real Sanae geometry and original ToonDX."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer, model_object, resolve_textures, snapshot, position_camera, render_image
from t_middle_converters import set_scene, add_empty
from t_middle_vertex import field
from ninesync_fx.runtime.middle.targets import apply, remove, inspect_bindings, MATERIAL, TOKEN, _core_node
from ninesync_fx.runtime.middle.vertex import VERTEX_CORE
from ninesync_fx.nodes.material_compile import mme_tree_signature

FIXTURE = Path(__file__).parent / "fixtures" / "middle" / "material_morph.fx"
NAMES = ("FX Tint Mul", "FX Tint Add", "FX All Mul", "FX Tint Group")
FRAMES = {0: (0, 0, 0, 0), 10: (0.6, 0.4, 0.3, 0), 20: (0.2, 0.1, 0.5, 0.7),
          30: (0.9, 0.2, 0.2, 0.9)}
OFFSETS = (
    dict(index=1, kind=0, diffuse=(0.4, 0.7, 0.9, 0.8), ambient=(0.2, 0.6, 0.3),
         specular=(0.5, 0.8, 0.4), power=0.5, edge=(0.6, 0.7, 0.8, 0.9)),
    dict(index=1, kind=1, diffuse=(0.08, -0.04, 0.02, -0.08), ambient=(0.03, 0.02, -0.01),
         specular=(0.1, 0.05, 0.15), power=1.3, edge=(0.03, 0.01, 0.02, 0.01)),
    dict(index=-1, kind=0, diffuse=(0.8, 1.05, 0.9, 1), ambient=(0.9, 0.7, 0.9),
         specular=(0.8, 0.9, 0.7), power=0.8, edge=(0.9, 0.8, 0.9, 1)),
)


def prepare_model(pmx, model):
    """Add labeled color probes to the real model; never change creator files."""
    # An ASCII diagnostic title avoids native MMD's ANSI model-selector ambiguity.
    model.name = model.name_e = "Sanae-Morph-Probe"
    original_morphs = len(model.morphs)
    for name, spec in zip(NAMES, OFFSETS):
        morph = pmx.MaterialMorph(name=name, name_e=name, category=pmx.Morph.CATEGORY_OHTER)
        d = pmx.MaterialMorphOffset()
        d.index, d.offset_type = spec["index"], spec["kind"]
        d.diffuse_offset, d.ambient_offset = list(spec["diffuse"]), list(spec["ambient"])
        d.specular_offset, d.shininess_offset = list(spec["specular"]), spec["power"]
        d.edge_color_offset = list(spec["edge"])
        d.edge_size_offset = 1.0 if spec["kind"] == 0 else 0.0
        d.texture_factor = d.sphere_texture_factor = d.toon_texture_factor = [1.0 if spec["kind"] == 0 else 0.0]*4
        morph.offsets = [d]
        model.morphs.append(morph)
    group = pmx.GroupMorph(name=NAMES[3], name_e=NAMES[3], category=pmx.Morph.CATEGORY_OHTER)
    group.offsets = []
    for index, factor in ((original_morphs, 0.4), (original_morphs+1, 0.6)):
        offset = pmx.GroupMorphOffset()
        offset.morph, offset.factor = index, factor
        group.offsets.append(offset)
    model.morphs.append(group)
    return model


def expected(material, weights):
    result = dict(diffuse=np.array(material.diffuse), ambient=np.array(material.ambient),
                  specular=np.array(material.specular), power=np.array([material.shininess]),
                  edge=np.array(material.edge_color))
    factors = ((weights[0], weights[3]*0.4), (weights[1], weights[3]*0.6), (weights[2],))
    for index in (2, 0, 1):
        spec = OFFSETS[index]
        for key in result:
            value = np.array(spec[key])
            for weight in factors[index]:
                result[key] = (result[key]+value*weight if spec["kind"] else result[key]*(1+(value-1)*weight))
    result["ambient_diffuse"] = result["diffuse"][:3].copy()
    result["diffuse"][:3] = material.diffuse[:3]
    result["power"] = np.maximum(result["power"], 0.1)
    return result


def run(args):
    pmx, importer = load_importer(args.mmd_tools)
    scene = set_scene()
    # Cycles retains sufficient precision for the independent numeric color check.
    # The original ToonDX playback/render check below uses EEVEE separately.
    scene.render.engine = "CYCLES"
    scene.render.resolution_x = scene.render.resolution_y = 400
    scene.render.film_transparent = True
    scene.render.fps = 30
    digest = sha256(args.model.read_bytes()).hexdigest()
    model = prepare_model(pmx, pmx.load(str(args.model)))
    resolve_textures(model, args.model, args.model.parent / "textures")
    for texture in model.textures:
        path = Path(texture.path)
        texture.path = str(path if path.is_absolute() else args.model.parent / path)
    args.output.mkdir(parents=True, exist_ok=True)
    pmx.save(str(args.output / "Sanae-material-morph-diagnostic.pmx"), model)
    importer().execute(pmx=model, scale=0.08, types={"MESH", "ARMATURE", "MORPHS"}, fix_bone_order=False)
    obj = model_object(scene, model)
    position_camera(scene, obj)
    from mmd_tools.core.model import Model, FnModel
    root = FnModel.find_root_object(obj)
    rig = Model(root)
    rig.morph_slider.bind()
    slider = rig.morph_slider.placeholder(binded=True)
    assert slider is not None
    for frame, weights in FRAMES.items():
        for name, weight in zip(NAMES, weights):
            key = slider.data.shape_keys.key_blocks[name]
            key.value = weight
            key.keyframe_insert("value", frame=frame)
    scene.frame_set(0)
    bpy.context.view_layer.update()
    source = snapshot(obj)
    graph_signatures = {mat: mme_tree_signature(mat.node_tree) for mat in obj.data.materials if mat}
    source_graphs = {mat: (len(mat.node_tree.nodes), len(mat.node_tree.links),
                          len(mat.node_tree.animation_data.drivers) if mat.node_tree.animation_data else 0)
                     for mat in obj.data.materials if mat}
    base = obj.material_slots[1].material
    result = apply(obj, FIXTURE, MATERIAL, slot=1, units=0.08)
    assert not inspect_bindings(obj, MATERIAL, slot=1)
    reports = []

    def check(frame, offset_delta=0):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        wanted = expected(model.materials[1], FRAMES[frame])
        wanted["ambient_diffuse"][0] += offset_delta*(FRAMES[frame][1]+FRAMES[frame][3]*0.6)
        outputs = [wanted["diffuse"], wanted["ambient_diffuse"], wanted["ambient"],
                   np.r_[wanted["specular"], wanted["power"]], wanted["edge"]]
        instance = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = instance.to_mesh()
        try:
            chosen = np.repeat([p.material_index == 1 for p in mesh.polygons], [p.loop_total for p in mesh.polygons])
            errors = []
            for i, value in enumerate(outputs):
                actual = np.column_stack([field(mesh, result.core[VERTEX_CORE], f"TEXCOORD{i}.{j}")
                                          for j in range(len(value))])
                error = float(np.abs(actual[chosen]-value).max())
                assert error < 3e-6, (frame, i, actual[chosen][0], value, error)
                errors.append(error)
        finally:
            instance.to_mesh_clear()
        rgb = (wanted["diffuse"][:3]*2+wanted["ambient_diffuse"]*2+wanted["ambient"]*2+wanted["specular"]*2)*0.1
        rgb += wanted["edge"][:3]*0.1+wanted["power"][0]*0.002+wanted["diffuse"][3]*0.04+wanted["edge"][3]*0.03
        rgb += FRAMES[frame][3]*0.01
        linear = np.where(rgb <= 0.04045, rgb/12.92, ((rgb+0.055)/1.055)**2.4)
        image = render_image(scene, args.output, f"diagnostic-{frame}", frame)
        matched = ((np.abs(image[:, :, :3]-linear).max(axis=2) < 2e-5) & (image[:, :, 3] > 0.99)).sum()
        if matched <= 1000:
            unique, counts = np.unique(np.round(image[:, :, :3][image[:, :, 3] > 0.99], 5), axis=0, return_counts=True)
            print("MORPH_RENDER_DIAGNOSTIC", linear, unique[np.argsort(counts)[-5:]], counts[np.argsort(counts)[-5:]], flush=True)
        assert matched > 1000, (frame, matched, rgb)
        assert snapshot(obj) == source
        for mat, counts in source_graphs.items():
            assert counts == (len(mat.node_tree.nodes), len(mat.node_tree.links),
                              len(mat.node_tree.animation_data.drivers) if mat.node_tree.animation_data else 0)
        reports.append(dict(frame=frame, maximum_component_error=max(errors), matched_shader_pixels=int(matched),
                            expected_encoded_rgb=rgb.tolist(), offset_delta=offset_delta,
                            weights=FRAMES[frame],
                            expected_inputs={key: value.tolist() for key, value in wanted.items()}))

    for frame in FRAMES:
        check(frame)
    # Data edits stay live; no recompilation or mutation of the source graph is needed.
    offset = root.mmd_root.material_morphs[NAMES[1]].data[0]
    previous = offset.diffuse_color[0]
    offset.diffuse_color[0] = previous+0.05
    assert not apply(obj, FIXTURE, MATERIAL, slot=1, units=0.08).rebuilt
    check(20, 0.05)
    offset.diffuse_color[0] = previous
    check(20)
    # An invalid source binding must report the problem and keep the last good FX.
    old_name = offset.name
    offset.name = "missing-material-morph-binding"
    assert any("binding" in message for message in inspect_bindings(obj, MATERIAL, slot=1))
    groups = len(bpy.data.node_groups)
    try:
        apply(obj, FIXTURE, MATERIAL, slot=1, units=0.08)
    except Exception as error:
        assert "binding" in str(error)
    else:
        raise AssertionError("An invalid material morph binding was accepted")
    assert len(bpy.data.node_groups) == groups
    offset.name = old_name
    assert not inspect_bindings(obj, MATERIAL, slot=1)
    groups = len(bpy.data.node_groups)
    result = apply(obj, FIXTURE, MATERIAL, slot=1, units=0.08, force_rebuild=True)
    assert len(bpy.data.node_groups) == groups
    check(20)
    assert not apply(obj, FIXTURE, MATERIAL, slot=1, units=0.08).rebuilt
    bpy.ops.file.pack_all()
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "material-morphs.blend"), relative_remap=False)
    remove(obj, MATERIAL, slot=1)
    assert snapshot(obj) == source and obj.material_slots[1].material == base
    # Original ToonDX uses the same morphed material semantics without source edits.
    scene.render.engine = "BLENDER_EEVEE"
    light = add_empty(scene, "ToonDX light", (-0.65, -1, 0.85))
    mapping = {0:"顔",1:"肌",11:"髪",12:"服",13:"服",15:"服",16:"服",17:"髪2",18:"その他"}
    for slot, source_slot in enumerate(obj.material_slots):
        if source_slot.material.mmd_material.alpha > 0 and slot not in mapping:
            mapping[slot] = "顔"
    assert len(mapping) == 17
    toon_hashes = {}
    for slot, name in mapping.items():
        toon = args.toondx / (name + ".fx")
        toon_hashes[str(toon)] = sha256(toon.read_bytes()).hexdigest()
        apply(obj, toon, MATERIAL, slot=slot, units=0.08, light=light)
    first = render_image(scene, args.output, "original-toondx-base", 0)
    tinted = render_image(scene, args.output, "original-toondx-morph", 20)
    difference = float(np.abs(first-tinted).mean())
    assert difference > 0.001, difference
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "original-toondx-morphs.blend"), relative_remap=False)
    for slot in mapping:
        remove(obj, MATERIAL, slot=slot)
    assert snapshot(obj) == source
    scene.frame_set(0)
    bpy.context.view_layer.update()
    assert all(mme_tree_signature(mat.node_tree) == value for mat, value in graph_signatures.items())
    beam_results = []
    if args.beamman:
        names = {"NewToon", "NewToon2", "full_NoneTexFilter", "full_SimpleSoftShadow", "full_BeamManCustom"}
        files = [path for path in sorted(args.beamman.rglob("*.fx")) if path.stem in names]
        assert len(files) == 6
        for index, path in enumerate(files):
            sha = sha256(path.read_bytes()).hexdigest()
            folder = args.output / f"beamman-{index:02}-{path.stem}"
            folder.mkdir(exist_ok=True)
            for slot in mapping:
                apply(obj, path, MATERIAL, slot=slot, units=0.08, light=light)
            normal = render_image(scene, folder, "base", 0)
            changed = render_image(scene, folder, "morph", 20)
            delta = float(np.abs(changed-normal).mean())
            assert delta > 0.0001, (path, delta)
            bpy.ops.file.pack_all()
            bpy.ops.wm.save_as_mainfile(filepath=str(folder / "morphs.blend"), relative_remap=False)
            for slot in mapping:
                remove(obj, MATERIAL, slot=slot)
            assert snapshot(obj) == source
            scene.frame_set(0)
            bpy.context.view_layer.update()
            assert all(mme_tree_signature(mat.node_tree) == value for mat, value in graph_signatures.items())
            assert sha256(path.read_bytes()).hexdigest() == sha
            beam_results.append(dict(path=str(path), sha256=sha, blend=str(folder/"morphs.blend"),
                                     render_difference=delta))
        for item in beam_results:
            path = Path(item["blend"])
            bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
            reference = bpy.data.images.load(str(path.parent/"morph.exr"),check_existing=False)
            pixels = np.array(reference.pixels[:]).reshape(400,400,4)
            bpy.data.images.remove(reference)
            image = render_image(bpy.context.scene, path.parent, "reopened", 20)
            error = float(np.abs(image-pixels).mean())
            assert error < 1e-5, (path,error)
            item["reopen_error"] = error
    bpy.ops.wm.open_mainfile(filepath=str(args.output / "original-toondx-morphs.blend"), load_ui=False)
    reopened = render_image(bpy.context.scene, args.output, "original-toondx-reopened", 20)
    reopen_error = float(np.abs(reopened-tinted).mean())
    assert reopen_error < 1e-5, reopen_error
    bpy.ops.wm.open_mainfile(filepath=str(args.output / "material-morphs.blend"), load_ui=False)
    scene = bpy.context.scene
    obj = model_object(scene, model)
    mat = obj.material_slots[1].material
    result = type("SavedResult", (), {"core": _core_node(mat.node_tree, mat[TOKEN]).node_tree})()
    source = snapshot(obj)
    source_graphs = {}
    check(20)
    assert not inspect_bindings(obj, MATERIAL, slot=1)
    assert sha256(args.model.read_bytes()).hexdigest() == digest
    for name, value in toon_hashes.items():
        assert sha256(Path(name).read_bytes()).hexdigest() == value
    report = dict(source_model=str(args.model), sha256=digest, diagnostic_morphs=NAMES,
                  checks=reports, original_toondx_sha256=toon_hashes, original_toondx_slots=mapping,
                  original_toondx_render_difference=difference, source_graphs_preserved=True,
                  original_toondx_reopen_error=reopen_error, live_offset_edit_verified=True,
                  beamman=beam_results, source_graph_signatures_verified=True,
                  invalid_binding_rollback_verified=True, rollback_verified=True, reopen_verified=True)
    (args.output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("PASS REAL_PMX_MATERIAL_MORPHS", json.dumps(dict(frames=list(FRAMES),checks=len(reports),
          maximum_component_error=max(item["maximum_component_error"] for item in reports),
          toondx_presets=len(toon_hashes),slots=len(mapping),beamman_presets=len(beam_results),
          source_graph_signatures_verified=True,reopen_verified=True)), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--mmd-tools", type=Path, required=True)
    parser.add_argument("--toondx", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--beamman", type=Path)
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
