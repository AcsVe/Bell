"""اشتراك النسخة المستضافة (license.py): الحالات، وما يبقى مفتوحاً بعد الإيقاف، والربط بالملف المشترك."""
import json
import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import license as L  # noqa: E402
from app import create_app  # noqa: E402

KEYS = ('LICENSE_UNTIL', 'LICENSE_WARN_DAYS', 'LICENSE_GRACE_DAYS', 'LICENSE_CONTACT', 'LICENSE_FILE', 'LICENSE_ID')


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path}/t.db'})


def until(monkeypatch, days, **env):
    monkeypatch.setenv('LICENSE_UNTIL', (L._today() + timedelta(days=days)).isoformat())
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def admin_client(app):
    c = app.test_client()
    assert c.post('/admin/login', data={'username': 'admin', 'password': 'admin'}).status_code == 302
    return c


def test_without_a_date_nothing_changes(app):
    c = admin_client(app)
    assert L.status()['state'] == 'none'
    assert c.get('/admin/').status_code in (200, 302)
    assert b'license-alert' not in c.get('/admin/settings').data


def test_states_follow_the_dates(monkeypatch):
    until(monkeypatch, 100); assert L.status()['state'] == 'ok'
    until(monkeypatch, 10); assert L.status()['state'] == 'warn'
    until(monkeypatch, -3); assert L.status()['state'] == 'grace'
    until(monkeypatch, -9); assert L.status()['state'] == 'expired'
    until(monkeypatch, -3, LICENSE_GRACE_DAYS='1'); assert L.status()['state'] == 'expired'


def test_invalid_date_is_ignored(monkeypatch):
    monkeypatch.setenv('LICENSE_UNTIL', 'not-a-date')
    assert L.status()['state'] == 'none'


def test_banner_in_the_admin_panel_before_the_end_and_in_the_grace(app, monkeypatch):
    c = admin_client(app)
    until(monkeypatch, 10, LICENSE_CONTACT='0790000000')
    page = c.get('/admin/settings').data.decode()
    assert 'license-alert' in page and '0790000000' in page
    until(monkeypatch, -3)
    assert 'license-alert' in c.get('/admin/settings').data.decode()


def test_expired_pauses_the_pages_but_keeps_sign_in_and_the_devices_open(app, monkeypatch):
    c = admin_client(app)
    until(monkeypatch, -9, LICENSE_CONTACT='0790000000')
    r = c.get('/admin/settings')
    assert r.status_code == 403 and '0790000000' in r.data.decode()
    assert c.get('/app').status_code == 403
    assert c.get('/login').status_code == 403
    assert c.post('/admin/teachers/add', data={'names': 'x'}).status_code == 403
    assert app.test_client().get('/admin/login').status_code == 200          # الدخول مفتوح
    assert app.test_client().get('/healthz').status_code == 200
    # الأجهزة والتطبيقات: لا تتوقف (الجرس الفعلي يستمر)
    assert app.test_client().get('/api/central/schedule').status_code != 403
    assert app.test_client().post('/api/central/poll', json={}).status_code != 403
    assert app.test_client().get('/api/schedule').status_code != 403
    assert app.test_client().get('/media/1').status_code != 403


def test_renewing_brings_it_back_at_once(app, monkeypatch):
    c = admin_client(app)
    until(monkeypatch, -9)
    assert c.get('/admin/settings').status_code == 403
    until(monkeypatch, 100)
    assert c.get('/admin/settings').status_code == 200


def test_locked_page_speaks_english_on_request(app, monkeypatch):
    until(monkeypatch, -9)
    page = app.test_client().get('/app?lang=en').data.decode()
    assert 'The subscription for this system has ended' in page


def test_shared_file_wins_over_env_and_applies_without_a_restart(app, monkeypatch, tmp_path):
    f = tmp_path / 'licenses.json'
    monkeypatch.setenv('LICENSE_FILE', str(f)); monkeypatch.setenv('LICENSE_ID', 'rasb')
    assert L.status()['state'] == 'none'                                      # الملف مفقود: يرجع إلى .env
    f.write_text(json.dumps({'rasb': {'until': (L._today() - timedelta(days=9)).isoformat(), 'contact': 'c-1'}}))
    assert L.status()['state'] == 'expired' and L.status()['contact'] == 'c-1'
    c = admin_client(app)
    assert c.get('/admin/settings').status_code == 403
    f.write_text(json.dumps({'rasb': {'until': (L._today() + timedelta(days=100)).isoformat()}}))
    assert L.status()['state'] == 'ok' and c.get('/admin/settings').status_code == 200
    f.write_text(json.dumps({'other': {'until': '2020-01-01'}}))
    assert L.status()['state'] == 'none'                                      # مدخل نظام آخر لا يؤثر
    monkeypatch.setenv('LICENSE_UNTIL', '2020-01-01')
    assert L.status()['state'] == 'none'                                      # الملف أقوى من .env
    f.write_text('{not json')
    assert L.status()['state'] == 'expired'                                   # ملف تالف: يرجع إلى .env ولا ينهار
