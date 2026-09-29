import re
from functools import wraps

from flask import (Blueprint, current_app, flash, redirect, render_template, request,
                   send_file, session, url_for)

from models import (db, AppSetting, BellProgram, BellProgramPeriod, Device, Grade,
                    GradeDayProgram, GradePeriodTime, MediaFile, Period, Section, Stage,
                    StageDayPeriodTime, StageWeekday, Teacher, TeacherSection, TeacherStage)
from services import (WEEK_ORDER, bump_schedule_version, chain_times, default_period_labels,
                      generate_code, minutes_between, parse_duration, rechain_stage,
                      resolve_grade_day, valid_time, weekday_names,
                      duplicate_stages, merge_stages, name_range, stage_range)

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin.login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def _msg(ar, en):
    return en if session.get('lang') == 'en' else ar


@admin_bp.after_request
def _bump_on_change(resp):
    """Every successful admin write bumps the schedule version so devices
    pick up the change on their next silent sync."""
    if request.method == 'POST' and session.get('admin_logged_in') \
            and request.endpoint not in ('admin.login',) and resp.status_code < 400:
        bump_schedule_version()
        db.session.commit()
    return resp


EMAIL_IN_LINE = re.compile(r'[^\s,;<>]+@[^\s,;<>]+\.[^\s,;<>]+')


def norm_name(v):
    """Arabic-aware comparison key (same rules as normText in the admin pages)."""
    v = re.sub('[\u064B-\u0652\u0640\u2066-\u2069]', '', str(v or '')).lower()
    v = re.sub('[أإآ]', 'ا', v).replace('ة', 'ه').replace('ى', 'ي')
    return re.sub(r'\s+', ' ', v).strip()


def _form_text(name):
    # Drop the bidi isolates the templates add around number ranges.
    return re.sub('[\u2066-\u2069]', '', request.form.get(name) or '').strip()


def _save_upload(field, kind, allowed_prefix, existing=None):
    f = request.files.get(field)
    if not f or not f.filename:
        return existing, False
    mime = f.mimetype or ''
    if not mime.startswith(allowed_prefix):
        flash(_msg('نوع الملف غير مدعوم', 'Unsupported file type'), 'error')
        return existing, False
    data = f.read()
    if existing:
        existing.data, existing.mime, existing.filename = data, mime, f.filename
        return existing, True
    m = MediaFile(kind=kind, filename=f.filename, mime=mime, data=data)
    db.session.add(m)
    db.session.flush()
    return m, True


# ── Auth ──────────────────────────────────────────────────────────────────
@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if (request.form.get('username', '') == current_app.config['ADMIN_USER'] and
                request.form.get('password', '') == current_app.config['ADMIN_PASS']):
            session.permanent = True
            session['admin_logged_in'] = True
            nxt = request.args.get('next') or ''
            return redirect(nxt if nxt.startswith('/admin') else url_for('admin.dashboard'))
        error = _msg('بيانات الدخول غير صحيحة', 'Invalid credentials')
    return render_template('admin/login.html', error=error)


@admin_bp.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin.login'))


# ── Dashboard / stages ────────────────────────────────────────────────────
@admin_bp.route('/')
@login_required
def dashboard():
    stages = Stage.query.order_by(Stage.sort_order, Stage.id).all()
    stats = {
        'sections': Section.query.count(),
        'teachers': Teacher.query.count(),
        'devices': Device.query.count(),
    }
    return render_template('admin/dashboard.html', stages=stages, stats=stats,
                           dupes=duplicate_stages(stages))


def _create_stage(name_ar, name_en=None):
    st = Stage(name_ar=name_ar, name_en=name_en, sort_order=Stage.query.count())
    db.session.add(st)
    db.session.flush()
    for wd in (6, 0, 1, 2, 3):   # default week: Sunday → Thursday
        db.session.add(StageWeekday(stage_id=st.id, weekday=wd))
    return st


@admin_bp.route('/stages/add', methods=['POST'])
@login_required
def stage_add():
    name_ar = _form_text('name_ar')
    if not name_ar:
        flash(_msg('اسم المرحلة مطلوب', 'Stage name is required'), 'error')
        return redirect(url_for('admin.dashboard'))
    st = _create_stage(name_ar, _form_text('name_en') or None)
    db.session.commit()
    return redirect(url_for('admin.stage_view', stage_id=st.id))


@admin_bp.route('/stages/<int:stage_id>')
@login_required
def stage_view(stage_id):
    st = db.get_or_404(Stage, stage_id)
    day = request.args.get('day', type=int)
    if day is None and st.weekday_list:
        day = st.weekday_list[0] if 6 not in st.weekday_list else 6
    overrides = {o.period_number: o for o in StageDayPeriodTime.query.filter_by(
        stage_id=st.id, weekday=day)} if day is not None else {}
    eff = []
    for p in st.periods:
        o = overrides.get(p.number)
        s, e = (o.start_time, o.end_time) if o else (p.start_time, p.end_time)
        eff.append({'p': p, 'start': s, 'end': e, 'dur': minutes_between(s, e)})
    others = Stage.query.filter(Stage.id != st.id).order_by(Stage.sort_order, Stage.id).all()
    return render_template('admin/stage.html', st=st, day=day, overrides=overrides, eff=eff, others=others,
                           week_order=WEEK_ORDER, wd_names=weekday_names(session.get('lang', 'ar')))


@admin_bp.route('/stages/<int:stage_id>/update', methods=['POST'])
@login_required
def stage_update(stage_id):
    st = db.get_or_404(Stage, stage_id)
    st.name_ar = _form_text('name_ar') or st.name_ar
    st.name_en = _form_text('name_en') or None
    st.sort_order = request.form.get('sort_order', type=int) or 0
    st.active = bool(request.form.get('active'))
    chosen = {int(x) for x in request.form.getlist('weekdays')}
    for w in list(st.weekdays):
        if w.weekday not in chosen:
            db.session.delete(w)
    have = set(st.weekday_list)
    for wd in chosen - have:
        db.session.add(StageWeekday(stage_id=st.id, weekday=wd))
    db.session.commit()
    flash(_msg('تم الحفظ', 'Saved'), 'ok')
    return redirect(url_for('admin.stage_view', stage_id=st.id))


