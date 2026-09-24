# -*- coding: utf-8 -*-
"""
طبقة قاعدة البيانات - SQLite محلي
Local SQLite layer. One file: school.db next to this module.
"""
import os
import json
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "school.db")

# أيام الأسبوع الدراسي السعودي (0 = الأحد)
DAY_NAMES = ["الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"]

DEFAULT_SETTINGS = {
    "school_name": "",
    "school_stage_label": "",
    "manager_name": "",
    "working_days": "0,1,2,3,4",      # الأحد .. الخميس
    "periods_per_day": "7",
    "periods_per_day_map": "{}",       # {"0": 7, "1": 6, ...} تجاوز لكل يوم
    "grade_periods_map": "{}",         # {"<grade_id>": 6} تجاوز لكل صف
    "core_subject_ids": "[]",          # المواد الأساسية (تُفضَّل في الحصص المبكرة)
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- المراحل: ابتدائي / متوسط / ثانوي
CREATE TABLE IF NOT EXISTS stages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

-- الصفوف داخل كل مرحلة
CREATE TABLE IF NOT EXISTS grades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id   INTEGER NOT NULL REFERENCES stages(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

-- الشعب داخل كل صف (تقابل classes في التطبيق الأصلي)
CREATE TABLE IF NOT EXISTS sections (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    grade_id         INTEGER NOT NULL REFERENCES grades(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    student_count    INTEGER NOT NULL DEFAULT 0,
    -- المطلوب من الحصص أسبوعياً. 0 = استعمل عدد الخانات المتاحة تلقائياً
    required_periods INTEGER NOT NULL DEFAULT 0,
    -- 1 = الصف نفسه بلا شعب: هذه شعبة ضمنية تمثّله، ولا يظهر لها اسم
    is_default       INTEGER NOT NULL DEFAULT 0,
    sort_order       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS subjects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    short_name      TEXT NOT NULL DEFAULT '',
    color_index     INTEGER NOT NULL DEFAULT 0,
    -- عدد حصص المادة المعتاد للفصل الواحد، يُقترح تلقائياً عند الإسناد
    default_periods INTEGER NOT NULL DEFAULT 0,
    sort_order      INTEGER NOT NULL DEFAULT 0
);

-- أي معلم يدرّس أي مادة (يُحدَّد من شاشة المواد)
CREATE TABLE IF NOT EXISTS subject_teachers (
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    teacher_id INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    PRIMARY KEY (subject_id, teacher_id)
);

CREATE TABLE IF NOT EXISTS teachers (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    phone                   TEXT NOT NULL DEFAULT '',
    specialization          TEXT NOT NULL DEFAULT '',
    max_periods_per_week    INTEGER NOT NULL DEFAULT 24,
    max_periods_per_day     INTEGER NOT NULL DEFAULT 6,
    is_administrative       INTEGER NOT NULL DEFAULT 0,
    admin_reduction_periods INTEGER NOT NULL DEFAULT 0,
    notes                   TEXT NOT NULL DEFAULT '',
    sort_order              INTEGER NOT NULL DEFAULT 0
);

-- الخانات التي لا يتاح فيها المعلم (قيد صارم)
CREATE TABLE IF NOT EXISTS teacher_unavailable (
    teacher_id    INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    day           INTEGER NOT NULL,
    period_number INTEGER NOT NULL,
    PRIMARY KEY (teacher_id, day, period_number)
);

-- الإسناد: من يُدرّس أي مادة لأي شعبة وكم حصة أسبوعياً
CREATE TABLE IF NOT EXISTS assignments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id          INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    subject_id          INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    teacher_id          INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    periods_per_week    INTEGER NOT NULL DEFAULT 1,
    double_periods      INTEGER NOT NULL DEFAULT 0,  -- عدد الحصص المزدوجة المطلوبة
    merge_group_id      TEXT,                        -- دمج شعب في حصة واحدة
    co_group_id         TEXT,                        -- تدريس مشترك
    room_id             INTEGER REFERENCES rooms(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS time_slots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    period_number INTEGER NOT NULL UNIQUE,
    start_time    TEXT NOT NULL DEFAULT '',
    end_time      TEXT NOT NULL DEFAULT '',
    is_break      INTEGER NOT NULL DEFAULT 0,
    name          TEXT NOT NULL DEFAULT ''
);

-- المعامل / القاعات المشتركة
CREATE TABLE IF NOT EXISTS rooms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    capacity   INTEGER NOT NULL DEFAULT 1   -- كم شعبة تتسع في نفس الوقت
);

CREATE TABLE IF NOT EXISTS versions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    is_active  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    notes      TEXT NOT NULL DEFAULT ''
);

-- خلية واحدة في الجدول = صف واحد هنا
CREATE TABLE IF NOT EXISTS schedule (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id         INTEGER NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    section_id         INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    subject_id         INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    teacher_id         INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    assignment_id      INTEGER,
    room_id            INTEGER REFERENCES rooms(id) ON DELETE SET NULL,
    day                INTEGER NOT NULL,
    period_number      INTEGER NOT NULL,
    merge_group_id     TEXT,
    adjacency_group_id TEXT,
    co_group_id        TEXT,
    is_pinned          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sched_ver     ON schedule(version_id);
CREATE INDEX IF NOT EXISTS idx_sched_sec     ON schedule(version_id, section_id, day, period_number);
CREATE INDEX IF NOT EXISTS idx_sched_teacher ON schedule(version_id, teacher_id, day, period_number);

CREATE TABLE IF NOT EXISTS gen_options (
    version_id   INTEGER PRIMARY KEY REFERENCES versions(id) ON DELETE CASCADE,
    options_json TEXT NOT NULL
);

-- لقطة كاملة قبل كل عملية تغيّر البيانات (تراجع/استرجاع)
CREATE TABLE IF NOT EXISTS history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id     INTEGER NOT NULL,
    number         INTEGER NOT NULL,
    snapshot_json  TEXT NOT NULL,
    change_summary TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hist_ver ON history(version_id, number DESC);
"""


# =====================================================================
#  الاتصال: SQLite محلياً، وTurso (libSQL) عند النشر على الإنترنت.
#  لغة الاستعلام واحدة في الحالتين (libSQL هو SQLite نفسه)، فلم نغيّر
#  أي عبارة SQL — غيّرنا طبقة الاتصال وحدها.
# =====================================================================

def _load_env_file():
    """
    يقرأ ملف .env المجاور إن وُجد — فلا تحتاج ضبط المتغيّرات في كل مرة.
    الملف محمي بـ .gitignore فلا يصل إلى GitHub.
    متغيّرات البيئة الحقيقية لها الأولوية دائماً (هكذا يعمل Vercel).
    """
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and val and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


_load_env_file()

TURSO_URL = os.environ.get("TURSO_DATABASE_URL", "").strip()
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()


def using_turso():
    return bool(TURSO_URL)


class Row(tuple):
    """صفّ يُقرأ بالاسم والرقم معاً، ويقبل dict(row) — كـ sqlite3.Row."""

    def __new__(cls, cols, values):
        self = super().__new__(cls, values)
        self._cols = cols
        return self

    def keys(self):
        return list(self._cols)

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._cols.index(key))
            except ValueError:
                raise IndexError("لا يوجد عمود باسم %r" % key)
        return tuple.__getitem__(self, key)

    def get(self, key, default=None):
        try:
            return self[key]
        except (IndexError, KeyError):
            return default


class _Cursor(object):
    """يلفّ مؤشّر libsql ليعيد صفوفاً تُقرأ بالاسم."""

    def __init__(self, cur):
        self._cur = cur
        self._cols = [d[0] for d in (cur.description or [])]

    def _wrap(self, r):
        return None if r is None else Row(self._cols, r)

    def fetchone(self):
        return self._wrap(self._cur.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description


class TursoConn(object):
    """واجهة شبيهة بـ sqlite3.Connection فوق libSQL."""

    def __init__(self, url, token):
        import libsql
        self._c = libsql.connect(url, auth_token=token) if token \
            else libsql.connect(url)

    def execute(self, sql, args=()):
        return _Cursor(self._c.execute(sql, tuple(args)))

    def executemany(self, sql, seq):
        return _Cursor(self._c.executemany(sql, [tuple(a) for a in seq]))

    def executescript(self, script):
        for stmt in [s.strip() for s in script.split(";")]:
            if stmt:
                self._c.execute(stmt)
        return self

    def commit(self):
        self._c.commit()

    def rollback(self):
        try:
            self._c.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self._c.close()
        except Exception:
            pass


def connect():
    if using_turso():
        return TursoConn(TURSO_URL, TURSO_TOKEN)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def checkpoint(conn):
    """تفريغ دفتر WAL — للنسخ المحلي فقط، ولا معنى له على الإنترنت."""
    if using_turso():
        return
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass


def insert_many(conn, sql_head, rows, chunk=180):
    """
    إدراج جماعي في عبارة واحدة بدل عبارة لكل صف.
    محلياً الفرق طفيف، وعلى الإنترنت هو الفرق بين ثانية و858 رحلة شبكة.
      sql_head: "INSERT INTO t(a, b, c) VALUES"
      rows:     [(1, 2, 3), ...]
    """
    rows = list(rows)
    if not rows:
        return 0
    ncols = len(rows[0])
    one = "(" + ",".join(["?"] * ncols) + ")"
    done = 0
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        sql = sql_head + " " + ",".join([one] * len(part))
        flat = []
        for r in part:
            flat.extend(r)
        conn.execute(sql, flat)
        done += len(part)
    return done


# أعمدة أُضيفت بعد الإصدار الأول - تُضاف للقواعد القديمة عند التشغيل
MIGRATIONS = [
    ("sections", "required_periods", "INTEGER NOT NULL DEFAULT 0"),
    ("sections", "is_default",       "INTEGER NOT NULL DEFAULT 0"),
    ("subjects", "default_periods",  "INTEGER NOT NULL DEFAULT 0"),
]


def migrate(conn):
    """يضيف الأعمدة الناقصة ويملأها من البيانات القائمة - بلا فقد أي بيانات."""
    for table, col, decl in MIGRATIONS:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        if col not in cols:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))

    # اربط المعلمين بالمواد من الإسنادات القائمة (أول مرة فقط)
    have = conn.execute("SELECT COUNT(*) c FROM subject_teachers").fetchone()["c"]
    if not have:
        conn.execute(
            "INSERT OR IGNORE INTO subject_teachers(subject_id, teacher_id) "
            "SELECT DISTINCT subject_id, teacher_id FROM assignments")

    # عدد حصص المادة الافتراضي: الأكثر شيوعاً في إسناداتها
    for r in conn.execute("SELECT id FROM subjects WHERE default_periods = 0"):
        row = conn.execute(
            "SELECT periods_per_week n, COUNT(*) c FROM assignments "
            "WHERE subject_id = ? GROUP BY periods_per_week "
            "ORDER BY c DESC, n DESC LIMIT 1", (r["id"],)).fetchone()
        if row:
            conn.execute("UPDATE subjects SET default_periods = ? WHERE id = ?",
                         (row["n"], r["id"]))

    ensure_default_sections(conn)


def ensure_default_sections(conn):
    """
    كل صف لا شعب له يمثّله شعبة ضمنية واحدة، فيُحسب صفاً كاملاً في الجدول
    دون أن يُجبَر المستخدم على اختراع اسم شعبة.
    """
    rows = conn.execute(
        "SELECT g.id FROM grades g "
        "LEFT JOIN sections se ON se.grade_id = g.id "
        "WHERE se.id IS NULL").fetchall()
    for r in rows:
        conn.execute(
            "INSERT INTO sections(grade_id, name, is_default, sort_order) "
            "VALUES (?, '', 1, 0)", (r["id"],))
    return len(rows)


def grade_display_name(raw_name, stage_name):
    """
    اسم الصف كما يظهر: «أول» في مرحلة «ثانوي» تصير «أول ثانوي».
    وإن كان الاسم يحمل اسم المرحلة أصلاً يُترك كما هو - فلا يتكرر.
    """
    raw = " ".join((raw_name or "").split())
    stage = " ".join((stage_name or "").split())
    if not stage or not raw:
        return raw
    # إن حمل اسم الصف أي كلمة دالّة من اسم المرحلة فلا نكرّرها
    # («أول ثنائي لغة» في مرحلة «ابتدائي ثنائي لغة» تبقى كما هي)
    for word in stage.split():
        if len(word) > 2 and word in raw:
            return raw
    return "%s %s" % (raw, stage)


# نص SQL موحّد لاسم الشعبة المعروض
SECTION_LABEL_SQL = (
    "CASE WHEN se.is_default = 1 THEN g.name "
    "     ELSE g.name || ' / ' || se.name END")


def init_db():
    """ينشئ الجداول ويضبط القيم الافتراضية. آمن للتكرار."""
    conn = connect()
    if not using_turso():
        for pragma in ("PRAGMA journal_mode = WAL", "PRAGMA foreign_keys = ON"):
            try:
                conn.execute(pragma)
            except Exception:
                pass
    conn.executescript(SCHEMA)
    migrate(conn)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    # أوقات الحصص الافتراضية إن لم تُضبط بعد
    n = conn.execute("SELECT COUNT(*) c FROM time_slots").fetchone()["c"]
    if n == 0:
        for p in range(1, 8):
            conn.execute(
                "INSERT INTO time_slots(period_number, start_time, end_time, is_break, name) "
                "VALUES (?, '', '', 0, ?)",
                (p, "الحصة %d" % p),
            )
    # نسخة افتراضية
    n = conn.execute("SELECT COUNT(*) c FROM versions").fetchone()["c"]
    if n == 0:
        conn.execute(
            "INSERT INTO versions(name, is_active, created_at) VALUES (?, 1, ?)",
            ("النسخة الأولى", datetime.now().isoformat(timespec="seconds")),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- settings

def get_settings(conn=None):
    own = conn is None
    conn = conn or connect()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    s = {r["key"]: r["value"] for r in rows}
    if own:
        conn.close()
    out = dict(DEFAULT_SETTINGS)
    out.update(s)
    return out


def set_setting(key, value, conn=None):
    own = conn is None
    conn = conn or connect()
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    if own:
        conn.commit()
        conn.close()


def working_days(settings):
    raw = (settings.get("working_days") or "").strip()
    if not raw:
        return [0, 1, 2, 3, 4]
    return [int(x) for x in raw.split(",") if x.strip() != ""]


def periods_for_day(settings, day):
    """عدد الحصص في يوم معيّن (مع احترام التجاوز لكل يوم)."""
    try:
        per_day = json.loads(settings.get("periods_per_day_map") or "{}")
    except (ValueError, TypeError):
        per_day = {}
    if str(day) in per_day:
        try:
            return int(per_day[str(day)])
        except (ValueError, TypeError):
            pass
    try:
        return int(settings.get("periods_per_day") or 7)
    except (ValueError, TypeError):
        return 7


def periods_for_grade_day(settings, grade_id, day):
    """عدد الحصص لصف معيّن في يوم معيّن (تجاوز الصف ثم تجاوز اليوم)."""
    base = periods_for_day(settings, day)
    try:
        gmap = json.loads(settings.get("grade_periods_map") or "{}")
    except (ValueError, TypeError):
        gmap = {}
    if str(grade_id) in gmap:
        try:
            return min(int(gmap[str(grade_id)]), base)
        except (ValueError, TypeError):
            pass
    return base


def day_period_limits(settings):
    """{day: عدد الحصص} لكل يوم دوام - ما تعرضه الشبكة وتمنع ما بعده."""
    return {d: periods_for_day(settings, d) for d in working_days(settings)}


def break_periods(conn=None):
    """أرقام الحصص المعلَّمة كفسحة - لا يُوضع فيها درس."""
    own = conn is None
    conn = conn or connect()
    rows = conn.execute(
        "SELECT period_number FROM time_slots WHERE is_break = 1"
    ).fetchall()
    if own:
        conn.close()
    return {r["period_number"] for r in rows}


# ---------------------------------------------------------------- versions

def active_version(conn=None):
    own = conn is None
    conn = conn or connect()
    row = conn.execute(
        "SELECT * FROM versions WHERE is_active = 1 ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM versions ORDER BY id LIMIT 1").fetchone()
    if own:
        conn.close()
    return row


def set_active_version(version_id, conn=None):
    own = conn is None
    conn = conn or connect()
    conn.execute("UPDATE versions SET is_active = 0")
    conn.execute("UPDATE versions SET is_active = 1 WHERE id = ?", (version_id,))
    if own:
        conn.commit()
        conn.close()


# ---------------------------------------------------------------- history

SNAPSHOT_TABLES = ["schedule"]


def save_snapshot(version_id, summary, conn=None):
    """لقطة كاملة لجدول النسخة قبل أي عملية تغيّرها."""
    own = conn is None
    conn = conn or connect()
    rows = conn.execute(
        "SELECT * FROM schedule WHERE version_id = ?", (version_id,)
    ).fetchall()
    payload = json.dumps([dict(r) for r in rows], ensure_ascii=False)
    nxt = conn.execute(
        "SELECT COALESCE(MAX(number), 0) + 1 n FROM history WHERE version_id = ?",
        (version_id,),
    ).fetchone()["n"]
    conn.execute(
        "INSERT INTO history(version_id, number, snapshot_json, change_summary, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (version_id, nxt, payload, summary,
         datetime.now().isoformat(timespec="seconds")),
    )
    # أبقِ آخر 40 لقطة فقط
    conn.execute(
        "DELETE FROM history WHERE version_id = ? AND number <= ?",
        (version_id, nxt - 40),
    )
    if own:
        conn.commit()
        conn.close()
    return nxt


def restore_snapshot(history_id, conn=None):
    """يسترجع لقطة - ويحفظ لقطة قبلها، فحتى التراجع قابل للتراجع."""
    own = conn is None
    conn = conn or connect()
    row = conn.execute("SELECT * FROM history WHERE id = ?", (history_id,)).fetchone()
    if row is None:
        if own:
            conn.close()
        return False
    vid = row["version_id"]
    save_snapshot(vid, "قبل استرجاع اللقطة رقم %d" % row["number"], conn=conn)
    conn.execute("DELETE FROM schedule WHERE version_id = ?", (vid,))
    cols = ["id", "version_id", "section_id", "subject_id", "teacher_id",
            "assignment_id", "room_id", "day", "period_number",
            "merge_group_id", "adjacency_group_id", "co_group_id", "is_pinned"]
    rows = [tuple(e.get(c) for c in cols)
            for e in json.loads(row["snapshot_json"])]
    insert_many(conn, "INSERT INTO schedule(%s) VALUES" % ", ".join(cols), rows)
    if own:
        conn.commit()
        conn.close()
    return True
