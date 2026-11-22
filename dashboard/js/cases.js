"use strict";
// shared list helper, AI buttons, case list and case detail
// Loaded by index.html as plain scripts, in order: core, cases, people, network, system, install, admin (they share one global scope).
/* ---- shared list helper: paging (10/25/50/100 per page), quick search, multi-select + bulk actions ----
   lister(key, root, {bulk:[{label,cls,confirm(n),run(id),only(id)}], reload, noun})
   Rows need data-rid="<id>" for bulk. State (page, size, selection) survives the periodic re-render of a page. */
const LST={};
const lsGet=k=>{try{return localStorage.getItem(k)}catch(e){return null}},lsSet=(k,v)=>{try{localStorage.setItem(k,v)}catch(e){}};
function lister(key,root,o={}){
  const table=root&&root.querySelector("table");if(!table||table.rows.length<2)return;
  const st=LST[key]||(LST[key]={page:0,size:+lsGet("lst_size")||10,sel:new Set(),q:""});
  const hasBulk=(o.bulk||[]).length>0&&canEdit(),noun=o.noun||"รายการ";
  const head=table.rows[0],rows=[...table.rows].slice(1);
  const ids=rows.map(r=>r.dataset.rid).filter(Boolean);st.sel=new Set([...st.sel].filter(i=>ids.includes(i)));
  if(hasBulk){const th=document.createElement("th");th.className="lck";th.innerHTML='<input type="checkbox" title="เลือกทั้งหน้า">';head.insertBefore(th,head.firstChild);
    rows.forEach(r=>{const td=document.createElement("td");td.className="lck";const id=r.dataset.rid;td.innerHTML=id?`<input type="checkbox" ${st.sel.has(id)?"checked":""}>`:"";r.insertBefore(td,r.firstChild);
      const cb=td.firstChild;if(cb)cb.onchange=()=>{cb.checked?st.sel.add(id):st.sel.delete(id);draw()}})}
  const bar=document.createElement("div"),bulk=document.createElement("div");bulk.className="lst-bulk";bulk.style.display="none";
  table.parentNode.insertBefore(bulk,table);table.parentNode.insertBefore(bar,table.nextSibling);bar.className="lst-bar";
  const match=r=>!st.q||r.textContent.toLowerCase().includes(st.q.toLowerCase());
  function draw(){
    const vis=rows.filter(match),size=st.size,pages=Math.max(1,Math.ceil(vis.length/size));if(st.page>=pages)st.page=pages-1;
    const from=st.page*size,cur=vis.slice(from,from+size);
    rows.forEach(r=>{r.style.display=cur.includes(r)?"":"none";r.classList.toggle("lsel",!!r.dataset.rid&&st.sel.has(r.dataset.rid))});
    if(hasBulk){rows.forEach(r=>{const cb=r.querySelector("td.lck input");if(cb)cb.checked=st.sel.has(r.dataset.rid)});
      const all=head.querySelector("th.lck input"),idc=cur.map(r=>r.dataset.rid).filter(Boolean);all.checked=idc.length>0&&idc.every(i=>st.sel.has(i));
      all.onchange=()=>{idc.forEach(i=>all.checked?st.sel.add(i):st.sel.delete(i));draw()}}
    const nb=[];for(let i=0;i<pages;i++)if(i===0||i===pages-1||Math.abs(i-st.page)<=2)nb.push(i);
    let pg="";nb.forEach((i,k)=>{if(k&&i-nb[k-1]>1)pg+="<span>…</span>";pg+=`<button data-pg="${i}" class="${i===st.page?"on":""}">${i+1}</button>`});
    bar.innerHTML=`<span>แสดง ${vis.length?from+1:0}–${from+cur.length} จาก ${vis.length}${vis.length!==rows.length?` (ทั้งหมด ${rows.length})`:""} ${noun}</span><span class="sp"></span>
      <input type="search" placeholder="ค้นหา…" value="${esc(st.q)}" style="width:150px" aria-label="ค้นหา">
      <label>ต่อหน้า <select>${[10,25,50,100].map(n=>`<option ${n===size?"selected":""}>${n}</option>`).join("")}</select></label>
      <span class="lst-pg"><button data-pg="${st.page-1}" ${st.page?"":"disabled"}>‹</button>${pg}<button data-pg="${st.page+1}" ${st.page<pages-1?"":"disabled"}>›</button></span>`;
    bar.querySelectorAll("[data-pg]").forEach(b=>b.onclick=()=>{st.page=+b.dataset.pg;draw()});
    bar.querySelector("select").onchange=e=>{st.size=+e.target.value;lsSet("lst_size",st.size);st.page=0;draw()};
    const q=bar.querySelector("input");q.oninput=()=>{st.q=q.value;st.page=0;const pos=q.selectionStart;draw();const n=bar.querySelector("input");n.focus();n.setSelectionRange(pos,pos)};
    if(hasBulk){const n=st.sel.size;bulk.style.display=n?"flex":"none";
      if(n){const vid=vis.map(r=>r.dataset.rid).filter(Boolean);
        bulk.innerHTML=`<b>เลือก ${n} ${noun}</b>${vid.length>n?`<a data-all>เลือกทั้งหมด ${vid.length} ${noun}</a>`:""}<span class="sp"></span>${o.bulk.map((b,i)=>`<button class="btn ${b.cls||""}" data-b="${i}">${b.label}</button>`).join("")}<a data-clr>ยกเลิกการเลือก</a>`;
        const al=bulk.querySelector("[data-all]");if(al)al.onclick=()=>{vid.forEach(i=>st.sel.add(i));draw()};
        bulk.querySelector("[data-clr]").onclick=()=>{st.sel.clear();draw()};
        bulk.querySelectorAll("[data-b]").forEach(bt=>bt.onclick=()=>runBulk(o.bulk[+bt.dataset.b]))}}}
  async function runBulk(b){
    const targets=[...st.sel].filter(i=>!b.only||b.only(i));
    if(!targets.length){toast("ไม่มี"+noun+"ที่ทำรายการนี้ได้");return}
    if(!await ask((b.confirm?b.confirm(targets.length):`${b.label} ${targets.length} ${noun}?`)+(targets.length<st.sel.size?`\n(ข้าม ${st.sel.size-targets.length} ${noun}ที่ใช้ไม่ได้กับรายการนี้)`:"")))return;
    let ok=0,bad=0,err="";document.querySelectorAll(".lst-bulk button").forEach(x=>x.disabled=true);
    for(const id of targets){try{await b.run(id);ok++;st.sel.delete(id)}catch(e){bad++;if(!err)err=e.message}}
    toast(`${b.label}: สำเร็จ ${ok}`+(bad?` · ไม่สำเร็จ ${bad} (${err})`:""));if(o.reload)o.reload()}
  draw()}


