"""إعداد ملف .env لنظام جرس الحصص على هذا الجهاز (مرة واحدة). يقرأ إعدادات موقع الجائزة IAJ إن وجده
(مجلد Google Drive + رمز Cloudflare)، ويطلب اسم مستخدم وكلمة مرور لوحة الإدارة، وإعدادات البريد Microsoft Graph
(اختيارية — تؤخذ من نظام آخر على الجهاز إن وُجدت، وEnter يتخطاها).

    python ops/setup_local.py          الإعداد الكامل (أول تثبيت)
    python ops/setup_local.py --mail   يكمل إعدادات البريد الناقصة فقط في .env موجود (يستدعيه 1_setup.bat)"""
import getpass
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BASE, t  # noqa: E402

ENV = os.path.join(BASE, '.env')


def read_env(path):
    out = {}
    if os.path.isfile(path):
        for raw in open(path, encoding='utf-8-sig', errors='ignore'):
            line = raw.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def find_iaj():
    """مجلد موقع الجائزة على نفس الجهاز (للاستفادة من BACKUP_DIR وCF_API_TOKEN)"""
    cands = [os.environ.get('IAJ_DIR', '')]
    parent = os.path.dirname(BASE)
    try:
        cands += [os.path.join(parent, d) for d in os.listdir(parent)]
    except OSError:
        pass
    for drive in 'CDEFG':
        cands += [f'{drive}:\\IAJ', f'{drive}:\\IAJ-main', f'{drive}:\\iaj', f'{drive}:\\Att']
    for d in cands:
        if d and os.path.isfile(os.path.join(d, '.env')) and read_env(os.path.join(d, '.env')).get('CF_API_TOKEN'):
            return d
    return ''


MAIL_KEYS = (('MS_TENANT_ID', 'Microsoft Tenant ID', 'Microsoft Tenant ID', False),
             ('MS_CLIENT_ID', 'Microsoft Client ID', 'Microsoft Client ID', False),
             ('MS_CLIENT_SECRET', 'Microsoft Client Secret', 'Microsoft Client Secret', True),
             ('MS_SENDER_EMAIL', 'بريد المُرسِل (مثل noreply@...)', 'Sender mailbox (e.g. noreply@...)', False))


def other_systems():
    """[(folder, settings)] of the other iajaward.org systems on this PC — the bookings system (venues), SAS and
    the tickets system already hold the Microsoft Graph mail settings."""
    cands = [os.environ.get('IAJ_DIR', '')]
    parent = os.path.dirname(BASE)
    try:
        cands += [os.path.join(parent, d) for d in os.listdir(parent)]
    except OSError:
        pass
    for drive in 'CDEFG':
        cands += [f'{drive}:\\{n}' for n in ('venues', 'SAS', 'RASTKT', 'Clearance', 'IAJ', 'IAJ-main', 'Att', 'attendance')]
    out, seen = [], set()
    for d in cands:
        if not d or os.path.normcase(d) in seen or os.path.normcase(d) == os.path.normcase(BASE):
            continue
        seen.add(os.path.normcase(d))
        for name in ('.env', 'config.env'):
            f = os.path.join(d, name)
            if os.path.isfile(f):
                out.append((d, read_env(f)))
    return out


def mail_values(cur):
    """Microsoft Graph settings: keep what .env has, else take a complete set from another system on this PC,
    else ask (Enter skips — mail stays off until they are completed by running 1_setup.bat again)."""
    vals = {k: cur.get(k, '') for k, *_ in MAIL_KEYS}
    if all(vals.values()):
        return vals
    for folder, env in other_systems():
        if all(env.get(k) for k, *_ in MAIL_KEYS):
            print(t(f'  إعدادات البريد (Microsoft Graph) مأخوذة من: {folder}', f'  Mail settings (Microsoft Graph) taken from: {folder}'))
            return {k: vals[k] or env[k] for k, *_ in MAIL_KEYS}
    print(t('\n  إعدادات البريد Microsoft Graph (اختيارية — اضغط Enter للتخطي)',
            '\n  Microsoft Graph mail settings (optional - press Enter to skip)'))
    for key, label_ar, label_en, secret in MAIL_KEYS:
        if not vals[key]:
            vals[key] = ask(t(label_ar, label_en), '', secret)
            if not vals[key] and key == 'MS_TENANT_ID':
                break                     # skipped: do not ask the other three
    return vals


def write_env(vals):
    order = ['SECRET_KEY', 'BASE_URL', 'PORT', 'SECURE_COOKIES', 'ADMIN_USER', 'ADMIN_PASS', 'BACKUP_DIR', 'CF_API_TOKEN',
             'MS_TENANT_ID', 'MS_CLIENT_ID', 'MS_CLIENT_SECRET', 'MS_SENDER_EMAIL']
    keys = order + [k for k in vals if k not in order]
    with open(ENV, 'w', encoding='utf-8') as fh:
        fh.write('# إعدادات نظام جرس الحصص على هذا الجهاز — لا ترفع هذا الملف ولا تشاركه\n')
        for k in keys:
            if vals.get(k):
                fh.write(f'{k}={vals[k]}\n')


