from datetime import datetime

from flask import (Blueprint, jsonify, redirect, render_template, request, session,
                   url_for)

from models import db, Device
from services import build_schedule_payload, find_by_code, normalize_code, schedule_version

client_bp = Blueprint('client', __name__)


def _current_owner():
    """Browsers (PWA) authenticate with the session cookie; the native apps
    (Android/iOS/desktop) store the code on the device and send it as a header."""
    code = request.headers.get('X-Bell-Code') or session.get('code')
    if not code:
        return None, None
    return find_by_code(code)


@client_bp.after_app_request
def _cors(resp):
    # Native shells load their UI from capacitor://localhost, https://localhost
    # or a file:// page, so the API must answer cross-origin. Auth is a header,
    # never a cookie, for these requests — so a wildcard origin is safe.
    if request.path.startswith('/api/') or request.path.startswith('/media/'):
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Bell-Code, X-Device-Id'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Max-Age'] = '86400'
    return resp


@client_bp.route('/api/login', methods=['POST', 'OPTIONS'])
def api_login():
    """Code check for the native apps (no session). Returns who the code is."""
    if request.method == 'OPTIONS':
        return '', 204
    code = normalize_code((request.get_json(silent=True) or {}).get('code'))
    kind, owner = find_by_code(code)
    if not kind:
        return jsonify({'error': 'code_invalid'}), 401
    who = owner.full_name('ar') if kind == 'section' else owner.name_ar
    return jsonify({'ok': True, 'code': code, 'type': kind, 'name': who})


@client_bp.route('/')
def index():
    return redirect(url_for('client.app_page') if session.get('code') else url_for('client.login'))


@client_bp.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        code = normalize_code(request.form.get('code'))
        kind, owner = find_by_code(code)
        if kind:
            session.permanent = True
            session['code'] = code
            return redirect(url_for('client.app_page'))
        error = 'invalid'
    return render_template('login.html', error=error)


@client_bp.route('/logout', methods=['POST'])
def logout():
    session.pop('code', None)
    return redirect(url_for('client.login'))


@client_bp.route('/app')
def app_page():
    # With no session the page still renders: a device that already cached a
    # schedule keeps alerting; one with nothing cached is sent to /login by JS.
    return render_template('display.html', logged_in=bool(session.get('code')))


@client_bp.route('/api/schedule', methods=['GET', 'OPTIONS'])
def api_schedule():
    if request.method == 'OPTIONS':
        return '', 204
    kind, owner = _current_owner()
    if not kind:
        return jsonify({'error': 'code_invalid'}), 401

    uid = (request.headers.get('X-Device-Id') or '')[:64]
    if uid:
        d = Device.query.filter_by(device_uid=uid).first()
        if not d:
            d = Device(device_uid=uid)
            db.session.add(d)
        d.code_type, d.owner_id = kind, owner.id
        d.user_agent = (request.headers.get('User-Agent') or '')[:300]
        d.last_sync = datetime.utcnow()
        db.session.commit()

    key = f'{kind}:{owner.id}'
    if request.args.get('v', type=int) == schedule_version() and request.args.get('k') == key:
        return jsonify({'unchanged': True, 'version': schedule_version()})
    payload = build_schedule_payload(kind, owner)
    payload['key'] = key
    resp = jsonify(payload)
    resp.headers['Cache-Control'] = 'no-store'
    return resp
