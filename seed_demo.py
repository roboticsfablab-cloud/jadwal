# -*- coding: utf-8 -*-
"""
بيانات تجريبية لاختبار المحرّك - مدرسة متوسطة واقعية.
تشغيل:  python seed_demo.py
تحذير: يمسح كل البيانات الحالية.
"""
import json
import random
import sys

try:                       # الطرفية في ويندوز تستعمل cp1252 افتراضياً
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import db

STAGES = [("متوسط", [("أول متوسط", 4), ("ثاني متوسط", 4), ("ثالث متوسط", 4)])]

# (اسم المادة، الاختصار، حصص أسبوعياً، كتل مزدوجة)
CURRICULUM = [
    ("القرآن الكريم", "قرآن", 4, 0),
    ("الدراسات الإسلامية", "إسلامية", 4, 0),
    ("اللغة العربية", "عربي", 5, 1),
    ("الرياضيات", "رياضيات", 5, 1),
    ("العلوم", "علوم", 4, 1),
    ("اللغة الإنجليزية", "إنجليزي", 4, 0),
    ("الدراسات الاجتماعية", "اجتماعيات", 3, 0),
    ("التربية الفنية", "فنية", 2, 0),
    ("التربية البدنية", "بدنية", 2, 1),
    ("المهارات الرقمية", "رقمية", 2, 1),
]
CORE = {"اللغة العربية", "الرياضيات", "العلوم", "اللغة الإنجليزية"}

TEACHERS = [
    ("محمد الغامدي", "قرآن"), ("عبدالله الشهري", "قرآن"),
    ("سعد الدوسري", "إسلامية"), ("فهد القرني", "إسلامية"),
    ("ناصر الحربي", "عربي"), ("تركي المالكي", "عربي"), ("ماجد السبيعي", "عربي"),
    ("خالد العتيبي", "رياضيات"), ("بندر الزهراني", "رياضيات"), ("أحمد الشمري", "رياضيات"),
    ("سلطان القحطاني", "علوم"), ("يوسف البقمي", "علوم"),
    ("عمر الرشيد", "إنجليزي"), ("زياد المطيري", "إنجليزي"), ("طلال العنزي", "إنجليزي"),
    ("راكان الجهني", "اجتماعيات"), ("عايض الأسمري", "اجتماعيات"),
    ("مشعل الخالدي", "فنية"), ("رائد الصاعدي", "فنية"),
    ("وليد الثبيتي", "بدنية"), ("هشام البلوي", "بدنية"),
    ("نايف الحارثي", "رقمية"), ("صالح الرشودي", "رقمية"),
]

SUBJECT_OF_SPEC = {
    "قرآن": "القرآن الكريم", "إسلامية": "الدراسات الإسلامية",
    "عربي": "اللغة العربية", "رياضيات": "الرياضيات", "علوم": "العلوم",
    "إنجليزي": "اللغة الإنجليزية", "اجتماعيات": "الدراسات الاجتماعية",
    "فنية": "التربية الفنية", "بدنية": "التربية البدنية", "رقمية": "المهارات الرقمية",
}


