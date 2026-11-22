"use strict";
// helpers, API calls, toasts/dialogs/notification centre, live link, alarm sound, sign-in shell and page frame
// Loaded by index.html as plain scripts, in order: core, cases, people, network, system, install, admin (they share one global scope).
const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt=iso=>iso?new Date(iso).toLocaleString("th-TH-u-ca-buddhist",{timeZone:"Asia/Bangkok",dateStyle:"short",timeStyle:"medium"}):"-";
const ST={new:"รอรับเรื่อง",accepted:"รับเรื่องแล้ว",closed:"ปิดเคส",cancelled:"ยกเลิกโดยผู้ป่วย"};
let token=sessionStorage.getItem("tk")||"",role=sessionStorage.getItem("role")||"",page="cases",ws=null,timer=null,audioOn=(()=>{try{return localStorage.getItem("snd")!=="0"}catch(e){return true}})();
async function api(path,opt={}){
  const r=await fetch("/api"+path,{...opt,headers:{"content-type":"application/json",...(token?{authorization:"Bearer "+token}:{}),...(opt.headers||{})},body:opt.body&&JSON.stringify(opt.body)});
  if(r.status===401&&!path.startsWith("/login")){logout();throw new Error("หมดเวลา กรุณาเข้าสู่ระบบใหม่")}
  const j=await r.json().catch(()=>({}));
  if(r.status===403&&j.detail==="2fa setup required"&&sessionStorage.getItem("ms")!=="1"){sessionStorage.setItem("ms","1");render();throw new Error("ต้องตั้งค่าการยืนยันตัวตน 2 ขั้นก่อนใช้งาน")}
  if(!r.ok)throw new Error(j.detail||("ผิดพลาด "+r.status));return j}
/* ---------- feedback layer: toasts, dialogs, notification centre, emergency popup ---------- */
const TT={ok:["check","สำเร็จ"],err:["xcirc","ผิดพลาด"],warn:["warnt","คำเตือน"],info:["info","แจ้งให้ทราบ"],sos:["cases","เหตุฉุกเฉิน"]};
function toastKind(m){m=String(m);
  if(/ไม่สำเร็จ|ไม่ได้|ผิดพลาด|ไม่ถูกต้อง|error|failed|denied|\b[45]\d\d\b/i.test(m))return "err";
  if(/^(บันทึก|ลบ|เพิ่ม|คัดลอก|ส่ง|เปลี่ยน|ปิด|เปิด|กลับไปใช้|ยกเลิก|รีเซ็ต|ผูก|ติดตั้ง|อัปเดต).*(แล้ว|ทันที)/.test(m))return "ok";
  if(/ต้อง|ก่อน|กรอก|ยังไม่|ขาดการเชื่อมต่อ|⚠️/.test(m))return "warn";return "info"}
function toast(m,kind){const box=$("#toast");if(!box)return;kind=TT[kind]?kind:toastKind(m);
  const t=document.createElement("div");t.className="tst tk-"+kind;t.setAttribute("role",kind==="err"?"alert":"status");
  t.innerHTML=`${ico(TT[kind][0])}<span>${esc(String(m).replace(/^[\u{1F300}-\u{1FAFF}☀-➿️\s]+/u,""))}</span><button type="button" aria-label="ปิด">${ico("x")}</button>`;
  box.appendChild(t);while(box.children.length>4)box.firstChild.remove();
  let tm;const close=()=>{clearTimeout(tm);t.classList.add("out");setTimeout(()=>t.remove(),180)},arm=()=>{tm=setTimeout(close,kind==="err"?7000:4000)};
  t.onmouseenter=()=>clearTimeout(tm);t.onmouseleave=arm;t.querySelector("button").onclick=close;arm()}

// modal dialog: resolves true/false (or the typed text / null when o.input is set)
function dlg(o){return new Promise(res=>{const d=document.createElement("dialog");d.className="dlg "+(o.tone||"");
  d.innerHTML=`<form method="dialog"><div class="dlg-h">${o.icon?`<span class="dlg-i">${ico(o.icon)}</span>`:""}<h2>${esc(o.title||"")}</h2></div>
   ${o.html?`<div class="dlg-b">${o.html}</div>`:o.body?`<div class="dlg-b">${esc(o.body).replace(/\n/g,"<br>")}</div>`:""}
   ${o.input?`<label class="fld dlg-in"><span>${esc(o.input.label||"")}</span><input value="${esc(o.input.value||"")}" placeholder="${esc(o.input.placeholder||"")}"></label>`:""}
   <div class="dlg-f">${o.cancel===false?"":`<button type="button" class="btn" data-v="0">${esc(o.cancel||"ยกเลิก")}</button>`}<button class="btn ${o.tone==="danger"?"r":"p"}" data-v="1">${esc(o.ok||"ตกลง")}</button></div></form>`;
  document.body.appendChild(d);const no=o.input?null:false;let fin=false;
  const done=v=>{if(fin)return;fin=true;d.classList.add("out");setTimeout(()=>{d.close();d.remove()},140);res(v)};
  d.querySelector("[data-v='0']")?.addEventListener("click",()=>done(no));
  d.querySelector("form").onsubmit=e=>{e.preventDefault();done(o.input?d.querySelector(".dlg-in input").value:true)};
  d.addEventListener("cancel",e=>{e.preventDefault();done(no)});
  d.addEventListener("close",()=>{if(!fin){fin=true;d.remove();res(no)}});   // Esc can close it without a cancelable "cancel"
  d.addEventListener("mousedown",e=>{if(e.target===d)done(no)});
  d.showModal();(d.querySelector(".dlg-in input")||d.querySelector(".dlg-f .btn.p,.dlg-f .btn.r")).focus()})}
