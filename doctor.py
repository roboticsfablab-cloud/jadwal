# -*- coding: utf-8 -*-
"""
فحص سلامة النظام — يكشف التناقضات الصامتة قبل أن تُفسد الجدول.

  python doctor.py          فحص وتقرير
  python doctor.py --fix    إصلاح ما يمكن إصلاحه بأمان (بنسخة احتياطية)

الفحوص:
  1  إسنادات لا تطابق الجدول المولَّد (العدد المُسند ≠ الموضوع فعلاً)
  2  خلايا جدول يتيمة: إسنادها محذوف
  3  إسنادات مكرّرة: نفس الفصل والمادة والمعلم مرتين
  4  نفس المادة لنفس الفصل عند معلمتين (قد يكون مقصوداً أو تكراراً)
  5  خلايا خارج الدوام: يوم غير عامل، فسحة، أو بعد حدّ حصص اليوم
  6  تعارض معلمة أو فصل في نفس الخانة
  7  خلايا في خانات عدم إتاحة المعلمة
  8  معلمات تجاوزن نصابهنّ الأسبوعي
  9  فصول المُسند فيها ≠ المطلوب
 10  صفوف بلا فصول، ومواد بلا معلمات
"""
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import db


def run_checks(conn, version_id):
    """يعيد قائمة نتائج: {code, level, title, detail, rows, fixable}"""
    out = []
    st = db.get_settings(conn)
    days = set(db.working_days(st))
    breaks = db.break_periods(conn)
    grade_of = {r["id"]: r["grade_id"] for r in
                conn.execute("SELECT id, grade_id FROM sections")}
    label = {r["id"]: r["lbl"] for r in conn.execute(
        "SELECT se.id, " + db.SECTION_LABEL_SQL + " lbl FROM sections se "
        "JOIN grades g ON g.id = se.grade_id")}
    tname = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM teachers")}

    def add(code, level, title, detail, rows=(), fixable=False):
        out.append({"code": code, "level": level, "title": title,
                    "detail": detail, "rows": list(rows), "fixable": fixable})

    # --- 1) الإسناد مقابل الجدول ------------------------------------
    planned = defaultdict(int)
    for r in conn.execute("SELECT section_id, subject_id, teacher_id, "
                          "periods_per_week FROM assignments"):
        planned[(r["section_id"], r["subject_id"], r["teacher_id"])] += \
            r["periods_per_week"]
    placed = defaultdict(int)
    for r in conn.execute(
            "SELECT section_id, subject_id, teacher_id, COUNT(*) n FROM schedule "
            "WHERE version_id = ? GROUP BY section_id, subject_id, teacher_id",
            (version_id,)):
        placed[(r["section_id"], r["subject_id"], r["teacher_id"])] = r["n"]

    subj = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM subjects")}
    diffs, notyet = [], []
    has_schedule = bool(placed)
    for key in set(planned) | set(placed):
        a, b = planned.get(key, 0), placed.get(key, 0)
        if a == b:
            continue
        sid, sbid, tid = key
        rec = {"section_id": sid, "subject_id": sbid, "teacher_id": tid,
               "assigned": a, "scheduled": b,
               "text": "%s | %s | %s: مُسند %d · في الجدول %d"
                       % (label.get(sid, "?"), subj.get(sbid, "?"),
                          tname.get(tid, "?"), a, b)}
        if b == 0:
            # إسناد أُضيف ولم يُولَّد بعد - حالة طبيعية لا خطأ
            notyet.append(rec)
        else:
            diffs.append(rec)

    if diffs:
        add("assign_vs_schedule", "err",
            "إسنادات لا تطابق الجدول المولَّد",
            "عدد الحصص المُسند يختلف عمّا هو موضوع فعلاً في الجدول. "
            "هذا يجعل صفحة المعلمات وميزان الفصول تعرض أرقاماً خاطئة.",
            diffs, fixable=True)
    if notyet and has_schedule:
        add("not_scheduled_yet", "warn",
            "إسنادات لم تدخل الجدول بعد",
            "أُضيفت بعد آخر توليد. أعد التوليد لإدراجها، أو أضفها يدوياً "
            "بالضغط على خانة فارغة في الجدول العام.",
            notyet)

    # --- 2) خلايا يتيمة ---------------------------------------------
    orphan = [dict(r) for r in conn.execute(
        "SELECT sc.id, sc.section_id, sc.subject_id, sc.teacher_id FROM schedule sc "
        "LEFT JOIN assignments a ON a.section_id = sc.section_id "
        "  AND a.subject_id = sc.subject_id AND a.teacher_id = sc.teacher_id "
        "WHERE sc.version_id = ? AND a.id IS NULL", (version_id,))]
    if orphan:
        add("orphan_cells", "err", "خلايا في الجدول بلا إسناد",
            "حصص موضوعة في الجدول لم يعد لها إسناد — غالباً حُذف الإسناد بعد التوليد.",
            [{"id": r["id"], "text": "%s | %s | %s"
              % (label.get(r["section_id"], "?"), subj.get(r["subject_id"], "?"),
                 tname.get(r["teacher_id"], "?"))} for r in orphan], fixable=True)

    # --- 3) إسنادات مكرّرة -------------------------------------------
    dups = [dict(r) for r in conn.execute(
        "SELECT section_id, subject_id, teacher_id, COUNT(*) n FROM assignments "
        "GROUP BY section_id, subject_id, teacher_id HAVING n > 1")]
    if dups:
        add("dup_assignments", "err", "إسنادات مكرّرة",
            "نفس الفصل والمادة والمعلمة مسجّلة أكثر من مرة.",
            [{"text": "%s | %s | %s (×%d)"
              % (label.get(r["section_id"], "?"), subj.get(r["subject_id"], "?"),
                 tname.get(r["teacher_id"], "?"), r["n"])} for r in dups],
            fixable=True)

    # --- 4) نفس المادة لنفس الفصل عند معلمتين ------------------------
    multi = [dict(r) for r in conn.execute(
        "SELECT section_id, subject_id, COUNT(DISTINCT teacher_id) n "
        "FROM assignments GROUP BY section_id, subject_id HAVING n > 1")]
    if multi:
        add("shared_subject", "warn", "مادة واحدة لفصل واحد عند أكثر من معلمة",
            "قد يكون مقصوداً (تدريس مشترك) وقد يكون تكراراً بالخطأ — راجعها.",
            [{"text": "%s | %s (%d معلمات)"
              % (label.get(r["section_id"], "?"), subj.get(r["subject_id"], "?"),
                 r["n"])} for r in multi])

    # --- 5) خلايا خارج الدوام ---------------------------------------
    bad_slot = []
    for r in conn.execute("SELECT * FROM schedule WHERE version_id = ?", (version_id,)):
        why = None
        if r["day"] not in days:
            why = "يوم غير عامل"
        elif r["period_number"] in breaks:
            why = "حصة فسحة"
        elif r["period_number"] > db.periods_for_grade_day(
                st, grade_of.get(r["section_id"]), r["day"]):
            why = "بعد حدّ حصص اليوم"
        if why:
            bad_slot.append({"id": r["id"], "text": "%s — %s ح%d (%s)"
                             % (label.get(r["section_id"], "?"),
                                db.DAY_NAMES[r["day"]], r["period_number"], why)})
    if bad_slot:
        add("outside_hours", "err", "خلايا خارج الدوام", "", bad_slot)

    # --- 6) تعارضات --------------------------------------------------
    sec_at = defaultdict(list)
    tea_at = defaultdict(list)
    for r in conn.execute("SELECT * FROM schedule WHERE version_id = ?", (version_id,)):
        sec_at[(r["section_id"], r["day"], r["period_number"])].append(r["id"])
        tea_at[(r["teacher_id"], r["day"], r["period_number"])].append(r["id"])
    clash = []
    for (sid, d, p), ids in sec_at.items():
        if len(ids) > 1:
            clash.append({"text": "فصل %s — %s ح%d (%d حصص)"
                          % (label.get(sid, "?"), db.DAY_NAMES[d], p, len(ids))})
    for (tid, d, p), ids in tea_at.items():
        if len(ids) > 1:
            clash.append({"text": "معلمة %s — %s ح%d (%d حصص)"
                          % (tname.get(tid, "?"), db.DAY_NAMES[d], p, len(ids))})
    if clash:
        add("clashes", "err", "تعارضات في الجدول", "", clash)

    # --- 7) خانات عدم الإتاحة ---------------------------------------
    unav = defaultdict(set)
    for r in conn.execute("SELECT * FROM teacher_unavailable"):
        unav[r["teacher_id"]].add((r["day"], r["period_number"]))
    hits = []
    for r in conn.execute("SELECT * FROM schedule WHERE version_id = ?", (version_id,)):
        if (r["day"], r["period_number"]) in unav.get(r["teacher_id"], ()):
            hits.append({"text": "%s — %s ح%d"
                         % (tname.get(r["teacher_id"], "?"),
                            db.DAY_NAMES[r["day"]], r["period_number"])})
    if hits:
        add("unavailable", "err", "حصص في خانات عدم الإتاحة", "", hits)

    # --- 8) تجاوز نصاب المعلمة --------------------------------------
    over = []
    for r in conn.execute(
            "SELECT t.id, t.name, t.max_periods_per_week cap, "
            "COALESCE((SELECT SUM(periods_per_week) FROM assignments a "
            "          WHERE a.teacher_id = t.id), 0) load FROM teachers t"):
        if r["cap"] and r["load"] > r["cap"]:
            over.append({"text": "%s: %d من %d" % (r["name"], r["load"], r["cap"])})
    if over:
        add("teacher_over", "warn", "معلمات تجاوز إسنادهنّ النصاب الأسبوعي", "", over)

    # --- 9) ميزان الفصول --------------------------------------------
    import solver
    data = solver.Data(conn, version_id)
    bal = []
    need = defaultdict(int)
    for r in conn.execute("SELECT section_id, periods_per_week FROM assignments"):
        need[r["section_id"]] += r["periods_per_week"]
    for sid, sec in data.sections.items():
        slots = len(data.section_slots.get(sid, ()))
        req = int(sec.get("required_periods") or 0) or slots
        got = need.get(sid, 0)
        if got != req:
            bal.append({"text": "%s: المُسند %d والمطلوب %d"
                        % (label.get(sid, "?"), got, req)})
    if bal:
        add("section_balance", "warn", "فصول المُسند فيها لا يساوي المطلوب", "", bal)

    # --- 10) بنية ناقصة ---------------------------------------------
    empty_g = [dict(r) for r in conn.execute(
        "SELECT g.name FROM grades g LEFT JOIN sections se ON se.grade_id = g.id "
        "WHERE se.id IS NULL")]
    if empty_g:
        add("grade_no_section", "err", "صفوف بلا فصول", "",
            [{"text": r["name"]} for r in empty_g], fixable=True)

    no_teacher = [dict(r) for r in conn.execute(
        "SELECT sb.name, (SELECT COUNT(*) FROM assignments a WHERE a.subject_id = sb.id) n "
        "FROM subjects sb LEFT JOIN subject_teachers stt ON stt.subject_id = sb.id "
        "WHERE stt.subject_id IS NULL GROUP BY sb.id")]
    no_teacher = [r for r in no_teacher if r["n"]]
    if no_teacher:
        add("subject_no_teacher", "warn",
            "مواد لها إسنادات لكن بلا معلمات مرتبطة في شاشة المواد",
            "لن تظهر في قائمة مواد المعلمة عند الإسناد.",
            [{"text": r["name"]} for r in no_teacher], fixable=True)

    return out


