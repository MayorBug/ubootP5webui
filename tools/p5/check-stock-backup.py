#!/usr/bin/env python3
"""Read-only P5 installation preflight. Does not create or flash an image."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def verified_backup(manifest_path, expected_format):
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('board') != 'cudy,p5' or manifest.get('format') != expected_format:
        raise ValueError('Wrong board or backup format')
    name = manifest['filename']
    if Path(name).name != name:
        raise ValueError('Backup filename must not contain a directory')
    data = (manifest_path.parent / name).read_bytes()
    if len(data) != manifest['bytes'] or hashlib.sha256(data).hexdigest() != manifest['sha256']:
        raise ValueError('Backup size or SHA256 does not match its manifest')
    return manifest, data


def appsbl(data, offset):
    slot = data[offset:offset + 0x140000]
    result = {'offset': hex(offset), 'slot_sha256': hashlib.sha256(slot).hexdigest()}
    if slot[:7] != b'\x7fELF\x01\x01\x01':
        result['format'] = 'unrecognized; not established as a bootable copy'
        return result
    h = struct.unpack_from('<16sHHIIIIIHHHHHH', slot)
    if h[1:4] != (2, 40, 1) or h[9] != 32 or not 1 <= h[10] <= 32:
        raise ValueError('Unsupported stock ELF header')
    if h[5] + h[9] * h[10] > len(slot):
        raise ValueError('ELF program table exceeds slot')
    result.update(format='ELF32 ARM executable', entry=hex(h[4]), segments=[])
    for i in range(h[10]):
        p = struct.unpack_from('<8I', slot, h[5] + i * h[9])
        kind, source, virtual, physical, filesz, memsz, flags, align = p
        if source + filesz > len(slot):
            raise ValueError('ELF segment exceeds slot')
        entry = dict(type=hex(kind), offset=hex(source), address=hex(physical), bytes=filesz, memory_bytes=memsz, flags=hex(flags))
        if flags & 0x07000000 == 0x02000000 and filesz >= 40:
            mbn = struct.unpack_from('<10I', slot, source)
            entry['hash_header'] = dict(image_id=mbn[0], version=mbn[1], image_size=mbn[4], code_size=mbn[5], signature_bytes=mbn[7], certificate_bytes=mbn[9])
        result['segments'].append(entry)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('data_manifest', type=Path)
    ap.add_argument('--oob-manifest', type=Path)
    args = ap.parse_args()
    manifest, data = verified_backup(args.data_manifest, 'nand-full-data')
    if len(data) != 0x8000000 or manifest.get('start') != 0:
        raise ValueError('Expected complete 128 MiB P5 data backup')
    result = {'backup_sha256_verified': True, 'device_mac': manifest.get('device_mac'), 'layout': manifest.get('layout'),
              'appsbl': [appsbl(data, offset) for offset in (0x380000, 0x4c0000)],
              'safe_to_install': False,
              'remaining': ['Live secure-boot policy is unverified.', 'SBL selection and rollback between APPSBL copies are unverified.', 'Flash-linked candidate must pass on-device preflight and RAM testing.', 'Physical install/readback, persistence and cold boot remain untested.']}
    if args.oob_manifest:
        raw_manifest, raw = verified_backup(args.oob_manifest, 'nand-raw-oob')
        if raw_manifest.get('device_mac') != manifest.get('device_mac') or len(raw) != (len(data) // 2048) * 2112:
            raise ValueError('OOB backup identity or geometry mismatch')
        mismatch = sum(raw[i*2112:i*2112+2048] != data[i*2048:i*2048+2048] for i in range(len(data)//2048))
        result['oob'] = {'sha256_verified': True, 'page_data_bytes': 2048, 'page_oob_bytes': 64,
                         'pages_different_after_simple_oob_strip': mismatch,
                         'note': 'Raw/ECC layout, corrected bits and bad-block mapping need analysis. This comparison does not establish corruption or restore compatibility.'}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, struct.error) as exc:
        raise SystemExit('Preflight failed: ' + str(exc))
