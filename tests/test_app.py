import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app  # noqa: E402
from models import (db, Device, Grade, GradePeriodTime, Period, Section, Stage,  # noqa: E402
                    StageDayPeriodTime, Teacher, TeacherSection)


@pytest.fixture
def app(tmp_path):
    app = create_app({'TESTING': True,
                      'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path}/t.db'})
    yield app


@pytest.fixture
def admin(app):
    c = app.test_client()
    r = c.post('/admin/login', data={'username': 'admin', 'password': 'admin'})
    assert r.status_code == 302
    return c


def _setup_school(admin, app):
    admin.post('/admin/stages/add', data={'name_ar': 'المرحلة الأساسية 1-3', 'name_en': 'Primary 1-3'})
    with app.app_context():
        st = Stage.query.one()
        sid = st.id
        assert sorted(st.weekday_list) == [0, 1, 2, 3, 6]
    for kind, dur in [('class', 45), ('class', 45), ('break', 20), ('class', 45)]:
        admin.post(f'/admin/stages/{sid}/periods/add', data={'kind': kind, 'duration': dur})
    admin.post(f'/admin/stages/{sid}/grades/add', data={'names': 'الصف الأول، الصف الثاني'})
    with app.app_context():
        g1 = Grade.query.filter_by(name_ar='الصف الأول').one()
        gid = g1.id
    admin.post(f'/admin/grades/{gid}/sections/add', data={'names': 'أ، ب'})
    admin.post('/admin/teachers/add', data={'names': 'أحمد\nسارة\n'})
    return sid, gid


def test_admin_crud_and_labels(admin, app):
    sid, gid = _setup_school(admin, app)
    with app.app_context():
        labels = [(p.kind, p.label_ar, p.label_en) for p in Period.query.order_by(Period.number)]
        assert labels == [('class', 'الحصة الأولى', 'Period 1'), ('class', 'الحصة الثانية', 'Period 2'),
                          ('break', 'الاستراحة', 'Break'), ('class', 'الحصة الثالثة', 'Period 3')]
        secs = Section.query.all()
        assert len(secs) == 2 and all(len(s.code) == 6 for s in secs)
        ts = Teacher.query.all()
        codes = {s.code for s in secs} | {t.code for t in ts}
        assert len(codes) == 4   # unique across both tables
    # every admin page renders in both languages
    for lang in ('ar', 'en'):
        admin.get(f'/lang/{lang}')
        for url in ['/admin/', f'/admin/stages/{sid}', f'/admin/grades/{gid}', '/admin/teachers',
                    '/admin/codes', '/admin/settings', f'/admin/stages/{sid}?day=3',
                    f'/admin/grades/{gid}?day=3']:
            r = admin.get(url)
            assert r.status_code == 200, url
        with app.app_context():
            s1 = Section.query.first().id
            t1 = Teacher.query.first().id
        assert admin.get(f'/admin/sections/{s1}').status_code == 200
        assert admin.get(f'/admin/teachers/{t1}').status_code == 200


def test_teacher_dropdown_inactive_after_link(admin, app):
    sid, gid = _setup_school(admin, app)
    with app.app_context():
        s1, s2 = [s.id for s in Section.query.order_by(Section.id)]
        t = Teacher.query.filter_by(name_ar='أحمد').one().id
    admin.post(f'/admin/sections/{s1}/teachers/add', data={'teacher_id': t})
    admin.post(f'/admin/sections/{s1}/teachers/add', data={'teacher_id': t})   # duplicate ignored
    html = admin.get(f'/admin/sections/{s1}').get_data(as_text=True)
    assert f'value="{t}" disabled' in html
    html2 = admin.get(f'/admin/sections/{s2}').get_data(as_text=True)
    assert f'value="{t}" disabled' not in html2 and f'value="{t}"' in html2
    with app.app_context():
        assert TeacherSection.query.count() == 1


def _times(rows):
    return [(r['start'], r['end']) for r in rows]


