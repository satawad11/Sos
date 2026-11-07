# อัปเดตระบบจากหน้าเว็บ (ปุ่ม "อัปเดตระบบ")

เมื่อมีการแก้โค้ดและ push ขึ้น GitHub (สาขา `main`) แล้ว แอดมินกดอัปเดตได้จากแดชบอร์ด ไม่ต้อง SSH เข้าเซิร์ฟเวอร์

## ทำงานอย่างไร
```
แดชบอร์ด (admin กดปุ่ม) → backend เขียน infra/update/request.json
        → systemd บนเซิร์ฟเวอร์ (sos-update.path) เห็นไฟล์ → รัน infra/scripts/sos-update.sh
        → git fetch → สำรองฐานข้อมูล → git merge → docker compose up -d --build → ตรวจ /healthz
        → ไม่ผ่าน: ย้อนกลับ commit เดิม + build ใหม่ให้อัตโนมัติ
        → เขียนความคืบหน้าลง infra/update/status.json + update.log → แดชบอร์ดแสดงผล
```
- backend **ไม่มีสิทธิ์** สั่ง docker/git เอง (ไม่ได้ต่อ Docker socket เข้าคอนเทนเนอร์) ทำได้แค่ "ขอ" ผ่านไฟล์ ส่วนงานจริงทำโดย systemd บนเครื่อง
- ระบบ**ตรวจหา**เวอร์ชันใหม่เองทุก 6 ชั่วโมง (แสดงในหน้าอัปเดต) แต่**ไม่อัปเดตเอง** ต้องมีแอดมินกดเสมอ
- ก่อนอัปเดตทุกครั้งจะรัน `scripts/backup.sh` (เก็บที่ `/var/backups/sos`) ถ้าสำรองไม่สำเร็จจะไม่อัปเดต
- ถ้าไฟล์ในเซิร์ฟเวอร์ถูกแก้เอง (ไม่ตรงกับ GitHub) จะไม่อัปเดตทับ และแจ้งชื่อไฟล์
- ถ้าไฟล์ตั้งค่า mosquitto / chirpstack / caddy เปลี่ยน จะรีสตาร์ทบริการนั้นให้
- มีค่าใหม่ใน `.env.example` ที่ยังไม่มีใน `.env` → แจ้งในข้อความผลลัพธ์ (ต้องเพิ่มเองใน `.env`)
- ระหว่างอัปเดต (≈1–5 นาที) หน้าเว็บและการส่ง LINE หยุดชั่วคราว → ทำนอกช่วงที่มีเคสค้าง

## ติดตั้งครั้งเดียวบนเซิร์ฟเวอร์ (CT100)
ต้องการ: โค้ดล่าสุดที่มีไฟล์นี้อยู่บน GitHub แล้ว · สิทธิ์ admin ของ repo บน GitHub (เพิ่ม Deploy key)

1. นำสคริปต์ขึ้นเครื่อง (จาก Windows):
   ```powershell
   scp C:\Users\<คุณ>\sos\infra\scripts\setup-updater.sh sos@<IP>:/tmp/
   ```
2. รันบนเซิร์ฟเวอร์:
   ```bash
   sudo bash /tmp/setup-updater.sh            # ค่าเริ่มต้น: git@github.com:satawad11/Sos.git  main  /opt/sos
   ```
3. สคริปต์จะแสดง **Deploy key** (บรรทัดขึ้นต้น `ssh-ed25519`) → GitHub → repo `Sos` → **Settings → Deploy keys → Add deploy key**
   Title `sos-server` · วางคีย์ · **ห้ามติ๊ก Allow write access** → Add key → กลับมากด Enter
4. ถ้า `/opt/sos` มาจาก ZIP สคริปต์จะแปลงเป็น git checkout ให้ ถ้ามีไฟล์ที่ต่างจาก GitHub จะ**สำรองไว้ก่อน** (`/var/backups/sos/local-files-*.tar.gz`) แล้วถามก่อนแทนที่
   (ไฟล์ลับ `.env`, `certs/`, `mosquitto/passwd` ไม่อยู่ใน git — ไม่ถูกแตะ)
5. จบแล้ว build ระบบใหม่หนึ่งครั้ง → แดชบอร์ดมีเมนู **อัปเดตระบบ** (เฉพาะ admin)

ตรวจ:
```bash
systemctl status sos-update.path sos-update-check.timer     # active
cat /opt/sos/infra/update/status.json
```

## ใช้งาน
แดชบอร์ด → **อัปเดตระบบ** → ดูรายการ "มีอะไรใหม่" → **อัปเดตตอนนี้** → ยืนยัน → รอ (หน้าเว็บจะขึ้น "กำลังรีสตาร์ท") → **โหลดหน้าใหม่**
ทุกครั้งบันทึกใน "บันทึกการใช้งาน" (`system_update_requested`) พร้อมชื่อผู้กด

สั่งจาก SSH ก็ได้: `sudo /opt/sos/infra/scripts/sos-update.sh check` หรือ `... update`

## แก้ปัญหา
| อาการ | ทางแก้ |
|---|---|
| หน้าอัปเดตขึ้น "ยังไม่ได้ติดตั้งตัวอัปเดต" | ยังไม่ได้รัน `setup-updater.sh` หรือ backend ยังไม่ได้ build ใหม่ (`docker compose up -d --build backend`) |
| "เขียนคำขอไม่ได้" | `sudo chown 10001:10001 /opt/sos/infra/update` |
| "ติดต่อ GitHub ไม่ได้" | อินเทอร์เน็ต/DNS ของเครื่อง หรือ Deploy key ถูกลบ: `sudo git -C /opt/sos fetch` ดู error |
| "มีไฟล์ในเซิร์ฟเวอร์ถูกแก้เอง" | ดู `sudo git -C /opt/sos status` · ถ้าต้องการใช้ของ GitHub: `sudo git -C /opt/sos checkout -- <ไฟล์>` · ถ้าต้องการเก็บ: นำการแก้ไปแก้ใน GitHub แทน |
| "อัปเดตและย้อนกลับไม่สำเร็จ" | ระบบอาจหยุด: `cd /opt/sos/infra && sudo docker compose ps` / `logs backend` · กู้ข้อมูลจาก `/var/backups/sos` ได้ (`pg_restore`) · Proxmox backup ของ CT ก็ใช้ได้ |
| ค้างที่ "กำลังอัปเดต" นาน | `journalctl -u sos-update.service -n 50` และ `tail -50 /opt/sos/infra/update/update.log` |