def mail_only():
    """1_setup.bat on an existing install: complete only the missing mail settings, keep everything else."""
    cur = read_env(ENV)
    if not cur:
        return main()
    vals = mail_values(cur)
    if all(cur.get(k, '') == vals[k] for k, *_ in MAIL_KEYS):
        print(t('  إعدادات البريد كما هي.', '  Mail settings unchanged.'))
        return
    cur.update(vals)
    write_env(cur)
    print(t(f'  ✓ حُفظت إعدادات البريد في {ENV} (تُطبَّق بعد إعادة التشغيل)', f'  ✓ Mail settings saved to {ENV} (applied after a restart)'))


def ask(label, default='', secret=False):
    shown = (' [' + t('مخفي', 'hidden') + ']') if secret and default else (f' [{default}]' if default else '')
    try:
        v = (getpass.getpass if secret else input)(f'  {label}{shown}: ').strip()
    except EOFError:                      # no keyboard (run unattended): keep the default
        v = ''
    return v or default


def main():
    cur = read_env(ENV)
    iaj = find_iaj()
    ie = read_env(os.path.join(iaj, '.env')) if iaj else {}
    if iaj:
        print(t(f'  وُجدت إعدادات نظام آخر في: {iaj}  (سآخذ منه مجلد Drive ورمز Cloudflare)', f'  Found another system\'s settings in: {iaj}  (taking the Drive folder and Cloudflare token from it)'))
    bk_default = cur.get('BACKUP_DIR') or ''
    if not bk_default and ie.get('BACKUP_DIR'):
        parent = os.path.dirname(ie['BACKUP_DIR'].rstrip('\\/')) if os.path.basename(ie['BACKUP_DIR'].rstrip('\\/')).lower() == 'attendance' else ie['BACKUP_DIR']
        bk_default = os.path.join(parent, 'bell')
    print(t('\n  إعدادات نظام جرس الحصص (اضغط Enter لقبول القيمة بين القوسين أو للتخطي)\n', '\n  School bell settings (press Enter to accept the value in brackets, or to skip)\n'))
    vals = dict(cur)
    vals.setdefault('SECRET_KEY', secrets.token_urlsafe(48))
    vals['BASE_URL'] = ask(t('عنوان الموقع', 'Site address'), cur.get('BASE_URL', 'https://5.iajaward.org'))
    vals['PORT'] = cur.get('PORT', '8005')
    vals['SECURE_COOKIES'] = '1'
    vals['BACKUP_DIR'] = ask(t('مجلد النسخ الاحتياطية (داخل Google Drive)', 'Backup folder (inside Google Drive)'), bk_default)
    vals['CF_API_TOKEN'] = cur.get('CF_API_TOKEN') or ie.get('CF_API_TOKEN', '') or ask(t('رمز Cloudflare API', 'Cloudflare API token'), '', True)
    vals['ADMIN_USER'] = ask(t('اسم مستخدم لوحة الإدارة', 'Admin user name'), cur.get('ADMIN_USER', 'admin'))
    while True:
        pw = getpass.getpass(t('  كلمة مرور لوحة الإدارة (8 أحرف على الأقل', '  Admin password (at least 8 characters') + ((t(' — Enter لتركها كما هي', ' - Enter keeps the current one')) if cur.get('ADMIN_PASS') else '') + '): ').strip()
        if not pw and cur.get('ADMIN_PASS'):
            break
        if len(pw) < 8:
            print(t('  قصيرة — 8 أحرف على الأقل', '  Too short - at least 8 characters'))
            continue
        if getpass.getpass(t('  أعد كتابتها: ', '  Type it again: ')).strip() != pw:
            print(t('  غير متطابقتين — أعد المحاولة', '  They do not match - try again'))
            continue
        vals['ADMIN_PASS'] = pw
        break
    vals.update(mail_values(cur))
    write_env(vals)
    print(t(f'\n  ✓ حُفظت الإعدادات في {ENV}', f'\n  ✓ Settings saved to {ENV}'))
    if not all(vals.get(k) for k, *_ in MAIL_KEYS):
        print(t('  ⚠ إعدادات البريد ناقصة — البريد معطّل حتى تُكمل بتشغيل 1_setup.bat مرة أخرى',
                '  ⚠ Mail settings incomplete - mail stays off until 1_setup.bat is run again'))


if __name__ == '__main__':
    mail_only() if '--mail' in sys.argv[1:] else main()
