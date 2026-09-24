# -*- coding: utf-8 -*-
"""
التعديل اليدوي على الجدول العام: التحقق، التبديل الآمن، والحلّ التلقائي للتعارضات.

يعمل مباشرة على صفوف قاعدة البيانات (لا على حالة المحرّك)، ويحترم نفس
القيود الصارمة: حدود الحصص لكل يوم، الفسحات، عدم إتاحة المعلم، تعارض
المعلم والشعبة، والحصص المثبّتة.
"""
from collections import defaultdict

import db


class Ctx(object):
    """كل ما يلزم للتحقق من خانة واحدة - يُقرأ مرة واحدة."""

    def __init__(self, conn, version_id):
        self.conn = conn
        self.version_id = version_id
        self.settings = db.get_settings(conn)
        self.days = db.working_days(self.settings)
        self.limits = db.day_period_limits(self.settings)
        self.breaks = db.break_periods(conn)
        self.grade_of = {r["id"]: r["grade_id"] for r in
                         conn.execute("SELECT id, grade_id FROM sections")}
        self.unavailable = defaultdict(set)
        for r in conn.execute("SELECT * FROM teacher_unavailable"):
            self.unavailable[r["teacher_id"]].add((r["day"], r["period_number"]))
        self.rows = [dict(r) for r in conn.execute(
            "SELECT sc.*, t.name teacher_name, sb.name subject_name, "
            "       se.name section_name, g.name grade_name, "
            "       CASE WHEN se.is_default = 1 THEN g.name ELSE g.name || ' / ' || se.name END section_label "
            "FROM schedule sc "
            "JOIN teachers t  ON t.id  = sc.teacher_id "
            "JOIN subjects sb ON sb.id = sc.subject_id "
            "JOIN sections se ON se.id = sc.section_id "
            "JOIN grades   g  ON g.id  = se.grade_id "
            "WHERE sc.version_id = ?", (version_id,))]
        self.by_id = {r["id"]: r for r in self.rows}
        self.rebuild()

    def rebuild(self):
        self.sec = defaultdict(list)
        self.tea = defaultdict(list)
        for r in self.rows:
            self.sec[(r["section_id"], r["day"], r["period_number"])].append(r["id"])
            self.tea[(r["teacher_id"], r["day"], r["period_number"])].append(r["id"])

    # ---------------------------------------------------------------- الخانة
    def periods_of(self, section_id, day):
        """عدد الحصص المسموح بها لهذه الشعبة في هذا اليوم."""
        gid = self.grade_of.get(section_id)
        return db.periods_for_grade_day(self.settings, gid, day)

    def slot_allowed(self, section_id, day, period):
        """هل توجد هذه الخانة أصلاً لهذه الشعبة؟ (يوم دوام، ضمن الحد، ليست فسحة)"""
        if day not in self.days:
            return False, "ليس يوم دوام"
        if period in self.breaks:
            return False, "الحصة %d فسحة" % period
        n = self.periods_of(section_id, day)
        if period < 1 or period > n:
            return False, ("يوم %s فيه %d حصص فقط - الحصة %d غير موجودة"
                           % (db.DAY_NAMES[day], n, period))
        return True, ""

    def clash(self, section_id, teacher_id, day, period, ignore=()):
        """من يتعارض مع وضع درس لهذه الشعبة وهذا المعلم هنا."""
        ignore = set(ignore)
        out = []
        for rid in self.sec.get((section_id, day, period), ()):
            if rid not in ignore:
                out.append(("section", rid))
        for rid in self.tea.get((teacher_id, day, period), ()):
            if rid not in ignore:
                out.append(("teacher", rid))
        if (day, period) in self.unavailable.get(teacher_id, ()):
            out.append(("unavailable", None))
        return out

    def describe(self, kind, rid):
        if kind == "unavailable":
            return "المعلم غير متاح في هذه الخانة"
        r = self.by_id.get(rid)
        if r is None:
            return "تعارض"
        if kind == "section":
            return "الشعبة عندها «%s» في نفس الوقت" % r["subject_name"]
        return "المعلم «%s» عنده «%s» مع %s في نفس الوقت" % (
            r["teacher_name"], r["subject_name"], r["section_label"])


# --------------------------------------------------------------- التحقق

def validate_move(ctx, entry_id, day, period):
    r = ctx.by_id.get(entry_id)
    if r is None:
        return False, "الحصة غير موجودة"
    ok, why = ctx.slot_allowed(r["section_id"], day, period)
    if not ok:
        return False, why
    problems = ctx.clash(r["section_id"], r["teacher_id"], day, period,
                         ignore=(entry_id,))
    if problems:
        return False, "؛ ".join(ctx.describe(k, i) for k, i in problems)
    return True, ""


