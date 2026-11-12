"use strict";
// USB provisioning and firmware update (Web Serial)
// Loaded by index.html as plain scripts, in order: core, cases, people, network, system, install, admin (they share one global scope).
// ---------------------------------------------------------------- USB provisioning (Web Serial)
class SerialLink{
  constructor(){this.port=null;this.lines=[];this.waiters=[];this.buf="";this.writer=null;this.reader=null;this.closed=false;this.stopping=false}
  static supported(){return "serial" in navigator}
  async open(port){
    this.port=port||await navigator.serial.requestPort();await this.port.open({baudRate:115200});
    this.writer=this.port.writable.getWriter();this._pump()}
  // Boards with a separate USB-UART chip (Heltec V2 / CP2102) produce a BREAK/framing error when the ESP32 restarts.
  // Web Serial reports that as a non-fatal read error and hands out a fresh readable stream — so keep reading
  // until the port is really gone (readable === null) or close() was called.
  async _pump(){const dec=new TextDecoder();
    while(!this.stopping&&this.port&&this.port.readable){
      this.reader=this.port.readable.getReader();let ended=false;
      try{for(;;){const {value,done}=await this.reader.read();if(done){ended=true;break}this.buf+=dec.decode(value,{stream:true});
        let i;while((i=this.buf.search(/\r?\n/))>=0){const l=this.buf.slice(0,i).trim();this.buf=this.buf.slice(i).replace(/^\r?\n/,"");if(l){this.lines.push(l);this._wake()}}}}catch{this.buf=""}
      finally{try{this.reader.releaseLock()}catch{}}
      if(ended||this.stopping)break;
      await new Promise(r=>setTimeout(r,100))}
    this.closed=true;this._wake()}
  _wake(){this.waiters.splice(0).forEach(f=>f())}
  async send(cmd){await this.writer.write(new TextEncoder().encode(cmd+"\n"))}
  flush(){this.lines.length=0}
  // wait for a line starting with prefix; lines starting with ERR reject
  async expect(prefix,ms){const end=Date.now()+ms;
    for(;;){while(this.lines.length){const l=this.lines.shift();if(l.startsWith(prefix))return l;if(l.startsWith("ERR"))throw new Error("เครื่องตอบ: "+l)}
      const left=end-Date.now();if(left<=0)throw new Error("ไม่มีคำตอบ "+prefix+" จากเครื่อง");if(this.closed)throw new Error("ขาดการเชื่อมต่อ USB");
      await new Promise(r=>{const t=setTimeout(r,left);this.waiters.push(()=>{clearTimeout(t);r()})})}}
  async ping(){for(let i=0;i<5;i++){this.flush();await this.send("PING");try{return await this.expect("PONG",1500)}catch{}}throw new Error("เครื่องไม่ตอบ PING — ตรวจสาย USB (ต้องเป็นสายข้อมูล) และว่าอัปโหลดเฟิร์มแวร์แล้ว")}
  async close(){this.stopping=true;try{await this.reader.cancel()}catch{}try{this.writer.releaseLock()}catch{}try{await this.port.close()}catch{}}
}
let link=null;
// ---------------------------------------------------------------- firmware update over USB (esptool-js, docs/FIRMWARE-UPDATE.md)
// Writes the application image only (0x10000): bootloader, partition table and the NVS partition holding the
// LoRaWAN keys stay as they are, so the button keeps its identity and does not need provisioning again.
let FWI=null,FWT=0;
async function fwLatest(force){if(role==="viewer")return null;if(force||Date.now()-FWT>300000){FWT=Date.now();try{FWI=await api("/firmware")}catch(e){FWI=null}}return FWI}
const verN=v=>String(v||"").split(".").map(x=>+x||0),verLt=(a,b)=>{const x=verN(a),y=verN(b);for(let i=0;i<Math.max(x.length,y.length);i++){if((x[i]||0)!==(y[i]||0))return (x[i]||0)<(y[i]||0)}return false};
function fwNewest(){return (FWI?.builds||[]).reduce((m,b)=>!m||verLt(m,b.version)?b.version:m,"")}
function fwBadge(v){const n=fwNewest();return v&&n&&verLt(v,n)?` <span class="tag accepted" title="เวอร์ชันล่าสุด ${esc(n)}">มี ${esc(n)}</span>`:""}
// MD5 of a Uint8Array → hex (esptool-js needs it to verify what landed in flash)
function md5hex(u8){const K=new Uint32Array(64),S=[7,12,17,22,7,12,17,22,7,12,17,22,7,12,17,22,5,9,14,20,5,9,14,20,5,9,14,20,5,9,14,20,4,11,16,23,4,11,16,23,4,11,16,23,4,11,16,23,6,10,15,21,6,10,15,21,6,10,15,21,6,10,15,21];
  for(let i=0;i<64;i++)K[i]=Math.floor(Math.abs(Math.sin(i+1))*4294967296)>>>0;
  const n=u8.length,len=((n+8>>6)+1)*64,b=new Uint8Array(len);b.set(u8);b[n]=0x80;const dv=new DataView(b.buffer);
  dv.setUint32(len-8,(n*8)>>>0,true);dv.setUint32(len-4,Math.floor(n/0x20000000)>>>0,true);
  let a0=0x67452301,b0=0xefcdab89,c0=0x98badcfe,d0=0x10325476;const M=new Uint32Array(16);
  for(let o=0;o<len;o+=64){for(let j=0;j<16;j++)M[j]=dv.getUint32(o+j*4,true);let A=a0,B=b0,C=c0,D=d0;
    for(let i=0;i<64;i++){let F,g;
      if(i<16){F=(B&C)|(~B&D);g=i}else if(i<32){F=(D&B)|(~D&C);g=(5*i+1)%16}else if(i<48){F=B^C^D;g=(3*i+5)%16}else{F=C^(B|~D);g=(7*i)%16}
      F=(F+A+K[i]+M[g])>>>0;A=D;D=C;C=B;B=(B+((F<<S[i])|(F>>>(32-S[i]))))>>>0}
    a0=(a0+A)>>>0;b0=(b0+B)>>>0;c0=(c0+C)>>>0;d0=(d0+D)>>>0}
  return [a0,b0,c0,d0].map(x=>[0,8,16,24].map(s=>((x>>>s)&255).toString(16).padStart(2,"0")).join("")).join("")}
