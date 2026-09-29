"""Business logic: code generation, time resolution and the schedule payload
that devices download and cache for offline use."""
import re
import secrets

from models import (db, AppSetting, GradePeriodTime, Section, Stage,
                    StageDayPeriodTime, Teacher)

# No 0/O/1/I/L so codes are easy to read off a screen and type on a tablet.
CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
CODE_LEN = 6
TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')

AR_ORDINALS = ['الأولى', 'الثانية', 'الثالثة', 'الرابعة', 'الخامسة', 'السادسة',
               'السابعة', 'الثامنة', 'التاسعة', 'العاشرة', 'الحادية عشرة', 'الثانية عشرة']


def normalize_code(code):
    # Also drop invisible direction marks a copy/paste may carry along.
    return re.sub(r'[\s\-\u200e\u200f\u2066-\u2069\u202a-\u202e]', '', (code or '')).upper()


def generate_code():
    """A code unique across BOTH sections and teachers, so login needs only the code."""
    while True:
        code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
        if not Section.query.filter_by(code=code).first() and \
           not Teacher.query.filter_by(code=code).first():
            return code


def find_by_code(code):
    """Return ('section', Section) / ('teacher', Teacher) / (None, None)."""
    code = normalize_code(code)
    if not code:
        return None, None
    s = Section.query.filter_by(code=code).first()
    if s:
        return 'section', s
    t = Teacher.query.filter_by(code=code, active=True).first()
    if t:
        return 'teacher', t
    return None, None


def valid_time(v):
    return bool(v and TIME_RE.match(v))


def to_minutes(hhmm):
    h, m = hhmm.split(':')
    return int(h) * 60 + int(m)


def from_minutes(total):
    return f'{total // 60:02d}:{total % 60:02d}'


def minutes_between(start, end):
    return to_minutes(end) - to_minutes(start)


def chain_times(day_start, durations):
    """Back-to-back periods: [(start, end), ...] from a start time and durations.
    Returns None if the day would run past midnight."""
    t = to_minutes(day_start)
    out = []
    for d in durations:
        if t + d >= 24 * 60:
            return None
        out.append((from_minutes(t), from_minutes(t + d)))
        t += d
    return out


def parse_duration(v):
    try:
        d = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return d if 1 <= d <= 240 else None


def rechain_stage(stage):
    """Recompute default period times, then every override set (stage-day and
    grade-day), so adding/removing a period keeps every schedule gap-free.
    Each override set keeps its own start time and durations; a period it
    doesn't cover yet takes the stage default duration."""
    periods = list(stage.periods)
    chained = chain_times(stage.day_start, [p.duration for p in periods])
    if chained:
        for p, (s, e) in zip(periods, chained):
            p.start_time, p.end_time = s, e
    numbers = [p.number for p in periods]
    defaults = {p.number: p.duration for p in periods}

    def _rechain(model, owner_field, owner_id, weekday):
        rows = model.query.filter_by(**{owner_field: owner_id}, weekday=weekday).all()
        if not rows:
            return
        by_n = {r.period_number: r for r in rows}
        start = min(r.start_time for r in rows)
        durs = [minutes_between(by_n[n].start_time, by_n[n].end_time) if n in by_n else defaults[n]
                for n in numbers]
        times = chain_times(start, durs)
        for r in rows:
            if r.period_number not in numbers:
                db.session.delete(r)
        if not times:
            return
        for n, (s, e) in zip(numbers, times):
            r = by_n.get(n)
            if r:
                r.start_time, r.end_time = s, e
            else:
                db.session.add(model(**{owner_field: owner_id}, weekday=weekday,
                                     period_number=n, start_time=s, end_time=e))

    for wd in {r.weekday for r in stage.day_times}:
        _rechain(StageDayPeriodTime, 'stage_id', stage.id, wd)
    for g in stage.grades:
        for wd in {r.weekday for r in g.period_times}:
            _rechain(GradePeriodTime, 'grade_id', g.id, wd)


def default_period_labels(kind, class_index):
    """Suggested labels when the admin adds a period (they stay editable)."""
    if kind == 'break':
        return 'الاستراحة', 'Break'
    ar = AR_ORDINALS[class_index - 1] if class_index <= len(AR_ORDINALS) else str(class_index)
    return f'الحصة {ar}', f'Period {class_index}'


