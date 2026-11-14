"use strict";
// map, passwords, users, integrations, PDPA, reports, installed app + push
// Loaded by index.html as plain scripts, in order: core, cases, people, network, system, install, admin (they share one global scope).
/* ---- map: devices, gateways and the links between them (Leaflet, vendored in /vendor/leaflet) ---- */
let _lf=null;
function loadLeaflet(){
  if(window.L)return Promise.resolve();
  if(_lf)return _lf;
  _lf=new Promise((ok,no)=>{const c=document.createElement("link");c.rel="stylesheet";c.href="/vendor/leaflet/leaflet.css";document.head.appendChild(c);
    const s=document.createElement("script");s.src="/vendor/leaflet/leaflet.js";s.onload=ok;s.onerror=()=>{_lf=null;no(new Error("โหลดไลบรารีแผนที่ไม่สำเร็จ"))};document.head.appendChild(s)});
  return _lf}
const QCOL={good:"#16a34a",fair:"#d97706",weak:"#ea580c",bad:"#dc2626",unknown:"#94a3b8"},QTH={good:"ดีมาก",fair:"พอใช้",weak:"อ่อน",bad:"แย่มาก",unknown:"ไม่ทราบ"};
const PSRC={gps:"GPS (ล่าสุด)",home:"ที่อยู่ที่ลงทะเบียน",gps_old:"GPS (เก่ากว่ากำหนด)"};
const kmTxt=m=>m==null?"-":m>=1000?(m/1000).toFixed(2)+" กม.":m+" ม.";
const mapOpt={links:true,names:true,onlyOn:false,cov:true};let mapObj=null,mapFitted=false;
const loadMap=guard(async()=>{
  const [d]=await Promise.all([api("/map"),loadLeaflet()]);
  if(page!=="map")return;
  const gwById=Object.fromEntries(d.gateways.map(g=>[g.gateway_id,g])),dvById=Object.fromEntries(d.devices.map(x=>[x.id,x]));
  const noPosDev=d.devices.filter(x=>x.lat==null&&x.lifecycle!=="ready"),noPosGw=d.gateways.filter(g=>g.lat==null);
  if(!$("#mapc")){mapFitted=false;
    $("#main").innerHTML=`${d.restricted?'<div class="warn">บัญชี "ดูอย่างเดียว" ไม่แสดงตำแหน่งบ้านผู้ป่วย/อุปกรณ์ตามนโยบาย PDPA — เห็นเฉพาะเกตเวย์และตารางการเชื่อมต่อ</div>':""}
    <div class="card"><div class="row"><label class="chip"><input type="checkbox" id="mo1" ${mapOpt.links?"checked":""}><span>เส้นเชื่อมต่อ</span></label><label class="chip"><input type="checkbox" id="mo2" ${mapOpt.names?"checked":""}><span>ชื่อบนแผนที่</span></label><label class="chip"><input type="checkbox" id="mo3" ${mapOpt.onlyOn?"checked":""}><span>เฉพาะที่ออนไลน์</span></label><label class="chip"><input type="checkbox" id="mo4" ${mapOpt.cov?"checked":""}><span>รัศมีเกตเวย์ <b id="mcovkm" style="font-weight:inherit"></b></span></label><span id="mpin" style="margin-left:auto"></span><button class="btn" id="mfit">จัดให้พอดี</button><span class="mut" id="mupd"></span></div>
    <div class="row mut" style="font-size:13px"><b>เส้น:</b> <span style="color:${QCOL.good}">━ ดี</span> <span style="color:${QCOL.fair}">━ พอใช้</span> <span style="color:${QCOL.weak}">━ อ่อน</span> <span style="color:${QCOL.bad}">━ แย่/ใกล้หลุด</span> <span>┅ อุปกรณ์ออฟไลน์/ไม่ทราบสัญญาณ</span> · <b>จุด:</b> 📡 เกตเวย์ · ● อุปกรณ์ (ขอบแดง = ออฟไลน์) · <i class="pin sm red"></i> เคสเปิด · <i class="pin sm blu"></i> บ้านที่ยังไม่มีปุ่ม</div>
    <div id="mapc" style="height:62vh;min-height:380px;border-radius:12px;margin-top:8px;overflow:hidden;background:var(--bg)"></div><div class="hint" id="mapnote"></div></div><div id="maptb"></div>`;
    if(mapObj){try{mapObj.remove()}catch{}}
    mapObj=L.map("mapc",{zoomControl:true,attributionControl:true}).setView([13.9,100.6],11);
    let bad=0;const tl=L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19,attribution:"© OpenStreetMap",referrerPolicy:"strict-origin-when-cross-origin"});
    tl.on("tileerror",()=>{if(++bad===3)$("#mapnote").textContent="โหลดแผนที่พื้นหลังไม่ได้ (เครื่องนี้อาจไม่มีอินเทอร์เน็ต) — ยังแสดงตำแหน่งและเส้นเชื่อมต่อได้"});tl.addTo(mapObj);
    mapObj._lg=L.layerGroup().addTo(mapObj);
    ["mo1","mo2","mo3","mo4"].forEach((id,i)=>$("#"+id).onchange=e=>{mapOpt[["links","names","onlyOn","cov"][i]]=e.target.checked;mapRedraw()});
    mapObj.on("popupopen",e=>{const b=e.popup.getElement().querySelector("[data-case]");if(b)b.onclick=()=>{openCaseAfter=+b.dataset.case;page="cases";render()}});
    $("#mfit").onclick=()=>mapFit()}
  window._mapData=d;mapRedraw();mapPinUI(d);
  $("#mcovkm").textContent=(d.coverage_m/1000).toLocaleString("th-TH",{maximumFractionDigits:1})+" กม.";
  $("#mupd").textContent="อัปเดต "+new Date().toLocaleTimeString("th-TH",{timeZone:"Asia/Bangkok"});
  const row=x=>{const g=gwById[x.gateway_id],lk=d.links.find(l=>l.device_id===x.id&&l.primary)||d.links.find(l=>l.device_id===x.id);
    return `<tr><td>${esc(x.label||x.dev_eui)}</td><td>${d.restricted?"":esc(x.patient||"-")}</td><td>${g?esc(g.label||g.gateway_id):'<span class="mut">-</span>'}</td><td>${lk&&lk.quality?`<span style="color:${QCOL[lk.quality]}">● ${QTH[lk.quality]}</span>`:"-"}</td><td>${x.rssi??"-"} / ${x.snr??"-"}</td><td>${kmTxt(lk?.distance_m)}</td><td>${x.lat!=null?PSRC[x.pos_src]:'<span class="mut">ไม่มีพิกัด</span>'}</td><td>${x.offline?'<span class="bad">ออฟไลน์</span>':'<span class="ok">ปกติ</span>'}</td></tr>`};
  $("#maptb").innerHTML=`<div class="card"><h3>การเชื่อมต่ออุปกรณ์ → เกตเวย์</h3>${d.devices.length?`<table><tr><th>อุปกรณ์</th><th>ผู้ป่วย</th><th>เกตเวย์ที่ได้ยินล่าสุด</th><th>สัญญาณ</th><th>RSSI/SNR</th><th>ระยะ</th><th>ที่มาของพิกัด</th><th>สถานะ</th></tr>${d.devices.map(row).join("")}</table>`:'<p class="mut">ยังไม่มีอุปกรณ์</p>'}
   ${noPosGw.length?`<p class="warn" style="margin-top:10px">เกตเวย์ที่ยังไม่มีตำแหน่ง (ไม่แสดงบนแผนที่): ${noPosGw.map(g=>esc(g.label||g.gateway_id)).join(", ")} — ตั้งพิกัดที่เมนูเกตเวย์</p>`:""}
   ${!d.restricted&&noPosDev.length?`<p class="warn" style="margin-top:10px">อุปกรณ์ที่ไม่มีพิกัด (ไม่แสดงบนแผนที่): ${noPosDev.map(x=>esc(x.label||x.dev_eui)).join(", ")} — ใส่ละติจูด/ลองจิจูดที่หน้าผู้ป่วย หรือใช้ชุด GPS</p>`:""}
    <p class="mut" style="font-size:13px">สีเส้นคำนวณจาก link margin ค่ากลาง 7 วันของทุกเกตเวย์ที่ได้ยินปุ่ม; เส้นทึบคือเกตเวย์หลัก เส้นประคือเกตเวย์สำรอง · วงประคือรัศมีวางแผน และวงเขียวคือระยะที่วัดได้จริงจากปุ่มที่ยังได้ยินชัด</p></div>`;
  if(!mapObj||mapObj._container!==$("#mapc"))return;
  clearInterval(timer);timer=setInterval(()=>{if(page==="map")loadMap();else clearInterval(timer)},30000)});
