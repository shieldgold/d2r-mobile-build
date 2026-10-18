"""Prepare an unsigned lower-resolution copy of the verified BOTW 1018.3 app.

Only the handheld size cases in main+0x1266074 change. The original frame
interval/VFR cuts, host, shaders, controller mapping and save paths survive.
Input is pinned by both the NSO build ID and the rewritten text fingerprint.
"""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import struct

BUILD_ID = 'cd57b23fa4bbad65803d9788c01821ee'
TEXT_SHA256 = 'c2da2ebdec2ae43ad4fbdfc5f23f4babae9d2eafc430cb960597739e9452c7db'
SIZES = {720: 1280, 540: 960, 432: 768, 360: 640}
ORIGINAL = {
    0x1266114: 0xd2a89408,  # fixed handheld: display width, float 1280
    0x1266118: 0xf2e89408,  # fixed handheld: render width, float 1280
    0x1266120: 0xd280a008,  # fixed handheld display width, integer 1280
    0x1266124: 0xf2c05a08,  # fixed handheld display height, integer 720
    0x1266134: 0x52a88689,  # dynamic handheld height base, float 720
    0x1266140: 0x52a89408,  # dynamic handheld width base, float 1280
    0x1266180: 0x5280a003,  # handheld crop width, integer 1280
    0x1266188: 0x52805a04,  # handheld crop height, integer 720
}


def patch_text(original, height):
    if height not in SIZES or len(original) < max(ORIGINAL) + 4:
        raise ValueError('Unsupported height or truncated guest text')
    for offset, word in ORIGINAL.items():
        if struct.unpack_from('<I', original, offset)[0] != word:
            raise ValueError(f'Original instruction mismatch at {offset:#x}')
    width = SIZES[height]
    floats = [struct.unpack('<I', struct.pack('<f', value))[0] for value in (width, height)]
    if any(value & 0xffff for value in floats):
        raise ValueError('Size cannot be represented by the verified MOV instructions')
    immediates = [floats[0] >> 16, floats[0] >> 16, width, height,
                  floats[1] >> 16, floats[0] >> 16, width, height]
    patched = bytearray(original)
    for (offset, word), immediate in zip(ORIGINAL.items(), immediates):
        word = (word & ~(0xffff << 5)) | immediate << 5
        struct.pack_into('<I', patched, offset, word)
    return bytes(patched)


def main_section(data):
    if len(data) < 32 or struct.unpack_from('<II', data)[0:2] != (0xfeedfacf, 0x100000c):
        raise ValueError('Expected a thin arm64 Mach-O executable')
    count, size = struct.unpack_from('<II', data, 16)
    end = 32 + size
    if end > len(data):
        raise ValueError('Truncated Mach-O load commands')
    cursor, matches = 32, []
    for _ in range(count):
        if cursor + 8 > end:
            raise ValueError('Truncated load command')
        command, length = struct.unpack_from('<II', data, cursor)
        if length < 8 or cursor + length > end:
            raise ValueError('Invalid load command size')
        if command == 25:
            if length < 72:
                raise ValueError('Truncated segment')
            sections = struct.unpack_from('<I', data, cursor + 64)[0]
            if 72 + 80 * sections > length:
                raise ValueError('Truncated section table')
            for index in range(sections):
                offset = cursor + 72 + index * 80
                if (data[offset:offset + 16].rstrip(b'\0') == b'__dmmainx0'
                        and data[offset + 16:offset + 32].rstrip(b'\0') == b'__TEXT'):
                    section_size = struct.unpack_from('<Q', data, offset + 40)[0]
                    file_offset = struct.unpack_from('<I', data, offset + 48)[0]
                    if file_offset < end or file_offset + section_size > len(data):
                        raise ValueError('Guest section is outside the executable')
                    matches.append((file_offset, section_size))
        cursor += length
    if cursor != end or len(matches) != 1:
        raise ValueError('Expected exactly one embedded main text section')
    return matches[0]


def prepare(source, destination, height, build):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(source):
        raise ValueError('Destination must be a new directory outside the source app')
    info = plistlib.loads((source / 'Info.plist').read_bytes())
    if (info['CFBundleIdentifier'], info['CFBundleShortVersionString'], info['CFBundleVersion']) != (
            'local.botw.native', '1.9.0', '1018.3'):
        raise ValueError('Expected the verified BOTW 1.9.0 build 1018.3 source app')
    fixups = dict(line[2:].split() for line in (source / 'dm/main.fixups.tsv').read_text().splitlines())
    if fixups['BUILD_ID'] != BUILD_ID:
        raise ValueError('Unsupported NSO build ID')
    name = info['CFBundleExecutable']
    if Path(name).name != name:
        raise ValueError('Invalid executable name')
    original = (source / name).read_bytes()
    offset, length = main_section(original)
    text = original[offset:offset + length]
    if hashlib.sha256(text).hexdigest() != TEXT_SHA256:
        raise ValueError('Rewritten guest text fingerprint differs from the verified source')
    patched = patch_text(text, height)
    executable = original[:offset] + patched + original[offset + length:]
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    (destination / name).write_bytes(executable)
    shutil.rmtree(destination / '_CodeSignature', ignore_errors=True)
    (destination / 'embedded.mobileprovision').unlink(missing_ok=True)
    info['CFBundleVersion'] = build
    (destination / 'Info.plist').write_bytes(plistlib.dumps(info))
    receipt = {'source_app': str(source), 'output_app': str(destination),
               'nso_build_id': BUILD_ID, 'source_guest_text_sha256': TEXT_SHA256,
               'source_executable_sha256': hashlib.sha256(original).hexdigest(),
               'output_executable_sha256': hashlib.sha256(executable).hexdigest(),
               'width': SIZES[height], 'height': height, 'build': build,
               'requires_handheld_DM_OPMODE': '0', 'signed': False,
               'device_validated': False, 'shader_translation_changed': False,
               'words': [{'offset': hex(address), 'before': hex(word),
                          'after': hex(struct.unpack_from('<I', patched, address)[0])}
                         for address, word in ORIGINAL.items()]}
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_app', type=Path)
    parser.add_argument('destination_app', type=Path)
    parser.add_argument('--height', type=int, choices=sorted(SIZES), required=True)
    parser.add_argument('--build', required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.source_app, args.destination_app, args.height, args.build)
    args.receipt.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
