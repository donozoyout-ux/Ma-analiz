// Local production UI only. All API responses are intercepted test fixtures.
// This runner cannot open a production origin or reach a real backend/R2.
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, join, basename } from 'node:path';
import assert from 'node:assert/strict';
import {clock,uid,routes,fixture} from './fixtures/premium-pages.mjs';
const base = new URL(process.argv[2] || 'http://127.0.0.1:3106');
if (base.protocol !== 'http:' || !['127.0.0.1','localhost'].includes(base.hostname) || base.username || base.password || base.pathname !== '/' || base.search || base.hash) throw Error('LOCAL_ORIGIN_REQUIRED');
const output = resolve(process.argv[3] || 'reports/premium-white-all-pages');
const chrome = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const profile = await mkdtemp(join(tmpdir(),'ma-premium-'));
await mkdir(output,{recursive:true});
const child = spawn(chrome,['--headless=new','--no-first-run','--no-default-browser-check','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{windowsHide:true,stdio:'ignore'});
const sleep = ms => new Promise(resolve=>setTimeout(resolve,ms));
let socket, mode='fixture', apiReads=0;
const held=new Map();
const checks=[], errors=[];
try {
 let port;
 for(let n=0;n<100;n++){try{port=Number((await readFile(join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0]);break;}catch{await sleep(100);}}
 assert.ok(port,'HEADLESS_BROWSER_NOT_READY');
 const tabs=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
 socket=new WebSocket(tabs.find(tab=>tab.type==='page').webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
 let id=0; const pending=new Map();
 const call=(method,params={})=>new Promise((resolve,reject)=>{
  const next=++id;const timer=setTimeout(()=>{pending.delete(next);reject(Error(`BROWSER_TIMEOUT:${method}`));},20000);
  pending.set(next,{resolve,reject,timer}); socket.send(JSON.stringify({id:next,method,params}));
 });
 socket.addEventListener('message',event=>{
  const message=JSON.parse(event.data);
  if(message.id && pending.has(message.id)) {
   const entry=pending.get(message.id); clearTimeout(entry.timer); pending.delete(message.id);
   message.error ? entry.reject(Error(`BROWSER_COMMAND_FAILED:${message.error.message}`)) : entry.resolve(message.result);
  }
  if(message.method==='Runtime.exceptionThrown') errors.push('UNHANDLED_BROWSER_EXCEPTION');
  if(message.method==='Runtime.consoleAPICalled'&&['error','warning'].includes(message.params.type)) errors.push('CONSOLE_WARNING_OR_ERROR');
  if(message.method==='Fetch.requestPaused') void intercept(message.params).catch(error=>errors.push('INTERCEPTION_FAILED:' + error.message));
 });
 async function intercept({requestId,request}) {
  const url=new URL(request.url);
  if(url.origin!==base.origin || request.method!=='GET') {
   errors.push('BLOCKED_NONLOCAL_OR_NON_GET'); await call('Fetch.failRequest',{requestId,errorReason:'BlockedByClient'});return;
  }
  if(url.pathname.startsWith('/api/')) {
   apiReads++;
   if(mode==='loading'){held.set(requestId,request);return;}
   const payload=fixture(url,mode);
   await call('Fetch.fulfillRequest',{requestId,responseCode:payload.status||200,responseHeaders:[{name:'content-type',value:'application/json'},{name:'cache-control',value:'no-store'}],body:Buffer.from(JSON.stringify(payload.body)).toString('base64')});return;
  }
  await call('Fetch.continueRequest',{requestId});
 }
 const evaluate=async expression=>{
  const response=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if(response.exceptionDetails) throw Error(`EVALUATION_FAILED:${expression.slice(0,100)}`);
  return response.result.value;
 };
 const wait=async expression=>{for(let n=0;n<100;n++){if(await evaluate('Boolean('+expression+')'))return;await sleep(100);}throw Error(`EXPECTATION_NOT_MET:${expression}`);};
 const check=async (name,expression)=>{assert.equal(await evaluate(expression),true,name); checks.push(name);};
 const shot=async (name,full=false)=>{
  const params={format:'png',captureBeyondViewport:full};
  if(full) {const {cssContentSize}=await call('Page.getLayoutMetrics');params.clip={x:0,y:0,width:cssContentSize.width,height:cssContentSize.height,scale:1};}
  const image=await call('Page.captureScreenshot',params); await writeFile(join(output,name),Buffer.from(image.data,'base64'));
 };
 const size=async(width,mobile=false)=>{
  await call('Emulation.setDeviceMetricsOverride',{width,height:mobile?844:1100,deviceScaleFactor:1,mobile});
  await call('Emulation.setTouchEmulationEnabled',{enabled:mobile});await sleep(150);
 };
 const navigate=async path=>{
  await call('Page.navigate',{url:new URL(path,base).href});
  await wait(`document.readyState==='complete' && !!document.querySelector('#main-content h1') && document.fonts.status==='loaded'`);
  await sleep(450);
 };
 const key=async(key,code,num)=>{await call('Input.dispatchKeyEvent',{type:'keyDown',key,code,windowsVirtualKeyCode:num});await call('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode:num});};
 await call('Page.enable');await call('Runtime.enable');await call('Network.enable');
 await call('Fetch.enable',{patterns:[{urlPattern:'*'}]});
 await call('Page.addScriptToEvaluateOnNewDocument',{source:`{
  const NativeDate=Date,now=${Date.parse(clock)};
  globalThis.Date=class extends NativeDate {constructor(...args){super(...(args.length?args:[now]));} static now(){return now;}};
  document.addEventListener('DOMContentLoaded',()=>{const b=document.createElement('div');b.textContent='SENTETİK UI TESTİ · PRODUCTION VERİSİ DEĞİL';b.style.cssText='position:fixed;bottom:8px;left:8px;z-index:99;padding:7px;background:#15171c;color:white;border-radius:6px;font:10px sans-serif;pointer-events:none';document.body.append(b);});
 }`});
 await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
 const shots=new Map([['/matches','match-center'],['/matches/'+uid,'match-detail'],['/matches/'+uid+'?source=history','history-detail'],['/predictions','predictions'],['/analysis','performance'],['/archive','archive'],['/management','management'],['/management?tab=settings','settings']]);
 for(const width of [1440,1024,768,390,360]) {
  await size(width,width<=768);
  for(const path of routes) {
   await navigate(path);
   await check(`theme_${width}_${path}`,`getComputedStyle(document.body).backgroundColor==='rgb(247, 248, 250)' && getComputedStyle(document.documentElement).colorScheme==='light' && getComputedStyle(document.querySelector('h1')).color==='rgb(21, 23, 28)'`);
   await check(`layout_${width}_${path}`,`document.documentElement.scrollWidth<=innerWidth+1 && [...document.querySelectorAll('.card,.football-match-card')].every(e=>{const r=e.getBoundingClientRect();return r.left>=-1&&r.right<=innerWidth+1&&getComputedStyle(e).backgroundColor==='rgb(255, 255, 255)'})`);
   await check(`active_${width}_${path}`,`[...document.querySelectorAll('.nav-link.active,.section-tab.active')].every(e=>getComputedStyle(e).color==='rgb(215, 25, 50)')`);
   if([1440,390].includes(width)&&shots.has(path))await shot(`${shots.get(path)}-${width}.png`,true);
  }
  console.log(`VIEWPORT_${width}_ALL_${routes.length}_ROUTES_PASS`);
 }
 await size(1440);mode='fixture';await navigate('/matches');
 await wait(`!!document.querySelector('.source-fixtures-table tbody tr')`);
 await check('source_bulletin_fixture',`document.querySelector('.source-fixtures-table').textContent.includes('SENTETİK Ev Takımı')`);
 await navigate('/archive'); await wait(`!!document.querySelector('.football-match-card')`);
 const moveTo=async selector=>{
  await evaluate(`document.querySelector('${selector}').scrollIntoView({block:'center'})`);await sleep(100);
  const point=await evaluate(`(()=>{const r=document.querySelector('${selector}').getBoundingClientRect();return {x:r.x+r.width*.8,y:r.y+r.height*.3}})()`);
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});await sleep(350);
 };
 await moveTo('.football-match-card');
 await check('match_card_tilt_bounds_reflection_shadow',`(()=>{const e=document.querySelector('.football-match-card');return e.hasAttribute('data-tilt-active')&&Math.abs(parseFloat(e.style.getPropertyValue('--tilt-x')))<=2&&Math.abs(parseFloat(e.style.getPropertyValue('--tilt-y')))<=2&&getComputedStyle(e).transform.startsWith('matrix3d')&&getComputedStyle(e,'::after').opacity==='1'})()`);
 await shot('match-card-tilt-1440.png',true);
 await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:1400,y:10});await sleep(350);
 await check('tilt_resets_on_exit',`!document.querySelector('[data-tilt-active]')`);
 await evaluate(`document.querySelector('.nav-link[href="/analysis"]').click()`);await wait(`location.pathname==='/analysis'&&!!document.querySelector('.performance-kpis')`);await sleep(500);
 await moveTo('.performance-kpis .card');await check('performance_card_uses_same_tilt',`!!document.querySelector('.performance-kpis [data-tilt-active]')`);
 await evaluate(`document.querySelector('.nav-link[href="/management"]').click()`);await wait(`location.pathname==='/management'&&document.body.innerText.includes('Sistem durumu')`);await sleep(500);
 await check('client_navigation_theme_and_cleanup',`getComputedStyle(document.body).backgroundColor==='rgb(247, 248, 250)'&&!document.querySelector('[data-tilt-active]')`);
 await moveTo('.card');await check('management_card_tilt',`!!document.querySelector('.card[data-tilt-active]')`);
 await evaluate(`document.querySelector('a[href="/management?tab=settings"]').click()`);await wait(`location.search==='?tab=settings'`);await sleep(300);
 await check('same_path_tab_cleanup',`!document.querySelector('[data-tilt-active]')`);
 await navigate('/matches/'+uid+'?source=history');
 await check('source_score_attribution_and_no_invented_analysis',`document.querySelector('.center-score strong').textContent==='1 – 1'&&document.body.innerText.includes('SENTETİK UI TESTİ · OpenLigaDB')&&document.body.innerText.includes('ODbL-1.0')&&document.body.innerText.includes('Analiz henüz hazırlanmadı')&&document.body.innerText.includes('İstatistik verisi mevcut değil')`);
 await moveTo('.center-provenance');await check('provenance_card_tilt',`!!document.querySelector('.center-provenance[data-tilt-active]')`);
 await navigate('/archive');await evaluate(`document.querySelector('.football-match-card a').click()`);await wait(`location.pathname==='/matches/${uid}'&&location.search==='?source=history'`);passCheck('history_link_keeps_source');
 function passCheck(name){checks.push(name);}
 await navigate('/archive');await evaluate(`document.querySelector('[aria-label="Tablo görünümü"]').click()`);await wait(`!!document.querySelector('.records-table')`);
 await evaluate(`document.querySelector('.records-scroll').focus()`);await key('Tab','Tab',9);
 await check('keyboard_table_links_visible_focus',`document.activeElement.matches(':focus-visible')&&getComputedStyle(document.activeElement).outlineWidth==='3px'`);
 await check('scroll_regions_keyboard_accessible',`document.querySelector('.records-scroll').tabIndex===0`);
 await moveTo('.records-scroll');await check('tables_do_not_tilt',`!document.querySelector('[data-tilt-active]')`);
 await evaluate(`document.querySelector('input[placeholder="Takım ara…"]').focus()`);await call('Input.insertText',{text:'NoSuchSyntheticTeam'});await evaluate(`document.querySelector('.center-submit').click()`);await wait(`document.body.innerText.includes('Bu kapsamda maç bulunamadı')`);passCheck('real_filter_control_empty_result');
 const dataRoutes=['/matches','/archive','/predictions','/predictions?tab=coupons','/analysis','/analysis?tab=lessons'];
 for(const state of ['empty','error','loading']) {
  mode=state;
  for(const path of dataRoutes) {
   await navigate(path);
   const expected = path==='/matches' ? (state==='loading'?'Güncel fikstür okunuyor':state==='error'?'Veri kaynağına bağlanılamadı':'Seçilen filtrelerde maç bulunamadı') : null;
   await check(`state_${state}_${path}`, expected ? `document.body.innerText.includes(${JSON.stringify(expected)})` : `document.body.innerText.toLocaleLowerCase('tr-TR').includes('${state==='loading'?'yükleniyor':state==='error'?'ulaşılamadı':'yok'}')||document.body.innerText.includes('Bu kapsamda maç bulunamadı')`);
   await check(`state_surface_${state}_${path}`,`[...document.querySelectorAll('.card')].every(e=>getComputedStyle(e).backgroundColor==='rgb(255, 255, 255)')`);
   if(state==='loading'){mode='empty';for(const [requestId,request]of held){held.delete(requestId);await intercept({requestId,request});}mode='loading';}
  }
 }
 mode='error';await navigate('/matches/'+uid);await wait(`document.body.innerText.includes('Maç detayı okunamadı')`);await check('detail_error_no_fabricated_score',`!document.querySelector('.center-score')`);
 mode='empty';await navigate('/');await check('empty_home_no_fabricated_rate',`document.querySelectorAll('.football-kpi-value')[2].textContent==='—'&&document.body.innerText.includes('Doğrulanmış sonuç bekleniyor')`);
 mode='fixture';await navigate('/matches/'+uid);await moveTo('.match-hero');
 await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});await sleep(150);
 await check('reduced_motion_everywhere',`!document.querySelector('[data-tilt-active]')&&getComputedStyle(document.querySelector('.match-hero')).transform==='none'&&document.getAnimations().filter(a=>a.playState==='running').length===0`);await shot('reduced-motion-detail.png',true);
 await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
 for(const width of [768,390,360]) {
  await size(width,true);await moveTo('.match-hero');
  await check(`touch_${width}_no_tilt`,`!document.querySelector('[data-tilt-active]')&&getComputedStyle(document.querySelector('.match-hero')).transform==='none'`);
 }
 await evaluate(`document.querySelector('.mobile-menu-toggle').focus()`);await key(' ','Space',32);await wait(`document.querySelector('.mobile-menu-toggle').getAttribute('aria-expanded')==='true'`);await shot('mobile-menu-360.png');await key('Escape','Escape',27);
 await check('menu_keyboard_escape_focus',`document.querySelector('.mobile-menu-toggle').getAttribute('aria-expanded')==='false'&&document.activeElement===document.querySelector('.mobile-menu-toggle')&&document.activeElement.matches(':focus-visible')`);
 assert.deepEqual(errors,[]);passCheck('no_console_hydration_or_unsafe_requests');
 const report={evidence:'LOCAL_FRONTEND_SYNTHETIC_API_INTERCEPTION_ONLY',source:'premium-pages.mjs TEST FIXTURES',frozen_clock:clock,route_count:routes.length,viewports:[1440,1024,768,390,360],checks,passed:checks.length,api_fixture_reads:apiReads,production_requests:0,SQL_WRITE:'NOT_RUN',R2_WRITE:'NOT_RUN',LIVE_DELETE:'NOT_RUN',SAFE_TO_DELETE:'NO',AUTO_RETENTION_DELETE_ENABLED:false,OPERATIONAL_DELETE_ENABLED:false};
 await writeFile(join(output,'browser-report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({passed:report.passed,route_count:report.route_count,production_requests:0}));
} finally {
 socket?.close();child.kill();await sleep(1000);
 const target=resolve(profile),root=resolve(tmpdir());
 if((target.startsWith(root+'\\')||target.startsWith(root+'/'))&&basename(target).startsWith('ma-premium-'))await rm(target,{recursive:true,force:true,maxRetries:3,retryDelay:200}).catch(()=>{});
}