/* ---- AI diagnosis of gateways/devices (Gemini; sends anonymous technical readings only, see app/ai.py) ---- */
let _aiOk=null;const aiReady=async()=>{if(_aiOk&&Date.now()-_aiOk.t<30000)return _aiOk.v;let v=false;try{v=(await api("/ai/status")).ready}catch(e){}_aiOk={t:Date.now(),v};return v};
const AIOUT={};
function aiBtn(scope,id,label){return `<button class="btn" data-aid="${scope}:${id??""}">✨ ${label}</button>`}
function aiWire(root){
  const show=(k,r)=>{const box=document.querySelector(`[data-aibox="${k}"]`)||document.getElementById("aibox");if(!box||!r)return;
    box.innerHTML=`<div class="card" style="margin-top:10px"><h3>✨ วิเคราะห์โดย AI <span class="mut" style="font-weight:400;font-size:13px">(${r.count} รายการ · ${esc(r.model)})</span></h3><p style="white-space:pre-wrap">${esc(r.text)}</p><p class="hint">${esc(r.note)}</p><div class="row"><button class="btn" data-aicp>คัดลอก</button><button class="btn" data-aiclr>ปิด</button></div></div>`;
    box.querySelector("[data-aicp]").onclick=()=>navigator.clipboard&&navigator.clipboard.writeText(r.text).then(()=>toast("คัดลอกแล้ว"));
    box.querySelector("[data-aiclr]").onclick=()=>{delete AIOUT[k];box.innerHTML=""}};
  (root||document).querySelectorAll("[data-aid]").forEach(b=>{const k=b.dataset.aid,[scope,id]=k.split(":");const old=b.textContent;
    if(AIOUT[k])show(k,AIOUT[k]);
    b.onclick=guard(async()=>{b.disabled=true;b.textContent="✨ AI กำลังวิเคราะห์…";
      try{const r=await api("/ai/diagnose",{method:"POST",body:{scope,id:id?+id:null}});AIOUT[k]=r;show(k,r)}finally{b.disabled=false;b.textContent=old}})})}

