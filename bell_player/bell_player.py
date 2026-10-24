"""Office bell player (الجرس المركزي) — runs on the office computer connected to the PA speakers.

    BellPlayer.exe            first run: asks for the server and the key, then starts with Windows (no admin needed)
    BellPlayer.exe --setup    change the server / key
    BellPlayer.exe --test     ring one built-in tone now (check the speakers)
    BellPlayer.exe --status   what is cached and when the next ring is
    BellPlayer.exe --run      the player itself (what Windows start-up runs, hidden)
    BellPlayer.exe --remove   remove it from Windows start-up
    BellPlayer.exe --fingerprint   this computer's fingerprint (send it to get a license key)
    BellPlayer.exe --license       enter / renew the license key
    BellPlayer.exe --net-test      check the connection to the server (certificates, firewall)
    BellPlayer.exe --restart       restart the hidden player      --stop   stop it      --log   last log lines
    BellPlayer.exe --tools         (re)create the .bat shortcuts for all of these next to the program
    BellPlayer.exe --audio         choose the sound output (sound card) of this player and play a test tone
    BellPlayer.exe --add           install ANOTHER player on this computer (one per sound card / amplifier)
    BellPlayer.exe --uninstall     remove THIS player completely (start-up, task, running copy, its folder)
Double-click, wherever it is:
  • outside the installation (e.g. Downloads) → installs it (asks the folder, default C:\\BellPlayer) or UPDATES the
    installed copy (settings and license kept), closes old copies, tidies leftover files, then runs the check;
  • the installed copy → «check and fix»: server, player key (asks again only if refused), license (asks only if
    missing/expired), schedule, start-up, running — then a summary.
Several players on one computer (1.6): each installed folder (C:\\BellPlayer, C:\\BellPlayer2 …) is an independent
player with its own player key (= one «bell device» on the admin page, with its own timings and volume), its own
cache and start-up entry, and its own sound output (--audio). The license is per computer, so one key covers all.
WAV tones play on the chosen sound card; an MP3 tone plays on the Windows default output (upload WAV for a 2nd card).

Works offline: the schedule and every tone are cached next to the program (player_cache/). The server is needed
only to receive changes; when it is down or the internet is cut the bell keeps ringing from the cache.
Time: rings by the SERVER's clock, not this PC's. Every poll measures the difference between this PC and the server
(and the school time zone) and keeps it (player_cache/clock.json), so it stays right while offline too.
The server is reached at its public address, or on the school network (addresses it reports) when the internet is down.
License: a signed key (ECDSA P-256) tied to this computer's fingerprint; without a valid key it does not ring.
Python standard library only (also runs as `python bell_player.py`).
"""
import configparser
import hashlib
import json
import logging
import logging.handlers
import os
import platform
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

VERSION = '1.6'
DEFAULT_SERVER = 'https://5.iajaward.org'
POLL_SECONDS = 15
CATCH_UP_SECONDS = 90         # a ring missed by up to 90 s (PC busy/waking up) still plays; later ones are skipped
BASE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
CACHE = os.path.join(BASE, 'player_cache')
INI = os.path.join(BASE, 'bell_player.ini')
IS_WIN = os.name == 'nt'
log = logging.getLogger('bell_player')


# ── settings ───────────────────────────────────────────────────────────────
def load_ini():
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(INI, encoding='utf-8')
    s = cp['player'] if cp.has_section('player') else {}
    out = {'server': (s.get('server') or '').rstrip('/'), 'key': s.get('key') or '', 'license': s.get('license') or ''}
    if 'audio' in s:                                    # absent = never chosen; '' = Windows default output
        out['audio'] = s.get('audio') or ''
    return out


def save_ini(server, key, license_key=None, audio=None):
    cur = load_ini() if (license_key is None or audio is None) else {}
    if license_key is None:
        license_key = cur.get('license', '')
    if audio is None:
        audio = cur.get('audio')
    cp = configparser.ConfigParser(interpolation=None)
    cp['player'] = {'server': server.rstrip('/'), 'key': key, 'license': license_key}
    if audio is not None:
        cp['player']['audio'] = audio
    with open(INI, 'w', encoding='utf-8') as f:
        cp.write(f)


def setup_logging(console=True):
    os.makedirs(CACHE, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(os.path.join(BASE, 'bell_player.log'), maxBytes=1_000_000, backupCount=3, encoding='utf-8')
    h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    handlers = [h]
    if console and sys.stdout:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, handlers=handlers)


# ── time: the server's clock ───────────────────────────────────────────────
CLOCK = {'offset': 0.0, 'tz_min': None, 'synced': None, 'lan': []}


def clock_file():
    return os.path.join(CACHE, 'clock.json')


def load_clock():
    try:
        with open(clock_file(), encoding='utf-8') as f:
            CLOCK.update(json.load(f))
    except (OSError, ValueError):
        pass


def save_clock():
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(clock_file() + '.part', 'w', encoding='utf-8') as f:
            json.dump(CLOCK, f)
        os.replace(clock_file() + '.part', clock_file())
    except OSError:
        pass


def amman_now():
    """School time by the SERVER's clock: this PC's UTC + the measured offset to the server + the school's UTC
    offset reported by the server. Before the first sync: the Amman zone (or this PC's local time)."""
    utc = datetime.now(timezone.utc) + timedelta(seconds=CLOCK.get('offset') or 0)
    if CLOCK.get('tz_min') is not None:
        return (utc + timedelta(minutes=CLOCK['tz_min'])).replace(tzinfo=None)
    try:
        from zoneinfo import ZoneInfo
        return utc.astimezone(ZoneInfo('Asia/Amman')).replace(tzinfo=None)
    except Exception:   # noqa: BLE001
        return datetime.now() + timedelta(seconds=CLOCK.get('offset') or 0)


