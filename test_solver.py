# -*- coding: utf-8 -*-
"""اختبار المحرّك: يولّد ثم يتحقّق أن القيود الصارمة لم تُكسر."""
import sys
import time
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import testkit  # noqa: F401  (يعزل الاختبار عن بياناتك)
import db
import solver


def main():
    conn = db.connect()
    vid = db.active_version(conn)["id"]
    data = solver.Data(conn, vid)

    errors, warnings = solver.check_feasibility(data)
    print("=== فحص الجدوى ===")
    for e in errors:
        print("  خطأ:", e)
    for w in warnings[:5]:
        print("  تنبيه:", w)
    if errors:
        print("توقّف: بيانات غير صالحة")
        return 1
    print("  سليم. عدد وحدات الوضع: %d" % len(data.lessons))

    opt = solver.merge_options({"time_limit_seconds": 25, "restarts": 5})
    t0 = time.time()
    res = solver.generate(data, opt, seed=11)
    took = time.time() - t0
    state = res["state"]

    print("\n=== النتيجة ===")
    print("  وُضع %d من %d درساً في %.1f ثانية (%d محاولة)"
          % (res["placed"], res["total"], took, res["attempts"]))
    print("  العقوبة الناعمة: %.1f" % res["cost"])
    if res["unplaced"]:
        print("  لم تُوضع %d:" % len(res["unplaced"]))
        for L in res["unplaced"][:5]:
            info = solver.explain_unplaced(state, L)
            print("    - %s | %s | %s" % (info["subject"], info["sections"], info["reason"]))

    # ---- التحقق من القيود الصارمة
    print("\n=== التحقق من القيود الصارمة ===")
    bad = 0

    sec_seen = defaultdict(set)
    tea_seen = defaultdict(set)
    for uid, (d, p) in state.place.items():
        L = state.by_uid[uid]
        for k in range(L.length):
            cell = (d, p + k)
            for sid in L.sections:
                if cell in sec_seen[sid]:
                    print("  !! تعارض شعبة %s في %s" % (sid, cell)); bad += 1
                sec_seen[sid].add(cell)
                if cell not in data.section_slots[sid]:
                    print("  !! خانة خارج دوام الشعبة %s: %s" % (sid, cell)); bad += 1
            for tid in L.teachers:
                if cell in tea_seen[tid]:
                    print("  !! تعارض معلم %s في %s" % (tid, cell)); bad += 1
                tea_seen[tid].add(cell)
                if cell in data.unavailable.get(tid, ()):
                    print("  !! معلم %s وُضع في خانة غير متاحة %s" % (tid, cell)); bad += 1

    # طاقة المعامل
    room_use = defaultdict(int)
    for uid, (d, p) in state.place.items():
        L = state.by_uid[uid]
        if not L.room_id:
            continue
        for k in range(L.length):
            room_use[(L.room_id, d, p + k)] += 1
    for (rid, d, p), n in room_use.items():
        cap = int(data.rooms.get(rid, {}).get("capacity") or 1)
        if n > cap:
            print("  !! تجاوز طاقة المعمل %s في (%d,%d): %d > %d" % (rid, d, p, n, cap))
            bad += 1

    # الكتل المزدوجة متجاورة فعلاً
    for uid, (d, p) in state.place.items():
        L = state.by_uid[uid]
        if L.length == 2 and (d, p + 1) not in data.section_slots[L.sections[0]]:
            print("  !! كتلة مزدوجة خارج الحدود: %s" % uid); bad += 1

    print("  %s" % ("لا خرق لأي قيد صارم ✓" if bad == 0 else "عدد الخروقات: %d" % bad))

    # ---- إحصاءات ناعمة
    print("\n=== مؤشرات الجودة ===")
    gaps = 0
    golden = 0
    overload = 0
    for tid, cells in tea_seen.items():
        t = data.teachers.get(tid, {})
        per_day = defaultdict(list)
        for (d, p) in cells:
            per_day[d].append(p)
        has_golden = False
        for d in data.days:
            ps = sorted(per_day.get(d, []))
            if not ps:
                has_golden = True
                continue
            gaps += ps[-1] - ps[0] + 1 - len(ps)
            if ps[-1] <= data.max_period // 2:
                has_golden = True
            if len(ps) > int(t.get("max_periods_per_day") or 6):
                overload += 1
        if has_golden:
            golden += 1
    print("  إجمالي فراغات المعلمين: %d" % gaps)
    print("  معلمون لهم يوم ذهبي: %d من %d" % (golden, len(tea_seen)))
    print("  أيام تجاوزت أقصى حصص يومية: %d" % overload)

    conn.close()
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