@admin_bp.route('/stages/<int:stage_id>/media', methods=['POST'])
@login_required
def stage_media(stage_id):
    st = db.get_or_404(Stage, stage_id)
    for field in ('logo', 'background'):
        if request.form.get(f'remove_{field}'):
            old = getattr(st, field)
            setattr(st, field, None)
            if old:
                db.session.delete(old)
            continue
        m, changed = _save_upload(field, field, 'image/', getattr(st, field))
        if changed and m is not getattr(st, field):
            setattr(st, field, m)
    db.session.commit()
    flash(_msg('تم تحديث الصور', 'Images updated'), 'ok')
    return redirect(url_for('admin.stage_view', stage_id=st.id))


@admin_bp.route('/stages/<int:stage_id>/tone', methods=['POST'])
@login_required
def stage_tone(stage_id):
    """Upload or remove this stage's own alert tone."""
    st = db.get_or_404(Stage, stage_id)
    back = request.form.get('next') or url_for('admin.settings') + '#tone-tab'
    if request.form.get('reset'):
        old = st.tone
        st.tone = None
        if old:
            db.session.delete(old)
        db.session.commit()
        flash(_msg(f'«{st.name_ar}» تعود للنغمة العامة', f'«{st.name_en or st.name_ar}» uses the default tone'), 'ok')
        return redirect(back)
    m, changed = _save_upload('tone', 'tone', 'audio/', st.tone)
    if changed:
        if m is not st.tone:
            st.tone = m
        db.session.commit()
        flash(_msg(f'تم حفظ نغمة «{st.name_ar}»', f'Tone saved for «{st.name_en or st.name_ar}»'), 'ok')
    elif not request.files.get('tone') or not request.files['tone'].filename:
        flash(_msg('اختر ملف النغمة', 'Choose the tone file'), 'error')
    return redirect(back)


@admin_bp.route('/stages/<int:stage_id>/delete', methods=['POST'])
@login_required
def stage_delete(stage_id):
    st = db.get_or_404(Stage, stage_id)
    for m in (st.logo, st.background, st.tone):
        if m:
            db.session.delete(m)
    for g in st.grades:
        _delete_grade_children(g)
    db.session.delete(st)
    db.session.commit()
    flash(_msg('تم حذف المرحلة', 'Stage deleted'), 'ok')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/stages/<int:stage_id>/merge', methods=['POST'])
@login_required
def stage_merge(stage_id):
    """Merge this stage into another: grades, sections, programs and teacher
    links move over; this stage is removed."""
    src = db.get_or_404(Stage, stage_id)
    dst = db.session.get(Stage, request.form.get('into', type=int) or 0)
    back = request.form.get('next') or url_for('admin.dashboard')
    if dst is None or dst.id == src.id:
        flash(_msg('اختر المرحلة التي تريد الدمج فيها', 'Choose the stage to merge into'), 'error')
        return redirect(back)
    src_media = [src.logo_id, src.background_id, src.tone_id]
    src_name, dst_name = src.name_ar, dst.name_ar
    merge_stages(src, dst)
    for mid in src_media:
        if mid and mid not in (dst.logo_id, dst.background_id, dst.tone_id):
            m = db.session.get(MediaFile, mid)
            if m:
                db.session.delete(m)
    db.session.commit()
    flash(_msg(f'تم دمج «{src_name}» في «{dst_name}»: الصفوف والشعب والبرامج والمعلمون أصبحوا في مرحلة واحدة.',
               f'Merged «{src_name}» into «{dst_name}».'), 'ok')
    return redirect(back if request.form.get('next') else url_for('admin.stage_view', stage_id=dst.id))


# ── Periods (back-to-back: day start + durations) ─────────────────────────
@admin_bp.route('/stages/<int:stage_id>/periods/add', methods=['POST'])
@login_required
def period_add(stage_id):
    st = db.get_or_404(Stage, stage_id)
    kind = 'break' if request.form.get('kind') == 'break' else 'class'
    dur = parse_duration(request.form.get('duration'))
    start = st.periods[-1].end_time if st.periods else st.day_start
    times = chain_times(start, [dur]) if dur else None
    if not times:
        flash(_msg('أدخل مدة صحيحة بالدقائق (1-240) لا تتجاوز منتصف الليل',
                   'Enter a valid duration in minutes (1-240) that ends before midnight'), 'error')
        return redirect(url_for('admin.stage_view', stage_id=st.id) + '#periods')
    number = max([p.number for p in st.periods] or [0]) + 1
    class_index = sum(1 for p in st.periods if p.kind == 'class') + 1
    d_ar, d_en = default_period_labels(kind, class_index)
    db.session.add(Period(stage_id=st.id, number=number, kind=kind,
                          label_ar=_form_text('label_ar') or d_ar,
                          label_en=_form_text('label_en') or d_en,
                          start_time=times[0][0], end_time=times[0][1]))
    db.session.flush()
    db.session.expire(st, ['periods'])
    rechain_stage(st)
    db.session.commit()
    return redirect(url_for('admin.stage_view', stage_id=st.id) + '#periods')


@admin_bp.route('/stages/<int:stage_id>/periods/save', methods=['POST'])
@login_required
def periods_save(stage_id):
    """Bulk-save day start + every period's label/type/duration in one submit;
    start/end times are recomputed as one gap-free chain."""
    st = db.get_or_404(Stage, stage_id)
    day_start = _form_text('day_start') or st.day_start
    if not valid_time(day_start):
        flash(_msg('وقت بداية الدوام غير صالح', 'Invalid day start time'), 'error')
        return redirect(url_for('admin.stage_view', stage_id=st.id) + '#periods')
    durs = []
    for p in st.periods:
        pre = f'p{p.id}_'
        durs.append(parse_duration(request.form.get(pre + 'duration')) or p.duration)
        p.label_ar = _form_text(pre + 'label_ar') or p.label_ar
        p.label_en = _form_text(pre + 'label_en') or None
        p.kind = 'break' if request.form.get(pre + 'kind') == 'break' else 'class'
    times = chain_times(day_start, durs)
    if times is None:
        db.session.rollback()
        flash(_msg('مجموع المدد يتجاوز منتصف الليل', 'Total duration runs past midnight'), 'error')
        return redirect(url_for('admin.stage_view', stage_id=st.id) + '#periods')
    st.day_start = day_start
    for p, (s, e) in zip(st.periods, times):
        p.start_time, p.end_time = s, e
    rechain_stage(st)
    db.session.commit()
    flash(_msg('تم حفظ الحصص', 'Periods saved'), 'ok')
    return redirect(url_for('admin.stage_view', stage_id=st.id) + '#periods')


