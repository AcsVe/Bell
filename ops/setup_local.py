"""إعداد ملف .env لنظام جرس الحصص على هذا الجهاز (مرة واحدة). يقرأ إعدادات موقع الجائزة IAJ إن وجده
(مجلد Google Drive + رمز Cloudflare)، ويطلب اسم مستخدم وكلمة مرور لوحة الإدارة."""
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


def ask(label, default='', secret=False):
    shown = (' [' + t('مخفي', 'hidden') + ']') if secret and default else (f' [{default}]' if default else '')
    v = (getpass.getpass if secret else input)(f'  {label}{shown}: ').strip()
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
    order = ['SECRET_KEY', 'BASE_URL', 'PORT', 'SECURE_COOKIES', 'ADMIN_USER', 'ADMIN_PASS', 'BACKUP_DIR', 'CF_API_TOKEN']
    keys = order + [k for k in vals if k not in order]
    with open(ENV, 'w', encoding='utf-8') as fh:
        fh.write('# إعدادات نظام جرس الحصص على هذا الجهاز — لا ترفع هذا الملف ولا تشاركه\n')
        for k in keys:
            if vals.get(k):
                fh.write(f'{k}={vals[k]}\n')
    print(t(f'\n  ✓ حُفظت الإعدادات في {ENV}', f'\n  ✓ Settings saved to {ENV}'))


if __name__ == '__main__':
    main()
