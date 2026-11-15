(function(){
 'use strict';
 var about=null,meta=null,ready=false,busy=false;
 function el(id){return document.getElementById(id);}
 function t(s){return uiText(s);}
 function hex(n){return '0x'+n.toString(16);}
 function failures(items){return items.map(t).join('\n');}
 function report(id,lines){el(id).textContent=lines.join('\n');}
 function disabled(v){busy=v;var blocked=!ready || !about.storage_writes || P5Plan.inspect(about,meta).errors.length>0;['layout-apply','restore-apply','install-apply','factory-flash'].forEach(function(id){el(id).disabled=v || blocked;});['layout-check','restore-check','boot-upload','boot-installed','uboot-ram-boot','install-upload-check','flash-ram-boot'].forEach(function(id){el(id).disabled=v;});}
 function show(){
  var section=location.hash.slice(1)||'home';
  if(['home','boot','layout','restore','install'].indexOf(section)<0)section='home';
  el('recovery-home').hidden=section!=='home';
  document.documentElement.classList.toggle('recovery-tool',section!=='home');
  var titles={home:'P5 recovery',boot:'Boot / Flash OpenWrt',layout:'Change flash layout',restore:'Restore partitions',install:'Test / Flash U-boot'};
  el('recovery-title').textContent=t(titles[section]);document.title=t(titles[section]);window.parent.document.title=document.title;
  document.querySelectorAll('.recovery-panel').forEach(function(p){p.hidden=p.id!==section;});
 }
 function selectTab(prefix,names,name){
  names.forEach(function(value){
   var selected=value===name,tab=el(prefix+'-'+value+'-tab');
   tab.classList.toggle('active',selected);tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;
   el(prefix+'-'+value+'-panel').hidden=!selected;
  });
 }
 [['uboot',['test','flash']],['openwrt',['boot','flash']]].forEach(function(group){
  var prefix=group[0],names=group[1];
  names.forEach(function(name){
   var tab=el(prefix+'-'+name+'-tab');
   tab.onclick=function(){if(!busy)selectTab(prefix,names,name);};
   tab.onkeydown=function(event){
    if(busy || ['ArrowLeft','ArrowRight','Home','End'].indexOf(event.key)<0)return;
    event.preventDefault();var next=event.key==='Home'?names[0]:event.key==='End'?names[1]:name===names[0]?names[1]:names[0];
    selectTab(prefix,names,next);el(prefix+'-'+next+'-tab').focus();
   };
  });
 });
 show();window.addEventListener('hashchange',show);disabled(true);
 Promise.all(['/about','/partitions'].map(function(url){return fetch(url,{cache:'no-store'}).then(function(r){if(!r.ok)throw Error();return r.json();});})).then(function(data){
  about=data[0];meta=data[1];var state=P5Plan.inspect(about,meta);
  el('recovery-status').textContent=(about.model||'P5')+' | '+(about.version||'')+' | '+t(state.layout==='original-dual'?'Original dual rootfs':(state.layout==='single-rootfs'?'Single rootfs':'Unknown layout'))+' | '+location.host;
  ready=!!(about.ram_test||about.p5_recovery) && about.machid==='0x8040002';disabled(!ready);
  if(!ready){el('recovery-error').hidden=false;el('recovery-error').textContent=t('Unsupported board or build. Checks are disabled.');}
 }).catch(function(){el('recovery-error').hidden=false;el('recovery-error').textContent=t('Could not read board metadata. Reload the page to retry.');});
 function map(parent,title,parts){
  var box=document.createElement('section'),h=document.createElement('h3');h.textContent=t(title);box.appendChild(h);
  var table=document.createElement('table');table.className='partition-table';
  parts.forEach(function(p){
   var row=table.insertRow();row.insertCell().textContent=p.name;
   var info=row.insertCell();var size=document.createElement('strong');size.textContent=formatSize(p.size);info.appendChild(size);
   var range=document.createElement('small');range.textContent=hex(p.start)+' / '+hex(p.start+p.size);info.appendChild(range);
  });box.appendChild(table);parent.appendChild(box);
 }
 el('layout-target').onchange=function(){el('layout-result').replaceChildren();};
 el('layout-check').onclick=function(){
  if(!ready)return;
  var p=P5Plan.conversion(about,meta,el('layout-target').value),out=el('layout-result');out.replaceChildren();
  if(p.errors.length){report('layout-result',[t('Check blocked:'),failures(p.errors)]);return;}
  var comparison=document.createElement('div');comparison.className='layout-comparison';
  map(comparison,'Current layout',meta.parts.filter(function(p){return p.name==='rootfs' || p.name==='rootfs_1';}));
  map(comparison,'Proposed layout',p.proposed);out.appendChild(comparison);
  var summary=document.createElement('p');summary.className='layout-summary';
  summary.textContent=p.noChange?t(el('layout-target').value==='single'?'Layout already matches. Apply layout to check and repair BOOTCONFIG if needed.':'Layout already matches. No layout change is needed.'):t('Conversion updates MIBIB. Single-rootfs conversion also sets both BOOTCONFIG copies to primary rootfs when needed. Firmware data stays unchanged.');out.appendChild(summary);
  var details=document.createElement('details'),label=document.createElement('summary');label.textContent=t('Full partition map');details.appendChild(label);
  map(details,'Current partition map',meta.parts);out.appendChild(details);
  if(!about.storage_writes){var note=document.createElement('p');note.textContent=t('Conversion and restore writes are disabled in this build.');out.appendChild(note);}
 };
 async function checkedFile(kind){
  var file=el(kind+'-file').files[0];
  if(!file || !file.size)throw Error(t('Choose a file first.'));
  if(file.size>0x4800000 || (kind==='install' && file.size>0x140000))throw Error(t('File exceeds this check’s size limit.'));
  var bytes=await file.arrayBuffer(),hash=sha256(bytes),manifest,errors;
  if(kind==='restore'){
   var mf=el('restore-manifest').files[0];if(!mf || mf.size>65536)throw Error(t('Choose a backup manifest under 64 KiB.'));
   manifest=JSON.parse(await mf.text());errors=P5Plan.restore(about,meta,manifest,{size:file.size,sha256:hash},el('restore-full').checked);
  }else errors=P5Plan.bootloader(bytes);
  if(errors.length)throw Error(failures(errors));
  return {file:file,bytes:bytes,hash:hash,manifest:manifest};
 }
 el('restore-check').onclick=async function(){
  if(!ready)return;
  disabled(true);setUiBusy(true);
  try{
   var checked=await checkedFile('restore');
   report('restore-result',[t('File size:')+' '+formatSize(checked.file.size),'SHA256: '+checked.hash,t('Backup integrity matches. NAND bad-block restore mapping and write/readback remain untested.'),t('No flash changes made.')]);
  }catch(e){report('restore-result',[t('Check blocked:'),e instanceof SyntaxError?t('Invalid JSON manifest.'):e.message,t('No flash changes made.')]);}
  finally{disabled(false);setUiBusy(false);}
 };
 async function requestBoot(kind, receipt) {
  var body=kind==='installed'?'installed':kind+','+receipt.bytes+','+receipt.crc32;
  var response=await fetch('/p5_boot',{method:'POST',headers:{'Content-Type':'text/plain'},body:body});
  if(!response.ok)throw Error(t('Boot refused. Check the image format and UART output.'));
  var result=await response.json();if(kind==='install' && result.checked)return; if(!result.queued)throw Error(t('Boot request was not accepted.'));
 }
 async function uploadAndBoot(inputId,resultId,kind){
  if(!ready)return;
  var input=el(inputId),file=input.files[0];
  if(!file || !file.size || file.size>32*1024*1024)return report(resultId,[t('Choose a non-empty file of at most 32 MiB.')]);
  disabled(true);input.disabled=true;setUiBusy(true);
  try {
   report(resultId,[t('Uploading to RAM…')]);
   var receipt=await upload(await file.arrayBuffer(),file.name);
   await requestBoot(kind,receipt);
   report(resultId,kind==='install'?[t('Package, board, secure-boot and slot checks pass. No flash changes made.'),'p5install check '+file.size.toString(16),t('Use Install U-Boot for permanent installation.')]:[t('Boot request accepted. The connection may close. Watch UART.')]);
  }catch(e){report(resultId,[e.message,t('No flash installation is performed.')]);}
  finally {disabled(false);input.disabled=false;setUiBusy(false);}
 }
 el('boot-upload').onclick=function(){uploadAndBoot('boot-upload-file','boot-upload-result','initramfs');};
 el('uboot-ram-boot').onclick=function(){uploadAndBoot('uboot-ram-file','uboot-ram-result','uboot');};
 el('flash-ram-boot').onclick=function(){uploadAndBoot('install-file','install-result','flash');};
 el('install-upload-check').onclick=function(){uploadAndBoot('install-file','install-result','install');};
 el('boot-installed').onclick=async function(){
  if(!ready)return;disabled(true);setUiBusy(true);
  try {await requestBoot('installed');report('installed-result',[t('Boot request accepted. The connection may close. Watch UART.')]);}
  catch(e){report('installed-result',[e.message]);}
  finally {disabled(false);setUiBusy(false);}
 };
 async function upload(bytes,name){
  if(!bytes.byteLength || bytes.byteLength>32*1024*1024)throw Error(t('Choose a non-empty file of at most 32 MiB.'));
  var form=new FormData();form.append('ram_image',new Blob([bytes]),name);
  var controller=new AbortController(),timer=setTimeout(function(){controller.abort();},120000),response;
  try {response=await fetch('/',{method:'POST',body:form,signal:controller.signal});}
  finally {clearTimeout(timer);}
  if(!response.ok)throw Error(t('RAM upload failed.'));
  var receipt=null,crc=P5Plan.crc32(new Uint8Array(bytes));
  for(var attempt=0;attempt<20;attempt++){
   var status=await fetch('/ram_status',{cache:'no-store'});
   if(status.ok){var candidate=await status.json();if(candidate.valid && candidate.executed===false && candidate.bytes===bytes.byteLength && candidate.crc32===crc){receipt=candidate;break;}}
   await new Promise(function(resolve){setTimeout(resolve,250);});
  }
  if(!receipt)throw Error(t('RAM receipt does not match this file.'));
  return {bytes:bytes.byteLength,crc32:crc};
 }
 function receiptArgs(receipt){return receipt.bytes+','+receipt.crc32;}
 el('factory-flash').onclick=async function(){
  if(!ready || busy)return;
  var file=el('factory-file').files[0];
  if(!file || !file.size || file.size>32*1024*1024 || file.size%0x20000)
   return report('factory-result',[t('Choose a factory UBI image of at most 32 MiB.')]);
  disabled(true);setUiBusy(true);el('factory-file').disabled=true;
  var started=false;
  try{
   var bytes=await file.arrayBuffer(),magic=new DataView(bytes).getUint32(0,false);
   if(magic!==0x55424923)throw Error(t('This is not a factory UBI image.'));
   if(!window.confirm(t('Make sure you have a backup. This replaces OpenWrt and settings. Do not disconnect power.')))return;
   report('factory-result',[t('Uploading to RAM…')]);
   var receipt=await upload(bytes,file.name);
   report('factory-result',[t('Checking image, writing and verifying. Do not disconnect power.')]);
   started=true;await storage('factory,'+receiptArgs(receipt)+',FLASH-P5-FACTORY');
   report('factory-result',[t('Write and readback verification complete. Restart manually when ready.')]);ready=false;
  }catch(e){
   report('factory-result',[e.message,t(started?'Stopped. No automatic retry or restart. Check UART before continuing.':'No flash changes made.')]);
   if(started)ready=false;
  }finally{disabled(!ready);setUiBusy(false);el('factory-file').disabled=!ready;}
 };

 async function storage(command){
  var response=await fetch('/p5_storage',{method:'POST',headers:{'Content-Type':'text/plain','X-P5-Recovery':'1'},body:command});
  if(!response.ok)throw Error(t('Storage request refused.'));
  var queued=await response.json();if(!queued.queued || !Number.isSafeInteger(queued.sequence))throw Error(t('Storage request refused.'));
  for(var i=0;i<2400;i++){
   await new Promise(function(resolve){setTimeout(resolve,250);});
   var status=await fetch('/p5_storage_status',{cache:'no-store'});if(!status.ok)break;
   var result=await status.json();if(result.sequence!==queued.sequence)break;
   if(!result.busy){if(result.result!==0 && result.result!==1)throw Error(t('Storage operation failed. Check UART before another action.'));return result.result;}
  }
  throw Error(t('Storage result is uncertain. Check UART before another action.'));
 }
 async function currentHash(p){
  var response=await fetch('/backup?part='+encodeURIComponent(p.name)+'&size='+p.size,{cache:'no-store'});if(!response.ok)throw Error(t('Current partition read failed.'));
  var bytes=await response.arrayBuffer();if(bytes.byteLength!==p.size)throw Error(t('Current backup size does not match.'));
  return sha256(bytes);
 }
 async function apply(kind){
  if(busy || !ready || !about.storage_writes)return;
  var state=P5Plan.inspect(about,meta);if(state.errors.length)return;
  disabled(true);setUiBusy(true);document.querySelectorAll('input,select').forEach(function(e){e.disabled=true;});var id=kind+'-result',storageAttempted=false;
  async function writeCommand(command){storageAttempted=true;return storage(command);}
  try{
   var bytes,file,p,manifest,hash;
   if(kind==='layout'){
    var plan=P5Plan.conversion(about,meta,el('layout-target').value);if(plan.errors.length)throw Error(failures(plan.errors));
    if(plan.noChange && el('layout-target').value!=='single')throw Error(t('Layout already matches.'));
    p=state.parts.find(function(p){return p.name==='0:MIBIB';});
   }else{
    var checked=await checkedFile(kind);file=checked.file;bytes=checked.bytes;hash=checked.hash;manifest=checked.manifest;
    p=kind==='install'?state.parts.find(function(p){return p.name==='0:APPSBL';}):P5Plan.destination(state,manifest);
   }
   var warning=kind==='install'?'Make sure you have a backup. This permanently replaces U-Boot. Do not disconnect power.':'Make sure you have a backup. This writes flash. Do not disconnect power.';
   if(!window.confirm(t(warning)))return;
   var protectedPart=p.name!=='rootfs' && p.name!=='rootfs_1';
   if(kind==='restore' && protectedPart && !window.confirm(t('Restore protected partition? This can prevent boot or replace calibration and identity data.')+' '+p.name))return;
   var oldHash=kind==='install'?null:await currentHash(p);report(id,[t('Writing and verifying. Do not disconnect power.')]);
   if(kind==='layout'){
    if(await writeCommand('layout,'+el('layout-target').value+','+oldHash+',CONVERT-P5-LAYOUT')===1){
     report(id,[t('Layout and boot selection already match. No changes made.')]);return;
    }
   }
   else if(kind==='install'){
    var receipt=await upload(bytes,file.name);await requestBoot('install',receipt);
    await writeCommand('install,'+receiptArgs(receipt)+',INSTALL-P5-APPSBL');
   }else{
    var hashes=[];for(var off=0;off<bytes.byteLength;off+=0x20000)hashes.push(sha256(bytes.slice(off,off+0x20000)));
    var descriptor=P5Plan.descriptor(p,about.mac_addr,oldHash,hash,hashes);
    var token=(p.name==='0:APPSBL' || p.name==='0:APPSBL_1')?'RESTORE-P5-FULL':protectedPart?'RESTORE-P5-PROTECTED':'RESTORE-P5-PARTITION';
    await writeCommand('restore-begin,'+receiptArgs(await upload(descriptor.buffer,'descriptor.bin'))+','+token);
    for(var i=0;i<hashes.length;i++){report(id,[t('Writing and verifying. Do not disconnect power.'),' '+(i+1)+' / '+hashes.length]);await writeCommand('restore-chunk,'+i+','+receiptArgs(await upload(bytes.slice(i*0x20000,(i+1)*0x20000),'chunk.bin')));}
    await writeCommand('restore-finish');
   }
   report(id,[t('Write and readback verification complete. Restart manually when ready.')]);ready=false;
  }catch(e){
   report(id,[e.message,t(storageAttempted?'Stopped. No automatic retry or restart. Check UART before continuing.':'No flash changes made.')]);
   if(storageAttempted)ready=false;
  }
  finally{disabled(!ready);setUiBusy(false);document.querySelectorAll('input,select').forEach(function(e){e.disabled=!ready;});}
 }
 ['layout','restore','install'].forEach(function(kind){el(kind+'-apply').onclick=function(){return apply(kind);};});
})();
