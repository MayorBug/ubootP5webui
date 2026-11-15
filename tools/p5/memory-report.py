#!/usr/bin/env python3
"""Report linked P5 memory use. Does not modify images or access hardware."""
import argparse
import importlib.util
import struct
from pathlib import Path
from board import CONSTANTS as BOARD


def elf(data):
    if len(data) < 52 or data[:7] != b'\x7fELF\x01\x01\x01':
        raise ValueError('Expected little-endian ELF32')
    h = struct.unpack_from('<16sHHIIIIIHHHHHH', data)
    if h[2] != 40 or h[9] != 32 or h[11] != 40:
        raise ValueError('Expected ARM ELF32 headers')
    for offset, size, count in [(h[5], h[9], h[10]), (h[6], h[11], h[12])]:
        if offset > len(data) or count * size > len(data) - offset:
            raise ValueError('Truncated ELF header table')
    loads = [struct.unpack_from('<8I', data, h[5] + i * h[9]) for i in range(h[10])]
    loads = [p for p in loads if p[0] == 1]
    sections = [struct.unpack_from('<10I', data, h[6] + i * h[11]) for i in range(h[12])]
    allocated = [s for s in sections if s[2] & 2 and s[5]]
    if not loads or not allocated:
        raise ValueError('Missing load segments or allocated sections')
    for p in loads:
        if p[2] != p[3] or p[4] > p[5] or p[1] > len(data) or p[4] > len(data) - p[1]:
            raise ValueError('Invalid load segment')
    for s in allocated:
        if s[1] != 8 and (s[4] > len(data) or s[5] > len(data) - s[4]):
            raise ValueError('Truncated allocated section')
    return h[4], loads, allocated


def report(image, package=None):
    entry, loads, sections = elf(image)
    flash = entry == BOARD['P5_FLASH_BASE']
    if not flash and entry != BOARD['P5_RAM_BASE'] + 1:
        raise ValueError('Not a fixed-address P5 image')
    base = BOARD['P5_FLASH_BASE'] if flash else BOARD['P5_RAM_BASE']
    # This is the reserved region checked by prepare-flash-elf.py.
    runtime_end = 0x4aa00000 if flash else BOARD['P5_RAM_END']
    end = max(s[3] + s[5] for s in sections)
    if any(not base <= s[3] < s[3] + s[5] <= runtime_end for s in sections):
        raise ValueError('Allocated sections exceed the runtime window')
    tlb = (end + 0xffff) & ~0xffff
    if tlb + 0x4000 > runtime_end:
        raise ValueError('TLB exceeds the runtime window')
    package_size = None
    if package is not None:
        if not flash:
            raise ValueError('A flash package cannot accompany a RAM image')
        spec = importlib.util.spec_from_file_location('package_check', Path(__file__).with_name('check-flash-package.py'))
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        checker.check(package)
        header = struct.unpack_from('<16sHHIIIIIHHHHHH', package)
        loads = [struct.unpack_from('<8I', package, header[5] + i * header[9]) for i in range(header[10])]
        loads = [p for p in loads if p[0] == 1]
        package_size = len(package)
    load_end = max(p[3] + p[5] for p in loads)
    limit = BOARD['P5_FLASH_CODE_END'] if flash else BOARD['P5_RAM_END']
    if any(not base <= p[3] < p[3] + p[5] <= limit for p in loads):
        raise ValueError('Load segments exceed the image window')
    return {
        'target': 'flash' if flash else 'ram', 'elf_bytes': len(image),
        'package_bytes': package_size,
        'package_free_bytes': BOARD['P5_APPSBL_SIZE'] - package_size if package_size is not None else None,
        'load_filesz_bytes': sum(p[4] for p in loads), 'load_memsz_bytes': sum(p[5] for p in loads),
        'load_window_bytes': limit - base, 'load_free_bytes': limit - load_end,
        'bss_bytes': sum(s[5] for s in sections if s[1] == 8),
        'allocated_end': end, 'runtime_window_bytes': runtime_end - base,
        'tlb_start': tlb, 'tlb_bytes': 0x4000, 'runtime_free_after_tlb_bytes': runtime_end - tlb - 0x4000,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('elf', type=Path)
    parser.add_argument('--package', type=Path)
    args = parser.parse_args()
    try:
        result = report(args.elf.read_bytes(), args.package.read_bytes() if args.package else None)
    except (OSError, ValueError, struct.error) as error:
        parser.exit(1, f'Memory report rejected: {error}\n')
    print(f"P5 {result['target']} memory report")
    for key, value in result.items():
        if key != 'target' and value is not None:
            print(f'{key}: {value:#x}' if key in ('allocated_end', 'tlb_start') else f'{key}: {value}')
    print('Free runtime space excludes malloc, stack and other live allocations.')


if __name__ == '__main__':
    main()