function mapPts(){const d=window._mapData;return [...d.gateways,...d.devices,...(d.homes||[])].filter(x=>x.lat!=null).map(x=>[x.lat,x.lon])}
// pin a patient's home: choose a name, then click the map
function mapPinUI(d){const box=$("#mpin");if(!box)return;const list=d.no_location||[];
  box.innerHTML=canEdit()&&list.length?`<select id="pinsel"><option value="">📍 ปักหมุดบ้านผู้ป่วย… (${list.length})</option>${list.map(p=>`<option value="${p.id}">${esc(p.name)}${p.village?" · "+esc(p.village):""}</option>`).join("")}</select>`:"";
  const sel=$("#pinsel");if(!sel)return;
  sel.onchange=()=>{const id=+sel.value,c=$("#mapc");if(!id){c.classList.remove("picking");return}const p=list.find(x=>x.id===id);c.classList.add("picking");$("#mapnote").innerHTML=`<b>คลิกบนแผนที่ตรงบ้านของ ${esc(p.name)}</b> (ซูมเข้าให้ใกล้ก่อนเพื่อความแม่นยำ)`;
    mapObj.once("click",guard(async e=>{c.classList.remove("picking");$("#mapnote").textContent="";if(!await ask(`ปักหมุดบ้าน ${p.name} ที่ ${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)} ?`)){sel.value="";return}
      await api(`/patients/${id}/location`,{method:"PUT",body:{lat:e.latlng.lat,lon:e.latlng.lng}});toast("บันทึกตำแหน่งแล้ว");loadMap()}))}}
