import os
from datetime import timedelta

from flask import (Flask, Response, abort, redirect, request, send_from_directory,
                   session, url_for)

from models import db, MediaFile, ensure_schema


import re as _re

# In right-to-left text "1-3" renders as "3-1" and "1+2" as "2+1". Wrap number
# ranges in a left-to-right isolate (U+2066 … U+2069) wherever a template prints text.
_NUM_RANGE = _re.compile(r'(?<![\u2066\d:./\-])(\d+\s*[-+–]\s*\d+)(?![\d\u2069:./\-])')


def bidi_fix(value):
    if isinstance(value, str) and _NUM_RANGE.search(value):
        from markupsafe import Markup
        fixed = _NUM_RANGE.sub('\u2066\\1\u2069', str(value))
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


def create_app(test_config=None):
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-change-me')
    app.config['ADMIN_USER'] = os.environ.get('ADMIN_USER', 'admin')
    app.config['ADMIN_PASS'] = os.environ.get('ADMIN_PASS', 'admin')
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
    app.register_blueprint(admin_bp)
    app.register_blueprint(client_bp)

    with app.app_context():
        db.create_all()
        ensure_schema()

    @app.context_processor
    def inject_lang():
        lang = session.get('lang', 'ar')

        def L(ar, en):
            return en if lang == 'en' else ar
        return {'lang': lang, 'L': L, 'dir': 'ltr' if lang == 'en' else 'rtl'}

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
