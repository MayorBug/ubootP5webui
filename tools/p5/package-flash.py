#!/usr/bin/env python3
"""Use the upstream Qualcomm packer with the stock P5 4 KiB hash alignment."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mbn_tools_py3
import elftombn_py3
mbn_tools_py3.ELF_BLOCK_ALIGN = 0x1000
elftombn_py3.main()


# Keep the physical load segment page aligned, like stock P5 APPSBL.
# This changes only file placement and the ELF-header hash; code is unchanged.
import struct
import hashlib
output = Path(sys.argv[sys.argv.index('-o') + 1])
b = bytearray(output.read_bytes())
h = struct.unpack_from('<16sHHIIIIIHHHHHH', b)
assert h[10] == 4 and h[6] == 0
load_at = h[5] + 2 * 32
load = list(struct.unpack_from('<8I', b, load_at))
payload = bytes(b[load[1]:load[1]+load[4]])
assert load[0] == 1 and load[2] == load[3] == 0x4a920000
assert len(payload) == load[4] and load[1] >= 0x2000
b = b[:0x2080] + bytearray(0x3000 - 0x2080) + payload
load[1], load[7] = 0x3000, 0x1000
struct.pack_into('<8I', b, load_at, *load)
# ARM PIC flag is unnecessary for the fixed-address image; match stock EABI flags.
struct.pack_into('<I', b, 36, h[7] & ~2)
hash_ph = struct.unpack_from('<8I', b, h[5] + 32)
b[hash_ph[1]+40:hash_ph[1]+72] = hashlib.sha256(b[:52+4*32]).digest()
output.write_bytes(b)
print('PASS: stock-compatible load file offset 0x3000, alignment 0x1000 and recomputed header hash')
