"""نسخة سطح المكتب من نظام جرس الحصص: برنامج واحد يعمل على كمبيوتر ويندوز دون خادم ولا إنترنت.

يجمع في برنامج واحد:
• النظام نفسه (لوحة الإدارة والجداول والبريد وأجهزة الشبكة المحلية والتطبيقات) بخادم Waitress على http://localhost:18005 (منفذ مختلف عن نظام الجرس الأصلي 8005، فيعملان على الكمبيوتر نفسه دون تعارض)
• مشغّل الصوت (bell_player/bell_player.py نفسه) يرنّ الجرس على سماعات هذا الكمبيوتر حتى لو انقطعت الشبكة.
• الترخيص: مفتاح موقّع (ECDSA P-256) مرتبط ببصمة الكمبيوتر، يُدخل من صفحة «الترخيص» في لوحة الإدارة؛ ومن دونه
  لا يرنّ الجرس (وتبقى لوحة الإدارة والبيانات مفتوحة). بصمة هذا الكمبيوتر هي نفسها بصمة BellPlayer.

البيانات في %LOCALAPPDATA%\\IAJ Bell (القاعدة والنسخ والسجل ومفتاح المشغّل)؛ لا يمسّها التحديث ولا إلغاء التثبيت.
أول تشغيل: تطلب صفحة الدخول من المستخدم اختيار كلمة مرور للمدير admin (مرة واحدة، من هذا الكمبيوتر فقط).
نصوص نوافذ الأوامر بالإنجليزية لأن نافذة الأوامر في ويندوز لا تعرض العربية سليمة.

    BellDesktop.exe                 يشغّل النظام ويفتح لوحة الإدارة (إن كان يعمل يفتح اللوحة فقط)
    BellDesktop.exe --background    كما في بدء ويندوز: بلا نافذة ولا متصفح
    BellDesktopTools.exe --fingerprint | --license KEY | --audio | --set-password | --reset-password | --stop | --selftest
"""
import argparse
import getpass
import logging
import logging.handlers
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

APP_TITLE = 'IAJ Bell'
PLAYER_NAME = 'هذا الكمبيوتر'          # اسم جهاز الرنين المضمَّن في صفحة «الجرس المركزي»


def app_dir():
    """مجلد ملفات البرنامج: مجلد البرنامج المبني، أو جذر المستودع عند التشغيل من المصدر."""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_root():
    base = os.environ.get('BELL_DESKTOP_HOME')
    if base:
        return base
    if os.name == 'nt':
        return os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), APP_TITLE)
    return os.path.join(os.path.expanduser('~'), '.iaj-bell')


def say(text=''):
    """طباعة إن كانت هناك نافذة أوامر (نسخة بلا نافذة: sys.stdout غير موجود)."""
    if sys.stdout:
        try:
            print(text)
        except (OSError, ValueError):
            pass


def alert(title, text):
    """رسالة منبثقة في نسخة ويندوز بلا نافذة أوامر (مثل كلمة المرور في أول تشغيل)."""
    if os.name == 'nt' and not sys.stdout:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
        except Exception:   # noqa: BLE001
            pass


def answering(port):
    """هل يعمل النظام على هذا المنفذ (نسخة سابقة من البرنامج)؟"""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/admin/login', timeout=2) as r:
            return r.headers.get('Server', '').startswith('BELL-DESKTOP')      # لا نخلط بينه وبين نظام الجرس الأصلي (ident=BELL)
    except Exception:   # noqa: BLE001
        return False


def port_free(port, host):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def read_password(root):
    path = os.path.join(root, 'admin.pass')
    try:
        with open(path, encoding='utf-8') as f:
            pw = f.read().strip()
        return pw or None
    except OSError:
        return None


def write_password(root, pw):
    with open(os.path.join(root, 'admin.pass'), 'w', encoding='utf-8') as f:
        f.write(pw)


