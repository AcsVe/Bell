"""Teacher import/export by stage via an Excel template.

Template layout (sheet "المعلمون", right-to-left):
    row 1 (hidden): machine keys — "stage:<id>", "", "", then "sec:<id>" per section column
    row 2:          headers — الاسم بالعربية | الاسم بالإنجليزية | كود المعلم | <one column per section>
    row 3+:         one teacher per row; any mark (✓, x, 1, نعم) under a section links them to it
The template comes pre-filled with the teachers already linked to that stage,
so the admin can edit it and upload it again.
"""
import io
import re

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

import csv

from models import db, Section, Stage, Teacher, TeacherSection, TeacherStage
from services import generate_code

SHEET = 'المعلمون'
MARK = '✓'
FALSY = {'', '0', 'no', 'لا', '-', '×', 'false'}
HEAD_FIXED = ['الاسم بالعربية', 'الاسم بالإنجليزية', 'البريد الإلكتروني', 'كود المعلم (للاطلاع)', 'كل المرحلة']
KEYS_FIXED = ['name_en', 'email', 'code', 'all']        # keys for columns B..E (A holds stage:<id>)
GUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _norm_name(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def _stage_sections(stage):
    return [s for g in stage.grades for s in g.sections]


def _norm_email(v):
    v = str(v or '').strip().lower()
    return v if EMAIL_RE.match(v) else None


def _stage_teachers(stage):
    """Teachers linked to the stage as a whole or to any of its sections."""
    section_ids = {s.id for s in _stage_sections(stage)}
    q = Teacher.query.outerjoin(TeacherSection).outerjoin(TeacherStage)
    conds = [TeacherStage.stage_id == stage.id]
    if section_ids:
        conds.append(TeacherSection.section_id.in_(section_ids))
    return q.filter(db.or_(*conds)).distinct().order_by(Teacher.name_ar).all()


def display_name_from(alias, email):
    """'Amira.Alnababteh' / 'esra'a.thyab' → 'Amira Alnababteh' / 'Esra'a Thyab'.
    Exchange shows a GUID for some accounts; fall back to the email then."""
    raw = str(alias or '').strip()
    if not raw or GUID_RE.match(raw) or '@' in raw:
        raw = (email or '').split('@')[0]
    words = [w for w in re.split(r'[._\-\s]+', raw) if w]
    return ' '.join(w[:1].upper() + w[1:] for w in words) or (email or '')


def _find_teacher(email, name_ar, by_email, by_name):
    if email and email in by_email:
        return by_email[email]
    t = by_name.get(name_ar)
    if t is not None and (not email or not t.email):
        return t
    return None


def _index_teachers():
    by_email, by_name = {}, {}
    for t in Teacher.query.all():
        if t.email:
            by_email[t.email] = t
        by_name.setdefault(_norm_name(t.name_ar), t)
    return by_email, by_name


def build_template(stage):
    """Excel template for one stage, pre-filled with its current teachers."""
    sections = _stage_sections(stage)
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.sheet_view.rightToLeft = True

    ws.append([f'stage:{stage.id}'] + KEYS_FIXED + [f'sec:{s.id}' for s in sections])
    ws.row_dimensions[1].hidden = True
    ws.append(HEAD_FIXED + [s.full_name('ar') for s in sections])

    head_fill = PatternFill('solid', fgColor='12305E')
    thin = Side(style='thin', color='C9D3E3')
    for cell in ws[2]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws.row_dimensions[2].height = 42
    for col, width in zip('ABCDE', (28, 24, 32, 14, 12)):
        ws.column_dimensions[col].width = width
    first_sec = len(HEAD_FIXED) + 1
    for i in range(len(sections)):
        ws.column_dimensions[ws.cell(row=2, column=first_sec + i).column_letter].width = 14

    for t in _stage_teachers(stage):
        linked = {l.section_id for l in t.section_links}
        whole = any(l.stage_id == stage.id for l in t.stage_links)
        ws.append([t.name_ar, t.name_en or '', t.email or '', t.code, MARK if whole else '']
                  + [MARK if s.id in linked else '' for s in sections])

    last_row = max(ws.max_row, 2) + 150
    last_col = len(HEAD_FIXED) + len(sections)
    for r in range(3, last_row + 1):
        for c in range(1, last_col + 1):
            cell = ws.cell(row=r, column=c)
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            if c >= 5:
                cell.alignment = Alignment(horizontal='center')
        ws.cell(row=r, column=4).font = Font(color='667085')
    dv = DataValidation(type='list', formula1=f'"{MARK}"', allow_blank=True,
                        error='اكتب ✓ أو اتركها فارغة', errorTitle='قيمة غير صحيحة')
    dv.add(f'E3:{ws.cell(row=last_row, column=last_col).coordinate}')
    ws.add_data_validation(dv)
    ws.freeze_panes = 'F3'

    info = wb.create_sheet('تعليمات')
    info.sheet_view.rightToLeft = True
    info.column_dimensions['A'].width = 100
    for line in [
        f'قالب معلمي: {stage.name_ar}',
        '',
        '• كل صف = معلم واحد. الاسم بالعربية إلزامي؛ الاسم بالإنجليزية والبريد اختياريان.',
        '• البريد الإلكتروني يطابق المعلم نفسه بين المراحل وبين مرات الرفع — يمكنك تعديل الاسم بحرية ما دام البريد ثابتاً.',
        '• ضع ✓ في «كل المرحلة» ليستقبل المعلم تنبيهات كل صفوف المرحلة، أو ✓ تحت شعب محددة فقط (يقبل أيضاً x أو 1 أو نعم).',
        '• المعلم المشترك بين مراحل (رياضة، موسيقى، دراما…) يُضاف في قالب كل مرحلة بنفس البريد أو الاسم، فيبقى له كود واحد.',
        '• عمود الكود للاطلاع فقط — يُولَّد تلقائياً للمعلم الجديد ولا يتغير للمعلم الموجود.',
        '• لا تغيّر ترتيب أعمدة الشعب ولا الصف الأول المخفي.',
        '• عند الرفع مع خيار «مزامنة»: الشعب غير المعلَّمة في هذه المرحلة تُزال من المعلمين الموجودين في الملف فقط.',
    ]:
        info.append([line])
    info['A1'].font = Font(bold=True, size=14)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def import_template(stage, fileobj, sync=True):
    """Create/update teachers and their section links for one stage.
    Returns a summary dict; raises ValueError for a file that isn't a valid template."""
    try:
        wb = load_workbook(fileobj, data_only=True)
    except Exception:
        raise ValueError('الملف ليس ملف Excel صالحاً (xlsx).')
    ws = wb[SHEET] if SHEET in wb.sheetnames else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 2:
        raise ValueError('الملف فارغ.')

    keys, headers = rows[0], rows[1]
    if keys and str(keys[0] or '').startswith('stage:'):
        file_stage = str(keys[0]).split(':', 1)[1]
        if file_stage != str(stage.id):
            other = db.session.get(Stage, int(file_stage)) if file_stage.isdigit() else None
            raise ValueError(f'هذا القالب يخص مرحلة أخرى ({other.name_ar if other else file_stage}). '
                             'اختر المرحلة الصحيحة أو نزّل قالب هذه المرحلة.')
        data_rows = rows[2:]
    else:
        # No hidden key row (e.g. retyped by hand): match section columns by header text.
        keys, headers, data_rows = None, rows[0], rows[1:]

    sections = {s.id: s for s in _stage_sections(stage)}
    sec_by_name = {s.full_name('ar'): s for s in sections.values()}
    head_roles = {'الاسم بالإنجليزية': 'name_en', 'english': 'name_en', 'البريد الإلكتروني': 'email',
                  'email': 'email', 'البريد': 'email', 'كل المرحلة': 'all'}
    roles, unknown_cols = {}, []
    for c in range(1, len(headers)):
        key = str(keys[c] or '').strip() if keys and c < len(keys) else ''
        head = _norm_name(headers[c])
        if key.startswith('sec:') and key[4:].isdigit() and int(key[4:]) in sections:
            roles[c] = sections[int(key[4:])]
        elif key in ('name_en', 'email', 'code', 'all'):
            roles[c] = key
        elif head in sec_by_name:
            roles[c] = sec_by_name[head]
        elif head.lower() in head_roles or head in head_roles:
            roles[c] = head_roles.get(head, head_roles.get(head.lower()))
        elif 'كود' in head or 'code' in head.lower():
            roles[c] = 'code'
        elif c == 1 and not keys:
            roles[c] = 'name_en'
        elif head:
            unknown_cols.append(head)
    col_of = {v: c for c, v in roles.items() if isinstance(v, str)}
    sec_cols = {c: v for c, v in roles.items() if not isinstance(v, str)}

    def cell(r, role):
        c = col_of.get(role)
        return r[c] if c is not None and c < len(r) else None

    def marked(v):
        return _norm_name(v).lower() not in FALSY

    by_email, by_name = _index_teachers()
    summary = {'created': 0, 'updated': 0, 'linked': 0, 'unlinked': 0, 'skipped': 0,
               'unknown_columns': unknown_cols, 'teachers': []}
    seen = set()
    for r in data_rows:
        r = list(r) + [None] * (len(headers) - len(r))
        name_ar = _norm_name(r[0])
        email = _norm_email(cell(r, 'email'))
        if not name_ar:
            if any(_norm_name(v) for v in r[1:]):
                summary['skipped'] += 1
            continue
        ident = email or name_ar
        if ident in seen:
            summary['skipped'] += 1
            continue
        seen.add(ident)
        name_en = _norm_name(cell(r, 'name_en')) or None
        t = _find_teacher(email, name_ar, by_email, by_name)
        if t is None:
            t = Teacher(name_ar=name_ar, name_en=name_en, email=email, code=generate_code())
            db.session.add(t)
            db.session.flush()
            summary['created'] += 1
        else:
            changed = False
            if t.name_ar != name_ar:
                t.name_ar, changed = name_ar, True
            if name_en and t.name_en != name_en:
                t.name_en, changed = name_en, True
            if email and not t.email:
                t.email, changed = email, True
            summary['updated'] += int(changed)
        if t.email:
            by_email[t.email] = t
        by_name[name_ar] = t

        # whole-stage link
        if 'all' in col_of:
            link = next((l for l in t.stage_links if l.stage_id == stage.id), None)
            if marked(cell(r, 'all')) and link is None:
                db.session.add(TeacherStage(teacher_id=t.id, stage_id=stage.id))
                summary['linked'] += 1
            elif sync and link is not None and not marked(cell(r, 'all')):
                db.session.delete(link)
                summary['unlinked'] += 1
        # section links
        wanted = {sec.id for c, sec in sec_cols.items() if marked(r[c])}
        current = {l.section_id: l for l in t.section_links if l.section_id in sections}
        for sid in wanted - set(current):
            db.session.add(TeacherSection(teacher_id=t.id, section_id=sid))
            summary['linked'] += 1
        if sync:
            for sid in set(current) - wanted:
                db.session.delete(current[sid])
                summary['unlinked'] += 1
        summary['teachers'].append(t)
    db.session.commit()
    return summary


def _decode(raw):
    for enc in ('utf-8-sig', 'utf-16', 'cp1256'):
        try:
            text = raw.decode(enc)
            if enc == 'utf-16' and '\x00' in text:
                continue
            return text
        except UnicodeError:
            continue
    raise ValueError('تعذّر قراءة ترميز الملف.')


def _read_rows(raw):
    """Rows from a CSV in any encoding / line-ending / separator, or from an
    Excel file (the same list re-saved as .xlsx). Never raises csv.Error."""
    if raw[:2] == b'PK':                                   # .xlsx
        try:
            ws = load_workbook(io.BytesIO(raw), data_only=True, read_only=True).worksheets[0]
            return [[('' if v is None else str(v)) for v in r] for r in ws.iter_rows(values_only=True)
                    if any(v not in (None, '') for v in r)]
        except Exception:
            raise ValueError('تعذّر قراءة ملف Excel.')
    text = _decode(raw)
    # Windows (CRLF), old Mac (CR) or Unix (LF) line endings — all become LF.
    text = text.replace('\r\n', '\n').replace('\r', '\n').lstrip('\ufeff')
    if not text.strip():
        return []
    first = next((ln for ln in text.split('\n') if ln.strip()), '')
    delim = max([',', ';', '\t'], key=first.count) if any(d in first for d in ',;\t') else ','
    try:
        rows = list(csv.reader(io.StringIO(text, newline=''), delimiter=delim, quotechar='"'))
    except csv.Error:
        # Stray quotes: read again treating quotes as ordinary characters.
        rows = [[c.strip().strip('"') for c in ln.split(delim)] for ln in text.split('\n')]
    return [[c.strip() for c in r] for r in rows if any(c.strip() for c in r)]


def import_members_csv(stage, fileobj, sync=False):
    """Microsoft 365 / Exchange group export (Name, PrimarySmtpAddress, …):
    every member is linked to the whole stage. Teachers are matched by email,
    so a person in several groups keeps one code.
    sync=True removes the stage link from teachers who are no longer in the file."""
    raw = fileobj.read()
    rows = _read_rows(raw)
    if not rows:
        raise ValueError('الملف فارغ — لا يحتوي أي أعضاء. أعد تصديره من Microsoft 365.')
    head = [str(h or '').strip().lower() for h in rows[0]]

    def col(*names):
        for n in names:
            if n in head:
                return head.index(n)
        return None
    c_email = col('primarysmtpaddress', 'email', 'mail', 'userprincipalname', 'emailaddress', 'البريد الإلكتروني')
    c_name = col('displayname', 'name', 'الاسم')
    c_type = col('recipienttype', 'type')
    if c_email is None:
        # No header row: find the column that holds email addresses.
        c_email = next((i for i, v in enumerate(rows[0]) if _norm_email(v)), None)
        data = rows
        if c_email is None:
            raise ValueError('لم أجد عمود البريد الإلكتروني (PrimarySmtpAddress) في الملف.')
    else:
        data = rows[1:]

    by_email, by_name = _index_teachers()
    summary = {'created': 0, 'updated': 0, 'linked': 0, 'unlinked': 0, 'skipped': 0, 'teachers': []}
    in_file = set()
    for r in data:
        if not r or c_email >= len(r):
            continue
        email = _norm_email(r[c_email])
        if not email:
            summary['skipped'] += 1
            continue
        if c_type is not None and c_type < len(r) and r[c_type] and 'mailbox' not in r[c_type].lower() \
                and 'user' not in r[c_type].lower():
            summary['skipped'] += 1          # groups, contacts, rooms…
            continue
        if email in in_file:
            continue
        in_file.add(email)
        alias = r[c_name] if c_name is not None and c_name < len(r) else ''
        t = by_email.get(email)
        if t is None:
            name = display_name_from(alias, str(r[c_email]).strip())   # keep original casing
            t = Teacher(name_ar=name, name_en=name, email=email, code=generate_code())
            db.session.add(t)
            db.session.flush()
            by_email[email] = t
            summary['created'] += 1
        if not any(l.stage_id == stage.id for l in t.stage_links):
            db.session.add(TeacherStage(teacher_id=t.id, stage_id=stage.id))
            summary['linked'] += 1
        summary['teachers'].append(t)
    if not in_file:
        raise ValueError('لم يُعثر على أي بريد إلكتروني صالح في الملف.')
    if sync:
        for l in TeacherStage.query.filter_by(stage_id=stage.id).all():
            if not l.teacher.email or l.teacher.email not in in_file:
                db.session.delete(l)
                summary['unlinked'] += 1
    db.session.commit()
    return summary


def build_codes_export(stage):
    """Codes sheet for handing out: teachers of a stage + section codes."""
    wb = Workbook()
    ws = wb.active
    ws.title = 'أكواد المعلمين'
    ws.sheet_view.rightToLeft = True
    ws.append(['المعلم', 'الكود', 'مرتبط بـ', 'البريد الإلكتروني'])
    for t in _stage_teachers(stage):
        scope = [st.name_ar + ' (كاملة)' for st in t.stages] + [s.full_name('ar') for s in t.sections]
        ws.append([t.name_ar, t.code, '، '.join(scope), t.email or ''])
    ws2 = wb.create_sheet('أكواد الصفوف')
    ws2.sheet_view.rightToLeft = True
    ws2.append(['الشعبة', 'الكود'])
    for s in _stage_sections(stage):
        ws2.append([s.full_name('ar'), s.code])
    for sh in (ws, ws2):
        for cell in sh[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='12305E')
        sh.column_dimensions['A'].width = 30
        sh.column_dimensions['B'].width = 14
        sh.column_dimensions['C'].width = 60
        sh.column_dimensions['D'].width = 34
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def build_codes_export_all(stages, unlinked):
    """One workbook: a sheet per stage (teacher codes + section codes side by side)
    and a sheet for teachers not linked to any stage."""
    wb = Workbook()
    wb.remove(wb.active)
    head_fill = PatternFill('solid', fgColor='12305E')

    def style(ws, widths):
        ws.sheet_view.rightToLeft = True
        for cell in ws[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = head_fill
        for col, w in widths.items():
            ws.column_dimensions[col].width = w

    used = set()
    for st in stages:
        title = re.sub(r'[\\/*?:\[\]]', '-', st.name_ar)[:28]
        while title in used:
            title = title[:26] + '_' + str(len(used))
        used.add(title)
        ws = wb.create_sheet(title)
        ws.append(['المعلم', 'كود المعلم', 'البريد', '', 'الشعبة', 'كود الصف'])
        teachers = _stage_teachers(st)
        sections = _stage_sections(st)
        for i in range(max(len(teachers), len(sections))):
            t = teachers[i] if i < len(teachers) else None
            sec = sections[i] if i < len(sections) else None
            ws.append([t.name_ar if t else '', t.code if t else '', (t.email or '') if t else '', '',
                       sec.full_name('ar') if sec else '', sec.code if sec else ''])
        style(ws, {'A': 30, 'B': 12, 'C': 32, 'D': 3, 'E': 26, 'F': 12})
    if unlinked:
        ws = wb.create_sheet('غير مرتبطين')
        ws.append(['المعلم', 'كود المعلم', 'البريد'])
        for t in unlinked:
            ws.append([t.name_ar, t.code, t.email or ''])
        style(ws, {'A': 30, 'B': 12, 'C': 32})
    if not wb.sheetnames:
        wb.create_sheet('الأكواد').append(['لا توجد بيانات'])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
