#!/usr/bin/env python3
"""Offline package integrity and atomic publication tests; no router access."""
from pathlib import Path
import hashlib,json,tempfile,zlib
import storage as s
from test_support import mibib_fixture

with tempfile.TemporaryDirectory(prefix='p5-storage-packages-') as directory:
    d=Path(directory);mibib=d/'mibib.bin';mibib.write_bytes(mibib_fixture())
    backup=d/'art.bin';data=b'\x35'*0x100000;backup.write_bytes(data)
    base=dict(schema='p5-partition-backup-v1',board='cudy,p5',machid='0x8040002',device_mac='80:AF:CA:26:AC:39, 80:AF:CA:26:AC:38',partition='0:ART',start=0x600000,partition_size=len(data),bytes=len(data),format='nand-ecc-data',sha256=hashlib.sha256(data).hexdigest())
    manifest=d/'backup.json';manifest.write_text(json.dumps(base));current=d/'current.json';current.write_text(json.dumps(dict(base,sha256=hashlib.sha256(b'\xff'*len(data)).hexdigest())))
    out=d/'restore';meta=s.prepare_restore(backup,manifest,current,mibib,out)
    desc=(out/'descriptor.bin').read_bytes();fields=s.DESCRIPTOR.unpack(desc[:144]);assert len(desc)==144+8*32 and fields[-1]==8 and fields[3].rstrip(b'\0')==b'0:ART'
    assert zlib.crc32(desc)==int(meta['descriptor_crc32'],16) and meta['token']=='RESTORE-P5-PROTECTED'
    for i in range(8):assert hashlib.sha256((out/f'chunk-{i:04d}.bin').read_bytes()).digest()==desc[144+i*32:176+i*32]
    assert hashlib.sha256(b''.join((out/f'chunk-{i:04d}.bin').read_bytes() for i in range(8))).hexdigest()==base['sha256']
    for change in [{'format':'nand-raw-oob'},{'device_mac':'wrong'},{'start':0x780000},{'sha256':'0'*64}]:
        manifest.write_text(json.dumps(dict(base,**change)))
        try:s.prepare_restore(backup,manifest,current,mibib,d/'refused');raise AssertionError(change)
        except ValueError:pass
        assert not (d/'refused').exists()
    manifest.write_text(json.dumps(base))
    for name,start in [('0:APPSBL',0x380000),('0:APPSBL_1',0x4c0000)]:
        data=b'\x35'*0x140000;backup.write_bytes(data)
        boot=dict(base,partition=name,start=start,partition_size=len(data),bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        manifest.write_text(json.dumps(boot));current.write_text(json.dumps(boot))
        try:s.prepare_restore(backup,manifest,current,mibib,d/'boot-refused');raise AssertionError('U-Boot accepted without opt-in')
        except ValueError as error:assert 'U-Boot is excluded' in str(error)
        assert not (d/'boot-refused').exists()
        assert s.prepare_restore(backup,manifest,current,mibib,d/name.replace(':','-'),True)['token']=='RESTORE-P5-FULL'
    data=b'\x35'*0x100000;backup.write_bytes(data)
    manifest.write_text(json.dumps(base));current.write_text(json.dumps(base))
    original=Path.open
    def fail_open(path,*args,**kwargs):
        if path.name=='chunk-0002.bin':raise OSError('injected disk-full error')
        return original(path,*args,**kwargs)
    Path.open=fail_open
    try:
        try:s.prepare_restore(backup,manifest,current,mibib,d/'partial');raise AssertionError('Expected export failure')
        except OSError:pass
    finally:Path.open=original
    assert not (d/'partial').exists() and not list(d.glob('.partial-*'))
    for target in (True,False):
        candidate=s.conversion(mibib.read_bytes(),target);assert s.select(candidate)[1]['single']==target
print('PASS: offline descriptor/chunk hashes, full-backup hash, wrong formats/identity/geometry, conversion packages and atomic failure cleanup')
