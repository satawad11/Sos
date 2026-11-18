// Patient SOS device firmware v0.7 — Heltec WiFi LoRa 32 V3, RadioLib LoRaWAN OTAA, AS923.
// NOT hardware-tested yet: follow firmware/README.md bench tests before any patient use.
#include <Arduino.h>
#include <Preferences.h>
#include <RadioLib.h>
#include <U8g2lib.h>
#include <Wire.h>
#include <esp_task_wdt.h>
#include <esp_system.h>
#include "config.h"
#if SOS_GPS
#include "gps_nmea.h"
#endif

// ---------------------------------------------------------------- radio
#ifdef SOS_BOARD_V2
SX1276 radio = new Module(LORA_NSS, LORA_DIO0, LORA_RST, LORA_DIO1);
#else
SX1262 radio = new Module(LORA_NSS, LORA_DIO1, LORA_RST, LORA_BUSY);
#endif
LoRaWANNode node(&radio, &AS923);           // Thailand = AS923-1 (923.2/923.4 MHz); ChirpStack region "as923"

// ---------------------------------------------------------------- storage
Preferences prefs;
uint64_t g_devEui = 0, g_joinEui = 0;
uint8_t  g_appKey[16];
bool     g_provisioned = false;
Config   g_cfg;
volatile int16_t g_joinError = 0;
uint8_t  g_alertId = 0;

static bool hexToBytes(const String& s, uint8_t* out, size_t n) {
  if (s.length() != n * 2) return false;
  for (size_t i = 0; i < n; i++) {
    char b[3] = {s[2 * i], s[2 * i + 1], 0}; char* e;
    long v = strtol(b, &e, 16); if (*e) return false; out[i] = (uint8_t)v;
  }
  return true;
}
static uint64_t be64(const uint8_t* b) { uint64_t v = 0; for (int i = 0; i < 8; i++) v = (v << 8) | b[i]; return v; }

void loadStorage() {
  prefs.begin("sos", false);
  uint8_t eui[8], jeui[8];
  if (prefs.getBytesLength("deveui") == 8 && prefs.getBytesLength("appkey") == 16) {
    prefs.getBytes("deveui", eui, 8); prefs.getBytes("appkey", g_appKey, 16);
    if (prefs.getBytesLength("joineui") == 8) prefs.getBytes("joineui", jeui, 8); else memset(jeui, 0, 8);
    g_devEui = be64(eui); g_joinEui = be64(jeui); g_provisioned = true;
  }
  g_cfg.ver = prefs.getUChar("cfg_ver", 1);
  g_cfg.heartbeat_min = prefs.getUShort("cfg_hb", 30);
  g_cfg.poll_s = prefs.getUChar("cfg_poll", 10);
  g_cfg.buzzer = prefs.getUChar("cfg_bz", 2);
  g_cfg.hold_ms = prefs.getUShort("cfg_hold", 1500);
  g_alertId = prefs.getUChar("alert_id", 0);
}
void saveConfig() {
  prefs.putUChar("cfg_ver", g_cfg.ver); prefs.putUShort("cfg_hb", g_cfg.heartbeat_min);
  prefs.putUChar("cfg_poll", g_cfg.poll_s); prefs.putUChar("cfg_bz", g_cfg.buzzer);
  prefs.putUShort("cfg_hold", g_cfg.hold_ms);
}
uint8_t nextAlertId() { g_alertId = (g_alertId % 255) + 1; prefs.putUChar("alert_id", g_alertId); return g_alertId; }

// ---------------------------------------------------------------- shared UI state (written by main task, read by UI task)
enum UiMode : uint8_t {
  UI_BOOT, UI_NOT_PROVISIONED, UI_JOINING, UI_JOIN_FAIL, UI_READY,
  UI_SENDING,        // button accepted, sending, no L1 yet
  UI_SENT_WAIT,      // L1 ok, waiting for human
  UI_FAIL,           // L1 timeout (60 s) — fast blink + buzzer
  UI_ACCEPTED, UI_CLOSED, UI_FALSE_ALARM, UI_AFTER_HOURS,
  UI_CANCELLED, UI_TEST_OK, UI_TEST_FAIL,
  UI_MSG             // preset message from staff (FPort 12)
};
volatile UiMode g_ui = UI_BOOT;
volatile uint8_t g_uiBattery = 255;
volatile bool    g_uiMains = true;
volatile uint8_t g_msgCode = 0, g_msgVal = 0;   // message on screen (UI_MSG)
volatile bool    g_accepted = false;            // staff accepted the current alert (green LED under a message)
volatile bool    g_cancelArmed = false;         // main loop: a fresh long press now would cancel (UI shows the countdown)

