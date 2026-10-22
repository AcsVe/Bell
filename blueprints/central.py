"""Central (office) bell: admin page + the API used by the office player (bell_player/bell_player.py)."""
import time
from datetime import date, datetime, timedelta

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
    central.persist_defaults()
    devs = central.devices()             # also moves an old single «office player» into the device list
    db.session.commit()
    cfg = central.config()
    group_list = central.groups()
    tones = central.effective_tones(cfg, group_list)
    week = central.week_rings(cfg)
    now = _today()
    today_rings = week[now.weekday()]
    events = central.off_events()
    off_map = central.off_dates('*', start=now.date() - timedelta(days=62), days=62 + 400,
                                events=events)
    today_off = (not cfg['enabled']) or now.strftime('%Y-%m-%d') in off_map
    next_ring = next((s for s in today_rings if s['t'] > now.strftime('%H:%M')), None)
    lib = [(k, central.tone_name(k, session.get('lang', 'ar')), central.tone_url(k)) for k in central.library_keys(cfg)]
    hidden_lib = [(k, central.tone_name(k, session.get('lang', 'ar')), central.tone_url(k)) for k in cfg.get('hidden_tones') or []
                  if k in central.CENTRAL_TONES]
    new_key = session.pop('central_new_key', None)
    new_key_for = session.pop('central_new_key_for', None)
    return render_template('admin/central.html', cfg=cfg, groups=group_list, tones=tones, week=week, lib=lib,
                           uploads=central.uploaded_tones(), st=central.status(), has_key=central.has_key(),
                           new_key=new_key, new_key_for=new_key_for, devices=devs, today_rings=today_rings, today_off=today_off, next_ring=next_ring,
                           now=now, week_order=WEEK_ORDER, wd_names=weekday_names(session.get('lang', 'ar')),
                           tone_name=lambda k: central.tone_name(k, session.get('lang', 'ar')),
                           tone_url=central.tone_url, silent=central.SILENT, custom=central.custom_rings(), group_periods=central.group_periods, hidden_lib=hidden_lib, server_url=request.host_url.rstrip('/'),
                           lib_urls={k: url for k, _n, url in lib}, off_events=events, off_map=off_map,
                           off_info={e['id']: central.off_dates('*', start=now.date(), events=[e]) for e in events}, dev_versions={d['id']: central.player_payload(d['id'])['version'] for d in devs})


@central_bp.route('/admin/central/save', methods=['POST'])
@login_required
def save():
    cfg = central.config()
    cfg['enabled'] = bool(request.form.get('enabled'))
    try:
        cfg['volume'] = max(10, min(100, int(request.form.get('volume') or 100)))
    except ValueError:
        pass
    dates = set(cfg.get('skip_dates') or []) if 'skip_dates' not in request.form else set()
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
    if request.form.get('switches'):         # the on/off switches of every timing and period were on the form
        seen_g = request.form.getlist('grp_seen')
        seen_p = request.form.getlist('per_seen')
        cfg['off_groups'] = sorted(g for g in seen_g if not request.form.get('on__' + g))
        cfg['off_periods'] = sorted(p for p in seen_p if not request.form.get('on__' + p))
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
    flash(_msg('حُفظت إعدادات الجرس المركزي، ويصل التحديث إلى أجهزة الجرس خلال 15 ثانية',
               'Central bell saved — the office player picks it up within 15 seconds'), 'ok')
    return redirect(url_for('central.page'))


@central_bp.route('/admin/central/key', methods=['POST'])
@login_required
def key():
    """New key for one device (form field «player»; default: the first device, created if there is none)."""
    pid = request.form.get('player') or None
    session['central_new_key'] = central.new_player_key(pid)
    session['central_new_key_for'] = pid or (sorted(central.players()) or [None])[0]
    db.session.commit()
    return redirect(url_for('central.page') + '#player-tab')


@central_bp.route('/admin/central/players/add', methods=['POST'])
@login_required
def player_add():
    pid, k = central.add_player(request.form.get('name'))
    session['central_new_key'], session['central_new_key_for'] = k, pid
    db.session.commit()
    flash(_msg('أُضيف الجهاز. انسخ مفتاحه الآن، وأدخله عند تثبيت BellPlayer عليه',
               'Device added — copy its key now and enter it when installing BellPlayer on it'), 'ok')
    return redirect(url_for('central.page') + '#player-tab')


