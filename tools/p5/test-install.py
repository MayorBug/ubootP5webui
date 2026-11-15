#!/usr/bin/env python3
"""Exercise actual installer C against an in-memory NAND, never hardware."""
from pathlib import Path
import ctypes as C
import hashlib
import struct
import subprocess as sp
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root/'board/qca/arm/common/p5_install.c').read_text()
body = source[source.index('DECLARE_GLOBAL_DATA_PTR;'):source.index('/* An explicit UART')]
prelude = r'''
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <elf.h>
#include <sys/mman.h>
#include <openssl/sha.h>
typedef unsigned long ulong; typedef uint32_t u32; typedef uint64_t u64; typedef uint8_t u8;
#include "p5_board.h"
#define ALIGN(x,a) (((x)+(a)-1)&~((a)-1))
#define DECLARE_GLOBAL_DATA_PTR
struct board { ulong bi_arch_number; }; struct global { struct board *bd; };
static struct board bd; static struct global global={&bd}; static struct global *gd=&global;
typedef struct {u64 size; u32 writesize,erasesize;} nand_info_t;
static nand_info_t nand_info[1]; static struct {u32 flash_block_size;} qca_smem_flash_info;
static unsigned char storage[0x140000];
static int secure,bad,fail,erases,writes,reads,prewrite_reads;
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define _SMEM_DUAL_BOOTINFO_MAGIC_END 0xb3b2b1b0
struct per_part_info {char name[16];u32 primaryboot;};
static struct {u32 numaltpart;struct per_part_info per_part_entry[16];u32 magic_end;} qca_smem_bootconfig_info;
static int smem_bootconfig_info(void){return 0;}
static void sha256_csum_wd(const void *p,unsigned n,unsigned char *out,unsigned chunk){(void)chunk;SHA256(p,n,out);}
int p5_storage_busy(void){return 0;}
int is_sec_boot_enabled(void){return secure;}
static int smem_getpart(char *name,u32 *start,u32 *count){if(strcmp(name,"0:APPSBL"))return -1;*start=0x1c;*count=10;return 0;}
static int nand_block_isbad(nand_info_t *m,ulong off){(void)m;(void)off;return bad;}
static int nand_read(nand_info_t *m,ulong off,size_t *n,void *p){(void)m;reads++;if(!writes)prewrite_reads++;if(off!=0x380000||*n!=sizeof(storage))return -1;memcpy(p,storage,*n);if(fail==2&&writes)((unsigned char*)p)[0]^=1;return 0;}
static int nand_erase(nand_info_t *m,ulong off,size_t n){
 if(m!=nand_info||off!=P5_APPSBL_OFFSET||n!=P5_APPSBL_SIZE)return -1;
 erases++;memset(storage,255,n);return 0;}
static int nand_write(nand_info_t *m,ulong off,size_t *n,void *p){
 if(m!=nand_info||off!=P5_APPSBL_OFFSET||!*n||*n>P5_APPSBL_SIZE||*n%m->writesize)return -1;
 writes++;if(fail==1)return -1;memcpy(storage,p,*n);return 0;}
'''+r'''
struct erase_info {nand_info_t *mtd;ulong addr,len;int scrub;void *callback;};
static int mtd_erase(nand_info_t *m,struct erase_info *e){if(e->mtd!=m||e->scrub||e->callback)return -1;return nand_erase(m,e->addr,e->len);}
'''
wrapper=r'''
void setup(int secure_arg,int bad_arg,int fail_arg){
 static int mapped;
 if(!mapped){if(mmap((void*)0x4b000000,0x2000000,3,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0)==MAP_FAILED)abort();if(mmap((void*)0x4d000000,0x140000,3,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0)==MAP_FAILED)abort();mapped=1;}
 bd.bi_arch_number=0x8040002;nand_info[0]=(nand_info_t){0x8000000,2048,0x20000};qca_smem_flash_info.flash_block_size=0x20000;
 memset(&qca_smem_bootconfig_info,0,sizeof(qca_smem_bootconfig_info));qca_smem_bootconfig_info.numaltpart=1;qca_smem_bootconfig_info.magic_end=_SMEM_DUAL_BOOTINFO_MAGIC_END;strcpy(qca_smem_bootconfig_info.per_part_entry[0].name,"0:APPSBL");
 secure=secure_arg;bad=bad_arg;fail=fail_arg;erases=writes=reads=prewrite_reads=writing=install_dirty=0;memset(storage,0x5a,sizeof(storage));}
void upload(const void *p,unsigned n){memcpy((void*)0x4b000000,p,n);}
int prewrite(void){return prewrite_reads;}
int counts(void){return erases*100+writes;}
void boot_select(unsigned selected,unsigned count){qca_smem_bootconfig_info.per_part_entry[0].primaryboot=selected;qca_smem_bootconfig_info.numaltpart=count;}
void clear_fault(void){fail=0;}
int idle(void){return !writing;}
int matches(const void *p,unsigned n){return !memcmp(storage,p,n);}
int boundary_test(void){size_t n=2048;return nand_erase(nand_info,P5_APPSBL_OFFSET-1,P5_APPSBL_SIZE)!=0&&nand_write(nand_info,P5_APPSBL_OFFSET-1,&n,storage)!=0;}

'''
with tempfile.TemporaryDirectory(prefix='p5-install-test-') as directory:
    d=Path(directory);(d/'test.c').write_text(prelude+body+wrapper)
    sp.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-Wno-deprecated-declarations','-iquote',str(root/'include'),str(d/'test.c'),'-lcrypto','-o',str(d/'test.so')],check=True)
    lib=C.CDLL(str(d/'test.so'))
    lib.p5_install_check.argtypes=[C.c_ulong,C.c_ulong]
    lib.p5_install_run.argtypes=[C.c_ulong,C.c_ulong]
    lib.upload.argtypes=[C.c_void_p,C.c_uint];lib.matches.argtypes=[C.c_void_p,C.c_uint]
    package=(root/'out-p5-flash/candidate/p5-uboot.mbn').read_bytes()
    image=C.create_string_buffer(package)
    lib.setup(0,0,0);lib.upload(image,len(package))
    assert lib.p5_install_check(0x4b000000,len(package))==0,'Real packaged candidate must pass the production C parser'
    for offset,value in [(16,3),(24,0x46000001)]:
        broken=bytearray(package);struct.pack_into('<I' if offset==24 else '<H',broken,offset,value)
        lib.upload(C.create_string_buffer(bytes(broken)),len(broken));assert lib.p5_install_check(0x4b000000,len(broken))!=0
    elf=struct.unpack_from('<16sHHIIIIIHHHHHH',package)
    hash_ph=struct.unpack_from('<8I',package,elf[5]+32)
    for field in [0,1,2,3,4,5,6,7,8,9]:
        broken=bytearray(package);at=hash_ph[1]+field*4;struct.pack_into('<I',broken,at,struct.unpack_from('<I',broken,at)[0]^1)
        lib.upload(C.create_string_buffer(bytes(broken)),len(broken));assert lib.p5_install_check(0x4b000000,len(broken))!=0,('hash header',field)
    broken=bytearray(package);broken[-1]^=1
    lib.upload(C.create_string_buffer(bytes(broken)),len(broken));assert lib.p5_install_check(0x4b000000,len(broken))!=0
    for secure,bad in [(1,0),(-1,0),(0,1)]:
        lib.setup(secure,bad,0);lib.upload(image,len(package))
        assert lib.p5_install_run(0x4b000000,len(package))!=0 and lib.counts()==0 and lib.idle()
    for selected,count in [(1,1),(0,0),(0,17)]:
        lib.setup(0,0,0);lib.upload(image,len(package));lib.boot_select(selected,count)
        assert lib.p5_install_run(0x4b000000,len(package))!=0 and lib.counts()==0
    for fault in [1,2]:
        lib.setup(0,0,fault);lib.upload(image,len(package))
        assert lib.p5_install_run(0x4b000000,len(package))!=0 and lib.idle() and lib.p5_install_boot_blocked()
    # Retry the identical validated candidate without reboot after destructive failure.
    lib.setup(0,0,1);lib.upload(image,len(package))
    assert lib.p5_install_run(0x4b000000,len(package))!=0 and lib.p5_install_boot_blocked()
    lib.clear_fault()
    # A different, correctly hashed package cannot replace an unfinished validated candidate.
    changed=bytearray(package);changed[-1]^=1
    load_ph=struct.unpack_from('<8I',package,elf[5]+2*32)
    changed[hash_ph[1]+40+2*32:hash_ph[1]+40+3*32]=hashlib.sha256(changed[load_ph[1]:load_ph[1]+load_ph[4]]).digest()
    lib.upload(C.create_string_buffer(bytes(changed)),len(changed))
    assert lib.p5_install_check(0x4b000000,len(changed))==0
    assert lib.p5_install_run(0x4b000000,len(changed))!=0 and lib.p5_install_boot_blocked()
    lib.upload(image,len(package))
    assert lib.p5_install_run(0x4b000000,len(package))==0 and not lib.p5_install_boot_blocked()
    lib.setup(0,0,0);lib.upload(image,len(package))
    assert lib.p5_install_run(0x4b000000,len(package))==0 and lib.counts()==101 and lib.matches(image,len(package)) and lib.idle()
    assert lib.boundary_test() and lib.prewrite()==0
    print('PASS: production C parses real package, rejects malformed/hash failures, refuses secure/unknown fuse, bad blocks and unexpected boot selection; write/readback errors retain failed-install state; success matches bytes; write scope excludes neighboring partitions')