def prepare(root, lan, port):
    """متغيرات البيئة قبل استيراد النظام: مسارات البيانات، والعنوان المحلي، وكلمة مرور المدير. → True إن لم تُختر كلمة مرور بعد (تُختار عند أول زيارة للوحة الإدارة)."""
    data = os.path.join(root, 'data')
    os.makedirs(data, exist_ok=True)
    os.makedirs(os.path.join(root, 'logs'), exist_ok=True)
    os.makedirs(os.path.join(root, 'player'), exist_ok=True)
    os.environ['DATA_DIR'] = data
    os.environ['BELL_DB_PATH'] = os.path.join(data, 'bell.db')
    os.environ['DATABASE_URL'] = 'sqlite:///' + os.environ['BELL_DB_PATH'].replace('\\', '/')
    os.environ.setdefault('BACKUP_DIR', os.path.join(root, 'backups'))
    os.environ['HOST'] = '0.0.0.0' if lan else '127.0.0.1'
    os.environ['PORT'] = str(port)
    # عنوان http محلي: ملفات تعريف الارتباط غير مقيّدة بـ https (المتصفح يعدّ localhost آمناً)
    os.environ['BASE_URL'] = f'http://localhost:{port}'
    os.environ.pop('SECURE_COOKIES', None)
    os.environ['BELL_EDITION'] = 'desktop'
    os.environ['ADMIN_USER'] = 'admin'
    pw = read_password(root)
    needs_setup = not pw
    if needs_setup:
        pw = secrets.token_urlsafe(24)      # عشوائية في الذاكرة فقط حتى يختار المستخدم كلمة (صفحة /admin/setup)
    os.environ['ADMIN_PASS'] = pw
    return needs_setup


