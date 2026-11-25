"""رنّة نهاية الدوام: عند نهاية آخر حصة في اليوم لكل برنامج، وتتبع اليوم (الثلاثاء غير بقية الأيام)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app  # noqa: E402
from tests.test_central import _seed  # noqa: E402

SUN, TUE = 6, 1          # الاثنين = 0 (انظر week_rings)


@pytest.fixture
def env(tmp_path):
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path}/e.db'})
    admin = app.test_client()
    admin.post('/admin/login', data={'username': 'admin', 'password': 'admin'})
    _seed(admin, app)
    return app, admin


def _times(week, wd):
    return {s['t']: s for s in week[wd]}


def _last_end(app, wd):
    from models import BellProgram
    with app.app_context():
        import central
        ends = []
        for st in central._active_stages():
            for g in st.grades:
                prog = g.program_for(wd)
                if prog:
                    ends.append(max((p.end_time for p in prog.periods), key=central._minutes))
        return set(ends)


def test_no_end_ring_by_default(env):
    app, admin = env
    import central
    with app.app_context():
        week = central.week_rings()
        for wd in (SUN, TUE):
            for end in _last_end(app, wd):
                kinds = {r['kind'] for r in _times(week, wd).get(end, {'rings': []})['rings']}
                assert 'custom' not in kinds


def test_end_ring_follows_each_programs_last_period_and_the_day(env):
    app, admin = env
    import central
    r = admin.post('/admin/central/save', data={'end_seen': '1', 'end_on': '1', 'end_tone': 'class-2'})
    assert r.status_code == 302
    with app.app_context():
        assert central.config()['end_ring'] == {'enabled': True, 'tone': 'class-2', 'repeat': 1}
        week = central.week_rings()
        for wd in (SUN, TUE):
            ends = _last_end(app, wd)
            assert ends
            for end in ends:
                slot = _times(week, wd)[end]
                ring = next(r for r in slot['rings'] if r['kind'] == 'custom' and r['label'] == 'نهاية الدوام')
                assert ring['tone'] == 'class-2' and ring['groups']
        assert _last_end(app, SUN) != _last_end(app, TUE)            # الثلاثاء ينتهي قبل بقية الأيام: أوقات مختلفة
        assert week[4] == [] and week[5] == []                       # لا رنّة في الجمعة والسبت


def test_saving_other_settings_keeps_the_end_ring(env):
    app, admin = env
    import central
    admin.post('/admin/central/save', data={'end_seen': '1', 'end_on': '1', 'end_tone': 'class-1'})
    admin.post('/admin/central/save', data={'volume': '60'})                 # نموذج لا يحمل خانة النهاية
    with app.app_context():
        assert central.config()['end_ring']['enabled'] is True


def test_unchecking_turns_it_off_and_a_device_gets_only_its_groups(env):
    app, admin = env
    import central
    admin.post('/admin/central/save', data={'end_seen': '1', 'end_on': '1', 'end_tone': 'class-2'})
    with app.app_context():
        keys = [g['key'] for g in central.groups()]
        only = {keys[0]}
        week = central.week_rings(only=only)
        groups = {g for wd in week.values() for s in wd for r in s['rings'] if r['kind'] == 'custom' for g in r['groups']}
        assert len(groups) == 1                                              # جهاز لمجموعة واحدة: رنّة نهايتها وحدها
        payload = central.player_payload()
        assert 'class-2' in payload['tones']
    admin.post('/admin/central/save', data={'end_seen': '1', 'end_tone': 'class-2'})      # أُزيلت العلامة
    with app.app_context():
        assert central.config()['end_ring']['enabled'] is False
        assert not any(r['kind'] == 'custom' for wd in central.week_rings().values() for s in wd for r in s['rings'])


def test_the_admin_page_shows_the_setting(env):
    app, admin = env
    html = admin.get('/admin/central').get_data(as_text=True)
    assert 'name="end_on"' in html and 'name="end_tone"' in html and 'رنّة نهاية الدوام' in html


# ── ترتيب أبجدي حسب لغة الواجهة ────────────────────────────────────────────────
def test_alphabetical_order_follows_the_interface_language():
    import services

    class T:
        def __init__(self, ar, en=None):
            self.name_ar, self.name_en = ar, en
    rows = [T('بلال', 'Bilal'), T('أحمد', 'Ahmad'), T('إبراهيم', 'Ibrahim'), T('آمنة', 'Amna'), T('زياد')]
    assert [t.name_ar for t in services.by_name(rows, 'ar')] == ['إبراهيم', 'أحمد', 'آمنة', 'بلال', 'زياد']   # أ/إ/آ = ا
    assert [t.name_en or t.name_ar for t in services.by_name(rows, 'en')] == ['Ahmad', 'Amna', 'Bilal', 'Ibrahim', 'زياد']
    assert services.alpha_key('أَحْمَد') == services.alpha_key('احمد')                                       # بلا تشكيل


def test_teacher_lists_are_sorted_in_each_language(env):
    app, admin = env
    from models import Teacher, db
    with app.app_context():
        for ar, en, code in (('بلال', 'Bilal', 'T001'), ('أحمد', 'Ahmad', 'T002'), ('إبراهيم', 'Ibrahim', 'T003')):
            db.session.add(Teacher(name_ar=ar, name_en=en, code=code, email=f'{code}@x.test'))
        db.session.commit()
    ar = admin.get('/admin/teachers').get_data(as_text=True)
    assert ar.index('إبراهيم') < ar.index('أحمد') < ar.index('بلال')
    with admin.session_transaction() as s:
        s['lang'] = 'en'
    en = admin.get('/admin/teachers').get_data(as_text=True)
    assert en.index('Ahmad') < en.index('Bilal') < en.index('Ibrahim')
