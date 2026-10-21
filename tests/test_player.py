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
    played = []
    monkeypatch.setattr(bp, 'play_file', lambda p, v=100: played.append((os.path.basename(p), v)))
    os.makedirs(bp.BASE)
    yield bp, admin, key, f'http://127.0.0.1:{srv.server_port}', played
    srv.shutdown()


def test_player_end_to_end(env, monkeypatch):
    bp, admin, key, server, played = env
    answers = iter([server, key])
    monkeypatch.setattr('builtins.input', lambda *_: next(answers))
    assert bp.setup()
    assert bp.load_ini() == {'server': server, 'key': key}
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
    bp.save_ini(server, key)
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
