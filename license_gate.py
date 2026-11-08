"""اشتراك النسخة المستضافة + نسخة كاملة من البيانات — وحدة واحدة مشتركة بين أنظمة iajaward.org (نسخة العملاء على الخادم).

الإعداد في ملف الإعدادات (.env) لنسخة العميل:
    LICENSE_UNTIL=2027-08-31     آخر يوم في الاشتراك (بلا قيمة = بلا انتهاء، كنسخة المدرسة نفسها — لا يتغيّر شيء)
    LICENSE_WARN_DAYS=30         أيام التنبيه قبل الانتهاء
    LICENSE_GRACE_DAYS=7         مهلة بعد الانتهاء يبقى فيها النظام عاملاً مع تنبيه
    LICENSE_CONTACT=...          جهة التجديد (هاتف أو بريد) في التنبيه وصفحة الإيقاف

الحالات: none · ok · warn (تنبيه أعلى صفحات الإدارة لمدير النظام) · grace (يعمل مع تنبيه) · expired (موقوف).
عند الإيقاف لا يُفتح إلا: الدخول والخروج والملفات الثابتة، وتحميل النسخة الكاملة لمدير النظام (/license/export).
لا يُحذف شيء ولا يتغيّر في القاعدة؛ تعديل التاريخ وإعادة التشغيل يعيد النظام فوراً.

النسخة الكاملة (/license/export، لمدير النظام فقط ومتاحة دائماً): ملف ZIP فيه جدول CSV لكل جدول (يُفتح في Excel؛
الأعمدة السرية مستبعدة)، ونسخة متسقة من قاعدة SQLite إن وُجدت، والمجلدات المرفوعة الممرَّرة، وملف README.

الاستعمال في التطبيق (بعد تثبيت حماية الصفحات إن وُجدت، حتى يُضاف التنبيه قبل التشفير):
    from license_gate import install
    install(app, is_admin=lambda: ..., allow_paths=('/login', '/logout'),
            sqlalchemy_db=db            # أو sqlite_path=lambda: DB_PATH
            , upload_dirs=lambda: [...])
"""
import csv
import html
import io
import os
import re
import sqlite3
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone

EXPORT_PATH = '/license/export'
_SECRET_HINTS = ('password', 'passwd', 'secret', 'token', 'p256dh', 'auth_key', 'private', 'api_key', 'pw_hash')


# ── الحالة ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _int_env(name, default):
    try:
        return max(0, int((os.environ.get(name) or '').strip() or default))
    except ValueError:
        return default


def _today():
    return (datetime.now(timezone.utc) + timedelta(hours=3)).date()     # Asia/Amman


def status(today=None):
    raw = (os.environ.get('LICENSE_UNTIL') or '').strip()
    contact = (os.environ.get('LICENSE_CONTACT') or '').strip()
    empty = {'state': 'none', 'until': '', 'lock_from': '', 'days_left': None, 'contact': contact}
    if not raw:
        return empty
    try:
        until = date.fromisoformat(raw)
    except ValueError:
        print('[license] LICENSE_UNTIL is not a valid YYYY-MM-DD date - ignored', flush=True)
        return empty
    today = today or _today()
    lock_from = until + timedelta(days=_int_env('LICENSE_GRACE_DAYS', 7) + 1)
    days_left = (until - today).days
    if today > until:
        state = 'expired' if today >= lock_from else 'grace'
    elif days_left <= _int_env('LICENSE_WARN_DAYS', 30):
        state = 'warn'
    else:
        state = 'ok'
    return {'state': state, 'until': until.isoformat(), 'lock_from': lock_from.isoformat(),
            'days_left': days_left, 'contact': contact}


# ── النصوص ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _lang():
    from flask import request, session
    q = request.args.get('lang')
    if q in ('ar', 'en'):
        return q
    for k in ('lang', 'admin_lang', 'ui_lang'):
        v = session.get(k)
        if v in ('ar', 'en'):
            return v
    v = request.cookies.get('lang') or request.cookies.get('ui_lang')
    return v if v in ('ar', 'en') else 'ar'


def _banner(lic, lang):
    e = html.escape
    contact = f' <bdi>{e(lic["contact"])}</bdi>' if lic['contact'] else ''
    if lang == 'en':
        lead = ('The subscription for this system ends on' if lic['state'] == 'warn'
                else 'The subscription for this system has ended; the system will be paused on')
        tail = 'Contact the service provider to renew it.'
        dl = 'Download a full copy of the data'
    else:
        lead = 'ينتهي اشتراك هذا النظام في' if lic['state'] == 'warn' else 'انتهى اشتراك هذا النظام، وسيتوقف في'
        tail = 'تواصل مع مزوّد الخدمة لتجديده.'
        dl = 'حمِّل نسخة كاملة من البيانات'
    day = lic['until'] if lic['state'] == 'warn' else lic['lock_from']
    return (f'<div id="licenseBanner" role="status" dir="{"ltr" if lang == "en" else "rtl"}" lang="{lang}" '
            'style="background:#fee2e2;color:#7f1d1d;border-bottom:1px solid #fecaca;padding:10px 16px;'
            'font:600 14px/1.6 system-ui,Tahoma,sans-serif;text-align:center">'
            f'{lead} <span dir="ltr">{e(day)}</span>. {tail}{contact} '
            f'<a href="{EXPORT_PATH}" style="color:#7f1d1d;text-decoration:underline;margin-inline-start:8px">{dl}</a></div>')


