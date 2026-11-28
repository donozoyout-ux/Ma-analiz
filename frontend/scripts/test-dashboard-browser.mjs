// Local production UI only. All API responses are intercepted test fixtures.
// This runner cannot open a production origin or reach a real backend/R2.
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, join, basename } from 'node:path';
import assert from 'node:assert/strict';
import {fixture, clock} from './fixtures/premium-pages.mjs';
const base = new URL(process.argv[2] || 'http://127.0.0.1:3106');
if (base.protocol !== 'http:' || !['127.0.0.1','localhost'].includes(base.hostname) || base.username || base.password || base.pathname !== '/' || base.search || base.hash) throw Error('LOCAL_ORIGIN_REQUIRED');
const output = resolve(process.argv[3] || 'reports/premium-white-3d');
const chrome = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const profile = await mkdtemp(join(tmpdir(),'ma-premium-'));
await mkdir(output,{recursive:true});
const child = spawn(chrome,['--headless=new','--no-first-run','--no-default-browser-check','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{windowsHide:true,stdio:'ignore'});
const sleep = ms => new Promise(resolve=>setTimeout(resolve,ms));
let socket, mode='empty', apiReads=0;
const checks=[], errors=[];
const day = new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Istanbul'}).format(new Date());
// Deliberately unmistakable synthetic identities. Never inserted into the app or storage.
const matches = [
 {id:'11111111-1111-4111-8111-111111111111',home_team:'Test Ev Takımı',away_team:'Test Konuk Takımı',league_name:'Sentetik Test Ligi',start_time:`${day}T10:00:00Z`,status:'FINISHED',analysis_summary:null,result:{home_score:0,away_score:0,status:'FINAL',verified_at:`${day}T13:00:00Z`}},
 {id:'22222222-2222-4222-8222-222222222222',home_team:'Uzun İsimli Test Takımı',away_team:'Diğer Test Takımı',league_name:'İkinci Test Ligi',start_time:`${day}T18:00:00Z`,status:'SCHEDULED',analysis_summary:null,result:null},
];
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
  if(message.method==='Fetch.requestPaused') void intercept(message.params).catch(()=>errors.push('INTERCEPTION_FAILED'));
 });
 async function intercept({requestId,request}) {
  const url=new URL(request.url);
  if(url.origin!==base.origin || request.method!=='GET') {
   errors.push('BLOCKED_NONLOCAL_OR_NON_GET'); await call('Fetch.failRequest',{requestId,errorReason:'BlockedByClient'});return;
  }
  if(url.pathname.startsWith('/api/')) {
   apiReads++;
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
 const wait=async expression=>{for(let n=0;n<100;n++){if(await evaluate(expression))return;await sleep(100);}throw Error(`EXPECTATION_NOT_MET:${expression}`);};
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
  await wait(`document.readyState==='complete' && !!document.querySelector('.premium-dashboard') && document.fonts.status==='loaded'`);
  await wait(`!document.querySelector('.center-loading')`);await sleep(600);
 };
 const key=async(key,code,num)=>{await call('Input.dispatchKeyEvent',{type:'keyDown',key,code,windowsVirtualKeyCode:num});await call('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode:num});};
 await call('Page.enable');await call('Runtime.enable');await call('Network.enable');
 await call('Fetch.enable',{patterns:[{urlPattern:'*'}]});
 await call('Page.addScriptToEvaluateOnNewDocument',{source:`{const D=Date,t=${Date.parse(clock)};globalThis.Date=class extends D{constructor(...args){super(...(args.length?args:[t]));}static now(){return t}}}`});
 await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
 await size(1440); mode='empty'; await navigate('/');
 await wait(`document.body.innerText.includes('Seçilen filtrelerde maç bulunamadı')`);
 await check('empty_no_fake_results',`document.body.innerText.includes('Doğrulanmış sonuç bekleniyor') && !document.querySelector('.source-fixtures-table tbody tr')`);
 mode='error';await navigate('/');
 await wait(`document.body.innerText.includes('Veri kaynağına bağlanılamadı')`);
 await check('error_no_fake_results',`document.body.innerText.includes('Tahmin verisine ulaşılamadı') && !document.querySelector('.source-fixtures-table tbody tr')`);
 mode='fixture';await navigate('/');
 await wait(`!!document.querySelector('.source-fixtures-table tbody tr')`);
 await check('fixture_source_table',`document.querySelector('.source-fixtures-table').textContent.includes('SENTETİK Ev Takımı') && document.querySelector('.source-fixtures-table').textContent.includes('—')`);
 await evaluate(`document.querySelector('input[placeholder="Takım adı"]').focus()`); await call('Input.insertText',{text:'NoSuchSyntheticTeam'});
 await wait(`document.body.innerText.includes('Seçilen filtrelerde maç bulunamadı')`);
 await check('team_filter_empty',`!document.querySelector('.source-fixtures-table tbody tr')`);
 await evaluate(`Array.from(document.querySelectorAll('button')).find(e=>e.textContent.includes('Filtreleri sıfırla')).click()`);
 await wait(`!!document.querySelector('.source-fixtures-table tbody tr')`);


 await evaluate(`(()=>{const e=document.querySelector('[aria-label="Maç tarihi filtresi"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'2026-11-26');e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))})()`);
 await wait(`document.body.innerText.includes('Seçilen filtrelerde maç bulunamadı')`); checks.push('date_filter');
 await evaluate(`Array.from(document.querySelectorAll('button')).find(e=>e.textContent.includes('Filtreleri sıfırla')).click()`); await wait(`!!document.querySelector('.source-fixtures-table tbody tr')`);
 await check('source_scroll_accessible',`document.querySelector('.source-fixtures-scroll').tabIndex===0 && document.querySelector('.source-fixtures-scroll').getAttribute('aria-label')==='Kaynak futbol maçları'`);
 for(const variable of ['--font-inter','--font-space','--font-bebas']) await check(`local_font_${variable}`,`document.fonts.load('16px '+getComputedStyle(document.documentElement).getPropertyValue('${variable}').trim(),'İstanbul').then(faces=>faces.length>0)`);
 await check('production_theme',`getComputedStyle(document.body).backgroundColor==='rgb(247, 248, 250)'`);
 await evaluate(`(()=>{const e=document.querySelector('[aria-label="Kaynak lig filtresi"]');e.value='uefa.cl';e.dispatchEvent(new Event('change',{bubbles:true}))})()`);
 await wait(`document.body.innerText.includes('Seçilen filtrelerde maç bulunamadı')`); checks.push('league_filter');
 await evaluate(`Array.from(document.querySelectorAll('button')).find(e=>e.textContent.includes('Filtreleri sıfırla')).click()`);
 await wait(`!!document.querySelector('.source-fixtures-table tbody tr')`);
 await evaluate(`(()=>{const e=document.querySelector('[aria-label="Maç durumu filtresi"]');e.value='finished';e.dispatchEvent(new Event('change',{bubbles:true}))})()`);
 await wait(`document.body.innerText.includes('Seçilen filtrelerde maç bulunamadı')`); checks.push('status_filter');
 await evaluate(`Array.from(document.querySelectorAll('button')).find(e=>e.textContent.includes('Filtreleri sıfırla')).click()`);
 await wait(`!!document.querySelector('.source-fixtures-table tbody tr')`);
 await evaluate(`document.querySelector('.football-kpi').scrollIntoView({block:'center'})`); await sleep(800);
 const point=await evaluate(`(()=>{const r=document.querySelector('.football-kpi').getBoundingClientRect();return {x:r.x+r.width*.8,y:r.y+r.height*.3}})()`);
 await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:1,y:1});await sleep(100);
 await call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});await sleep(350);
 await check('desktop_kpi_tilt',`document.querySelector('.football-kpi').hasAttribute('data-tilt-active')`);
 await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:1,y:1});await sleep(350);
 await check('tilt_reset',`!document.querySelector('[data-tilt-active]')`);
 await evaluate(`document.querySelector('.home-refresh').focus()`);
 await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
 await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
 await check('keyboard_focus',`document.activeElement.matches(':focus-visible')`);
 for(const width of [1440,1024,390,360]) {
  await size(width,width<700);
  await check(`overflow_${width}`,`document.documentElement.scrollWidth<=innerWidth`);
  if(width<700) await check(`touch_no_tilt_${width}`,`!document.querySelector('[data-tilt-active]')`);
 }

 await size(360,true);
 await evaluate(`document.querySelector('.mobile-menu-toggle').focus()`);
 await call('Input.dispatchKeyEvent',{type:'keyDown',key:' ',code:'Space',windowsVirtualKeyCode:32});await call('Input.dispatchKeyEvent',{type:'keyUp',key:' ',code:'Space',windowsVirtualKeyCode:32});
 await wait(`document.querySelector('.mobile-menu-toggle').getAttribute('aria-expanded')==='true'`); checks.push('mobile_keyboard_open');
 await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
 await check('mobile_escape_focus',`document.querySelector('.mobile-menu-toggle').getAttribute('aria-expanded')==='false' && document.activeElement===document.querySelector('.mobile-menu-toggle')`);
 await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
 await check('reduced_motion',`matchMedia('(prefers-reduced-motion: reduce)').matches`);
 assert.deepEqual(errors,[]); console.log(`DASHBOARD_BROWSER_PASS ${checks.length} checks`);
} finally {
 socket?.close();child.kill();await sleep(1000);
 const target=resolve(profile),root=resolve(tmpdir());
 if(target.startsWith(root+'\\') || target.startsWith(root+'/')) {
  if(basename(target).startsWith('ma-premium-')) await rm(target,{recursive:true,force:true,maxRetries:3,retryDelay:200}).catch(()=>{});
 }
}