function responseTrend(cases, now = new Date()) {
  const dayKey = date => date.toLocaleDateString("en-CA", {timeZone:"Asia/Bangkok"});
  const days = Array.from({length:7}, (_, i) => {
    const date = new Date(now.getTime() - (6-i)*86400000);
    return {key:dayKey(date), label:date.toLocaleDateString("th-TH",{day:"numeric",month:"short",timeZone:"Asia/Bangkok"}), samples:[]};
  });
  for (const item of cases) {
    if (!item.accepted_at || item.is_test) continue;
    const created = new Date(item.created_at);
    const seconds = (new Date(item.accepted_at)-created)/1000;
    const day = days.find(d => d.key === dayKey(created));
    if (day && seconds >= 0) day.samples.push(seconds);
  }
  const means = days.map(d => d.samples.length ? d.samples.reduce((a,b)=>a+b,0)/d.samples.length : null);
  if (means.every(v => v === null)) return '<p class="mut">ยังไม่มีเคสที่รับเรื่องในช่วง 7 วันนี้</p>';
  const peak = Math.max(60, Math.ceil(Math.max(...means)/60)*60);
  let path = "";
  const marks = days.map((day,i) => {
    const x = 36+i*58, value = means[i];
    let point = "";
    if (value !== null) {
      const y = 100-80*value/peak;
      path += (i && means[i-1] !== null ? "L" : "M")+x+" "+y+" ";
      point = `<circle cx="${x}" cy="${y}" r="4"><title>${day.label} · ${Math.round(value)} วินาที</title></circle>`;
    }
    return point+`<text x="${x}" y="121" text-anchor="middle">${day.label}</text>`;
  }).join("");
  return `<svg class="response-chart" viewBox="0 0 416 133" role="img" aria-label="เวลารับเรื่องเฉลี่ยรายวัน หน่วยนาที: ${days.map((d,i)=>d.label+' '+(means[i]===null?'ไม่มีข้อมูล':Math.round(means[i])+' วินาที')).join(', ')}"><path class="grid" d="M30 20H395 M30 60H395 M30 100H395"/><text x="2" y="24">${peak/60}</text><text x="2" y="102">0</text><path class="trend" d="${path}"/>${marks}</svg>`;
}

