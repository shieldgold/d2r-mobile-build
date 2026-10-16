"""Source-lock and compiler dependency integrity for the complete host build."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "experiments/botw_upstream_host/scripts/build_link_only.py"
spec = importlib.util.spec_from_file_location("whole_host_build", DRIVER)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class WholeHostBuildTests(unittest.TestCase):
    def test_only_selected_sdk_dependencies_may_be_outside_locked_component(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            component, staged, sdk = (root / p for p in ("component", "staged", "SDK"))
            for p in (component, staged, sdk):
                p.mkdir()
            original = component / "adapter.c"
            original.write_text("int platform(void);\n")
            upstream = staged / "upstream.c"
            upstream.write_text("int upstream(void);\n")
            settings = sdk / "SDKSettings.json"
            settings.write_text("{}\n")
            outside = root / "unlocked.h"
            outside.write_text("/* reject */\n")
            locked = {"adapter.c": build.sha(original), "upstream/upstream.c": build.sha(upstream)}
            sources, sdk_inputs = build.classify_dependencies({original, upstream, settings},
                staged, component, sdk, locked, {})
            self.assertEqual({p["component_path"] for p in sources}, set(locked))
            self.assertEqual(sdk_inputs, [{"sdk_relative_path": "SDKSettings.json", "sha256": build.sha(settings)}])
            with self.assertRaisesRegex(ValueError, "unlocked non-system"):
                build.classify_dependencies({outside}, staged, component, sdk, locked, {})
            external = {"unlocked.h": {"sha256": build.sha(outside)}}
            source_list, _ = build.classify_dependencies({outside}, staged, component, sdk,
                locked, {}, root, external)
            self.assertEqual(source_list[0]["repository_path"], "unlocked.h")
            outside.write_text("/* tampered */")
            with self.assertRaisesRegex(ValueError, "external source lock mismatch"):
                build.classify_dependencies({outside}, staged, component, sdk, locked, {}, root, external)
            original.write_text("/* changed adapter */")
            with self.assertRaisesRegex(ValueError, "compiled source lock mismatch"):
                build.classify_dependencies({original}, staged, component, sdk, locked, {})
            upstream.write_text("/* changed staged source */")
            with self.assertRaisesRegex(ValueError, "compiled source lock mismatch"):
                build.classify_dependencies({upstream}, staged, component, sdk, locked, {})

    def test_stage_revalidation_uses_patch_hashes_and_rejects_late_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            staged = Path(directory)
            source = staged / "unit.c"
            source.write_text("/* patched */")
            patched = {"unit.c": build.sha(source)}
            before = {"upstream/unit.c": "original hash differs"}
            build.verify_staged(staged, before, patched)
            source.write_text("/* late change */")
            with self.assertRaisesRegex(ValueError, "staged source lock mismatch"):
                build.verify_staged(staged, before, patched)

    def test_lock_rejects_changed_added_missing_and_symlinked_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            component = Path(directory)
            original = component / "source.c"
            original.write_text("int f(void) { return 1; }\n")
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            lock = {"files": {"source.c": {"sha256": digest}}}
            self.assertEqual(build.verify_sources(component, lock), {"source.c": digest})
            original.write_text("int f(void) { return 0; }\n")
            with self.assertRaisesRegex(ValueError, "source lock mismatch"):
                build.verify_sources(component, lock)
            original.unlink()
            with self.assertRaisesRegex(ValueError, "missing"):
                build.verify_sources(component, lock)
            original.write_text("int f(void) { return 1; }\n")
            extra = component / "hidden.h"
            extra.write_text("/* extra */")
            with self.assertRaisesRegex(ValueError, "added"):
                build.verify_sources(component, lock)
            extra.unlink()
            sibling = component.parent / (component.name + "-outside.c")
            try:
                sibling.write_bytes(original.read_bytes())
                original.unlink()
                original.symlink_to(sibling)
                with self.assertRaisesRegex(ValueError, "source lock mismatch"):
                    build.verify_sources(component, lock)
            finally:
                sibling.unlink(missing_ok=True)

    def test_compiler_dependencies_keep_spaces_and_continuations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unit.d"
            path.write_text("unit.o: /fixture/source.c \\\n /fixture/space\\ name.h /fixture/last.inc\n")
            self.assertEqual(build.parse_dependencies(path), [Path("/fixture/source.c"),
                Path("/fixture/space name.h"), Path("/fixture/last.inc")])
            path.write_text("no dependency rule")
            with self.assertRaises(ValueError):
                build.parse_dependencies(path)


if __name__ == "__main__":
    unittest.main()
