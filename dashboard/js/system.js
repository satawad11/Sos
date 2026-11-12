"use strict";
// system update, two-step sign-in, monthly button self-test
// Loaded by index.html as plain scripts, in order: core, cases, people, network, system, install, admin (they share one global scope).
// ---------------- system update (GitHub) — admin only; the host updater does the work (docs/UPDATE.md) ----------------
let upSawBusy=false;
const UPST={idle:["ok","พร้อม"],done:["ok","สำเร็จ"],failed:["bad","ไม่สำเร็จ"],queued:["mut","รอคิว"],checking:["mut","กำลังตรวจ…"],updating:["mut","กำลังอัปเดต…"],rolling_back:["bad","กำลังย้อนกลับ…"]};
const verLine=v=>v?`<code>${esc(v.short)}</code> ${esc(v.subject)} <span class="mut">· ${fmt(v.date)}</span>`:'<span class="mut">-</span>';
/* ---- update progress: steps reported by infra/scripts/sos-update.sh (status.step), estimated fill inside a step ---- */
const UPS=["ตรวจเวอร์ชันบน GitHub","สำรองฐานข้อมูล","ดึงโค้ดใหม่","Build และเริ่มระบบใหม่","ตรวจว่าระบบทำงาน"];
const UPB=[0,4,14,20,82,100],UPT=[6,25,6,100,30];   // % at start of each step, typical seconds per step
let upLast=null,upPct=0,upTick=0,upOffline=false;
function upStep(st,queued){
  if(st.state==="done")return 6;if(queued)return 0;
  if(st.step)return st.step;const m=st.message||"";   // older updater without step numbers
  return /ตรวจว่าระบบ/.test(m)?5:/build/i.test(m)?4:/ดึงโค้ด/.test(m)?3:/สำรอง/.test(m)?2:1}
function upPctOf(st,k){
  if(k>=6)return 100;if(k<1)return 1;
  const t0=Date.parse(st.step_at||st.updated_at||"")||Date.now(),el=Math.max(0,(Date.now()-t0)/1000);
  return UPB[k-1]+(UPB[k]-UPB[k-1])*0.92*(1-Math.exp(-el/UPT[k-1]))}
const mmss=sec=>{sec=Math.max(0,Math.round(sec));return Math.floor(sec/60)+":"+String(sec%60).padStart(2,"0")};
function upProgHTML(st,queued){
  const k=upStep(st,queued),fail=st.state==="failed",rb=st.state==="rolling_back",done=st.state==="done";
  const t0=Date.parse(st.started_at||"")||0,t1=Date.parse(st.finished_at||"")||Date.now(),el=t0?mmss((t1-t0)/1000):"";
  const head=done?"อัปเดตสำเร็จ":fail?"อัปเดตไม่สำเร็จ":rb?"กำลังย้อนกลับเวอร์ชันเดิม":queued?"รอตัวอัปเดตบนเซิร์ฟเวอร์รับคำขอ…":`ขั้นที่ ${k} จาก 5 · ${UPS[k-1]}`;
  return `<div class="upg ${done?"ok":fail||rb?"bad":""}" id="upprog" role="status" aria-live="polite">
    <div class="upg-h"><b>${head}</b><span><span id="uppct">${Math.round(fail||rb?upPct:upPctOf(st,k))}%</span>${el?` · <span id="upel">${done||fail?"ใช้เวลา":"ผ่านไป"} ${el}</span>`:""}</span></div>
    <div class="upg-bar"><span id="upbar" style="width:${upPct}%"></span></div>
    <ol class="upg-s">${UPS.map((n,i)=>{const j=i+1,c=j<k||done?"d":j===k?(fail||rb?"x":"a"):"";return `<li class="${c}"><i></i><span>${n}</span></li>`}).join("")}</ol>
    <p class="upg-m" id="upmsg">${upOffline&&!done&&!fail?"ระบบกำลังเริ่มใหม่ หน้าเว็บจะกลับมาเองเมื่อพร้อม…":esc(st.message||"")}</p>
    ${done?'<div class="row"><button class="btn g" id="uprl">โหลดหน้าใหม่เพื่อใช้เวอร์ชันใหม่</button></div>':""}</div>`}
function upAnimate(st,queued){   // move the bar from its last width to the new one so the transition shows
  const bar=$("#upbar");if(!bar)return;const k=upStep(st,queued),bad=st.state==="failed"||st.state==="rolling_back";
  const to=bad?upPct:Math.max(upPct,upPctOf(st,k));requestAnimationFrame(()=>{bar.style.width=to+"%"});upPct=to;
  const pc=$("#uppct");if(pc)pc.textContent=Math.round(to)+"%";
  if(st.started_at&&!st.finished_at){const e=$("#upel");if(e)e.textContent="ผ่านไป "+mmss((Date.now()-Date.parse(st.started_at))/1000)}}
