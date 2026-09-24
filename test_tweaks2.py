# -*- coding: utf-8 -*-
"""
اختبار التعديلات الستّة الجديدة — ويتحقّق أن بيانات المستخدم لم تُمسّ.
"""
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import testkit  # noqa: F401  (يعزل الاختبار عن بياناتك)
import db
import app as webapp

FAILED = []


def ok(label, cond, extra=""):
    print("  %-56s %s%s" % (label, "✓" if cond else "!! فشل",
                            ("  " + extra) if extra else ""))
    if not cond:
        FAILED.append(label)


def main():
    db.init_db()
    webapp.app.config["TESTING"] = True
    c = webapp.app.test_client()
    conn = db.connect()

    before = {t: conn.execute("SELECT COUNT(*) n FROM %s" % t).fetchone()["n"]
              for t in ("stages", "grades", "sections", "subjects", "teachers",
                        "assignments", "schedule")}
    print("البيانات قبل الاختبار:", before, "\n")

    # ===================================================== 1) حصص الأيام حيّة
    print("=== 1) عدد الحصص الافتراضي في خانات الأيام ===")
    page = c.get("/settings").get_data(as_text=True)
    ok("خانة الافتراضي لها معرّف يربطها بالجافاسكربت", 'id="defaultPeriods"' in page)
    ok("خانات الأيام تحمل صنف التحديث الحيّ",
       page.count('class="pdinput"') >= 5, "%d خانة" % page.count('class="pdinput"'))
    ok("لكل يوم سطر ملاحظة يتغيّر فوراً", 'class="hint pdnote"' in page)
    ok("الأيام غير العاملة تظهر معطّلة", 'daybox' in page)

    # =============================================== 2) اسم الصف مع المرحلة
    print("\n=== 2) اسم الصف يحمل مرحلته، والصف فصل بذاته ===")
    ok("«اول» + «ثانوي» = «اول ثانوي»",
       db.grade_display_name("اول", "ثانوي") == "اول ثانوي")
    ok("لا تكرار إن كان الاسم يحمل المرحلة",
       db.grade_display_name("أول ثانوي", "ثانوي عام") == "أول ثانوي")
    ok("لا تكرار مع مرحلة مركّبة",
       db.grade_display_name("أول ثنائي لغة", "ابتدائي ثنائي لغة") == "أول ثنائي لغة")

    # اسمان بلا كلمة مشتركة، ليظهر أثر إضافة المرحلة
    st_id = conn.execute(
        "INSERT INTO stages(name, sort_order) VALUES ('تجريبي', 99)").lastrowid
    conn.commit()
    c.post("/structure/grade", data={"stage_id": st_id, "names": "سابع"})
    g = conn.execute("SELECT * FROM grades WHERE stage_id = ?", (st_id,)).fetchone()
    ok("الصف حُفظ باسمه الكامل مع مرحلته",
       g and g["name"] == "سابع تجريبي", g["name"] if g else "")
    secs = conn.execute("SELECT * FROM sections WHERE grade_id = ?", (g["id"],)).fetchall()
    ok("الصف بلا شعب يُحسب فصلاً واحداً تلقائياً",
       len(secs) == 1 and secs[0]["is_default"] == 1)

    label = conn.execute(
        "SELECT " + db.SECTION_LABEL_SQL + " lbl FROM sections se "
        "JOIN grades g ON g.id = se.grade_id WHERE se.id = ?",
        (secs[0]["id"],)).fetchone()["lbl"]
    ok("اسم الفصل = اسم الصف (بلا شرطة شعبة)", label == g["name"], label)

    # أضف شعبتين: الأولى تحلّ محلّ الضمنية فلا يصير العدد ثلاثة
    c.post("/structure/section", data={"grade_id": g["id"], "names": "أ\nب",
                                       "student_count": 20})
    secs = conn.execute("SELECT * FROM sections WHERE grade_id = ? ORDER BY id",
                        (g["id"],)).fetchall()
    ok("شعبتان تعطيان فصلين لا ثلاثة", len(secs) == 2, "%d" % len(secs))
    ok("لم تبقَ شعبة ضمنية", all(not x["is_default"] for x in secs))
    lbl = conn.execute(
        "SELECT " + db.SECTION_LABEL_SQL + " lbl FROM sections se "
        "JOIN grades g ON g.id = se.grade_id WHERE se.id = ?",
        (secs[0]["id"],)).fetchone()["lbl"]
    ok("اسم الفصل المقسّم يحمل الشعبة", " / " in lbl, lbl)

    # حذف كل الشعب يعيد الصف فصلاً واحداً
    for x in secs:
        c.post("/structure/delete/sections/%d" % x["id"])
    secs = conn.execute("SELECT * FROM sections WHERE grade_id = ?", (g["id"],)).fetchall()
    ok("حذف كل الشعب يعيد الصف فصلاً واحداً",
       len(secs) == 1 and secs[0]["is_default"] == 1)

    c.post("/structure/delete/stages/%d" % st_id)          # تنظيف

    # ========================================== 3) المواد: صف + حصص + معلمون
    print("\n=== 3) شاشة المواد ===")
    n0 = conn.execute("SELECT COUNT(*) n FROM subjects").fetchone()["n"]
    c.post("/subjects/row", data={"count": 2})
    n1 = conn.execute("SELECT COUNT(*) n FROM subjects").fetchone()["n"]
    ok("إضافة صفوف فارغة", n1 == n0 + 2, "%d ← %d" % (n0, n1))

    blanks = [r["id"] for r in conn.execute("SELECT id FROM subjects WHERE TRIM(name)=''")]
    tids = [r["id"] for r in conn.execute("SELECT id FROM teachers LIMIT 2")]
    r = c.post("/subjects/save", json={"rows": [
        {"id": blanks[0], "name": "مادة اختبار", "short_name": "اختبار",
         "default_periods": 5, "color_index": 3, "teachers": tids}]})
    ok("الحفظ يرجع JSON بلا إعادة تحميل", r.get_json().get("ok") is True,
       r.get_json().get("message", ""))

    row = conn.execute("SELECT * FROM subjects WHERE id = ?", (blanks[0],)).fetchone()
    ok("عدد الحصص حُفظ في المادة", row and row["default_periods"] == 5)
    links = [x["teacher_id"] for x in conn.execute(
        "SELECT teacher_id FROM subject_teachers WHERE subject_id = ?", (blanks[0],))]
    ok("أكثر من معلم لنفس المادة", sorted(links) == sorted(tids), str(links))
    left = conn.execute("SELECT COUNT(*) n FROM subjects WHERE TRIM(name)=''").fetchone()["n"]
    ok("الصف بلا اسم يُحذف عند الحفظ", left == 0)

    # ============================================= 4) الإسناد بالأيقونات
    print("\n=== 4) شاشات الإسناد ===")
    hub = c.get("/assignments").get_data(as_text=True)
    ok("لوحة الأيقونات تعرض الأربع",
       hub.count("iconcard") >= 4, "%d أيقونة" % hub.count("iconcard"))
    for path in ("/assignments/teacher", "/assignments/list",
                 "/assignments/balance", "/assignments/rooms"):
        ok("شاشة مستقلة %s" % path, c.get(path).status_code == 200)

    tid = tids[0]
    subs = c.get("/api/teacher/%d/subjects" % tid).get_json()
    linked = {x["subject_id"] for x in conn.execute(
        "SELECT subject_id FROM subject_teachers WHERE teacher_id = ?", (tid,))}
    ok("قائمة المواد تعرض مواد هذا المعلم فقط",
       {x["id"] for x in subs} == linked, "%d مادة" % len(subs))

    if subs:
        sub = subs[0]["id"]
        d = c.get("/api/teacher/%d/subject/%d/sections" % (tid, sub)).get_json()
        ok("الفصول تظهر تلقائياً عند اختيار المادة", len(d["sections"]) > 0,
           "%d فصل" % len(d["sections"]))
        ok("لكل فصل خانة عدد حصص مستقلة",
           all("periods" in x for x in d["sections"]))
        ok("عدد الحصص المقترح من نصاب المادة",
           d["default_periods"] == (subs[0]["default_periods"] or 0))

        # لا يُسمح بتجاوز نصاب المادة
        cap = d["default_periods"]
        if cap:
            two = d["sections"][:2]
            r = c.post("/api/assign/save", json={
                "teacher_id": tid, "subject_id": sub,
                "sections": [{"id": two[0]["id"], "periods": cap + 5,
                              "label": two[0]["label"]}]}).get_json()
            saved = conn.execute(
                "SELECT periods_per_week p FROM assignments WHERE teacher_id=? "
                "AND subject_id=? AND section_id=?", (tid, sub, two[0]["id"])).fetchone()
            # النصاب مقترح لا سقف: تُحفظ القيمة كما أُدخلت مع تنبيه
            ok("القيمة الأكبر من المقترح تُحفظ بلا اقتطاع",
               saved and saved["p"] == cap + 5,
               "طُلب %d فحُفظ %d" % (cap + 5, saved["p"]))
            ok("ويُنبَّه المستخدم على الاختلاف", bool(r.get("over")),
               (r.get("message") or "")[:60])

            # فصول مختلفة بأعداد مختلفة
            if len(two) == 2:
                c.post("/api/assign/save", json={
                    "teacher_id": tid, "subject_id": sub, "sections": [
                        {"id": two[0]["id"], "periods": max(1, cap - 1)},
                        {"id": two[1]["id"], "periods": cap}]})
                got = {r2["section_id"]: r2["periods_per_week"] for r2 in conn.execute(
                    "SELECT section_id, periods_per_week FROM assignments "
                    "WHERE teacher_id=? AND subject_id=?", (tid, sub))}
                ok("كل فصل بعدد حصص مختلف",
                   got.get(two[0]["id"]) == max(1, cap - 1) and got.get(two[1]["id"]) == cap,
                   str(got))

            # إلغاء التحديد يحذف الإسناد
            c.post("/api/assign/save", json={"teacher_id": tid, "subject_id": sub,
                                             "sections": []})
            n = conn.execute("SELECT COUNT(*) n FROM assignments WHERE teacher_id=? "
                             "AND subject_id=?", (tid, sub)).fetchone()["n"]
            ok("إلغاء التحديد يلغي الإسناد", n == 0)

    r = c.post("/assignments/required", json={"rows": []}).get_json()
    ok("حفظ المطلوب يرجع JSON (بلا قفز لأعلى)", r.get("ok") is True)
    r = c.post("/api/assign/bulk", json={"rows": []}).get_json()
    ok("حفظ الكل في جدول الإسنادات يرجع JSON", r.get("ok") is True)

    # ================================= 5) ملء الخانات الفارغة من الجدول
    print("\n=== 5) ملء الخانة الفارغة من الجدول العام ===")
    grid = c.get("/grid?view=sections").get_data(as_text=True)
    ok("نافذة الإضافة موجودة في الصفحة", 'id="addModal"' in grid)
    ok("قائمة المادة وقائمة ثانية", 'id="addSubject"' in grid and 'id="addSecond"' in grid)

    sec = conn.execute(
        "SELECT section_id FROM assignments GROUP BY section_id LIMIT 1").fetchone()
    if sec:
        d = c.get("/api/cell-options?view=sections&owner=%d" % sec["section_id"]).get_json()
        ok("خيارات الفصل مبنية على إسناداته", len(d["options"]) > 0,
           "%d خيار" % len(d["options"]))
        subj_ids = {o["subject_id"] for o in d["options"]}
        real = {r2["subject_id"] for r2 in conn.execute(
            "SELECT subject_id FROM assignments WHERE section_id=?", (sec["section_id"],))}
        ok("لا تظهر مواد غير مُسندة لهذا الفصل", subj_ids == real)
        ok("يُعرض المتبقي من النصاب",
           all("left" in o and "total" in o for o in d["options"]))

    tea = conn.execute(
        "SELECT teacher_id FROM assignments GROUP BY teacher_id LIMIT 1").fetchone()
    if tea:
        d = c.get("/api/cell-options?view=teachers&owner=%d" % tea["teacher_id"]).get_json()
        real = {r2["section_id"] for r2 in conn.execute(
            "SELECT section_id FROM assignments WHERE teacher_id=?", (tea["teacher_id"],))}
        ok("عرض المعلم يظهر فصوله هو فقط",
           {o["section_id"] for o in d["options"]} == real)

    # ============================================ 6) اسم رئيسة الإشراف
    print("\n=== 6) اسم رئيسة الإشراف التعليمي ===")
    ok("في الإعدادات", "رئيسة الإشراف التعليمي" in
       c.get("/settings").get_data(as_text=True))
    ok("في الطباعة", "رئيسة الإشراف التعليمي" in
       c.get("/print?kind=sections&sig=1").get_data(as_text=True))
    import exports
    src = open("exports.py", encoding="utf-8").read()
    ok("في ملف PDF", "رئيسة الإشراف التعليمي" in src)
    ok("لم يبقَ اللفظ القديم",
       "المشرفة التعليمية" not in c.get("/settings").get_data(as_text=True))

    # ==================================================== سلامة البيانات
    print("\n=== سلامة بيانات المستخدم ===")
    conn.execute("DELETE FROM subjects WHERE name = 'مادة اختبار'")
    conn.commit()
    after = {t: conn.execute("SELECT COUNT(*) n FROM %s" % t).fetchone()["n"]
             for t in before}
    for t in ("stages", "grades", "sections", "teachers", "subjects"):
        ok("%s كما كانت" % t, after[t] == before[t],
           "%d ← %d" % (before[t], after[t]))
    ok("جدول الحصص لم يُمسّ", after["schedule"] == before["schedule"],
       "%d ← %d" % (before["schedule"], after["schedule"]))

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
