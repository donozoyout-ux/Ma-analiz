// Share UI checks: recorded read-only data and explicitly mocked browser share capabilities.
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, join, basename } from "node:path";
import assert from "node:assert/strict";
const base=process.argv[2];
if(!base || !["localhost","127.0.0.1"].includes(new URL(base).hostname))throw Error("Local test URL required");
const chrome=process.env.CHROME_PATH||"C:/Program Files/Google/Chrome/Application/chrome.exe";
const profile=await mkdtemp(join(tmpdir(),"ma7e-"));
const output=resolve(process.argv[3]||"../docs/reports/match-share-browser");await mkdir(output,{recursive:true});
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
 const evaluate=async expression=>{const r=await call("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error("BROWSER_EVALUATION_FAILED");return r.result.value;};
 const wait=async expression=>{for(let i=0;i<100;i++){if(await evaluate(expression))return;await sleep(200);}throw Error("BROWSER_EXPECTATION_NOT_MET");};
 const shot=async name=>{const image=await call("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});await writeFile(join(output,name),Buffer.from(image.data,"base64"));};

 await call("Page.enable"); await call("Network.enable");
 await call("Emulation.setEmulatedMedia",{features:[{name:"prefers-reduced-motion",value:"reduce"}]});
 const snapshot=JSON.parse(await readFile(new URL("../../docs/reports/stage7e-live-read.json",import.meta.url),"utf8")).report;
 const requests=[];
 socket.addEventListener("message",async event=>{
  const m=JSON.parse(event.data);if(m.method!=="Fetch.requestPaused")return;
  const {requestId,request}=m.params;const url=new URL(request.url);requests.push(url.pathname+url.search);
  const row=url.searchParams.get("source")==="history"?snapshot.history_detail:snapshot.active_detail;
  await call("Fetch.fulfillRequest",{requestId,responseCode:200,responseHeaders:[{name:"Content-Type",value:"application/json"}],body:Buffer.from(JSON.stringify(url.pathname.includes("match-center")?row:[])).toString("base64")});
 });
 await call("Fetch.enable",{patterns:[{urlPattern:"*/api/v1/*",requestStage:"Request"}]});
 const click=async index=>{
  const r=await evaluate(`(()=>{const b=document.querySelectorAll('.match-share-button')[${index}];b.scrollIntoView({block:'center'});const r=b.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
  await call("Input.dispatchMouseEvent",{type:"mousePressed",button:"left",clickCount:1,...r});
  await call("Input.dispatchMouseEvent",{type:"mouseReleased",button:"left",clickCount:1,...r});
 };
 const feedback=()=>evaluate(`document.querySelector('.match-share-feedback').textContent.trim()`);
 const results=[];
 for(const width of [1440,390]){
  await call("Emulation.setDeviceMetricsOverride",{width,height:width===390?844:1000,deviceScaleFactor:1,mobile:width===390});
  await call("Emulation.setTouchEmulationEnabled",{enabled:width===390});
  for(const history of [false,true]){
   const url=base+"/matches/"+snapshot.history_detail.id+(history?"?source=history":"");
   await call("Page.navigate",{url});await call("Page.bringToFront");
   await wait(`document.querySelectorAll('.match-share-button').length===2`);
   assert.equal(await evaluate(`document.documentElement.scrollWidth<=innerWidth+1`),true);
   await call("Browser.grantPermissions",{origin:base,permissions:["clipboardReadWrite","clipboardSanitizedWrite"]});
   await click(0);await wait(`document.querySelector('.match-share-feedback').textContent.includes('panoya kopyalandı')`);
   assert.equal(await evaluate(`navigator.clipboard.readText()`),url);
   await shot(`${width}-${history?'history':'active'}-copy.png`);
   // Browser capability/permission scenarios are controlled stubs, not OS dialogs.
   await evaluate(`Object.defineProperty(navigator,'clipboard',{configurable:true,writable:true,value:{writeText:async value=>{window.copied=value}}});Object.defineProperty(navigator,'share',{configurable:true,writable:true,value:undefined});Object.defineProperty(navigator,'canShare',{configurable:true,writable:true,value:undefined});window.copied=null`);
   await click(1);await wait(`window.copied===location.href`);assert.match(await feedback(),/panoya kopyalandı/);
   await evaluate(`navigator.clipboard.writeText=async()=>{throw new DOMException('denied','NotAllowedError')}`);
   await click(0);await wait(`document.querySelector('.match-share-feedback').textContent.includes('adres çubuğundan')`);
   assert.equal(await evaluate(`Array.from(document.querySelectorAll('.match-share-button')).every(b=>!b.disabled)`),true);
   await evaluate(`navigator.clipboard.writeText=async()=>{throw Error('unexpected copy')};Object.defineProperty(navigator,'share',{configurable:true,writable:true,value:async data=>{window.shared=data}});window.shared=null`);
   await click(1);await wait(`window.shared!==null && document.querySelector('.match-share-feedback').textContent.includes('Paylaşım tamamlandı')`);
   assert.equal(await evaluate(`window.shared.url`),url);assert.match(await evaluate(`window.shared.title`),/FC Bayern München.*1. FC Köln/);
   // Cancellation after success must clear stale feedback and not copy.
   await evaluate(`navigator.share=async()=>{throw new DOMException('cancel','AbortError')}`);
   await click(1);await wait(`Array.from(document.querySelectorAll('.match-share-button')).every(b=>!b.disabled) && document.querySelector('.match-share-feedback').textContent.trim()===''`);
   await evaluate(`navigator.share=async()=>{throw new DOMException('denied','NotAllowedError')}`);
   await click(1);await wait(`document.querySelector('.match-share-feedback').textContent.includes('adres çubuğundan')`);
   await evaluate(`Object.defineProperty(navigator,'canShare',{configurable:true,writable:true,value:()=>false});navigator.share=async()=>{throw Error('must not share')};navigator.clipboard.writeText=async value=>{window.copied=value};window.copied=null`);
   await click(1);await wait(`window.copied===location.href`);
   await evaluate(`navigator.canShare=()=>true;navigator.share=()=>new Promise(resolve=>{window.finishShare=resolve})`);
   await click(1);await wait(`Array.from(document.querySelectorAll('.match-share-button')).every(b=>b.disabled)`);
   await evaluate(`window.finishShare()`);await wait(`Array.from(document.querySelectorAll('.match-share-button')).every(b=>!b.disabled)`);
   await evaluate(`document.querySelector('.match-share-button').focus()`);
   await call("Input.dispatchKeyEvent",{type:"keyDown",key:"Tab",code:"Tab",windowsVirtualKeyCode:9});
   await call("Input.dispatchKeyEvent",{type:"keyUp",key:"Tab",code:"Tab",windowsVirtualKeyCode:9});
   assert.equal(await evaluate(`document.activeElement===document.querySelectorAll('.match-share-button')[1] && document.activeElement.matches(':focus-visible')`),true);
   results.push({width,source:history?"history":"active",real_clipboard:"PASS",unsupported_share:"PASS",clipboard_denial:"PASS",native_share_stub:"PASS",cancel_stub:"PASS",share_denial_stub:"PASS",canShare_false:"PASS",pending_disabled:"PASS",keyboard:"PASS",overflow:"PASS"});
  }
 }
 assert.ok(requests.some(u=>u.includes('source=history')));
 const report={observed_at:new Date().toISOString(),evidence:"RECORDED_VERIFIED_READBACK_LOCAL_UI_WITH_MOCKED_SHARE_CAPABILITIES",results,history_request:"PASS",SQL_WRITE:"NOT_RUN",R2_WRITE:"NOT_RUN",LIVE_DELETE:"NOT_RUN"};
 await writeFile(join(output,"report.json"),JSON.stringify(report,null,2)+"\n");console.log(JSON.stringify(report));
} finally {
 socket?.close();child.kill();await sleep(1000);
 const target=resolve(profile),root=resolve(tmpdir());
 if(target.startsWith(root+"\\")||target.startsWith(root+"/")){
  if(basename(target).startsWith("ma7e-"))await rm(target,{recursive:true,force:true,maxRetries:3,retryDelay:200}).catch(()=>{});
 }
}
