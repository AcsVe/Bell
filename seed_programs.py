"""Built-in bell programs for 2026/2027, transcribed from the school's
«برنامج الحصص اليومي» Word document, so they can be loaded with one click.

Grades are grouped the way the school's staff groups are (1-3, 4-6, 7-12);
the admin picks which existing stage each group goes to (auto-matched by name).
"""
import io
import re

from openpyxl import Workbook

from programs_io import ASSIGN_SHEET, DAY_COLS, PROG_HEAD, PROG_SHEET

TITLE = 'برنامج الحصص اليومي 2026/2027'
ORD = ['الأولى', 'الثانية', 'الثالثة', 'الرابعة', 'الخامسة', 'السادسة', 'السابعة', 'الثامنة']
B = 'B'
# Period numbers and B (break), with times as printed (1:10 = 13:10).
PROGRAMS = {
    'الصفوف 1+2': ([1, 2, B, 3, 4, 5, B, 6, 7, 8],
        ['8:00-8:45', '8:45-9:30', '9:30-9:50', '9:50-10:35', '10:35-11:20', '11:20-12:05', '12:05-12:20', '12:20-1:05', '1:05-1:50', '1:50-2:35']),
    'الصفوف 1+2 — الثلاثاء': ([1, 2, B, 3, 4, 5, B, 6, 7, 8],
        ['8:00-8:35', '8:35-9:10', '9:10-9:25', '9:25-10:00', '10:00-10:35', '10:35-11:10', '11:10-11:25', '11:25-12:00', '12:00-12:35', '12:35-1:20']),
    'الصفوف 3+4': ([1, 2, B, 3, 4, 5, 6, B, 7, 8],
        ['8:00-8:45', '8:45-9:30', '9:30-9:50', '9:50-10:35', '10:35-11:20', '11:20-12:05', '12:05-12:50', '12:50-1:05', '1:05-1:50', '1:50-2:35']),
    'الصفوف 3+4 — الثلاثاء': ([1, 2, B, 3, 4, 5, B, 6, 7, 8],
        ['8:00-8:35', '8:35-9:10', '9:10-9:25', '9:25-10:00', '10:00-10:35', '10:35-11:10', '11:10-11:25', '11:25-12:00', '12:00-12:35', '12:35-1:20']),
    'الصفوف 5+6': ([1, 2, 3, B, 4, 5, B, 6, 7, 8],
        ['8:00-8:45', '8:45-9:30', '9:30-10:15', '10:15-10:35', '10:35-11:20', '11:20-12:05', '12:05-12:20', '12:20-1:05', '1:05-1:50', '1:50-2:35']),
    'الصفوف 5+6 — الثلاثاء': ([1, 2, 3, B, 4, 5, B, 6, 7, 8],
        ['8:00-8:35', '8:35-9:10', '9:10-9:45', '9:45-10:00', '10:00-10:35', '10:35-11:10', '11:10-11:25', '11:25-12:00', '12:00-12:35', '12:35-1:20']),
    'الصفوف 7-9': ([1, 2, 3, B, 4, 5, 6, B, 7, 8],
        ['8:00-8:45', '8:45-9:30', '9:30-10:15', '10:15-10:35', '10:35-11:20', '11:20-12:05', '12:05-12:50', '12:50-1:05', '1:05-1:50', '1:50-2:35']),
    'الصفوف 7-9 — الثلاثاء': ([1, 2, 3, B, 4, 5, 6, B, 7, 8],
        ['8:00-8:35', '8:35-9:10', '9:10-9:45', '9:45-10:00', '10:00-10:35', '10:35-11:10', '11:10-11:45', '11:45-12:00', '12:00-12:35', '12:35-1:20']),
    # Printed: break 11:00–11:35, period 5 from 11:40. Periods are back-to-back,
    # so the break runs to 11:40 and period 5 starts exactly as printed.
    'الصفوف 10-12': ([1, 2, 3, 4, B, 5, 6, 7, 8],
        ['8:00-8:45', '8:45-9:30', '9:30-10:15', '10:15-11:00', '11:00-11:40', '11:40-12:25', '12:25-1:10', '1:10-1:50', '1:50-2:35']),
    'الصفوف 10-12 — الثلاثاء': ([1, 2, 3, 4, B, 5, 6, 7, 8],
        ['8:00-8:35', '8:35-9:10', '9:10-9:45', '9:45-10:20', '10:20-10:50', '10:50-11:25', '11:25-12:00', '12:00-12:35', '12:35-1:20']),
}
# group key → (label, default new-stage name, [(grade ar, grade en, program)])
GROUPS = {
    'g13': ('الصفوف 1–3', 'المرحلة الأساسية 1-3', [
        ('الصف الأول', 'Grade 1', 'الصفوف 1+2'), ('الصف الثاني', 'Grade 2', 'الصفوف 1+2'),
        ('الصف الثالث', 'Grade 3', 'الصفوف 3+4')]),
    'g46': ('الصفوف 4–6', 'المرحلة الأساسية 4-6', [
        ('الصف الرابع', 'Grade 4', 'الصفوف 3+4'), ('الصف الخامس', 'Grade 5', 'الصفوف 5+6'),
        ('الصف السادس', 'Grade 6', 'الصفوف 5+6')]),
    'g712': ('الصفوف 7–12', 'المرحلة الثانوية', [
        ('الصف السابع', 'Grade 7', 'الصفوف 7-9'), ('الصف الثامن', 'Grade 8', 'الصفوف 7-9'),
        ('الصف التاسع', 'Grade 9', 'الصفوف 7-9'), ('الصف العاشر', 'Grade 10', 'الصفوف 10-12'),
        ('الصف الحادي عشر', 'Grade 11', 'الصفوف 10-12'), ('الصف الثاني عشر', 'Grade 12', 'الصفوف 10-12')]),
}
SCHOOL_DAYS = {6, 0, 1, 2, 3}      # Sunday–Thursday (python weekday numbers)
TUESDAY = 1
_MATCH = {
    'g13': re.compile(r'1\s*[-–+]\s*3'),
    'g46': re.compile(r'4\s*[-–+]\s*6'),
    'g712': re.compile(r'ثانو|second|7\s*[-–+]\s*12|10\s*[-–+]\s*12', re.I),
}


