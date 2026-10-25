# คู่มือติดตั้งทั้งระบบ (เครื่อง Windows + เซิร์ฟเวอร์ Proxmox ของคุณเอง)

ลำดับ: **A เตรียมของ → B VM บน Proxmox → C ระบบบน VM → D ทดสอบด้วยตัวจำลอง → E เกตเวย์ → F อุปกรณ์ผู้ป่วย → G ทดสอบความทนทาน**
ทุกช่วงมี **จุดตรวจ** — ไม่ผ่านอย่าไปต่อ · ค่าตัวอย่างในคู่มือ: โดเมน `sos.dmtc.ac.th`, IP ภายในของ VM `192.168.x.50` (เปลี่ยนเป็นของคุณ) · ทุกคำสั่งบน Windows ใช้ **PowerShell**

> ⚠️ **สถานะความจริง:** ชุดนี้ผ่านเทสต์อัตโนมัติและคอมไพล์เฟิร์มแวร์ใน CI แล้ว แต่ **ยังไม่เคยรันบน Proxmox/Pi/WM1302/ChirpStack/LINE/บอร์ด Heltec จริง** คาดว่าจะเจอปัญหาเล็กน้อยบางจุด — เก็บข้อความ error ไว้ส่งให้แก้

## A. เตรียมของบนเครื่อง Windows
1. **SSH key** (ครั้งเดียว ใช้กับทั้ง VM และ Pi)
   ```powershell
   ssh -V                       # ต้องมีเวอร์ชัน (ถ้าไม่มี: Settings > Apps > Optional features > OpenSSH Client)
   ssh-keygen -t ed25519        # Enter ตามค่าเริ่มต้น
   Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub     # คัดลอกบรรทัดนี้เก็บไว้
   ```
2. **โค้ด:** https://github.com/satawad11/Sos → Code → Download ZIP → แตกที่ `C:\Users\<คุณ>\Sos-main`
3. **LINE Official Account + Messaging API** (INSTALL-5 ข้อ 2) เก็บ Channel secret + Channel access token ไว้ (ใช้ขั้น C7)
4. **IP สาธารณะ:** เลือก 1 ตัวที่ **ไม่มีบริการอื่นใช้พอร์ต 80/443** ให้ `sos.dmtc.ac.th` (จดเป็น `X.X.X.X`)
5. **DNS:** `sos.dmtc.ac.th  A  X.X.X.X` (ผู้จัดการ DNS ของ dmtc.ac.th) → ตรวจ `nslookup sos.dmtc.ac.th`
6. **ตกลงเรื่องข้อมูล:** ผู้ควบคุมข้อมูล/หนังสือยินยอม (`docs/PDPA.md`) — ไม่ต้องรอเพื่อทดสอบ แต่ต้องปิดก่อนใช้กับผู้ป่วยจริง

**จุดตรวจ A:** `ssh -V` ได้, มีคีย์, ได้โค้ด, DNS ชี้ถูก

## B. VM บน Proxmox
1. อัปโหลด ISO **Ubuntu Server 24.04 LTS** ที่ Proxmox (storage → ISO Images)
2. Create VM: **VM ไม่ใช่ LXC** (Docker บน LXC มีปัญหาสิทธิ์) · 2 vCPU · RAM 4 GB · ดิสก์ 40 GB+ · Network VirtIO · ติ๊ก **Qemu Agent** · CPU type `host`
3. ติดตั้ง Ubuntu: hostname `sos`, ผู้ใช้ `sos`, ติ๊ก **Install OpenSSH server** (วางคีย์จาก A1 ถ้ามีช่อง), ตั้ง **IP ภายในคงที่**
4. หลังบูต: `sudo apt install -y qemu-guest-agent && sudo systemctl enable --now qemu-guest-agent`
5. **เครือข่าย:** NAT/port-forward จาก `X.X.X.X` พอร์ต **80, 443, 8883 (tcp)** → VM · **ห้ามเปิด** 8006 (Proxmox) และ 22 สู่อินเทอร์เน็ต · แนะนำให้อยู่ DMZ/VLAN ที่เข้าเครือข่ายภายในไม่ได้
6. **Backup:** Proxmox → Datacenter → Backup ตั้งสำรองทั้ง VM ทุกคืนไปไว้คนละดิสก์/เครื่อง

