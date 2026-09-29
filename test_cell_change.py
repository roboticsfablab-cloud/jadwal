# -*- coding: utf-8 -*-
"""
اختبار: تبديل مادة الحصة أو فصلها من الجدول، وحذفها، وأثر ذلك على الإسناد.
يعمل على نسخة مؤقّتة — بياناتك الحقيقية لا تُمسّ.
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import testkit  # noqa: F401
import db
import edits
import app as webapp

FAILED = []


def ok(label, cond, extra=""):
    print("  %-56s %s%s" % (label, "✓" if cond else "!! فشل",
                            ("  " + extra) if extra else ""))
    if not cond:
        FAILED.append(label)


def periods(conn, sec, sub, tea):
    r = conn.execute("SELECT periods_per_week n FROM assignments WHERE section_id=? "
                     "AND subject_id=? AND teacher_id=?", (sec, sub, tea)).fetchone()
    return r["n"] if r else 0


def load(conn, tea):
    return conn.execute("SELECT COALESCE(SUM(periods_per_week),0) n FROM assignments "
                        "WHERE teacher_id=?", (tea,)).fetchone()["n"]


def main():
    db.init_db()
    webapp.app.config["TESTING"] = True
    c = webapp.app.test_client()
    conn = db.connect()
    vid = db.active_version(conn)["id"]
    ctx = edits.Ctx(conn, vid)
    if not ctx.rows:
        print("لا يوجد جدول مولّد — تخطّي الاختبار")
        return 0

    # حصة غير مدمجة ومعلمتها وفصل آخر خالٍ في نفس الخانة
    entry = target_sec = None
    displaced = []
    secs = [r["id"] for r in conn.execute("SELECT id FROM sections")]
    for r in ctx.rows:
        if r["merge_group_id"]:
            continue
        for sid in secs:
            if sid == r["section_id"]:
                continue
            ok_, _, gone = edits.validate_change(ctx, r["id"], sid, r["subject_id"],
                                                 r["teacher_id"], replace=True)
            if ok_:
                entry, target_sec, displaced = r, sid, gone
                break
        if entry:
            break
    ok("وُجدت حصة يمكن نقل فصلها", entry is not None)
    if not entry:
        return 1

    tea, sub, sec = entry["teacher_id"], entry["subject_id"], entry["section_id"]
    other_sub = conn.execute("SELECT id FROM subjects WHERE id != ? LIMIT 1",
                             (sub,)).fetchone()["id"]

    print("=== 1) خيارات النافذة ===")
    d = c.get("/api/cell-change-options/%d" % entry["id"]).get_json()
    ok("تعيد المواد والفصول والمعلمات", d["subjects"] and d["sections"] and d["teachers"])
    ok("فصل الحصة الحالي غير مشغول بنفسه",
       not next(x for x in d["sections"] if x["id"] == sec)["busy"])

    print("=== 2) تبديل الفصل والمادة معاً ===")
    occ = [ctx.by_id[i] for i in displaced]
    occ_n = [periods(conn, o["section_id"], o["subject_id"], o["teacher_id"]) for o in occ]
    old_n = periods(conn, sec, sub, tea)
    new_n = periods(conn, target_sec, other_sub, tea)
    before = load(conn, tea)
    r = c.post("/grid/cell", json={"action": "change", "id": entry["id"],
                                    "section_id": target_sec,
                                    "subject_id": other_sub}).get_json()
    ok("فصل مشغول بلا استبدال = مرفوض", r["ok"] == (not occ), r.get("reason", ""))
    r = c.post("/grid/cell", json={"action": "change", "id": entry["id"],
                                    "section_id": target_sec, "replace": True,
                                    "subject_id": other_sub}).get_json()
    ok("التبديل مع الاستبدال مقبول", r["ok"], r.get("reason", ""))
    conn = db.connect()
    row = conn.execute("SELECT * FROM schedule WHERE id=?", (entry["id"],)).fetchone()
    ok("الحصة في الجدول تغيّرت", (row["section_id"], row["subject_id"]) ==
       (target_sec, other_sub))
    ok("الحصة بقيت في خانتها", (row["day"], row["period_number"]) ==
       (entry["day"], entry["period_number"]))
    ok("الإسناد القديم نقص حصة", periods(conn, sec, sub, tea) == max(0, old_n - 1))
    ok("الإسناد الجديد زاد حصة", periods(conn, target_sec, other_sub, tea) == new_n + 1)
    if not any(o["teacher_id"] == tea for o in occ):
        ok("نصاب المعلمة ثابت (نقص واحدة وزادت واحدة)", load(conn, tea) == before)
    for o, n in zip(occ, occ_n):
        ok("الحصة المستبدَلة حُذفت", conn.execute(
            "SELECT 1 FROM schedule WHERE id=?", (o["id"],)).fetchone() is None)
        ok("إسناد الحصة المستبدَلة نقص حصة", periods(
            conn, o["section_id"], o["subject_id"], o["teacher_id"]) ==
            max(0, n - 1) + (1 if (o["subject_id"], o["teacher_id"]) ==
                             (other_sub, tea) else 0))
    ok("المعلمة صارت تدرّس المادة الجديدة", conn.execute(
        "SELECT 1 FROM subject_teachers WHERE subject_id=? AND teacher_id=?",
        (other_sub, tea)).fetchone() is not None)

    print("=== 3) رفض التعارض وعدم التغيير ===")
    r = c.post("/grid/cell", json={"action": "change", "id": entry["id"],
                                    "section_id": target_sec, "replace": True,
                                    "subject_id": other_sub}).get_json()
    ok("بلا تغيير = مرفوض", not r["ok"])
    other_t = conn.execute(
        "SELECT teacher_id FROM schedule WHERE version_id=? AND day=? "
        "AND period_number=? AND teacher_id != ?",
        (vid, entry["day"], entry["period_number"], tea)).fetchone()
    if other_t:
        r = c.post("/grid/cell", json={"action": "change", "id": entry["id"],
                                        "teacher_id": other_t["teacher_id"],
                                        "replace": True}).get_json()
        ok("معلمة مشغولة في الخانة = مرفوض", not r["ok"], r.get("reason", ""))

    print("=== 4) الحذف يُنقص النصاب ويُبقي الخانة فارغة ===")
    n = periods(conn, target_sec, other_sub, tea)
    before = load(conn, tea)
    c.post("/grid/cell", json={"action": "delete", "id": entry["id"]})
    conn = db.connect()
    ok("الحصة حُذفت", conn.execute("SELECT 1 FROM schedule WHERE id=?",
                                   (entry["id"],)).fetchone() is None)
    ok("إسنادها نقص حصة", periods(conn, target_sec, other_sub, tea) == n - 1)
    ok("نصاب المعلمة نقص حصة", load(conn, tea) == before - 1)

    print("=== 5) الإضافة فوق النصاب تزيده ===")
    before = load(conn, tea)
    r = c.post("/grid/cell", json={"action": "add", "section_id": target_sec,
                                    "subject_id": other_sub, "teacher_id": tea,
                                    "day": entry["day"],
                                    "period": entry["period_number"]}).get_json()
    ok("الإضافة مقبولة", r["ok"], r.get("reason", ""))
    conn = db.connect()
    placed = conn.execute("SELECT COUNT(*) n FROM schedule WHERE version_id=? AND "
                          "section_id=? AND subject_id=? AND teacher_id=?",
                          (vid, target_sec, other_sub, tea)).fetchone()["n"]
    ok("نصاب الإسناد لا يقل عن المُدرج", periods(conn, target_sec, other_sub, tea) >= placed)
    ok("نصاب المعلمة لم ينقص", load(conn, tea) >= before)
    conn.close()

    print()
    if FAILED:
        print("فشل %d: %s" % (len(FAILED), "، ".join(FAILED)))
        return 1
    print("كل الاختبارات نجحت ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
