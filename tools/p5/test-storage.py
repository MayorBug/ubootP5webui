#!/usr/bin/env python3
"""Production C on simulated NAND; vendor CRC emulation; no device access."""
from pathlib import Path
import ctypes as C
import hashlib
import importlib.util
import struct
import subprocess as sp
import tempfile
import zlib
import sys

root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(Path(__file__).parent));import storage as model
from test_support import mibib_fixture, vendor_partition_tool
VENDOR=vendor_partition_tool()
MIBIB=mibib_fixture()

def bootconfig(active=0, age=16):
    names=('0:QSEE','0:DEVCFG','0:CDT','0:APPSBL','0:HLOS','rootfs','0:WIFIFW','0:BTFW')
    record=struct.pack('<3I',0xa3a2a1a0,age,len(names))
    record+=b''.join(struct.pack('<16sI',name.encode(),active if name=='rootfs' else 0) for name in names)
    record+=bytes((16-len(names))*20)+struct.pack('<I',0xb3b2b1b0)
    return record+bytes(2048-len(record))+b'\xff'*(model.BLOCK-2048)

BOOTCONFIG=bootconfig()
# The generated secondary fixture exactly matches both saved 256 KiB vendor partitions.
assert hashlib.sha256(bootconfig(1)+b'\xff'*model.BLOCK).hexdigest()=='c38311ecd751ff5f8d8bf8d02f51b947643b5f4bbe1866100eb0ef874b453e9b'
common=r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/mman.h>
#include <openssl/sha.h>
#include <zlib.h>
typedef uint8_t u8;typedef uint32_t u32;typedef uint64_t u64;typedef unsigned long ulong;typedef unsigned char uchar;typedef unsigned int uint;
#include "p5_board.h"
typedef int cmd_tbl_t;
#define CMD_RET_FAILURE 1
#define CMD_RET_USAGE 2
#define DECLARE_GLOBAL_DATA_PTR
#define CONFIG_P5_EXPERIMENTAL_STORAGE_WRITES 1
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
int p5_factory_boot_blocked(void){return 0;}
static int factory_calls;
int p5_factory_run(ulong a,ulong b){(void)a;(void)b;factory_calls++;return 0;}
int p5_install_boot_blocked(void){return 0;}
int p5_install_run(ulong a,ulong b){(void)a;(void)b;return -1;}
#define WATCHDOG_RESET() do{}while(0)
struct p5_part {char name[16];u32 start,blocks,attr;};
static ulong simple_strtoul(const char *s,char **end,unsigned base){return strtoul(s,end,base);}
static u32 p5_crc(u32 seed,const void *b,unsigned n){return crc32(seed,b,n);}
#define crc32 p5_crc
struct board{ulong bi_arch_number;};struct global{struct board *bd;};static struct board board;static struct global global={&board};static struct global *gd=&global;
typedef struct {u64 size;u32 writesize,erasesize;} nand_info_t;static nand_info_t nand_info[1];
struct erase_info{nand_info_t *mtd;u64 addr,len;int scrub;void *callback;};
typedef SHA256_CTX sha256_context;
static void sha256_starts(sha256_context *c){SHA256_Init(c);}
static void sha256_update(sha256_context *c,const void *p,u32 n){SHA256_Update(c,p,n);}
static void sha256_finish(sha256_context *c,u8 *h){SHA256_Final(h,c);}
static void sha256_csum_wd(const void *p,unsigned n,u8 *h,unsigned chunk){(void)chunk;SHA256(p,n,h);}
static u8 *flash;
static int secure,bad,erase_count,write_count,fail_erase,fail_write,corrupt_read,partial_write;
static ulong bad_offset, fail_read_offset;
int is_sec_boot_enabled(void){return secure;}
int get_eth_mac_address(uchar *out,uint n){static const u8 mac[12]={0x80,0xaf,0xca,0x26,0xac,0x39,0x80,0xaf,0xca,0x26,0xac,0x38};if(n!=2)return -1;memcpy(out,mac,12);return 0;}
static int nand_block_isbad(nand_info_t *m,ulong off){(void)m;return bad||off==bad_offset;}
static int nand_read(nand_info_t *m,ulong off,size_t *n,void *out){if(m!=nand_info||off>0x8000000||*n>0x8000000-off||off==fail_read_offset)return -1;memcpy(out,flash+off,*n);if(corrupt_read&&write_count)((u8*)out)[0]^=1;return 0;}
static int mtd_erase(nand_info_t *m,struct erase_info *e){
 if(e->mtd!=m||e->scrub||e->callback||e->len!=P5_NAND_BLOCK||e->addr%P5_NAND_BLOCK||e->addr>P5_FLASH_BYTES-e->len)abort();
 erase_count++;memset(flash+e->addr,0xff,e->len);return erase_count==fail_erase?-1:0;}