# ── license (ECDSA P-256, same key pair and key format as the desktop analysis program) ──
LICENSE_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE9hskm3ufgTN+cmD+Vhzv2w6OIiuw
RL/YjNVP/aJaT05+91ZOAv634CJt812D8dP5TtfS6XpAeHRX8ALMi95b9Q==
-----END PUBLIC KEY-----"""
_P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
_A = _P - 3
_N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
_G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
      0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)


def _ec_add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0] and (p[1] + q[1]) % _P == 0:
        return None
    if p == q:
        lam = (3 * p[0] * p[0] + _A) * pow(2 * p[1], -1, _P) % _P
    else:
        lam = (q[1] - p[1]) * pow(q[0] - p[0], -1, _P) % _P
    x = (lam * lam - p[0] - q[0]) % _P
    return x, (lam * (p[0] - x) - p[1]) % _P


def _ec_mul(k, p):
    r = None
    while k:
        if k & 1:
            r = _ec_add(r, p)
        p = _ec_add(p, p)
        k >>= 1
    return r


def _public_point(pem):
    import base64
    der = base64.b64decode(''.join(ln for ln in pem.strip().splitlines() if '-----' not in ln))
    raw = der[-65:]                                  # SubjectPublicKeyInfo ends with 04 || X || Y
    if raw[0] != 4:
        raise ValueError('bad public key')
    return int.from_bytes(raw[1:33], 'big'), int.from_bytes(raw[33:], 'big')


def _der_sig(sig):
    """ECDSA DER signature → (r, s)."""
    if len(sig) < 8 or sig[0] != 0x30:
        raise ValueError('bad signature')
    i = 2 if sig[1] < 0x80 else 2 + (sig[1] & 0x7f)
    out = []
    for _ in range(2):
        if sig[i] != 0x02:
            raise ValueError('bad signature')
        ln = sig[i + 1]
        out.append(int.from_bytes(sig[i + 2:i + 2 + ln], 'big'))
        i += 2 + ln
    return out[0], out[1]


def ecdsa_verify(message, signature, pem=None):
    try:
        r, s_ = _der_sig(signature)
        q = _public_point(pem or LICENSE_PUBLIC_KEY_PEM)
    except (ValueError, IndexError):
        return False
    if not (1 <= r < _N and 1 <= s_ < _N):
        return False
    z = int.from_bytes(hashlib.sha256(message).digest(), 'big')
    w = pow(s_, -1, _N)
    pt = _ec_add(_ec_mul(z * w % _N, _G), _ec_mul(r * w % _N, q))
    return pt is not None and pt[0] % _N == r


def check_license(key, fingerprint, today):
    """→ (valid, expired, 'PERMANENT' | 'YYYY-MM-DD' | None). Key format «EXP:signature» (EXP = YYYYMMDD or PERMANENT)."""
    import base64
    parts = (key or '').strip().split(':', 1)
    if len(parts) != 2:
        return False, False, None
    exp, sig = parts[0].strip().upper(), parts[1].strip()
    try:
        raw = base64.urlsafe_b64decode(sig + '=' * (-len(sig) % 4))
    except ValueError:
        return False, False, None
    if not ecdsa_verify(f'{fingerprint.strip().upper()}|{exp}'.encode(), raw):
        return False, False, None
    if exp == 'PERMANENT':
        return True, False, 'PERMANENT'
    try:
        d = datetime.strptime(exp, '%Y%m%d').date()
    except ValueError:
        return False, False, None
    return True, today > d, d.isoformat()


def _run_quiet(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return (r.stdout or '').strip()
    except Exception:   # noqa: BLE001
        return ''


def machine_fingerprint():
    """16 characters tied to this computer: the motherboard serial (else the Windows machine id, else the network
    card). Different from the analysis program's fingerprint on the same PC, so its keys don't unlock the bell."""
    raw = ''
    if IS_WIN:
        raw = _run_quiet(['powershell', '-NoProfile', '-Command', '(Get-CimInstance -ClassName Win32_BaseBoard).SerialNumber'])
        if not raw or raw.lower() in ('none', 'to be filled by o.e.m.', 'default string'):
            lines = [ln.strip() for ln in _run_quiet(['wmic', 'baseboard', 'get', 'serialnumber']).splitlines()
                     if ln.strip() and 'serialnumber' not in ln.lower()]
            raw = lines[0] if lines else ''
        if not raw or raw.lower() in ('none', 'to be filled by o.e.m.', 'default string'):
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography') as k:
                    raw = 'guid:' + winreg.QueryValueEx(k, 'MachineGuid')[0]
            except OSError:
                raw = ''
    cache = os.path.join(CACHE, 'fingerprint.txt')
    if raw:
        fp = hashlib.sha256(('BELL-PLAYER|' + raw).encode()).hexdigest()[:16].upper()
    else:
        try:
            with open(cache, encoding='utf-8') as f:
                return f.read().strip()
        except OSError:
            import uuid
            fp = hashlib.sha256(('BELL-PLAYER|mac:' + str(uuid.getnode())).encode()).hexdigest()[:16].upper()
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(cache, 'w', encoding='utf-8') as f:
            f.write(fp)
    except OSError:
        pass
    return fp


# ── server ─────────────────────────────────────────────────────────────────
_SSL = None


def ssl_context():
    """Trust BOTH the Windows certificate store (incl. an antivirus / school firewall that inspects HTTPS) and the
    bundled Mozilla list (certifi) — a fresh Windows often lacks the root certificate until a browser fetches it,
    which gives CERTIFICATE_VERIFY_FAILED in programs."""
    global _SSL
    if _SSL is None:
        import ssl
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except Exception:   # noqa: BLE001 — running as a plain script without certifi: the system store only
            pass
        _SSL = ctx
    return _SSL


def http(method, url, key, body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'X-Bell-Key': key, 'User-Agent': f'BellPlayer/{VERSION}', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context() if url.startswith('https') else None) as r:
        return r.status, r.read()


def tone_file(key, url):
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or '.wav'
    if ext.lower() not in ('.wav', '.mp3'):
        ext = '.wav'
    tag = hashlib.sha1(url.encode()).hexdigest()[:10]
    safe = ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in key)
    return os.path.join(CACHE, 'tones', f'{safe}-{tag}{ext}')


def download(server, key, url, dest):
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    full = url if url.startswith('http') else server + url
    status, data = http('GET', full, key, timeout=60)
    if status != 200 or not data:
        raise IOError(f'tone download failed ({status})')
    tmp = dest + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, dest)
    return dest


def load_cached_schedule():
    try:
        with open(os.path.join(CACHE, 'schedule.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_schedule(body):
    os.makedirs(CACHE, exist_ok=True)
    tmp = os.path.join(CACHE, 'schedule.json.part')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(body, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(CACHE, 'schedule.json'))


# ── audio ──────────────────────────────────────────────────────────────────
AUDIO = {'device': '', 'warned_mp3': False, 'missing': False}   # device = output name ('' = Windows default)


def audio_devices():
    """[(id, name)] of the sound outputs (Windows waveOut devices; names are cut at 31 characters)."""
    if not IS_WIN:
        return []
    import ctypes
    from ctypes import wintypes

    class WAVEOUTCAPSW(ctypes.Structure):
        _fields_ = [('wMid', wintypes.WORD), ('wPid', wintypes.WORD), ('vDriverVersion', wintypes.UINT),
                    ('szPname', wintypes.WCHAR * 32), ('dwFormats', wintypes.DWORD), ('wChannels', wintypes.WORD),
                    ('wReserved1', wintypes.WORD), ('dwSupport', wintypes.DWORD)]
    w = ctypes.windll.winmm
    out = []
    for i in range(w.waveOutGetNumDevs()):
        caps = WAVEOUTCAPSW()
        if w.waveOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            out.append((i, caps.szPname))
    return out


def resolve_device(name):
    """Output name → current device id (ids change when a USB card is plugged elsewhere), or None."""
    if not name:
        return None
    devs = audio_devices()
    for i, n in devs:
        if n == name:
            return i
    for i, n in devs:
        if n.lower().startswith(name.lower()[:24]):
            return i
    return None


def audio_label():
    d = AUDIO['device']
    if not d:
        return 'Windows default'
    return d + (' (NOT FOUND - Windows default used)' if AUDIO['missing'] else '')


def play_wav_on(path, dev_id, volume=100):
    """PCM WAV → one chosen output (waveOut), with the volume applied to the samples. Raises on any problem."""
    import array
    import ctypes
    import wave
    from ctypes import wintypes
    with wave.open(path, 'rb') as wf:
        nch, sw, rate = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    f = max(0, min(100, int(volume))) / 100.0
    if f < 1 and sw == 2:
        a = array.array('h', frames)
        a = array.array('h', (int(x * f) for x in a))
        frames = a.tobytes()
    elif f < 1 and sw == 1:
        frames = bytes(int(128 + (b - 128) * f) for b in frames)

    class WAVEFORMATEX(ctypes.Structure):
        _fields_ = [('wFormatTag', wintypes.WORD), ('nChannels', wintypes.WORD), ('nSamplesPerSec', wintypes.DWORD),
                    ('nAvgBytesPerSec', wintypes.DWORD), ('nBlockAlign', wintypes.WORD),
                    ('wBitsPerSample', wintypes.WORD), ('cbSize', wintypes.WORD)]

    class WAVEHDR(ctypes.Structure):
        _fields_ = [('lpData', ctypes.c_char_p), ('dwBufferLength', wintypes.DWORD), ('dwBytesRecorded', wintypes.DWORD),
                    ('dwUser', ctypes.c_size_t), ('dwFlags', wintypes.DWORD), ('dwLoops', wintypes.DWORD),
                    ('lpNext', ctypes.c_void_p), ('reserved', ctypes.c_size_t)]
    w = ctypes.windll.winmm
    fmt = WAVEFORMATEX(1, nch, rate, rate * nch * sw, nch * sw, sw * 8, 0)
    hwo = wintypes.HANDLE()
    if w.waveOutOpen(ctypes.byref(hwo), dev_id, ctypes.byref(fmt), 0, 0, 0) != 0:
        raise RuntimeError('cannot open the sound output')
    buf = ctypes.create_string_buffer(frames, len(frames))
    hdr = WAVEHDR(ctypes.cast(buf, ctypes.c_char_p), len(frames), 0, 0, 0, 0, None, 0)
    try:
        if w.waveOutPrepareHeader(hwo, ctypes.byref(hdr), ctypes.sizeof(hdr)) != 0:
            raise RuntimeError('cannot prepare the sound')
        try:
            if w.waveOutWrite(hwo, ctypes.byref(hdr), ctypes.sizeof(hdr)) != 0:
                raise RuntimeError('cannot play the sound')
            limit = time.time() + len(frames) / float(rate * nch * sw) + 5
            while not (hdr.dwFlags & 1) and time.time() < limit:          # WHDR_DONE
                time.sleep(0.05)
        finally:
            w.waveOutReset(hwo)
            w.waveOutUnprepareHeader(hwo, ctypes.byref(hdr), ctypes.sizeof(hdr))
    finally:
        w.waveOutClose(hwo)
    return True


def play_file(path, volume=100):
    """Plays one file to the end. Windows: the chosen sound card (WAV) or MCI on the default output (WAV and MP3,
    with volume); elsewhere a command-line player."""
    if IS_WIN and AUDIO['device']:
        dev = resolve_device(AUDIO['device'])
        if dev is None:
            if not AUDIO['missing']:
                log.error('sound output «%s» not found (unplugged?) - playing on the Windows default output', AUDIO['device'])
            AUDIO['missing'] = True
        else:
            AUDIO['missing'] = False
            if path.lower().endswith('.wav'):
                try:
                    return play_wav_on(path, dev, volume)
                except Exception as e:   # noqa: BLE001 — e.g. a compressed WAV: fall back to MCI
                    log.warning('could not play %s on «%s» (%s) - Windows default output used', os.path.basename(path),
                                AUDIO['device'], e)
            elif not AUDIO['warned_mp3']:
                AUDIO['warned_mp3'] = True
                log.warning('MP3 tones play on the Windows default output, not on «%s» - upload WAV tones for this '
                            'sound card, or set BellPlayer.exe to it in Windows Settings > Sound > App volume', AUDIO['device'])
    if IS_WIN:
        import ctypes
        mci = ctypes.windll.winmm.mciSendStringW
        alias = f'bell{int(time.time() * 1000) % 100000}'
        buf = ctypes.create_unicode_buffer(256)
        if mci(f'open "{path}" type mpegvideo alias {alias}', buf, 255, 0) == 0:
            try:
                mci(f'setaudio {alias} volume to {max(0, min(1000, int(volume) * 10))}', buf, 255, 0)
                if mci(f'play {alias} wait', buf, 255, 0) == 0:
                    return True
            finally:
                mci(f'close {alias}', buf, 255, 0)
        if path.lower().endswith('.wav'):          # fallback without volume control
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME)
            return True
        raise RuntimeError('MCI could not play ' + path)
    for cmd in (['afplay', path], ['paplay', path], ['aplay', '-q', path]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, check=False)
            return True
    log.info('(no audio player on this system) would play %s', path)
    return False


class Player:
    def __init__(self, cfg):
        self.primary, self.key = cfg['server'], cfg['key']
        self.server = self.primary                      # the address that answered last (public or school network)
        self.license_key = cfg.get('license', '')
        load_clock()
        self.schedule = load_cached_schedule()
        self.lock = threading.Lock()
        self.audio = queue.Queue()
        self.fired = set()
        self.last_ring = ''
        self.last_test = ''
        self.error = ''
        self.clock_drift = None
        self.fp = None
        self.lic = (False, False, None)
        self.lic_day = None
        self._lic_t = 0
        self._clock_saved = 0

    # license ----------------------------------------------------------------
    def license_state(self):
        """Checked once per school day (by the server's date), so an expiry stops the bell the next morning. While
        not licensed it re-reads bell_player.ini every 30 s, so `BellPlayer.exe --license` works without a restart."""
        day = amman_now().date()
        bad = not (self.lic[0] and not self.lic[1])
        if self.lic_day != day or (bad and time.time() - self._lic_t > 30):
            if os.path.isfile(INI):
                self.license_key = load_ini().get('license') or self.license_key
            if self.fp is None:
                self.fp = machine_fingerprint()
            before = self.lic
            self.lic = check_license(self.license_key, self.fp, day)
            self._lic_t = time.time()
            if self.lic != before or self.lic_day != day:
                if not self.lic[0]:
                    log.error('NO VALID LICENSE for fingerprint %s — the bell will not ring. Run: BellPlayer.exe --license', self.fp)
                elif self.lic[1]:
                    log.error('the license expired on %s — the bell will not ring. Run: BellPlayer.exe --license', self.lic[2])
                else:
                    log.info('licensed (%s)', self.lic[2])
            self.lic_day = day
        return self.lic

    def licensed(self):
        ok, expired, _ = self.license_state()
        return ok and not expired

    def license_text(self):
        ok, expired, exp = self.license_state()
        return 'invalid' if not ok else (f'expired:{exp}' if expired else exp)

    # server -----------------------------------------------------------------
    def bases(self):
        """Try the address that worked last, then the server's school-network addresses, then the public one."""
        out = [self.server] + [u for u in (CLOCK.get('lan') or [])] + [self.primary]
        seen = []
        for u in out:
            if u and u.rstrip('/') not in seen:
                seen.append(u.rstrip('/'))
        return seen

    def call(self, method, path, body=None):
        last = None
        for base in self.bases():
            try:
                t0 = time.time()
                status, data = http(method, base + path, self.key, body, timeout=20 if base == self.primary else 4)
                self.server = base
                return data, t0, time.time()
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise
                last = e                                 # something else answered on that address — try the next
            except Exception as e:   # noqa: BLE001
                last = e
        raise last or IOError('no server address')

    # sync -------------------------------------------------------------------
    def cached_ok(self):
        s = self.schedule
        return bool(s) and all(os.path.isfile(tone_file(k, t['url'])) for k, t in s.get('tones', {}).items())

    def refresh(self, version_hint=None):
        cur = (self.schedule or {}).get('version')
        if version_hint and version_hint == cur and self.cached_ok():
            return
        data, _t0, _t1 = self.call('GET', '/api/central/schedule' + (f'?v={cur}' if cur and self.cached_ok() else ''))
        body = json.loads(data.decode('utf-8'))
        if body.get('unchanged'):
            return
        for k, t in body.get('tones', {}).items():      # download every tone BEFORE switching to the new schedule
            download(self.server, self.key, t['url'], tone_file(k, t['url']))
        save_schedule(body)
        with self.lock:
            self.schedule = body
        log.info('schedule updated (version %s, %d tones)', body.get('version'), len(body.get('tones', {})))

    def sync_clock(self, r, t0, t1):
        """Offset = server time − this PC's time, measured at the middle of the request."""
        if 'server_ms' not in r or t1 - t0 > 5:
            return
        offset = r['server_ms'] / 1000.0 + (t1 - t0) / 2 - t1
        changed = abs(offset - (CLOCK.get('offset') or 0)) > 0.5 or CLOCK.get('tz_min') != r.get('tz_offset_min') \
            or (r.get('lan') and r['lan'] != CLOCK.get('lan'))
        if abs(offset - (CLOCK.get('offset') or 0)) > 5 and CLOCK.get('synced'):
            log.info('server clock: this PC is %+.1f s off — ringing by the server time', -offset)
        CLOCK['offset'] = offset
        if r.get('tz_offset_min') is not None:
            CLOCK['tz_min'] = int(r['tz_offset_min'])
        if r.get('lan'):
            CLOCK['lan'] = list(r['lan'])
        CLOCK['synced'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.clock_drift = int(round(-offset))
        if changed or time.time() - self._clock_saved > 600:
            save_clock()
            self._clock_saved = time.time()

    def poll(self):
        st = {'host': socket.gethostname(), 'version': (self.schedule or {}).get('version'), 'player': VERSION,
              'last_ring': self.last_ring, 'next_ring': self.next_ring_text(), 'clock_drift': self.clock_drift,
              'cached': self.cached_ok(), 'error': self.error[:300], 'license': self.license_text(),
              'fingerprint': self.fp, 'via': ('internet' if self.server == self.primary else 'school network ') + self.server,
              'audio': audio_label() + (f' - {BASE}' if inst_tag() else '')}
        data, t0, t1 = self.call('POST', '/api/central/poll', st)
        r = json.loads(data.decode('utf-8'))
        self.sync_clock(r, t0, t1)
        self.refresh(r.get('version'))
        t = r.get('test')
        if t and t.get('id') != self.last_test:
            self.last_test = t['id']
            if not self.licensed():
                log.warning('test ring ignored — no valid license')
                return
            try:
                path = download(self.server, self.key, t['url'], tone_file(t['tone'], t['url']))
                self.audio.put(([path], 'test'))
                log.info('test ring requested from the admin page: %s', t['tone'])
            except Exception as e:   # noqa: BLE001
                log.warning('test ring: %s', e)

    def sync_loop(self):
        backoff = POLL_SECONDS
        while True:
            try:
                self.poll()
                if self.error:
                    log.info('server reachable again (%s)', self.server)
                self.error, backoff = '', POLL_SECONDS
            except urllib.error.HTTPError as e:
                self.error = f'HTTP {e.code}'
                if e.code == 401:
                    log.error('the server refused the key — a new key was created? run: BellPlayer.exe --setup')
                    backoff = 300
            except Exception as e:   # noqa: BLE001 — offline: keep ringing from the cache (and the last server offset)
                if not self.error:
                    log.warning('server not reachable (%s) — ringing from the saved schedule by the last known server time', e)
                self.error = str(e)
                backoff = min(backoff * 2, 120)
            time.sleep(backoff)

    # rings ------------------------------------------------------------------
    def today_slots(self, now):
        s = self.schedule or {}
        if not s.get('enabled', True) or now.strftime('%Y-%m-%d') in (s.get('skip_dates') or []):
            return []
        return s.get('days', {}).get(str(now.weekday()), [])

    def next_ring_text(self):
        now = amman_now()
        for add in range(8):
            day = now + timedelta(days=add)
            for slot in self.today_slots(day):
                if add or slot['t'] > now.strftime('%H:%M'):
                    return f"{day.strftime('%Y-%m-%d')} {slot['t']}"
        return ''

    def due(self, now):
        """Slots whose time has come in the last CATCH_UP_SECONDS and were not played yet."""
        out = []
        for slot in self.today_slots(now):
            h, m = map(int, slot['t'].split(':'))
            at = now.replace(hour=h, minute=m, second=0, microsecond=0)
            k = f"{now.strftime('%Y-%m-%d')} {slot['t']}"
            if 0 <= (now - at).total_seconds() <= CATCH_UP_SECONDS and k not in self.fired:
                out.append((k, slot))
        return out

    def ring_loop(self):
        day = None
        # On start-up don't replay a ring that already happened more than a few seconds ago.
        start = amman_now()
        for slot in self.today_slots(start):
            h, m = map(int, slot['t'].split(':'))
            if (start - start.replace(hour=h, minute=m, second=0, microsecond=0)).total_seconds() > 5:
                self.fired.add(f"{start.strftime('%Y-%m-%d')} {slot['t']}")
        while True:
            now = amman_now()
            if day != now.date():
                day = now.date()
                self.fired = {k for k in self.fired if k.startswith(now.strftime('%Y-%m-%d'))}
            with self.lock:
                due = self.due(now)
                tones = (self.schedule or {}).get('tones', {})
            for k, slot in due:
                self.fired.add(k)
                if not self.licensed():
                    log.warning('RING %s skipped — no valid license', k)
                    continue
                paths = []
                for r in slot['rings']:
                    t = tones.get(r['tone'])
                    if t and os.path.isfile(tone_file(r['tone'], t['url'])):
                        paths.append(tone_file(r['tone'], t['url']))
                    else:
                        log.error('tone %s is not cached — not played', r['tone'])
                if paths:
                    self.audio.put((paths, k))
                    log.info('RING %s: %s', k, ' + '.join(f"{r['tone']} ({', '.join(r['groups'])})" for r in slot['rings']))
            time.sleep(0.5)

    def audio_loop(self):
        while True:
            paths, what = self.audio.get()
            vol = (self.schedule or {}).get('volume', 100)
            for i, p in enumerate(paths):
                try:
                    play_file(p, vol)
                except Exception as e:   # noqa: BLE001
                    log.error('could not play %s: %s', p, e)
                if i < len(paths) - 1:
                    time.sleep(0.6)
            if what != 'test':
                self.last_ring = what

    def run(self):
        log.info('Bell player %s started — server %s, cache %s, sound output: %s', VERSION, self.server, CACHE, audio_label())
        if not self.schedule:
            log.warning('no saved schedule yet — waiting for the server')
        for target in (self.sync_loop, self.audio_loop):
            threading.Thread(target=target, daemon=True).start()
        self.ring_loop()


# ── single instance ─────────────────────────────────────────────────────────
def single_instance():
    """One running copy per installed folder (several folders = several players on this computer)."""
    tag = hashlib.sha1(os.path.normcase(os.path.abspath(BASE)).encode()).hexdigest()[:10]
    if IS_WIN:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, False, 'Global\\IAJ_BellPlayer_' + tag)
        return ctypes.windll.kernel32.GetLastError() != 183          # ERROR_ALREADY_EXISTS
    try:
        s = socket.socket()
        s.bind(('127.0.0.1', 47811 + int(tag[:4], 16) % 1000))
        single_instance.sock = s
        return True
    except OSError:
        return False


# ── Windows start-up ────────────────────────────────────────────────────────
def startup_dir():
    return os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')


def _vbs_target(vbs):
    try:
        import re
        m = re.search(r'FileExists\("([^"]+)"\)', open(vbs, encoding='utf-8').read())
        return m.group(1) if m else None
    except OSError:
        return None


_TAG = {}


def inst_tag():
    """'' for the first player of this computer (start-up file BellPlayer.vbs, task «BELL Office Player»);
    otherwise a short name of its folder, used in the start-up file and task names of the other players."""
    if BASE in _TAG:
        return _TAG[BASE]
    main_vbs = os.path.join(startup_dir(), 'BellPlayer.vbs')
    target = _vbs_target(main_vbs)
    if target and os.path.isfile(target) and not _same(os.path.dirname(target), BASE):
        tag = ''.join(ch for ch in os.path.basename(os.path.normpath(BASE)) if ch.isalnum())[:20] or \
            hashlib.sha1(BASE.encode()).hexdigest()[:6]
    else:
        tag = ''
    _TAG[BASE] = tag
    return tag


def vbs_path():
    t = inst_tag()
    return os.path.join(startup_dir(), f'BellPlayer-{t}.vbs' if t else 'BellPlayer.vbs')


def task_name():
    t = inst_tag()
    return f'BELL Office Player ({t})' if t else 'BELL Office Player'


def all_installs():
    """Every installed BellPlayer.exe on this computer (one per start-up file), the first player first."""
    out = []
    try:
        names = sorted(f for f in os.listdir(startup_dir()) if f.lower().startswith('bellplayer') and f.lower().endswith('.vbs'))
    except OSError:
        names = []
    names.sort(key=lambda f: f.lower() != 'bellplayer.vbs')
    for f in names:
        t = _vbs_target(os.path.join(startup_dir(), f))
        if t and os.path.isfile(t) and not any(_same(t, x) for x in out):
            out.append(t)
    return out


def pids_of(exe):
    """Running BellPlayer.exe processes started from this exact file (not the other players), except this one."""
    if not IS_WIN:
        return []
    out = _run_quiet(['powershell', '-NoProfile', '-Command',
                      "Get-CimInstance Win32_Process -Filter \"Name='BellPlayer.exe'\" | "
                      "ForEach-Object { \"$($_.ProcessId)|$($_.ExecutablePath)\" }"])
    res = []
    for ln in out.splitlines():
        pid, _, path = ln.partition('|')
        if pid.strip().isdigit() and path.strip() and _same(path.strip(), exe) and int(pid) != os.getpid():
            res.append(int(pid))
    return res


def kill_same(exe):
    for pid in pids_of(exe):
        subprocess.run(['taskkill', '/f', '/pid', str(pid)], capture_output=True)


def launch_cmd():
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}" --run'
    pyw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    return f'"{pyw if os.path.isfile(pyw) else sys.executable}" "{os.path.abspath(__file__)}" --run'