const mapActions=(phone,lat,lon,cs)=>`<div class="row" style="margin-top:8px">${phone?`<a class="btn sm" href="tel:${esc(phone)}">${ico("phone")}โทร</a>`:""}${cs?`<button class="btn sm ${cs.state==="new"?"r":"p"}" data-case="${cs.id}">เปิดเคส</button>`:""}<a class="btn sm" target="_blank" rel="noopener" href="https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}">🧭 นำทาง</a></div>`;
function mapFit(){const p=mapPts();if(p.length>1)mapObj.fitBounds(p,{padding:[40,40],maxZoom:17});else if(p.length===1)mapObj.setView(p[0],15)}
function mapRedraw(){
  const d=window._mapData,lg=mapObj._lg;lg.clearLayers();
  const gw=Object.fromEntries(d.gateways.map(g=>[g.gateway_id,g]));
  if(mapOpt.cov&&d.coverage_m)d.gateways.filter(g=>g.lat!=null).forEach(g=>{L.circle([g.lat,g.lon],{radius:d.coverage_m,color:g.offline?"#dc2626":"#1d4ed8",weight:1.2,fillOpacity:.03,dashArray:"6 6",interactive:false}).addTo(lg);if(g.range_m)L.circle([g.lat,g.lon],{radius:g.range_m,color:"#16a34a",weight:2,fillOpacity:.06}).bindTooltip(`${esc(g.label||g.gateway_id)}: ระยะที่วัดได้จริง ${kmTxt(g.range_m)}`,{sticky:true}).addTo(lg)});
  const devs=d.devices.filter(x=>x.lat!=null&&(!mapOpt.onlyOn||!x.offline));
  if(mapOpt.links)d.links.forEach(l=>{const x=d.devices.find(v=>v.id===l.device_id),g=gw[l.gateway_id];if(!x||!g||x.lat==null||g.lat==null)return;if(mapOpt.onlyOn&&x.offline)return;
    const col=QCOL[l.quality]||"#94a3b8",secondary=l.primary===false,unknown=!l.quality||l.quality==="unknown";
    L.polyline([[x.lat,x.lon],[g.lat,g.lon]],{color:col,weight:secondary?1.5:(unknown?2:3),opacity:l.offline?.5:(secondary?.55:.85),dashArray:l.offline||unknown?"6 8":(secondary?"2 5":null)})
     .bindTooltip(`${esc(x.label||x.dev_eui)} → ${esc(g.label||g.gateway_id)}${secondary?" (เกตเวย์สำรอง)":""}${l.n?` · ค่ากลาง ${l.n} ข้อความ/7 วัน`:""}<br>RSSI ${l.rssi??"-"} dBm · SNR ${l.snr??"-"} dB${l.link_margin!=null?` · margin ${l.link_margin} dB`:""}${l.quality?" · "+QTH[l.quality]:""}<br>ระยะ ${kmTxt(l.distance_m)}`,{sticky:true}).addTo(lg)});
  d.gateways.filter(g=>g.lat!=null).forEach(g=>{
    const ic=L.divIcon({className:"",iconSize:[34,34],iconAnchor:[17,17],html:`<div class="mk mk-gw ${g.offline?"off":""}">📡</div>`});
    const m=L.marker([g.lat,g.lon],{icon:ic,zIndexOffset:500}).addTo(lg);
    m.bindPopup(`<b>${esc(g.label||g.gateway_id)}</b><br>สถานะ: ${g.offline?'<span class="bad">ออฟไลน์'+(g.reason==="radio"?" (ตัวรับสัญญาณเสีย)":"")+"</span>":'<span class="ok">ออนไลน์</span>'}<br>อุปกรณ์ที่ใช้: ${g.devices} เครื่อง<br><span class="mut">${g.gateway_id}<br>พิกัดจาก${g.pos_src==="gps"?" GPS ของเกตเวย์":"ที่ลงทะเบียน"}</span>`);
    if(mapOpt.names)m.bindTooltip(esc(g.label||g.gateway_id),{permanent:true,direction:"bottom",offset:[0,16],className:"mk-tip"})});
  devs.forEach(x=>{const col=x.offline?"#dc2626":(QCOL[x.quality]||"#2f5bea"),cs=x.case;
    const ic=cs?L.divIcon({className:"",iconSize:[30,30],iconAnchor:[15,28],popupAnchor:[0,-26],html:`<div class="pin ${cs.state==="new"?"red pulse":"amb"}"><span>!</span></div>`})
      :L.divIcon({className:"",iconSize:[22,22],iconAnchor:[11,11],html:`<div class="mk mk-dv ${x.offline?"off":""}" style="background:${col}"></div>`});
    const m=L.marker([x.lat,x.lon],{icon:ic,zIndexOffset:cs?1000:300}).addTo(lg);
    m.bindPopup(`<b>${esc(x.label||x.dev_eui)}</b>${x.patient?`<br>${esc(x.patient)}`:""}<br>${x.offline?'<span class="bad">ออฟไลน์</span>':'<span class="ok">ออนไลน์</span>'} · แบต ${x.battery??"-"}% · ไฟบ้าน ${x.mains_ok===false?"ดับ":"ปกติ"}<br>สัญญาณ ${x.quality?QTH[x.quality]+" ("+(x.rssi??"-")+" dBm)":"-"}${x.gateway_id&&gw[x.gateway_id]?` → ${esc(gw[x.gateway_id].label||x.gateway_id)}`:""}${cs?`<div style="margin:6px 0"><span class="tag ${cs.state}">${ST[cs.state]}</span> เคส #${cs.id} · ${ago(cs.created_at)}${cs.overdue?' <b class="bad">เกินเวลา</b>':""}</div>`:"<br>"}<span class="mut">พิกัดจาก${PSRC[x.pos_src]}${x.address?" · "+esc(x.address):""}</span>${mapActions(x.phone,x.lat,x.lon,cs)}`);
    if(mapOpt.names)m.bindTooltip(esc(x.label||x.dev_eui),{permanent:true,direction:"bottom",offset:[0,12],className:"mk-tip"})});
  if(!mapOpt.onlyOn)(d.homes||[]).forEach(p=>{L.marker([p.lat,p.lon],{icon:L.divIcon({className:"",iconSize:[30,30],iconAnchor:[15,28],popupAnchor:[0,-26],html:'<div class="pin blu"><span></span></div>'}),zIndexOffset:200}).addTo(lg)
    .bindPopup(`<b>${esc(p.name)}</b><br><span class="mut">${esc(p.address||"")} ${esc(p.village||"")}</span><br>ยังไม่ได้ผูกปุ่ม${mapActions(p.phone,p.lat,p.lon,null)}`)});
  if(!mapFitted){mapFitted=true;mapFit()}}

/* ===================== v0.5: passwords, users, PDPA, reports, locations ===================== */
const dist=m=>m==null?"":m>=1000?(m/1000).toFixed(1)+" กม.":m+" ม.";
const gmap=(la,lo,t="แผนที่")=>la==null?"":`<a target="_blank" rel="noopener" href="https://maps.google.com/?q=${encodeURIComponent(la)},${encodeURIComponent(lo)}">${t}</a>`;
// Device list location: the GPS fix if the button has GPS, else the patient's home pin (set by hand),
// with ways to set the pin: paste coordinates / a Google Maps link, pick on the map, or copy the GPS fix.
function devLocCell(d){const can=canEdit()&&d.patient_id,useGps=can&&d.loc?`<br><button class="btn sm" type="button" data-usegps="${d.id}">ใช้พิกัดนี้เป็นตำแหน่งบ้าน</button>`:"";
  if(d.loc)return locCell(d.loc)+useGps;
  if(d.home)return `${gmap(d.home.lat,d.home.lon,"📍 บ้าน")} <span class="mut">ปักเอง · ยังไม่มีพิกัดจาก GPS</span>${can?` <button class="lnk" type="button" data-sethome="${d.id}">แก้</button>`:""}`;
  return can?`<button class="btn sm" type="button" data-sethome="${d.id}">ปักตำแหน่งบ้าน</button>`:'<span class="mut">-</span>'}
function parseLatLon(t){t=String(t||"").trim();let m=t.match(/@(-?\d+\.\d+),\s*(-?\d+\.\d+)/)||t.match(/[?&](?:q|query|ll|destination)=(-?\d+\.\d+),\s*(-?\d+\.\d+)/)||t.match(/^(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)$/);
  if(!m)return null;const lat=+m[1],lon=+m[2];return Math.abs(lat)<=90&&Math.abs(lon)<=180&&!(lat===0&&lon===0)?{lat,lon}:null}
function devLocWire(ds){
  document.querySelectorAll("[data-sethome]").forEach(b=>b.onclick=guard(async()=>{const d=ds.find(x=>x.id==b.dataset.sethome);
    const v=await dlg({icon:"map",title:`ตำแหน่งบ้าน: ${d.patient||d.label}`,html:`<p>วางพิกัด (เช่น <code>13.91234, 100.60123</code>) หรือลิงก์จาก Google Maps ที่ปักหมุดบ้านไว้</p><p class="mut">ใน Google Maps: กดค้างที่บ้าน → แตะตัวเลขพิกัดเพื่อคัดลอก · หรือ <a href="#" id="pickmap">เลือกบนแผนที่ของระบบ</a></p>`,
      input:{label:"พิกัดหรือลิงก์",value:d.home?`${d.home.lat}, ${d.home.lon}`:"",placeholder:"13.91234, 100.60123"},ok:"บันทึก"});
    if(v==null)return;const p=parseLatLon(v);if(!p){toast(/goo\.gl|app\.goo/.test(v)?"ลิงก์แบบย่อ (maps.app.goo.gl) ไม่มีพิกัดอยู่ในลิงก์ — ใน Google Maps กดค้างที่บ้านแล้วคัดลอกตัวเลขพิกัดมาวางแทน":"อ่านพิกัดไม่ได้ — ใส่เป็น ละติจูด, ลองจิจูด เช่น 13.91234, 100.60123","warn");return}
    await api(`/patients/${d.patient_id}/location`,{method:"PUT",body:p});toast("บันทึกตำแหน่งบ้านแล้ว");loadDevices()}));
  document.querySelectorAll("[data-usegps]").forEach(b=>b.onclick=guard(async()=>{const d=ds.find(x=>x.id==b.dataset.usegps);
    if(!await ask(`ใช้พิกัด GPS ล่าสุดของ ${d.label} เป็นตำแหน่งบ้านของ ${d.patient}?\nพิกัด ${d.loc.lat.toFixed(5)}, ${d.loc.lon.toFixed(5)} · ดาวเทียม ${d.loc.sats??"-"} ดวง · ${ago(d.loc.at)}`,{ok:"ใช้พิกัดนี้"}))return;
    await api(`/patients/${d.patient_id}/location`,{method:"PUT",body:{lat:d.loc.lat,lon:d.loc.lon}});toast("บันทึกตำแหน่งบ้านแล้ว");loadDevices()}))}
document.addEventListener("click",e=>{if(e.target.id==="pickmap"){e.preventDefault();document.querySelector("dialog.dlg[open]")?.close();page="map";render()}});
const locCell=l=>!l?'<span class="mut">-</span>':`${gmap(l.lat,l.lon,"📍 แผนที่")} <span class="mut">${ago(l.at)} · ดาวเทียม ${l.sats??"-"}${l.fresh===false?" · เก่า":""}</span>${l.away?` <b style="color:var(--amb)">ห่างบ้าน ${dist(l.home_distance_m)}</b>`:l.home_distance_m!=null?` <span class="mut">(ห่างบ้าน ${dist(l.home_distance_m)})</span>`:""}`;

/* ---- password change (voluntary and forced) ---- */
function pwScreen(forced){
  authShell(`<form class="auth-card" id="pf" novalidate><div class="auth-logo">${LOGO(56)}</div>
  <h1>${forced?"ตั้งรหัสผ่านใหม่":"เปลี่ยนรหัสผ่าน"}</h1><p class="sub">${forced?"บัญชีนี้ใช้รหัสผ่านชั่วคราว ต้องตั้งรหัสใหม่ก่อนใช้งาน":"เครื่องอื่นที่ล็อกอินอยู่จะถูกออกจากระบบ"}</p>
  <div class="err" id="perr" role="alert" hidden></div>
  <label class="fld"><span>รหัสผ่านปัจจุบัน</span><input id="pc" type="password" autocomplete="current-password"></label>
  <label class="fld"><span>รหัสผ่านใหม่ <span class="mut" style="font-weight:normal">(อย่างน้อย 10 ตัวอักษร)</span></span><div class="pwbox"><input id="pn" type="password" autocomplete="new-password"><button type="button" class="eye" id="eye2" aria-label="แสดงรหัสผ่าน">👁</button></div></label>
  <label class="fld"><span>ยืนยันรหัสผ่านใหม่</span><input id="pn2" type="password" autocomplete="new-password"></label>
  <p class="mut" style="font-size:13px;margin:-4px 0 10px">ห้ามมีชื่อผู้ใช้ · ห้ามเป็นรหัสที่เดาง่ายหรือตัวอักษรซ้ำ ๆ</p>
  <button class="btn p big" id="pgo" type="submit">บันทึกรหัสผ่านใหม่</button>
  <div class="alt" style="margin-top:10px">${forced?'<button type="button" class="btn" id="pout">ออกจากระบบ</button>':'<button type="button" class="btn" id="pback">ยกเลิก</button>'}</div></form>`);
  eyeToggle($("#eye2"),$("#pn"));$("#pc").focus();
  if(forced)$("#pout").onclick=logout;else $("#pback").onclick=()=>render();
  $("#pf").onsubmit=async e=>{e.preventDefault();const err=$("#perr");err.hidden=true;
    try{if($("#pn").value!==$("#pn2").value)throw new Error("รหัสผ่านใหม่ทั้งสองช่องไม่ตรงกัน");
      busyBtn($("#pgo"),true);
      const r=await api("/me/password",{method:"POST",body:{current_password:$("#pc").value,new_password:$("#pn").value}});
      token=r.token;sessionStorage.setItem("tk",token);sessionStorage.removeItem("mc");toast("เปลี่ยนรหัสผ่านแล้ว");render()}
    catch(x){err.textContent=x.message;err.hidden=false;busyBtn($("#pgo"),false,"บันทึกรหัสผ่านใหม่")}}}

/* ---- users (admin) ---- */
const RTH={admin:"ผู้ดูแลระบบ",staff:"เจ้าหน้าที่",viewer:"ดูอย่างเดียว"};

/* ---- integrations (admin): LINE credentials. Values are never shown back, only the last 4 characters. ---- */
const loadIntegrations=guard(async()=>{
  if(role!=="admin"){$("#main").innerHTML='<div class="warn">เฉพาะผู้ดูแลระบบ</div>';return}
  const [l,cs,ai]=await Promise.all([api("/integrations/line"),api("/caregivers"),api("/integrations/ai")]);if(page!=="integrations")return;
  const src={dashboard:"ตั้งจากแดชบอร์ด",env:"ตั้งจากไฟล์ .env บนเซิร์ฟเวอร์",none:"ยังไม่ได้ตั้งค่า"};
  const st=(set,hint,so)=>set?`<span class="ok">● ตั้งแล้ว</span> <code>${esc(hint)}</code> <span class="mut">(${src[so]})</span>`:'<span class="bad">● ยังไม่ได้ตั้งค่า</span>';
  $("#main").innerHTML=`<div class="card"><h3>LINE Messaging API</h3>
    <div class="row">สถานะ: ${l.ready?'<span class="ok">พร้อมส่งแจ้งเตือนและรับการผูกบัญชี</span>':'<span class="bad">ยังไม่ครบ — ต้องมีทั้ง Channel access token และ Channel secret</span>'}</div>
    <div class="row"><b>Channel access token</b> ${st(l.token_set,l.token_hint,l.token_source)}</div>
    <div class="row"><input id="lt" type="password" size="50" placeholder="วาง Channel access token (long-lived) ใหม่ที่นี่" autocomplete="off"></div>
    <div class="row"><b>Channel secret</b> ${st(l.secret_set,l.secret_hint,l.secret_source)}</div>
    <div class="row"><input id="ls" type="password" size="50" placeholder="วาง Channel secret ใหม่ที่นี่" autocomplete="off"></div>
    <div class="row"><button class="btn p" id="lsv">บันทึก</button><button class="btn" id="lts">ทดสอบการเชื่อมต่อ LINE</button>${l.token_source==="dashboard"||l.secret_source==="dashboard"?'<button class="btn r" id="lrm">ลบค่าที่ตั้งจากแดชบอร์ด</button>':""}<span id="lres" class="mut"></span></div>
    <p class="hint">เว้นช่องว่างไว้ = ไม่เปลี่ยนค่านั้น · ค่าถูกเข้ารหัสในฐานข้อมูล ไม่แสดงกลับมาและไม่อยู่ใน audit · มีผลทันที ไม่ต้องรีสตาร์ท · ถ้าลบค่าที่ตั้งจากแดชบอร์ด ระบบจะกลับไปใช้ค่าในไฟล์ .env (ถ้ามี)</p></div>
  <div class="card"><h3>ตั้งค่าใน LINE Developers Console</h3>
    <ol><li>เข้า <a href="https://developers.line.biz/console/" target="_blank" rel="noopener">LINE Developers Console</a> → Provider → ช่อง <b>Messaging API</b> ของ รพ.สต.</li>
    <li>แท็บ <b>Basic settings</b> → คัดลอก <b>Channel secret</b> · แท็บ <b>Messaging API</b> → กด Issue ที่ <b>Channel access token (long-lived)</b> แล้วคัดลอก</li>
    <li>ที่แท็บ Messaging API ตั้ง <b>Webhook URL</b> เป็นค่าด้านล่าง แล้วเปิด <b>Use webhook</b> และกด Verify</li>
    <li>ปิด <b>Auto-reply messages</b> และ <b>Greeting messages</b> (ไม่เช่นนั้นบอตจะตอบทับ)</li></ol>
    <div class="row">Webhook URL: <code id="lwh" style="user-select:all">${esc(l.webhook_url)}</code><button class="btn" id="lcp">คัดลอก</button></div>
    ${l.webhook_url.startsWith("https://")?"":'<div class="warn">LINE ต้องการ URL แบบ https — ตั้งค่า SOS_PUBLIC_URL ของเซิร์ฟเวอร์ให้เป็นโดเมนจริงก่อน</div>'}</div>
  <div class="card"><h3>AI — Google Gemini (ใช้ฟรีได้)</h3>
    <div class="row">API key: ${ai.key_set?`<span class="ok">● ตั้งแล้ว</span> <code>${esc(ai.key_hint)}</code>`:'<span class="bad">● ยังไม่ได้ตั้งค่า</span>'}</div>
    <div class="row"><input id="ak" type="password" size="50" placeholder="วาง Gemini API key ใหม่ที่นี่ (ขึ้นต้นด้วย AIza…)" autocomplete="off"></div>
    <div class="row">โมเดล <input id="am" list="aml" value="${esc(ai.model)}" size="28"><datalist id="aml"></datalist><button class="btn" id="aml-b">ดูรายชื่อโมเดล</button>
      <label><input type="checkbox" id="aen" ${ai.enabled?"checked":""}> เปิดใช้ AI</label></div>
    <div class="row"><button class="btn p" id="asv">บันทึก</button><button class="btn" id="ats">ทดสอบ AI</button>${ai.key_set?'<button class="btn r" id="arm">ลบ API key</button>':""}<span id="ares" class="mut"></span></div>
    <p class="hint">ขอคีย์ฟรีที่ <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">Google AI Studio</a> (ล็อกอินบัญชี Google → Create API key) · ตอนนี้ AI ใช้ในหน้า <b>รายงาน</b> ปุ่ม “AI สรุปเป็นข้อความ”</p>
    <div class="warn">⚠️ PDPA: แพ็กเกจฟรีของ Google อาจนำข้อความที่ส่งไปปรับปรุงบริการของตน ระบบนี้จึงส่งเฉพาะ <b>ตัวเลขรวม</b> เท่านั้น (จำนวนเคส เวลารับเรื่อง ชั่วโมง/วันที่เกิดเหตุ สถานะอุปกรณ์) — ไม่ส่งชื่อ ที่อยู่ เบอร์โทร ชื่อหมู่บ้าน หรือรหัสเครื่อง · ตรวจทานข้อความของ AI ก่อนใช้ทุกครั้ง</div></div>
  <div class="card"><h3>ส่งข้อความทดสอบ</h3><p class="mut">ส่งข้อความสั้น ๆ ไปหา อสม. ที่ผูก LINE แล้ว เพื่อเช็กว่าแจ้งเตือนถึงจริง</p>
    <div class="row"><select id="ltc">${cs.filter(c=>c.line_bound).map(c=>`<option value="${c.id}">${esc(c.full_name)}</option>`).join("")||'<option value="">(ยังไม่มีใครผูก LINE)</option>'}</select><button class="btn" id="ltp" ${cs.some(c=>c.line_bound)?"":"disabled"}>ส่งทดสอบ</button></div></div>`;
  const val=id=>$("#"+id).value.trim();
  $("#asv").onclick=guard(async()=>{const b={enabled:$("#aen").checked};if(val("am"))b.model=val("am");if(val("ak"))b.key=val("ak");await api("/integrations/ai",{method:"PUT",body:b});toast("บันทึกแล้ว");loadIntegrations()});
  $("#ats").onclick=guard(async()=>{$("#ares").textContent="กำลังทดสอบ…";const r=await api("/integrations/ai/test",{method:"POST"});
    $("#ares").innerHTML=r.ok?`<span class="ok">✔ ${esc(r.detail)} (${esc(r.model)}): “${esc(r.reply)}”</span>`:`<span class="bad">✖ ${esc(r.detail)}</span>`});
  $("#aml-b").onclick=guard(async()=>{const r=await api("/integrations/ai/models");$("#aml").innerHTML=r.models.map(m=>`<option value="${esc(m)}">`).join("");toast(`พบ ${r.models.length} โมเดล — คลิกช่องโมเดลเพื่อเลือก`);$("#am").value="";$("#am").focus()});
  if($("#arm"))$("#arm").onclick=guard(async()=>{if(!await ask("ลบ Gemini API key?"))return;await api("/integrations/ai",{method:"PUT",body:{key:""}});toast("ลบแล้ว");loadIntegrations()});
  $("#lsv").onclick=guard(async()=>{const b={};if(val("lt"))b.token=val("lt");if(val("ls"))b.secret=val("ls");if(!Object.keys(b).length){toast("ยังไม่ได้กรอกค่าใหม่");return}
    await api("/integrations/line",{method:"PUT",body:b});toast("บันทึกแล้ว — มีผลทันที");loadIntegrations()});
  $("#lts").onclick=guard(async()=>{$("#lres").textContent="กำลังตรวจ…";const r=await api("/integrations/line/test",{method:"POST"});
    $("#lres").innerHTML=r.ok?`<span class="ok">✔ ${esc(r.detail)}: บอต “${esc(r.bot)}” ${esc(r.basic_id)}</span>${r.secret_set?"":' <b class="bad">แต่ยังไม่ได้ตั้ง Channel secret</b>'}`:`<span class="bad">✖ ${esc(r.detail)}</span>`});
  if($("#lrm"))$("#lrm").onclick=guard(async()=>{if(!await ask("ลบค่า LINE ที่ตั้งจากแดชบอร์ด? ระบบจะกลับไปใช้ค่าใน .env (ถ้าไม่มี การแจ้งเตือน LINE จะหยุดทำงาน)"))return;
    await api("/integrations/line",{method:"PUT",body:{token:"",secret:""}});toast("ลบแล้ว");loadIntegrations()});
  $("#lcp").onclick=()=>navigator.clipboard&&navigator.clipboard.writeText(l.webhook_url).then(()=>toast("คัดลอกแล้ว"));
  $("#ltp").onclick=guard(async()=>{const r=await api("/integrations/line/test-push",{method:"POST",body:{caregiver_id:+$("#ltc").value}});toast(r.ok?"ส่งแล้ว — ตรวจใน LINE":"ส่งไม่สำเร็จ: "+r.detail)})});

const loadUsers=guard(async()=>{
  if(role!=="admin"){$("#main").innerHTML='<div class="warn">เฉพาะผู้ดูแลระบบ</div>';return}
  const us=await api("/users");if(page!=="users")return;
  $("#main").innerHTML=`<div class="card"><h3>เพิ่มผู้ใช้</h3><div class="row"><input id="nu" placeholder="ชื่อผู้ใช้ (a-z 0-9 . _ -)"><input id="nd" placeholder="ชื่อที่แสดง"><input id="np" type="password" placeholder="รหัสผ่านเริ่มต้น (≥10)" autocomplete="new-password"><select id="nr"><option value="staff">เจ้าหน้าที่</option><option value="viewer">ดูอย่างเดียว</option><option value="admin">ผู้ดูแลระบบ</option></select><button class="btn p" id="nadd">เพิ่ม</button></div><p class="mut">ผู้ใช้ใหม่ต้องเปลี่ยนรหัสผ่านเมื่อเข้าสู่ระบบครั้งแรก · "ดูอย่างเดียว" จะไม่เห็นที่อยู่/เบอร์เต็ม/พิกัดของผู้ป่วย</p></div>
  <div id="tmp"></div>
  <div class="card"><table><tr><th>ผู้ใช้</th><th>สิทธิ์</th><th>สถานะ</th><th>เข้าสู่ระบบล่าสุด</th><th>เปลี่ยนรหัสล่าสุด</th><th></th></tr>${us.map(u=>`<tr data-rid="${u.id}"><td>${esc(u.username)}<div class="mut">${esc(u.display_name)}</div></td><td><select data-role="${u.id}">${Object.entries(RTH).map(([k,v])=>`<option value="${k}" ${u.role===k?"selected":""}>${v}</option>`).join("")}</select></td><td>${u.active?'<span class="ok">ใช้งาน</span>':'<span class="bad">ปิด</span>'}${u.locked?' <b class="bad">🔒 ถูกล็อก</b>':""}${u.must_change?' <span class="tag">รอตั้งรหัสใหม่</span>':""}${u.mfa?' <span class="tag closed">🔐 2FA</span>':""}</td><td>${fmt(u.last_login_at)}</td><td>${fmt(u.password_changed_at)}</td><td style="white-space:nowrap"><button class="btn" data-rs="${u.id}">รีเซ็ตรหัสผ่าน</button> ${u.locked?`<button class="btn" data-ul="${u.id}">ปลดล็อก</button> `:""}${u.mfa?`<button class="btn" data-m2="${u.id}">รีเซ็ต 2FA</button> `:""}<button class="btn" data-ac="${u.id}" data-to="${u.active?0:1}">${u.active?"ปิดบัญชี":"เปิดบัญชี"}</button>${!u.active?` <button class="btn r" data-ud="${u.id}">ลบ</button>`:""}</td></tr>`).join("")}</table>
  <p class="mut">ไม่มีการรีเซ็ตทางอีเมล: ผู้ดูแลระบบกดรีเซ็ตให้ จะได้รหัสชั่วคราวแสดงครั้งเดียว (ส่งต่อให้ผู้ใช้ทางที่ปลอดภัย) · ถ้าผู้ดูแลระบบทุกคนเข้าไม่ได้ ใช้คำสั่ง <code>python -m app.cli reset-password &lt;ผู้ใช้&gt;</code> บนเซิร์ฟเวอร์</p></div>`;
  $("#nadd").onclick=guard(async()=>{await api("/users",{method:"POST",body:{username:$("#nu").value,display_name:$("#nd").value,password:$("#np").value,role:$("#nr").value}});toast("เพิ่มผู้ใช้แล้ว");loadUsers()});
  document.querySelectorAll("[data-role]").forEach(s=>s.onchange=guard(async()=>{try{await api("/users/"+s.dataset.role,{method:"PUT",body:{role:s.value}});toast("บันทึกสิทธิ์แล้ว")}finally{loadUsers()}}));
  document.querySelectorAll("[data-ac]").forEach(b=>b.onclick=guard(async()=>{await api("/users/"+b.dataset.ac,{method:"PUT",body:{active:b.dataset.to==="1"}});loadUsers()}));
  document.querySelectorAll("[data-m2]").forEach(b=>b.onclick=guard(async()=>{const u=us.find(x=>x.id==b.dataset.m2);if(!await ask("รีเซ็ตการยืนยัน 2 ขั้นของ "+u.username+" ?\nใช้เมื่อมือถือหาย — ผู้ใช้จะถูกออกจากระบบ และต้องตั้งค่าใหม่ (ถ้านโยบายบังคับ)"))return;await api("/users/"+u.id+"/reset-2fa",{method:"POST"});toast("รีเซ็ต 2FA แล้ว");loadUsers()}));
  const uById=i=>us.find(x=>x.id==i);
  lister("users",$("#main").children[$("#main").children.length-1],{noun:"บัญชี",reload:loadUsers,bulk:[
    {label:"เปิดบัญชี",only:i=>!uById(i).active,run:i=>api("/users/"+i,{method:"PUT",body:{active:true}})},
    {label:"ปิดบัญชี",only:i=>uById(i).active,confirm:n=>`ปิด ${n} บัญชี? ผู้ใช้เหล่านี้จะถูกออกจากระบบ`,run:i=>api("/users/"+i,{method:"PUT",body:{active:false}})},
    {label:"ลบ",cls:"r",only:i=>!uById(i).active,confirm:n=>`ลบ ${n} บัญชีถาวร?`,run:i=>api("/users/"+i,{method:"DELETE"})}]});
  document.querySelectorAll("[data-ud]").forEach(b=>b.onclick=guard(async()=>{const u=us.find(x=>x.id==b.dataset.ud);if(!await ask(`ลบบัญชี ${u.username} ถาวร?`))return;await api("/users/"+u.id,{method:"DELETE"});toast("ลบแล้ว");loadUsers()}));
  document.querySelectorAll("[data-ul]").forEach(b=>b.onclick=guard(async()=>{await api("/users/"+b.dataset.ul+"/unlock",{method:"POST"});toast("ปลดล็อกแล้ว");loadUsers()}));
  document.querySelectorAll("[data-rs]").forEach(b=>b.onclick=guard(async()=>{const u=us.find(x=>x.id==b.dataset.rs);if(!await ask("รีเซ็ตรหัสผ่านของ "+u.username+" ?\nผู้ใช้จะถูกออกจากระบบทุกเครื่อง"))return;
    const r=await api("/users/"+u.id+"/reset-password",{method:"POST"});
    $("#tmp").innerHTML=`<div class="card" style="border:2px solid var(--amb)"><b>รหัสผ่านชั่วคราวของ ${esc(r.username)}</b> (แสดงครั้งเดียว — ปิดหน้านี้แล้วดูอีกไม่ได้)<div class="row"><code id="tpw" style="font-size:22px;letter-spacing:1px">${esc(r.temp_password)}</code><button class="btn" id="tcp">คัดลอก</button><button class="btn" id="thd">ซ่อน</button></div></div>`;
    $("#tcp").onclick=()=>navigator.clipboard&&navigator.clipboard.writeText(r.temp_password).then(()=>toast("คัดลอกแล้ว"));$("#thd").onclick=()=>{$("#tmp").innerHTML=""};scrollTo(0,0)}))});

/* ---- PDPA / privacy ---- */
const loadPrivacy=guard(async()=>{
  const adm=role==="admin";const [ov,s]=await Promise.all([api("/privacy/overview"),api("/settings")]);if(page!=="privacy")return;
  const pv=ov.settings,g=s.geo,rp=ov.retention_preview;
  $("#main").innerHTML=`<div class="card"><h3>ภาพรวมข้อมูลส่วนบุคคล (PDPA)</h3>
   <div class="row"><span class="tag">ผู้ป่วยทั้งหมด ${ov.patients}</span><span class="tag">ใช้งานอยู่ ${ov.active}</span><span class="tag">ถอนความยินยอม ${ov.withdrawn}</span><span class="tag">ลบข้อมูลแล้ว ${ov.anonymized}</span></div>
   ${ov.without_consent.length?`<div class="warn" style="margin-top:8px"><b>ยังไม่มีบันทึกความยินยอม ${ov.without_consent.length} ราย:</b> ${ov.without_consent.map(p=>esc(p.full_name)+(p.has_active_device?" (มีอุปกรณ์ใช้งานอยู่!)":"")).join(", ")}<br>ไปที่หน้า "ผู้ป่วย" → ปุ่ม "ข้อมูล/PDPA" เพื่อบันทึกความยินยอม</div>`:'<p class="ok">ผู้ป่วยที่ใช้งานอยู่ทุกรายมีบันทึกความยินยอมแล้ว</p>'}
   <p class="mut">ระบบนี้เป็นเครื่องมือช่วยจัดการ ไม่ใช่คำปรึกษากฎหมาย — ผู้ควบคุมข้อมูล (รพ.สต./อบต.) ต้องกำหนดระยะเวลาเก็บ ข้อความขอความยินยอม และผู้รับผิดชอบเอง · ข้อมูลที่ส่งผ่าน LINE อยู่กับผู้ให้บริการ LINE (ต่างประเทศ) ต้องระบุในหนังสือยินยอมด้วย</p></div>
  <div class="card"><h3>นโยบายข้อมูล</h3>${adm?"":'<p class="mut">เฉพาะผู้ดูแลระบบแก้ไขได้</p>'}
   <div class="row"><label><input type="checkbox" id="rc" ${pv.require_consent?"checked":""} ${adm?"":"disabled"}> ต้องมีบันทึกความยินยอมก่อนเปิดใช้อุปกรณ์ (แนะนำให้เปิดไว้)</label></div>
   <div class="row">ข้อความ LINE <select id="ld" ${adm?"":"disabled"}><option value="full" ${pv.line_detail==="full"?"selected":""}>เต็ม (ชื่อ ที่อยู่ ปุ่มโทร/นำทาง)</option><option value="minimal" ${pv.line_detail==="minimal"?"selected":""}>น้อยที่สุด (ชื่อ+หมู่บ้าน เท่านั้น)</option></select></div>
   <div class="row">เก็บเคสที่ปิดแล้ว <input id="crd" type="number" min="30" max="3650" value="${pv.case_retention_days}" ${adm?"":"disabled"}> วัน · เก็บบันทึกการใช้งาน <input id="ard" type="number" min="90" max="3650" value="${pv.audit_retention_days}" ${adm?"":"disabled"}> วัน</div>
   <div class="row"><label><input type="checkbox" id="ap" ${pv.auto_purge?"checked":""} ${adm?"":"disabled"}> ลบข้อมูลที่เกินกำหนดอัตโนมัติทุกชั่วโมง (ปิดไว้ = ลบเองด้วยปุ่มด้านล่าง)</label></div>
   ${adm?'<div class="row"><button class="btn p" id="pvs">บันทึกนโยบาย</button></div>':""}</div>
  <div class="card"><h3>ลบข้อมูลที่เกินระยะเก็บ</h3><p>ตอนนี้เข้าเกณฑ์: เคส <b>${rp.cases}</b> รายการ (+ เหตุการณ์ ${rp.case_events}, การแจ้งเตือน ${rp.notifications}) · บันทึกการใช้งาน <b>${rp.audit_rows}</b> แถว</p>
   <p class="mut">เคสที่ยังเปิดอยู่ไม่ถูกลบ · เคสทดสอบเก็บ 30 วัน · บันทึกการใช้งานด้านข้อมูลส่วนบุคคลเก็บไว้เสมอ</p>${adm?'<div class="row"><button class="btn r" id="pur">ลบตอนนี้</button></div>':""}</div>
  <div class="card"><h3>ตำแหน่ง (GPS)</h3>
   <div class="row">เตือนเมื่ออุปกรณ์อยู่ห่างบ้านเกิน <input id="hr" type="number" min="20" max="5000" value="${g.home_radius_m}" ${adm?"":"disabled"}> เมตร · ถือว่าตำแหน่ง "ล่าสุด" ภายใน <input id="gf" type="number" min="1" max="1440" value="${g.gps_fresh_min}" ${adm?"":"disabled"}> นาที · เตือนเกตเวย์ถูกย้ายเมื่อห่าง <input id="gm" type="number" min="20" max="100000" value="${g.gateway_move_m}" ${adm?"":"disabled"}> เมตร</div>
   ${adm?'<div class="row"><button class="btn p" id="gs">บันทึก</button></div>':""}<p class="mut">ตำแหน่งของผู้ป่วยเป็นข้อมูลอ่อนไหว: ระบบเก็บพิกัดล่าสุดของอุปกรณ์/เคสเพื่อช่วยค้นหา แต่บันทึกเหตุการณ์ไม่เก็บพิกัด และจะถูกลบเมื่อถอนความยินยอม</p></div>`;
  if(!adm)return;const put=(k,v)=>guard(async()=>{await api("/settings/"+k,{method:"PUT",body:v});toast("บันทึกแล้ว");loadPrivacy()})();
  $("#pvs").onclick=()=>put("privacy",{require_consent:$("#rc").checked,line_detail:$("#ld").value,case_retention_days:+$("#crd").value,audit_retention_days:+$("#ard").value,auto_purge:$("#ap").checked});
  $("#gs").onclick=()=>put("geo",{home_radius_m:+$("#hr").value,gps_fresh_min:+$("#gf").value,gateway_move_m:+$("#gm").value});
  $("#pur").onclick=guard(async()=>{const p=await api("/privacy/retention",{method:"POST",body:{dry_run:true}});
    if(!p.cases&&!p.audit_rows){toast("ไม่มีข้อมูลที่เกินกำหนด");return}
    if(!await ask(`จะลบถาวร: เคส ${p.cases} รายการ (เหตุการณ์ ${p.case_events}, การแจ้งเตือน ${p.notifications}) และบันทึกการใช้งาน ${p.audit_rows} แถว\nกู้คืนไม่ได้ ยืนยัน?`))return;
    const r=await api("/privacy/retention",{method:"POST",body:{dry_run:false}});toast(`ลบแล้ว: เคส ${r.cases}, บันทึก ${r.audit_rows}`);loadPrivacy()})});

/* ---- per-patient PDPA panel ---- */
async function patientPrivacy(p){
  const adm=role==="admin";const st=p.anonymized_at?'<b class="bad">ลบข้อมูลแล้ว</b>':p.consent_withdrawn_at?'<b class="bad">ถอนความยินยอมแล้ว</b> ('+fmt(p.consent_withdrawn_at)+')':p.consent_at?'<b class="ok">ให้ความยินยอมแล้ว</b> ('+fmt(p.consent_at)+' · บันทึกโดย '+esc(p.consent_by)+')':'<b class="bad">ยังไม่มีบันทึกความยินยอม</b>';
  $("#main").innerHTML=`<button class="btn" id="back">← กลับ</button><div class="card"><h3>ข้อมูล/PDPA: ${esc(p.full_name)}</h3>
   <p>สถานะ: ${st}</p>${p.consent_note?`<p class="mut">หมายเหตุ: ${esc(p.consent_note)}</p>`:""}
   ${p.anonymized_at?"":`<div class="row"><input id="cn" placeholder="เลขที่แบบฟอร์ม / ผู้ให้ความยินยอมแทน (ถ้ามี)" size="40" maxlength="128"><button class="btn g" id="cg">บันทึกว่าได้รับความยินยอมแล้ว</button></div>
   <p class="mut">บันทึกเมื่อมีหนังสือยินยอมที่ผู้ป่วย (หรือผู้ใช้อำนาจแทน) ลงนามแล้วเท่านั้น</p>
   ${p.consent_at&&!p.consent_withdrawn_at?'<div class="row"><button class="btn r" id="cw">ถอนความยินยอม</button></div><p class="mut">ถอนแล้ว: ปิดผู้ป่วย คืนอุปกรณ์เป็นสถานะ ready ลบรายชื่อผู้ดูแลที่แจ้ง และล้างตำแหน่งของอุปกรณ์</p>':""}`}</div>
  ${adm?`<div class="card"><h3>คำขอของเจ้าของข้อมูล</h3><div class="row"><button class="btn" id="ex">⬇ ส่งออกข้อมูลทั้งหมดของผู้ป่วย (JSON)</button></div>
   ${p.anonymized_at?"":`<hr><p>ลบข้อมูลที่ระบุตัวตน (ย้อนกลับไม่ได้): ชื่อ อายุ ที่อยู่ เบอร์ พิกัด หมายเหตุ ถูกลบ เหลือสถิติเคสแบบไม่ระบุตัวตน</p>
   <div class="row"><label><input type="checkbox" id="kv" checked> คงชื่อหมู่บ้านไว้เพื่อสถิติ</label></div>
   <div class="row"><input id="cf" placeholder="พิมพ์เลขที่ผู้ป่วย ${p.id} เพื่อยืนยัน" size="28"><button class="btn r" id="an">ลบข้อมูลที่ระบุตัวตน</button></div>`}</div>`:""}`;crumb(`ข้อมูล/PDPA: ${p.full_name}`);
  $("#back").onclick=()=>render();
  const act=f=>guard(async()=>{await f();loadPatients()});
  if($("#cg"))$("#cg").onclick=act(async()=>{await api(`/patients/${p.id}/consent`,{method:"PUT",body:{given:true,note:$("#cn").value}});toast("บันทึกความยินยอมแล้ว")});
  if($("#cw"))$("#cw").onclick=act(async()=>{if(!await ask("ถอนความยินยอมของ "+p.full_name+" ?"))return;const r=await api(`/patients/${p.id}/consent`,{method:"PUT",body:{given:false,note:$("#cn").value}});toast(`ถอนแล้ว · คืนอุปกรณ์ ${r.devices_freed} เครื่อง`)});
  if($("#ex"))$("#ex").onclick=guard(async()=>{const r=await fetch(`/api/patients/${p.id}/export`,{headers:{authorization:"Bearer "+token}});if(!r.ok)throw new Error("ส่งออกไม่สำเร็จ ("+r.status+")");dl(await r.blob(),`patient-${p.id}-export.json`)});
  if($("#an"))$("#an").onclick=act(async()=>{const n=+$("#cf").value;if(n!==p.id)throw new Error("เลขที่ยืนยันไม่ตรง");if(!await ask("ลบข้อมูลที่ระบุตัวตนของ "+p.full_name+" ถาวร?"))return;
    const r=await api(`/patients/${p.id}/anonymize`,{method:"POST",body:{confirm_id:n,keep_village:$("#kv").checked}});toast(`ลบแล้ว · ล้างข้อมูลในเคส ${r.cases_scrubbed} รายการ`)})}
function dl(blob,name){const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=name;document.body.appendChild(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},1000)}

