// Host test for gps_nmea.h:  g++ -std=c++17 -Wall -Wextra -o /tmp/test_gps firmware/test/test_gps.cpp && /tmp/test_gps
#include <cstdio>
#include <string>
#include "../include/gps_nmea.h"
static int fails = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)

static std::string withSum(const std::string& body) {
  uint8_t s = 0; for (char c : body) s ^= (uint8_t)c;
  char t[8]; snprintf(t, sizeof t, "*%02X", s); return "$" + body + t + "\r\n";
}
static bool feedAll(NmeaParser& p, const std::string& s) { bool r = false; for (char c : s) r = p.feed(c) || r; return r; }

int main() {
  struct V { int id; double la, lo; int s; double h; const char* hex; };
  const V vs[] = {
#include "loc_vectors.inc"
  };
  for (const V& v : vs) {
    uint8_t o[9]; encodeLoc((uint8_t)v.id, v.la, v.lo, (uint8_t)v.s, v.h, o);
    char hx[19]; for (int i = 0; i < 9; i++) snprintf(hx + 2 * i, 3, "%02x", o[i]);
    if (strcmp(hx, v.hex) != 0) { printf("FAIL vector id=%d got %s want %s\n", v.id, hx, v.hex); fails++; }
  }
  { NmeaParser p; GpsFix f;
    CHECK(feedAll(p, withSum("GPGGA,123519,1354.6900,N,10035.7240,E,1,08,1.2,10.0,M,0,M,,")));
    CHECK(p.take(f) && f.valid && f.sats == 8);
    CHECK(fabs(f.lat - 13.911500) < 1e-6 && fabs(f.lon - 100.595400) < 1e-6 && fabs(f.hdop - 1.2) < 1e-3); }
  { NmeaParser p; GpsFix f;                                         // other talker, southern/western hemisphere
    CHECK(feedAll(p, withSum("GNGGA,000000,3352.1280,S,15112.5580,W,2,10,0.9,5,M,0,M,,")));
    CHECK(p.take(f) && f.lat < -33.86 && f.lat > -33.87 && f.lon < -151.2 && f.lon > -151.21); }
  { NmeaParser p; GpsFix f; std::string s = withSum("GPGGA,1,1354.6900,N,10035.7240,E,1,08,1.2,10.0,M,0,M,,"); s[s.size() - 4] ^= 1;   // corrupt checksum
    CHECK(!feedAll(p, s)); CHECK(!p.take(f)); }
  { NmeaParser p; GpsFix f;                                         // quality 0 = no fix
    CHECK(!feedAll(p, withSum("GPGGA,1,,,,,0,00,99.99,,M,,M,,"))); CHECK(!(p.take(f) && f.valid)); }
  { NmeaParser p; CHECK(!feedAll(p, withSum("GPRMC,1,A,1354.6900,N,10035.7240,E,0,0,010125,,,A"))); }       // not GGA
  { NmeaParser p; CHECK(!feedAll(p, "$GPGGA,garbage*00\r\n")); CHECK(!feedAll(p, std::string(300, 'x') + "\r\n")); }
  { NmeaParser p; GpsFix f;                                         // junk, then a good sentence (resync on '$')
    CHECK(feedAll(p, "xx$GPG" + withSum("GPGGA,1,1354.6900,N,10035.7240,E,1,07,2.0,10.0,M,0,M,,"))); CHECK(p.take(f) && f.sats == 7); }
  { NmeaParser p; CHECK(!feedAll(p, withSum("GPGGA,1,9999.0000,X,10035.7240,E,1,08,1.2,10.0,M,0,M,,"))); }   // bad hemisphere
  printf(fails ? "%d FAILED\n" : "ALL OK\n", fails);
  return fails ? 1 : 0;
}
