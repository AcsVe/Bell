/*
 * School bell DEVICE — ESP32-S3 firmware (الجرس المركزي — جهاز جرس بدون كمبيوتر)
 *
 * Same job as BellPlayer.exe, on a small box connected to the PA amplifier:
 *   • Wi-Fi mode: talks to the bell server exactly like BellPlayer (/api/central/poll + /schedule, X-Bell-Key),
 *     rings by the SERVER clock, reaches the server on the school network when the internet is down.
 *   • Bluetooth mode (no network in the building): the admin page sends the schedule, tones, time and settings over
 *     Bluetooth (Chrome, Web Bluetooth). The DS3231 clock keeps the time.
 *   • Rings from its own copy (flash) — no internet / server needed after a sync.
 *   • License: ECDSA P-256 key tied to this chip's fingerprint (same key pair as BellPlayer / home.py).
 *
 * Hardware (see docs/Bell_Hardware.md):
 *   ESP32-S3-DevKitC-1 N16R8 · PCM5102A I2S DAC (line out → amplifier) · DS3231 RTC (I2C) · optional SSD1306 OLED
 *   I2S: BCK=GPIO5  LRCK=GPIO6  DIN=GPIO7      I2C: SDA=GPIO8  SCL=GPIO9      BOOT button (GPIO0) held 3 s = Wi-Fi setup
 */
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <RTClib.h>
#include <NimBLEDevice.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <driver/i2s.h>
#include <mbedtls/pk.h>
#include <mbedtls/md.h>
#include <mbedtls/base64.h>
#include <algorithm>
#include <sys/time.h>
#include <bitset>
#include <vector>

#define FW_VERSION "device-1.0"
#define PIN_BCK 5
#define PIN_LRCK 6
#define PIN_DOUT 7
#define PIN_SDA 8
#define PIN_SCL 9
#define PIN_BOOT 0
#define CATCH_UP_S 90
#define POLL_MS 15000

extern const uint8_t crt_bundle_start[] asm("_binary_data_embed_x509_crt_bundle_bin_start");

static const char *LICENSE_PUBLIC_KEY_PEM =
    "-----BEGIN PUBLIC KEY-----\n"
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE9hskm3ufgTN+cmD+Vhzv2w6OIiuw\n"
    "RL/YjNVP/aJaT05+91ZOAv634CJt812D8dP5TtfS6XpAeHRX8ALMi95b9Q==\n"
    "-----END PUBLIC KEY-----\n";

#define BLE_SVC "6e1b0001-5a1e-4b1e-9c1e-be11be11be11"
#define BLE_RX  "6e1b0002-5a1e-4b1e-9c1e-be11be11be11"
#define BLE_ST  "6e1b0003-5a1e-4b1e-9c1e-be11be11be11"

// ── state ──────────────────────────────────────────────────────────────────
struct Config {
  String ssid, pass, server, key, license, lan, lastGood;
  int tzMin = 180;
  bool tzKnown = false;
} cfg;

struct Slot { uint16_t minute; std::vector<String> tones; };
struct Schedule {
  bool ok = false, enabled = true;
  int volume = 100;
  String version;
  std::vector<String> skip;
  std::vector<Slot> days[7];        // Monday = 0 … Sunday = 6 (same as the server)
  std::vector<String> toneKeys;
};

Preferences prefs;
Schedule *sched = new Schedule();
SemaphoreHandle_t schedLock;
QueueHandle_t audioQ;
RTC_DS3231 rtc;
bool hasRtc = false;
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
bool hasOled = false;
String fingerprint;
String lastRing, lastError, lastTest, via = "-";
int clockDrift = 0;
bool licValid = false, licExpired = false;
String licExp;
int licDay = -1;
std::bitset<1440> fired;
int firedDay = -1;
bool portalOn = false;
WebServer web(80);
DNSServer dns;
NimBLECharacteristic *stChar = nullptr;

struct AudioJob { char path[64]; int vol; bool test; };

// ── helpers ────────────────────────────────────────────────────────────────
static String safeKey(const String &k) {
  String s;
  for (char c : k) s += (isalnum((unsigned char)c) || c == '-' || c == '_') ? c : '_';
  return s;
}
static String tonePath(const String &k) { return "/t/" + safeKey(k) + ".wav"; }

static time_t nowUtc() { return time(nullptr); }
static bool timeValid() { return nowUtc() > 1735689600; }   // after 2025-01-01
static struct tm localNow() {
  time_t t = nowUtc() + (time_t)cfg.tzMin * 60;
  struct tm tmv;
  gmtime_r(&t, &tmv);
  return tmv;
}
static int weekdayMon0(const struct tm &t) { return (t.tm_wday + 6) % 7; }
static int dateInt(const struct tm &t) { return (t.tm_year + 1900) * 10000 + (t.tm_mon + 1) * 100 + t.tm_mday; }
static String dateIso(const struct tm &t) {
  char b[12];
  snprintf(b, sizeof b, "%04d-%02d-%02d", t.tm_year + 1900, t.tm_mon + 1, t.tm_mday);
  return b;
}