static int nand_write(nand_info_t *m,ulong off,size_t *n,void *p){
 if(m!=nand_info||*n!=P5_NAND_BLOCK||off%P5_NAND_BLOCK||off>P5_FLASH_BYTES-*n)abort();
 write_count++;if(write_count==fail_write){if(partial_write)memcpy(flash+off,p,128);return -1;}memcpy(flash+off,p,*n);return 0;}
'''
smem=(root/'arch/arm/include/asm/arch-qca-common/smem.h').read_text()
common+='\n#define NUM_ALT_PARTITION 16\n'+smem[smem.index('#define ALT_PART_NAME_LENGTH'):smem.index('extern qca_smem_bootconfig_info_t')]
mi=(root/'board/qca/arm/common/p5_mibib.c').read_text();mi='\n'.join(l for l in mi.splitlines() if not l.startswith('#include'))
s=(root/'board/qca/arm/common/p5_storage.c').read_text();body=s[s.index('DECLARE_GLOBAL_DATA_PTR;'):s.index('\nU_BOOT_CMD')]
wrapper=r'''
void setup(const void *mibib){
 static int mapped;
 if(!mapped){if(mmap((void*)0x4b000000,0x2000000,3,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0)==MAP_FAILED)abort();if(mmap((void*)0x4d000000,0x200000,3,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0)==MAP_FAILED)abort();flash=malloc(0x8000000);if(!flash)abort();mapped=1;}
 memset(flash,255,0x8000000);memcpy(flash+0x80000,mibib,0x80000);memset(&state,0,sizeof(state));
 nand_info[0]=(nand_info_t){0x8000000,2048,0x20000};board.bi_arch_number=0x8040002;secure=bad=erase_count=write_count=fail_erase=fail_write=corrupt_read=0;}