def _unblock(path):
    """Remove Windows' 'downloaded from the internet' mark — otherwise the hidden start-up launch is stopped by
    SmartScreen ('The operation was canceled by the user', 800704C7) because its prompt cannot be shown."""
    try:
        os.remove(path + ':Zone.Identifier')
    except OSError:
        pass


def install_startup():
    if not IS_WIN:
        return
    exe = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
    _unblock(exe)
    vbs = vbs_path()
    os.makedirs(startup_dir(), exist_ok=True)
    # Hidden window, starts at every sign-in. Never shows an error box: if the program is missing or blocked it
    # just does nothing (the log and the admin page show the player as offline).
    cmd = launch_cmd().replace('"', '""')
    exe_v = exe.replace('"', '""')
    with open(vbs, 'w', encoding='utf-8') as f:
        f.write('On Error Resume Next\n'
                'Set fso = CreateObject("Scripting.FileSystemObject")\n'
                f'If fso.FileExists("{exe_v}") Then CreateObject("WScript.Shell").Run "{cmd}", 0, False\n')
    # Close the copy of THIS player that is already running (it may hold an old key) — not the other players.
    if getattr(sys, 'frozen', False):
        kill_same(sys.executable)
    # Every 5 minutes: start it again if it was closed or crashed (a second copy exits at once).
    subprocess.run(['schtasks', '/create', '/f', '/tn', task_name(), '/sc', 'minute', '/mo', '5',
                    '/tr', f'wscript.exe //B //Nologo "{vbs}"'], capture_output=True)
    subprocess.Popen(['wscript.exe', '//B', '//Nologo', vbs])