static void setClockMs(double epochMs) {
  struct timeval tv;
  tv.tv_sec = (time_t)(epochMs / 1000.0);
  tv.tv_usec = (suseconds_t)(fmod(epochMs, 1000.0) * 1000);
  settimeofday(&tv, nullptr);
  if (hasRtc) rtc.adjust(DateTime((uint32_t)tv.tv_sec));      // the DS3231 keeps UTC
}

static void saveCfg() {
  prefs.putString("ssid", cfg.ssid);
  prefs.putString("pass", cfg.pass);
  prefs.putString("server", cfg.server);
  prefs.putString("key", cfg.key);
  prefs.putString("license", cfg.license);
  prefs.putString("lan", cfg.lan);
  prefs.putInt("tz", cfg.tzMin);
  prefs.putBool("tzk", cfg.tzKnown);
}
static void loadCfg() {
  cfg.ssid = prefs.getString("ssid", "");
  cfg.pass = prefs.getString("pass", "");
  cfg.server = prefs.getString("server", "https://5.iajaward.org");
  cfg.key = prefs.getString("key", "");
  cfg.license = prefs.getString("license", "");
  cfg.lan = prefs.getString("lan", "");
  cfg.tzMin = prefs.getInt("tz", 180);
  cfg.tzKnown = prefs.getBool("tzk", false);
}

// ── fingerprint + license ─────────────────────────────────────────────────
static void sha256(const uint8_t *in, size_t n, uint8_t out[32]) {
  mbedtls_md(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), in, n, out);
}