/* ---- reports ---- */
const MTH=["มกราคม","กุมภาพันธ์","มีนาคม","เมษายน","พฤษภาคม","มิถุนายน","กรกฎาคม","สิงหาคม","กันยายน","ตุลาคม","พฤศจิกายน","ธันวาคม"];
let rq=null;
const sec=s=>s==null?"-":s<90?Math.round(s)+" วินาที":(s/60).toFixed(1)+" นาที";
const loadReports=guard(async()=>{
  const nowBkk=new Date(new Date().toLocaleString("en-US",{timeZone:"Asia/Bangkok"}));
  if(!rq)rq={y:nowBkk.getFullYear(),m:nowBkk.getMonth()+1};
  const ys=[];for(let y=nowBkk.getFullYear();y>=nowBkk.getFullYear()-4;y--)ys.push(y);
  const q="month="+rq.y+"-"+String(rq.m).padStart(2,"0");
  const r=await api("/reports/summary?"+q);if(page!=="reports")return;
  const c=r.cases,rs=r.response,mx=Math.max(1,...r.by_hour);
  const tbl=(h,rows)=>rows.length?`<table><tr>${h.map(x=>`<th>${x}</th>`).join("")}</tr>${rows.map(x=>`<tr>${x.map(y=>`<td>${y}</td>`).join("")}</tr>`).join("")}</table>`:'<p class="mut">ไม่มีข้อมูล</p>';
  $("#main").innerHTML=`<div class="card"><div class="row"><b>รายงานเดือน</b><select id="rm">${MTH.map((n,i)=>`<option value="${i+1}" ${rq.m===i+1?"selected":""}>${n}</option>`).join("")}</select><select id="ry">${ys.map(y=>`<option value="${y}" ${rq.y===y?"selected":""}>${y+543}</option>`).join("")}</select>
   <button class="btn" id="rai" title="AI สรุปจากตัวเลขรวม (ไม่ส่งชื่อ/ที่อยู่ผู้ป่วย)">✨ AI สรุปเป็นข้อความ</button><button class="btn p" id="rpdf">📄 รายงาน PDF (มีหัวหน่วยงาน + ลงนาม)</button><button class="btn" id="rprint">🖨 พิมพ์หน้านี้</button>${canEdit()?'<button class="btn" id="rcsv">⬇ ดาวน์โหลด CSV (เปิดใน Excel ได้)</button>':""}</div></div>
  <div id="rpt"><div class="card"><h2 style="margin-top:0">รายงานสรุปเหตุฉุกเฉิน ${MTH[rq.m-1]} ${rq.y+543}</h2><p class="mut">ช่วง ${esc(r.period.from_be)} – ${esc(r.period.to_be)} (เวลาประเทศไทย)</p>
   <div class="row"><span class="tag">เคสทั้งหมด <b>${c.total}</b></span><span class="tag">ช่วยเหลือสำเร็จ ${c.resolved}</span><span class="tag">เหตุผิดพลาด ${c.false_alarm}</span><span class="tag">ผู้ป่วยยกเลิกเอง ${c.cancelled_by_device}</span><span class="tag">นอกเวลาทำการ ${c.after_hours}</span><span class="tag">เกินเวลา ${c.overdue}</span><span class="tag">ต้องแจ้งต่อคนถัดไป ${c.escalated}</span><span class="tag">ยังเปิดอยู่ ${c.still_open}</span><span class="tag">ทดสอบ ${c.tests}</span></div></div>
  <div class="card"><h3>ความเร็วในการรับเรื่อง</h3>${rs.accepted?`<div class="row"><span class="tag">รับเรื่อง ${rs.accepted} เคส</span><span class="tag">เฉลี่ย ${sec(rs.accept_avg_s)}</span><span class="tag">มัธยฐาน ${sec(rs.accept_median_s)}</span><span class="tag">P90 ${sec(rs.accept_p90_s)}</span><span class="tag">ภายในเป้า ${rs.target_s} วินาที: <b>${rs.within_target_pct}%</b></span><span class="tag">ปิดเคสเฉลี่ย ${rs.close_avg_min??"-"} นาที</span></div>`:'<p class="mut">ยังไม่มีเคสที่รับเรื่องในช่วงนี้</p>'}</div>
  <div class="card"><h3>แยกตามหมู่บ้าน</h3>${tbl(["หมู่บ้าน","เคส","เหตุผิดพลาด","รับเรื่องเฉลี่ย"],r.by_village.map(v=>[esc(v.village||"(ไม่ระบุ)"),v.cases,v.false_alarm,sec(v.accept_avg_s)]))}</div>
  <div class="card"><h3>ช่วงเวลาที่เกิดเหตุ</h3><div style="display:flex;align-items:flex-end;gap:2px;height:80px">${r.by_hour.map((n,h)=>`<div title="${h}:00 น. — ${n} เคส" style="flex:1;background:var(--pri);height:${Math.max(n?4:1,Math.round(n/mx*76))}px;opacity:${n?1:.25}"></div>`).join("")}</div><div class="mut" style="display:flex;justify-content:space-between"><span>0 น.</span><span>12 น.</span><span>23 น.</span></div>
   <div class="row" style="margin-top:8px">${r.by_weekday.map(d=>`<span class="tag">${esc(d.day)} ${d.cases}</span>`).join("")}</div></div>
  ${r.top_patients.length?`<div class="card"><h3>ผู้ป่วยที่แจ้งบ่อย (10 อันดับ)</h3>${tbl(["ผู้ป่วย","เคส"],r.top_patients.map(p=>[esc(p.name||p.full_name||"-"),p.cases]))}</div>`:""}
  <div class="card"><h3>ระบบ</h3><div class="row"><span class="tag">LINE ส่งสำเร็จ ${r.line.sent}</span><span class="tag">LINE ล้มเหลว ${r.line.failed}</span><span class="tag">อุปกรณ์ใช้งาน ${r.devices.active}</span><span class="tag">ออฟไลน์ตอนนี้ ${r.devices.offline_now}</span><span class="tag">แบตต่ำ ${r.devices.low_battery}</span><span class="tag">ไฟบ้านดับ ${r.devices.mains_lost}</span></div>
   <h4>เกตเวย์ขาดการเชื่อมต่อในช่วงนี้</h4>${r.gateways.length?tbl(["เกตเวย์","จำนวนครั้ง","รวมเวลาขาด (นาที)","ปิดซ่อมบำรุง (นาที)",""],r.gateways.map(g=>[esc(g.label||g.gateway_id),g.outages,g.downtime_min,g.maint_min||"-",g.ongoing?"<b class='bad'>ยังขาดอยู่</b>":""])):'<p class="ok">ไม่มี</p>'}</div></div>`;
  $("#rm").onchange=()=>{rq.m=+$("#rm").value;loadReports()};$("#ry").onchange=()=>{rq.y=+$("#ry").value;loadReports()};
  $("#rai").onclick=guard(async()=>{const b=$("#rai");b.disabled=true;b.textContent="AI กำลังเขียน…";
    try{const r=await api("/reports/ai-summary?"+q,{method:"POST"});let o=$("#raiout");if(!o){o=document.createElement("div");o.id="raiout";o.className="card";$("#rpt").parentNode.insertBefore(o,$("#rpt"))}
      o.innerHTML=`<h3>✨ สรุปโดย AI</h3><p style="white-space:pre-wrap">${esc(r.text)}</p><p class="hint">${esc(r.note)} · โมเดล ${esc(r.model)}</p><div class="row"><button class="btn" id="raicp">คัดลอกข้อความ</button></div>`;
      $("#raicp").onclick=()=>navigator.clipboard&&navigator.clipboard.writeText(r.text).then(()=>toast("คัดลอกแล้ว"))}
    finally{b.disabled=false;b.textContent="✨ AI สรุปเป็นข้อความ"}});
  $("#rpdf").onclick=guard(async()=>{busyBtn($("#rpdf"),true);try{const x=await fetch("/api/reports/monthly.pdf?"+q,{headers:{authorization:"Bearer "+token}});if(!x.ok)throw new Error("สร้าง PDF ไม่สำเร็จ ("+x.status+")");
    const a=document.createElement("a");a.href=URL.createObjectURL(await x.blob());a.download=`sos-report-${rq.y+543}-${String(rq.m).padStart(2,"0")}.pdf`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),4000)}
    finally{busyBtn($("#rpdf"),false,"📄 รายงาน PDF (มีหัวหน่วยงาน + ลงนาม)")}});
  $("#rprint").onclick=()=>{document.body.classList.add("rpt");window.print();setTimeout(()=>document.body.classList.remove("rpt"),500)};
  if($("#rcsv"))$("#rcsv").onclick=guard(async()=>{const x=await fetch("/api/reports/cases.csv?"+q,{headers:{authorization:"Bearer "+token}});if(!x.ok)throw new Error("ดาวน์โหลดไม่สำเร็จ ("+x.status+")");dl(await x.blob(),`sos-cases-${rq.y+543}-${String(rq.m).padStart(2,"0")}.csv`)})});

