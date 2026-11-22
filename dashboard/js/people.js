"use strict";
// patients, case history, CSV import, data readiness, system status, device history, patient statistics, caregivers
// Loaded by index.html as plain scripts, in order: core, cases, people, network, system, install, admin (they share one global scope).
/* ---- patient priority: manual level (critical / watch) + automatic "presses often" ranking over 7 / 30 days ---- */
const PRI={critical:["วิกฤต","pr-critical"],watch:["เฝ้าระวัง","pr-watch"],normal:["ปกติ",""]};
const priBadge=l=>l&&l!=="normal"?`<span class="pr ${PRI[l][1]}">${l==="critical"?"🚨 ":"👁 "}${PRI[l][0]}</span>`:"";
const PSORT={name:"ลำดับลงทะเบียน",priority:"วิกฤตก่อน → กดบ่อย",week:"กดบ่อยสุดใน 7 วัน",month:"กดบ่อยสุดใน 30 วัน",recent:"กดล่าสุดก่อน"};
let patSort=lsGet("pat_sort")||"name",patFilter="all";
const PRK={critical:0,watch:1,normal:2},tms=x=>x?Date.parse(x):0;
function patOrder(a,b){const f={
  priority:()=>PRK[a.priority]-PRK[b.priority]||b.cases_7d-a.cases_7d||b.cases_30d-a.cases_30d,
  week:()=>b.cases_7d-a.cases_7d||b.cases_30d-a.cases_30d||PRK[a.priority]-PRK[b.priority],
  month:()=>b.cases_30d-a.cases_30d||b.cases_7d-a.cases_7d||PRK[a.priority]-PRK[b.priority],
  recent:()=>tms(b.last_case_at)-tms(a.last_case_at)}[patSort];
  return (f?f():0)||a.id-b.id}
const PFILT={all:["ทั้งหมด",()=>true],critical:["🚨 วิกฤต",p=>p.priority==="critical"],watch:["👁 เฝ้าระวัง",p=>p.priority==="watch"],week:["🔥 กดบ่อย (7 วัน)",p=>p.frequent_week],month:["🔥 กดบ่อย (30 วัน)",p=>p.frequent_month]};
const loadPatients=guard(async()=>{
  const [ps,cs,fq]=await Promise.all([api("/patients"),api("/caregivers"),role==="admin"?api("/settings").then(x=>x.frequent):null]);
  const view=ps.filter(PFILT[patFilter][1]).sort(patOrder);
  const opts=cs.filter(c=>c.active).map(c=>`<option value="${c.id}">${esc(c.full_name)}</option>`).join("");
  $("#main").innerHTML=`${canEdit()?`<div class="card"><h3>เพิ่มผู้ป่วย</h3><div class="row"><input id="n" placeholder="ชื่อ-สกุล"><input id="a" placeholder="ที่อยู่" size="30"><input id="v" placeholder="หมู่บ้าน"><input id="ph" placeholder="โทร"><input id="la" placeholder="ละติจูด" size="9"><input id="lo" placeholder="ลองจิจูด" size="9"><button class="btn p" id="add">เพิ่ม</button><button class="btn" id="imp" type="button">📥 นำเข้าหลายรายจากไฟล์</button></div></div>`:""}
  <div class="card"><div class="pfilter">${Object.entries(PFILT).map(([k,v])=>`<button class="chipb ${patFilter===k?"on":""}" data-pf="${k}">${v[0]} (${ps.filter(v[1]).length})</button>`).join("")}<span style="flex:1"></span>
    <span class="plg" title="แถบสีด้านซ้ายของแต่ละแถว"><span><i style="background:var(--p-critical)"></i>วิกฤต</span><span><i style="background:var(--p-watch)"></i>เฝ้าระวัง</span><span><i style="background:var(--p-normal)"></i>ปกติ</span></span>
    <label>เรียงตาม <select id="psort">${Object.entries(PSORT).map(([k,v])=>`<option value="${k}" ${patSort===k?"selected":""}>${v}</option>`).join("")}</select></label></div>
    ${role==="admin"?`<details style="margin-top:10px"><summary class="mut" style="cursor:pointer">เกณฑ์ "กดบ่อย" (ตอนนี้: ตั้งแต่ ${fq.week_threshold||"ปิด"} ครั้งใน 7 วัน · ตั้งแต่ ${fq.month_threshold||"ปิด"} ครั้งใน 30 วัน)</summary>
      <div class="row" style="margin-top:8px">ใน 7 วัน ≥ <input id="fqw" type="number" min="0" max="50" style="width:70px" value="${fq.week_threshold}"> ครั้ง · ใน 30 วัน ≥ <input id="fqm" type="number" min="0" max="200" style="width:70px" value="${fq.month_threshold}"> ครั้ง
      <label><input type="checkbox" id="fqf" ${fq.count_false_alarm?"checked":""}> นับเคสที่แจ้งผิดพลาดด้วย</label><button class="btn p" id="fqs">บันทึกเกณฑ์</button></div><p class="hint">ใส่ 0 = ปิดเกณฑ์นั้น · นับเฉพาะเคสจริงจากปุ่ม (ไม่รวมการทดสอบรายเดือน) · ผู้ป่วยที่ถึงเกณฑ์จะมีป้าย 🔥 กดบ่อย</p></details>`:""}</div>
  <div class="card"><table><tr><th>ชื่อ / ระดับ</th><th title="จำนวนเคสใน 7 วันล่าสุด">7 วัน</th><th title="จำนวนเคสใน 30 วันล่าสุด">30 วัน</th><th>ที่อยู่/โทร</th><th>อุปกรณ์</th><th>ผู้ดูแล (ลำดับแจ้ง)</th><th>PDPA</th><th></th></tr>${view.map(p=>`<tr data-rid="${p.id}" class="pri-${p.priority}"><td><button type="button" class="lnk" data-ps="${p.id}" title="ดูสถิติรายบุคคล">${esc(p.full_name)}</button><div style="margin-top:3px">${canEdit()&&!p.anonymized_at?`<select data-prio="${p.id}" title="ระดับความสำคัญ">${Object.entries(PRI).map(([k,v])=>`<option value="${k}" ${p.priority===k?"selected":""}>${v[0]}</option>`).join("")}</select>`:priBadge(p.priority)}${p.frequent_week||p.frequent_month?` <span class="fq" title="ถึงเกณฑ์กดบ่อย">🔥 กดบ่อย</span>`:""}</div></td>
    <td><b ${p.frequent_week?'class="bad"':""}>${p.cases_7d}</b></td><td><b ${p.frequent_month?'class="bad"':""}>${p.cases_30d}</b>${p.last_case_at?`<div class="mut" style="font-size:12px">ล่าสุด ${fmt(p.last_case_at)}</div>`:""}</td><td>${esc(p.address)}<br>${esc(p.phone)}</td><td>${esc(p.device||"-")}</td><td>${p.assignments.map(a=>a.priority+". "+esc(a.name)).join("<br>")||'<span class="mut">ยังไม่กำหนด</span>'}</td><td>${p.anonymized_at?'<span class="bad">ลบแล้ว</span>':p.consent_ok?'<span class="ok">✔ ยินยอม</span>':'<span class="bad">ไม่มีบันทึก</span>'}</td><td style="white-space:nowrap"><button class="btn" data-ps="${p.id}">📊 สถิติ</button> ${canEdit()&&!p.anonymized_at?`<button class="btn" data-id="${p.id}">กำหนดผู้ดูแล</button> `:""}${canEdit()&&!p.anonymized_at?`<button class="btn" data-pe="${p.id}">แก้ไข</button> `:""}${canEdit()?`<button class="btn" data-pv="${p.id}">ข้อมูล/PDPA</button>`:""}${role==="admin"?` <button class="btn r" data-pd="${p.id}">ลบ</button>`:""}</td></tr>`).join("")}</table></div>`;
  if($("#add"))$("#add").onclick=guard(async()=>{const num=v=>v===""?null:Number(v);await api("/patients",{method:"POST",body:{full_name:$("#n").value,address:$("#a").value,village:$("#v").value,phone:$("#ph").value,lat:num($("#la").value),lon:num($("#lo").value)}});loadPatients()});
  document.querySelectorAll("[data-pf]").forEach(b=>b.onclick=()=>{patFilter=b.dataset.pf;if(LST.patients)LST.patients.page=0;loadPatients()});
  $("#psort").onchange=e=>{patSort=e.target.value;lsSet("pat_sort",patSort);if(LST.patients)LST.patients.page=0;loadPatients()};
  document.querySelectorAll("select[data-prio]").forEach(sel=>sel.onchange=guard(async()=>{await api(`/patients/${sel.dataset.prio}/priority`,{method:"PUT",body:{priority:sel.value}});toast("ตั้งระดับแล้ว");loadPatients()}));
  if($("#fqs"))$("#fqs").onclick=guard(async()=>{await api("/settings/frequent",{method:"PUT",body:{week_threshold:+$("#fqw").value,month_threshold:+$("#fqm").value,count_false_alarm:$("#fqf").checked}});toast("บันทึกเกณฑ์แล้ว");loadPatients()});
  const downNote=lv=>{const sel=[...(LST.patients?LST.patients.sel:[])].map(pById).filter(Boolean),d=sel.filter(p=>p.priority==="critical"||(lv==="normal"&&p.priority==="watch"));
    return d.length?`\n⚠️ รวมผู้ป่วย${d.some(p=>p.priority==="critical")?"วิกฤต":"เฝ้าระวัง"} ${d.length} ราย ที่จะถูกลดระดับลง`:""};
  const pBody=(p,o)=>({full_name:p.full_name,age:p.age,address:p.address,village:p.village,phone:p.phone,lat:p.lat,lon:p.lon,notes:p.notes,active:p.active,...o}),pById=i=>ps.find(x=>x.id==i);
  lister("patients",$("#main").lastElementChild,{noun:"ราย",reload:loadPatients,bulk:[
    {label:"🚨 ตั้งเป็นวิกฤต",only:i=>!pById(i).anonymized_at&&pById(i).priority!=="critical",run:i=>api(`/patients/${i}/priority`,{method:"PUT",body:{priority:"critical"}})},
    {label:"👁 เฝ้าระวัง",only:i=>!pById(i).anonymized_at&&pById(i).priority!=="watch",confirm:n=>`ตั้งเป็น "เฝ้าระวัง" ${n} ราย?`+downNote("watch"),run:i=>api(`/patients/${i}/priority`,{method:"PUT",body:{priority:"watch"}})},
    {label:"ตั้งเป็นปกติ",only:i=>!pById(i).anonymized_at&&pById(i).priority!=="normal",confirm:n=>`ตั้งเป็น "ปกติ" ${n} ราย?`+downNote("normal"),run:i=>api(`/patients/${i}/priority`,{method:"PUT",body:{priority:"normal"}})},
    {label:"เปิดใช้งาน",only:i=>!pById(i).anonymized_at&&!pById(i).active,run:i=>api("/patients/"+i,{method:"PUT",body:pBody(pById(i),{active:true})})},
    {label:"ปิดใช้งาน",only:i=>!pById(i).anonymized_at&&pById(i).active,confirm:n=>`ปิดใช้งาน ${n} ราย? ระบบจะไม่รับแจ้งเหตุของผู้ป่วยกลุ่มนี้`,run:i=>api("/patients/"+i,{method:"PUT",body:pBody(pById(i),{active:false})})},
    ...(role==="admin"?[{label:"ลบ",cls:"r",confirm:n=>`ลบผู้ป่วย ${n} ราย?\nลบได้เฉพาะรายที่ไม่มีอุปกรณ์/ประวัติเคส (รายอื่นจะถูกข้ามและแจ้งผล)`,run:i=>api("/patients/"+i,{method:"DELETE"})}]:[])]});
  document.querySelectorAll("button[data-pv]").forEach(b=>b.onclick=()=>patientPrivacy(ps.find(x=>x.id==b.dataset.pv)));
  document.querySelectorAll("button[data-pd]").forEach(b=>b.onclick=guard(async()=>{const p=ps.find(x=>x.id==b.dataset.pd);if(!await ask(`ลบผู้ป่วย "${p.full_name}" ?\nลบได้เฉพาะรายที่ไม่มีอุปกรณ์/ประวัติเคส — ถ้ามีประวัติ ให้ใช้ ข้อมูล/PDPA → ลบข้อมูลที่ระบุตัวตน`))return;await api("/patients/"+p.id,{method:"DELETE"});toast("ลบแล้ว");loadPatients()}));
  document.querySelectorAll("[data-ps]").forEach(b=>b.onclick=()=>patientStats(+b.dataset.ps));
  if($("#imp"))$("#imp").onclick=()=>importView("patients");
  document.querySelectorAll("button[data-pe]").forEach(b=>b.onclick=()=>{const p=ps.find(x=>x.id==b.dataset.pe);devEdit=true;
    $("#main").innerHTML=`<button class="btn" id="back">← กลับ</button><div class="card"><h3>แก้ไขผู้ป่วย #${p.id}</h3>
     <div class="row">ชื่อ-สกุล <input id="en" value="${esc(p.full_name)}" maxlength="128"> อายุ <input id="eg" size="3" value="${p.age??""}"></div>
     <div class="row">ที่อยู่ <input id="ea" size="40" value="${esc(p.address)}"> หมู่บ้าน <input id="ev" value="${esc(p.village)}"> โทร <input id="ep" value="${esc(p.phone)}"></div>
     <div class="row">ละติจูด <input id="ela" size="10" value="${p.lat??""}"> ลองจิจูด <input id="elo" size="10" value="${p.lon??""}"></div>
     <div class="row">หมายเหตุ <input id="eno" size="50" value="${esc(p.notes)}"></div>
     <div class="row"><label><input type="checkbox" id="eac" ${p.active?"checked":""}> เปิดใช้งาน (ถ้าปิด ระบบจะไม่รับแจ้งเหตุของผู้ป่วยรายนี้)</label></div>
     <div class="row"><button class="btn p" id="esv">บันทึก</button></div></div>`;crumb(`แก้ไขผู้ป่วย #${p.id}`);
    $("#back").onclick=()=>{devEdit=false;render()};
    $("#esv").onclick=guard(async()=>{const num=v=>v.trim()===""?null:Number(v);const age=num($("#eg").value),la=num($("#ela").value),lo=num($("#elo").value);
      if([age,la,lo].some(v=>v!==null&&isNaN(v))){toast("อายุ/ละติจูด/ลองจิจูดต้องเป็นตัวเลข");return}
      await api("/patients/"+p.id,{method:"PUT",body:{full_name:$("#en").value,age,address:$("#ea").value,village:$("#ev").value,phone:$("#ep").value,lat:la,lon:lo,notes:$("#eno").value,active:$("#eac").checked}});toast("บันทึกแล้ว");devEdit=false;render()})});
  document.querySelectorAll("button[data-id]").forEach(b=>b.onclick=()=>{const p=ps.find(x=>x.id==b.dataset.id);
    $("#main").innerHTML=`<button class="btn" id="back">← กลับ</button><div class="card"><h3>ผู้ดูแลของ ${esc(p.full_name)}</h3><p class="mut">ลำดับที่ 1 จะได้รับแจ้งก่อน ถ้าไม่ตอบรับภายในเวลาที่ตั้งไว้ จะแจ้งคนถัดไป</p>${[1,2,3].map(i=>`<div class="row">ลำดับ ${i}: <select id="s${i}"><option value="">- ไม่ระบุ -</option>${opts}</select></div>`).join("")}<button class="btn p" id="sv">บันทึก</button></div>`;crumb(`ผู้ดูแลของ ${p.full_name}`);
    p.assignments.forEach(a=>{$("#s"+a.priority).value=a.caregiver_id});
    $("#back").onclick=()=>render();$("#sv").onclick=guard(async()=>{const ids=[1,2,3].map(i=>$("#s"+i).value).filter(Boolean).map(Number);await api(`/patients/${p.id}/assignments`,{method:"PUT",body:{caregiver_ids:ids}});toast("บันทึกแล้ว");render()})})});

