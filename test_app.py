# -*- coding: utf-8 -*-
"""اختبار دخان: يمرّ على كل الشاشات والمسارات ويتحقّق من عدم وجود أخطاء."""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import testkit  # noqa: F401  (يعزل الاختبار عن بياناتك)
import db
import app as webapp

FAILED = []


def check(label, resp, expect=(200,)):
    code = resp.status_code
    ok = code in expect
    if not ok:
        FAILED.append("%s -> %s" % (label, code))
    print("  %-46s %s" % (label, code if ok else "!! %s" % code))
    return resp


def main():
    db.init_db()
    webapp.app.config["TESTING"] = True
    c = webapp.app.test_client()

    print("=== صفحات العرض ===")
    check("GET /", c.get("/"))
    check("GET /settings", c.get("/settings"))
    check("GET /structure", c.get("/structure"))
    check("GET /subjects", c.get("/subjects"))
    check("GET /teachers", c.get("/teachers"))
    check("GET /assignments", c.get("/assignments"))
    check("GET /assignments/teacher", c.get("/assignments/teacher"))
    check("GET /assignments/list", c.get("/assignments/list"))
    check("GET /assignments/balance", c.get("/assignments/balance"))
    check("GET /assignments/rooms", c.get("/assignments/rooms"))
    check("GET /generate", c.get("/generate"))
    check("GET /grid (فصول)", c.get("/grid?view=sections"))
    check("GET /grid (معلمين)", c.get("/grid?view=teachers"))
    check("GET /history", c.get("/history"))
    check("GET /print", c.get("/print?kind=sections&sig=1"))

    conn = db.connect()
    tid = conn.execute("SELECT id FROM teachers LIMIT 1").fetchone()["id"]
    sec = conn.execute("SELECT id FROM sections LIMIT 1").fetchone()["id"]
    sub = conn.execute("SELECT id FROM subjects LIMIT 1").fetchone()["id"]
    conn.close()
    check("GET /teachers/<id>/availability", c.get("/teachers/%d/availability" % tid))

    print("\n=== التصدير ===")
    r = check("GET /export/excel", c.get("/export/excel?kind=sections"))
    print("       حجم الملف: %d بايت" % len(r.data))
    r = check("GET /export/excel (معلمين)", c.get("/export/excel?kind=teachers"))
    r = check("GET /export/pdf", c.get("/export/pdf?kind=sections&sig=1"))
    print("       حجم الملف: %d بايت، يبدأ بـ %r" % (len(r.data), r.data[:5]))
    r = check("GET /export/backup", c.get("/export/backup"))
    print("       حجم الملف: %d بايت" % len(r.data))

    print("\n=== التوليد عبر الويب ===")
    r = check("POST /generate/run", c.post("/generate/run", json={
        "options": {"time_limit_seconds": 8, "restarts": 2},
        "keep_pinned": True}))
    d = r.get_json()
    if d and d.get("ok"):
        print("       وُضع %d/%d · لم يوضع %d · عقوبة %s"
              % (d["placed"], d["total"], len(d["unplaced"]), d["cost"]))
    else:
        FAILED.append("generate/run returned %s" % (d,))
        print("       !!", d)

    print("\n=== التعديل اليدوي ===")
    conn = db.connect()
    vid = db.active_version(conn)["id"]
    row = conn.execute(
        "SELECT * FROM schedule WHERE version_id=? LIMIT 1", (vid,)).fetchone()
    two = conn.execute(
        "SELECT * FROM schedule WHERE version_id=? LIMIT 2", (vid,)).fetchall()
    conn.close()
    if row:
        check("POST تثبيت", c.post("/grid/cell",
              json={"action": "pin", "id": row["id"], "pinned": True}))
        if len(two) == 2:
            check("POST تبديل", c.post("/grid/cell",
                  json={"action": "swap", "id": two[0]["id"], "other": two[1]["id"]}))
        check("POST نقل", c.post("/grid/cell", json={
            "action": "move", "id": row["id"], "day": row["day"],
            "period": row["period_number"]}))
        check("POST حذف", c.post("/grid/cell",
              json={"action": "delete", "id": row["id"]}))
        check("POST إضافة", c.post("/grid/cell", json={
            "action": "add", "section_id": sec, "subject_id": sub,
            "teacher_id": tid, "day": 0, "period": 1}))

    print("\n=== إدخال البيانات ===")
    check("POST صف مادة جديد", c.post("/subjects/row", data={"count": 1}), (200, 302))
    check("POST حفظ المواد", c.post("/subjects/save", json={"rows": [
        {"id": sub, "name": "مادة محفوظة", "short_name": "محفوظة",
         "default_periods": 3, "color_index": 2, "teachers": [tid]}]}))
    check("POST صف معلم جديد", c.post("/teachers/row", data={"count": 2}), (200, 302))
    check("POST حفظ المعلمين", c.post("/teachers/save", data={
        "row_id": [str(tid)], "name_%d" % tid: "معلم محفوظ",
        "specialization_%d" % tid: "عام", "max_periods_per_week_%d" % tid: "20",
        "max_periods_per_day_%d" % tid: "5"}), (200, 302))
    check("POST مرحلة", c.post("/structure/stage", data={"names": "مرحلة اختبار"}),
          (200, 302))
    check("POST عدم الإتاحة", c.post("/teachers/%d/availability" % tid,
          data={"cell": ["0_1", "0_2"]}), (200, 302))
    check("POST نسخة جديدة", c.post("/versions/new",
          data={"name": "نسخة اختبار", "copy_from": 0}), (200, 302))

    print("\n=== السجل والتراجع ===")
    conn = db.connect()
    h = conn.execute("SELECT id FROM history ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if h:
        check("POST استرجاع لقطة", c.post("/history/%d/restore" % h["id"]), (200, 302))

    print("\n" + "=" * 50)
    if FAILED:
        print("فشل %d:" % len(FAILED))
        for f in FAILED:
            print("  -", f)
        return 1
    print("كل المسارات تعمل ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