static String makeFingerprint() {
  uint64_t m = ESP.getEfuseMac();
  uint8_t mac[6];
  for (int i = 0; i < 6; i++) mac[i] = (m >> (8 * i)) & 0xff;
  char raw[40];
  snprintf(raw, sizeof raw, "BELL-DEVICE|%02X%02X%02X%02X%02X%02X", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  uint8_t h[32];
  sha256((const uint8_t *)raw, strlen(raw), h);
  char out[17];
  for (int i = 0; i < 8; i++) snprintf(out + i * 2, 3, "%02X", h[i]);
  return String(out);
}

static bool verifySig(const String &msg, const String &sigB64url) {
  String b = sigB64url;
  b.replace('-', '+');
  b.replace('_', '/');
  while (b.length() % 4) b += '=';
  uint8_t sig[160];
  size_t sl = 0;
  if (mbedtls_base64_decode(sig, sizeof sig, &sl, (const uint8_t *)b.c_str(), b.length()) != 0) return false;
  uint8_t h[32];
  sha256((const uint8_t *)msg.c_str(), msg.length(), h);
  mbedtls_pk_context pk;
  mbedtls_pk_init(&pk);
  bool ok = mbedtls_pk_parse_public_key(&pk, (const uint8_t *)LICENSE_PUBLIC_KEY_PEM, strlen(LICENSE_PUBLIC_KEY_PEM) + 1) == 0 &&
            mbedtls_pk_verify(&pk, MBEDTLS_MD_SHA256, h, 32, sig, sl) == 0;
  mbedtls_pk_free(&pk);
  return ok;
}

// valid / expired / exp ("PERMANENT" or "YYYY-MM-DD")
static void checkLicense(const String &key, bool &valid, bool &expired, String &exp) {
  valid = expired = false;
  exp = "";
  int c = key.indexOf(':');
  if (c <= 0) return;
  String e = key.substring(0, c);
  e.trim();
  e.toUpperCase();
  String sig = key.substring(c + 1);
  sig.trim();
  if (!verifySig(fingerprint + "|" + e, sig)) return;
  valid = true;
  if (e == "PERMANENT") { exp = "PERMANENT"; return; }
  if (e.length() != 8) { valid = false; return; }
  exp = e.substring(0, 4) + "-" + e.substring(4, 6) + "-" + e.substring(6, 8);
  if (timeValid()) expired = dateInt(localNow()) > e.toInt();
}
static void refreshLicense(bool force = false) {
  int d = timeValid() ? dateInt(localNow()) : 0;
  if (!force && d == licDay) return;
  checkLicense(cfg.license, licValid, licExpired, licExp);
  licDay = d;
}
static bool licensed() { refreshLicense(); return licValid && !licExpired; }
static String licenseText() {
  refreshLicense();
  if (!licValid) return "invalid";
  if (licExpired) return "expired:" + licExp;
  return licExp;
}

// ── schedule ──────────────────────────────────────────────────────────────
static Schedule *parseSchedule(const String &path) {
  File f = LittleFS.open(path, "r");
  if (!f) return nullptr;
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, f);
  f.close();
  if (err) return nullptr;
  Schedule *s = new Schedule();
  s->enabled = doc["enabled"] | true;
  s->volume = doc["volume"] | 100;
  s->version = (const char *)(doc["version"] | "");
  for (JsonVariant v : doc["skip_dates"].as<JsonArray>()) s->skip.push_back(String((const char *)v));
  for (JsonPair kv : doc["tones"].as<JsonObject>()) s->toneKeys.push_back(String(kv.key().c_str()));
  for (int wd = 0; wd < 7; wd++) {
    for (JsonVariant slot : doc["days"][String(wd)].as<JsonArray>()) {
      String t = (const char *)(slot["t"] | "");
      if (t.length() < 5) continue;
      Slot sl;
      sl.minute = t.substring(0, 2).toInt() * 60 + t.substring(3, 5).toInt();
      for (JsonVariant r : slot["rings"].as<JsonArray>()) sl.tones.push_back(String((const char *)(r["tone"] | "")));
      s->days[wd].push_back(sl);
    }
  }
  s->ok = true;
  return s;
}
static void swapSchedule(Schedule *n) {
  if (!n) return;
  xSemaphoreTake(schedLock, portMAX_DELAY);
  Schedule *old = sched;
  sched = n;
  xSemaphoreGive(schedLock);
  delete old;
}
static bool tonesCached() {
  bool ok = true;
  xSemaphoreTake(schedLock, portMAX_DELAY);
  for (auto &k : sched->toneKeys) if (!LittleFS.exists(tonePath(k))) { ok = false; break; }
  xSemaphoreGive(schedLock);
  return ok && sched->ok;
}
static String nextRingText() {
  if (!timeValid()) return "";
  struct tm t = localNow();
  int nowMin = t.tm_hour * 60 + t.tm_min;
  String out;
  xSemaphoreTake(schedLock, portMAX_DELAY);
  if (sched->ok && sched->enabled) {
    for (int add = 0; add < 8 && out.isEmpty(); add++) {
      time_t tt = nowUtc() + (time_t)cfg.tzMin * 60 + add * 86400;
      struct tm d;
      gmtime_r(&tt, &d);
      String iso = dateIso(d);
      bool skip = false;
      for (auto &s : sched->skip) if (s == iso) skip = true;
      if (skip) continue;
      for (auto &sl : sched->days[weekdayMon0(d)]) {
        if (add || sl.minute > nowMin) {
          char b[24];
          snprintf(b, sizeof b, "%s %02d:%02d", iso.c_str(), sl.minute / 60, sl.minute % 60);
          out = b;
          break;
        }
      }
    }
  }
  xSemaphoreGive(schedLock);
  return out;
}

// ── audio (PCM5102A over I2S) ─────────────────────────────────────────────
static void i2sInit() {
  i2s_config_t c = {};
  c.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX);
  c.sample_rate = 22050;
  c.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  c.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  c.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  c.intr_alloc_flags = 0;
  c.dma_buf_count = 8;
  c.dma_buf_len = 256;
  c.use_apll = false;
  c.tx_desc_auto_clear = true;
  i2s_driver_install(I2S_NUM_0, &c, 0, nullptr);
  i2s_pin_config_t p = {};
  p.mck_io_num = I2S_PIN_NO_CHANGE;
  p.bck_io_num = PIN_BCK;
  p.ws_io_num = PIN_LRCK;
  p.data_out_num = PIN_DOUT;
  p.data_in_num = I2S_PIN_NO_CHANGE;
  i2s_set_pin(I2S_NUM_0, &p);
  i2s_zero_dma_buffer(I2S_NUM_0);
}