@admin_bp.route('/periods/<int:period_id>/delete', methods=['POST'])
@login_required
def period_delete(period_id):
    p = db.get_or_404(Period, period_id)
    st = p.stage
    db.session.delete(p)
    db.session.flush()
    db.session.expire(st, ['periods'])
    rechain_stage(st)   # later periods move up; overrides drop this period
    db.session.commit()
    return redirect(url_for('admin.stage_view', stage_id=st.id) + '#periods')


def _save_day_override(model, owner_field, owner_id, weekdays, stage):
    """Whole-day override: a day start + a duration per period, stored as a
    complete gap-free set of rows. 'reset' removes the set (inherit again).
    Returns an error message or None."""
    q = lambda wd: model.query.filter_by(**{owner_field: owner_id}, weekday=wd)
    if request.form.get('reset'):
        for wd in weekdays:
            q(wd).delete()
        return None
    day_start = _form_text('day_start')
    durs = [parse_duration(request.form.get(f'n{p.number}_dur')) for p in stage.periods]
    if not valid_time(day_start) or None in durs:
        return _msg('أدخل وقت بداية ومدة صحيحة لكل حصة', 'Enter a start time and a valid duration for every period')
    times = chain_times(day_start, durs)
    if times is None:
        return _msg('مجموع المدد يتجاوز منتصف الليل', 'Total duration runs past midnight')
    for wd in weekdays:
        q(wd).delete()
        for p, (s, e) in zip(stage.periods, times):
            db.session.add(model(**{owner_field: owner_id}, weekday=wd, period_number=p.number,
                                 start_time=s, end_time=e))
    return None


@admin_bp.route('/stages/<int:stage_id>/day-times', methods=['POST'])
@login_required
def stage_day_times(stage_id):
    st = db.get_or_404(Stage, stage_id)
    day = request.form.get('weekday', type=int)
    err = _save_day_override(StageDayPeriodTime, 'stage_id', st.id, [day], st)
    db.session.commit()
    flash(err or _msg('تم الحفظ', 'Saved'), 'error' if err else 'ok')
    return redirect(url_for('admin.stage_view', stage_id=st.id, day=day) + '#daytimes')


# ── Grades ────────────────────────────────────────────────────────────────
@admin_bp.route('/stages/<int:stage_id>/grades/add', methods=['POST'])
@login_required
def grade_add(stage_id):
    st = db.get_or_404(Stage, stage_id)
    names = [n.strip() for n in _form_text('names').replace('،', ',').split(',') if n.strip()]
    for i, n in enumerate(names):
        db.session.add(Grade(stage_id=st.id, name_ar=n, sort_order=len(st.grades) + i))
    db.session.commit()
    return redirect(url_for('admin.stage_view', stage_id=st.id) + '#grades')


@admin_bp.route('/grades/<int:grade_id>')
@login_required
def grade_view(grade_id):
    g = db.get_or_404(Grade, grade_id)
    st = g.stage
    day = request.args.get('day', type=int)
    if day is None and st.weekday_list:
        day = 6 if 6 in st.weekday_list else st.weekday_list[0]
    overrides = {o.period_number: o for o in GradePeriodTime.query.filter_by(
        grade_id=g.id, weekday=day)} if day is not None else {}
    resolved = resolve_grade_day(g, day) if day is not None else []
    return render_template('admin/grade.html', g=g, st=st, day=day, overrides=overrides,
                           program=g.program_for(day) if day is not None else None,
                           resolved=resolved, week_order=WEEK_ORDER,
                           wd_names=weekday_names(session.get('lang', 'ar')))


@admin_bp.route('/grades/<int:grade_id>/update', methods=['POST'])
@login_required
def grade_update(grade_id):
    g = db.get_or_404(Grade, grade_id)
    g.name_ar = _form_text('name_ar') or g.name_ar
    g.name_en = _form_text('name_en') or None
    g.sort_order = request.form.get('sort_order', type=int) or 0
    db.session.commit()
    flash(_msg('تم الحفظ', 'Saved'), 'ok')
    return redirect(url_for('admin.grade_view', grade_id=g.id))


def _delete_grade_children(g):
    for s in g.sections:
        Device.query.filter_by(code_type='section', owner_id=s.id).delete()


@admin_bp.route('/grades/<int:grade_id>/delete', methods=['POST'])
@login_required
def grade_delete(grade_id):
    g = db.get_or_404(Grade, grade_id)
    stage_id = g.stage_id
    _delete_grade_children(g)
    db.session.delete(g)
    db.session.commit()
    return redirect(url_for('admin.stage_view', stage_id=stage_id) + '#grades')


@admin_bp.route('/grades/<int:grade_id>/times', methods=['POST'])
@login_required
def grade_times(grade_id):
    g = db.get_or_404(Grade, grade_id)
    day = request.form.get('weekday', type=int)
    days = g.stage.weekday_list if request.form.get('all_days') else [day]
    err = _save_day_override(GradePeriodTime, 'grade_id', g.id, days, g.stage)
    db.session.commit()
    flash(err or _msg('تم الحفظ', 'Saved'), 'error' if err else 'ok')
    return redirect(url_for('admin.grade_view', grade_id=g.id, day=day) + '#times')


# ── Sections ──────────────────────────────────────────────────────────────
@admin_bp.route('/grades/<int:grade_id>/sections/add', methods=['POST'])
@login_required
def section_add(grade_id):
    g = db.get_or_404(Grade, grade_id)
    names = [n.strip() for n in _form_text('names').replace('،', ',').split(',') if n.strip()]
    for i, n in enumerate(names):
        db.session.add(Section(grade_id=g.id, name_ar=n, sort_order=len(g.sections) + i,
                               code=generate_code()))
        db.session.flush()   # keeps generate_code() unique within the batch
    db.session.commit()
    return redirect(url_for('admin.grade_view', grade_id=g.id) + '#sections')


@admin_bp.route('/sections/<int:section_id>')
@login_required
def section_view(section_id):
    s = db.get_or_404(Section, section_id)
    linked_ids = {l.teacher_id for l in s.teacher_links}
    teachers = Teacher.query.order_by(Teacher.name_ar).all()
    devices = Device.query.filter_by(code_type='section', owner_id=s.id).all()
    all_stages = Stage.query.order_by(Stage.sort_order, Stage.id).all()
    return render_template('admin/section.html', s=s, teachers=teachers, all_stages=all_stages,
                           linked_ids=linked_ids, devices=devices)


