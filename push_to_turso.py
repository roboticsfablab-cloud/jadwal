# -*- coding: utf-8 -*-
"""
رفع قاعدة البيانات المحلية (school.db) إلى Turso — مع تحقّق صفّاً بصفّ.

قبل التشغيل اضبط المتغيّرين:
    set TURSO_DATABASE_URL=libsql://اسم-قاعدتك.turso.io
    set TURSO_AUTH_TOKEN=الرمز

    python push_to_turso.py            معاينة: يقارن المحلي بالسحابي
    python push_to_turso.py --push     رفع فعلي (يستبدل ما في السحابة)
    python push_to_turso.py --pull     تنزيل من السحابة إلى نسخة محلية

الرفع يمسح جداول السحابة ثم ينسخ كل شيء، فما في جهازك هو المرجع.
"""
import os
import shutil
import sqlite3
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import db

# ترتيب يحترم المفاتيح الأجنبية: الآباء قبل الأبناء
TABLES = [
    "settings", "stages", "grades", "sections", "subjects", "teachers",
    "subject_teachers", "teacher_unavailable", "rooms", "assignments",
    "time_slots", "versions", "schedule", "gen_options", "history",
]


def local_conn():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "school.db")
    if not os.path.exists(path):
        print("لا يوجد ملف school.db بجانب هذا السكربت")
        return None
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def columns_of(conn, table):
    return [r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)]


def counts(conn):
    out = {}
    for t in TABLES:
        try:
            out[t] = conn.execute("SELECT COUNT(*) c FROM %s" % t).fetchone()["c"]
        except Exception:
            out[t] = None          # الجدول غير موجود بعد
    return out


def show(local, remote):
    print("%-22s %10s %10s" % ("الجدول", "محلي", "على Turso"))
    print("-" * 46)
    same = True
    for t in TABLES:
        a, b = local.get(t), remote.get(t)
        mark = ""
        if b is None:
            mark = "  (غير موجود)"
            same = False
        elif a != b:
            mark = "  <<< مختلف"
            same = False
        print("%-22s %10s %10s%s"
              % (t, a, "-" if b is None else b, mark))
    return same


def push(lc, rc):
    print("\nإنشاء المخطّط على Turso…")
    rc.executescript(db.SCHEMA)
    db.migrate(rc)
    rc.commit()

    print("نسخ البيانات…")
    rc.execute("PRAGMA foreign_keys = OFF")
    for t in reversed(TABLES):                 # احذف الأبناء أولاً
        rc.execute("DELETE FROM %s" % t)
    rc.commit()

    total = 0
    for t in TABLES:
        cols = columns_of(lc, t)
        rows = [tuple(r[c] for c in cols) for r in
                lc.execute("SELECT %s FROM %s" % (", ".join(cols), t))]
        if not rows:
            print("   %-22s فارغ" % t)
            continue
        n = db.insert_many(
            rc, "INSERT INTO %s(%s) VALUES" % (t, ", ".join(cols)), rows)
        rc.commit()
        total += n
        print("   %-22s %d صف" % (t, n))
    print("\nالمجموع المرفوع: %d صف" % total)


def pull(rc):
    """ينزّل ما في السحابة إلى ملف محلي جديد — للنسخ الاحتياطي."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "من-turso-%s.db" % stamp)
    out = sqlite3.connect(dest)
    out.executescript(db.SCHEMA)
    db.migrate(out)
    out.commit()
    total = 0
    for t in TABLES:
        cols = columns_of(rc, t)
        rows = [tuple(r[c] for c in cols) for r in
                rc.execute("SELECT %s FROM %s" % (", ".join(cols), t))]
        if rows:
            marks = ",".join(["?"] * len(cols))
            out.executemany("INSERT INTO %s(%s) VALUES (%s)"
                            % (t, ", ".join(cols), marks), rows)
            total += len(rows)
    out.commit()
    out.close()
    print("نُزّل %d صف إلى %s" % (total, os.path.basename(dest)))


def main():
    if not db.using_turso():
        print("اضبط TURSO_DATABASE_URL (و TURSO_AUTH_TOKEN) أولاً.")
        print("مثال في PowerShell:")
        print('  $env:TURSO_DATABASE_URL="libsql://xxx.turso.io"')
        print('  $env:TURSO_AUTH_TOKEN="ey..."')
        return 1

    print("الوجهة: %s\n" % db.TURSO_URL)
    try:
        rc = db.connect()
    except Exception as e:
        print("تعذّر الاتصال بـ Turso: %s" % e)
        return 1

    if "--pull" in sys.argv:
        pull(rc)
        rc.close()
        return 0

    lc = local_conn()
    if lc is None:
        rc.close()
        return 1

    before = show(counts(lc), counts(rc))

    if "--push" not in sys.argv:
        print("\n(معاينة فقط — أضف --push للرفع)")
        lc.close()
        rc.close()
        return 0

    # نسخة احتياطية محلية قبل أي شيء
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "school.db")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = os.path.join(os.path.dirname(src), "نسخة-قبل-الرفع-%s.db" % stamp)
    shutil.copy2(src, bak)
    print("\nنسخة احتياطية محلية: %s" % os.path.basename(bak))

    push(lc, rc)

    print("\nالتحقّق بعد الرفع:")
    ok = show(counts(lc), counts(rc))

    # تحقّق أعمق: عيّنة من الأسماء الفعلية
    print("\nعيّنة من البيانات على Turso:")
    for sql, label in (
            ("SELECT name FROM teachers ORDER BY id LIMIT 3", "معلمات"),
            ("SELECT name FROM subjects ORDER BY id LIMIT 3", "مواد"),
            ("SELECT name FROM grades ORDER BY id LIMIT 3", "صفوف")):
        vals = [r["name"] for r in rc.execute(sql)]
        print("   %-8s %s" % (label, "، ".join(vals)))

    lc.close()
    rc.close()
    print("\n%s" % ("تم الرفع والتحقّق — الأعداد متطابقة ✓" if ok
                    else "!! الأعداد غير متطابقة — راجع الجدول أعلاه"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
