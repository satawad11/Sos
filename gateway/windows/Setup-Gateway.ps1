<#
  SOS LoRa — ติดตั้งเกตเวย์แบบคลิกเดียว (รันบน Windows 10/11)
  ดับเบิลคลิก INSTALL-GATEWAY.bat (ไฟล์นี้ถูกเรียกจาก .bat)

  ก่อนรัน: เขียนการ์ดด้วย Raspberry Pi Imager (Pi OS Lite 64-bit, เปิด SSH) -> เสียบ LAN -> เปิด Pi
  สคริปต์จะทำต่อจนจบ: ฝากกุญแจ SSH, ส่งชุดติดตั้ง, ลงทะเบียน MQTT + ChirpStack ที่ VPS,
  ติดตั้งบน Pi (reboot ให้เองถ้าจำเป็น) และตรวจสุขภาพ
#>
param([switch]$Reset)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$OutputEncoding = New-Object System.Text.UTF8Encoding $false

$Here         = Split-Path -Parent $MyInvocation.MyCommand.Path
$GatewayDir   = (Resolve-Path (Join-Path $Here '..')).Path          # <repo>\gateway
$RepoDir      = (Resolve-Path (Join-Path $GatewayDir '..')).Path     # <repo>
$SettingsFile = Join-Path $Here 'gateway-settings.json'
$LogDir       = Join-Path $Here 'logs'
$SshOpts      = @('-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ServerAliveInterval=15')
$Total        = 9

function Step($n, $t) { Write-Host ''; Write-Host "[$n/$Total] $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  [OK] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [!!] $t" -ForegroundColor Yellow }
function Fail($t) { Write-Host ''; Write-Host "  [หยุด] $t" -ForegroundColor Red; throw 'SETUP_STOPPED' }

function Ask([string]$q, [string]$def, [string]$pattern = '.+', [string]$hint = '') {
  while ($true) {
    $p = if ($def) { "  $q [$def]" } else { "  $q" }
    $v = Read-Host $p
    if ([string]::IsNullOrWhiteSpace($v)) { $v = $def }
    if ($v) { $v = $v.Trim() }
    if ($v -and $v -match "^(?:$pattern)$") { return $v }
    Warn "รูปแบบไม่ถูกต้อง $hint"
  }
}

# Non-interactive ssh: returns @{Code; Out}. Never put double quotes (") in $Cmd — Windows PowerShell 5.1 mangles them.
function Invoke-Ssh([string]$Target, [string]$Cmd, [string]$InputText = $null) {
  $ErrorActionPreference = 'Continue'      # stderr from native tools must not throw in PS 5.1
  if ($null -ne $InputText -and $InputText -ne '') {
    $out = $InputText | & ssh @SshOpts -o BatchMode=yes $Target $Cmd 2>&1
  } else {
    $out = & ssh @SshOpts -o BatchMode=yes $Target $Cmd 2>&1
  }
  $code = $LASTEXITCODE
  return @{ Code = $code; Out = (($out | ForEach-Object { "$_" }) -join "`n").Trim() }
}

function Test-SshLogin([string]$Target) {
  $r = Invoke-Ssh $Target 'echo SSH_OK'
  return @{ Ok = ($r.Code -eq 0 -and $r.Out -match 'SSH_OK'); Out = $r.Out }
}

# Make sure we can log in with the key (asks for the password ONCE to install the key if needed).
function Ensure-SshKey([string]$Target, [string]$PubKeyPath, [string]$Label) {
  $t = Test-SshLogin $Target
  if ($t.Ok) { Ok "$Label เข้าด้วยกุญแจ SSH ได้"; return }
  if ($t.Out -match 'IDENTIFICATION HAS CHANGED') {
    Warn "$Label มีลายนิ้วมือ SSH เปลี่ยน (ปกติเมื่อเพิ่งเขียนการ์ดใหม่)"
    $a = Ask 'เพิ่งเขียนการ์ด/ติดตั้ง OS ใหม่ใช่ไหม? (y/n)' 'n' '[yYnN]'
    if ($a -notmatch '^[yY]$') { Fail "ยกเลิกเพื่อความปลอดภัย: ลายนิ้วมือของ $Target เปลี่ยนโดยไม่ทราบสาเหตุ" }
    $hostOnly = ($Target -split '@')[-1]
    & ssh-keygen -R $hostOnly | Out-Null
    $t = Test-SshLogin $Target
    if ($t.Ok) { Ok "$Label เข้าด้วยกุญแจ SSH ได้"; return }
  }
  if ($t.Out -match 'Could not resolve|timed out|No route|refused|unreachable') {
    Fail "ติดต่อ $Target ไม่ได้: $($t.Out)`n         ตรวจว่าเครื่องเปิดอยู่/อยู่ในเครือข่ายเดียวกัน หรือใส่เป็น IP แทน"
  }
  Warn "$Label ยังไม่มีกุญแจ SSH ของเครื่องนี้ -> พิมพ์รหัสผ่านของ $Target 1 ครั้ง"
  $ErrorActionPreference = 'Continue'
  Get-Content $PubKeyPath | & ssh @SshOpts $Target "umask 077; mkdir -p ~/.ssh; tr -d '\r' >> ~/.ssh/authorized_keys"
  $t = Test-SshLogin $Target
  if (-not $t.Ok) { Fail "ยังเข้า $Target ด้วยกุญแจไม่ได้: $($t.Out)" }
  Ok "$Label ฝากกุญแจ SSH แล้ว"
}