# ------------------------------------------------------------- الإصلاح

def apply_fixes(conn, version_id, results):
    """إصلاحات آمنة فقط - لا تحذف شيئاً يمكن أن يكون مقصوداً."""
    done = []
    by = {r["code"]: r for r in results}

    # الجدول هو الحقيقة بعد التوليد: صحّح عدد الإسناد ليطابق ما وُضع
    r = by.get("assign_vs_schedule")
    if r:
        n = 0
        for d in r["rows"]:
            if d["scheduled"] == 0:
                continue                     # لا نلغي إسناداً لم يُجدول بعد
            cur = conn.execute(
                "SELECT id FROM assignments WHERE section_id=? AND subject_id=? "
                "AND teacher_id=?", (d["section_id"], d["subject_id"],
                                     d["teacher_id"])).fetchone()
            if cur:
                conn.execute("UPDATE assignments SET periods_per_week=? WHERE id=?",
                             (d["scheduled"], cur["id"]))
            else:
                conn.execute(
                    "INSERT INTO assignments(section_id, subject_id, teacher_id, "
                    "periods_per_week) VALUES (?,?,?,?)",
                    (d["section_id"], d["subject_id"], d["teacher_id"],
                     d["scheduled"]))
            n += 1
        if n:
            done.append("صُحّح %d إسناد ليطابق الجدول" % n)

    # إسنادات مكرّرة: ادمجها في واحد
    r = by.get("dup_assignments")
    if r:
        rows = conn.execute(
            "SELECT section_id, subject_id, teacher_id, COUNT(*) n, "
            "MIN(id) keep, SUM(periods_per_week) tot FROM assignments "
            "GROUP BY section_id, subject_id, teacher_id HAVING n > 1").fetchall()
        for x in rows:
            conn.execute("DELETE FROM assignments WHERE section_id=? AND subject_id=? "
                         "AND teacher_id=? AND id<>?",
                         (x["section_id"], x["subject_id"], x["teacher_id"], x["keep"]))
        if rows:
            done.append("دُمج %d إسناد مكرّر" % len(rows))

    # صف بلا فصول -> أنشئ له فصله الضمني
    if by.get("grade_no_section"):
        n = db.ensure_default_sections(conn)
        if n:
            done.append("أُنشئ فصل ضمني لـ %d صف" % n)

    # مادة لها إسنادات بلا معلمات مرتبطة -> اربطها من إسناداتها
    if by.get("subject_no_teacher"):
        cur = conn.execute(
            "INSERT OR IGNORE INTO subject_teachers(subject_id, teacher_id) "
            "SELECT DISTINCT subject_id, teacher_id FROM assignments")
        if cur.rowcount:
            done.append("رُبط %d معلم بمادته" % cur.rowcount)

    conn.commit()
    return done