// ---------------- installed app (PWA) + push notifications ----------------
let swReg=null,installEvt=null;
if("serviceWorker" in navigator&&isSecureContext)navigator.serviceWorker.register("sw.js").then(r=>{swReg=r}).catch(()=>{});
addEventListener("beforeinstallprompt",e=>{e.preventDefault();installEvt=e;if(page==="account"&&token)loadAccount()});
const isIOS=/iPad|iPhone|iPod/.test(navigator.userAgent)||(navigator.platform==="MacIntel"&&navigator.maxTouchPoints>1);
const standalone=()=>matchMedia("(display-mode: standalone)").matches||navigator.standalone===true;
const b64u=s=>{const p="=".repeat((4-s.length%4)%4),r=atob((s+p).replace(/-/g,"+").replace(/_/g,"/"));return Uint8Array.from([...r].map(c=>c.charCodeAt(0)))};
const devLabel=()=>{const u=navigator.userAgent;return (/Android/.test(u)?"Android":isIOS?"iPhone/iPad":/Windows/.test(u)?"Windows":/Mac/.test(u)?"Mac":"อุปกรณ์")+" · "+(/Edg\//.test(u)?"Edge":/Chrome\//.test(u)?"Chrome":/Firefox\//.test(u)?"Firefox":/Safari\//.test(u)?"Safari":"เบราว์เซอร์")};
async function pushState(){if(!("PushManager" in window)||!swReg)return{supported:false};const s=await swReg.pushManager.getSubscription();return{supported:true,perm:Notification.permission,sub:s}}
async function pushEnable(){
  if(Notification.permission==="denied")throw new Error("เบราว์เซอร์บล็อกการแจ้งเตือนไว้ — เปิดในการตั้งค่าเว็บไซต์ของเบราว์เซอร์ก่อน");
  if(await Notification.requestPermission()!=="granted")throw new Error("ไม่ได้อนุญาตการแจ้งเตือน");
  const k=await api("/push/key");const s=await swReg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:b64u(k.public_key)});
  const j=s.toJSON();await api("/push/subscribe",{method:"POST",body:{endpoint:j.endpoint,keys:j.keys,label:devLabel()}})}
