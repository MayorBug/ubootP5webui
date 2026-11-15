/* Pure validation and descriptor helpers; no device commands. */
(function(root) {
 'use strict';
 var START=0x7c0000, SLOT=0x2400000, END=0x4fc0000;
 function integer(n) { return Number.isSafeInteger(n) && n>=0; }
 function inspect(about, meta) {
  var errors=[], parts=meta && meta.parts;
  if(!about || about.machid!=='0x8040002' || !(about.ram_test||about.p5_recovery)) errors.push('This planner requires the P5 RAM-test build.');
  if(!meta || meta.source!=='smem' || !meta.has_nand || meta.nand_size!==0x8000000) errors.push('Expected a complete P5 128 MiB NAND metadata snapshot.');
  if(!Array.isArray(parts) || !parts.length || parts.length>64) return {layout:'unknown',errors:errors.concat('Partition metadata is missing or invalid.'),parts:[]};
  var names=new Set(), sorted=parts.slice().sort(function(a,b){return a.start-b.start;});
  sorted.forEach(function(p,i){
   if(typeof p.name!=='string' || !p.name || names.has(p.name) || p.flash!=='nand' || !integer(p.start) || !integer(p.size) || !p.size || p.start+p.size>0x8000000 || p.start%0x20000 || p.size%0x20000) errors.push('Partition metadata has invalid geometry or duplicate names.');
   names.add(p.name);
   if(i && sorted[i-1].start+sorted[i-1].size>p.start) errors.push('Partition regions overlap.');
  });
  [['0:SBL1',0,0x80000],['0:BOOTCONFIG',0x100000,0x40000],['0:BOOTCONFIG1',0x140000,0x40000],['0:QSEE',0x180000,0x100000],['0:DEVCFG',0x280000,0x40000],['0:CDT',0x2c0000,0x40000],['0:TRAINING',0x700000,0x80000],['0:MIBIB',0x80000,0x80000],['0:APPSBLENV',0x300000,0x80000],['0:APPSBL',0x380000,0x140000],['0:APPSBL_1',0x4c0000,0x140000],['0:ART',0x600000,0x100000],['bdinfo',0x780000,0x40000]].forEach(function(spec){
   if(!parts.some(function(p){return p.name===spec[0] && p.start===spec[1] && p.size===spec[2];})) errors.push('Protected P5 partition does not match.');
  });
  var firmware=parts.filter(function(p){return p.name==='rootfs' || p.name==='rootfs_1';}).sort(function(a,b){return a.start-b.start;});
  var dual=firmware.length===2 && firmware[0].start===START && firmware[0].size===SLOT && firmware[1].start===START+SLOT && firmware[1].size===SLOT && new Set(firmware.map(function(p){return p.name;})).size===2;
  var single=firmware.length===1 && firmware[0].name==='rootfs' && firmware[0].start===START && firmware[0].size===2*SLOT;
  if(!dual && !single)errors.push('Only the checked P5 dual or single-rootfs geometry is supported.');
  return {layout:errors.length?'unknown':(single?'single-rootfs':'original-dual'),errors:errors,parts:sorted,firmware:firmware};
 }
 function conversion(about,meta,target) {
  var state=inspect(about,meta), errors=state.errors.slice();
  if(target!=='single' && target!=='dual') errors.push('Choose single or dual rootfs.');
  var proposed=target==='single'?[{name:'rootfs',start:START,size:END-START}]:[{name:'rootfs',start:START,size:SLOT},{name:'rootfs_1',start:START+SLOT,size:SLOT}];
  var blockers=[about && about.storage_writes?'Storage writes are enabled. Each action requires confirmation.':'Conversion and restore writes are disabled in this build.','Conversion updates MIBIB and, for single rootfs, BOOTCONFIG. Firmware data stays unchanged. Restart before installing matching firmware.'];
  return {layout:state.layout,errors:errors,proposed:errors.length?[]:proposed,protected:state.parts.filter(function(p){return p.name!=='rootfs' && p.name!=='rootfs_1';}),unusedTail:{start:END,size:0x8000000-END},noChange:(state.layout==='original-dual' && target==='dual') || (state.layout==='single-rootfs' && target==='single'),blockers:blockers,canApply:!errors.length && !!about.storage_writes};
 }
 function restore(about,meta,manifest,file,allowFull) {
  var state=inspect(about,meta), errors=state.errors.slice();
  if(!manifest || manifest.schema!=='p5-partition-backup-v1' || manifest.board!=='cudy,p5' || manifest.machid!==(about&&about.machid)) return errors.concat('Unsupported or wrong-board backup manifest.');
  if(manifest.layout!==state.layout) errors.push('Backup layout does not match current metadata.');
  if(!manifest.device_mac || manifest.device_mac!==(about&&about.mac_addr)) errors.push('Backup device identity does not match this P5.');
  var p=destination(state,manifest);
  if(!p || p.start!==manifest.start || p.size!==manifest.partition_size) errors.push('Backup destination does not match current metadata.');
  if(manifest.format!=='nand-ecc-data' || manifest.bytes!==manifest.partition_size) errors.push('Only complete ECC-corrected partition backups without OOB are supported.');
  if(!file || file.size!==manifest.bytes || file.sha256!==manifest.sha256 || !/^[a-f0-9]{64}$/.test(manifest.sha256 || '')) errors.push('Backup size or SHA256 does not match.');
  if(p && (p.name==='0:APPSBL' || p.name==='0:APPSBL_1') && allowFull!==true) errors.push('U-Boot is excluded. Select Allow full restoration to include it.');
  if(p && p.name==='0:MIBIB') errors.push('MIBIB requires the layout converter.');
  return errors;
 }
 function destination(state,m) {
  return state.parts.find(function(p){return p.start===m.start && p.size===m.partition_size && (p.name===m.partition || ((p.name==='rootfs' || p.name==='rootfs_1') && (m.partition==='rootfs' || m.partition==='rootfs_1')));});
 }
 function descriptor(p,mac,oldHash,newHash,hashes) {
  var bytes=new Uint8Array(144+hashes.length*32),v=new DataView(bytes.buffer);
  bytes.set(new TextEncoder().encode('P5RST01'));v.setUint32(8,1,true);v.setUint32(12,bytes.length,true);
  bytes.set(new TextEncoder().encode(p.name),16);
  [p.start,p.size,2048,0x20000,0x8040002].forEach(function(n,i){v.setUint32(48+i*4,n,true);});
  function put(hex,off){bytes.set(hex.match(/../g).map(function(h){return parseInt(h,16);}),off);}
  put(mac.split(',')[0].trim().replace(/:/g,''),68);put(oldHash,76);put(newHash,108);v.setUint32(140,hashes.length,true);
  hashes.forEach(function(h,i){put(h,144+i*32);});return bytes;
 }
 function bootloader(bytes) {
  var errors=[],v=new DataView(bytes);
  if(bytes.byteLength<52 || bytes.byteLength>0x140000)return ['Expected a P5 Qualcomm ELF package within the APPSBL slot capacity.'];
  if(v.getUint32(0,true)!==0x464c457f || v.getUint8(4)!==1 || v.getUint8(5)!==1 || v.getUint16(16,true)!==2 || v.getUint16(18,true)!==40 || v.getUint32(24,true)!==0x4a920000)return ['Expected a flash-linked P5 ELF package with entry 0x4a920000.'];
  var off=v.getUint32(28,true),count=v.getUint16(44,true);
  if(off!==52 || v.getUint16(42,true)!==32 || count<3 || count>16 || off+count*32>bytes.byteLength)errors.push('ELF program headers are invalid.');
  return errors;
 }

 function crc32(bytes) {
  var crc=0xffffffff;
  for(var i=0;i<bytes.length;i++) {
   crc^=bytes[i];
   for(var j=0;j<8;j++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);
  }
  return ((crc^0xffffffff)>>>0).toString(16).padStart(8,'0');
 }
 root.P5Plan={crc32:crc32,destination:destination,descriptor:descriptor,inspect:inspect,conversion:conversion,restore:restore,bootloader:bootloader};
 if(typeof module!=='undefined') module.exports=root.P5Plan;
})(typeof window!=='undefined'?window:globalThis);