@admin_bp.route('/sections/<int:section_id>/update', methods=['POST'])
@login_required
def section_update(section_id):
    s = db.get_or_404(Section, section_id)
    s.name_ar = _form_text('name_ar') or s.name_ar
    s.name_en = _form_text('name_en') or None
    s.sort_order = request.form.get('sort_order', type=int) or 0
    db.session.commit()
    flash(_msg('تم الحفظ', 'Saved'), 'ok')
    return redirect(url_for('admin.section_view', section_id=s.id))


@admin_bp.route('/sections/<int:section_id>/regen', methods=['POST'])
@login_required
def section_regen(section_id):
    s = db.get_or_404(Section, section_id)
    s.code = generate_code()
    Device.query.filter_by(code_type='section', owner_id=s.id).delete()
    db.session.commit()
    flash(_msg('تم توليد كود جديد — الأجهزة القديمة ستحتاج الكود الجديد',
               'New code generated — old devices will need the new code'), 'ok')
    return redirect(request.referrer or url_for('admin.section_view', section_id=s.id))


@admin_bp.route('/sections/<int:section_id>/delete', methods=['POST'])
@login_required
def section_delete(section_id):
    s = db.get_or_404(Section, section_id)
    grade_id = s.grade_id
    Device.query.filter_by(code_type='section', owner_id=s.id).delete()
    db.session.delete(s)
    db.session.commit()
    return redirect(url_for('admin.grade_view', grade_id=grade_id) + '#sections')


@admin_bp.route('/sections/<int:section_id>/teachers/add', methods=['POST'])
@login_required
def section_teacher_add(section_id):
    s = db.get_or_404(Section, section_id)
    have = {l.teacher_id for l in s.teacher_links}
    ids = {int(i) for i in request.form.getlist('teacher_id') if str(i).isdigit()} - have
    for t in Teacher.query.filter(Teacher.id.in_(ids)).all() if ids else []:
        db.session.add(TeacherSection(teacher_id=t.id, section_id=s.id))
    db.session.commit()
    if ids:
        flash(_msg(f'تم ربط {len(ids)} معلم', f'{len(ids)} teacher(s) linked'), 'ok')
    return redirect(url_for('admin.section_view', section_id=s.id))


@admin_bp.route('/links/<int:link_id>/delete', methods=['POST'])
@login_required
def link_delete(link_id):
    l = db.get_or_404(TeacherSection, link_id)
    db.session.delete(l)
    db.session.commit()
    return redirect(request.referrer or url_for('admin.dashboard'))


# ── Teachers ──────────────────────────────────────────────────────────────
@admin_bp.route('/teachers')
@login_required
def teachers():
    rows = Teacher.query.order_by(Teacher.name_ar).all()
    stages = Stage.query.order_by(Stage.sort_order, Stage.id).all()
    new_ids = [int(i) for i in (request.args.get('new') or '').split(',') if i.isdigit()]
    known = [[norm_name(t.name_ar), t.email or '', t.name_ar, t.id] for t in rows]
    return render_template('admin/teachers.html', teachers=rows, stages=stages,
                           new_ids=new_ids, known=known)


XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


@admin_bp.route('/teachers/template/<int:stage_id>')
@login_required
def teachers_template(stage_id):
    from teacher_io import build_template
    st = db.get_or_404(Stage, stage_id)
    return send_file(build_template(st), mimetype=XLSX, as_attachment=True,
                     download_name=f'قالب معلمي {st.name_ar}.xlsx')


@admin_bp.route('/teachers/codes/<int:stage_id>')
@login_required
def teachers_codes_export(stage_id):
    from teacher_io import build_codes_export
    st = db.get_or_404(Stage, stage_id)
    return send_file(build_codes_export(st), mimetype=XLSX, as_attachment=True,
                     download_name=f'أكواد {st.name_ar}.xlsx')


@admin_bp.route('/teachers/import-members', methods=['POST'])
@login_required
def teachers_import_members():
    """Microsoft 365 group member export (CSV) → teachers linked to the whole stage."""
    from teacher_io import import_members_csv
    back = url_for('admin.teachers') + '#members'
    f = request.files.get('file')
    if not f or not f.filename:
        flash(_msg('اختر ملف CSV أولاً', 'Choose the CSV file first'), 'error')
        return redirect(back)
    choice = request.form.get('stage_id', '')
    if choice == 'new':
        name = _form_text('new_stage')
        if not name:
            flash(_msg('اكتب اسم المرحلة الجديدة', 'Enter the new stage name'), 'error')
            return redirect(back)
        st = Stage.query.filter_by(name_ar=name).first()
        if st is None:                  # same grade range as an existing stage → use it
            want = name_range(name)
            st = next((s for s in Stage.query.all() if want and stage_range(s) == want), None)
            if st is not None:
                flash(_msg(f'أُضيفوا إلى المرحلة الموجودة «{st.name_ar}» (نفس الصفوف)',
                           f'Added to the existing stage «{st.name_en or st.name_ar}» (same grades)'), 'ok')
        if st is None:
            st = _create_stage(name)
            db.session.commit()          # the stage stays even if the file turns out empty
            flash(_msg(f'تم إنشاء {name}', f'Created {name}'), 'ok')
    else:
        st = db.session.get(Stage, int(choice)) if choice.isdigit() else None
        if st is None:
            flash(_msg('اختر المرحلة', 'Choose the stage'), 'error')
            return redirect(back)
    try:
        r = import_members_csv(st, f.stream, sync=bool(request.form.get('sync')))
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'error')
        return redirect(back)
    except Exception as e:                      # never a 500 page for a bad file
        db.session.rollback()
        current_app.logger.exception('members import failed')
        flash(_msg(f'تعذّر قراءة الملف ({type(e).__name__}). جرّب تصديره من جديد من Microsoft 365 دون فتحه في Excel.',
                   f'Could not read the file ({type(e).__name__}). Try exporting it again from Microsoft 365 without opening it in Excel.'), 'error')
        return redirect(back)
    parts = [_msg(f'{st.name_ar}: {len(r["teachers"])} معلم في الملف',
                  f'{st.name_en or st.name_ar}: {len(r["teachers"])} teachers in file'),
             _msg(f'جديد {r["created"]}', f'{r["created"]} new'),
             _msg(f'ربط بالمرحلة {r["linked"]}', f'{r["linked"]} linked to stage')]
    if r['unlinked']:
        parts.append(_msg(f'إزالة {r["unlinked"]} لم يعودوا في المجموعة', f'{r["unlinked"]} no longer in group removed'))
    if r['skipped']:
        parts.append(_msg(f'تجاهل {r["skipped"]} (ليس بريد مستخدم)', f'{r["skipped"]} skipped (not a user mailbox)'))
    flash(' · '.join(parts), 'ok')
    new = [t.id for t in r['teachers']][:400]
    return redirect(url_for('admin.teachers', new=','.join(map(str, new))) + '#list')