async function pushDisable(){const s=await swReg.pushManager.getSubscription();if(s){await api("/push/unsubscribe",{method:"POST",body:{endpoint:s.endpoint}}).catch(()=>{});await s.unsubscribe()}}
async function appCards(){
  const st=await pushState().catch(()=>({supported:false})),me=role!=="viewer";
  const push=!st.supported?`<p class="mut">${isIOS&&!standalone()?"iPhone/iPad: ต้อง<b>ติดตั้งเป็นแอป</b>ก่อน (การ์ดด้านล่าง) แล้วเปิดจากไอคอนบนหน้าจอ จึงจะเปิดการแจ้งเตือนได้":"เบราว์เซอร์นี้ไม่รองรับการแจ้งเตือนแบบ push"}</p>`
    :st.sub?`<p><span class="tag closed">เปิดอยู่</span> อุปกรณ์นี้จะเด้งแจ้งเตือนเมื่อมีเหตุฉุกเฉินใหม่ / ยังไม่มีผู้รับเรื่อง / เกตเวย์ขาดการเชื่อมต่อ</p><div class="row"><button class="btn" id="pst">🔔 ส่งทดสอบ</button><button class="btn" id="psd">ปิดบนอุปกรณ์นี้</button></div>`
    :`<p class="mut">${st.perm==="denied"?'<b class="bad">เบราว์เซอร์บล็อกการแจ้งเตือนไว้</b> — เปิดในการตั้งค่าเว็บไซต์ก่อน':"เปิดแล้วจะได้รับแจ้งเหตุบนมือถือ/คอมเครื่องนี้ทันที แม้ไม่ได้เปิดหน้าเว็บค้างไว้ (เป็นช่องทางเสริม LINE)"}</p><button class="btn p" id="pse">🔔 เปิดการแจ้งเตือนบนอุปกรณ์นี้</button>`;
  const inst=standalone()?'<p><span class="tag closed">ติดตั้งแล้ว</span> กำลังใช้งานแบบแอป</p>'
    :installEvt?'<p class="mut">ติดตั้งลงหน้าจอหลัก เปิดได้ทันทีเหมือนแอป ไม่ต้องพิมพ์ที่อยู่เว็บ</p><button class="btn p" id="pin">📲 ติดตั้งแอป</button>'
    :isIOS?'<ol class="steps"><li>เปิดหน้านี้ใน <b>Safari</b></li><li>แตะปุ่ม <b>แชร์</b> (สี่เหลี่ยมมีลูกศรขึ้น)</li><li>เลือก <b>เพิ่มไปยังหน้าจอโฮม</b> แล้วแตะ <b>เพิ่ม</b></li></ol>'
    :'<p class="mut">Android/Chrome: เมนู ⋮ → <b>ติดตั้งแอป</b> หรือ <b>เพิ่มลงในหน้าจอหลัก</b> · คอม: ไอคอนติดตั้ง ⊕ ที่ช่องที่อยู่เว็บ</p>';
  return `${me?`<div class="card"><h3>🔔 แจ้งเตือนบนอุปกรณ์นี้</h3>${push}</div>`:""}<div class="card"><h3>📲 ติดตั้งเป็นแอป</h3>${inst}</div>`}
