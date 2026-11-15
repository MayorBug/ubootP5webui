#!/usr/bin/env python3
"""Validate this fixed-address ARM/Thumb RAM image, including BSS.

This checks the image, not the stock loader's live allocations or cache state.
This RAM target links ET_EXEC because the stock bootelf rejects ET_DYN.
"""
import struct
import sys
from pathlib import Path

BASE = 0x46000000
LIMIT = 0x46800000


def window(addr, size):
    if size and not BASE <= addr < addr + size <= LIMIT:
        raise ValueError('Allocated image memory exceeds provisional RAM window')


def check(data):
    if data[:7] != b'\x7fELF\x01\x01\x01':
        raise ValueError('Expected little-endian ELF32')
    header = struct.unpack_from('<16sHHIIIIIHHHHHH', data)
    _, kind, machine, _, entry, phoff, shoff, _, _, phsize, phnum, shsize, shnum, _ = header
    if kind != 2 or machine != 40 or entry != BASE + 1:
        raise ValueError('Expected ARM ELF with Thumb entry at 0x46000001')
    if phsize != 32 or not phnum or shsize != 40 or not shnum:
        raise ValueError('Missing load or section headers')
    count = 0
    stub_checked = False
    for i in range(phnum):
        typ, off, va, pa, filesz, memsz, flags, align = struct.unpack_from('<8I', data, phoff + i * phsize)
        if typ in (2, 3):
            raise ValueError('Dynamic loader/interpreter is not supported')
        if typ != 1:
            continue
        count += 1
        if va != pa or filesz > memsz or off + filesz > len(data):
            raise ValueError('Unexpected load segment mapping')
        window(pa, memsz)
        if pa == BASE and data[off:off + 4] == bytes.fromhex('784700bf'):
            stub_checked = True
    if not count or not stub_checked:
        raise ValueError('Missing load segment or Thumb-to-ARM entry stub')
    for i in range(shnum):
        _, typ, flags, addr, off, size, *_ = struct.unpack_from('<10I', data, shoff + i * shsize)
        if flags & 2:  # SHF_ALLOC includes BSS, even when outside PT_LOAD.
            window(addr, size)
            if typ != 8 and off + size > len(data):
                raise ValueError('Allocated section is truncated')
    return count


if __name__ == '__main__':
    try:
        print(f'ELF and BSS window check passed: {check(Path(sys.argv[1]).read_bytes())} load segments')
    except (ValueError, struct.error, OSError, IndexError) as exc:
        sys.exit(f'RAM image rejected: {exc}')
