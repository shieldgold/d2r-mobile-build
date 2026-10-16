"""Owned CPU force routing and static file identity; never execute guest code."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/botw_upstream_host'
BASELINE = os.environ.get('BOTW_WHOLE_FORCE_BASELINE') == '1'
ORDER = ('main', 'subsdk0', 'subsdk1', 'subsdk2', 'subsdk3', 'sdk')
# Independently recovered from the original six NSOs' loader-visible RELA/JMPREL.
# A bit means one actual force_lookup hit from that module, in ORDER above.
EXPECTED_REFERENCES = {
    "_ZN2nn2am23GetAppletResourceUserIdEv": 32,
    "_ZN2nn2fs10CreateFileEPKcl": 35,
    "_ZN2nn2fs10DeleteFileEPKc": 35,
    "_ZN2nn2fs11GetFileSizeEPlNS0_10FileHandleE": 51,
    "_ZN2nn2fs11SetFileSizeENS0_10FileHandleEl": 35,
    "_ZN2nn2fs12GetEntryTypeEPNS0_18DirectoryEntryTypeEPKc": 35,
    "_ZN2nn2fs13MountSaveDataEPKcRKNS_7account3UidE": 1,
    "_ZN2nn2fs13OpenDirectoryEPNS0_15DirectoryHandleEPKci": 33,
    "_ZN2nn2fs13ReadDirectoryEPlPNS0_14DirectoryEntryENS0_15DirectoryHandleEl": 33,
    "_ZN2nn2fs14CloseDirectoryENS0_15DirectoryHandleE": 33,
    "_ZN2nn2fs14CommitSaveDataEPKc": 33,
    "_ZN2nn2fs15CreateDirectoryEPKc": 35,
    "_ZN2nn2fs22GetDirectoryEntryCountEPlNS0_15DirectoryHandleE": 32,
    "_ZN2nn2fs22QueryMountRomCacheSizeEPm": 1,
    "_ZN2nn2fs26DeleteDirectoryRecursivelyEPKc": 1,
    "_ZN2nn2fs7UnmountEPKc": 35,
    "_ZN2nn2fs8MountRomEPKcPvm": 1,
    "_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci": 51,
    "_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm": 51,
    "_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvmRKNS0_10ReadOptionE": 33,
    "_ZN2nn2fs9CloseFileENS0_10FileHandleE": 51,
    "_ZN2nn2fs9FlushFileENS0_10FileHandleE": 35,
    "_ZN2nn2fs9WriteFileENS0_10FileHandleElPKvmRKNS0_11WriteOptionE": 35,
    "_ZN2nn2oe10InitializeEv": 1,
    "_ZN2nn2oe16GetOperationModeEv": 1,
    "_ZN2nn2oe17FinishStartupLogoEv": 1,
    "_ZN2nn2oe18GetDesiredLanguageEv": 33,
    "_ZN2nn2oe18GetPerformanceModeEv": 1,
    "_ZN2nn2oe24GetExpectedVolumeBalanceEPfS1_": 1,
    "_ZN2nn2oe24SetExpectedVolumeBalanceEff": 1,
    "_ZN2nn2oe25TryPopNotificationMessageEPj": 1,
    "_ZN2nn2oe27SetPerformanceConfigurationENS0_15PerformanceModeEi": 1,
    "_ZN2nn2oe28SetResumeNotificationEnabledEb": 1,
    "_ZN2nn2oe38SetUserInactivityDetectionTimeExtendedEb": 1,
    "_ZN2nn2oe42SetOperationModeChangedNotificationEnabledEb": 1,
    "_ZN2nn2oe44SetPerformanceModeChangedNotificationEnabledEb": 1,
    "_ZN2nn2os14FlushDataCacheEPKvm": 34,
    "_ZN2nn2os6detail21AddressSpaceAllocatorC1Emmm": 32,
    "_ZN2nn2vi10InitializeEv": 5,
    "_ZN2nn2vi11CreateLayerEPPNS0_5LayerEPNS0_7DisplayE": 5,
    "_ZN2nn2vi15GetNativeWindowEPPvPNS0_5LayerE": 1,
    "_ZN2nn2vi18OpenDefaultDisplayEPPNS0_7DisplayE": 1,
    "_ZN2nn2vi19SetLayerScalingModeEPNS0_5LayerENS0_11ScalingModeE": 1,
    "_ZN2nn2vi20GetDisplayVsyncEventEPNS_2os15SystemEventTypeEPNS0_7DisplayE": 1,
    "_ZN2nn3aoc10InitializeEv": 1,
    "_ZN2nn3hid12GetNpadStateEPNS0_16NpadFullKeyStateERKj": 1,
    "_ZN2nn3hid12GetNpadStateEPNS0_17NpadHandheldStateERKj": 1,
    "_ZN2nn3hid12GetNpadStateEPNS0_16NpadJoyDualStateERKj": 1,
    "_ZN2nn3hid12GetNpadStateEPNS0_16NpadJoyLeftStateERKj": 1,
    "_ZN2nn3hid12GetNpadStateEPNS0_17NpadJoyRightStateERKj": 1,
    "_ZN2nn3hid14InitializeNpadEv": 1,
    "_ZN2nn3hid18InitializeDebugPadEv": 1,
    "_ZN2nn3hid16GetDebugPadStateEPNS0_13DebugPadStateE": 1,
    "_ZN2nn3hid15GetNpadStyleSetERKj": 1,
    "_ZN2nn3hid18SendVibrationValueERKNS0_21VibrationDeviceHandleERKNS0_14VibrationValueE": 1,
    "_ZN2nn3hid18SetNpadJoyHoldTypeENS0_15NpadJoyHoldTypeE": 1,
    "_ZN2nn3hid18StartSixAxisSensorERKNS0_19SixAxisSensorHandleE": 1,
    "_ZN2nn3hid21ShowControllerSupportERKNS0_20ControllerSupportArgE": 1,
    "_ZN2nn3hid22GetSixAxisSensorStatesEPNS0_18SixAxisSensorStateEiRKNS0_19SixAxisSensorHandleE": 1,
    "_ZN2nn3hid22SetSupportedNpadIdTypeEPKjm": 1,
    "_ZN2nn3hid23GetSixAxisSensorHandlesEPNS0_19SixAxisSensorHandleEiRKjNS_4util10BitFlagSetILi32ENS0_12NpadStyleTagEEE": 1,
    "_ZN2nn3hid24SetSupportedNpadStyleSetENS_4util10BitFlagSetILi32ENS0_12NpadStyleTagEEE": 1,
    "_ZN2nn3hid25GetVibrationDeviceHandlesEPNS0_21VibrationDeviceHandleEiRKjNS_4util10BitFlagSetILi32ENS0_12NpadStyleTagEEE": 1,
    "_ZN2nn3hid25InitializeVibrationDeviceERKNS0_21VibrationDeviceHandleE": 1,
    "_ZN2nn3hid25SetGyroscopeZeroDriftModeERKNS0_19SixAxisSensorHandleERKNS0_22GyroscopeZeroDriftModeE": 1,
    "_ZN2nn3hid28SetNpadJoyAssignmentModeDualERKj": 1,
    "_ZN2nn3nfp10InitializeEv": 1,
    "_ZN2nn3nfp11ListDevicesEPNS0_12DeviceHandleEPii": 1,
    "_ZN2nn3nfp8FinalizeEv": 1,
    "_ZN2nn3nfp8GetStateEv": 1,
    "_ZN2nn4diag34InitializeApplicationAbortObserverEv": 1,
    "_ZN2nn4pctl10InitializeEv": 1,
    "_ZN2nn4time10InitializeEv": 1,
    "_ZN2nn4time13IsInitializedEv": 1,
    "_ZN2nn4time14GetSpanBetweenEPlRKNS0_20SteadyClockTimePointES4_": 1,
    "_ZN2nn4time14ToCalendarTimeEPNS0_12CalendarTimeEPNS0_22CalendarAdditionalInfoERKNS0_9PosixTimeE": 1,
    "_ZN2nn4time19StandardSteadyClock19GetCurrentTimePointEPNS0_20SteadyClockTimePointE": 1,
    "_ZN2nn4time23StandardUserSystemClock14GetCurrentTimeEPNS0_9PosixTimeE": 1,
    "_ZN2nn4time8FinalizeEv": 1,
    "_ZN2nn5audio17AcquireMemoryPoolEPNS0_19AudioRendererConfigEPNS0_14MemoryPoolTypeEPvm": 1,
    "_ZN2nn5audio17OpenAudioRendererEPNS0_19AudioRendererHandleEPNS_2os11SystemEventERKNS0_22AudioRendererParameterEPvm": 1,
    "_ZN2nn5audio17ReleaseMemoryPoolEPNS0_19AudioRendererConfigEPNS0_14MemoryPoolTypeE": 1,
    "_ZN2nn5audio17StopAudioRendererENS0_19AudioRendererHandleE": 1,
    "_ZN2nn5audio18CloseAudioRendererENS0_19AudioRendererHandleE": 1,
    "_ZN2nn5audio18StartAudioRendererENS0_19AudioRendererHandleE": 1,
    "_ZN2nn5audio19ListAudioDeviceNameEPNS0_15AudioDeviceNameEi": 1,
    "_ZN2nn5audio26RequestUpdateAudioRendererENS0_19AudioRendererHandleEPKNS0_19AudioRendererConfigE": 1,
    "_ZN2nn5audio26SetAudioDeviceOutputVolumeEPKNS0_15AudioDeviceNameEf": 1,
    "_ZN2nn5audio29InitializeAudioRendererConfigEPNS0_19AudioRendererConfigERKNS0_22AudioRendererParameterEPvm": 1,
    "_ZN2nn5audio30GetAudioRendererWorkBufferSizeERKNS0_22AudioRendererParameterE": 1,
    "_ZN2nn5audio36GetAudioRendererConfigWorkBufferSizeERKNS0_22AudioRendererParameterE": 1,
    "_ZN2nn5prepo10PlayReport4SaveERKNS_7account3UidE": 1,
    "_ZN2nn5prepo10PlayReport4SaveEv": 1,
    "_ZN2nn6applet23GetAppletResourceUserIdEv": 38,
    "_ZN2nn7account10InitializeEv": 1,
    "_ZN2nn7account12ListAllUsersEPiPNS0_3UidEi": 33,
    "_ZN2nn7account9GetUserIdEPNS0_3UidERKNS0_10UserHandleE": 33,
    "_ZN2nv18InitializeGraphicsEPvm": 1,
    "_ZN2nv20SetGraphicsAllocatorEPFPvmmS0_EPFvS0_S0_EPFS0_S0_mS0_ES0_": 1,
    "_ZN2nv28SetGraphicsDevtoolsAllocatorEPFPvmmS0_EPFvS0_S0_EPFS0_S0_mS0_ES0_": 1,
    "nvnBootstrapLoader": 1
}


def stage_source(destination):
    shutil.copytree(COMPONENT / 'upstream', destination)
    lock = json.loads((COMPONENT / 'SOURCE.lock.json').read_text())
    patches = list(lock['patches'])
    if not BASELINE and 'patches/base-v0-force.patch' not in patches:
        patches.append('patches/base-v0-force.patch')
    if 'patches/base-v0-vi-layer.patch' not in patches:
        patches.append('patches/base-v0-vi-layer.patch')
    if 'patches/base-v0-stack-allocator.patch' not in patches:
        patches.append('patches/base-v0-stack-allocator.patch')
    if 'patches/base-v0-debug-pad.patch' not in patches:
        patches.append('patches/base-v0-debug-pad.patch')
    for name in patches:
        if BASELINE and name == 'patches/base-v0-force.patch':
            continue
        subprocess.run(['patch', '--batch', '-p1', '-d', str(destination), '-i',
                        str(COMPONENT / name)], check=True, capture_output=True, text=True)


def source_registry(stage):
    rows = []
    pattern = r'\bHLE_FORCE\(\s*("(?:[^"\\]|\\.)*")\s*,\s*("(?:[^"\\]|\\.)*")'
    for file in sorted(stage.rglob('*')):
        if file.suffix not in ('.c', '.m'):
            continue
        text = file.read_text()
        title = 4 if '#define HLE_FORCE_TITLES DM_TM_BOTW' in text else 2
        for match in re.finditer(pattern, text):
            rows.append((json.loads(match[1]), json.loads(match[2]), title))
    return rows


@unittest.skipUnless(sys.platform == 'darwin', 'actual CommonCrypto/Mach-O force fixture requires Darwin')
class WholeForceProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-force-owned-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        stage = cls.work / 'stage'
        stage_source(stage)
        text = (stage / 'dm/host/loader.c').read_text()
        begin = text.index('static const hle_force_ent *g_force;')
        end = text.index('// Read the rewriter table header', begin)
        (cls.work / 'loader_force.inc').write_text(text[begin:end])
        rows = source_registry(stage)
        if len(rows) != 136:
            raise AssertionError('actual source registry changed: ' + str(len(rows)))
        owned = []
        for name, why, title in rows:
            owned.append('    {%s, (void *)owned_callback, %s, %du},' %
                         (json.dumps(name), json.dumps(why), title))
        (cls.work / 'registry.inc').write_text('\n'.join(owned))
        refs = ['    {%s, %du},' % (json.dumps(name), mask)
                for name, mask in EXPECTED_REFERENCES.items()]
        (cls.work / 'references.inc').write_text('\n'.join(refs))
        cls.binary = cls.work / 'probe'
        command = [shutil.which('clang') or 'cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                   '-Wno-unused-function', '-Wno-deprecated-declarations', '-pthread',
                   '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                   '-I', str(cls.work), '-I', str(COMPONENT / 'adapter'),
                   str(COMPONENT / 'tests/base_v0_force_probe.c'), '-o', str(cls.binary)]
        if BASELINE:
            command.append('-DFORCE_BASELINE=1')
        compiled = subprocess.run(command, capture_output=True, text=True)
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)
        cls.staged = stage

    def probe(self, mode, expected=0, *args):
        env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0:halt_on_error=1',
                   UBSAN_OPTIONS='halt_on_error=1')
        run = subprocess.run([str(self.binary), mode, *map(str, args)], env=env,
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, expected, run.stdout + run.stderr)
        return run

    def test_actual_force_check_accepts_fixed_profile_relocations(self):
        self.probe('route')

    @unittest.skipIf(BASELINE, 'baseline has no fixed-profile matcher')
    def test_unknown_modules_order_and_call_order_fail_closed(self):
        for mode in ('begin-order', 'begin-unknown', 'call-order'):
            with self.subTest(mode=mode): self.probe(mode)

    @unittest.skipIf(BASELINE, 'baseline has no identity verifier')
    def test_invalid_identity_and_bounds_reject_before_byte_stream_access(self):
        for mode in ('image-guard', 'identity-change', 'layout-change', 'image-order', 'image-repeat'):
            with self.subTest(mode=mode): self.probe(mode)

    @unittest.skipIf(BASELINE, 'baseline has no registry manifest')
    def test_changed_unknown_null_duplicate_or_wrong_candidate_registry_fails(self):
        for mode in ('registry-unknown', 'registry-reason', 'registry-title',
                     'registry-null', 'registry-duplicate', 'registry-candidate', 'registry-incomplete'):
            with self.subTest(mode=mode): self.probe(mode, 77)

    @unittest.skipIf(BASELINE, 'baseline has no per-module reference matrix')
    def test_missing_duplicate_and_moved_actual_force_hits_fail(self):
        self.probe('missing-reference')
        self.probe('reference-order')
        for mode in ('duplicate-reference', 'moved-reference'):
            with self.subTest(mode=mode): self.probe(mode, 77)

    @unittest.skipIf(BASELINE, 'baseline check is the original')
    def test_original_force_check_body_is_byte_identical(self):
        original = (COMPONENT / 'upstream/dm/host/loader.c').read_text()
        patched = (self.staged / 'dm/host/loader.c').read_text()
        def body(text):
            start = text.index('static void force_check(void) {')
            return text[start:text.index('\n}\n', start) + 3]
        self.assertEqual(body(original), body(patched))
        self.assertLess(patched.index('base_v0_force_begin(names'), patched.index('    create_keys();'))
        self.assertLess(patched.index('base_v0_verify_image(m, parts, lengths, 3, 1)'),
                        patched.index('m->base = (uint8_t *)base; install_key_page(m);'))
        self.assertLess(patched.index('base_v0_verify_image(m, parts, lengths, 1, 0)'),
                        patched.index('memcpy(m->base, raw, sz);'))

    @unittest.skipIf(BASELINE, 'baseline has no fixed identity manifest')
    def test_real_original_nso_identity_and_actual_force_lookup_relocation_matrix(self):
        directory = ROOT / 'outputs/botw-native-base/ExeFS'
        if not all((directory / name).is_file() for name in ORDER):
            self.skipTest('private original NSOs are not present; owned contracts still run')
        try:
            import lz4.block  # noqa: F401 -- original compressed source reader dependency
        except ImportError:
            self.skipTest('original compressed NSO audit requires lz4')
        sys.path.insert(0, str(ROOT / 'scripts'))
        from botw_native.nso import initialized_image
        rows = self.probe('manifest').stdout.splitlines()
        manifest = {r.split()[0]: r.split()[1:] for r in rows}
        candidate_names = {name for name, _, title in source_registry(self.staged)
                           if title == 4 or name in BORROWED}
        self.assertEqual(len(candidate_names), 134)
        actual = {}
        for module_index, name in enumerate(ORDER):
            data = (directory / name).read_bytes()
            expected = manifest[name]
            self.assertEqual(hashlib.sha256(data).hexdigest(), expected[0])
            self.assertEqual(data[0x40:0x60].hex(), expected[1])
            image = initialized_image(data, name)
            mod0 = struct.unpack_from('<I', image, 4)[0]
            dynamic = mod0 + struct.unpack_from('<i', image, mod0 + 4)[0]
            tags = {}
            for index in range(65536):
                tag, value = struct.unpack_from('<QQ', image, dynamic + index * 16)
                if tag == 0: break
                tags[tag] = value
            else: self.fail('unterminated actual MOD0 dynamic table')
            symbols = (tags[5] - tags[6]) // 24  # exact upstream loader nsym rule
            # Exactly the original relocate's two tables, excluding RELATIVE;
            # defined as well as undefined symbols still pass force_lookup.
            per_module = {}
            for table, size in ((tags.get(7, 0), tags.get(8, 0)),
                                (tags.get(23, 0), tags.get(2, 0))):
                self.assertEqual(size % 24, 0)
                for offset in range(table, table + size, 24):
                    _, info, _ = struct.unpack_from('<QQq', image, offset)
                    if info & 0xffffffff == 1027: continue
                    symbol = info >> 32
                    self.assertLess(symbol, symbols)
                    string = struct.unpack_from('<I', image, tags[6] + symbol * 24)[0] + tags[5]
                    end = image.index(b'\0', string)
                    symbol_name = image[string:end].decode()
                    if symbol_name in candidate_names:
                        per_module[symbol_name] = per_module.get(symbol_name, 0) + 1
            for symbol_name, count in per_module.items():
                self.assertEqual(count, 1)
                actual[symbol_name] = actual.get(symbol_name, 0) | (1 << module_index)
        self.assertEqual(actual, EXPECTED_REFERENCES)
        self.assertEqual(sum(mask.bit_count() for mask in actual.values()), 142)

    @unittest.skipIf(BASELINE, 'baseline has no stream verifier')
    def test_real_fixed_images_hash_as_raw_and_three_padded_signed_sections(self):
        directory = ROOT / 'outputs/botw-native-host39-bounded-pthread-stack-v4/rewrite'
        if not all((directory / (name + '.patched.bin')).is_file() for name in ORDER):
            self.skipTest('private rewritten disk images are not present; no guest executed')
        self.probe('disk-raw', 0, directory)
        self.probe('disk-padded', 0, directory)
        self.probe('disk-mutated', 0, directory)
        self.probe('disk-bad-padding', 0, directory)
        manifest = {r.split()[0]: r.split()[1:] for r in self.probe('manifest').stdout.splitlines()}
        guest = ROOT / ('outputs/botw-guest-framework-host39-bounded-pthread-stack-v4-six-final-ios18/'
                        'Guest.framework/Guest')
        if guest.is_file():
            # Static Mach-O sections only; no dyld, VM inspection or execution.
            data = guest.read_bytes()
            self.assertEqual(struct.unpack_from('<I', data)[0], 0xfeedfacf)
            commands = struct.unpack_from('<I', data, 16)[0]
            cursor, sections = 32, {}
            for _ in range(commands):
                command, size = struct.unpack_from('<II', data, cursor)
                if command == 0x19:
                    count = struct.unpack_from('<I', data, cursor + 64)[0]
                    for index in range(count):
                        at = cursor + 72 + index * 80
                        section, segment = struct.unpack_from('<16s16s', data, at)
                        section = section.rstrip(b'\0').decode(); segment = segment.rstrip(b'\0').decode()
                        _, length, offset = struct.unpack_from('<QQI', data, at + 32)
                        self.assertLessEqual(offset + length, len(data))
                        sections[segment, section] = data[offset:offset + length]
                cursor += size
            for name in ORDER:
                streams = [sections['__TEXT', '__dm' + name + 'x0'],
                           sections['__DM' + name + 'W', '__img'],
                           sections['__TEXT', '__dm' + name + 'x1']]
                digest = hashlib.sha256()
                for stream in streams: digest.update(stream)
                self.assertEqual(digest.hexdigest(), manifest[name][3])


BORROWED = ('nndiagStartup', '_ZN2nn2oe10InitializeEv', '_ZN2nn3aoc10InitializeEv',
            '_ZN2nn3err26InitializeContextWriterApiEv', '_ZN2nn2fs24InitializeForApplicationEv',
            '_ZN2nn4pctl10InitializeEv', '_ZN2nn4diag34InitializeApplicationAbortObserverEv')

if __name__ == '__main__':
    unittest.main()
