# ติดตั้งที่ 4: เครื่องมือ Provision (ใส่กุญแจ + ลงทะเบียนอุปกรณ์ทีละเครื่อง)

ทำบนคอมพิวเตอร์ของคุณ (Windows/macOS/Linux) · ต้องมี Python 3.10+ · ทำซ้ำได้กับทุกเครื่อง (~2 นาที/เครื่อง)

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