**จุดตรวจ B:** `ssh sos@192.168.x.50` เข้าได้ (ถ้า `Permission denied (publickey)` ดู "แก้ปัญหา SSH" ท้ายเอกสาร)

## C. ระบบบน VM
```powershell
scp -r C:\Users\<คุณ>\Sos-main sos@192.168.x.50:/home/sos/sos
ssh sos@192.168.x.50
```
บน VM:
```bash
sudo apt update && sudo apt -y upgrade
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw allow 8883/tcp && sudo ufw --force enable
sudo apt -y install fail2ban unattended-upgrades docker.io docker-compose-v2 git
sudo systemctl enable --now docker fail2ban
sudo timedatectl set-timezone Asia/Bangkok
sudo mv ~/sos /opt/sos && sudo chown -R sos:sos /opt/sos
cd /opt/sos/infra && sudo bash scripts/init.sh sos.dmtc.ac.th        # จดรหัสแอดมินที่แสดงครั้งเดียว
sudo docker compose up -d --build
sudo docker compose ps
curl https://sos.dmtc.ac.th/healthz                                   # ต้องได้ {"ok":true}
```
- สำรอง `infra/.env` และ `infra/certs/ca.key` ไว้ออฟไลน์ (ถ้าหายต้องตั้งใหม่ทั้งหมด)
- ถ้าไฟล์ `.sh` ขึ้น `bad interpreter` (ปลายบรรทัด Windows): `sudo sed -i 's/\r$//' /opt/sos/infra/scripts/*.sh /opt/sos/gateway/pi/*.sh`
- ถ้า log ของ chirpstack ฟ้องรหัสผ่าน/MQTT ว่าง → ใส่ค่าจริงจาก `.env` ลงใน `chirpstack/*.toml` แล้ว `docker compose restart chirpstack` (INSTALL-1 ข้อ 4)

**C5 ตั้งค่า ChirpStack ครั้งเดียว** (SSH tunnel จาก Windows):
```powershell
ssh -L 8080:localhost:8080 -L 8090:localhost:8090 sos@192.168.x.50
```
เปิด http://localhost:8080 (admin/admin → เปลี่ยนรหัสทันที) → Device profile `SOS-button` (AS923, LoRaWAN 1.0.4, OTAA) · Application `sos-patients` · API key `provision` → ใส่ `CHIRPSTACK_APP_ID=<uuid>` ใน `infra/.env` → `sudo docker compose up -d backend`
**C6** เปิด https://sos.dmtc.ac.th → ล็อกอิน admin → ระบบบังคับตั้งรหัสใหม่
**C7** ใส่ LINE token/secret (INSTALL-5 ข้อ 3) · เพิ่ม อสม. แล้วให้ อสม. ผูก LINE

**จุดตรวจ C:** `docker compose ps` ทุกตัว running, เว็บเปิดได้, ล็อกอินได้

## D. ทดสอบซอฟต์แวร์ด้วยตัวจำลอง (ยังไม่ต้องมีฮาร์ดแวร์)
ในแดชบอร์ด: เพิ่ม อสม. → ผู้ป่วย → **บันทึกความยินยอม** (ผู้ป่วย → ข้อมูล/PDPA) → กำหนดผู้ดูแล → เพิ่มอุปกรณ์ → ตั้งเป็น active · แล้วรัน `simulator/sim.py press` (INSTALL-5 ข้อ 5) ต้องได้ LINE ถึง อสม., กดรับเรื่องแล้วตัวจำลองได้ ACK_L2
ตั้ง "ผู้รับแจ้งเตือนระบบ" (ตั้งค่า) แล้วกด "ส่ง LINE ทดสอบ"

**จุดตรวจ D:** ครบวงจร กด → LINE → รับเรื่อง → ACK

