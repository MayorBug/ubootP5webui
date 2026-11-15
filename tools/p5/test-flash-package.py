#!/usr/bin/env python3
"""Exercise final-package bounds using a real package; no router access."""
import importlib.util
from pathlib import Path
import struct

directory = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('packagecheck', directory / 'check-flash-package.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = (directory.parents[1] / 'out-p5-flash/candidate/p5-uboot.mbn').read_bytes()
module.check(original)
base, end = module.BOARD['P5_FLASH_BASE'], module.BOARD['P5_FLASH_CODE_END']


def changed(index, field, value):
    data = bytearray(original)
    struct.pack_into('<I', data, 52 + index * 32 + field * 4, value)
    return data


def reject(name, data):
    try:
        module.check(data)
    except (ValueError, struct.error):
        print('PASS:', name)
    else:
        raise AssertionError('Accepted invalid package: ' + name)


def hash_at(address):
    data = changed(1, 2, address)
    struct.pack_into('<I', data, 52 + 32 + 12, address)
    return data


# Exact limits remain accepted; one byte beyond must fail. These mutations
# test bounds only: test-install.py covers the production parser and hashes.
module.check(changed(2, 5, end - base))
reject('PT_LOAD one byte beyond limit', changed(2, 5, end - base + 1))
hash_address = struct.unpack_from('<I', original, 52 + 32 + 12)[0]
module.check(changed(1, 5, end - hash_address))
reject('hash memory one byte beyond limit', changed(1, 5, end - hash_address + 1))
reject('hash address below image', hash_at(base - 1))
reject('hash address above limit', hash_at(end + 1))
reject('PT_LOAD file extent truncated', changed(2, 4, len(original)))
reject('APPSBL capacity exceeded', original + bytes(module.BOARD['P5_APPSBL_SIZE'] + 1 - len(original)))
# Hash p_memsz can fit while the separate MBN offset/size extent does not.
hash_offset = struct.unpack_from('<I', original, 52 + 32 + 4)[0]
mbn_limit = end - hash_offset - 40 - 4 * 32
module.check(hash_at(mbn_limit))
reject('MBN hash extent one byte beyond limit', hash_at(mbn_limit + 1))
reject('truncated program header table', original[:100])
print('PASS: current final package and exact boundaries; oversized/truncated package ranges rejected')