// Preset messages — codes MUST match backend/app/codec.py MESSAGES. Max 16 characters per line; %u = value.
struct MsgText { const char* l1; const char* l2; };
const MsgText kMsgs[] = {
  {"MESSAGE", ""},                        // 0 unused
  {"HELP COMING", "ETA %u MIN"},          // 1 volunteer/staff on the way
  {"AMBULANCE", "ETA %u MIN"},            // 2
  {"STAFF WILL CALL", "ANSWER PHONE"},    // 3
  {"STAY STILL", "DO NOT GET UP"},        // 4
  {"WE GOT IT", "NO NEED TO PRESS"},      // 5
};
const uint8_t kMsgCount = sizeof(kMsgs) / sizeof(kMsgs[0]);
volatile uint32_t g_uiSince = 0;
volatile bool    g_buttonDown = false;
volatile uint32_t g_buttonDownAt = 0;
volatile uint32_t g_buttonHeld = 0;      // ms held (valid on release event)
volatile bool    g_pressEvent = false;   // set when hold reached hold_ms (still down)
volatile bool    g_releaseEvent = false; // set on release
volatile uint8_t g_shortPresses = 0; volatile uint32_t g_lastShortAt = 0;

void setUi(UiMode m) { g_ui = m; g_uiSince = millis(); }

// ---------------------------------------------------------------- hardware helpers
U8G2_SSD1306_128X64_NONAME_F_HW_I2C oled(U8G2_R0, OLED_RST, OLED_SCL, OLED_SDA);

uint8_t readBatteryPct(bool* charging = nullptr) {
  if (PIN_ADC_CTRL >= 0) { pinMode(PIN_ADC_CTRL, OUTPUT); digitalWrite(PIN_ADC_CTRL, ADC_CTRL_ACTIVE_LEVEL); }
  delay(5);
  uint32_t mv = 0; for (int i = 0; i < 8; i++) { mv += analogReadMilliVolts(PIN_VBAT_ADC); delay(2); }
  if (PIN_ADC_CTRL >= 0) digitalWrite(PIN_ADC_CTRL, !ADC_CTRL_ACTIVE_LEVEL);
  float v = (mv / 8.0f) * VBAT_DIVIDER / 1000.0f;
  if (charging) *charging = v > 4.25f;
  if (v < 2.0f) return 255;                // no battery fitted / reading invalid
  float pct = (v - 3.3f) / (4.15f - 3.3f) * 100.0f;
  return (uint8_t)constrain((int)pct, 0, 100);
}
bool mainsOk() {
  static bool stable=digitalRead(PIN_MAINS)==HIGH, candidate=stable;
  static uint32_t since=0;
  bool raw=digitalRead(PIN_MAINS)==HIGH;
  if(raw!=candidate){candidate=raw;since=millis();}
  if(millis()-since>=3000)stable=candidate;
  return stable;
}

void buzz(bool on) { digitalWrite(PIN_BUZZER, on && g_cfg.buzzer > 0 ? HIGH : LOW); }

// pattern = on/off ms pairs repeating; returns whether output should be on at time t
bool pat(uint32_t t, uint16_t on, uint16_t off) { return (t % (on + off)) < on; }

