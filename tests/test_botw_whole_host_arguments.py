"""Execute the actual original guest_main with owned native SDK stand-ins only."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "experiments/botw_upstream_host"
BASELINE = os.environ.get("BOTW_WHOLE_HOST_ARGUMENTS_BASELINE") == "1"


class WholeHostArguments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        work = Path(cls.tmp.name)
        target = work / "stage/dm/host/main.c"
        target.parent.mkdir(parents=True)
        original = (COMPONENT / "upstream/dm/host/main.c").read_text()
        target.write_text(original)
        if not BASELINE:
            subprocess.run(["patch", "--batch", "-p1", "-d", str(work / "stage"), "-i",
                            str(COMPONENT / "patches/base-v0-host-arguments.patch")], check=True, capture_output=True, text=True)
        source = target.read_text()
        # Exact bounded original function, not a mirrored invocation sequence.
        start = source.index("static void guest_main(void) {")
        end = source.index("\n}\n\nstruct dm_flags", start) + 2
        request = source[source.index("static void *req("):source.index("\n\n// Game arguments:")]
        (work / "actual_guest_main.inc").write_text(request + "\n" + source[start:end] + "\n")
        cls.binary = work / "probe"
        subprocess.run([shutil.which("clang") or "cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-g",
                        "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I", str(work),
                        "-I", str(COMPONENT / "upstream/dm/host"),
                        str(COMPONENT / "tests/whole_host_arguments_probe.c"), "-o", str(cls.binary)],
                       check=True, capture_output=True, text=True)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_default_title_arguments_are_visible_to_early_sdk_and_ctors(self):
        self.case("default")

    def test_explicit_game_arguments_keep_original_count_pointer_and_bytes(self):
        self.case("arguments")


if __name__ == "__main__":
    unittest.main()