def bump_schedule_version():
    """Any admin change that affects what devices display bumps this, so
    devices re-download only when something actually changed."""
    v = int(AppSetting.get('schedule_version', '0') or 0) + 1
    AppSetting.set('schedule_version', v)
    return v


def schedule_version():
    return int(AppSetting.get('schedule_version', '0') or 0)


def resolve_program(program):
    out = [{'n': p.number, 'kind': p.kind, 'ar': p.label_ar, 'en': p.label_en or p.label_ar,
            'start': p.start_time, 'end': p.end_time, 'source': 'program',
            'dur': minutes_between(p.start_time, p.end_time)} for p in program.periods]
    out.sort(key=lambda x: x['n'])
    return out


def resolve_grade_day(grade, weekday):
    """The periods of one grade on one weekday, with the effective times.
    Priority: bell program assigned to the grade for that day → grade time
    override → stage day override → stage default."""
    program = grade.program_for(weekday)
    if program is not None:
        return resolve_program(program)
    stage = grade.stage
    grade_ovr = {g.period_number: g for g in GradePeriodTime.query.filter_by(
        grade_id=grade.id, weekday=weekday)}
    stage_ovr = {s.period_number: s for s in StageDayPeriodTime.query.filter_by(
        stage_id=stage.id, weekday=weekday)}
    out = []
    for p in stage.periods:
        src, o = 'default', None
        if p.number in grade_ovr:
            src, o = 'grade', grade_ovr[p.number]
        elif p.number in stage_ovr:
            src, o = 'stage', stage_ovr[p.number]
        out.append({
            'n': p.number, 'kind': p.kind,
            'ar': p.label_ar, 'en': p.label_en or p.label_ar,
            'start': o.start_time if o else p.start_time,
            'end': o.end_time if o else p.end_time,
            'source': src,
        })
    for r in out:
        r['dur'] = minutes_between(r['start'], r['end'])
    out.sort(key=lambda x: x['n'])
    return out


def resolve_stage_day(stage, weekday):
    """Stage timing for a day with no grade overrides (stage with no grades yet)."""
    stage_ovr = {s.period_number: s for s in StageDayPeriodTime.query.filter_by(
        stage_id=stage.id, weekday=weekday)}
    out = []
    for p in stage.periods:
        o = stage_ovr.get(p.number)
        start, end = (o.start_time, o.end_time) if o else (p.start_time, p.end_time)
        out.append({'n': p.number, 'kind': p.kind, 'ar': p.label_ar, 'en': p.label_en or p.label_ar,
                    'start': start, 'end': end, 'source': 'stage' if o else 'default',
                    'dur': minutes_between(start, end)})
    return out


def build_schedule_payload(code_type, owner):
    """Everything a device needs to run fully offline."""
    stages, days, targets, seen = {}, {}, [], set()

    def add_stage(stage):
        if stage.id not in stages:
            stages[stage.id] = {
                'id': stage.id, 'ar': stage.name_ar, 'en': stage.name_en or stage.name_ar,
                'logo': stage.logo.url if stage.logo else None,
                'background': stage.background.url if stage.background else None,
                'tone': stage.tone.url if stage.tone else None,     # None = school default
            }

    def add_grade(grade):
        if str(grade.id) not in days:
            days[str(grade.id)] = {str(wd): resolve_grade_day(grade, wd) for wd in grade.stage.weekday_list}

    sections = [owner] if code_type == 'section' else owner.sections
    for sec in sections:
        grade, stage = sec.grade, sec.grade.stage
        if not stage.active:
            continue
        add_stage(stage)
        add_grade(grade)
        seen.add(('g', grade.id))
        targets.append({'sectionId': sec.id, 'stageId': stage.id, 'gradeId': grade.id,
                        'ar': sec.full_name('ar'), 'en': sec.full_name('en')})

    # Whole-stage links: one target per grade (grades may run different times).
    for stage in (owner.stages if code_type == 'teacher' else []):
        if not stage.active:
            continue
        add_stage(stage)
        name = {'ar': stage.name_ar, 'en': stage.name_en or stage.name_ar}
        if stage.grades:
            for grade in stage.grades:
                if ('g', grade.id) in seen:
                    continue
                seen.add(('g', grade.id))
                add_grade(grade)
                # Grade name, so a merged alert says which grades it is for.
                targets.append({'sectionId': None, 'stageId': stage.id, 'gradeId': grade.id,
                                'ar': grade.name_ar, 'en': grade.name_en or grade.name_ar})
        else:
            key = f'stage-{stage.id}'
            days[key] = {str(wd): resolve_stage_day(stage, wd) for wd in stage.weekday_list}
            targets.append({'sectionId': None, 'stageId': stage.id, 'gradeId': key, **name})

    tone_id = AppSetting.get('tone_id')
    tone_url = None
    if tone_id:
        from models import MediaFile
        m = db.session.get(MediaFile, int(tone_id))
        tone_url = m.url if m else None
    if code_type == 'section':
        who = {'type': 'section', 'ar': owner.full_name('ar'), 'en': owner.full_name('en')}
    else:
        who = {'type': 'teacher', 'ar': owner.name_ar, 'en': owner.name_en or owner.name_ar}
    return {
        'version': schedule_version(),
        'who': who,
        'stages': stages,
        'targets': targets,
        'days': days,
        'tone': tone_url or '/static/sounds/chime.wav',
        'toneCustom': bool(tone_url),      # False = the built-in chime (bundled in the apps)
    }


