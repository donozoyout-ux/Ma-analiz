// Local production UI only. All API responses are intercepted test fixtures.
// This runner cannot open a production origin or reach a real backend/R2.
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, join, basename } from 'node:path';
import assert from 'node:assert/strict';
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
   const isMatches=url.pathname==='/api/v1/matches';
   const rows=mode==='fixture' && isMatches ? matches : [];
   await call('Fetch.fulfillRequest',{requestId,responseCode:mode==='error'?503:200,responseHeaders:[{name:'content-type',value:'application/json'},{name:'cache-control',value:'no-store'}],body:Buffer.from(JSON.stringify(mode==='error'?{detail:'TEST_UNAVAILABLE'}:rows)).toString('base64')});return;
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
 await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
 await size(1440);await navigate('/');
 await check('white_home_and_red_active_nav',`getComputedStyle(document.body).backgroundColor==='rgb(247, 248, 250)' && getComputedStyle(document.querySelector('.nav-link.active')).color==='rgb(215, 25, 50)'`);
 await check('empty_has_no_fabricated_match_or_rate',`document.body.innerText.includes('Bu görünümde maç bulunamadı') && document.body.innerText.includes('Doğrulanmış sonuç bekleniyor') && document.querySelectorAll('.football-match-card').length===0 && document.querySelectorAll('.football-kpi-value')[2].textContent==='—'`);
 await check('home_sidebar_indicator',`getComputedStyle(document.querySelector('.nav-motion-indicator')).opacity==='1' && Math.abs(document.querySelector('.nav-motion-indicator').getBoundingClientRect().top-document.querySelector('.nav-link.active').getBoundingClientRect().top-8)<2`);
 for(const variable of ['--font-inter','--font-space','--font-bebas']) await check(`local_font_${variable}`,`document.fonts.load('16px '+getComputedStyle(document.documentElement).getPropertyValue('${variable}').trim(),'İstanbul ŞĞÜÖ').then(faces=>faces.length>0)`);
 for(const width of [1440,1024,390,360]) {
  await size(width,width<700);
  await check(`no_overflow_${width}`,`document.documentElement.scrollWidth<=innerWidth && [...document.querySelectorAll('.football-kpi,.filter-panel,.match-list-panel,.insight-card')].every(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth})`);
  if(width===1440) await shot('desktop-1440-empty.png',true);
  if(width===1024) await shot('tablet-1024-empty.png',true);
  if(width===390) {await shot('mobile-390-empty.png');await shot('mobile-390-full.png',true);}
  if(width===360) await shot('mobile-360-empty.png');
 }
 await evaluate(`document.querySelector('.mobile-menu-toggle').focus()`);await key(' ','Space',32);
 await wait(`document.querySelector('.mobile-menu-toggle').getAttribute('aria-expanded')==='true'`);
 await check('mobile_menu_no_overflow',`document.documentElement.scrollWidth<=innerWidth && [...document.querySelectorAll('.nav-link')].filter(e=>e.getClientRects().length).length===6`);
 await shot('mobile-menu.png');await key('Escape','Escape',27);
 await check('escape_returns_focus',`document.querySelector('.mobile-menu-toggle').getAttribute('aria-expanded')==='false' && document.activeElement===document.querySelector('.mobile-menu-toggle')`);
 await check('keyboard_visible_focus',`document.activeElement.matches(':focus-visible') && getComputedStyle(document.activeElement).outlineWidth==='3px'`);
 await shot('keyboard-focus-mobile.png');
 mode='error';await navigate('/');
 await check('api_failure_and_no_fake_content',`document.body.innerText.includes('Maç verisine ulaşılamadı') && document.body.innerText.includes('Tahmin verisine ulaşılamadı') && document.querySelectorAll('.football-match-card').length===0`);
 mode='fixture';await evaluate(`document.querySelector('.home-refresh').click()`);
 await wait(`document.querySelectorAll('.football-match-card').length===2`);
 await evaluate(`const b=document.createElement('div');b.id='fixture-label';b.textContent='İZOLE UI TESTİ · SENTETİK VERİ';b.style.cssText='position:fixed;bottom:8px;left:8px;z-index:99;background:#15171c;color:white;padding:8px;font:11px sans-serif;border-radius:6px;pointer-events:none';document.body.append(b)`);
 await size(1440);await sleep(400);
 await check('fixture_zero_score_and_pending_score',`document.querySelectorAll('.football-score strong')[0].textContent==='0 – 0' && document.querySelectorAll('.football-score strong')[1].textContent==='— : —'`);
 const position=await evaluate(`(()=>{const e=document.querySelector('.football-kpi');const r=e.getBoundingClientRect();return {x:r.left+r.width*.85,y:r.top+r.height*.2}})()`);
 await call('Input.dispatchMouseEvent',{type:'mouseMoved',...position});await sleep(450);
 await check('desktop_mouse_3d_tilt_and_shadow',`document.querySelector('.football-kpi').hasAttribute('data-tilt-active') && getComputedStyle(document.querySelector('.football-kpi')).transform.startsWith('matrix3d') && getComputedStyle(document.querySelector('.football-kpi')).boxShadow!=='none'`);
 await shot('desktop-1440-fixture-tilt.png',true);
 await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:1300,y:20});await sleep(350);
 await check('pointer_exit_resets',`!document.querySelector('[data-tilt-active]') && getComputedStyle(document.querySelector('.football-kpi')).transform==='none'`);
 const buttonPosition=await evaluate(`(()=>{const r=document.querySelector('.home-refresh').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
 await call('Input.dispatchMouseEvent',{type:'mouseMoved',...buttonPosition});await sleep(220);
 await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...buttonPosition});await sleep(220);
 await check('button_press_micro_interaction',`getComputedStyle(document.querySelector('.home-refresh')).transform.includes('0.97')`);
 await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...buttonPosition});
 await wait(`document.querySelectorAll('.football-match-card').length===2`);
 for(const [label,selector] of [['match_card','.football-match-card'],['insight_card','.insight-card']]) {
  await evaluate(`document.querySelector('${selector}').scrollIntoView({block:'center'})`);await sleep(100);
  const p=await evaluate(`(()=>{const r=document.querySelector('${selector}').getBoundingClientRect();return {x:r.x+r.width*.8,y:r.y+r.height*.3}})()`);
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',...p});await sleep(350);
  await check(`${label}_3d_tilt`,`document.querySelector('${selector}').hasAttribute('data-tilt-active') && getComputedStyle(document.querySelector('${selector}')).transform.startsWith('matrix3d')`);
 }
 await evaluate(`document.querySelector('.football-kpi').scrollIntoView()`);
 await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:900,y:1000});
 await evaluate(`document.querySelector('.home-refresh').focus()`);await key('Tab','Tab',9);
 await check('desktop_tab_focus',`document.activeElement.matches(':focus-visible') && getComputedStyle(document.activeElement).outlineStyle==='solid'`);
 // Filter through real React events (not replacing a component's state).
 await evaluate(`(()=>{const e=document.querySelector('select');e.value='İkinci Test Ligi';e.dispatchEvent(new Event('change',{bubbles:true}))})()`);
 await wait(`document.querySelectorAll('.football-match-card').length===1`);
 await check('league_filter',`document.querySelector('.football-match-card').textContent.includes('Uzun İsimli Test Takımı')`);
 await evaluate(`Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='Sıfırla').click()`);
 await wait(`document.querySelectorAll('.football-match-card').length===2`);
 await evaluate(`document.querySelector('.search-field input').focus()`);
 await call('Input.insertText',{text:'Uzun İsimli'});
 await wait(`document.querySelectorAll('.football-match-card').length===1`);
 await check('team_search',`document.querySelector('.football-match-card').textContent.includes('Uzun İsimli Test Takımı')`);
 await evaluate(`Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='Sıfırla').click()`);
 await wait(`document.querySelectorAll('.football-match-card').length===2`);
 await evaluate(`(()=>{const e=document.querySelector('input[type="date"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'2000-01-01');e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))})()`);
 await wait(`document.querySelectorAll('.football-match-card').length===0`);
 await check('date_filter_no_match',`document.body.innerText.includes('Bu görünümde maç bulunamadı') && document.querySelector('input[type="date"]').value==='2000-01-01'`);
 await evaluate(`Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='Sıfırla').click()`);
 await wait(`document.querySelectorAll('.football-match-card').length===2`);
 await evaluate(`(()=>{const e=document.querySelectorAll('select')[1];e.value='FINISHED';e.dispatchEvent(new Event('change',{bubbles:true}))})()`);
 await wait(`document.querySelectorAll('.football-match-card').length===1`);
 await check('status_filter',`document.querySelector('.football-match-card').textContent.includes('Test Ev Takımı')`);
 await evaluate(`Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='Sıfırla').click()`);
 await wait(`document.querySelectorAll('.football-match-card').length===2`);
 await evaluate(`document.querySelector('[aria-label="Tablo görünümü"]').click()`);await wait(`!!document.querySelector('tbody tr')`);
 await check('table_toggle',`document.querySelectorAll('tbody tr').length===2`);
 await evaluate(`document.querySelector('[aria-label="Kart görünümü"]').click()`);await wait(`document.querySelectorAll('.football-match-card').length===2`);
 await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
 await evaluate(`window.scrollTo(0,0)`);await sleep(100);
 const reducedPosition=await evaluate(`(()=>{const r=document.querySelector('.football-kpi').getBoundingClientRect();return {x:r.x+30,y:r.y+30}})()`);
 await call('Input.dispatchMouseEvent',{type:'mouseMoved',...reducedPosition});await sleep(100);
 await check('reduced_motion_no_tilt_or_animations',`!document.querySelector('[data-tilt-active]') && getComputedStyle(document.querySelector('.football-kpi')).transform==='none' && document.getAnimations().filter(a=>a.playState==='running').length===0 && getComputedStyle(document.querySelector('.nav-motion-indicator')).transitionDuration==='0s'`);
 await shot('reduced-motion.png');
 await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
 for(const width of [390,360]) {
  await size(width,true);
  await evaluate(`document.querySelector('.football-match-card').scrollIntoView({block:'center'})`);await sleep(350);
  await check(`fixture_mobile_${width}_no_overflow`, `document.documentElement.scrollWidth<=innerWidth`);
  const touchPosition=await evaluate(`(()=>{const r=document.querySelector('.football-match-card').getBoundingClientRect();return {x:r.x+30,y:r.y+30}})()`);
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',...touchPosition});await sleep(350);
  await check(`touch_${width}_no_tilt`, `!document.querySelector('[data-tilt-active]') && getComputedStyle(document.querySelector('.football-match-card')).transform==='none'`);
  await shot(`mobile-${width}-fixture-card.png`);
 }
 // Client navigation must remove the route-specific white theme and its effect.
 await size(1440);await evaluate(`document.querySelector('.nav-link[href="/matches"]').click()`);
 await wait(`location.pathname==='/matches' && !document.querySelector('.premium-dashboard')`);await sleep(350);
 await check('other_page_keeps_original_theme',`getComputedStyle(document.body).backgroundColor==='rgb(8, 13, 23)' && !document.querySelector('[data-tilt-active]')`);
 await check('sidebar_indicator_tracks_active_route',`Math.abs(document.querySelector('.nav-motion-indicator').getBoundingClientRect().top-document.querySelector('.nav-link.active').getBoundingClientRect().top-8)<2`);
 assert.deepEqual(errors,[]); checks.push('no_unhandled_or_external_requests');
 const report={evidence:'LOCAL_PRODUCTION_BUILD_EMPTY_AND_SYNTHETIC_FIXTURES',checks,passed:checks.length,api_fixture_reads:apiReads,production_requests:0,SQL_WRITE:'NOT_RUN',R2_WRITE:'NOT_RUN',LIVE_DELETE:'NOT_RUN',SAFE_TO_DELETE:'NO',AUTO_RETENTION_DELETE_ENABLED:false,OPERATIONAL_DELETE_ENABLED:false};
 await writeFile(join(output,'browser-report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2));
} finally {
 socket?.close();child.kill();await sleep(1000);
 const target=resolve(profile),root=resolve(tmpdir());
 if(target.startsWith(root+'\\') || target.startsWith(root+'/')) {
  if(basename(target).startsWith('ma-premium-')) await rm(target,{recursive:true,force:true,maxRetries:3,retryDelay:200}).catch(()=>{});
 }
}
