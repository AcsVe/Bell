"""Central (office) bell: groups, tones, weekly rings, player API, test ring, admin page."""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app  # noqa: E402
from models import Stage  # noqa: E402


@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path}/t.db'})


@pytest.fixture
def admin(app):
    c = app.test_client()
    assert c.post('/admin/login', data={'username': 'admin', 'password': 'admin'}).status_code == 302
    return c


def _seed(admin, app):
    """The school's ten bell programs (1+2, 3+4, 5+6, 7-9, 10-12 — normal and Tuesday) over three stages."""
    for n in ('Primary1-3', 'Primary 4-6', 'Secondary'):
        admin.post('/admin/stages/add', data={'name_ar': n})
    with app.app_context():
        ids = {s.name_ar: s.id for s in Stage.query.all()}
    admin.post('/admin/programs/seed', data={'g13': ids['Primary1-3'], 'g46': ids['Primary 4-6'], 'g712': ids['Secondary']})
    return ids


def test_groups_tones_and_week(admin, app):
    _seed(admin, app)
    import central
    with app.app_context():
        groups = central.groups()
        keys = [g['key'] for g in groups]
        assert keys and all(k.startswith('program:') for k in keys)        # every grade follows a program
        tones = central.effective_tones()
        # defaults: a different period tone per timing (up to the library size) and a break tone each
        cls = [tones[k]['class'] for k in keys]
        assert len(set(cls[:8])) == min(8, len(cls))
        assert all(tones[k]['break'].startswith('break-') for k in keys)
        week = central.week_rings()
        sunday = week[6]
        assert sunday and sunday == sorted(sunday, key=lambda s: s['t'])
        kinds = {r['kind'] for s in sunday for r in s['rings']}
        assert kinds == {'class', 'break'}
        # a break ring uses that group's break tone
        for s in sunday:
            for r in s['rings']:
                assert r['tone'].startswith('break-' if r['kind'] == 'break' else 'class-')
        assert week[4] == [] and week[5] == []                              # Friday / Saturday: no school
        # same minute + same tone rings once, listing every group
        flat = [(s['t'], r['tone']) for s in sunday for r in s['rings']]
        assert len(flat) == len(set(flat))


def test_admin_page_save_and_silent(admin, app):
    _seed(admin, app)
    import central
    html = admin.get('/admin/central').get_data(as_text=True)
    assert 'الجرس المركزي' in html and 'class__program:' in html
    with app.app_context():
        g = central.groups()[0]['key']
        assert central.config()['tones'][g]                                 # defaults frozen on first view
    r = admin.post('/admin/central/save', data={'enabled': '1', 'volume': '70', f'class__{g}': 'class-4',
                                                f'break__{g}': central.SILENT, 'skip_dates': '2099-01-01\nbad\n2000-01-01'},
                   follow_redirects=True)
    body = r.get_data(as_text=True)
    assert 'تاريخ غير صحيح' in body
    with app.app_context():
        cfg = central.config()
        assert cfg['volume'] == 70 and cfg['skip_dates'] == ['2099-01-01']   # past dates dropped
        assert cfg['tones'][g] == {'class': 'class-4', 'break': central.SILENT}
        week = central.week_rings()
        rings = [r for d in week.values() for s in d for r in s['rings']]
        assert not any(r['tone'] == central.SILENT for r in rings)
    for lang in ('ar', 'en'):
        admin.get(f'/lang/{lang}')
        assert admin.get('/admin/central').status_code == 200