def test_chained_times_and_overrides(admin, app):
    sid, gid = _setup_school(admin, app)
    from services import resolve_grade_day
    with app.app_context():
        g = db.session.get(Grade, gid)
        assert _times(resolve_grade_day(g, 6)) == [('08:00', '08:45'), ('08:45', '09:30'),
                                                   ('09:30', '09:50'), ('09:50', '10:35')]
        pids = [p.id for p in Period.query.order_by(Period.number)]
    # Thursday: whole stage has 40-minute classes
    admin.post(f'/admin/stages/{sid}/day-times', data={'weekday': 3, 'day_start': '08:00',
               'n1_dur': 40, 'n2_dur': 40, 'n3_dur': 20, 'n4_dur': 40})
    # Grade 1 on Thursday starts later
    admin.post(f'/admin/grades/{gid}/times', data={'weekday': 3, 'day_start': '08:30',
               'n1_dur': 40, 'n2_dur': 40, 'n3_dur': 20, 'n4_dur': 40})
    with app.app_context():
        g = db.session.get(Grade, gid)
        g2 = Grade.query.filter_by(name_ar='الصف الثاني').one()
        assert _times(resolve_grade_day(g2, 3)) == [('08:00', '08:40'), ('08:40', '09:20'),
                                                    ('09:20', '09:40'), ('09:40', '10:20')]
        thu = resolve_grade_day(g, 3)
        assert thu[0]['source'] == 'grade' and thu[-1]['end'] == '10:50'
        assert resolve_grade_day(g, 6)[0]['source'] == 'default'
    # changing the default duration of period 1 shifts every later default period
    form = {'day_start': '07:45'}
    for pid, d in zip(pids, [50, 45, 20, 45]):
        form.update({f'p{pid}_duration': d, f'p{pid}_kind': 'break' if d == 20 else 'class'})
    admin.post(f'/admin/stages/{sid}/periods/save', data=form)
    with app.app_context():
        g = db.session.get(Grade, gid)
        assert _times(resolve_grade_day(g, 6))[-1] == ('09:40', '10:25')
        assert resolve_grade_day(g, 3)[-1]['end'] == '10:50'     # override untouched
    # adding a period extends every override set with the default duration, gap-free
    admin.post(f'/admin/stages/{sid}/periods/add', data={'kind': 'class', 'duration': 45})
    with app.app_context():
        g = db.session.get(Grade, gid)
        thu = resolve_grade_day(g, 3)
        assert thu[-1]['start'] == '10:50' and thu[-1]['end'] == '11:35' and thu[-1]['source'] == 'grade'
        for wd in (3, 6):
            rows = resolve_grade_day(g, wd)
            assert all(a['end'] == b['start'] for a, b in zip(rows, rows[1:]))
    # deleting the break moves later periods up, in defaults and overrides
    admin.post(f'/admin/periods/{pids[2]}/delete')
    with app.app_context():
        g = db.session.get(Grade, gid)
        for wd in (3, 6):
            rows = resolve_grade_day(g, wd)
            assert len(rows) == 4 and all(a['end'] == b['start'] for a, b in zip(rows, rows[1:]))
        assert resolve_grade_day(g, 3)[2]['start'] == '09:50'
    # reset returns grade 1 to the stage-day timing; all_days writes every school day
    admin.post(f'/admin/grades/{gid}/times', data={'weekday': 3, 'reset': '1'})
    with app.app_context():
        assert resolve_grade_day(db.session.get(Grade, gid), 3)[0]['source'] == 'stage'
    admin.post(f'/admin/grades/{gid}/times', data={'weekday': 6, 'all_days': '1', 'day_start': '08:00',
               'n1_dur': 30, 'n2_dur': 30, 'n4_dur': 30, 'n5_dur': 30})
    with app.app_context():
        assert GradePeriodTime.query.filter_by(grade_id=gid).count() == 5 * 4
    # invalid input is rejected without changes
    admin.post(f'/admin/stages/{sid}/day-times', data={'weekday': 0, 'day_start': '23:00',
               'n1_dur': 90, 'n2_dur': 90, 'n4_dur': 90, 'n5_dur': 90})
    with app.app_context():
        assert StageDayPeriodTime.query.filter_by(weekday=0).count() == 0


def test_code_login_schedule_and_versioning(admin, app):
    sid, gid = _setup_school(admin, app)
    with app.app_context():
        sec = Section.query.order_by(Section.id).first()
        s_code, s_id = sec.code, sec.id
        t = Teacher.query.filter_by(name_ar='سارة').one()
        t_code, t_id = t.code, t.id
        s_ids = [s.id for s in Section.query.all()]
    for s in s_ids:
        admin.post(f'/admin/sections/{s}/teachers/add', data={'teacher_id': t_id})

    dev = app.test_client()
    assert dev.get('/api/schedule').status_code == 401
    r = dev.post('/login', data={'code': 'zzzzzz'})
    assert r.status_code == 200 and 'data-error="invalid"' in r.get_data(as_text=True)
    r = dev.post('/login', data={'code': ' ' + s_code.lower()[:3] + '-' + s_code.lower()[3:]})
    assert r.status_code == 302 and r.headers['Location'].endswith('/app')
    r = dev.get('/api/schedule', headers={'X-Device-Id': 'dev-1'})
    data = r.get_json()
    assert data['who']['type'] == 'section'
    assert len(data['targets']) == 1 and data['targets'][0]['sectionId'] == s_id
    day = data['days'][str(gid)]['6']
    assert [p['ar'] for p in day] == ['الحصة الأولى', 'الحصة الثانية', 'الاستراحة', 'الحصة الثالثة']
    assert data['tone'].endswith('chime.wav')
    v, k = data['version'], data['key']
    assert k == f'section:{s_id}'
    assert dev.get(f'/api/schedule?v={v}&k={k}').get_json() == {'unchanged': True, 'version': v}
    # same version but a different identity (new code on the device) → full payload
    assert 'unchanged' not in dev.get(f'/api/schedule?v={v}&k=teacher:1').get_json()
    admin.post(f'/admin/stages/{sid}/periods/save', data={})   # any change bumps version
    assert 'unchanged' not in dev.get(f'/api/schedule?v={v}&k={k}').get_json()
    with app.app_context():
        assert Device.query.filter_by(device_uid='dev-1', code_type='section', owner_id=s_id).count() == 1

    # teacher code covers all linked sections
    tdev = app.test_client()
    tdev.post('/login', data={'code': t_code})
    td = tdev.get('/api/schedule').get_json()
    assert td['who']['type'] == 'teacher' and len(td['targets']) == 2

    # regenerating the code invalidates the old session
    admin.post(f'/admin/sections/{s_id}/regen')
    assert dev.get('/api/schedule').status_code == 401
    with app.app_context():
        assert Device.query.filter_by(owner_id=s_id, code_type='section').count() == 0

    # disabled teacher is rejected
    admin.post(f'/admin/teachers/{t_id}/update', data={'name_ar': 'سارة'})
    assert tdev.get('/api/schedule').status_code == 401


