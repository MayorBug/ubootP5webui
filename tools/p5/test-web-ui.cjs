#!/usr/bin/env node
// Local fixture tests only. This server never connects to a router.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(process.env.P5_UI_ROOT || path.join(__dirname, '../../failsafe/html/pig'));
const about = {ram_test:true, model:'Cudy P5 RAM test', config_name:'config@mp03.3', config_name_source:'fdt', machid:'0x8040002', ram_size:268435456, flash_type:'QSPI-NAND', flash_chips:'nand0 128MiB', mac_addr:'80:AF:CA:26:AC:39, 80:AF:CA:26:AC:38', version:'test', gpios:[{name:'modem-power', gpio:35, dir:'out', value:1, parent:'modem_gpio', source:'fdt'}]};
const mac = {mac_editable:false, mac_source:'bdinfo+0xde00', mac_addr:about.mac_addr, mac_off:'bdinfo+0xDE00 (base+1), bdinfo+0xDE00', wifi_mac:'00:03:7F:12:34:56, 00:03:7F:12:34:57, 00:03:7F:12:34:56, 00:03:7F:12:34:57', wifi_off:'0x100E, 0x1014, 0x2680E, 0x26814', wifi_cs:'6AFB, 6AFB, 3A1A, 3A1A', wifi_cs_off:'0x100A, 0x100A, 0x2680A, 0x2680A', wifi_cs_ok:'1, 1, 1, 1'};
const parts = require('./p5-fixtures.cjs').meta;
const requests = [];
let ramReceipt={valid:false}, badRamReceipt=false;
const bootRequests=[];
const storageRequests=[];let sequence=0;
const fixturePlan=require('../../failsafe/html/pig/recovery.js');
const server = http.createServer((req,res) => {
  requests.push(req.method+' '+req.url);
  const url = new URL(req.url,'http://localhost');
  const fixtures = {'/about':about,'/mac_info':mac,'/partitions':parts,'/btn_detect':[]};
  if(url.pathname==='/p5_storage_status'){res.setHeader('Content-Type','application/json');return res.end(JSON.stringify({busy:false,sequence,result:0}));}
  if(req.method==='POST' && url.pathname==='/p5_storage'){
   assert.equal(req.headers['x-p5-recovery'],'1');let body='';req.on('data',b=>body+=b);req.on('end',()=>{storageRequests.push(body);sequence++;res.statusCode=202;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({queued:true,sequence}));});return;
  }
  if (fixtures[url.pathname]) { res.setHeader('Content-Type','application/json'); return res.end(JSON.stringify(fixtures[url.pathname])); }
  if(req.method==='POST' && url.pathname==='/p5_boot'){
   let body='';req.on('data',b=>body+=b);req.on('end',()=>{
    if(body==='install,'+ramReceipt.bytes+','+ramReceipt.crc32){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({checked:true}));return;}
    const valid=body==='installed'||body==='initramfs,'+ramReceipt.bytes+','+ramReceipt.crc32||body==='uboot,'+ramReceipt.bytes+','+ramReceipt.crc32||body==='flash,'+ramReceipt.bytes+','+ramReceipt.crc32;
    if(!valid){res.statusCode=400;return res.end('refused');}
    bootRequests.push(body);res.statusCode=202;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({queued:true}));
   });return;
  }
  if (req.method==='POST' && url.pathname==='/') {
   const chunks=[];req.on('data',b=>chunks.push(b));req.on('end',()=>{
    const all=Buffer.concat(chunks),headerEnd=all.indexOf(Buffer.from('\r\n\r\n')),boundary=Buffer.from('\r\n--'+req.headers['content-type'].split('boundary=')[1]);
    const end=all.indexOf(boundary,headerEnd+4),file=all.subarray(headerEnd+4,end);
    ramReceipt={valid:true,bytes:file.length,address:0x4b000000,crc32:badRamReceipt?'00000000':fixturePlan.crc32(file),limit:32*1024*1024,executed:false};res.end('');
   });return;
  }
  if (url.pathname === '/ram_status') {res.setHeader('Content-Type','application/json');return res.end(JSON.stringify(ramReceipt));}
  if (url.pathname === '/backup' && parts.parts.some(p=>p.name===url.searchParams.get('part'))) { const p=parts.parts.find(p=>p.name===url.searchParams.get('part'));if(Number(url.searchParams.get('size'))!==p.size){res.statusCode=400;return res.end('Missing size');}res.setHeader('Content-Type','application/octet-stream');return res.end(Buffer.alloc(p.size,0x5a)); }
  if (url.pathname === '/webterm/status') return res.end('1');
  if (url.pathname === '/webterm/data') return res.end('Raw UART: 中文 output must stay unchanged\n');
  const file = path.join(root, url.pathname === '/' ? 'index.html' : url.pathname);
  if (!file.startsWith(root + path.sep)) {res.statusCode=403;return res.end();}
  try {res.setHeader('Content-Type', file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'text/html');res.end(fs.readFileSync(file));}
  catch {res.statusCode=404;res.end('not found');}
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({headless:true});
 try {
  const staticContext=await browser.newContext({javaScriptEnabled:false});
  const staticPage=await staticContext.newPage();
  for(const file of ['index','about','backup','img','term','update','recovery','404']){
   await staticPage.goto(origin+'/'+file+'.html');
   const title=await staticPage.title();
   assert(title && !/[\u4e00-\u9fff]/.test(title),file+': initial browser tab title must be English');
  }
  await staticContext.close();
  const context=await browser.newContext({locale:'en-US',viewport:{width:1440,height:1000}});
  const page=await context.newPage();
  const errors=[]; page.on('pageerror',e=>errors.push(e.stack));
  await page.route('**/about',route=>route.fulfill({status:503,body:'unavailable'}));
  await page.goto(origin+'/recovery.html#layout');
  await page.locator('#recovery-error').waitFor({state:'visible'});
  assert.match(await page.locator('#recovery-error').innerText(),/Reload/);
  await page.unroute('**/about');

  if(process.env.P5_SCREENSHOT_DIR){
   fs.mkdirSync(process.env.P5_SCREENSHOT_DIR,{recursive:true});
   for(const width of [1100,390]){
    await page.setViewportSize({width,height:850});
    for(const route of ['recovery.html','backup.html','recovery.html#boot','recovery.html#layout','recovery.html#restore','recovery.html#install']){
     await page.goto(origin+'/'+route);await page.waitForTimeout(200);
     if(route.endsWith('#layout'))await page.click('#layout-check');
     if(route.endsWith('#install')){
      await page.screenshot({path:path.join(process.env.P5_SCREENSHOT_DIR,'uboot-test-'+width+'.png'),fullPage:true});
      await page.click('#uboot-flash-tab');
     }
     await page.screenshot({path:path.join(process.env.P5_SCREENSHOT_DIR,route.replace(/[.#]/g,'-')+'-'+width+'.png'),fullPage:true});
     assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Page overflows: '+route);
    }
   }
   await page.setViewportSize({width:1440,height:1000});
  }

  about.storage_writes=true;
  await page.goto(origin+'/recovery.html#boot');
  await page.reload();
  await page.waitForFunction(()=>!document.querySelector('#factory-flash').disabled);
  await page.locator('#openwrt-flash-tab').click();
  await page.locator('#factory-flash').waitFor({state:'visible'});
  assert.equal(await page.locator('#openwrt-boot-panel').isVisible(),false);
  if(process.env.P5_SCREENSHOT_DIR){
   for(const width of [1100,390]){
    await page.setViewportSize({width,height:850});
    await page.screenshot({path:path.join(process.env.P5_SCREENSHOT_DIR,'factory-'+width+'.png'),fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Factory page overflows');
   }
   await page.setViewportSize({width:1440,height:1000});
  }
  const factory=Buffer.alloc(0x20000,0xff);factory.writeUInt32BE(0x55424923,0);
  await page.locator('#factory-file').setInputFiles({name:'test-factory.ubi',mimeType:'application/octet-stream',buffer:factory});
  let factoryBefore=storageRequests.length;
  page.once('dialog',d=>d.dismiss());await page.click('#factory-flash');
  await page.waitForTimeout(100);assert.equal(storageRequests.length,factoryBefore,'Cancelled factory write sends no command');
  page.once('dialog',d=>d.accept());await page.click('#factory-flash');
  await page.waitForFunction(()=>document.querySelector('#factory-result').textContent.includes('Restart manually'));
  assert.match(storageRequests.at(-1),/^factory,131072,[0-9a-f]{8},FLASH-P5-FACTORY$/);
  storageRequests.length=factoryBefore;
  await page.reload();await page.waitForFunction(()=>!document.querySelector('#factory-flash').disabled);await page.click('#openwrt-flash-tab');
  await page.locator('#factory-file').setInputFiles({name:'wrong.ubi',mimeType:'application/octet-stream',buffer:Buffer.alloc(0x20000)});
  await page.click('#factory-flash');await page.waitForTimeout(100);
  assert.match(await page.locator('#factory-result').innerText(),/not a factory UBI/);
  assert.equal(storageRequests.length,factoryBefore,'Non-UBI cannot issue a flash command');
  await page.goto(origin+'/recovery.html#boot');
  about.storage_writes=false;
  requests.length=0;
  const englishOnly = async label => {
   const text = (await page.locator('body').innerText()).replace('简体中文','').replace('Raw UART: 中文 output must stay unchanged','');
   assert(!/[\u4e00-\u9fff]/.test(text),label+': untranslated visible text '+text);
  };
  for (const lang of ['en','zh']) {
   await context.addInitScript(lang=>localStorage.setItem('uboot.language',lang),lang);
   for (const file of ['index','about','backup','img','term','update','recovery','404']) {
    await page.goto(origin+'/'+file+'.html'); await page.waitForTimeout(180);
    assert.equal(await page.locator('html').getAttribute('lang'),lang==='en'?'en':'zh-CN');
    if (lang==='en') {
     assert(!/[\u4e00-\u9fff]/.test(await page.title()),file+': untranslated browser tab title');
     const text=await page.locator('body').innerText();
     const remaining=text.replace('简体中文','').replace('Raw UART: 中文 output must stay unchanged','');
     assert(!/[\u4e00-\u9fff]/.test(remaining),file+': untranslated visible text '+remaining);
    }
    if(file==='about') {
     assert.equal(await page.title(),lang==='en'?'Device information':'设备信息');
     for (const tab of ['gpio','smem','mac']) {
      await page.locator('[data-tab='+tab+']').click();await page.waitForTimeout(70);
      if(lang==='en') await englishOnly('about '+tab);
     }
     await page.locator('[data-tab=mac]').click(); await page.waitForTimeout(100);
     assert.equal(await page.locator('#mac-tbody button').count(),0);
     assert.match(await page.locator('#mac-source').innerText(),/bdinfo/);
     for (const width of [1440, 1024, 768]) {
      await page.setViewportSize({width,height:1000});
      const geometry=await page.evaluate(()=>({
       tabs:Array.from(document.querySelectorAll('.tab-item')).map(e=>e.getBoundingClientRect().top),
       rows:Array.from(document.querySelectorAll('#mac-tbody tr')).map(r=>Array.from(r.cells).map(c=>({display:getComputedStyle(c).display,top:c.getBoundingClientRect().top,bottom:c.getBoundingClientRect().bottom})))
      }));
      assert(geometry.tabs.every(y=>Math.abs(y-geometry.tabs[0])<1),'Tabs must stay on one scrollable row');
      for(const row of geometry.rows) for(const cell of row){
       assert.equal(cell.display,'table-cell');
       assert(Math.abs(cell.top-row[0].top)<1 && Math.abs(cell.bottom-row[0].bottom)<1,'MAC cell borders must align');
      }
     }
     await page.setViewportSize({width:1440,height:1000});
     if(lang==='en' && process.env.P5_MAC_SCREENSHOT) await page.screenshot({path:process.env.P5_MAC_SCREENSHOT,fullPage:true});
    }
    if(file==='backup') {
     await page.selectOption('#part-select',String(parts.parts.findIndex(p=>p.name==='bdinfo')));
     const downloaded=page.waitForEvent('download');await page.click('#backup-btn');
     const backup=await downloaded;assert.equal(fs.readFileSync(await backup.path()).length,0x40000);
     const manifestDownload=page.waitForEvent('download');await page.click('#manifest-button');
     const saved=await manifestDownload;const manifest=JSON.parse(fs.readFileSync(await saved.path(),'utf8'));
     assert.equal(manifest.partition,'bdinfo');assert.equal(manifest.start,0x780000);
     assert.equal(manifest.layout,'original-dual');assert.equal(manifest.format,'nand-ecc-data');
     assert.equal(manifest.sha256,require('node:crypto').createHash('sha256').update(Buffer.alloc(0x40000,0x5a)).digest('hex'));
     await page.selectOption('#part-select',String(parts.parts.findIndex(p=>p.name==='0:MIBIB')));
     assert.equal(await page.locator('#manifest-button').isDisabled(),true,'Old manifest must clear after partition change');
     const secondDownload=page.waitForEvent('download');await page.click('#backup-btn');await secondDownload;
     const secondManifest=page.waitForEvent('download');await page.click('#manifest-button');
     const next=JSON.parse(fs.readFileSync(await (await secondManifest).path(),'utf8'));
     assert.equal(next.partition,'0:MIBIB');assert.equal(next.bytes,0x80000);assert.equal(next.start,0x80000);
     assert.equal(await page.locator('#backup-btn').isEnabled(),true);
     assert.match(await page.locator('#backup-btn').innerText(),lang==='en'?/Download selected/:/下载所选/);

    }
    if(file==='recovery') {
     await page.goto(origin+'/recovery.html#layout');await page.waitForTimeout(120);
     assert.equal(await page.locator('#recovery-home').isVisible(),false);
     assert.equal(await page.locator('#recovery-title').innerText(),lang==='en'?'Change flash layout':'更改闪存布局');
     await page.click('#layout-check');
     assert.match(await page.locator('#layout-result').innerText(),/72.00 MiB/);
     if(lang==='en' && process.env.P5_LAYOUT_SCREENSHOT) await page.screenshot({path:process.env.P5_LAYOUT_SCREENSHOT,fullPage:true});
     assert.match(await page.locator('#layout-result').textContent(),/0:ART/);
     assert.equal(await page.locator('#layout-result > div > section').count(),2);
     if(lang==='en') await englishOnly('conversion dry run');
     await page.selectOption('#layout-target','dual');await page.click('#layout-check');
     assert.match(await page.locator('#layout-result').innerText(),lang==='en'?/No layout change/:/无需更改/);
     await page.goto(origin+'/recovery.html#restore');await page.waitForTimeout(120);
     await page.locator('#restore-file').setInputFiles({name:'backup.bin',mimeType:'application/octet-stream',buffer:Buffer.from('test')});
     await page.locator('#restore-manifest').setInputFiles({name:'manifest.json',mimeType:'application/json',buffer:Buffer.from('{broken')});
     await page.click('#restore-check');await page.waitForTimeout(100);
     assert.match(await page.locator('#restore-result').innerText(),lang==='en'?/Invalid JSON/:/JSON 清单无效/);
     await page.goto(origin+'/recovery.html#install');await page.waitForTimeout(120);
     assert.equal(await page.locator('#uboot-test-panel').isVisible(),true);
     assert.equal(await page.locator('#uboot-flash-panel').isVisible(),false);
     await page.locator('#uboot-test-tab').focus();await page.keyboard.press('ArrowRight');
     assert.equal(await page.locator('#uboot-flash-tab').getAttribute('aria-selected'),'true');
     assert.equal(await page.locator('#uboot-flash-panel').isVisible(),true);
     assert.equal(await page.locator('#install-check').count(),0);
     assert.equal(await page.locator('#install-upload-check').innerText(),lang==='en'?'Check image':'检查镜像');
     assert.equal(await page.locator('#flash-ram-boot').innerText(),lang==='en'?'Test from RAM':'从 RAM 测试');
     const installBytes=fs.readFileSync(path.join(__dirname,'../../out-p5-flash/candidate/p5-uboot.mbn'));
     await page.locator('#install-file').setInputFiles({name:'p5.mbn',mimeType:'application/octet-stream',buffer:installBytes});
     await page.click('#install-upload-check');await page.waitForTimeout(500);
     assert.match(await page.locator('#install-result').innerText(),/p5install check/);
     assert.equal(bootRequests.filter(b=>b.startsWith('install,')).length,0,'Preflight must not queue a boot or installation');
     await page.click('#flash-ram-boot');await page.waitForTimeout(500);
     assert.match(await page.locator('#install-result').innerText(),lang==='en'?/Boot request accepted/:/启动请求已接受/);
     await page.goto(origin+'/recovery.html#boot');await page.waitForTimeout(120);
     assert.equal(await page.locator('#boot-name').count(),0);
     assert.equal(await page.locator('#recovery-title').innerText(),lang==='en'?'Boot / Flash OpenWrt':'启动 / 刷写 OpenWrt');
     const uploadBytes=Buffer.from('P5 RAM upload fixture');
     await page.locator('#boot-upload-file').setInputFiles({name:'test.itb',mimeType:'application/octet-stream',buffer:uploadBytes});
     let staleReceipt=true;
     await page.route('**/ram_status',route=>{
      if(staleReceipt){staleReceipt=false;return route.fulfill({json:{valid:true,executed:false,bytes:1,crc32:'00000000'}});}
      return route.continue();
     });
     await page.click('#boot-upload');await page.waitForFunction(()=>!document.getElementById('boot-upload').disabled);
     assert.match(await page.locator('#boot-upload-result').innerText(),lang==='en'?/Boot request accepted/:/启动请求已接受/);
     await page.unroute('**/ram_status');
     badRamReceipt=true;
     await page.click('#boot-upload');
     await page.waitForFunction(()=>!document.getElementById('boot-upload').disabled);
     assert.match(await page.locator('#boot-upload-result').innerText(),lang==='en'?/does not match/:/不匹配/);
     badRamReceipt=false;
     await page.goto(origin+'/recovery.html#install');await page.waitForTimeout(120);
     await page.click('#uboot-test-tab');
     await page.locator('#uboot-ram-file').setInputFiles({name:'p5.elf',mimeType:'application/octet-stream',buffer:uploadBytes});
     await page.click('#uboot-ram-boot');await page.waitForTimeout(200);
     assert.match(await page.locator('#uboot-ram-result').innerText(),lang==='en'?/Boot request accepted/:/启动请求已接受/);
     await page.goto(origin+'/recovery.html');await page.waitForTimeout(120);
     assert.equal(await page.locator('.recovery-actions').count(),0);
     await page.click('#boot-installed');await page.waitForTimeout(120);
     assert.match(await page.locator('#installed-result').innerText(),lang==='en'?/Boot request accepted/:/启动请求已接受/);
    }
    if(file==='term') {
     assert.match(await page.locator('#terminalOutput').innerText(),/中文/);
     assert.equal(await page.locator('#expandBtn').getAttribute('title'),lang==='en'?'Back':'返回');
    }
    if(file==='update') {
     for (const tab of ['uboot','art','cdt','ptable','initramfs']) {
      await page.locator('[data-tab='+tab+']').click();
      if(lang==='en') await englishOnly('update '+tab);
     }
     await page.evaluate(()=>showFail(true));
     if(lang==='en') await englishOnly('validation failure');
     await page.route('**/upgrade_status',route=>route.fulfill({body:'rebooting'}));
     await page.evaluate(()=>{pingDevice=function(){window.rebootBusy=uiBusy;};setUiBusy(true);pollUpgradeStatus();});
     await page.waitForFunction(()=>typeof window.rebootBusy==='boolean');
     assert.equal(await page.evaluate(()=>window.rebootBusy),false,'Completed legacy update must release navigation guard before reconnect');
     await page.unroute('**/upgrade_status');
    }
   }
  }
  await page.goto(origin+'/img.html');
  await page.evaluate(()=>localStorage.setItem('auto_reboot','0'));
  await page.reload();
  assert.match(await page.locator('#img-reboot-tip').innerText(),/settings|按需/);
  // Language switching without navigation actions or persistent device writes.
  await page.goto(origin+'/index.html');
  page.on('dialog',d=>d.accept());
  await page.selectOption('#language-picker','en'); await page.waitForTimeout(200);
  assert.equal(await page.locator('html').getAttribute('lang'),'en');
  assert.equal(await page.frameLocator('#content-frame').locator('html').getAttribute('lang'),'en');
  await page.evaluate(()=>document.getElementById('content-frame').contentWindow.setUiBusy(true));
  assert.equal(await page.locator('#language-picker').isDisabled(),true);
  assert.equal(await page.locator('#rebootBtn').getAttribute('aria-disabled'),'true');
  const busyFrameUrl=page.frames()[1].url(),busyRequests=requests.length;
  await page.locator('a[href="backup.html"]').click({force:true});await page.waitForTimeout(100);
  assert.equal(page.frames()[1].url(),busyFrameUrl,'Busy work must block sidebar navigation');
  await page.locator('#rebootBtn').click({force:true});await page.waitForTimeout(100);
  assert(!requests.slice(busyRequests).some(r=>r==='POST /webterm/cmd'),'Busy work must block reboot');
  assert.equal(await page.frames()[1].evaluate(()=>{const e=new Event('beforeunload',{cancelable:true});dispatchEvent(e);return e.defaultPrevented;}),true,'Busy work must guard browser reload');
  await page.evaluate(()=>document.getElementById('content-frame').contentWindow.setUiBusy(false));
  await page.screenshot({path:'/tmp/p5-ui-english.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'/tmp/p5-ui-mobile.png',fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth),'Mobile shell must fit the viewport');
  // Disabled storage still permits initial rendering and a session language choice.
  const restricted=await browser.newContext({locale:'en-GB'});
  await restricted.addInitScript(()=>Object.defineProperty(window,'localStorage',{get(){throw new Error('storage blocked');}}));
  const blocked=await restricted.newPage();blocked.on('pageerror',e=>errors.push(e.message));
  await blocked.goto(origin+'/index.html');
  assert.equal(await blocked.locator('html').getAttribute('lang'),'en');
  blocked.on('dialog',d=>d.accept());
  await blocked.selectOption('#language-picker','zh');await blocked.waitForTimeout(200);
  assert.equal(await blocked.frameLocator('#content-frame').locator('html').getAttribute('lang'),'zh-CN');
  // Backup manifests require identity and partition metadata from the same session.
  const delayedAbout=await context.newPage();let releaseAbout;
  await delayedAbout.route('**/about',async route=>{await new Promise(resolve=>releaseAbout=resolve);return route.continue();});
  await delayedAbout.goto(origin+'/backup.html');
  await delayedAbout.waitForTimeout(100);
  assert.equal(await delayedAbout.locator('#backup-btn').isDisabled(),true,'Partition response alone must not enable backup');
  releaseAbout();await delayedAbout.waitForFunction(()=>!document.getElementById('backup-btn').disabled);
  await delayedAbout.close();
  const missingAbout=await context.newPage();
  await missingAbout.route('**/about',route=>route.fulfill({status:503,body:'unavailable'}));
  await missingAbout.goto(origin+'/backup.html');
  await missingAbout.locator('#backup-error').waitFor({state:'visible'});
  assert.equal(await missingAbout.locator('#backup-btn').isDisabled(),true);
  assert.equal(await missingAbout.locator('#manifest-button').isDisabled(),true);
  assert.match(await missingAbout.locator('#backup-error').innerText(),/Reload|重新加载/);
  await missingAbout.close();
  const unknownLayout=await context.newPage();
  await unknownLayout.route('**/partitions',route=>route.fulfill({json:{...parts,source:'unknown'}}));
  await unknownLayout.goto(origin+'/backup.html');
  await unknownLayout.waitForFunction(()=>!document.getElementById('backup-btn').disabled);
  assert.equal(await unknownLayout.evaluate(()=>P5Plan.inspect(backupAbout,backupMeta).layout),'unknown','Unknown geometry must still permit read-only backups');
  await unknownLayout.close();
  // Processing exceptions after receipt must release the controls.
  await page.goto(origin+'/backup.html');await page.waitForTimeout(150);
  await page.selectOption('#part-select',String(parts.parts.findIndex(p=>p.name==='bdinfo')));
  await page.evaluate(()=>P5Plan.inspect=function(){throw Error('fixture planner failure');});
  await page.click('#backup-btn');await page.waitForTimeout(300);
  assert.equal(await page.locator('#backup-btn').isEnabled(),true);
  assert.equal(await page.locator('#part-select').isEnabled(),true);
  assert.match(await page.locator('#backup-progress').innerText(),/fixture planner failure/);
  assert.equal(await page.locator('#manifest-button').isDisabled(),true);
  // A failed stylesheet must produce an explicit recovery action.
  const broken=await context.newPage();
  await broken.route('**/style.css*',route=>route.abort());
  await broken.goto(origin+'/recovery.html#layout');
  await broken.locator('#resource-error').waitFor();
  assert.match(await broken.locator('#resource-error').innerText(),/Reload/);
  await broken.close();
  // Leave the polling terminal and load the real tool view repeatedly.
  await page.goto(origin+'/index.html');
  for(let repeat=0;repeat<3;repeat++){
   await page.locator('a[href="term.html"]').first().click();await page.waitForTimeout(120);
   await page.locator('a[href="recovery.html#restore"]').click();await page.waitForTimeout(120);
   const frame=page.frameLocator('#content-frame');
   assert.equal(await frame.locator('#recovery-home').isVisible(),false);
   assert.equal(await frame.locator('#restore').isVisible(),true);
   assert.equal(await frame.locator('body').evaluate(el=>getComputedStyle(el).fontFamily.includes('Arial')),true);
  }
  assert.deepEqual(errors,[]);
  assert(!requests.some(r=>r.startsWith('POST ') && r!=='POST /' && r!=='POST /p5_boot'),'Upload tests must not send any command or flash RPC');
  assert(requests.filter(r=>r==='POST /').length===10,'Five uploads per language should POST');
  assert.equal(bootRequests.filter(b=>b==='installed').length,2);
  assert.equal(bootRequests.filter(b=>b.startsWith('initramfs,')).length,2);
  assert.equal(bootRequests.filter(b=>b.startsWith('uboot,')).length,2);
  // Enabled writes require explicit confirmation; cancellation sends no upload/write.
  about.storage_writes=true;
  const writePage=await browser.newPage({locale:'en-US'});await writePage.addInitScript(()=>localStorage.setItem('uboot.language','en'));await writePage.goto(origin+'/recovery.html#layout');
  await writePage.waitForFunction(()=>!document.getElementById('layout-apply').disabled);
  await writePage.selectOption('#layout-target','dual');await writePage.click('#layout-apply');
  await writePage.waitForFunction(()=>document.getElementById('layout-result').textContent.includes('already matches'));
  assert.equal(await writePage.locator('#layout-apply').isEnabled(),true,'No-op preflight must leave the page usable');
  await writePage.selectOption('#layout-target','single');
  writePage.on('dialog',d=>d.dismiss());const before=requests.length;
  await writePage.click('#layout-apply');await writePage.waitForTimeout(100);
  assert.equal(storageRequests.length,0);assert(!requests.slice(before).some(r=>r.startsWith('POST ')));
  writePage.removeAllListeners('dialog');writePage.on('dialog',d=>d.accept());
  await writePage.click('#layout-apply');await writePage.waitForFunction(()=>document.getElementById('layout-result').textContent.includes('verification complete'));
  assert.match(storageRequests[0],/^layout,single,[a-f0-9]{64},CONVERT-P5-LAYOUT$/);
  assert.equal(await writePage.locator('#layout-apply').isDisabled(),true,'Reload required after metadata changes');
  await writePage.goto(origin+'/recovery.html#restore');await writePage.reload();await writePage.waitForFunction(()=>!document.getElementById('restore-apply').disabled);
  assert.equal(await writePage.locator('#restore-full').isChecked(),false);
  await writePage.click('#restore-apply');
  await writePage.waitForFunction(()=>document.getElementById('restore-result').textContent.includes('Choose a file'));
  assert.equal(await writePage.locator('#restore-apply').isEnabled(),true,'Missing file must leave the page usable');
  const data=Buffer.alloc(0x140000,0x5a),p=parts.parts.find(p=>p.name==='0:APPSBL');
  const restoreManifest={schema:'p5-partition-backup-v1',board:'cudy,p5',machid:about.machid,device_mac:about.mac_addr,layout:'original-dual',partition:p.name,start:p.start,partition_size:p.size,bytes:p.size,format:'nand-ecc-data',sha256:require('node:crypto').createHash('sha256').update(data).digest('hex')};
  await writePage.locator('#restore-file').setInputFiles({name:'backup.bin',mimeType:'application/octet-stream',buffer:data});
  await writePage.locator('#restore-manifest').setInputFiles({name:'manifest.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(restoreManifest))});
  await writePage.click('#restore-apply');await writePage.waitForTimeout(150);
  assert.match(await writePage.locator('#restore-result').innerText(),/U-Boot is excluded/);assert.equal(storageRequests.length,1);
  assert.equal(await writePage.locator('#restore-apply').isEnabled(),true,'Excluded U-Boot preflight must leave the page usable');
  await writePage.goto(origin+'/recovery.html#restore');await writePage.reload();await writePage.waitForFunction(()=>!document.getElementById('restore-apply').disabled);
  const bp=parts.parts.find(p=>p.name==='bdinfo'),bd=Buffer.alloc(bp.size,0x5a),bm={...restoreManifest,partition:bp.name,start:bp.start,partition_size:bp.size,bytes:bp.size,sha256:require('node:crypto').createHash('sha256').update(bd).digest('hex')};
  await writePage.locator('#restore-file').setInputFiles({name:'bdinfo.bin',mimeType:'application/octet-stream',buffer:bd});
  await writePage.locator('#restore-manifest').setInputFiles({name:'bdinfo.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(bm))});
  await writePage.click('#restore-apply');await writePage.waitForFunction(()=>document.getElementById('restore-result').textContent.includes('verification complete'));
  assert.match(storageRequests[1],/^restore-begin,208,[a-f0-9]{8},RESTORE-P5-PROTECTED$/);assert.match(storageRequests[2],/^restore-chunk,0,131072,[a-f0-9]{8}$/);assert.match(storageRequests[3],/^restore-chunk,1,131072,[a-f0-9]{8}$/);assert.equal(storageRequests[4],'restore-finish');
  await writePage.goto(origin+'/recovery.html#install');await writePage.reload();await writePage.waitForFunction(()=>!document.getElementById('install-apply').disabled);
  await writePage.click('#uboot-flash-tab');
  await writePage.locator('#install-file').setInputFiles({name:'p5.mbn',mimeType:'application/octet-stream',buffer:fs.readFileSync(path.join(__dirname,'../../out-p5-flash/candidate/p5-uboot.mbn'))});
  const installRequestsStart=requests.length;let automaticDownloads=0;writePage.on('download',()=>automaticDownloads++);
  await writePage.click('#install-apply');await writePage.waitForFunction(()=>document.getElementById('install-result').textContent.includes('verification complete'));
  assert.match(storageRequests[5],/^install,[0-9]+,[a-f0-9]{8},INSTALL-P5-APPSBL$/);
  assert.equal(automaticDownloads,0,'Install must not download an automatic backup');
  assert(!requests.slice(installRequestsStart).some(r=>r.startsWith('GET /backup')), 'Install must not read/hash the old partition through backup');
  // Once a write request is attempted, a failed/uncertain result keeps the page stopped.
  await writePage.goto(origin+'/recovery.html#layout');await writePage.reload();await writePage.waitForFunction(()=>!document.getElementById('layout-apply').disabled);
  await writePage.route('**/p5_storage_status',route=>route.fulfill({json:{busy:false,sequence,result:-5}}));
  await writePage.click('#layout-apply');
  await writePage.waitForFunction(()=>document.getElementById('layout-result').textContent.includes('Stopped.'));
  assert.equal(await writePage.locator('#layout-apply').isDisabled(),true,'Failed storage request must keep the page stopped');
  await writePage.close();
  // Matching single layout must still reach the backend for BOOTCONFIG-only repair.
  const singleParts={...parts,parts:parts.parts.filter(p=>p.name!=='rootfs'&&p.name!=='rootfs_1').concat({name:'rootfs',start:0x7c0000,size:0x4800000,flash:'nand'})};
  const repairPage=await browser.newPage({locale:'en-US'});
  await repairPage.addInitScript(()=>localStorage.setItem('uboot.language','en'));
  await repairPage.route('**/partitions',route=>route.fulfill({json:singleParts}));
  await repairPage.goto(origin+'/recovery.html#layout');
  await repairPage.waitForFunction(()=>!document.getElementById('layout-apply').disabled);
  const start=storageRequests.length;
  repairPage.once('dialog',d=>d.accept());await repairPage.click('#layout-apply');
  await repairPage.waitForFunction(()=>/already matches|verification complete/.test(document.getElementById('layout-result').textContent));
  assert.equal(storageRequests.length,start+1,'Matching single layout must reach BOOTCONFIG repair');
  assert.match(storageRequests.at(-1),/^layout,single,[a-f0-9]{64},CONVERT-P5-LAYOUT$/);
  assert.equal(await repairPage.locator('#layout-apply').isDisabled(),true,'Actual repair requires reload');
  await repairPage.route('**/p5_storage_status',route=>route.fulfill({json:{busy:false,sequence,result:1}}));
  await repairPage.reload();await repairPage.waitForFunction(()=>!document.getElementById('layout-apply').disabled);
  repairPage.once('dialog',d=>d.accept());await repairPage.click('#layout-apply');
  await repairPage.waitForFunction(()=>document.getElementById('layout-result').textContent.includes('No changes made'));
  assert.equal(await repairPage.locator('#layout-apply').isEnabled(),true,'No-op leaves the page usable');
  await repairPage.close();
  about.storage_writes=false;
  console.log('PASS: eight pages, enabled install/layout/chunked restore, confirmations and U-Boot exclusion, both languages, dynamic tabs, terminal text, MAC guards, picker and transfer guard');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>server.close());