@admin_bp.route('/teachers/<int:teacher_id>/stages/add', methods=['POST'])
@login_required
def teacher_stage_add(teacher_id):
    t = db.get_or_404(Teacher, teacher_id)
    have = {l.stage_id for l in t.stage_links}
    ids = {int(i) for i in request.form.getlist('stage_id') if str(i).isdigit()} - have
    for st in Stage.query.filter(Stage.id.in_(ids)).all() if ids else []:
        db.session.add(TeacherStage(teacher_id=t.id, stage_id=st.id))
    db.session.commit()
    return redirect(url_for('admin.teacher_view', teacher_id=t.id))


@admin_bp.route('/stage-links/<int:link_id>/delete', methods=['POST'])
@login_required
def stage_link_delete(link_id):
    l = db.get_or_404(TeacherStage, link_id)
    db.session.delete(l)
    db.session.commit()
    return redirect(request.referrer or url_for('admin.teachers'))


@admin_bp.route('/teachers/import', methods=['POST'])
@login_required
def teachers_import():
    from teacher_io import import_template
    st = db.get_or_404(Stage, request.form.get('stage_id', type=int))
    f = request.files.get('file')
    if not f or not f.filename:
        flash(_msg('اختر ملف القالب أولاً', 'Choose the template file first'), 'error')
        return redirect(url_for('admin.teachers') + '#import')
    try:
        r = import_template(st, f.stream, sync=bool(request.form.get('sync')))
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'error')
        return redirect(url_for('admin.teachers') + '#import')
    except Exception as e:                      # never a 500 page for a bad file
        db.session.rollback()
        current_app.logger.exception('import failed')
        flash(_msg(f'تعذّر قراءة الملف ({type(e).__name__}).', f'Could not read the file ({type(e).__name__}).'), 'error')
        return redirect(url_for('admin.teachers') + '#import')
    parts = [_msg(f'{st.name_ar}: {len(r["teachers"])} معلم في الملف',
                  f'{st.name_en or st.name_ar}: {len(r["teachers"])} teachers in file'),
             _msg(f'جديد {r["created"]}', f'{r["created"]} new'),
             _msg(f'ربط {r["linked"]}', f'{r["linked"]} links added')]
    if r['unlinked']:
        parts.append(_msg(f'إزالة ربط {r["unlinked"]}', f'{r["unlinked"]} links removed'))
    if r['skipped']:
        parts.append(_msg(f'تجاهل {r["skipped"]} صف (بلا اسم أو مكرر)', f'{r["skipped"]} rows skipped (no name / duplicate)'))
    flash(' · '.join(parts), 'ok')
    if r['unknown_columns']:
        flash(_msg('أعمدة لم تُعرف كشعب في هذه المرحلة وتم تجاهلها: ', 'Columns not matching a section, ignored: ')
              + '، '.join(r['unknown_columns']), 'error')
    return redirect(url_for('admin.teachers') + '#import')


@admin_bp.route('/teachers/add', methods=['POST'])
@login_required
def teacher_add():
    """One teacher per line: a name, optionally with an email. Existing people
    (same email, or same name) are not duplicated. New ones go to the top of the list."""
    stage = db.session.get(Stage, request.form.get('stage_id', type=int) or 0)
    by_email = {t.email: t for t in Teacher.query.filter(Teacher.email.isnot(None))}
    by_name = {norm_name(t.name_ar): t for t in Teacher.query.all()}
    created, existing, linked = [], [], set()
    for line in (request.form.get('names') or '').splitlines():
        line = re.sub('[\u2066-\u2069]', '', line).strip()
        if not line:
            continue
        m = EMAIL_IN_LINE.search(line)
        email = m.group(0).lower() if m else None
        name = re.sub(r'[,;<>\t]+', ' ', line.replace(m.group(0), '') if m else line).strip()
        if not name and email:
            from teacher_io import display_name_from
            name = display_name_from('', m.group(0))
        t = (by_email.get(email) if email else None) or by_name.get(norm_name(name))
        if t is None:
            t = Teacher(name_ar=name, email=email, code=generate_code())
            db.session.add(t)
            db.session.flush()
            by_name[norm_name(name)] = t
            if email:
                by_email[email] = t
            created.append(t)
        else:
            if email and not t.email:
                t.email = email
            existing.append(t)
        if stage and t.id not in linked and not any(l.stage_id == stage.id for l in t.stage_links):
            db.session.add(TeacherStage(teacher_id=t.id, stage_id=stage.id))
        linked.add(t.id)
    db.session.commit()
    existing = list({t.id: t for t in existing if t not in created}.values())
    msg = _msg(f'تمت إضافة {len(created)} معلم — ظاهرون أعلى القائمة', f'{len(created)} teacher(s) added — shown at the top')
    if existing:
        msg += _msg(f' · {len(existing)} موجود مسبقاً ولم يُكرَّر', f' · {len(existing)} already existed, not duplicated')
    if stage:
        msg += _msg(f' · رُبطوا بـ {stage.name_ar}', f' · linked to {stage.name_en or stage.name_ar}')
    flash(msg, 'ok')
    ids = ','.join(dict.fromkeys(str(t.id) for t in created + existing))
    return redirect(url_for('admin.teachers', new=ids) + '#list')


@admin_bp.route('/teachers/<int:teacher_id>')
@login_required
def teacher_view(teacher_id):
    t = db.get_or_404(Teacher, teacher_id)
    linked = {l.section_id: l for l in t.section_links}
    stages = Stage.query.order_by(Stage.sort_order, Stage.id).all()
    devices = Device.query.filter_by(code_type='teacher', owner_id=t.id).all()
    stage_linked = {l.stage_id: l for l in t.stage_links}
    return render_template('admin/teacher.html', t=t, linked=linked, stages=stages,
                           devices=devices, stage_linked=stage_linked)