def uninstall():
    """--uninstall: this player only — start-up file, scheduled task, running copy, then its whole folder
    (deleted a few seconds after this window closes). The license key is kept in Documents for a reinstall."""
    print(f'\n  Uninstall the bell player in {BASE}')
    others = [os.path.dirname(x) for x in installs() if not _same(os.path.dirname(x), BASE)]
    if others:
        print('  The other players on this computer stay: ' + ', '.join(others))
    if input('  This player will stop ringing. Type YES to uninstall: ').strip() != 'YES':
        print('  cancelled - nothing changed')
        return False
    lic = load_ini().get('license') or ''
    if lic and not others:
        try:
            docs = os.path.join(os.path.expanduser('~'), 'Documents')
            os.makedirs(docs, exist_ok=True)
            with open(os.path.join(docs, 'BellPlayer-license.txt'), 'w', encoding='utf-8') as f:
                f.write(f'Bell player license key (fingerprint {machine_fingerprint()}):\n{lic}\n')
            print(f'  [OK] license key kept in {docs}\\BellPlayer-license.txt (for a reinstall)')
        except OSError:
            pass
    remove_startup()
    print('  [OK] stopped and removed from Windows start-up')
    if IS_WIN and getattr(sys, 'frozen', False):
        folder = BASE.replace('"', '')
        # the running exe cannot delete itself: a hidden cmd removes the folder after this process exits
        # (retries for up to 2 minutes, so it also works while this window waits for Enter)
        subprocess.Popen(f'cmd /c for /l %i in (1,1,60) do (ping -n 3 127.0.0.1 >nul & rmdir /s /q "{folder}" 2>nul'
                         f' & if not exist "{folder}" exit)', shell=True,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), cwd=os.path.dirname(folder) or None)
        print(f'  [OK] the folder {folder} is deleted as soon as this window closes')
    print('  Also remove this device on the admin page: Central bell > Bell devices > Delete.')
    return True


