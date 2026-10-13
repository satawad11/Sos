# ติดตั้งที่ 4: Provision อุปกรณ์ (ใส่กุญแจ + ลงทะเบียนทีละเครื่อง)

มี 2 วิธี ผลเหมือนกัน เลือกวิธีเดียว:
- **วิธี A (แนะนำ): หน้าเว็บ "ติดตั้งอุปกรณ์" ในแดชบอร์ด** ผ่าน Web Serial ไม่ต้องติดตั้งโปรแกรม ดูหัวข้อ A
- **วิธี B: สคริปต์ `provision.py`** สำหรับคนที่ชอบ command line หรือทำเป็นชุดใหญ่ ดูหัวข้อ B (ข้อ 1-6 ด้านล่าง)

## A. หน้าเว็บ "ติดตั้งอุปกรณ์" (เฉพาะผู้ดูแลระบบ)

**เตรียมครั้งเดียวที่ VPS**
1. สร้าง API key ใน ChirpStack (Tenant → API keys → ชื่อ `provision`) และจด Device profile ID (INSTALL-1 ข้อ 5)
2. ใส่ใน `/opt/sos/infra/.env`:
   ```
   CHIRPSTACK_API_KEY=<API key>
   CHIRPSTACK_PROFILE_ID=<Device profile ID>
   ```
3. `cd /opt/sos/infra && docker compose up -d --build backend` (backend คุยกับ ChirpStack เองภายใน docker โทเคนไม่อยู่ในเบราว์เซอร์)

**ต่อเครื่องใหม่แต่ละเครื่อง** (~2 นาที)
1. เปิดแดชบอร์ดด้วย **Chrome หรือ Edge บนคอมพิวเตอร์** (ผ่าน https) → เมนู **ติดตั้งอุปกรณ์**
2. เสียบ Heltec (อัปโหลดเฟิร์มแวร์แล้ว หน้าจอ NOT SETUP) ด้วยสาย USB ที่เป็นสายข้อมูล
3. ตั้งป้ายชื่อ (ระบบเสนอเลขถัดไปให้ เช่น SOS-004) กด **เริ่มติดตั้ง** แล้วเลือกพอร์ตของเครื่องในหน้าต่างที่เด้งขึ้น
4. รอครบ 5 ขั้น: ตรวจเครื่อง → ลงทะเบียน → เขียนคีย์ → อ่านค่ากลับยืนยัน → รอเครื่อง join
5. กด **พิมพ์ป้าย** (QR ของ DevEUI) ติดกล่อง · ไปหน้า **อุปกรณ์** ผูกผู้ป่วยแล้วตั้งสถานะ `active`

ถ้าขั้นใดล้ม ระบบ**ลบการลงทะเบียนให้อัตโนมัติ** (ทั้ง ChirpStack และแดชบอร์ด) แล้วกดเริ่มใหม่ได้ · ขั้น "รอเครื่อง join" ข้ามได้ถ้ายังไม่มี gateway
ปัญหาที่พบบ่อย: เลือกพอร์ตไม่เจอ → ใช้สาย USB ที่เป็นสายข้อมูล/ลองพอร์ตอื่น · "เครื่องไม่ตอบ PING" → เฟิร์มแวร์ยังไม่ถูกอัปโหลด หรือโปรแกรมอื่น (Serial Monitor) เปิดพอร์ตอยู่ ให้ปิดก่อน

## B. สคริปต์ command line (provision.py)
ทำบนคอมพิวเตอร์ของคุณ (Windows/macOS/Linux) · ต้องมี Python 3.10+ (เฉพาะวิธี B) · ทำซ้ำได้กับทุกเครื่อง (~2 นาที/เครื่อง)

## 1. ติดตั้ง
```bash
cd sos/tools/provision
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install httpx pyserial qrcode[pil]
```

## 2. เปิดอุโมงค์ไป ChirpStack (เปิดค้างไว้ในอีกหน้าต่าง)
```bash
ssh -L 8090:localhost:8090 sos@sos.โดเมนของคุณ
```

## 3. ตั้งค่าตัวแปร (ค่าจาก INSTALL-1 ข้อ 5)
```bash
export CS_URL=http://localhost:8090
export CS_API_KEY=<API key ที่ชื่อ provision>
export CS_APP_ID=<Application ID>
export CS_PROFILE_ID=<Device profile ID>
export SOS_URL=https://sos.โดเมนของคุณ
export SOS_USER=admin
export SOS_PASSWORD=<รหัสแอดมิน>
```
(Windows PowerShell: `$env:CS_URL="..."`)

## 4. ลองแบบแห้ง (ไม่ต่อเครือข่าย/ไม่เขียนอุปกรณ์)
```bash
python provision.py --dry-run --label SOS-001
```

## 5. Provision จริง
1. เสียบบอร์ดที่แฟลช firmware แล้ว (หน้าจอ NOT SETUP) ทาง USB-C → หา port (`ls /dev/ttyACM*` / Windows: Device Manager → COMx)
2. ```bash
   python provision.py --port /dev/ttyACM0 --label SOS-001 --qr
   ```
3. ผลที่ถูกต้อง: `ChirpStack: registered` → `Backend: registered` → `Device: INFO fw=0.1 provisioned=1 deveui=...` → `DONE`
4. ถ้าขั้นใดล้ม เครื่องมือจะ**ลบรายการที่ ChirpStack ให้อัตโนมัติ** แล้วรันใหม่ได้ (รายการที่ backend ถ้าเกิดแล้วให้ลบ/ปรับสถานะเป็น retired ในหน้าอุปกรณ์)
5. ติดป้าย (ไฟล์ `SOS-001.png` = QR ของ DevEUI) ที่กล่อง
6. จ่ายไฟใหม่ → อุปกรณ์ join → ใน ChirpStack เห็น "Join request/accept" → ในแดชบอร์ดหน้า **อุปกรณ์** เห็น `last_seen`

## 6. ผูกอุปกรณ์กับผู้ป่วย
แดชบอร์ด → **อุปกรณ์** → แก้ไข → เลือกผู้ป่วย → สถานะ `active` → บันทึก (อุปกรณ์สถานะ `ready` ยังรับสัญญาณทดสอบได้ แต่ควร `active` ก่อนติดตั้งจริง)

## ความปลอดภัยของกุญแจ
- AppKey ถูกสร้างบนเครื่องคุณ ส่งเข้า ChirpStack (ผ่านอุโมงค์) และบอร์ด (USB) เท่านั้น **ไม่ถูกบันทึกลงไฟล์ CSV** (ยกเว้นใช้ `--save-key` — ไม่แนะนำ)
- ไฟล์ `provisioned_devices.csv` เก็บ DevEUI+ป้าย+เวลา สำหรับทำทะเบียน
- เปลี่ยนเครื่องที่เสีย: ตั้งอุปกรณ์เดิมเป็น `repair`/`retired` แล้ว provision เครื่องใหม่ (DevEUI ใหม่) และผูกผู้ป่วยเดิม
- ทดสอบสคริปต์: `python -m pytest -q` (5 เทสต์)
