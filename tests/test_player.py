"""Office player against a real (local) server: setup, cache, offline ringing, test ring, key change."""
import importlib.util
import json
import os
import re
import sys
import threading
from datetime import datetime

import pytest
from werkzeug.serving import make_server

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)
from app import create_app  # noqa: E402
from tests.test_central import _seed  # noqa: E402


def _keys():
    """A throw-away ECDSA P-256 pair; returns (public PEM, sign(fingerprint, exp) → license key) like the generator."""
    ec = pytest.importorskip('cryptography.hazmat.primitives.asymmetric.ec')
    from cryptography.hazmat.primitives import hashes, serialization
    import base64
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def sign(fp, exp='PERMANENT'):
        sig = priv.sign(f'{fp}|{exp}'.encode(), ec.ECDSA(hashes.SHA256()))
        return f'{exp}:' + base64.urlsafe_b64encode(sig).decode().rstrip('=')
    return pem, sign


FP = '0123456789ABCDEF'


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path}/t.db'})
    admin = app.test_client()
    admin.post('/admin/login', data={'username': 'admin', 'password': 'admin'})
    _seed(admin, app)
    admin.get('/admin/central')
    admin.post('/admin/central/key')
    key = re.search(r'user-select:all">([^<]+)<', admin.get('/admin/central').get_data(as_text=True)).group(1)
    import central
    # the test server must not send this computer's real school-network addresses (a real bell server may run there)
    monkeypatch.setattr(central, 'lan_urls', lambda: [])
    srv = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    spec = importlib.util.spec_from_file_location('bell_player', os.path.join(ROOT, 'bell_player', 'bell_player.py'))
    bp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bp)
    monkeypatch.setattr(bp, 'BASE', str(tmp_path / 'player'))
    monkeypatch.setattr(bp, 'CACHE', str(tmp_path / 'player' / 'player_cache'))
    monkeypatch.setattr(bp, 'INI', str(tmp_path / 'player' / 'bell_player.ini'))
    pem, sign = _keys()
    monkeypatch.setattr(bp, 'LICENSE_PUBLIC_KEY_PEM', pem)
    monkeypatch.setattr(bp, 'machine_fingerprint', lambda: FP)
    # never touch the Windows of the computer running the tests (start-up entries, scheduled task, power plan)
    monkeypatch.setattr(bp, 'install_startup', lambda: None)
    monkeypatch.setattr(bp, 'pids_of', lambda exe: [])
    monkeypatch.setattr(bp, '_power_call', lambda cmd: True)
    bp.real_keep_awake = bp.keep_awake
    monkeypatch.setattr(bp, 'keep_awake', lambda: True)
    bp.CLOCK.clear()
    bp.CLOCK.update({'offset': 0.0, 'tz_min': None, 'synced': None, 'lan': []})
    bp.sign = sign
    played = []
    monkeypatch.setattr(bp, 'play_file', lambda p, v=100: played.append((os.path.basename(p), v)))
    os.makedirs(bp.BASE)
    yield bp, admin, key, f'http://127.0.0.1:{srv.server_port}', played
    srv.shutdown()


