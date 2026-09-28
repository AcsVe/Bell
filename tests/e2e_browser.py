"""End-to-end check in real Chrome: code login → cached schedule → local alert,
then the same with the network cut (service worker + IndexedDB)."""
import os
import subprocess
import sys
import time
from datetime import datetime

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get('E2E_OUT', '/tmp/e2e')
os.makedirs(OUT, exist_ok=True)
DB = os.path.join(OUT, 'e2e.db')
if os.path.exists(DB):
    os.remove(DB)
env = dict(os.environ, DATABASE_URL=f'sqlite:///{DB}', PORT='5055')

# Seed through the real admin routes.
sys.path.insert(0, ROOT)
os.environ['DATABASE_URL'] = env['DATABASE_URL']
from app import create_app  # noqa: E402
from models import Section, Stage, Teacher  # noqa: E402

app = create_app({'SQLALCHEMY_DATABASE_URI': env['DATABASE_URL']})
c = app.test_client()
c.post('/admin/login', data={'username': 'admin', 'password': 'admin'})
c.post('/admin/stages/add', data={'name_ar': 'المرحلة الأساسية 1-3', 'name_en': 'Primary 1-3'})
with app.app_context():
    sid = Stage.query.one().id
for kind, dur in [('class', 45), ('class', 45), ('break', 20), ('class', 45)]:
    c.post(f'/admin/stages/{sid}/periods/add', data={'kind': kind, 'duration': dur})
c.post(f'/admin/stages/{sid}/update', data={'name_ar': 'المرحلة الأساسية 1-3', 'name_en': 'Primary 1-3',
       'active': '1', 'weekdays': ['6', '0', '1', '2', '3']})
c.post(f'/admin/stages/{sid}/grades/add', data={'names': 'الصف الأول'})
with app.app_context():
    gid = Stage.query.one().grades[0].id
c.post(f'/admin/grades/{gid}/sections/add', data={'names': 'أ، ب'})
c.post('/admin/teachers/add', data={'names': 'سارة الخطيب'})
with app.app_context():
    code = Section.query.order_by(Section.id).first().code
    secs = [s.id for s in Section.query.all()]
    tid = Teacher.query.one().id
    tcode = Teacher.query.one().code
for s in secs:
    c.post(f'/admin/sections/{s}/teachers/add', data={'teacher_id': tid})

srv = subprocess.Popen([sys.executable, 'app.py'], cwd=ROOT, env=dict(env, FLASK_DEBUG='0'),
                       stdout=open(os.path.join(OUT, 'server.log'), 'w'), stderr=subprocess.STDOUT)
