"""Central (office) bell: admin page + the API used by the office player (bell_player/bell_player.py)."""
from datetime import date, datetime

from flask import (Blueprint, flash, jsonify, redirect, render_template, request, session, url_for)

import central
from blueprints.admin import _msg, _save_upload, login_required
from models import db, MediaFile
from services import WEEK_ORDER, weekday_names

central_bp = Blueprint('central', __name__)


def _today():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('Asia/Amman'))
    except Exception:   # noqa: BLE001 — tzdata missing: fall back to the server clock
        return datetime.now()


# ── admin ───────────────────────────────────────────────────────────────────
@central_bp.route('/admin/central')
@login_required
def page():
    if central.persist_defaults():
        db.session.commit()
    cfg = central.config()
    group_list = central.groups()
    tones = central.effective_tones(cfg, group_list)
    week = central.week_rings(cfg)
    now = _today()
    today_rings = week[now.weekday()]
    today_off = (not cfg['enabled']) or now.strftime('%Y-%m-%d') in (cfg.get('skip_dates') or [])
    next_ring = next((s for s in today_rings if s['t'] > now.strftime('%H:%M')), None)
    lib = [(k, central.tone_name(k, session.get('lang', 'ar')), central.tone_url(k)) for k in central.all_tone_keys()]
    new_key = session.pop('central_new_key', None)
    return render_template('admin/central.html', cfg=cfg, groups=group_list, tones=tones, week=week, lib=lib,
                           uploads=central.uploaded_tones(), st=central.status(), has_key=central.has_key(),
                           new_key=new_key, today_rings=today_rings, today_off=today_off, next_ring=next_ring,
                           now=now, week_order=WEEK_ORDER, wd_names=weekday_names(session.get('lang', 'ar')),
                           tone_name=lambda k: central.tone_name(k, session.get('lang', 'ar')),
                           tone_url=central.tone_url, silent=central.SILENT, server_url=request.host_url.rstrip('/'),
                           lib_urls={k: url for k, _n, url in lib}, week_version=central.player_payload()['version'])


@central_bp.route('/admin/central/save', methods=['POST'])
@login_required
def save():
    cfg = central.config()
    cfg['enabled'] = bool(request.form.get('enabled'))
    try:
        cfg['volume'] = max(10, min(100, int(request.form.get('volume') or 100)))
    except ValueError:
        pass
    dates = set()
    for raw in (request.form.get('skip_dates') or '').replace(',', '\n').splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            dates.add(date.fromisoformat(raw).isoformat())
        except ValueError:
            flash(_msg(f'تاريخ غير صحيح: {raw} (الصيغة 2026-12-31)', f'Invalid date: {raw} (format 2026-12-31)'), 'error')
    # keep only today and later (old days are useless to the player)
    today = _today().date().isoformat()
    cfg['skip_dates'] = sorted(d for d in dates if d >= today)
    valid = set(central.all_tone_keys()) | {central.SILENT}
    for g in central.groups():
        c, b = request.form.get(f'class__{g["key"]}'), request.form.get(f'break__{g["key"]}')
        cur = cfg['tones'].setdefault(g['key'], {})
        if c in valid:
            cur['class'] = c
        if b in valid:
            cur['break'] = b
    central.save_config(cfg)
    db.session.commit()
    flash(_msg('حُفظت إعدادات الجرس المركزي — يصل التحديث لجهاز المكتب خلال 15 ثانية',
               'Central bell saved — the office player picks it up within 15 seconds'), 'ok')
    return redirect(url_for('central.page'))


@central_bp.route('/admin/central/key', methods=['POST'])
@login_required
def key():
    session['central_new_key'] = central.new_player_key()
    db.session.commit()
    return redirect(url_for('central.page') + '#player-tab')


@central_bp.route('/admin/central/test', methods=['POST'])
@login_required
def test():
    tone = request.form.get('tone')
    if tone not in central.all_tone_keys():
        flash(_msg('اختر نغمة', 'Choose a tone'), 'error')
    else:
        central.request_test(tone)
        db.session.commit()
        st = central.status()
        flash(_msg('أُرسل طلب التجربة — يرنّ جهاز المكتب خلال 15 ثانية' if st.get('online') else
                   'أُرسل طلب التجربة، لكن جهاز المكتب غير متصل الآن — يرنّ إن اتصل خلال دقيقتين',
                   'Test sent — the office player rings within 15 seconds' if st.get('online') else
                   'Test sent, but the office player is offline — it rings if it connects within 2 minutes'), 'ok')
    return redirect(url_for('central.page') + '#tones-tab')


@central_bp.route('/admin/central/upload', methods=['POST'])
@login_required
def upload():
    f = request.files.get('tone')
    if f and f.filename and not f.filename.lower().endswith(('.wav', '.mp3')):
        flash(_msg('ارفع ملف WAV أو MP3', 'Upload a WAV or MP3 file'), 'error')
        return redirect(url_for('central.page') + '#tones-tab')
    m, changed = _save_upload('tone', 'central', 'audio/')
    if changed:
        db.session.commit()
        flash(_msg('رُفعت النغمة — اخترها لأي مجموعة', 'Tone uploaded — choose it for any group'), 'ok')
    return redirect(url_for('central.page') + '#tones-tab')


@central_bp.route('/admin/central/tone/<int:media_id>/delete', methods=['POST'])
@login_required
def tone_delete(media_id):
    m = db.get_or_404(MediaFile, media_id)
    if m.kind == 'central':
        cfg = central.config()
        key = f'media:{m.id}'
        for t in cfg['tones'].values():   # groups using it fall back to their default tone
            for k in ('class', 'break'):
                if t.get(k) == key:
                    t.pop(k)
        central.save_config(cfg)
        db.session.delete(m)
        db.session.commit()
    return redirect(url_for('central.page') + '#tones-tab')


# ── office player API (X-Bell-Key header) ───────────────────────────────────
def _authorized():
    return central.check_key(request.headers.get('X-Bell-Key') or '')


@central_bp.route('/api/central/schedule')
def api_schedule():
    if not _authorized():
        return jsonify({'error': 'key_invalid'}), 401
    body = central.player_payload()
    if request.args.get('v') == body['version']:
        return jsonify({'unchanged': True, 'version': body['version']})
    resp = jsonify(body)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@central_bp.route('/api/central/poll', methods=['POST'])
def api_poll():
    """Every ~15 s: the player reports its state; the reply says whether the schedule changed and carries a
    pending test ring."""
    if not _authorized():
        return jsonify({'error': 'key_invalid'}), 401
    data = request.get_json(silent=True) or {}
    central.record_status(data, (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip())
    db.session.commit()
    now = datetime.utcnow()
    return jsonify({'version': central.player_payload()['version'], 'test': central.pending_test(),
                    'server_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ')})