@central_bp.route('/admin/central/players/<pid>/save', methods=['POST'])
@login_required
def player_save(pid):
    valid = {g['key'] for g in central.groups()}
    if request.form.get('all_groups'):
        groups = None
    else:
        groups = [g for g in request.form.getlist('groups') if g in valid]
    try:
        vol = int(request.form.get('volume') or 0)
        vol = max(10, min(100, vol)) if vol else None
    except ValueError:
        vol = None
    if central.update_player(pid, request.form.get('name'), groups, vol):
        db.session.commit()
        flash(_msg('حُفظت إعدادات الجهاز، ويصل إليه التغيير خلال 15 ثانية' + ('' if groups is None or groups else ' (تنبيه: لم تختر أي توقيت، فلن يرنّ)'),
                   'Device saved — it picks up the change within 15 seconds' + ('' if groups is None or groups else ' (note: no timing chosen, it will not ring)')), 'ok')
    return redirect(url_for('central.page') + '#player-tab')


@central_bp.route('/admin/central/players/<pid>/delete', methods=['POST'])
@login_required
def player_delete(pid):
    central.delete_player(pid)
    db.session.commit()
    flash(_msg('حُذف الجهاز، وأُوقف مفتاحه', 'Device removed — its key no longer works'), 'ok')
    return redirect(url_for('central.page') + '#player-tab')


