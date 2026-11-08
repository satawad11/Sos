#pragma once
#ifdef SOS_BOARD_V2
// ===== Heltec WiFi LoRa 32 V2 (ESP32 + SX1276/SX1278, SSD1306 OLED) — EXPERIMENTAL port, verify against your board's pin map =====
#define LORA_NSS   18
#define LORA_SCK   5
#define LORA_MOSI  27
#define LORA_MISO  19
#define LORA_RST   14
#define LORA_DIO0  26
#define LORA_DIO1  35
#define PIN_VEXT   21   // LOW = Vext (OLED power) ON
#define OLED_SDA   4
#define OLED_SCL   15
#define OLED_RST   16
// External parts: GPIOs that are free on the V2 header (CHECK them against your board before wiring)
#define PIN_BUTTON    13   // button -> GND (INPUT_PULLUP)
#define PIN_LED_RED   2
#define PIN_LED_GREEN 17
#define PIN_BUZZER    22   // active buzzer via NPN
#define PIN_MAINS     36   // input-only pin: 5V sense via divider 10k/20k, HIGH = mains present
#define PIN_VBAT_ADC  37   // on-board battery divider
#define PIN_ADC_CTRL  -1   // V2: no switch pin assumed (set if your revision has one)
#define ADC_CTRL_ACTIVE_LEVEL LOW
#define VBAT_DIVIDER  2.0f // CALIBRATE with a multimeter (V2 ADC is noisy/nonlinear)
#else
// ===== Heltec WiFi LoRa 32 V3 pins (verified against Heltec pin map) =====
#define LORA_NSS   8
#define LORA_SCK   9
#define LORA_MOSI  10
#define LORA_MISO  11
#define LORA_RST   12
#define LORA_BUSY  13
#define LORA_DIO1  14
#define PIN_VEXT   36   // LOW = Vext (OLED power) ON
#define OLED_SDA   17
#define OLED_SCL   18
#define OLED_RST   21

// ===== External parts (free GPIOs on header; change to suit your enclosure) =====
#define PIN_BUTTON    4    // big red button: GPIO4 -> button -> GND (INPUT_PULLUP)
#define PIN_LED_RED   5    // via 330R to LED -> GND
#define PIN_LED_GREEN 6
#define PIN_BUZZER    7    // ACTIVE buzzer via NPN transistor (GPIO -> 1k -> base)
#define PIN_MAINS     3    // 5V USB supply sense via divider (10k/20k): HIGH = mains present
#define PIN_VBAT_ADC  1    // Heltec V3 on-board battery divider
#define PIN_ADC_CTRL  37   // Heltec V3: enables battery divider. CHECK your board revision (see README)
#define ADC_CTRL_ACTIVE_LEVEL LOW
#define VBAT_DIVIDER  4.9f // calibrate with a multimeter
#endif

// ===== Protocol (must match docs/contract) =====
#define PROTO_VER 1
enum MsgType : uint8_t { T_ALERT = 1, T_CANCEL, T_TEST, T_HEARTBEAT, T_POLL, T_ACK_L2, T_CONFIG, T_LOC, T_MSG };
enum : uint8_t { F_MAINS_OK = 1, F_BATT_LOW = 2, F_CHARGING = 4, F_TEST_MODE = 8 };
// fw 0.2+: POLL flags bits 4-6 = ACK_L2 result already shown, bit 7 = byte 7 carries the last shown MSG seq (receipt)
#define F_ACK_SHIFT 4
#define F_MSG_SEQ   0x80
#define FPORT_EVENT 1
#define FPORT_HEARTBEAT 2
#define FPORT_POLL 3
#define FPORT_LOC 4
#define FPORT_ACK_L2 10
#define FPORT_CONFIG 11
#define FPORT_MSG 12

// ===== Behaviour =====
#ifdef SOS_DEBUG_FAST_TIMERS
  #define L1_TIMEOUT_MS      20000UL
  #define POLL_MAX_MS        120000UL
#else
  #define L1_TIMEOUT_MS      60000UL      // no L1 ACK within 60 s -> fast-blink failure
  #define POLL_MAX_MS        (30UL*60000) // stop polling after 30 min
#endif
#define CANCEL_HOLD_MS       3000UL
#define ACCEPTED_POLL_MIN_S  30          // after acceptance keep polling (slower) so staff messages can arrive
#define DONE_DISPLAY_MS      (5UL*60000)
#define BATT_LOW_PCT         20
#define JOIN_RETRY_MIN_MS    10000UL
#define JOIN_RETRY_MAX_MS    300000UL

struct Config {              // runtime-tunable from server via CONFIG downlink
  uint8_t  ver = 1;
  uint16_t heartbeat_min = 30;
  uint8_t  poll_s = 10;
  uint8_t  buzzer = 2;       // 0 off, 1 low, 2 normal, 3 loud (pattern length)
  uint16_t hold_ms = 1500;
};

// ===== Optional GPS module (NEO-6M / ATGM336H class, 3.3 V, NMEA 9600 baud). Off by default: build env heltec_v3_gps =====
#ifndef SOS_GPS
  #define SOS_GPS 0
#endif
#if SOS_GPS
  #define PIN_GPS_RX 33          // ESP32 RX  <- GPS TX.  CHECK these pins are on YOUR board's header before wiring
  #define PIN_GPS_TX 34          // ESP32 TX  -> GPS RX
  #define GPS_BAUD 9600
  #define GPS_MIN_SATS 4         // below this the fix is ignored (also enforced again by the server)
  #define GPS_MAX_AGE_MS 120000UL  // a fix older than 2 min is not reported
  #define GPS_LOC_MAX_TRIES 4    // tries per alert (first right after L1 ACK, then on the next polls)
#endif
