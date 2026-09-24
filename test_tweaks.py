# -*- coding: utf-8 -*-
"""
اختبار التعديلات الأربعة:
  1) جدول المعلمين القابل للتحرير (إضافة صف + حفظ الكل)
  2) عدد حصص مخصّص لكل يوم، وإلزامه في الشبكة والتعديل اليدوي
  3) المطلوب لكل شعبة قابل للتعديل، والمُدرج يُحسب من الجدول
  4) الحل التلقائي للتعارض، ومنع التبديل المتعارض، وفحص التوليد
"""
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import testkit  # noqa: F401  (يعزل الاختبار عن بياناتك)
import db
import edits
import solver
import app as webapp

FAILED = []


def ok(label, cond, extra=""):
    print("  %-58s %s%s" % (label, "✓" if cond else "!! فشل",
                            ("  " + extra) if extra else ""))
    if not cond:
        FAILED.append(label)


def main():
    db.init_db()
    webapp.app.config["TESTING"] = True
    c = webapp.app.test_client()
    conn = db.connect()
    vid = db.active_version(conn)["id"]

    # ============================================================ (1) المعلمون
    print("\n=== 1) جدول المعلمين القابل للتحرير ===")
    before = conn.execute("SELECT COUNT(*) n FROM teachers").fetchone()["n"]
    c.post("/teachers/row", data={"count": 3})
    after = conn.execute("SELECT COUNT(*) n FROM teachers").fetchone()["n"]
    ok("إضافة 3 صفوف فارغة", after == before + 3, "%d ← %d" % (before, after))

    blanks = [r["id"] for r in conn.execute(
        "SELECT id FROM teachers WHERE TRIM(name) = ''")]
    ok("الصفوف الجديدة فارغة", len(blanks) == 3)

    # املأ واحداً فقط واترك اثنين فارغين
    form = {"row_id": [str(x) for x in blanks]}
    form["name_%d" % blanks[0]] = "المعلمة نورة"
    form["specialization_%d" % blanks[0]] = "رياضيات"
    form["phone_%d" % blanks[0]] = "0500000000"
    form["max_periods_per_week_%d" % blanks[0]] = "22"
    form["max_periods_per_day_%d" % blanks[0]] = "5"
    c.post("/teachers/save", data=form)

    row = conn.execute("SELECT * FROM teachers WHERE id = ?", (blanks[0],)).fetchone()
    ok("حُفظت كل خانات الصف", row and row["name"] == "المعلمة نورة"
       and row["specialization"] == "رياضيات" and row["max_periods_per_week"] == 22
       and row["max_periods_per_day"] == 5)
    left = conn.execute("SELECT COUNT(*) n FROM teachers WHERE TRIM(name)=''").fetchone()["n"]
    ok("الصفوف التي بقيت بلا اسم حُذفت", left == 0)
    conn.execute("DELETE FROM teachers WHERE id = ?", (blanks[0],))
    conn.commit()

    # ================================================= (2) حصص مخصّصة لكل يوم
    print("\n=== 2) عدد حصص مخصّص لكل يوم ===")
    st = db.get_settings(conn)
    days = db.working_days(st)
    default_n = int(st["periods_per_day"])
    thursday = 4 if 4 in days else days[-1]
    short = default_n - 1

    db.set_setting("periods_per_day_map", json.dumps({str(thursday): short}), conn)
    conn.commit()

    st = db.get_settings(conn)
    ok("%s صار %d حصص والباقي %d"
       % (db.DAY_NAMES[thursday], short, default_n),
       db.periods_for_day(st, thursday) == short
       and db.periods_for_day(st, days[0]) == default_n)

    limits = db.day_period_limits(st)
    ok("خريطة الحدود صحيحة", limits[thursday] == short)

    # المحرّك لا يولّد بعد الحد
    data = solver.Data(conn, vid)
    sid = next(iter(data.sections))
    bad = [(d, p) for (d, p) in data.section_slots[sid] if d == thursday and p > short]
    ok("المحرّك لا يرى خانات بعد الحد", not bad)

    # التعديل اليدوي مرفوض بعد الحد
    ctx = edits.Ctx(conn, vid)
    allowed, why = ctx.slot_allowed(sid, thursday, default_n)
    ok("الخانة %d في %s مرفوضة" % (default_n, db.DAY_NAMES[thursday]),
       not allowed, why)

    row = conn.execute(
        "SELECT * FROM schedule WHERE version_id=? AND section_id=? LIMIT 1",
        (vid, sid)).fetchone()
    if row:
        r = c.post("/grid/cell", json={"action": "move", "id": row["id"],
                                       "day": thursday, "period": default_n})
        d = r.get_json()
        ok("نقل حصة إلى خانة محظورة مرفوض", d.get("ok") is False,
           d.get("reason", ""))

        r = c.post("/grid/cell", json={
            "action": "add", "section_id": sid, "subject_id": row["subject_id"],
            "teacher_id": row["teacher_id"], "day": thursday, "period": default_n})
        ok("إضافة حصة في خانة محظورة مرفوضة", r.get_json().get("ok") is False)

    # ================================================ (3) المطلوب لكل شعبة
    print("\n=== 3) المطلوب مقابل الإسناد ===")
    r = c.post("/assignments/required",
               data={"section_id": [str(sid)], "req_%d" % sid: "30"})
    val = conn.execute("SELECT required_periods FROM sections WHERE id=?",
                       (sid,)).fetchone()["required_periods"]
    ok("حُفظ المطلوب = 30", val == 30)

    page = c.get("/assignments/balance").get_data(as_text=True)
    ok("شاشة الميزان تعرض عمود المُدرج", "المُدرج" in page and "المطلوب" in page)

    scheduled = conn.execute(
        "SELECT COUNT(*) n FROM schedule WHERE version_id=? AND section_id=?",
        (vid, sid)).fetchone()["n"]
    ok("المُدرج يُحسب من الجدول العام", str(scheduled) in page, "=%d" % scheduled)

    data = solver.Data(conn, vid)
    errs, warns = solver.check_feasibility(data)
    ok("فحص الجدوى يستعمل المطلوب",
       any("المطلوب" in x for x in errs + warns))

    c.post("/assignments/required",
           data={"section_id": [str(sid)], "req_%d" % sid: ""})
    val = conn.execute("SELECT required_periods FROM sections WHERE id=?",
                       (sid,)).fetchone()["required_periods"]
    ok("الفارغ يرجع للحساب التلقائي", val == 0)

    # ============================================ (4) التعارض والحل التلقائي
    print("\n=== 4) التعارض والحل التلقائي ===")

    # ضع حصة عمداً في خانة صارت خارج الدوام (تجاوز التحقق، مباشرةً في القاعدة)
    stray = conn.execute(
        "SELECT * FROM schedule WHERE version_id=? AND is_pinned=0 LIMIT 1",
        (vid,)).fetchone()
    conn.execute("UPDATE schedule SET day=?, period_number=? WHERE id=?",
                 (thursday, default_n, stray["id"]))
    conn.commit()

    ctx = edits.Ctx(conn, vid)
    orphans = [x for x in edits.conflicts(ctx) if x["type"] == "out_of_range"]
    ok("الحصة الواقعة خارج حدود اليوم اكتُشفت", len(orphans) > 0,
       "%d حصة" % len(orphans))
    r = c.post("/grid/autofix", json={}).get_json()
    ok("الحصص التي تعذّر نقلها مذكورة بسببها (لا تختفي بصمت)",
       len(r.get("remaining", [])) <= len(r.get("failed", [])) + len(r.get("moved", [])),
       "نُقل %d · تعذّر %d" % (len(r.get("moved", [])), len(r.get("failed", []))))
    ok("سبب التعذّر مذكور", all(f.get("why") for f in r.get("failed", [])))

    # أعِد الحدود لطبيعتها ثم اختبر الحل التلقائي حيث توجد مساحة فعلاً
    db.set_setting("periods_per_day_map", "{}", conn)
    conn.commit()
    c.post("/grid/autofix", json={})          # نظّف ما تبقّى

    two = conn.execute(
        "SELECT * FROM schedule WHERE version_id=? AND is_pinned=0 LIMIT 2",
        (vid,)).fetchall()
    if len(two) == 2:
        a, b = two
        # اصنع تعارض معلم يدوياً: ننقل b إلى خانة a ونعطيه معلّم a
        conn.execute("UPDATE schedule SET teacher_id=?, day=?, period_number=? WHERE id=?",
                     (a["teacher_id"], a["day"], a["period_number"], b["id"]))
        conn.commit()

        ctx = edits.Ctx(conn, vid)
        found = [x for x in edits.conflicts(ctx) if x["type"] == "teacher"]
        ok("التعارض المصطنع اكتُشف", len(found) >= 1)

        before = len(edits.conflicts(ctx))
        r = c.post("/grid/autofix", json={}).get_json()
        after = len(r.get("remaining", []))
        ok("الحل التلقائي نفّذ نقلاً", r.get("ok") and len(r.get("moved", [])) > 0)
        ok("عدد التعارضات انخفض", after < before, "%d ← %d" % (before, after))

        # كل ما نُقل يحترم حدود اليوم
        ctx = edits.Ctx(conn, vid)
        bad = []
        for m in r.get("moved", []):
            row = ctx.by_id.get(m["id"])
            if row:
                good, _ = ctx.slot_allowed(row["section_id"], row["day"],
                                           row["period_number"])
                if not good:
                    bad.append(m["label"])
        ok("النقل التلقائي احترم حدود الأيام المختصرة", not bad, str(bad[:2]))

    # التبديل المتعارض مرفوض
    ctx = edits.Ctx(conn, vid)
    rejected = tested = 0
    for x in ctx.rows[:40]:
        for y in ctx.rows[:40]:
            if x["id"] >= y["id"]:
                continue
            good, why = edits.validate_swap(ctx, x["id"], y["id"])
            tested += 1
            if not good:
                rejected += 1
    ok("التحقق من التبديل يعمل", tested > 0,
       "فُحص %d زوجاً، رُفض %d" % (tested, rejected))

    # تبديل مرفوض فعلاً لا يُنفَّذ
    victim = None
    for x in ctx.rows:
        for y in ctx.rows:
            if x["id"] < y["id"] and not edits.validate_swap(ctx, x["id"], y["id"])[0]:
                victim = (x, y)
                break
        if victim:
            break
    if victim:
        x, y = victim
        px = (x["day"], x["period_number"])
        r = c.post("/grid/cell", json={"action": "swap", "id": x["id"],
                                       "other": y["id"]}).get_json()
        now = conn.execute("SELECT day, period_number FROM schedule WHERE id=?",
                           (x["id"],)).fetchone()
        ok("التبديل المتعارض مرفوض ولم يُنفَّذ",
           r.get("ok") is False and (now["day"], now["period_number"]) == px,
           r.get("reason", "")[:50])

    # الخانات الآمنة لا تشمل ما بعد حد اليوم
    if ctx.rows:
        e = ctx.rows[0]
        targets = edits.safe_targets(ctx, e["id"])
        bad = [(d, p) for d, p in targets
               if not ctx.slot_allowed(e["section_id"], d, p)[0]]
        ok("الخانات الآمنة كلها ضمن حدود أيامها", not bad,
           "%d خانة آمنة" % len(targets))

    # التوليد يبلّغ عن التعارضات والفراغات
    print("\n=== التوليد مع الفحص ===")
    r = c.post("/generate/run", json={
        "options": {"time_limit_seconds": 8, "restarts": 2}}).get_json()
    if r.get("ok"):
        ok("التوليد يعيد تقرير الفحص",
           "conflicts" in r and "empty" in r and "clean" in r,
           "تعارضات=%d فراغات=%d نظيف=%s"
           % (len(r["conflicts"]), len(r["empty"]), r["clean"]))
        ok("عدّ الحصص متّسق", r["placed"] + len(r["unplaced"]) == r["total"])
    else:
        ok("التوليد يعرض زر التخطّي عند الخطأ", r.get("skippable") is True)
        r2 = c.post("/generate/run", json={
            "options": {"time_limit_seconds": 8, "restarts": 2},
            "skip_checks": True}).get_json()
        ok("التخطّي يولّد فعلاً", r2.get("ok") is True)

    db.set_setting("periods_per_day_map", "{}", conn)
    conn.commit()
    conn.close()

    print("\n" + "=" * 62)
    if FAILED:
        print("فشل %d:" % len(FAILED))
        for f in FAILED:
            print("  -", f)
        return 1
    print("كل التعديلات تعمل ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