## E. เกตเวย์ (Pi 4B + WM1302) — INSTALL-2
1. ประกอบ: WM1302 บน header ให้แน่น + **ต่อเสาก่อนเปิดไฟ** · วางสูงใกล้หน้าต่าง
2. Raspberry Pi Imager → **Raspberry Pi OS Lite (64-bit)** → ⚙ hostname `sos-gw1`, ผู้ใช้/รหัสผ่าน, SSH **แบบรหัสผ่าน** (หรือวางคีย์จาก A1), timezone Asia/Bangkok → เขียนการ์ด → ต่อสาย LAN → เปิดไฟ รอ 1–2 นาที
3. `ssh <user>@sos-gw1.local` (ถ้าไม่เจอ ใช้ IP จากหน้าเราเตอร์)
4. ```powershell
   scp -r C:\Users\<คุณ>\Sos-main\gateway\pi <user>@sos-gw1.local:~/gw
   ssh <user>@sos-gw1.local
   ```
   ```bash
   sed -i 's/\r$//' ~/gw/*.sh ~/gw/bin/* 2>/dev/null
   cd ~/gw && sudo ./install-gateway.sh --show-id        # จด Gateway ID 16 หลัก
   ```
5. บน VM: `cd /opt/sos/infra && sudo bash scripts/add-gateway.sh <ID>` (จดรหัส MQTT ครั้งเดียว) · ChirpStack UI → Gateways → Add (ID เดียวกัน)
6. คัดลอก CA: `scp sos@192.168.x.50:/opt/sos/infra/certs/ca.crt .` แล้ว `scp ca.crt <user>@sos-gw1.local:~/gw/ca.crt`
7. บน Pi: `cd ~/gw && sudo ./install-gateway.sh` (ใส่ `sos.dmtc.ac.th`, รหัส MQTT, `./ca.crt`, พิกัด) → `sudo reboot` ตามที่มันบอก
8. `sudo sos-gateway-health` · `sudo sos-gateway-report && echo sent`

**จุดตรวจ E:** แดชบอร์ด → เกตเวย์ เป็น 🟢 · ChirpStack "Last seen" อัปเดต

## F. อุปกรณ์ผู้ป่วย (Heltec V3) — INSTALL-3, INSTALL-4
1. ต่อวงจร (ปุ่ม GPIO4, LED GPIO5/6, บัซเซอร์ GPIO7, ฯลฯ ตาม INSTALL-3)
2. Flash เฟิร์มแวร์: ติดตั้ง VS Code + PlatformIO แล้ว `pio run -t upload` ในโฟลเดอร์ `firmware` (CI ยืนยันว่าคอมไพล์ผ่านทั้ง 2 รุ่น)
3. ลงทะเบียนผ่านแดชบอร์ด → "ติดตั้งอุปกรณ์" (Chrome/Edge, Web Serial) → ผูกผู้ป่วย → active
4. กดปุ่มจริง: LINE ถึง → กดรับเรื่อง → ไฟเขียวที่ปุ่ม

**จุดตรวจ F:** ครบวงจรด้วยวิทยุจริง

## G. ทดสอบความทนทาน (ก่อนนำร่อง)
reboot เกตเวย์ 3 ครั้ง · ถอดสาย LAN เกตเวย์ 6 นาที (ต้องได้ LINE "ขาดการเชื่อมต่อ") · ปิดปุ่ม/ถอดไฟปุ่ม (ต้องได้ LINE "อุปกรณ์เงียบหาย" หลังเกินเวลาที่ตั้ง และ "ไฟบ้านดับ") · reboot VM · ทดสอบระยะและแบต · **ทดสอบกู้คืน backup 1 ครั้ง**

## แก้ปัญหา SSH
| อาการ | ทางแก้ |
|---|---|
| `Permission denied (publickey)` | `ssh -v ...` ดูว่าส่งคีย์ไหม · ไม่มีคีย์ที่ตรง → ใช้เขียนการ์ดใหม่แบบรหัสผ่าน หรือเพิ่ม `.pub` ใน `~/.ssh/authorized_keys` ของเครื่องปลายทาง |
| `sos-gw1.local` ไม่เจอ | ใช้ IP จากหน้าเราเตอร์ หรือ `arp -a` |
| เข้า VM ได้จากในวงเท่านั้น | ถูกต้อง — SSH ไม่เปิดสู่อินเทอร์เน็ต |
