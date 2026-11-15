#!/usr/bin/env python3
"""Memory report boundaries using synthetic ELF sections and program headers."""
import importlib.util
import struct
from pathlib import Path

spec = importlib.util.spec_from_file_location('memory_report', Path(__file__).with_name('memory-report.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def image(base, end, filesz=16, bss=16):
    data = bytearray(512)
    ident = b'\x7fELF\x01\x01\x01' + bytes(9)
    entry = base if base == m.BOARD['P5_FLASH_BASE'] else base + 1
    struct.pack_into('<16sHHIIIIIHHHHHH', data, 0, ident, 2, 40, 1, entry, 52, 256, 0, 52, 32, 1, 40, 2, 0)
    struct.pack_into('<8I', data, 52, 1, 128, base, base, filesz, filesz, 7, 4)
    struct.pack_into('<10I', data, 256, 0, 1, 2, base, 128, filesz, 0, 0, 4, 0)
    struct.pack_into('<10I', data, 296, 0, 8, 3, end - bss, 0, bss, 0, 0, 4, 0)
    return data


def rejects(data, package=None):
    try:
        m.report(data, package)
    except (ValueError, struct.error):
        return
    raise AssertionError('Accepted invalid image')


base = m.BOARD['P5_FLASH_BASE']
r = m.report(image(base, base + 0x20000))
assert r['bss_bytes'] == 16 and r['load_filesz_bytes'] == 16
assert r['load_memsz_bytes'] == 16 and r['package_bytes'] is None
assert r['load_free_bytes'] == m.BOARD['P5_FLASH_CODE_END'] - base - 16
assert r['allocated_end'] == base + 0x20000
assert r['tlb_start'] == base + 0x20000
assert r['runtime_free_after_tlb_bytes'] == 0x4aa00000 - base - 0x24000
# Last 64 KiB boundary with room for the real 16 KiB table.
r = m.report(image(base, 0x4a9f0000))
assert r['runtime_free_after_tlb_bytes'] == 0xc000
rejects(image(base, 0x4a9f0001))
rejects(image(base, 0x4aa00001))
rejects(image(base, base + 32)[:100])
bad = image(base, base + 32)
struct.pack_into('<I', bad, 52 + 20, m.BOARD['P5_FLASH_CODE_END'] - base + 1)
rejects(bad)
ram = m.BOARD['P5_RAM_BASE']
r = m.report(image(ram, ram + 0x20000))
assert r['target'] == 'ram' and r['load_window_bytes'] == m.BOARD['P5_RAM_END'] - ram
rejects(image(ram, ram + 0x20000), b'package')
rejects(image(base + 1, base + 32))
print('PASS: load/BSS accounting, TLB alignment and limits, flash/RAM windows and malformed ELF rejection')