def remove_startup():
    if not IS_WIN:
        return
    try:
        os.remove(vbs_path())
    except OSError:
        pass
    subprocess.run(['schtasks', '/delete', '/f', '/tn', task_name()], capture_output=True)
    if getattr(sys, 'frozen', False):
        kill_same(sys.executable)


TOOLS = [
    ('1 - Check and fix (start here).bat', '--check', 'Checks everything and asks only for what is missing'),
    ('2 - Setup (server, player key, license).bat', '--setup', 'Server address, player key and license key'),
    ('3 - License (enter or renew).bat', '--license', 'Shows the fingerprint, enter a new license key'),
    ('4 - Status.bat', '--status', 'Server, schedule, next ring, license, clock'),
    ('5 - Network test.bat', '--net-test', 'Checks the connection to the server'),
    ('6 - Test speakers.bat', '--test', 'Plays one tone on this computer now'),
    ('7 - Fingerprint.bat', '--fingerprint', 'This computer\'s fingerprint (for the license)'),
    ('8 - Restart player.bat', '--restart', 'Restarts the hidden player'),
    ('9 - Stop player.bat', '--stop', 'Stops the player until the next sign-in (it does NOT ring)'),
    ('10 - Show log.bat', '--log', 'Last lines of bell_player.log'),
    ('11 - Remove from Windows start-up.bat', '--remove', 'Stops it and removes it from start-up'),
    ('12 - Sound output (sound card).bat', '--audio', 'Choose the speakers / sound card of this player'),
    ('13 - Add another player (2nd sound card).bat', '--add', 'Installs one more player on this computer'),
    ('14 - Uninstall this player.bat', '--uninstall', 'Removes this player and its folder from this computer'),
]