def setup_logging(root):
    handler = logging.handlers.RotatingFileHandler(os.path.join(root, 'logs', 'server.log'),
                                                   maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    return logging.getLogger('bell.desktop')


def load_system():
    base = app_dir()
    os.chdir(base)
    for p in (base, os.path.join(base, 'ops')):
        if p not in sys.path:
            sys.path.insert(0, p)
    from app import app
    return app


def load_player(root):
    """مشغّل الصوت نفسه (bell_player.py): مجلده أولاً في المسار حتى لا يُقرأ المجلد كحزمة، ثم يُوجَّه إلى بيانات هذا البرنامج."""
    folder = os.path.join(app_dir(), 'bell_player')
    if folder not in sys.path:
        sys.path.insert(0, folder)
    import bell_player as bp
    pdir = os.path.join(root, 'player')
    bp.BASE = pdir
    bp.CACHE = os.path.join(pdir, 'player_cache')
    bp.INI = os.path.join(pdir, 'bell_player.ini')
    os.makedirs(bp.CACHE, exist_ok=True)
    return bp


def provision_player(app, root):
    """جهاز الرنين المضمَّن في النظام: مفتاحه محفوظ في player/player.key، ويُنشأ مرة واحدة (ولا يمسّ أجهزة المستخدم الأخرى)."""
    import central
    from models import db
    path = os.path.join(root, 'player', 'player.key')
    with app.app_context():
        key = ''
        try:
            with open(path, encoding='utf-8') as f:
                key = f.read().strip()
        except OSError:
            pass
        if key and central.check_key(key):
            return key
        pid = next((p for p, v in central.players().items() if v.get('name') == PLAYER_NAME), None)
        if pid:
            key = central.new_player_key(pid)
        else:
            _pid, key = central.add_player(PLAYER_NAME)
        db.session.commit()
    with open(path, 'w', encoding='utf-8') as f:
        f.write(key)
    return key


def start_player(app, bp, root, port):
    """يوجّه المشغّل إلى الخادم المحلي ويشغّله في خيط؛ ويحسب بصمة الكمبيوتر خلفياً (استدعاء أوامر ويندوز بطيء)."""
    key = provision_player(app, root)
    bp.save_ini(f'http://127.0.0.1:{port}', key)           # يحفظ الترخيص والمخرج الصوتي المحفوظين
    bp.setup_logging(console=False)
    cfg = bp.load_ini()
    bp.AUDIO['device'] = cfg.get('audio') or ''
    app.config['BELL_PLAYER'] = bp

    def fingerprint():
        app.config['BELL_FP'] = bp.machine_fingerprint()
    threading.Thread(target=fingerprint, daemon=True).start()
    player = bp.Player(cfg)
    threading.Thread(target=player.run, daemon=True, name='bell-player').start()
    return player


def pid_file(root):
    return os.path.join(root, 'run.pid')


def stop_running(root):
    try:
        with open(pid_file(root), encoding='utf-8') as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        say('Not running.')
        return 0
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    try:
        os.remove(pid_file(root))
    except OSError:
        pass
    say('Stopped.')
    return 0


def selftest():
    """فحص البناء: يشغّل النظام على قاعدة مؤقتة ويتحقق من الدخول وصفحة الترخيص وجهاز الرنين والملفات المضمَّنة."""
    import tempfile
    os.environ['BELL_DESKTOP_HOME'] = tempfile.mkdtemp(prefix='bell-selftest-')
    root = data_root()
    os.environ['PAGE_SHIELD'] = '0'            # الصفحات المشفّرة لا تُقرأ في الفحص
    needs = prepare(root, False, 0)
    assert needs, 'a fresh install must ask for a password'
    app = load_system()
    bp = load_player(root)
    key = provision_player(app, root)
    assert key and provision_player(app, root) == key, 'the ringing device key is not stable'
    bp.save_ini('http://127.0.0.1:1', key)
    app.config['BELL_PLAYER'] = bp
    app.config['BELL_FP'] = 'SELFTEST00000000'
    app.config['BELL_NEEDS_SETUP'] = True
    app.config['BELL_SAVE_PASSWORD'] = lambda pw: write_password(root, pw)
    c = app.test_client()
    r = c.get('/admin/login')
    assert r.status_code == 302 and r.headers['Location'].endswith('/admin/setup'), 'first visit must ask for a password'
    assert c.get('/admin/setup').status_code == 200, 'password page'
    r = c.post('/admin/setup', data={'password': 'selftest-pass-1', 'again': 'selftest-pass-1'})
    assert r.status_code == 302 and read_password(root) == 'selftest-pass-1', 'the password was not saved'
    assert c.get('/admin/').status_code == 200, 'dashboard'
    page = c.get('/admin/license')
    assert page.status_code == 200 and b'SELFTEST00000000' in page.data, 'license page'
    assert 'الجرس لا يرنّ'.encode() in c.get('/admin/central').data, 'the not-licensed notice is missing'
    r = c.post('/admin/license/save', data={'key': 'PERMANENT:bad'}, follow_redirects=True)
    assert 'غير صالح'.encode() in r.data, 'an invalid key was not refused'
    sched = c.get('/api/central/schedule', headers={'X-Bell-Key': key})
    assert sched.status_code == 200, 'the ringing device cannot read the schedule'
    for path in ('static/css/admin.css', 'static/sounds/central/air-1.wav', 'static/firmware/manifest.json'):
        assert os.path.exists(os.path.join(app_dir(), path)), 'missing file: ' + path
    assert bp.machine_fingerprint(), 'no computer fingerprint'
    bp.check_license('PERMANENT:x', 'SELFTEST00000000', bp.amman_now().date())
    from zoneinfo import ZoneInfo           # المنطقة الزمنية (tzdata) مضمَّنة، وإلا لا يعرف المشغّل وقت عمّان
    ZoneInfo('Asia/Amman')
    import io
    import openpyxl
    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    wb.active.append(['name', 'email'])
    wb.save(buf)
    assert len(buf.getvalue()) > 100, 'the Excel engine is missing'
    print('selftest ok')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='BellDesktop', description='IAJ Bell (desktop edition)')
    ap.add_argument('--port', type=int, default=int(os.environ.get('BELL_DESKTOP_PORT') or 18005))
    ap.add_argument('--local-only', action='store_true', help='do not accept connections from other devices on the network')
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--background', action='store_true', help='Windows start-up: no browser, no message boxes')
    ap.add_argument('--page', default='admin/', help='page to open in the browser (default: admin/)')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--stop', action='store_true')
    ap.add_argument('--fingerprint', action='store_true')
    ap.add_argument('--license', metavar='KEY')
    ap.add_argument('--audio', action='store_true')
    ap.add_argument('--set-password', action='store_true')
    ap.add_argument('--reset-password', action='store_true', help='forget the administrator password; the next start asks for a new one')
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    root = data_root()
    os.makedirs(root, exist_ok=True)

    if args.stop:
        return stop_running(root)
    if args.fingerprint or args.license or args.audio:
        bp = load_player(root)
        if args.fingerprint:
            fp = bp.machine_fingerprint()
            say(f'This computer fingerprint: {fp}')
            alert(APP_TITLE, f'Fingerprint: {fp}')
        if args.license:
            fp = bp.machine_fingerprint()
            ok, expired, exp = bp.check_license(args.license.strip(), fp, bp.amman_now().date())
            if not ok or expired:
                say('The key is not valid for this computer.' if not ok else f'The key expired on {exp}.')
                return 1
            cfg = bp.load_ini()
            bp.save_ini(cfg['server'], cfg['key'], license_key=args.license.strip())
            say(f'License saved ({exp}).')
        if args.audio:
            bp.AUDIO['device'] = bp.load_ini().get('audio') or ''
            bp.choose_audio()
        return 0
    if args.reset_password:
        stop_running(root)
        for name in ('admin.pass', 'first-sign-in.txt'):
            try:
                os.remove(os.path.join(root, name))
            except OSError:
                pass
        say('The password was cleared. Start the program and choose a new one.')
        alert(APP_TITLE, 'The password was cleared. Start the program and choose a new one.')
        return 0
    if args.set_password:
        pw = getpass.getpass('New password (at least 8 characters, hidden while typing): ')
        if len(pw) < 8 or pw != getpass.getpass('Type it again: '):
            say('Too short or not matching.')
            return 1
        write_password(root, pw)
        say('Done. Restart the program to use the new password.')
        return 0

    page = args.page.lstrip('/')
    url = f'http://localhost:{args.port}/{page}'
    if answering(args.port):
        say(f'{APP_TITLE} is already running: {url}')
        if not args.no_browser and not args.background:
            webbrowser.open(url)
        return 0
    lan = not args.local_only
    host = '0.0.0.0' if lan else '127.0.0.1'
    port = args.port
    while not port_free(port, host) and port < args.port + 20:
        port += 1
    url = f'http://localhost:{port}/{page}'
    needs_setup = prepare(root, lan, port)
    log = setup_logging(root)

    say('=' * 62)
    say(f'  {APP_TITLE} - desktop edition')
    say('=' * 62)
    say('  Starting, please wait...')
    try:
        app = load_system()
        bp = load_player(root)
        app.config['BELL_NEEDS_SETUP'] = needs_setup
        app.config['BELL_SAVE_PASSWORD'] = lambda pw: write_password(root, pw)
        start_player(app, bp, root, port)
    except Exception:   # noqa: BLE001
        log.exception('The system failed to start')
        alert(APP_TITLE, 'The system could not start. Details: ' + os.path.join(root, 'logs', 'server.log'))
        say('\n  The system could not start. Details are in:')
        say('  ' + os.path.join(root, 'logs', 'server.log'))
        if sys.stdin:
            input('\n  Press Enter to close this window.')
        return 1
    with open(pid_file(root), 'w', encoding='utf-8') as f:
        f.write(str(os.getpid()))

    say(f'\n  Open in the browser : {url}')
    if lan:
        try:
            say(f'  From other devices  : http://{socket.gethostbyname(socket.gethostname())}:{port}')
        except OSError:
            pass
    if needs_setup:
        say('\n  First time: open the address above and choose your password (username: admin).')
        if not args.background:
            alert(APP_TITLE, 'Welcome!\n\nThe program is starting. In the browser page that opens, choose your password '
                             '(username: admin).\n\nThen enter the license key (Admin panel > License): the bell does not ring without it.\n\n'
                             'Address: ' + url)
    say(f'\n  Data folder: {root}')
    say('\n  Keep this window open while using the system. Close it to stop.')
    if not args.no_browser and not args.background:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    from waitress import serve
    log.info('BELL desktop on %s (data: %s)', url, root)
    serve(app, host=host, port=port, threads=8, max_request_body_size=64 * 1024 ** 2, channel_timeout=300, ident='BELL-DESKTOP')
    return 0


if __name__ == '__main__':
    sys.exit(main())