def validate_swap(ctx, id_a, id_b):
    """لا نسمح بالتبديل إن أنتج تعارضاً مع أي حصة أخرى."""
    a, b = ctx.by_id.get(id_a), ctx.by_id.get(id_b)
    if a is None or b is None:
        return False, "الحصة غير موجودة"
    if a["is_pinned"] or b["is_pinned"]:
        return False, "إحدى الحصتين مثبّتة - ألغِ التثبيت أولاً"
    for src, dst in ((a, b), (b, a)):
        ok, why = ctx.slot_allowed(src["section_id"], dst["day"], dst["period_number"])
        if not ok:
            return False, why
        problems = ctx.clash(src["section_id"], src["teacher_id"],
                             dst["day"], dst["period_number"], ignore=(id_a, id_b))
        if problems:
            return False, "؛ ".join(ctx.describe(k, i) for k, i in problems)
    return True, ""


def validate_add(ctx, section_id, teacher_id, day, period):
    ok, why = ctx.slot_allowed(section_id, day, period)
    if not ok:
        return False, why
    problems = ctx.clash(section_id, teacher_id, day, period)
    if problems:
        return False, "؛ ".join(ctx.describe(k, i) for k, i in problems)
    return True, ""


def safe_targets(ctx, entry_id):
    """الخانات التي يمكن نقل هذه الحصة إليها بلا تعارض - للتلوين الأخضر."""
    r = ctx.by_id.get(entry_id)
    if r is None:
        return []
    out = []
    for d in ctx.days:
        for p in range(1, ctx.periods_of(r["section_id"], d) + 1):
            if p in ctx.breaks or (d, p) == (r["day"], r["period_number"]):
                continue
            if not ctx.clash(r["section_id"], r["teacher_id"], d, p, ignore=(entry_id,)):
                out.append([d, p])
    return out


def swappable(ctx, entry_id):
    """الحصص التي يمكن تبديلها مع هذه الحصة بأمان."""
    out = []
    for other in ctx.rows:
        if other["id"] == entry_id:
            continue
        ok, _ = validate_swap(ctx, entry_id, other["id"])
        if ok:
            out.append(other["id"])
    return out


# ------------------------------------------------------------- التعارضات

def conflicts(ctx):
    """كل التعارضات في الجدول الحالي مع أسبابها."""
    out = []
    seen = set()
    for (sid, d, p), ids in ctx.sec.items():
        groups = {ctx.by_id[i].get("merge_group_id") or i for i in ids}
        if len(ids) > 1 and len(groups) > 1:
            r = ctx.by_id[ids[0]]
            out.append({"type": "section", "title": "تعارض شعبة",
                        "detail": "الفصل «%s» عنده %d حصص في %s الحصة %d"
                                  % (r["section_label"], len(ids),
                                     db.DAY_NAMES[d], p),
                        "day": d, "period": p, "ids": list(ids)})
            seen.update(ids)
    for (tid, d, p), ids in ctx.tea.items():
        groups = {ctx.by_id[i].get("merge_group_id") or i for i in ids}
        if len(ids) > 1 and len(groups) > 1:
            r = ctx.by_id[ids[0]]
            out.append({"type": "teacher", "title": "تعارض معلم",
                        "detail": "المعلم «%s» عنده %d حصص في %s الحصة %d"
                                  % (r["teacher_name"], len(ids), db.DAY_NAMES[d], p),
                        "day": d, "period": p, "ids": list(ids)})
    for r in ctx.rows:
        d, p = r["day"], r["period_number"]
        if (d, p) in ctx.unavailable.get(r["teacher_id"], ()):
            out.append({"type": "unavailable", "title": "خانة غير متاحة",
                        "detail": "المعلم «%s» غير متاح في %s الحصة %d"
                                  % (r["teacher_name"], db.DAY_NAMES[d], p),
                        "day": d, "period": p, "ids": [r["id"]]})
        ok, why = ctx.slot_allowed(r["section_id"], d, p)
        if not ok:
            out.append({"type": "out_of_range", "title": "خانة خارج الدوام",
                        "detail": "«%s» لـ %s: %s"
                                  % (r["subject_name"], r["section_label"], why),
                        "day": d, "period": p, "ids": [r["id"]]})
    return out