def test_player_end_to_end(env, monkeypatch):
    bp, admin, key, server, played = env
    answers = iter([server, key, 'PERMANENT:bad', bp.sign('FFFFFFFFFFFFFFFF'), bp.sign(FP, '20000101'), bp.sign(FP)])
    monkeypatch.setattr('builtins.input', lambda *_: next(answers))
    assert bp.setup()
    ini = bp.load_ini()
    assert (ini['server'], ini['key']) == (server, key)
    assert bp.check_license(bp.load_ini()['license'], FP, datetime(2030, 1, 1).date())[:2] == (True, False)
    assert bp.CLOCK['tz_min'] is not None and bp.CLOCK['synced']        # setup synced with the server clock
    p = bp.Player(bp.load_ini())
    assert p.cached_ok() and p.schedule['days']['6']
    # Sunday at the first ring time → due once, then never again
    first = p.schedule['days']['6'][0]
    h, m = map(int, first['t'].split(':'))
    sun = datetime(2026, 10, 25, h, m, 20)
    assert sun.weekday() == 6
    due = p.due(sun)
    assert [k for k, _ in due] == [f"2026-10-25 {first['t']}"]
    p.fired.add(due[0][0])
    assert p.due(sun) == []
    assert p.due(sun.replace(minute=m, second=0).__class__(2026, 10, 25, h, m) .replace(hour=h)) == []
    # more than 90 s late → skipped
    from datetime import timedelta
    p2 = bp.Player(bp.load_ini())
    assert p2.due(datetime(2026, 10, 25, h, m) + timedelta(seconds=120)) == []
    # Friday: nothing; skip date: nothing
    assert p.today_slots(datetime(2026, 10, 23, 9, 0)) == []
    admin.post('/admin/central/save', data={'enabled': '1', 'skip_dates': '2099-11-01'})
    p.poll()
    assert '2099-11-01' in p.schedule['skip_dates']
    assert p.today_slots(datetime(2099, 11, 1, 9, 0)) == []
    # offline: a fresh player with an unreachable server still has the schedule from the cache
    off = bp.Player({'server': 'http://127.0.0.1:9', 'key': key})
    assert off.cached_ok() and off.schedule['version'] == p.schedule['version']
    # test ring from the admin page reaches the audio queue once
    admin.post('/admin/central/test', data={'tone': 'class-3'})
    p.poll()
    paths, what = p.audio.get_nowait()
    assert what == 'test' and os.path.basename(paths[0]).startswith('class-3')
    p.poll()
    assert p.audio.empty()
    # status reached the server
    html = admin.get('/admin/central').get_data(as_text=True)
    assert 'جهاز المكتب متصل' in html
    # disabling the bell → no slots
    admin.post('/admin/central/save', data={'volume': '60'})
    p.poll()
    assert p.today_slots(sun) == [] and p.schedule['volume'] == 60
    # new key → 401, player keeps its cache
    admin.post('/admin/central/key')
    import urllib.error
    with pytest.raises(urllib.error.HTTPError):
        p.poll()
    assert p.schedule['volume'] == 60