def write_tools(folder=None):
    """One .bat per command next to BellPlayer.exe, so nobody has to type commands."""
    folder = folder or BASE
    exe = os.path.basename(sys.executable) if getattr(sys, 'frozen', False) else 'BellPlayer.exe'
    keep = {n for n, _a, _w in TOOLS}
    try:
        for f in os.listdir(folder):          # shortcuts of an older version (other numbering)
            if f.endswith('.bat') and f[:1].isdigit() and ' - ' in f and f not in keep:
                os.remove(os.path.join(folder, f))
    except OSError:
        pass
    for name, arg, what in TOOLS:
        try:
            with open(os.path.join(folder, name), 'w', encoding='ascii', errors='replace', newline='\r\n') as f:
                f.write(f'@echo off\ntitle Bell player - {what}\ncd /d "%~dp0"\n"%~dp0{exe}" {arg}\necho.\npause\n')
        except OSError:
            pass
    try:
        with open(os.path.join(folder, 'README.txt'), 'w', encoding='utf-8', newline='\r\n') as f:
            f.write('Bell player - double-click a .bat file:\n\n' + '\n'.join(f'  {n:<44} {w}' for n, _a, w in TOOLS) +
                    '\n\nUpdate: download the new BellPlayer.exe and run it - it updates every player on this computer.\n'
                    'Two sound cards: 13 - Add another player, give it its own player key, then 12 - Sound output.\n')
    except OSError:
        pass


def installed_exe():
    """The BellPlayer.exe that Windows start-up launches (the first player), or None."""
    t = _vbs_target(os.path.join(startup_dir(), 'BellPlayer.vbs'))
    return t if t and os.path.isfile(t) else None


DEFAULT_DIR = r'C:\BellPlayer'


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def find_install():
    """The installed BellPlayer.exe: the one Windows start-up launches, else C:\\BellPlayer\\BellPlayer.exe, else None."""
    cur = installed_exe()
    if cur:
        return cur
    d = os.path.join(DEFAULT_DIR, 'BellPlayer.exe')
    return d if os.path.isfile(d) else None


def installs():
    """All installed players (the first one first), including C:\\BellPlayer when it is not in start-up yet."""
    out = all_installs()
    d = os.path.join(DEFAULT_DIR, 'BellPlayer.exe')
    if os.path.isfile(d) and not any(_same(d, x) for x in out):
        out.insert(0, d)
    return out


def next_folder():
    n = 2
    while os.path.exists(f'{DEFAULT_DIR}{n}'):
        n += 1
    return f'{DEFAULT_DIR}{n}'


def kill_others(exe=None):
    """Stop the running copies of one installed player (exe), or of every BellPlayer.exe."""
    if IS_WIN:
        if exe:
            kill_same(exe)
        else:
            subprocess.run(['taskkill', '/f', '/fi', f'PID ne {os.getpid()}', '/im', 'BellPlayer.exe'], capture_output=True)
        time.sleep(1.5)


def _copy_exe(me, dest):
    kill_others(dest)
    for i in range(6):
        try:
            shutil.copyfile(me, dest)
            return True
        except OSError:
            kill_others(dest if i < 2 else None)      # still locked: stop every copy (start-up restarts them)
    print(f'  [X] could not write {dest} - is it open somewhere? Restart the computer and run this again.')
    return False


def install_flow(add=False):
    """Run from Downloads (or anywhere that is not the installation): install or update every player on this
    computer, or (add=True / answer A) install one more player for another sound card; clean up, then check."""
    me = os.path.abspath(sys.executable)
    here = os.path.dirname(me)
    found = installs()
    print(f'\n  Bell player {VERSION}\n')
    if found and not add:
        print('  Installed on this computer:')
        for i, x in enumerate(found, 1):
            print(f'    {i}. {os.path.dirname(x)}')
        ans = input('  Enter = update them;  A = add ANOTHER player (for another sound card): ').strip().lower()
        add = ans == 'a'
    if add:
        folder = input(f'  Folder for the new player [{next_folder()}]: ').strip().strip('"') or next_folder()
        if any(_same(os.path.join(folder, 'BellPlayer.exe'), x) for x in found):
            print('  [X] a player is already installed in that folder - choose another folder')
            return False
        targets = [folder]
    elif found:
        targets = [os.path.dirname(x) for x in found]
        print('  Updating ' + ', '.join(targets))
    else:
        targets = [input(f'  Install folder [{DEFAULT_DIR}]: ').strip().strip('"') or DEFAULT_DIR]
    for folder in targets:
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            print(f'  [X] cannot create {folder}: {e}')
            return False
        if _same(os.path.join(folder, 'BellPlayer.exe'), me):
            continue
        if not _copy_exe(me, os.path.join(folder, 'BellPlayer.exe')):
            return False
    folder = targets[0]
    cur = found and not add
    # settings: keep the installed ones; bring along a settings file left next to this download if there are none
    stray_ini = os.path.join(here, 'bell_player.ini')
    if not any(_same(here, t) for t in targets):
        if not add and os.path.isfile(stray_ini) and not os.path.isfile(os.path.join(folder, 'bell_player.ini')):
            shutil.copyfile(stray_ini, os.path.join(folder, 'bell_player.ini'))
            if os.path.isdir(os.path.join(here, 'player_cache')) and not os.path.isdir(os.path.join(folder, 'player_cache')):
                shutil.copytree(os.path.join(here, 'player_cache'), os.path.join(folder, 'player_cache'))
        for name in ['bell_player.ini', 'README.txt'] + [n for n, _a, _w in TOOLS] + \
                [f for f in os.listdir(here) if f.startswith('bell_player.log')]:
            try:
                os.remove(os.path.join(here, name))
            except OSError:
                pass
        shutil.rmtree(os.path.join(here, 'player_cache'), ignore_errors=True)
    ok = True
    for folder in targets:
        print(f'  [OK] {"updated" if cur else "installed"} in {folder}')
        ok = subprocess.call([os.path.join(folder, 'BellPlayer.exe'), '--check']) == 0 and ok
    return ok


