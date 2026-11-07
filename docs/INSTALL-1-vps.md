# ติดตั้งที่ 1: VPS (DigitalOcean สิงคโปร์) — ChirpStack + Mosquitto + Backend + Dashboard

**ทำก่อนอุปกรณ์อื่นทั้งหมด** · เวลาประมาณ 1–2 ชั่วโมง · ต้องมี: บัญชี DigitalOcean, โดเมน (เช่น `sos.โดเมนของคุณ`), เครื่องคอมพิวเตอร์ที่ใช้ SSH ได้

## 1. สร้างเซิร์ฟเวอร์
1. DigitalOcean → Create Droplet → Region **Singapore (SGP1)** → Ubuntu 24.04 LTS → แผน **2 GB RAM / 1 vCPU ขึ้นไป** (ChirpStack+Postgres+Redis+Backend ต้องการ ~1.5 GB) → เปิด **Backups** (แนะนำ) → ใส่ **SSH key** (ไม่ใช้รหัสผ่าน)
2. ที่ DNS ของโดเมน: สร้าง record `A`  `sos.โดเมนของคุณ → IP ของ Droplet`
3. ตรวจ: `ping sos.โดเมนของคุณ` ต้องได้ IP ของ Droplet

## 2. เตรียมเครื่อง
```bash
ssh root@sos.โดเมนของคุณ
apt update && apt -y upgrade
adduser sos && usermod -aG sudo sos          # สร้างผู้ใช้ประจำ (ตั้งรหัสผ่านยาว)
rsync -a ~/.ssh /home/sos/ && chown -R sos:sos /home/sos/.ssh
# ปิด login ด้วย root/รหัสผ่าน
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/; s/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh
# firewall: SSH, HTTPS(+80 สำหรับขอใบรับรอง), MQTT TLS สำหรับ gateway
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 8883/tcp && ufw --force enable
apt -y install fail2ban unattended-upgrades docker.io docker-compose-v2 git
systemctl enable --now docker fail2ban
```
เปิดหน้าต่างใหม่ทดสอบ `ssh sos@sos.โดเมนของคุณ` ให้เข้าได้ **ก่อนปิดหน้าต่างเดิม**

> หมายเหตุ: Docker เผยแพร่พอร์ตผ่าน iptables ซึ่งอาจข้าม ufw — ในไฟล์ compose นี้พอร์ตที่เปิดออกภายนอกมีแค่ 80/443/8883 ส่วน ChirpStack UI/REST ผูกกับ `127.0.0.1` เท่านั้น

## 3. นำโค้ดขึ้นเซิร์ฟเวอร์ และตั้งค่าเริ่มต้น
```bash
# จากเครื่องคุณ (โฟลเดอร์ที่มี sos/) 
rsync -av --exclude '.git' sos/ sos@sos.โดเมนของคุณ:/opt/sos/      # หรือ git clone จาก repo ของคุณ
ssh sos@sos.โดเมนของคุณ
cd /opt/sos/infra
sudo bash scripts/init.sh sos.โดเมนของคุณ
```
สคริปต์จะ: สร้าง `.env` พร้อมรหัสลับสุ่มทั้งหมด, สร้าง CA + ใบรับรองสำหรับ Mosquitto (`certs/`), สร้างบัญชี MQTT ของ chirpstack/backend แล้ว**แสดงรหัสผ่านแอดมิน dashboard ครั้งเดียว — จดไว้**
**สำรองไฟล์ `infra/.env` และ `infra/certs/ca.key` ไว้ที่ออฟไลน์** (ถ้าหายต้องตั้งใหม่ทั้งหมด)