function bindAppCards(){
  if($("#pse"))$("#pse").onclick=guard(async()=>{await pushEnable();toast("เปิดการแจ้งเตือนแล้ว");loadAccount()});
  if($("#psd"))$("#psd").onclick=guard(async()=>{await pushDisable();toast("ปิดการแจ้งเตือนบนอุปกรณ์นี้แล้ว");loadAccount()});
  if($("#pst"))$("#pst").onclick=guard(async()=>{const r=await api("/push/test",{method:"POST"});toast(`ส่งแล้ว ${r.sent}/${r.devices} อุปกรณ์`)});
  if($("#pin"))$("#pin").onclick=async()=>{installEvt.prompt();await installEvt.userChoice;installEvt=null;loadAccount()}}
// deep links from notifications: ./?case=12  ./?page=gateways
{const q=new URLSearchParams(location.search);if(q.get("case")){openCaseAfter=+q.get("case")||0;page="cases"}else if(["map","patients","caregivers","devices","selftest","gateways","reports","privacy","install","integrations","users","update","settings","audit","account"].includes(q.get("page")))page=q.get("page");
 if(location.search)history.replaceState(null,"",location.pathname)}
navigator.serviceWorker&&navigator.serviceWorker.addEventListener("message",e=>{if(e.data&&e.data.url)location.href=e.data.url});

render();