async function sha256hex(u8){const h=await crypto.subtle.digest("SHA-256",u8);return [...new Uint8Array(h)].map(b=>b.toString(16).padStart(2,"0")).join("")}

async function fwCard(box,ds){
  const m=await fwLatest(true);const ok=SerialLink.supported();
  if(!m?.available){box.innerHTML=`<h3>อัปเดตเฟิร์มแวร์ผ่าน USB</h3><div class="warn">ยังไม่มีไฟล์เฟิร์มแวร์บนเซิร์ฟเวอร์ — ไฟล์จะมาเองหลัง GitHub build เสร็จ แล้วกด "ตรวจหาเวอร์ชันใหม่" หรือ "อัปเดตระบบ" ที่หน้าอัปเดตระบบ${role==="admin"?' หรือกดปุ่มด้านล่าง':""}</div>${role==="admin"?'<button class="btn" id="fwsync">ดึงไฟล์เฟิร์มแวร์จาก GitHub</button>':""}`;
    $("#fwsync")&&($("#fwsync").onclick=guard(async()=>{await api("/system/update",{method:"POST",body:{action:"firmware"}});toast("ส่งคำขอแล้ว รอประมาณ 10 วินาทีแล้วเปิดหน้านี้ใหม่","info")}));return}
  const vs=[...new Set(m.builds.map(b=>b.version))];
  box.innerHTML=`<h3>อัปเดตเฟิร์มแวร์ผ่าน USB</h3>
   <p class="mut">เฟิร์มแวร์ล่าสุด <b>${esc(vs.join(", "))}</b> · build ${esc(m.commit)} เมื่อ ${fmt(m.built_at)} · อัปเดตเฉพาะโปรแกรม คีย์ LoRaWAN และการตั้งค่าในเครื่องยังอยู่ ไม่ต้องติดตั้งใหม่</p>
   ${ok?"":'<div class="warn">เบราว์เซอร์นี้ไม่รองรับ Web Serial — ใช้ Chrome หรือ Edge บนคอมพิวเตอร์</div>'}
   <ol class="mut"><li>เสียบเครื่องด้วยสาย USB (ต้องเป็นสายที่ส่งข้อมูลได้ ไม่ใช่สายชาร์จอย่างเดียว) เข้าคอมพิวเตอร์เครื่องนี้</li><li>ปิดหน้าต่างอื่นที่เปิดพอร์ตนี้อยู่ (Serial Monitor, PlatformIO)</li><li>กด "เริ่มอัปเดต" แล้วเลือกพอร์ตของเครื่อง ระหว่างเขียนห้ามถอดสาย</li></ol>
   <div class="row">บอร์ด <select id="fwenv"><option value="">ตรวจอัตโนมัติ</option>${m.builds.map(b=>`<option value="${esc(b.env)}">${esc(b.label)} · ${esc(b.version)}</option>`).join("")}</select>
    <button class="btn p" id="fwgo" ${ok?"":"disabled"}>เริ่มอัปเดต</button></div>
   <div id="fwsteps"></div>`;
  $("#fwgo").onclick=()=>fwRun(m,ds||[],$("#fwenv").value);
}