const loadCases=guard(async()=>{caseOpen=0;
  const [open,recent,st,gw]=await Promise.all([api("/cases?state=new,accepted"),api("/cases?limit=50"),api("/stats"),api("/gateways")]);
  if(page!=="cases")return;
  const row=c=>`<tr class="pri-${c.patient?c.patient.priority:"normal"}"><td>#${c.id}</td><td>${esc(c.patient?c.patient.full_name:"(ไม่ทราบผู้ป่วย) "+c.device.dev_eui)} ${priBadge(c.patient&&c.patient.priority)}<div class="mut">${esc(c.patient?.address||"")} ${c.patient?.phone?`${esc(c.patient.phone)}`:""}</div></td><td><span class="tag ${c.state}">${ST[c.state]}</span>${c.overdue&&c.state==="new"?' <b style="color:var(--red)">เกินเวลา!</b>':""}${c.after_hours?' <span class="tag">นอกเวลา</span>':""}</td><td>${fmt(c.created_at)}</td><td>${esc(c.accepted_by)}</td><td><button class="btn" data-id="${c.id}" data-a="view">รายละเอียด</button></td></tr>`;
  const gwBad=gw.filter(g=>g.offline&&!g.maint),gwWarn=gw.filter(g=>g.status==="warning"),gwFix=gw.filter(g=>g.maint);
  const nNew=open.filter(c=>c.state==="new").length,nAcc=open.length-nNew,gwOn=gw.length-gw.filter(g=>g.offline).length;
  const kpi=(ic,label,val,sub,cls,go)=>`<div class="kpi ${cls}${go?" go":""}"${go?` data-go="${go}" role="button" tabindex="0"`:""}><div class="ki">${ic}</div><div><div class="kl">${label}</div><div class="kv">${val}</div>${sub?`<div class="ks">${sub}</div>`:""}</div></div>`;
  const card=c=>`<div class="ac ${c.state} pri-${c.patient?c.patient.priority:"normal"}"><div class="ach"><span class="tag ${c.state}">${ST[c.state]}</span><span>#${c.id} · ${ago(c.created_at)}</span></div>
    <div class="acn">${esc(c.patient?c.patient.full_name:"ไม่ทราบผู้ป่วย")} ${priBadge(c.patient&&c.patient.priority)}</div><div class="mut">${esc(c.patient?.address||c.device.dev_eui)}</div>
    <div class="mut" style="font-size:13px">${c.overdue&&c.state==="new"?'<b style="color:var(--red)">เกินเวลารับเรื่อง</b> · ':""}${c.after_hours?"นอกเวลาทำการ · ":""}${c.accepted_by?"รับเรื่องโดย "+esc(c.accepted_by):"ยังไม่มีผู้รับเรื่อง"}</div>
    <div class="row">${c.patient?.phone?`<a class="btn" href="tel:${esc(c.patient.phone)}">${ico("phone")}โทร</a>`:""}<button class="btn ${c.state==="new"?"r":"p"}" data-id="${c.id}" data-a="view">เปิดเคส</button></div></div>`;
  $("#main").innerHTML=`${gwBad.length?`<div class="warn" style="background:#fef2f2;border-color:#fecaca;color:#991b1b">⚠️ เกตเวย์ขาดการเชื่อมต่อ: ${gwBad.map(g=>esc(g.label||g.gateway_id)+(g.reason==="radio"?" (ตัวรับสัญญาณ LoRa เสีย)":"")).join(", ")} — ปุ่มของผู้ป่วยในระยะนี้อาจส่งไม่ถึง · <a href="#" id="gwgo">ดูรายละเอียด</a></div>`:""}${gwFix.length?`<div class="gm-on">🔧 เกตเวย์อยู่ระหว่างซ่อมบำรุง: ${gwFix.map(g=>esc(g.label||g.gateway_id)+(g.offline?" (ออฟไลน์)":"")).join(", ")} — ปุ่มที่ใช้เกตเวย์นี้อย่างเดียวอาจส่งเหตุไม่ถึง</div>`:""}${gwWarn.length&&!gwBad.length?`<div class="warn">เกตเวย์มีข้อควรตรวจ: ${gwWarn.map(g=>esc(g.label||g.gateway_id)+" — "+esc(g.critical.concat(g.warnings).join(", ")||"ตรวจสอบ")).join(" · ")}</div>`:""}
  <div class="kpis">${kpi("🚨","รอรับเรื่อง",nNew,nNew?"ต้องดำเนินการทันที":"ไม่มีเคสรอ",nNew?"red":"grn")}${kpi("🩺","กำลังดูแล",nAcc,"รับเรื่องแล้ว ยังไม่ปิด","amb")}${kpi("📈","เคส 30 วัน",st.cases_30d,`ผิดพลาด ${st.false_alarm} · นอกเวลา ${st.after_hours}`,"ind")}${kpi("⏱️","รับเรื่องเฉลี่ย",dur(st.avg_accept_s),"ตั้งแต่กดปุ่มถึงมีคนรับ","")}${kpi("📡","เกตเวย์ออนไลน์",gw.length?`${gwOn}/${gw.length}`:"0",!gw.length?"ยังไม่มีเกตเวย์":gwBad.length?"มีเกตเวย์ขาดการเชื่อมต่อ":"ทำงานปกติ",gwBad.length?"red":gw.length?"grn":"","gateways")}${(()=>{const h=st.devices||{active:0};if(!h.active)return kpi("🔘","ปุ่มส่งสัญญาณปกติ","0","ยังไม่มีปุ่มที่ใช้งาน","","devices");
    const bad=h.offline+h.never;return kpi("🔘","ปุ่มส่งสัญญาณปกติ",`${h.ok}/${h.active}`,bad?`ขาดการติดต่อ ${bad} เครื่อง${h.late?` · เลยรอบ ${h.late}`:""}`:h.late?`เลยรอบ ${h.late} เครื่อง — รอรอบถัดไป`:"ส่งตามรอบทุกเครื่อง",bad?"red":h.late?"amb":"grn","devices")})()}</div>
  <div class="clinical-grid"><section class="card"><h3>เคสที่ต้องดำเนินการ · ${open.length}</h3>${open.length?`<div class="clinical-queue">${[...open].sort((a,b)=>Number(b.state==="new")-Number(a.state==="new")).map(card).join("")}</div>`:`<div class="empty"><div class="big">✅</div><h4>ไม่มีเคสค้าง</h4></div>`}</section>
  <div><section class="card"><h3>ประสิทธิภาพการตอบสนอง</h3><p class="mut">เวลารับเรื่องเฉลี่ย (นาที) · 7 วัน จาก 50 เคสล่าสุด</p>${responseTrend(recent)}</section>
  <section class="card"><h3>สถานะเครือข่าย</h3>${gw.map(g=>`<div class="gateway-row"><b>${esc(g.label||g.gateway_id)}</b><span class="pill ${g.maint?"amb":g.offline?"red":g.status==="warning"?"amb":"grn"}">${g.maint?"ซ่อมบำรุง":g.offline?"ออฟไลน์":g.status==="warning"?"ควรตรวจสอบ":"ออนไลน์"}</span></div>`).join("")||'<p class="mut">ยังไม่มีเกตเวย์</p>'}</section></div></div>
  <div class="card"><div class="row" style="justify-content:space-between"><h3 style="margin:0">เคสล่าสุด (50 รายการ)</h3><button class="btn" id="chist">🔎 ค้นหาประวัติเคสทั้งหมด</button></div>${recent.length?`<table><tr><th>#</th><th>ผู้ป่วย</th><th>สถานะ</th><th>เวลา</th><th>ผู้รับเรื่อง</th><th></th></tr>${recent.map(row).join("")}</table>`:'<p class="mut">ยังไม่มีเคส</p>'}</div>`;
  lister("cases",$("#main").lastElementChild,{noun:"เคส"});
  if(nNew){
    const banner=document.createElement("div");
    banner.className="case-banner";
    banner.textContent=`มีเคสรอรับเรื่อง ${nNew} เคส`;
    $("#main .kpis").before(banner);
  }
  document.querySelectorAll("[data-a=view]").forEach(b=>b.onclick=()=>showCase(b.dataset.id));
  if($("#chist"))$("#chist").onclick=()=>caseHistory();
  document.querySelectorAll(".kpi[data-go]").forEach(k=>{const go=()=>{page=k.dataset.go;render();scrollTo(0,0)};k.onclick=go;k.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();go()}}});
  if(openCaseAfter){const id=openCaseAfter;openCaseAfter=0;showCase(id)}});
