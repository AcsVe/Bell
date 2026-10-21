"""Office bell player (الجرس المركزي) — runs on the office computer connected to the PA speakers.

    BellPlayer.exe            first run: asks for the server and the key, then starts with Windows (no admin needed)
    BellPlayer.exe --setup    change the server / key
    BellPlayer.exe --test     ring one built-in tone now (check the speakers)
    BellPlayer.exe --status   what is cached and when the next ring is
    BellPlayer.exe --run      the player itself (what Windows start-up runs, hidden)
    BellPlayer.exe --remove   remove it from Windows start-up

Works offline: the schedule and every tone are cached next to the program (player_cache/). The server is needed
only to receive changes; when it is down or the internet is cut the bell keeps ringing from the cache.
Rings use this computer's clock — keep "Set time automatically" on and the time zone on Amman.
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

VERSION = '1.0'
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
    cp = configparser.ConfigParser()
    cp.read(INI, encoding='utf-8')
    s = cp['player'] if cp.has_section('player') else {}
    return {'server': (s.get('server') or '').rstrip('/'), 'key': s.get('key') or ''}


def save_ini(server, key):
    cp = configparser.ConfigParser()
    cp['player'] = {'server': server.rstrip('/'), 'key': key}
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


# ── time ───────────────────────────────────────────────────────────────────
def amman_now():
    """Local school time. Uses the Amman zone when available, otherwise the computer's own clock."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('Asia/Amman')).replace(tzinfo=None)
    except Exception:   # noqa: BLE001
        return datetime.now()