def weekday_names(lang):
    if lang == 'en':
        return ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    return ['الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد']


# Admin forms list Sunday first (the school week in Jordan).
WEEK_ORDER = [6, 0, 1, 2, 3, 4, 5]


# ── Duplicate stages (e.g. «Primary 4-6» from the staff CSV and
#    «المرحلة الأساسية 4-6» from the bell programs) ────────────────────────
_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')
_RANGE_RE = re.compile(r'(\d{1,2})\s*[-–—‐‑−+/]\s*(\d{1,2})')


def name_range(text):
    """(first, last) grade numbers in a stage name, e.g. 'Primary 4-6' → (4, 6)."""
    m = _RANGE_RE.search((text or '').translate(_DIGITS))
    return (int(m.group(1)), int(m.group(2))) if m else None


def stage_range(st):
    return name_range(f'{st.name_ar} {st.name_en or ""}')


def duplicate_stages(stages):
    """Pairs (src, dst) of stages that look like the same stage: same grade
    range in the name. dst is the one to keep (more teachers, then older)."""
    by_range = {}
    for st in stages:
        r = stage_range(st)
        if r:
            by_range.setdefault(r, []).append(st)
    pairs = []
    for group in by_range.values():
        if len(group) < 2:
            continue
        group = sorted(group, key=lambda s: (-len(s.teacher_links), s.id))
        pairs += [(src, group[0]) for src in group[1:]]
    return pairs


def merge_stages(src, dst):
    """Move everything from src into dst, then delete src. Grades with the
    same name are combined (sections, programs and times move over)."""
    if src.id == dst.id:
        return
    for g in list(src.grades):
        twin = next((x for x in dst.grades if x.name_ar.strip() == g.name_ar.strip()), None)
        if twin is None:
            g.sort_order = len(dst.grades)
            g.stage = dst
            continue
        for sec in list(g.sections):
            sec.sort_order = len(twin.sections)
            sec.grade = twin
        have = {a.weekday for a in twin.day_programs}
        for a in list(g.day_programs):
            if a.weekday not in have:
                a.grade = twin
        have = {(t.weekday, t.period_number) for t in twin.period_times}
        for t in list(g.period_times):
            if (t.weekday, t.period_number) not in have:
                t.grade = twin
        twin.name_en = twin.name_en or g.name_en
        db.session.flush()
        db.session.delete(g)
    linked = {l.teacher_id for l in dst.teacher_links}
    for link in list(src.teacher_links):
        if link.teacher_id in linked:
            db.session.delete(link)
        else:
            link.stage = dst
            linked.add(link.teacher_id)
    if not dst.logo_id and src.logo_id:
        dst.logo_id, src.logo_id = src.logo_id, None
    if not dst.background_id and src.background_id:
        dst.background_id, src.background_id = src.background_id, None
    if not dst.tone_id and src.tone_id:
        dst.tone_id, src.tone_id = src.tone_id, None
    if not dst.name_en and src.name_en:
        dst.name_en = src.name_en
    db.session.flush()
    db.session.delete(src)
