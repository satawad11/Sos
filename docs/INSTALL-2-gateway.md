# ติดตั้งที่ 2: Gateway (Raspberry Pi 4B + Seeed WM1302 SPI HAT, ย่าน AS923-1 ไทย)

> **เปลี่ยนจากแผนเดิม:** ตรวจ repo ทางการของ ChirpStack Gateway OS แล้วพบว่า image สำหรับ Raspberry Pi มีเฉพาะ Pi Zero/1, Pi 2 (32-bit) และ Pi 5 — **ไม่มี image ของ Pi 4 โดยเฉพาะ** (และ Pi 5 ไม่รองรับ WM1302 HAT ตามที่ Seeed ระบุ) จึงเปลี่ยนเป็น **Raspberry Pi OS Lite 64-bit + ChirpStack Concentratord + MQTT Forwarder** ติดตั้งด้วยสคริปต์ชุดเดียว (ใช้ซอฟต์แวร์ตัวเดียวกับ Gateway OS) ค่า pin/โมเดลตรวจกับซอร์ส concentratord แล้ว: `seeed_wm1302`, SPI `/dev/spidev0.0`, reset GPIO17, power-enable GPIO18, SX1261 reset GPIO5

> ⚠️ **สถานะ:** สคริปต์ผ่านการตรวจ syntax และการ render ไฟล์ตั้งค่าแล้ว แต่ **ยังไม่เคยรันบน Pi/WM1302 จริง** และชื่อไฟล์ binary ใน GitHub releases ตรวจไม่ได้จากที่เขียน (สคริปต์ค้นหาอัตโนมัติ ถ้าไม่เจอจะบอกวิธีใส่ลิงก์เอง) — รันครั้งแรกให้เฝ้าดู

## ⚡ แบบคลิกเดียว (แนะนำ — จาก Windows)
1. เขียนการ์ดด้วย Raspberry Pi Imager: **Raspberry Pi OS Lite (64-bit)** · hostname `sos-gw1` · ตั้งผู้ใช้/รหัสผ่าน · **เปิด SSH** · timezone Asia/Bangkok
2. ประกอบ WM1302 + **เสาอากาศ** → เสียบการ์ด → **สาย LAN** → เปิดเครื่อง รอ 2 นาที
3. ในโฟลเดอร์ repo ดับเบิลคลิก **`gateway\windows\INSTALL-GATEWAY.bat`** แล้วตอบคำถาม (ส่วนใหญ่กด Enter)