def try_server(server, key):
    """→ ('ok', rings a week) | ('key', '') | ('net', error). Also takes the server clock (for the license date)."""
    p = Player({'server': server, 'key': key, 'license': ''})
    try:
        data, t0, t1 = p.call('POST', '/api/central/poll', {'host': socket.gethostname(), 'player': VERSION})
        p.sync_clock(json.loads(data.decode('utf-8')), t0, t1)
        save_clock()
        return 'ok', ''
    except urllib.error.HTTPError as e:
        return ('key', '') if e.code == 401 else ('net', f'HTTP {e.code}')
    except Exception as e:   # noqa: BLE001
        return 'net', str(e)


def doctor(force=False):
    """Double-click on the installed BellPlayer.exe (and «Check and fix»): checks everything, asks only for what is
    missing or refused, then makes sure it starts with Windows and is running. force = ask everything (Setup)."""
    print(f'\n  Bell player {VERSION} - checking this computer ({BASE})\n')
    os.makedirs(CACHE, exist_ok=True)
    cfg = load_ini()
    others = other_settings()
    server, key, lic = cfg['server'] or (others[0]['server'] if others and others[0]['server'] else DEFAULT_SERVER), \
        cfg['key'], cfg['license']
    if inst_tag() and not cfg['key']:
        print('  This is an ADDITIONAL player on this computer: on the admin page add a new bell device for it\n'
              '  (Central bell > Bell devices > Add), choose its timings, and paste its player key here.\n')
    if force or not cfg['server']:
        server = input(f'  Server address [{server}]: ').strip() or server
    if not server.startswith('http'):
        server = 'https://' + server
    server = server.rstrip('/')
    if force and key:
        key = input('  Player key [Enter = keep]: ').strip() or key
    # 1) server + player key
    state, why = 'nokey', ''
    while True:
        if not key:
            key = input('  Player key (admin page > Central bell > Bell devices > this device > New key): ').strip()
            if not key:
                print('  [!] no player key - the bell keeps its saved schedule but gets no changes. Later: 1 - Check and fix.bat')
                break
        state, why = try_server(server, key)
        if state == 'ok':
            print(f'  [OK] server {server} - player key accepted')
            break
        if state == 'key':
            print('  [X] the server does not accept this player key (a new key was made for this device, or it was removed).')
            key = ''
            continue
        print(f'  [!] the server is not reachable right now ({why}) - saved; it keeps trying by itself')
        break
    save_ini(server, key, lic)
    # 2) license
    load_clock()
    fp = machine_fingerprint()
    ok, expired, exp = check_license(lic, fp, amman_now().date())
    if not (ok and not expired):                # the license is per computer: take it from another player here
        for o in others:
            v, e, _x = check_license(o['license'], fp, amman_now().date())
            if v and not e:
                lic = o['license']
                cfg2 = load_ini()
                save_ini(cfg2['server'], cfg2['key'], lic)
                print(f'  [OK] license taken from the player in {o["folder"]}')
                ok, expired, exp = check_license(lic, fp, amman_now().date())
                break
    if ok and not expired and not force:
        print('  [OK] license ' + ('permanent' if exp == 'PERMANENT' else f'valid until {exp}') + f'  (fingerprint {fp})')
    else:
        ask_license()
    # 3) sound output (asked once when this computer has more than one)
    cfg3 = load_ini()
    if audio_devices()[1:] and (force or 'audio' not in cfg3):
        choose_audio(test=False)
    AUDIO['device'] = load_ini().get('audio') or ''
    if audio_devices()[1:]:
        print(f'  [OK] sound output: {audio_label()}')
        for o in others:
            if AUDIO['device'] and (o.get('audio') or '') == AUDIO['device']:
                print(f'  [!] the player in {o["folder"]} uses the same sound output - choose another one: 12 - Sound output.bat')
    # 4) schedule
    p = Player(load_ini())
    if state == 'ok':
        try:
            p.refresh()
        except Exception as e:   # noqa: BLE001
            print(f'  [!] could not download the schedule now ({e}) - it retries by itself')
    s = p.schedule
    if s:
        print(f'  [OK] schedule saved on this computer - {sum(len(v) for v in s.get("days", {}).values())} rings a week'
              + ('' if p.cached_ok() else ' (some tones still downloading)'))
    else:
        print('  [!] no schedule on this computer yet - it arrives as soon as the server is reached')
    # 5) start-up + running
    write_tools()
    install_startup()
    time.sleep(3)
    running = (not IS_WIN) or (bool(pids_of(sys.executable)) if getattr(sys, 'frozen', False) else True)
    print('  [OK] starts with Windows' + (' - running now' if running else ''))
    print(f'  Next ring: {p.next_ring_text() or "-"}')
    good = state == 'ok' and p.licensed() and bool(s)
    print('\n  ' + ('Everything is ready.' if good else 'Ready, with the warnings above.') +
          '  Test: admin page > Central bell > Tone library & test > Ring now\n')
    return good


def setup():
    """--setup: ask for everything again (Enter keeps the current value)."""
    return doctor(force=True)


def other_settings():
    """Settings of the OTHER players installed on this computer: [{folder, server, key, license, audio}]."""
    out = []
    for exe in installs():
        folder = os.path.dirname(exe)
        if _same(folder, BASE):
            continue
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read(os.path.join(folder, 'bell_player.ini'), encoding='utf-8')
        except (OSError, configparser.Error):
            continue
        s = cp['player'] if cp.has_section('player') else {}
        out.append({'folder': folder, 'server': (s.get('server') or '').rstrip('/'), 'key': s.get('key') or '',
                    'license': s.get('license') or '', 'audio': s.get('audio')})
    return out


def choose_audio(test=True):
    """--audio: pick the sound output of THIS player (number), Enter = Windows default; then a test tone."""
    devs = audio_devices()
    cur = load_ini()
    print('\n  -- Sound output of this player --')
    if not devs:
        print('  (no sound outputs found - Windows default is used)')
        return False
    used = {o.get('audio') or '': o['folder'] for o in other_settings()}
    for i, (_d, name) in enumerate(devs, 1):
        mark = ' <- now' if name == (cur.get('audio') or '') else ''
        other = f'   (used by {used[name]})' if name in used and name else ''
        print(f'    {i}. {name}{mark}{other}')
    print(f'    0. Windows default output{" <- now" if not cur.get("audio") else ""}')
    while True:
        a = input('  Number [Enter = keep]: ').strip()
        if not a:
            name = cur.get('audio') or ''
            break
        if a.isdigit() and 0 <= int(a) <= len(devs):
            name = '' if a == '0' else devs[int(a) - 1][1]
            break
        print('  - type one of the numbers above')
    save_ini(cur['server'], cur['key'], cur['license'], audio=name)
    AUDIO['device'] = name
    print(f'  [OK] sound output: {name or "Windows default"}')
    if test:
        path = os.path.join(CACHE, 'tones')
        files = sorted(f for f in os.listdir(path) if f.lower().endswith('.wav')) if os.path.isdir(path) else []
        if files:
            print('  playing a test tone on it ...')
            try:
                play_file(os.path.join(path, files[0]), (load_cached_schedule() or {}).get('volume', 100))
            except Exception as e:   # noqa: BLE001
                print(f'  [X] {e}')
        if IS_WIN and getattr(sys, 'frozen', False) and cur.get('server'):
            install_startup()          # the running player takes the new output
    return True


