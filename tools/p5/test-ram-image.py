#!/usr/bin/env python3
"""Check image-validator rejection paths against a real linked U-Boot ELF."""
import importlib.util
import struct
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location('ramcheck', Path(__file__).with_name('check-ram-image.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = Path(sys.argv[1]).read_bytes()
assert module.check(original) >= 1
header = struct.unpack_from('<16sHHIIIIIHHHHHH', original)
phoff, shoff, phsize, phnum, shsize, shnum = header[5], header[6], header[9], header[10], header[11], header[12]


def rejects(name, data):
    try:
        module.check(data)
    except (ValueError, struct.error):
        print('PASS:', name)
    else:
        raise AssertionError('Accepted unsafe image: ' + name)


bad = bytearray(original)
struct.pack_into('<H', bad, 16, 3)
rejects('ET_DYN rejected by stock bootelf', bad)
bad = bytearray(original)
struct.pack_into('<I', bad, 24, 0x4a920001)
rejects('wrong entry address', bad)
bad = bytearray(original)
first_load = next(phoff + i * phsize for i in range(phnum) if struct.unpack_from('<I', bad, phoff + i * phsize)[0] == 1)
file_offset = struct.unpack_from('<I', bad, first_load + 4)[0]
bad[file_offset:file_offset + 4] = bytes(4)
rejects('missing Thumb entry stub', bad)
bad = bytearray(original)
bss = next(shoff + i * shsize for i in range(shnum) if struct.unpack_from('<I', bad, shoff + i * shsize + 4)[0] == 8)
struct.pack_into('<I', bad, bss + 12, module.LIMIT)
rejects('BSS outside allowed memory', bad)
bad = bytearray(original)
struct.pack_into('<I', bad, first_load, 3)
rejects('unexpected ELF interpreter', bad)
rejects('truncated ELF section table', original[:shoff + 8])
print('PASS: original linked image; six unsafe variants rejected')