function upTicker(on){clearInterval(upTick);upTick=0;if(on)upTick=setInterval(()=>{if(page!=="update"||!upLast){clearInterval(upTick);return}upAnimate(upLast.st,upLast.q)},1000)}
async function loadUpdate(){
  if(role!=="admin"){$("#main").innerHTML='<div class="warn">เฉพาะผู้ดูแลระบบ</div>';return}
  clearTimeout(timer);let s;
  try{s=await api("/system/update")}
  catch(e){if(page!=="update")return;
    if(upSawBusy&&upLast){upOffline=true;const st={...upLast.st};if((st.step||0)<4)st.step=4;
      $("#main").innerHTML=`<div class="card"><h3>อัปเดตระบบ</h3>${upProgHTML(st,false)}</div>`;upLast={st,q:false};upAnimate(st,false);upTicker(true)}
    else $("#main").innerHTML=`<div class="card"><h3>อัปเดตระบบ</h3><p>ติดต่อเซิร์ฟเวอร์ไม่ได้ (${esc(e.message)})</p></div>`;
    timer=setTimeout(()=>{if(page==="update")loadUpdate()},4000);return}
  if(page!=="update")return;
  const st=s.status||{},busy=s.busy,state=s.pending?"queued":(st.state||"idle"),[cls,lbl]=UPST[state]||["mut",state];
  if(busy)upSawBusy=true;
  const finished=!busy&&upSawBusy&&(state==="done"||state==="failed");upOffline=false;
  const showProg=(busy&&(st.request?.action==="update"||state==="updating"||state==="rolling_back"||state==="queued"))||finished;
  if(!showProg)upPct=0;
  if(!s.installed){$("#main").innerHTML=`<div class="card"><h3>อัปเดตระบบ</h3><div class="warn">ยังไม่ได้ติดตั้งตัวอัปเดตบนเซิร์ฟเวอร์ — ให้ผู้ดูแลเซิร์ฟเวอร์รัน <code>sudo bash infra/scripts/setup-updater.sh</code> หนึ่งครั้ง (ดู docs/UPDATE.md)</div></div>`;return}
  const ch=st.changes||[],n=st.behind||0,lu=st.last_update;
  $("#main").innerHTML=`<div class="card"><h3>อัปเดตระบบ <span class="mut" style="font-weight:normal">(โค้ดจาก GitHub · สาขา ${esc(st.branch||"main")})</span></h3>
   <div class="row"><b>เวอร์ชันที่ใช้อยู่:</b> ${verLine(st.current)}</div>
   <div class="row"><b>ล่าสุดบน GitHub:</b> ${verLine(st.latest)}</div>
   <div class="row mut">ตรวจล่าสุด ${fmt(st.checked_at)}${lu?` · อัปเดตครั้งก่อน ${fmt(lu.at)} <code>${esc(lu.from.slice(0,7))}</code> → <code>${esc(lu.to.slice(0,7))}</code> ${lu.ok?'<span class="ok">สำเร็จ</span>':'<span class="bad">ไม่สำเร็จ (ย้อนกลับ)</span>'}`:""}</div>
   ${showProg?upProgHTML(st,state==="queued"):`<div class="row"><b>สถานะ:</b> <span class="${cls}">${esc(lbl)}</span> ${esc(st.message||"")}</div>`}
   <div class="row"><button class="btn" id="upchk" ${busy?"disabled":""}>ตรวจหาอัปเดต</button>
     <button class="btn p" id="upgo" ${busy||!n?"disabled":""}>อัปเดตตอนนี้${n?` (${n} รายการ)`:""}</button></div>
   ${n?`<h4>มีอะไรใหม่ (${n})</h4><ul>${ch.map(c=>`<li><code>${esc(c.short)}</code> ${esc(c.subject)} <span class="mut">· ${esc(c.author)} · ${fmt(c.date)}</span></li>`).join("")}</ul>`:'<p class="mut">ไม่มีเวอร์ชันใหม่ (กด "ตรวจหาอัปเดต" เพื่อเช็กกับ GitHub อีกครั้ง — ระบบเช็กเองทุก 6 ชั่วโมง)</p>'}
   <p class="mut">ขั้นตอนอัปเดต: สำรองฐานข้อมูล → ดึงโค้ด → build ใหม่ → ตรวจว่าระบบทำงาน — ถ้าไม่ผ่าน ระบบจะ<b>ย้อนกลับเวอร์ชันเดิมให้อัตโนมัติ</b> · ระหว่างอัปเดต (ประมาณ 1–5 นาที) หน้าเว็บและการส่ง LINE จะหยุดชั่วคราว ควรทำนอกช่วงที่มีเคสค้าง</p>
   <details ${busy||state==="failed"?"open":""}><summary>บันทึกการทำงาน (log)</summary><pre style="white-space:pre-wrap;max-height:320px;overflow:auto;font-size:12px;background:#0f1720;color:#d6e2ee;padding:8px;border-radius:8px">${esc((s.log||[]).join("\n"))||"-"}</pre></details></div>`;
  if($("#uprl"))$("#uprl").onclick=()=>location.reload();
  const send=action=>guard(async()=>{await api("/system/update",{method:"POST",body:{action}});upSawBusy=action==="update";if(upSawBusy)upPct=0;toast(action==="update"?"ส่งคำขออัปเดตแล้ว":"กำลังตรวจกับ GitHub…");loadUpdate()})();
  $("#upchk").onclick=()=>send("check");
  $("#upgo").onclick=guard(async()=>{
    const open=(await api("/cases?state=new")).length;
    if(!await ask(`อัปเดตเป็น ${st.latest?st.latest.short:"เวอร์ชันล่าสุด"} (${n} รายการ)?\n\nระหว่างอัปเดตประมาณ 1–5 นาที หน้าเว็บและการแจ้ง LINE จะหยุดชั่วคราว`+(open?`\n\n⚠️ ขณะนี้มีเคสรอรับเรื่อง ${open} เคส — แนะนำให้จัดการก่อน`:"")+"\n\nระบบจะสำรองฐานข้อมูลก่อน และย้อนกลับเองถ้าอัปเดตไม่ผ่าน"))return;
    send("update")});
  if(showProg){upLast={st,q:state==="queued"};upAnimate(st,state==="queued")}else upLast=null;
  upTicker(showProg&&busy);
  if(busy)timer=setTimeout(()=>{if(page==="update")loadUpdate()},3000);
}

