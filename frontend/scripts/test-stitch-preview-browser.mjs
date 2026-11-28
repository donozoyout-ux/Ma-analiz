// Local production UI only. All API responses are intercepted test fixtures.
// This runner cannot open a production origin or reach a real backend/R2.
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, join, basename } from 'node:path';
import assert from 'node:assert/strict';
import {fixture,clock,uid} from './fixtures/premium-pages.mjs';
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
   const payload=mode==='invalid' && url.pathname==='/api/v1/bundesliga/team-performance' ? {body:{team:'SENTETIK',played:1,form:[],warnings:[],wins:-1}} : fixture(url,mode);
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

 await call('Page.enable'); await call('Runtime.enable'); await call('Network.enable');
 await call('Fetch.enable',{patterns:[{urlPattern:'*'}]});
 await call('Page.addScriptToEvaluateOnNewDocument',{source:`{const D=Date,t=${Date.parse(clock)};globalThis.Date=class extends D{constructor(...args){super(...(args.length?args:[t]));}static now(){return t}}}`});
 mode='fixture';
 for (const width of [360,390,1280,1440]) {
  await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<700});
  await call('Emulation.setTouchEmulationEnabled',{enabled:width<700});
  await call('Page.navigate',{url:new URL('/stitch-preview',base).href});
  await wait(`document.readyState==='complete' && !!document.querySelector('.stitch-stage')`);
  for (let i=0;i<6;i++) {
   const selector=width<700?(i<4?`.stitch-preview-bottom-nav button:nth-child(${i+1})`:`.stitch-preview-context button:nth-child(${i-3})`):`.stitch-preview-nav button:nth-child(${i+1})`;
   await evaluate(`document.querySelector('${selector}').click()`);
   await wait(`document.activeElement.id==='stitch-content'`); await sleep(250);
   await check(`no_overflow_${width}_${i}`,`document.documentElement.scrollWidth<=innerWidth`);
   await check(`one_main_${width}_${i}`,`document.querySelectorAll('main').length===1`);
   if(width===390||width===1440) await shot(`stitch-${width}-${i}.png`,true);
   if(i===4) await check(`archive_locked_${width}`,`!document.querySelector('select option[value="all"]') || !document.body.innerText.includes('Kayıt kapsamı')`);
  }
  if(width<700) await check(`touch_${width}`,`document.querySelectorAll('.stitch-preview-bottom-nav button').length===4 && [...document.querySelectorAll('.stitch-preview-bottom-nav button')].every(e=>e.getBoundingClientRect().width>=44 && e.getBoundingClientRect().height>=44)`);
 }
 await evaluate(`document.querySelector('.stitch-preview-nav button:nth-child(5)').click()`);
 await wait(`!!document.querySelector('.football-match-card a')`);
 await evaluate(`document.querySelector('.football-match-card a').click()`);
 await wait(`!!document.querySelector('.center-score strong')`);
 await check('detail_real_id_history',`document.body.innerText.includes('SENTETİK Ev Takımı') && document.body.innerText.includes('Doğrulanmış R2 arşivi') && document.body.innerText.includes('Doğrulanmış oran verisi bulunmuyor.') && location.pathname==='/stitch-preview'`);
 await evaluate(`document.querySelector('.stitch-preview-nav button:nth-child(1)').focus()`);
 await call('Input.dispatchKeyEvent',{type:'keyDown',key:' ',code:'Space',windowsVirtualKeyCode:32});
 await call('Input.dispatchKeyEvent',{type:'keyUp',key:' ',code:'Space',windowsVirtualKeyCode:32});
 await wait(`document.activeElement.id==='stitch-content' && document.querySelector('.stitch-preview-nav button:first-child').getAttribute('aria-current')==='page'`);checks.push('keyboard_navigation_focus');
 mode='invalid'; await call('Page.navigate',{url:new URL('/stitch-preview',base).href});
 await wait(`!!document.querySelector('.stitch-preview-nav')`); await evaluate(`document.querySelector('.stitch-preview-nav button:nth-child(4)').click()`);
 await wait(`document.body.innerText.includes('Takım verileri alınamadı')`); checks.push('invalid_stats_rejected');
 mode='error'; await call('Page.navigate',{url:new URL('/stitch-preview',base).href});
 await wait(`document.body.innerText.includes('Veri kaynağına bağlanılamadı')`);
 await check('error_no_fabricated_data',`!document.querySelector('.football-match-card')`);
 mode='empty';await call('Page.navigate',{url:new URL('/stitch-preview',base).href});
 await wait(`document.body.innerText.includes('Seçilen filtrelerde maç bulunamadı')`);
 await check('empty_no_fabricated_data',`!document.querySelector('.football-match-card')`);
 await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
 await check('reduced_motion',`matchMedia('(prefers-reduced-motion: reduce)').matches`);
 assert.equal(errors.length,0,errors.join(','));
 console.log(`STITCH_BROWSER_PASS ${checks.length} checks; 12 screenshots`);
} finally { if(socket) socket.close(); child.kill(); await rm(profile,{recursive:true,force:true,maxRetries:5,retryDelay:200}).catch(()=>{}); }
