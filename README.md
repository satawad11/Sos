# SOS LoRa — ระบบแจ้งเหตุฉุกเฉินผู้ป่วย (ต้นแบบ → นำร่อง 10 บ้าน)

ปุ่มกดที่บ้านผู้ป่วย (Heltec V3, LoRaWAN AS923) → Gateway (Pi 4B + WM1302) → VPS (ChirpStack + Mosquitto + Backend + แดชบอร์ด) → LINE อสม. ตามลำดับ → เจ้าหน้าที่/อสม. กดรับ → สัญญาณกลับไปที่อุปกรณ์ผู้ป่วย
สัญญาเชื่อมระหว่างส่วนต่าง ๆ: เอกสาร "สัญญากลางระบบแจ้งเหตุฉุกเฉิน LoRaWAN (ต้นแบบ v0.2)"

## ลำดับติดตั้ง (ต่ออุปกรณ์)
| # | อุปกรณ์ | โค้ด | คู่มือ |
|---|---|---|---|
| 1 | VPS (DigitalOcean SGP) | `infra/`, `backend/`, `dashboard/` | `docs/INSTALL-1-vps.md` |
| 2 | Gateway Pi 4B + WM1302 | `gateway/pi/` (สคริปต์ติดตั้ง+config+systemd+health) | `docs/INSTALL-2-gateway.md` |
| 3 | อุปกรณ์ผู้ป่วย Heltec V3 | `firmware/` | `docs/INSTALL-3-device-firmware.md` |
| 4 | เครื่องมือ provision (คอมพิวเตอร์ของคุณ) | `tools/provision/` | `docs/INSTALL-4-provision.md` |
| 5 | แดชบอร์ด + LINE OA | `dashboard/`, `backend/app/line.py` | `docs/INSTALL-5-dashboard-line.md` |
| – | ตัวจำลองอุปกรณ์ (ทดสอบโดยไม่มีวิทยุ) | `simulator/sim.py` | ท้าย INSTALL-5 |

## สถานะการทดสอบ (ตรงไปตรงมา)
| ส่วน | สถานะ |
|---|---|
| backend (case engine, ACK_L2, escalation, after-hours, LINE webhook, auth/role/lockout, settings→CONFIG) | ✅ เทสต์อัตโนมัติ 24 ข้อ + end-to-end จริงกับ Mosquitto (กด→รับ→ได้ ACK) |
| dashboard | ✅ ทดสอบใน Chromium (login, ทุกหน้า, เคสขึ้นสีแดง) · ❗ ยังไม่ทดสอบบนมือถือ/จอเล็กและเสียงเตือนจริง |
| Mosquitto TLS + ACL ต่อ gateway | ✅ ทดสอบด้วย mosquitto 2.0.18 (gateway ปลอมหัวข้อของตัวอื่นไม่ได้) |
| gateway installer | ⚠️ ตรวจ syntax + render config + TOML parse ผ่าน · ❗ ยังไม่เคยรันบน Pi/WM1302 จริง |
| provision tool | ✅ ยูนิตเทสต์ 5 ข้อ (serial/HTTP จำลอง) · ❗ ยังไม่ได้ลองกับ ChirpStack/บอร์ดจริง |
| docker-compose / ChirpStack config | ⚠️ `docker compose config` ผ่านเท่านั้น **ยังไม่เคยรัน stack จริง** (ไม่มี Docker daemon/เครือข่ายในที่เขียน) — ข้อเสี่ยงที่รู้: การแทน `$ตัวแปร` ใน toml ของ ChirpStack (มีวิธีแก้ใน INSTALL-1) |
| **firmware** | ⚠️ **ยังไม่เคยคอมไพล์/รันบนบอร์ด** — ตรวจ API กับซอร์ส RadioLib 7.2.1 แล้ว (beginOTAA/activateOTAA/sendReceive/NONCES buffer/`LoRaWANEvent_t.confirming,fPort`/AS923) |
| LINE จริง | ❗ ยังไม่ทดสอบกับ LINE API จริง (ทดสอบด้วย webhook จำลอง + ตรวจลายเซ็น) |
| ประสิทธิภาพ/ระยะ/แบตเตอรี่ | ❗ ยังไม่วัด — ต้องวัดตอนต้นแบบ |

## ก่อนใช้กับผู้ป่วยจริง ต้องปิดให้ได้
ทดสอบบนโต๊ะครบ 14 ข้อ (INSTALL-3 ข้อ 5) · ทดสอบ failover/ไฟดับ/เน็ตหลุด · PDPA (ผู้ควบคุมข้อมูล, ความยินยอม, ข้อมูลข้ามประเทศ สิงคโปร์/LINE, DPO) · กสทช./อย. · ตกลงผู้รับสายนอกเวลา · เปลี่ยนรหัสเริ่มต้นทั้งหมด · ทดสอบกู้คืนข้อมูลสำรอง