@admin_bp.route('/teachers/<int:teacher_id>/update', methods=['POST'])
@login_required
def teacher_update(teacher_id):
    t = db.get_or_404(Teacher, teacher_id)
    t.name_ar = _form_text('name_ar') or t.name_ar
    t.name_en = _form_text('name_en') or None
    t.active = bool(request.form.get('active'))
    email = _form_text('email').lower() or None
    if email and Teacher.query.filter(Teacher.email == email, Teacher.id != t.id).first():
        flash(_msg('هذا البريد مستخدم لمعلم آخر', 'This email belongs to another teacher'), 'error')
        return redirect(url_for('admin.teacher_view', teacher_id=t.id))
    t.email = email
    db.session.commit()
    flash(_msg('تم الحفظ', 'Saved'), 'ok')
    return redirect(url_for('admin.teacher_view', teacher_id=t.id))


@admin_bp.route('/teachers/<int:teacher_id>/sections/add', methods=['POST'])
@login_required
def teacher_section_add(teacher_id):
    t = db.get_or_404(Teacher, teacher_id)
    have = {l.section_id for l in t.section_links}
    ids = {int(i) for i in request.form.getlist('section_id') if str(i).isdigit()} - have
    for sec in Section.query.filter(Section.id.in_(ids)).all() if ids else []:
        db.session.add(TeacherSection(teacher_id=t.id, section_id=sec.id))
    db.session.commit()
    if ids:
        flash(_msg(f'تم ربط {len(ids)} شعبة', f'{len(ids)} section(s) linked'), 'ok')
    return redirect(url_for('admin.teacher_view', teacher_id=t.id))


@admin_bp.route('/teachers/<int:teacher_id>/regen', methods=['POST'])
@login_required
def teacher_regen(teacher_id):
    t = db.get_or_404(Teacher, teacher_id)
    t.code = generate_code()
    Device.query.filter_by(code_type='teacher', owner_id=t.id).delete()
    db.session.commit()
    flash(_msg('تم توليد كود جديد', 'New code generated'), 'ok')
    return redirect(request.referrer or url_for('admin.teacher_view', teacher_id=t.id))


@admin_bp.route('/teachers/<int:teacher_id>/delete', methods=['POST'])
@login_required
def teacher_delete(teacher_id):
    t = db.get_or_404(Teacher, teacher_id)
    Device.query.filter_by(code_type='teacher', owner_id=t.id).delete()
    db.session.delete(t)
    db.session.commit()
    return redirect(url_for('admin.teachers'))


# ── Bell programs ─────────────────────────────────────────────────────────
@admin_bp.route('/programs')
@login_required
def programs():
    progs = BellProgram.query.order_by(BellProgram.sort_order, BellProgram.id).all()
    usage = {p.id: sorted({(a.grade.stage.sort_order, a.grade.sort_order, a.grade.name_ar)
                           for a in p.assignments}) for p in progs}
    import seed_programs as sp
    stages = Stage.query.order_by(Stage.sort_order, Stage.id).all()
    seed = [(g, label, default, sp.guess_stage(g, stages)) for g, (label, default, _) in sp.GROUPS.items()]
    return render_template('admin/programs.html', progs=progs, usage=usage, stages=stages,
                           seed=seed, seed_title=sp.TITLE)


@admin_bp.route('/programs/add', methods=['POST'])
@login_required
def program_add():
    name = _form_text('name')
    if not name or BellProgram.query.filter_by(name=name).first():
        flash(_msg('اكتب اسماً غير مستخدم للبرنامج', 'Enter an unused program name'), 'error')
        return redirect(url_for('admin.programs'))
    prog = BellProgram(name=name, sort_order=BellProgram.query.count())
    src = db.session.get(BellProgram, request.form.get('copy_from', type=int) or 0)
    for p in (src.periods if src else []):
        prog.periods.append(BellProgramPeriod(number=p.number, kind=p.kind, label_ar=p.label_ar,
                                              label_en=p.label_en, start_time=p.start_time,
                                              end_time=p.end_time))
    db.session.add(prog)
    db.session.commit()
    return redirect(url_for('admin.program_view', program_id=prog.id))


@admin_bp.route('/programs/<int:program_id>')
@login_required
def program_view(program_id):
    prog = db.get_or_404(BellProgram, program_id)
    return render_template('admin/program.html', prog=prog)


def _rechain_program(prog, day_start, durations):
    times = chain_times(day_start, durations)
    if times is None:
        return False
    for p, (a, b) in zip(prog.periods, times):
        p.start_time, p.end_time = a, b
    return True


@admin_bp.route('/programs/<int:program_id>/save', methods=['POST'])
@login_required
def program_save(program_id):
    prog = db.get_or_404(BellProgram, program_id)
    name = _form_text('name') or prog.name
    if name != prog.name and BellProgram.query.filter_by(name=name).first():
        flash(_msg('يوجد برنامج بهذا الاسم', 'A program with this name exists'), 'error')
        return redirect(url_for('admin.program_view', program_id=prog.id))
    day_start = _form_text('day_start') or prog.day_start
    if not valid_time(day_start):
        flash(_msg('وقت البداية غير صالح', 'Invalid start time'), 'error')
        return redirect(url_for('admin.program_view', program_id=prog.id))
    durs = []
    for p in prog.periods:
        pre = f'p{p.id}_'
        durs.append(parse_duration(request.form.get(pre + 'duration')) or p.duration)
        p.label_ar = _form_text(pre + 'label_ar') or p.label_ar
        p.label_en = _form_text(pre + 'label_en') or None
        p.kind = 'break' if request.form.get(pre + 'kind') == 'break' else 'class'
    if not _rechain_program(prog, day_start, durs):
        db.session.rollback()
        flash(_msg('مجموع المدد يتجاوز منتصف الليل', 'Total duration runs past midnight'), 'error')
        return redirect(url_for('admin.program_view', program_id=prog.id))
    prog.name = name
    db.session.commit()
    flash(_msg('تم حفظ البرنامج', 'Program saved'), 'ok')
    return redirect(url_for('admin.program_view', program_id=prog.id))


