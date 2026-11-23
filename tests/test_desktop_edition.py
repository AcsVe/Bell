"""نسخة سطح المكتب (winapp/bell_desktop.py): جهاز الرنين المضمَّن، وصفحة الترخيص، وتنبيه «الجرس لا يرنّ»، وإقلاع المشغّل.

التوقيع هنا بحساب ECDSA P-256 بايثون صرف (دوال المشغّل نفسها) فلا تلزم مكتبة خارجية.
"""
import base64
import hashlib
import importlib.util
import os
import re
import secrets
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FP = '0123456789ABCDEF'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _keys(bp):
    """زوج مفاتيح مؤقت: → (PEM العام، دالة sign(بصمة، انتهاء) ← مفتاح ترخيص بصيغة المولّد «EXP:التوقيع»)."""
    d = secrets.randbelow(bp._N - 1) + 1
    qx, qy = bp._ec_mul(d, bp._G)
    der = bytes.fromhex('3059301306072a8648ce3d020106082a8648ce3d030107034200') + b'\x04' + qx.to_bytes(32, 'big') + qy.to_bytes(32, 'big')
    pem = '-----BEGIN PUBLIC KEY-----\n' + base64.b64encode(der).decode() + '\n-----END PUBLIC KEY-----'

    def enc(v):
        b = v.to_bytes((v.bit_length() + 7) // 8 or 1, 'big')
        b = b'\x00' + b if b[0] & 0x80 else b
        return b'\x02' + bytes([len(b)]) + b

    def sign(fp, exp='PERMANENT'):
        z = int.from_bytes(hashlib.sha256(f'{fp}|{exp}'.encode()).digest(), 'big')
        while True:
            k = secrets.randbelow(bp._N - 1) + 1
            r = bp._ec_mul(k, bp._G)[0] % bp._N
            s = pow(k, -1, bp._N) * (z + r * d) % bp._N
            if r and s:
                break
        body = enc(r) + enc(s)
        sig = b'\x30' + bytes([len(body)]) + body
        return exp + ':' + base64.urlsafe_b64encode(sig).decode().rstrip('=')
    return pem, sign


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('BELL_DESKTOP_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('BELL_EDITION', 'desktop')
    monkeypatch.setenv('PAGE_SHIELD', '0')
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'home' / 'data'))
    monkeypatch.setenv('BELL_DB_PATH', str(tmp_path / 'home' / 'data' / 'bell.db'))
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'home' / 'data' / 'bell.db').replace('\\', '/'))
    os.makedirs(tmp_path / 'home' / 'data')
    os.makedirs(tmp_path / 'home' / 'player')
    from app import create_app
    app = create_app({'TESTING': True})
    launcher = _load('bell_desktop_under_test', os.path.join(ROOT, 'winapp', 'bell_desktop.py'))
    bp = _load('bell_player_desk', os.path.join(ROOT, 'bell_player', 'bell_player.py'))
    pdir = str(tmp_path / 'home' / 'player')
    monkeypatch.setattr(bp, 'BASE', pdir)
    monkeypatch.setattr(bp, 'CACHE', os.path.join(pdir, 'player_cache'))
    monkeypatch.setattr(bp, 'INI', os.path.join(pdir, 'bell_player.ini'))
    os.makedirs(bp.CACHE)
    pem, sign = _keys(bp)
    monkeypatch.setattr(bp, 'LICENSE_PUBLIC_KEY_PEM', pem)
    app.config['BELL_PLAYER'] = bp
    app.config['BELL_FP'] = FP
    key = launcher.provision_player(app, str(tmp_path / 'home'))
    bp.save_ini('http://127.0.0.1:1', key)
    c = app.test_client()
    c.post('/admin/login', data={'username': 'admin', 'password': 'admin'})
    return app, c, bp, sign, launcher, key, str(tmp_path / 'home')


