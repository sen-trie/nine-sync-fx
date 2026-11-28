import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mme.package import scan_package


FIX = ROOT / "tests" / "fixtures" / "mme"


class AnalyzerTests(unittest.TestCase):
    def test_textured_geometry(self):
        report = scan_package(FIX / "triangle")
        self.assertEqual(report["target_kinds"], ["ACCESSORY_GEOMETRY"])
        self.assertEqual(report["package_modes"], ["A1"])
        self.assertEqual(report["meshes"][0]["textures"][0]["path"], "albedo.tga")
        effect = report["effects"][0]
        self.assertEqual(effect["stage"], "PARSED")
        self.assertEqual(effect["verification"], "unverified")
        self.assertEqual(effect["compatibility_prediction"], "A")
        self.assertEqual(effect["techniques"][0]["passes"][0]["reads"], ["albedo", "surface", "transform"])
        self.assertEqual(effect["draws"][0]["kind"], "geometry")
        self.assertEqual(effect["dry_run"], "draws_planned")
        self.assertEqual(scan_package(FIX / "triangle", probe=True)["effects"][0]["dry_run"], "sample_evaluated")

    def test_animated_texture_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(FIX / "triangle", root, dirs_exist_ok=True)
            shader = root / "triangle.fx"
            shader.write_text("texture unused : ANIMATEDTEXTURE;\n" +
                              shader.read_text().replace("MATERIALTEXTURE", "ANIMATEDTEXTURE"))
            effect = scan_package(root)["effects"][0]
            self.assertEqual(effect["unsupported"], ["albedo: animated texture samples return black"])

    def test_probe_failure_keeps_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(FIX / "triangle", root, dirs_exist_ok=True)
            shader = root / "triangle.fx"
            shader.write_text(shader.read_text().replace("tex2D(surface, input.uv)", "unavailable(input.uv)"))
            effect = scan_package(root, probe=True)["effects"][0]
            self.assertEqual(effect["stage"], "PARSED")
            self.assertEqual(effect["dry_run"], "failed")
            self.assertTrue(any("unavailable" in w and "Surface" in w for w in effect["warnings"]))

    def test_controller_and_assigned_shader(self):
        report = scan_package(FIX / "controlled")
        self.assertEqual(report["package_modes"], ["A3", "A4"])
        self.assertEqual(report["controller_files"], ["handle.x"])
        self.assertEqual(report["effects"][0]["target_kinds"], ["MODEL_MATERIAL"])
        self.assertEqual(report["effects"][0]["dependencies"], ["D0", "D2"])
        self.assertEqual(report["effects"][0]["draws"][0]["pass"], "Surface")

    def test_looped_and_unpaired_shaders(self):
        report = scan_package(FIX.parent / "Synth")
        effects = {e["path"]: e for e in report["effects"]}
        self.assertEqual(effects["synth.fx"]["package_modes"], ["A2"])
        self.assertEqual(effects["synth.fx"]["draws"][0]["loops"], [("index", 4, "Copies")])
        self.assertEqual(effects["probe.fx"]["target_kinds"], ["UNKNOWN"])
        self.assertNotIn("D2", effects["synth.fx"]["dependencies"])

    def test_post_with_mesh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(FIX / "triangle" / "triangle.x", root / "screen.x")
            (root / "screen.fx").write_text(
                'texture capture : RENDERCOLORTARGET;\n#include "draw.fxh"\n')
            (root / "draw.fxh").write_text(
                'technique T < string Script = "RenderColorTarget=capture;ScriptExternal=Color;'
                'RenderColorTarget=;Pass=Image;"; > { pass Image < string Script="Draw\t= Buffer;"; > {} }')
            effect = scan_package(root, probe=True)["effects"][0]
            self.assertEqual(effect["includes"], ["draw.fxh"])
            self.assertEqual(effect["package_modes"], ["A5"])
            self.assertEqual(effect["dependencies"], ["D0", "D3", "D4"])
            self.assertEqual(effect["draws"][0]["kind"], "buffer")
            self.assertEqual(effect["dry_run"], "not_run")
            self.assertIn("scriptexternal=Color", effect["unsupported"])

    def test_incomplete_files_do_not_hide_other_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(FIX / "triangle", root / "ok")
            (root / "broken.fx").write_text('#if 0\n#include "unused.fxh"\n#endif\n#include "lost.fxh"\n#include "two.fxh"')
            (root / "cut.fx").write_text("float4 tint : register(")
            (root / "odd.emd").touch()
            effects = {e["path"]: e for e in scan_package(root)["effects"]}
            self.assertEqual(effects["broken.fx"]["missing_includes"], [
                {"folder": ".", "name": "lost.fxh"}, {"folder": ".", "name": "two.fxh"}])
            self.assertEqual(effects["cut.fx"]["stage"], "FILES_PRESENT")
            self.assertIn("unexpected end", effects["cut.fx"]["warnings"][0])
            self.assertIsNone(effects["cut.fx"]["compatibility_prediction"])
            self.assertEqual(effects["ok/triangle.fx"]["stage"], "PARSED")

    def test_binary_mesh_and_resource_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "s.x").write_bytes(b"xof 0303bin 0032")
            (root / "s.fx").write_text('texture a <string ResourceName="lost.png";>; technique T {pass P {}}')
            effect = scan_package(root)["effects"][0]
            self.assertIn("binary/compressed .x effect geometry", effect["unsupported"])
            self.assertEqual(effect["resources"][0], {"name": "lost.png", "path": None})
            self.assertEqual(effect["dry_run"], "not_run")

    def test_offscreen_routing_and_mixed_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(FIX / "triangle", root, dirs_exist_ok=True)
            with (root / "triangle.fx").open("a") as shader:
                shader.write('texture scene : OFFSCREENRENDERTARGET < string DefaultEffect="self=another.fx;"; >;'
                             'technique Alternate { pass Screen < string Script="Draw=Buffer;"; > {} }')
            (root / "another.fx").write_text("technique Empty {}")
            report = scan_package(root)
            effect = next(e for e in report["effects"] if e["mesh"])
            self.assertEqual(len(effect["draws"]), 1)
            self.assertEqual(len(effect["techniques"]), 2)
            self.assertEqual(effect["offscreen_effects"][0]["path"], "another.fx")
            self.assertEqual(effect["package_modes"], ["A1", "A5"])
            self.assertIn("A6", report["package_modes"])
            self.assertEqual(effect["compatibility_prediction"], "B")
            self.assertTrue(effect["partial"])

    def test_post_only_effect_is_not_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "screen.fx").write_text(
                'technique T < string Script = "Pass=Image;"; > { pass Image < string Script="Draw=Buffer;"; > {} }')
            effect = scan_package(root)["effects"][0]
            self.assertEqual(effect["compatibility_prediction"], "D")
            self.assertFalse(effect["partial"])
            self.assertEqual(effect["skipped_draws"], ["T.Image"])

    def test_sphere_map_texture_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(FIX / "triangle", root, dirs_exist_ok=True)
            mesh = root / "triangle.x"
            mesh.write_text(mesh.read_text().replace('"albedo.tga"', '"albedo.tga*shine.sph"'))
            textures = scan_package(root)["meshes"][0]["textures"]
            self.assertEqual([t["name"] for t in textures], ["albedo.tga", "shine.sph"])
            self.assertEqual(textures[0]["path"], "albedo.tga")
            self.assertIsNone(textures[1]["path"])

    def state_shader(self, root, state_reads_itself):
        shutil.copytree(FIX / "triangle", root, dirs_exist_ok=True)
        shader = root / "triangle.fx"
        source = shader.read_text().replace("Texture = <albedo>;", "Texture = <state>;").replace(
            "technique Object {", 'technique Object < string Script = "RenderColorTarget0=state;Pass=Step;'
            'RenderColorTarget0=;Pass=Surface;"; > {')
        step = "tex2D(surface, input.uv)" if state_reads_itself else "float4(1, 0, 0, 1)"
        source = source.replace("float4 ps(", "float4 step(Vertex input) : COLOR0 { return %s; }\nfloat4 ps(" % step)
        source = source.replace("    pass Surface {", '    pass Step < string Script = "Draw=Buffer;"; > {\n'
                                "        PixelShader = compile ps_3_0 step();\n    }\n    pass Surface {")
        shader.write_text("texture state : RENDERCOLORTARGET;\n" + source)

    def test_geometry_reading_buffer_state_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.state_shader(root, False)
            effect = scan_package(root, probe=True)["effects"][0]
            self.assertEqual(effect["compatibility_prediction"], "D")
            self.assertFalse(effect["partial"])
            self.assertIn("Object.Surface: geometry reads state, computed by a buffer pass", effect["unsupported"])
            self.assertNotIn("D5", effect["dependencies"])
            self.assertEqual(effect["dry_run"], "not_run")

    def test_frame_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.state_shader(root, True)
            effect = scan_package(root)["effects"][0]
            self.assertIn("D5", effect["dependencies"])
            self.assertTrue(any("frame feedback" in u for u in effect["unsupported"]))

    def test_roles_controllers_and_paired_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sub").mkdir()
            (root / "Ctrl.pmx").touch()
            (root / "Smoke.pmx").touch()
            (root / "part.fx").write_text("float tint;\n")
            (root / "other.fx").write_text("technique Hidden {}\n")
            (root / "sub" / "main.fx").write_text(
                '#include "../part.fx"\n'
                'float size : CONTROLOBJECT < string name = "Ctrl.pmx"; string item = "Si"; >;\n'
                'texture rt : OFFSCREENRENDERTARGET < string DefaultEffect = "self=hide; *=../other.fx;"; >;\n'
                'float4 ps() : COLOR0 { return size; }\n'
                'technique T { pass P { PixelShader = compile ps_3_0 ps(); } }\n')
            (root / "Smoke.fx").write_text('technique T { pass P {} }\n')
            report = scan_package(root)
            effects = {e["path"]: e for e in report["effects"]}
            self.assertEqual(effects["part.fx"]["role"], "include")
            self.assertEqual(effects["other.fx"]["role"], "offscreen_companion")
            self.assertEqual(effects["sub/main.fx"]["controllers"][0]["package_file"], "Ctrl.pmx")
            self.assertFalse(any("External controller" in w for w in effects["sub/main.fx"]["warnings"]))
            self.assertEqual(effects["Smoke.fx"]["paired_model"], "Smoke.pmx")
            self.assertIn("PAIRED_MODEL", effects["Smoke.fx"]["target_kinds"])
            self.assertEqual(report["controller_files"], ["Ctrl.pmx"])

    def test_leading_separator_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(FIX / "triangle" / "albedo.tga", root / "mask.tga")
            (root / "m.fx").write_text('texture a <string ResourceName="\\\\mask.tga";>; technique T {pass P {}}')
            self.assertEqual(scan_package(root)["effects"][0]["resources"][0]["path"], "mask.tga")

    def test_cli(self):
        result = subprocess.run([sys.executable, "-m", "tools.mme", str(FIX / "triangle"), "--source", "local"],
                                cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["source"], "local")
        self.assertNotIn("bpy", sys.modules)


if __name__ == "__main__":
    unittest.main()