static bool playWav(const char *path, int vol) {
  File f = LittleFS.open(path, "r");
  if (!f) return false;
  char id[4];
  uint32_t sz;
  if (f.read((uint8_t *)id, 4) != 4 || memcmp(id, "RIFF", 4)) { f.close(); return false; }
  f.read((uint8_t *)&sz, 4);
  f.read((uint8_t *)id, 4);
  if (memcmp(id, "WAVE", 4)) { f.close(); return false; }
  uint16_t fmt = 0, ch = 1, bits = 16;
  uint32_t rate = 22050, dataLen = 0;
  while (f.available()) {
    if (f.read((uint8_t *)id, 4) != 4) break;
    f.read((uint8_t *)&sz, 4);
    if (!memcmp(id, "fmt ", 4)) {
      uint8_t b[16];
      f.read(b, 16);
      fmt = b[0] | b[1] << 8;
      ch = b[2] | b[3] << 8;
      rate = b[4] | b[5] << 8 | b[6] << 16 | (uint32_t)b[7] << 24;
      bits = b[14] | b[15] << 8;
      if (sz > 16) f.seek(f.position() + (sz - 16));
    } else if (!memcmp(id, "data", 4)) {
      dataLen = sz;
      break;
    } else {
      f.seek(f.position() + sz + (sz & 1));
    }
  }
  if (fmt != 1 || bits != 16 || !dataLen || ch < 1 || ch > 2) { f.close(); return false; }
  i2s_set_clk(I2S_NUM_0, rate, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_STEREO);
  const int gain = constrain(vol, 0, 100) * 256 / 100;
  static int16_t in[512], out[1024];
  uint32_t left = dataLen;
  while (left) {
    size_t want = left < 1024 ? left : 1024;
    int got = f.read((uint8_t *)in, want);
    if (got <= 0) break;
    left -= got;
    int n = got / 2, o = 0;
    if (ch == 1) for (int i = 0; i < n; i++) { int16_t v = (int32_t)in[i] * gain >> 8; out[o++] = v; out[o++] = v; }
    else for (int i = 0; i < n; i++) out[o++] = (int32_t)in[i] * gain >> 8;
    size_t w;
    i2s_write(I2S_NUM_0, out, o * 2, &w, portMAX_DELAY);
  }
  f.close();
  memset(out, 0, sizeof out);
  size_t w;
  for (int i = 0; i < 4; i++) i2s_write(I2S_NUM_0, out, sizeof out, &w, portMAX_DELAY);
  return true;
}

static void audioTask(void *) {
  AudioJob j;
  for (;;) {
    if (xQueueReceive(audioQ, &j, portMAX_DELAY) == pdTRUE) {
      if (!playWav(j.path, j.vol)) lastError = String("cannot play ") + j.path;
      if (uxQueueMessagesWaiting(audioQ)) vTaskDelay(pdMS_TO_TICKS(600));
    }
  }
}
static void enqueue(const String &path, int vol, bool test) {
  AudioJob j = {};
  strlcpy(j.path, path.c_str(), sizeof j.path);
  j.vol = vol;
  j.test = test;
  xQueueSend(audioQ, &j, 0);
}

// ── rings ─────────────────────────────────────────────────────────────────
static void ringTick(bool startup) {
  if (!timeValid()) return;
  struct tm t = localNow();
  int d = dateInt(t);
  if (d != firedDay) { fired.reset(); firedDay = d; }
  int secOfDay = t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec;
  String iso = dateIso(t);
  xSemaphoreTake(schedLock, portMAX_DELAY);
  bool skip = !sched->ok || !sched->enabled;
  for (auto &s : sched->skip) if (s == iso) skip = true;
  std::vector<String> paths;
  String label;
  if (!skip) {
    for (auto &sl : sched->days[weekdayMon0(t)]) {
      int late = secOfDay - sl.minute * 60;
      if (fired[sl.minute]) continue;
      if (startup) { if (late > 5) fired.set(sl.minute); continue; }   // don't replay a ring missed while off
      if (late < 0 || late > CATCH_UP_S) continue;
      fired.set(sl.minute);
      char b[24];
      snprintf(b, sizeof b, "%s %02d:%02d", iso.c_str(), sl.minute / 60, sl.minute % 60);
      label = b;
      for (auto &k : sl.tones) paths.push_back(tonePath(k));
    }
  }
  int vol = sched->volume;
  xSemaphoreGive(schedLock);
  if (paths.empty()) return;
  if (!licensed()) { Serial.printf("RING %s skipped: no valid license\n", label.c_str()); return; }
  for (auto &p : paths) {
    if (LittleFS.exists(p)) enqueue(p, vol, false);
    else lastError = "tone not cached: " + p;
  }
  lastRing = label;
  Serial.printf("RING %s (%d tones)\n", label.c_str(), (int)paths.size());
}

// ── status JSON (server poll + Bluetooth) ────────────────────────────────
static String statusJson(bool forBle) {
  JsonDocument d;
  d["host"] = "BELL-" + fingerprint.substring(0, 6);
  d["player"] = FW_VERSION;
  d["fingerprint"] = fingerprint;
  d["license"] = licenseText();
  d["version"] = sched->version;
  d["cached"] = tonesCached();
  d["last_ring"] = lastRing;
  d["next_ring"] = nextRingText();
  d["clock_drift"] = clockDrift;
  d["error"] = lastError.substring(0, 200);
  d["via"] = via;
  if (forBle) {
    d["wifi"] = WiFi.status() == WL_CONNECTED;
    d["ssid"] = cfg.ssid;
    d["ip"] = WiFi.localIP().toString();
    d["server"] = cfg.server;
    d["has_key"] = cfg.key.length() > 0;
    d["rtc"] = hasRtc;
    if (timeValid()) {
      struct tm t = localNow();
      char b[24];
      snprintf(b, sizeof b, "%s %02d:%02d:%02d", dateIso(t).c_str(), t.tm_hour, t.tm_min, t.tm_sec);
      d["time"] = b;
    }
    d["free_kb"] = (int)((LittleFS.totalBytes() - LittleFS.usedBytes()) / 1024);
  }
  String s;
  serializeJson(d, s);
  return s;
}

