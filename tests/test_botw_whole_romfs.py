"""Real upstream FS + real native reader, only self-owned CPU image fixtures."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from test_botw_romfs import fixture

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments/botw_upstream_host"
BASELINE = os.environ.get("BOTW_WHOLE_ROMFS_BASELINE") == "1"
SANITIZE = os.environ.get("BOTW_WHOLE_ROMFS_SANITIZE") == "1"


@unittest.skipUnless(sys.platform == "darwin", "upstream FS uses Apple clonefile and Mach-O HLE sections")
class WholeRomFS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="botw-whole-romfs-cpu-")
        cls.work = Path(cls.temp.name)
        cls.source = cls.work / "source"
        shutil.copytree(EXP / "upstream/dm/host", cls.source / "dm/host")
        if not BASELINE:
            subprocess.run(["patch", "-p1", "-i", str(EXP / "patches/romfs-image-provider.patch")],
                           cwd=cls.source, check=True, capture_output=True, text=True)
        cls.image = cls.work / "owned-small.romfs.bin"
        cls.image.write_bytes(fixture()[0])
        cls.binary = cls.work / "probe"
        cmd = ["xcrun", "clang", "-std=gnu11", "-g", "-O1", "-Wno-format", "-Wno-unused-function",
               "-I", str(cls.source / "dm/host"), "-I", str(EXP / "adapter"),
               "-I", str(ROOT / "native/botw_resources"), str(EXP / "tests/whole_romfs_probe.c"),
               "-o", str(cls.binary)]
        if not BASELINE:
            cmd += ["-DBOTW_ROMFS_ADAPTED=1", str(EXP / "adapter/whole_romfs.c"),
                    str(ROOT / "native/botw_resources/romfs.c")]
        if SANITIZE:
            cmd += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_real_mountrom_accepts_regular_romfs_image(self):
        run = subprocess.run([str(self.binary), str(self.image), "mount"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("actual upstream MountRom accepted", run.stdout)

    def run_case(self, mode, image=None):
        owned = self.work / (mode + ".romfs.bin")
        owned.write_bytes(image if image is not None else self.image.read_bytes())
        run = subprocess.run([str(self.binary), str(owned), mode], capture_output=True, text=True,
                             env=dict(os.environ, ASAN_OPTIONS="detect_leaks=0:halt_on_error=1", UBSAN_OPTIONS="halt_on_error=1"))
        self.assertEqual(run.returncode, 0, run.stderr)

    @unittest.skipIf(BASELINE, "original FS has no image provider")
    def test_file_read_size_eof_close_and_generations(self):
        self.run_case("files")

    @unittest.skipIf(BASELINE, "original FS has no image provider")
    def test_directory_iteration_filter_count_and_cursor(self):
        self.run_case("directory")

    @unittest.skipIf(BASELINE, "original FS has no image provider")
    def test_readonly_mutation_family_and_save_commit_reject(self):
        self.run_case("readonly")

    @unittest.skipIf(BASELINE, "original FS has no image provider")
    def test_unmount_invalidates_file_and_directory_handles(self):
        self.run_case("stale")

    @unittest.skipIf(BASELINE, "original FS has no image provider")
    def test_path_normalization_validation_and_type_checks(self):
        self.run_case("paths")

    @unittest.skipIf(BASELINE, "original FS has no image provider")
    def test_truncated_owned_image_is_real_read_error(self):
        self.run_case("truncate")

    @unittest.skipIf(BASELINE, "original FS has no image provider")
    def test_native_bounded_reader_handles_more_than_one_chunk(self):
        self.run_case("large", fixture([("/Large.bin", b"x" * (17 * 1024 * 1024 + 7))])[0])

    @unittest.skipIf(BASELINE, "original FS has no image provider")
    def test_save_transaction_and_uid_source_blocks_remain_exact(self):
        original = (EXP / "upstream/dm/host/botw_fs.c").read_text()
        adapted = (self.source / "dm/host/botw_fs.c").read_text()
        begin = "// ---------- Save data ----------"
        end = "// ---------- Files ----------"
        self.assertEqual(original.split(begin, 1)[1].split(end, 1)[0],
                         adapted.split(begin, 1)[1].split(end, 1)[0])

    @unittest.skipIf(BASELINE, "original FS has no image provider")
    def test_malformed_owned_image_fails_without_publishing_mount(self):
        owned = self.work / "malformed.romfs.bin"
        owned.write_bytes(b"not a RomFS" + bytes(128))
        run = subprocess.run([str(self.binary), str(owned), "mount"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertIn("invalid RomFS image", run.stderr)

    @unittest.skipIf(BASELINE, "original FS has no image provider")
    def test_existing_directory_provider_uses_same_actual_services(self):
        directory = self.work / "owned-directory"
        (directory / "System").mkdir(parents=True)
        (directory / "Bootup.pack").write_bytes(b"boot data")
        (directory / "System/Actor.byml").write_bytes(b"actor data")
        run = subprocess.run([str(self.binary), str(directory), "files"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    @unittest.skipIf(BASELINE, "original FS has no image provider")
    def test_directory_emitter_error_advances_only_successful_entries(self):
        self.run_case("emit-error")

    @unittest.skipIf(BASELINE, "original FS has no image provider")
    def test_owned_concurrent_read_and_unmount_preserve_reader_lifetime(self):
        self.run_case("concurrent")

    @unittest.skipIf(BASELINE, "original FS has no image provider")
    def test_image_files_share_original_bounded_handle_table(self):
        self.run_case("slots")


if __name__ == "__main__":
    unittest.main()
