"""Exercise the actual Objective-C feedback implementation with observable UIKit doubles."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'Foundation runtime test requires macOS')
class ButtonHapticsTests(unittest.TestCase):
    def test_device_lifecycle_and_multitouch_feedback(self):
        source = ROOT / 'source-overrides/dm/ios/Sources'
        stubs = ROOT / 'tests/haptic_stubs'
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / 'button-haptics'
            subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-fblocks', '-Wall', '-Wextra',
                            '-Werror', '-framework', 'Foundation', '-I' + str(stubs),
                            '-I' + str(source), str(stubs / 'button_haptics_test.m'),
                            str(source / 'DmButtonHaptics.m'), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)


if __name__ == '__main__':
    unittest.main()
