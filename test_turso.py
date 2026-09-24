# -*- coding: utf-8 -*-
"""
اختبار مسار Turso (libSQL) — نفس الشيفرة التي ستعمل على الإنترنت،
لكن على ملف محلي، فنتحقّق من الطبقة كاملة بلا حساب ولا شبكة.

  python test_turso.py
"""
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

# لا بدّ من ضبط المتغيّر قبل استيراد db
_REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "school.db")
_TMP = os.path.join(tempfile.gettempdir(), "turso_test_%d.db" % os.getpid())
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(_TMP + suffix)
    except OSError:
        pass
if os.path.exists(_REAL):
    shutil.copy2(_REAL, _TMP)

os.environ["TURSO_DATABASE_URL"] = _TMP
os.environ.pop("TURSO_AUTH_TOKEN", None)

import db          # noqa: E402
import doctor      # noqa: E402
import exports     # noqa: E402
import solver      # noqa: E402
import app as webapp   # noqa: E402

FAILED = []


def ok(label, cond, extra=""):
    print("  %-52s %s%s" % (label, "✓" if cond else "!! فشل",
                            ("  " + extra) if extra else ""))
    if not cond:
        FAILED.append(label)


def cleanup():
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(_TMP + suffix)
        except OSError:
            pass


def main():
    print("قاعدة libSQL مؤقّتة: %s\n" % os.path.basename(_TMP))
    ok("النظام يستعمل مسار Turso", db.using_turso())

    db.init_db()
    conn = db.connect()
    ok("نوع الاتصال صحيح", type(conn).__name__ == "TursoConn",
       type(conn).__name__)

    # ---------------------------------------------- طبقة الصفوف
    print("\n=== طبقة الصفوف ===")
    r = conn.execute("SELECT id, name FROM teachers ORDER BY id LIMIT 1").fetchone()
    ok("القراءة بالاسم", r is not None and isinstance(r["name"], str), r["name"])
    ok("القراءة بالرقم", r[0] == r["id"])
    d = dict(r)
    ok("dict(row) يعمل", set(d.keys()) == {"id", "name"}, str(list(d.keys())))
    ok("keys() يعمل", r.keys() == ["id", "name"])
    rows = conn.execute("SELECT id FROM teachers").fetchall()
    ok("fetchall يعيد قائمة", isinstance(rows, list) and len(rows) > 0,
       "%d صف" % len(rows))
    n = 0
    for _ in conn.execute("SELECT id FROM subjects"):
        n += 1
    ok("التكرار على المؤشّر", n > 0, "%d مادة" % n)

    cur = conn.execute("INSERT INTO stages(name, sort_order) VALUES (?, ?)",
                       ("مرحلة turso", 99))
    ok("lastrowid يعمل", isinstance(cur.lastrowid, int) and cur.lastrowid > 0,
       str(cur.lastrowid))
    sid = cur.lastrowid
    cur = conn.execute("UPDATE stages SET name = ? WHERE id = ?", ("م2", sid))
    ok("rowcount يعمل", cur.rowcount == 1, str(cur.rowcount))
    conn.execute("DELETE FROM stages WHERE id = ?", (sid,))
    conn.commit()

    # ---------------------------------------------- الترحيل والمخطّط
    print("\n=== المخطّط والترحيل ===")
    cols = {x["name"] for x in conn.execute("PRAGMA table_info(sections)")}
    ok("PRAGMA table_info يعمل", "required_periods" in cols and "is_default" in cols,
       "%d عمود" % len(cols))
    tabs = {x["name"] for x in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    ok("كل الجداول موجودة",
       {"schedule", "assignments", "teachers", "subject_teachers"} <= tabs,
       "%d جدول" % len(tabs))

    # ---------------------------------------------- البيانات محفوظة
    print("\n=== سلامة البيانات ===")
    counts = {}
    for t in ("stages", "grades", "sections", "subjects", "teachers",
              "assignments", "schedule"):
        counts[t] = conn.execute("SELECT COUNT(*) c FROM %s" % t).fetchone()["c"]
    print("   ", counts)
    ok("البيانات منقولة", counts["teachers"] > 0 and counts["assignments"] > 0)

    # ---------------------------------------------- الإدراج الجماعي
    print("\n=== الإدراج الجماعي ===")
    vid = db.active_version(conn)["id"]
    before = conn.execute("SELECT COUNT(*) c FROM schedule WHERE version_id=?",
                          (vid,)).fetchone()["c"]
    row = conn.execute("SELECT * FROM schedule WHERE version_id=? LIMIT 1",
                       (vid,)).fetchone()
    if row:
        extra = [(vid, row["section_id"], row["subject_id"], row["teacher_id"],
                  None, None, 6, 99 + k, None, None, None, 0) for k in range(250)]
        db.insert_many(conn,
                       "INSERT INTO schedule(version_id, section_id, subject_id, "
                       "teacher_id, assignment_id, room_id, day, period_number, "
                       "merge_group_id, adjacency_group_id, co_group_id, is_pinned) "
                       "VALUES", extra)
        conn.commit()
        after = conn.execute("SELECT COUNT(*) c FROM schedule WHERE version_id=?",
                             (vid,)).fetchone()["c"]
        ok("250 صفاً في دفعات", after == before + 250, "%d ← %d" % (before, after))
        conn.execute("DELETE FROM schedule WHERE version_id=? AND period_number>=99",
                     (vid,))
        conn.commit()

    # ---------------------------------------------- اللقطات والتراجع
    print("\n=== اللقطات ===")
    num = db.save_snapshot(vid, "اختبار turso", conn=conn)
    conn.commit()
    ok("حفظ لقطة", isinstance(num, int) and num > 0, "رقم %s" % num)
    h = conn.execute("SELECT id FROM history WHERE version_id=? ORDER BY id DESC "
                     "LIMIT 1", (vid,)).fetchone()
    n_before = conn.execute("SELECT COUNT(*) c FROM schedule WHERE version_id=?",
                            (vid,)).fetchone()["c"]
    conn.close()
    ok("استرجاع لقطة", db.restore_snapshot(h["id"]) is True)
    conn = db.connect()
    n_after = conn.execute("SELECT COUNT(*) c FROM schedule WHERE version_id=?",
                           (vid,)).fetchone()["c"]
    ok("الجدول كما كان بعد الاسترجاع", n_after == n_before,
       "%d ← %d" % (n_before, n_after))

    # ---------------------------------------------- المحرّك والفحص
    print("\n=== المحرّك والفحص ===")
    data = solver.Data(conn, vid)
    ok("المحرّك يقرأ البيانات", len(data.lessons) > 0, "%d وحدة" % len(data.lessons))
    errs, warns = solver.check_feasibility(data)
    ok("فحص الجدوى يعمل", isinstance(errs, list) and isinstance(warns, list),
       "%d خطأ · %d تنبيه" % (len(errs), len(warns)))
    res = doctor.run_checks(conn, vid)
    ok("فحص السلامة يعمل", isinstance(res, list),
       "%d نتيجة" % len(res))
    conn.close()

    # ---------------------------------------------- الواجهة كاملة
    print("\n=== الصفحات عبر Turso ===")
    webapp.app.config["TESTING"] = True
    c = webapp.app.test_client()
    for p in ("/", "/settings", "/structure", "/subjects", "/teachers",
              "/assignments", "/assignments/teacher", "/assignments/list",
              "/assignments/balance", "/assignments/rooms", "/generate",
              "/grid?view=sections", "/grid?view=teachers",
              "/grid?view=sections&layout=combined", "/history", "/doctor",
              "/print?kind=sections"):
        ok("GET %s" % p[:40], c.get(p).status_code == 200)

    print("\n=== التصدير عبر Turso ===")
    r = c.get("/export/excel?kind=sections")
    ok("Excel", r.status_code == 200 and len(r.data) > 3000, "%d بايت" % len(r.data))
    r = c.get("/export/pdf?kind=sections&sig=1")
    ok("PDF", r.status_code == 200 and r.data[:5] == b"%PDF-", "%d بايت" % len(r.data))
    r = c.get("/export/pdf?kind=sections&layout=combined")
    ok("PDF مجمّع", r.status_code == 200 and r.data[:5] == b"%PDF-")
    r = c.get("/export/backup")
    ok("نسخة احتياطية JSON", r.status_code == 200 and len(r.data) > 1000)

    print("\n=== التوليد عبر Turso ===")
    r = c.post("/generate/run", json={
        "options": {"time_limit_seconds": 12, "restarts": 2}}).get_json()
    if r.get("ok"):
        ok("التوليد يعمل ويكتب", r["placed"] > 0,
           "وُضع %d من %d" % (r["placed"], r["total"]))
        ok("العدّ متّسق", r["placed"] + len(r["unplaced"]) == r["total"])
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) c FROM schedule WHERE version_id=?",
                         (vid,)).fetchone()["c"]
        conn.close()
        ok("الحصص محفوظة فعلاً", n == r["placed"], "%d خلية" % n)
    else:
        ok("التوليد يعمل", bool(r.get("skippable")), str(r)[:70])

    print("\n=== التعديل اليدوي عبر Turso ===")
    conn = db.connect()
    two = conn.execute("SELECT * FROM schedule WHERE version_id=? LIMIT 2",
                       (vid,)).fetchall()
    conn.close()
    if len(two) == 2:
        ok("تثبيت", c.post("/grid/cell", json={
            "action": "pin", "id": two[0]["id"], "pinned": True}).status_code == 200)
        ok("تبديل", c.post("/grid/cell", json={
            "action": "swap", "id": two[0]["id"],
            "other": two[1]["id"]}).status_code == 200)
        ok("حذف", c.post("/grid/cell", json={
            "action": "delete", "id": two[1]["id"]}).status_code == 200)
    ok("الحل التلقائي", c.post("/grid/autofix", json={}).status_code == 200)

    print("\n" + "=" * 60)
    if FAILED:
        print("فشل %d:" % len(FAILED))
        for f in FAILED:
            print("  -", f)
        return 1
    print("مسار Turso يعمل بالكامل ✓")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        cleanup()
    sys.exit(code)
