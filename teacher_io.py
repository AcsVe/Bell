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

from models import db, Section, Stage, Teacher, TeacherSection
from services import generate_code

SHEET = 'المعلمون'
MARK = '✓'
FALSY = {'', '0', 'no', 'لا', '-', '×', 'false'}
HEAD_FIXED = ['الاسم بالعربية', 'الاسم بالإنجليزية', 'كود المعلم (للاطلاع)']


def _norm_name(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def _stage_sections(stage):
    return [s for g in stage.grades for s in g.sections]


def build_template(stage):
    """Excel template for one stage, pre-filled with its current teachers."""
    sections = _stage_sections(stage)
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.sheet_view.rightToLeft = True

    ws.append([f'stage:{stage.id}', '', ''] + [f'sec:{s.id}' for s in sections])
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
    ws.column_dimensions['A'].width = 28
    ws.column_dimensions['B'].width = 24
    ws.column_dimensions['C'].width = 16
    for i in range(len(sections)):
        ws.column_dimensions[ws.cell(row=2, column=4 + i).column_letter].width = 14

    section_ids = {s.id for s in sections}
    teachers = (Teacher.query.join(TeacherSection).filter(TeacherSection.section_id.in_(section_ids))
                .distinct().order_by(Teacher.name_ar).all()) if section_ids else []
    for t in teachers:
        linked = {l.section_id for l in t.section_links}
        ws.append([t.name_ar, t.name_en or '', t.code] + [MARK if s.id in linked else '' for s in sections])

    last_row = max(ws.max_row, 2) + 150
    for r in range(3, last_row + 1):
        for c in range(1, 4 + len(sections)):
            cell = ws.cell(row=r, column=c)
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            if c >= 4:
                cell.alignment = Alignment(horizontal='center')
        ws.cell(row=r, column=3).font = Font(color='667085')
    if sections:
        dv = DataValidation(type='list', formula1=f'"{MARK}"', allow_blank=True,
                            error='اكتب ✓ أو اتركها فارغة', errorTitle='قيمة غير صحيحة')
        first = ws.cell(row=3, column=4).coordinate
        last = ws.cell(row=last_row, column=3 + len(sections)).coordinate
        dv.add(f'{first}:{last}')
        ws.add_data_validation(dv)
    ws.freeze_panes = 'D3'

    info = wb.create_sheet('تعليمات')
    info.sheet_view.rightToLeft = True
    info.column_dimensions['A'].width = 100
    for line in [
        f'قالب معلمي: {stage.name_ar}',
        '',
        '• كل صف = معلم واحد. اكتب الاسم بالعربية (إلزامي) والاسم بالإنجليزية (اختياري).',
        '• ضع ✓ تحت كل شعبة يدرّسها المعلم (يقبل أيضاً x أو 1 أو نعم).',
        '• المعلم المشترك بين مراحل (رياضة، موسيقى، دراما…) يُضاف في قالب كل مرحلة بنفس الاسم تماماً، فيبقى له كود واحد.',
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
    by_name = {s.full_name('ar'): s for s in sections.values()}
    col_section, unknown_cols = {}, []
    for c in range(3, len(headers)):
        sec = None
        key = str(keys[c] or '') if keys and c < len(keys) else ''
        if key.startswith('sec:') and key[4:].isdigit():
            sec = sections.get(int(key[4:]))
        if sec is None:
            sec = by_name.get(_norm_name(headers[c]))
        if sec is not None:
            col_section[c] = sec
        elif _norm_name(headers[c]):
            unknown_cols.append(_norm_name(headers[c]))

    existing = {_norm_name(t.name_ar): t for t in Teacher.query.all()}
    summary = {'created': 0, 'updated': 0, 'linked': 0, 'unlinked': 0, 'skipped': 0,
               'unknown_columns': unknown_cols, 'teachers': []}
    seen = set()
    for r in data_rows:
        r = list(r) + [None] * (len(headers) - len(r))
        name_ar = _norm_name(r[0])
        if not name_ar:
            if any(_norm_name(v) for v in r[1:]):
                summary['skipped'] += 1
            continue
        if name_ar in seen:
            summary['skipped'] += 1
            continue
        seen.add(name_ar)
        name_en = _norm_name(r[1]) or None
        t = existing.get(name_ar)
        if t is None:
            t = Teacher(name_ar=name_ar, name_en=name_en, code=generate_code())
            db.session.add(t)
            db.session.flush()
            existing[name_ar] = t
            summary['created'] += 1
        elif name_en and t.name_en != name_en:
            t.name_en = name_en
            summary['updated'] += 1

        wanted = {sec.id for c, sec in col_section.items()
                  if _norm_name(r[c]).lower() not in FALSY}
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


def build_codes_export(stage):
    """Codes sheet for handing out: teachers of a stage + section codes."""
    wb = Workbook()
    ws = wb.active
    ws.title = 'أكواد المعلمين'
    ws.sheet_view.rightToLeft = True
    ws.append(['المعلم', 'الكود', 'الشعب'])
    section_ids = {s.id for s in _stage_sections(stage)}
    teachers = (Teacher.query.join(TeacherSection).filter(TeacherSection.section_id.in_(section_ids))
                .distinct().order_by(Teacher.name_ar).all()) if section_ids else []
    for t in teachers:
        ws.append([t.name_ar, t.code, '، '.join(s.full_name('ar') for s in t.sections)])
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
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