def _locked_page(lic, lang, can_export, login_path):
    e = html.escape
    en = lang == 'en'
    contact = f': <bdi>{e(lic["contact"])}</bdi>' if lic['contact'] else ''
    if en:
        title, msg = 'Subscription ended', (f'The subscription for this system ended on <span dir="ltr">{e(lic["until"])}</span>, '
                                           'so it is paused. Your data is kept in full; nothing has been deleted.')
        renew = f'To renew the subscription, contact the service provider{contact}.'
        btn = (f'<a class="b p" href="{EXPORT_PATH}">Download a full copy of your data</a>' if can_export else
               f'<a class="b" href="{e(login_path)}">System administrator sign-in to download the data</a>')
        other = '<a class="b" href="?lang=ar" lang="ar">العربية</a>'
    else:
        title, msg = 'انتهى الاشتراك', (f'انتهى اشتراك هذا النظام في <span dir="ltr">{e(lic["until"])}</span>، فأُوقف مؤقتاً. '
                                        'بياناتكم محفوظة كاملة ولم يُحذف منها شيء.')
        renew = f'لتجديد الاشتراك تواصل مع مزوّد الخدمة{contact}.'
        btn = (f'<a class="b p" href="{EXPORT_PATH}">حمِّل نسخة كاملة من بياناتكم</a>' if can_export else
               f'<a class="b" href="{e(login_path)}">دخول مدير النظام لتحميل البيانات</a>')
        other = '<a class="b" href="?lang=en" lang="en">English</a>'
    return f'''<!DOCTYPE html>
<html lang="{lang}" dir="{'ltr' if en else 'rtl'}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f7fb;color:#0f172a;
font:16px/1.7 system-ui,Tahoma,"Segoe UI",sans-serif;padding:16px;box-sizing:border-box}}
.box{{background:#fff;border-radius:18px;box-shadow:0 10px 30px rgb(15 23 42/.08);padding:32px 24px;max-width:520px;text-align:center}}
.ic{{width:64px;height:64px;border-radius:50%;background:#fee2e2;color:#b91c1c;display:grid;place-items:center;margin:0 auto 14px;font-size:30px}}
h1{{font-size:1.3rem;margin:0 0 10px}} p{{margin:0 0 12px}} .h{{color:#475569;font-size:.9rem}}
.b{{display:inline-block;margin:6px 4px 0;padding:10px 18px;border-radius:10px;border:1px solid #cbd5e1;color:#0f172a;text-decoration:none;font-weight:600}}
.b.p{{background:#0C67EC;border-color:#0C67EC;color:#fff}}
</style></head><body><main class="box"><div class="ic" aria-hidden="true">&#128274;</div>
<h1>{title}</h1><p>{msg}</p><p class="h">{renew}</p>{btn} {other}</main></body></html>'''


# ── النسخة الكاملة ────────────────────────────────────────────────────────────────────────────────────────────────
def _is_secret(name):
    n = name.lower()
    return any(h in n for h in _SECRET_HINTS) or n in ('auth', 'pin', 'pin_hash')


def _cell(v):
    if v is None:
        return ''
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f'[binary {len(bytes(v))} bytes]'
    if isinstance(v, datetime):
        return v.isoformat(sep=' ', timespec='seconds')
    return v


def _csv_bytes(cols, rows):
    buf = io.StringIO()
    buf.write('﻿')
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([_cell(v) for v in r])
    return buf.getvalue()


