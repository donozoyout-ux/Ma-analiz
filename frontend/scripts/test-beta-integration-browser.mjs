// Integration proof against the actual local Next -> FastAPI -> SQLite/memory-R2
// chain. No API interception, production URLs, credentials or external egress.
import { spawn } from 'node:child_process';
import { mkdtemp,readFile,writeFile,mkdir,rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve,join,basename } from 'node:path';
import assert from 'node:assert/strict';
const base='http://127.0.0.1:3106', control='http://127.0.0.1:8000';
// Reject a frontend build that could proxy the local GETs to a remote backend.
const manifest=JSON.parse(await readFile('.next/routes-manifest.json','utf8'));
const rewrites=Array.isArray(manifest.rewrites)?manifest.rewrites:Object.values(manifest.rewrites).flat();
if(!rewrites.some(row=>row.source==='/api/v1/:path*'&&row.destination==='http://127.0.0.1:8000/api/v1/:path*') ||
   rewrites.some(row=>!row.destination.startsWith('http://127.0.0.1:8000/')))throw Error('LOCAL_PROXY_BUILD_REQUIRED');
const out=resolve('reports/beta-v0-integration');await mkdir(out,{recursive:true});
const uid='a1111111-1111-4111-8111-111111111111'; // synthetic, never the production UUID
const get=async path=>{
 const response=await fetch(base+path,{redirect:'error',credentials:'omit',signal:AbortSignal.timeout(8000)});
 assert.equal(response.headers.get('x-beta-isolation'),'synthetic-sqlite-memory-r2');return response;
};
const status=async()=>{const r=await fetch(control+'/__beta_test__/status',{redirect:'error',signal:AbortSignal.timeout(3000)});return r.json();};
const scenario=async mode=>{
 assert.ok(['empty','valid','missing','corrupt','timeout','partial'].includes(mode));
 const r=await fetch(control+'/__beta_test__/scenario/'+mode,{method:'POST',headers:{'x-beta-test':'synthetic-only'},redirect:'error',signal:AbortSignal.timeout(3000)});assert.equal(r.status,200);
};
const initial=await status();assert.equal(initial.evidence,'ISOLATED_SYNTHETIC_ONLY');
assert.equal(initial.SAFE_TO_DELETE,'NO');assert.equal(initial.LIVE_DELETE,'NOT_RUN');
const checks=[];const pass=name=>checks.push(name);
await scenario('empty');
assert.deepEqual(await (await get('/api/v1/matches?limit=500')).json(),[]);
assert.deepEqual((await (await get('/api/v1/match-center?source=history&limit=12')).json()).items,[]);pass('empty_real_api');
await scenario('valid');
const sqlPage=await (await get('/api/v1/match-center?source=active&limit=12')).json();
const historyPage=await (await get('/api/v1/match-center?source=history&limit=12')).json();
const sql=await (await get('/api/v1/match-center/'+uid)).json();
const history=await (await get('/api/v1/match-center/'+uid+'?source=history')).json();
assert.equal(sqlPage.items.length,2);assert.equal(historyPage.items.length,2);
assert.equal(new Set([...sqlPage.items,...historyPage.items].map(row=>row.id)).size,2);
for(const field of ['id','home_team','away_team','start_time','league_id','league_name','season','status','result','source']) assert.deepEqual(sql[field],history[field],field);
assert.equal(sql.id,uid);assert.equal(sql.storage,'POSTGRESQL');assert.equal(history.storage,'R2');
assert.equal(sql.result.home_score,1);assert.equal(sql.result.away_score,1);
assert.equal(sql.source.licence,'ODbL-1.0');assert.ok(sql.source.name && sql.source.attribution && sql.source.licence_url);
assert.equal(sql.analysis_summary,null);assert.equal(history.analysis_summary,null);
assert.deepEqual(sql.statistics,[]);assert.deepEqual(history.statistics,[]);pass('same_uuid_teams_score_utc_league_source_analysis_statistics');
assert.equal((await get('/api/v1/match-center/99999999-9999-4999-8999-999999999999')).status,404);pass('unknown_uuid_404');
for(const mode of ['missing','corrupt','timeout']) {
 await scenario(mode);const response=await get('/api/v1/match-center/'+uid+'?source=history');
 assert.equal(response.status,503);assert.equal((await response.json()).detail,'MATCH_ARCHIVE_NOT_VERIFIED');pass(mode+'_archive_api_503');
}
await scenario('partial');const partial=await (await get('/api/v1/match-center?source=history&limit=12')).json();
assert.equal(partial.history_status,'PARTIAL');assert.equal(partial.items.length,1);assert.deepEqual(partial.warnings,['SOME_MATCHES_NOT_VERIFIED']);pass('partial_archive_api');