def test_media_and_tone_upload(admin, app):
    sid, _ = _setup_school(admin, app)
    png = b'\x89PNG\r\n\x1a\n' + b'0' * 20
    admin.post(f'/admin/stages/{sid}/media', data={'logo': (io.BytesIO(png), 'logo.png', 'image/png')},
               content_type='multipart/form-data')
    with app.app_context():
        url = db.session.get(Stage, sid).logo.url
    r = admin.get(url)
    assert r.status_code == 200 and r.data == png
    # wrong type rejected
    admin.post(f'/admin/stages/{sid}/media', data={'background': (io.BytesIO(b'x'), 'a.txt', 'text/plain')},
               content_type='multipart/form-data')
    with app.app_context():
        assert db.session.get(Stage, sid).background is None
    admin.post('/admin/settings', data={'tone': (io.BytesIO(b'RIFF....'), 'bell.wav', 'audio/wav')},
               content_type='multipart/form-data')
    from models import AppSetting
    with app.app_context():
        assert AppSetting.get('tone_id')
    admin.post(f'/admin/stages/{sid}/media', data={'remove_logo': '1'}, content_type='multipart/form-data')
    with app.app_context():
        assert db.session.get(Stage, sid).logo is None


def test_delete_cascades(admin, app):
    sid, gid = _setup_school(admin, app)
    with app.app_context():
        s1 = Section.query.first().id
        t = Teacher.query.first().id
    admin.post(f'/admin/sections/{s1}/teachers/add', data={'teacher_id': t})
    admin.post(f'/admin/stages/{sid}/delete')
    with app.app_context():
        assert Stage.query.count() == 0 and Section.query.count() == 0
        assert TeacherSection.query.count() == 0 and Teacher.query.count() == 2


def test_pwa_routes(app):
    c = app.test_client()
    r = c.get('/sw.js')
    assert r.status_code == 200 and 'no-cache' in r.headers['Cache-Control']
    assert c.get('/manifest.webmanifest').status_code == 200
    assert c.get('/app').status_code == 200
    assert c.get('/login').status_code == 200
    assert c.get('/static/sounds/chime.wav').status_code == 200


def test_native_header_auth_and_cors(admin, app):
    _setup_school(admin, app)
    with app.app_context():
        code = Section.query.first().code
    c = app.test_client()   # no cookies at all, like a native app
    r = c.options('/api/schedule', headers={'Origin': 'capacitor://localhost',
                  'Access-Control-Request-Headers': 'x-bell-code'})
    assert r.status_code == 204 and r.headers['Access-Control-Allow-Origin'] == '*'
    assert 'X-Bell-Code' in r.headers['Access-Control-Allow-Headers']
    assert c.post('/api/login', json={'code': 'nope'}).status_code == 401
    r = c.post('/api/login', json={'code': code.lower()})
    assert r.status_code == 200 and r.get_json()['code'] == code and r.get_json()['type'] == 'section'
    r = c.get('/api/schedule', headers={'X-Bell-Code': code, 'X-Device-Id': 'android-1'})
    assert r.status_code == 200 and r.get_json()['who']['type'] == 'section'
    assert r.headers['Access-Control-Allow-Origin'] == '*'
    assert c.get('/api/schedule', headers={'X-Bell-Code': 'WRONG1'}).status_code == 401
    # admin pages never get CORS headers
    assert 'Access-Control-Allow-Origin' not in admin.get('/admin/').headers
