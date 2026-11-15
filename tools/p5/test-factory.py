#!/usr/bin/env python3
"""Run production factory-UBI validation and writes against a mock NAND."""
from pathlib import Path
import ctypes as C
import hashlib
import os
import struct
import subprocess as sp
import tempfile
import zlib
from test_support import mibib_fixture
import storage as model

ROOT = Path(__file__).resolve().parents[2]
BLOCK, VID, DATA = 0x20000, 2048, 4096
LEB = BLOCK - DATA
LAYOUT = 0x7FFFEFFF
crc = lambda b: zlib.crc32(b) ^ 0xFFFFFFFF


def table_record(reserved=0, name='', flags=0):
    b = bytearray(172)
    if reserved:
        struct.pack_into('>IIIBBH', b, 0, reserved, 1, 0, 1, 0, len(name))
        b[16:16 + len(name)] = name.encode()
        b[144] = flags
    struct.pack_into('>I', b, 168, crc(b[:168]))
    return bytes(b)


def peb(vol, lnum, payload, sequence=17):
    b = bytearray(b'\xff' * BLOCK)
    ec = bytearray(64)
    struct.pack_into('>IB3xQIII', ec, 0, 0x55424923, 1, 0, VID, DATA, sequence)
    struct.pack_into('>I', ec, 60, crc(ec[:60]))
    vh = bytearray(64)
    struct.pack_into('>IBBBBII', vh, 0, 0x55424921, 1, 1, 0, 5 if vol == LAYOUT else 0, vol, lnum)
    struct.pack_into('>I', vh, 60, crc(vh[:60]))
    b[:64], b[VID:VID+64] = ec, vh
    b[DATA:DATA+len(payload)] = payload
    return bytes(b)


def fit(directory, single=True, board='cudy,p5'):
    d = Path(directory)
    dts = f'''/dts-v1/; / {{ compatible = "{board}", "qcom,ipq5018";
    partitions {{ #address-cells=<1>; #size-cells=<1>;
    partition@7c0000 {{ label="rootfs"; reg=<0x7c0000 0x{0x4800000 if single else 0x2400000:x}>; }};
    {'' if single else 'partition@2bc0000 { label="rootfs_1"; reg=<0x2bc0000 0x2400000>; };'}
    }}; }};'''
    (d/'board.dts').write_text(dts)
    sp.run(['dtc', '-q', '-I', 'dts', '-O', 'dtb', '-o', str(d/'board.dtb'), str(d/'board.dts')], check=True)
    dtb = (d/'board.dtb').read_bytes()
    kernel = b'test compressed kernel bytes' * 64
    def data_node(name, data, typ, extra=''):
        values = ' '.join(f'{b:02x}' for b in data)
        h1 = ' '.join(f'{b:02x}' for b in struct.pack('>I', zlib.crc32(data)))
        h2 = ' '.join(f'{b:02x}' for b in hashlib.sha1(data).digest())
        return f'''{name} {{ type="{typ}"; data=[{values}]; {extra}
        hash-1 {{ algo="crc32"; value=[{h1}]; }}; hash-2 {{ algo="sha1"; value=[{h2}]; }}; }};'''
    (d/'fit.dts').write_text('/dts-v1/; / { description="P5 host fixture"; images {'+
        data_node('kernel-1', kernel, 'kernel', 'arch="arm64"; os="linux"; compression="lzma";')+
        data_node('fdt-1', dtb, 'flat_dt', 'compression="none";')+
        '}; configurations { default="config@mp03.3"; config@mp03.3 { kernel="kernel-1"; fdt="fdt-1"; }; }; };')
    sp.run(['dtc', '-q', '-I', 'dts', '-O', 'dtb', '-o', str(d/'fit.bin'), str(d/'fit.dts')], check=True)
    return (d/'fit.bin').read_bytes()


def image(fit_blob):
    kernel = [fit_blob[i:i+LEB] for i in range(0, len(fit_blob), LEB)]
    rootfs = bytearray(128)
    rootfs[:4] = b'hsqs'
    struct.pack_into('<Q', rootfs, 40, len(rootfs))
    vt = table_record(len(kernel), 'kernel') + table_record(1, 'rootfs') + table_record(1, 'rootfs_data', 1)
    vt += table_record() * 125
    return b''.join([peb(LAYOUT, 0, vt), peb(LAYOUT, 1, vt)] +
        [peb(0, i, p) for i, p in enumerate(kernel)] + [peb(1, 0, rootfs)])