def test_player_device_is_created_once_and_keeps_other_devices(desk):
    app, c, bp, sign, launcher, key, home = desk
    import central
    with app.app_context():
        assert central.check_key(key) is not None
        other_pid, other_key = central.add_player('جهاز آخر')
        from models import db
        db.session.commit()
    assert launcher.provision_player(app, home) == key                  # المفتاح ثابت بين التشغيلات
    with app.app_context():
        assert central.check_key(other_key) == other_pid               # ولا يُمسّ جهاز المستخدم الآخر
    os.remove(os.path.join(home, 'player', 'player.key'))              # فُقد الملف: يُجدَّد مفتاح هذا الجهاز فقط
    new = launcher.provision_player(app, home)
    assert new != key
    with app.app_context():
        assert central.check_key(new) and central.check_key(other_key) == other_pid and central.check_key(key) is None


def test_the_device_can_read_the_schedule(desk):
    app, c, bp, sign, launcher, key, home = desk
    r = app.test_client().get('/api/central/schedule', headers={'X-Bell-Key': key})
    assert r.status_code == 200


def test_not_licensed_banner_and_page(desk):
    app, c, bp, sign, launcher, key, home = desk
    page = c.get('/admin/central').get_data(as_text=True)
    assert 'الجرس لا يرنّ' in page and 'license-alert' in page
    lic = c.get('/admin/license').get_data(as_text=True)
    assert FP in lic and 'بلا ترخيص' in lic and 'الجرس لا يرنّ حتى تُدخل مفتاحاً صالحاً' in lic


def test_invalid_expired_and_valid_keys(desk):
    app, c, bp, sign, launcher, key, home = desk
    r = c.post('/admin/license/save', data={'key': 'PERMANENT:bad'}, follow_redirects=True)
    assert 'غير صالح' in r.get_data(as_text=True)
    r = c.post('/admin/license/save', data={'key': sign('FFFFFFFFFFFFFFFF')}, follow_redirects=True)      # لجهاز آخر
    assert 'غير صالح' in r.get_data(as_text=True)
    r = c.post('/admin/license/save', data={'key': sign(FP, '20000101')}, follow_redirects=True)
    assert 'انتهى هذا المفتاح' in r.get_data(as_text=True)
    assert bp.load_ini()['license'] == ''
    good = sign(FP)
    r = c.post('/admin/license/save', data={'key': good}, follow_redirects=True)
    text = r.get_data(as_text=True)
    assert 'حُفظ الترخيص' in text and 'مرخّص' in text and 'license-alert' not in text
    assert bp.load_ini()['license'] == good
    assert 'الجرس لا يرنّ' not in c.get('/admin/central').get_data(as_text=True)      # التنبيه اختفى
    # الإعدادات المحفوظة (الخادم والمفتاح) لم تتغيّر بحفظ الترخيص
    assert bp.load_ini()['key'] == key and bp.load_ini()['server'] == 'http://127.0.0.1:1'


def test_expiring_key_shows_the_date(desk):
    app, c, bp, sign, launcher, key, home = desk
    c.post('/admin/license/save', data={'key': sign(FP, '20991231')})
    assert '2099-12-31' in c.get('/admin/license').get_data(as_text=True)


def test_the_license_page_needs_the_admin_sign_in(desk):
    app, c, bp, sign, launcher, key, home = desk
    anon = app.test_client()
    assert anon.get('/admin/license').status_code in (301, 302)
    assert anon.post('/admin/license/save', data={'key': sign(FP)}).status_code in (301, 302)
    assert bp.load_ini()['license'] == ''


def test_the_player_rings_only_with_a_valid_license(desk):
    app, c, bp, sign, launcher, key, home = desk
    p = bp.Player(bp.load_ini())
    p.fp = FP
    assert p.licensed() is False
    bp.save_ini(bp.load_ini()['server'], key, license_key=sign(FP))
    p._lic_t = 0                                                          # المشغّل يعيد قراءة الملف خلال 30 ثانية
    assert p.licensed() is True