def test_ring_loop_plays_due_slot(env, monkeypatch):
    bp, admin, key, server, played = env
    bp.save_ini(server, key, bp.sign(FP))
    p = bp.Player(bp.load_ini())
    p.refresh()
    first = p.schedule['days']['6'][0]
    h, m = map(int, first['t'].split(':'))
    clock = [datetime(2026, 10, 25, h, m) - __import__('datetime').timedelta(seconds=3)]
    monkeypatch.setattr(bp, 'amman_now', lambda: clock[0])
    calls = {'n': 0}

    def fake_sleep(_s):
        calls['n'] += 1
        clock[0] += __import__('datetime').timedelta(seconds=2)
        if calls['n'] > 6:
            raise KeyboardInterrupt
    monkeypatch.setattr(bp.time, 'sleep', fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        p.ring_loop()
    paths, what = p.audio.get_nowait()
    assert what == f"2026-10-25 {first['t']}" and len(paths) == len(first['rings'])
    assert p.audio.empty()
    assert json.loads(json.dumps(p.next_ring_text()))


def test_server_clock_lan_fallback_and_license(env, monkeypatch):
    import time as _t
    bp, admin, key, server, played = env
    bp.save_ini('http://127.0.0.1:9', key, bp.sign(FP, '20991231'))      # public address unreachable
    p = bp.Player(bp.load_ini())
    with pytest.raises(Exception):
        p.poll()
    bp.CLOCK['lan'] = [server]                                           # …but the school-network address works
    p.poll()
    assert p.server == server and p.schedule
    assert bp.CLOCK['tz_min'] == 180 and abs(bp.CLOCK['offset']) < 2
    # rings follow the server clock, not this PC: pretend the server is 1 h ahead
    before = bp.amman_now()
    p.sync_clock({'server_ms': (_t.time() + 3600) * 1000, 'tz_offset_min': 180}, _t.time(), _t.time())
    assert 3590 < (bp.amman_now() - before).total_seconds() < 3610 and p.clock_drift == -3600
    # the offset is kept for offline use
    bp.CLOCK['offset'] = 0
    bp.load_clock()
    assert abs(bp.CLOCK['offset'] - 3600) < 2
    # license reported to the admin page
    p.sync_clock({'server_ms': _t.time() * 1000, 'tz_offset_min': 180}, _t.time(), _t.time())
    p.poll()
    html = admin.get('/admin/central').get_data(as_text=True)
    assert '2099-12-31' in html and FP in html
    # no / wrong license → rings are skipped, and the admin page says so
    bp.save_ini(server, key, 'PERMANENT:AAAA')
    q = bp.Player(bp.load_ini())
    assert not q.licensed() and q.license_text() == 'invalid'
    first = q.schedule['days']['6'][0]
    h, m = map(int, first['t'].split(':'))
    clock = [datetime(2026, 10, 25, h, m, 1)]
    monkeypatch.setattr(bp, 'amman_now', lambda: clock[0])
    q.lic_day = None
    n = {'i': 0}

    def stop(_s):
        n['i'] += 1
        if n['i'] > 3:
            raise KeyboardInterrupt
    monkeypatch.setattr(bp.time, 'sleep', stop)
    with pytest.raises(KeyboardInterrupt):
        q.ring_loop()
    assert q.audio.empty()
    q.poll()
    assert 'غير مفعّل' in admin.get('/admin/central').get_data(as_text=True)
    # entering a valid key later is picked up without a restart
    bp.save_ini(server, key, bp.sign(FP))
    q._lic_t = 0
    assert q.licensed()


def test_ecdsa_matches_the_generator_format():
    import importlib.util as iu
    spec = iu.spec_from_file_location('bpx', os.path.join(ROOT, 'bell_player', 'bell_player.py'))
    bp = iu.module_from_spec(spec)
    spec.loader.exec_module(bp)
    import base64
    pem, sign = _keys()
    for i in range(30):
        fp = f'{i:016X}'
        sig = sign(fp, '20301231').split(':')[1]
        raw = base64.urlsafe_b64decode(sig + '=' * (-len(sig) % 4))
        assert bp.ecdsa_verify(f'{fp}|20301231'.encode(), raw, pem)
        assert not bp.ecdsa_verify(f'{fp}|20311231'.encode(), raw, pem)          # edited date → rejected
        assert not bp.ecdsa_verify(f'{fp}|20301231'.encode(), raw)              # other key pair → rejected
    # the real public key is the one shared with the desktop analysis program
    assert 'MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE9hskm3ufgTN' in bp.LICENSE_PUBLIC_KEY_PEM


def test_check_and_fix_asks_only_for_what_is_wrong(env, monkeypatch):
    """«Check and fix»: a refused player key is asked again; a valid license is not asked at all."""
    bp, admin, key, server, played = env
    lic = bp.sign(FP)
    bp.save_ini(server, 'OLD-REFUSED-KEY', lic)
    asked = []
    answers = iter([key])
    monkeypatch.setattr('builtins.input', lambda prompt='': (asked.append(prompt), next(answers))[1])
    assert bp.doctor()
    assert len(asked) == 1 and 'Player key' in asked[0]
    assert bp.load_ini()['key'] == key and bp.load_ini()['license'] == lic
    # everything fine now → no question at all
    asked.clear()
    assert bp.doctor() and asked == []
    # server down → not stuck: warns and keeps the settings
    bp.save_ini('http://127.0.0.1:9', key, lic)
    assert not bp.doctor() and asked == [] and bp.load_ini()['key'] == key


def test_several_players_share_the_license_and_keep_their_sound_output(env, monkeypatch, tmp_path):
    """A second player on the same computer takes the license of the first one and keeps its own sound output."""
    bp, admin, key, server, played = env
    lic = bp.sign(FP)
    first = tmp_path / 'first'
    first.mkdir()
    (first / 'bell_player.ini').write_text(f'[player]\nserver = {server}\nkey = OTHER\nlicense = {lic}\naudio = Speakers (USB A)\n',
                                           encoding='utf-8')
    (first / 'BellPlayer.exe').write_bytes(b'')
    monkeypatch.setattr(bp, 'installs', lambda: [str(first / 'BellPlayer.exe')])
    monkeypatch.setattr(bp, 'inst_tag', lambda: 'player')
    bp.save_ini(server, key, '')
    monkeypatch.setattr('builtins.input', lambda prompt='': (_ for _ in ()).throw(AssertionError('asked: ' + prompt)))
    assert bp.doctor()                                   # nothing asked: server + key fine, license from the first player
    assert bp.load_ini()['license'] == lic
    # the sound output is kept by every later save
    bp.save_ini(server, key, lic, audio='Speakers (USB B)')
    bp.save_ini(server, key)
    assert bp.load_ini()['audio'] == 'Speakers (USB B)'
    assert [o['audio'] for o in bp.other_settings()] == ['Speakers (USB A)']
    # one running copy per folder: the lock name depends on the folder
    assert bp.single_instance()


# ── a real school day: several programs at once, a late start and the PC asleep (live test 2026-11-03) ──
def _run_day(bp, p, monkeypatch, start, end, sleeps=(), step=2):
    """Run ring_loop on a fake clock from start to end (2 s a loop); sleeps = [(from, to)] when the PC was asleep."""
    from datetime import timedelta
    clock = [start]
    monkeypatch.setattr(bp, 'amman_now', lambda: clock[0])

    def fake_sleep(_s):
        clock[0] += timedelta(seconds=step)
        for a, b in sleeps:
            if a <= clock[0] < b:
                clock[0] = b                        # nothing runs while Windows sleeps: the clock jumps on wake
        if clock[0] >= end:
            raise KeyboardInterrupt
    monkeypatch.setattr(bp.time, 'sleep', fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        p.ring_loop()
    rung = []
    while not p.audio.empty():
        paths, what = p.audio.get_nowait()
        rung.append((what, sorted(os.path.basename(x).split('-')[0] + '-' + os.path.basename(x).split('-')[1] for x in paths)))
    return rung


def _tuesday_player(env, groups='all'):
    bp, admin, key, server, played = env
    bp.save_ini(server, key, bp.sign(FP))
    if groups == 'every-box':                    # «all programs» ticked one by one on the device (like «جهاز المكتب»)
        import central
        with admin.application.app_context():
            pid = next(iter(central.players()))
            central.update_player(pid, groups=[g['key'] for g in central.groups()])
    p = bp.Player(bp.load_ini())
    p.refresh()
    return bp, p


@pytest.mark.parametrize('groups', ['all', 'every-box'])
def test_whole_tuesday_with_every_program_rings_every_slot(env, monkeypatch, groups):
    bp, p = _tuesday_player(env, groups)
    tue = p.schedule['days']['1']
    assert len({g for s in tue for r in s['rings'] for g in r['groups']}) >= 5      # several programs together
    assert any(len(s['rings']) > 1 for s in tue)                                      # different tones in one minute
    rung = _run_day(bp, p, monkeypatch, datetime(2026, 11, 3, 7, 0), datetime(2026, 11, 3, 14, 0))
    assert [w for w, _ in rung] == [f"2026-11-03 {s['t']}" for s in tue]             # every slot, once, in order
    for (_w, tones), s in zip(rung, tue):
        assert tones == sorted(r['tone'] for r in s['rings'])                         # every tone of that minute


def test_late_start_and_sleeping_pc(env, monkeypatch, caplog):
    """2026-11-03: Windows woke at 09:25:26, the player started 46 s after a ring (it was dropped), then the lid was
    closed 09:44:38 → 10:05:50 (two rings lost without a word in the log)."""
    from datetime import timedelta
    bp, p = _tuesday_player(env)
    tue = p.schedule['days']['1']
    at = [datetime(2026, 11, 3, *map(int, s['t'].split(':'))) for s in tue]
    start = at[3] + timedelta(seconds=46)                         # started 46 s late: still within the 90 s catch-up
    asleep = (at[4] - timedelta(seconds=22), at[5] + timedelta(minutes=5, seconds=50))
    caplog.set_level('INFO', logger='bell_player')
    rung = _run_day(bp, p, monkeypatch, start, at[7] + timedelta(minutes=1), sleeps=[asleep])
    assert [w for w, _ in rung] == [f"2026-11-03 {s['t']}" for s in (tue[3], tue[6], tue[7])]
    warn = [r.getMessage() for r in caplog.records if r.levelname == 'WARNING' and 'MISSED' in r.getMessage()]
    assert len(warn) == 1 and tue[4]['t'] in warn[0] and tue[5]['t'] in warn[0] and 'asleep' in warn[0]
    assert tue[4]['t'] in p.missed and tue[5]['t'] in p.missed                       # reported to the admin page
    p.poll()
    html = env[1].get('/admin/central').get_data(as_text=True)
    assert 'رنين فات' in html and tue[4]['t'] in html
    # a restart a few seconds after a ring does not play it twice (what was played is kept on disk)
    p2 = bp.Player(bp.load_ini())
    again = _run_day(bp, p2, monkeypatch, at[7] + timedelta(seconds=20), at[7] + timedelta(seconds=80))
    assert again == []


def test_rings_missed_while_the_pc_was_off_are_logged_at_start(env, monkeypatch, caplog):
    from datetime import timedelta
    bp, p = _tuesday_player(env)
    tue = p.schedule['days']['1']
    at = [datetime(2026, 11, 3, *map(int, s['t'].split(':'))) for s in tue]
    _run_day(bp, p, monkeypatch, at[0] - timedelta(seconds=10), at[0] + timedelta(seconds=70))   # rang, then «off»
    caplog.set_level('INFO', logger='bell_player')
    p2 = bp.Player(bp.load_ini())
    _run_day(bp, p2, monkeypatch, at[2] + timedelta(minutes=3), at[2] + timedelta(minutes=4))
    warn = [r.getMessage() for r in caplog.records if 'MISSED' in r.getMessage()]
    lost = warn[0].split(' - ')[0] if warn else ''
    assert len(warn) == 1 and tue[1]['t'] in lost and tue[2]['t'] in lost and tue[0]['t'] not in lost


def test_keeps_windows_awake_and_lid_does_not_sleep(env, monkeypatch):
    bp, *_ = env
    calls = []
    monkeypatch.setattr(bp, 'IS_WIN', True)
    monkeypatch.setattr(bp, '_power_call', lambda cmd: calls.append(cmd) or True)
    assert bp.power_fix()
    flat = [' '.join(c) for c in calls]
    assert any('setacvalueindex' in c and bp.LID_ACTION in c and c.endswith(' 0') for c in flat)
    assert any('setdcvalueindex' in c and bp.LID_ACTION in c and c.endswith(' 0') for c in flat)
    assert any('standby-timeout-ac 0' in c for c in flat) and flat[-1].endswith('/setactive SCHEME_CURRENT')

    class K:
        flags = []

        def SetThreadExecutionState(self, f):
            K.flags.append(f)
            return 1
    fake = type('W', (), {'kernel32': K()})()
    monkeypatch.setattr('ctypes.windll', fake, raising=False)
    assert bp.real_keep_awake() and K.flags == [0x80000000 | 0x1]
