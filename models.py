"""Database models for the school bell notification system.

Schedule resolution for (section, weekday, period_number) — most specific wins:
    GradePeriodTime(grade, weekday, n)      per grade, per day   (same as venues)
    StageDayPeriodTime(stage, weekday, n)   whole stage, one day
    Period.start_time / end_time            stage default, every day
weekday follows Python's date.weekday(): Monday=0 .. Sunday=6.
"""
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class MediaFile(db.Model):
    """Uploaded logos, backgrounds and the alert tone. Stored in Postgres (not
    on disk) because Render's free disk is wiped on every deploy."""
    __tablename__ = 'media_files'
    id         = db.Column(db.Integer, primary_key=True)
    kind       = db.Column(db.String(20), nullable=False)   # logo | background | tone
    filename   = db.Column(db.String(255))
    mime       = db.Column(db.String(100), nullable=False)
    data       = db.Column(db.LargeBinary, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def url(self):
        return f'/media/{self.id}?v={int(self.updated_at.timestamp()) if self.updated_at else 0}'


class Stage(db.Model):
    __tablename__ = 'stages'
    id            = db.Column(db.Integer, primary_key=True)
    name_ar       = db.Column(db.String(200), nullable=False)
    name_en       = db.Column(db.String(200))
    sort_order    = db.Column(db.Integer, default=0)
    active        = db.Column(db.Boolean, default=True)
    # Periods run back-to-back with no gaps: each period's start/end is
    # computed from day_start + the durations of the periods before it.
    day_start     = db.Column(db.String(5), nullable=False, default='08:00')
    logo_id       = db.Column(db.Integer, db.ForeignKey('media_files.id', ondelete='SET NULL'))
    background_id = db.Column(db.Integer, db.ForeignKey('media_files.id', ondelete='SET NULL'))
    # Own alert tone for this stage; empty = the school's default tone.
    tone_id       = db.Column(db.Integer, db.ForeignKey('media_files.id', ondelete='SET NULL'))

    logo       = db.relationship('MediaFile', foreign_keys=[logo_id])
    background = db.relationship('MediaFile', foreign_keys=[background_id])
    tone       = db.relationship('MediaFile', foreign_keys=[tone_id])
    weekdays   = db.relationship('StageWeekday', backref='stage', cascade='all, delete-orphan',
                                 order_by='StageWeekday.weekday')
    periods    = db.relationship('Period', backref='stage', cascade='all, delete-orphan',
                                 order_by='Period.number')
    day_times  = db.relationship('StageDayPeriodTime', backref='stage', cascade='all, delete-orphan')
    grades     = db.relationship('Grade', backref='stage', cascade='all, delete-orphan',
                                 order_by='Grade.sort_order')
    teacher_links = db.relationship('TeacherStage', backref='stage', cascade='all, delete-orphan')

    @property
    def weekday_list(self):
        return [w.weekday for w in self.weekdays]


class StageWeekday(db.Model):
    """School days for a stage (stages may run different weeks)."""
    __tablename__ = 'stage_weekdays'
    id       = db.Column(db.Integer, primary_key=True)
    stage_id = db.Column(db.Integer, db.ForeignKey('stages.id'), nullable=False)
    weekday  = db.Column(db.Integer, nullable=False)
    __table_args__ = (db.UniqueConstraint('stage_id', 'weekday', name='uq_stage_weekday'),)


class Period(db.Model):
    """A daily period of a stage (class or break). Its label drives the alert
    text: "انتهت {label} - بداية {next label}"."""
    __tablename__ = 'periods'
    id         = db.Column(db.Integer, primary_key=True)
    stage_id   = db.Column(db.Integer, db.ForeignKey('stages.id'), nullable=False)
    number     = db.Column(db.Integer, nullable=False)      # order within the day, 1..n
    kind       = db.Column(db.String(10), nullable=False, default='class')  # class | break
    label_ar   = db.Column(db.String(100), nullable=False)
    label_en   = db.Column(db.String(100))
    start_time = db.Column(db.String(5), nullable=False)    # "HH:MM" (computed from the chain)
    end_time   = db.Column(db.String(5), nullable=False)
    __table_args__ = (db.UniqueConstraint('stage_id', 'number', name='uq_stage_period'),)

    @property
    def duration(self):
        from services import minutes_between
        return minutes_between(self.start_time, self.end_time)


class StageDayPeriodTime(db.Model):
    """Stage-wide override for one weekday (e.g. a shorter Thursday). New grades
    inherit it automatically."""
    __tablename__ = 'stage_day_period_times'
    id            = db.Column(db.Integer, primary_key=True)
    stage_id      = db.Column(db.Integer, db.ForeignKey('stages.id'), nullable=False)
    weekday       = db.Column(db.Integer, nullable=False)
    period_number = db.Column(db.Integer, nullable=False)
    start_time    = db.Column(db.String(5), nullable=False)
    end_time      = db.Column(db.String(5), nullable=False)
    __table_args__ = (
        db.UniqueConstraint('stage_id', 'weekday', 'period_number', name='uq_stage_day_period_time'),
    )


class Grade(db.Model):
    __tablename__ = 'grades'
    id         = db.Column(db.Integer, primary_key=True)
    stage_id   = db.Column(db.Integer, db.ForeignKey('stages.id'), nullable=False)
    name_ar    = db.Column(db.String(100), nullable=False)
    name_en    = db.Column(db.String(100))
    sort_order = db.Column(db.Integer, default=0)

    sections    = db.relationship('Section', backref='grade', cascade='all, delete-orphan',
                                  order_by='Section.sort_order')
    period_times = db.relationship('GradePeriodTime', backref='grade', cascade='all, delete-orphan')
    day_programs = db.relationship('GradeDayProgram', backref='grade', cascade='all, delete-orphan')

    def program_for(self, weekday):
        return next((a.program for a in self.day_programs if a.weekday == weekday), None)


class GradePeriodTime(db.Model):
    """Per-grade, per-weekday override of a period's start/end time (reused
    as-is from the venues project)."""
    __tablename__ = 'grade_period_times'
    id            = db.Column(db.Integer, primary_key=True)
    grade_id      = db.Column(db.Integer, db.ForeignKey('grades.id'), nullable=False)
    weekday       = db.Column(db.Integer, nullable=False)
    period_number = db.Column(db.Integer, nullable=False)
    start_time    = db.Column(db.String(5), nullable=False)
    end_time      = db.Column(db.String(5), nullable=False)
    __table_args__ = (
        db.UniqueConstraint('grade_id', 'weekday', 'period_number', name='uq_grade_period_time'),
    )


class BellProgram(db.Model):
    """A complete bell program: an ordered list of periods and breaks with
    their times (e.g. "الصفوف 3+4" or "الصفوف 3+4 — الثلاثاء"). Grades are
    assigned a program per weekday; that assignment beats every other layer."""
    __tablename__ = 'bell_programs'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(200), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0)

    periods     = db.relationship('BellProgramPeriod', backref='program', cascade='all, delete-orphan',
                                  order_by='BellProgramPeriod.number')
    assignments = db.relationship('GradeDayProgram', backref='program', cascade='all, delete-orphan')

    @property
    def day_start(self):
        return self.periods[0].start_time if self.periods else '08:00'

    @property
    def day_end(self):
        return self.periods[-1].end_time if self.periods else ''