async function fwRun(m,ds,pick){
  const S=[["usb","เชื่อมต่อและอ่านข้อมูลเครื่อง"],["chip","เข้าโหมดอัปเดตและตรวจชิป"],["dl","ดาวน์โหลดและตรวจไฟล์เฟิร์มแวร์"],["write","เขียนเฟิร์มแวร์ลงเครื่อง"],["boot","รีสตาร์ตและยืนยันเวอร์ชันใหม่"]];
  const box=$("#fwsteps"),go=$("#fwgo");if(!box)return;go.disabled=true;
  box.innerHTML=`<ol class="fws">${S.map(([k,t])=>`<li data-k="${k}"><span class="fwi"></span><span class="fwt">${t}<small></small></span></li>`).join("")}</ol><div class="fwbar" hidden><span></span></div><div id="fwres"></div>`;
  const set=(k,state,note)=>{const li=box.querySelector(`[data-k=${k}]`);if(!li)return;li.className="s-"+state;if(note!=null)li.querySelector("small").textContent=note?" — "+note:""};
  const bar=box.querySelector(".fwbar"),barS=bar.querySelector("span");
  let port=null,link=null,transport=null,cur=null,before={fw:"",eui:""},build=null,step="usb";
  const report=(ok,error="")=>api("/firmware/flashed",{method:"POST",body:{dev_eui:(before.eui||"").slice(0,16),env:build?.env||pick||"-",version:build?.version||"-",from_version:before.fw||"",ok,error:String(error).slice(0,200)}}).catch(()=>{});
  const readInfo=async lk=>{await lk.ping();lk.flush();await lk.send("INFO");const l=await lk.expect("INFO",3000);
    return {fw:(l.match(/fw=([\d.]+)/)||[])[1]||"",eui:(l.match(/deveui=([0-9a-f]{16})/i)||[])[1]?.toLowerCase()||"",prov:/provisioned=1/.test(l)}};
  try{
    // 1 — what is plugged in? (an empty or broken board may not answer: allowed to continue)
    set("usb","run");port=await navigator.serial.requestPort();link=new SerialLink();await link.open(port);
    try{before=await readInfo(link)}catch(e){before={fw:"",eui:""}}
    await link.close();link=null;
    cur=ds.find(d=>d.dev_eui===before.eui);
    set("usb","ok",before.fw?`${cur?esc(cur.label)+" · ":""}เวอร์ชัน ${before.fw}${before.eui?" · "+before.eui:""}${before.eui&&!cur?" (ไม่พบในรายการอุปกรณ์)":""}`:"เครื่องไม่ตอบ (อาจยังไม่มีเฟิร์มแวร์) — อัปเดตต่อได้");
    // 2/3 — chip decides which images fit; the image must match the manifest byte for byte
    set("chip","run");step="chip";
    const esp=await import("./vendor/esptool/esptool-0.7.1.js");
    transport=new esp.Transport(port,true);
    const loader=new esp.ESPLoader({transport,baudrate:460800,romBaudrate:115200,terminal:{clean(){},writeLine(){},write(){}}});
    const desc=await loader.main("default_reset");const chip=loader.chip.CHIP_NAME;
    const fits=m.builds.filter(b=>b.chip===chip);
    if(!fits.length)throw new Error(`ชิป ${chip} ไม่มีเฟิร์มแวร์ที่ตรงกัน (มีสำหรับ ${[...new Set(m.builds.map(b=>b.chip))].join(", ")})`);
    if(pick){build=fits.find(b=>b.env===pick);if(!build)throw new Error(`บอร์ดที่เลือกไม่ตรงกับชิปที่เสียบอยู่ (${chip}) — เลือก "ตรวจอัตโนมัติ" หรือบอร์ดที่ถูกต้อง`)}
    else build=fits.length===1?fits[0]:(fits.find(b=>/gps/.test(b.env)===!!cur?.loc)||fits[0]);
    set("chip","ok",`${desc} → ${build.label}`);
    set("dl","run");step="dl";
    const r=await fetch(`/api/firmware/${encodeURIComponent(build.env)}.bin`,{headers:{authorization:"Bearer "+token},cache:"no-store"});
    if(!r.ok)throw new Error("ดาวน์โหลดไฟล์ไม่สำเร็จ ("+r.status+")");
    const img=new Uint8Array(await r.arrayBuffer());
    if(img.length!==build.size||await sha256hex(img)!==build.sha256)throw new Error("ไฟล์ที่ดาวน์โหลดไม่ตรงกับที่ build ไว้ (SHA-256) — ลองใหม่อีกครั้ง");
    set("dl","ok",`${build.version} · ${(img.length/1024).toFixed(0)} KB · SHA-256 ตรง`);
    if(before.fw&&!verLt(before.fw,build.version)&&!await ask(`เครื่องนี้ใช้เวอร์ชัน ${before.fw} อยู่แล้ว\nจะเขียนเวอร์ชัน ${build.version} ทับหรือไม่`,{ok:"เขียนทับ"}))throw new Error("ยกเลิกแล้ว — เครื่องยังใช้เวอร์ชันเดิม");
    // 4 — write + verify (esptool compares the MD5 of what is in flash with ours)
    set("write","run");step="write";bar.hidden=false;
    await loader.writeFlash({fileArray:[{data:img,address:build.offset??0x10000}],flashMode:"keep",flashFreq:"keep",flashSize:"keep",eraseAll:false,compress:true,
      reportProgress:(i,w,t)=>{const p=Math.round(w/t*100);barS.style.width=p+"%";set("write","run",p+"%")},calculateMD5Hash:md5hex});
    set("write","ok","เขียนและตรวจ MD5 แล้ว");bar.hidden=true;
    // 5 — reboot, then ask the new firmware who it is
    set("boot","run");step="boot";
    await loader.after("hard_reset");await transport.disconnect();transport=null;
    let after=null;const end=Date.now()+25000;
    while(!after&&Date.now()<end){await new Promise(r=>setTimeout(r,1500));
      try{link=new SerialLink();await link.open(port);after=await readInfo(link)}catch(e){}finally{if(link){await link.close();link=null}}
      if(!after){const ps=await navigator.serial.getPorts();if(ps.length===1&&ps[0]!==port)port=ps[0]}}   // native-USB boards come back as a new port
    if(!after){set("boot","warn","เขียนสำเร็จแต่อ่านเวอร์ชันกลับไม่ได้ — ถอดเสียบสาย USB แล้วดูเวอร์ชันในหน้าอุปกรณ์หลังเครื่องส่ง heartbeat");await report(true,"no INFO after reboot");
      $("#fwres").innerHTML=`<div class="warn">เขียนเฟิร์มแวร์ ${esc(build.version)} สำเร็จ แต่ยืนยันหลังรีสตาร์ตไม่ได้</div>`;return}
    if(after.fw!==build.version)throw new Error(`เครื่องรีสตาร์ตแล้วยังรายงานเวอร์ชัน ${after.fw||"?"} (ควรเป็น ${build.version})`);
    if(before.eui&&after.eui&&after.eui!==before.eui)throw new Error(`DevEUI เปลี่ยนจาก ${before.eui} เป็น ${after.eui} — ตรวจการติดตั้งเครื่องนี้ใหม่`);
    before.eui=before.eui||after.eui;
    set("boot","ok",`เวอร์ชัน ${after.fw}${after.prov?" · คีย์ LoRaWAN ยังอยู่":" · เครื่องยังไม่ได้ติดตั้งคีย์ (ใช้ขั้นตอนติดตั้งด้านบน)"}`);
    await report(true);FWT=0;
    $("#fwres").innerHTML=`<div class="fwdone">${ico("check")}<div><b>อัปเดตเป็นเวอร์ชัน ${esc(after.fw)} สำเร็จ</b><br><span class="mut">${cur?esc(cur.label)+" · ":""}ถอดสาย USB แล้วนำเครื่องไปติดตั้งได้ เครื่องจะเชื่อมต่อเครือข่ายใหม่เอง</span></div></div>`;
    toast(`อัปเดตเฟิร์มแวร์เป็น ${after.fw} แล้ว`,"ok");
  }catch(e){
    const raw=e?.message||String(e),msg=e?.name==="NotFoundError"?"ไม่ได้เลือกพอร์ต":/Failed to open|already open|NetworkError/i.test(raw)?"เปิดพอร์ตไม่ได้ — ปิดโปรแกรมอื่นที่ใช้พอร์ตนี้อยู่ (Serial Monitor) แล้วลองใหม่"
      :step==="chip"&&!/[\u0E00-\u0E7F]/.test(raw)?"เข้าโหมดอัปเดตไม่ได้ ("+raw+") — กดปุ่ม PRG ค้างไว้ แล้วกด RST หนึ่งครั้ง จากนั้นกดเริ่มอัปเดตใหม่"
      :step==="write"&&!/[\u0E00-\u0E7F]/.test(raw)?"เขียนไม่สำเร็จ ("+raw+")":raw;
    set(step,"bad",msg);bar.hidden=true;
    if(step!=="usb"||port)await report(false,msg);
    $("#fwres").innerHTML=`<div class="warn">${esc(msg)}${step==="write"?"<br>เครื่องยังเข้าโหมดอัปเดตได้ตามปกติ กด \"เริ่มอัปเดต\" อีกครั้งได้เลย":""}</div>`;
  }finally{try{link&&await link.close()}catch{}try{transport&&await transport.disconnect()}catch{}go.disabled=false}
}