PRELUDE = r'''
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <sys/mman.h>
#include <openssl/sha.h>
#include <zlib.h>
#include <libfdt.h>
typedef unsigned long ulong; typedef uint32_t u32; typedef uint8_t u8; typedef uint64_t u64;
#include "p5_board.h"
struct p5_part { char name[16]; u32 start,blocks,attr; };
#define DECLARE_GLOBAL_DATA_PTR
#define WATCHDOG_RESET() do {} while (0)
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
struct board { ulong bi_arch_number; }; struct global { struct board *bd; };
static struct board bd;static struct global global={&bd};static struct global *gd=&global;
typedef struct { u64 size;u32 writesize,erasesize; } nand_info_t;
static nand_info_t nand_info[1];
static u8 *flash;
static int secure, bad, fail_erase, fail_write, corrupt, writes, erases, active, boot_error;
#define _SMEM_DUAL_BOOTINFO_MAGIC_END 0xb3b2b1b0
struct per_part_info {char name[16];u32 primaryboot;};
static struct {struct per_part_info per_part_entry[16];u32 numaltpart,magic_end;} qca_smem_bootconfig_info;
static int smem_bootconfig_info(void){return boot_error;}
static unsigned get_rootfs_active_partition(void){return active;}
int is_sec_boot_enabled(void){return secure;}
static int nand_block_isbad(nand_info_t *m,ulong off){(void)m;return bad && off==0x800000;}
static int nand_read(nand_info_t *m,ulong off,size_t *n,void *p){
 if(m!=nand_info||off>0x8000000-*n)abort();memcpy(p,flash+off,*n);
 if(corrupt&&erases&&off>=0x7c0000)((u8*)p)[0]^=1;return 0;}
struct erase_info {nand_info_t *mtd;ulong addr,len;};
static int mtd_erase(nand_info_t *m,struct erase_info *e){
 if(m!=nand_info||e->addr%0x20000||e->len!=0x20000||e->addr>0x8000000-e->len)abort();
 erases++;memset(flash+e->addr,255,e->len);return erases==fail_erase?-1:0;}
static int nand_write(nand_info_t *m,ulong off,size_t *n,void *p){
 if(m!=nand_info||*n!=0x20000||off%0x20000||off>0x8000000-*n)abort();writes++;
 if(writes==fail_write)return -1;memcpy(flash+off,p,*n);return 0;}
static void sha1_csum_wd(const void *p,unsigned n,u8 *out,unsigned chunk){(void)chunk;SHA1(p,n,out);}
static void sha256_csum_wd(const void *p,unsigned n,u8 *out,unsigned chunk){(void)chunk;SHA256(p,n,out);}
'''
WRAPPER = r'''
void setup(const void *mibib){
 static int mapped;
 if(!mapped){
 if(mmap((void*)0x4b000000,0x4000000,3,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0)==MAP_FAILED)abort();
 flash=malloc(0x8000000);if(!flash)abort();mapped=1;}
 memset(flash,0x5a,0x8000000);memcpy(flash+0x80000,mibib,0x80000);
 bd.bi_arch_number=0x8040002;nand_info[0]=(nand_info_t){0x8000000,2048,0x20000};
 memset(&qca_smem_bootconfig_info,0,sizeof(qca_smem_bootconfig_info));qca_smem_bootconfig_info.magic_end=_SMEM_DUAL_BOOTINFO_MAGIC_END;
 secure=bad=fail_erase=fail_write=corrupt=writes=erases=active=boot_error=factory_dirty=0;}
void upload(const void *p,unsigned n){if(n>0x2000000)abort();memcpy((void*)0x4b000000,p,n);}
void faults(int s,int b,int e,int w,int c){secure=s;bad=b;fail_erase=e;fail_write=w;corrupt=c;}
void slot(int a,int e){active=a;boot_error=e;}
int counts(void){return erases*1000+writes;}
void read_flash(unsigned at,unsigned n,void *out){memcpy(out,flash+at,n);}
'''