// ---------------------------------------------------------------- case history (server-side search + paging)
function historyFilters(q=""){return {from:"",to:"",state:"",reason:"",village:"",q,offset:0}}
let CH=historyFilters();const CH_PAGE=50;
async function apiList(path){const r=await fetch("/api"+path,{headers:{authorization:"Bearer "+token}});if(!r.ok){let m=r.status;try{m=(await r.json()).detail||m}catch(e){}throw new Error(m)}return {rows:await r.json(),total:+r.headers.get("X-Total-Count")||0}}
const caseHistory=guard(async()=>{devEdit=true;
  const vs=[...new Set((await api("/patients")).map(p=>p.village).filter(Boolean))].sort();
  $("#main").innerHTML=`<button class="btn" id="back">← กลับ</button><div class="card"><h3>ค้นหาประวัติเคส</h3>
    <div class="fbar"><label>ตั้งแต่วันที่<input type="date" id="hf" value="${CH.from}"></label><label>ถึงวันที่<input type="date" id="ht" value="${CH.to}"></label>
      <label>สถานะ<select id="hs"><option value="">ทั้งหมด</option>${Object.entries(ST).map(([k,v])=>`<option value="${k}" ${CH.state===k?"selected":""}>${v}</option>`).join("")}</select></label>
      <label>ผล<select id="hr"><option value="">ทั้งหมด</option><option value="resolved" ${CH.reason==="resolved"?"selected":""}>แก้ไขแล้ว</option><option value="false_alarm" ${CH.reason==="false_alarm"?"selected":""}>แจ้งผิดพลาด</option></select></label>
      <label>หมู่บ้าน<select id="hv"><option value="">ทั้งหมด</option>${vs.map(v=>`<option ${CH.village===v?"selected":""}>${esc(v)}</option>`).join("")}</select></label>
      <label>ค้นหา (ชื่อผู้ป่วย / ปุ่ม / เลขเคส)<input id="hq" value="${esc(CH.q)}" size="22"></label>
      <button class="btn p" id="hgo">ค้นหา</button><button class="btn" id="hcl">ล้างตัวกรอง</button></div></div><div class="card" id="hres"></div>`;
  crumb("ค้นหาประวัติเคส");
  const run=guard(async()=>{const q=new URLSearchParams({limit:CH_PAGE,offset:CH.offset});
    if(CH.from)q.set("from",CH.from);if(CH.to)q.set("to",CH.to);if(CH.state)q.set("state",CH.state);if(CH.reason)q.set("reason",CH.reason);if(CH.village)q.set("village",CH.village);if(CH.q)q.set("q",CH.q);
    const {rows,total}=await apiList("/cases?"+q),a=CH.offset+1,b=CH.offset+rows.length;
    $("#hres").innerHTML=rows.length?`<div class="row" style="justify-content:space-between"><b>พบ ${total.toLocaleString()} เคส</b><span class="mut">แสดง ${a}–${b}</span></div>
      <table><tr><th>#</th><th>ผู้ป่วย</th><th>สถานะ / ผล</th><th>เวลาแจ้งเหตุ</th><th>ผู้รับเรื่อง</th><th></th></tr>${rows.map(c=>`<tr><td>#${c.id}</td><td>${esc(c.patient?c.patient.full_name:"(ไม่ทราบผู้ป่วย) "+c.device.dev_eui)}<div class="mut">${esc(c.patient?.village||"")}</div></td><td><span class="tag ${c.state}">${ST[c.state]}</span>${c.close_reason==="false_alarm"?' <span class="tag">แจ้งผิดพลาด</span>':""}${c.after_hours?' <span class="tag">นอกเวลา</span>':""}</td><td>${fmt(c.created_at)}</td><td>${esc(c.accepted_by||"-")}</td><td><button class="btn" data-hc="${c.id}">รายละเอียด</button></td></tr>`).join("")}</table>
      <div class="row" style="justify-content:flex-end"><button class="btn" id="hp" ${CH.offset?"":"disabled"}>‹ ก่อนหน้า</button><button class="btn" id="hn" ${b<total?"":"disabled"}>ถัดไป ›</button></div>`:'<p class="mut">ไม่พบเคสตามเงื่อนไขนี้</p>';
    document.querySelectorAll("[data-hc]").forEach(x=>x.onclick=()=>{devEdit=false;openCaseAfter=+x.dataset.hc;render();scrollTo(0,0)});
    if($("#hp"))$("#hp").onclick=()=>{CH.offset=Math.max(0,CH.offset-CH_PAGE);run()};if($("#hn"))$("#hn").onclick=()=>{CH.offset+=CH_PAGE;run()}});
  const read=()=>{CH={from:$("#hf").value,to:$("#ht").value,state:$("#hs").value,reason:$("#hr").value,village:$("#hv").value,q:$("#hq").value.trim(),offset:0}};
  $("#hgo").onclick=()=>{read();run()};$("#hq").onkeydown=e=>{if(e.key==="Enter"){read();run()}};
  $("#hcl").onclick=()=>{CH=historyFilters();caseHistory()};
  $("#back").onclick=()=>{devEdit=false;render()};run()});

// ---------------------------------------------------------------- bulk import from CSV
const IMP={patients:["ผู้ป่วย","ราย",["ชื่อ-สกุล","อายุ","หมู่บ้าน","โทร","ระดับ"],d=>[d.full_name,d.age??"",d.village,d.phone,PRI[d.priority]?.[0]||""]],
  caregivers:["อสม./ผู้ดูแล","คน",["ชื่อ-สกุล","บทบาท","โทร"],d=>[d.full_name,d.role,d.phone]]};
const IMPS={new:["เพิ่มใหม่","grn"],duplicate:["มีอยู่แล้ว — ข้าม","amb"],duplicate_in_file:["ซ้ำในไฟล์ — ข้าม","amb"],error:["ข้อมูลผิด — ข้าม","red"]};
function importView(kind){const [noun,unit,cols,cells]=IMP[kind];devEdit=true;
  $("#main").innerHTML=`<button class="btn" id="back">← กลับ</button><div class="card"><h3>นำเข้า${noun}จากไฟล์</h3>
    <ol style="margin:0 0 10px;padding-left:20px;line-height:1.8"><li><button class="lnk" id="itpl" type="button">ดาวน์โหลดไฟล์ตัวอย่าง</button> แล้วเปิดด้วย Excel หรือ Google Sheets</li><li>กรอกข้อมูล บรรทัดละ 1 ${unit} (ห้ามลบบรรทัดหัวตาราง) แล้วบันทึกเป็น <b>CSV UTF-8</b></li><li>เลือกไฟล์ด้านล่าง หรือคัดลอกตารางจาก Excel มาวางในช่องข้อความ แล้วกด "ตรวจสอบ"</li></ol>
    <div class="row"><input type="file" id="ifile" accept=".csv,.txt,.tsv,text/csv"></div><textarea id="impt" placeholder="หรือวางข้อมูลที่คัดลอกจาก Excel ที่นี่"></textarea>
    <div class="row"><button class="btn p" id="ichk">ตรวจสอบ</button><span class="mut">ยังไม่บันทึกอะไรจนกว่าจะกดยืนยัน · ชื่อที่มีอยู่แล้วจะถูกข้าม ไม่เขียนทับ</span></div></div><div id="ires"></div>`;
  crumb(`นำเข้า${noun}`);$("#back").onclick=()=>{devEdit=false;render()};
  $("#itpl").onclick=guard(async()=>{const r=await fetch(`/api/import/${kind}/template.csv`,{headers:{authorization:"Bearer "+token}});if(!r.ok)throw new Error("ดาวน์โหลดไม่สำเร็จ");dl(await r.blob(),`sos-import-${kind}.csv`)});
  $("#ifile").onchange=async e=>{const f=e.target.files[0];if(!f)return;const buf=await f.arrayBuffer();let t=new TextDecoder("utf-8").decode(buf);
    if(t.includes("�"))try{t=new TextDecoder("windows-874").decode(buf)}catch(_){}   // Excel's plain "CSV" on Thai Windows
    $("#impt").value=t};
  const show=(r,done)=>{const n=r.counts.new;
    $("#ires").innerHTML=`<div class="card"><h3>${done?`นำเข้าแล้ว ${r.created} ${unit}`:"ผลการตรวจสอบ"}</h3>
      <div class="row"><span class="pill grn">เพิ่มใหม่ ${n}</span><span class="pill amb">ข้าม (ซ้ำ) ${r.counts.duplicate+r.counts.duplicate_in_file}</span><span class="pill red">ข้อมูลผิด ${r.counts.error}</span>${r.ignored_columns.length?`<span class="mut">คอลัมน์ที่ไม่ใช้: ${r.ignored_columns.map(esc).join(", ")}</span>`:""}</div>
      ${!done&&n?`<div class="row"><button class="btn p" id="icommit">ยืนยันนำเข้า ${n} ${unit}</button></div>`:""}${done&&kind==="patients"&&r.created?'<p class="mut">ขั้นต่อไป: บันทึกความยินยอม (PDPA) กำหนด อสม. และผูกปุ่มให้ผู้ป่วยใหม่ — ดูรายการที่ยังขาดได้ที่เมนู "ความพร้อมข้อมูล"</p>':""}
      <table><tr><th>บรรทัด</th><th>ผล</th>${cols.map(c=>`<th>${c}</th>`).join("")}<th>หมายเหตุ</th></tr>${r.rows.map(x=>`<tr><td>${x.line}</td><td><span class="pill ${IMPS[x.status][1]}">${IMPS[x.status][0]}</span></td>${cells(x.data).map(v=>`<td>${esc(v)}</td>`).join("")}<td class="bad">${x.errors.map(esc).join("<br>")}</td></tr>`).join("")}</table></div>`;
    if($("#icommit"))$("#icommit").onclick=guard(async()=>{if(!await ask(`นำเข้า${noun} ${n} ${unit}?`))return;show(await api(`/import/${kind}`,{method:"POST",body:{csv:$("#impt").value,dry_run:false}}),true);toast("นำเข้าแล้ว")})};
  $("#ichk").onclick=guard(async()=>{if(!$("#impt").value.trim()){toast("ยังไม่ได้เลือกไฟล์หรือวางข้อมูล","warn");return}show(await api(`/import/${kind}`,{method:"POST",body:{csv:$("#impt").value,dry_run:true}}),false)})}

// ---------------------------------------------------------------- data readiness
const loadReady=guard(async()=>{const r=await api("/readiness");if(page!=="ready")return;
  const pct=r.total?Math.round(r.ready/r.total*100):0,LV={red:["เสี่ยง: อาจแจ้งเหตุไม่ถึงใคร","red"],amb:["ยังขาดบางรายการ","amb"],grn:["พร้อม","grn"]},lg=r.line?.gaps||[],lineOk=!!r.line?.ready;
  $("#main").innerHTML=`<div class="tiles">${tile("ผู้ป่วยที่เปิดใช้งาน",r.total,"ราย")}${tile("ข้อมูลครบ พร้อมใช้งาน",r.ready,"ราย",`${pct}% ของทั้งหมด`,"grn")}${tile("เสี่ยงแจ้งเหตุไม่ถึง",r.at_risk,"ราย","ขาดผู้ดูแล / LINE / ปุ่ม",r.at_risk?"red":"")}${tile("ผู้รับ LINE ต้องแก้",lg.length,"คน",lineOk?"เลือกแจ้งเตือนแล้วแต่ยังไม่ผูก LINE":"LINE ของระบบยังไม่พร้อม",!lineOk||lg.length?"red":"grn")}${tile("ปุ่มสำรองที่ยังว่าง",r.spare_devices,"เครื่อง")}</div>
  <div class="card"><h3>ความพร้อมของการแจ้งเตือน</h3><p>${lineOk?'<span class="ok">✅ LINE ของระบบพร้อมส่ง</span>':'<span class="bad">❌ LINE ของระบบยังไม่พร้อม</span>'} ${role==="admin"?`<button class="btn sm" id="rline">${lineOk?"ตรวจการเชื่อมต่อ LINE":"ไปตั้งค่า LINE"}</button>`:""}</p>${lg.length?`<ul class="flags">${lg.map(c=>`<li class="red"><b>${esc(c.full_name)}</b> ยังไม่ได้ผูก LINE · ตั้งให้รับ: ${esc(c.reasons.join(", "))}${role==="admin"?` <button class="btn sm" data-rline="${c.user_id||""}">${c.user_id?"ไปหน้าผู้ใช้":"ไปหน้า อสม./ผู้ดูแล"}</button>`:""}</li>`).join("")}</ul>`:'<p class="mut">ผู้รับทุกคนที่ระบบตั้งไว้ผูก LINE แล้ว</p>'}</div>
  <div class="card"><h3>ภาพรวมแต่ละรายการ</h3><div class="cols2">${r.checks.map(c=>`<div><div class="row" style="justify-content:space-between;margin:0"><span>${esc(c.label)}${c.critical?' <span class="mut" title="ถ้าขาด การแจ้งเหตุอาจไม่ถึงใคร">(สำคัญ)</span>':""}</span><b>${c.ok}/${c.total}</b></div><div class="prog"><i class="${c.ok===c.total?"":c.critical?"red":"amb"}" style="width:${c.total?c.ok/c.total*100:0}%"></i></div></div>`).join("")}</div>
    ${r.caregivers_no_line.length?`<ul class="flags">${r.caregivers_no_line.map(c=>`<li class="${c.in_use?"red":""}">${esc(c.full_name)} ยังไม่ได้ผูก LINE${c.in_use?" — มีผู้ป่วยในความดูแลแต่จะไม่ได้รับแจ้งเหตุ":""}</li>`).join("")}</ul>`:""}</div>
  <div class="card"><h3>รายผู้ป่วย (เรียงจากรายที่ต้องแก้ก่อน)</h3><div style="overflow-x:auto"><table class="rdy"><tr><th style="max-width:none">ผู้ป่วย</th><th style="max-width:none">สถานะ</th>${r.checks.map(c=>`<th>${esc(c.label)}</th>`).join("")}</tr>
    ${r.patients.map(p=>`<tr data-rid="${p.id}"><td><button type="button" class="lnk" data-ps="${p.id}">${esc(p.full_name)}</button> ${priBadge(p.priority)}<div class="mut">${esc(p.village||"")}</div></td><td><span class="pill ${LV[p.level][1]}">${LV[p.level][0]}</span></td>${r.checks.map(c=>`<td class="c">${p.checks[c.key]?'<span class="ck" title="มีแล้ว">✓</span>':`<span class="ck ${c.critical?"no":"soft"}" title="ยังไม่มี">✕</span>`}</td>`).join("")}</tr>`).join("")}</table></div>
    <p class="mut">✓ มีแล้ว · <span class="ck no" style="width:16px;height:16px;font-size:10px">✕</span> ขาดรายการสำคัญ · <span class="ck soft" style="width:16px;height:16px;font-size:10px">✕</span> ควรเพิ่ม — กดชื่อเพื่อดูรายละเอียดของผู้ป่วย</p></div>`;
  document.querySelectorAll("[data-ps]").forEach(b=>b.onclick=()=>{page="patients";render();patientStats(+b.dataset.ps)});
  if($("#rline"))$("#rline").onclick=()=>{page=lineOk?"integrations":"integrations";render()};
  document.querySelectorAll("[data-rline]").forEach(b=>b.onclick=()=>{page=b.dataset.rline?"users":"caregivers";render()});
  lister("ready",$("#main").lastElementChild,{noun:"ราย"})});

// ---------------------------------------------------------------- system status (admin)
const fsize=n=>n>1048576?(n/1048576).toFixed(1)+" MB":Math.ceil(n/1024)+" KB";
const loadSysHealth=guard(async()=>{
  if(role!=="admin"){$("#main").innerHTML='<div class="warn">เฉพาะผู้ดูแลระบบ</div>';return}
  const h=await api("/system/health");if(page!=="sysh")return;const b=h.backup,q=h.queue||{},hb=h.heartbeat,m=h.monitoring;
  const okT=(label,ok,good,bad)=>tile(label,ok?"ปกติ":"มีปัญหา","",ok?good:bad,ok?"grn":"red");
  $("#main").innerHTML=`<div class="tiles">${okT("ฐานข้อมูล",h.db,"เชื่อมต่อได้","เชื่อมต่อไม่ได้")}${okT("ตัวจัดการเคส",h.loop,"ทำงานทุก 5 วินาที","หยุดทำงาน — เคสจะไม่ถูกส่งต่อ")}${okT("รับสัญญาณจากเกตเวย์ (MQTT)",h.mqtt,"เชื่อมต่ออยู่",`ขาดมาแล้ว ${durTxt(h.mqtt_down_s)}`)}${okT("คิวส่ง LINE",h.line_queue,`รอส่ง ${q.pending??0} · ส่งแล้ว 24 ชม. ${q.sent_24h??0}`,`ค้าง ${q.pending??0} ข้อความ นาน ${durTxt(q.oldest_s||0)}`)}${tile("LINE ส่งไม่สำเร็จ 24 ชม.",q.failed_24h??0,"ข้อความ",q.last_error?esc(q.last_error.slice(0,60)):"",q.failed_24h?"amb":"")}${tile("เปิดทำงานมาแล้ว",durTxt(h.uptime_s),"")}</div>
  <div class="card"><h3>สำรองข้อมูล</h3>${!b.known?`<ul class="flags"><li class="red">${esc(b.problem)}</li></ul>`:`${b.problem?`<ul class="flags"><li class="red">${esc(b.problem)}</li></ul>`:'<ul class="flags"><li class="grn">สำรองข้อมูลล่าสุดสำเร็จ</li></ul>'}
    <dl class="kvl" style="margin-top:10px"><dt>ครั้งล่าสุด</dt><dd>${fmt(b.at)} (${b.age_h<1?"ไม่ถึง 1 ชั่วโมงก่อน":Math.round(b.age_h)+" ชั่วโมงก่อน"})</dd><dt>เข้ารหัสไฟล์</dt><dd>${b.encrypted?'<span class="ok">เข้ารหัสแล้ว</span>':'<span class="bad">ยังไม่เข้ารหัส</span> — สร้างไฟล์กุญแจตามหัวไฟล์ infra/scripts/backup.sh'}</dd>
    <dt>สำเนานอกเครื่อง</dt><dd>${b.offsite.configured?(b.offsite.ok?`<span class="ok">ส่งแล้ว</span> (${esc(b.offsite.detail)})`:`<span class="bad">ไม่สำเร็จ</span> ${esc(b.offsite.detail)}`):'<span class="bad">ยังไม่ได้ตั้งค่า</span> — ถ้าดิสก์เครื่องนี้เสีย ข้อมูลสำรองจะหายไปด้วย (ตั้ง BACKUP_REMOTE)'}</dd>
    <dt>ไฟล์</dt><dd>${b.files.map(f=>`${esc(f.name)} (${fsize(f.size)})`).join("<br>")||"-"}</dd><dt>เก็บย้อนหลัง</dt><dd>${b.keep_days??"-"} วัน</dd></dl>`}</div>
  <div class="card"><h3>เฝ้าระวังจากภายนอก</h3><p class="mut">ถ้าเซิร์ฟเวอร์ ไฟฟ้า หรืออินเทอร์เน็ตของเครื่องนี้ดับ ระบบจะแจ้งเตือนตัวเองไม่ได้ จึงต้องมีตัวเฝ้าจากข้างนอก เลือกทำอย่างน้อย 1 อย่าง</p>
    <ul class="flags"><li class="${hb.configured?(hb.ok===false?"red":"grn"):""}"><b>1) ส่งสัญญาณชีพออกไป (heartbeat)</b> — ${hb.configured?(hb.at?`ส่งล่าสุด ${fmt(hb.at)} · ${esc(hb.detail)}`:"ตั้งค่าแล้ว รอรอบส่งแรก"):"ยังไม่ได้ตั้งค่า"}<br><span class="mut">สมัครบริการแบบ dead-man's switch (เช่น healthchecks.io) แล้ววาง URL ที่ได้ด้านล่าง ระบบจะส่งสัญญาณเฉพาะตอนที่ทุกส่วนปกติ ถ้าสัญญาณหาย บริการนั้นจะแจ้งเตือนคุณ</span></li>
    <li><b>2) ให้เครื่องอื่นมาตรวจ</b> — ตั้ง uptime monitor หรือรัน <code>infra/scripts/watchdog.sh</code> จากเครื่องอื่น ชี้มาที่ <code>${location.origin}/healthz?strict=1</code></li></ul>
    <div class="fbar" style="margin-top:12px"><label style="flex:1 1 320px">URL สำหรับส่ง heartbeat (https://… เว้นว่าง = ปิด)<input id="mhu" value="${esc(m.heartbeat_url||"")}" placeholder="https://hc-ping.com/xxxxxxxx"></label><label>ส่งทุก (นาที)<input id="mhi" type="number" min="1" max="60" value="${m.interval_min}" style="width:90px"></label><label>แจ้งเตือนเมื่อไม่มี backup เกิน (ชั่วโมง)<input id="mba" type="number" min="12" max="240" value="${m.backup_max_age_h}" style="width:110px"></label><button class="btn p" id="msv">บันทึก</button></div></div>`;
  $("#msv").onclick=guard(async()=>{await api("/settings/monitoring",{method:"PUT",body:{heartbeat_url:$("#mhu").value.trim(),interval_min:+$("#mhi").value,backup_max_age_h:+$("#mba").value}});toast("บันทึกแล้ว");loadSysHealth()});
  clearInterval(timer);timer=setInterval(()=>{if(page==="sysh"&&document.activeElement?.tagName!=="INPUT")loadSysHealth();else if(page!=="sysh")clearInterval(timer)},30000)});

// ---------------------------------------------------------------- device history (battery / signal)
function lineChart(pts,key,o){const W=640,H=170,L=38,R=8,T=10,B=24,v=pts.filter(p=>p[key]!=null);
  if(v.length<2)return '<p class="mut">ยังมีข้อมูลไม่พอสำหรับวาดกราฟ (ต้องมีอย่างน้อย 2 รายงาน)</p>';
  const t0=Date.parse(v[0].at),t1=Date.parse(v[v.length-1].at)||t0+1,ys=v.map(p=>p[key]);
  let lo=o.min??Math.min(...ys),hi=o.max??Math.max(...ys);if(hi===lo){hi+=1;lo-=1}
  const x=t=>L+(Date.parse(t)-t0)/(t1-t0||1)*(W-L-R),y=n=>T+(hi-n)/(hi-lo)*(H-T-B),d=iso=>new Date(iso).toLocaleDateString("th-TH-u-ca-buddhist",{timeZone:"Asia/Bangkok",day:"numeric",month:"short"});
  return `<svg class="lch" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.label)}"><line class="ax" x1="${L}" y1="${T}" x2="${L}" y2="${H-B}"/><line class="ax" x1="${L}" y1="${H-B}" x2="${W-R}" y2="${H-B}"/>
    ${o.low!=null&&o.low>lo&&o.low<hi?`<line class="lo" x1="${L}" y1="${y(o.low)}" x2="${W-R}" y2="${y(o.low)}"/><text x="${W-R}" y="${y(o.low)-4}" text-anchor="end">ระดับเตือน ${o.low}${o.unit}</text>`:""}
    <polyline class="ln" points="${v.map(p=>`${x(p.at).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ")}"/>
    <text x="${L-5}" y="${T+4}" text-anchor="end">${Math.round(hi)}</text><text x="${L-5}" y="${H-B}" text-anchor="end">${Math.round(lo)}</text><text x="${L}" y="${H-6}">${d(v[0].at)}</text><text x="${W-R}" y="${H-6}" text-anchor="end">${d(v[v.length-1].at)}</text></svg>`}
const deviceHistory=guard(async(d,days)=>{devEdit=true;const r=await api(`/devices/${d.id}/telemetry?days=${days}`),s=r.summary,tr=s.trend;
  const trendTxt=tr.per_day==null?"ยังประเมินไม่ได้":tr.per_day<-0.05?`ลดลง ${Math.abs(tr.per_day)}% ต่อวัน`:"คงที่";
  $("#main").innerHTML=`<button class="btn" id="back">← กลับ</button>
  <div class="card"><div class="row" style="justify-content:space-between"><h3 style="margin:0">ประวัติอุปกรณ์ ${esc(d.label||d.dev_eui)}${d.patient?` · ${esc(d.patient)}`:""}</h3><label>ช่วงเวลา <select id="dhd">${[[7,"7 วัน"],[30,"30 วัน"],[90,"90 วัน"],[365,"1 ปี"]].map(([n,t])=>`<option value="${n}" ${n==days?"selected":""}>${t}</option>`).join("")}</select></label></div>
    <div class="tiles">${tile("แบตเตอรี่ล่าสุด",s.battery_now??"-",s.battery_now!=null?"%":"",s.battery_min!=null?`ต่ำสุดในช่วงนี้ ${s.battery_min}%`:"",s.battery_now!=null&&s.battery_now<=r.battery_low_pct?"red":"")}${tile("แนวโน้มแบต (ขณะใช้แบต)",trendTxt,"",tr.per_day==null?"ต้องมีรายงานขณะไม่ได้เสียบไฟอย่างน้อย 3 วัน":`คิดจาก ${tr.basis} รายงาน`)}${tile("คาดว่าถึงระดับเตือน",tr.days_to_low!=null?tr.days_to_low:"-",tr.days_to_low!=null?"วัน":"",tr.days_to_low!=null?`ระดับเตือน ${r.battery_low_pct}%`:s.battery_now!=null&&s.battery_now<=r.battery_low_pct?"ต่ำกว่าระดับเตือนแล้ว — ควรเปลี่ยน/ชาร์จแบต":"ไม่มีแนวโน้มลดลง",tr.days_to_low!=null&&tr.days_to_low<=7?"amb":"")}${tile("สัญญาณเฉลี่ย (RSSI)",s.rssi_avg??"-",s.rssi_avg!=null?"dBm":"",s.rssi_min!=null?`แย่สุด ${s.rssi_min} dBm`:"")}${tile("รายงานขณะไฟบ้านดับ",s.mains_off_reports,"ครั้ง")}${tile("จำนวนรายงาน",r.count,"ครั้ง")}</div></div>
  <div class="cols2"><div class="card"><h3>แบตเตอรี่ (%)</h3>${lineChart(r.points,"battery",{min:0,max:100,low:r.battery_low_pct,unit:"%",label:"กราฟแบตเตอรี่"})}</div>
    <div class="card"><h3>ความแรงสัญญาณ RSSI (dBm) — ยิ่งใกล้ 0 ยิ่งดี</h3>${lineChart(r.points,"rssi",{unit:" dBm",label:"กราฟสัญญาณ"})}</div></div>
  ${canEdit()?`<div class="card"><div class="row"><button class="btn" id="dhx">⬇ ดาวน์โหลดข้อมูลช่วงนี้ของทุกอุปกรณ์ (CSV)</button><span class="mut">สำหรับใช้ประกอบรายงานผลการทดลอง · เก็บประวัติเริ่มตั้งแต่วันที่อัปเดตระบบรุ่นนี้</span></div></div>`:""}`;
  crumb(`ประวัติอุปกรณ์ ${d.label||d.dev_eui}`);$("#back").onclick=()=>{devEdit=false;render()};$("#dhd").onchange=e=>deviceHistory(d,+e.target.value);
  if($("#dhx"))$("#dhx").onclick=guard(async()=>{const ymd=t=>new Date(t).toLocaleDateString("sv-SE",{timeZone:"Asia/Bangkok"}),n=Math.min(days,366),to=ymd(Date.now()),fr=ymd(Date.now()-(n-1)*864e5);
    const x=await fetch(`/api/reports/telemetry.csv?from=${fr}&to=${to}`,{headers:{authorization:"Bearer "+token}});if(!x.ok)throw new Error("ดาวน์โหลดไม่สำเร็จ ("+x.status+")");dl(await x.blob(),`sos-telemetry-${fr}-to-${to}.csv`)})});

// ---------------------------------------------------------------- per-patient statistics
const MTHS=["ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.","ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."],WDAY=["จันทร์","อังคาร","พุธ","พฤหัสบดี","ศุกร์","เสาร์","อาทิตย์"];
const durTxt=s=>s==null?"-":s<60?`${Math.round(s)} วินาที`:s<3600?`${Math.floor(s/60)} นาที${Math.round(s%60)?` ${Math.round(s%60)} วินาที`:""}`:`${Math.floor(s/3600)} ชม. ${Math.round(s%3600/60)} นาที`;
const tile=(label,val,unit,sub,cls)=>`<div class="tile ${cls||""}"><div class="tl">${label}</div><div class="tv${/[ก-๙]/.test(String(val))?" txt":""}">${val}${unit?`<small>${unit}</small>`:""}</div>${sub?`<div class="ts">${sub}</div>`:""}</div>`;
const hbars=rows=>{const mx=Math.max(1,...rows.map(r=>r[1]));return `<div class="hb">${rows.map(r=>`<span>${esc(r[0])}</span><span class="ht"><i style="width:${r[1]/mx*100}%"></i></span><span class="hn">${r[1]}</span>`).join("")}</div>`};
function patFlags(r){const f=[],t=r.totals,p=r.patient,d=r.device,c=r.caregivers;
  if(p.anonymized_at)return [["","ข้อมูลผู้ป่วยรายนี้ถูกลบ/ทำให้ไม่ระบุตัวตนแล้ว"]];
  if(t.open)f.push(["red",`มีเคสที่ยังไม่ปิด ${t.open} เคส`]);
  if(!c.length)f.push(["red","ยังไม่ได้กำหนด อสม./ผู้ดูแล — ถ้ากดปุ่มจะไม่มีใครได้รับแจ้งทาง LINE"]);
  else if(!c[0].line_bound||!c[0].active)f.push(["red",`ผู้ดูแลลำดับ 1 (${esc(c[0].name)}) ${c[0].active?"ยังไม่ได้ผูก LINE":"ถูกปิดใช้งาน"} — จะไม่ได้รับแจ้งเหตุ`]);
  if(!d)f.push(["red","ยังไม่มีปุ่มแจ้งเหตุที่ผูกกับผู้ป่วยรายนี้"]);
  else{if(d.link==="offline"||d.link==="never")f.push(["red",d.link==="never"?"ปุ่มยังไม่เคยส่งสัญญาณเข้าระบบ":"ปุ่มขาดการติดต่อ — อาจแจ้งเหตุไม่ได้ ควรโทรตรวจสอบ"]);
    else if(d.link==="late")f.push(["","ปุ่มส่งสัญญาณช้ากว่ากำหนด"]);
    if(d.battery!=null&&d.battery<=20)f.push(["",`แบตเตอรี่ปุ่มเหลือ ${d.battery}%`]);
    if(d.mains_ok===false)f.push(["","ปุ่มใช้ไฟจากแบตสำรองอยู่ (ไฟบ้านดับหรือถอดปลั๊ก)"])}
  if(!p.consent_ok)f.push(["","ยังไม่มีบันทึกความยินยอม (PDPA)"]);
  if(role!=="viewer"&&p.lat==null)f.push(["","ยังไม่ได้ปักหมุดตำแหน่งบ้าน"]);
  if(t.all>=4&&t.false_alarm/t.all>=.5)f.push(["",`แจ้งผิดพลาด ${t.false_alarm} จาก ${t.all} ครั้ง — ควรทบทวนวิธีใช้ปุ่มกับผู้ป่วย/ญาติ`]);
  const night=(r.dayparts.find(x=>x.key==="night")||{}).n||0;if(t.all>=4&&night/t.all>=.5)f.push(["",`กดปุ่มช่วงกลางคืนบ่อย (${night} จาก ${t.all} ครั้ง)`]);
  if(t.after_hours&&t.all>=3&&t.after_hours/t.all>=.6)f.push(["",`ส่วนใหญ่เกิดนอกเวลาราชการ (${t.after_hours} จาก ${t.all} ครั้ง)`]);
  if(d&&!r.tests)f.push(["","ยังไม่เคยทดสอบปุ่ม"]);
  if(!f.length)f.push(["grn","ข้อมูลครบและอุปกรณ์ทำงานปกติ"]);
  return f}
const patientStats=guard(async pid=>{
  const r=await api(`/patients/${pid}/stats`),p=r.patient,t=r.totals,rs=r.response,d=r.device,ms=r.months;devEdit=true;
  const cur=ms[ms.length-1].n,prev=ms[ms.length-2].n,mx=Math.max(1,...ms.map(m=>m.n));
  const trend=cur>prev?`<span class="bad">เพิ่มขึ้น</span>`:cur<prev?`<span class="ok">ลดลง</span>`:"เท่าเดิม";
  const sum=!t.all?`ยังไม่เคยกดปุ่มแจ้งเหตุ${r.tests?` (ทดสอบปุ่มแล้ว ${r.tests} ครั้ง)`:""}`
    :`กดปุ่มแจ้งเหตุมาแล้ว <b>${t.all}</b> ครั้ง · ครั้งล่าสุด <b>${fmt(r.last_case_at)}</b><br>เดือนนี้ <b>${cur}</b> ครั้ง เดือนก่อน <b>${prev}</b> ครั้ง (${trend})${rs.n_accepted?` · มีผู้รับเรื่องเฉลี่ยภายใน <b>${durTxt(rs.avg_accept_s)}</b>`:""}`;
  const link={ok:["ปกติ","ok"],late:["ส่งสัญญาณช้า",""],offline:["ขาดการติดต่อ","bad"],never:["ยังไม่เคยส่งสัญญาณ","bad"]};
  const out=[["แก้ไขแล้ว/ช่วยเหลือแล้ว",t.resolved],["แจ้งผิดพลาด",t.false_alarm],["ผู้ป่วยยกเลิกเอง",t.cancelled],["ยังไม่ปิด",t.open]];
  $("#main").innerHTML=`<button class="btn" id="back">← กลับ</button>
  <div class="card"><h3>${esc(p.full_name)} ${priBadge(p.priority)}${p.active?"":' <span class="mut">(ปิดใช้งาน)</span>'}</h3>
    <div class="mut">${[p.age!=null?`อายุ ${p.age} ปี`:"",p.village?`หมู่บ้าน ${esc(p.village)}`:"",p.phone?`โทร ${esc(p.phone)}`:""].filter(Boolean).join(" · ")}</div>
    <p class="sum">${sum}</p>
    <ul class="flags">${patFlags(r).map(x=>`<li class="${x[0]}">${x[1]}</li>`).join("")}</ul></div>
  <div class="tiles">${tile("7 วันล่าสุด",t.d7,"ครั้ง")}${tile("30 วันล่าสุด",t.d30,"ครั้ง")}${tile("12 เดือนล่าสุด",t.d365,"ครั้ง")}${tile("ทั้งหมด",t.all,"ครั้ง",r.first_case_at?`ตั้งแต่ ${fmt(r.first_case_at).split(" ")[0]}`:"")}
    ${tile("รับเรื่องเฉลี่ย",rs.n_accepted?durTxt(rs.avg_accept_s):"-","",rs.n_accepted?`ช้าสุด ${durTxt(rs.max_accept_s)}`:"ยังไม่มีข้อมูล")}${tile("เกินเวลารับเรื่อง",t.overdue,"ครั้ง","ไม่มีผู้รับภายในเวลาที่ตั้งไว้",t.overdue?"amb":"")}${tile("นอกเวลาราชการ",t.after_hours,"ครั้ง")}${tile("แจ้งผิดพลาด",t.false_alarm,"ครั้ง",t.all?`คิดเป็น ${Math.round(t.false_alarm/t.all*100)}%`:"")}</div>
  <div class="card"><h3>จำนวนครั้งที่กดปุ่ม รายเดือน (12 เดือนล่าสุด)</h3>
    <div class="bch" role="img" aria-label="กราฟจำนวนเคสรายเดือน">${ms.map((m,i)=>{const [y,mo]=m.month.split("-").map(Number);return `<div class="bc1 ${i===ms.length-1?"cur":""}" title="${MTHS[mo-1]} ${y+543}: ${m.n} ครั้ง${m.false_alarm?` (ผิดพลาด ${m.false_alarm})`:""}"><span class="bn ${m.n?"":"z"}">${m.n}</span><span class="bb" style="height:${m.n/mx*100}%">${m.false_alarm?`<i style="height:${m.false_alarm/m.n*100}%"></i>`:""}</span><span class="bx">${MTHS[mo-1]}${mo===1||i===0?" "+String(y+543).slice(2):""}</span></div>`}).join("")}</div>
    <div class="lg"><span><i style="background:var(--pri)"></i>เหตุจริง</span><span><i style="background:var(--amb)"></i>แจ้งผิดพลาด</span><span>ไม่นับการทดสอบปุ่มประจำเดือน</span></div></div>
  <div class="cols2">
    <div class="card"><h3>กดปุ่มช่วงเวลาใด</h3>${t.all?hbars(r.dayparts.map(x=>[x.label,x.n])):'<p class="mut">ยังไม่มีข้อมูล</p>'}</div>
    <div class="card"><h3>กดปุ่มวันใด</h3>${t.all?hbars(r.weekdays.map((n,i)=>[WDAY[i],n])):'<p class="mut">ยังไม่มีข้อมูล</p>'}</div>
    <div class="card"><h3>ผลของแต่ละครั้ง</h3>${t.all?hbars(out):'<p class="mut">ยังไม่มีข้อมูล</p>'}</div>
    <div class="card"><h3>ผู้ดูแลและอุปกรณ์</h3>
      <p><b>ผู้ดูแล (ลำดับแจ้ง)</b><br>${r.caregivers.map(c=>`${c.priority}. ${esc(c.name)} ${c.active?(c.line_bound?'<span class="ok">ผูก LINE แล้ว</span>':'<span class="bad">ยังไม่ผูก LINE</span>'):'<span class="bad">ปิดใช้งาน</span>'}`).join("<br>")||'<span class="bad">ยังไม่กำหนด</span>'}</p>
      <p><b>ปุ่มแจ้งเหตุ</b><br>${d?`${esc(d.label||d.dev_eui)} · สถานะ <span class="${link[d.link][1]}">${link[d.link][0]}</span>${d.battery!=null?` · แบต ${d.battery}%`:""}<br><span class="mut">ส่งสัญญาณล่าสุด ${fmt(d.last_seen)}</span>`:'<span class="bad">ยังไม่มี</span>'}</p>
      <p><b>ทดสอบปุ่ม</b><br>${r.tests?`${r.tests} ครั้ง · ล่าสุด ${fmt(r.last_test_at)}`:'<span class="mut">ยังไม่เคยทดสอบ</span>'}</p></div></div>
  <div class="card"><h3>10 ครั้งล่าสุด</h3>${r.recent.length?`<table><tr><th>เคส</th><th>วันเวลา</th><th>ผล</th><th>รับเรื่องภายใน</th><th>ผู้รับเรื่อง</th></tr>${r.recent.map(c=>`<tr><td><button type="button" class="lnk" data-oc="${c.id}">#${c.id}</button></td><td>${fmt(c.created_at)}${c.after_hours?' <span class="mut">(นอกเวลา)</span>':""}</td><td>${c.close_reason==="false_alarm"?"แจ้งผิดพลาด":esc(ST[c.state]||c.state)}</td><td>${c.accept_s!=null?durTxt(c.accept_s):'<span class="mut">-</span>'}${c.overdue?' <span class="bad">เกินเวลา</span>':""}</td><td>${esc(c.accepted_by||"-")}</td></tr>`).join("")}</table>`:'<p class="mut">ยังไม่มีเคส</p>'}</div>`;
  crumb(`สถิติ: ${p.full_name}`);scrollTo(0,0);
  $("#back").onclick=()=>{devEdit=false;render()};
  document.querySelectorAll("[data-oc]").forEach(b=>b.onclick=()=>{devEdit=false;openCaseAfter=+b.dataset.oc;page="cases";render();scrollTo(0,0)})});

const loadCaregivers=guard(async()=>{
  const [cs,sm]=await Promise.all([api("/caregivers"),api("/caregivers/summary")]);
  const mxw=Math.max(1,...cs.map(c=>c.n_patients||0)),noLine=cs.filter(c=>c.active&&c.n_patients&&!c.line_bound);
  const wl=c=>!c.n_patients?`<span class="mut">ยังไม่มีผู้ป่วยในความดูแล</span>`:`<div class="wl"><div class="wn">${c.n_patients}<small>ราย</small></div><div class="wt"><i style="width:${c.n_patients/mxw*100}%"></i></div>
    <div class="ws">ผู้ดูแลหลัก ${c.n_primary} · สำรอง ${c.n_backup}${c.n_critical?` · <span class="bad">วิกฤต ${c.n_critical}</span>`:""}${c.n_watch?` · เฝ้าระวัง ${c.n_watch}`:""}${c.cases_notified_30d?`<br>ได้รับแจ้งเหตุ 30 วัน: ${c.cases_notified_30d} เคส`:""}</div>
    <details><summary>ดูรายชื่อ</summary><ol>${c.patients.map(x=>`<li><button type="button" class="lnk" data-ps="${x.id}">${esc(x.full_name)}</button> <span class="mut">${x.order===1?"หลัก":"สำรองลำดับ "+x.order}${x.village?" · "+esc(x.village):""}</span> ${priBadge(x.priority)}</li>`).join("")}</ol></details></div>`;
  const rt=[];
  if(!sm.system_admins)rt.push('<li class="amb">ยังไม่ได้กำหนดผู้รับ “แจ้งเตือนระบบ” (ผู้ดูแลระบบ) — ตอนนี้ระบบจะส่งไปที่ “ผู้รับสุดท้าย” แทน · กด “แก้ไข” ที่ชื่อผู้ดูแลระบบแล้วติ๊กเลือก</li>');
  else if(sm.system_admins_line<sm.system_admins)rt.push('<li class="red">ผู้รับแจ้งเตือนระบบบางคนยังไม่ได้ผูก LINE — จะไม่ได้รับแจ้งเตือน</li>');
  if(sm.central&&sm.central_line<sm.central)rt.push('<li class="red">เจ้าหน้าที่ส่วนกลางบางคนยังไม่ได้ผูก LINE — จะไม่ได้รับแจ้งเหตุ</li>');
  $("#main").innerHTML=`<div class="tiles">${tile("อสม./ผู้ดูแลที่เปิดใช้งาน",sm.active,"คน",`ผูก LINE แล้ว ${sm.line_bound} คน`,sm.line_bound<sm.active?"amb":"")}${tile("ผู้ป่วยที่มีผู้ดูแลแล้ว",`${sm.patients_covered}/${sm.patients}`,"ราย",sm.patients_uncovered?`ยังไม่มีผู้ดูแล ${sm.patients_uncovered} ราย`:"ครบทุกราย",sm.patients_uncovered?"red":"grn")}${tile("เฉลี่ยต่อ อสม.",sm.avg_per_caregiver,"ราย/คน",`มากที่สุด ${sm.max_per_caregiver} ราย`)}${tile("อสม. ที่ยังไม่มีผู้ป่วย",sm.without_patients,"คน")}</div>
  ${sm.uncovered.length||noLine.length||rt.length?`<div class="card"><h3>ต้องดำเนินการ</h3><ul class="flags">${rt.join("")}${sm.uncovered.length?`<li class="red">ผู้ป่วยที่ยังไม่มี อสม./ผู้ดูแล (กดปุ่มแล้วจะไม่มีใครได้รับ LINE): ${sm.uncovered.map(x=>`<button type="button" class="lnk" data-ps="${x.id}">${esc(x.full_name)}</button>`).join(", ")}</li>`:""}${noLine.map(c=>`<li class="red">${esc(c.full_name)} ดูแลผู้ป่วย ${c.n_patients} ราย แต่ยังไม่ได้ผูก LINE — จะไม่ได้รับแจ้งเหตุ</li>`).join("")}</ul></div>`:""}
  <div class="card"><h3>ใครได้รับการแจ้งเตือนอะไร</h3><table class="rtb"><tr><th>เหตุการณ์</th><th>ผู้ที่ได้รับ LINE</th></tr>
    <tr><td>🚨 ผู้ป่วยกดปุ่มแจ้งเหตุ</td><td><b>อสม. ที่ดูแลผู้ป่วยรายนั้น</b> (ตามลำดับ ถ้าไม่รับเรื่องจะแจ้งคนถัดไป) และ <span class="pill grn">ส่วนกลาง</span> ทุกคนพร้อมกัน ${sm.central?`(${sm.central} คน)`:'<span class="bad">— ยังไม่ได้กำหนด</span>'}</td></tr>
    <tr><td>🔋 ปุ่มของผู้ป่วยมีปัญหา<div class="mut">เงียบหาย · แบตต่ำ · ไฟบ้านดับ</div></td><td>${sm.device_alert?`<span class="pill amb">ปุ่มผู้ป่วย</span> ผู้ที่เลือกรับเรื่องนี้ (${sm.device_alert} คน)`:'<span class="pill amb">แจ้งเตือนระบบ</span> (ยังไม่มีใครเลือกรับเรื่องปุ่มโดยเฉพาะ)'} ${sm.device_to_caregiver?"และ อสม. ลำดับ 1 ของผู้ป่วยรายนั้น":""} <span class="mut">(เปลี่ยนได้ที่ ตั้งค่า → แจ้งเตือนอุปกรณ์)</span></td></tr>
    ${sm.overdue?`<tr><td>⏰ เคสที่ยังไม่มีคนรับเรื่อง<div class="mut">เลยเวลารับเรื่องที่ตั้งไว้</div></td><td>ผู้ใช้ระบบที่เลือก “เฉพาะเคสที่ยังไม่มีคนรับเรื่อง” (${sm.overdue} คน)</td></tr>`:""}
    <tr><td>📡 เกตเวย์ · เซิร์ฟเวอร์ · สำรองข้อมูล</td><td><span class="pill amb">แจ้งเตือนระบบ</span> เท่านั้น ${sm.system_admins?`(${sm.system_admins} คน)`:'<span class="bad">— ยังไม่ได้กำหนด</span>'}</td></tr></table>
    <p class="mut" style="margin-bottom:0">ปิดเคสผ่าน LINE ได้: ผู้ที่กดรับเรื่อง และเจ้าหน้าที่ส่วนกลาง · กำหนดได้ที่ปุ่ม “แก้ไข” ของแต่ละคน (เฉพาะผู้ดูแลระบบ) · เจ้าหน้าที่และผู้ดูแลระบบผูก LINE ของตัวเองและเลือกเรื่องที่รับได้ที่เมนู “บัญชีของฉัน”</p></div>
  ${canEdit()?`<div class="card"><h3>เพิ่ม อสม./ผู้ดูแล</h3><div class="row"><input id="n" placeholder="ชื่อ-สกุล"><input id="r" value="อสม."><input id="ph" placeholder="โทร"><button class="btn p" id="add">เพิ่ม</button><button class="btn" id="imp" type="button">📥 นำเข้าหลายคนจากไฟล์</button></div></div>`:""}
  <div class="card"><table><tr><th>ชื่อ</th><th>บทบาท</th><th>โทร</th><th>LINE</th><th>ผู้ป่วยที่ดูแล</th><th></th></tr>${cs.map(c=>`<tr data-rid="${c.id}"><td style="min-width:150px">${esc(c.full_name)}${c.active?"":" (ปิดใช้งาน)"}${c.user_id||c.is_central||c.system_alert||c.alert_device||c.alert_overdue?`<div style="margin-top:4px">${c.user_id?'<span class="pill" title="บัญชีผู้ใช้ของแดชบอร์ดที่ผูก LINE เพื่อรับการแจ้งเตือน (กำหนดที่ บัญชีของฉัน หรือหน้า ผู้ใช้)">ผู้ใช้ระบบ</span> ':""}${c.is_central?'<span class="pill grn" title="รับแจ้งเหตุของผู้ป่วยทุกราย และปิดเคสผ่าน LINE ได้">ส่วนกลาง</span> ':""}${c.alert_overdue?'<span class="pill grn" title="รับเฉพาะเคสที่เลยเวลาแล้วยังไม่มีคนรับเรื่อง">เคสที่ไม่มีคนรับ</span> ':""}${c.alert_device?'<span class="pill amb" title="รับเรื่องปุ่มเงียบหาย แบตต่ำ ไฟบ้านดับ">ปุ่มผู้ป่วย</span> ':""}${c.system_alert?'<span class="pill amb" title="รับแจ้งเตือนระบบ: เกตเวย์ เซิร์ฟเวอร์ สำรองข้อมูล">แจ้งเตือนระบบ</span>':""}</div>`:""}</td><td>${esc(c.role)}</td><td>${esc(c.phone)}</td><td style="white-space:nowrap">${c.line_bound?"✅ ผูกแล้ว":"❌ ยังไม่ผูก"}</td><td>${wl(c)}</td><td><div class="acts">${canEdit()?`<button class="btn" data-bind="${c.id}">สร้างรหัสผูก LINE</button>${c.line_bound?` <button class="btn" data-un="${c.id}">ยกเลิกผูก</button>`:""} <button class="btn" data-ce="${c.id}">แก้ไข</button> <button class="btn" data-ct="${c.id}">${c.active?"ปิดใช้งาน":"เปิดใช้งาน"}</button>`:""}${role==="admin"?` <button class="btn r" data-cd="${c.id}">ลบ</button>`:""}</div></td></tr>`).join("")}</table><p class="mut">วิธีผูก: กดสร้างรหัส → ให้ อสม. เพิ่มเพื่อนบัญชี LINE ของ รพ.สต. แล้วพิมพ์รหัสนั้นส่งไป (รหัสใช้ได้ 30 นาที ครั้งเดียว)</p></div>`;
  document.querySelectorAll("[data-ps]").forEach(b=>b.onclick=()=>patientStats(+b.dataset.ps));
  if($("#imp"))$("#imp").onclick=()=>importView("caregivers");
  if($("#add"))$("#add").onclick=guard(async()=>{await api("/caregivers",{method:"POST",body:{full_name:$("#n").value,role:$("#r").value,phone:$("#ph").value}});loadCaregivers()});
  document.querySelectorAll("[data-bind]").forEach(b=>b.onclick=guard(async()=>{const r=await api(`/caregivers/${b.dataset.bind}/bind-code`,{method:"POST"});dlg({icon:"integrations",title:`ผูก LINE: ${r.name||""}`,html:bindHTML(r),ok:"ปิด",cancel:false}).then(()=>loadCaregivers?.())}));
  document.querySelectorAll("[data-un]").forEach(b=>b.onclick=guard(async()=>{if(await ask("ยกเลิกการผูก LINE?")){await api(`/caregivers/${b.dataset.un}/unbind`,{method:"POST"});loadCaregivers()}}));
  const cgBody=(c,o)=>({full_name:c.full_name,role:c.role,phone:c.phone,active:c.active,...o}),cById=i=>cs.find(x=>x.id==i);
  lister("caregivers",$("#main").lastElementChild,{noun:"คน",reload:loadCaregivers,bulk:[
    {label:"เปิดใช้งาน",only:i=>!cById(i).active,run:i=>api("/caregivers/"+i,{method:"PUT",body:cgBody(cById(i),{active:true})})},
    {label:"ปิดใช้งาน",only:i=>cById(i).active,confirm:n=>`ปิดใช้งาน ${n} คน? จะไม่ได้รับแจ้งเหตุจนกว่าจะเปิดใหม่`,run:i=>api("/caregivers/"+i,{method:"PUT",body:cgBody(cById(i),{active:false})})},
    {label:"ยกเลิกผูก LINE",only:i=>cById(i).line_bound,run:i=>api(`/caregivers/${i}/unbind`,{method:"POST"})},
    ...(role==="admin"?[{label:"ลบ",cls:"r",confirm:n=>`ลบ ${n} คน?\nคนที่เคยได้รับแจ้งเหตุแล้วจะถูกข้าม (ให้ปิดใช้งานแทน)`,run:i=>api("/caregivers/"+i,{method:"DELETE"})}]:[])]});
  document.querySelectorAll("[data-ct]").forEach(b=>b.onclick=guard(async()=>{const c=cs.find(x=>x.id==b.dataset.ct);
    if(c.active&&!await ask(`ปิดใช้งาน ${c.full_name}?\nจะไม่ได้รับแจ้งเหตุอีกจนกว่าจะเปิดใหม่`))return;
    await api("/caregivers/"+c.id,{method:"PUT",body:cgBody(c,{active:!c.active})});toast(c.active?"ปิดใช้งานแล้ว":"เปิดใช้งานแล้ว");loadCaregivers()}));
  document.querySelectorAll("[data-cd]").forEach(b=>b.onclick=guard(async()=>{const c=cs.find(x=>x.id==b.dataset.cd);
    if(!await ask(`ลบ ${c.full_name} ?\nจะถูกถอดออกจากผู้ป่วยทุกราย — ถ้าเคยได้รับแจ้งเหตุแล้วจะลบไม่ได้ (ให้ปิดใช้งานแทน)`))return;
    await api("/caregivers/"+c.id,{method:"DELETE"});toast("ลบแล้ว");loadCaregivers()}));
  document.querySelectorAll("[data-ce]").forEach(b=>b.onclick=()=>{const c=cs.find(x=>x.id==b.dataset.ce);devEdit=true;
    $("#main").innerHTML=`<button class="btn" id="back">← กลับ</button><div class="card"><h3>แก้ไข อสม./ผู้ดูแล</h3>
     <div class="row">ชื่อ-สกุล <input id="cn" value="${esc(c.full_name)}" maxlength="128"> บทบาท <input id="cr" value="${esc(c.role)}"> โทร <input id="cp" value="${esc(c.phone)}"></div>
     <div class="row"><label><input type="checkbox" id="cac" ${c.active?"checked":""}> เปิดใช้งาน (รับแจ้งเหตุ)</label></div>
     <h3 style="margin-top:14px">การแจ้งเตือนทาง LINE ที่คนนี้ได้รับ</h3>
     <div class="optl"><label><input type="checkbox" checked disabled> <span><b>แจ้งเหตุจากผู้ป่วยที่ตนดูแล</b><small>ได้รับอัตโนมัติเมื่อถูกกำหนดเป็นผู้ดูแลในหน้าผู้ป่วย${c.n_patients?` (ตอนนี้ ${c.n_patients} ราย)`:" (ตอนนี้ยังไม่มี)"}</small></span></label>
      <label><input type="checkbox" id="ccen" ${c.is_central?"checked":""} ${role==="admin"?"":"disabled"}> <span><b>เจ้าหน้าที่ส่วนกลาง</b><small>ได้รับแจ้งเหตุของผู้ป่วย<u>ทุกราย</u>พร้อมกับ อสม. และปิดเคสใดก็ได้ผ่าน LINE</small></span></label>
      <label><input type="checkbox" id="csys" ${c.system_alert?"checked":""} ${role==="admin"?"":"disabled"}> <span><b>ผู้ดูแลระบบ (แจ้งเตือนระบบ)</b><small>เกตเวย์ขาดการเชื่อมต่อ · เซิร์ฟเวอร์ · สำรองข้อมูล</small></span></label>
      <label><input type="checkbox" id="cdev" ${c.alert_device?"checked":""} ${role==="admin"?"":"disabled"}> <span><b>ปัญหาของปุ่มผู้ป่วย (ทุกเครื่อง)</b><small>ปุ่มเงียบหาย · แบตต่ำ · ไฟบ้านดับ — ถ้ายังไม่มีใครเลือกรายการนี้ ระบบส่งให้ผู้รับแจ้งเตือนระบบแทน</small></span></label></div>
     ${role==="admin"?"":'<p class="mut">เฉพาะผู้ดูแลระบบที่เปลี่ยนสามรายการหลังได้</p>'}${c.line_bound?"":'<div class="warn">คนนี้ยังไม่ได้ผูก LINE — จะยังไม่ได้รับข้อความใด ๆ</div>'}
     <div class="row"><button class="btn p" id="csv">บันทึก</button></div></div>`;crumb("แก้ไข อสม./ผู้ดูแล");
    $("#back").onclick=()=>{devEdit=false;render()};
    $("#csv").onclick=guard(async()=>{await api("/caregivers/"+c.id,{method:"PUT",body:{full_name:$("#cn").value,role:$("#cr").value,phone:$("#cp").value,active:$("#cac").checked,...(role==="admin"?{is_central:$("#ccen").checked,system_alert:$("#csys").checked,alert_device:$("#cdev").checked}:{})}});toast("บันทึกแล้ว");devEdit=false;render()})})});

document.addEventListener("click",e=>{if(e.target&&e.target.id==="gwgo"){e.preventDefault();page="gateways";render()}});

const GWS={online:["🟢","ออนไลน์"],warning:["🟡","ออนไลน์ แต่มีข้อควรตรวจ"],offline:["🔴","ออฟไลน์"],unknown:["⚪","ยังไม่เคยเชื่อมต่อ"],disabled:["⛔","ปิดการเฝ้าระวัง"]};
const ago=iso=>{if(!iso)return "-";const s=(Date.now()-new Date(iso))/1000;return s<90?Math.max(0,Math.round(s))+" วินาทีที่แล้ว":s<5400?Math.round(s/60)+" นาทีที่แล้ว":s<172800?Math.round(s/3600)+" ชม.ที่แล้ว":Math.round(s/86400)+" วันที่แล้ว"};
const upt=s=>s==null?"-":s<3600?Math.round(s/60)+" นาที":s<172800?(s/3600).toFixed(1)+" ชม.":Math.round(s/86400)+" วัน";
let gwStatsDays=7;
const loadGateways=guard(async()=>{
  const [gs,aiOk,ns]=await Promise.all([api("/gateways"),aiReady(),api(`/net/gateways?days=${gwStatsDays}`)]);if(page!=="gateways")return;
  const why=g=>g.status==="offline"?(g.reason==="radio"?(g.critical.join("; ")||"ตัวรับสัญญาณ LoRa ใช้ไม่ได้"):"ไม่ได้รับสัญญาณเกินเวลาที่กำหนด"):g.critical.concat(g.warnings).join("; ");
  const statsCard=`<div class="card"><div class="row"><h3 style="margin:0">สถิติการรับสัญญาณ</h3><label class="mut">ย้อนหลัง <select id="gwdays">${[7,30,90].map(d=>`<option value="${d}" ${d===gwStatsDays?"selected":""}>${d} วัน</option>`).join("")}</select></label><span class="mut">ระบบบันทึกได้ตั้งแต่เปิดฟีเจอร์นี้ เฟรมหนึ่งนับให้ทุกเกตเวย์ที่ ChirpStack ระบุว่าได้รับ</span></div>${ns.gateways.length?`<table><tr><th>เกตเวย์</th><th>ข้อความ/วัน</th><th>อุปกรณ์ที่ได้ยิน</th><th>สัดส่วนเฟรมเครือข่ายที่ได้ยิน</th><th>RSSI / SNR เฉลี่ย</th><th>ปริมาณรายวัน</th></tr>${ns.gateways.map(g=>{const vals=Object.values(g.daily),max=Math.max(1,...vals);return`<tr><td>${esc(g.label||"-")}<div class="mut"><code>${esc(g.gateway_id)}</code></div></td><td><b>${g.uplinks_per_day}</b></td><td>${g.devices}</td><td>${g.heard_pct==null?"-":g.heard_pct+"%"}</td><td>${g.rssi==null?"-":g.rssi+" dBm"} / ${g.snr==null?"-":g.snr+" dB"}</td><td title="${esc(vals.join(", "))}">${vals.length?vals.map(v=>`<span title="${v} เฟรม" style="display:inline-block;vertical-align:bottom;width:5px;height:${Math.max(3,Math.round(24*v/max))}px;margin-right:2px;background:var(--pri);border-radius:2px"></span>`).join(""):"-"}</td></tr>`}).join("")}</table>`:'<p class="mut">ยังไม่มีข้อมูลรับเฟรมจากเกตเวย์</p>'}<p class="mut">สัดส่วนเฟรม = จำนวนเฟรมที่เกตเวย์ได้รับเทียบกับข้อความขึ้นทั้งหมดในระบบ · ผู้พึ่งพาเกตเวย์ดูได้ในคอลัมน์ “อุปกรณ์” ด้านล่าง</p></div>`;
  $("#main").innerHTML=`${statsCard}${aiOk&&canEdit()&&gs.length?`<div class="card"><div class="row">${aiBtn("gateways","","AI วิเคราะห์เกตเวย์ทั้งหมด")}<span class="mut">ให้ AI ช่วยอ่านค่าสถานะ (ส่งเฉพาะค่าทางเทคนิค ไม่มีชื่อ/ตำแหน่ง)</span></div><div data-aibox="gateways:"></div></div>`:""}${role==="admin"?`<div class="card"><h3>เพิ่มเกตเวย์</h3><div class="row"><input id="gi" placeholder="Gateway ID 16 หลัก" size="24" title="ได้จาก sudo ./install-gateway.sh --show-id"><input id="gl" placeholder="ป้าย เช่น รพ.สต. ม.3"><button class="btn p" id="ga">เพิ่ม</button></div><p class="mut">ไม่ต้องเพิ่มเองก็ได้ ระบบจะเพิ่มให้เองเมื่อเกตเวย์เชื่อมต่อครั้งแรก — เพิ่มล่วงหน้าเพื่อตั้งป้ายชื่อ</p></div>`:""}
  <div class="card">${gs.length?`<table><tr><th>สถานะ</th><th>ป้าย / ID</th><th>เห็นล่าสุด</th><th>รายงานล่าสุด</th><th title="active = เปิดใช้งานแล้ว · ได้ยิน = ทุกเครื่องที่ส่งสัญญาณผ่านเกตเวย์นี้ล่าสุด">อุปกรณ์ (active / ได้ยิน)</th><th>เฟรมสะสม</th><th>CPU / ทำงานมาแล้ว</th><th>ตำแหน่ง</th><th>ข้อสังเกต</th><th></th></tr>${gs.map(g=>{const r=g.report||{};return`<tr data-rid="${g.id}"><td style="white-space:nowrap">${GWS[g.status][0]} ${GWS[g.status][1]}${g.maint?`<div class="gm-tag" title="${esc(g.maint.reason||"ไม่ส่ง LINE เรื่องเกตเวย์นี้ระหว่างซ่อม")}">🔧 ซ่อมบำรุง · ถึง ${esc(g.maint.end==="time"&&g.maint.until?hhmm(g.maint.until):g.maint.end==="manual"?"กดสิ้นสุด":"กลับมาออนไลน์")}</div>`:""}</td><td>${esc(g.label||"-")}<div class="mut"><code>${esc(g.gateway_id)}</code></div></td><td>${ago(g.last_seen)}</td><td>${ago(g.last_report_at)}</td><td><b>${g.devices}</b><span class="mut"> / ${g.devices_heard??g.devices}</span>${(g.devices_heard??0)>g.devices?`<br><button type="button" class="lnk" data-gwdev>${g.devices_heard-g.devices} เครื่องยังไม่ active</button>`:""}</td><td>${g.uplinks_total}</td><td>${r.cpu_temp_c!=null?r.cpu_temp_c+" °C":"-"} / ${upt(r.uptime_s)}</td><td>${g.loc.registered?gmap(g.loc.registered.lat,g.loc.registered.lon,"📍 แผนที่"):'<span class="mut">ยังไม่ระบุ</span>'}${g.loc.moved_m!=null?` <b style="color:var(--amb)">ย้าย ${dist(g.loc.moved_m)}?</b>`:""}</td><td style="color:${g.status==="offline"?"var(--red)":"var(--amb)"}">${esc(why(g))}</td><td>${canEdit()?`<button class="btn" data-gw="${g.id}">แก้ไข</button>`:""}</td></tr>`}).join("")}</table>`:'<p class="mut">ยังไม่มีเกตเวย์ — ติดตั้งตาม INSTALL-2 แล้วเกตเวย์จะปรากฏที่นี่เอง</p>'}
  <p class="mut">เกตเวย์ส่งรายงานตัวเองทุก 1 นาที ถ้าเงียบเกินที่ตั้งไว้ (หน้าตั้งค่า → ออฟไลน์) หรือตัวรับสัญญาณ LoRa เสียเกิน 3 นาที ระบบจะส่ง LINE แจ้งผู้รับที่เลือกไว้ในหน้าตั้งค่า</p></div>`;
  const gById=i=>gs.find(x=>x.id==i);
  lister("gateways",$("#main").lastElementChild,{noun:"เครื่อง",reload:loadGateways,bulk:role==="admin"?[
    {label:"เปิดเฝ้าระวัง",only:i=>gById(i).enabled===false,run:i=>api("/gateways/"+i,{method:"PUT",body:{enabled:true}})},
    {label:"ปิดเฝ้าระวัง",only:i=>gById(i).enabled!==false,confirm:n=>`ปิดเฝ้าระวัง ${n} เครื่อง? ระบบจะไม่ส่ง LINE เมื่อเครื่องเหล่านี้ขาดการเชื่อมต่อ`,run:i=>api("/gateways/"+i,{method:"PUT",body:{enabled:false}})},
    {label:"ลบ",cls:"r",confirm:n=>`ลบเกตเวย์ ${n} เครื่อง (และลบใน ChirpStack ถ้าตั้งค่าไว้)?\nถ้าเครื่องยังเปิดอยู่ จะถูกเพิ่มกลับเองเมื่อส่งข้อมูล`,run:i=>api("/gateways/"+i,{method:"DELETE"})}]:[]});
  aiWire($("#main"));
  if($("#gwdays"))$("#gwdays").onchange=e=>{gwStatsDays=+e.target.value;loadGateways()};
  if($("#ga"))$("#ga").onclick=guard(async()=>{const r=await api("/gateways",{method:"POST",body:{gateway_id:$("#gi").value,label:$("#gl").value}});toast("เพิ่มแล้ว"+csNote(r.chirpstack));loadGateways()});
  document.querySelectorAll("button[data-gw]").forEach(b=>b.onclick=guard(async()=>{const g=gs.find(x=>x.id==b.dataset.gw),r=g.report||{};devEdit=true;
    const [cgs,gwc,gwt,sets]=await Promise.all([role==="admin"?api("/caregivers"):Promise.resolve([]),role==="admin"?api(`/gateways/${g.id}/config`):Promise.resolve(null),api(`/gateways/${g.id}/timeline`),api("/settings").catch(()=>null)]);
    const sysGwLine=sets?.system_alert?.gateway_line!==false;
    const configuredIds=g.alert_ids?.length?g.alert_ids:(sets?.system_alert?.caregiver_ids||[]);
    const notifyIds=configuredIds.length?configuredIds:[sets?.escalation?.final_caregiver_id].filter(Boolean);
    const notifyPeople=cgs.filter(c=>c.active&&notifyIds.includes(c.id)),boundPeople=notifyPeople.filter(c=>c.line_bound);
    const notifyCard=role==="admin"?`<div class="card"><h3>ความพร้อมการแจ้งเตือน</h3><p>${sysGwLine?'<span class="ok">● เปิดส่ง LINE เรื่องเกตเวย์</span>':'<span class="bad">● ปิดส่ง LINE เรื่องเกตเวย์ทั้งระบบ</span>'} · ผู้รับที่ตั้งไว้ ${notifyPeople.length} คน · <b>${boundPeople.length} คนผูก LINE แล้ว</b></p>${!sysGwLine?'<p class="warn">เปิดได้ที่ ตั้งค่า → ผู้รับแจ้งเตือน → แจ้งเตือนเกตเวย์ทาง LINE</p>':!notifyPeople.length?'<p class="warn">ยังไม่มีผู้รับเฉพาะเกตเวย์นี้หรือผู้รับระบบ — ควรกำหนดอย่างน้อย 1 คน</p>':boundPeople.length<notifyPeople.length?`<p class="warn">ยังไม่ได้ผูก LINE: ${notifyPeople.filter(c=>!c.line_bound).map(c=>esc(c.full_name)).join(", ")}</p>`:'<p class="mut">ผู้รับทุกคนผูก LINE แล้ว</p>'}</div>`:"";
    const TLA={gateway_offline:"เกตเวย์ขาดการเชื่อมต่อ",gateway_online:"เกตเวย์กลับมาออนไลน์",gateway_offline_repeat:"เตือนเกตเวย์ซ้ำ",gateway_maint_on:"เริ่มซ่อมบำรุง",gateway_maint_change:"เปลี่ยนแผนซ่อมบำรุง",gateway_maint_off:"สิ้นสุดซ่อมบำรุง",gateway_maint_overdue:"ซ่อมบำรุงเกินเวลา",gateway_command:"ส่งคำสั่งระยะไกล",gateway_update:"แก้ไขข้อมูลเกตเวย์"};
    const timelineCard=`<div class="card"><h3>ไทม์ไลน์เกตเวย์</h3>${gwt.length?`<div class="tw"><table><tr><th>เวลา</th><th>เหตุการณ์</th><th>โดย</th></tr>${gwt.map(x=>`<tr><td>${fmt(x.at)}</td><td>${esc(TLA[x.action]||x.action)}${x.detail?.cmd?` <span class="mut">(${esc(x.detail.cmd)})</span>`:""}${x.detail?.reason?` <span class="mut">${esc(x.detail.reason)}</span>`:""}</td><td>${esc(x.actor)}</td></tr>`).join("")}</table></div>`:'<p class="mut">ยังไม่มีเหตุการณ์ที่บันทึกไว้</p>'}</div>`;
    const row=(k,v)=>`<tr><th style="width:230px">${k}</th><td>${v}</td></tr>`;
    $("#main").innerHTML=`<button class="btn" id="back">← กลับ</button><div class="card"><h3>${GWS[g.status][0]} ${esc(g.gateway_id)}</h3>
      <div class="row">ป้าย <input id="gl2" value="${esc(g.label)}" maxlength="64"></div>
      <div class="row"><label><input type="checkbox" id="gen" ${g.enabled?"checked":""} ${role==="admin"?"":"disabled"}> เฝ้าระวังเกตเวย์นี้ (ส่ง LINE เมื่อขาดการเชื่อมต่อ)</label>${role==="admin"?"":'<span class="mut">เฉพาะผู้ดูแลระบบ</span>'}</div>
      <p class="mut">ปิดการเฝ้าระวังเมื่อถอดเกตเวย์เครื่องนี้ออกจากระบบถาวร มิฉะนั้นระบบจะแจ้งว่าออฟไลน์</p>
      ${aiOk&&canEdit()?`<div class="row">${aiBtn("gateways",g.id,"AI วิเคราะห์เครื่องนี้")}</div><div data-aibox="gateways:${g.id}"></div>`:""}<div class="row"><button class="btn p" id="gsv">บันทึก</button>${role==="admin"?'<button class="btn r" id="gdl">ลบเกตเวย์นี้</button>':""}</div></div>
    <div class="card" id="gmc"><h3>🔧 ซ่อมบำรุง</h3>${maintHTML(g,sysGwLine)}<div id="gimp" class="gimp"><span class="mut">กำลังตรวจว่ามีผู้ป่วยคนไหนใช้เกตเวย์นี้…</span></div></div>
    <div class="card"><h3>ตั้งค่าเกตเวย์นี้</h3>
      <div class="row">หมายเหตุ <input id="gnt" value="${esc(g.notes)}" maxlength="500" size="40" placeholder="เช่น ติดบนหลังคา รพ.สต. ชั้น 2"></div>
      <div class="row">เตือนเมื่อเงียบเกิน <input id="gof" type="number" min="3" max="240" size="4" style="width:80px" value="${g.offline_min??""}" placeholder="${g.offline_min_effective}" ${role==="admin"?"":"disabled"}> นาที
        <span class="mut">${g.offline_min==null?"(ใช้ค่ากลางของระบบ)":"(ตั้งเฉพาะเครื่องนี้)"}</span>${role==="admin"&&g.offline_min!=null?'<button class="btn" id="gofr">ใช้ค่ากลาง</button>':""}</div>
      <div class="row"><b>ผู้รับแจ้งเตือนเกตเวย์นี้</b> <span class="mut">${g.alert_ids==null?"(ใช้ผู้รับตามหน้าตั้งค่า)":"(เลือกเฉพาะเครื่องนี้)"}</span></div>
      <div class="chips" id="gcg">${role==="admin"?(cgs.filter(c=>c.active).map(c=>`<label class="chip"><input type="checkbox" value="${c.id}" ${(g.alert_ids||[]).includes(c.id)?"checked":""}><span>${esc(c.full_name)}${c.line_bound?"":" (ยังไม่ผูก LINE)"}</span></label>`).join("")||'<span class="mut">ยังไม่มีผู้ดูแล</span>'):'<span class="mut">เฉพาะผู้ดูแลระบบ</span>'}</div>
      ${role==="admin"?`<div class="row"><button class="btn p" id="gss">บันทึกการตั้งค่า</button>${g.alert_ids!=null?'<button class="btn" id="gcr">ใช้ผู้รับตามหน้าตั้งค่า</button>':""}</div>`:`<div class="row"><button class="btn p" id="gss">บันทึกหมายเหตุ</button></div>`}
      <p class="hint">ถ้าไม่มีผู้รับที่ผูก LINE ไว้เลย ระบบจะแจ้งผู้ดูแลคนสุดท้ายที่ผูกไว้เสมอ เพื่อไม่ให้การแจ้งเตือนหายไปเงียบ ๆ</p></div>
    ${notifyCard}${timelineCard}${role==="admin"?`<div class="card" id="grm"><h3>สั่งงานระยะไกล</h3><div id="grb"><span class="mut">กำลังโหลด…</span></div></div>`:""}
    ${role==="admin"&&gwc?`<div class="card"><h3>ค่าที่ทำงานบน Gateway</h3>
      <p class="mut">ระบบส่งเฉพาะค่าที่อนุญาตพร้อมลายเซ็น Agent จะตรวจค่า สำรองไฟล์ ทดสอบ TOML และย้อนกลับอัตโนมัติถ้าบริการเริ่มใหม่ไม่สำเร็จ · Gateway ID, MQTT, CA และช่องความถี่แก้จากหน้านี้ไม่ได้</p>
      <div class="row">สถานะ ${gwc.status==="applied"?'<span class="ok">✅ ใช้ค่าแล้ว</span>':gwc.status==="pending"?'<span style="color:var(--amb)">⏳ รอ Gateway</span>':gwc.status==="failed"?'<span class="bad">❌ ไม่สำเร็จ</span>':'<span class="mut">ยังไม่เคยส่งค่า</span>'} · revision ${gwc.applied_version}/${gwc.version}${gwc.error?` · <span class="bad">${esc(gwc.error)}</span>`:""}</div>
      <div class="row"><label>อัตราขยายเสา <input id="gca" type="number" min="0" max="30" step="0.1" value="${gwc.desired.antenna_gain}"> dBi</label>
        <label>ระดับ log <select id="gcl">${["TRACE","DEBUG","INFO","WARN","ERROR"].map(x=>`<option ${x===gwc.desired.log_level?"selected":""}>${x}</option>`).join("")}</select></label>
        <label>รายงานทุก <input id="gci" type="number" min="30" max="3600" value="${gwc.desired.report_interval_s}"> วินาที</label></div>
      <div class="row"><label>ละติจูด <input id="gct" type="number" min="-90" max="90" step="0.000001" value="${gwc.desired.latitude}"></label>
        <label>ลองจิจูด <input id="gcn" type="number" min="-180" max="180" step="0.000001" value="${gwc.desired.longitude}"></label>
        <button class="btn p" id="gcsave">ส่งค่าไปใช้</button></div>
      <p class="hint">ค่าที่ Gateway รายงาน: ${Object.keys(gwc.reported||{}).length?`revision ${gwc.reported.version??"-"} · เสา ${gwc.reported.antenna_gain??"-"} dBi · ${esc(gwc.reported.log_level||"-")} · ทุก ${gwc.reported.report_interval_s??"-"} วินาที`:'Gateway รุ่นเดิมยังไม่รายงานค่าที่ใช้อยู่'}</p>
      <h4>เวอร์ชันซอฟต์แวร์</h4><div class="row">Bundle <code>${esc(gwc.versions.bundle||"-")}</code> · Agent <code>${esc(gwc.versions.agent||"-")}</code> · Reporter <code>${esc(gwc.versions.report||"-")}</code> · Concentratord <code>${esc(gwc.versions.concentratord||"-")}</code> · Forwarder <code>${esc(gwc.versions.forwarder||"-")}</code></div>
      ${gwc.releases.length?`<div class="row"><label>อัปเดตเป็น <select id="gwrel">${gwc.releases.map(x=>`<option value="${esc(x.version)}">${esc(x.version)}</option>`).join("")}</select></label><button class="btn" id="gwup">อัปเดต Gateway</button><span class="mut">ตรวจ SHA-256 และ rollback หากตรวจสุขภาพไม่ผ่าน</span></div>`:'<p class="mut">ยังไม่มี release ที่อนุมัติ — ตั้งค่า <code>SOS_GATEWAY_RELEASES_JSON</code> บน backend ก่อน</p>'}
    </div>`:""}
    <div class="card"><h3>ตำแหน่งเกตเวย์</h3>
      <p>ตำแหน่งที่ลงทะเบียน: ${g.loc.registered?`${g.loc.registered.lat.toFixed(5)}, ${g.loc.registered.lon.toFixed(5)} ${gmap(g.loc.registered.lat,g.loc.registered.lon)}`:'<span class="mut">ยังไม่ระบุ</span>'}</p>
      <p>GPS ของเกตเวย์: ${g.loc.gps?`${g.loc.gps.lat.toFixed(5)}, ${g.loc.gps.lon.toFixed(5)} ${gmap(g.loc.gps.lat,g.loc.gps.lon)} <span class="mut">(${ago(g.loc.gps.at)} · ดาวเทียม ${g.loc.gps.sats??"-"}${g.loc.gps.fresh?"":" · เก่า"})</span>`:`<span class="mut">${g.loc.gps_attached?"ติดตั้ง GPS แล้ว แต่ยังไม่ได้ตำแหน่ง (ต้องเห็นท้องฟ้า)":"ไม่มี GPS ติดตั้ง — ใส่ตำแหน่งเองด้านล่างได้"}</span>`}</p>
      ${g.loc.moved_m!=null?`<div class="warn">GPS ห่างจากตำแหน่งที่ลงทะเบียน ${dist(g.loc.moved_m)} — เกตเวย์อาจถูกย้ายหรือถูกนำออกไป</div>`:""}
      <div class="row">ละติจูด <input id="glat" size="10" value="${g.loc.registered?g.loc.registered.lat:""}"> ลองจิจูด <input id="glon" size="10" value="${g.loc.registered?g.loc.registered.lon:""}"></div>
      <div class="row"><button class="btn p" id="gls">บันทึกตำแหน่ง</button>${g.loc.gps?'<button class="btn" id="glu">ใช้ตำแหน่งจาก GPS</button>':""}${g.loc.gps&&canEdit()?'<button class="btn" id="gcs">ส่งพิกัด GPS ไป ChirpStack</button>':""}${g.loc.registered?'<button class="btn" id="glc">ล้างตำแหน่ง</button>':""}</div>
      <p class="mut">หาพิกัดได้จาก Google Maps (กดค้างบนแผนที่) · GPS ในอาคารไม่แม่น ควรลงทะเบียนตำแหน่งตอนติดตั้งแล้วใช้ GPS เทียบเพื่อดูว่าถูกย้ายหรือไม่</p></div>
    <div class="card"><h3>รายงานล่าสุดจากเกตเวย์</h3><table>${row("รับรายงานเมื่อ",fmt(g.last_report_at)+" ("+ago(g.last_report_at)+")")}${row("ที่อยู่ IP ในเครือข่าย",esc(r.ip||"-"))}${row("ทำงานมาแล้ว",upt(r.uptime_s))}${row("อุณหภูมิ CPU",r.cpu_temp_c!=null?r.cpu_temp_c+" °C":"-")}${row("ไฟเลี้ยง (get_throttled)",esc(r.throttled||"-")+' <span class="mut">(0x0 = ปกติ)</span>')}${row("พื้นที่การ์ดที่ใช้",r.disk_pct!=null?r.disk_pct+"%":"-")}${row("หน่วยความจำที่ใช้",r.mem_pct!=null?r.mem_pct+"%":"-")}${row("ตัวรับสัญญาณ LoRa (concentratord)",r.services?(r.services.concentratord?'<span class="ok">ทำงาน</span>':'<b class="bad">หยุด</b>'):"-")}${row("ตัวส่งต่อ MQTT (forwarder)",r.services?(r.services.forwarder?'<span class="ok">ทำงาน</span>':'<b class="bad">หยุด</b>'):"-")}${row("SPI ของ WM1302",r.spi==null?"-":r.spi?'<span class="ok">พบ</span>':'<b class="bad">ไม่พบ</b>')}${row("เวลา (NTP)",r.ntp==null?"-":r.ntp?"ซิงก์แล้ว":'<b class="bad">ยังไม่ซิงก์</b>')}${row("ใบรับรอง MQTT",r.tls_days==null?"-":r.tls_days<0?'<b class="bad">หมดอายุ '+(-r.tls_days)+' วัน</b>':r.tls_days<14?'<b style="color:var(--amb)">เหลือ '+r.tls_days+' วัน</b>':'เหลือ '+r.tls_days+' วัน')}${row("sos-gateway-agent",r.agent?'<span class="ok">ทำงาน · รับคำสั่งที่เซ็นลายเซ็นเท่านั้น</span>':'<span class="mut">ยังไม่ทำงาน</span>')}${row("รีสตาร์ตวิทยุอัตโนมัติสะสม",r.radio_fail??"-")}${row("ซอฟต์แวร์รายงาน",esc(r.sw||"-"))}</table></div>`;crumb(`เกตเวย์ ${g.gateway_id}`);
    $("#back").onclick=()=>{devEdit=false;render()};
    const glp=body=>guard(async()=>{await api("/gateways/"+g.id,{method:"PUT",body});toast("บันทึกแล้ว");devEdit=false;render()})();
    $("#gls").onclick=()=>{const la=$("#glat").value.trim(),lo=$("#glon").value.trim();if(la===""||lo===""||isNaN(+la)||isNaN(+lo)){toast("กรอกละติจูดและลองจิจูดเป็นตัวเลข");return}glp({lat:+la,lon:+lo})};
    if($("#glu"))$("#glu").onclick=()=>glp({use_gps:true});if($("#gcs"))$("#gcs").onclick=guard(async()=>{const r=await api(`/gateways/${g.id}/push-location`,{method:"POST"});toast(r.changed?"ส่งพิกัดไป ChirpStack แล้ว":"ChirpStack มีพิกัดนี้อยู่แล้ว")});if($("#glc"))$("#glc").onclick=()=>glp({clear_location:true});
    $("#gsv").onclick=guard(async()=>{const body={label:$("#gl2").value};if(role==="admin")body.enabled=$("#gen").checked;await api("/gateways/"+g.id,{method:"PUT",body});toast("บันทึกแล้ว");devEdit=false;render()});
    if($("#gdl"))$("#gdl").onclick=guard(async()=>{if(!await ask(`ลบเกตเวย์ ${g.gateway_id} ออกจากระบบ?\nถ้าเครื่องยังเปิดอยู่ มันจะถูกเพิ่มกลับเองเมื่อส่งข้อมูลครั้งถัดไป — ถ้าแค่ไม่ต้องการแจ้งเตือน ให้ปิด "เฝ้าระวัง" แทน`))return;const r=await api("/gateways/"+g.id,{method:"DELETE"});toast("ลบแล้ว"+csNote(r.chirpstack));devEdit=false;render()});
    $("#gss").onclick=()=>{const body={notes:$("#gnt").value};
      if(role==="admin"){const v=$("#gof").value.trim();if(v!==""){if(!(+v>=3&&+v<=240)){toast("เวลาออฟไลน์ต้องอยู่ระหว่าง 3–240 นาที");return}body.offline_min=Math.round(+v)}
        const ids=[...document.querySelectorAll("#gcg input:checked")].map(i=>+i.value);if(ids.length||g.alert_ids!=null)body.alert_ids=ids}
      glp(body)};
    if($("#gofr"))$("#gofr").onclick=()=>glp({reset_offline:true});
    if($("#gcsave"))$("#gcsave").onclick=guard(async()=>{const body={antenna_gain:+$("#gca").value,log_level:$("#gcl").value,latitude:+$("#gct").value,longitude:+$("#gcn").value,report_interval_s:+$("#gci").value};
      if(!await ask(`ส่งค่าตั้ง revision ใหม่ไปยัง ${g.label||g.gateway_id}?\nบริการ LoRa จะรีสตาร์ทประมาณ 10 วินาที และจะย้อนกลับเองหากเริ่มไม่สำเร็จ`))return;
      await api(`/gateways/${g.id}/config`,{method:"PUT",body});toast("ส่งค่าแล้ว กำลังรอ Gateway นำไปใช้","info");devEdit=false;render()});
    if($("#gwup"))$("#gwup").onclick=guard(async()=>{const version=$("#gwrel").value;if(!await ask(`อัปเดต Gateway เป็น ${version}?\nระบบจะตรวจ checksum และย้อนกลับไฟล์เดิมหากตรวจสุขภาพไม่ผ่าน`))return;
      await api(`/gateways/${g.id}/update`,{method:"POST",body:{version}});toast("ส่งคำสั่งอัปเดตแล้ว","info");devEdit=false;render()});
    maintWire(g,glp);if($("#gcr"))$("#gcr").onclick=()=>glp({reset_alerts:true});
    aiWire($("#main"));
    if(role==="admin")gwRemote(g)}))});

