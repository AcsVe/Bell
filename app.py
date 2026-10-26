import os
from datetime import timedelta

from flask import (Flask, Response, abort, redirect, request, send_from_directory,
                   session, url_for)

from models import db, MediaFile, ensure_schema


import re as _re

# In right-to-left text "1-3" renders as "3-1" and "1+2" as "2+1". Wrap number
# ranges in a left-to-right isolate (U+2066 … U+2069) wherever a template prints text.
_NUM_RANGE = _re.compile(r'(?<![\u2066\d:./\-])(\d+\s*[-+–]\s*\d+)(?![\d\u2069:./\-])')


_ARABIC = _re.compile('[\u0600-\u06FF]')


_TOKEN = _re.compile(r'[A-Za-z0-9_\-]{20,}')


def bidi_fix(value):
    # A long key/token (e.g. a bell device key from secrets.token_urlsafe) is never a number range: leave it intact.
    if isinstance(value, str) and _TOKEN.fullmatch(value):
        return value
    if isinstance(value, str) and _NUM_RANGE.search(value) and '\u2066' not in value:
        from markupsafe import Markup
        if _ARABIC.search(value) or '<' in value:
            fixed = _NUM_RANGE.sub('\u2066\\1\u2069', str(value))
        else:
            # A Latin name like "Primary1-3": keep it whole, left-to-right, so an
            # Arabic page shows it as written instead of "1-3Primary".
            fixed = '\u2066' + str(value) + '\u2069'
        return Markup(fixed) if isinstance(value, Markup) else fixed
    return value


def normalize_db_url(url):
    """Whatever form Render/Neon give (postgres://, postgresql://,
    postgresql+psycopg2://, postgresql+psycopg://), always use the psycopg 3
    driver, which ships ready-made wheels for current Python versions."""
    url = (url or '').strip()
    for prefix in ('postgres://', 'postgresql://', 'postgresql+psycopg2://', 'postgresql+psycopg://'):
        if url.startswith(prefix):
            return 'postgresql+psycopg://' + url[len(prefix):]
    return url