void reboot_state(void){memset(&state,0,sizeof(state));fail_erase=fail_write=corrupt_read=0;}
void faults(int sec,int bad_arg,int erase_arg,int write_arg,int read_arg){secure=sec;bad=bad_arg;fail_erase=erase_arg;fail_write=write_arg;corrupt_read=read_arg;}
int layout(int single,const char *hash){return convert_layout(single,hash);}
void upload(const void *p,unsigned n){if(n>0x2000000)abort();memcpy((void*)UPLOAD,p,n);}
int begin(unsigned n,u32 crc,const char *token){return restore_begin(n,crc,token);}
int chunk(unsigned index,unsigned n,u32 crc){return restore_chunk(index,n,crc);}
int finish(void){return restore_finish();}
int writes(void){return write_count;}
int erases(void){return erase_count;}
int blocked(void){return p5_storage_boot_blocked();}
int next(void){return state.next;}
int uart(int n,char **v){return do_p5storage(NULL,0,n,v);}
void read_flash(unsigned off,void *out,unsigned n){memcpy(out,flash+off,n);}
void flip(unsigned off){flash[off]^=1;}
void load_flash(unsigned off,const void *p,unsigned n){memcpy(flash+off,p,n);}
void block_fault(unsigned off,int read_error){if(read_error)fail_read_offset=off;else bad_offset=off;}
void clear_block_faults(void){bad_offset=fail_read_offset=~0UL;}
void torn_write(void){partial_write=1;}
void clear_torn_write(void){partial_write=0;}
'''
with tempfile.TemporaryDirectory(prefix='p5-storage-test-') as directory:
    d=Path(directory);(d/'storage.c').write_text(common+mi+body+wrapper)
    sp.run(['cc','-shared','-fPIC','-O2','-Wall','-Wextra','-Werror','-Wno-deprecated-declarations','-Wno-unused-parameter','-Wno-unused-function','-iquote',str(root/'include'),str(d/'storage.c'),'-lcrypto','-lz','-o',str(d/'storage.so')],check=True)
    lib=C.CDLL(str(d/'storage.so'));lib.setup.argtypes=[C.c_void_p];lib.upload.argtypes=[C.c_void_p,C.c_uint];lib.layout.argtypes=[C.c_int,C.c_char_p];lib.begin.argtypes=[C.c_uint,C.c_uint,C.c_char_p];lib.read_flash.argtypes=[C.c_uint,C.c_void_p,C.c_uint]
    lib.p5_storage_execute.argtypes=[C.c_char_p,C.c_ulong,C.c_uint]
    lib.load_flash.argtypes=[C.c_uint,C.c_void_p,C.c_uint]
    lib.p5_mibib_validate.argtypes=[C.c_void_p,C.c_uint];lib.p5_mibib_make.argtypes=[C.c_void_p,C.c_void_p,C.c_uint,C.c_int,C.c_uint]
    def load(off,data):lib.load_flash(off,C.create_string_buffer(data),len(data))
    def setup(data=MIBIB,boot0=BOOTCONFIG,boot1=BOOTCONFIG):
        lib.setup(C.create_string_buffer(data));lib.clear_block_faults();lib.clear_torn_write();load(0x100000,boot0);load(0x140000,boot1)
    def read(off,n):
        out=C.create_string_buffer(n);lib.read_flash(off,out,n);return out.raw
    def upload(data):lib.upload(C.create_string_buffer(data),len(data))
    setup();upload(b'factory')
    factory_crc=zlib.crc32(b'factory')
    assert lib.p5_storage_execute(f'factory,7,{factory_crc:08x},WRONG'.encode(),7,factory_crc)!=0
    assert lib.p5_storage_execute(f'factory,7,{factory_crc:08x},FLASH-P5-FACTORY'.encode(),6,factory_crc)!=0
    assert lib.p5_storage_execute(f'factory,7,{factory_crc:08x},FLASH-P5-FACTORY'.encode(),7,factory_crc)==0
    old_hash=hashlib.sha256(MIBIB).hexdigest().encode()
    # A secondary rootfs selection must be normalized before its MIBIB entry disappears.
    setup(boot0=bootconfig(1),boot1=bootconfig(1))
    assert lib.layout(1,old_hash)==0
    for offset in (0x100000,0x140000):
        assert struct.unpack_from('<I',read(offset,model.BLOCK),128)[0]==0, 'secondary rootfs remains selected'
    assert lib.writes()==4
    assert lib.p5_storage_boot_blocked()
    # BOOTCONFIG updates retain the newest unrelated selectors and each copy's opaque bytes.
    older=bytearray(bootconfig(1,16));newer=bytearray(bootconfig(1,20))
    struct.pack_into('<I',newer,88,1)  # APPSBL selection belongs to another operation.
    older[1024]=0x35;newer[1024]=0x57
    for first,second in ((older,newer),(newer,older)):
        setup(boot0=bytes(first),boot1=bytes(second))
        load(0x120000,b'\x71'*model.BLOCK);load(0x160000,b'\x93'*model.BLOCK)
        assert lib.layout(1,old_hash)==0 and lib.writes()==4
        for offset,original,age in ((0x100000,first,21 if first is older else 22),
                                   (0x140000,second,21 if second is older else 22)):
            want=bytearray(original);want[:336]=newer[:336]
            struct.pack_into('<I',want,4,age);struct.pack_into('<I',want,128,0)
            assert read(offset,model.BLOCK)==want
        assert read(0x120000,model.BLOCK)==b'\x71'*model.BLOCK
        assert read(0x160000,model.BLOCK)==b'\x93'*model.BLOCK
    # Bad records and ambiguous equal-age records refuse before changing either metadata type.
    invalid=[]
    for offset,value in ((0,0xa3a2a1a1),(8,0),(8,17),(128,2),(332,0)):
        bad=bytearray(bootconfig(1));struct.pack_into('<I',bad,offset,value);invalid.append(bytes(bad))
    bad=bytearray(bootconfig(1));bad[112:128]=b'rootfs_1'+bytes(8);invalid.append(bytes(bad))
    bad=bytearray(bootconfig(1));bad[12:28]=b'rootfs'+bytes(10);invalid.append(bytes(bad))
    bad=bytearray(bootconfig(1));bad[12:28]=b'x'*16;invalid.append(bytes(bad))
    for bad in invalid:
        for first,second in ((bad,bootconfig(1)),(bootconfig(1),bad)):
            setup(boot0=first,boot1=second)
            assert lib.layout(1,old_hash)!=0 and lib.erases()==0
            assert read(0x80000,model.MIBIB_SIZE)==MIBIB
    for first,second in ((bootconfig(0),bootconfig(1)),
                         (b'\xff'*model.BLOCK,b'\xff'*model.BLOCK),
                         (bootconfig(1,0xfffffffd),bootconfig(1,0xfffffffd))):
        setup(boot0=first,boot1=second)
        assert lib.layout(1,old_hash)!=0 and lib.erases()==0
    aged=bytearray(MIBIB)
    for offset in (0,model.BLOCK):
        struct.pack_into('<I',aged,offset+12,0xfffffffd)
        struct.pack_into('<I',aged,offset+6156,model.crc(aged[offset:offset+6144]))
    setup(bytes(aged),bootconfig(1),bootconfig(1))
    assert lib.layout(1,hashlib.sha256(aged).hexdigest().encode())!=0 and lib.erases()==0
    for offset in (0x100000,0x140000):
        for read_error in (False,True):
            setup(boot0=bootconfig(1),boot1=bootconfig(1));lib.block_fault(offset,read_error)
            assert lib.layout(1,old_hash)!=0 and lib.erases()==0
    # Do not touch BOOTCONFIG for dual layout, or for a stale MIBIB confirmation hash.
    setup(boot0=bootconfig(1),boot1=bootconfig(1))
    assert lib.layout(0,old_hash)==1 and lib.erases()==0
    assert lib.layout(1,b'0'*64)!=0 and lib.erases()==0
    # BOOTCONFIG failures leave MIBIB intact. Every write failure retains a valid record.
    for reboot in (False,True):
        for kind in ('erase','write','read','erase-second','write-second'):
            setup(boot0=bootconfig(1),boot1=bootconfig(1))
            lib.faults(0,0,1 if kind=='erase' else (2 if kind=='erase-second' else 0),
                       1 if kind=='write' else (2 if kind=='write-second' else 0),kind=='read')
            assert lib.layout(1,old_hash)!=0 and lib.p5_storage_boot_blocked()
            assert read(0x80000,model.MIBIB_SIZE)==MIBIB
            assert any(read(offset,4)==struct.pack('<I',0xa3a2a1a0) and
                       read(offset+332,4)==struct.pack('<I',0xb3b2b1b0) for offset in (0x100000,0x140000))
            if reboot:lib.reboot_state()
            lib.faults(0,0,0,0,0)
            assert lib.layout(1,old_hash)==0 and lib.p5_storage_boot_blocked()
            assert model.select(read(0x80000,model.MIBIB_SIZE))[1]['single']
            for offset in (0x100000,0x140000):assert read(offset+128,4)==bytes(4)
    # A partly programmed record is not an erased copy. Keep recovery blocked instead of guessing.
    setup(boot0=bootconfig(1),boot1=bootconfig(1));lib.torn_write();lib.faults(0,0,0,1,0)
    assert lib.layout(1,old_hash)!=0 and lib.p5_storage_boot_blocked()
    assert read(0x100000,model.BLOCK)==bootconfig(1) and read(0x80000,model.MIBIB_SIZE)==MIBIB
    assert read(0x140000,128)==bootconfig(1,17)[:128] and read(0x140000+128,model.BLOCK-128)==b'\xff'*(model.BLOCK-128)
    erases=lib.erases();lib.faults(0,0,0,0,0)
    assert lib.layout(1,old_hash)!=0 and lib.erases()==erases and lib.p5_storage_boot_blocked()
    # Repair secondary selection even when the MIBIB layout already matches single.
    single_map=model.conversion(MIBIB,True)
    setup(single_map)
    assert lib.p5_storage_execute(f'layout,single,{hashlib.sha256(single_map).hexdigest()},CONVERT-P5-LAYOUT'.encode(),0,0)==1
    assert lib.writes()==0 and not lib.p5_storage_boot_blocked()
    setup(single_map,bootconfig(1),bootconfig(1))
    assert lib.layout(1,hashlib.sha256(single_map).hexdigest().encode())==0 and lib.writes()==2
    assert read(0x80000,model.MIBIB_SIZE)==single_map and lib.p5_storage_boot_blocked()
    # Missing erased copies can be rebuilt from one valid record, without guessing unknown formats.
    for first,second in ((bootconfig(1),b'\xff'*model.BLOCK),(b'\xff'*model.BLOCK,bootconfig(1))):
        setup(boot0=first,boot1=second)
        assert lib.layout(1,old_hash)==0 and lib.writes()==4
    # MIBIB interruption after BOOTCONFIG success also supports retries with the fresh hash.
    for failure in (3,4):
        setup(boot0=bootconfig(1),boot1=bootconfig(1));lib.faults(0,0,0,failure,0)
        assert lib.layout(1,old_hash)!=0 and lib.p5_storage_boot_blocked()
        for offset in (0x100000,0x140000):assert read(offset+128,4)==bytes(4)
        current=read(0x80000,model.MIBIB_SIZE);lib.faults(0,0,0,0,0)
        assert lib.layout(1,hashlib.sha256(current).hexdigest().encode())==0
    print('PASS: secondary BOOTCONFIG normalization, newest-record selection, opaque/protected bytes, malformed/ambiguous/overflow refusal, copy failures and retries, MIBIB ordering and repair')
    # Exact vendor bytes and Python/C agreement, including the inverse conversion.
    for i in range(2):assert lib.p5_mibib_validate(C.create_string_buffer(MIBIB[i*model.BLOCK:(i+1)*model.BLOCK]),model.BLOCK)==0
    out=C.create_string_buffer(model.BLOCK);src=C.create_string_buffer(MIBIB[model.BLOCK:2*model.BLOCK]);rr=lib.p5_mibib_make(out,src,model.BLOCK,1,2)
    if rr:
        print('MAKE RESULT',rr,flush=True)
        try:print(model.parse_copy(out.raw),flush=True)
        except Exception as e:print('PARSE',str(e),flush=True)
    setup();result=lib.layout(1,old_hash);assert result==0 and lib.writes()==2,(result,lib.writes(),lib.erases())
    single=read(0x80000,model.MIBIB_SIZE);assert single==model.conversion(MIBIB,True)
    lib.reboot_state();assert lib.layout(0,hashlib.sha256(single).hexdigest().encode())==0
    dual=read(0x80000,model.MIBIB_SIZE);assert dual==model.conversion(single,False)
    assert read(0,0x80000)==b'\xff'*0x80000 and read(0x100000,model.BLOCK)==BOOTCONFIG
    for sec,bad in [(1,0),(-1,0),(0,1)]:
        setup();lib.faults(sec,bad,0,0,0);assert lib.layout(1,old_hash)!=0 and lib.writes()==0
    setup();assert lib.layout(1,b'0'*64)!=0 and lib.writes()==0
    for kind in ('erase','write','read','erase-second','write-second'):
        setup();lib.faults(0,0,1 if kind=='erase' else (2 if kind=='erase-second' else 0),1 if kind=='write' else (2 if kind=='write-second' else 0),kind=='read')
        assert lib.layout(1,old_hash)!=0
        damaged=read(0x80000,model.MIBIB_SIZE);assert model.select(damaged)[0] in (0,1)
    # Loss after writing the older copy: the untouched higher-age old copy remains valid.
    setup();lib.faults(0,0,0,1,0);assert lib.layout(1,old_hash)!=0
    remaining=read(0x80000,model.MIBIB_SIZE);assert model.select(remaining)[1]['single'] is False
    # Resume a conversion after a cut leaves only one valid new copy.
    setup();lib.faults(0,0,0,2,0);assert lib.layout(1,old_hash)!=0
    interrupted=read(0x80000,model.MIBIB_SIZE);assert model.select(interrupted)[1]['single']
    lib.reboot_state();assert lib.layout(1,hashlib.sha256(interrupted).hexdigest().encode())==0
    assert model.parse_copy(read(0x80000,model.BLOCK))['single'] and model.parse_copy(read(0xa0000,model.BLOCK))['single']
    # Never fall back to a stale known copy when another checked map is unsupported.
    unknown=bytearray(MIBIB);unknown[0x800+16+28+16]^=1;struct.pack_into('<I',unknown,6156,model.crc(unknown[:6144]))
    setup(bytes(unknown));assert lib.layout(1,hashlib.sha256(unknown).hexdigest().encode())!=0 and lib.writes()==0
    try:model.select(bytes(unknown));raise AssertionError('Unsupported checked copy accepted')
    except ValueError:pass
    # Synthetic 36 MiB restore proves bounded, multi-upload operation beyond 32 MiB.
    chunks=[bytes([i%251])*model.BLOCK for i in range(288)]
    full_hash=hashlib.sha256(b''.join(chunks)).digest();hashes=b''.join(hashlib.sha256(x).digest() for x in chunks)
    descriptor=model.DESCRIPTOR.pack(b'P5RST01\0',1,144+len(hashes),b'rootfs',0x7c0000,288*model.BLOCK,2048,model.BLOCK,0x8040002,bytes.fromhex('80afca26ac39'),hashlib.sha256(b'\xff'*(288*model.BLOCK)).digest(),full_hash,288)+hashes
    setup();upload(descriptor);assert lib.begin(len(descriptor),zlib.crc32(descriptor),b'RESTORE-P5-PARTITION')==0
    assert lib.finish()!=0
    upload(chunks[0]);assert lib.chunk(1,model.BLOCK,zlib.crc32(chunks[0]))!=0 and lib.writes()==0
    bad_chunk=bytes([255])*model.BLOCK;upload(bad_chunk);assert lib.chunk(0,model.BLOCK,zlib.crc32(bad_chunk))!=0 and lib.writes()==0
    for i,chunk in enumerate(chunks):upload(chunk);assert lib.chunk(i,len(chunk),zlib.crc32(chunk))==0
    assert lib.finish()==0 and lib.writes()==288
    assert hashlib.sha256(read(0x7c0000,288*model.BLOCK)).digest()==full_hash
    assert read(0x7a0000,model.BLOCK)==b'\xff'*model.BLOCK and read(0x2bc0000,model.BLOCK)==b'\xff'*model.BLOCK
    # Both U-Boot copies require the dedicated full-restoration confirmation.
    for name,start in [(b'0:APPSBL',0x380000),(b'0:APPSBL_1',0x4c0000)]:
        boot_hashes=b''.join(hashlib.sha256(chunks[0]).digest() for _ in range(10))
        boot=model.DESCRIPTOR.pack(b'P5RST01\0',1,144+len(boot_hashes),name,start,10*model.BLOCK,2048,model.BLOCK,0x8040002,bytes.fromhex('80afca26ac39'),hashlib.sha256(b'\xff'*(10*model.BLOCK)).digest(),hashlib.sha256(chunks[0]*10).digest(),10)+boot_hashes
        for token in (b'RESTORE-P5-PARTITION',b'RESTORE-P5-PROTECTED'):
            setup();upload(boot);assert lib.begin(len(boot),zlib.crc32(boot),token)!=0 and lib.erases()==0
        setup();upload(boot);assert lib.begin(len(boot),zlib.crc32(boot),b'RESTORE-P5-FULL')==0 and lib.erases()==0
    # SMEM can expose the two firmware names in opposite order; physical bounds remain authoritative.
    setup();alias=bytearray(descriptor);alias[16:48]=b'rootfs_1'+bytes(24)
    upload(bytes(alias));assert lib.begin(len(alias),zlib.crc32(alias),b'RESTORE-P5-PARTITION')==0 and lib.erases()==0
    # Text API rejects malformed/stale actions before any NAND change.
    for action in [b'',b'layout,no,0,CONVERT-P5-LAYOUT',b'install,12,00000000,0,BAD',b'restore-chunk,0,131072,00000000',b'abort,extra',b'restore-begin,-1,00000000,RESTORE-P5-PARTITION',b'restore-begin,4294967296,00000000,RESTORE-P5-PARTITION']:
        setup();assert lib.p5_storage_execute(action,0,0)!=0 and lib.erases()==0,action
    setup();upload(descriptor)
    action=f'restore-begin,{len(descriptor)},{zlib.crc32(descriptor):08x},RESTORE-P5-PARTITION'.encode()
    assert lib.p5_storage_execute(action,len(descriptor),zlib.crc32(descriptor))==0
    upload(chunks[0]);assert lib.chunk(0,model.BLOCK,zlib.crc32(chunks[0]))==0
    assert lib.p5_storage_execute(b'abort',0,0)==0 and lib.p5_storage_boot_blocked()
    assert lib.layout(0,hashlib.sha256(MIBIB).hexdigest().encode())!=0 and lib.p5_storage_boot_blocked()
    # Restart same region using a fresh current-destination digest, preserving the boot block until finish.
    resumed=bytearray(descriptor);resumed[76:108]=hashlib.sha256(read(0x7c0000,288*model.BLOCK)).digest()
    upload(bytes(resumed));assert lib.begin(len(resumed),zlib.crc32(resumed),b'RESTORE-P5-PARTITION')==0
    assert lib.p5_storage_execute(b'abort',0,0)==0 and lib.p5_storage_boot_blocked()
    upload(bytes(resumed));assert lib.begin(len(resumed),zlib.crc32(resumed),b'RESTORE-P5-PARTITION')==0
    for i,chunk in enumerate(chunks):upload(chunk);assert lib.chunk(i,len(chunk),zlib.crc32(chunk))==0
    assert lib.finish()==0 and not lib.p5_storage_boot_blocked()
    # Retry a failed conversion in the same RAM session with its fresh MIBIB identity.
    setup();lib.faults(0,0,2,0,0)
    assert lib.layout(1,hashlib.sha256(MIBIB).hexdigest().encode())!=0
    current=read(0x80000,model.MIBIB_SIZE);lib.faults(0,0,0,0,0)
    assert lib.layout(1,hashlib.sha256(current).hexdigest().encode())==0
    assert model.select(read(0x80000,model.MIBIB_SIZE))[1]['single']
    lib.uart.argtypes=[C.c_int,C.POINTER(C.c_char_p)]
    def uart(*args):
        values=(C.c_char_p*(len(args)+1))(b'p5storage',*(x.encode() for x in args))
        return lib.uart(len(args)+1,values)
    setup(single_map)
    assert uart('layout','single',hashlib.sha256(single_map).hexdigest(),'CONVERT-P5-LAYOUT')==0 and lib.writes()==0
    for size in ('-1','+1','100000000','0x',' 1'):
        setup();assert uart('restore-begin',size,'0','RESTORE-P5-PARTITION')!=0 and lib.erases()==0
    setup();upload(descriptor)
    assert uart('restore-begin',f'{len(descriptor):x}',f'{zlib.crc32(descriptor):08x}','RESTORE-P5-PARTITION')==0
    assert lib.p5_storage_boot_blocked() and uart('abort')==0 and not lib.p5_storage_boot_blocked()
    # Full small restore and layout operation parity through each public adapter.
    small=[bytes([0x5a])*model.BLOCK,bytes([0xa5])*model.BLOCK]
    small_hashes=b''.join(hashlib.sha256(x).digest() for x in small)
    small_descriptor=model.DESCRIPTOR.pack(b'P5RST01\0',1,144+len(small_hashes),b'bdinfo',0x780000,2*model.BLOCK,2048,model.BLOCK,0x8040002,bytes.fromhex('80afca26ac39'),hashlib.sha256(b'\xff'*(2*model.BLOCK)).digest(),hashlib.sha256(b''.join(small)).digest(),2)+small_hashes
    for transport in ('uart','web'):
        setup();upload(small_descriptor);crc=zlib.crc32(small_descriptor)
        if transport=='uart':assert uart('restore-begin',f'{len(small_descriptor):x}',f'{crc:08x}','RESTORE-P5-PROTECTED')==0
        else:assert lib.p5_storage_execute(f'restore-begin,{len(small_descriptor)},{crc:08x},RESTORE-P5-PROTECTED'.encode(),len(small_descriptor),crc)==0
        for index,chunk in enumerate(small):
            upload(chunk);crc=zlib.crc32(chunk)
            if transport=='uart':assert uart('restore-chunk',f'{index:x}',f'{len(chunk):x}',f'{crc:08x}')==0
            else:assert lib.p5_storage_execute(f'restore-chunk,{index},{len(chunk)},{crc:08x}'.encode(),len(chunk),crc)==0
        assert (uart('restore-finish') if transport=='uart' else lib.p5_storage_execute(b'restore-finish',0,0))==0
        assert read(0x780000,2*model.BLOCK)==b''.join(small) and not lib.p5_storage_boot_blocked()
        setup(boot0=bootconfig(1),boot1=bootconfig(1));old=hashlib.sha256(MIBIB).hexdigest()
        result=uart('layout','single',old,'CONVERT-P5-LAYOUT') if transport=='uart' else lib.p5_storage_execute(f'layout,single,{old},CONVERT-P5-LAYOUT'.encode(),0,0)
        assert result==0 and model.select(read(0x80000,model.MIBIB_SIZE))[1]['single']
        for offset in (0x100000,0x140000):assert read(offset+128,4)==bytes(4)
    # Descriptor mutations must refuse before erase (CRC is recomputed intentionally).
    for offset in (0,8,12,48,52,56,60,64,68,74,76,140):
        setup();bad=bytearray(descriptor);bad[offset]^=1;upload(bytes(bad));assert lib.begin(len(bad),zlib.crc32(bad),b'RESTORE-P5-PARTITION')!=0 and lib.erases()==0,offset
    # A failed chunk can be retried; finishing still checks the complete NAND image.
    setup();upload(descriptor);assert lib.begin(len(descriptor),zlib.crc32(descriptor),b'RESTORE-P5-PARTITION')==0
    lib.faults(0,0,0,1,0);upload(chunks[0]);assert lib.chunk(0,model.BLOCK,zlib.crc32(chunks[0]))!=0 and lib.next()==0
    lib.faults(0,0,0,0,0);assert lib.chunk(0,model.BLOCK,zlib.crc32(chunks[0]))==0
    for i,chunk in enumerate(chunks[1:],1):upload(chunk);assert lib.chunk(i,len(chunk),zlib.crc32(chunk))==0
    lib.flip(0x7c0000);assert lib.finish()!=0 and lib.p5_storage_boot_blocked()
    C.CDLL(None).fflush(None)
    print('PASS: actual C checksum/conversion round trip; protected bytes unchanged; copy-at-a-time failures retain a valid copy; 36 MiB chunk restore; identity/hash/geometry refusal; chunk retry; whole-NAND readback catches corruption; failed operations keep installed boot blocked')

with tempfile.TemporaryDirectory(prefix='p5-storage-disabled-') as directory:
    d=Path(directory)
    disabled_prelude=common.replace('#define CONFIG_P5_EXPERIMENTAL_STORAGE_WRITES 1','')+'\ntypedef int cmd_tbl_t;\n#define CMD_RET_FAILURE 1\n#define CMD_RET_USAGE 2\n'
    disabled_source=s[s.index('DECLARE_GLOBAL_DATA_PTR;'):s.index('\nU_BOOT_CMD')]
    (d/'disabled.c').write_text(disabled_prelude+mi+disabled_source+'''\nint test_gate(void){char *a[]={"p5storage","layout","single","0","CONVERT-P5-LAYOUT"};return do_p5storage(0,0,5,a);}\n''')
    sp.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-Wno-deprecated-declarations','-Wno-unused-parameter','-Wno-unused-function','-Wno-unused-variable','-iquote',str(root/'include'),str(d/'disabled.c'),'-lcrypto','-lz','-o',str(d/'disabled.so')],check=True)
    lib=C.CDLL(str(d/'disabled.so'))
    assert lib.test_gate()==1 and lib.p5_storage_busy()==0 and lib.p5_storage_boot_blocked()==0
    print('PASS: default-off production implementation refuses layout/restore actions without invoking NAND')

# Emulate the vendor's pure CRC routine, including its shipped table.
if not VENDOR.is_file():
    print('SKIP: optional vendor machine-code comparison; set P5_VENDOR_PARTITION_TOOL')
    sys.exit(0)
try:
    from unicorn import Uc,UC_ARCH_X86,UC_MODE_32,UC_HOOK_CODE
except ImportError:
    print('SKIP: optional vendor machine-code comparison; install Unicorn or set PYTHONPATH')
    sys.exit(0)
from unicorn.x86_const import UC_X86_REG_ESP,UC_X86_REG_EAX
binary=VENDOR.read_bytes();h=struct.unpack_from('<16sHHIIIIIHHHHHH',binary)
u=Uc(UC_ARCH_X86,UC_MODE_32);u.mem_map(0x8048000,0x10000)
for i in range(h[10]):
    p=struct.unpack_from('<8I',binary,h[5]+i*h[9])
    if p[0]==1:u.mem_write(p[2],binary[p[1]:p[1]+p[4]])
u.mem_map(0x100000,0x10000);u.mem_map(0x200000,0x10000);u.mem_map(0x300000,0x1000)
for i in range(2):
    data=MIBIB[i*model.BLOCK:i*model.BLOCK+6144];u.mem_write(0x100000,data);u.mem_write(0x20f000,struct.pack('<4I',0x300000,0x100000,len(data)*8,0));u.reg_write(UC_X86_REG_ESP,0x20f000)
    u.emu_start(0x8049b32,0x300000,count=2000000)
    want=struct.unpack_from('<I',MIBIB,i*model.BLOCK+6156)[0]
    assert u.reg_read(UC_X86_REG_EAX)==model.crc(data)==want
print('PASS: vendor crc_32_calc machine code reproduces both saved MIBIB checksums')

# Independently check both directions against the shipped user->system converter.
from unicorn.x86_const import UC_X86_REG_EIP

def libc_hook(uc,address,size,_):
    if address==0x8048861:raise AssertionError('Vendor converter rejected the test geometry')
    if address not in (0x80486e0,0x8048600,0x8048730):return
    stack=uc.reg_read(UC_X86_REG_ESP);ret,a,b,n=struct.unpack('<4I',bytes(uc.mem_read(stack,16)))
    if address==0x80486e0:uc.mem_write(a,bytes([b&255])*n);value=a
    elif address==0x8048600:uc.mem_write(a,bytes(uc.mem_read(b,n)));value=a
    else:
        left=bytes(uc.mem_read(a,n)).split(b'\0',1)[0];right=bytes(uc.mem_read(b,n)).split(b'\0',1)[0];value=0 if left==right else 1
    uc.reg_write(UC_X86_REG_EAX,value);uc.reg_write(UC_X86_REG_ESP,stack+4);uc.reg_write(UC_X86_REG_EIP,ret)
u.hook_add(UC_HOOK_CODE,libc_hook)
for address,value in [(0x804c0d8,1024),(0x804c0dc,64),(0x804c0e0,2048),(0x804c158,1024),(0x804c15c,64),(0x804c160,2048)]:u.mem_write(address,struct.pack('<I',value))
for single in (False,True):
    block=model.convert_copy(MIBIB[:model.BLOCK],single,2)
    u.mem_write(0x101000,block[4096:4096+0x390]);u.mem_write(0x102000,b'\xa5'*0x390)
    u.mem_write(0x20f000,struct.pack('<3I',0x300000,0x101000,0x102000));u.reg_write(UC_X86_REG_ESP,0x20f000)
    u.emu_start(0x80489d1,0x300000,count=2000000)
    assert u.reg_read(UC_X86_REG_EAX)==0
    got=bytes(u.mem_read(0x102000,0x390));want=block[2048:2048+0x390]
    assert got==want,('vendor table mismatch',single,[(i,got[i],want[i]) for i in range(len(got)) if got[i]!=want[i]][:10])
print('PASS: vendor user-to-system converter exactly reproduces both dual and single-rootfs partition tables')