/* ---- preset messages to the patient's device screen (FPort 12; English text is fixed in the firmware) ---- */
let _devMsgs=null;const devMsgs=async()=>_devMsgs||(_devMsgs=await api("/device-messages"));
const oled=lines=>`<div style="display:inline-block;background:#0b1220;color:#7dd3fc;font:600 14px/1.35 ui-monospace,Consolas,monospace;padding:8px 12px;border-radius:8px;min-width:150px;white-space:pre">${lines.map(esc).join("\n")}</div>`;
let caseOpen=0;
function caseMsgCard(c){
  if(c.is_test||!(c.state==="new"||c.state==="accepted")){
    const last=[...c.events].reverse().find(e=>e.kind==="message_sent");
    return last?`<div class="card"><h3>ข้อความบนจออุปกรณ์</h3><p class="mut">ข้อความล่าสุด: ${esc(last.detail.text||"")}</p></div>`:""}
  const del=[...c.events].reverse().find(e=>e.kind==="message_delivered"),sent=[...c.events].reverse().find(e=>e.kind==="message_sent");
  const status=c.msg_pending?`<p>⏳ <b>กำลังส่งไปที่อุปกรณ์</b> (ส่งซ้ำอัตโนมัติจนกว่าจะขึ้นจอ)<br>${oled(c.msg_pending.lines)}</p>`
    :del?`<p>✅ <b>ขึ้นจออุปกรณ์แล้ว</b> <span class="mut">${fmt(del.at)}</span><br>${oled(sent&&sent.detail.seq===del.detail.seq?(sent.detail.text||"").split(" / "):[])}</p>`:"";
  if(!canEdit())return status?`<div class="card"><h3>ข้อความบนจออุปกรณ์</h3>${status}</div>`:"";
  return `<div class="card" id="msgcard"><h3>📟 ส่งข้อความขึ้นจออุปกรณ์ผู้ป่วย</h3>
  ${c.msg_capable?"":`<div class="warn">เฟิร์มแวร์อุปกรณ์นี้ (${esc(c.device.fw||"เก่า")}) ยังไม่รองรับข้อความ — อัปเดตเป็นเวอร์ชัน 0.2 ขึ้นไป</div>`}
  ${status}
  <div class="row"><select id="mcode"><option value="">— เลือกข้อความ —</option></select>
  <label id="mvalw" style="display:none">ถึงใน <input id="mval" type="number" min="1" max="255" value="10" style="width:70px"> นาที</label>
  <button class="btn p" id="msend" ${c.msg_capable?"":"disabled"}>ส่งข้อความ</button></div>
  <div id="mprev" style="margin-top:8px"></div>
  <p class="hint">จออุปกรณ์แสดงเป็นภาษาอังกฤษสั้น ๆ (ตามตัวอย่างด้านบน) พร้อมเสียงบี๊บ 3 ครั้ง · ข้อความถึงภายในประมาณ 10–30 วินาที ขณะเคสยังเปิดอยู่ · อุปกรณ์รับข้อความได้นานสุด 30 นาทีหลังกดปุ่ม/หลังรับเรื่อง · ส่งข้อความใหม่จะแทนข้อความที่ยังไม่ขึ้นจอ</p></div>`}