def empty_sessions(ctx):
    """الخانات الفارغة لكل شعبة ضمن دوامها - يجب ألا تبقى بعد التوليد."""
    out = []
    names = {r["id"]: r["section_label"] for r in ctx.conn.execute(
        "SELECT se.id, se.name, g.name grade_name, CASE WHEN se.is_default = 1 THEN g.name ELSE g.name || ' / ' || se.name END section_label FROM sections se "
        "JOIN grades g ON g.id = se.grade_id ORDER BY g.sort_order, se.sort_order")}
    for sid, label in names.items():
        cells = []
        for d in ctx.days:
            for p in range(1, ctx.periods_of(sid, d) + 1):
                if p in ctx.breaks:
                    continue
                if not ctx.sec.get((sid, d, p)):
                    cells.append((d, p))
        if cells:
            out.append({"section_id": sid, "section": label,
                        "count": len(cells),
                        "cells": ["%s ح%d" % (db.DAY_NAMES[d], p) for d, p in cells[:6]]})
    return out


# ----------------------------------------------------------- الحل التلقائي

def best_slot_for(ctx, entry_id, avoid_ids=()):
    """
    أفضل خانة بديلة لهذه الحصة: خالية من التعارض، ضمن حدود اليوم،
    ويُفضَّل نفس اليوم ثم الأقرب زمنياً، وتُفضَّل الأيام غير المختصرة.
    """
    r = ctx.by_id.get(entry_id)
    if r is None:
        return None
    ignore = set(avoid_ids) | {entry_id}
    full = max(ctx.limits.values()) if ctx.limits else 0
    best = None
    for d in ctx.days:
        n = ctx.periods_of(r["section_id"], d)
        for p in range(1, n + 1):
            if p in ctx.breaks or (d, p) == (r["day"], r["period_number"]):
                continue
            if ctx.clash(r["section_id"], r["teacher_id"], d, p, ignore=ignore):
                continue
            score = (0 if d == r["day"] else 1,          # نفس اليوم أولاً
                     0 if n == full else 1,               # الأيام الكاملة قبل المختصرة
                     abs(p - r["period_number"]))         # الأقرب زمنياً
            if best is None or score < best[0]:
                best = (score, d, p)
    return None if best is None else (best[1], best[2])


def autofix(ctx, only_ids=None):
    """
    يحلّ التعارضات بنقل الحصص غير المثبّتة إلى خانات آمنة.
    يعيد قائمة بما نُقل وما تعذّر، ويذكر إن كانت الوجهة يوماً مختصراً.
    """
    moved, failed = [], []
    full = max(ctx.limits.values()) if ctx.limits else 0

    def resolve(ids, keep_first=True):
        """في مجموعة متعارضة: أبقِ واحدة وانقل الباقي."""
        ids = sorted(ids, key=lambda i: (0 if ctx.by_id[i]["is_pinned"] else 1, i))
        for rid in ids[1:] if keep_first else ids:
            r = ctx.by_id[rid]
            if r["is_pinned"]:
                failed.append({"id": rid, "why": "مثبّتة",
                               "label": "%s — %s" % (r["subject_name"],
                                                      r["section_label"])})
                continue
            pos = best_slot_for(ctx, rid)
            if pos is None:
                failed.append({"id": rid, "why": "لا توجد خانة آمنة",
                               "label": "%s — %s" % (r["subject_name"],
                                                      r["section_label"])})
                continue
            d, p = pos
            ctx.conn.execute("UPDATE schedule SET day = ?, period_number = ? WHERE id = ?",
                             (d, p, rid))
            note = ""
            if ctx.periods_of(r["section_id"], d) < full:
                note = "نُقلت إلى يوم مختصر (%s فيه %d حصص)" % (
                    db.DAY_NAMES[d], ctx.periods_of(r["section_id"], d))
            moved.append({"id": rid,
                          "label": "%s — %s" % (r["subject_name"], r["section_label"]),
                          "from": "%s ح%d" % (db.DAY_NAMES[r["day"]], r["period_number"]),
                          "to": "%s ح%d" % (db.DAY_NAMES[d], p),
                          "note": note})
            r["day"], r["period_number"] = d, p
            ctx.rebuild()

    for c in conflicts(ctx):
        ids = [i for i in c["ids"] if only_ids is None or i in only_ids]
        if not ids:
            continue
        if c["type"] in ("unavailable", "out_of_range"):
            resolve(ids, keep_first=False)
        else:
            resolve(ids, keep_first=True)

    ctx.conn.commit()
    return {"moved": moved, "failed": failed, "remaining": conflicts(ctx)}