def main():
    fix = "--fix" in sys.argv
    db.init_db()
    conn = db.connect()
    vid = db.active_version(conn)["id"]

    if fix:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(os.path.dirname(db.DB_PATH),
                            "نسخة-قبل-الإصلاح-%s.db" % stamp)
        try:
            db.checkpoint(conn)
        except Exception:
            pass
        shutil.copy2(db.DB_PATH, dest)
        print("نسخة احتياطية: %s\n" % os.path.basename(dest))

    results = run_checks(conn, vid)
    errs = [r for r in results if r["level"] == "err"]
    warns = [r for r in results if r["level"] == "warn"]

    if not results:
        print("لا توجد مشاكل — النظام سليم ✓")
    for r in results:
        mark = "خطأ " if r["level"] == "err" else "تنبيه"
        print("[%s] %s — %d حالة" % (mark, r["title"], len(r["rows"])))
        if r["detail"]:
            print("        %s" % r["detail"])
        for x in r["rows"][:6]:
            print("        • %s" % x["text"])
        if len(r["rows"]) > 6:
            print("        … و%d أخرى" % (len(r["rows"]) - 6))
        print()

    print("=" * 60)
    print("أخطاء: %d   تنبيهات: %d" % (len(errs), len(warns)))

    if fix:
        done = apply_fixes(conn, vid, results)
        print()
        for d in done:
            print("  ✓", d)
        if not done:
            print("  لا يوجد ما يمكن إصلاحه تلقائياً")
        left = run_checks(conn, vid)
        print("\nبعد الإصلاح — أخطاء: %d   تنبيهات: %d"
              % (len([r for r in left if r["level"] == "err"]),
                 len([r for r in left if r["level"] == "warn"])))
    elif any(r["fixable"] for r in results):
        print("\n(أضف --fix للإصلاح التلقائي الآمن)")

    conn.close()
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
