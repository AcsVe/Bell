"""Mobile web layer in real Chrome: the built mobile/www bundle served from a
different origin (like Capacitor's https://localhost), with fake native
plugins, signing in cross-origin against the live Flask server."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = '/tmp/e2e-mobile'
os.makedirs(OUT, exist_ok=True)
DB = f'{OUT}/m.db'
if os.path.exists(DB):
    os.remove(DB)
env = dict(os.environ, DATABASE_URL=f'sqlite:///{DB}', PORT='5057')
sys.path.insert(0, ROOT)
from app import create_app  # noqa: E402
from models import Section, Stage  # noqa: E402

app = create_app({'SQLALCHEMY_DATABASE_URI': env['DATABASE_URL']})
c = app.test_client()
c.post('/admin/login', data={'username': 'admin', 'password': 'admin'})
c.post('/admin/stages/add', data={'name_ar': 'المرحلة الأساسية 4-6', 'name_en': 'Primary 4-6'})
with app.app_context():
    sid = Stage.query.one().id
for kind, dur in [('class', 45), ('class', 45), ('break', 20), ('class', 45)]:
    c.post(f'/admin/stages/{sid}/periods/add', data={'kind': kind, 'duration': dur})
import io  # noqa: E402
from PIL import Image  # noqa: E402
buf = io.BytesIO()
Image.new('RGB', (120, 120), (200, 30, 30)).save(buf, 'PNG')
c.post(f'/admin/stages/{sid}/media', data={'logo': (io.BytesIO(buf.getvalue()), 'l.png', 'image/png')},
       content_type='multipart/form-data')
c.post(f'/admin/stages/{sid}/grades/add', data={'names': 'الصف الرابع'})
with app.app_context():
    gid = Stage.query.one().grades[0].id
c.post(f'/admin/grades/{gid}/sections/add', data={'names': 'ب'})
with app.app_context():
    code = Section.query.one().code

subprocess.run(['node', 'native/build-web.js', 'mobile'], cwd=ROOT, check=True,
               env=dict(os.environ, BELL_SERVER='http://127.0.0.1:5057'))
srv = subprocess.Popen([sys.executable, 'app.py'], cwd=ROOT, env=env,
                       stdout=open(f'{OUT}/server.log', 'w'), stderr=subprocess.STDOUT)
web = subprocess.Popen([sys.executable, '-m', 'http.server', '5058', '--bind', '127.0.0.1'],
                       cwd=os.path.join(ROOT, 'mobile', 'www'), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.5)

FAKE = """
window.__calls = [];
const store = {};
window.Capacitor = {
  getPlatform: () => 'android',
  Plugins: {
    Preferences: { get: async ({key}) => ({ value: store[key] || null }), set: async ({key, value}) => { store[key] = value; },
                   remove: async ({key}) => { delete store[key]; } },
    LocalNotifications: { checkPermissions: async () => ({ display: 'granted' }), requestPermissions: async () => ({ display: 'granted' }),
                          getPending: async () => ({ notifications: [] }), addListener: () => {} },
    BellAlarm: {
      _alarms: [],
      status: async function () { return { count: this._alarms.length, exact: true, notifications: true, battery: true }; },
      setAlarms: async function ({ alarms }) { this._alarms = alarms; window.__calls.push(alarms); return {}; },
      clear: async function () { this._alarms = []; },
      testAlarm: async () => {},
    },
  },
};
"""
results = []


def check(name, ok):
    results.append(ok)
    print(('PASS ' if ok else 'FAIL ') + name)


try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path='/opt/google/chrome/chrome', args=['--no-sandbox'])
        ctx = b.new_context(viewport={'width': 390, 'height': 820}, is_mobile=True, has_touch=True,
                            timezone_id='Asia/Amman')
        ctx.add_init_script(FAKE)
        pg = ctx.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.clock.install(time=datetime(2026, 9, 27, 8, 40, 0))
        pg.goto('http://127.0.0.1:5058/index.html')
        pg.wait_for_url('**/login.html')
        check('default server comes from the build', pg.input_value('#server') == 'http://127.0.0.1:5057')
        pg.fill('#code', 'WRONG1')
        pg.click('#go')
        pg.wait_for_selector('#msg.err')
        check('wrong code rejected (cross-origin API)', 'غير صحيح' in pg.inner_text('#msg'))
        pg.fill('#code', code.lower())
        pg.click('#go')
        pg.wait_for_url('**/app.html', timeout=10000)
        pg.wait_for_function('window.__calls.length > 0', timeout=10000)
        alarms = pg.evaluate('window.__calls[window.__calls.length - 1]')
        check('alarms registered with the OS module', len(alarms) == 5 * 5)
        a = next(x for x in alarms if x['weekday'] == 1 and x['hour'] == 8 and x['minute'] == 45)
        check('Sunday 08:45 alarm text', a['title'] == 'انتهت الحصة الأولى - بداية الحصة الثانية')
        pg.wait_for_function("document.querySelector('#logo') && !document.querySelector('#logo').hidden")
        check('logo embedded as data URL for offline use', pg.get_attribute('#logo', 'src').startswith('data:image/png'))
        pg.click('#menuBtn')
        check('status shows registered alerts', '25' in pg.inner_text('#syncInfo'))
        pg.click('[data-act=close]')
        pg.screenshot(path=f'{OUT}/app.png')

        # Offline: stop the server, relaunch the app → cached schedule, alerts still registered
        srv.terminate(); srv.wait()
        pg.goto('http://127.0.0.1:5058/index.html')
        pg.wait_for_url('**/app.html', timeout=10000)
        pg.wait_for_function("document.querySelector('#now').textContent.length > 0", timeout=10000)
        check('offline relaunch goes straight to alert screen', 'الحصة الأولى' in pg.inner_text('#now'))
        pg.wait_for_function("document.querySelector('#status').classList.contains('offline')", timeout=10000)
        check('offline status shown', True)
        for _ in range(60):
            pg.clock.run_for(10_000)
            if pg.is_visible('#alert'):
                break
        pg.wait_for_selector('#alert:not([hidden])', timeout=2000)
        check('fires at 08:45', pg.inner_text('#alertTime') == '08:45')
        check('in-app overlay also shows while open', 'انتهت الحصة الأولى' in pg.inner_text('#alertText'))
        pg.screenshot(path=f'{OUT}/offline-alert.png')
        check('no JS errors', not errs)
        if errs:
            print(errs)
        b.close()
finally:
    for proc in (srv, web):
        if proc.poll() is None:
            proc.terminate()
print(f'{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
