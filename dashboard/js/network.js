"use strict";
// gateways (maintenance, remote control), devices, radio link, LoRaWAN frames, ChirpStack
// Loaded by index.html as plain scripts, in order: core, cases, people, network, system, install, admin (they share one global scope).
/* ---- maintenance mode + who depends on this gateway ---- */
const MEND={online:"เมื่อเกตเวย์กลับมาออนไลน์",manual:"เมื่อกดสิ้นสุดเอง"};
const hhmm=iso=>new Date(iso).toLocaleString("th-TH-u-ca-buddhist",{timeZone:"Asia/Bangkok",day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"})+" น.";
const maintEnd=m=>m.end==="time"&&m.until?" "+hhmm(m.until):MEND[m.end]||MEND.online;
function maintHTML(g,sysLine){
  const m=g.maint;
  if(m)return `<div class="gm-on"><b>อยู่ในโหมดซ่อมบำรุง</b>${m.reason?` — ${esc(m.reason)}`:""}<br>
      <span>${m.since?"เริ่ม "+hhmm(m.since)+(m.by?" โดย "+esc(m.by):"")+" · ":""}สิ้นสุด${maintEnd(m)}</span><br>
      <span>ระหว่างนี้ไม่ส่ง LINE เรื่องเกตเวย์นี้ แต่แดชบอร์ดยังแสดงสถานะจริง (ตอนนี้: ${GWS[g.status][0]} ${GWS[g.status][1]})</span></div>
    <div class="row">${m.end==="time"?'<button class="btn" id="gmx">ขยายเวลา +1 ชม.</button>':""}<button class="btn p" id="gmo">สิ้นสุดการซ่อมบำรุง</button></div>
    ${g.offline?'<p class="hint">ถ้ากดสิ้นสุดตอนที่เกตเวย์ยังออฟไลน์ ระบบจะส่ง LINE แจ้งทันที 1 ครั้ง แล้วเตือนซ้ำตามปกติ</p>':""}`;
  const opt=(v,t,on)=>`<label class="chip"><input type="radio" name="gme" value="${v}" ${on?"checked":""}><span>${t}</span></label>`;
  return `<p class="mut" style="margin-top:0">ใช้ตอนรู้แล้วว่าเครื่องเสีย หรือกำลังจะปิดเพื่อซ่อม/ย้าย — ระหว่างซ่อมจะไม่ส่ง LINE เรื่องเกตเวย์นี้ (ทั้งแจ้งครั้งแรกและเตือนซ้ำ) และไม่นับเป็นเวลาขาดในรายงาน</p>
    <div class="row">งานที่ทำ <input id="gmr" maxlength="200" size="34" placeholder="เช่น เปลี่ยนสายแลน / ย้ายจุดติดตั้ง"></div>
    <div class="row"><span>สิ้นสุดเมื่อ</span><div class="chips">${opt("h1","1 ชม.",!g.offline)}${opt("h4","4 ชม.")}${opt("m8","พรุ่งนี้ 08:00")}${opt("online","กลับมาออนไลน์",g.offline)}${opt("manual","กดสิ้นสุดเอง")}</div></div>
    <p class="hint" id="gmh"></p>
    <div class="row"><button class="btn p" id="gms">🔧 เข้าโหมดซ่อมบำรุง</button></div>
    ${!sysLine?'<p class="hint">หมายเหตุ: ตอนนี้ปิดการแจ้งเตือนเกตเวย์ทาง LINE ทั้งระบบอยู่ (หน้าตั้งค่า → ผู้รับแจ้งเตือน)</p>':""}`}
/* "tomorrow 08:00" in Bangkok time, whatever the browser's time zone */
function maintUntil(v){const now=Date.now();
  if(v==="h1")return new Date(now+3600e3);if(v==="h4")return new Date(now+4*3600e3);
  if(v==="m8"){const b=new Date(now+7*3600e3);return new Date(Date.UTC(b.getUTCFullYear(),b.getUTCMonth(),b.getUTCDate()+1,8-7))}
  return null}
let gImp=null;
async function maintWire(g,glp){
  const name=g.label||g.gateway_id;
  const hint=()=>{const v=(document.querySelector("input[name=gme]:checked")||{}).value,u=maintUntil(v);if($("#gmh"))$("#gmh").textContent=u?"สิ้นสุด "+hhmm(u.toISOString())+" — ถ้าถึงเวลาแล้วเกตเวย์ยังไม่กลับมา ระบบจะส่ง LINE เตือน":v==="online"?"ออกจากโหมดซ่อมเองเมื่อเกตเวย์กลับมาออนไลน์ แล้วส่ง LINE ว่าซ่อมเสร็จ":"ต้องกดสิ้นสุดเอง — อย่าลืมกดเมื่อซ่อมเสร็จ"};
  document.querySelectorAll("input[name=gme]").forEach(x=>x.onchange=hint);hint();
  if($("#gms"))$("#gms").onclick=async()=>{const v=document.querySelector("input[name=gme]:checked").value,u=maintUntil(v);
    const n=gImp?gImp.only_here:0;
    const risk=n?`\n\n⚠️ มีปุ่มผู้ป่วย ${n} เครื่องที่ใช้เกตเวย์นี้อย่างเดียว ระหว่างซ่อมจะส่งเหตุไม่ถึง — ควรโทรแจ้งหรือเช็กอาการเป็นระยะ (รายชื่ออยู่ในการ์ดซ่อมบำรุง)`:"";
    if(!await ask(`เข้าโหมดซ่อมบำรุง: ${name}?\nไม่ส่ง LINE เรื่องเกตเวย์นี้ จนถึง${u?" "+hhmm(u.toISOString()):v==="online"?"เมื่อกลับมาออนไลน์":"เมื่อกดสิ้นสุดเอง"}${risk}`))return;
    glp({maintenance:{on:true,end:u?"time":v,until:u?u.toISOString():null,reason:$("#gmr").value}})};
  if($("#gmo"))$("#gmo").onclick=async()=>{if(!await ask(`สิ้นสุดการซ่อมบำรุง ${name}?${g.offline?"\nเกตเวย์ยังออฟไลน์อยู่ ระบบจะส่ง LINE แจ้งทันที":""}`))return;glp({maintenance:{on:false}})};
  if($("#gmx"))$("#gmx").onclick=()=>{const b=Math.max(Date.now(),new Date(g.maint.until).getTime());glp({maintenance:{on:true,end:"time",until:new Date(b+3600e3).toISOString(),reason:g.maint.reason}})};
  const box=$("#gimp");gImp=null;
  try{const r=await api(`/gateways/${g.id}/impact`);gImp=r;if(!document.body.contains(box))return;
    if(!r.total){box.innerHTML='<p class="mut">7 วันที่ผ่านมา ไม่มีปุ่มผู้ป่วยที่ส่งผ่านเกตเวย์นี้</p>';return}
    const only=r.devices.filter(x=>x.only_here),rest=r.devices.filter(x=>!x.only_here);
    const tr=x=>`<tr><td>${x.patient?esc(x.patient.full_name):'<span class="mut">ยังไม่ผูกผู้ป่วย</span>'}${x.patient?.village?`<div class="mut">${esc(x.patient.village)}</div>`:""}</td><td>${x.patient?.phone?`<a href="tel:${esc(x.patient.phone)}">${esc(x.patient.phone)}</a>`:"-"}</td><td>${esc(x.label)}</td><td>${ago(x.last_seen)}</td></tr>`;
    const tb=rows=>`<div class="tw"><table><tr><th>ผู้ป่วย</th><th>โทร</th><th>ปุ่ม</th><th>ส่งล่าสุด</th></tr>${rows.map(tr).join("")}</table></div>`;
    box.innerHTML=`<div class="gi-sum"><span>ปุ่มผู้ป่วยที่ส่งผ่านเกตเวย์นี้ (7 วันล่าสุด) <b>${r.total}</b> เครื่อง</span>${only.length?`<span class="gi-bad">ใช้เกตเวย์นี้อย่างเดียว <b>${only.length}</b> เครื่อง</span>`:'<span class="gi-ok">ทุกเครื่องมีเกตเวย์อื่นรับได้ด้วย</span>'}</div>
      ${only.length?`<p class="mut">ถ้าเกตเวย์นี้ดับ ปุ่มเหล่านี้จะส่งเหตุไม่ถึง — ระหว่างซ่อมควรโทรแจ้งผู้ป่วย/ญาติ หรือเช็กอาการเป็นระยะ</p>${tb(only)}`:""}
      ${rest.length?`<details class="gi-more"><summary>มีเกตเวย์อื่นรับได้ด้วย ${rest.length} เครื่อง</summary>${tb(rest)}</details>`:""}
      <p class="hint">นับจากสัญญาณที่เกตเวย์ได้รับจริงใน 7 วัน · ช่วงแรกหลังอัปเดต ระบบยังรู้แค่เกตเวย์ล่าสุดของแต่ละปุ่ม จึงอาจนับ "อย่างเดียว" มากกว่าความจริง</p>`}
  catch(e){if(document.body.contains(box))box.innerHTML='<span class="mut">ตรวจรายชื่อไม่สำเร็จ</span>'}}

/* ---- remote control of one gateway (admin): signed whitelisted commands, see docs/GATEWAY-REMOTE.md ---- */
let gwRT=null;
async function gwRemote(g){
  clearTimeout(gwRT);
  const box=$("#grb");if(!box)return;
  const ACT={report_now:["ขอรายงานตอนนี้",false],logs:["ดู log ล่าสุด",false],restart_services:["รีสตาร์ท LoRa services",true],reboot:["รีบูตเกตเวย์",true]};
  const ST={pending:["⏳","รอเกตเวย์ตอบ"],ok:["✅","สำเร็จ"],failed:["❌","ไม่สำเร็จ"],expired:["⌛","หมดเวลา (เกตเวย์ไม่ตอบ)"]};
  const draw=async()=>{
    if(!$("#grb")||page!=="gateways")return;
    const d=await api(`/gateways/${g.id}/commands`),pend=d.commands.some(c=>c.status==="pending");
    $("#grb").innerHTML=`<div class="row">เอเจนต์บนเกตเวย์: ${d.agent?'<span class="ok">● พร้อม</span>':'<span class="bad">● ยังไม่ทำงาน</span>'}</div>
      ${d.agent?"":'<div class="warn">เกตเวย์ยังไม่ได้ติดตั้ง/เปิดเอเจนต์ — ใช้ปุ่มด้านล่างยังไม่ได้ ดูวิธีที่ docs/GATEWAY-REMOTE.md หรือกด "แสดงคีย์เอเจนต์"</div>'}
      <div class="row">${Object.entries(ACT).map(([k,v])=>`<button class="btn" data-act="${k}" ${!d.agent||pend?"disabled":""}>${v[0]}</button>`).join("")}</div>
      <p class="hint">สั่งได้เฉพาะ 4 อย่างนี้ — ไม่มีการรันคำสั่งอิสระ · ทุกคำสั่งมีลายเซ็นและบันทึกใน "บันทึกการใช้งาน" · รีบูตทำให้ปุ่มผู้ป่วยส่งไม่ได้ประมาณ 1–2 นาที</p>
      ${d.commands.length?`<table><tr><th>เวลา</th><th>คำสั่ง</th><th>สถานะ</th><th>ผล</th></tr>${d.commands.map(c=>`<tr><td>${fmt(c.created_at)}</td><td>${esc(ACT[c.action]?ACT[c.action][0]:c.label)}<div class="mut">${esc(c.by||"")}</div></td><td>${ST[c.status][0]} ${ST[c.status][1]}</td><td>${esc(c.detail||"")}${c.output?`<details><summary>ดูผลลัพธ์</summary><pre style="white-space:pre-wrap;max-height:260px;overflow:auto;font-size:12px">${esc(c.output)}</pre></details>`:""}</td></tr>`).join("")}</table>`:'<p class="mut">ยังไม่เคยสั่งงาน</p>'}
      <div class="row"><button class="btn" id="gky">แสดงคีย์เอเจนต์</button><span id="gkv"></span></div>`;
    document.querySelectorAll("#grb button[data-act]").forEach(b=>b.onclick=guard(async()=>{
      const k=b.dataset.act;
      if(ACT[k][1]&&!await ask(k==="reboot"?"รีบูตเกตเวย์?\nปุ่มผู้ป่วยจะส่งสัญญาณไม่ได้ประมาณ 1–2 นาที — ไม่ควรทำถ้ามีเหตุเกิดอยู่":"รีสตาร์ท LoRa services?\nการรับสัญญาณจะสะดุดประมาณ 10 วินาที"))return;
      await api(`/gateways/${g.id}/commands`,{method:"POST",body:{action:k}});toast("ส่งคำสั่งแล้ว");draw()}));
    $("#gky").onclick=guard(async()=>{const r=await api(`/gateways/${g.id}/agent-key`);$("#gkv").innerHTML=` <code style="user-select:all">${esc(r.key)}</code> <span class="mut">ใส่เป็น AGENT_KEY ใน /etc/sos-gateway.env บนเกตเวย์ — เป็นความลับ (การดูถูกบันทึก)</span>`});
    if(pend)gwRT=setTimeout(()=>{draw().catch(()=>{})},3000)};
  window._gwDraw=draw;
  try{await draw()}catch(e){box.innerHTML='<span class="bad">โหลดไม่สำเร็จ</span>'}}

const loadDevices=guard(async()=>{
  const [ds,ps,aiOk]=await Promise.all([api("/devices"),api("/patients"),aiReady()]);if(page!=="devices")return;
  const LC=["ready","active","repair","retired","lost"];
  $("#main").innerHTML=`${aiOk&&canEdit()&&ds.length?`<div class="card"><div class="row">${aiBtn("devices","","AI วิเคราะห์อุปกรณ์ทั้งหมด")}<span class="mut">ให้ AI ช่วยดูแบตเตอรี่ สัญญาณ และเครื่องที่เงียบ (ไม่ส่งชื่อผู้ป่วย/รหัสเครื่อง)</span></div><div data-aibox="devices:"></div></div>`:""}${canEdit()?`<div class="card"><h3>เพิ่มอุปกรณ์</h3><div class="row"><input id="ndu" placeholder="DevEUI 16 หลัก" size="20" maxlength="23"><input id="ndl" placeholder="ป้าย เช่น บ้านลุงสม"><button class="btn p" id="nda">เพิ่ม</button></div><p class="mut">สำหรับลงทะเบียนเครื่องที่โปรแกรมไว้แล้ว — ถ้าเป็นเครื่องใหม่ ใช้เมนู "ติดตั้งอุปกรณ์" (ผู้ดูแลระบบ) จะง่ายกว่า</p></div>`:""}<div class="card"><table><tr><th>ป้าย</th><th>DevEUI</th><th>สถานะ</th><th>ผู้ป่วย</th><th>แบต/ไฟ</th><th>สัญญาณ</th><th>เห็นล่าสุด</th><th title="พิกัดจาก GPS ของปุ่ม ถ้ายังไม่มีจะแสดงตำแหน่งบ้านที่ปักเอง">ตำแหน่ง</th><th>fw</th><th></th></tr>${ds.map(d=>`<tr data-rid="${d.id}" class="${linkNow(d)==="ok"?"":"stale"}"><td>${esc(d.label)}${Object.keys(d.config_override||{}).length?' <span class="tag" title="ใช้ค่าเฉพาะเครื่อง">ค่าเฉพาะ</span>':""}${d.config_pending?' <span class="tag" style="color:var(--amb)" title="ค่าใหม่ยังไม่ถึงเครื่อง">⏳ รอเครื่องรับค่า</span>':""}</td><td><code>${d.dev_eui}</code></td><td>${d.lifecycle}<br>${linkBadge(d)}</td><td>${esc(d.patient||"-")}</td><td class="lv">${d.battery==null||d.battery===255?"-":d.battery+"%"} ${d.mains_ok===false?"⚡ไฟดับ":""}<small>ค่าล่าสุด</small></td><td class="lv">${d.rssi??"-"} dBm / ${d.snr??"-"}<small>ค่าล่าสุด</small></td><td>${d.last_seen?`<span data-ago="${esc(d.last_seen)}">${ago(d.last_seen)}</span><div class="mut" style="font-size:12.5px">${fmt(d.last_seen)}</div>`:"-"}</td><td>${devLocCell(d)}</td><td>${esc(d.fw)}${fwBadge(d.fw)}</td><td>${canEdit()?`<button class="btn" data-id="${d.id}">แก้ไข</button>`:""} <button class="btn" data-dh="${d.id}">📈 ประวัติ</button></td></tr>`).join("")}</table><p class="mut">อุปกรณ์ใหม่ลงทะเบียนอัตโนมัติจากเครื่องมือ provision (สถานะ ready) → ผูกผู้ป่วยแล้วตั้งเป็น active ก่อนติดตั้งที่บ้าน</p></div>`;
  aiWire($("#main"));
  devLocWire(ds);
  if(seenFlash){const tr=$(`#main tr[data-rid="${seenFlash}"]`);seenFlash=0;if(tr){tr.classList.add("seen");setTimeout(()=>tr.classList.remove("seen"),2400)}}
  const dById=i=>ds.find(x=>x.id==i),dBody=(d,lc)=>({dev_eui:d.dev_eui,label:d.label,lifecycle:lc,patient_id:d.patient_id});
  lister("devices",$("#main").lastElementChild,{noun:"เครื่อง",reload:loadDevices,bulk:[
    {label:"ตั้งเป็น ready",only:i=>dById(i).lifecycle!=="ready",run:i=>api("/devices/"+i,{method:"PUT",body:dBody(dById(i),"ready")})},
    {label:"ตั้งเป็น repair",only:i=>dById(i).lifecycle!=="repair",run:i=>api("/devices/"+i,{method:"PUT",body:dBody(dById(i),"repair")})},
    {label:"ปลดระวาง (retired)",only:i=>dById(i).lifecycle!=="retired",confirm:n=>`ปลดระวาง ${n} เครื่อง?\nถ้าตั้งค่า ChirpStack ไว้ ระบบจะปิดเครื่องเหล่านี้ที่นั่นด้วย`,run:i=>api("/devices/"+i,{method:"PUT",body:dBody(dById(i),"retired")})},
    ...(role==="admin"?[{label:"ลบ",cls:"r",confirm:n=>`ลบอุปกรณ์ ${n} เครื่อง (และลบใน ChirpStack ถ้าตั้งค่าไว้)?\nเครื่องที่เคยมีเคสจะถูกข้าม`,run:i=>api("/devices/"+i,{method:"DELETE"})}]:[])]});
  if($("#nda"))$("#nda").onclick=guard(async()=>{await api("/devices",{method:"POST",body:{dev_eui:$("#ndu").value.trim(),label:$("#ndl").value,lifecycle:"ready",patient_id:null}});toast("เพิ่มแล้ว");loadDevices()});
  document.querySelectorAll("[data-dh]").forEach(b=>b.onclick=()=>deviceHistory(ds.find(x=>x.id==b.dataset.dh),30));
  document.querySelectorAll("button[data-id]").forEach(b=>b.onclick=guard(async()=>{const d=ds.find(x=>x.id==b.dataset.id);devEdit=true;
    const dd=(await api("/settings")).device_defaults;
    const CF=[["heartbeat_min","Heartbeat (นาที)",5,1275,"เครื่องรายงานสถานะทุกกี่นาที (ยิ่งถี่ยิ่งกินแบต)"],["poll_s","Poll (วินาที)",5,120,"ช่วงถามผลรับเรื่องระหว่างมีเหตุ"],["buzzer","เสียงบัซเซอร์ (0-3)",0,3,"0=ปิด 1=เบา 2=ปกติ 3=ดัง"],["hold_ms","กดปุ่มค้าง (ms)",500,5000,"ต้องกดค้างนานเท่าไรจึงนับเป็นการแจ้งเหตุ"]];
    const ov=d.config_override||{};
    const cfgCard=canEdit()?`<div class="card"><h3>ตั้งค่าเครื่องนี้</h3>
      <p class="mut">ค่าที่ไม่ติ๊ก "ค่าเฉพาะ" จะใช้ค่ากลางจากหน้าตั้งค่า ระบบส่งค่าใหม่ให้เครื่องเองตอนเครื่องรายงานสถานะรอบถัดไป (อาจใช้เวลาถึง 2 รอบ heartbeat) ไม่ต้องเดินไปที่เครื่อง</p>
      ${CF.map(([k,t,lo,hi,h])=>`<div class="row"><label style="min-width:190px"><input type="checkbox" class="ck" data-k="${k}" ${k in ov?"checked":""}> ${t}</label><input type="number" class="cv" data-k="${k}" min="${lo}" max="${hi}" value="${k in ov?ov[k]:dd[k]}" ${k in ov?"":"disabled"}><span class="mut">ค่ากลาง ${dd[k]} · ช่วง ${lo}-${hi} · ${h}</span></div>`).join("")}
      <div class="row">สถานะ: ${d.config_pending?'<b style="color:var(--amb)">⏳ รอเครื่องรับค่า</b>':d.config_applied_at?"✅ เครื่องใช้ค่านี้แล้ว (เมื่อ "+fmt(d.config_applied_at)+")":"✅ ตรงกับค่าที่ตั้ง"} · เวอร์ชันค่า ${d.config_ver}</div>
      <div class="row"><button class="btn p" id="svc">ส่งค่าไปยังเครื่อง</button><button class="btn" id="rsc">กลับไปใช้ค่ากลางทั้งหมด</button></div></div>`:"";
    $("#main").innerHTML=`<button class="btn" id="back">← กลับ</button><div class="card"><h3>${d.dev_eui}</h3><div class="row">ป้าย <input id="l" value="${esc(d.label)}"></div><div class="row">สถานะ <select id="lc">${LC.map(x=>`<option ${x===d.lifecycle?"selected":""}>${x}</option>`).join("")}</select></div><div class="row">เฟิร์มแวร์ <b>${esc(d.fw||"-")}</b>${fwBadge(d.fw)}${role==="admin"?' <button class="btn sm" type="button" id="fwup">อัปเดตผ่าน USB</button>':""}</div><div class="row">ผู้ป่วย <select id="pt"><option value="">- ไม่ผูก -</option>${ps.map(p=>`<option value="${p.id}" ${p.id===d.patient_id?"selected":""}>${esc(p.full_name)}${p.consent_ok?"":" (ยังไม่มีความยินยอม PDPA)"}</option>`).join("")}</select></div>${aiOk&&canEdit()?`<div class="row">${aiBtn("devices",d.id,"AI วิเคราะห์เครื่องนี้")}</div><div data-aibox="devices:${d.id}"></div>`:""}<div class="row"><button class="btn p" id="sv">บันทึก</button>${role==="admin"?'<button class="btn r" id="dxl">ลบอุปกรณ์นี้</button>':""}</div>${role==="admin"?`<div class="row"><button class="btn" id="fln">รีเซ็ตตัวนับ join (DevNonce)</button><span class="mut">ใช้เมื่อเครื่องขึ้น NO NETWORK ค้างหลังรีบูต/อัปโหลดเฟิร์มแวร์ ทั้งที่เกตเวย์ออนไลน์ — ChirpStack จะลืม DevNonce เก่าแล้วเครื่อง join ได้ในรอบถัดไป</span></div>`:""}</div>${cfgCard}`;crumb(`อุปกรณ์ ${d.label||d.dev_eui}`);if($("#fwup"))$("#fwup").onclick=()=>{devEdit=false;page="install";render();setTimeout(()=>$("#fwcard")?.scrollIntoView({behavior:"smooth"}),600)};
    $("#back").onclick=()=>{devEdit=false;render()};
    {const c=$("#main .card");if(c){const n=document.createElement("div");n.id="netc";c.after(n);netCard(d)}}
    $("#sv").onclick=guard(async()=>{const pid=$("#pt").value?Number($("#pt").value):null;let r;
      try{r=await api("/devices/"+d.id,{method:"PUT",body:{dev_eui:d.dev_eui,label:$("#l").value,lifecycle:$("#lc").value,patient_id:pid}})}
      catch(e){const p=ps.find(x=>x.id===pid);if(!p||!/ความยินยอม/.test(e.message))throw e;
        // PDPA: an active button needs the patient's consent on record — offer the way there instead of a dead end
        if(await dlg({icon:"warnt",title:`ยังผูก ${p.full_name} กับอุปกรณ์นี้ไม่ได้`,body:`ผู้ป่วยยังไม่มีบันทึกความยินยอม (PDPA) ระบบจึงไม่ให้ตั้งอุปกรณ์เป็น active\nบันทึกความยินยอมก่อน แล้วกลับมาผูกอีกครั้ง หรือเปลี่ยนสถานะเป็น ready เพื่อผูกไว้ก่อนโดยยังไม่เปิดใช้งาน`,ok:"ไปบันทึกความยินยอม",cancel:"ปิด"})){
          devEdit=false;page="patients";render();const t=setInterval(()=>{if($("#main [data-pd]")){clearInterval(t);patientPrivacy(p)}},120);setTimeout(()=>clearInterval(t),6000)}
        return}
      toast("บันทึกแล้ว"+csNote(r.chirpstack));devEdit=false;render()});
    aiWire($("#main"));
    if($("#fln"))$("#fln").onclick=guard(async()=>{if(!await ask(`รีเซ็ตตัวนับ join ของ ${d.dev_eui} ใน ChirpStack?\nเครื่องจะ join ได้ในรอบ retry ถัดไป (ไม่เกิน ~5 นาที หรือกด RST ที่เครื่องให้เร็วขึ้น)`))return;await api(`/devices/${d.id}/flush-nonces`,{method:"POST"});toast("รีเซ็ตแล้ว — กด RST ที่เครื่องเพื่อ join ทันที")});
    if($("#dxl"))$("#dxl").onclick=guard(async()=>{if(!await ask(`ลบอุปกรณ์ ${d.dev_eui} ?\nลบได้เฉพาะเครื่องที่ยังไม่เคยมีเคส — ถ้าเคยใช้งานแล้วให้เปลี่ยนสถานะเป็น retired/lost แทน`))return;const r=await api("/devices/"+d.id,{method:"DELETE"});toast("ลบแล้ว"+csNote(r.chirpstack));devEdit=false;render()});
    document.querySelectorAll(".ck").forEach(c=>c.onchange=()=>{document.querySelector(`.cv[data-k="${c.dataset.k}"]`).disabled=!c.checked});
    if($("#svc")){
      $("#svc").onclick=guard(async()=>{const o={};document.querySelectorAll(".ck:checked").forEach(c=>o[c.dataset.k]=Number(document.querySelector(`.cv[data-k="${c.dataset.k}"]`).value));
        const r=await api(`/devices/${d.id}/config`,{method:"PUT",body:{override:o}});toast(r.changed?"บันทึกแล้ว รอเครื่องรับค่า":"ไม่มีการเปลี่ยนแปลง");devEdit=false;render()});
      $("#rsc").onclick=guard(async()=>{if(!await ask("กลับไปใช้ค่ากลางทั้งหมด?"))return;await api(`/devices/${d.id}/config`,{method:"PUT",body:{override:{}}});toast("กลับไปใช้ค่ากลางแล้ว");devEdit=false;render()})}
  }))});

const IC={
ready:'<path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/>',
sysh:'<path d="M3 12h4l2-6 4 12 2-6h6"/>',
moon:'<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
warnt:'<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
check:'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
xcirc:'<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
help:'<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.5v.4M12 17h.01"/>',
x:'<path d="M6 6l12 12M18 6 6 18"/>',
vol:'<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/>',
volx:'<path d="M4 9v6h4l5 4V5L8 9z"/><path d="m17 9 5 6M22 9l-5 6"/>',
chev:'<path d="m6 9 6 6 6-6"/>',
phone:'<path d="M5 3h4l2 5-2.5 1.5a11 11 0 0 0 6 6L16 13l5 2v4a2 2 0 0 1-2 2A17 17 0 0 1 3 5a2 2 0 0 1 2-2"/>',
menu:'<path d="M4 7h16M4 12h16M4 17h10"/>',
map:'<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/>',
selftest:'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
chirpstack:'<circle cx="12" cy="12" r="2"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M4.9 4.9a10 10 0 0 0 0 14.2M19.1 4.9a10 10 0 0 1 0 14.2"/>',
integrations:'<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
update:'<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v4h-4"/>',
account:'<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
cases:'<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
patients:'<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
caregivers:'<circle cx="9" cy="8" r="3.5"/><path d="M2 21c0-3.5 3.2-5.5 7-5.5s7 2 7 5.5M16 4.5a3.5 3.5 0 0 1 0 7M18.5 15.8c2 .7 3.5 2.2 3.5 5.2"/>',
devices:'<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 1v4M15 1v4M9 19v4M15 19v4M1 9h4M1 15h4M19 9h4M19 15h4"/>',
gateways:'<path d="M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M2 9a15 15 0 0 1 20 0"/><circle cx="12" cy="19.5" r="1"/>',
install:'<path d="M12 3v12m0 0-4-4m4 4 4-4M4 21h16"/>',
reports:'<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>',
privacy:'<path d="M12 2 4 5v6c0 5 3.5 9 8 11 4.5-2 8-6 8-11V5z"/>',
users:'<circle cx="9" cy="8" r="4"/><path d="M2 21c0-4 3-6 7-6M16 19l2 2 4-4"/>',
settings:'<path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
audit:'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h6"/>',
bell:'<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
bellOff:'<path d="M8.7 3A6 6 0 0 1 18 8c0 3 .5 5.200 1.300 6.800M17 17H3s3-2 3-9c0-.8.1-1.500.4-2.200M10.300 21a1.900 1.900 0 0 0 3.400 0M2 2l20 20"/>',
key:'<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9m-3 3 3 3m-6-3 2 2"/>',
out:'<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
alert:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.700 21a2 2 0 0 1-3.400 0"/>',
send:'<path d="m22 2-7 20-4-9-9-4zM22 2 11 13"/>',
chip:'<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/>',
wifi:'<path d="M5 12.500a10 10 0 0 1 14 0M8.500 16a5 5 0 0 1 7 0M2 9a15 15 0 0 1 20 0"/><circle cx="12" cy="19.500" r="1"/>'};
const ico=n=>`<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${IC[n]}</svg>`;
let setSec=sessionStorage.getItem("sec")||"hours";
function setDirty(){return false}
const human=s=>s>=3600?(s/3600)+" ชั่วโมง":s>=60?(Math.floor(s/60)+" นาที"+(s%60?" "+s%60+" วินาที":"")):s+" วินาที";
const humanMin=m=>m>=1440&&m%1440===0?(m/1440)+" วัน":m>=60?(Math.floor(m/60)+" ชม."+(m%60?" "+m%60+" นาที":"")):m+" นาที";
async function showLogo(sl){const box=$("#lg-"+sl);if(!box)return;const x=await fetch("/api/org/logo/"+sl,{headers:{authorization:"Bearer "+token}}).catch(()=>null);
  box.innerHTML=x&&x.ok?`<img src="${URL.createObjectURL(await x.blob())}" alt="">`:'<span class="mut">ไม่มี</span>'}
const loadSettings=guard(async()=>{
  const [s,hs,cgs]=await Promise.all([api("/settings"),api("/holidays"),api("/caregivers")]);
  if(page!=="settings")return;
  const adm=role==="admin",dis=adm?"":"disabled";
  const oh=s.office_hours,es=s.escalation,dd=s.device_defaults,of=s.offline,sa=s.system_alert,da=s.device_alert;
  const DAYS=["จ","อ","พ","พฤ","ศ","ส","อา"];
  const num=(id,v,min,max,unit,step)=>`<div class="inp"><input id="${id}" type="number" inputmode="decimal" value="${v}" min="${min}" max="${max}" ${step?`step="${step}"`:""} ${dis}><span>${unit}</span></div>`;
  const fld=(label,help,ctl)=>`<div class="sf"><div class="fl"><label>${label}</label>${help?`<p>${help}</p>`:""}</div><div class="fc">${ctl}</div></div>`;
  const chips=(id,vals)=>adm?`<div class="chips" data-for="${id}">${vals.map(v=>`<button type="button" data-v="${v[0]}">${v[1]}</button>`).join("")}</div>`:"";
  const head=(ic,t,d)=>`<div class="chead"><div class="ci">${ico(ic)}</div><div><h3>${t}</h3><p>${d}</p></div></div>`;
  const active=cgs.filter(c=>c.active);
  const SECS=[["hours","เวลาทำการ","clock"],["alert","การแจ้งเหตุ","send"],["notify","ผู้รับแจ้งเตือน","alert"],["device","อุปกรณ์และเครือข่าย","chip"],["org","หน่วยงาน / รายงาน","reports"],["security","ความปลอดภัย","key"]];
  if(!SECS.some(x=>x[0]===setSec))setSec="hours";
  const holRows=h=>h.length?h.map(x=>`<div class="hol"><span>${x.day}</span><span>${esc(x.name)}</span>${adm?`<button class="btn" data-h="${x.id}">ลบ</button>`:""}</div>`).join(""):'<p class="mut">ยังไม่มีวันหยุดที่เพิ่มไว้</p>';
  $("#main").innerHTML=`${adm?"":'<div class="warn">เฉพาะผู้ดูแลระบบแก้ไขได้ (คุณดูได้อย่างเดียว)</div>'}
  <div class="pills" id="pills">${SECS.map(x=>`<button data-s="${x[0]}" class="${setSec===x[0]?"on":""}">${ico(x[2])}${x[1]}<i class="dot"></i></button>`).join("")}</div>
  <div id="sr">
  <div class="sec" data-sec="hours" ${setSec==="hours"?"":"hidden"}>
   <div class="card">${head("clock","เวลาทำการ","นอกช่วงเวลานี้และในวันหยุด ระบบยังแจ้ง อสม. ตามปกติ แต่จอปุ่มผู้ป่วยจะแสดงให้โทร 1669")}
    ${fld("วันทำการ","เลือกวันที่เจ้าหน้าที่ รพ.สต. อยู่ปฏิบัติงาน",`<div class="row">${DAYS.map((d,i)=>`<label class="chip"><input type="checkbox" class="dy" value="${i}" ${oh.days.includes(i)?"checked":""} ${dis}><span>${d}</span></label>`).join("")}</div>`)}
    ${fld("เวลาเปิด–ปิด","ช่วงเวลาทำการในแต่ละวัน",`<div class="row"><input id="st" type="time" value="${oh.start}" ${dis}> <span class="mut">ถึง</span> <input id="en" type="time" value="${oh.end}" ${dis}></div>`)}</div>
   <div class="card">${head("clock","วันหยุดราชการ","วันที่เพิ่มไว้จะนับเป็นนอกเวลาทำการ (เพิ่ม/ลบมีผลทันที ไม่ต้องกดบันทึก)")}
    <div id="hol">${holRows(hs)}</div>${adm?`<div class="row" style="margin-top:10px"><input id="hd" type="date"><input id="hn" placeholder="ชื่อวันหยุด เช่น วันสงกรานต์" size="24"><button class="btn p" id="ha">เพิ่มวันหยุด</button></div>`:""}</div>
  </div>
  <div class="sec" data-sec="alert" ${setSec==="alert"?"":"hidden"}>
   <div class="card">${head("send","การแจ้งต่อเมื่อไม่มีคนรับเรื่อง","เมื่อมีเหตุ ระบบแจ้ง อสม. คนที่ 1 ก่อน ถ้าไม่มีใครกดรับตามเวลาที่ตั้ง จะแจ้งคนถัดไป")}
    ${fld("แจ้งคนถัดไปเมื่อไม่ตอบรับใน","แนะนำ 1–3 นาที สั้นเกินไป อสม. จะตอบไม่ทัน",`${num("ea",es.escalate_after_s,10,3600,"วินาที")}<div class="hint" id="eah"></div>${chips("ea",[[60,"1 นาที"],[120,"2 นาที"],[180,"3 นาที"],[300,"5 นาที"]])}`)}
    ${fld("ถือว่า “เกินเวลา” เมื่อผ่านไป","เคสที่ไม่มีใครรับเรื่องนานเกินนี้จะแสดงเตือนสีแดงบนแดชบอร์ด",`${num("ov",es.overdue_after_s,30,7200,"วินาที")}<div class="hint" id="ovh"></div>${chips("ov",[[180,"3 นาที"],[300,"5 นาที"],[600,"10 นาที"]])}`)}
    ${fld("ผู้รับสุดท้าย","คนที่จะถูกแจ้งเป็นคนสุดท้ายถ้าทุกคนไม่ตอบ (แนะนำให้เลือกเสมอ)",`<select id="fin" ${dis}><option value="">- ไม่ระบุ -</option>${cgs.map(c=>`<option value="${c.id}" ${c.id===es.final_caregiver_id?"selected":""}>${esc(c.full_name)}</option>`).join("")}</select>`)}</div>
  </div>
  <div class="sec" data-sec="notify" ${setSec==="notify"?"":"hidden"}>
   <div class="card">${head("alert","ผู้รับแจ้งเตือนระบบ","คนที่ได้รับ LINE เมื่อเกตเวย์ขาดการเชื่อมต่อ และเมื่อปุ่มผู้ป่วยเงียบหาย/แบตต่ำ/ไฟดับ")}
    ${fld("เลือกผู้รับ","ต้องผูก LINE แล้วจึงจะได้รับ · ถ้าไม่เลือกใครเลย ระบบจะแจ้ง “ผู้รับสุดท้าย” แทน",`<div class="row">${active.map(c=>`<label class="chip" title="${c.line_bound?"":"ยังไม่ผูก LINE"}"><input type="checkbox" class="sa" value="${c.id}" ${sa.caregiver_ids.includes(c.id)?"checked":""} ${dis}><span>${esc(c.full_name)}${c.line_bound?"":" ⚠"}</span></label>`).join("")||'<span class="mut">ยังไม่มี อสม./ผู้ดูแล — เพิ่มได้ที่เมนู อสม./ผู้ดูแล</span>'}</div>`)}
    ${fld("แจ้งเตือนเกตเวย์ทาง LINE","ปิด = ไม่ส่ง LINE เรื่องเกตเวย์ขาด/กลับมาเลย (ยังแสดงบนแดชบอร์ด) · ถ้าเสียแค่บางเครื่อง ให้ปิดที่หน้าเกตเวย์เครื่องนั้นแทน",`<label class="sw"><input type="checkbox" id="sgl" ${sa.gateway_line!==false?"checked":""} ${dis}><i></i><span id="sglt">${sa.gateway_line!==false?"เปิด":"ปิด"}</span></label>`)}
    ${fld("เตือนซ้ำทุก","ถ้าปัญหายังไม่หาย · ใส่ 0 = ไม่เตือนซ้ำ (ช่วงที่ใช้ได้ 10–1440 นาที)",`${num("sar",sa.repeat_min,0,1440,"นาที")}<div class="hint" id="sarh"></div>${chips("sar",[[0,"ไม่เตือนซ้ำ"],[30,"30 นาที"],[60,"1 ชม."],[120,"2 ชม."]])}`)}
    ${adm?`<div class="row" style="margin-top:6px"><button class="btn" id="s6">ส่ง LINE ทดสอบ</button><span class="mut">ควรกดทดสอบทุกครั้งหลังเปลี่ยนผู้รับ (ทดสอบจากค่าที่บันทึกแล้ว)</span></div><div id="testres" class="hint"></div>`:""}</div>
   <div class="card">${head("chip","แจ้งเตือนปุ่มผู้ป่วย","เตือนเมื่อปุ่มในบ้านผู้ป่วยเงียบหาย แบตเตอรี่ต่ำ หรือไฟบ้านดับ")}
    ${fld("เปิดใช้งาน","ส่ง LINE ถึงผู้รับแจ้งเตือนระบบ (รายชื่อด้านบน)",`<label class="sw"><input type="checkbox" id="dae" ${da.enabled?"checked":""} ${dis}><i></i><span id="daet">${da.enabled?"เปิด":"ปิด"}</span></label>`)}
    ${fld("เตือนเมื่อแบตต่ำกว่า","ช่วง 5–50%",num("dab",da.battery_low_pct,5,50,"%"))}
    ${fld("เตือนเมื่อไฟบ้านดับนานกว่า","ไฟกระพริบสั้น ๆ จะไม่เตือน และถ้าไฟติด ๆ ดับ ๆ ภายใน 30 นาทีนับเป็นครั้งเดียว (ช่วง 1–240 นาที)",num("dam",da.mains_grace_min,1,240,"นาที"))}
    ${fld("เตือนซ้ำเรื่องแบต/ไฟดับ","ปกติแจ้งครั้งเดียวต่อเหตุการณ์ (แบตแจ้งอีกครั้งเมื่อต่ำกว่า 10%) · ปุ่มเงียบหายยังเตือนซ้ำตาม 'เตือนซ้ำทุก' ด้านบน",`${num("dar",da.issue_repeat_h??0,0,72,"ชม.")}${chips("dar",[[0,"ไม่เตือนซ้ำ"],[6,"6 ชม."],[12,"12 ชม."],[24,"วันละครั้ง"]])}`)}
    ${fld("งดส่งตอนกลางคืน","เรื่องแบต/ไฟดับช่วง 21:00–07:00 รอส่งตอนเช้า ถ้าหายก่อนเช้าจะไม่ส่งเลย · ปุ่มเงียบหายยังส่งทันที",`<label class="sw"><input type="checkbox" id="daq" ${da.quiet_night??true?"checked":""} ${dis}><i></i><span id="daqt">${da.quiet_night??true?"เปิด":"ปิด"}</span></label>`)}</div>
    ${fld("เตือนเมื่อสัญญาณวิทยุอ่อนมาก","ปุ่มยังส่งได้แต่ใกล้หลุด (margin ต่ำกว่า 3 dB หรือข้อความหาย ≥30%) — แจ้งครั้งเดียวต่อเหตุการณ์ ตัดสินหลังได้รับอย่างน้อย 8 ข้อความ",`<label class="sw"><input type="checkbox" id="das" ${da.signal_alert??true?"checked":""} ${dis}><i></i><span>${da.signal_alert??true?"เปิด":"ปิด"}</span></label>`)}
  </div>
  <div class="sec" data-sec="device" ${setSec==="device"?"":"hidden"}>
   <div class="card">${head("chip","ค่าเริ่มต้นของปุ่มผู้ป่วย","ส่งลงปุ่มทุกเครื่องอัตโนมัติผ่านรอบ heartbeat · เฉพาะบางเครื่องตั้งแยกได้ที่เมนูอุปกรณ์")}
    ${fld("ปุ่มรายงานสถานะทุก","ถี่ขึ้น = รู้เร็วว่าเครื่องหาย แต่ใช้แบต/ช่องสัญญาณมากขึ้น (5–1275 นาที, แนะนำ 30)",num("hb",dd.heartbeat_min,5,1275,"นาที"))}
    ${fld("ถามผลตอบรับทุก","หลังกดปุ่ม เครื่องจะถามเซิร์ฟเวอร์ว่ามีคนรับเรื่องแล้วหรือยัง (5–120 วินาที, แนะนำ 10)",num("po",dd.poll_s,5,120,"วินาที"))}
    ${fld("ระดับเสียงบี๊บ","",`<select id="bz" ${dis}>${[[0,"ปิดเสียง"],[1,"เบา"],[2,"ปกติ"],[3,"ดัง"]].map(o=>`<option value="${o[0]}" ${dd.buzzer===o[0]?"selected":""}>${o[1]}</option>`).join("")}</select>`)}
    ${fld("ต้องกดค้างนาน","กันกดพลาด · ยิ่งนานยิ่งกันพลาดได้ดี แต่ผู้ป่วยต้องกดนานขึ้น (0.5–5 วินาที, แนะนำ 1.5)",num("ho",dd.hold_ms/1000,0.5,5,"วินาที","0.1"))}</div>
   <div class="card">${head("wifi","ตรวจจับขาดการเชื่อมต่อ","ถ้าเงียบนานเกินกำหนด ระบบจะถือว่าออฟไลน์และแจ้งเตือน")}
    ${fld("ปุ่มผู้ป่วยเงียบเกิน","ควรมากกว่ารอบรายงานสถานะอย่างน้อย 2–3 เท่า (10–10080 นาที, แนะนำ 90)",`${num("do",of.device_offline_min,10,10080,"นาที")}<div class="hint" id="doh"></div>`)}
    ${fld("เกตเวย์เงียบเกิน","เกตเวย์รายงานทุก 1 นาที (3–240 นาที, แนะนำ 5)",num("go",of.gateway_offline_min,3,240,"นาที"))}</div>
  </div>
  <div class="sec" data-sec="org" ${setSec==="org"?"":"hidden"}>
  <div class="card"><h3>🏥 หน่วยงาน (หัวรายงาน PDF และผู้ลงนาม)</h3>
    <div class="orgf">${[["oname","name","ชื่อหน่วยงานหลัก","เช่น โรงพยาบาลส่งเสริมสุขภาพตำบล..."],["opart","partner","หน่วยงานร่วม","เช่น องค์การบริหารส่วนตำบล..."],["osys","system_name","ชื่อระบบ/โครงการ",""],
      ["orn","reporter_name","ชื่อผู้รายงาน","ชื่อ-สกุล"],["ort","reporter_title","ตำแหน่งผู้รายงาน",""],["oan","approver_name","ชื่อผู้อนุมัติ","ชื่อ-สกุล"],["oat","approver_title","ตำแหน่งผู้อนุมัติ",""]]
      .map(([id,k,l,ph])=>`<label class="fld"><span>${l}</span><input id="${id}" value="${esc(s.org[k]||"")}" placeholder="${ph}" maxlength="160" ${adm?"":"disabled"}></label>`).join("")}</div>
    <div class="row">${["left","right"].map(sl=>`<div class="logo"><div class="mut">โลโก้${sl==="left"?"ซ้าย":"ขวา"}</div><div class="lbox" id="lg-${sl}"><span class="mut">ไม่มี</span></div>${adm?`<div class="row" style="margin:0"><label class="btn">เลือกรูป<input type="file" accept="image/png,image/jpeg" data-logo="${sl}" hidden></label><button class="btn" data-logodel="${sl}">ลบ</button></div>`:""}</div>`).join("")}</div>
    <p class="mut">PNG หรือ JPG ไม่เกิน 400 KB · พื้นหลังโปร่งใสจะสวยที่สุด · แสดงที่มุมบนของรายงาน PDF</p>${adm?'<button class="btn p" id="s10">บันทึก</button>':""}</div>
  </div>
  <div class="sec" data-sec="security" ${setSec==="security"?"":"hidden"}>
  <div class="card"><h3>🔐 ความปลอดภัยการเข้าสู่ระบบ</h3>
    <label class="sw"><input type="checkbox" id="mfon" ${s.security.mfa_enabled?"checked":""} ${adm?"":"disabled"}><span class="sl"></span><b>บังคับใช้การยืนยันตัวตน 2 ขั้น (รหัสจากแอปในมือถือ)</b></label>
    <div class="row mut">ใช้กับ: <label><input type="checkbox" checked disabled> ผู้ดูแลระบบ</label><label><input type="checkbox" class="mfr" value="staff" ${s.security.mfa_roles.includes("staff")?"checked":""} ${adm?"":"disabled"}> เจ้าหน้าที่</label><label><input type="checkbox" class="mfr" value="viewer" ${s.security.mfa_roles.includes("viewer")?"checked":""} ${adm?"":"disabled"}> ดูอย่างเดียว</label></div>
    <p class="mut">ค่าเริ่มต้น: ปิด · เมื่อเปิด ผู้ใช้กลุ่มที่เลือกที่ยังไม่ได้ตั้งค่าจะต้องสแกน QR ตั้งค่าในการใช้งานครั้งถัดไป · ทุกคนเปิดใช้เองได้ที่ "บัญชี" แม้นโยบายปิดอยู่ · มือถือหาย: ผู้ดูแลระบบอีกคนกด "รีเซ็ต 2FA" ในหน้าผู้ใช้</p>
    ${adm?'<button class="btn p" id="s9">บันทึก</button>':""}</div>
  </div>
  </div>
  ${adm?`<div class="savebar" id="sb" hidden><span><b id="sbn">0</b> หมวดที่เปลี่ยน: <span id="sbl"></span></span><span class="row" style="margin:0"><button class="btn" id="sbc">ยกเลิก</button><button class="btn p" id="sbs">บันทึกทั้งหมด</button></span></div>`:""}`;
  showLogo("left");showLogo("right");
  if(!adm){setDirty=()=>false;return}
  const put=(k,v)=>guard(async()=>{await api("/settings/"+k,{method:"PUT",body:v});toast("บันทึกแล้ว")})();
  $("#s10").onclick=()=>put("org",{name:$("#oname").value,partner:$("#opart").value,system_name:$("#osys").value,reporter_name:$("#orn").value,reporter_title:$("#ort").value,approver_name:$("#oan").value,approver_title:$("#oat").value});
  document.querySelectorAll("[data-logo]").forEach(inp=>inp.onchange=guard(async()=>{const f=inp.files[0];if(!f)return;if(f.size>400000)throw new Error("ไฟล์ใหญ่เกิน 400 KB");
    const url=await new Promise((res,rej)=>{const r=new FileReader();r.onload=()=>res(r.result);r.onerror=rej;r.readAsDataURL(f)});
    await api("/org/logo/"+inp.dataset.logo,{method:"PUT",body:{data_url:url}});toast("บันทึกโลโก้แล้ว");showLogo(inp.dataset.logo)}));
  document.querySelectorAll("[data-logodel]").forEach(b=>b.onclick=guard(async()=>{await api("/org/logo/"+b.dataset.logodel,{method:"DELETE"});toast("ลบแล้ว");showLogo(b.dataset.logodel)}));
  $("#s9").onclick=async()=>{const on=$("#mfon").checked;if(on&&!await ask("เปิดบังคับใช้การยืนยัน 2 ขั้น?\n\nผู้ใช้กลุ่มที่เลือก (รวมถึงคุณ ถ้ายังไม่ได้ตั้งค่า) ต้องสแกน QR ด้วยแอป Authenticator ก่อนใช้งานต่อ"))return;
    put("security",{mfa_enabled:on,mfa_roles:["admin",...[...document.querySelectorAll(".mfr:checked")].map(x=>x.value)]})};
  const $v=id=>$("#"+id).value,N=id=>+$v(id);
  const get=()=>({
    office_hours:{days:[...document.querySelectorAll(".dy:checked")].map(x=>+x.value),start:$v("st"),end:$v("en")},
    escalation:{escalate_after_s:N("ea"),overdue_after_s:N("ov"),final_caregiver_id:$v("fin")?+$v("fin"):null,max_rounds:1},
    system_alert:{caregiver_ids:[...document.querySelectorAll(".sa:checked")].map(x=>+x.value),repeat_min:N("sar"),gateway_line:$("#sgl").checked},
    device_alert:{enabled:$("#dae").checked,battery_low_pct:N("dab"),mains_grace_min:N("dam"),issue_repeat_h:N("dar"),quiet_night:$("#daq").checked,signal_alert:$("#das").checked},
    device_defaults:{heartbeat_min:N("hb"),poll_s:N("po"),buzzer:N("bz"),hold_ms:Math.round(N("ho")*1000)},
    offline:{device_offline_min:N("do"),gateway_offline_min:N("go")}});
  const NAMES={office_hours:"เวลาทำการ",escalation:"การแจ้งต่อ",system_alert:"ผู้รับแจ้งเตือน",device_alert:"แจ้งเตือนปุ่มผู้ป่วย",device_defaults:"ค่าเริ่มต้นปุ่ม",offline:"ตรวจจับออฟไลน์"};
  const SECOF={office_hours:"hours",escalation:"alert",system_alert:"notify",device_alert:"notify",device_defaults:"device",offline:"device"};
  let base=JSON.stringify(get()),baseO=get();
  const changed=()=>{const c=get();return Object.keys(c).filter(k=>JSON.stringify(c[k])!==JSON.stringify(baseO[k]))};
  setDirty=()=>changed().length>0;
  const hints=()=>{$("#eah").textContent="= "+human(N("ea"));$("#ovh").textContent="= "+human(N("ov"))+(N("ov")<=N("ea")?" · ควรมากกว่าเวลาแจ้งต่อ":"");
    $("#sarh").textContent=N("sar")===0?"ไม่เตือนซ้ำ":"= ทุก "+humanMin(N("sar"));
    $("#doh").textContent="= "+humanMin(N("do"))+(N("do")<N("hb")*2?` · ควรอย่างน้อย ${N("hb")*2} นาที (2 เท่าของรอบรายงาน)`:"");
    $("#daet").textContent=$("#dae").checked?"เปิด":"ปิด";$("#daqt").textContent=$("#daq").checked?"เปิด":"ปิด";$("#sglt").textContent=$("#sgl").checked?"เปิด":"ปิด"};
  const refresh=()=>{hints();const ch=changed();$("#sb").hidden=!ch.length;$("#sbn").textContent=ch.length;$("#sbl").textContent=ch.map(k=>NAMES[k]).join(", ");
    document.querySelectorAll("#pills button").forEach(b=>b.classList.toggle("dirty",ch.some(k=>SECOF[k]===b.dataset.s)))};
  const validate=()=>{let bad=null;document.querySelectorAll("#sr input[type=number]").forEach(i=>{const box=i.closest(".inp");const v=i.value===""?NaN:+i.value;const ok=!isNaN(v)&&v>=+i.min&&v<=+i.max;box.classList.toggle("bad3",!ok);
      if(!ok&&!bad)bad={i,msg:`${i.closest(".sf").querySelector("label").textContent}: ต้องอยู่ระหว่าง ${i.min}–${i.max}`}});
    const r=N("sar");if(!bad&&r!==0&&r<10)bad={i:$("#sar"),msg:"เตือนซ้ำ: ใส่ 0 (ไม่เตือน) หรือ 10 นาทีขึ้นไป"};
    if(!bad&&!$v("st")||!bad&&!$v("en"))bad={i:$("#st"),msg:"กรุณาระบุเวลาเปิด–ปิด"};return bad};
  $("#sr").addEventListener("input",refresh);$("#sr").addEventListener("change",refresh);
  document.querySelectorAll("#pills button").forEach(b=>b.onclick=()=>{setSec=b.dataset.s;sessionStorage.setItem("sec",setSec);document.querySelectorAll("#pills button").forEach(x=>x.classList.toggle("on",x===b));document.querySelectorAll(".sec").forEach(x=>x.hidden=x.dataset.sec!==setSec)});
  document.querySelectorAll(".chips button").forEach(b=>b.onclick=()=>{const i=$("#"+b.parentNode.dataset.for);i.value=b.dataset.v;i.dispatchEvent(new Event("input",{bubbles:true}))});
  $("#sbc").onclick=()=>{setDirty=()=>false;loadSettings()};
  $("#sbs").onclick=guard(async()=>{const bad=validate();if(bad){const sec=bad.i.closest(".sec");if(sec&&sec.hidden)document.querySelector(`#pills [data-s="${sec.dataset.sec}"]`).click();bad.i.focus();throw new Error(bad.msg)}
    const c=get(),ks=changed();let done=0;
    for(const k of ks){try{await api("/settings/"+k,{method:"PUT",body:c[k]});baseO[k]=c[k];done++}catch(e){refresh();throw new Error(NAMES[k]+": "+e.message+(done?` (บันทึกแล้ว ${done} หมวด)`:""))}}
    refresh();toast("บันทึกแล้ว "+done+" หมวด")});
  if($("#s6"))$("#s6").onclick=guard(async()=>{$("#testres").textContent="กำลังส่ง...";try{const r=await api("/system-alert/test",{method:"POST"});$("#testres").innerHTML=r.results.map(x=>`<div class="${x.ok?"ok":"bad"}">${x.ok?"✔":"✖"} ${esc(x.caregiver)}: ${x.ok?"ส่งแล้ว":"ไม่สำเร็จ ("+esc(x.detail)+")"}</div>`).join("")}catch(e){$("#testres").innerHTML=`<span class="bad">${esc(e.message)}</span>`}});
  const bindHol=()=>document.querySelectorAll("[data-h]").forEach(b=>b.onclick=guard(async()=>{await api("/holidays/"+b.dataset.h,{method:"DELETE"});$("#hol").innerHTML=holRows(await api("/holidays"));bindHol()}));
  $("#ha").onclick=guard(async()=>{if(!$("#hd").value)throw new Error("เลือกวันที่ก่อน");await api("/holidays",{method:"POST",body:{day:$("#hd").value,name:$("#hn").value}});$("#hd").value="";$("#hn").value="";$("#hol").innerHTML=holRows(await api("/holidays"));bindHol();toast("เพิ่มวันหยุดแล้ว")});
  bindHol();refresh()});

// ---------------- device page: radio link quality, diagnosis, ping test (backend app/netmon.py, app/netapi.py) ----------------
const NQCOL={good:"var(--grn)",fair:"var(--pri)",weak:"var(--amb)",bad:"var(--red)",unknown:"var(--mut)"};
const nqTag=(q,th)=>`<span class="tag" style="background:${NQCOL[q]||NQCOL.unknown};color:#fff">${esc(th||q)}</span>`;
let netDays=7,netDev=null;
function sigChart(pts){                     // link margin over time, with the "about to drop" (3 dB) and "weak" (6 dB) lines
  const P=pts.filter(p=>p.margin!=null);if(P.length<2)return'<p class="mut">ยังมีข้อมูลไม่พอสำหรับกราฟ (ต้องได้รับอย่างน้อย 2 ข้อความ)</p>';
  const W=640,H=180,L=34,R=8,T=8,B=22,t0=+new Date(P[0].at),t1=+new Date(P[P.length-1].at)||t0+1;
  const ys=P.map(p=>p.margin),lo=Math.min(0,...ys)-1,hi=Math.max(14,...ys)+1;
  const X=t=>L+(W-L-R)*((t-t0)/Math.max(1,t1-t0)),Y=v=>T+(H-T-B)*(1-(v-lo)/(hi-lo));
  const band=(a,b,c)=>`<rect x="${L}" y="${Y(Math.min(b,hi))}" width="${W-L-R}" height="${Math.max(0,Y(Math.max(a,lo))-Y(Math.min(b,hi)))}" fill="${c}" opacity=".10"/>`;
  const line=P.map((p,i)=>`${i?"L":"M"}${X(+new Date(p.at)).toFixed(1)},${Y(p.margin).toFixed(1)}`).join("");
  const dots=P.map(p=>{const m=p.margin,c=m<3?NQCOL.bad:m<6?NQCOL.weak:m<10?NQCOL.fair:NQCOL.good;
    return`<circle cx="${X(+new Date(p.at)).toFixed(1)}" cy="${Y(m).toFixed(1)}" r="2.6" fill="${c}"><title>${fmt(p.at)} · margin ${m} dB · RSSI ${p.rssi} · SNR ${p.snr} · SF${p.sf??"?"} · ${p.gw||""}</title></circle>`}).join("");
  const ticks=[0,3,6,10].filter(v=>v>=lo&&v<=hi).map(v=>`<line x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--bd)" stroke-dasharray="3 3"/><text x="${L-4}" y="${Y(v)+4}" font-size="10" text-anchor="end" fill="var(--mut)">${v}</text>`).join("");
  const d0=new Date(t0),d1=new Date(t1),dl=x=>x.toLocaleDateString("th-TH",{day:"numeric",month:"short",timeZone:"Asia/Bangkok"});
  return`<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;height:auto" role="img" aria-label="กราฟ link margin">
    ${band(lo,3,NQCOL.bad)}${band(3,6,NQCOL.weak)}${ticks}<path d="${line}" fill="none" stroke="var(--pri)" stroke-width="1.2" opacity=".55"/>${dots}
    <text x="${L}" y="${H-6}" font-size="10" fill="var(--mut)">${dl(d0)}</text><text x="${W-R}" y="${H-6}" font-size="10" text-anchor="end" fill="var(--mut)">${dl(d1)}</text>
    <text x="4" y="${T+8}" font-size="10" fill="var(--mut)">dB</text></svg>
    <div class="mut" style="font-size:12px">link margin = SNR เหนือขีดที่วิทยุยังถอดรหัสได้ · ต่ำกว่า 3 dB (แถบแดง) = ใกล้หลุด · 3–6 dB (แถบเหลือง) = อ่อน</div>`}
async function netCard(d){
  netDev=d;const box=$("#netc");if(!box)return;
  let n;try{n=await api(`/devices/${d.id}/network?days=${netDays}`)}catch(e){box.innerHTML=`<div class="card warn">${esc(e.message)}</div>`;return}
  if(!$("#netc")||netDev!==d)return;
  const st=n.stats,s=n.sig||{},canAct=canEdit(),hb=n.heartbeat_min;
  const fixBtn=f=>!canAct||!f.fix?"":f.fix==="flush_nonces"&&role==="admin"?`<button class="btn" data-fix="flush_nonces">ล้าง DevNonce</button>`
    :f.fix==="reprovision"&&role==="admin"?`<button class="btn" data-fix="reprovision">ไปหน้าติดตั้งอุปกรณ์</button>`:f.fix==="ping"?`<button class="btn" data-fix="ping">ทดสอบส่งกลับ</button>`:"";
  const dg=(n.diagnosis||[]).map(f=>`<div class="row" style="align-items:flex-start;gap:8px"><span>${f.level==="error"?"⛔":f.level==="warning"?"⚠️":"✅"}</span><div style="flex:1"><b>${esc(f.title)}</b><div class="mut">${esc(f.detail)}</div></div>${fixBtn(f)}</div>`).join("");
  const pend=(n.pings||[]).find(p=>p.status==="queued"||p.status==="sent");
  const pings=n.pings?`<h4>ทดสอบส่งกลับ (ping)</h4>
    <p class="mut">ส่งข้อความทดสอบจากเซิร์ฟเวอร์ → เกตเวย์ → ปุ่ม แล้วรอปุ่มยืนยัน เพื่อให้มั่นใจว่า ACK "รับเรื่องแล้ว" จะถึงผู้ป่วยจริง ·
      ปุ่มประหยัดไฟ รับข้อความได้เฉพาะตอนส่งสัญญาณขึ้นมา (ทุก ${hb} นาที) จึงใช้เวลาประมาณ ${hb}–${2*hb} นาที · ปุ่มไม่ดัง/ไม่แสดงอะไร</p>
    ${canAct?`<div class="row"><button class="btn p" id="nping" ${pend?"disabled":""}>📡 ทดสอบส่งกลับ</button>${pend?`<span class="mut">⏳ ${esc(pend.status_th)} (เริ่ม ${fmt(pend.created_at)})</span>`:""}</div>`:""}
    ${n.pings.length?`<table><tr><th>เวลา</th><th>ผล</th><th>โดย</th></tr>${n.pings.map(p=>`<tr><td>${fmt(p.created_at)}</td><td>${p.status==="acked"?"✅":p.status==="queued"||p.status==="sent"?"⏳":"⚠️"} ${esc(p.status_th)}${p.note?` <span class="mut">${esc(p.note)}</span>`:""}</td><td>${esc(p.actor)}</td></tr>`).join("")}</table>`:""}`:"";
  const evs=n.events?`<details><summary>เหตุการณ์ล่าสุดจาก ChirpStack (${n.events.length})</summary><table>${n.events.map(e=>`<tr><td style="white-space:nowrap">${fmt(e.at)}</td><td>${esc(e.kind)}</td><td>${e.level==="error"?"⛔ ":e.level==="warning"?"⚠️ ":""}${esc(e.text)}</td></tr>`).join("")}</table></details>`:"";
  box.innerHTML=`<div class="card"><h3>สัญญาณและการเชื่อมต่อ</h3>
    <div class="row">คุณภาพสัญญาณตอนนี้ ${nqTag(s.q||"unknown",n.quality_th[s.q||"unknown"])}
      <span class="mut">margin ${s.margin??"-"} dB · RSSI ${s.rssi??"-"} dBm · SNR ${s.snr??"-"} dB${s.sf?` · SF${s.sf}`:""}${s.loss!=null?` · ข้อความหาย ${s.loss}%`:""} (จาก ${s.n||0} ข้อความล่าสุด)</span></div>
    <h4>การวินิจฉัย</h4>${dg||'<p class="mut">ยังไม่มีข้อมูล</p>'}
    <h4>ย้อนหลัง <select id="ndays">${[7,30,90].map(x=>`<option value="${x}" ${x===netDays?"selected":""}>${x} วัน</option>`).join("")}</select>
      <span class="mut">${st.n} ข้อความ · ${nqTag(st.q,st.q_th)} margin กลาง ${st.margin??"-"} dB${st.loss!=null?` · หาย ${st.loss}%`:""}</span></h4>
    ${sigChart(st.points)}
    ${st.gateways.length?`<table><tr><th>เกตเวย์ที่ได้ยิน</th><th>ข้อความ</th><th>margin กลาง</th><th>RSSI กลาง</th></tr>${st.gateways.map(g=>`<tr><td><code>${esc(g.gateway_id)}</code></td><td>${g.n}</td><td>${nqTag(g.q,(g.margin??"-")+" dB")}</td><td>${g.rssi??"-"} dBm</td></tr>`).join("")}</table>`:""}
    ${pings}${evs}</div>`;
  $("#ndays").onchange=e=>{netDays=+e.target.value;netCard(d)};
  if($("#nping"))$("#nping").onclick=guard(async()=>{const r=await api(`/devices/${d.id}/ping`,{method:"POST"});toast(`ส่งคำสั่งทดสอบแล้ว — รอผลประมาณ ${r.eta_min[0]}–${r.eta_min[1]} นาที`);netCard(d)});
  box.querySelectorAll("[data-fix]").forEach(b=>b.onclick=guard(async()=>{const f=b.dataset.fix;
    if(f==="ping"){$("#nping")?.click();return}
    if(f==="reprovision"){devEdit=false;page="install";render();return}
    if(f==="flush_nonces"){if(!await ask(`ล้าง DevNonce ของ ${d.dev_eui} ใน ChirpStack?\nจากนั้นรีสตาร์ตปุ่ม (กด RST) ให้ขอเข้าเครือข่ายใหม่`))return;
      await api(`/devices/${d.id}/flush-nonces`,{method:"POST"});toast("ล้างแล้ว — รีสตาร์ตปุ่มเพื่อให้เข้าเครือข่ายใหม่");netCard(d)}}));
}

// ---------------- LoRaWAN frame monitor: compact live uplinks without application payloads ----------------
let netLiveRows=[];
const netLiveKey=f=>[f.at,f.dev_eui,f.kind,f.f_cnt,f.text].join("|");
function netLiveMerge(rows){const seen=new Set(),out=[];for(const r of rows){const k=netLiveKey(r);if(!seen.has(k)){seen.add(k);out.push(r)}}return out.sort((a,b)=>new Date(b.at)-new Date(a.at)).slice(0,100)}
function renderNetLive(){
  const box=$("#netlive-list");if(!box)return;
  const kind={up:"Uplink",join:"Join",ack:"ACK",txack:"TX ACK",log:"Log",status:"Status",down:"Downlink"};
  box.innerHTML=netLiveRows.length?`<div class="tw"><table><thead><tr><th>เวลา</th><th>ชนิด</th><th>ปุ่ม</th><th>เฟรม / เหตุการณ์</th></tr></thead><tbody>${netLiveRows.map(f=>`<tr><td style="white-space:nowrap">${fmt(f.at)}</td><td>${esc(kind[f.kind]||f.kind||"-")}</td><td>${esc(f.label||"")}<div class="mut"><code>${esc(f.dev_eui||"")}</code></div></td><td>${f.kind==="up"?`FCnt ${f.f_cnt??"-"} · FPort ${f.f_port??"-"} · RSSI ${f.rssi??"-"} dBm · SNR ${f.snr??"-"} dB · ${f.sf?"SF"+f.sf:"SF?"} · ${f.freq?((f.freq/1e6).toFixed(3)+" MHz"):"-"} · <code>${esc(f.gateway_id||"-")}</code>${f.gw_count>1?` +${f.gw_count-1} gateway`:""}`:esc(f.text||f.code||"-")}</td></tr>`).join("")}</tbody></table></div>`:'<p class="mut">ยังไม่มีเฟรม — เมื่อปุ่มส่งข้อความขึ้นมา ข้อมูลจะปรากฏที่นี่</p>';
}
const loadNetLive=guard(async()=>{
  if(page!=="netlive")return;
  if(!$("#netlive-list"))$("#main").innerHTML=`<div class="card"><div class="row"><div style="flex:1"><h3 style="margin:0">เฟรม LoRaWAN ล่าสุด</h3><p class="mut">แสดง metadata สำหรับติดตั้งและ debug โดยไม่แสดง payload ของผู้ป่วย · อัปเดตสดผ่านการเชื่อมต่อแดชบอร์ด</p></div><span class="hchip"><span class="sdot ${wsUp?"on":"off"}"></span>${wsUp?"สด":"กำลังเชื่อมต่อ"}</span></div><div id="netlive-list"><span class="spin"></span> กำลังโหลด…</div></div>`;
  window._netLive=m=>{if(page!=="netlive"||m.type!=="net_event")return;
    const row=m.kind==="up"&&m.frame?{...m.frame,kind:"up",at:m.at,dev_eui:m.dev_eui,label:m.label||""}:
      {kind:m.kind,at:m.at,dev_eui:m.dev_eui,label:m.label||"",text:m.text||m.code||""};
    netLiveRows=netLiveMerge([row,...netLiveRows]);renderNetLive()};
  try{const [frames,events]=await Promise.all([api("/net/frames?limit=100"),api("/net/events?limit=100")]);
    netLiveRows=netLiveMerge([...netLiveRows,...frames.map(f=>({...f,kind:"up"})),...events]);if(page==="netlive")renderNetLive()}
  catch(e){if(page==="netlive")$("#netlive-list").innerHTML=`<div class="warn">${esc(e.message)}</div>`}
});

// ---------------- ChirpStack (network server) — admin only: status, compare with the dashboard, sync ----------------
const csNote=r=>!r||r==="unchanged"||r==="not_configured"?"":r==="created"?" · ลงทะเบียนใน ChirpStack แล้ว":r==="exists"?" · มีใน ChirpStack อยู่แล้ว":r==="disabled"?" · ปิดใช้งานใน ChirpStack แล้ว":r==="enabled"?" · เปิดใช้งานใน ChirpStack แล้ว":r==="deleted"?" · ลบใน ChirpStack แล้ว":r==="absent"?"":" · ⚠️ ChirpStack: "+r;
async function loadChirpstack(){
  if(role!=="admin"){$("#main").innerHTML='<div class="warn">เฉพาะผู้ดูแลระบบ</div>';return}
  $("#main").innerHTML='<div class="card"><span class="spin"></span> กำลังตรวจ ChirpStack…</div>';
  let s;try{s=await api("/chirpstack")}catch(e){if(page==="chirpstack")$("#main").innerHTML=`<div class="warn">${esc(e.message)}</div>`;return}
  if(page!=="chirpstack")return;
  const c=s.compare,li=(a,f)=>a.length?`<ul>${a.map(f).join("")}</ul>`:'<p class="mut">ไม่มี</p>';
  const ui=s.ui_url?`<button class="btn p" id="csopen">เปิดหน้า ChirpStack ↗</button> <span class="mut">${esc(s.ui_url)} · เข้าได้จากทุกเครือข่ายด้วยบัญชีผู้ดูแลระบบนี้ (ครั้งละ 8 ชั่วโมง)</span>`
    :'<span class="mut">ยังไม่ได้เปิดโดเมนสำหรับ ChirpStack — บนเซิร์ฟเวอร์รัน <code>sudo bash scripts/enable-chirpstack-domain.sh lns.โดเมน</code> หรือใช้ SSH tunnel</span>';
  const need=c?c.gateways_missing_in_cs.length+c.disabled_mismatch.length:0;
  $("#main").innerHTML=`<div class="card"><h3>ChirpStack (LoRaWAN Network Server)</h3>
    <div class="row"><b>สถานะ:</b> ${!s.configured?'<span class="bad">ยังไม่ได้ตั้งค่า</span>':s.reachable?'<span class="ok">เชื่อมต่อได้</span>':'<span class="bad">เชื่อมต่อไม่ได้</span>'} ${s.error?`<span class="mut">${esc(s.error)}</span>`:""}</div>
    ${s.reachable?`<div class="row"><b>Application:</b> ${esc(s.application)} · <b>Device profile:</b> ${esc(s.device_profile)} <span class="mut">${esc(s.region)} ${esc(s.mac_version)}</span></div>`:""}
    <div class="row">${ui}</div></div>${s.reachable?`<div class="card" id="csprofile"><h3>ตั้งค่า Device Profile</h3><span class="mut">กำลังโหลดรายการ ADR…</span></div>
    <div class="card" id="csbackup"><h3>สำรองและย้ายข้อมูล ChirpStack</h3><p class="mut">ส่งออกอุปกรณ์ เกตเวย์ และ keys เป็นไฟล์เข้ารหัสด้วยรหัสผ่าน (AES-256-GCM) · การนำเข้าจะเขียนข้อมูลลง Application/Device Profile ที่ตั้งค่าในเซิร์ฟเวอร์นี้ และอัปเดต keys ของ DevEUI ที่มีอยู่</p>
      <div class="row"><label>รหัสผ่านไฟล์ (อย่างน้อย 16 ตัวอักษร) <input id="csbpass" type="password" minlength="16" maxlength="128" autocomplete="new-password"></label><button class="btn p" id="csbexport">ดาวน์โหลดไฟล์เข้ารหัส</button></div>
      <div class="row"><label>ไฟล์สำรอง <input id="csbfile" type="file" accept="application/json,.json"></label><button class="btn" id="csbimport">นำเข้า / กู้คืน</button></div>
      <div id="csbresult"></div><p class="hint">เก็บไฟล์และรหัสผ่านแยกจากกัน ผู้ที่มีทั้งสองอย่างจะอ่าน keys และย้ายอุปกรณ์ได้</p></div>`:""}
   ${c?`<div class="card"><h3>เทียบข้อมูลกับแดชบอร์ด</h3>
    <div class="row">อุปกรณ์: ในแดชบอร์ด <b>${c.devices_sos}</b> · ใน ChirpStack <b>${c.devices_cs}</b> · เกตเวย์ใน ChirpStack <b>${c.gateways_cs}</b></div>
    <h4>เกตเวย์ที่ยังไม่ได้ลงทะเบียนใน ChirpStack (${c.gateways_missing_in_cs.length})</h4>${li(c.gateways_missing_in_cs,g=>`<li><code>${esc(g.gateway_id)}</code> ${esc(g.label)}</li>`)}
    <h4>สถานะเปิด/ปิดไม่ตรงกัน (${c.disabled_mismatch.length})</h4><p class="mut">อุปกรณ์ที่ "ปลดระวาง" จะถูกปิดใช้งานใน ChirpStack ส่วนสถานะอื่นจะเปิดใช้งาน</p>${li(c.disabled_mismatch,e=>`<li><code>${esc(e)}</code></li>`)}
    <div class="row"><button class="btn p" id="cssync" ${need?"":"disabled"}>🔄 ซิงก์ไปที่ ChirpStack${need?` (${need} รายการ)`:""}</button> <button class="btn" id="csre">ตรวจใหม่</button></div>
    <h4>⚠️ มีในแดชบอร์ดแต่ไม่มีใน ChirpStack (${c.missing_in_cs.length})</h4><p class="mut">ปุ่มเหล่านี้ join เครือข่ายไม่ได้ — ต้องลงทะเบียนใหม่ที่หน้า "ติดตั้งอุปกรณ์" (ระบบไม่สร้างให้อัตโนมัติ เพราะ AppKey อยู่บนตัวปุ่มเท่านั้น)</p>${li(c.missing_in_cs,d=>`<li><code>${esc(d.dev_eui)}</code> ${esc(d.label)} <span class="mut">${esc(d.lifecycle)}</span></li>`)}
    <h4>⚠️ มีใน ChirpStack แต่ไม่มีในแดชบอร์ด (${c.unknown_in_sos.length})</h4><p class="mut">กดปุ่มแล้วจะไม่มีใครได้รับแจ้ง — เพิ่มที่หน้า "อุปกรณ์" หรือลบออกจาก ChirpStack</p>${li(c.unknown_in_sos,d=>`<li><code>${esc(d.dev_eui)}</code> ${esc(d.name)} <span class="mut">เห็นล่าสุด ${fmt(d.last_seen)}</span></li>`)}
   </div>`:s.compare_error?`<div class="warn">${esc(s.compare_error)}</div>`:""}`;
  if(s.reachable){try{const p=await api("/chirpstack/profile-settings");if(page!=="chirpstack")return;
    const el=$("#csprofile"),opts=(p.adr_algorithms||[]).map(a=>`<option value="${esc(a.id)}" ${a.id===p.adr_algorithm_id?"selected":""}>${esc(a.name)}</option>`).join("");
    el.innerHTML=`<h3>ตั้งค่า Device Profile: ${esc(p.profile||"")}</h3><div class="row"><label>อัลกอริทึม ADR <select id="csadr">${opts}</select></label><button class="btn p" id="csadrsave" ${opts?"":"disabled"}>บันทึกค่า ADR</button></div><p class="mut">เปลี่ยนเฉพาะ ADR algorithm · อุปกรณ์ต้องส่ง uplink โดยเปิด ADR flag จึงจะใช้ค่านี้ได้ · ช่วงเวลาถือว่าปุ่มออฟไลน์ตั้งได้ใน “ตั้งค่า → ออฟไลน์”</p>${opts?"":'<div class="warn">ChirpStack ไม่ส่งรายการ ADR algorithms กลับมา จึงยังเปลี่ยนค่าจากหน้านี้ไม่ได้</div>'}`;
    if($("#csadrsave"))$("#csadrsave").onclick=guard(async()=>{await api("/chirpstack/profile-settings",{method:"PUT",body:{adr_algorithm_id:$("#csadr").value}});toast("บันทึกค่า ADR แล้ว");loadChirpstack()})
  }catch(e){const el=$("#csprofile");if(el)el.innerHTML=`<h3>ตั้งค่า Device Profile</h3><div class="warn">${esc(e.message)}</div>`}}
  if($("#csbexport"))$("#csbexport").onclick=guard(async()=>{
    const pass=$("#csbpass").value;if(pass.length<16){toast("รหัสผ่านไฟล์ต้องยาวอย่างน้อย 16 ตัวอักษร","warn");return}
    const file=await api("/chirpstack/backup/export",{method:"POST",body:{passphrase:pass}});
    const blob=new Blob([JSON.stringify(file,null,2)],{type:"application/json"}),url=URL.createObjectURL(blob),a=document.createElement("a");
    a.href=url;a.download=`sos-chirpstack-${new Date().toISOString().slice(0,10)}.sosbackup.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$("#csbpass").value="";toast("ดาวน์โหลดไฟล์สำรองที่เข้ารหัสแล้ว","ok");
  });
  if($("#csbimport"))$("#csbimport").onclick=guard(async()=>{
    const f=$("#csbfile").files[0],pass=$("#csbpass").value;if(!f){toast("เลือกไฟล์สำรองก่อน","warn");return}if(f.size>17*1024*1024){toast("ไฟล์สำรองใหญ่เกิน 17 MB","warn");return}if(pass.length<16){toast("รหัสผ่านไฟล์ต้องยาวอย่างน้อย 16 ตัวอักษร","warn");return}
    if(!await ask(`นำเข้า ${f.name} หรือไม่?\nรายการที่มี DevEUI/Gateway ID ซ้ำจะถูกอัปเดต รวมถึง keys ซึ่งอาจทำให้อุปกรณ์ที่ใช้ key ชุดเดิมเชื่อมต่อไม่ได้`))return;
    const envelope=JSON.parse(await f.text()),r=await api("/chirpstack/backup/import",{method:"POST",body:{passphrase:pass,envelope}});
    const failed=[...(r.failed_devices||[]).map(x=>"อุปกรณ์ "+x),...(r.failed_gateways||[]).map(x=>"เกตเวย์ "+x)];
    toast(`กู้คืนแล้ว: อุปกรณ์เพิ่ม ${r.devices_created} · แก้ ${r.devices_updated} · เกตเวย์เพิ่ม ${r.gateways_created} · แก้ ${r.gateways_updated}${failed.length?` · ไม่สำเร็จ ${failed.length}`:""}`,failed.length?"warn":"ok");
    if(failed.length)$("#csbresult").innerHTML=`<div class="warn">รายการที่กู้คืนไม่สำเร็จ: ${failed.map(esc).join(", ")}</div>`;
    $("#csbpass").value="";$("#csbfile").value="";
  });
  if($("#csre"))$("#csre").onclick=loadChirpstack;
  // one-time sign-in link (60 s) -> ChirpStack domain sets its own 8 h session (backend app/csgate.py). The tab is opened
  // before the request so popup blockers allow it.
  if($("#csopen"))$("#csopen").onclick=async()=>{const w=window.open("about:blank","_blank");
    try{const r=await api("/chirpstack/ui-ticket",{method:"POST"});if(w){w.opener=null;w.location.replace(r.url)}else location.href=r.url}
    catch(e){if(w)w.close();toast(e.message)}};
  if($("#cssync"))$("#cssync").onclick=guard(async()=>{busyBtn($("#cssync"),true);const r=await api("/chirpstack/sync",{method:"POST"});
    toast(`ซิงก์แล้ว: เพิ่มเกตเวย์ ${r.gateways_added.length} · ปิด ${r.devices_disabled.length} · เปิด ${r.devices_enabled.length}${r.errors.length?" · ผิดพลาด "+r.errors.length:""}`);loadChirpstack()});
}