def test_the_ordinary_server_has_no_license_page(tmp_path, monkeypatch):
    monkeypatch.delenv('BELL_EDITION', raising=False)
    from app import create_app
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path}/n.db'})
    c = app.test_client()
    c.post('/admin/login', data={'username': 'admin', 'password': 'admin'})
    assert c.get('/admin/license').status_code == 404
    assert 'الجرس لا يرنّ' not in c.get('/admin/central').get_data(as_text=True)


# ── اختيار كلمة المرور عند أول زيارة ─────────────────────────────────────────────
@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """تثبيت جديد: لم تُختر كلمة مرور بعد، فكلمة المدير عشوائية في الذاكرة فقط."""
    monkeypatch.setenv('BELL_EDITION', 'desktop')
    monkeypatch.setenv('PAGE_SHIELD', '0')
    monkeypatch.setenv('ADMIN_PASS', 'random-unknown-' + secrets.token_hex(8))
    from app import create_app
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path}/f.db'})
    saved = []
    app.config['BELL_NEEDS_SETUP'] = True
    app.config['BELL_SAVE_PASSWORD'] = saved.append
    return app, saved


def test_first_visit_asks_for_a_password_on_this_computer(fresh):
    app, saved = fresh
    r = app.test_client().get('/admin/login')
    assert r.status_code == 302 and r.headers['Location'].endswith('/admin/setup')
    page = app.test_client().get('/admin/setup').get_data(as_text=True)
    assert 'اختر كلمة مرور' in page and 'name="again"' in page


def test_setup_checks_the_password_then_signs_in_and_closes(fresh):
    app, saved = fresh
    c = app.test_client()
    r = c.post('/admin/setup', data={'password': 'short', 'again': 'short'})
    assert 'أقصر من 8' in r.get_data(as_text=True) and saved == []
    r = c.post('/admin/setup', data={'password': 'long-enough-1', 'again': 'long-enough-2'})
    assert 'غير متطابقتين' in r.get_data(as_text=True) and saved == []
    r = c.post('/admin/setup', data={'password': 'long-enough-1', 'again': 'long-enough-1'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/admin/license')
    assert saved == ['long-enough-1']
    assert c.get('/admin/').status_code == 200                              # دخل تلقائياً
    other = app.test_client()
    assert other.get('/admin/login').status_code == 200                      # الصفحة عادت صفحة دخول عادية
    assert other.get('/admin/setup').status_code == 302                      # وأُغلقت صفحة الاختيار
    r = other.post('/admin/login', data={'username': 'admin', 'password': 'long-enough-1'})
    assert r.status_code == 302 and '/admin/login' not in r.headers['Location']


def test_setup_is_closed_to_other_computers_and_the_random_password_is_useless(fresh):
    app, saved = fresh
    far = {'REMOTE_ADDR': '10.0.0.7'}
    c = app.test_client()
    r = c.get('/admin/login', environ_overrides=far)
    assert r.status_code == 200 and 'name="again"' not in r.get_data(as_text=True)      # صفحة دخول عادية لا اختيار
    r = c.post('/admin/setup', data={'password': 'long-enough-1', 'again': 'long-enough-1'}, environ_overrides=far)
    assert r.status_code == 302 and r.headers['Location'].endswith('/admin/login') and saved == []
    r = c.post('/admin/login', data={'username': 'admin', 'password': 'admin'}, environ_overrides=far)
    assert r.status_code == 200 and 'غير صحيحة' in r.get_data(as_text=True)


def test_the_embedded_device_has_an_english_name(desk):
    """جهاز الرنين المضمَّن اسمه «هذا الكمبيوتر»: في الواجهة الإنجليزية يُعرض «This computer» (والعربية كما هي)."""
    app, c, bp, sign, launcher, key, home = desk
    ar = c.get('/admin/central').get_data(as_text=True)
    assert 'هذا الكمبيوتر' in ar and 'This computer' not in ar
    with c.session_transaction() as s:
        s['lang'] = 'en'
    en = c.get('/admin/central').get_data(as_text=True)
    assert 'This computer' in en
    assert 'dev-name">هذا الكمبيوتر' not in en