// ---------------- two-step sign-in (TOTP) ----------------
function mfaLogin(ticket){
  sessionStorage.setItem("mt",ticket);let backup=false;
  authShell(`<form class="auth-card" id="mf" novalidate><div class="auth-logo"><div class="shield">🔐</div></div>
    <h1>ยืนยันตัวตนขั้นที่ 2</h1><p class="sub" id="msub">เปิดแอป Authenticator ในมือถือ แล้วกรอกรหัส 6 หลัก</p>
    <div class="err" id="merr" role="alert" hidden></div>
    <label class="fld"><span id="mlbl">รหัส 6 หลัก</span><input id="mcode" class="otp" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="••••••"></label>
    <button class="btn p big" id="mgo" type="submit">ยืนยัน</button>
    <div class="alt" style="margin-top:12px"><button type="button" class="btn" id="mbk">ใช้รหัสสำรอง</button><button type="button" class="btn" id="mcx">← กลับ</button></div>
    <p class="foot">รหัสเปลี่ยนทุก 30 วินาที · ทำรายการภายใน 5 นาที</p></form>`);
  const c=$("#mcode"),err=$("#merr");c.focus();
  c.oninput=()=>{if(!backup){c.value=c.value.replace(/\D/g,"").slice(0,6);if(c.value.length===6)$("#mf").requestSubmit()}};
  $("#mbk").onclick=()=>{backup=!backup;c.value="";c.maxLength=backup?9:6;c.inputMode=backup?"text":"numeric";c.placeholder=backup?"XXXX-XXXX":"••••••";c.classList.toggle("otp",!backup);
    $("#mlbl").textContent=backup?"รหัสสำรอง (ใช้ได้ครั้งเดียว)":"รหัส 6 หลัก";$("#mbk").textContent=backup?"ใช้รหัสจากแอป":"ใช้รหัสสำรอง";c.focus()};
  $("#mcx").onclick=()=>{sessionStorage.removeItem("mt");render()};
  $("#mf").onsubmit=async e=>{e.preventDefault();err.hidden=true;if(!c.value.trim())return;busyBtn($("#mgo"),true);
    try{const r=await api("/login/2fa",{method:"POST",body:{ticket,code:c.value}});sessionStorage.removeItem("mt");
      token=r.token;role=r.role;sessionStorage.setItem("tk",token);sessionStorage.setItem("role",role);sessionStorage.setItem("nm",r.name);
      if(r.must_change)sessionStorage.setItem("mc","1");render();
      if(r.backup_codes_left<=3&&backup)setTimeout(()=>toast(`เหลือรหัสสำรอง ${r.backup_codes_left} ชุด — สร้างใหม่ที่ "บัญชีของฉัน"`),600)}
    catch(x){if(/หมดเวลา|ล็อก|locked/.test(x.message)){sessionStorage.removeItem("mt")}
      err.textContent=/locked/.test(x.message)?"บัญชีถูกล็อกชั่วคราว 15 นาที เพราะใส่รหัสผิดหลายครั้ง":x.message;err.hidden=false;busyBtn($("#mgo"),false,"ยืนยัน");c.select();
      if(/หมดเวลา|locked/.test(x.message))setTimeout(render,2500)}}}
