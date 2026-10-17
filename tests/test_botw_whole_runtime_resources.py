"""Real local-file checks; no dlopen, guest execution, Metal or device access."""
import hashlib
import pathlib
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PART = ROOT / "experiments/botw_upstream_host"


def owned_macho(platform=2, duplicate=False):
    # Physical file order x0,x1,w differs from the canonical x0,w,x1 image.
    pages = [b"X" * 16384, b"P" * 16384, b"W" * 16384]
    def segment(name, sections, prot):
        n = len(sections)
        cmd = struct.pack("<II16sQQQQiiII", 25, 72 + 80*n, name.encode(),
                          sections[0][1], 16384*n, sections[0][1], 16384*n,
                          prot, prot, n, 0)
        for section, offset in sections:
            cmd += struct.pack("<16s16sQQIIIIIIII", section.encode(), name.encode(),
                               offset, 16384, offset, 14, 0, 0, 0, 0, 0, 0)
        return cmd
    text = [("__dmownedx0", 16384), ("__dmownedx1", 32768)]
    if duplicate:
        text[1] = ("__dmownedx0", 32768)
    commands = segment("__TEXT", text, 5) + segment("__DMownedW", [("__img", 49152)], 3)
    commands += struct.pack("<IIIIII", 0x32, 24, platform, 0x120000, 0x120000, 0)
    header = struct.pack("<IiiIIIII", 0xfeedfacf, 0x100000c, 0, 6, 3, len(commands), 0, 0)
    image = (header + commands).ljust(16384, b"\0") + b"".join(pages)
    digest = hashlib.sha256(pages[0] + pages[2] + pages[1]).hexdigest()
    return image, digest


@unittest.skipUnless(shutil.which("clang") and __import__("sys").platform == "darwin", "macOS toolchain")
class RuntimeResources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.directory = pathlib.Path(cls.tmp.name)
        cls.exe = cls.directory / "probe"
        subprocess.run(["clang", "-std=c11", "-D_DARWIN_C_SOURCE", "-Wall", "-Wextra", "-Werror",
                        "-Wno-deprecated-declarations", "-fsanitize=address,undefined", "-g",
                        "-I" + str(PART / "adapter"),
                        str(PART / "adapter/whole_runtime_resources.m"),
                        str(PART / "adapter/base_v0_force.c"),
                        str(ROOT / "native/botw_resources/romfs.c"),
                        str(PART / "tests/whole_runtime_resources_probe.c"),
                        "-o", str(cls.exe)], check=True, capture_output=True, text=True)
        cls.lifecycle = cls.directory / "lifecycle"
        subprocess.run(["clang", "-fobjc-arc", "-fblocks", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-DBOTW_WHOLE_RUNTIME_CONFIGURED=1", "-fsanitize=address,undefined", "-g",
                        "-Wl,-dead_strip", "-framework", "Foundation",
                        str(PART / "tests/whole_runtime_lifecycle_probe.m"),
                        "-o", str(cls.lifecycle)], check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_probe(self, mode, path, sha, size=0):
        return subprocess.run([str(self.exe), mode, str(path), sha, str(size)], capture_output=True, text=True)

    def test_valid_regular_identity_and_empty_gate(self):
        for content in (b"owned\tmetadata\n", b""):
            (self.directory / "owned.tsv").write_bytes(content)
            result = self.run_probe("file", self.directory, hashlib.sha256(content).hexdigest(), len(content))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_content_and_size_are_both_required(self):
        (self.directory / "owned.tsv").write_bytes(b"abc")
        for digest, size in ((hashlib.sha256(b"abd").hexdigest(), 3), (hashlib.sha256(b"abc").hexdigest(), 2)):
            self.assertEqual(self.run_probe("file", self.directory, digest, size).returncode, 1)

    def test_symlink_is_rejected_even_with_matching_bytes(self):
        file = self.directory / "owned.tsv"
        file.unlink(missing_ok=True)
        other = self.directory / "other"
        other.write_bytes(b"abc")
        file.symlink_to(other)
        try:
            self.assertEqual(self.run_probe("file", self.directory, hashlib.sha256(b"abc").hexdigest(), 3).returncode, 1)
        finally:
            file.unlink()

    def test_owned_ios_macho_canonical_section_order(self):
        content, digest = owned_macho()
        path = self.directory / "Guest"
        path.write_bytes(content)
        result = self.run_probe("guest", path, digest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wrong_platform_corrupt_payload_duplicate_and_truncation(self):
        good, digest = owned_macho()
        cases = [owned_macho(platform=1)[0], owned_macho(duplicate=True)[0], good[:-1],
                 good[:-1] + bytes([good[-1] ^ 1])]
        for content in cases:
            path = self.directory / "badGuest"
            path.write_bytes(content)
            self.assertEqual(self.run_probe("guest", path, digest).returncode, 1)

    def test_actual_lifecycle_order_failure_and_no_retry(self):
        for mode in ("cycle", "unprepared", "a", "p", "P", "A", "o", "s"):
            result = subprocess.run([str(self.lifecycle), mode], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, mode + result.stdout + result.stderr)
        result = subprocess.run([str(self.lifecycle), "fatal-dispatch"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 70, result.stdout + result.stderr)

    def test_private_guard_package_static_only_when_available(self):
        original = ROOT / "outputs/botw-guest-framework-host39-v4-normal-allocation-guard-v1-six-final-ios18"
        if not (original / "Guest.framework/Guest").is_file():
            self.skipTest("private allocation-guard disk artifact is not distributed in the repository")
        bundle = self.directory / "Owned.app"
        shutil.copytree(original / "dm", bundle / "botw", dirs_exist_ok=True)
        shutil.copy2(ROOT / "profiles/botw-switch-base-v0.json", bundle / "botw/base-profile.json")
        shutil.copy2(original / "guest-framework-report.json", bundle / "botw/guest-framework-report.json")
        shutil.copytree(original / "Guest.framework", bundle / "Frameworks/Guest.framework", dirs_exist_ok=True)
        result = self.run_probe("package", bundle, "unused")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Still reject a change to one of the actual loader metadata files.
        with (bundle / "botw/main.gates.tsv").open("ab") as target:
            target.write(b"\n")
        self.assertEqual(self.run_probe("package", bundle, "unused").returncode, 1)

    def test_private_legacy_v4_is_rejected_by_the_new_runtime_identity(self):
        original = ROOT / "outputs/botw-guest-framework-host39-bounded-pthread-stack-v4-six-final-ios18"
        if not (original / "Guest.framework/Guest").is_file():
            self.skipTest("private legacy v4 disk artifact is not distributed in the repository")
        bundle = self.directory / "Legacy.app"
        shutil.copytree(original / "dm", bundle / "botw", dirs_exist_ok=True)
        shutil.copy2(ROOT / "profiles/botw-switch-base-v0.json", bundle / "botw/base-profile.json")
        shutil.copy2(original / "guest-framework-report.json", bundle / "botw/guest-framework-report.json")
        shutil.copytree(original / "Guest.framework", bundle / "Frameworks/Guest.framework", dirs_exist_ok=True)
        result = self.run_probe("package", bundle, "unused")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_runtime_provider_requires_explicit_build_macro(self):
        result = subprocess.run(["clang", "-fobjc-arc", "-fblocks", "-std=c11", "-fsyntax-only",
                                 str(PART / "frontend/whole_runtime_configuration.m")], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit BOTW_WHOLE_RUNTIME_CONFIGURED=1", result.stderr)
