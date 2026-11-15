#!/usr/bin/env python3
"""Compile the real C ELF planner on host and emulate its ARM scratch loader.
Needs a host C compiler and Unicorn. No router access or hardware writes.
"""
from pathlib import Path
import ctypes as C
import os
import struct
import subprocess as sp
import tempfile
import sys

root=Path(__file__).resolve().parents[2]
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_CPSR,UC_ARM_REG_SP
source=(root/'board/qca/arm/common/p5_recovery.c').read_text()
start=source.index('static int bounded('); end=source.index('int p5_boot_check(')
prelude='''#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <errno.h>
#include <elf.h>
#include <sys/mman.h>
typedef unsigned long ulong; typedef uint32_t u32;
#include "p5_board.h"
#define IMAGE_BASE P5_RAM_BASE
#define IMAGE_END P5_RAM_END
#define MAX_MOVES 32
struct p5_move {u32 destination,source,length;};
'''
wrapper='''int test_plan(const void *data, unsigned size, struct p5_move *moves, unsigned *count) {
 static void *region;
 if(size > (32u<<20)) return -1;
 if(!region) {region=mmap((void*)0x4b000000,32u<<20,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0); if(region==MAP_FAILED)return -2;}
 memcpy(region,data,size);return p5_elf_plan(0x4b000000,size,moves,count);
}
'''
class Move(C.Structure):_fields_=[('destination',C.c_uint32),('source',C.c_uint32),('length',C.c_uint32)]

def section(data,target):
 h=struct.unpack_from('<16sHHIIIIIHHHHHH',data)
 entries=[struct.unpack_from('<10I',data,h[6]+i*h[11]) for i in range(h[12])]
 names=entries[h[13]];strings=data[names[4]:names[4]+names[5]]
 for s in entries:
  name=strings[s[0]:strings.index(0,s[0])].decode()
  if name==target:return data[s[4]:s[4]+s[5]]
 raise AssertionError(target)

with tempfile.TemporaryDirectory(prefix='p5-chain-test-') as tmp:
 p=Path(tmp);(p/'planner.c').write_text(prelude+source[start:end]+wrapper)
 sp.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-iquote',str(root/'include'),'-o',str(p/'planner.so'),str(p/'planner.c')],check=True)
 lib=C.CDLL(str(p/'planner.so'));lib.test_plan.argtypes=[C.c_void_p,C.c_uint,C.POINTER(Move),C.POINTER(C.c_uint)]
 image=(root/'out-p5-ramtest/u-boot').read_bytes()
 def plan(data):
  moves=(Move*32)();count=C.c_uint();buf=C.create_string_buffer(data)
  result=lib.test_plan(buf,len(data),moves,C.byref(count))
  return result,list(moves[:count.value])
 result,moves=plan(image);assert result==0 and moves
 for name,offset,fmt,value in [('entry',24,'I',0x48000001),('section alignment',32,'I',1),('type',16,'H',3),('machine',18,'H',183),('table count',48,'H',65535)]:
  bad=bytearray(image);struct.pack_into('<'+fmt,bad,offset,value);assert plan(bytes(bad))[0]!=0,name
 assert plan(image[:64])[0]!=0
 h=struct.unpack_from('<16sHHIIIIIHHHHHH',image)
 for i in range(h[12]):
  s=struct.unpack_from('<10I',image,h[6]+i*h[11])
  if s[1]==8 and s[5]:
   bad=bytearray(image);struct.pack_into('<I',bad,h[6]+i*h[11]+12,0x48000000);assert plan(bytes(bad))[0]!=0
   break
 code=section((root/'out-p5-ramtest/board/qca/arm/common/p5_chain.o').read_bytes(),'.text.p5_chain')
 # The copied loader must contain no relocations or external references.
 rel=sp.run(['readelf','-rW',str(root/'out-p5-ramtest/board/qca/arm/common/p5_chain.o')],capture_output=True,text=True,check=True).stdout
 assert "Relocation section '.rel.text.p5_chain'" not in rel,rel
 emu=Uc(UC_ARCH_ARM,UC_MODE_ARM)
 emu.mem_map(0x46000000,0x800000);emu.mem_map(0x48000000,0x10000);emu.mem_map(0x4b000000,0x2000000)
 emu.mem_write(0x46000000,b'\xa5'*0x800000);emu.mem_write(0x4b000000,image)
 emu.mem_write(0x48000000,code)
 descriptors=b''.join(struct.pack('<III',m.destination,m.source,m.length) for m in moves)
 emu.mem_write(0x48001000,descriptors)
 for reg,value in [(UC_ARM_REG_R0,0x48001000),(UC_ARM_REG_R1,len(moves)),(UC_ARM_REG_R2,0x46000001),(UC_ARM_REG_R3,0x4800f000),(UC_ARM_REG_CPSR,0x13)]:emu.reg_write(reg,value)
 reached=[]
 def hook(uc,address,size,_):
  if address==0x46000000:reached.append(address);uc.emu_stop()
 emu.hook_add(UC_HOOK_CODE,hook)
 emu.emu_start(0x48000000,0,count=16000000)
 assert reached,'Loader did not reach the new Thumb entry'
 assert emu.reg_read(UC_ARM_REG_SP)==0x4800f000
 assert emu.reg_read(UC_ARM_REG_CPSR)&0x20
 for m in moves:
  expected=image[m.source-0x4b000000:m.source-0x4b000000+m.length] if m.source else b'\0'*m.length
  assert bytes(emu.mem_read(m.destination,m.length))==expected,hex(m.destination)
 assert bytes(emu.mem_read(0x48000000,len(code)))==code
 print('PASS: actual C ELF planner rejects unsafe headers/BSS; ARM loader copies all real ELF sections, zeros BSS and reaches Thumb entry from independent scratch RAM')

# Copy the exact flash-linked package PT_LOAD and stop at its ARM entry.
package=(root/'out-p5-flash/candidate/p5-uboot.mbn').read_bytes()
h=struct.unpack_from('<16sHHIIIIIHHHHHH',package)
p=struct.unpack_from('<8I',package,h[5]+2*h[9])
assert p[0]==1 and p[2]==p[3]==h[4]==0x4a920000
assert struct.unpack_from('<I',package,p[1])[0]&0xff000000==0xea000000,'Expected standard ARM reset-vector branch'
emu=Uc(UC_ARCH_ARM,UC_MODE_ARM)
emu.mem_map(0x4a800000,0x200000);emu.mem_map(0x48000000,0x10000);emu.mem_map(0x4b000000,0x2000000)
emu.mem_write(0x4b000000,package);emu.mem_write(0x48000000,code)
emu.mem_write(0x48001000,struct.pack('<III',p[3],0x4b000000+p[1],p[4]))
for reg,value in [(UC_ARM_REG_R0,0x48001000),(UC_ARM_REG_R1,1),(UC_ARM_REG_R2,h[4]),(UC_ARM_REG_R3,0x4800f000),(UC_ARM_REG_CPSR,0x13)]:emu.reg_write(reg,value)
reached=[]
def stop_flash(uc,address,size,_):
 if address==h[4]:reached.append(address);uc.emu_stop()
emu.hook_add(UC_HOOK_CODE,stop_flash)
emu.emu_start(0x48000000,0,count=16000000)
assert reached and not(emu.reg_read(UC_ARM_REG_CPSR)&32)
assert bytes(emu.mem_read(p[3],p[4]))==package[p[1]:p[1]+p[4]]
assert bytes(emu.mem_read(0x48000000,len(code)))==code
print('PASS: real Qualcomm package PT_LOAD copies to 0x4a920000; scratch survives; branch reaches ARM entry')