## 4. เริ่มระบบ
```bash
cd /opt/sos/infra
sudo docker compose up -d --build
sudo docker compose ps          # ทุกตัวต้องเป็น running (backend อาจรีสตาร์ท 1-2 ครั้งระหว่างรอ Postgres)
sudo docker compose logs chirpstack | tail -20
```
**ตรวจ:** `curl https://sos.โดเมนของคุณ/healthz` ต้องได้ `{"ok":true}` (Caddy ขอใบรับรอง Let's Encrypt อัตโนมัติ — ใช้เวลาครึ่งนาที)
ถ้า log ของ chirpstack ฟ้อง password/MQTT ว่าว่าง: การแทนค่า `$ตัวแปร` ใน toml ไม่ทำงานกับเวอร์ชันที่ดึงมา → ให้แทนค่าจริงลงใน `chirpstack/chirpstack.toml` และ `chirpstack/region_as923.toml` โดยตรง (ค่าอยู่ใน `.env`) แล้ว `docker compose restart chirpstack`

## 5. ตั้งค่า ChirpStack (ครั้งเดียว) — ผ่าน SSH tunnel
> อยากเปิดผ่านโดเมน (เช่น `https://lns.dmtc.ac.th`) และจัดการจากแดชบอร์ด → ดู [CHIRPSTACK-WEB.md](CHIRPSTACK-WEB.md)

```bash
# จากเครื่องคุณ
ssh -L 8080:localhost:8080 -L 8090:localhost:8090 sos@sos.โดเมนของคุณ
# เปิดเบราว์เซอร์ http://localhost:8080   (ผู้ใช้เริ่มต้น admin / admin  → **เปลี่ยนรหัสผ่านทันที**)
```
1. **Device profiles → Add**: ชื่อ `SOS-button` · Region **AS923** · MAC version **LoRaWAN 1.0.4** · Regional parameters revision **RP002-1.0.x ตัวล่าสุดที่มี** · ADR algorithm ปกติ · **Supports OTAA = on** · Class-B/C = off · Uplink interval (expected) = 1800 วินาที → Save
   จด **Device profile ID** (uuid ใน URL)
2. **Applications → Add**: ชื่อ `sos-patients` → Save · จด **Application ID** (uuid ใน URL)
3. **API keys (ของ Tenant) → Add API key** ชื่อ `provision` → จด token (แสดงครั้งเดียว)
4. ใส่ Application ID ลงไฟล์ `.env` แล้วรีสตาร์ท backend:
```bash
sudo nano /opt/sos/infra/.env           # CHIRPSTACK_APP_ID=<uuid>
sudo docker compose up -d backend
```

## 6. ลงทะเบียน gateway
บน gateway (ดู INSTALL-2) จะได้ **Gateway ID** 16 หลัก (ตัวพิมพ์เล็ก) จากนั้นบน VPS:
```bash
cd /opt/sos/infra && sudo bash scripts/add-gateway.sh 0016c001ff10abcd
```
จดรหัสผ่านที่แสดง (ครั้งเดียว) · แล้วที่ ChirpStack UI → **Gateways → Add gateway** ใส่ Gateway ID เดียวกัน

## 7. สำรองข้อมูล + เฝ้าระวัง
```bash
sudo crontab -e     # เพิ่มบรรทัด:
0 2 * * * /opt/sos/infra/scripts/backup.sh >> /var/log/sos-backup.log 2>&1
```
ตั้ง uptime monitor ภายนอก (เช่น UptimeRobot ฟรี) ยิงมาที่ `https://sos.โดเมนของคุณ/healthz` แจ้งเตือนเข้า LINE/อีเมลของคุณเมื่อล่ม
**ทดสอบกู้คืนอย่างน้อยหนึ่งครั้ง** (`pg_restore` ลงเครื่องอื่น) ก่อนใช้งานจริง — ข้อมูลสำรองมีข้อมูลผู้ป่วย ต้องเก็บตามที่ผู้ควบคุมข้อมูล (PDPA) อนุมัติ

## แก้ปัญหา
| อาการ | ตรวจ |
|---|---|
| เว็บเปิดไม่ได้/ใบรับรองไม่ออก | DNS ชี้ถูกไหม, พอร์ต 80/443 เปิดไหม, `docker compose logs caddy` |
| backend รีสตาร์ทวน | `docker compose logs backend` — มักเป็นรหัสผ่านฐานข้อมูล (ฐานข้อมูลสร้างตอนเริ่มครั้งแรกเท่านั้น ถ้าแก้ `.env` ทีหลังต้อง `docker compose down -v` แล้วเริ่มใหม่ — **ลบข้อมูลทั้งหมด**) |
| gateway ต่อ MQTT ไม่ได้ | ดู INSTALL-2 ขั้น 5; `docker compose logs mosquitto` มี "not authorised" ไหม |