async function mfaSetup(forced){
  let s;try{s=await api("/me/2fa/setup",{method:"POST"})}catch(e){if(/เปิดใช้งานอยู่แล้ว/.test(e.message)){sessionStorage.removeItem("ms");render();return}toast(e.message);return}
  authShell(`<form class="auth-card wide" id="ef" novalidate><div class="auth-logo"><div class="shield">🔐</div></div>
    <h1>ตั้งค่าการยืนยันตัวตน 2 ขั้น</h1><p class="sub">${forced?"ผู้ดูแลระบบกำหนดให้บัญชีนี้ต้องใช้ — ทำครั้งเดียว ใช้เวลาประมาณ 2 นาที":"เพิ่มความปลอดภัย: แม้รหัสผ่านหลุดก็เข้าบัญชีไม่ได้"}</p>
    <ol class="steps"><li>ติดตั้งแอป <b>Google Authenticator</b> หรือ <b>Microsoft Authenticator</b> ในมือถือ</li>
    <li>กด ➕ ในแอป แล้ว<b>สแกน QR</b> นี้<div class="qr">${s.qr_svg}</div><details><summary>สแกนไม่ได้? ใส่รหัสเอง</summary><code class="totpsec">${esc(s.secret.replace(/(.{4})/g,"$1 ").trim())}</code></details></li>
    <li>กรอกรหัส 6 หลักที่แอปแสดง</li></ol>
    <div class="err" id="eerr" role="alert" hidden></div>
    <label class="fld"><input id="ecode" class="otp" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="••••••"></label>
    <button class="btn p big" id="ego" type="submit">ยืนยันและเปิดใช้งาน</button>
    <div class="alt" style="margin-top:10px">${forced?'<button type="button" class="btn" id="eout">ออกจากระบบ</button>':'<button type="button" class="btn" id="ecx">ยกเลิก</button>'}</div></form>`);
  const c=$("#ecode");c.focus();c.oninput=()=>{c.value=c.value.replace(/\D/g,"").slice(0,6)};
  if(forced)$("#eout").onclick=logout;else $("#ecx").onclick=()=>render();
  $("#ef").onsubmit=async e=>{e.preventDefault();const err=$("#eerr");err.hidden=true;busyBtn($("#ego"),true);
    try{const r=await api("/me/2fa/enable",{method:"POST",body:{code:c.value}});token=r.token;sessionStorage.setItem("tk",token);sessionStorage.removeItem("ms");showBackupCodes(r.backup_codes,true)}
    catch(x){err.textContent=x.message;err.hidden=false;busyBtn($("#ego"),false,"ยืนยันและเปิดใช้งาน");c.select()}}}
function showBackupCodes(codes,first){
  authShell(`<div class="auth-card wide"><div class="auth-logo"><div class="shield ok">✅</div></div>
    <h1>${first?"เปิดใช้งานแล้ว":"รหัสสำรองชุดใหม่"}</h1><p class="sub">เก็บ<b>รหัสสำรอง</b>ไว้ที่ปลอดภัย (พิมพ์หรือจดเก็บ) ใช้แทนรหัสจากแอปเมื่อมือถือหาย — แต่ละรหัสใช้ได้ครั้งเดียว และจะไม่แสดงอีก</p>
    <div class="codes">${codes.map(c=>`<code>${esc(c)}</code>`).join("")}</div>
    <div class="alt"><button class="btn" id="bcp">📋 คัดลอก</button><button class="btn" id="bdl">⬇️ ดาวน์โหลด</button></div>
    <label class="row" style="margin-top:14px"><input type="checkbox" id="bok"> ฉันเก็บรหัสสำรองไว้แล้ว</label>
    <button class="btn p big" id="bgo" disabled>ไปต่อ</button></div>`);
  const txt="รหัสสำรอง ระบบแจ้งเหตุฉุกเฉิน ("+(sessionStorage.getItem("nm")||"")+")\n"+codes.join("\n")+"\nแต่ละรหัสใช้ได้ครั้งเดียว\n";
  $("#bcp").onclick=()=>navigator.clipboard&&navigator.clipboard.writeText(txt).then(()=>toast("คัดลอกแล้ว"));
  $("#bdl").onclick=()=>{const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([txt],{type:"text/plain"}));a.download="sos-backup-codes.txt";a.click()};
  $("#bok").onchange=e=>{$("#bgo").disabled=!e.target.checked};$("#bgo").onclick=()=>{page="account";render()}}
