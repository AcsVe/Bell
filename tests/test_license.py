"""اشتراك النسخة المستضافة والنسخة الكاملة من البيانات (license_gate.py)."""
import io
import os
import sys
import zipfile
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app  # noqa: E402
import license_gate as L  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv('LICENSE_UNTIL', raising=False)
    monkeypatch.setenv('PAGE_SHIELD', '0')
    return create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path}/t.db'})


def _admin(app):
    c = app.test_client()
    assert c.post('/admin/login', data={'username': 'admin', 'password': 'admin'}).status_code == 302
    return c


def _until(monkeypatch, days):
    monkeypatch.setenv('LICENSE_UNTIL', (date.today() + timedelta(days=days)).isoformat())


def test_states(monkeypatch):
    t = date(2027, 1, 10)
    monkeypatch.delenv('LICENSE_UNTIL', raising=False)
    assert L.status(t)['state'] == 'none'
    for until, state in [('2027-03-01', 'ok'), ('2027-01-30', 'warn'), ('2027-01-10', 'warn'),
                         ('2027-01-05', 'grace'), ('2027-01-02', 'expired'), ('bad', 'none')]:
        monkeypatch.setenv('LICENSE_UNTIL', until)
        assert L.status(t)['state'] == state, until


def test_no_license_changes_nothing(app):
    c = _admin(app)
    assert c.get('/admin/').status_code == 200
    assert 'licenseBanner' not in c.get('/admin/').get_data(as_text=True)


def test_warn_banner_for_admin_only(app, monkeypatch):
    _until(monkeypatch, 10)
    assert 'licenseBanner' in _admin(app).get('/admin/').get_data(as_text=True)
    assert 'licenseBanner' not in app.test_client().get('/admin/login').get_data(as_text=True)


def test_expired_locks_but_keeps_login_and_export(app, monkeypatch):
    _until(monkeypatch, -30)
    anon = app.test_client()
    assert anon.get('/admin/login').status_code == 200
    r = anon.get('/')
    assert r.status_code == 403 and 'dir="rtl"' in r.get_data(as_text=True)
    assert anon.get(L.EXPORT_PATH).status_code == 302          # to the sign-in page
    c = _admin(app)
    r = c.get('/admin/')
    assert r.status_code == 403 and L.EXPORT_PATH in r.get_data(as_text=True)
    r = c.get(L.EXPORT_PATH)
    assert r.status_code == 200 and r.mimetype == 'application/zip'
    names = zipfile.ZipFile(io.BytesIO(r.get_data())).namelist()
    assert 'README.txt' in names and any(n.startswith('csv/') for n in names)
    assert any(n.startswith('database/') for n in names)
    r.close()


def test_export_leaves_out_secret_columns(app):
    c = _admin(app)
    r = c.get(L.EXPORT_PATH)
    zf = zipfile.ZipFile(io.BytesIO(r.get_data()))
    for n in zf.namelist():
        if n.startswith('csv/'):
            header = zf.read(n).decode('utf-8-sig').splitlines()[0].lower()
            assert 'password' not in header and 'secret' not in header, n
    r.close()