$transcript = $false
$Pi = $null
try {
  New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
  try { Start-Transcript -Path (Join-Path $LogDir ("setup-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))) | Out-Null; $transcript = $true } catch {}

  Write-Host '=====================================================' -ForegroundColor White
  Write-Host '   SOS LoRa — ติดตั้งเกตเวย์ (Pi 4B + WM1302) คลิกเดียว' -ForegroundColor White
  Write-Host '=====================================================' -ForegroundColor White

  # ---------------------------------------------------------------- 1
  Step 1 'ตรวจเครื่องมือบน Windows'
  foreach ($c in 'ssh', 'scp', 'ssh-keygen', 'tar') {
    if (-not (Get-Command $c -ErrorAction SilentlyContinue)) {
      if ($c -eq 'tar') { Fail 'ไม่พบ tar.exe (ต้องเป็น Windows 10 1803 ขึ้นไป)' }
      Fail "ไม่พบ $c — เปิด Settings > Apps > Optional features > Add 'OpenSSH Client' แล้วรันใหม่"
    }
  }
  foreach ($f in 'pi\install-gateway.sh', 'pi\config\concentratord.toml.template') {
    if (-not (Test-Path (Join-Path $GatewayDir $f))) { Fail "ไม่พบ gateway\$f — ต้องรันจากในโฟลเดอร์ repo sos" }
  }
  $AddGw = Join-Path $RepoDir 'infra\scripts\add-gateway.sh'
  if (-not (Test-Path $AddGw)) { Fail 'ไม่พบ infra\scripts\add-gateway.sh ใน repo' }
  Ok 'ssh / scp / tar พร้อม'

  # ---------------------------------------------------------------- 2
  Step 2 'ข้อมูลการติดตั้ง (กด Enter เพื่อใช้ค่าในวงเล็บ)'
  $s = @{ Vps = 'sos@sos.dmtc.ac.th'; VpsPath = '/opt/sos'; Domain = 'sos.dmtc.ac.th'; PiUser = 'pi'; PiHost = 'sos-gw1.local'; AntennaGain = '2' }
  if ((Test-Path $SettingsFile) -and -not $Reset) {
    $saved = Get-Content $SettingsFile -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($k in @($s.Keys)) { if ($saved.$k) { $s[$k] = "$($saved.$k)" } }
  }
  $Vps     = Ask 'VPS (ผู้ใช้@โฮสต์)' $s.Vps '[A-Za-z0-9._-]+@[A-Za-z0-9.-]+' '(เช่น sos@sos.dmtc.ac.th)'
  $VpsPath = Ask 'โฟลเดอร์ระบบบน VPS' $s.VpsPath '/[A-Za-z0-9._/-]+'
  $Domain  = Ask 'โดเมนของ VPS' $s.Domain '[A-Za-z0-9.-]+'
  $PiUser  = Ask 'ชื่อผู้ใช้บน Pi (ที่ตั้งใน Imager)' $s.PiUser '[a-z_][a-z0-9_-]*'
  $PiHost  = Ask 'ที่อยู่ Pi (ชื่อ.local หรือ IP)' $s.PiHost '[A-Za-z0-9.-]+'
  $GwName  = Ask 'ชื่อเกตเวย์ (ภาษาอังกฤษ ไม่เว้นวรรค)' 'gw-rpst-1' '[A-Za-z0-9._-]{1,60}'
  $Lat     = Ask 'ละติจูด (เช่น 13.9126)' '0' '-?[0-9]{1,3}(\.[0-9]+)?'
  $Lon     = Ask 'ลองจิจูด (เช่น 100.6068)' '0' '-?[0-9]{1,3}(\.[0-9]+)?'
  $Gain    = Ask 'อัตราขยายเสา dBi (เสาในชุด ~2)' $s.AntennaGain '[0-9]{1,2}(\.[0-9]+)?'
  $Pi = "$PiUser@$PiHost"
  if ($Lat -eq '0' -or $Lon -eq '0') { Warn 'ยังไม่ใส่พิกัด — แก้ทีหลังได้ใน ChirpStack (Gateways > แก้ไข)' }
  @{ Vps = $Vps; VpsPath = $VpsPath; Domain = $Domain; PiUser = $PiUser; PiHost = $PiHost; AntennaGain = $Gain } |
    ConvertTo-Json | Set-Content -Path $SettingsFile -Encoding UTF8
  Ok 'บันทึกค่าไว้ใช้ครั้งหน้า (ไม่มีรหัสผ่านในไฟล์)'

  # ---------------------------------------------------------------- 3
  Step 3 'กุญแจ SSH'
  $Key = Join-Path $env:USERPROFILE '.ssh\id_ed25519'
  if (-not (Test-Path $Key)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $Key) | Out-Null
    & ssh-keygen -t ed25519 -q -N '""' -f $Key
    if (-not (Test-Path "$Key.pub")) { Fail 'สร้างกุญแจ SSH ไม่สำเร็จ' }
    Ok 'สร้างกุญแจ SSH ใหม่แล้ว'
  }
  Ensure-SshKey $Pi  "$Key.pub" 'Pi'
  Ensure-SshKey $Vps "$Key.pub" 'VPS'

  # ---------------------------------------------------------------- 4
  Step 4 'ตรวจ Pi'
  $r = Invoke-Ssh $Pi 'uname -m; cat /sys/class/net/eth0/address 2>/dev/null || echo NO_ETH0; sudo -n true 2>/dev/null && echo SUDO_OK || echo SUDO_NO'
  if ($r.Out -notmatch 'aarch64') { Fail "Pi ไม่ใช่ OS 64-bit (ได้: $($r.Out -split "`n" | Select-Object -First 1)) — เขียนการ์ดใหม่เป็น Raspberry Pi OS Lite (64-bit)" }
  if ($r.Out -match 'NO_ETH0') { Fail 'ไม่พบการ์ดแลน eth0 — ต้องเสียบสาย LAN (Gateway ID สร้างจาก MAC ของ eth0)' }
  if ($r.Out -notmatch 'SUDO_OK') { Fail "ผู้ใช้ $PiUser ใช้ sudo แบบไม่ถามรหัสไม่ได้ — ใช้ผู้ใช้แรกที่สร้างจาก Imager" }
  Ok 'Pi OS 64-bit, มี eth0, sudo ใช้ได้'
  $r = Invoke-Ssh $Vps "test -d $VpsPath/infra && test -f $VpsPath/infra/certs/ca.crt && echo VPS_OK"
  if ($r.Out -notmatch 'VPS_OK') { Fail "VPS ไม่มี $VpsPath/infra หรือ certs/ca.crt — ทำ INSTALL-1 (ติดตั้ง VPS) ให้เสร็จก่อน" }
  Ok 'VPS มีระบบและใบรับรองพร้อม'

  # ---------------------------------------------------------------- 5
  Step 5 'ส่งชุดติดตั้งไปที่ Pi และอ่าน Gateway ID'
  $tgz = Join-Path $env:TEMP 'sos-gw-pi.tgz'
  if (Test-Path $tgz) { Remove-Item $tgz -Force }
  & tar.exe -czf $tgz -C $GatewayDir pi
  if ($LASTEXITCODE -ne 0) { Fail 'บีบอัดไฟล์ไม่สำเร็จ' }
  & scp @SshOpts -o BatchMode=yes -q $tgz "${Pi}:sos-gw-pi.tgz"
  if ($LASTEXITCODE -ne 0) { Fail 'ส่งไฟล์ไป Pi ไม่สำเร็จ' }
  Remove-Item $tgz -Force
  $r = Invoke-Ssh $Pi 'rm -rf ~/gw && mkdir -p ~/gw && tar -xzf ~/sos-gw-pi.tgz -C ~/gw --strip-components=1 && rm -f ~/sos-gw-pi.tgz && cd ~/gw && find . -type f -exec sed -i ''s/\r$//'' {} + && chmod +x install-gateway.sh bin/* && echo COPY_OK'
  if ($r.Out -notmatch 'COPY_OK') { Fail "แตกไฟล์บน Pi ไม่สำเร็จ: $($r.Out)" }
  $r = Invoke-Ssh $Pi 'cd ~/gw && sudo ./install-gateway.sh --show-id'
  $m = [regex]::Match($r.Out, '\b[0-9a-f]{16}\b')
  if (-not $m.Success) { Fail "อ่าน Gateway ID ไม่ได้: $($r.Out)" }
  $GwId = $m.Value
  Ok "Gateway ID = $GwId"

  # ---------------------------------------------------------------- 6
  Step 6 'ลงทะเบียนที่ VPS (บัญชี MQTT + ChirpStack)'
  & scp @SshOpts -o BatchMode=yes -q $AddGw "${Vps}:sos-add-gateway.sh"
  if ($LASTEXITCODE -ne 0) { Fail 'ส่ง add-gateway.sh ไป VPS ไม่สำเร็จ' }
  Write-Host '  (ถ้าถาม [sudo] password ให้ใส่รหัสผ่านของผู้ใช้บน VPS)' -ForegroundColor DarkGray
  $Cred = "~/.sos-gw-$GwId.cred"
  $vcmd = "tr -d '\r' < ~/sos-add-gateway.sh > ~/sos-add-gateway.lf && sudo install -m 755 ~/sos-add-gateway.lf $VpsPath/infra/scripts/add-gateway.sh && rm -f ~/sos-add-gateway.sh ~/sos-add-gateway.lf && sudo bash $VpsPath/infra/scripts/add-gateway.sh $GwId --name $GwName --lat $Lat --lon $Lon --cred-file $Cred"
  & ssh @SshOpts -t $Vps $vcmd
  if ($LASTEXITCODE -ne 0) { Fail 'ลงทะเบียนที่ VPS ไม่สำเร็จ (ดูข้อความด้านบน)' }
  $r = Invoke-Ssh $Vps "cat $Cred; rm -f $Cred"
  $pw = [regex]::Match($r.Out, 'MQTT_PASSWORD=([A-Za-z0-9._~-]+)')
  if (-not $pw.Success) { Fail 'ไม่ได้รับรหัสผ่าน MQTT จาก VPS' }
  $MqttPw = $pw.Groups[1].Value
  $CsOk = $r.Out -match 'CHIRPSTACK_REGISTERED=1'
  $ak = [regex]::Match($r.Out, 'AGENT_KEY=([0-9a-f]{32,64})')
  $AgentKey = if ($ak.Success) { $ak.Groups[1].Value } else { '' }
  $r = Invoke-Ssh $Vps "cat $VpsPath/infra/certs/ca.crt"
  if ($r.Out -notmatch 'BEGIN CERTIFICATE') { Fail 'อ่าน ca.crt จาก VPS ไม่ได้' }
  $Ca = $r.Out
  Ok 'ได้บัญชี MQTT และใบรับรอง CA แล้ว'
  if ($CsOk) { Ok 'เพิ่มเกตเวย์ใน ChirpStack แล้ว' } else { Warn "ต้องเพิ่มเกตเวย์ใน ChirpStack เอง: Gateways > Add gateway > ID $GwId" }
  if ($AgentKey) { Ok 'ได้คีย์เอเจนต์ (สั่งงานเกตเวย์จากแดชบอร์ดได้)' } else { Warn 'ไม่ได้คีย์เอเจนต์ — การสั่งงานระยะไกลจะยังปิดอยู่ (ดู docs/GATEWAY-REMOTE.md)' }

  # ---------------------------------------------------------------- 7
  Step 7 'ส่งค่าตั้งไปที่ Pi'
  $envText = "DOMAIN=$Domain`nMQTT_PASSWORD=$MqttPw`nCA_FILE=./ca.crt`nLAT=$Lat`nLON=$Lon`nANTENNA_GAIN=$Gain`nREBOOT_EXIT_CODE=10`n"
  if ($AgentKey) { $envText += "AGENT_KEY=$AgentKey`n" }
  $r = Invoke-Ssh $Pi "umask 077; tr -d '\r' > ~/gw/.install.env && echo ENV_OK" $envText
  if ($r.Out -notmatch 'ENV_OK') { Fail "เขียนค่าตั้งบน Pi ไม่สำเร็จ: $($r.Out)" }
  $r = Invoke-Ssh $Pi "tr -d '\r' > ~/gw/ca.crt && echo CA_OK" $Ca
  if ($r.Out -notmatch 'CA_OK') { Fail 'ส่ง ca.crt ไป Pi ไม่สำเร็จ' }
  $MqttPw = $null; $AgentKey = $null; $envText = $null
  Ok 'ส่งแล้ว (ไฟล์รหัสบน Pi สิทธิ์ 600 จะถูกลบหลังติดตั้ง)'

  # ---------------------------------------------------------------- 8
  Step 8 'ติดตั้งบน Pi (5-15 นาที ขึ้นกับความเร็วเน็ต)'
  $installCmd = 'cd ~/gw && sudo bash -c ''set -a; . ./.install.env; set +a; ./install-gateway.sh'''
  $ErrorActionPreference = 'Continue'
  & ssh @SshOpts -o BatchMode=yes $Pi $installCmd
  $code = $LASTEXITCODE
  if ($code -eq 10) {
    Warn 'เปิด SPI แล้ว ต้อง reboot — กำลังรีบูต Pi ให้อัตโนมัติ'
    Invoke-Ssh $Pi 'sudo systemctl reboot' | Out-Null
    Start-Sleep -Seconds 25
    $up = $false
    for ($i = 0; $i -lt 36; $i++) {
      if ((Test-SshLogin $Pi).Ok) { $up = $true; break }
      Write-Host '  ...รอ Pi บูต' -ForegroundColor DarkGray
      Start-Sleep -Seconds 5
    }
    if (-not $up) { Fail 'Pi ไม่กลับมาภายใน 3 นาที — ตรวจไฟ/สาย LAN แล้วดับเบิลคลิก INSTALL-GATEWAY.bat ใหม่' }
    Ok 'Pi บูตกลับมาแล้ว ติดตั้งต่อ'
    & ssh @SshOpts -o BatchMode=yes $Pi $installCmd
    $code = $LASTEXITCODE
  }
  $ErrorActionPreference = 'Stop'
  Invoke-Ssh $Pi 'rm -f ~/gw/.install.env' | Out-Null
  if ($code -ne 0) { Fail "ติดตั้งบน Pi ไม่สำเร็จ (exit $code) — ดูข้อความด้านบน หรือบน Pi: journalctl -u chirpstack-concentratord -n 50" }
  Ok 'ติดตั้งเสร็จ'

  # ---------------------------------------------------------------- 9
  Step 9 'ตรวจสุขภาพเกตเวย์'
  Write-Host '  รอให้วิทยุและ MQTT เชื่อมต่อ 30 วินาที...' -ForegroundColor DarkGray
  Start-Sleep -Seconds 30
  $r = Invoke-Ssh $Pi 'sudo sos-gateway-health'
  $r.Out -split "`n" | ForEach-Object {
    if ($_ -match 'FAIL') { Write-Host "    $_" -ForegroundColor Red } else { Write-Host "    $_" }
  }
  $fails = ([regex]::Matches($r.Out, 'FAIL')).Count

  Write-Host ''
  Write-Host '=====================================================' -ForegroundColor White
  if ($fails -eq 0) {
    Write-Host "  สำเร็จ! เกตเวย์ $GwName ($GwId) ทำงานแล้ว" -ForegroundColor Green
  } else {
    Write-Host "  ติดตั้งแล้ว แต่ตรวจไม่ผ่าน $fails รายการ (ดูสีแดงด้านบน)" -ForegroundColor Yellow
    Write-Host '  ถ้าเป็น "recent concentrator log" ให้รอ 1-2 นาทีแล้วรันบน Pi: sudo sos-gateway-health'
  }
  Write-Host "  ต่อไป: เปิด https://$Domain -> เมนู เกตเวย์ ต้องเห็นสถานะออนไลน์"
  Write-Host '  แนะนำ: ทดสอบ reboot 3 ครั้ง และถอดสาย LAN 6 นาทีเพื่อทดสอบ LINE แจ้งเตือน'
  Write-Host '=====================================================' -ForegroundColor White
}
catch {
  if ($_.Exception.Message -ne 'SETUP_STOPPED') {
    Write-Host ''
    Write-Host "  [ผิดพลาดไม่คาดคิด] $($_.Exception.Message)" -ForegroundColor Red
  }
  if ($Pi) { try { Invoke-Ssh $Pi 'rm -f ~/gw/.install.env' | Out-Null } catch {} }
  Write-Host '  แก้ตามข้อความแล้วดับเบิลคลิก INSTALL-GATEWAY.bat ใหม่ได้เลย (รันซ้ำได้ปลอดภัย)' -ForegroundColor Yellow
  if ($transcript) { Write-Host "  log: $LogDir" -ForegroundColor DarkGray }
  if ($transcript) { try { Stop-Transcript | Out-Null } catch {} }
  exit 1
}
if ($transcript) { try { Stop-Transcript | Out-Null } catch {} }
exit 0