/* ---- LINE alerts for dashboard users: each person binds their own LINE and picks what to be told (backend app/routing.py) ---- */
const LAC={off:["ไม่รับ","ยังเห็นเคสบนแดชบอร์ดและได้ยินเสียงเตือนตามปกติ"],overdue:["เฉพาะเคสที่ยังไม่มีคนรับเรื่อง","ส่งเมื่อเลยเวลารับเรื่องแล้วยังไม่มีใครกดรับ — รบกวนน้อยที่สุด"],all:["ทุกเคส ตั้งแต่กดปุ่มครั้งแรก","ได้รับพร้อม อสม. ของผู้ป่วย และปิดเคสใดก็ได้ผ่าน LINE (เจ้าหน้าที่ส่วนกลาง)"]};
function lineAlertForm(st,p){const al=st.allowed,pr=st.prefs;
  return `<div class="optl">${al.includes("cases")?`<div><b>🚨 แจ้งเหตุจากผู้ป่วย</b><div class="optl" style="margin:6px 0 0 4px">${Object.entries(LAC).map(([k,v])=>`<label><input type="radio" name="${p}c" value="${k}" ${pr.cases===k?"checked":""}> <span>${v[0]}<small>${v[1]}</small></span></label>`).join("")}</div></div>`:""}
   ${al.includes("device")?`<label><input type="checkbox" id="${p}d" ${pr.device?"checked":""}> <span><b>🔋 ปัญหาของปุ่มผู้ป่วย</b><small>ปุ่มเงียบหาย · แบตต่ำ · ไฟบ้านดับ (ทุกเครื่อง)</small></span></label>`:""}
   ${al.includes("system")?`<label><input type="checkbox" id="${p}s" ${pr.system?"checked":""}> <span><b>📡 ระบบ</b><small>เกตเวย์ขาดการเชื่อมต่อ · เซิร์ฟเวอร์ · การสำรองข้อมูล (เฉพาะผู้ดูแลระบบ)</small></span></label>`:""}</div>`}
function lineAlertRead(st,p){const o={};if(st.allowed.includes("cases"))o.cases=document.querySelector(`input[name="${p}c"]:checked`)?.value||"off";
  if(st.allowed.includes("device"))o.device=$("#"+p+"d").checked;if(st.allowed.includes("system"))o.system=$("#"+p+"s").checked;return o}
const lineAlertOn=pr=>pr.system||pr.device||pr.cases!=="off";
function lineAlertPills(l){const pr=l.prefs,a=[];if(pr.cases==="all")a.push('<span class="pill grn">ทุกเคส</span>');if(pr.cases==="overdue")a.push('<span class="pill grn">เคสที่ไม่มีคนรับ</span>');
  if(pr.device)a.push('<span class="pill amb">ปุ่มผู้ป่วย</span>');if(pr.system)a.push('<span class="pill amb">ระบบ</span>');return a.join(" ")}
function lineCard(st){
  if(!st.allowed.length)return `<div class="card"><h3>💬 รับการแจ้งเตือนทาง LINE</h3><p class="mut">บัญชีแบบ "ดูอย่างเดียว" ไม่รับการแจ้งเตือนทาง LINE</p></div>`;
  return `<div class="card"><h3>💬 รับการแจ้งเตือนทาง LINE</h3>
    <div class="row">${st.bound?'<span class="ok">✅ ผูก LINE ของฉันแล้ว</span><button class="btn" id="lun">ยกเลิกผูก</button><button class="btn" id="lbd">ผูกใหม่ (เปลี่ยนเครื่อง/บัญชี)</button>':'<span class="bad">❌ ยังไม่ได้ผูก LINE</span><button class="btn p" id="lbd">ผูก LINE ของฉัน</button>'}</div>
    ${!st.bound&&lineAlertOn(st.prefs)?'<div class="warn">เลือกรับการแจ้งเตือนไว้แล้ว แต่ยังไม่ได้ผูก LINE — จะยังไม่ได้รับข้อความ</div>':""}
    <p class="mut" style="margin:10px 0 4px">เลือกเฉพาะเรื่องที่ต้องการ เพื่อไม่ให้ข้อความมากเกินไป (เปลี่ยนได้ทุกเมื่อ)</p>${lineAlertForm(st,"ml")}
    <div class="row"><button class="btn p" id="lsv">บันทึกการแจ้งเตือน</button></div>
    <p class="mut">ถ้าคุณถูกกำหนดเป็นผู้ดูแลของผู้ป่วยรายใดในหน้าผู้ป่วย จะได้รับแจ้งเหตุของรายนั้นเสมอ ไม่ขึ้นกับตัวเลือกนี้</p></div>`}
