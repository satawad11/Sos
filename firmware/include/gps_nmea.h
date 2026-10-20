#pragma once
// Optional GPS support (build with -D SOS_GPS=1). Pure C++ with NO Arduino dependency so it can be unit-tested on a PC:
//   g++ -std=c++17 -Wall -Wextra -o /tmp/test_gps firmware/test/test_gps.cpp && /tmp/test_gps
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>

struct GpsFix {
  bool    valid = false;     // true only when a position with >= 1 satellite was parsed from a checksummed sentence
  double  lat = 0, lon = 0;  // decimal degrees
  uint8_t sats = 0;
  float   hdop = 99.f;
};

// Feed raw bytes from the module; call take() to read the newest fix. Understands $xxGGA (position/sats/HDOP).
// Any talker id works (GP, GN, GL, BD...). Sentences with a bad or missing checksum are ignored.
class NmeaParser {
 public:
  bool feed(char c) {                       // returns true when a complete valid GGA with a fix was just parsed
    if (c == '$') { len_ = 0; active_ = true; return false; }
    if (!active_) return false;
    if (c == '\r' || c == '\n') { active_ = false; return finish(); }
    if (len_ >= sizeof buf_ - 1) { active_ = false; return false; }   // overlong: junk
    buf_[len_++] = c;
    return false;
  }
  bool take(GpsFix& out) { if (!have_) return false; out = fix_; return true; }
  void reset() { have_ = false; fix_ = GpsFix(); }

 private:
  char buf_[120]; size_t len_ = 0; bool active_ = false; bool have_ = false; GpsFix fix_;

  static int hex(char c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'A' && c <= 'F' ? c - 'A' + 10 : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1; }

  static bool parseCoord(const char* v, char hemi, bool isLat, double& out) {   // ddmm.mmmm / dddmm.mmmm
    if (!*v) return false;
    int degDigits = isLat ? 2 : 3;
    if (strlen(v) < (size_t)degDigits + 2) return false;
    char d[4] = {0}; memcpy(d, v, degDigits);
    for (int i = 0; i < degDigits; i++) if (d[i] < '0' || d[i] > '9') return false;
    char* end = nullptr; double minutes = strtod(v + degDigits, &end);
    if (end == v + degDigits || *end != '\0' || minutes < 0 || minutes >= 60) return false;
    double deg = atoi(d) + minutes / 60.0;
    if (hemi == 'S' || hemi == 'W') deg = -deg; else if (hemi != 'N' && hemi != 'E') return false;
    out = deg; return true;
  }

  bool finish() {
    buf_[len_] = 0;
    char* star = strrchr(buf_, '*');
    if (!star || strlen(star) < 3) return false;
    uint8_t sum = 0; for (const char* p = buf_; p < star; p++) sum ^= (uint8_t)*p;
    int h1 = hex(star[1]), h2 = hex(star[2]);
    if (h1 < 0 || h2 < 0 || sum != (uint8_t)(h1 * 16 + h2)) return false;
    *star = 0;
    if (strlen(buf_) < 6 || strncmp(buf_ + 2, "GGA", 3) != 0) return false;    // buf_ = "GPGGA,..." (talker 2 chars + GGA)
    // split on commas (keep empty fields)
    char* f[16]; int n = 0; char* p = buf_;
    while (n < 15) { f[n++] = p; char* c = strchr(p, ','); if (!c) break; *c = 0; p = c + 1; }
    if (n < 9) return false;
    // f[1]=time f[2]=lat f[3]=N/S f[4]=lon f[5]=E/W f[6]=quality f[7]=sats f[8]=hdop
    int quality = atoi(f[6]);
    if (quality < 1) { fix_.valid = false; have_ = true; return false; }          // 0 = no fix
    double lat, lon;
    if (!parseCoord(f[2], f[3][0], true, lat) || !parseCoord(f[4], f[5][0], false, lon)) return false;
    int sats = atoi(f[7]); if (sats < 1) return false;
    fix_.lat = lat; fix_.lon = lon; fix_.sats = (uint8_t)(sats > 255 ? 255 : sats);
    fix_.hdop = f[8][0] ? (float)atof(f[8]) : 99.f; fix_.valid = true; have_ = true;
    return true;
  }
};

// LOC uplink (FPort 4, 9 bytes) — must produce exactly the bytes backend/app/codec.py encode_loc() produces.
#define PROTO_VER_GPS 1
#define T_LOC_MSG 8
static inline void encodeLoc(uint8_t alertId, double lat, double lon, uint8_t sats, double hdop, uint8_t out[9]) {
  const double Q = 16777216.0;                                      // 2^24
  double la = floor((lat + 90.0) / 180.0 * Q + 0.5), lo = floor((lon + 180.0) / 360.0 * Q + 0.5);
  la = la < 0 ? 0 : (la > Q - 1 ? Q - 1 : la);
  lo = lo < 0 ? 0 : (lo > Q - 1 ? Q - 1 : lo);
  uint32_t a = (uint32_t)la, b = (uint32_t)lo;
  double h = floor(hdop * 2.0 + 0.5); h = h < 0 ? 0 : (h > 15 ? 15 : h);
  out[0] = (uint8_t)((PROTO_VER_GPS << 4) | T_LOC_MSG); out[1] = alertId;
  out[2] = (uint8_t)(a >> 16); out[3] = (uint8_t)(a >> 8); out[4] = (uint8_t)a;
  out[5] = (uint8_t)(b >> 16); out[6] = (uint8_t)(b >> 8); out[7] = (uint8_t)b;
  out[8] = (uint8_t)(((sats > 15 ? 15 : sats) << 4) | (uint8_t)h);
}