// drop-in for confirm(): first line = title, the rest = details; destructive wording gets a red button
const ask=(msg,o={})=>{const [t,...r]=String(msg).split("\n"),danger=/ลบ|ถาวร|รีเซ็ต|ถอน|รีบูต|ปิดใช้งาน|ยกเลิกการผูก/.test(t);
  return dlg({title:t.replace(/^[⚠️\s]+/u,""),body:r.join("\n").trim(),tone:danger?"danger":"",icon:danger?"warnt":"help",ok:/^ลบ/.test(t)?"ลบ":"ยืนยัน",...o})};

// notification centre (per browser, newest first, max 50)
const NC={get(){try{return JSON.parse(localStorage.getItem("ncv1")||"[]")}catch(e){return []}},set(a){lsSet("ncv1",JSON.stringify(a.slice(0,50)))}};
function notify(n){const a=NC.get();if(n.id&&a.some(x=>x.id===n.id))return false;a.unshift({at:new Date().toISOString(),read:false,...n});NC.set(a);ncBadge();if($("#ncp")&&!$("#ncp").hidden)ncDraw();return true}
function ncBadge(){const b=$("#ncb");if(!b)return;const u=NC.get().filter(x=>!x.read).length;b.textContent=u>9?"9+":u;b.hidden=!u;$("#nc").setAttribute("aria-label","การแจ้งเตือน"+(u?` (ยังไม่อ่าน ${u})`:""))}
function ncGo(x){if(x.go?.case){openCaseAfter=+x.go.case;page="cases"}else if(x.go?.page)page=x.go.page;render();scrollTo(0,0)}
function ncDraw(){const a=NC.get(),p=$("#ncp");if(!p)return;
  p.innerHTML=`<div class="ncp-h"><b>การแจ้งเตือน</b><span>${a.some(x=>!x.read)?'<button type="button" id="ncr">อ่านทั้งหมด</button>':""}${a.length?'<button type="button" id="ncx">ล้าง</button>':""}</span></div>
   ${a.length?`<ul>${a.map((x,i)=>`<li class="${x.read?"":"un"} nk-${x.kind}"><button type="button" data-ni="${i}"><span class="nci">${ico(TT[x.kind]?.[0]||"info")}</span><span class="nct"><b>${esc(x.title)}</b>${x.body?`<span>${esc(x.body)}</span>`:""}<time>${Date.now()-Date.parse(x.at)<60000?"เมื่อสักครู่":ago(x.at)}</time></span></button></li>`).join("")}</ul>`
   :'<p class="ncp-e">ยังไม่มีการแจ้งเตือน เคสใหม่ เคสเกินเวลา เกตเวย์หรืออุปกรณ์มีปัญหา จะแสดงที่นี่</p>'}`;
  $("#ncr")&&($("#ncr").onclick=()=>{NC.set(NC.get().map(x=>({...x,read:true})));ncBadge();ncDraw()});
  $("#ncx")&&($("#ncx").onclick=()=>{NC.set([]);ncBadge();ncDraw()});
  p.querySelectorAll("[data-ni]").forEach(b=>b.onclick=()=>{const all=NC.get(),x=all[+b.dataset.ni];x.read=true;NC.set(all);ncClose();ncGo(x)})}
function ncClose(){const p=$("#ncp");if(p&&!p.hidden){p.hidden=true;$("#nc").setAttribute("aria-expanded","false")}}
function ncInit(){const b=$("#nc"),p=$("#ncp");if(!b)return;ncBadge();
  b.onclick=e=>{e.stopPropagation();if(p.hidden){p.hidden=false;b.setAttribute("aria-expanded","true");ncDraw()}else ncClose()};
  p.onclick=e=>e.stopPropagation()}
document.addEventListener("click",ncClose);
document.addEventListener("click",e=>{if(e.target.closest("[data-gwdev]")){page="devices";render();scrollTo(0,0)}});document.addEventListener("keydown",e=>{if(e.key==="Escape")ncClose()});

// live events → notification centre (+ emergency popup for new cases)
const DVI={offline:"ขาดการติดต่อ",battery:"แบตเตอรี่ต่ำ",mains:"ไฟบ้านดับ"};
async function onEvt(m){
  const id=m.case_id;
  if(m.type==="case_new"){let c=null;try{c=await api(`/cases/${id}`)}catch(e){}
    alarmAdd(id);notify({id:`c${id}n`,kind:"sos",title:`เคสฉุกเฉินใหม่ #${id}`,body:c?.patient?`${c.patient.full_name} · ${c.patient.address||""}`:"",go:{case:id}});sosPopup(id,c)}
  else if(m.type==="case_update"){
    if(m.overdue&&m.state==="new")notify({id:`c${id}o`,kind:"warn",title:`เคส #${id} เกินเวลารับเรื่อง`,body:"ยังไม่มีผู้รับเรื่อง",go:{case:id}});
    else if(m.state==="accepted")notify({id:`c${id}a`,kind:"info",title:`รับเรื่องเคส #${id} แล้ว`,go:{case:id}});
    else if(m.state==="closed")notify({id:`c${id}c`,kind:"ok",title:`ปิดเคส #${id} แล้ว`,go:{case:id}});
    if(m.state!=="new"){sosDrop(id);alarmDrop(id)}}
  else if(m.type==="case_repeat"){let c=null;try{c=await api(`/cases/${id}`)}catch(e){}
    notify({id:`c${id}r${Date.now()}`,kind:"sos",title:`ผู้ป่วยกดปุ่มซ้ำ — เคส #${id}`,body:c?.patient?`${c.patient.full_name} · ${c.state==="accepted"?"รับเรื่องแล้ว แต่ผู้ป่วยยังกดอยู่ — โทรติดตาม":"ยังไม่มีผู้รับเรื่อง"}`:"",go:{case:id}});
    toast(`ผู้ป่วยกดปุ่มซ้ำ (เคส #${id})`,"warn");if(audioOn&&!SND.timer)siren()}
  else if(m.type==="case_test"){beep();notify({kind:"info",title:"ได้รับสัญญาณทดสอบปุ่ม",go:{page:"selftest"}})}
  else if(m.type==="gateway_state")notify({kind:m.state==="offline"?"warn":"ok",title:m.state==="offline"?`เกตเวย์ ${m.gateway_id} ขาดการเชื่อมต่อ`:`เกตเวย์ ${m.gateway_id} กลับมาออนไลน์`,go:{page:"gateways"}});
  else if(m.type==="device_state")devNotify(m)}
