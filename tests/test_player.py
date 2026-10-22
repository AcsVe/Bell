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