def test_player_api_key_version_and_test_ring(admin, app):
    _seed(admin, app)
    c = app.test_client()
    assert c.get('/api/central/schedule').status_code == 401
    admin.post('/admin/central/key')
    html = admin.get('/admin/central').get_data(as_text=True)
    import re
    key = re.search(r'user-select:all">([^<]+)<', html).group(1)
    assert 'user-select:all' not in admin.get('/admin/central').get_data(as_text=True)   # shown once
    h = {'X-Bell-Key': key}
    body = c.get('/api/central/schedule', headers=h).get_json()
    assert body['enabled'] and body['tz'] == 'Asia/Amman' and body['days']['6']
    for t in body['tones'].values():
        assert c.get(t['url']).status_code == 200                           # every tone downloadable
    v = body['version']
    assert c.get(f'/api/central/schedule?v={v}', headers=h).get_json() == {'unchanged': True, 'version': v}
    # status + test ring through poll
    p = c.post('/api/central/poll', headers=h, json={'host': 'OFFICE-PC', 'version': v, 'player': '1.0',
                                                      'clock_drift': 2, 'cached': True}).get_json()
    assert p['version'] == v and p['test'] is None
    admin.post('/admin/central/test', data={'tone': 'class-2'})
    p = c.post('/api/central/poll', headers=h, json={'host': 'OFFICE-PC'}).get_json()
    assert p['test']['tone'] == 'class-2' and p['test']['url'].endswith('class-2.wav')
    html = admin.get('/admin/central').get_data(as_text=True)
    assert 'OFFICE-PC' in html and 'جهاز المكتب متصل' in html
    # changing the bell changes the version the player sees
    admin.post('/admin/central/save', data={'volume': '50'})
    assert c.post('/api/central/poll', headers=h, json={}).get_json()['version'] != v
    # a new key stops the old one
    admin.post('/admin/central/key')
    assert c.get('/api/central/schedule', headers=h).status_code == 401


def test_uploaded_tone(admin, app):
    _seed(admin, app)
    import central
    wav = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'sounds', 'central', 'class-1.wav'), 'rb').read()
    r = admin.post('/admin/central/upload', data={'tone': (io.BytesIO(wav), 'school.wav', 'audio/wav')},
                   content_type='multipart/form-data', follow_redirects=True)
    assert 'رُفعت النغمة' in r.get_data(as_text=True)
    assert 'رُفعت' not in admin.post('/admin/central/upload', data={'tone': (io.BytesIO(b'x'), 'x.txt', 'text/plain')},
                                     content_type='multipart/form-data', follow_redirects=True).get_data(as_text=True)
    with app.app_context():
        m = central.uploaded_tones()[0]
        g = central.groups()[0]['key']
    admin.post('/admin/central/save', data={f'class__{g}': f'media:{m.id}'})
    with app.app_context():
        body = central.player_payload()
        assert f'media:{m.id}' in body['tones'] and body['tones'][f'media:{m.id}']['url'].startswith('/media/')
    admin.post(f'/admin/central/tone/{m.id}/delete')
    with app.app_context():
        assert not central.uploaded_tones()
        assert central.effective_tones()[g]['class'].startswith('class-')    # back to a default


def test_stage_without_program_and_inactive_stage(admin, app):
    import central
    admin.post('/admin/stages/add', data={'name_ar': 'الروضة'})
    with app.app_context():
        sid = Stage.query.one().id
    for kind, dur in [('class', 40), ('break', 15), ('class', 40)]:
        admin.post(f'/admin/stages/{sid}/periods/add', data={'kind': kind, 'duration': dur})
    with app.app_context():
        assert [g['key'] for g in central.groups()] == [f'stage:{sid}']
        assert [s['t'] for s in central.week_rings()[6]] == ['08:00', '08:40', '08:55']
        Stage.query.get(sid).active = False
        from models import db
        db.session.commit()
        assert central.groups() == [] and central.week_rings()[6] == []


def test_import_render_copies_everything(tmp_path):
    """ops/import_render.py from an old database file keeps ids and codes."""
    import subprocess
    root = os.path.dirname(os.path.dirname(__file__))
    old = tmp_path / 'old.db'
    a = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{old}'})
    c = a.test_client()
    c.post('/admin/login', data={'username': 'admin', 'password': 'admin'})
    _seed(c, a)
    c.post('/admin/teachers/add', data={'names': 'منى'})
    from models import Teacher
    with a.app_context():
        code = Teacher.query.one().code
    env = dict(os.environ, DATA_DIR=str(tmp_path / 'data'), DATABASE_URL='')
    env.pop('DATABASE_URL')
    r = subprocess.run([sys.executable, os.path.join(root, 'ops', 'import_render.py'), str(old), '--yes'],
                       capture_output=True, text=True, env=env, cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    import sqlite3
    con = sqlite3.connect(tmp_path / 'data' / 'bell.db')
    assert con.execute('select code from teachers').fetchone()[0] == code
    assert con.execute('select count(*) from bell_programs').fetchone()[0] == 10
