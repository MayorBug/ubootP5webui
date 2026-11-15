#!/usr/bin/env python3
"""Host sysupgrade strings must not require BSD functions from libc."""
from pathlib import Path
import subprocess as sp
import tempfile

root = Path(__file__).resolve().parents[2]
flags = [
    '-DUSE_HOSTCC', '-D__KERNEL_STRICT_NAMES', '-D_GNU_SOURCE',
    '-DCONFIG_SYS_TEXT_BASE=0x46000000',
    '-I' + str(root/'tools/p5/host-include'), '-I' + str(root/'tools'),
    '-I' + str(root/'lib/libfdt'), '-include', str(root/'include/libfdt_env.h'),
    '-idirafter' + str(root/'include'), '-idirafter' + str(root/'arch/arm/include'),
]
with tempfile.TemporaryDirectory(prefix='p5-host-strings-') as directory:
    d = Path(directory)
    obj = d/'sysupgrade.o'
    sp.run(['cc', *flags, '-c', str(root/'tools/sysupgrade.c'), '-o', str(obj)], check=True)
    symbols = sp.check_output(['nm', '-u', str(obj)], text=True)
    assert not {'strlcpy', 'strlcat'} & {line.split()[-1] for line in symbols.splitlines()}, symbols
    source = d/'test.c'
    source.write_text('#include "' + str(root/'tools/sysupgrade.c') + '"\n' + r'''
#include <assert.h>
int main(void)
{
    char buffer[8];
    memset(buffer, '!', sizeof(buffer));
    assert(sysupgrade_strlcpy(buffer, "abc", 0) == 3 && buffer[0] == '!');
    assert(sysupgrade_strlcpy(buffer, "abc", 1) == 3 && buffer[0] == 0);
    assert(sysupgrade_strlcpy(buffer, "abc", 4) == 3 && !strcmp(buffer, "abc"));
    assert(sysupgrade_strlcpy(buffer, "abcdefghi", 4) == 9 && !strcmp(buffer, "abc"));
    assert(buffer[4] == '!');
    assert(sysupgrade_strlcat(buffer, "def", sizeof(buffer)) == 6 && !strcmp(buffer, "abcdef"));
    assert(sysupgrade_strlcat(buffer, "ghijk", sizeof(buffer)) == 11 && !strcmp(buffer, "abcdefg"));
    assert(sysupgrade_strlcat(buffer, "", sizeof(buffer)) == 7 && !strcmp(buffer, "abcdefg"));
    memset(buffer, '!', sizeof(buffer));
    assert(sysupgrade_strlcat(buffer, "abc", 0) == 3 && buffer[0] == '!');
    assert(sysupgrade_strlcat(buffer, "abc", sizeof(buffer)) == 11);
    for (unsigned i = 0; i < sizeof(buffer); i++) assert(buffer[i] == '!');
    return 0;
}
''')
    binary = d/'test'
    sp.run(['cc', *flags, '-ffunction-sections', '-fdata-sections', str(source),
            '-Wl,--gc-sections', '-o', str(binary)], check=True)
    sp.run([str(binary)], check=True)
print('PASS: host sysupgrade needs no libc strlcpy/strlcat; copy/append bounds and truncation')
