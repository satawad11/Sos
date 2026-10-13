# ติดตั้งที่ 5: แดชบอร์ด (หน้าเว็บ รพ.สต.) + LINE Official Account

แดชบอร์ดถูกติดตั้งพร้อม backend ใน INSTALL-1 แล้ว (เปิดที่ `https://sos.โดเมนของคุณ`) — ส่วนนี้คือตั้งค่าใช้งานและเชื่อม LINE

## 1. เข้าแดชบอร์ด
เปิดเว็บ → เข้าสู่ระบบด้วย `admin` + รหัสที่ได้จาก `init.sh` → เมนู **บันทึกการใช้งาน** → เพิ่มผู้ใช้จริง (staff = เจ้าหน้าที่ รพ.สต. รับ/ปิดเคสได้, viewer = ดูอย่างเดียว) รหัสผ่าน ≥ 10 ตัว · **ไม่ใช้บัญชีร่วมกัน** (บันทึกการรับเรื่องจะระบุชื่อผู้กด)
- กดปุ่ม "🔕 เปิดเสียงเตือน" บนเครื่องที่เฝ้าหน้าจอ (เบราว์เซอร์ต้องให้กดก่อนจึงจะเล่นเสียงได้) แล้วเปิดหน้านี้ค้างไว้ในเวลาราชการ
- ระบบแจ้งเหตุตัวแรกที่ต้องทดสอบ: แจ้ง Gateway ออฟไลน์ (แถบเหลือง) และอุปกรณ์ออฟไลน์ (หน้าอุปกรณ์)

## 2. สร้าง LINE Official Account + Messaging API
1. สมัคร LINE Official Account (ชื่อ เช่น "แจ้งเหตุ รพ.สต.xxx") ที่ https://manager.line.biz
2. ที่ LINE Official Account Manager → Settings → **Messaging API → Enable** (เลือก/สร้าง Provider) → จะได้ช่อง Messaging API ใน https://developers.line.biz
3. ใน LINE Developers → ช่องของคุณ:
   - แท็บ **Basic settings** → คัดลอก **Channel secret**
   - แท็บ **Messaging API** → **Channel access token (long-lived) → Issue** → คัดลอก
   - **Webhook URL** = `https://sos.โดเมนของคุณ/api/line/webhook` → Update → เปิด **Use webhook** → กด **Verify** ต้องขึ้น Success
4. ใน LINE Official Account Manager → Response settings: **ปิด** "Auto-response messages" และ "Greeting" (ไม่ให้ตอบแทรกกับระบบ)
5. ใส่ค่าที่คัดลอกลง `/opt/sos/infra/.env` (`LINE_CHANNEL_SECRET`, `LINE_CHANNEL_TOKEN`) แล้ว `sudo docker compose up -d backend`
> โควตาข้อความฟรีของ LINE OA เปลี่ยนตามแผนและช่วงเวลา (ยังไม่ยืนยัน 300 หรือ 500 ข้อความ/เดือน) — **ตรวจในหน้าแผนบริการของคุณ** แล้วประเมินจากจำนวนเคส × จำนวนผู้รับต่อเคส (1–3 ข้อความ) · เคสทดสอบไม่ส่ง LINE

## 3. ตั้งค่าข้อมูลในแดชบอร์ด (ลำดับที่แนะนำ)
1. **อสม./ผู้ดูแล** → เพิ่มทุกคน (ชื่อ, บทบาท, โทร)
2. ผูก LINE ทีละคน: กด **สร้างรหัสผูก LINE** → ให้ อสม. เพิ่มเพื่อนบัญชี OA แล้ว**พิมพ์รหัสนั้นส่งในแชท** → ได้ข้อความ "ผูกบัญชีสำเร็จ" และหน้านี้เปลี่ยนเป็น ✅ (รหัสใช้ได้ 30 นาที ครั้งเดียว)
3. **ผู้ป่วย** → เพิ่ม (เก็บเท่าที่จำเป็น: ชื่อ ที่อยู่ โทร พิกัด; ช่องหมายเหตุอย่าใส่ข้อมูลโรคละเอียดเกินจำเป็น) → **กำหนดผู้ดูแล** ลำดับ 1,2,3
4. **อุปกรณ์** → ผูกอุปกรณ์กับผู้ป่วย → `active`
5. **ตั้งค่า** → เวลาทำการ/วันหยุด, เวลาก่อนแจ้งคนถัดไป (เริ่มต้น 120 วินาที), "ผู้รับสุดท้าย" (เช่น ผอ.รพ.สต. — **ต้องตกลงว่าใครรับสายนอกเวลา 24 ชม.**), ค่าเริ่มต้นอุปกรณ์

## 4. ใช้งานประจำวัน
- เคสใหม่: การ์ดสีแดงกะพริบ + เสียง + ข้อความ LINE ถึง อสม. คนที่ 1 (ปุ่ม **รับเรื่อง / โทร / นำทาง**)
- ใครกดรับก่อน ชนะ (คนอื่นจะเห็นว่า "ถูกรับโดย…แล้ว") → อุปกรณ์ผู้ป่วยได้สัญญาณเขียวภายในไม่กี่วินาที
- ปิดเคสเมื่อเสร็จ: "ปิดเคส" หรือ "เหตุผิดพลาด" + บันทึก
- นอกเวลาราชการ: อุปกรณ์แสดง "โทร 1669" ขณะที่ LINE ยังเรียก อสม. ตามลำดับ

## 5. รันแบบทดสอบบนเครื่องตัวเอง (ไม่ต้องมี VPS/อุปกรณ์)
```bash
cd sos/backend
pip install -r requirements.txt pytest
python -m pytest -q                                    # 24 เทสต์
# รัน broker + backend
mosquitto -c ../infra/dev-mosquitto.conf &             # หรือ docker run -p 1883:1883 eclipse-mosquitto:2 (ตั้ง allow_anonymous)
SOS_STATIC_DIR=../dashboard SOS_MQTT_TLS=false SOS_MQTT_HOST=127.0.0.1 SOS_MQTT_PORT=1883 SOS_MQTT_USER= \
SOS_APP_ID=app1 SOS_BOOTSTRAP_ADMIN_PASSWORD=password12345 SOS_JWT_SECRET=dev-secret-dev-secret-dev-secret-1234 \
python -m uvicorn app.main:app --port 8000
# จำลองการกดปุ่ม (เพิ่มอุปกรณ์ใน UI ก่อน DevEUI = 02aabbccddeeff00 สถานะ active)
python ../simulator/sim.py press --app-id app1 --dev-eui 02aabbccddeeff00 --poll 3
```