def _secret_key():
    """SECRET_KEY from the environment (.env on the school PC, Render's generated value), otherwise a random key
    generated once and kept next to the database — never a fixed default that would let anyone forge a session."""
    key = os.environ.get('SECRET_KEY')
    if key:
        return key
    folder = os.environ.get('DATA_DIR') or os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(folder, '.secret_key')
    try:
        with open(path, encoding='utf-8') as f:
            key = f.read().strip()
    except OSError:
        key = ''
    if len(key) < 32:
        import secrets
        key = secrets.token_hex(32)
        try:
            os.makedirs(folder, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(key)
        except OSError:
            pass
    return key


def create_app(test_config=None):
    app = Flask(__name__)
    app.config['SECRET_KEY'] = _secret_key()
    app.config['ADMIN_USER'] = os.environ.get('ADMIN_USER', 'admin')
    app.config['ADMIN_PASS'] = os.environ.get('ADMIN_PASS', 'admin')
    # Microsoft Graph mail (mail.py) — written to .env by ops/setup_local.py; all optional.
    for key in ('MS_TENANT_ID', 'MS_CLIENT_ID', 'MS_CLIENT_SECRET', 'MS_SENDER_EMAIL'):
        app.config[key] = os.environ.get(key, '')
    # Long-lived session (same pattern as the venues print station): a
    # classroom tablet logs in once with its code and stays logged in.
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 60 * 60 * 24 * 7

    db_path = os.path.join(os.path.dirname(__file__), 'bell.db')
    database_url = normalize_db_url(os.environ.get('DATABASE_URL', f'sqlite:///{db_path}'))
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # Managed Postgres (Neon/Render) drops idle connections; ping before reuse.
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True, 'pool_recycle': 280}
    if os.environ.get('RENDER') or os.environ.get('SECURE_COOKIES'):
        app.config['SESSION_COOKIE_SECURE'] = True
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    app.jinja_env.finalize = bidi_fix

    from blueprints.admin import admin_bp
    from blueprints.client import client_bp
    from blueprints.central import central_bp
    app.register_blueprint(admin_bp)
    app.register_blueprint(client_bp)
    app.register_blueprint(central_bp)

    # حماية مصدر الصفحات: HTML مشفّر + منع الزر الأيمن وF12 وعرض المصدر — انظر page_shield.py
    from page_shield import install as _shield
    _shield(app)

    @app.after_request
    def _firmware_no_cache(resp):
        # the device firmware changes with each build: never serve a stale manifest or .bin from the browser cache
        if request.path.startswith('/static/firmware/'):
            resp.headers['Cache-Control'] = 'no-cache, max-age=0'
        return resp

    with app.app_context():
        db.create_all()
        ensure_schema()

    @app.context_processor
    def inject_asset():
        """asset('css/admin.css') → /static/css/admin.css?v=<mtime>: a new URL after every
        deploy, so phones never keep an old stylesheet from their 7-day cache."""
        def asset(filename):
            try:
                v = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
            except OSError:
                v = 0
            return url_for('static', filename=filename, v=v)
        return {'asset': asset}

    @app.context_processor
    def inject_lang():
        lang = session.get('lang', 'ar')

        def L(ar, en):
            return en if lang == 'en' else ar
        return {'lang': lang, 'L': L, 'dir': 'ltr' if lang == 'en' else 'rtl'}

    @app.context_processor
    def inject_brand():
        """The organization's identity (Settings ← الهوية): names, accent colour and logo. Until a logo is
        uploaded the system icon (static/brand/system.svg) is shown everywhere."""
        from datetime import datetime
        from models import AppSetting
        try:
            org_ar = AppSetting.get('org_name_ar') or ''
            org_en = AppSetting.get('org_name_en') or ''
            accent = AppSetting.get('accent') or ''
            logo_id = AppSetting.get('org_logo_id') or ''
            logo = db.session.get(MediaFile, int(logo_id)) if logo_id else None
        except Exception:
            db.session.rollback()
            org_ar = org_en = accent = ''
            logo = None
        lang = session.get('lang', 'ar')
        org = (org_en or org_ar) if lang == 'en' else (org_ar or org_en)
        return {'org_name': org,
                'accent': accent if _re.fullmatch(r'#[0-9A-Fa-f]{6}', accent or '') else '#0C67EC',
                'brand_logo_url': logo.url if logo else url_for('static', filename='brand/system.svg'),
                'brand_is_icon': not logo,
                'now_year': datetime.now().year,
                'admin_name': app.config.get('ADMIN_USER') or 'admin'}

    @app.route('/lang/<code>')
    def set_lang(code):
        session['lang'] = 'en' if code == 'en' else 'ar'
        session.permanent = True
        return redirect(request.args.get('next') or url_for('client.app_page'))

    # Service worker served from the root so its scope covers the whole site.
    @app.route('/sw.js')
    def service_worker():
        resp = send_from_directory(os.path.join(app.root_path, 'static', 'js'), 'sw.js',
                                   mimetype='application/javascript', max_age=0)
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        resp.headers['Service-Worker-Allowed'] = '/'
        return resp

    @app.route('/manifest.webmanifest')
    def manifest():
        return send_from_directory(os.path.join(app.root_path, 'static'), 'manifest.webmanifest',
                                   mimetype='application/manifest+json', max_age=3600)

    @app.route('/media/<int:media_id>')
    def media(media_id):
        m = db.session.get(MediaFile, media_id)
        if not m:
            abort(404)
        resp = Response(m.data, mimetype=m.mime)
        # URLs carry ?v=<timestamp>, so a replaced file gets a new URL.
        resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return resp

    @app.route('/healthz')
    def healthz():
        return 'ok'

    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