// one bell entry per device and issue: a reminder only refreshes its time, a recovery turns it into "back to normal"
function devNotify(m){const is=m.issue||"",k=Object.keys(DVI).find(k=>is.startsWith("device_"+k));if(!k)return;
  const ok=/_ok$/.test(is),rep=/_repeat$/.test(is),crit=/_critical$/.test(is),id=`d:${m.dev_eui}:${k}`,who=m.label||m.dev_eui;
  const a=NC.get(),i=a.findIndex(x=>x.id===id),old=i>=0?a.splice(i,1)[0]:null;
  if(rep&&old){a.unshift({...old,at:new Date().toISOString()});NC.set(a);ncBadge();return}
  const title=ok?`${who}: ${DVI[k]} — กลับมาปกติแล้ว`:crit?`${who}: แบตเตอรี่วิกฤต`:`${who}: ${DVI[k]}`;
  a.unshift({id,kind:ok?"ok":"warn",title,body:ok?"":(k==="offline"?"ปุ่มอาจแจ้งเหตุไม่ได้ โปรดโทรตรวจสอบ":k==="mains"?"ปุ่มใช้แบตสำรองอยู่":"โปรดเปลี่ยนหรือชาร์จแบต"),
    at:new Date().toISOString(),read:ok&&old?old.read:false,go:{page:"devices"}});
  NC.set(a);ncBadge();if($("#ncp")&&!$("#ncp").hidden)ncDraw()}
async function ncSeed(){if(ncSeed.done)return;ncSeed.done=true;   // open cases that arrived while nobody was signed in
  try{const cs=await api("/cases/open?pending_only=true");alarmSet(cs.filter(c=>!c.is_test).map(c=>c.id));cs.filter(c=>!c.is_test).forEach(c=>notify({id:`c${c.id}n`,kind:"sos",title:`เคสฉุกเฉินใหม่ #${c.id}`,body:c.patient?`${c.patient.full_name} · ${c.patient.address||""}`:"",go:{case:c.id},at:c.created_at}))}catch(e){}}