with tempfile.TemporaryDirectory(prefix='p5-factory-test-') as directory:
    d = Path(directory)
    production = ''
    for file in ['p5_mibib.c', 'p5_factory.c']:
        production += '\n'.join(line for line in (ROOT/'board/qca/arm/common'/file).read_text().splitlines()
                                if not line.startswith('#include')) + '\n'
    (d/'test.c').write_text(PRELUDE+production+WRAPPER)
    sp.run(['cc', '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-function', '-Wno-misleading-indentation', '-iquote', str(ROOT/'include'),
            str(d/'test.c'), '-lfdt', '-lcrypto', '-lz', '-o', str(d/'test.so')], check=True)
    lib = C.CDLL(str(d/'test.so'))
    lib.setup.argtypes = [C.c_void_p]
    lib.upload.argtypes = [C.c_void_p, C.c_uint]
    lib.p5_factory_check.argtypes = lib.p5_factory_run.argtypes = [C.c_ulong, C.c_ulong]
    lib.read_flash.argtypes = [C.c_uint, C.c_uint, C.c_void_p]
    dual = mibib_fixture()
    single = model.conversion(dual, True)
    checks = 0
    def setup(layout=single): lib.setup(C.create_string_buffer(layout))
    def load(data): lib.upload(C.create_string_buffer(data), len(data))
    def check(data, good=True):
        global checks
        load(data)
        result = lib.p5_factory_check(0x4b000000, len(data))
        assert (result == 0) == good, ('validation', result, len(data), good)
        assert lib.counts() == 0, 'validation writes NAND'
        checks += 1
    def read(at, n):
        out = C.create_string_buffer(n);lib.read_flash(at, n, out);return out.raw
    one = image(fit(d))
    setup();check(one)
    for data in [b'', one[:-1], one[:3*BLOCK], b'\0'*len(one)]:
        setup();check(data, False)
    for offset in [0, 4, 60, VID, VID+60, DATA+168, BLOCK+DATA+16, 2*BLOCK+VID+12,
                   2*BLOCK+DATA, one.index(b'test compressed kernel bytes'), one.index(hashlib.sha1(b'test compressed kernel bytes' * 64).digest()), 3*BLOCK+DATA]:
        broken = bytearray(one);broken[offset] ^= 1
        setup();check(bytes(broken), False)
    # CRC/header consistency does not turn a malformed volume/sequence into a valid image.
    for off, relative, value in [(2*BLOCK, VID+8, 7), (2*BLOCK, VID+12, 1), (3*BLOCK, 24, 123),
                                 (2*BLOCK, VID+28, 1)]:
        broken = bytearray(one);struct.pack_into('>I', broken, off+relative, value)
        h = off+(VID if relative >= VID else 0)
        struct.pack_into('>I', broken, h+60, crc(broken[h:h+60]))
        setup();check(bytes(broken), False)
    setup();check(image(fit(d, board='other,router')), False)
    dual_image = image(fit(d, single=False))
    setup();check(dual_image, False)
    setup(dual);check(one, False)
    setup(dual);check(dual_image)
    for secure,bad in [(1,0),(-1,0),(0,1)]:
        setup();lib.faults(secure,bad,0,0,0);check(one,False)
    setup(dual);lib.slot(0,1);check(dual_image,False)
    setup(dual);lib.slot(2,0);check(dual_image,False)
    # All writes and erases stay inside the selected firmware region.
    for layout,data,slot,start,length in [(single,one,0,0x7c0000,0x4800000),
                                        (dual,dual_image,0,0x7c0000,0x2400000),
                                        (dual,dual_image,1,0x2bc0000,0x2400000)]:
        setup(layout);lib.slot(slot,0);load(data)
        before = hashlib.sha256(read(0,start)).digest()
        after = hashlib.sha256(read(start+length,0x8000000-start-length)).digest()
        assert lib.p5_factory_run(0x4b000000,len(data)) == 0
        assert read(start,len(data)) == data
        assert read(start+len(data),length-len(data)) == b'\xff'*(length-len(data))
        assert hashlib.sha256(read(0,start)).digest() == before
        assert hashlib.sha256(read(start+length,0x8000000-start-length)).digest() == after
        assert not lib.p5_factory_boot_blocked()
        checks += 1
    for erase,write,corrupt in [(1,0,0),(0,1,0),(0,0,1),(0,0,0)]:
        setup();load(one);lib.faults(0,0,erase,write,corrupt)
        if not (erase or write or corrupt):
            # Fail while erasing the unused tail, after all image blocks were written.
            lib.faults(0,0,len(one)//BLOCK+1,0,0)
        assert lib.p5_factory_run(0x4b000000,len(one)) != 0
        assert lib.p5_factory_boot_blocked()
        checks += 1
    # Optionally validate the current real OpenWrt factory image without writes.
    real = os.environ.get('P5_FACTORY_IMAGE')
    if real:
        setup();check(Path(real).read_bytes())
        print('PASS: real OpenWrt factory UBI parser and embedded P5 FIT/layout checks')
    print(f'PASS: {checks} factory checks; production C, all headers/FIT hashes, wrong-board/layout rejection, active-slot scope and write/readback failure blocking')
