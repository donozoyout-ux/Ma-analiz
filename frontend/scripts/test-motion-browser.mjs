// Motion/accessibility browser checks against read-only match views; no production writes.
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, join, basename } from "node:path";
import assert from "node:assert/strict";
const base=process.argv[2];
if(!base || !/^https?:\/\//.test(base))throw Error("Explicit public/local test URL required");
const chrome=process.env.CHROME_PATH||"C:/Program Files/Google/Chrome/Application/chrome.exe";
const profile=await mkdtemp(join(tmpdir(),"ma7e-"));
const output=resolve(process.argv[3]||"../docs/reports/motion-ui-browser");await mkdir(output,{recursive:true});
const child=spawn(chrome,["--headless=new","--disable-gpu","--no-first-run","--no-default-browser-check","--remote-debugging-port=0",`--user-data-dir=${profile}`,"about:blank"],{windowsHide:true,stdio:"ignore"});
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let socket;
try {
 let port;
 for(let n=0;n<100;n++){try{port=Number((await readFile(join(profile,"DevToolsActivePort"),"utf8")).split("\n")[0]);break;}catch{await sleep(100);}}
 if(!port)throw Error("HEADLESS_BROWSER_NOT_READY");
 const tabs=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
 socket=new WebSocket(tabs.find(t=>t.type==="page").webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{socket.addEventListener("open",resolve,{once:true});socket.addEventListener("error",reject,{once:true});});
 let id=0;const pending=new Map();
 socket.addEventListener("message",event=>{const m=JSON.parse(event.data);if(m.id&&pending.has(m.id)){const {resolve,reject,timer}=pending.get(m.id);clearTimeout(timer);pending.delete(m.id);m.error?reject(Error("BROWSER_COMMAND_FAILED")):resolve(m.result);}});
 const call=(method,params={})=>new Promise((resolve,reject)=>{const next=++id;const timer=setTimeout(()=>{pending.delete(next);reject(Error("BROWSER_COMMAND_TIMEOUT"));},20000);pending.set(next,{resolve,reject,timer});socket.send(JSON.stringify({id:next,method,params}));});
 const evaluate=async expression=>(await call("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true})).result.value;
 const wait=async expression=>{for(let i=0;i<100;i++){if(await evaluate(expression))return;await sleep(200);}throw Error("BROWSER_EXPECTATION_NOT_MET");};
 const shot=async name=>{const image=await call("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});await writeFile(join(output,name),Buffer.from(image.data,"base64"));};
 await call("Page.enable");await call("Network.enable");
 await call("Emulation.setEmulatedMedia",{features:[{name:"prefers-reduced-motion",value:"no-preference"}]});
 await call("Emulation.setDeviceMetricsOverride",{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await call("Page.navigate",{url:base+"/matches"});
 await wait(`document.body.innerText.includes('FC Bayern München') && document.fonts.status==='loaded'`);
 assert.equal(await evaluate("document.documentElement.scrollWidth<=innerWidth+1"),true);
 for(const variable of ['--font-inter','--font-space','--font-bebas']){
  assert.equal(await evaluate(`document.fonts.load('16px '+getComputedStyle(document.documentElement).getPropertyValue('${variable}').trim(),'İstanbul ŞĞÜÖ').then(faces=>faces.length>0)`),true);
 }
 const rect=await evaluate(`(()=>{const r=document.querySelector('.football-match-card').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
 await call("Input.dispatchMouseEvent",{type:"mouseMoved",...rect});await sleep(350);
 assert.notEqual(await evaluate(`getComputedStyle(document.querySelector('.football-match-card')).transform`),'none');
 assert.notEqual(await evaluate(`getComputedStyle(document.querySelector('.football-match-card')).boxShadow`),'none');
 await shot('desktop-hover.png');
 const disabled=await evaluate(`(()=>{const b=document.querySelector('button:disabled');const r=b.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
 await call("Input.dispatchMouseEvent",{type:"mouseMoved",...disabled});await sleep(250);
 assert.equal(await evaluate(`getComputedStyle(document.querySelector('button:disabled')).transform`),'none');
 const button=await evaluate(`(()=>{const r=document.querySelector('.center-submit').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
 await call("Input.dispatchMouseEvent",{type:"mouseMoved",...button});await sleep(220);
 await call("Input.dispatchMouseEvent",{type:"mousePressed",button:"left",clickCount:1,...button});await sleep(220);
 const pressed=await evaluate(`getComputedStyle(document.querySelector('.center-submit')).transform`);
 assert.match(pressed,/0\.97/);
 await call("Input.dispatchMouseEvent",{type:"mouseReleased",button:"left",clickCount:1,...button});
 await wait(`document.body.innerText.includes('FC Bayern München')`);
 await call("Input.dispatchKeyEvent",{type:"keyDown",key:"Tab",code:"Tab",windowsVirtualKeyCode:9});
 await call("Input.dispatchKeyEvent",{type:"keyUp",key:"Tab",code:"Tab",windowsVirtualKeyCode:9});
 assert.equal(await evaluate(`document.activeElement.matches(':focus-visible') && getComputedStyle(document.activeElement).outlineStyle!=='none'`),true);
 await shot('keyboard-focus.png');
 const before=await evaluate(`document.querySelector('.nav-motion-indicator').style.transform`);
 await evaluate(`document.querySelector('.nav-link[href="/archive"]').click()`);
 await wait(`location.pathname==='/archive' && document.body.innerText.includes('FC Bayern München')`);await sleep(300);
 const after=await evaluate(`document.querySelector('.nav-motion-indicator').style.transform`);assert.notEqual(before,after);
 assert.equal(await evaluate(`Math.abs(document.querySelector('.nav-motion-indicator').getBoundingClientRect().top-document.querySelector('.nav-link.active').getBoundingClientRect().top-8)<2`),true);
 await call("Emulation.setEmulatedMedia",{features:[{name:"prefers-reduced-motion",value:"reduce"}]});await sleep(100);
 const reducedRect=await evaluate(`(()=>{const r=document.querySelector('.football-match-card').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
 await call("Input.dispatchMouseEvent",{type:"mouseMoved",...reducedRect});await sleep(100);
 assert.equal(await evaluate(`getComputedStyle(document.querySelector('.football-match-card')).transform`),'none');
 assert.equal(await evaluate(`document.getAnimations().filter(a=>a.playState==='running').length`),0);
 assert.equal(await evaluate(`getComputedStyle(document.querySelector('.nav-motion-indicator')).transitionDuration`),'0s');
 await shot('reduced-motion.png');
 await call("Emulation.setEmulatedMedia",{features:[{name:"prefers-reduced-motion",value:"no-preference"}]});
 await call("Emulation.setDeviceMetricsOverride",{width:390,height:844,deviceScaleFactor:1,mobile:true});
 await call("Emulation.setTouchEmulationEnabled",{enabled:true});await sleep(150);
 assert.equal(await evaluate(`matchMedia('(hover:none)').matches`),true);
 assert.equal(await evaluate(`document.documentElement.scrollWidth<=innerWidth+1`),true);
 await evaluate(`document.querySelector('.mobile-menu-toggle').focus()`);
 await call("Input.dispatchKeyEvent",{type:"keyDown",key:" ",code:"Space",windowsVirtualKeyCode:32});
 await call("Input.dispatchKeyEvent",{type:"keyUp",key:" ",code:"Space",windowsVirtualKeyCode:32});
 await wait(`document.querySelector('.mobile-menu-toggle').getAttribute('aria-expanded')==='true'`);
 await shot('mobile-menu.png');
 await call("Input.dispatchKeyEvent",{type:"keyDown",key:"Escape",code:"Escape",windowsVirtualKeyCode:27});
 await call("Input.dispatchKeyEvent",{type:"keyUp",key:"Escape",code:"Escape",windowsVirtualKeyCode:27});
 assert.equal(await evaluate(`document.querySelector('.mobile-menu-toggle').getAttribute('aria-expanded')==='false' && document.activeElement===document.querySelector('.mobile-menu-toggle')`),true);
 await evaluate(`document.querySelector('.football-match-card').scrollIntoView({block:'center'})`);await sleep(100);
 assert.equal(await evaluate(`getComputedStyle(document.querySelector('.football-match-card')).transform`),'none');
 await shot('mobile-card.png');
 const report={observed_at:new Date().toISOString(),evidence:"RECORDED_VERIFIED_PRODUCTION_READBACK_UI_TEST",desktop_1440:"PASS",mobile_390:"PASS",local_fonts:"PASS",hover_glow:"PASS",disabled_button:"PASS",button_press:"PASS",keyboard_focus:"PASS",sidebar_indicator:"PASS",mobile_keyboard_escape:"PASS",touch_no_hover_lift:"PASS",reduced_motion:"PASS",SQL_WRITE:"NOT_RUN",R2_WRITE:"NOT_RUN",LIVE_DELETE:"NOT_RUN"};
 await writeFile(join(output,"report.json"),JSON.stringify(report,null,2)+"\n");console.log(JSON.stringify(report));
} finally {
 socket?.close();child.kill();await sleep(1000);
 const target=resolve(profile),root=resolve(tmpdir());
 if(target.startsWith(root+"\\")||target.startsWith(root+"/")){
  if(basename(target).startsWith("ma7e-"))await rm(target,{recursive:true,force:true,maxRetries:3,retryDelay:200}).catch(()=>{});
 }
}
