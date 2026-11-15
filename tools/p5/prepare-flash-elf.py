#!/usr/bin/env python3
"""Check the flash-linked allocation window and mark fixed-address ELF ET_EXEC."""
import struct
import importlib.util
import sys
from pathlib import Path
from board import CONSTANTS as BOARD

p = Path(sys.argv[1])
b = bytearray(p.read_bytes())
h = struct.unpack_from('<16sHHIIIIIHHHHHH', b)
assert b[:7] == b'\x7fELF\x01\x01\x01' and h[1] in (2, 3) and h[2] == 40 and h[4] == BOARD['P5_FLASH_BASE']
assert h[11] == 40 and h[12]
end = 0
for i in range(h[12]):
    s = struct.unpack_from('<10I', b, h[6] + i * h[11])
    if s[2] & 2 and s[5]:
        assert BOARD['P5_FLASH_BASE'] <= s[3] < s[3] + s[5] <= 0x4aa00000, 'Allocated section outside stock bootloader region'
        end = max(end, s[3] + s[5])
tlb = (end + 0xffff) & ~0xffff
assert tlb + 0x4000 <= 0x4aa00000, 'TLB would leave stock reserved region'
# CONFIG_IPQ_NO_RELOC uses this exact address. Preserve relocation records;
# this changes the container declaration, not code generation or section data.
struct.pack_into('<H', b, 16, 2)
p.write_bytes(b)
print(f'PASS: fixed ARM entry 0x4a920000, allocated end {end:#x}, TLB {tlb:#x}')

spec=importlib.util.spec_from_file_location('dtcheck',Path(__file__).with_name('check-embedded-dtb.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
print('PASS: flash-linked DTB table contains one P5 tree,',m.validate(m.embedded(bytes(b)),0x4a8e0000),'bytes')