function bindLineCard(st){
  if($("#lbd"))$("#lbd").onclick=guard(async()=>{const r=await api("/me/line/bind-code",{method:"POST"});await dlg({icon:"integrations",title:"ผูก LINE ของฉัน",html:bindHTML(r,true),ok:"ผูกเสร็จแล้ว",cancel:false});loadAccount()});
  if($("#lun"))$("#lun").onclick=guard(async()=>{if(!await ask("ยกเลิกการผูก LINE ของฉัน?\nจะไม่ได้รับการแจ้งเตือนทาง LINE จนกว่าจะผูกใหม่"))return;await api("/me/line/unbind",{method:"POST"});toast("ยกเลิกผูกแล้ว");loadAccount()});
  if($("#lsv"))$("#lsv").onclick=guard(async()=>{await api("/me/line",{method:"PUT",body:lineAlertRead(st,"ml")});toast("บันทึกแล้ว");loadAccount()})}
const loadAccount=guard(async()=>{
  const [me,lst]=await Promise.all([api("/me"),api("/me/line")]);if(page!=="account")return;const m=me.mfa;
  $("#main").innerHTML=`<div class="card"><h3>ข้อมูลบัญชี</h3><div class="row"><b>${esc(me.username)}</b><span class="mut">${esc(me.name||"")}</span><span class="tag">${esc({admin:"ผู้ดูแลระบบ",staff:"เจ้าหน้าที่",viewer:"ดูอย่างเดียว"}[me.role]||me.role)}</span></div>
    <div class="row"><button class="btn" id="apw">🔑 เปลี่ยนรหัสผ่าน</button></div></div>${lineCard(lst)}
   <div class="card"><h3>🔐 การยืนยันตัวตน 2 ขั้น</h3>
    ${m.enabled?`<p><span class="tag closed">เปิดใช้งาน</span> ตั้งแต่ ${fmt(m.enabled_at)} · รหัสสำรองเหลือ <b>${m.backup_left}</b> ชุด${m.backup_left<=3?' <b class="bad">— ควรสร้างใหม่</b>':""}</p>
      <div class="row"><input id="acode" inputmode="numeric" placeholder="รหัส 6 หลักจากแอป" maxlength="9" style="max-width:200px"><button class="btn" id="abc">สร้างรหัสสำรองใหม่</button></div>
      ${m.required?'<p class="mut">นโยบายของระบบบังคับใช้กับบัญชีนี้ — ปิดเองไม่ได้ (ถ้ามือถือหาย ให้ผู้ดูแลระบบอีกคนกด "รีเซ็ต 2FA")</p>':`<details><summary>ปิดการยืนยัน 2 ขั้น</summary><div class="row"><input id="dpw" type="password" placeholder="รหัสผ่าน" autocomplete="current-password"><input id="dcode" inputmode="numeric" placeholder="รหัส 6 หลัก" maxlength="9" style="max-width:160px"><button class="btn r" id="adis">ปิดใช้งาน</button></div></details>`}`
    :`<p class="mut">ยังไม่ได้เปิด${m.required?' — <b class="bad">ระบบกำหนดให้ต้องเปิด</b>':""} · เมื่อเปิดแล้ว การเข้าสู่ระบบต้องใช้รหัสผ่าน + รหัส 6 หลักจากแอปในมือถือ</p><button class="btn p" id="aen">เปิดใช้งาน</button>`}</div>${await appCards()}`;
  bindAppCards();bindLineCard(lst);
  $("#apw").onclick=()=>pwScreen(false);
  if($("#aen"))$("#aen").onclick=()=>mfaSetup(false);
  if($("#abc"))$("#abc").onclick=guard(async()=>{const r=await api("/me/2fa/backup-codes",{method:"POST",body:{code:$("#acode").value}});showBackupCodes(r.backup_codes,false)});
  if($("#adis"))$("#adis").onclick=guard(async()=>{const r=await api("/me/2fa/disable",{method:"POST",body:{code:$("#dcode").value,password:$("#dpw").value}});token=r.token;sessionStorage.setItem("tk",token);toast("ปิดการยืนยัน 2 ขั้นแล้ว");loadAccount()})});