def ask_license():
    """Shows this computer's fingerprint and asks for the license key (Enter keeps a valid one). True = licensed."""
    cur = load_ini()
    fp = machine_fingerprint()
    load_clock()
    today = amman_now().date()
    ok, expired, exp = check_license(cur['license'], fp, today)
    print('\n  -- License --')
    print(f'  Device fingerprint:  {fp}')
    if ok and not expired:
        print('  [OK] licensed - ' + ('permanent' if exp == 'PERMANENT' else f'valid until {exp}'))
    elif ok:
        print(f'  [X] the license expired on {exp}')
    else:
        print('  Send the fingerprint above to the developer to get a license key')
    while True:
        prompt = '  License key' + (' [Enter = keep]' if ok and not expired else '') + ': '
        entered = input(prompt).strip()
        if not entered:
            if ok and not expired:
                return True
            print('  [X] The bell will not ring without a valid license key. Later: BellPlayer.exe --license')
            return False
        v, e, x = check_license(entered, fp, today)
        if not v:
            print('  [X] this key is not valid for this computer - copy it completely')
            continue
        if e:
            print(f'  [X] this key expired on {x}')
            continue
        cfg = load_ini()
        save_ini(cfg['server'], cfg['key'], entered)
        print('  [OK] activated - ' + ('permanent' if x == 'PERMANENT' else f'until {x}'))
        return True


def status_report():
    p = Player(load_ini())
    s = p.schedule
    print(f'  server: {p.server or "-"}\n  cached version: {(s or {}).get("version", "-")}  tones cached: {p.cached_ok()}')
    print(f'  enabled: {(s or {}).get("enabled")}  volume: {(s or {}).get("volume")}')
    print(f'  next ring: {p.next_ring_text() or "-"}')
    print(f'  license: {p.license_text()}  (fingerprint {p.fp})')
    AUDIO['device'] = load_ini().get('audio') or ''
    print(f'  sound output: {audio_label()}' + (f'   (one of {len(installs())} players on this computer)' if len(installs()) > 1 else ''))
    print(f'  server clock offset: {CLOCK.get("offset", 0):+.1f} s  (synced {CLOCK.get("synced") or "never"})  school network: {", ".join(CLOCK.get("lan") or []) or "-"}')


def main():
    args = sys.argv[1:]
    if IS_WIN and getattr(sys, 'frozen', False):
        _unblock(sys.executable)
    AUDIO['device'] = load_ini().get('audio') or ''
    if '--audio' in args:
        choose_audio()
        if sys.stdin and sys.stdin.isatty():
            input('  Press Enter to close')
        return
    if '--add' in args:
        ok = install_flow(add=True) if (IS_WIN and getattr(sys, 'frozen', False)) else False
        if not (IS_WIN and getattr(sys, 'frozen', False)):
            print('  --add works with BellPlayer.exe on Windows')
        if sys.stdin and sys.stdin.isatty():
            input('  Press Enter to close')
        sys.exit(0 if ok else 1)
    if '--tools' in args:
        write_tools()
        print('  shortcut .bat files written to', BASE)
        return
    if '--restart' in args:
        install_startup()
        print('  restarted')
        return
    if '--stop' in args:
        if IS_WIN:
            subprocess.run(['schtasks', '/change', '/tn', task_name(), '/disable'], capture_output=True)
            kill_same(sys.executable)
        print('  stopped - it does NOT ring now. It starts again at the next sign-in, or with: 8 - Restart player.bat')
        return
    if '--log' in args:
        try:
            with open(os.path.join(BASE, 'bell_player.log'), encoding='utf-8', errors='replace') as f:
                print(''.join(f.readlines()[-40:]))
        except OSError:
            print('  no log yet')
        return
    if '--check' in args:
        ok = doctor()
        if sys.stdin and sys.stdin.isatty():
            input('  Press Enter to close')
        sys.exit(0 if ok else 1)
    if not args and IS_WIN and getattr(sys, 'frozen', False):
        # double-click: outside the installation → install/update; the installed copy → check and fix
        me = os.path.abspath(sys.executable)
        if any(_same(x, me) for x in installs()) or (not installs() and _same(os.path.dirname(me), DEFAULT_DIR)):
            doctor()
        else:
            install_flow()
        if sys.stdin and sys.stdin.isatty():
            input('  Press Enter to close')
        return
    if '--uninstall' in args:
        ok = uninstall()
        if sys.stdin and sys.stdin.isatty():
            input('  Press Enter to close')
        sys.exit(0 if ok else 1)
    if '--remove' in args:
        remove_startup()
        print('  removed from Windows start-up')
        return
    if '--net-test' in args:          # checks HTTPS to the server (certificates, firewall) and prints the answer
        url = (load_ini()['server'] or DEFAULT_SERVER) + '/healthz'
        try:
            print(f'  {url}: HTTP', http('GET', url, '')[0])
        except urllib.error.HTTPError as e:
            print(f'  {url}: HTTP {e.code} (the connection and certificates are fine)')
        except Exception as e:   # noqa: BLE001
            print(f'  {url}: {e}')
            sys.exit(2 if 'CERTIFICATE' in str(e).upper() else 1)
        return
    if '--fingerprint' in args:
        print(machine_fingerprint())
        return
    if '--license' in args:
        ok = ask_license()
        if ok and IS_WIN and load_ini()['server']:
            install_startup()          # restart it with the new license (a running copy re-reads at the next start)
        if sys.stdin and sys.stdin.isatty():
            input('  Press Enter to close')
        sys.exit(0 if ok else 1)
    if '--status' in args:
        status_report()
        return
    if '--test' in args:
        setup_logging()
        path = os.path.join(CACHE, 'tones')
        files = sorted(os.listdir(path)) if os.path.isdir(path) else []
        if not files:
            print('  no tones cached yet — run --setup first')
            return
        print('  playing', files[0])
        play_file(os.path.join(path, files[0]), (load_cached_schedule() or {}).get('volume', 100))
        return
    cfg = load_ini()
    if '--setup' in args or (not args and not (cfg['server'] and cfg['key'])):
        ok = setup()
        if not args and sys.stdin and sys.stdin.isatty():
            input('  Press Enter to close')
        sys.exit(0 if ok else 1)
    setup_logging(console='--run' not in args or not IS_WIN)
    if not single_instance():
        log.info('already running — exit')
        return
    Player(cfg).run()


if __name__ == '__main__':
    main()
