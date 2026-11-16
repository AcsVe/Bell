"""اشتراك النسخة المستضافة: تاريخ انتهاء يُضبط من صفحة «إدارة الاشتراكات» في بوابة العميل، أو في ملف الإعدادات (.env).

الربط بالملف المشترك الذي تكتبه البوابة (له الأولوية على المتغيّرات التالية):
    LICENSE_FILE=/opt/ras/licenses.json     مسار الملف
    LICENSE_ID=rasb                         مفتاح هذا النظام فيه (أول جزء من عنوانه)

أو مباشرة في .env:
    LICENSE_UNTIL=2027-08-31     آخر يوم في الاشتراك (بلا قيمة = بلا انتهاء)
    LICENSE_WARN_DAYS=30         عدد الأيام قبل الانتهاء التي يظهر فيها التنبيه في لوحة الإدارة
    LICENSE_GRACE_DAYS=7         مهلة بعد الانتهاء يبقى فيها النظام عاملاً مع تنبيه
    LICENSE_CONTACT=...          جهة التجديد التي تظهر في التنبيه وصفحة الإيقاف (هاتف أو بريد)

الحالات: none (بلا اشتراك) · ok · warn (اقترب الانتهاء) · grace (انتهى وفي المهلة) · expired (موقوف).
بعد الإيقاف تُعرض صفحة «انتهى الاشتراك» (الرمز 403) بدل صفحات النظام، ويبقى مفتوحاً ما يُمرَّر إلى install()
(الدخول والخروج، وما تحتاجه الأجهزة والتطبيقات لتعمل). لا يُحذف شيء ولا يتغيّر في القاعدة؛ تجديد التاريخ يعيد
النظام فوراً. الملف المفقود أو التالف لا يوقف النظام (يُرجع إلى .env).
"""
import json
import os
from datetime import date, datetime, timedelta, timezone

_FILE_CACHE = {'sig': None, 'data': {}}


def _int_env(name, default):
    try:
        return max(0, int((os.environ.get(name) or '').strip() or default))
    except ValueError:
        return default


def _today():
    # التوقيت المعتمد Asia/Amman (UTC+3 طوال العام)
    return (datetime.now(timezone.utc) + timedelta(hours=3)).date()


def _file_entry():
    """إدخال هذا النظام من ملف الاشتراكات المشترك، أو None إن لم يُربط أو تعذّرت قراءة الملف، و{} إن لا اشتراك له فيه.
    يُعاد تحميل الملف عند تغيّره فقط، فيسري التعديل فوراً دون إعادة تشغيل."""
    path = (os.environ.get('LICENSE_FILE') or '').strip()
    key = (os.environ.get('LICENSE_ID') or '').strip().lower()
    if not path or not key:
        return None
    try:
        st = os.stat(path)
        sig = (path, st.st_mtime_ns, st.st_size)
        if _FILE_CACHE['sig'] != sig:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            _FILE_CACHE.update(sig=sig, data=data if isinstance(data, dict) else {})
    except (OSError, ValueError):
        return None
    entry = _FILE_CACHE['data'].get(key)
    return entry if isinstance(entry, dict) else {}


def _entry_int(entry, name, env_name, default):
    try:
        return max(0, int(entry[name]))
    except (KeyError, TypeError, ValueError):
        return _int_env(env_name, default)


def status(today=None):
    """حالة الاشتراك الآن: dict فيه state وuntil ولآخر يوم في المهلة lock_from وعدد الأيام المتبقية days_left."""
    entry = _file_entry()
    if entry is not None:       # مربوط بملف الاشتراكات المشترك: له الأولوية على .env
        raw = str(entry.get('until') or '').strip()
        contact = str(entry.get('contact') or os.environ.get('LICENSE_CONTACT') or '').strip()
        grace = _entry_int(entry, 'grace_days', 'LICENSE_GRACE_DAYS', 7)
        warn = _entry_int(entry, 'warn_days', 'LICENSE_WARN_DAYS', 30)
    else:
        raw = (os.environ.get('LICENSE_UNTIL') or '').strip()
        contact = (os.environ.get('LICENSE_CONTACT') or '').strip()
        grace = _int_env('LICENSE_GRACE_DAYS', 7)
        warn = _int_env('LICENSE_WARN_DAYS', 30)
    if not raw:
        return {'state': 'none', 'until': '', 'lock_from': '', 'days_left': None, 'contact': contact}
    try:
        until = date.fromisoformat(raw)
    except ValueError:
        # تاريخ غير صالح: لا يُوقَف النظام بسبب خطأ كتابة، بل يُتجاهل ويُكتب تنبيه في سجل الخادم
        print('[license] the subscription end date is not a valid YYYY-MM-DD date - ignored', flush=True)
        return {'state': 'none', 'until': '', 'lock_from': '', 'days_left': None, 'contact': contact}
    today = today or _today()
    lock_from = until + timedelta(days=grace + 1)
    days_left = (until - today).days
    if today > until:
        state = 'expired' if today >= lock_from else 'grace'
    elif days_left <= warn:
        state = 'warn'
    else:
        state = 'ok'
    return {'state': state, 'until': until.isoformat(), 'lock_from': lock_from.isoformat(),
            'days_left': days_left, 'contact': contact}


def install(app, open_endpoints=(), open_prefixes=(), lang_of=None):
    """يضيف حالة الاشتراك إلى كل القوالب (lic)، ويوقف الصفحات بعد انتهاء المهلة.

    open_endpoints: أسماء المسارات التي تبقى مفتوحة بعد الإيقاف (الدخول والخروج…).
    open_prefixes:  بدايات عناوين تبقى مفتوحة (واجهات الأجهزة والتطبيقات…).
    lang_of:        دالة (request, session) ← 'ar' أو 'en' للغة صفحة الإيقاف."""
    from flask import jsonify, render_template, request, session

    open_eps = set(open_endpoints) | {'static'}
    prefixes = tuple(open_prefixes)

    @app.context_processor
    def _inject_license():
        return {'lic': status()}

    @app.before_request
    def _license_gate():
        lic = status()
        if lic['state'] != 'expired':
            return None
        if (request.endpoint or '') in open_eps or (prefixes and request.path.startswith(prefixes)):
            return None
        lang = (lang_of(request, session) if lang_of else (request.args.get('lang') or session.get('lang'))) or 'ar'
        lang = 'en' if lang == 'en' else 'ar'
        msg_ar = 'انتهى اشتراك هذا النظام. تواصل مع مزوّد الخدمة لتجديده.'
        msg_en = 'The subscription for this system has ended. Contact the service provider to renew it.'
        accept = request.headers.get('Accept', '')
        if request.path.startswith(('/api/', '/admin/api/')) or accept.startswith('application/json'):
            return jsonify({'success': False, 'error': msg_en if lang == 'en' else msg_ar, 'license': 'expired'}), 403
        return render_template('license_locked.html', lang=lang, lic=lic), 403