// ---------------- monthly button self-test ----------------
let stMonth="";
const loadSelftest=guard(async()=>{
  const [s,cfg]=await Promise.all([api("/selftest"+(stMonth?"?month="+stMonth:"")),api("/settings")]);if(page!=="selftest")return;
  const c=cfg.self_test,adm=role==="admin",ed=role!=="viewer",pct=s.pct??0,todo=s.active-s.tested,cur=new Date().toISOString().slice(0,7);
  const last=s.reminders[s.reminders.length-1];
  $("#main").innerHTML=`<div class="kpis">
    <div class="kpi ${pct>=100?"grn":pct>=60?"amb":"red"}"><div class="ki">🧪</div><div><div class="kl">ทดสอบแล้ว · ${esc(s.month_th)}</div><div class="kv">${s.tested}<small>/ ${s.active} เครื่อง</small></div><div class="bar"><span style="width:${pct}%"></span></div></div></div>
    <div class="kpi ${todo?"red":"grn"}"><div class="ki">${todo?"⏳":"✅"}</div><div><div class="kl">ยังไม่ทดสอบ</div><div class="kv">${todo}</div><div class="ks">${todo?"ควรให้ อสม. ช่วยกดทดสอบ":"ครบทุกเครื่องแล้ว"}</div></div></div>
    <div class="kpi ind"><div class="ki">📨</div><div><div class="kl">แจ้งเตือน LINE ล่าสุด</div><div class="kv" style="font-size:18px">${last?fmt(last.at):"-"}</div><div class="ks">${last?`ส่งถึง อสม. ${last.caregivers} คน · ${last.by==="system"?"อัตโนมัติ":"โดย "+esc(last.by)}`:c.enabled?`อัตโนมัติวันที่ ${c.remind_day}${c.second_day?` และ ${c.second_day}`:""} เวลา ${c.remind_time} น.`:"ปิดการเตือนอัตโนมัติอยู่"}</div></div></div></div>
  <div class="card"><div class="row" style="justify-content:space-between"><h3 style="margin:0">ผลการทดสอบรายเครื่อง</h3>
    <div class="row"><input type="month" id="stm" value="${s.month}" max="${cur}">${ed&&s.month===cur&&todo?`<button class="btn p" id="strm">📨 ส่ง LINE เตือนตอนนี้</button>`:""}</div></div>
    ${s.devices.length?`<table><tr><th></th><th>ปุ่ม</th><th>ผู้ป่วย</th><th>อสม. ที่ดูแล</th><th>ทดสอบเดือนนี้</th><th>ทดสอบล่าสุด</th><th>แบต</th><th>สัญญาณ</th></tr>${s.devices.map(d=>`<tr>
      <td>${d.tested?'<span class="tag closed">✅ ผ่าน</span>':'<span class="tag accepted">⏳ ยังไม่ทดสอบ</span>'}</td><td><b>${esc(d.label)}</b></td>
      <td>${esc(d.patient)}<div class="mut">${esc(d.village)}</div></td><td>${d.caregiver?esc(d.caregiver)+(d.caregiver_has_line?"":' <span class="bad" title="ยังไม่ผูก LINE">⚠️</span>'):'<span class="mut">ยังไม่กำหนด</span>'}</td>
      <td>${d.tests_this_month||"-"}</td><td>${d.last_test_at?ago(d.last_test_at):'<span class="mut">ไม่เคย</span>'}</td><td>${d.battery!=null&&d.battery!==255?d.battery+"%":"-"}</td><td>${d.rssi!=null?d.rssi+" dBm":"-"}</td></tr>`).join("")}</table>`
      :'<div class="empty"><div class="big">📟</div><h4>ยังไม่มีปุ่มที่ใช้งาน</h4><p class="mut">ปุ่มที่ตั้งเป็น active ในหน้า "อุปกรณ์" จะแสดงที่นี่</p></div>'}</div>
  <div class="card"><h3>วิธีทดสอบ (บอกผู้ป่วยและ อสม.)</h3><ol class="steps"><li>กดปุ่ม<b>สั้น ๆ 3 ครั้งติดกัน</b> (ไม่ต้องกดค้าง)</li><li>ไฟ<b style="color:var(--grn)">เขียว</b>กะพริบ = สัญญาณถึงระบบ ผ่าน · ไฟ<b style="color:var(--red)">แดง</b> = ส่งไม่ถึง แจ้ง รพ.สต.</li><li>เป็นการทดสอบเท่านั้น ไม่ใช่การแจ้งเหตุ ไม่มีใครถูกเรียก</li></ol></div>
  <div class="card"><h3>ตั้งค่าการเตือนทดสอบประจำเดือน</h3>
    <label class="sw"><input type="checkbox" id="ste" ${c.enabled?"checked":""} ${adm?"":"disabled"}><span class="sl"></span><b>ส่ง LINE เตือน อสม. ให้ทดสอบปุ่มทุกเดือน</b></label>
    <div class="row">ครั้งแรกวันที่ <input id="std1" type="number" min="1" max="28" value="${c.remind_day}" style="width:76px" ${adm?"":"disabled"}> ครั้งที่สอง (เฉพาะที่ยังไม่ทดสอบ) วันที่ <input id="std2" type="number" min="0" max="28" value="${c.second_day}" style="width:76px" ${adm?"":"disabled"}> <span class="mut">(0 = ไม่ส่ง)</span> เวลา <input id="stt" type="time" value="${c.remind_time}" ${adm?"":"disabled"}></div>
    <label class="row"><input type="checkbox" id="stc" ${c.confirm_line?"checked":""} ${adm?"":"disabled"}> ส่ง LINE ยืนยันให้ อสม. ทุกครั้งที่ปุ่มทดสอบผ่าน</label>
    <p class="mut">เตือนเฉพาะ อสม. ลำดับแรกของผู้ป่วยแต่ละราย และเฉพาะปุ่มที่ยังไม่ได้ทดสอบในเดือนนั้น</p>${adm?'<button class="btn p" id="sts">บันทึก</button>':""}</div>`;
  $("#stm").onchange=e=>{stMonth=e.target.value;loadSelftest()};
  if($("#strm"))$("#strm").onclick=guard(async()=>{if(!await ask(`ส่ง LINE เตือน อสม. ให้ทดสอบปุ่มที่ยังไม่ทดสอบ (${todo} เครื่อง) ตอนนี้?`))return;
    const r=await api("/selftest/remind",{method:"POST"});toast(`ส่งถึง อสม. ${r.caregivers} คน`+(r.failed?` · ส่งไม่ได้ ${r.failed}`:"")+(r.no_caregiver?` · ไม่มี อสม. ${r.no_caregiver} เครื่อง`:""));loadSelftest()});
  if($("#sts"))$("#sts").onclick=guard(async()=>{await api("/settings/self_test",{method:"PUT",body:{enabled:$("#ste").checked,remind_day:+$("#std1").value,second_day:+$("#std2").value,remind_time:$("#stt").value,confirm_line:$("#stc").checked}});toast("บันทึกแล้ว");loadSelftest()});
});

