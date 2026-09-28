"""Real desktop run (Electron under Xvfb): sign in against a live server, stop
the server, and check the alert window opens by itself at the scheduled minute."""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = '/tmp/e2e-desktop'
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
DB = os.path.join(OUT, 'd.db')
os.environ['TZ'] = 'Asia/Amman'
time.tzset()
env = dict(os.environ, DATABASE_URL=f'sqlite:///{DB}', PORT='5056', TZ='Asia/Amman')

sys.path.insert(0, ROOT)
from app import create_app  # noqa: E402
from models import Section, Stage  # noqa: E402

app = create_app({'SQLALCHEMY_DATABASE_URI': env['DATABASE_URL']})
c = app.test_client()
c.post('/admin/login', data={'username': 'admin', 'password': 'admin'})
c.post('/admin/stages/add', data={'name_ar': 'المرحلة الثانوية', 'name_en': 'Secondary'})
with app.app_context():
    sid = Stage.query.one().id
# The alert minute T is ~2 minutes from now: period 1 ends exactly at T.
T = (datetime.now() + timedelta(minutes=2)).replace(second=0, microsecond=0)
start = T - timedelta(minutes=45)
c.post(f'/admin/stages/{sid}/update', data={'name_ar': 'المرحلة الثانوية', 'name_en': 'Secondary', 'active': '1',
       'weekdays': [str(i) for i in range(7)]})
for kind, dur in [('class', 45), ('class', 45)]:
    c.post(f'/admin/stages/{sid}/periods/add', data={'kind': kind, 'duration': dur})
with app.app_context():
    pids = [p.id for p in Stage.query.one().periods]
form = {'day_start': start.strftime('%H:%M')}
for pid in pids:
    form.update({f'p{pid}_duration': 45, f'p{pid}_kind': 'class'})
c.post(f'/admin/stages/{sid}/periods/save', data=form)
c.post(f'/admin/stages/{sid}/grades/add', data={'names': 'الصف العاشر'})
with app.app_context():
    gid = Stage.query.one().grades[0].id
c.post(f'/admin/grades/{gid}/sections/add', data={'names': 'أ'})
with app.app_context():
    code = Section.query.one().code
print('alert expected at', T.strftime('%H:%M:%S'), 'code', code)

srv = subprocess.Popen([sys.executable, 'app.py'], cwd=ROOT, env=env,
                       stdout=open(f'{OUT}/server.log', 'w'), stderr=subprocess.STDOUT)
time.sleep(2.5)
subprocess.run(['node', 'native/build-web.js', 'desktop'], cwd=ROOT, check=True)
el_env = dict(env, BELL_SELFTEST=os.path.join(ROOT, 'tests', 'desktop_selftest.js'), BELL_OUT=OUT,
              BELL_USERDATA=f'{OUT}/userdata', BELL_SERVER_URL='http://127.0.0.1:5056', BELL_CODE=code.lower(),
              ELECTRON_DISABLE_SECURITY_WARNINGS='1')
electron = os.path.join(ROOT, 'desktop', 'node_modules', '.bin', 'electron')
el = subprocess.Popen(['xvfb-run', '-a', '-s', '-screen 0 1366x768x24', electron, '--no-sandbox', '.'],
                      cwd=os.path.join(ROOT, 'desktop'), env=el_env,
                      stdout=open(f'{OUT}/electron.log', 'w'), stderr=subprocess.STDOUT)
try:
    deadline = time.time() + 60
    while not os.path.exists(f'{OUT}/synced') and time.time() < deadline and el.poll() is None:
        time.sleep(0.5)
    srv.terminate()
    srv.wait()
    open(f'{OUT}/server-stopped', 'w').write('1')
    print('server stopped at', datetime.now().strftime('%H:%M:%S'), '— app is now fully offline')
    el.wait(timeout=6 * 60)
finally:
    if srv.poll() is None:
        srv.terminate()
    if el.poll() is None:
        el.kill()

res = json.load(open(f'{OUT}/result.json')) if os.path.exists(f'{OUT}/result.json') else {'ok': False, 'error': 'no result'}
print(json.dumps(res, ensure_ascii=False, indent=2))
checks = [
    ('app signed in and cached schedule', res.get('ok') and bool(res.get('status'))),
    ('server was stopped before the alert', res.get('serverDown')),
    ('alert fired at the scheduled minute', res.get('firedAt', '')[:5] == T.strftime('%H:%M')),
    ('alert text correct', 'انتهت الحصة الأولى' in res.get('alertText', '') and 'بداية الحصة الثانية' in res.get('alertText', '')),
    ('tone playing', res.get('soundPlaying')),
    ('window always on top', res.get('alwaysOnTop')),
]
for name, ok in checks:
    print(('PASS ' if ok else 'FAIL ') + name)
sys.exit(0 if all(ok for _, ok in checks) else 1)