@admin_bp.route('/programs/<int:program_id>/periods/add', methods=['POST'])
@login_required
def program_period_add(program_id):
    prog = db.get_or_404(BellProgram, program_id)
    kind = 'break' if request.form.get('kind') == 'break' else 'class'
    dur = parse_duration(request.form.get('duration'))
    start = prog.periods[-1].end_time if prog.periods else (_form_text('day_start') or '08:00')
    times = chain_times(start, [dur]) if dur and valid_time(start) else None
    if not times:
        flash(_msg('أدخل مدة صحيحة بالدقائق', 'Enter a valid duration in minutes'), 'error')
        return redirect(url_for('admin.program_view', program_id=prog.id))
    class_index = sum(1 for p in prog.periods if p.kind == 'class') + 1
    d_ar, d_en = default_period_labels(kind, class_index)
    if kind == 'break':
        d_ar = 'الفرصة'
    prog.periods.append(BellProgramPeriod(number=len(prog.periods) + 1, kind=kind,
                                          label_ar=_form_text('label_ar') or d_ar,
                                          label_en=_form_text('label_en') or d_en,
                                          start_time=times[0][0], end_time=times[0][1]))
    db.session.commit()
    return redirect(url_for('admin.program_view', program_id=prog.id) + '#periods')


@admin_bp.route('/program-periods/<int:period_id>/delete', methods=['POST'])
@login_required
def program_period_delete(period_id):
    p = db.get_or_404(BellProgramPeriod, period_id)
    prog = p.program
    start = prog.day_start
    db.session.delete(p)
    db.session.flush()
    db.session.expire(prog, ['periods'])
    for n, q in enumerate(prog.periods, start=1):
        q.number = n
    _rechain_program(prog, start, [q.duration for q in prog.periods])
    db.session.commit()
    return redirect(url_for('admin.program_view', program_id=prog.id) + '#periods')


@admin_bp.route('/programs/<int:program_id>/delete', methods=['POST'])
@login_required
def program_delete(program_id):
    prog = db.get_or_404(BellProgram, program_id)
    db.session.delete(prog)
    db.session.commit()
    flash(_msg('تم حذف البرنامج؛ الصفوف التي كانت تتبعه عادت لتوقيت مرحلتها',
               'Program deleted; its grades fell back to their stage timing'), 'ok')
    return redirect(url_for('admin.programs'))


@admin_bp.route('/programs/assign', methods=['GET', 'POST'])
@login_required
def programs_assign():
    stages = Stage.query.order_by(Stage.sort_order, Stage.id).all()
    progs = BellProgram.query.order_by(BellProgram.sort_order, BellProgram.id).all()
    if request.method == 'POST':
        valid = {p.id for p in progs}
        for st in stages:
            for g in st.grades:
                current = {a.weekday: a for a in g.day_programs}
                for wd in st.weekday_list:
                    pid = request.form.get(f'g{g.id}_d{wd}', type=int)
                    if pid in valid:
                        if wd in current:
                            current[wd].program_id = pid
                        else:
                            db.session.add(GradeDayProgram(grade_id=g.id, weekday=wd, program_id=pid))
                    elif wd in current:
                        db.session.delete(current[wd])
        db.session.commit()
        flash(_msg('تم حفظ التوزيع', 'Assignments saved'), 'ok')
        return redirect(url_for('admin.programs_assign'))
    return render_template('admin/programs_assign.html', stages=stages, progs=progs,
                           dupes=duplicate_stages(stages),
                           week_order=WEEK_ORDER, wd_names=weekday_names(session.get('lang', 'ar')))


@admin_bp.route('/programs/seed', methods=['POST'])
@login_required
def programs_seed():
    """Load the built-in 2026/2027 programs (from the school's Word document).
    Each grade group goes to the stage chosen in the form (pre-matched by name)."""
    import seed_programs as sp
    from programs_io import import_programs
    names = {}
    all_stages = Stage.query.order_by(Stage.sort_order, Stage.id).all()
    for group, (_, default_name, _) in sp.GROUPS.items():
        choice = request.form.get(group, '')
        st = db.session.get(Stage, int(choice)) if choice.isdigit() else None
        if st is None:                  # never create a twin of an existing stage
            st = sp.guess_stage(group, all_stages)
        names[group] = st.name_ar if st else (_form_text(group + '_new') or default_name)
    try:
        r = import_programs(sp.build_workbook(names))
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'error')
        return redirect(url_for('admin.programs') + '#import-tab')
    flash(_msg(f'تم تحميل {sp.TITLE}: {r["programs"]} برامج، و{r["assigned"]} تعيين صف/يوم'
               + (f'، صفوف جديدة {r["grades"]}' if r['grades'] else '')
               + (f'، مراحل جديدة {r["stages"]}' if r['stages'] else ''),
               f'Loaded {r["programs"]} programs and {r["assigned"]} grade/day assignments'), 'ok')
    return redirect(url_for('admin.programs'))


@admin_bp.route('/programs/export')
@login_required
def programs_export():
    from programs_io import export_programs
    return send_file(export_programs(), mimetype=XLSX, as_attachment=True,
                     download_name='برامج الجرس.xlsx')


@admin_bp.route('/programs/import', methods=['POST'])
@login_required
def programs_import():
    from programs_io import import_programs
    f = request.files.get('file')
    if not f or not f.filename:
        flash(_msg('اختر الملف أولاً', 'Choose the file first'), 'error')
        return redirect(url_for('admin.programs'))
    try:
        r = import_programs(f.stream)
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'error')
        return redirect(url_for('admin.programs'))
    except Exception as e:                      # never a 500 page for a bad file
        db.session.rollback()
        current_app.logger.exception('import failed')
        flash(_msg(f'تعذّر قراءة الملف ({type(e).__name__}).', f'Could not read the file ({type(e).__name__}).'), 'error')
        return redirect(url_for('admin.programs'))
    flash(_msg(f'تم: {r["programs"]} برنامج · {r["assigned"]} تعيين يوم/صف'
               + (f' · مراحل جديدة {r["stages"]}' if r['stages'] else '')
               + (f' · صفوف جديدة {r["grades"]}' if r['grades'] else ''),
               f'Done: {r["programs"]} programs · {r["assigned"]} grade/day assignments'
               + (f' · {r["stages"]} new stages' if r['stages'] else '')
               + (f' · {r["grades"]} new grades' if r['grades'] else '')), 'ok')
    for w in r['warnings'][:5]:
        flash(w, 'error')
    return redirect(url_for('admin.programs'))


# ── Bulk actions (checkbox selection on every list) ───────────────────────
def _delete_stage(st):
    for m in (st.logo, st.background, st.tone):
        if m:
            db.session.delete(m)
    for g in st.grades:
        _delete_grade_children(g)
    db.session.delete(st)


def _delete_grade(g):
    _delete_grade_children(g)
    db.session.delete(g)