let openCaseAfter=0;          // set by the map / a notification deep link; consumed by loadCases()

const loadAudit=guard(async()=>{
  if(role!=="admin"){$("#main").innerHTML='<div class="warn">เฉพาะผู้ดูแลระบบ</div>';return}
  const [a,us]=await Promise.all([api("/audit"),api("/users")]);
  $("#main").innerHTML=`<div class="card"><h3>ผู้ใช้งานระบบ</h3><table>${us.map(u=>`<tr><td>${esc(u.username)}</td><td>${esc(u.display_name)}</td><td>${u.role}</td></tr>`).join("")}</table><div class="row"><input id="nu" placeholder="username"><input id="nd" placeholder="ชื่อแสดง"><input id="np" type="password" placeholder="รหัสผ่าน ≥10 ตัว"><select id="nr"><option>staff</option><option>viewer</option><option>admin</option></select><button class="btn p" id="au">เพิ่มผู้ใช้</button></div></div>
  <div class="card"><h3>Audit log</h3><table>${a.map(r=>`<tr><td>${fmt(r.at)}</td><td>${esc(r.actor)}</td><td>${esc(r.action)}</td><td>${esc(r.target)}</td></tr>`).join("")}</table></div>`;
  lister("audit",$("#main").lastElementChild,{noun:"รายการ"});
  $("#au").onclick=guard(async()=>{await api("/users",{method:"POST",body:{username:$("#nu").value,display_name:$("#nd").value,password:$("#np").value,role:$("#nr").value}});toast("เพิ่มแล้ว");loadAudit()})});