// ── HTTP to the server ───────────────────────────────────────────────────
static int httpCall(const String &base, const String &path, bool post, const String &body, String *out, File *file, uint32_t &t0, uint32_t &t1) {
  HTTPClient http;
  WiFiClientSecure sc;
  WiFiClient pc;
  String url = path.startsWith("http") ? path : base + path;
  bool ok;
  if (url.startsWith("https")) { sc.setCACertBundle(crt_bundle_start); ok = http.begin(sc, url); }
  else ok = http.begin(pc, url);
  if (!ok) return -1;
  http.setTimeout(url.startsWith(cfg.server) ? 20000 : 4000);
  http.setConnectTimeout(url.startsWith(cfg.server) ? 10000 : 3000);
  http.addHeader("X-Bell-Key", cfg.key);
  http.addHeader("Content-Type", "application/json");
  http.setUserAgent("BellDevice/" FW_VERSION);
  t0 = millis();
  int code = post ? http.POST(body) : http.GET();
  t1 = millis();
  if (code == 200) {
    if (file) http.writeToStream(file);
    else if (out) *out = http.getString();
  }
  http.end();
  return code;
}

// try the address that worked last, then the school-network addresses, then the public one
static int callServer(const String &path, bool post, const String &body, String *out, File *file, uint32_t &t0, uint32_t &t1) {
  std::vector<String> bases;
  auto add = [&](String b) { b.trim(); while (b.endsWith("/")) b.remove(b.length() - 1); if (b.length() && std::find(bases.begin(), bases.end(), b) == bases.end()) bases.push_back(b); };
  add(cfg.lastGood);
  int from = 0;
  while (from <= (int)cfg.lan.length()) {
    int c = cfg.lan.indexOf(',', from);
    if (c < 0) c = cfg.lan.length();
    add(cfg.lan.substring(from, c));
    from = c + 1;
  }
  add(cfg.server);
  int code = -1;
  for (auto &b : bases) {
    code = httpCall(b, path, post, body, out, file, t0, t1);
    if (code == 200 || code == 401) {
      if (code == 200) { cfg.lastGood = b; via = (b == cfg.server ? "internet " : "school network ") + b; }
      return code;
    }
  }
  return code;
}

static bool downloadTone(const String &key, const String &url) {
  String p = tonePath(key);
  if (LittleFS.exists(p)) return true;
  if (url.endsWith(".mp3")) { lastError = "MP3 tone " + key + " cannot play on the device - use WAV"; return false; }
  File f = LittleFS.open("/t/dl.part", "w");
  if (!f) return false;
  uint32_t a, b;
  int code = callServer(url, false, "", nullptr, &f, a, b);
  size_t n = f.size();
  f.close();
  if (code != 200 || n < 64) { LittleFS.remove("/t/dl.part"); return false; }
  LittleFS.rename("/t/dl.part", p);
  return true;
}