const loadInstall=guard(async()=>{
  if(role!=="admin"){$("#main").innerHTML='<div class="warn">เฉพาะผู้ดูแลระบบ</div>';return}
  const [st,ds]=await Promise.all([api("/provision/status"),api("/devices")]);if(page!=="install")return;
  const next="SOS-"+String(1+Math.max(0,...ds.map(d=>+((d.label||"").match(/^SOS-(\d+)$/)||[0,0])[1]))).padStart(3,"0");
  const supported=SerialLink.supported();
  $("#main").innerHTML=`<div class="card"><h3>ติดตั้งอุปกรณ์ใหม่ผ่าน USB</h3>
   ${st.configured?"":'<div class="warn">ยังไม่ได้ตั้ง ChirpStack API key / Device profile ใน .env ของเซิร์ฟเวอร์ (CHIRPSTACK_API_KEY, CHIRPSTACK_PROFILE_ID) — ดู docs/INSTALL-4</div>'}
   ${supported?"":'<div class="warn">เบราว์เซอร์นี้ไม่รองรับ Web Serial — ใช้ Chrome หรือ Edge บนคอมพิวเตอร์ (ต้องเปิดผ่าน https)</div>'}
   <ol><li>เสียบเครื่อง Heltec ด้วยสาย USB (สายข้อมูล) เข้าคอมพิวเตอร์เครื่องนี้ ซึ่งอัปโหลดเฟิร์มแวร์แล้ว</li><li>ตั้งป้ายชื่อ กด "เริ่มติดตั้ง" แล้วเลือกพอร์ตของเครื่องในหน้าต่างที่เด้งขึ้น</li><li>รอจนครบทุกขั้นตอน แล้วพิมพ์ป้าย QR ติดกล่อง</li></ol>
   <div class="row">ป้ายชื่อเครื่อง <input id="plbl" value="${next}" maxlength="64"><button class="btn p" id="pgo" ${st.configured&&supported?"":"disabled"}>เริ่มติดตั้ง</button></div>
   <div id="psteps"></div><div id="pres"></div></div>
   <div class="card" id="fwcard"></div>
   <p class="mut">คีย์ลับ (AppKey) สร้างที่เซิร์ฟเวอร์ ส่งให้หน้านี้ผ่าน HTTPS ครั้งเดียวเพื่อเขียนลงเครื่องผ่าน USB เท่านั้น ไม่เก็บในแดชบอร์ด ไม่แสดงบนจอ และไม่อยู่ใน audit log</p>`;
  fwCard($("#fwcard"),ds);
  if(!$("#pgo"))return;
  $("#pgo").onclick=async()=>{
    const label=$("#plbl").value.trim();if(!label){toast("ใส่ป้ายชื่อก่อน");return}
    $("#pgo").disabled=true;$("#pres").innerHTML="";
    const steps=[["usb","เชื่อมต่อ USB และตรวจเครื่อง"],["reg","ลงทะเบียนในระบบ (ChirpStack + แดชบอร์ด)"],["flash","เขียนคีย์ลงเครื่อง"],["verify","อ่านค่ากลับมายืนยัน"],["join","รอเครื่องเชื่อมต่อเครือข่าย (ข้ามได้ถ้ายังไม่มี gateway)"]];
    const mark=(k,cls,msg)=>{const e=document.getElementById("st-"+k);if(e)e.innerHTML=`<span class="${cls}">${cls==="ok"?"✔":cls==="bad"?"✘":"…"}</span> ${steps.find(x=>x[0]===k)[1]}${msg?` <span class="mut">${esc(msg)}</span>`:""}`};
    $("#psteps").innerHTML=steps.map(([k])=>`<div class="step" id="st-${k}"><span class="mut">○</span> ${steps.find(x=>x[0]===k)[1]}</div>`).join("");
    let reg=null,cur="usb";
    try{
      link=new SerialLink();await link.open();mark("usb","wait");await new Promise(r=>setTimeout(r,1500));await link.ping();mark("usb","ok","ตอบ PONG");
      cur="reg";mark("reg","wait");reg=await api("/provision/start",{method:"POST",body:{label}});mark("reg","ok",reg.dev_eui);
      cur="flash";mark("flash","wait");link.flush();await link.send(`PROV ${reg.dev_eui} ${reg.app_key} ${reg.join_eui}`);let okLine=null;try{okLine=await link.expect("OK",6000)}catch(e){if(/^เครื่องตอบ/.test(e.message))throw e}reg.app_key=null;
      if(link.closed)throw new Error("ขาดการเชื่อมต่อ USB (ถอดสายหรือพอร์ตหาย)");
      mark("flash","ok",okLine?"เครื่องกำลังรีสตาร์ท":"ไม่เห็นคำตอบ OK — จะยืนยันด้วยการอ่านค่ากลับ");
      cur="verify";mark("verify","wait");await new Promise(r=>setTimeout(r,3000));let info=null;
      for(let i=0;i<6&&!info;i++){link.flush();await link.send("INFO");try{info=await link.expect("INFO",2500)}catch{}}
      if(!info)throw new Error("อ่านค่ากลับไม่ได้");
      if(!info.includes("deveui="+reg.dev_eui)||!info.includes("provisioned=1"))throw new Error("ค่าที่อ่านกลับไม่ตรง: "+info);
      await api(`/provision/${reg.dev_eui}/confirm`,{method:"POST",body:{info}});mark("verify","ok",info);cur="label";
      await link.close();link=null;
      const svg=await (await fetch(`/api/provision/${reg.dev_eui}/label.svg`,{headers:{authorization:"Bearer "+token}})).text();
      $("#pres").innerHTML=`<div class="card"><div class="row" style="align-items:flex-start"><div style="flex:1;min-width:240px"><b class="ok">ติดตั้งสำเร็จ</b><br>ถอดสาย USB แล้วจ่ายไฟเครื่อง (ถ้าเครื่องอยู่ในระยะ gateway จะเชื่อมต่อเอง) จากนั้นไปที่หน้า "อุปกรณ์" เพื่อผูกผู้ป่วยและตั้งเป็น active</div>
        <div id="plabel" style="display:inline-block;margin:8px;padding:8px;border:1px dashed var(--bd);width:220px"><div style="width:120px">${svg.replace(/<\?xml[^>]*>/,"")}</div><div><b>${esc(label)}</b></div><div style="font:12px monospace">${reg.dev_eui}</div></div></div>
        <div class="row"><button class="btn" id="pprint">🖨 พิมพ์ป้าย</button><button class="btn" id="pnext">ติดตั้งเครื่องถัดไป</button></div></div>`;
      $("#pprint").onclick=()=>window.print();$("#pnext").onclick=()=>loadInstall();
      cur="join";mark("join","wait","กำลังรอ… จ่ายไฟเครื่องแล้วรอสักครู่");
      const end=Date.now()+180000;let seen=null;
      while(Date.now()<end&&page==="install"&&!seen){await new Promise(r=>setTimeout(r,5000));const l=await api("/devices");const d=l.find(x=>x.dev_eui===reg.dev_eui);if(d&&d.last_seen)seen=d}
      if(seen)mark("join","ok",`เห็นเครื่องแล้ว RSSI ${seen.rssi??"-"} dBm`);else{const e=document.getElementById("st-join");if(e)e.innerHTML='<span class="mut">○</span> ยังไม่เห็นเครื่อง — ไม่เป็นไร ให้ตรวจที่หน้า "อุปกรณ์" ภายหลัง (ตรวจว่า gateway ออนไลน์)'}
    }catch(e){
      mark(cur,"bad",e.message);
      if(link){await link.close();link=null}
      if(reg&&cur!=="join"&&cur!=="label"){try{await api(`/provision/${reg.dev_eui}/abort`,{method:"POST"});toast("ติดตั้งไม่สำเร็จ ยกเลิกการลงทะเบียนแล้ว ลองใหม่ได้")}catch(x){toast("ยกเลิกการลงทะเบียนไม่สำเร็จ: ลบเครื่อง "+reg.dev_eui+" เองในหน้าอุปกรณ์/ChirpStack")}}
      else toast(e.message);
      $("#pgo").disabled=false}
  }});