def main():
    db.init_db()
    conn = db.connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    for t in ("schedule", "assignments", "teacher_unavailable", "teachers",
              "subjects", "sections", "grades", "stages", "rooms", "history",
              "gen_options"):
        conn.execute("DELETE FROM %s" % t)
    conn.commit()

    # الهيكل
    for si, (stage, grades) in enumerate(STAGES):
        sid = conn.execute(
            "INSERT INTO stages(name, sort_order) VALUES (?, ?)", (stage, si)
        ).lastrowid
        for gi, (gname, nsec) in enumerate(grades):
            gid = conn.execute(
                "INSERT INTO grades(stage_id, name, sort_order) VALUES (?, ?, ?)",
                (sid, gname, gi)).lastrowid
            for k in range(1, nsec + 1):
                conn.execute(
                    "INSERT INTO sections(grade_id, name, student_count, sort_order) "
                    "VALUES (?, ?, ?, ?)", (gid, str(k), 28, k))

    # المواد
    subj_id = {}
    for i, (name, short, _ppw, _dbl) in enumerate(CURRICULUM):
        subj_id[name] = conn.execute(
            "INSERT INTO subjects(name, short_name, color_index, sort_order) "
            "VALUES (?, ?, ?, ?)", (name, short, i % 12, i)).lastrowid

    # المعلمون
    tea_by_spec = {}
    for i, (name, spec) in enumerate(TEACHERS):
        tid = conn.execute(
            "INSERT INTO teachers(name, specialization, max_periods_per_week, "
            "max_periods_per_day, sort_order) VALUES (?, ?, ?, ?, ?)",
            (name, spec, 24, 6, i)).lastrowid
        tea_by_spec.setdefault(spec, []).append(tid)

    # معمل حاسب
    room_id = conn.execute(
        "INSERT INTO rooms(name, subject_id, capacity) VALUES (?, ?, ?)",
        ("معمل الحاسب", subj_id["المهارات الرقمية"], 1)).lastrowid

    # الإعدادات: 5 أيام × 7 حصص = 35 خانة
    total_ppw = sum(c[2] for c in CURRICULUM)
    per_day = 7
    days = [0, 1, 2, 3, 4]
    need = len(days) * per_day
    print("مجموع حصص المنهج: %d ، خانات الشعبة: %d" % (total_ppw, need))
    if total_ppw != need:
        print("!! المنهج لا يساوي الخانات — عدّل CURRICULUM")

    db.set_setting("school_name", "متوسطة الأمير سلطان", conn)
    db.set_setting("manager_name", "أ. عبدالرحمن الفهد", conn)
    db.set_setting("school_stage_label", "المرحلة المتوسطة", conn)
    db.set_setting("working_days", ",".join(str(d) for d in days), conn)
    db.set_setting("periods_per_day", per_day, conn)
    db.set_setting("core_subject_ids",
                   json.dumps([subj_id[n] for n in CORE]), conn)
    conn.commit()

    # الإسناد: وزّع الشعب على معلمي كل تخصص بالتساوي
    sections = [r["id"] for r in conn.execute("SELECT id FROM sections ORDER BY id")]
    rng = random.Random(7)
    counters = {}
    for sec in sections:
        for name, short, ppw, dbl in CURRICULUM:
            spec = [k for k, v in SUBJECT_OF_SPEC.items() if v == name][0]
            pool = tea_by_spec[spec]
            n = counters.get(spec, 0)
            tid = pool[n % len(pool)]
            counters[spec] = n + 1
            conn.execute(
                "INSERT INTO assignments(section_id, subject_id, teacher_id, "
                "periods_per_week, double_periods, room_id) VALUES (?,?,?,?,?,?)",
                (sec, subj_id[name], tid, ppw, dbl,
                 room_id if name == "المهارات الرقمية" else None))

    # بعض قيود عدم الإتاحة الواقعية
    for tid in rng.sample([t for pool in tea_by_spec.values() for t in pool], 4):
        d = rng.choice(days)
        for p in (1, 2):
            conn.execute("INSERT OR IGNORE INTO teacher_unavailable"
                         "(teacher_id, day, period_number) VALUES (?,?,?)", (tid, d, p))

    conn.commit()

    tot = conn.execute("SELECT SUM(periods_per_week) s FROM assignments").fetchone()["s"]
    print("الشعب: %d · المعلمون: %d · الإسنادات: %d · مجموع الحصص: %d"
          % (len(sections), len(TEACHERS),
             conn.execute("SELECT COUNT(*) c FROM assignments").fetchone()["c"], tot))
    conn.close()
    print("تم إنشاء البيانات التجريبية.")


if __name__ == "__main__":
    sys.exit(main())
