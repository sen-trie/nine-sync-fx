import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType
from unittest.mock import patch
import unittest


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("fx_experiment")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package

from fx_experiment.experimental_adapters.registry import load_registry, recognize
from fx_experiment.experimental_adapters.scan import FILE_LIMIT, scan

FIX = ROOT / "tests" / "fixtures"


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "package"
        shutil.copytree(FIX / "mme" / "known", self.root)
        self.entry = self.root / "tonal.fx"

    def test_exact_and_relocation(self):
        facts = scan(self.entry)
        self.assertEqual(facts, scan(FIX / "mme" / "known" / "tonal.fx"))
        self.assertEqual(recognize(facts).kind, "VERIFIED_EXACT")
        self.assertEqual(recognize(facts).adapter, "tonal")

    def test_comment_parameter_and_unrelated_logic(self):
        original = self.entry.read_bytes()
        for modified in (original + b"// same structure\n", original.replace(b"0.3", b"0.4"),
                         original.replace(b"Amount, 0.3, 0.6", b"1.0, Amount, 0.1")):
            with self.subTest(modified=modified):
                self.entry.write_bytes(modified)
                match = recognize(scan(self.entry))
                self.assertEqual(match.kind, "FAMILY_CANDIDATE")
                self.assertIsNone(match.adapter)
        self.entry.write_text("technique Empty {}")
        self.assertEqual(recognize(scan(self.entry)).kind, "UNKNOWN")

    def test_include_changes_closure(self):
        before = scan(self.entry)
        (self.root / "settings.fxh").write_text("float Amount = 0.5;")
        after = scan(self.entry)
        self.assertNotEqual(before.closure, after.closure)
        self.assertIn("settings.fxh", recognize(after).reasons[0])

    def test_sibling_entrypoints_and_relative_includes(self):
        (self.root / "other").mkdir()
        (self.root / "other" / "settings.fxh").write_text("float Offset = 0;")
        other = self.root / "other" / "tonal.fx"
        other.write_bytes(self.entry.read_bytes().replace(b"Amount", b"Offset"))
        first, second = scan(self.entry), scan(other, self.root)
        self.assertNotEqual(first.closure, second.closure)
        self.assertEqual(second.parameters, (("Offset", "float"),))
        entries = load_registry()
        for facts in (first, second):
            entries[facts.fingerprint] = dict(next(iter(entries.values())), entry=facts.entry, files=facts.files)
        self.assertEqual(recognize(first, entries).entry["entry"], "tonal.fx")
        self.assertEqual(recognize(second, entries).entry["entry"], "other/tonal.fx")

    def test_incomplete_and_bounded(self):
        for source in ('#include "lost.fxh"', '#include "tonal.fx"', '#include "bad',
                       '#include "../outside.fx"', "float4 broken(", '#if 9 ** 99999\n#endif',
                       'texture t < string ResourceName="lost.png"; >;'):
            with self.subTest(source=source):
                self.entry.write_text(source)
                facts = scan(self.entry)
                self.assertEqual(recognize(facts).kind, "INCOMPLETE")
                self.assertIsNone(facts.fingerprint)
        with (self.root / "large.fx").open("wb") as handle:
            handle.truncate(FILE_LIMIT + 1)
        self.assertIn("Size limit", scan(self.entry).errors[0])

    def test_link_rejected(self):
        external = self.root.parent / "external.fx"
        external.touch()
        try:
            (self.root / "escape.fx").symlink_to(external)
        except OSError as error:
            if os.name != "nt" or error.winerror != 1314:
                raise
            self.skipTest("Windows disabled symbolic link creation")
        self.assertIn("Linked package entry", scan(self.entry).errors[0])

    def test_roles_and_multipass(self):
        self.assertEqual(scan(FIX / "mme" / "triangle" / "triangle.x").role, "ACCESSORY_GEOMETRY")
        self.assertEqual(scan(FIX / "Synth" / "synth.x").role, "PARTICLE")
        self.assertEqual(scan(FIX / "mme" / "controlled" / "surface.fx").role, "MODEL_MATERIAL")
        shutil.copy(FIX / "mme" / "triangle" / "triangle.x", self.entry.with_suffix(".x"))
        self.entry.write_text(
            'texture Cut : RENDERCOLORTARGET; technique Multi < string Script = '
            '"RenderColorTarget=Cut;Pass=Mask;RenderColorTarget=;Pass=Glow;"; > '
            '{ pass Mask < string Script="Draw=Buffer;"; > {} '
            'pass Glow < string Script="Draw=Buffer;"; > {} }')
        facts = scan(self.entry.with_suffix(".x"))
        self.assertEqual(facts.role, "POST_CONTROLLER")
        self.assertIn("render_targets", facts.features)
        self.assertEqual(recognize(facts).kind, "UNKNOWN")

    def test_registry_policy(self):
        facts = scan(self.entry)
        entries = load_registry()
        entry = entries[facts.fingerprint]
        for status, expected in (("review", "BLOCKED_FOR_REVIEW"), ("incompatible", "INCOMPATIBLE")):
            entry["status"] = status
            result = recognize(facts, entries)
            self.assertEqual(result.kind, expected)
            self.assertIsNone(result.adapter)
        corrupt = self.root.parent / "registry.json"
        corrupt.write_text('{"schema":1,"schema":1,"entries":[]}')
        with self.assertRaisesRegex(ValueError, "Duplicate registry field"):
            load_registry(corrupt)
        with patch("fx_experiment.experimental_adapters.registry.load_registry",
                   side_effect=ValueError("Invalid registry")):
            match = recognize(facts)
        self.assertEqual(match.kind, "BLOCKED_FOR_REVIEW")
        self.assertIsNone(match.adapter)

    @unittest.skipUnless(os.getenv("NINESYNC_TONEMAP"), "Set NINESYNC_TONEMAP to the original unpacked package")
    def test_creator_package(self):
        path = Path(os.environ["NINESYNC_TONEMAP"])
        for ext in (".fx", ".x"):
            self.assertEqual(recognize(scan(path / f"o_Tonemap{ext}")).kind, "VERIFIED_EXACT")
        self.assertIsNone(recognize(scan(path / "o_Plain.fx")).adapter)
        shutil.copytree(path, self.root, dirs_exist_ok=True)
        for extra in ("tonal.fx", "settings.fxh"):
            (self.root / extra).unlink()
        edited = self.root / "o_Tonemap.fx"
        self.assertEqual(recognize(scan(edited)).kind, "VERIFIED_EXACT")
        with edited.open("ab") as handle:
            handle.write(b" ")
        self.assertIsNone(recognize(scan(edited)).adapter)
        shutil.copyfile(path / "o_Tonemap.fx", edited)
        (self.root / "bitmap" / "Plain.bmp").unlink()
        self.assertEqual(recognize(scan(edited)).kind, "INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
