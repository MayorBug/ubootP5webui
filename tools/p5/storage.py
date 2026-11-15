#!/usr/bin/env python3
"""Offline P5 MIBIB plans and device-bound ECC restore packages. No device access."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import zlib
import tempfile

from board import CONSTANTS

BLOCK=CONSTANTS['P5_NAND_BLOCK']
MIBIB_SIZE=CONSTANTS['P5_MIBIB_SIZE']
PROTECTED=[('0:SBL1',0,4),('0:MIBIB',4,4),('0:BOOTCONFIG',8,2),('0:BOOTCONFIG1',10,2),('0:QSEE',12,8),('0:DEVCFG',20,2),('0:CDT',22,2),('0:APPSBLENV',24,4),('0:APPSBL',28,10),('0:APPSBL_1',38,10),('0:ART',48,8),('0:TRAINING',56,4),('bdinfo',60,2)]
USER_SIZES=[(0x100,0x100),(0x100,0x100),(0x80,0x80),(0x80,0x80),(0x200,0x200),(0x80,0x80),(0x80,0x80),(0x100,0x100),(0x400,0x100),(0x400,0x100),(0x200,0x200),(0x100,0x100),(0x80,0x80)]
DESCRIPTOR=struct.Struct('<8sII32sIIIII6s2x32s32sI')
assert DESCRIPTOR.size==144

def crc(bytes_):
    value=0
    for byte in bytes_:
        value^=byte<<24
        for _ in range(8):
            value=((value<<1)^(0x04c11db7 if value&0x80000000 else 0))&0xffffffff
    return value

def entry(name,a,b,attr):
    return struct.pack('<16sIII',name.encode(),a,b,attr)

def parse_copy(block):
    if len(block)!=BLOCK:raise ValueError('Expected one 128 KiB MIBIB copy')
    head=struct.unpack_from('<4I',block)
    system=struct.unpack_from('<4I',block,2048);user=struct.unpack_from('<4I',block,4096)
    check=struct.unpack_from('<4I',block,6144)
    if head[:3]!=(0xfe569fac,0xcd7f127a,4) or system[:3]!=(0x55ee73aa,0xe35ebddb,4) or user[:3]!=(0xaa7d1b9a,0x1f7d48bc,4) or check[:3]!=(0x9d41bea1,0xf1ded2ea,1) or check[3]!=crc(block[:6144]):raise ValueError('Invalid MIBIB header/table/checksum')
    count=system[3]
    if count not in (14,15) or user[3]!=count:raise ValueError('Unsupported partition count')
    expected=[entry(n,a,b,0xffff) for n,a,b in PROTECTED]
    expected_user=[entry(n,*USER_SIZES[i],0xff00ffff) for i,(n,_,_) in enumerate(PROTECTED)]
    single=count==14
    expected.append(entry('rootfs',62,576 if single else 288,0xffff))
    expected_user.append(entry('rootfs',0x11f00 if single else 0x8f00,0x100,0xff00ffff))
    if not single:
        expected.append(entry('rootfs_1',350,288,0xffff));expected_user.append(entry('rootfs_1',0x8f00,0x100,0xff00ffff))
    for off,parts in [(2048,expected),(4096,expected_user)]:
        if block[off+16:off+16+count*28]!=b''.join(parts) or block[off+16+count*28:off+0x390]!=bytes(0x390-16-count*28) or block[off+0x390:off+2048]!=b'\xff'*(2048-0x390):raise ValueError('Unsupported partition map or padding')
    if block[16:2048]!=b'\xff'*(2048-16) or block[6160:]!=b'\xff'*(BLOCK-6160):raise ValueError('Unknown MIBIB trailing data')
    return {'age':head[3],'single':single,'parts':[(n,a,b) for n,a,b in PROTECTED]+[('rootfs',62,576 if single else 288)]+([] if single else [('rootfs_1',350,288)])}

def select(data):
    if len(data)!=MIBIB_SIZE or any(data[i*BLOCK:(i+1)*BLOCK] not in (b'\xff'*BLOCK,bytes(BLOCK)) for i in (2,3)):raise ValueError('Expected original 512 KiB MIBIB with two known copies and untouched blank slots')
    valid=[]
    for index in range(2):
        try:valid.append((index,parse_copy(data[index*BLOCK:(index+1)*BLOCK])))
        except ValueError:
            block=data[index*BLOCK:(index+1)*BLOCK]
            h=struct.unpack_from('<4I',block);c=struct.unpack_from('<4I',block,6144)
            if (h[:2]==(0xfe569fac,0xcd7f127a) and h[2]!=4) or (c[:2]==(0x9d41bea1,0xf1ded2ea) and c[3]==crc(block[:6144])):
                raise ValueError('Checked or versioned MIBIB copy has unsupported metadata; refusing stale fallback')
    if not valid:raise ValueError('No valid MIBIB copy')
    if len(valid)==2 and valid[0][1]['age']==valid[1][1]['age'] and data[:BLOCK]!=data[BLOCK:2*BLOCK]:raise ValueError('Conflicting copies have equal ages')
    return max(valid,key=lambda pair:pair[1]['age'])

def convert_copy(block,single,age):
    parse_copy(block)
    out=bytearray(block);struct.pack_into('<I',out,12,age)
    for off,magic in [(2048,(0x55ee73aa,0xe35ebddb)),(4096,(0xaa7d1b9a,0x1f7d48bc))]:
        struct.pack_into('<4I',out,off,*magic,4,14 if single else 15)
        out[off+16+13*28:off+0x390]=bytes(19*28)
        p=off+16+13*28
        out[p:p+28]=entry('rootfs',62 if off==2048 else (0x11f00 if single else 0x8f00),(576 if single else 288) if off==2048 else 0x100,0xffff if off==2048 else 0xff00ffff)
        if not single:out[p+28:p+56]=entry('rootfs_1',350 if off==2048 else 0x8f00,288 if off==2048 else 0x100,0xffff if off==2048 else 0xff00ffff)
    struct.pack_into('<I',out,6156,crc(out[:6144]));parse_copy(out);return bytes(out)

def conversion(data,single):
    index,active=select(data);out=bytearray(data)
    try:
        if active['single']==single and parse_copy(data[(1-index)*BLOCK:(2-index)*BLOCK])['single']==single:return data
    except ValueError:pass
    if active['age']>0xfffffffc:raise ValueError('MIBIB age wrap is not supported')
    source=data[index*BLOCK:(index+1)*BLOCK]
    for step,target in enumerate([1-index,index],1):
        source=convert_copy(source,single,active['age']+step);out[target*BLOCK:(target+1)*BLOCK]=source
    return bytes(out)

def read_manifest(path):
    m=json.loads(path.read_text())
    if m.get('schema')!='p5-partition-backup-v1' or m.get('board')!='cudy,p5' or m.get('machid')!='0x8040002' or m.get('format')!='nand-ecc-data' or m.get('bytes')!=m.get('partition_size') or not re.fullmatch('[a-f0-9]{64}',m.get('sha256','')):raise ValueError('Expected complete P5 ECC partition backup manifest without OOB')
    return m

def prepare_restore(backup,manifest,current,mibib,output,allow_full=False):
    source=read_manifest(manifest);now=read_manifest(current)
    if source['device_mac']!=now['device_mac'] or source['start']!=now['start'] or source['partition_size']!=now['partition_size']:raise ValueError('Backup/current destination identity or geometry differs')
    _,active=select(mibib.read_bytes())
    parts=[p for p in active['parts'] if p[1]*BLOCK==source['start'] and p[2]*BLOCK==source['partition_size']]
    if len(parts)!=1 or parts[0][0]=='0:MIBIB':raise ValueError('Destination does not match the current MIBIB map; MIBIB needs the conversion backend')
    name,start,blocks=parts[0]
    bootloader=name in ('0:APPSBL','0:APPSBL_1')
    if bootloader and not allow_full:raise ValueError('U-Boot is excluded; use --allow-full-restoration explicitly to include it')
    if not 1<=blocks<=1024 or backup.stat().st_size!=blocks*BLOCK:raise ValueError('Backup length is not a complete supported partition')
    hashes=[];whole=hashlib.sha256()
    with backup.open('rb') as f:
        for _ in range(blocks):
            chunk=f.read(BLOCK);whole.update(chunk);hashes.append(hashlib.sha256(chunk).digest())
        if f.read(1):raise ValueError('Backup grew during read')
    if whole.hexdigest()!=source['sha256']:raise ValueError('Backup SHA256 differs from manifest')
    mac=bytes.fromhex(source['device_mac'].split(',')[0].strip().replace(':',''))
    if len(mac)!=6:raise ValueError('Invalid device MAC')
    descriptor=DESCRIPTOR.pack(b'P5RST01\0',1,144+blocks*32,name.encode(),start*BLOCK,blocks*BLOCK,2048,BLOCK,0x8040002,mac,bytes.fromhex(now['sha256']),whole.digest(),blocks)+b''.join(hashes)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',output.name):raise ValueError('Use a simple package directory name for UART/TFTP commands')
    if output.exists():raise FileExistsError(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    final_output=output
    with tempfile.TemporaryDirectory(prefix='.'+output.name+'-',dir=output.parent) as staging:
        output=Path(staging)/'package'
        output.mkdir()
        (output/'descriptor.bin').write_bytes(descriptor)
        # Recheck each chunk before exporting; changing input cannot make a valid package.
        with backup.open('rb') as f:
            for index,want in enumerate(hashes):
                chunk=f.read(BLOCK)
                if hashlib.sha256(chunk).digest()!=want:raise ValueError('Backup changed during package preparation')
                (output/f'chunk-{index:04d}.bin').write_bytes(chunk)
        meta={'partition':name,'start':hex(start*BLOCK),'bytes':blocks*BLOCK,'chunks':blocks,'descriptor_bytes':len(descriptor),'descriptor_crc32':f'{zlib.crc32(descriptor):08x}','whole_sha256':whole.hexdigest(),'token':'RESTORE-P5-FULL' if bootloader else 'RESTORE-P5-PARTITION' if name in ('rootfs','rootfs_1') else 'RESTORE-P5-PROTECTED','writes_enabled':False}
        commands=['httpd s',f'tftpboot 0x4b000000 {final_output.name}/descriptor.bin',f'p5storage restore-begin {len(descriptor):x} {meta["descriptor_crc32"]} {meta["token"]}']
        for index in range(blocks):
            chunk=(output/f'chunk-{index:04d}.bin').read_bytes()
            commands.extend([f'tftpboot 0x4b000000 {final_output.name}/chunk-{index:04d}.bin',f'p5storage restore-chunk {index:x} {BLOCK:x} {zlib.crc32(chunk):08x}'])
        commands.append('p5storage restore-finish')
        (output/'commands.txt').write_text('# EXPERIMENTAL WRITES: disabled in normal builds. Stop on any error. Do not source this file.\n'+'\n'.join(commands)+'\n')
        (output/'package.json').write_text(json.dumps(meta,indent=2)+'\n')
        if final_output.exists():raise FileExistsError(final_output)
        output.rename(final_output)
        return meta

def split_full(backup,manifest,output):
    m=json.loads(manifest.read_text())
    if m.get('schema')!='p5-partition-backup-v1' or m.get('board')!='cudy,p5' or m.get('machid')!='0x8040002' or m.get('format')!='nand-full-data' or m.get('start')!=0 or m.get('bytes')!=0x8000000:raise ValueError('Expected a full ECC data backup, not raw OOB')
    data=backup.read_bytes()
    if len(data)!=0x8000000 or hashlib.sha256(data).hexdigest()!=m.get('sha256'):raise ValueError('Full backup size or SHA256 differs from its manifest')
    _,active=select(data[0x80000:0x100000])
    if output.exists():raise FileExistsError(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.'+output.name+'-',dir=output.parent) as staging:
        stage=Path(staging)/'package';stage.mkdir()
        for name,start,blocks in active['parts']:
            part=data[start*BLOCK:(start+blocks)*BLOCK];filename=name.replace(':','_')+'.bin'
            (stage/filename).write_bytes(part)
            pm=dict(m,schema='p5-partition-backup-v1',partition=name,start=start*BLOCK,partition_size=len(part),bytes=len(part),sha256=hashlib.sha256(part).hexdigest(),format='nand-ecc-data',filename=filename,layout='single-rootfs' if active['single'] else 'original-dual',restore_tested=False)
            (stage/(filename+'.json')).write_text(json.dumps(pm,indent=2)+'\n')
        result={'partitions':len(active['parts']),'unused_tail_restored':False,'oob_supported':False,'writes_enabled':False}
        (stage/'split.json').write_text(json.dumps(result,indent=2)+'\n')
        if output.exists():raise FileExistsError(output)
        stage.rename(output)
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='command',required=True)
    l=sub.add_parser('layout');l.add_argument('--mibib',type=Path,required=True);l.add_argument('--target',choices=['single','dual'],required=True);l.add_argument('--output',type=Path,required=True)
    r=sub.add_parser('restore');r.add_argument('--backup',type=Path,required=True);r.add_argument('--manifest',type=Path,required=True);r.add_argument('--current-manifest',type=Path,required=True);r.add_argument('--mibib',type=Path,required=True);r.add_argument('--output',type=Path,required=True)
    r.add_argument('--allow-full-restoration',action='store_true',help='Explicitly allow a U-Boot partition restore')
    f=sub.add_parser('split-full');f.add_argument('--backup',type=Path,required=True);f.add_argument('--manifest',type=Path,required=True);f.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    if args.command=='split-full':result=split_full(args.backup,args.manifest,args.output)
    elif args.command=='restore':result=prepare_restore(args.backup,args.manifest,args.current_manifest,args.mibib,args.output,args.allow_full_restoration)
    else:
        old=args.mibib.read_bytes();new=conversion(old,args.target=='single')
        with args.output.open('xb') as f:f.write(new)
        result={'target':args.target,'old_sha256':hashlib.sha256(old).hexdigest(),'new_sha256':hashlib.sha256(new).hexdigest(),'firmware_migrated':False,'writes_enabled':False}
    print(json.dumps(result,indent=2))
if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,OSError) as e:raise SystemExit('Preparation refused: '+str(e))