@central_bp.route('/admin/central/test', methods=['POST'])
@login_required
def test():
    tone = request.form.get('tone')
    if tone not in central.all_tone_keys():
        flash(_msg('اختر نغمة', 'Choose a tone'), 'error')
    elif request.form.get('devsel') and not request.form.getlist('players'):
        flash(_msg('اختر جهازاً واحداً على الأقل', 'Choose at least one device'), 'error')
    else:
        chosen = request.form.getlist('players') or ([request.form['player']] if request.form.get('player') else [])
        known = set(central.players())
        chosen = [p for p in chosen if p in known]
        target = 'all' if not chosen or set(chosen) == known else chosen
        central.request_test(tone, target)
        db.session.commit()
        sts = [central.status(p) for p in (known if target == 'all' else chosen)]
        st = {'online': bool(sts) and all(x.get('online') for x in sts)}
        flash(_msg('أُرسل طلب التجربة، وترنّ الأجهزة المختارة خلال 15 ثانية' if st.get('online') else
                   'أُرسل طلب التجربة، لكن بعض الأجهزة المختارة غير متصلة الآن، وترنّ إن اتصلت خلال دقيقتين',
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
        flash(_msg('رُفعت النغمة، ويمكنك اختيارها لأي توقيت', 'Tone uploaded — choose it for any group'), 'ok')
    return redirect(url_for('central.page') + '#tones-tab')


@central_bp.route('/admin/central/tone/<key>/hide', methods=['POST'])
@login_required
def tone_hide(key):
    """Remove a built-in tone from the library (it can be restored). Refused while a timing or ring uses it."""
    if key not in central.CENTRAL_TONES:
        return redirect(url_for('central.page') + '#tones-tab')
    cfg = central.config()
    uses = central.tone_uses(key, cfg)
    if uses:
        flash(_msg('لا يمكن حذف النغمة لأنها مستخدمة في: ' + '، '.join(uses) + '. اختر لها نغمة أخرى أولاً.',
                   'This tone is in use: ' + ', '.join(uses) + '. Choose another tone there first.'), 'error')
    else:
        cfg['hidden_tones'] = sorted(set(cfg.get('hidden_tones') or []) | {key})
        central.save_config(cfg)
        db.session.commit()
        flash(_msg('حُذفت النغمة من المكتبة، ويمكنك إعادتها من «النغمات المحذوفة» أسفل الصفحة.',
                   'Tone removed from the library — restore it from “Removed tones” below.'), 'ok')
    return redirect(url_for('central.page') + '#tones-tab')


@central_bp.route('/admin/central/tone/<key>/restore', methods=['POST'])
@login_required
def tone_restore(key):
    cfg = central.config()
    cfg['hidden_tones'] = [k for k in cfg.get('hidden_tones') or [] if k != key]
    central.save_config(cfg)
    db.session.commit()
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
    """→ the device id of the X-Bell-Key header, or None."""
    pid = central.check_key(request.headers.get('X-Bell-Key') or '')
    db.session.commit()                  # players() may have moved the old single key into the device list
    return pid


@central_bp.route('/api/central/schedule')
def api_schedule():
    pid = _authorized()
    if not pid:
        return jsonify({'error': 'key_invalid'}), 401
    body = central.player_payload(pid)
    if request.args.get('v') == body['version']:
        return jsonify({'unchanged': True, 'version': body['version']})
    resp = jsonify(body)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@central_bp.route('/api/central/poll', methods=['POST'])
def api_poll():
    """Every ~15 s: the player reports its state; the reply says whether the schedule changed and carries a
    pending test ring."""
    pid = _authorized()
    if not pid:
        return jsonify({'error': 'key_invalid'}), 401
    data = request.get_json(silent=True) or {}
    central.record_status(pid, data, (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip())
    db.session.commit()
    now = datetime.utcnow()
    loc = _today()
    off = loc.utcoffset()
    return jsonify({'version': central.player_payload(pid)['version'], 'test': central.pending_test(pid),
                    'server_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    # the player rings by the SERVER's clock: exact time (ms) + the school's UTC offset
                    'server_ms': int(time.time() * 1000),
                    'tz_offset_min': int(off.total_seconds() // 60) if off is not None else 180,
                    # the same server on the school network — used when the internet is down
                    'lan': central.lan_urls()})


# ── custom rings (name + any time) ──────────────────────────────────────────
def _custom_from_form(cur=None):
    import re
    c = dict(cur or {})
    name = (request.form.get('name') or '').strip()[:60]
    t = (request.form.get('t') or '').strip()
    if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', t):
        return None, _msg('أدخل وقتاً صحيحاً مثل 07:45', 'Enter a valid time such as 07:45')
    days = sorted({int(d) for d in request.form.getlist('days') if d.isdigit() and 0 <= int(d) <= 6})
    if not days:
        return None, _msg('اختر يوماً واحداً على الأقل', 'Choose at least one day')
    tone = request.form.get('tone')
    if tone not in central.all_tone_keys():
        return None, _msg('اختر نغمة', 'Choose a tone')
    known = set(central.players())
    devs = [d for d in request.form.getlist('devices') if d in known]
    c.update({'name': name or t, 't': t, 'days': days, 'tone': tone, 'enabled': bool(request.form.get('enabled')),
              'devices': None if request.form.get('all_devices') or not known or set(devs) == known else devs})
    return c, None


@central_bp.route('/admin/central/custom/add', methods=['POST'])
@login_required
def custom_add():
    import uuid
    c, err = _custom_from_form()
    if err:
        flash(err, 'error')
    else:
        items = central.custom_rings()
        c['id'] = uuid.uuid4().hex[:8]
        items.append(c)
        central.save_custom_rings(items)
        db.session.commit()
        flash(_msg(f'أُضيف «{c["name"]}» الساعة {c["t"]}', f'Added “{c["name"]}” at {c["t"]}'), 'ok')
    return redirect(url_for('central.page') + '#custom-tab')


@central_bp.route('/admin/central/custom/<cid>/save', methods=['POST'])
@login_required
def custom_save(cid):
    items = central.custom_rings()
    for i, cur in enumerate(items):
        if cur.get('id') == cid:
            c, err = _custom_from_form(cur)
            if err:
                flash(err, 'error')
            else:
                items[i] = c
                central.save_custom_rings(items)
                db.session.commit()
                flash(_msg('حُفظ', 'Saved'), 'ok')
            break
    return redirect(url_for('central.page') + '#custom-tab')


@central_bp.route('/admin/central/custom/<cid>/delete', methods=['POST'])
@login_required
def custom_delete(cid):
    central.save_custom_rings([c for c in central.custom_rings() if c.get('id') != cid])
    db.session.commit()
    return redirect(url_for('central.page') + '#custom-tab')


# ── calendar of days without the bell ──────────────────────────────────────
def _off_from_form(cur=None):
    e = dict(cur or {})
    name = (request.form.get('name') or '').strip()[:80]
    kind = request.form.get('kind') if request.form.get('kind') in ('range', 'dates') else 'range'
    rep = request.form.get('repeat') if request.form.get('repeat') in ('none', 'monthly', 'yearly') else 'none'
    if not name:
        return None, _msg('اكتب اسم الحدث (مثل: عطلة منتصف الفصل)', 'Enter the event name (e.g. Mid-term holiday)')
    a = central._d(request.form.get('from'))
    b = central._d(request.form.get('to')) or a
    dates = []
    if kind == 'range':
        if not a:
            return None, _msg('اختر تاريخ البداية (من)', 'Choose the start date (from)')
        if b < a:
            return None, _msg('تاريخ النهاية قبل البداية', 'The end date is before the start date')
    else:
        bad = []
        for raw in (request.form.get('dates') or '').replace(',', '\n').replace('،', '\n').split():
            d = central._d(raw.strip())
            (dates.append(d.isoformat()) if d else bad.append(raw))
        if bad:
            return None, _msg('تاريخ غير صحيح: ' + '، '.join(bad) + ' (الصيغة 2026-12-31)',
                              'Invalid date: ' + ', '.join(bad) + ' (format 2026-12-31)')
        if not dates:
            return None, _msg('اختر يوماً واحداً على الأقل من الروزنامة', 'Pick at least one day on the calendar')
    until = central._d(request.form.get('until'))
    known = set(central.players())
    devs = [d for d in request.form.getlist('devices') if d in known]
    e.update({'name': name, 'kind': kind, 'repeat': rep,
              'from': a.isoformat() if kind == 'range' else '', 'to': b.isoformat() if kind == 'range' else '',
              'dates': sorted(set(dates)), 'until': until.isoformat() if until and rep != 'none' else '',
              'enabled': bool(request.form.get('enabled')),
              'devices': None if request.form.get('all_devices') or not known or set(devs) == known else devs})
    return e, None


@central_bp.route('/admin/central/off/add', methods=['POST'])
@central_bp.route('/admin/central/off/<eid>/save', methods=['POST'])
@login_required
def off_save(eid=None):
    import uuid
    items = central.off_events()
    idx = next((i for i, x in enumerate(items) if x.get('id') == eid), None) if eid else None
    if eid and idx is None:
        flash(_msg('الحدث غير موجود', 'Event not found'), 'error')
        return redirect(url_for('central.page') + '#off-tab')
    e, err = _off_from_form(items[idx] if idx is not None else None)
    if err:
        flash(err, 'error')
    else:
        if idx is None:
            e['id'] = uuid.uuid4().hex[:8]
            items.append(e)
        else:
            items[idx] = e
        central.save_off_events(items)
        db.session.commit()
        n = len(central.off_dates('*', events=[e]))
        flash(_msg(f'حُفظ «{e["name"]}» — {n} يوماً بلا جرس خلال السنة القادمة',
                   f'Saved “{e["name"]}” — {n} days without the bell in the coming year'), 'ok')
    return redirect(url_for('central.page') + '#off-tab')


@central_bp.route('/admin/central/off/bulk', methods=['POST'])
@login_required
def off_bulk():
    act = request.form.get('action')
    ids = set(request.form.getlist('ids'))
    items = central.off_events()
    if act == 'delete':
        left = [x for x in items if x.get('id') not in ids]
        n = len(items) - len(left)
        items = left
    elif act in ('enable', 'disable'):
        n = 0
        for x in items:
            if x.get('id') in ids:
                x['enabled'] = act == 'enable'
                n += 1
    else:
        return redirect(url_for('central.page') + '#off-tab')
    central.save_off_events(items)
    db.session.commit()
    verb = {'delete': ('حُذف', 'Deleted'), 'enable': ('فُعِّل', 'Enabled'), 'disable': ('عُطِّل', 'Disabled')}[act]
    flash(_msg(f'{verb[0]}: {n}', f'{verb[1]}: {n}'), 'ok')
    return redirect(url_for('central.page') + '#off-tab')


# ── bell DEVICE (ESP32 box): flashing page + Bluetooth programming ──────────
@central_bp.route('/admin/central/device')
@login_required
def device_page():
    import os
    devs = central.devices()
    db.session.commit()
    fw = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'firmware', 'manifest.json')
    fw_version = None
    if os.path.isfile(fw):
        try:
            import json
            fw_version = json.load(open(fw, encoding='utf-8')).get('version')
        except (OSError, ValueError):
            fw_version = '?'
    return render_template('admin/device.html', devices=devs, fw_version=fw_version,
                           server_url=request.host_url.rstrip('/'))


@central_bp.route('/admin/central/players/<pid>/payload.json')
@login_required
def player_payload_json(pid):
    """What the Bluetooth page sends to a device that has no network: its schedule + tones + the server time."""
    if pid not in central.players():
        return jsonify({'error': 'unknown device'}), 404
    body = central.player_payload(pid)
    off = _today().utcoffset()
    body.update({'server_ms': int(time.time() * 1000), 'tz_offset_min': int(off.total_seconds() // 60) if off else 180,
                 'lan': central.lan_urls()})
    return jsonify(body)


@central_bp.route('/admin/central/players/<pid>/newkey.json', methods=['POST'])
@login_required
def player_newkey_json(pid):
    if pid not in central.players():
        return jsonify({'error': 'unknown device'}), 404
    k = central.new_player_key(pid)
    db.session.commit()
    return jsonify({'key': k})


@central_bp.route('/admin/central/players/<pid>/ble_status', methods=['POST'])
@login_required
def player_ble_status(pid):
    """After programming over Bluetooth the page reports what the device said (so its card shows it)."""
    if pid not in central.players():
        return jsonify({'error': 'unknown device'}), 404
    data = request.get_json(silent=True) or {}
    data['via'] = 'bluetooth ' + datetime.now().strftime('%Y-%m-%d %H:%M')
    central.record_status(pid, data, 'bluetooth')
    db.session.commit()
    return jsonify({'ok': True})
