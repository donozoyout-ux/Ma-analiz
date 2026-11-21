// Manual read-only browser smoke. No credentials; only the public match centre is opened.
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, join, basename } from "node:path";
import assert from "node:assert/strict";
const base=process.argv[2];
if(!base || !/^https?:\/\//.test(base))throw Error("Explicit public/local test URL required");
const chrome=process.env.CHROME_PATH||"C:/Program Files/Google/Chrome/Application/chrome.exe";
const profile=await mkdtemp(join(tmpdir(),"ma7e-"));
const output=resolve(process.argv[3]||"../docs/reports/stage7e-browser");await mkdir(output,{recursive:true});
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
 const evaluate=async expression=>(await call("Runtime.evaluate",{expression,returnByValue:true})).result.value;
 const wait=async expression=>{for(let i=0;i<100;i++){if(await evaluate(expression))return;await sleep(200);}throw Error("BROWSER_EXPECTATION_NOT_MET");};
 const shot=async name=>{const image=await call("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});await writeFile(join(output,name),Buffer.from(image.data,"base64"));};
 await call("Page.enable");await call("Network.enable");
 await call("Emulation.setDeviceMetricsOverride",{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await call("Page.navigate",{url:base+"/matches"});
 await wait(`document.body.innerText.includes('FC Bayern München') && document.body.innerText.includes('1 – 1')`);
 const detail=await evaluate(`document.querySelector('.football-match-card a').href`);
 await shot("desktop.png");
 await call("Emulation.setDeviceMetricsOverride",{width:390,height:844,deviceScaleFactor:1,mobile:true});
 assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth+1"),true,"Mobile horizontal overflow");
 await evaluate("document.querySelector('.football-match-card').scrollIntoView({block:'center'})");await sleep(150);await shot("mobile.png");
 await call("Page.navigate",{url:detail});
 await wait(`document.body.innerText.includes('Doğrulanmış final skor') && document.body.innerText.includes('ODbL-1.0')`);
 assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth+1"),true);
 await shot("detail-mobile.png");
 await call("Page.navigate",{url:base+"/matches"});await wait(`!!document.querySelector('.center-submit')`);
 await wait(`document.body.innerText.includes('FC Bayern München')`);
 await evaluate(`{const e=document.querySelector('input[placeholder="Takım ara…"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'stage7e-no-match');e.dispatchEvent(new Event('input',{bubbles:true}));}`);
 await sleep(200);await evaluate("document.querySelector('.center-submit').click()");
 await wait(`document.body.innerText.includes('Bu kapsamda maç bulunamadı')`);
 await call("Network.setBlockedURLs",{urls:["*api/v1/match-center*"]});
 await call("Page.navigate",{url:base+"/matches"});await wait(`document.body.innerText.includes('Maç verisine ulaşılamadı')`);
 await call("Network.setBlockedURLs",{urls:[]});
 await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Yeniden dene').click()`);
 await wait(`document.body.innerText.includes('FC Bayern München')`);
 await call("Page.navigate",{url:base+"/archive"});
 await wait(`document.querySelector('.match-freshness')?.textContent.includes('Doğrulanmış arşiv')`);
 const archivedDetail=await evaluate(`document.querySelector('.football-match-card a').href`);
 assert.match(archivedDetail,/source=history/);
 await call("Page.navigate",{url:archivedDetail});
 await wait(`document.querySelector('.center-meta')?.textContent.includes('Doğrulanmış R2 arşivi')`);
 await shot("history-mobile.png");
 const report={observed_at:new Date().toISOString(),evidence:process.env.MATCH_CENTER_RECORDED_UI === "true" ? "RECORDED_VERIFIED_PRODUCTION_READBACK_UI_TEST" : "PUBLIC_READ_ONLY_BROWSER",desktop:"PASS",mobile_390:"PASS",detail:"PASS",history:"PASS",empty_filter:"PASS",network_error_and_retry:"PASS",SQL_WRITE:"NOT_RUN",R2_WRITE:"NOT_RUN",R2_DELETE:"NOT_RUN",LIVE_DELETE:"NOT_RUN"};
 await writeFile(join(output,"report.json"),JSON.stringify(report,null,2)+"\n");console.log(JSON.stringify(report));
} finally {
 socket?.close();child.kill();await sleep(1000);
 const target=resolve(profile),root=resolve(tmpdir());
 if(target.startsWith(root+"\\")||target.startsWith(root+"/")){
  if(basename(target).startsWith("ma7e-"))await rm(target,{recursive:true,force:true,maxRetries:3,retryDelay:200}).catch(()=>{});
 }
}
