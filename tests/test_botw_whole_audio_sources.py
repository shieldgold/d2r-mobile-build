"""Complete audio build inventory and prepared-source integrity; no execution."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("whole_audio_sources",
    ROOT / "experiments/botw_upstream_host/scripts/audio_sources.py")
audio = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audio)


class WholeAudioSourceTests(unittest.TestCase):
    def test_original_algorithm_closure_requires_every_renderer_and_adsp_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            renderer = root / "src/audio_core/renderer"
            adsp = root / "src/audio_core/adsp/apps/audio_renderer"
            for folder, count in ((renderer, 67), (adsp, 2)):
                folder.mkdir(parents=True)
                for i in range(count):
                    (folder / f"unit{i}.cpp").write_text("// owned closure fixture\n")
            manager = root / "src/audio_core/audio_render_manager.cpp"
            sink = root / "src/audio_core/sink/sink_stream.cpp"
            sink.parent.mkdir()
            for path in (manager, sink):
                path.write_text("// owned manager/sink fixture\n")
            self.assertEqual(len(audio.original_units(root)), 71)
            (renderer / "unit1.cpp").unlink()
            with self.assertRaisesRegex(ValueError, "incomplete"):
                audio.original_units(root)
            (renderer / "unit1.cpp").write_text("// restored\n")
            manager.unlink()
            with self.assertRaisesRegex(ValueError, "missing"):
                audio.original_units(root)

    def test_entire_component_inventory_rejects_extra_changed_and_symlinked_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            component = root / "audio"
            component.mkdir()
            source = component / "unit.cpp"
            source.write_text("// owned source\n")
            contract = {"component": "audio", "files": {"unit.cpp": audio.sha(source)}}
            audio.verify_inventory(root, contract)
            source.write_text("// changed\n")
            with self.assertRaisesRegex(ValueError, "source lock mismatch"):
                audio.verify_inventory(root, contract)
            source.write_text("// owned source\n")
            extra = component / "extra.h"
            extra.write_text("// unreviewed\n")
            with self.assertRaisesRegex(ValueError, "inventory"):
                audio.verify_inventory(root, contract)
            extra.unlink()
            outside = root / "outside.cpp"
            outside.write_bytes(source.read_bytes())
            source.unlink()
            source.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "source lock mismatch"):
                audio.verify_inventory(root, contract)

    def test_prepared_source_identity_rejects_changed_added_missing_and_symlinked_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "unit.cpp"
            source.write_text("// reviewed patch output\n")
            contract = {"prepared_source_sha256": {"unit.cpp": audio.sha(source)}}
            plan = {"prepared": root}
            audio.verify_prepared(plan, contract)
            source.write_text("// changed after compilation\n")
            with self.assertRaisesRegex(ValueError, "changed"):
                audio.verify_prepared(plan, contract)
            source.write_text("// reviewed patch output\n")
            extra = root / "extra.h"
            extra.write_text("// unreviewed\n")
            with self.assertRaisesRegex(ValueError, "changed"):
                audio.verify_prepared(plan, contract)
            extra.unlink()
            source.unlink()
            with self.assertRaisesRegex(ValueError, "changed"):
                audio.verify_prepared(plan, contract)


if __name__ == "__main__":
    unittest.main()