static void syncOnce() {
  if (WiFi.status() != WL_CONNECTED || cfg.key.isEmpty()) return;
  uint32_t t0, t1;
  String resp;
  int code = callServer("/api/central/poll", true, statusJson(false), &resp, nullptr, t0, t1);
  if (code != 200) { lastError = code == 401 ? "server refused the player key" : "server not reachable (" + String(code) + ")"; return; }
  JsonDocument r;
  if (deserializeJson(r, resp)) return;
  // server clock: its time + half the round trip
  if (!r["server_ms"].isNull() && t1 - t0 < 5000) {
    double srv = r["server_ms"].as<double>() + (t1 - t0) / 2.0;
    struct timeval tv;
    gettimeofday(&tv, nullptr);
    double mine = tv.tv_sec * 1000.0 + tv.tv_usec / 1000.0;
    clockDrift = (int)lround((mine - srv) / 1000.0);
    if (fabs(mine - srv) > 500) setClockMs(srv);
  }
  bool dirty = false;
  if (!r["tz_offset_min"].isNull() && (cfg.tzMin != r["tz_offset_min"].as<int>() || !cfg.tzKnown)) { cfg.tzMin = r["tz_offset_min"]; cfg.tzKnown = true; dirty = true; }
  String lan;
  for (JsonVariant v : r["lan"].as<JsonArray>()) { if (lan.length()) lan += ","; lan += (const char *)v; }
  if (lan.length() && lan != cfg.lan) { cfg.lan = lan; dirty = true; }
  if (dirty) saveCfg();
  refreshLicense(true);
  // schedule
  String ver = (const char *)(r["version"] | "");
  if (ver != sched->version || !tonesCached()) {
    File f = LittleFS.open("/schedule.part", "w");
    if (f) {
      uint32_t a, b;
      int c = callServer("/api/central/schedule", false, "", nullptr, &f, a, b);
      f.close();
      Schedule *n = c == 200 ? parseSchedule("/schedule.part") : nullptr;
      if (n) {
        File sf = LittleFS.open("/schedule.part", "r");
        JsonDocument sd;
        deserializeJson(sd, sf);
        sf.close();
        bool all = true;
        for (JsonPair kv : sd["tones"].as<JsonObject>()) all &= downloadTone(kv.key().c_str(), (const char *)(kv.value()["url"] | ""));
        LittleFS.remove("/schedule.json");
        LittleFS.rename("/schedule.part", "/schedule.json");
        swapSchedule(n);
        Serial.printf("schedule %s (%s)\n", n->version.c_str(), all ? "all tones" : "some tones missing");
        if (all) lastError = "";
      }
    }
  }
  // test ring from the admin page
  JsonVariant test = r["test"];
  if (!test.isNull()) {
    String id = (const char *)(test["id"] | "");
    if (id.length() && id != lastTest) {
      lastTest = id;
      String k = (const char *)(test["tone"] | "");
      if (licensed() && downloadTone(k, (const char *)(test["url"] | ""))) enqueue(tonePath(k), sched->volume, true);
    }
  }
}

static void netTask(void *) {
  uint32_t lastTry = 0;
  for (;;) {
    if (cfg.ssid.length() && WiFi.status() != WL_CONNECTED && millis() - lastTry > 20000) {
      lastTry = millis();
      WiFi.mode(portalOn ? WIFI_AP_STA : WIFI_STA);
      WiFi.begin(cfg.ssid.c_str(), cfg.pass.c_str());
    }
    syncOnce();
    vTaskDelay(pdMS_TO_TICKS(POLL_MS));
  }
}

// ── Bluetooth (admin page → device, Web Bluetooth) ────────────────────────
//   RX write: [op][payload]   op 1 = begin file "name\nsize", 2 = data, 3 = end, 4 = time "epoch_ms,tz_min", 5 = command
//   ST read/notify: status JSON; acks {"ack":op,"ok":bool,"err":"..."}
static File bleFile;
static String bleName;
static size_t bleSize = 0, bleGot = 0;

static void notify(const String &s) {
  if (!stChar) return;
  stChar->setValue(s.c_str());
  stChar->notify();
}
static void ack(int op, bool ok, const String &err = "") {
  JsonDocument d;
  d["ack"] = op;
  d["ok"] = ok;
  if (err.length()) d["err"] = err;
  String s;
  serializeJson(d, s);
  notify(s);
}

static void applyConfigJson(const String &path) {
  File f = LittleFS.open(path, "r");
  JsonDocument d;
  deserializeJson(d, f);
  f.close();
  LittleFS.remove(path);
  if (!d["ssid"].isNull()) cfg.ssid = (const char *)d["ssid"];
  if (!d["pass"].isNull()) cfg.pass = (const char *)d["pass"];
  if (!d["server"].isNull()) cfg.server = (const char *)d["server"];
  if (!d["key"].isNull()) cfg.key = (const char *)d["key"];
  if (!d["license"].isNull()) cfg.license = (const char *)d["license"];
  if (!d["tz_offset_min"].isNull()) { cfg.tzMin = d["tz_offset_min"]; cfg.tzKnown = true; }
  if (!d["lan"].isNull()) cfg.lan = (const char *)d["lan"];
  saveCfg();
  refreshLicense(true);
  if (!d["ssid"].isNull()) { WiFi.disconnect(); }     // netTask reconnects with the new network
}