async function wireCaseMsg(c){
  const sel=$("#mcode");if(!sel)return;const ms=await devMsgs();
  sel.innerHTML+=ms.map(m=>`<option value="${m.code}">${esc(m.th)}</option>`).join("");
  const draw=()=>{const m=ms.find(x=>x.code==sel.value);$("#mvalw").style.display=m&&m.value==="minutes"?"":"none";
    $("#mprev").innerHTML=m?`<span class="mut">ตัวอย่างบนจอ:</span><br>${oled(m.lines.map(l=>l.replace("{v}",String(+$("#mval").value||0))))}`:""};
  sel.onchange=draw;$("#mval").oninput=draw;
  $("#msend").onclick=guard(async()=>{const m=ms.find(x=>x.code==sel.value);if(!m){toast("เลือกข้อความก่อน","warn");return}
    const v=m.value==="minutes"?Math.round(+$("#mval").value):0;if(m.value==="minutes"&&!(v>=1&&v<=255)){toast("ใส่จำนวนนาที 1–255","warn");return}
    await api(`/cases/${c.id}/message`,{method:"POST",body:{code:m.code,value:v}});toast("ส่งข้อความแล้ว — รออุปกรณ์รับ");showCase(c.id)})}
const showCase=guard(async id=>{
  const c=await api("/cases/"+id);caseOpen=+id;
  $("#main").innerHTML=`<button class="btn" id="back">← กลับ</button><div class="card"><h2>เคส #${c.id} <span class="tag ${c.state}">${ST[c.state]}</span></h2>
  <p><b>${esc(c.patient?.full_name||"ไม่ทราบผู้ป่วย")}</b><br>${esc(c.patient?.address||"")} ${esc(c.patient?.village||"")}<br>${c.patient?.phone?`<a href="tel:${esc(c.patient.phone)}">${esc(c.patient.phone)}</a>`:""} ${c.patient?.lat!=null?`· <a target="_blank" rel="noopener" href="https://maps.google.com/?q=${c.patient.lat},${c.patient.lon}">แผนที่</a>`:""}</p>
  <p class="mut">${esc(c.patient?.notes||"")}</p>
  ${c.loc?`<p>📍 <b>ตำแหน่งอุปกรณ์ขณะแจ้งเหตุ:</b> ${gmap(c.loc.lat,c.loc.lon,"เปิดแผนที่")} <span class="mut">(${ago(c.loc.at)} · ดาวเทียม ${c.loc.sats} · HDOP ${c.loc.hdop})</span>${c.loc.home_distance_m!=null?` · ห่างจากที่อยู่ที่ลงทะเบียน <b>${dist(c.loc.home_distance_m)}</b>`:""}<br><span class="mut">GPS ในอาคารคลาดเคลื่อนได้หลายสิบเมตร — ใช้เป็นแนวทางประกอบ ไม่ใช่ตำแหน่งที่แน่นอน</span></p>`:""}
  <p>อุปกรณ์ ${esc(c.device.label)} (${c.device.dev_eui}) · แบต ${c.device.battery??"-"}% · ไฟบ้าน ${c.device.mains_ok===false?"ดับ":"ปกติ"}</p>
  ${canEdit()&&c.state==="new"?`<button class="btn g" id="acc">✅ รับเรื่อง (ส่งสัญญาณกลับอุปกรณ์)</button> `:""}
  ${canEdit()&&(c.state==="new"||c.state==="accepted")?`<div class="row"><input id="note" placeholder="บันทึก (ถ้ามี)" size="40"><button class="btn p" id="cls">ปิดเคส</button><button class="btn r" id="fal">เหตุผิดพลาด</button></div>`:""}
  </div>${caseMsgCard(c)}<div class="card"><h3>ไทม์ไลน์</h3><table>${c.events.map(e=>`<tr><td>${fmt(e.at)}</td><td>${esc(e.kind)}</td><td>${esc(e.actor)}</td><td class="mut">${esc(JSON.stringify(e.detail))}</td></tr>`).join("")}</table></div>`;crumb(`เคส #${c.id}`);
  $("#back").onclick=()=>render();
  const act=(url,body)=>guard(async()=>{await api(url,{method:"POST",body});toast("บันทึกแล้ว");showCase(id)});
  if($("#acc"))$("#acc").onclick=act(`/cases/${id}/accept`);
  if($("#cls"))$("#cls").onclick=()=>act(`/cases/${id}/close`,{reason:"resolved",note:$("#note").value})();
  if($("#fal"))$("#fal").onclick=()=>act(`/cases/${id}/close`,{reason:"false_alarm",note:$("#note").value})();
  await wireCaseMsg(c)});