# ── server ─────────────────────────────────────────────────────────────────
def http(method, url, key, body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'X-Bell-Key': key, 'User-Agent': f'BellPlayer/{VERSION}', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
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
def play_file(path, volume=100):
    """Plays one file to the end. Windows: MCI (WAV and MP3, with volume); elsewhere a command-line player."""
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
        self.server, self.key = cfg['server'], cfg['key']
        self.schedule = load_cached_schedule()
        self.lock = threading.Lock()
        self.audio = queue.Queue()
        self.fired = set()
        self.last_ring = ''
        self.last_test = ''
        self.error = ''
        self.clock_drift = None

    # sync -------------------------------------------------------------------
    def cached_ok(self):
        s = self.schedule
        return bool(s) and all(os.path.isfile(tone_file(k, t['url'])) for k, t in s.get('tones', {}).items())

    def refresh(self, version_hint=None):
        cur = (self.schedule or {}).get('version')
        if version_hint and version_hint == cur and self.cached_ok():
            return
        status, data = http('GET', f'{self.server}/api/central/schedule' + (f'?v={cur}' if cur and self.cached_ok() else ''), self.key)
        body = json.loads(data.decode('utf-8'))
        if body.get('unchanged'):
            return
        for k, t in body.get('tones', {}).items():      # download every tone BEFORE switching to the new schedule
            download(self.server, self.key, t['url'], tone_file(k, t['url']))
        save_schedule(body)
        with self.lock:
            self.schedule = body
        log.info('schedule updated (version %s, %d tones)', body.get('version'), len(body.get('tones', {})))

    def poll(self):
        st = {'host': socket.gethostname(), 'version': (self.schedule or {}).get('version'), 'player': VERSION,
              'last_ring': self.last_ring, 'next_ring': self.next_ring_text(), 'clock_drift': self.clock_drift,
              'cached': self.cached_ok(), 'error': self.error[:300]}
        status, data = http('POST', f'{self.server}/api/central/poll', self.key, st)
        r = json.loads(data.decode('utf-8'))
        try:
            srv = datetime.strptime(r['server_utc'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            self.clock_drift = int((datetime.now(timezone.utc) - srv).total_seconds())
            if abs(self.clock_drift) > 30:
                log.warning('this PC clock differs from the server by %s s — turn on automatic time', self.clock_drift)
        except (KeyError, ValueError):
            pass
        self.refresh(r.get('version'))
        t = r.get('test')
        if t and t.get('id') != self.last_test:
            self.last_test = t['id']
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
                    log.info('server reachable again')
                self.error, backoff = '', POLL_SECONDS
            except urllib.error.HTTPError as e:
                self.error = f'HTTP {e.code}'
                if e.code == 401:
                    log.error('the server refused the key — a new key was created? run: BellPlayer.exe --setup')
                    backoff = 300
            except Exception as e:   # noqa: BLE001 — offline: keep ringing from the cache
                if not self.error:
                    log.warning('server not reachable (%s) — ringing from the saved schedule', e)
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
                paths = []
                for r in slot['rings']:
                    t = tones.get(r['tone'])
                    if t and os.path.isfile(tone_file(r['tone'], t['url'])):
                        paths.append(tone_file(r['tone'], t['url']))
                    else:
                        log.error('tone %s is not cached — not played', r['tone'])
                if paths:
                    self.audio.put((paths, k))
                    log.info('RING %s: %s', k, ' + '.join(f"{r['tone']} ({'، '.join(r['groups'])})" for r in slot['rings']))
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
        log.info('Bell player %s started — server %s, cache %s', VERSION, self.server, CACHE)
        if not self.schedule:
            log.warning('no saved schedule yet — waiting for the server')
        for target in (self.sync_loop, self.audio_loop):
            threading.Thread(target=target, daemon=True).start()
        self.ring_loop()


# ── single instance ─────────────────────────────────────────────────────────
def single_instance():
    if IS_WIN:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, False, 'Global\\IAJ_BellPlayer')
        return ctypes.windll.kernel32.GetLastError() != 183          # ERROR_ALREADY_EXISTS
    try:
        s = socket.socket()
        s.bind(('127.0.0.1', 47811))
        single_instance.sock = s
        return True
    except OSError:
        return False


# ── Windows start-up ────────────────────────────────────────────────────────
def startup_dir():
    return os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')


def launch_cmd():
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}" --run'
    pyw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    return f'"{pyw if os.path.isfile(pyw) else sys.executable}" "{os.path.abspath(__file__)}" --run'


def install_startup():
    if not IS_WIN:
        return
    vbs = os.path.join(startup_dir(), 'BellPlayer.vbs')
    os.makedirs(startup_dir(), exist_ok=True)
    with open(vbs, 'w', encoding='utf-8') as f:     # hidden window, starts at every sign-in
        f.write('CreateObject("WScript.Shell").Run "{}", 0, False\n'.format(launch_cmd().replace('"', '""')))
    # Every 5 minutes: start it again if it was closed or crashed (a second copy exits at once).
    subprocess.run(['schtasks', '/create', '/f', '/tn', 'BELL Office Player', '/sc', 'minute', '/mo', '5',
                    '/tr', f'wscript.exe "{vbs}"'], capture_output=True)
    subprocess.Popen(['wscript.exe', vbs])


def remove_startup():
    if not IS_WIN:
        return
    try:
        os.remove(os.path.join(startup_dir(), 'BellPlayer.vbs'))
    except OSError:
        pass
    subprocess.run(['schtasks', '/delete', '/f', '/tn', 'BELL Office Player'], capture_output=True)
    subprocess.run(['taskkill', '/f', '/im', os.path.basename(sys.executable)] if getattr(sys, 'frozen', False) else ['cmd', '/c', 'echo'],
                   capture_output=True)


def setup():
    cur = load_ini()
    print('\n  الجرس المركزي — إعداد جهاز المكتب / Office bell player setup\n')
    server = input(f'  عنوان الخادم / Server [{cur["server"] or DEFAULT_SERVER}]: ').strip() or cur['server'] or DEFAULT_SERVER
    if not server.startswith('http'):
        server = 'https://' + server
    server = server.rstrip('/')
    key = input('  مفتاح جهاز المكتب (من صفحة «الجرس المركزي» ← جهاز المكتب) / Player key' + (' [Enter = keep]' if cur['key'] else '') + ': ').strip() or cur['key']
    try:
        status, data = http('GET', f'{server}/api/central/schedule', key)
        body = json.loads(data.decode('utf-8'))
        print(f'  ✓ متصل — {sum(len(v) for v in body.get("days", {}).values())} رنين في الأسبوع / connected')
    except urllib.error.HTTPError as e:
        print('  ✖ المفتاح غير صحيح / wrong key' if e.code == 401 else f'  ✖ HTTP {e.code}')
        return False
    except Exception as e:   # noqa: BLE001
        print(f'  ✖ تعذّر الاتصال بالخادم / cannot reach the server: {e}')
        return False
    save_ini(server, key)
    os.makedirs(CACHE, exist_ok=True)
    p = Player(load_ini())
    p.refresh()
    print('  ✓ حُفظ الجدول والنغمات على هذا الجهاز / schedule and tones saved')
    install_startup()
    print('  ✓ يعمل الآن ويبدأ تلقائياً مع ويندوز / running, starts with Windows')
    print('  للتجربة: صفحة «الجرس المركزي» ← «رنّ الآن على جهاز المكتب» / test from the admin page\n')
    return True


def status_report():
    p = Player(load_ini())
    s = p.schedule
    print(f'  server: {p.server or "-"}\n  cached version: {(s or {}).get("version", "-")}  tones cached: {p.cached_ok()}')
    print(f'  enabled: {(s or {}).get("enabled")}  volume: {(s or {}).get("volume")}')
    print(f'  next ring: {p.next_ring_text() or "-"}')


def main():
    args = sys.argv[1:]
    if '--remove' in args:
        remove_startup()
        print('  removed from Windows start-up')
        return
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
    if '--setup' in args or not (cfg['server'] and cfg['key']):
        ok = setup()
        if not args and sys.stdin and sys.stdin.isatty():
            input('  Enter ↵')
        sys.exit(0 if ok else 1)
    setup_logging(console='--run' not in args or not IS_WIN)
    if not single_instance():
        log.info('already running — exit')
        return
    Player(cfg).run()


if __name__ == '__main__':
    main()