class BellProgramPeriod(db.Model):
    __tablename__ = 'bell_program_periods'
    id         = db.Column(db.Integer, primary_key=True)
    program_id = db.Column(db.Integer, db.ForeignKey('bell_programs.id'), nullable=False)
    number     = db.Column(db.Integer, nullable=False)
    kind       = db.Column(db.String(10), nullable=False, default='class')   # class | break
    label_ar   = db.Column(db.String(100), nullable=False)
    label_en   = db.Column(db.String(100))
    start_time = db.Column(db.String(5), nullable=False)
    end_time   = db.Column(db.String(5), nullable=False)
    __table_args__ = (db.UniqueConstraint('program_id', 'number', name='uq_program_period'),)

    @property
    def duration(self):
        from services import minutes_between
        return minutes_between(self.start_time, self.end_time)


class GradeDayProgram(db.Model):
    __tablename__ = 'grade_day_programs'
    id         = db.Column(db.Integer, primary_key=True)
    grade_id   = db.Column(db.Integer, db.ForeignKey('grades.id'), nullable=False)
    weekday    = db.Column(db.Integer, nullable=False)
    program_id = db.Column(db.Integer, db.ForeignKey('bell_programs.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('grade_id', 'weekday', name='uq_grade_day_program'),)


class Section(db.Model):
    __tablename__ = 'sections'
    id         = db.Column(db.Integer, primary_key=True)
    grade_id   = db.Column(db.Integer, db.ForeignKey('grades.id'), nullable=False)
    name_ar    = db.Column(db.String(100), nullable=False)
    name_en    = db.Column(db.String(100))
    sort_order = db.Column(db.Integer, default=0)
    code       = db.Column(db.String(12), unique=True, nullable=False)

    teacher_links = db.relationship('TeacherSection', backref='section', cascade='all, delete-orphan')

    def full_name(self, lang='ar'):
        if lang == 'en':
            return f'{self.grade.name_en or self.grade.name_ar} - {self.name_en or self.name_ar}'
        return f'{self.grade.name_ar} - {self.name_ar}'


class Teacher(db.Model):
    __tablename__ = 'teachers'
    id      = db.Column(db.Integer, primary_key=True)
    name_ar = db.Column(db.String(200), nullable=False)
    name_en = db.Column(db.String(200))
    active  = db.Column(db.Boolean, default=True)
    code    = db.Column(db.String(12), unique=True, nullable=False)
    # School email (lower-case). Matches the same person across imports and stages.
    email   = db.Column(db.String(255), unique=True)

    section_links = db.relationship('TeacherSection', backref='teacher', cascade='all, delete-orphan')
    stage_links   = db.relationship('TeacherStage', backref='teacher', cascade='all, delete-orphan')

    @property
    def sections(self):
        return sorted((l.section for l in self.section_links),
                      key=lambda s: (s.grade.stage.sort_order, s.grade.sort_order, s.sort_order))

    @property
    def stages(self):
        return sorted((l.stage for l in self.stage_links), key=lambda st: (st.sort_order, st.id))


class TeacherStage(db.Model):
    """Whole-stage link: the teacher gets the alerts of every grade in the
    stage (used for staff lists that aren't broken down by section)."""
    __tablename__ = 'teacher_stages'
    id         = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id'), nullable=False)
    stage_id   = db.Column(db.Integer, db.ForeignKey('stages.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('teacher_id', 'stage_id', name='uq_teacher_stage'),)


class TeacherSection(db.Model):
    __tablename__ = 'teacher_sections'
    id         = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id'), nullable=False)
    section_id = db.Column(db.Integer, db.ForeignKey('sections.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('teacher_id', 'section_id', name='uq_teacher_section'),)


class Device(db.Model):
    """A browser/tablet that logged in with a code — lets the admin see which
    classrooms actually have a live screen."""
    __tablename__ = 'devices'
    id           = db.Column(db.Integer, primary_key=True)
    device_uid   = db.Column(db.String(64), unique=True, nullable=False)
    code_type    = db.Column(db.String(10))            # section | teacher
    owner_id     = db.Column(db.Integer)                # section.id or teacher.id
    user_agent   = db.Column(db.String(300))
    first_seen   = db.Column(db.DateTime, default=datetime.utcnow)
    last_sync    = db.Column(db.DateTime, default=datetime.utcnow)


class AppSetting(db.Model):
    """Key/value store (same pattern as venues): tone_id, schedule_version."""
    __tablename__ = 'app_settings'
    key   = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text)

    @staticmethod
    def get(key, default=None):
        row = db.session.get(AppSetting, key)
        return row.value if row else default

    @staticmethod
    def set(key, value):
        row = db.session.get(AppSetting, key)
        if row:
            row.value = str(value)
        else:
            db.session.add(AppSetting(key=key, value=str(value)))


def ensure_schema():
    """create_all() adds new tables but never new columns on existing ones.
    Add the columns introduced after the first deploy (idempotent)."""
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    if 'teachers' in insp.get_table_names():
        cols = {c['name'] for c in insp.get_columns('teachers')}
        if 'email' not in cols:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE teachers ADD COLUMN email VARCHAR(255)'))
                conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_teachers_email ON teachers (email)'))
    if 'stages' in insp.get_table_names():
        cols = {c['name'] for c in insp.get_columns('stages')}
        if 'tone_id' not in cols:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE stages ADD COLUMN tone_id INTEGER REFERENCES media_files(id) ON DELETE SET NULL'))
