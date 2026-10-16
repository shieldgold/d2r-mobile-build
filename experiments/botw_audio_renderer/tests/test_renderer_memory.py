"""Actual bounded native renderer memory port on owned CPU allocations."""
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

COMPONENT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == 'Darwin', 'actual macOS native toolchain required')
class RendererMemoryTests(unittest.TestCase):
    def test_mapping_translation_command_lease_busy_unmap_and_real_revocation(self):
        with tempfile.TemporaryDirectory(prefix='botw-renderer-memory-') as temporary:
            binary = Path(temporary) / 'probe'
            command = ['xcrun', 'clang++', '-std=c++23', '-O1', '-Wall', '-Wextra', '-Werror', '-pthread',
                       '-I', COMPONENT / 'platform', '-I', COMPONENT / 'native',
                       COMPONENT / 'platform/platform.cpp', COMPONENT / 'native/renderer_memory.cpp',
                       COMPONENT / 'tests/renderer_memory_probe.cpp', '-o', binary]
            if os.environ.get('BOTW_AUDIO_RENDERER_SANITIZERS') == '1':
                command += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
            built = subprocess.run([str(part) for part in command], text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
