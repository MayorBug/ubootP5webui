const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const vm=require('node:vm');const fs=require('node:fs');const path=require('node:path');
const plan=require('../../failsafe/html/pig/recovery.js');const fixture=require('./p5-fixtures.cjs');
const {about,meta}=fixture;const copy=x=>JSON.parse(JSON.stringify(x));
assert.equal(plan.crc32(Buffer.from('123456789')),'cbf43926');
assert.equal(plan.inspect(about,meta).layout,'original-dual');
const single=plan.conversion(about,meta,'single');assert.equal(single.canApply,false);assert.deepEqual(single.errors,[]);assert.equal(single.proposed[0].size,0x4800000);assert.equal(single.unusedTail.start,0x4fc0000);
assert.equal(plan.conversion(about,meta,'dual').noChange,true);assert.equal(single.noChange,false);
const singleMeta=copy(meta);singleMeta.parts=singleMeta.parts.filter(p=>p.name!=='rootfs'&&p.name!=='rootfs_1');singleMeta.parts.push({name:'rootfs',start:0x7c0000,size:0x4800000,flash:'nand'});assert.equal(plan.inspect(about,singleMeta).layout,'single-rootfs');assert.equal(plan.conversion(about,singleMeta,'single').noChange,true);assert.equal(plan.conversion(about,singleMeta,'dual').noChange,false);assert.equal(plan.conversion(about,singleMeta,'dual').proposed.length,2);

for(const mutate of [m=>m.parts[0].size++,m=>m.parts.push(copy(m.parts[0])),m=>m.parts.pop(),m=>m.parts[1].start=0,m=>m.parts[3].name='unexpected',m=>m.source='unknown',m=>m.nand_size=0x10000000]){
 const m=copy(meta);mutate(m);assert.equal(plan.inspect(about,m).layout,'unknown');assert.equal(plan.conversion(about,m,'single').proposed.length,0);
}
assert.equal(plan.inspect({...about,machid:'wrong'},meta).layout,'unknown');
assert.equal(plan.conversion(about,meta,'erase').proposed.length,0);
const hash='a'.repeat(64),manifest={schema:'p5-partition-backup-v1',board:'cudy,p5',machid:about.machid,device_mac:about.mac_addr,layout:'original-dual',partition:'rootfs',start:0x2bc0000,partition_size:0x2400000,bytes:0x2400000,sha256:hash,format:'nand-ecc-data'};
assert.deepEqual(plan.restore(about,meta,manifest,{size:manifest.bytes,sha256:hash}),[]);
for(const change of [{device_mac:'wrong'},{layout:'single'},{start:0},{format:'nand-raw-oob'},{sha256:'bad'}])assert(plan.restore(about,meta,{...manifest,...change},{size:manifest.bytes,sha256:hash}).length);
assert.deepEqual(plan.restore(about,meta,{...manifest,partition:'0:ART',start:0x600000,partition_size:0x100000,bytes:0x100000},{size:0x100000,sha256:hash}),[]);
assert(plan.restore(about,meta,{...manifest,partition:'0:MIBIB',start:0x80000,partition_size:0x80000,bytes:0x80000},{size:0x80000,sha256:hash}).length);
assert.deepEqual(plan.restore(about,meta,{...manifest,partition:'rootfs_1'},{size:manifest.bytes,sha256:hash}),[],'Firmware aliases bind by physical bounds');
const desc=plan.descriptor({name:'bdinfo',start:0x780000,size:0x40000},about.mac_addr,'a'.repeat(64),'b'.repeat(64),['c'.repeat(64),'d'.repeat(64)]);
assert.equal(desc.length,208);assert.equal(Buffer.from(desc.subarray(0,8)).toString(),'P5RST01\0');
const dv=new DataView(desc.buffer);assert.equal(dv.getUint32(48,true),0x780000);assert.equal(dv.getUint32(140,true),2);assert.equal(Buffer.from(desc.subarray(76,108)).toString('hex'),'a'.repeat(64));assert.equal(Buffer.from(desc.subarray(144,176)).toString('hex'),'c'.repeat(64));
for(const name of ['0:APPSBL','0:APPSBL_1']) {
 const p=meta.parts.find(p=>p.name===name),m={...manifest,partition:name,start:p.start,partition_size:p.size,bytes:p.size},f={size:p.size,sha256:hash};
 assert(plan.restore(about,meta,m,f).some(e=>e.includes('U-Boot is excluded')));
 assert(plan.restore(about,meta,m,f,false).length);
 assert.deepEqual(plan.restore(about,meta,m,f,true),[]);
}
assert(plan.restore(about,meta,null,null).length);
assert(plan.bootloader(new ArrayBuffer(8)).length);
const pkg=require('node:fs').readFileSync(require('node:path').join(__dirname,'../../out-p5-flash/candidate/p5-uboot.mbn'));const b=pkg.buffer.slice(pkg.byteOffset,pkg.byteOffset+pkg.byteLength);assert.deepEqual(plan.bootloader(b),[]);new DataView(b).setUint32(24,0x46000001,true);assert(plan.bootloader(b).length);
const sandbox={};vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../failsafe/html/pig/sha256.js'),'utf8'),sandbox);
for(const text of ['','abc','P5 recovery test']){const bytes=Buffer.from(text);assert.equal(sandbox.sha256(Uint8Array.from(bytes).buffer),crypto.createHash('sha256').update(bytes).digest('hex'));}
console.log('PASS: layout detection, geometry/identity failures, protected restore validation, descriptor offsets, invalid manifests, Qualcomm ELF headers, SHA256 vectors');
