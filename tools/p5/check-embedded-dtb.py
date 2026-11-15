#!/usr/bin/env python3
"""Validate the combined board table inside the actual P5 RAM ELF."""
import struct
import sys
import zlib
from pathlib import Path


def validate(blob, table_base=0x45fc0000):
    count, machid, address = struct.unpack_from('<III', blob)
    if count != 1 or machid != 0x08040002:
        raise ValueError('Expected exactly one P5 board entry')
    offset = address - table_base
    if offset < 12 or offset % 4 or offset + 40 > len(blob):
        raise ValueError('Invalid board DTB pointer')
    magic, size, off_struct, off_strings, off_reserve, version, compatible, cpu, strings_size, struct_size = struct.unpack_from('>10I', blob, offset)
    if magic != 0xd00dfeed or size < 40 or offset + size > len(blob):
        raise ValueError('Invalid DTB magic or size')
    if version != 17 or compatible > 17:
        raise ValueError('Unsupported DTB version')
    for start, length in ((off_struct, struct_size), (off_strings, strings_size), (off_reserve, 16)):
        if start < 40 or start + length > size:
            raise ValueError('DTB block outside tree')
    return size


def embedded(data):
    header = struct.unpack_from('<16sHHIIIIIHHHHHH', data)
    shoff, shsize, shnum, shstr = header[6], header[11], header[12], header[13]
    headers = [struct.unpack_from('<10I', data, shoff + i * shsize) for i in range(shnum)]
    strings = headers[shstr]
    names = data[strings[4]:strings[4] + strings[5]]
    for section in headers:
        if names[section[0]:].split(b'\0', 1)[0] == b'.dtb':
            payload = data[section[4]:section[4] + section[5]]
            if not payload.startswith(b'\x1f\x8b'):
                raise ValueError('Expected gzip-compressed board table')
            return zlib.decompress(payload, 31)
    raise ValueError('ELF has no embedded DTB section')


if __name__ == '__main__':
    try:
        blob = embedded(Path(sys.argv[1]).read_bytes())
        size = validate(blob)
        print(f'Embedded DTB passes: one P5 entry, {size} tree bytes')
        # Regression for the actual failure: repeated entries must be rejected.
        for count in (0, 2, 6):
            bad = bytearray(blob)
            struct.pack_into('<I', bad, 0, count)
            try:
                validate(bad)
            except ValueError:
                continue
            raise ValueError('Validator accepted duplicate/missing board entries')
        print('Regression passes: counts 0, 2 and 6 rejected')
    except (ValueError, struct.error, OSError, IndexError, zlib.error) as exc:
        sys.exit(f'Embedded DTB rejected: {exc}')