void uiTask(void*) {
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_LED_RED, OUTPUT); pinMode(PIN_LED_GREEN, OUTPUT); pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_MAINS, INPUT);
  pinMode(PIN_VEXT, OUTPUT); digitalWrite(PIN_VEXT, LOW);
  delay(50);
  oled.begin(); oled.setFont(u8g2_font_8x13B_tr);
  uint32_t lastDraw = 0; bool prevDown = false; bool holdFired = false;
  for (;;) {
    uint32_t now = millis();
    // ---- button with debounce (20 ms) ----
    static uint32_t chg = 0; static bool stable = false;
    bool raw = digitalRead(PIN_BUTTON) == LOW;
    if (raw != stable && now - chg > 20) { stable = raw; chg = now; }
    if (stable && !prevDown) { g_buttonDownAt = now; holdFired = false; g_buttonDown = true; }
    if (stable && !holdFired && now - g_buttonDownAt >= g_cfg.hold_ms) { holdFired = true; g_pressEvent = true; }
    if (!stable && prevDown) {
      g_buttonHeld = now - g_buttonDownAt; g_buttonDown = false; g_releaseEvent = true;
      if (g_buttonHeld < 600) {   // short press counting for TEST (3 short presses)
        if (now - g_lastShortAt > 1500) g_shortPresses = 0;
        g_shortPresses++; g_lastShortAt = now;
      }
    }
    prevDown = stable;
    // ---- LEDs / buzzer by mode ----
    uint32_t t = now - g_uiSince; bool red = false, green = false, bz = false;
    switch (g_ui) {
      case UI_BOOT: red = green = (t / 200) % 2; break;
      case UI_NOT_PROVISIONED: red = pat(now, 100, 900); green = pat(now + 500, 100, 900); break;
      case UI_JOINING: green = pat(now, 300, 700); break;
      case UI_JOIN_FAIL: green = pat(now, 100, 1900); break;
      case UI_READY: green = pat(now, 60, 4940); break;                    // heartbeat flash every 5 s
      case UI_SENDING: red = pat(now, 150, 150); bz = pat(now, 80, 920); break;
      case UI_SENT_WAIT: red = true; bz = (t < 600) && pat(t, 100, 100); break;
      case UI_FAIL: red = pat(now, 100, 100); bz = pat(now, 200, 800); break;
      case UI_ACCEPTED: green = true; bz = (t < 700) && pat(t, 200, 200); break;      // 2 beeps
      case UI_CLOSED: green = pat(now, 500, 500); bz = (t < 900) && pat(t, 300, 300); break;      // 2 beeps
      case UI_FALSE_ALARM: green = pat(now, 150, 150); bz = (t < 300); break;
      case UI_AFTER_HOURS: red = true; green = pat(now, 200, 800); bz = (t < 3000) && pat(t, 700, 300); break;
      case UI_CANCELLED: green = true; red = true; bz = (t < 300); break;
      case UI_TEST_OK: green = pat(now, 100, 100); bz = (t < 400) && pat(t, 100, 100); break;
      case UI_TEST_FAIL: red = pat(now, 100, 100); bz = (t < 1000) && pat(t, 100, 100); break;
      case UI_MSG: green = g_accepted; red = !g_accepted; bz = (t < 900) && pat(t, 150, 150); break;   // 3 beeps
    }
    // Battery indication never overrides an emergency or staff message. Two short beeps at 10%, three at 5%.
    if(g_ui==UI_READY && !g_uiMains && g_uiBattery!=255 && g_uiBattery<=10){
      const bool critical=g_uiBattery<=5;
      red=pat(now,100,critical?1900:4900);
      const uint32_t cycle=critical?300000UL:900000UL;
      uint32_t bt=now%cycle;
      bz=bt<(critical?600UL:400UL) && (bt%200)<80;
    }
    // holding to cancel: alternate red/green, tick every second, countdown on the OLED (drawn below)
    bool cancelHold = g_cancelArmed && g_buttonDown && now - g_buttonDownAt > 300;
    if (cancelHold) { uint32_t h = now - g_buttonDownAt; red = (h / 250) % 2; green = !red; bz = (h % 1000) < 60; }
    // brief confirmation beep while holding button (feedback that hold counted)
    if (g_buttonDown && holdFired && t > 0) bz = bz || (now - g_buttonDownAt < g_cfg.hold_ms + 200);
    digitalWrite(PIN_LED_RED, red); digitalWrite(PIN_LED_GREEN, green);
    if (g_cfg.buzzer == 1) bz = bz && pat(now, 20, 20);
    buzz(bz);
    // ---- OLED (English / digits only; Thai goes via LINE & the staff) ----
    if (now - lastDraw > 250) {
      lastDraw = now;
      const char* l1 = ""; const char* l2 = ""; static char mb1[20], mb2[20];
      switch (g_ui) {
        case UI_BOOT: l1 = "SOS DEVICE"; l2 = "starting"; break;
        case UI_NOT_PROVISIONED: l1 = "NOT SETUP"; l2 = "run provision"; break;
        case UI_JOINING: l1 = "CONNECTING"; l2 = "please wait"; break;
        case UI_JOIN_FAIL: l1 = "NO NETWORK"; snprintf(mb2,sizeof mb2,"retry err %d",(int)g_joinError);l2=mb2;break;
        case UI_READY: l1 = "READY"; l2 = "hold red button"; break;
        case UI_SENDING: l1 = "SENDING..."; l2 = "keep calm"; break;
        case UI_SENT_WAIT: l1 = "NETWORK RECEIVED"; l2 = "waiting staff"; break;
        case UI_FAIL: l1 = "NOT SENT!"; l2 = "CALL 1669"; break;
        case UI_ACCEPTED: l1 = "STAFF ACCEPTED"; l2 = "help is coming"; break;
        case UI_CLOSED: l1 = "CASE CLOSED"; l2 = ""; break;
        case UI_FALSE_ALARM: l1 = "FALSE ALARM"; l2 = "noted"; break;
        case UI_AFTER_HOURS: l1 = "AFTER HOURS"; l2 = "CALL 1669 NOW"; break;
        case UI_CANCELLED: l1 = "CANCELLED"; l2 = ""; break;
        case UI_TEST_OK: l1 = "TEST OK"; l2 = "link works"; break;
        case UI_TEST_FAIL: l1 = "TEST FAILED"; l2 = "no network"; break;
        case UI_MSG: {
          uint8_t c = g_msgCode;
          if (c > 0 && c < kMsgCount) { snprintf(mb1, sizeof mb1, kMsgs[c].l1, g_msgVal); snprintf(mb2, sizeof mb2, kMsgs[c].l2, g_msgVal); }
          else { snprintf(mb1, sizeof mb1, "MESSAGE %u", c); snprintf(mb2, sizeof mb2, "update firmware"); }
          l1 = mb1; l2 = mb2; break;
        }
      }
      static char cb[20];
      if (cancelHold) {
        uint32_t h = now - g_buttonDownAt, left = h >= CANCEL_HOLD_MS ? 0 : (CANCEL_HOLD_MS - h + 999) / 1000;
        snprintf(cb, sizeof cb, "CANCEL IN %lu s", (unsigned long)left); l1 = "KEEP HOLDING"; l2 = cb;
      }
      oled.clearBuffer();
      oled.drawStr(0, 16, l1); oled.drawStr(0, 34, l2);
      char b[24]; uint8_t bp = g_uiBattery;
      if (bp == 255) snprintf(b, sizeof b, "%s", g_uiMains ? "AC" : "NO AC");
      else snprintf(b, sizeof b, "%s BAT %u%%", g_uiMains ? "AC" : "NO AC", bp);
      oled.setFont(u8g2_font_6x10_tr); oled.drawStr(0, 62, b); oled.setFont(u8g2_font_8x13B_tr);
      oled.sendBuffer();
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

// ---------------------------------------------------------------- LoRaWAN
bool g_joined = false;
bool g_radioReady = false;

uint8_t flagsNow(bool test = false) {
  bool chg = false; uint8_t pct = readBatteryPct(&chg);
  uint8_t f = 0;
  if (mainsOk()) f |= F_MAINS_OK;
  if (pct != 255 && pct < BATT_LOW_PCT) f |= F_BATT_LOW;
  if (chg) f |= F_CHARGING;
  if (test) f |= F_TEST_MODE;
  g_uiBattery = pct; g_uiMains = mainsOk();
  return f;
}

// returns: <0 radio error, 0 sent/no downlink, >0 downlink window. Fills down[] / downLen / confirming.
uint8_t g_downPort = 0;   // fPort of the last received downlink (from LoRaWANEvent_t)
int16_t sendFrame(MsgType t, uint8_t alertId, uint8_t port, bool confirmed, uint8_t pollCount,
                  uint8_t* down, size_t* downLen, bool* confirming, bool test = false, uint8_t extraFlags = 0) {
  uint8_t pct = readBatteryPct();
  uint8_t p[8] = {(uint8_t)((PROTO_VER << 4) | t), alertId, (uint8_t)(flagsNow(test) | extraFlags), pct,
                  FW_MAJOR, FW_MINOR, g_cfg.ver, pollCount};
  node.setDeviceStatus(pct == 255 ? 255 : (uint8_t)map(pct, 0, 100, 1, 254));
  LoRaWANEvent_t up, dn; memset(&dn, 0, sizeof dn);
  *downLen = 0;
  memset(&up, 0, sizeof up);
  int16_t s = node.sendReceive(p, sizeof p, port, down, downLen, confirmed, &up, &dn);
  *confirming = (s > 0) && dn.confirming;
  Serial.printf("[tx] type=%u port=%u state=%d dr=%u freq=%.1f pwr=%d fcnt=%lu down=%u/%u ack=%d\n", t, port, s, up.datarate,
                up.freq, up.power, (unsigned long)up.fCnt, (s > 0) ? dn.fPort : 0, (unsigned)*downLen, *confirming);
  g_downPort = (s > 0) ? dn.fPort : 0;
  return s;
}

uint8_t g_msgSeen = 0;    // seq of the last MSG shown for the current alert (echoed to the server as a receipt)
void applyDownlink(const uint8_t* d, size_t n, uint8_t port, uint8_t alertId, int* ackResult) {
  if (port == FPORT_MSG && n == 5 && d[0] == ((PROTO_VER << 4) | T_MSG) && d[1] == alertId && alertId != 0) {
    if (d[2] != g_msgSeen) {            // a re-sent copy of the same message is not shown (or beeped) twice
      g_msgSeen = d[2]; g_msgCode = d[3]; g_msgVal = d[4]; setUi(UI_MSG);
      Serial.printf("[msg] seq=%u code=%u value=%u\n", d[2], d[3], d[4]);
    }
  }
  else if (port == FPORT_ACK_L2 && n == 3 && d[0] == ((PROTO_VER << 4) | T_ACK_L2) && d[1] == alertId && ackResult)
    *ackResult = d[2];
  else if (port == FPORT_CONFIG && n == 6 && d[0] == ((PROTO_VER << 4) | T_CONFIG)) {
    if (d[2] == 0 || d[3] < 5 || d[3] > 120 || d[4] > 3 || d[5] < 5 || d[5] > 50) {
      Serial.println("[config] rejected invalid values"); return;
    }
    g_cfg.ver = d[1]; g_cfg.heartbeat_min = max<uint16_t>(5, d[2] * 5); g_cfg.poll_s = constrain(d[3], 5, 120);
    g_cfg.buzzer = min<uint8_t>(d[4], 3); g_cfg.hold_ms = constrain(d[5] * 100, 500, 5000);
    saveConfig();
    Serial.printf("[config] applied ver=%u hb=%u poll=%u buzzer=%u hold=%u\n", g_cfg.ver, g_cfg.heartbeat_min, g_cfg.poll_s, g_cfg.buzzer, g_cfg.hold_ms);
  }
}

// No ADR: the FCtrl ADR bit tells ChirpStack not to change our data rate / power. Re-applied after every join and
// before alert retries, so a LinkADRReq that slipped through can never leave the button deaf again.
void applyLinkPolicy() {
  node.setADR(false);
  int16_t a = node.setDatarate(UPLINK_DR), b = node.setTxPower(UPLINK_POWER_DBM);
  if (a || b) Serial.printf("[link] setDatarate=%d setTxPower=%d\n", a, b);
}

bool joinNetwork() {
  setUi(UI_JOINING);
  // ORDER MATTERS: beginOTAA() resets the nonce state, and setBufferNonces() only accepts a buffer whose key checksum
  // matches the keys given to beginOTAA(). Restoring first (fw <= 0.2) silently discarded the saved counter on every
  // boot -> DevNonce restarted at 1 -> ChirpStack ignored the already-used DevNonces -> "NO NETWORK" after a reboot.
  int16_t init = node.beginOTAA(g_joinEui, g_devEui, g_appKey, g_appKey);
  if(init != RADIOLIB_ERR_NONE){g_joinError=init;Serial.printf("[join] beginOTAA failed %d\n",init);return false;}
  uint8_t nonces[RADIOLIB_LORAWAN_NONCES_BUF_SIZE];
  if (prefs.getBytesLength("nonces") == RADIOLIB_LORAWAN_NONCES_BUF_SIZE) {
    prefs.getBytes("nonces", nonces, sizeof nonces);
    int16_t r = node.setBufferNonces(nonces);                // keeps the DevNonce counter across reboots
    if (r != RADIOLIB_ERR_NONE) Serial.printf("[join] saved nonces not used (%d)\n", r);
  }
  int16_t s = node.activateOTAA(UPLINK_DR);
  g_joinError = s;
  if (s == RADIOLIB_LORAWAN_NEW_SESSION || s == RADIOLIB_LORAWAN_SESSION_RESTORED) {
    prefs.putBytes("nonces", node.getBufferNonces(), RADIOLIB_LORAWAN_NONCES_BUF_SIZE);
    applyLinkPolicy();
    g_joined = true; return true;
  }
  Serial.printf("[join] failed %d\n", s);
  prefs.putBytes("nonces", node.getBufferNonces(), RADIOLIB_LORAWAN_NONCES_BUF_SIZE);   // DevNonce was consumed
  return false;
}

// ---------------------------------------------------------------- serial provisioning
//   PING | INFO | PROV <deveui16> <appkey32> [joineui16] | WIPE
void serialCommands() {
  static String line;
  while (Serial.available()) {
    char c = Serial.read();
    if (c != '\n' && c != '\r') { if (line.length() < 200) line += c; continue; }
    line.trim(); String cmd = line; line = ""; if (!cmd.length()) continue;
    if (cmd == "PING") Serial.println("PONG");
    else if (cmd == "DIAG") {
      Serial.printf("DIAG reset=%d heap=%u battery=%u mains=%d joined=%d cfg=%u hb=%u poll=%u pending=%u\n",
        (int)esp_reset_reason(),(unsigned)ESP.getFreeHeap(),readBatteryPct(),mainsOk(),g_joined,g_cfg.ver,
        g_cfg.heartbeat_min,g_cfg.poll_s,prefs.getUChar("pending",0));
      digitalWrite(PIN_LED_RED,HIGH);digitalWrite(PIN_LED_GREEN,HIGH);buzz(true);delay(150);buzz(false);
    }
    else if (cmd == "INFO") {
      Serial.printf("INFO fw=%d.%d provisioned=%d deveui=%016llx cfg_ver=%u\n", FW_MAJOR, FW_MINOR,
                    g_provisioned, (unsigned long long)g_devEui, g_cfg.ver);
    } else if (cmd.startsWith("PROV ")) {
      String a[3]; int n = 0, from = 5;
      while (n < 3) { int sp = cmd.indexOf(' ', from); if (sp < 0) { a[n++] = cmd.substring(from); break; } a[n++] = cmd.substring(from, sp); from = sp + 1; }
      uint8_t eui[8], key[16], jeui[8] = {0};
      if (n < 2 || !hexToBytes(a[0], eui, 8) || !hexToBytes(a[1], key, 16) || (n == 3 && !hexToBytes(a[2], jeui, 8))) {
        Serial.println("ERR bad-args"); continue;
      }
      prefs.putBytes("deveui", eui, 8); prefs.putBytes("appkey", key, 16); prefs.putBytes("joineui", jeui, 8);
      prefs.remove("nonces");
      Serial.printf("OK deveui=%s\n", a[0].c_str()); delay(200); ESP.restart();
    } else if (cmd == "WIPE") {
      prefs.clear(); Serial.println("OK wiped"); delay(200); ESP.restart();
    } else Serial.println("ERR unknown");
  }
}


// ---------------------------------------------------------------- optional GPS (build with -D SOS_GPS=1, see docs/GPS-kit.md)
#if SOS_GPS
NmeaParser g_nmea; GpsFix g_fix; uint32_t g_fixAt = 0; bool g_locSent = false; uint8_t g_locTries = 0;
void gpsPoll() {                                  // drain the UART; keep only the newest valid fix
  while (Serial1.available()) {
    if (g_nmea.feed((char)Serial1.read())) { g_nmea.take(g_fix); g_fixAt = millis(); }
  }
}
bool gpsFresh() { return g_fix.valid && g_fix.sats >= GPS_MIN_SATS && millis() - g_fixAt <= GPS_MAX_AGE_MS; }
// One unconfirmed LOC uplink per alert (FPort 4) — sent only AFTER the L1 ACK so it can never delay the alert itself.
void sendLocIfAny(uint8_t alertId) {
  if (g_locSent || g_locTries >= GPS_LOC_MAX_TRIES) return;
  g_locTries++;
  if (!gpsFresh()) return;
  uint8_t p[9]; encodeLoc(alertId, g_fix.lat, g_fix.lon, g_fix.sats, g_fix.hdop, p);
  LoRaWANEvent_t up, dn; uint8_t down[8]; size_t dl = 0;
  int16_t s = node.sendReceive(p, sizeof p, FPORT_LOC, down, &dl, false, &up, &dn);
  Serial.printf("[gps] LOC sent state=%d sats=%u hdop=%.1f\n", s, g_fix.sats, g_fix.hdop);
  if (s >= 0) g_locSent = true;
}
#endif

// ---------------------------------------------------------------- main logic
enum Phase { P_IDLE, P_ALERT } phase = P_IDLE;
uint8_t  curAlert = 0;
bool     l1Ok = false;
uint32_t alertStart = 0, nextTry = 0, nextPoll = 0, doneAt = 0;
uint8_t  tries = 0, pollCount = 0;
uint8_t  ackShown = 0;          // ACK_L2 result the device shows for curAlert (reported back so the server stops re-sending)
uint32_t acceptedAt = 0;
bool     g_repress = false;     // patient pressed again after acceptance -> resend ALERT (server logs "pressed again")
bool     g_releasedSinceAlert = false;   // button let go at least once since this alert began (cancel needs a fresh press)
uint32_t nextHeartbeat = 0;

const char* radioErr(int16_t s) {
  switch (s) {
    case -1101: return "not joined"; case -1104: return "no channel"; case -1108: return "duty cycle wait";
    case -1114: return "dwell time (data rate too slow)"; case -4: return "packet too long"; case -5: return "TX timeout";
    case -2: return "radio chip not found"; case -16: return "SPI error"; default: return "see RadioLib TypeDef.h";
  }
}

void endAlert(UiMode m) { phase = P_IDLE; if(m==UI_CLOSED || m==UI_FALSE_ALARM || m==UI_CANCELLED) prefs.remove("pending"); setUi(m); doneAt = millis(); }

void doAlertStep() {
  uint32_t now = millis();
  uint8_t down[32]; size_t dl = 0; bool conf = false; int ack = 0;
  if (!l1Ok) {
    if (now < nextTry) return;
    if (tries >= 2) applyLinkPolicy();             // no L1 yet: make sure we are on the robust DR / full power
    int16_t s = sendFrame(T_ALERT, curAlert, FPORT_EVENT, true, 0, down, &dl, &conf);
    Serial.printf("[alert] try=%u state=%d L1=%d\n", tries, s, conf);
    tries++;
    if (conf) {                                   // L1: server acknowledged the confirmed uplink
      l1Ok = true; nextPoll = millis() + 3000; setUi(UI_SENT_WAIT);
      prefs.putBool("delivered", true);
#if SOS_GPS
      sendLocIfAny(curAlert);
#endif
    } else {
      nextTry = millis() + min<uint32_t>(3000UL << min<uint8_t>(tries, 4), 30000UL);   // backoff 6,12,24,30 s…
      if (s < 0) Serial.printf("[alert] radio refused to send: %d (%s)\n", s, radioErr(s));
      if (millis() - alertStart > L1_TIMEOUT_MS && g_ui != UI_FAIL) setUi(UI_FAIL);
      if (millis() - alertStart > POLL_MAX_MS) {     // 30 min of trying: stop, keep NOT SENT on screen, accept new presses
        Serial.println("[alert] giving up after 30 min"); endAlert(UI_FAIL);
      }
    }
    return;
  }
  if (g_repress) {                                 // pressed again while help is on the way: tell the server, stay accepted
    g_repress = false;
    int16_t s = sendFrame(T_ALERT, curAlert, FPORT_EVENT, true, 0, down, &dl, &conf);
    Serial.printf("[alert] pressed again, L1=%d\n", conf);
    if (s > 0 && dl > 0) applyDownlink(down, dl, g_downPort, curAlert, &ack);   // a queued MSG may ride on this one
    return;
  }
  // stop polling 30 min after the alert (or after acceptance); the screen keeps the last state for DONE_DISPLAY_MS
  if (now - (g_accepted ? acceptedAt : alertStart) > POLL_MAX_MS) { endAlert(g_accepted ? g_ui : UI_READY); return; }
  if (now < nextPoll) return;
  pollCount++;
#if SOS_GPS
  sendLocIfAny(curAlert);                          // retry on the early polls if there was no fix at first
#endif
  // byte 7 = last shown MSG seq (receipt) instead of the poll counter; flags carry the ACK result already shown
  int16_t s = sendFrame(T_POLL, curAlert, FPORT_POLL, false, g_msgSeen, down, &dl, &conf, false,
                        (uint8_t)((ackShown << F_ACK_SHIFT) | F_MSG_SEQ));
  uint16_t every = g_accepted ? max<uint16_t>(g_cfg.poll_s, ACCEPTED_POLL_MIN_S) : g_cfg.poll_s;
  nextPoll = millis() + (uint32_t)every * 1000UL;
  if (s > 0 && dl > 0) {
    applyDownlink(down, dl, g_downPort, curAlert, &ack);
  }
  switch (ack) {
    case 1:                                        // accepted: keep polling (slower) so staff messages can arrive
      if (!g_accepted) { g_accepted = true; prefs.putBool("accepted", true); acceptedAt = millis(); ackShown = 1; if (g_ui != UI_MSG) setUi(UI_ACCEPTED); }
      break;
    case 2: ackShown = 2; endAlert(UI_CLOSED); break;
    case 3: ackShown = 3; endAlert(UI_FALSE_ALARM); break;
    case 4: ackShown = 4; if (g_ui != UI_AFTER_HOURS && g_ui != UI_MSG) setUi(UI_AFTER_HOURS); break;   // keep polling: a volunteer may still accept
    default: break;
  }
}

void startAlert() {
  curAlert = nextAlertId(); phase = P_ALERT; l1Ok = false; tries = 0; pollCount = 0;
  ackShown = 0; g_msgSeen = 0; g_accepted = false; g_repress = false; g_releasedSinceAlert = false;
  alertStart = millis(); nextTry = 0; setUi(UI_SENDING);
  prefs.putBool("delivered", false); prefs.putBool("accepted", false); prefs.putUChar("pending", curAlert);
#if SOS_GPS
  g_locSent = false; g_locTries = 0;
#endif
}

void doCancel() {
  setUi(UI_CANCELLED);                              // show it at once; the radio call below takes a few seconds
  uint8_t down[32]; size_t dl = 0; bool conf = false;
  int16_t s = sendFrame(T_CANCEL, curAlert, FPORT_EVENT, true, 0, down, &dl, &conf);
  Serial.printf("[cancel] sent state=%d L1=%d\n", s, conf);
  endAlert(UI_CANCELLED);
}

void doTest() {
  setUi(UI_SENDING);
  uint8_t down[8]; size_t dl; bool conf = false;
  uint8_t id = nextAlertId();
  for (int i = 0; i < 3 && !conf; i++) sendFrame(T_TEST, id, FPORT_EVENT, true, 0, down, &dl, &conf, true);
  endAlert(conf ? UI_TEST_OK : UI_TEST_FAIL);
}

void doHeartbeat() {
  uint8_t down[32]; size_t dl = 0; bool conf; int ack = 0;
  int16_t s = sendFrame(T_HEARTBEAT, 0, FPORT_HEARTBEAT, false, 0, down, &dl, &conf);
  if (s > 0 && dl > 0) applyDownlink(down, dl, g_downPort, 0, &ack);
  static bool diagnosticSent=false;
  if(!diagnosticSent && s>=0){
    uint8_t diag[5]={1,(uint8_t)esp_reset_reason(),g_cfg.ver,(uint8_t)min<uint32_t>(255,ESP.getFreeHeap()/1024),readBatteryPct()};
    LoRaWANEvent_t up,dn;size_t n=0;uint8_t rx[32];
    int16_t result=node.sendReceive(diag,sizeof diag,5,rx,&n,false,&up,&dn);
    diagnosticSent=result>=0;
    if(result>0 && n)applyDownlink(rx,n,dn.fPort,0,&ack);
  }
  uint16_t interval=g_cfg.heartbeat_min;
  if(g_uiBattery!=255 && !g_uiMains)interval=min<uint16_t>(interval,g_uiBattery<=10?2:5);
  nextHeartbeat = millis() + (uint32_t)interval * 60000UL;
}

void setup() {
  Serial.begin(115200);
  Serial.printf("[boot] reset_reason=%d\n", (int)esp_reset_reason());
  // Radio join/receive calls can block; allow two minutes before recovering a stuck main task.
  esp_task_wdt_init(120, true);
  esp_task_wdt_add(nullptr);
  setUi(UI_BOOT);
  xTaskCreatePinnedToCore(uiTask, "ui", 6144, nullptr, 2, nullptr, 0);
  loadStorage();
#if SOS_GPS
  Serial1.begin(GPS_BAUD, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);
#endif
  delay(800);
  if (!g_provisioned) { setUi(UI_NOT_PROVISIONED); return; }
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_NSS);
  // frequency/SF are overridden by the LoRaWAN band; Heltec V3 SX1262 has a 1.8 V TCXO on DIO3
#ifdef SOS_BOARD_V2
  int16_t s = radio.begin(923.2, 125.0, 9, 7, RADIOLIB_SX127X_SYNC_WORD, 10, 8, 0);   // SX1276 (PA_BOOST on V2)
#else
  int16_t s = radio.begin(923.2, 125.0, 9, 7, RADIOLIB_SX126X_SYNC_WORD_PRIVATE, 10, 8, 1.8);
#endif
  if (s != RADIOLIB_ERR_NONE) { g_joinError=s;Serial.printf("[radio] begin failed %d\n", s); setUi(UI_JOIN_FAIL); return; }
  g_radioReady=true;
#ifndef SOS_BOARD_V2
  radio.setDio2AsRfSwitch(true);                   // V3 switches the RF path via DIO2
#endif
  Serial.printf("INFO fw=%d.%d deveui=%016llx\n", FW_MAJOR, FW_MINOR, (unsigned long long)g_devEui);
}

void loop() {
  esp_task_wdt_reset();
  serialCommands();
#if SOS_GPS
  gpsPoll();
#endif
  if (!g_provisioned) { delay(50); return; }
  if (!g_radioReady) { delay(50); return; }

  static uint32_t nextJoin = 0, joinDelay = (uint32_t)JOIN_RETRY_MIN_MS;
  if (!g_joined) {
    if (millis() >= nextJoin) {
      if (joinNetwork()) {
        setUi(UI_READY); joinDelay = JOIN_RETRY_MIN_MS; nextHeartbeat = millis() + 2000;
        uint8_t pending=prefs.getUChar("pending",0);
        if(pending){curAlert=pending;phase=P_ALERT;l1Ok=prefs.getBool("delivered",false);g_accepted=prefs.getBool("accepted",false);
          ackShown=g_accepted?1:0;alertStart=acceptedAt=millis();nextTry=nextPoll=millis()+2000;
          setUi(g_accepted?UI_ACCEPTED:l1Ok?UI_SENT_WAIT:UI_SENDING);Serial.printf("[recover] alert=%u accepted=%d\n",curAlert,g_accepted);}
      }
      else { setUi(UI_JOIN_FAIL); nextJoin = millis() + joinDelay; joinDelay = min<uint32_t>(joinDelay * 2, JOIN_RETRY_MAX_MS); }
    }
    // Even with no network, a button hold still shows feedback so the patient knows to call 1669
    if (g_pressEvent) { g_pressEvent = false; setUi(UI_FAIL); }
    delay(20); return;
  }

  // ---- idle-state UI refresh after a result has been shown for a while ----
  if (phase == P_IDLE && g_ui != UI_READY && g_ui != UI_SENDING && millis() - doneAt > DONE_DISPLAY_MS) setUi(UI_READY);
  if (phase == P_IDLE && (g_ui == UI_TEST_OK || g_ui == UI_TEST_FAIL || g_ui == UI_CANCELLED || g_ui == UI_CLOSED ||
                          g_ui == UI_FALSE_ALARM) && millis() - g_uiSince > RESULT_SHOW_MS) setUi(UI_READY);   // ready for the next emergency

  // ---- button logic ----
  if (g_pressEvent) {
    g_pressEvent = false;
    Serial.printf("[btn] hold (phase=%d ui=%d)\n", phase, g_ui);
    if (phase == P_IDLE) startAlert();                 // hold reached hold_ms
    else if (phase == P_ALERT && g_accepted) g_repress = true;
  }
  // Cancel needs a NEW press: the button must have been released after the alert started (fw <= 0.3 measured from the
  // alert's own press, so simply holding on cancelled the emergency). Only while nobody has accepted, in the first minute.
  if (phase == P_ALERT && g_releaseEvent) g_releasedSinceAlert = true;
  // - not delivered yet (SENDING / NOT SENT): cancel any time = stop retrying (+ a CANCEL in case one try got through)
  // - delivered (red steady / AFTER HOURS / message): cancel during the first minute, until somebody accepts
  bool cancelOk = phase == P_ALERT && !g_accepted && g_releasedSinceAlert &&
                  (!l1Ok || (millis() - alertStart < CANCEL_WINDOW_MS &&
                             (g_ui == UI_SENT_WAIT || g_ui == UI_AFTER_HOURS || g_ui == UI_MSG)));
  g_cancelArmed = cancelOk;
  if (cancelOk && g_buttonDown && millis() - g_buttonDownAt >= CANCEL_HOLD_MS) {
    g_cancelArmed = false; Serial.printf("[cancel] by patient (delivered=%d)\n", l1Ok); doCancel();
  }
  if (phase == P_IDLE && g_shortPresses >= 3 && millis() - g_lastShortAt < 1500) { g_shortPresses = 0; doTest(); }
  g_releaseEvent = false;

  if (phase == P_ALERT) { doAlertStep(); delay(10); return; }

  // Sample locally every 15 s. Event-driven reports are rate-limited to one per minute.
  static uint32_t sampled=0,lastPowerReport=0;
  static uint8_t lastBattery=255;static bool lastMains=true;
  if(millis()-sampled>=15000UL){
    sampled=millis();uint8_t battery=readBatteryPct();bool mains=mainsOk();
    g_uiBattery=battery;g_uiMains=mains;
    bool crossed=battery!=255 && lastBattery!=255 && ((lastBattery>10 && battery<=10)||(lastBattery>5 && battery<=5));
    bool changed=battery!=255 && lastBattery!=255 && abs((int)battery-(int)lastBattery)>=3;
    if((crossed||changed||mains!=lastMains) && millis()-lastPowerReport>=60000UL){
      doHeartbeat();lastPowerReport=millis();lastBattery=battery;lastMains=mains;
    }else if(lastBattery==255){lastBattery=battery;lastMains=mains;}
  }
  if (millis() >= nextHeartbeat) doHeartbeat();
  delay(20);
}