def guess_stage(group, stages):
    """The existing stage whose name matches the group (e.g. 'Primary1-3'), or None."""
    for st in stages:
        names = f'{st.name_ar} {st.name_en or ""}'.replace('⁦', '').replace('⁩', '')
        if _MATCH[group].search(names):
            return st
    return None


def build_workbook(stage_names):
    """stage_names: {group: stage name to use}. Returns xlsx bytes for import_programs()."""
    wb = Workbook()
    ws = wb.active
    ws.title = PROG_SHEET
    ws.append(PROG_HEAD)
    for name, (seq, times) in PROGRAMS.items():
        for i, (x, t) in enumerate(zip(seq, times), start=1):
            a, b = t.split('-')
            if x == B:
                ws.append([name, i, 'استراحة', 'الفرصة', 'Break', a, b])
            else:
                ws.append([name, i, 'حصة', f'الحصة {ORD[x - 1]}', f'Period {x}', a, b])
    wa = wb.create_sheet(ASSIGN_SHEET)
    wa.append(['المرحلة', 'الصف', 'الصف بالإنجليزية'] + [n for _, n in DAY_COLS])
    for group, (_, _, grades) in GROUPS.items():
        for g_ar, g_en, prog in grades:
            days = ['' if wd not in SCHOOL_DAYS else (f'{prog} — الثلاثاء' if wd == TUESDAY else prog)
                    for wd, _ in DAY_COLS]
            wa.append([stage_names[group], g_ar, g_en] + days)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
