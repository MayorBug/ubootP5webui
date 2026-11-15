#!/usr/bin/env python3
"""Check final P5 package file/load/hash bounds before publishing it.

Matches p5_install_check's package memory limits. BSS and TLB allocations
are checked separately by prepare-flash-elf.py and may use a wider window.
"""
from pathlib import Path
import struct
import sys
from board import CONSTANTS as BOARD


def check(data):
    base, end = BOARD['P5_FLASH_BASE'], BOARD['P5_FLASH_CODE_END']
    if not 52 <= len(data) <= BOARD['P5_APPSBL_SIZE']:
        raise ValueError('Package exceeds APPSBL or lacks an ELF header')
    h = struct.unpack_from('<16sHHIIIIIHHHHHH', data)
    if (data[:7] != b'\x7fELF\x01\x01\x01' or h[1:5] != (2, 40, 1, base)
            or h[5] != 52 or h[8:11] != (52, 32, 4)):
        raise ValueError('Expected the four-header fixed-address P5 ELF package')
    ph = [struct.unpack_from('<8I', data, h[5] + i * 32) for i in range(4)]
    for p in ph:
        if p[1] > len(data) or p[4] > len(data) - p[1]:
            raise ValueError('Package segment exceeds file')
    load, hashed = ph[2], ph[1]
    if ([i for i, p in enumerate(ph) if p[0] == 1] != [2]
            or load[2] != base or load[3] != base or load[4] > load[5]
            or load[5] > end - base):
        raise ValueError('PT_LOAD exceeds P5 installation memory window')
    if ([i for i, p in enumerate(ph) if p[6] & 0x07000000 == 0x02000000] != [1]
            or hashed[0] != 0 or hashed[2] != hashed[3]
            or not base <= hashed[3] <= end or hashed[4] > hashed[5]
            or hashed[5] > end - hashed[3] or hashed[1] & 3
            or hashed[4] != 40 + 4 * 32):
        raise ValueError('Hash segment exceeds P5 installation memory window')
    mbn = struct.unpack_from('<10I', data, hashed[1])
    # Production checks this extent as well as the hash segment's p_memsz.
    if mbn[4] != 4 * 32 or hashed[1] + 40 + mbn[4] > end - hashed[3]:
        raise ValueError('MBN hash extent exceeds P5 installation memory window')


if __name__ == '__main__':
    try:
        check(Path(sys.argv[1]).read_bytes())
        print('PASS: final package file, PT_LOAD and hash bounds match P5 installation limits')
    except (ValueError, struct.error, OSError, IndexError) as exc:
        sys.exit('Flash package rejected: ' + str(exc))
