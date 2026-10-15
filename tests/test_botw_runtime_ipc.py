"""Exercise real rewritten SVC gates against the host's session bridge."""
import platform
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

import test_botw_runtime as fixture


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'native bridge tests require Apple ARM64')
class RuntimeIPCBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.NativeRuntimeTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixture.NativeRuntimeTests.tearDownClass.__func__(cls)

    def run_bridge(self, port=b'sm:', invalid_pointer=False):
        with tempfile.TemporaryDirectory(prefix='botw-native-ipc-') as directory:
            root = Path(directory)
            for name in fixture.MODULES:
                fixture.image(root, name)
            path = root / 'botw/sdk.patched.bin'
            raw = bytearray(path.read_bytes())
            # Guest calls ConnectToNamedPort and requires a nonzero session.
            literal = 0x58000001 | (((0x8208 - 0x100) // 4) << 5)
            branch = 0x14000000 | ((0x10000 - 0x104) // 4)
            struct.pack_into('<IIIII', raw, 0x100, literal, branch,
                             0xb5000041, 0xd4200000, 0xd65f03c0)
            imm = (-4) & ((1 << 21) - 1)
            adrp = 0x9000001d | ((imm & 3) << 29) | ((imm >> 2) << 5)
            gate = (0xa9bf7bfd, 0xd53bd07e, adrp, 0xf9400fbd, 0x8b1d03de,
                    0xf94003de, 0xf94003fd, 0xd63f03c0, 0xa8c17bfd, 0x17ffc039)
            struct.pack_into('<' + 'I' * len(gate), raw, 0x10000, *gate)
            if not invalid_pointer:
                raw[0x8230:0x8230 + len(port) + 1] = port + b'\0'
                struct.pack_into('<QQq', raw, 0x8b30, 0x8208, 1027, 0x8230)
                for offset in range(0x8000, 0x8100, 16):
                    if struct.unpack_from('<Q', raw, offset)[0] == 8:
                        struct.pack_into('<Q', raw, offset + 8, 72)
                        break
            path.write_bytes(raw)
            (root / 'botw/sdk.gates.tsv').write_text('0x10020\tsvc\t31\t0x104\n')
            return subprocess.run([str(self.exe), str(root), '--sdk-probe'],
                                  capture_output=True, text=True, timeout=10)

    def test_real_guest_connects_to_registered_service_manager(self):
        result = self.run_bridge()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('named_port_connected=sm:', result.stdout)
        self.assertIn('sdk_probe_completed=true', result.stdout)

    def test_port_pointer_is_validated_before_host_reads_it(self):
        result = self.run_bridge(invalid_pointer=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('named port string is outside guest readable memory', result.stdout)
        self.assertNotIn('guest CPU fault', result.stdout)


if __name__ == '__main__':
    unittest.main()