// emergency popup: one modal that lists every new case not yet handled in this browser
const sosQ=new Map();
function sosPopup(id,c){sosQ.set(+id,c);let d=$("#sos");
  if(!d){d=document.createElement("dialog");d.id="sos";d.className="dlg sos";document.body.appendChild(d);
    d.addEventListener("cancel",e=>{e.preventDefault();sosClose()});d.addEventListener("close",()=>sosQ.clear())}
  const [first,...rest]=[...sosQ.entries()].reverse(),[fid,fc]=first,p=fc?.patient;
  d.innerHTML=`<div class="sos-r" aria-hidden="true"><span></span>${ico("cases")}</div>
    <h2>เหตุฉุกเฉินใหม่</h2><p class="sos-n">${p?esc(p.full_name):`เคส #${fid}`}</p>
    <p class="sos-a">${p?esc([p.address,p.village].filter(Boolean).join(" ")):"ไม่พบข้อมูลผู้ป่วยของอุปกรณ์นี้"}</p>
    <p class="sos-t">เคส #${fid}${fc?` · กดปุ่มเมื่อ ${new Date(fc.created_at).toLocaleTimeString("th-TH",{hour:"2-digit",minute:"2-digit"})} น. · ${esc(fc.device?.label||fc.device?.dev_eui||"")}`:""}</p>
    ${rest.length?`<p class="sos-m">และมีเคสใหม่อีก ${rest.length} เคส: ${rest.map(([i])=>"#"+i).join(", ")}</p>`:""}
    <div class="sos-f">${p?.phone?`<a class="btn" href="tel:${esc(p.phone)}">${ico("phone")}โทร ${esc(p.phone)}</a>`:""}<button class="btn r" id="sosgo" type="button">เปิดเคส #${fid}</button></div>
    <button class="sos-x" type="button" id="sosx">ปิดหน้าต่างและหยุดเสียง 2 นาที เคสยังรอรับเรื่องอยู่</button>`;
  $("#sosgo").onclick=()=>{sosClose();openCaseAfter=fid;page="cases";render();scrollTo(0,0)};$("#sosx").onclick=()=>{sosClose();if(SND.timer)alarmMute(2)};
  if(!d.open){d.showModal();$("#sosgo").focus()}}
// LINE bind dialog: one QR that opens the OA chat with the code typed (scan → tap send), the code for typing by hand
function bindHTML(r,self){const oa=r.oa,w=self?"":"ให้ อสม. ";
  return `${oa?`<div class="bindq"><div class="qr">${oa.qr_svg}</div><ol>
      <li>${w}เปิดกล้องมือถือหรือ LINE แล้ว<b>สแกน QR นี้</b></li>
      <li>LINE จะเปิดแชทกับ <b>${esc(oa.name||oa.basic_id)}</b> พร้อมรหัสที่พิมพ์ไว้แล้ว → กด <b>ส่ง</b></li>
      <li>กด <b>เพิ่มเพื่อน</b> ด้วย (ถ้ายังไม่ได้เพิ่ม) ไม่เช่นนั้นจะไม่ได้รับข้อความ</li></ol></div>
    <p class="mut">ถ้าสแกนไม่ได้: เพิ่มเพื่อน <a href="${esc(oa.add_url)}" target="_blank" rel="noopener">${esc(oa.basic_id)}</a> แล้วพิมพ์รหัสนี้ส่งในแชท</p>`
   :`<p>${w}เพิ่มเพื่อนบัญชี LINE OA ของ รพ.สต. แล้วพิมพ์รหัสนี้ส่งในแชท</p>`}
   <div class="bigcode">${esc(r.code)}</div><p class="mut">ใช้ได้ ${esc(r.expires_minutes)} นาที ครั้งเดียว · ผูกสำเร็จแล้ว${self?"คุณ":" อสม. "}จะได้ข้อความ "ผูกบัญชีสำเร็จ"${self?" ใน LINE":" และรายชื่อจะขึ้น ✅"}</p>`}
function sosDrop(id){if(!sosQ.delete(+id))return;if(!sosQ.size)sosClose();else{const [i,c]=[...sosQ.entries()].pop();sosQ.delete(i);sosPopup(i,c)}}
function sosClose(){sosQ.clear();const d=$("#sos");if(d?.open)d.close()}

const guard=f=>async(...a)=>{try{return await f(...a)}catch(e){toast(e.message,"err")}};
function logout(){token="";page="cases";sessionStorage.clear();wsGen++;if(ws){ws.onclose=null;ws.close()}alarmSet([]);render()}
// ---------------------------------------------------------------- live link (WebSocket)
// One connection at a time: every connect bumps wsGen, so callbacks of an older socket can never schedule another
// reconnect (that loop used to replace a healthy connection every 3 s and lose events in between).
// A ping every 25 s notices a dead link (sleeping laptop, Wi-Fi switch, proxy timeout); after a reconnect the open
// cases are re-read, so nothing that happened while offline is missed.
let wsGen=0,wsLast=0,wsDown=false;
function connectWS(){
  const gen=++wsGen;if(ws){ws.onopen=ws.onclose=ws.onmessage=null;try{ws.close()}catch(e){}}
  const s=new WebSocket((location.protocol==="https:"?"wss":"ws")+"://"+location.host+"/ws?token="+encodeURIComponent(token));ws=s;
  s.onopen=()=>{if(gen!==wsGen)return;wsLast=Date.now();setConn(true);if(wsDown){wsDown=false;liveResync()}};
  s.onmessage=e=>{if(gen!==wsGen)return;wsLast=Date.now();let m;try{m=JSON.parse(e.data)}catch(_){return}if(m.type!=="pong")liveEvent(m)};
  s.onclose=()=>{if(gen!==wsGen)return;setConn(false);wsDown=true;if(token)setTimeout(()=>{if(gen===wsGen&&token)connectWS()},2000)}}
setInterval(()=>{if(!token||!ws)return;
  if(ws.readyState===1){if(Date.now()-wsLast>60000){wsDown=true;setConn(false);connectWS()}else try{ws.send("ping")}catch(e){}}},25000);
document.addEventListener("visibilitychange",()=>{if(document.hidden||!token)return;if(!ws||ws.readyState>1||Date.now()-wsLast>40000){wsDown=true;connectWS()}});
addEventListener("online",()=>{if(token){wsDown=true;connectWS()}});
// ---------------------------------------------------------------- button link badge (ok / late / offline / never)
// The server sends late_at / off_at; the badge moves on by itself as time passes and resets on every uplink (device_seen).
const LINK={ok:["ปกติ","lk-ok"],late:["เลยรอบ","lk-late"],offline:["ขาดการติดต่อ","lk-off"],never:["ยังไม่เคยส่ง","lk-never"]};
function linkNow(d){if(!d.last_seen||!d.late_at)return "never";const t=Date.now();return t<Date.parse(d.late_at)?"ok":t<Date.parse(d.off_at)?"late":"offline"}
function linkBadge(d){const [t,c]=LINK[linkNow(d)];
  return `<span class="lk ${c}" data-seen="${esc(d.last_seen||"")}" data-late="${esc(d.late_at||"")}" data-off="${esc(d.off_at||"")}" title="${d.hb_min?`รายงานทุก ${d.hb_min} นาที`:""}${d.last_seen?" · ส่งล่าสุด "+fmt(d.last_seen):""}"><i></i><span>${t}</span></span>`}
function linkRepaint(){document.querySelectorAll(".lk[data-late]").forEach(el=>{const [t,c]=LINK[linkNow({last_seen:el.dataset.seen,late_at:el.dataset.late,off_at:el.dataset.off})];
    if(!el.classList.contains(c)){el.className="lk "+c;el.querySelector("span").textContent=t}
    el.closest("tr")?.classList.toggle("stale",c!=="lk-ok")});
  document.querySelectorAll("[data-ago]").forEach(el=>{if(el.dataset.ago)el.textContent=ago(el.dataset.ago)})}
setInterval(linkRepaint,30000);
let seenFlash=0;
function devSeen(m){clearTimeout(devSeen.t);
  if(page==="devices"&&!devEdit){seenFlash=m.device_id;devSeen.t=setTimeout(()=>{if(page==="devices"&&!devEdit)loadDevices()},600)}
  else if(page==="cases"&&!caseOpen)devSeen.t=setTimeout(()=>{if(page==="cases"&&!caseOpen)loadCases()},1500)}
async function liveResync(){
  try{const cs=await api("/cases/open?pending_only=true");alarmSet(cs.filter(c=>!c.is_test).map(c=>c.id))}catch(e){}
  if(page==="cases"&&!caseOpen&&!devEdit)loadCases();else if(page==="map")loadMap?.();else if(!devEdit&&page==="devices")loadDevices();else if(!devEdit&&page==="gateways")loadGateways()}

// ---------------------------------------------------------------- alarm sound
// One AudioContext, unlocked by the first click/tap/key on the page (browsers block sound before that).
// While any new case is unanswered the siren repeats every 3 s, until the case is accepted/closed or someone
// presses "หยุดเสียง 2 นาที". The on/off choice is remembered per browser (on by default).
const SND={ctx:null,timer:0,muteUntil:0,cases:new Set()};
function sndCtx(){if(!SND.ctx){const A=window.AudioContext||window.webkitAudioContext;if(!A)return null;try{SND.ctx=new A()}catch(e){return null}}return SND.ctx}
const sndReady=()=>SND.ctx?.state==="running";
function sndUnlock(){const c=sndCtx();if(c&&c.state!=="running")c.resume().then(()=>{sndUI();alarmSync()}).catch(()=>{})}
["pointerdown","keydown","touchend"].forEach(t=>document.addEventListener(t,sndUnlock,{capture:true,passive:true}));
function tones(seq,vol=.22){const c=sndCtx();if(!c||c.state!=="running")return false;let t=c.currentTime+.02;
  for(const [f,d] of seq){const o=c.createOscillator(),g=c.createGain();o.type="triangle";o.frequency.value=f;
    g.gain.setValueAtTime(.0001,t);g.gain.exponentialRampToValueAtTime(vol,t+.02);g.gain.setValueAtTime(vol,t+d-.04);g.gain.exponentialRampToValueAtTime(.0001,t+d);
    o.connect(g).connect(c.destination);o.start(t);o.stop(t+d+.02);t+=d}return true}
const siren=()=>tones([[988,.28],[740,.28],[988,.28],[740,.28],[988,.28],[740,.28]],.28);
function beep(){if(audioOn)tones([[880,.12],[1175,.18]],.18)}            // short chime: sound switched on, test press
function alarmSet(ids){SND.cases=new Set(ids);alarmSync()}
function alarmAdd(id){SND.cases.add(+id);alarmSync()}
function alarmDrop(id){SND.cases.delete(+id);alarmSync()}
function alarmSync(){const ring=audioOn&&SND.cases.size>0&&Date.now()>=SND.muteUntil;
  if(ring&&!SND.timer){siren();SND.timer=setInterval(()=>{if(Date.now()<SND.muteUntil)return;siren()},3000)}
  else if(!ring&&SND.timer){clearInterval(SND.timer);SND.timer=0}
  if(SND.muteUntil&&Date.now()>=SND.muteUntil){SND.muteUntil=0}sndUI()}
function alarmMute(min=2){SND.muteUntil=Date.now()+min*60000;alarmSync();toast(`หยุดเสียงเตือน ${min} นาที เคสยังรอรับเรื่องอยู่`,"info");setTimeout(alarmSync,min*60000+200)}
function sndUI(){const b=$("#snd");if(!b)return;const ringing=audioOn&&SND.cases.size>0&&Date.now()>=SND.muteUntil,locked=audioOn&&!sndReady();
  const [ic,lbl,cls,tip]=!audioOn?["volx","เปิดเสียงเตือน","","เสียงเตือนปิดอยู่ — กดเพื่อเปิด"]
    :ringing?["vol","หยุดเสียง 2 นาที","ring","มีเคสใหม่รอรับเรื่อง — กดเพื่อหยุดเสียงชั่วคราว"]
    :locked?["volx","แตะเพื่อเปิดเสียง","lock","เบราว์เซอร์ยังไม่อนุญาตให้เล่นเสียง — คลิกที่ใดก็ได้บนหน้านี้หนึ่งครั้ง"]
    :SND.muteUntil>Date.now()?["volx",`หยุดเสียงถึง ${new Date(SND.muteUntil).toLocaleTimeString("th-TH",{hour:"2-digit",minute:"2-digit"})}`,"","กดเพื่อให้เสียงกลับมาดังทันที"]
    :["vol","เสียงเปิด","on","เสียงเตือนเปิดอยู่ — กดเพื่อปิด"];
  b.className="btn snd "+cls;b.title=tip;b.innerHTML=`${ico(ic)}<span class="lbl">${lbl}</span>`}
function sndClick(){sndUnlock();
  if(!audioOn){audioOn=true;lsSet("snd","1");setTimeout(()=>{beep();alarmSync()},60);return}
  if(SND.cases.size&&Date.now()>=SND.muteUntil){alarmMute(2);return}
  if(SND.muteUntil>Date.now()){SND.muteUntil=0;alarmSync();return}
  audioOn=false;lsSet("snd","0");alarmSync()}
setInterval(sndUI,30000);
function liveEvent(m){if((m.type==="ping"||m.type==="net_event")&&netDev&&$("#netc")&&m.dev_eui===netDev.dev_eui){clearTimeout(window._netT);window._netT=setTimeout(()=>netCard(netDev),800);if(m.type==="ping"&&m.status==="acked")toast("ปุ่มยืนยันรับข้อความทดสอบแล้ว ✅","ok")}
  if(m.type==="net_event"||m.type==="ping"){if(page==="netlive"&&window._netLive)window._netLive(m);return}
  if(m.type==="device_seen"){devSeen(m);return}onEvt(m).catch(()=>{});if(m.type==="gateway_cmd"&&page==="gateways"&&window._gwDraw)window._gwDraw().catch(()=>{});if(m.type==="gateway_state"){if("maint" in m)toast(m.maint?"เกตเวย์เข้าโหมดซ่อมบำรุง":"เกตเวย์ออกจากโหมดซ่อมบำรุง","info");else toast(m.state==="offline"?"เกตเวย์ขาดการเชื่อมต่อ":"เกตเวย์กลับมาออนไลน์",m.state==="offline"?"warn":"ok");if(page==="gateways"&&!devEdit)loadGateways()}if(m.type==="case_test"){toast("ได้รับสัญญาณทดสอบปุ่ม","info");if(page==="selftest")loadSelftest()}if(page==="cases"){if(caseOpen){if(m.case_id===caseOpen&&(m.type==="case_msg"||m.type==="case_update"||m.type==="case_repeat")&&!(document.activeElement&&/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)))showCase(caseOpen)}else loadCases()}if(page==="map"&&(m.type==="case_new"||m.type==="case_update"))loadMap()}

const LOGO=(s=36)=>`<svg width="${s}" height="${s}" viewBox="0 0 64 64" aria-hidden="true"><rect x="2" y="2" width="60" height="60" rx="16" fill="#e53935"/><path d="M27 17h10v10h10v10H37v10H27V37H17V27h10z" fill="#fff"/><path d="M47 9a14 14 0 0 1 8 8M50 4a20 20 0 0 1 10 10" stroke="#fff" stroke-width="3.5" fill="none" stroke-linecap="round" opacity=".85"/></svg>`;
const ICON={cases:"🚨",map:"🗺️",patients:"🧑‍🦳",caregivers:"🧑‍⚕️",devices:"📟",selftest:"🧪",gateways:"📡",netlive:"📶",reports:"📊",privacy:"🔒",install:"🔌",chirpstack:"🛰️",users:"👥",update:"⬆️",integrations:"🔗",settings:"⚙️",audit:"📜",account:"👤"};
const LS={get:k=>{try{return localStorage.getItem(k)||""}catch{return""}},set:(k,v)=>{try{localStorage.setItem(k,v)}catch{}}};
function authShell(inner){setTimeout(thmInit);$("#app").innerHTML=`<div class="auth"><button class="thm" id="thm" type="button"></button><div class="auth-side">${LOGO(52)}<h2>ระบบแจ้งเหตุฉุกเฉิน</h2><p>ปุ่มแจ้งเหตุสำหรับผู้ป่วยติดบ้าน ส่งเรื่องถึงเจ้าหน้าที่ รพ.สต. และ อสม. ในพื้นที่ทันที</p></div>${inner}</div>`}
function busyBtn(b,on,label){b.disabled=on;b.innerHTML=on?`<span class="spin"></span> กำลังดำเนินการ…`:label}
function eyeToggle(btn,input){btn.onclick=()=>{const s=input.type==="password";input.type=s?"text":"password";btn.textContent=s?"🙈":"👁";btn.setAttribute("aria-label",s?"ซ่อนรหัสผ่าน":"แสดงรหัสผ่าน");input.focus()}}
function capsWatch(input,el){const f=e=>{if(e.getModifierState)el.hidden=!e.getModifierState("CapsLock")};input.addEventListener("keydown",f);input.addEventListener("keyup",f)}
// wide tables scroll sideways on phones instead of squashing the page
new MutationObserver(()=>document.querySelectorAll("main table").forEach(t=>{if(!t.parentElement.classList.contains("tw")){const w=document.createElement("div");w.className="tw";t.replaceWith(w);w.appendChild(t)}})).observe(document.documentElement,{childList:true,subtree:true});

let wsUp=false;
const nowTH=()=>new Date().toLocaleString("th-TH",{weekday:"short",day:"numeric",month:"short",year:"numeric",hour:"2-digit",minute:"2-digit"});
function setConn(up){wsUp=up;const c=$("#conn");if(c)c.innerHTML=`<span class="sdot ${up?"on":"off"}"></span>${up?"เชื่อมต่อสด":"ขาดการเชื่อมต่อ — กำลังลองใหม่"}`}
setInterval(()=>{const c=$("#clk");if(c)c.textContent=nowTH()},20000);
function dur(value){
  if(value==null)return "-";
  const seconds=Math.round(value);
  if(seconds<60)return seconds+"<small>วินาที</small>";
  return Math.floor(seconds/60)+":"+String(seconds%60).padStart(2,"0")+"<small>นาที</small>";
}

function render(){
  if(page==="cases"){devEdit=false;caseOpen=0}
  if(token&&sessionStorage.getItem("mc")==="1"){pwScreen(true);return}
  if(token&&sessionStorage.getItem("ms")==="1"){mfaSetup(true);return}
  if(!token){
    if(sessionStorage.getItem("mt")){mfaLogin(sessionStorage.getItem("mt"));return}
    authShell(`<form class="auth-card" id="lf" novalidate>
      <div class="auth-logo">${LOGO(64)}</div>
      <h1>เข้าสู่ระบบ</h1><p class="sub">สำหรับเจ้าหน้าที่และผู้ดูแลระบบ</p>
      <div class="err" id="lerr" role="alert" hidden></div>
      <label class="fld"><span>ชื่อผู้ใช้</span><input id="u" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required></label>
      <label class="fld"><span>รหัสผ่าน</span><div class="pwbox"><input id="p" name="password" type="password" autocomplete="current-password" required><button type="button" class="eye" id="eye" aria-label="แสดงรหัสผ่าน">👁</button></div></label>
      <div class="caps" id="caps" hidden>⇪ Caps Lock เปิดอยู่</div>
      <button class="btn p big" id="go" type="submit">เข้าสู่ระบบ</button>
      <p class="foot">ใช้งานเฉพาะผู้ได้รับอนุญาต · การเข้าใช้งานทุกครั้งถูกบันทึกตาม PDPA</p></form>`);
    const u=$("#u"),p=$("#p"),err=$("#lerr");u.value=LS.get("lu");(u.value?p:u).focus();eyeToggle($("#eye"),p);capsWatch(p,$("#caps"));
    $("#lf").onsubmit=async e=>{e.preventDefault();err.hidden=true;
      if(!u.value.trim()||!p.value){err.textContent="กรอกชื่อผู้ใช้และรหัสผ่าน";err.hidden=false;return}
      busyBtn($("#go"),true);
      try{const r=await api("/login",{method:"POST",body:{username:u.value.trim(),password:p.value}});
        LS.set("lu",u.value.trim());if(r.mfa_required){mfaLogin(r.ticket);return}
        if(r.mfa_setup)sessionStorage.setItem("ms","1");
        token=r.token;role=r.role;sessionStorage.setItem("tk",token);sessionStorage.setItem("role",role);sessionStorage.setItem("nm",r.name||u.value.trim());LS.set("lu",u.value.trim());
        if(r.must_change)sessionStorage.setItem("mc","1");else sessionStorage.removeItem("mc");render()}
      catch(x){const m=/bad credentials/.test(x.message)?"ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง":/locked/.test(x.message)?"บัญชีถูกล็อกชั่วคราว 15 นาที เพราะใส่รหัสผิดหลายครั้ง":/Failed to fetch|NetworkError/.test(x.message)?"ติดต่อเซิร์ฟเวอร์ไม่ได้ — ตรวจอินเทอร์เน็ต":x.message;
        err.textContent=m;err.hidden=false;busyBtn($("#go"),false,"เข้าสู่ระบบ");p.select()}};
    return}
  const tabs=[["cases","เคสฉุกเฉิน"],["map","แผนที่"],["patients","ผู้ป่วย"],["caregivers","อสม./ผู้ดูแล"],["ready","ความพร้อมข้อมูล"],["devices","อุปกรณ์"],["selftest","ทดสอบปุ่ม"],["gateways","เกตเวย์"],...(role!=="viewer"?[["netlive","เฟรม LoRaWAN สด"]]:[]),["reports","รายงาน"],...(role!=="viewer"?[["privacy","PDPA"]]:[]),...(role==="admin"?[["install","ติดตั้งอุปกรณ์"],["chirpstack","ChirpStack"],["integrations","เชื่อมต่อ LINE/AI"],["users","ผู้ใช้"],["sysh","สถานะระบบ"],["update","อัปเดตระบบ"]]:[]),["settings","ตั้งค่า"],["audit","บันทึกการใช้งาน"]];
  const RN={admin:"ผู้ดูแลระบบ",staff:"เจ้าหน้าที่",viewer:"ดูอย่างเดียว"};
  const NG=[["งานประจำวัน",["cases","map","selftest"]],["ข้อมูล",["patients","caregivers","ready","devices","gateways","netlive","reports"]]],inG=k=>NG.some(g=>g[1].includes(k));
  const groups=[...NG.map(g=>[g[0],tabs.filter(t=>g[1].includes(t[0]))]),["ระบบ",tabs.filter(t=>!inG(t[0]))]].filter(g=>g[1].length);
  const navb=t=>`<button data-p="${t[0]}" class="${page===t[0]?"on":""}"${page===t[0]?' aria-current="page"':""}>${IC[t[0]]?ico(t[0]):""}<span>${t[1]}</span></button>`;
  const title=(tabs.find(t=>t[0]===page)||["","บัญชีของฉัน"])[1],curG=groups.find(g=>g[1].some(t=>t[0]===page));
  const navC=new Set(JSON.parse(lsGet("navc")||"[]"));if(curG)navC.delete(curG[0]);
  const ngrp=(g,i)=>{const open=!navC.has(g[0]);return `<div class="ngw"><button type="button" class="ng" data-g="${esc(g[0])}" aria-expanded="${open}" aria-controls="ng${i}"><span>${g[0]}</span>${g[0]===curG?.[0]?"":`<em>${g[1].length}</em>`}${ico("chev")}</button><div class="ngi" id="ng${i}" ${open?"":"hidden"}>${g[1].map(navb).join("")}</div></div>`};
  const bc=`<nav class="bc" aria-label="ตำแหน่งหน้า"><ol><li><button type="button" data-bp="cases">หน้าหลัก</button></li>${curG?`<li class="bcg">${curG[0]}</li>`:""}<li id="bccur" aria-current="page"><span>${title}</span></li></ol></nav>`;
  $("#app").innerHTML=`<div class="shell" id="shell">
   <aside id="side" aria-label="เมนู"><div class="sbrand">${LOGO(40)}<div><b>SOS LoRa</b><span>ศูนย์แจ้งเหตุฉุกเฉิน</span></div></div>
    <nav>${groups.map(ngrp).join("")}</nav>
    <div class="me"><div class="who"><span class="av">${esc((sessionStorage.getItem("nm")||"?").trim().charAt(0).toUpperCase())}</span><div><b>${esc(sessionStorage.getItem("nm")||"")}</b><span>${RN[role]||role}</span></div></div><div class="row"><button class="btn" id="pw">บัญชี</button><button class="btn" id="out">ออกจากระบบ</button></div></div></aside>
   <header class="top"><button class="ham" id="ham" aria-label="เปิดเมนู">${ico("menu")}</button><div class="ptw">${bc}<h1 class="pt" id="ptitle">${title}</h1></div>
    <div class="phr"><span class="hchip" id="conn"><span class="sdot ${wsUp?"on":"off"}"></span>${wsUp?"เชื่อมต่อสด":"กำลังเชื่อมต่อ…"}</span><span class="hchip tm" id="clk">${nowTH()}</span>
    <div class="ncw"><button class="btn icn" id="nc" type="button" aria-haspopup="true" aria-expanded="false" aria-controls="ncp" title="การแจ้งเตือน">${ico("bell")}<span class="ncb" id="ncb" hidden></span></button><div class="ncp" id="ncp" hidden></div></div>
    <button class="btn thm" id="thm" type="button"></button>
    <button class="btn snd ${audioOn?"on":""}" id="snd" title="เสียงเตือนเคสใหม่">${ico(audioOn?"vol":"volx")}<span class="lbl">${audioOn?"เสียงเปิด":"เปิดเสียงเตือน"}</span></button></div></header>
   <div class="scrim" id="scrim"></div><main><div id="main"></div></main></div>`;
  const go=async p=>{if(setDirty()&&!await ask("มีการเปลี่ยนแปลงที่ยังไม่ได้บันทึก ออกจากหน้านี้?"))return;setDirty=()=>false;page=p;render();scrollTo(0,0)};
  document.querySelectorAll("#side nav button[data-p]").forEach(b=>b.onclick=()=>go(b.dataset.p));
  document.querySelectorAll(".bc [data-bp]").forEach(b=>b.onclick=()=>go(b.dataset.bp));
  document.querySelectorAll("#side .ng").forEach(b=>b.onclick=()=>{const box=b.nextElementSibling,open=box.hidden;box.hidden=!open;b.setAttribute("aria-expanded",open);
    const c=new Set(JSON.parse(lsGet("navc")||"[]"));open?c.delete(b.dataset.g):c.add(b.dataset.g);lsSet("navc",JSON.stringify([...c]))});
  // sub-views (case detail, edit forms) add a last breadcrumb item via crumb(); it goes away when #main no longer shows a "back" view
  new MutationObserver(()=>{if(!$("#main #back"))crumb(null)}).observe($("#main"),{childList:true});
  if(page==="cases"){
    const search=document.createElement("form");
    search.className="case-search";
    search.setAttribute("role","search");
    search.innerHTML='<input type="search" aria-label="ค้นหาประวัติเคส" placeholder="ค้นหาผู้ป่วย ปุ่ม หรือเลขเคส">';
    search.onsubmit=e=>{
      e.preventDefault();
      CH=historyFilters(search.elements[0].value.trim());
      caseOpen=0;
      caseHistory();
    };
    $(".phr").before(search);
  }
  ncInit();ncSeed();thmInit();fwLatest();
  $("#ham").onclick=()=>$("#shell").classList.add("nav-open");$("#scrim").onclick=()=>$("#shell").classList.remove("nav-open");
  $("#out").onclick=logout;$("#pw").onclick=()=>{page="account";render()};$("#snd").onclick=sndClick;sndUI();
  clearInterval(timer);(({cases:loadCases,map:loadMap,patients:loadPatients,caregivers:loadCaregivers,devices:loadDevices,selftest:loadSelftest,gateways:loadGateways,netlive:loadNetLive,reports:loadReports,privacy:loadPrivacy,users:loadUsers,install:loadInstall,chirpstack:loadChirpstack,integrations:loadIntegrations,update:loadUpdate,ready:loadReady,sysh:loadSysHealth,account:loadAccount,settings:loadSettings,audit:loadAudit})[page])();
  if(page==="cases"||page==="devices"||page==="gateways"||page==="netlive"){let pt=0;timer=setInterval(()=>{   // live events redraw these pages; polling is the fallback: every 15 s while the link is down, every 60 s as a safety net otherwise
    if(page!=="netlive"&&wsUp&&++pt%4)return;
    if(page==="cases"){if(!caseOpen&&!devEdit)loadCases()}else if(page==="netlive")loadNetLive();else if(!devEdit){if(page==="devices")loadDevices();else loadGateways()}},page==="netlive"?30000:15000)}
  if(!ws||ws.readyState>1)connectWS()}

// day / night theme: light by default, choice remembered per browser
const isDark=()=>document.documentElement.dataset.theme==="dark";
function thmInit(){const b=$("#thm");if(!b)return;const d=isDark();b.innerHTML=ico(d?"sun":"moon");
  b.title=b.ariaLabel=d?"เปลี่ยนเป็นโหมดกลางวัน":"เปลี่ยนเป็นโหมดกลางคืน";b.setAttribute("aria-pressed",d);
  b.onclick=()=>{const t=isDark()?"light":"dark";document.documentElement.dataset.theme=t;lsSet("theme",t);
    document.querySelector('meta[name=theme-color]')?.setAttribute("content",t==="dark"?"#12141c":"#4c5fd5");thmInit()}}
function crumb(label){const p=$("#bccur"),pt=$("#ptitle");if(!p)return;const base=(p.dataset.base??=p.querySelector("span").textContent);
  document.querySelectorAll(".bc .bcx").forEach(e=>e.remove());
  if(label==null){p.innerHTML=`<span>${esc(base)}</span>`;p.setAttribute("aria-current","page");if(pt)pt.textContent=page==="cases"?"ภาพรวมศูนย์แจ้งเหตุ":base;return}
  p.removeAttribute("aria-current");p.innerHTML=`<button type="button">${esc(base)}</button>`;p.querySelector("button").onclick=()=>{setDirty=()=>false;render();scrollTo(0,0)};
  p.insertAdjacentHTML("afterend",`<li class="bcx" aria-current="page"><span>${esc(label)}</span></li>`);if(pt)pt.textContent=label}
let devEdit=false;
const canEdit=()=>role==="admin"||role==="staff";