def _sqlite_tables(conn):
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _copy_sqlite(src_path, zf, arcname):
    fd, tmp = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    try:
        src = sqlite3.connect(src_path, timeout=30)
        dst = sqlite3.connect(tmp)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        zf.write(tmp, arcname)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def build_export(system_name, sqlalchemy_db=None, sqlite_path=None, upload_dirs=()):
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    fd, path = tempfile.mkstemp(prefix='export-', suffix='.zip')
    os.close(fd)
    counts = []
    db_file = None
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        if sqlite_path:
            db_file = sqlite_path
            conn = sqlite3.connect(sqlite_path, timeout=30)
            try:
                for t in _sqlite_tables(conn):
                    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")') if not _is_secret(r[1])]
                    if not cols:
                        continue
                    rows = conn.execute('SELECT ' + ', '.join(f'"{c}"' for c in cols) + f' FROM "{t}"').fetchall()
                    zf.writestr(f'csv/{t}.csv', _csv_bytes(cols, rows))
                    counts.append((t, len(rows)))
            finally:
                conn.close()
        elif sqlalchemy_db is not None:
            from sqlalchemy import MetaData, select
            engine = sqlalchemy_db.engine
            md = MetaData()
            md.reflect(bind=engine)
            with engine.connect() as conn:
                for t in md.sorted_tables:
                    cols = [c for c in t.columns if not _is_secret(c.name)]
                    if not cols or t.name == 'alembic_version':
                        continue
                    rows = conn.execute(select(*cols)).fetchall()
                    zf.writestr(f'csv/{t.name}.csv', _csv_bytes([c.name for c in cols], rows))
                    counts.append((t.name, len(rows)))
            if engine.url.get_backend_name() == 'sqlite' and engine.url.database:
                db_file = engine.url.database
        if db_file and os.path.isfile(db_file):
            _copy_sqlite(db_file, zf, f'database/{os.path.basename(db_file)}')
        for up in (upload_dirs or []):
            if up and os.path.isdir(up):
                base = os.path.basename(os.path.normpath(up))
                for root, _d, files in os.walk(up):
                    for n in files:
                        full = os.path.join(root, n)
                        zf.write(full, f'{base}/' + os.path.relpath(full, up).replace(os.sep, '/'))
        stamp = now.strftime('%Y-%m-%d %H:%M')
        lines = [f'نسخة كاملة من بيانات النظام: {system_name}', f'تاريخ التصدير: {stamp} (بتوقيت عمّان)', '',
                 'csv/       جدول لكل نوع بيانات، يُفتح في Excel. الأعمدة السرية (كلمات المرور والمفاتيح) مستبعدة.',
                 'database/  القاعدة نفسها (SQLite) لاسترجاع النظام كاملاً، إن كان النظام يستعمل SQLite.', '',
                 'الجداول وعدد السجلات:'] + [f'  {n}: {c}' for n, c in counts] + [
                 '', '-' * 60, f'Full copy of the system data: {system_name}', f'Exported: {stamp} (Amman time)', '',
                 'csv/       one table per data type; opens in Excel. Secret columns (passwords, keys) are left out.',
                 'database/  the database itself (SQLite), to restore the whole system, when the system uses SQLite.']
        zf.writestr('README.txt', '\r\n'.join(lines) + '\r\n')
    slug = re.sub(r'[^a-z0-9]+', '-', system_name.lower()).strip('-') or 'system'
    return path, f'{slug}-data-{now.strftime("%Y-%m-%d")}.zip'


# ── التثبيت ───────────────────────────────────────────────────────────────────────────────────────────────────────
def install(app, *, is_admin, system_name, allow_paths=(), login_path='/login',
            sqlalchemy_db=None, sqlite_path=None, upload_dirs=None):
    """is_admin: دالة بلا وسائط تعيد True لمدير النظام الحالي. sqlite_path/upload_dirs: قيم أو دوال تعيد القيمة."""
    from flask import Response, jsonify, request, send_file

    def _val(v):
        return v() if callable(v) else v

    def _admin():
        try:
            return bool(is_admin())
        except Exception:
            return False

    allowed = tuple(allow_paths) + ('/static/', EXPORT_PATH, '/favicon', '/robots.txt')

    @app.route(EXPORT_PATH)
    def license_full_export():
        if not _admin():
            from flask import redirect
            return redirect(login_path)
        path, name = build_export(system_name, sqlalchemy_db=sqlalchemy_db, sqlite_path=_val(sqlite_path),
                                  upload_dirs=_val(upload_dirs) or ())
        resp = send_file(path, mimetype='application/zip', as_attachment=True, download_name=name)

        @resp.call_on_close
        def _rm():
            try:
                os.remove(path)
            except OSError:
                pass
        return resp

    @app.before_request
    def _license_gate():
        lic = status()
        if lic['state'] != 'expired':
            return None
        p = request.path or '/'
        if any(p == a.rstrip('/') or p.startswith(a) for a in allowed):
            return None
        if '/api/' in p or request.is_json or 'application/json' in (request.headers.get('Accept') or ''):
            en = _lang() == 'en'
            return jsonify({'ok': False, 'success': False, 'license': 'expired',
                            'error': ('The subscription for this system has ended.' if en
                                      else 'انتهى اشتراك هذا النظام.')}), 403
        return Response(_locked_page(lic, _lang(), _admin(), login_path), status=403, mimetype='text/html')

    @app.after_request
    def _license_banner(resp):
        try:
            lic = status()
            if lic['state'] not in ('warn', 'grace') or resp.mimetype != 'text/html' or resp.direct_passthrough \
                    or resp.status_code != 200 or not _admin():
                return resp
            body = resp.get_data(as_text=True)
            m = re.search(r'<body[^>]*>', body, re.I)
            if not m:
                return resp
            lm = re.search(r'<html[^>]*\blang="(en|ar)', body[:600], re.I)
            lang = (lm.group(1).lower() if lm else _lang())
            resp.set_data(body[:m.end()] + _banner(lic, lang) + body[m.end():])
        except Exception:
            pass
        return resp