class RxCb : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic *c) override {
    std::string v = c->getValue();
    if (v.empty()) return;
    uint8_t op = (uint8_t)v[0];
    const char *p = v.data() + 1;
    size_t n = v.size() - 1;
    if (op == 1) {
      String s(p, n);
      int nl = s.indexOf('\n');
      bleName = s.substring(0, nl);
      bleSize = s.substring(nl + 1).toInt();
      bleGot = 0;
      if (bleFile) bleFile.close();
      bool ok = bleName.length() && bleName.indexOf("..") < 0 && bleSize > 0 && bleSize < (LittleFS.totalBytes() - LittleFS.usedBytes());
      if (ok) bleFile = LittleFS.open("/ble.part", "w");
      ack(1, ok && bleFile, ok ? "" : "no space or bad name");
    } else if (op == 2) {
      if (bleFile) { bleFile.write((const uint8_t *)p, n); bleGot += n; }
    } else if (op == 3) {
      if (bleFile) bleFile.close();
      if (bleGot != bleSize) { LittleFS.remove("/ble.part"); ack(3, false, "size mismatch"); return; }
      if (bleName == "config.json") { applyConfigJson("/ble.part"); ack(3, true); return; }
      String dest = "/" + bleName;
      if (bleName == "schedule.json") {
        Schedule *s = parseSchedule("/ble.part");
        if (!s) { LittleFS.remove("/ble.part"); ack(3, false, "bad schedule"); return; }
        LittleFS.remove(dest);
        LittleFS.rename("/ble.part", dest);
        swapSchedule(s);
        via = "bluetooth";
      } else {
        if (!LittleFS.exists("/t")) LittleFS.mkdir("/t");
        LittleFS.remove(dest);
        LittleFS.rename("/ble.part", dest);
      }
      ack(3, true);
    } else if (op == 4) {
      String s(p, n);
      int comma = s.indexOf(',');
      setClockMs(atof(s.substring(0, comma).c_str()));
      if (comma > 0) { cfg.tzMin = s.substring(comma + 1).toInt(); cfg.tzKnown = true; saveCfg(); }
      refreshLicense(true);
      ack(4, true);
    } else if (op == 5) {
      String cmd(p, n);
      if (cmd.startsWith("test:")) {
        String k = cmd.substring(5);
        bool ok = licensed() && LittleFS.exists(tonePath(k));
        if (ok) enqueue(tonePath(k), sched->volume, true);
        ack(5, ok, ok ? "" : (licensed() ? "tone not on the device" : "no valid license"));
      } else if (cmd == "status") {
        notify(statusJson(true));
      } else if (cmd == "reboot") {
        ack(5, true);
        delay(300);
        ESP.restart();
      } else if (cmd == "clear-tones") {
        File dir = LittleFS.open("/t");
        std::vector<String> names;
        for (File e = dir.openNextFile(); e; e = dir.openNextFile()) names.push_back(String("/t/") + e.name());
        for (auto &x : names) LittleFS.remove(x);
        ack(5, true);
      } else ack(5, false, "unknown command");
    }
  }
};
class StCb : public NimBLECharacteristicCallbacks {
  void onRead(NimBLECharacteristic *c) override { c->setValue(statusJson(true).c_str()); }
};

static void bleInit() {
  NimBLEDevice::init(("Bell-" + fingerprint.substring(0, 4)).c_str());
  NimBLEDevice::setMTU(517);
  NimBLEServer *srv = NimBLEDevice::createServer();
  NimBLEService *svc = srv->createService(BLE_SVC);
  NimBLECharacteristic *rx = svc->createCharacteristic(BLE_RX, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR, 600);
  rx->setCallbacks(new RxCb());
  stChar = svc->createCharacteristic(BLE_ST, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY, 600);
  stChar->setCallbacks(new StCb());
  svc->start();
  NimBLEAdvertising *adv = NimBLEDevice::getAdvertising();
  adv->addServiceUUID(BLE_SVC);
  adv->setScanResponse(true);
  adv->start();
  srv->advertiseOnDisconnect(true);
}

// ── Wi-Fi setup page (no Bluetooth browser at hand): hold BOOT 3 s → network «Bell-XXXX» → http://192.168.4.1 ──
static void startPortal() {
  if (portalOn) return;
  portalOn = true;
  WiFi.mode(WIFI_AP_STA);
  String ap = "Bell-" + fingerprint.substring(0, 4);
  WiFi.softAP(ap.c_str(), "bellsetup");
  dns.start(53, "*", WiFi.softAPIP());
  web.on("/", HTTP_GET, []() {
    String h = "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><title>Bell device</title>"
               "<style>body{font-family:sans-serif;max-width:420px;margin:20px auto;padding:0 12px}input{width:100%;padding:8px;margin:4px 0 12px}"
               "button{padding:10px 18px}</style><h2>School bell device</h2><p>Fingerprint: <b>" + fingerprint + "</b><br>License: " + licenseText() +
               "</p><form method=post action=/save>Wi-Fi name<input name=ssid value='" + cfg.ssid + "'>Wi-Fi password<input name=pass type=password>"
               "Server<input name=server value='" + cfg.server + "'>Player key (admin page)<input name=key placeholder='" + (cfg.key.length() ? "(kept)" : "") +
               "'>License key<input name=license placeholder='" + (cfg.license.length() ? "(kept)" : "") + "'><button>Save and restart</button></form>";
    web.send(200, "text/html", h);
  });
  web.on("/save", HTTP_POST, []() {
    if (web.arg("ssid").length()) cfg.ssid = web.arg("ssid");
    if (web.arg("pass").length()) cfg.pass = web.arg("pass");
    if (web.arg("server").length()) cfg.server = web.arg("server");
    if (web.arg("key").length()) cfg.key = web.arg("key");
    if (web.arg("license").length()) cfg.license = web.arg("license");
    saveCfg();
    web.send(200, "text/html", "<meta name=viewport content='width=device-width'><h3>Saved - restarting...</h3>");
    delay(800);
    ESP.restart();
  });
  web.onNotFound([]() { web.sendHeader("Location", "http://192.168.4.1/"); web.send(302, "text/plain", ""); });
  web.begin();
  Serial.printf("setup portal: Wi-Fi %s / bellsetup -> http://192.168.4.1\n", ap.c_str());
}

