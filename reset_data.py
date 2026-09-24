# -*- coding: utf-8 -*-
"""
تفريغ البيانات — يعيد البرنامج كأنه جديد لتُدخل بياناتك من الصفر.

يحفظ نسخة كاملة من school.db قبل أي حذف، ثم يمسح:
المراحل والصفوف والشعب، المواد، المعلمين وعدم إتاحتهم، الإسنادات،
المعامل، الجدول، النسخ، وسجل اللقطات، ويصفّر إعدادات المدرسة.

يُبقي: مخطّط قاعدة البيانات، سبع حصص افتراضية، ونسخة فارغة واحدة.

تشغيل:  python reset_data.py            (يسأل قبل التنفيذ)
        python reset_data.py --yes      (بلا سؤال)
"""
import os
import shutil
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import db

# تُفرَّغ بالكامل
WIPE = ["schedule", "history", "gen_options", "assignments", "rooms",
        "teacher_unavailable", "teachers", "subjects",
        "sections", "grades", "stages", "versions", "time_slots"]


def backup():
    if not os.path.exists(db.DB_PATH):
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(os.path.dirname(db.DB_PATH),
                        "نسخة-قبل-التفريغ-%s.db" % stamp)
    # أغلق دفتر WAL أولاً حتى تكون النسخة مكتملة
    conn = db.connect()
    try:
        db.checkpoint(conn)
    except Exception:
        pass
    conn.close()
    shutil.copy2(db.DB_PATH, dest)
    return dest


def main():
    db.init_db()

    conn = db.connect()
    counts = {t: conn.execute("SELECT COUNT(*) c FROM %s" % t).fetchone()["c"]
              for t in WIPE}
    conn.close()
    total = sum(counts.values())

    print("البيانات الحالية:")
    for t, n in counts.items():
        if n:
            print("  %-20s %d" % (t, n))
    if not total:
        print("  (فارغة أصلاً)")

    if "--yes" not in sys.argv:
        print("\nسيُحذف كل ما سبق. تُحفظ نسخة احتياطية أولاً.")
        try:
            answer = input("اكتب  نعم  للمتابعة: ").strip()
        except EOFError:
            answer = ""
        if answer not in ("نعم", "y", "Y", "yes"):
            print("أُلغي. لم يُحذف شيء.")
            return 1

    path = backup()
    if path:
        print("\nنسخة احتياطية: %s" % os.path.basename(path))

    conn = db.connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    for t in WIPE:
        conn.execute("DELETE FROM %s" % t)
    conn.execute("DELETE FROM sqlite_sequence")     # ارجع بالمعرّفات إلى 1
    conn.execute("DELETE FROM settings")
    conn.commit()
    conn.close()

    db.init_db()        # يعيد الإعدادات الافتراضية والحصص السبع ونسخة فارغة

    conn = db.connect()
    left = {t: conn.execute("SELECT COUNT(*) c FROM %s" % t).fetchone()["c"]
            for t in WIPE}
    conn.close()

    print("\nتم التفريغ:")
    for t in WIPE:
        print("  %-20s %d" % (t, left[t]))
    print("\nالبرنامج جاهز لبياناتك. ابدأ من: الإعدادات ← المراحل والصفوف والشعب.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