สคริปต์ทำให้ทั้งหมด: ฝากกุญแจ SSH (ถามรหัสผ่าน Pi/VPS ครั้งเดียว), ส่งชุดติดตั้ง (แก้ CRLF ให้), อ่าน Gateway ID, สร้างบัญชี MQTT **และเพิ่มเกตเวย์ในแดชบอร์ด + ChirpStack อัตโนมัติ**, ใส่ **คีย์เอเจนต์สั่งงานระยะไกล** (docs/GATEWAY-REMOTE.md) ให้เอง, คัดลอก CA, ติดตั้ง, **รีบูตให้เองแล้วติดตั้งต่อ**, ตรวจสุขภาพ — รันซ้ำได้ปลอดภัย (รันซ้ำ = หมุนรหัส MQTT ใหม่) · ค่าที่ตอบเก็บใน `gateway-settings.json` (ไม่มีรหัสผ่าน) · log อยู่ที่ `gateway\windows\logs\`
> การเพิ่มใน ChirpStack ใช้ค่า ChirpStack ของ backend (`CHIRPSTACK_API_KEY` / `CHIRPSTACK_APP_ID` / `CHIRPSTACK_PROFILE_ID` ใน `.env`, INSTALL-1 ข้อ 5) — ถ้ายังไม่ได้ตั้ง สคริปต์จะบอกให้เพิ่มเองที่แดชบอร์ด > เกตเวย์

ถ้าติดปัญหาหรืออยากทำทีละขั้น ใช้ขั้นตอนด้านล่าง

---

เวลา ~1 ชั่วโมง · ต้องมี: Pi 4B + แหล่งจ่าย 5V/3A คุณภาพดี, microSD ≥ 16 GB, WM1302 Pi HAT + เสา AS923, **สาย LAN** (ID gateway สร้างจาก MAC ของ eth0), VPS พร้อมแล้ว (INSTALL-1)
> **ห้ามเปิดเครื่องโดยไม่ต่อเสาอากาศ**

## 1. ฮาร์ดแวร์
ต่อ WM1302 เข้า 40-pin header ให้แน่น · ต่อเสา · วางสูง ใกล้หน้าต่าง ห่างเราเตอร์/อินเวอร์เตอร์ ≥ 1 ม. · กล่องต้องไม่ใช่โลหะปิดทึบ

## 2. เขียน OS
Raspberry Pi Imager → **Raspberry Pi OS Lite (64-bit)** → ตั้งค่า (⚙): hostname `sos-gw1`, **เปิด SSH แบบ public key** (วางคีย์ของคุณ), ตั้งผู้ใช้/รหัสผ่านยาว, timezone Asia/Bangkok → เขียนการ์ด → เสียบสาย LAN → เปิดเครื่อง → `ssh <user>@sos-gw1.local`

## 3. นำชุดติดตั้งไปที่ Pi + ดู Gateway ID
```bash
# จากเครื่องคุณ
scp -r sos/gateway/pi <user>@sos-gw1.local:~/gw
ssh <user>@sos-gw1.local
cd ~/gw && sudo ./install-gateway.sh --show-id        # พิมพ์ Gateway ID 16 หลัก (จดไว้)
```

## 4. ลงทะเบียนที่ VPS (ทำบน VPS — INSTALL-1 ข้อ 6)
```bash
cd /opt/sos/infra && sudo bash scripts/add-gateway.sh <GATEWAY_ID>    # ได้รหัสผ่าน MQTT (ครั้งเดียว)
```
สคริปต์เพิ่มเกตเวย์ในแดชบอร์ด SOS (หน้า **เกตเวย์**) และ ChirpStack ให้เอง — หรือเพิ่มที่แดชบอร์ดเอง (ถ้าสคริปต์แจ้ง `[!!]`) · คัดลอก CA ลง Pi: `scp sos@sos.โดเมน:/opt/sos/infra/certs/ca.crt ~/gw/ca.crt` (ผ่านเครื่องคุณ)

## 5. ติดตั้ง
```bash
cd ~/gw
sudo ./install-gateway.sh        # ถามโดเมน, รหัส MQTT, ที่อยู่ ca.crt, พิกัด, อัตราขยายเสา
```
ครั้งแรกสคริปต์เปิด SPI แล้วขอให้ `sudo reboot` — หลังบูต service เริ่มเอง ตรวจด้วย `sudo sos-gateway-health`
สคริปต์จะ: ติดตั้ง concentratord + mqtt-forwarder, ตั้งค่า AS923 / WM1302, ตั้ง systemd (restart เมื่อล้ม), hardware watchdog, firewall (เปิดแค่ SSH), fail2ban, NTP, และ **ตัวตรวจสุขภาพทุก 2 นาที** (restart service ที่ตาย; วิทยุผิดปกติ 5 รอบติด → reboot อัตโนมัติ เพื่อรับมือปัญหา SPI/reset ของ WM1302 ที่ชุมชนรายงาน)

แบบไม่ถามคำถาม:
```bash
sudo DOMAIN=sos.example.org MQTT_PASSWORD=xxxx CA_FILE=./ca.crt LAT=13.91 LON=100.60 ./install-gateway.sh
```
ถ้าหา binary ไม่เจอ: เปิด https://github.com/chirpstack/chirpstack-concentratord/releases และ …/chirpstack-mqtt-forwarder/releases คัดลอกลิงก์ **linux arm64** (concentratord ต้องเป็นรุ่น `sx1302`) แล้วรันด้วย `CONCENTRATORD_URL=... MQTT_FORWARDER_URL=...`

## 6. ตรวจว่าใช้งานได้
| ตรวจ | ผลที่ต้องได้ |
|---|---|
| `sudo sos-gateway-health` | ทุกบรรทัด OK (undervoltage = 0x0) |
| `journalctl -u chirpstack-concentratord -f` | ไม่มี error SPI/"failed to start"; มี stats ทุก 60 วินาที |
| `journalctl -u chirpstack-mqtt-forwarder -f` | เชื่อมต่อ MQTT สำเร็จ ไม่มี TLS/auth error |
| ChirpStack UI → Gateways | **Last seen** อัปเดตภายในไม่กี่สิบวินาที |
| อุปกรณ์ต้นแบบ join | เห็น Join request/accept ใน Gateway → LoRaWAN frames |
| **reboot 3 ครั้งติดกัน** (`sudo reboot`) | วิทยุขึ้นเองทุกครั้งโดยไม่ต้องถอดปลั๊ก |
| ถอดสาย LAN 5 นาที แล้วต่อใหม่ | ต่อ MQTT กลับเองภายใน ~1 นาที; แดชบอร์ดมีเตือน gateway ออฟไลน์ระหว่างนั้น |
| ดึงปลั๊ก 3 ครั้ง | การ์ดไม่เสีย/บูตขึ้นปกติ (ถ้าไม่ผ่านพิจารณาการ์ดอุตสาหกรรมหรือ read-only root) |

## 7. รายงานสุขภาพเกตเวย์ → แดชบอร์ด + LINE (เพิ่มใน v0.4)
เกตเวย์ส่ง JSON เล็ก ๆ ขึ้น VPS **ทุก 1 นาที** (หัวข้อ `as923/gateway/<ID>/sos/health` ผ่าน TLS บัญชีเดียวกับ forwarder) ประกอบด้วย: ตัวรับสัญญาณ LoRa/forwarder/SPI ทำงานอยู่ไหม, อุณหภูมิ CPU, ไฟเลี้ยงต่ำ (`get_throttled`), พื้นที่การ์ด, หน่วยความจำ, เวลา NTP, IP ภายใน, เวลาทำงาน
ทำไมต้องมี: อุปกรณ์ส่งสัญญาณแค่ทุก ~30 นาที ถ้าดูแค่เฟรมจากอุปกรณ์จะแยกไม่ออกระหว่าง "เกตเวย์ดับ" กับ "ไม่มีใครกดอะไร"

ระบบถือว่าเกตเวย์ **ออฟไลน์** และส่ง LINE เมื่อ
1. เงียบเกินเวลาที่ตั้ง (ค่าเริ่มต้น **5 นาที**, แก้ที่ตั้งค่า → ออฟไลน์) หรือ
2. ยังส่งรายงานอยู่ แต่ตัวรับสัญญาณ LoRa / forwarder / SPI เสียต่อเนื่อง > 3 นาที (Pi ยังอยู่แต่ไม่มีเฟรมไหนผ่านได้)

และส่งข้อความ "กลับมาออนไลน์" เมื่อหาย · ระบบไม่ตัดสินใจเมื่อ backend เองเพิ่งเชื่อม MQTT ใหม่ (รอ 2 นาทีให้รายงานเข้า) เพื่อไม่ให้เตือนผิดตอน VPS รีสตาร์ต

**ทำที่ VPS ครั้งเดียว (ถ้ามีระบบเก่าอยู่แล้ว):** ACL ต้องอนุญาตให้ backend อ่านหัวข้อนี้
```bash
# คัดลอกโค้ดเวอร์ชันใหม่ขึ้น /opt/sos แล้ว
grep 'sos/health' /opt/sos/infra/mosquitto/acl.conf     # ต้องเห็น: topic read as923/gateway/+/sos/health
cd /opt/sos/infra && sudo docker compose up -d --build backend && sudo docker compose restart mosquitto
```
**ที่เกตเวย์:** รัน `sudo ./install-gateway.sh` ซ้ำ (ลง `mosquitto-clients`, ตั้งไฟล์รหัสที่ `/etc/sos-gateway/` สิทธิ์ 600, เปิด timer, ส่งรายงานแรกทันทีแล้วบอกผล)

ตรวจ:
```bash
sudo sos-gateway-report --print                  # ดู JSON ที่จะส่ง
sudo sos-gateway-report && echo sent             # ส่งเอง 1 ครั้ง
systemctl list-timers | grep sos-gateway         # report ทุก 1 นาที, health ทุก 2 นาที
```
แล้วเปิดแดชบอร์ด → **เกตเวย์**: ต้องเห็น 🟢 ออนไลน์ และ "รายงานล่าสุด" ไม่กี่วินาทีที่แล้ว

**ทดสอบการแจ้งเตือน (ต้องทำก่อนใช้จริง):** ตั้งค่า → "ผู้รับแจ้งเตือนระบบ" → เลือกผู้รับ → กด **ส่ง LINE ทดสอบ** → ถอดสาย LAN ของเกตเวย์ ~6 นาที ต้องได้ข้อความ "ขาดการเชื่อมต่อ" → ต่อสายคืน ต้องได้ "กลับมาออนไลน์"

| อาการ | ทางแก้ |
|---|---|
| `sos-gateway-report` แจ้ง report FAILED | ดู user/password/CA ใน `/etc/sos-gateway/mosquitto_pub` (ต้องเขียน**ทีละบรรทัดละ 1 option**); ที่ VPS `docker compose logs mosquitto` (Not authorised = รหัสผิด) |
| แดชบอร์ดไม่เห็นรายงาน แต่ส่งสำเร็จ | ACL ฝั่ง VPS ยังไม่มีบรรทัด `sos/health` หรือยังไม่ได้ restart mosquitto; ดู `docker compose logs backend` |
| ขึ้น 🟡 "ไฟเลี้ยงต่ำ" | เปลี่ยนอะแดปเตอร์/สาย (5V/3A คุณภาพดี) ก่อน — อาการนี้ทำให้วิทยุ WM1302 หลุดบ่อย |

## แก้ปัญหา
| อาการ | ทางแก้ |
|---|---|
| `/dev/spidev0.0` ไม่มี | ยังไม่ reboot หลังเปิด SPI / ตรวจ `dtparam=spi=on` ใน `/boot/firmware/config.txt` |
| concentratord ขึ้นแล้วดับ, error SPI/reset | ต่อ HAT ให้แน่น, แหล่งจ่าย 5V/3A, `vcgencmd get_throttled` ต้อง 0x0, ลองปิด-เปิดไฟทั้งชุด |
| forwarder: TLS/certificate error | CA ไม่ตรง หรือเวลาเพี้ยน (`timedatectl`), โดเมนใน config ไม่ตรงกับใบรับรอง |
| forwarder: not authorised | รหัสผ่าน/Gateway ID ไม่ตรงกับที่สร้างด้วย `add-gateway.sh` |
| เห็น gateway แต่ไม่เห็นเฟรมอุปกรณ์ | เสา/ย่านความถี่ (AS923-1), อุปกรณ์ยังไม่ provision, ระยะไกล |
| ChirpStack ไม่เห็นข้อมูลเลย แต่ forwarder ต่อได้ | `topic_prefix` ต้อง `as923` ทั้งสองฝั่ง; `json=false` ต้องตรงกับฝั่ง ChirpStack (ค่าเริ่มต้นเป็น protobuf) |

## บำรุงรักษา
- อัปเดต binary: รันสคริปต์ซ้ำ หรือใช้การ์ด “ค่าที่ทำงานบน Gateway” ในแดชบอร์ดเมื่อผู้ดูแลเซิร์ฟเวอร์ตั้ง `GATEWAY_RELEASES_JSON` และ SHA-256 ไว้แล้ว — **อัปเกรดทีละ gateway และทดสอบ** ก่อนนำร่อง
- เปลี่ยน Pi/การ์ด: Gateway ID ผูกกับ MAC ของ eth0 — ใช้ `GATEWAY_ID=<id เดิม> ./install-gateway.sh` เพื่อคง ID/บัญชี MQTT เดิม
- สำรองการ์ด (image) หลังติดตั้งเสร็จและทดสอบผ่าน เก็บไว้ 1 ชุด