// ── screen ───────────────────────────────────────────────────────────────
static void drawOled() {
  if (!hasOled) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(2);
  oled.setCursor(0, 0);
  if (timeValid()) {
    struct tm t = localNow();
    char b[12];
    snprintf(b, sizeof b, "%02d:%02d:%02d", t.tm_hour, t.tm_min, t.tm_sec);
    oled.print(b);
    oled.setTextSize(1);
    oled.setCursor(0, 18);
    oled.print(dateIso(t));
  } else {
    oled.print("--:--:--");
    oled.setTextSize(1);
    oled.setCursor(0, 18);
    oled.print("time not set");
  }
  oled.setCursor(0, 30);
  String nx = nextRingText();
  oled.print("Next ");
  oled.print(nx.length() > 11 ? nx.substring(11) : String("-"));
  oled.setCursor(0, 42);
  oled.print(WiFi.status() == WL_CONNECTED ? "WiFi OK " : (cfg.ssid.length() ? "WiFi... " : "No WiFi "));
  oled.print(licensed() ? "Lic OK" : "NO LICENSE");
  oled.setCursor(0, 54);
  oled.print(portalOn ? "Setup: Bell-" + fingerprint.substring(0, 4) : "FP " + fingerprint);
  oled.display();
}

// ── main ─────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(200);
  pinMode(PIN_BOOT, INPUT_PULLUP);
  schedLock = xSemaphoreCreateMutex();
  audioQ = xQueueCreate(8, sizeof(AudioJob));
  fingerprint = makeFingerprint();
  Serial.printf("\nBell device %s  fingerprint %s\n", FW_VERSION, fingerprint.c_str());
  prefs.begin("bell", false);
  loadCfg();
  LittleFS.begin(true);
  if (!LittleFS.exists("/t")) LittleFS.mkdir("/t");
  Wire.begin(PIN_SDA, PIN_SCL);
  hasRtc = rtc.begin(&Wire);
  if (hasRtc && !rtc.lostPower()) {
    DateTime n = rtc.now();
    if (n.year() >= 2025) { struct timeval tv = {(time_t)n.unixtime(), 0}; settimeofday(&tv, nullptr); }
  }
  Wire.beginTransmission(0x3C);
  hasOled = Wire.endTransmission() == 0 && oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  Schedule *s = parseSchedule("/schedule.json");
  if (s) swapSchedule(s);
  refreshLicense(true);
  i2sInit();
  xTaskCreatePinnedToCore(audioTask, "audio", 6144, nullptr, 3, nullptr, 1);
  bleInit();
  if (cfg.ssid.isEmpty() && !sched->ok) startPortal();    // brand new: offer the setup page (Bluetooth works too)
  if (cfg.ssid.length()) { WiFi.mode(WIFI_STA); WiFi.begin(cfg.ssid.c_str(), cfg.pass.c_str()); }
  xTaskCreatePinnedToCore(netTask, "net", 12288, nullptr, 1, nullptr, 0);
  ringTick(true);
  Serial.printf("license: %s  rtc: %s  schedule: %s\n", licenseText().c_str(), hasRtc ? "yes" : "NO", sched->ok ? sched->version.c_str() : "none");
}

void loop() {
  static uint32_t lastRingCheck = 0, lastDraw = 0, bootDown = 0;
  if (millis() - lastRingCheck >= 250) { lastRingCheck = millis(); ringTick(false); }
  if (millis() - lastDraw >= 1000) { lastDraw = millis(); drawOled(); }
  if (digitalRead(PIN_BOOT) == LOW) {
    if (!bootDown) bootDown = millis();
    else if (millis() - bootDown > 3000) startPortal();
  } else bootDown = 0;
  if (portalOn) { dns.processNextRequest(); web.handleClient(); }
  delay(5);
}
