#!/usr/bin/env python3
"""Verify generated responses are linked into ELF, then extract them for tests."""
import gzip
from pathlib import Path
import re
import sys

source, output = map(Path, sys.argv[1:])
data = source.read_bytes()
if data[:7] != b'\x7fELF\x01\x01\x01':
    raise ValueError('Expected little-endian ELF32')
# The RAM ELF is stripped. Match complete generated arrays in the image instead
# of depending on a symbol table. A stale fsdata.c fails this comparison.
fsdata = source.resolve().parent / 'failsafe/fsdata-generated.c'
if not fsdata.is_file():
    raise ValueError('Missing generated web data for ELF: ' + str(fsdata))
arrays = re.findall(r'static const char data_(\w+)\[\] = \{([^}]+)\}', fsdata.read_text())
output.mkdir(parents=True, exist_ok=True)
for name, array in arrays:
    raw = bytes(int(value, 16) for value in re.findall(r'0x([0-9a-fA-F]{2})', array))
    if raw not in data:
        raise ValueError('Generated resource is not linked into ELF: ' + name)
    headers, payload = raw.split(b'\r\n\r\n', 1)
    if b'Content-Encoding: gzip' not in headers:
        raise ValueError('Unexpected uncompressed response: ' + name)
    filename, suffix = name.rsplit('_', 1)
    (output / (filename + '.' + suffix)).write_bytes(gzip.decompress(payload))
expected = sum(1 for f in (Path(__file__).resolve().parents[2] / 'failsafe/html/pig').iterdir() if f.suffix in ('.html', '.js', '.css', '.svg'))
if len(arrays) != expected:
    raise ValueError('Expected all source web resources, found ' + str(len(arrays)))
print('PASS: ' + str(len(arrays)) + ' generated gzip responses match the linked ELF and extract correctly')
