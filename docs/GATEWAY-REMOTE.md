# ตั้งค่า/สั่งงานเกตเวย์จากแดชบอร์ด

**ตั้งค่าในระบบ** (ไม่ต้องแตะเกตเวย์): ชื่อ, หมายเหตุ, พิกัด, เวลาที่ถือว่าออฟไลน์ และผู้รับแจ้งเตือนต่อเกตเวย์ — แก้ที่เมนู "เกตเวย์" (ค่าเกินกว่าชื่อ/หมายเหตุ เฉพาะผู้ดูแลระบบ)

**สั่งงานระยะไกล** สำหรับผู้ใช้ทำได้ 4 อย่างเท่านั้น (ไม่มีการรันคำสั่งอิสระ): ขอรายงานตอนนี้ · ดู log · รีสตาร์ท LoRa services · รีบูตเกตเวย์

ผู้ดูแลระบบยังตั้งค่าแบบปลอดภัยได้จากการ์ด **ค่าที่ทำงานบน Gateway**: antenna gain, log level, พิกัด และรอบรายงานสุขภาพ ระบบใช้ desired-config revision ที่เซ็นลายเซ็น Agent ตรวจ schema/ช่วงค่า สำรองไฟล์ ตรวจ TOML และ rollback หากบริการ LoRa เริ่มใหม่ไม่สำเร็จ ค่าความลับ, Gateway ID, region และช่องความถี่แก้จากหน้าเว็บไม่ได้

## อัปเดตซอฟต์แวร์ Gateway

Backend แสดงเฉพาะ release ที่กำหนดไว้ใน `GATEWAY_RELEASES_JSON` (`infra/.env`) แต่ละรายการต้องมี `version`, URL แบบ HTTPS และ SHA-256 ของไฟล์ `.tar.gz` หน้าเว็บจึงส่งได้เฉพาะรุ่นในรายการนี้ Agent จะตรวจ checksum, ปฏิเสธ path traversal, รับเฉพาะสคริปต์สามไฟล์ใน `bin/` และ rollback หาก health report ทำงานไม่ผ่าน

โครงสร้าง bundle:
```
bin/sos-gateway-agent
bin/sos-gateway-report
bin/sos-gateway-health
```

## ติดตั้ง
1. **เซิร์ฟเวอร์**: อัปเดตระบบ แล้วตรวจว่า `infra/mosquitto/acl.conf` มีบรรทัด `topic write as923/gateway/+/sos/cmd` และ `topic read as923/gateway/+/sos/cmd-result` ของ backend จากนั้น `docker compose restart mosquitto`
2. แดชบอร์ด → เกตเวย์ → เกตเวย์ที่ต้องการ → "สั่งงานระยะไกล" → คัดลอก **คีย์เอเจนต์** (ผู้ดูแลระบบเท่านั้น, ถูกบันทึกใน audit)
3. **บน Pi**: ใส่ `AGENT_KEY=<คีย์>` ต่อท้าย `/etc/sos-gateway.env` (หรือรันติดตั้งใหม่ด้วย `AGENT_KEY=... sudo -E ./install-gateway.sh`) แล้ว `sudo systemctl enable --now sos-gateway-agent`
> ติดตั้งด้วย `gateway\windows\INSTALL-GATEWAY.bat` หรือ `add-gateway.sh` รุ่นใหม่ ข้อ 2–3 ทำให้อัตโนมัติ (สคริปต์คำนวณคีย์จาก backend บน VPS)

4. รอรายงานถัดไป ป้ายเอเจนต์ในแดชบอร์ดจะขึ้น "พร้อม"

## ความปลอดภัย
- คำสั่งผู้ใช้มีรูปแบบ `{id, action, ts, sig}` ส่วน config/update มี `params` ที่ถูกนำไปรวมในลายเซ็นแบบ canonical JSON; คีย์แต่ละเกตเวย์ต่างกัน คีย์หลุดจึงกระทบเครื่องเดียว
- เอเจนต์ปฏิเสธ: ลายเซ็นผิด, ตัวอักษร action ที่ไม่อยู่ในรายการ, คำสั่งเก่า/ใหม่เกิน 120 วินาที (ต้องตั้งเวลาเครื่องให้ตรง — chrony), id ที่เคยทำแล้ว
- สี่คำสั่งผู้ใช้ไม่มีพารามิเตอร์ ส่วน config/update รับเฉพาะ JSON schema และช่วงค่าที่กำหนด ไม่มีการนำข้อความไปประกอบ shell command; log ถูกปกปิดรหัสผ่าน/โทเคนก่อนส่ง
- ACL ของ Mosquitto: เกตเวย์อ่าน/เขียนได้เฉพาะหัวข้อของตัวเอง; มีแต่ backend ที่เขียน `.../sos/cmd` ได้
- รีบูตทำให้ปุ่มผู้ป่วยส่งไม่ได้ ~1–2 นาที ระบบจึงให้ยืนยันก่อนและไม่รันซ้อน

## ทดสอบเอเจนต์โดยไม่ต้องมี broker
`SOS_AGENT_CONF=./env SOS_AGENT_DRYRUN=1 gateway/pi/bin/sos-gateway-agent --stdin < commands.jsonl`