const profile=await mkdtemp(join(tmpdir(),'ma-beta-browser-'));
const chrome=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
const child=spawn(chrome,['--headless=new','--disable-background-networking','--no-first-run','--no-default-browser-check','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{windowsHide:true,stdio:'ignore'});
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));let socket;
const errors=[],requests=[],httpErrors=[];
try {
 let port;for(let n=0;n<100;n++){try{port=Number((await readFile(join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0]);break;}catch{await sleep(100);}}
 assert.ok(port,'CHROME_REQUIRED');const tabs=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
 socket=new WebSocket(tabs.find(tab=>tab.type==='page').webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
 let id=0;const pending=new Map();
 const call=(method,params={})=>new Promise((resolve,reject)=>{
  const next=++id,timer=setTimeout(()=>{pending.delete(next);reject(Error('CDP_TIMEOUT:'+method));},20000);
  pending.set(next,{resolve,reject,timer,method});socket.send(JSON.stringify({id:next,method,params}));
 });
 socket.addEventListener('message',event=>{
  const m=JSON.parse(event.data);
  if(m.id&&pending.has(m.id)){const p=pending.get(m.id);clearTimeout(p.timer);pending.delete(m.id);m.error?p.reject(Error('CDP_ERROR:'+p.method+':'+m.error.message)):p.resolve(m.result);}
  if(m.method==='Runtime.exceptionThrown') errors.push('UNHANDLED_EXCEPTION');
  if(m.method==='Runtime.consoleAPICalled'&&['error','warning'].includes(m.params.type))errors.push('CONSOLE_ERROR_OR_WARNING');
  if(m.method==='Network.responseReceived'&&m.params.response.status>=400)httpErrors.push({path:new URL(m.params.response.url).pathname,status:m.params.response.status});
  if(m.method==='Fetch.requestPaused') {
   const {requestId,request}=m.params,url=new URL(request.url);
   if(url.origin!==base||request.method!=='GET'){errors.push('UNSAFE_BROWSER_REQUEST');void call('Fetch.failRequest',{requestId,errorReason:'BlockedByClient'}).catch(()=>errors.push('INTERCEPTION_FAILURE'));}
   else {requests.push(url.pathname+url.search);void call('Fetch.continueRequest',{requestId}).catch(()=>errors.push('INTERCEPTION_FAILURE'));}
  }
 });
 const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error('BROWSER_EVALUATION_FAILED');return r.result.value;};
 const wait=async expression=>{for(let n=0;n<120;n++){if(await evaluate(`Boolean(${expression})`))return;await sleep(100);}throw Error('WAIT_FAILED:'+expression);};
 const check=async(name,expression)=>{assert.equal(await evaluate(expression),true,name);pass(name);};
 const shot=async(name,full=false)=>{
  const params={format:'png',captureBeyondViewport:full};if(full){const {cssContentSize:r}=await call('Page.getLayoutMetrics');params.clip={x:0,y:0,width:r.width,height:r.height,scale:1};}
  const image=await call('Page.captureScreenshot',params);await writeFile(join(out,name),Buffer.from(image.data,'base64'));
 };
 const size=async(width,mobile=false)=>{await call('Emulation.setDeviceMetricsOverride',{width,height:mobile?844:1100,deviceScaleFactor:1,mobile});await call('Emulation.setTouchEmulationEnabled',{enabled:mobile});await sleep(150);};
 const nav=async path=>{await call('Page.navigate',{url:base+path});await wait(`document.readyState==='complete' && document.fonts.status==='loaded'`);await sleep(650);};
 await call('Page.enable');await call('Runtime.enable');await call('Network.enable');await call('Fetch.enable',{patterns:[{urlPattern:'*'}]});
 await call('Page.addScriptToEvaluateOnNewDocument',{source:`{
 const NativeDate=Date;const now=${Date.parse(initial.clock_utc)};
 globalThis.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}};
 document.addEventListener('DOMContentLoaded',()=>{const b=document.createElement('div');b.textContent='İZOLE ENTEGRASYON TESTİ · SENTETİK VERİ';b.style.cssText='position:fixed;bottom:8px;left:8px;z-index:99;background:#15171c;color:white;padding:7px;font:10px sans-serif;border-radius:5px;pointer-events:none';document.body.append(b);});
 }`});
 await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
 await scenario('empty');await size(1440);await nav('/');
 await wait(`document.body.innerText.includes('Bu görünümde maç bulunamadı')`);
 await check('white_dashboard_red_menu_beta_notice',`getComputedStyle(document.body).backgroundColor==='rgb(247, 248, 250)' && getComputedStyle(document.querySelector('.nav-link.active')).color==='rgb(215, 25, 50)' && document.body.innerText.includes('BETA / TEST SÜRÜMÜ')`);
 await check('no_prediction_or_success_percentage',`document.querySelectorAll('.football-kpi-value')[2].textContent==='—' && document.body.innerText.includes('Bugün için kayıtlı tahmin yok') && document.body.innerText.includes('Doğrulanmış sonuç bekleniyor')`);
 await shot('desktop-empty.png',true);
 await scenario('valid');const before=requests.filter(path=>path.startsWith('/api/v1/matches?')).length;
 await evaluate(`document.querySelector('.home-refresh').click()`);await wait(`document.querySelectorAll('.football-match-card').length===1`);
 assert.ok(requests.filter(path=>path.startsWith('/api/v1/matches?')).length>before);pass('refresh_reads_real_local_api');
 await check('dashboard_kpis_and_actual_sql_fixture',`document.querySelectorAll('.football-kpi-value')[0].textContent==='1' && document.querySelectorAll('.football-kpi-value')[1].textContent==='0' && document.querySelectorAll('.football-kpi-value')[2].textContent==='—' && document.querySelector('.football-match-card').innerText.includes('1 – 1')`);
 await shot('desktop-fixture.png',true);
 for(const width of [1440,1024,768,390,360]) {
  await size(width,width<=768);
  await check('dashboard_no_overflow_'+width,`document.documentElement.scrollWidth<=innerWidth && [...document.querySelectorAll('.football-kpi,.filter-panel,.football-match-card,.insight-card')].every(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth})`);
  if(width===390)await shot('mobile-dashboard.png',true);
  if(width===768)await shot('tablet-768.png',true);
 }
 await evaluate(`document.querySelector('.mobile-menu-toggle').click()`);await wait(`document.querySelector('.mobile-menu-toggle').getAttribute('aria-expanded')==='true'`);await shot('mobile-menu.png');
 await evaluate(`document.querySelector('.nav-link[href="/"]').click()`);await wait(`document.querySelector('.mobile-menu-toggle').getAttribute('aria-expanded')==='false'`);pass('touch_menu_navigation');
 await size(1440);await evaluate('scrollTo(0,0)');await sleep(150);
 const pos=await evaluate(`(()=>{const r=document.querySelector('.football-kpi').getBoundingClientRect();return {x:r.left+r.width*.95,y:r.top+r.height*.1}})()`);
 for(let n=0;n<80;n++)await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:pos.x-(n%3),y:pos.y});await sleep(350);
 await check('tilt_bounded_glow_shadow',`(()=>{const e=document.querySelector('.football-kpi'),s=getComputedStyle(e);return e.hasAttribute('data-tilt-active') && Math.abs(parseFloat(e.style.getPropertyValue('--tilt-x')))<=2 && Math.abs(parseFloat(e.style.getPropertyValue('--tilt-y')))<=2 && s.transform.startsWith('matrix3d') && s.boxShadow!=='none' && getComputedStyle(e,'::after').opacity==='1';})()`);
 await shot('desktop-tilt.png',true);await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:1400,y:10});await sleep(350);
 await check('tilt_exit_reset',`!document.querySelector('[data-tilt-active]')`);
 await evaluate(`document.querySelector('.match-card-bottom a').click()`);await wait(`location.pathname==='/matches/${uid}' && document.querySelector('.center-provenance')`);
 await check('sql_detail_same_uuid_score_attribution_empty_analysis',`document.querySelector('.center-score strong').textContent==='1 – 1' && document.body.innerText.includes('PostgreSQL operasyonel havuzu') && document.body.innerText.includes('ODbL-1.0') && document.body.innerText.includes('Analiz henüz hazırlanmadı') && document.body.innerText.includes('İstatistik verisi mevcut değil')`);
 await check('shared_white_theme_on_detail',`!document.querySelector('.premium-dashboard') && getComputedStyle(document.body).backgroundColor==='rgb(247, 248, 250)'`);
 await evaluate(`document.querySelector('.nav-link[href="/archive"]').click()`);await wait(`location.pathname==='/archive' && !!document.querySelector('.football-match-card a[href*="${uid}"][href*="source=history"]')`);
 await evaluate(`document.querySelector('.football-match-card a[href*="${uid}"][href*="source=history"]').click()`);await wait(`location.search==='?source=history' && !!document.querySelector('.center-provenance')`);
 await check('r2_detail_same_uuid_source_and_licence',`location.pathname==='/matches/${uid}' && document.querySelector('.center-score strong').textContent==='1 – 1' && document.body.innerText.includes('Doğrulanmış R2 arşivi') && document.body.innerText.includes('ODbL-1.0') && document.body.innerText.includes('Analiz henüz hazırlanmadı') && document.body.innerText.includes('doğrulanmış oran kaydı sunulmuyor')`);
 await shot('history-detail-desktop.png',true);
 for(const width of [1440,1024,768,390,360]) {
  await size(width,width<=768);await check('history_detail_no_overflow_'+width,`document.documentElement.scrollWidth<=innerWidth`);
  if(width===390)await shot('history-detail-mobile.png',true);
 }
 for(const mode of ['missing','corrupt','timeout']) {
  await scenario(mode);await nav('/matches/'+uid+'?source=history');await wait(`document.body.innerText.includes('Maç detayı okunamadı')`);
  await check(mode+'_archive_ui_safe',`!document.querySelector('.center-score') && document.body.innerText.includes('Skor üretilmedi')`);
 }
 await scenario('partial');await nav('/archive');await wait(`!!document.querySelector('.center-notice')`);
 await check('partial_ui_warning',`document.querySelectorAll('.football-match-card').length===1 && document.body.innerText.includes('Bazı arşiv kayıtları doğrulanamadığı için gösterilmedi')`);
 await scenario('valid');await size(1440);await nav('/');await wait(`document.querySelectorAll('.football-match-card').length===1`);
 await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});await sleep(100);
 await check('reduced_motion',`document.getAnimations().filter(a=>a.playState==='running').length===0 && !document.querySelector('[data-tilt-active]') && getComputedStyle(document.querySelector('.football-kpi')).transform==='none'`);await shot('reduced-motion.png',true);
 await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});await size(390,true);
 await evaluate(`document.querySelector('.football-kpi').scrollIntoView({block:'center'})`);await sleep(150);
 await check('mobile_tilt_disabled',`matchMedia('(hover:none)').matches && !document.querySelector('[data-tilt-active]') && getComputedStyle(document.querySelector('.football-kpi')).transform==='none'`);
 await evaluate(`document.querySelector('.mobile-menu-toggle').focus()`);
 await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
 await check('keyboard_focus_visible',`document.activeElement.matches(':focus-visible') && getComputedStyle(document.activeElement).outlineStyle==='solid'`);
 const archiveErrors=httpErrors.filter(row=>row.path==='/api/v1/match-center/'+uid&&row.status===503);
 const faviconErrors=httpErrors.filter(row=>row.path==='/favicon.ico'&&row.status===404);
 assert.equal(archiveErrors.length,3);assert.equal(httpErrors.length,archiveErrors.length+faviconErrors.length);pass('only_expected_archive_http_failures');
 await sleep(500);await check('no_continuous_idle_animation',`document.getAnimations().filter(a=>a.playState==='running').length===0`);
 assert.deepEqual(errors,[]);pass('no_hydration_console_or_runtime_errors');
 const final=await status();assert.equal(final.sql_write_attempts,0);assert.equal(final.object_write_attempts,0);assert.equal(final.objects_unchanged,true);assert.ok(final.sql_reads>0&&final.object_reads>0);pass('read_only_sql_and_immutable_memory_archive');
 const report={evidence:'LOCAL_NEXT_FASTAPI_SQLITE_MEMORY_PARQUET_END_TO_END',synthetic:true,frozen_clock_utc:initial.clock_utc,checks,passed:checks.length,expected_archive_503_responses:archiveErrors.length,benign_favicon_404_responses:faviconErrors.length,unexpected_http_errors:0,console_errors:errors.length,backend_selects:final.sql_reads-initial.sql_reads,memory_object_reads:final.object_reads-initial.object_reads,production_requests:0,SQL_WRITE:'NOT_RUN',R2_WRITE:'NOT_RUN',LIVE_DELETE:'NOT_RUN',SAFE_TO_DELETE:'NO',AUTO_RETENTION_DELETE_ENABLED:false,OPERATIONAL_DELETE_ENABLED:false};
 await writeFile(join(out,'integration-browser-report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2));
} finally {
 socket?.close();child.kill();await sleep(1000);
 const target=resolve(profile),root=resolve(tmpdir());if((target.startsWith(root+'\\')||target.startsWith(root+'/'))&&basename(target).startsWith('ma-beta-browser-')) await rm(target,{recursive:true,force:true,maxRetries:3,retryDelay:200}).catch(()=>{});
}