def _delete_section(sec):
    Device.query.filter_by(code_type='section', owner_id=sec.id).delete()
    db.session.delete(sec)


def _delete_teacher(t):
    Device.query.filter_by(code_type='teacher', owner_id=t.id).delete()
    db.session.delete(t)


BULK = {
    # entity: (model, {action: handler(obj)})
    'teachers': (Teacher, {
        'delete': _delete_teacher,
        'deactivate': lambda t: setattr(t, 'active', False),
        'activate': lambda t: setattr(t, 'active', True),
        'regen': lambda t: (setattr(t, 'code', generate_code()),
                            Device.query.filter_by(code_type='teacher', owner_id=t.id).delete(),
                            db.session.flush()),
    }),
    'sections': (Section, {
        'delete': _delete_section,
        'regen': lambda sec: (setattr(sec, 'code', generate_code()),
                              Device.query.filter_by(code_type='section', owner_id=sec.id).delete(),
                              db.session.flush()),
    }),
    'grades': (Grade, {'delete': _delete_grade}),
    'stages': (Stage, {'delete': _delete_stage}),
    'programs': (BellProgram, {'delete': db.session.delete}),
    'devices': (Device, {'delete': db.session.delete}),
    'section_links': (TeacherSection, {'unlink': db.session.delete}),
    'stage_links': (TeacherStage, {'unlink': db.session.delete}),
}
BULK_DONE = {
    'delete': ('تم حذف {n}', '{n} deleted'), 'unlink': ('تمت إزالة {n}', '{n} removed'), 'deactivate': ('تم إيقاف {n}', '{n} disabled'),
    'activate': ('تم تفعيل {n}', '{n} enabled'), 'regen': ('تم توليد أكواد جديدة لـ {n}', 'New codes for {n}'),
    'link_stage': ('تم ربط {n} بالمرحلة', '{n} linked to the stage'),
    'unlink_stage': ('تمت إزالة {n} من المرحلة', '{n} removed from the stage'),
}


@admin_bp.route('/bulk/<entity>', methods=['POST'])
@login_required
def bulk(entity):
    back = request.referrer or url_for('admin.dashboard')
    if entity not in BULK:
        return redirect(back)
    model, actions = BULK[entity]
    action = request.form.get('action', '')
    ids = {int(i) for i in request.form.getlist('ids') if str(i).isdigit()}
    objs = model.query.filter(model.id.in_(ids)).all() if ids else []
    if not objs:
        flash(_msg('لم يُحدَّد شيء', 'Nothing selected'), 'error')
        return redirect(back)
    if entity == 'teachers' and action in ('link_stage', 'unlink_stage'):
        st = db.session.get(Stage, request.form.get('stage_id', type=int) or 0)
        if st is None:
            flash(_msg('اختر المرحلة', 'Choose the stage'), 'error')
            return redirect(back)
        for t in objs:
            link = next((l for l in t.stage_links if l.stage_id == st.id), None)
            if action == 'link_stage' and link is None:
                db.session.add(TeacherStage(teacher_id=t.id, stage_id=st.id))
            elif action == 'unlink_stage' and link is not None:
                db.session.delete(link)
    elif action in actions:
        for o in objs:
            actions[action](o)
    else:
        return redirect(back)
    db.session.commit()
    ar, en = BULK_DONE.get(action, ('تم ({n})', 'Done ({n})'))
    flash(_msg(ar.format(n=len(objs)), en.format(n=len(objs))), 'ok')
    # A deleted object's own page no longer exists.
    if action == 'delete' and entity in ('stages', 'grades') and '/admin/' in back:
        if entity == 'stages' and '/stages/' in back:
            back = url_for('admin.dashboard')
    return redirect(back)


# ── Codes overview ────────────────────────────────────────────────────────
@admin_bp.route('/codes')
@login_required
def codes():
    from teacher_io import _stage_teachers
    stages = Stage.query.order_by(Stage.sort_order, Stage.id).all()
    by_stage = {st.id: _stage_teachers(st) for st in stages}
    linked = {t.id for ts in by_stage.values() for t in ts}
    unlinked = [t for t in Teacher.query.order_by(Teacher.name_ar) if t.id not in linked]
    return render_template('admin/codes.html', stages=stages, by_stage=by_stage, unlinked=unlinked)


@admin_bp.route('/codes/export')
@login_required
def codes_export_all():
    from teacher_io import _stage_teachers, build_codes_export_all
    stages = Stage.query.order_by(Stage.sort_order, Stage.id).all()
    linked = {t.id for st in stages for t in _stage_teachers(st)}
    unlinked = [t for t in Teacher.query.order_by(Teacher.name_ar) if t.id not in linked]
    return send_file(build_codes_export_all(stages, unlinked), mimetype=XLSX, as_attachment=True,
                     download_name='أكواد جرس الحصص.xlsx')


# ── Settings: tone + devices ──────────────────────────────────────────────
@admin_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    tone_id = AppSetting.get('tone_id')
    tone = db.session.get(MediaFile, int(tone_id)) if tone_id else None
    if request.method == 'POST':
        if request.form.get('reset_tone'):
            if tone:
                db.session.delete(tone)
            AppSetting.set('tone_id', '')
            flash(_msg('تمت العودة للنغمة الافتراضية', 'Default tone restored'), 'ok')
        else:
            m, changed = _save_upload('tone', 'tone', 'audio/', tone)
            if changed:
                AppSetting.set('tone_id', m.id)
                flash(_msg('تم رفع النغمة', 'Tone uploaded'), 'ok')
        db.session.commit()
        return redirect(url_for('admin.settings'))
    devices = Device.query.order_by(Device.last_sync.desc()).all()
    owners = {}
    for d in devices:
        if d.code_type == 'section':
            s = db.session.get(Section, d.owner_id)
            owners[d.id] = s.full_name(session.get('lang', 'ar')) if s else '—'
        else:
            t = db.session.get(Teacher, d.owner_id)
            owners[d.id] = t.name_ar if t else '—'
    stages = Stage.query.order_by(Stage.sort_order, Stage.id).all()
    return render_template('admin/settings.html', tone=tone, devices=devices, owners=owners, stages=stages)


@admin_bp.route('/devices/<int:device_id>/delete', methods=['POST'])
@login_required
def device_delete(device_id):
    d = db.get_or_404(Device, device_id)
    db.session.delete(d)
    db.session.commit()
    return redirect(url_for('admin.settings'))