time.sleep(2.5)
BASE = 'http://127.0.0.1:5055'
results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name)


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome', args=['--no-sandbox'])
        ctx = browser.new_context(viewport={'width': 1280, 'height': 800}, timezone_id='Asia/Amman',
                                  locale='ar-JO', service_workers='allow')
        page = ctx.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        # Sunday 27 Sep 2026, 08:44:40 — 20 s before period 1 ends
        page.clock.install(time=datetime(2026, 9, 27, 8, 44, 40))

        page.goto(BASE + '/login')
        page.fill('#code', 'bad123')
        page.click('button.big')
        check('invalid code shows error', page.locator('.err').is_visible())
        page.fill('#code', code.lower())
        page.click('button.big')
        page.wait_for_url('**/app')
        page.wait_for_timeout(1500)
        if page.locator('#gate').is_visible():
            page.evaluate("document.getElementById('gateBtn').click()")
        page.wait_for_function("document.querySelector('#now').textContent.includes('الحصة الأولى')", timeout=8000)
        check('idle screen shows current period', 'الحصة الأولى' in page.inner_text('#now'))
        check('next alert shown', '08:45' in page.inner_text('#next'))
        page.screenshot(path=f'{OUT}/1-idle.png')

        page.clock.run_for(25_000)
        page.wait_for_selector('#alert:not([hidden])', timeout=5000)
        txt = page.inner_text('#alertText')
        check('alert fires at 08:45 with combined text', 'انتهت الحصة الأولى' in txt and 'بداية الحصة الثانية' in txt)
        page.screenshot(path=f'{OUT}/2-alert.png')
        page.evaluate("document.getElementById('alert').click()")

        # Wait for the service worker to take control, then go offline.
        page.wait_for_function('navigator.serviceWorker && navigator.serviceWorker.controller', timeout=10000)
        page.reload()
        page.wait_for_function('navigator.serviceWorker.controller', timeout=10000)
        ctx.set_offline(True)
        page.clock.set_system_time(datetime(2026, 9, 27, 9, 29, 50))
        page.reload()
        page.wait_for_selector('#hm', timeout=8000)
        if page.locator('#gate').is_visible():
            page.evaluate("document.getElementById('gateBtn').click()")
        page.wait_for_function("document.querySelector('#status').classList.contains('offline')", timeout=15000)
        check('offline: page served from cache and status shows offline', True)
        if page.locator('#gate').is_visible():
            page.evaluate("document.getElementById('gateBtn').click()")
        check('offline: schedule restored from IndexedDB', '09:30' in page.inner_text('#next'))
        page.clock.run_for(15_000)
        page.wait_for_selector('#alert:not([hidden])', timeout=5000)
        txt = page.inner_text('#alertText')
        check('offline alert fires: end of period 2 → break', 'انتهت الحصة الثانية' in txt and 'الاستراحة' in txt)
        page.screenshot(path=f'{OUT}/3-offline-alert.png')
        page.evaluate("document.getElementById('alert').click()")

        # English toggle (offline, client-side)
        page.click('#menuBtn')
        page.click('#langBtn')
        page.wait_for_function("document.documentElement.dir === 'ltr'")
        check('English toggle works offline', 'Now' in page.inner_text('#now') or 'Break' in page.inner_text('#now'))
        page.screenshot(path=f'{OUT}/4-english.png')
        ctx.set_offline(False)

        # Teacher code on a phone
        phone = browser.new_context(viewport={'width': 390, 'height': 800}, timezone_id='Asia/Amman',
                                    is_mobile=True, has_touch=True)
        tp = phone.new_page()
        tp.clock.install(time=datetime(2026, 9, 27, 9, 40, 0))
        tp.goto(BASE + '/login')
        tp.fill('#code', tcode)
        tp.click('button.big')
        tp.wait_for_url('**/app')
        tp.wait_for_timeout(1500)
        if tp.locator('#gate').is_visible():
            tp.evaluate("document.getElementById('gateBtn').click()")
        tp.wait_for_function("document.querySelector('#whoName').textContent.includes('سارة')", timeout=8000)
        check('teacher code covers 2 sections', '2' in tp.inner_text('#whoName'))
        tp.screenshot(path=f'{OUT}/5-teacher-phone.png')

        # Admin pages render (mobile width)
        ap = phone.new_page()
        ap.goto(BASE + '/admin/login')
        ap.fill('input[name=username]', 'admin')
        ap.fill('input[name=password]', 'admin')
        ap.click('button')
        ap.goto(f'{BASE}/admin/stages/{sid}')
        ap.screenshot(path=f'{OUT}/6-admin-stage-phone.png', full_page=True)
        ad = ctx.new_page()
        ad.goto(BASE + '/admin/login')
        ad.fill('input[name=username]', 'admin')
        ad.fill('input[name=password]', 'admin')
        ad.click('button')
        ad.goto(f'{BASE}/admin/stages/{sid}')
        ad.screenshot(path=f'{OUT}/7-admin-stage.png', full_page=True)
        ad.goto(f'{BASE}/admin/sections/{secs[1]}')
        ad.screenshot(path=f'{OUT}/8-admin-section.png', full_page=True)
        ad.goto(f'{BASE}/admin/settings')
        check('device registry lists both devices', ad.locator('tbody tr').count() >= 2)
        check('no JS errors', not errors)
        if errors:
            print(errors)
        browser.close()
finally:
    srv.terminate()

failed = [n for n, ok in results if not ok]
print(f'\n{len(results) - len(failed)}/{len(results)} checks passed')
sys.exit(1 if failed else 0)
