# -*- coding: utf-8 -*-
"""
محرّك توليد الجدول المدرسي.

الطريقة: بناء جشع مرتّب حسب صعوبة الدرس، ثم إزاحة بسلاسل (ejection chains)
لما يستحيل الوضع مباشرة، ثم تحسين محلي يقلّل العقوبات الناعمة،
مع إعادة تشغيل عشوائية متعددة ونحتفظ بأفضل نتيجة.

القيود الصارمة لا تُكسر أبداً:
  - المعلم لا يكون في مكانين في نفس الوقت
  - الشعبة لا تأخذ حصتين في نفس الوقت
  - خانات عدم إتاحة المعلم
  - الحصص المثبّتة لا تتحرّك ولا يوضع فوقها شيء
  - طاقة المعمل/القاعة المشتركة
  - الفسحات وحدود عدد الحصص اليومية لكل صف

القيود الناعمة (اختيارية، لكل منها وزن) تُحوَّل إلى عقوبة تُقلَّل قدر الإمكان.
"""
import json
import time
import random
from collections import defaultdict

import db

# ----------------------------------------------------------------- الخيارات

DEFAULT_OPTIONS = {
    "balanced_time_slots": True,        # توازن الحصص المبكرة والمتأخرة للمعلم
    "spread_across_days": True,         # توزيع المادة على أيام مختلفة
    "limit_same_class_per_day": True,   # حدّ تكرار المعلم مع نفس الشعبة يومياً
    "early_finish_day": True,           # اليوم الذهبي: يوم ينتهي فيه المعلم مبكراً
    "minimize_gaps": True,              # تقليل الفراغات بين حصص المعلم
    "no_consecutive_same_subject": True,# لا مادتان متتاليتان لنفس الشعبة
    "main_subjects_early": True,        # المواد الأساسية في الحصص المبكرة
    "respect_teacher_max_daily": True,  # احترام أقصى حصص يومية للمعلم
    "max_consecutive_periods": 3,       # أقصى حصص متتالية للمعلم
    "max_daily_periods": 6,             # أقصى حصص للمعلم في اليوم (افتراضي عام)
    "max_late_days_per_week": 3,        # أقصى أيام ينتهي فيها المعلم متأخراً
    "time_limit_seconds": 20,           # سقف زمني للتوليد
    "restarts": 6,                      # عدد إعادات التشغيل العشوائية
}

# أوزان العقوبات الناعمة
W_SPREAD = 14        # نفس المادة مرتين في اليوم لنفس الشعبة
W_ADJACENT = 10      # مادتان متتاليتان لنفس الشعبة
W_EARLY = 3          # مادة أساسية متأخرة (لكل حصة بعد العتبة)
W_MAXDAY = 60        # تجاوز أقصى حصص يومية للمعلم
W_CONSEC = 30        # تجاوز أقصى حصص متتالية
W_GAP = 8            # فراغ في يوم المعلم
W_BALANCE = 4        # اختلال بين الحصص المبكرة والمتأخرة
W_SAMECLASS = 12     # تكرار المعلم مع نفس الشعبة أكثر من مرتين يومياً
W_LATEDAY = 9        # تجاوز أيام الانتهاء المتأخر
W_NO_GOLDEN = 15     # المعلم بلا يوم ذهبي


def merge_options(raw):
    o = dict(DEFAULT_OPTIONS)
    if raw:
        for k, v in raw.items():
            if k in o:
                if isinstance(o[k], bool):
                    o[k] = bool(v)
                else:
                    try:
                        o[k] = int(v)
                    except (TypeError, ValueError):
                        pass
    return o


# ------------------------------------------------------------------- الدرس

class Lesson(object):
    """وحدة وضع واحدة: حصة مفردة أو كتلة مزدوجة."""
    __slots__ = ("uid", "assignment_id", "sections", "teachers", "subject_id",
                 "length", "room_id", "merge_group_id", "co_group_id", "label")

    def __init__(self, uid, assignment_id, sections, teachers, subject_id,
                 length, room_id, merge_group_id, co_group_id, label):
        self.uid = uid
        self.assignment_id = assignment_id
        self.sections = sections      # قد تكون أكثر من شعبة عند الدمج
        self.teachers = teachers      # قد يكون أكثر من معلم عند التدريس المشترك
        self.subject_id = subject_id
        self.length = length          # 1 أو 2
        self.room_id = room_id
        self.merge_group_id = merge_group_id
        self.co_group_id = co_group_id
        self.label = label


# ------------------------------------------------------------- تحميل البيانات

class Data(object):
    """كل ما يحتاجه المحرّك، مقروءاً مرة واحدة من قاعدة البيانات."""

    def __init__(self, conn, version_id):
        self.version_id = version_id
        s = db.get_settings(conn)
        self.settings = s
        self.days = db.working_days(s)
        self.breaks = db.break_periods(conn)

        try:
            self.core_subjects = set(json.loads(s.get("core_subject_ids") or "[]"))
        except (ValueError, TypeError):
            self.core_subjects = set()

        self.sections = {r["id"]: dict(r) for r in conn.execute(
            "SELECT s.*, g.stage_id, g.name grade_name, g.id grade_id "
            "FROM sections s JOIN grades g ON g.id = s.grade_id"
        )}
        self.subjects = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM subjects")}
        self.teachers = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM teachers")}
        self.rooms = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM rooms")}

        self.unavailable = defaultdict(set)
        for r in conn.execute("SELECT * FROM teacher_unavailable"):
            self.unavailable[r["teacher_id"]].add((r["day"], r["period_number"]))

        # الخانات المتاحة لكل شعبة
        self.section_slots = {}
        for sid, sec in self.sections.items():
            slots = set()
            for d in self.days:
                n = db.periods_for_grade_day(s, sec["grade_id"], d)
                for p in range(1, n + 1):
                    if p not in self.breaks:
                        slots.add((d, p))
            self.section_slots[sid] = slots

        # أقصى رقم حصة مستعمل - لحساب "المتأخرة"
        self.max_period = 1
        for slots in self.section_slots.values():
            for _, p in slots:
                if p > self.max_period:
                    self.max_period = p

        self.assignments = [dict(r) for r in conn.execute("SELECT * FROM assignments")]
        self.lessons = self._build_lessons()

    def _build_lessons(self):
        """يحوّل الإسنادات إلى وحدات وضع، مع دمج الشعب والتدريس المشترك."""
        lessons = []
        uid = 0

        # جمّع حسب الدمج والتدريس المشترك
        merge_groups = defaultdict(list)
        co_groups = defaultdict(list)
        plain = []
        for a in self.assignments:
            if a.get("merge_group_id"):
                merge_groups[a["merge_group_id"]].append(a)
            elif a.get("co_group_id"):
                co_groups[a["co_group_id"]].append(a)
            else:
                plain.append(a)

        def emit(assignment, sections, teachers, ppw, doubles, room_id,
                 merge_id, co_id):
            nonlocal uid
            subj = self.subjects.get(assignment["subject_id"], {})
            doubles = max(0, min(int(doubles or 0), ppw // 2))
            singles = ppw - 2 * doubles
            label = subj.get("name", "?")
            for _ in range(doubles):
                uid += 1
                lessons.append(Lesson(uid, assignment["id"], sections, teachers,
                                      assignment["subject_id"], 2, room_id,
                                      merge_id, co_id, label))
            for _ in range(singles):
                uid += 1
                lessons.append(Lesson(uid, assignment["id"], sections, teachers,
                                      assignment["subject_id"], 1, room_id,
                                      merge_id, co_id, label))

        for a in plain:
            emit(a, (a["section_id"],), (a["teacher_id"],),
                 int(a["periods_per_week"] or 0), a.get("double_periods"),
                 a.get("room_id"), None, None)

        # الدمج: عدة شعب، معلم واحد، نفس الخانة
        for gid, items in merge_groups.items():
            head = items[0]
            sections = tuple(sorted({i["section_id"] for i in items}))
            ppw = max(int(i["periods_per_week"] or 0) for i in items)
            emit(head, sections, (head["teacher_id"],), ppw,
                 head.get("double_periods"), head.get("room_id"), gid, None)

        # التدريس المشترك: شعبة واحدة، عدة معلمين
        for gid, items in co_groups.items():
            head = items[0]
            teachers = tuple(sorted({i["teacher_id"] for i in items}))
            ppw = max(int(i["periods_per_week"] or 0) for i in items)
            emit(head, (head["section_id"],), teachers, ppw,
                 head.get("double_periods"), head.get("room_id"), None, gid)

        return lessons


# ------------------------------------------------------------ فحص الجدوى

def check_feasibility(data):
    """
    يمنع التوليد على بيانات مستحيلة - نفس ما يفعله التطبيق الأصلي:
    مجموع حصص المواد لكل شعبة يجب أن يساوي عدد خاناتها.
    """
    errors, warnings = [], []

    if not data.days:
        errors.append("لم تُحدَّد أيام الدوام.")
    if not data.sections:
        errors.append("لا توجد شعب. أضف المراحل والصفوف والشعب أولاً.")
    if not data.lessons:
        errors.append("لا توجد إسنادات. أسنِد المواد للمعلمين على الشعب أولاً.")

    # لكل شعبة: المطلوب مقابل المتاح
    need = defaultdict(int)
    for L in data.lessons:
        for sid in L.sections:
            need[sid] += L.length
    for sid, sec in data.sections.items():
        have = len(data.section_slots.get(sid, ()))
        got = need.get(sid, 0)
        explicit = int(sec.get("required_periods") or 0)
        req = explicit or have
        label = "%s / %s" % (sec.get("grade_name", ""), sec.get("name", ""))

        # الفحوص مستقلة - نبلّغ عن كل مشكلة قائمة، لا عن الأولى فقط
        if got > have:
            errors.append(
                "الشعبة «%s»: مجموع حصص موادها %d وهو أكبر من خاناتها %d. "
                "قلّل نصاب مادة أو زد عدد الحصص اليومية." % (label, got, have))
        if explicit and explicit > have:
            errors.append(
                "الشعبة «%s»: المطلوب %d أكبر من الخانات المتاحة %d. "
                "عدّل المطلوب أو زد عدد الحصص اليومية." % (label, explicit, have))
        if got != req:
            warnings.append(
                "الشعبة «%s»: المُسند %d والمطلوب %d — الفارق %d %s."
                % (label, got, req, abs(got - req),
                   "حصة ستبقى فارغة" if got < req else "حصة زائدة"))

    # طاقة المعلمين
    load = defaultdict(int)
    for L in data.lessons:
        for tid in L.teachers:
            load[tid] += L.length
    for tid, total in load.items():
        t = data.teachers.get(tid)
        if not t:
            continue
        cap = int(t.get("max_periods_per_week") or 0)
        if cap and total > cap:
            warnings.append(
                "المعلم «%s»: أُسند له %d حصة ونصابه الأسبوعي %d — قد يتعثّر التوليد."
                % (t.get("name", ""), total, cap))
        avail = 0
        for d in data.days:
            n = db.periods_for_day(data.settings, d)
            for p in range(1, n + 1):
                if p in data.breaks:
                    continue
                if (d, p) not in data.unavailable.get(tid, ()):
                    avail += 1
        if total > avail:
            errors.append(
                "المعلم «%s»: أُسند له %d حصة بينما خاناته المتاحة %d. "
                "قلّل إسناده أو ارفع عنه قيود عدم الإتاحة." % (t.get("name", ""), total, avail))

    return errors, warnings


# ------------------------------------------------------------ حالة البحث

class State(object):
    def __init__(self, data):
        self.data = data
        self.sec = defaultdict(dict)    # section_id -> {(d,p): uid}
        self.tea = defaultdict(dict)    # teacher_id -> {(d,p): uid}
        self.room = defaultdict(int)    # (room_id,d,p) -> count
        self.place = {}                 # uid -> (d, p)
        self.pinned = set()             # uids مثبّتة
        self.by_uid = {L.uid: L for L in data.lessons}

    # ---- فحوص صارمة
    def cells(self, L, d, p):
        return [(d, p + k) for k in range(L.length)]

    def fits(self, L, d, p, ignore=frozenset()):
        data = self.data
        for (dd, pp) in self.cells(L, d, p):
            for sid in L.sections:
                if (dd, pp) not in data.section_slots.get(sid, ()):
                    return False
                occ = self.sec[sid].get((dd, pp))
                if occ is not None and occ != L.uid and occ not in ignore:
                    return False
            for tid in L.teachers:
                if (dd, pp) in data.unavailable.get(tid, ()):
                    return False
                occ = self.tea[tid].get((dd, pp))
                if occ is not None and occ != L.uid and occ not in ignore:
                    return False
            if L.room_id:
                cap = int(data.rooms.get(L.room_id, {}).get("capacity") or 1)
                used = self.room[(L.room_id, dd, pp)]
                if occ_room_excess(self, L, dd, pp, ignore):
                    used -= 1
                if used >= cap:
                    return False
        return True

    def blockers(self, L, d, p):
        """من يمنع وضع الدرس هنا (فقط الدروس القابلة للتحريك)."""
        out = set()
        data = self.data
        for (dd, pp) in self.cells(L, d, p):
            for sid in L.sections:
                if (dd, pp) not in data.section_slots.get(sid, ()):
                    return None          # خانة غير موجودة أصلاً
                occ = self.sec[sid].get((dd, pp))
                if occ is not None and occ != L.uid:
                    if occ in self.pinned:
                        return None
                    out.add(occ)
            for tid in L.teachers:
                if (dd, pp) in data.unavailable.get(tid, ()):
                    return None          # قيد صارم لا يُكسر
                occ = self.tea[tid].get((dd, pp))
                if occ is not None and occ != L.uid:
                    if occ in self.pinned:
                        return None
                    out.add(occ)
            if L.room_id:
                cap = int(data.rooms.get(L.room_id, {}).get("capacity") or 1)
                if self.room[(L.room_id, dd, pp)] >= cap:
                    return None
        return out

    # ---- وضع وإزالة
    def put(self, L, d, p):
        for (dd, pp) in self.cells(L, d, p):
            for sid in L.sections:
                self.sec[sid][(dd, pp)] = L.uid
            for tid in L.teachers:
                self.tea[tid][(dd, pp)] = L.uid
            if L.room_id:
                self.room[(L.room_id, dd, pp)] += 1
        self.place[L.uid] = (d, p)

    def pull(self, L):
        pos = self.place.pop(L.uid, None)
        if pos is None:
            return None
        d, p = pos
        for (dd, pp) in self.cells(L, d, p):
            for sid in L.sections:
                if self.sec[sid].get((dd, pp)) == L.uid:
                    del self.sec[sid][(dd, pp)]
            for tid in L.teachers:
                if self.tea[tid].get((dd, pp)) == L.uid:
                    del self.tea[tid][(dd, pp)]
            if L.room_id:
                self.room[(L.room_id, dd, pp)] -= 1
        return pos


def occ_room_excess(state, L, d, p, ignore):
    """هل أحد المتجاهَلين يشغل نفس المعمل في هذه الخانة؟"""
    if not ignore or not L.room_id:
        return False
    for uid in ignore:
        other = state.by_uid.get(uid)
        if other is None or other.room_id != L.room_id:
            continue
        pos = state.place.get(uid)
        if pos and (d, p) in state.cells(other, pos[0], pos[1]):
            return True
    return False


# ------------------------------------------------------------ العقوبة

def local_cost(state, L, d, p, opt):
    """تكلفة وضع هذا الدرس هنا - تُستعمل لترتيب الخانات المرشّحة."""
    data = state.data
    cost = 0.0
    cells = state.cells(L, d, p)

    # ---- على مستوى الشعبة
    for sid in L.sections:
        grid = state.sec[sid]
        if opt["spread_across_days"]:
            same_day = 0
            for (dd, pp), uid in grid.items():
                if dd != d or uid == L.uid:
                    continue
                other = state.by_uid.get(uid)
                if other is not None and other.subject_id == L.subject_id:
                    same_day += 1
            if same_day:
                cost += W_SPREAD * same_day
        if opt["no_consecutive_same_subject"]:
            edges = [cells[0][1] - 1, cells[-1][1] + 1]
            for pp in edges:
                uid = grid.get((d, pp))
                if uid is None or uid == L.uid:
                    continue
                other = state.by_uid.get(uid)
                if other is not None and other.subject_id == L.subject_id:
                    cost += W_ADJACENT
        if opt["main_subjects_early"] and L.subject_id in data.core_subjects:
            threshold = max(1, data.max_period // 2)
            for (_, pp) in cells:
                if pp > threshold:
                    cost += W_EARLY * (pp - threshold)

    # ---- على مستوى المعلم
    for tid in L.teachers:
        grid = state.tea[tid]
        t = data.teachers.get(tid, {})
        day_periods = sorted(pp for (dd, pp), uid in grid.items()
                             if dd == d and uid != L.uid)
        merged = sorted(set(day_periods) | {c[1] for c in cells})

        if opt["respect_teacher_max_daily"]:
            cap = int(t.get("max_periods_per_day") or opt["max_daily_periods"])
            if cap and len(merged) > cap:
                cost += W_MAXDAY * (len(merged) - cap)

        run = longest_run(merged)
        if run > opt["max_consecutive_periods"]:
            cost += W_CONSEC * (run - opt["max_consecutive_periods"])

        if opt["minimize_gaps"] and merged:
            span = merged[-1] - merged[0] + 1
            cost += W_GAP * (span - len(merged))

        if opt["balanced_time_slots"]:
            half = max(1, data.max_period // 2)
            early = sum(1 for (dd, pp), uid in grid.items()
                        if uid != L.uid and pp <= half)
            late = sum(1 for (dd, pp), uid in grid.items()
                       if uid != L.uid and pp > half)
            for (_, pp) in cells:
                if pp <= half:
                    early += 1
                else:
                    late += 1
            cost += W_BALANCE * abs(early - late) / max(1, early + late)

        if opt["limit_same_class_per_day"]:
            for sid in L.sections:
                n = 0
                for (dd, pp), uid in grid.items():
                    if dd != d or uid == L.uid:
                        continue
                    other = state.by_uid.get(uid)
                    if other is not None and sid in other.sections:
                        n += 1
                n += L.length
                if n > 2:
                    cost += W_SAMECLASS * (n - 2)

        if opt["max_late_days_per_week"]:
            late_edge = data.max_period - 1
            if any(pp >= late_edge for (_, pp) in cells):
                late_days = {dd for (dd, pp), uid in grid.items()
                             if uid != L.uid and pp >= late_edge}
                late_days.add(d)
                if len(late_days) > opt["max_late_days_per_week"]:
                    cost += W_LATEDAY * (len(late_days) - opt["max_late_days_per_week"])

        # اليوم الذهبي: لا تفتح آخر يوم فارغ للمعلم بلا داعٍ
        if opt["early_finish_day"] and not day_periods:
            busy = {dd for (dd, _), uid in grid.items() if uid != L.uid}
            if len(busy) >= len(data.days) - 1:
                cost += W_NO_GOLDEN

    return cost


def longest_run(sorted_periods):
    best = run = 0
    prev = None
    for p in sorted_periods:
        if prev is not None and p == prev + 1:
            run += 1
        else:
            run = 1
        prev = p
        if run > best:
            best = run
    return best


def total_cost(state, opt):
    """التكلفة الكلية - للمقارنة بين إعادات التشغيل."""
    data = state.data
    cost = 0.0

    for sid, grid in state.sec.items():
        per_day = defaultdict(list)
        for (d, p), uid in grid.items():
            per_day[d].append((p, uid))
        threshold = max(1, data.max_period // 2)
        for d, items in per_day.items():
            items.sort()
            # كم درساً مميزاً لكل مادة في هذا اليوم (الكتلة المزدوجة درس واحد)
            distinct = defaultdict(set)
            for p, uid in items:
                L = state.by_uid.get(uid)
                if L is None:
                    continue
                distinct[L.subject_id].add(uid)
                if opt["main_subjects_early"] and L.subject_id in data.core_subjects:
                    if p > threshold:
                        cost += W_EARLY * (p - threshold)
            if opt["no_consecutive_same_subject"]:
                for i in range(len(items) - 1):
                    p, uid = items[i]
                    np_, nuid = items[i + 1]
                    if np_ != p + 1 or nuid == uid:
                        continue
                    a, b = state.by_uid.get(uid), state.by_uid.get(nuid)
                    if a is not None and b is not None and a.subject_id == b.subject_id:
                        cost += W_ADJACENT
            if opt["spread_across_days"]:
                for uids in distinct.values():
                    if len(uids) > 1:
                        cost += W_SPREAD * (len(uids) - 1)

    for tid, grid in state.tea.items():
        t = data.teachers.get(tid, {})
        per_day = defaultdict(list)
        for (d, p), uid in grid.items():
            per_day[d].append(p)
        golden = False
        late_days = 0
        half = max(1, data.max_period // 2)
        early = late = 0
        for d in data.days:
            ps = sorted(set(per_day.get(d, ())))
            if not ps:
                golden = True
                continue
            cap = int(t.get("max_periods_per_day") or opt["max_daily_periods"])
            if opt["respect_teacher_max_daily"] and cap and len(ps) > cap:
                cost += W_MAXDAY * (len(ps) - cap)
            run = longest_run(ps)
            if run > opt["max_consecutive_periods"]:
                cost += W_CONSEC * (run - opt["max_consecutive_periods"])
            if opt["minimize_gaps"]:
                cost += W_GAP * (ps[-1] - ps[0] + 1 - len(ps))
            if ps[-1] <= half:
                golden = True
            if ps[-1] >= data.max_period - 1:
                late_days += 1
            early += sum(1 for p in ps if p <= half)
            late += sum(1 for p in ps if p > half)
        if opt["early_finish_day"] and not golden:
            cost += W_NO_GOLDEN
        if opt["max_late_days_per_week"] and late_days > opt["max_late_days_per_week"]:
            cost += W_LATEDAY * (late_days - opt["max_late_days_per_week"])
        if opt["balanced_time_slots"] and (early + late):
            cost += W_BALANCE * abs(early - late) / (early + late) * 10
    return cost


# ------------------------------------------------------------ البناء

def candidate_slots(state, L):
    """كل الخانات التي يصلح فيها هذا الدرس (قيود صارمة فقط)."""
    data = state.data
    base = None
    for sid in L.sections:
        s = data.section_slots.get(sid, set())
        base = set(s) if base is None else (base & s)
    if not base:
        return []
    out = []
    for (d, p) in base:
        if L.length == 2 and (d, p + 1) not in base:
            continue
        if state.fits(L, d, p):
            out.append((d, p))
    return out


def difficulty(state, L):
    """كم خانة تصلح لهذا الدرس - الأقل أولاً (MRV)."""
    return len(candidate_slots(state, L))


def try_place(state, L, opt, rng, top_k=4):
    cands = candidate_slots(state, L)
    if not cands:
        return False
    scored = [(local_cost(state, L, d, p, opt), d, p) for (d, p) in cands]
    scored.sort(key=lambda x: x[0])
    pick = scored[:top_k]
    _, d, p = pick[rng.randrange(len(pick))] if len(pick) > 1 else pick[0]
    state.put(L, d, p)
    return True


def try_eject(state, L, opt, rng, depth=2):
    """
    لا توجد خانة حرة: ابحث عن خانة يمنعها درس واحد قابل للتحريك،
    أزِحه وضَع هذا مكانه، ثم أعد إسكان المُزاح. سلسلة إزاحة محدودة العمق.
    """
    data = state.data
    base = None
    for sid in L.sections:
        s = data.section_slots.get(sid, set())
        base = set(s) if base is None else (base & s)
    if not base:
        return False

    options = []
    for (d, p) in base:
        if L.length == 2 and (d, p + 1) not in base:
            continue
        blk = state.blockers(L, d, p)
        if blk is None or not blk:
            continue
        if len(blk) <= 2:
            options.append((len(blk), local_cost(state, L, d, p, opt), d, p, blk))
    if not options:
        return False
    options.sort(key=lambda x: (x[0], x[1]))

    for _, _, d, p, blk in options[:6]:
        moved = []
        for uid in blk:
            other = state.by_uid[uid]
            pos = state.pull(other)
            moved.append((other, pos))

        if state.fits(L, d, p):
            state.put(L, d, p)
            failed = [other for other, _ in moved
                      if not try_place(state, other, opt, rng)
                      and not (depth > 0 and try_eject(state, other, opt, rng, depth - 1))]
            if not failed:
                return True
            state.pull(L)          # تراجع: أعِد كل شيء لمكانه

        for other, pos in moved:
            if other.uid not in state.place and pos and state.fits(other, pos[0], pos[1]):
                state.put(other, pos[0], pos[1])
    return False


def construct(data, opt, rng, pinned):
    state = State(data)

    # المثبّتة أولاً - تُوضع ثم يُبنى الجدول حولها
    for uid, (d, p) in pinned.items():
        L = state.by_uid.get(uid)
        if L is not None and state.fits(L, d, p):
            state.put(L, d, p)
            state.pinned.add(uid)

    rest = [L for L in data.lessons if L.uid not in state.place]
    # الأصعب أولاً: كتلة مزدوجة، ثم أقل الخانات، ثم معلم مثقل
    rest.sort(key=lambda L: (-L.length, difficulty(state, L), rng.random()))

    unplaced = []
    for L in rest:
        if not try_place(state, L, opt, rng):
            if not try_eject(state, L, opt, rng):
                unplaced.append(L)
    return state, unplaced


def repair_unplaced(state, opt, rng, unplaced):
    """محاولة إسكان ما لم يُوضع، مباشرة أو بسلسلة إزاحة."""
    still = []
    for L in unplaced:
        if L.uid in state.place:
            continue
        if try_place(state, L, opt, rng) or try_eject(state, L, opt, rng, depth=3):
            continue
        still.append(L)
    return still


def snapshot(state):
    return dict(state.place)


def restore(state, snap):
    for uid in list(state.place.keys()):
        state.pull(state.by_uid[uid])
    for uid, (d, p) in snap.items():
        L = state.by_uid[uid]
        if state.fits(L, d, p):
            state.put(L, d, p)


def movable(state):
    return [state.by_uid[u] for u in state.place.keys() if u not in state.pinned]


SAMPLE = 26      # كم خانة مرشّحة نجرّب في كل خطوة


def swap_partners(state, L, rng, limit=30):
    """
    في جدول ممتلئ لا توجد خانات فارغة، فالحركة الوحيدة المجدية هي التبديل.
    أفضل شريك: درس في نفس الشعبة - التبديل يُبقي توازن الشعبة كما هو
    ولا يحرّك إلا المعلمين.
    """
    out, seen = [], {L.uid}
    for sid in L.sections:
        for uid in state.sec[sid].values():
            if uid in seen or uid in state.pinned:
                continue
            M = state.by_uid.get(uid)
            if M is None or M.length != L.length:
                continue
            seen.add(uid)
            out.append(M)
    rng.shuffle(out)
    return out[:limit]


def exchange(state, L, M):
    """بدّل خانتَي درسين إن سمحت القيود الصارمة. يعيد True عند النجاح."""
    pos_l = state.place.get(L.uid)
    pos_m = state.place.get(M.uid)
    if pos_l is None or pos_m is None or pos_l == pos_m:
        return False
    state.pull(L)
    state.pull(M)
    if state.fits(L, pos_m[0], pos_m[1]) and state.fits(M, pos_l[0], pos_l[1]):
        state.put(L, pos_m[0], pos_m[1])
        state.put(M, pos_l[0], pos_l[1])
        return True
    state.put(L, pos_l[0], pos_l[1])
    state.put(M, pos_m[0], pos_m[1])
    return False


def swap_gain(state, L, M, opt):
    """كم تنخفض التكلفة لو بدّلنا؟ (موجب = تحسّن). لا يغيّر الحالة."""
    pos_l = state.place.get(L.uid)
    pos_m = state.place.get(M.uid)
    if pos_l is None or pos_m is None or pos_l == pos_m:
        return None
    state.pull(L)
    state.pull(M)
    gain = None
    if state.fits(L, pos_m[0], pos_m[1]) and state.fits(M, pos_l[0], pos_l[1]):
        base = (local_cost(state, L, pos_l[0], pos_l[1], opt) +
                local_cost(state, M, pos_m[0], pos_m[1], opt))
        new = (local_cost(state, L, pos_m[0], pos_m[1], opt) +
               local_cost(state, M, pos_l[0], pos_l[1], opt))
        gain = base - new
    state.put(L, pos_l[0], pos_l[1])
    state.put(M, pos_m[0], pos_m[1])
    return gain


def hill_climb(state, opt, rng, deadline, stale_limit=700):
    """نقل لخانة فارغة إن وُجدت، وإلا تبديل. كل خطوة تقلّل التكلفة."""
    pool = movable(state)
    if not pool:
        return
    stale = 0
    while time.time() < deadline and stale < stale_limit:
        L = pool[rng.randrange(len(pool))]
        old = state.place.get(L.uid)
        if old is None:
            stale += 1
            continue

        # --- نقل إلى خانة فارغة (يعمل فقط في الجداول غير الممتلئة)
        state.pull(L)
        best_cost = local_cost(state, L, old[0], old[1], opt)
        best_pos = old
        cands = candidate_slots(state, L)
        if len(cands) > SAMPLE:
            cands = rng.sample(cands, SAMPLE)
        for (d, p) in cands:
            c = local_cost(state, L, d, p, opt)
            if c < best_cost - 1e-9:
                best_cost, best_pos = c, (d, p)
        state.put(L, best_pos[0], best_pos[1])
        if best_pos != old:
            stale = 0
            continue

        # --- تبديل مع أفضل شريك من نفس الشعبة
        best_gain, best_partner = 1e-9, None
        for M in swap_partners(state, L, rng):
            g = swap_gain(state, L, M, opt)
            if g is not None and g > best_gain:
                best_gain, best_partner = g, M
        if best_partner is not None and exchange(state, L, best_partner):
            stale = 0
        else:
            stale += 1


def teacher_days(state, tid):
    per_day = defaultdict(list)
    for (d, p), uid in state.tea[tid].items():
        per_day[d].append((p, uid))
    for d in per_day:
        per_day[d].sort()
    return per_day


def golden_day_pass(state, opt, rng):
    """
    اليوم الذهبي لا يتحقّق بنقل درس واحد - لا بد من إخلاء يوم كامل.
    لكل معلم بلا يوم ذهبي: جرّب إخلاء أخفّ أيامه دفعةً واحدة.
    """
    if not opt["early_finish_day"]:
        return
    data = state.data
    half = max(1, data.max_period // 2)

    for tid in list(state.tea.keys()):
        per_day = teacher_days(state, tid)
        if any(not per_day.get(d) for d in data.days):
            continue                                   # عنده يوم فارغ أصلاً
        if any(per_day[d] and per_day[d][-1][0] <= half for d in per_day):
            continue                                   # ينتهي مبكراً في يوم ما
        order = sorted((d for d in per_day if per_day[d]),
                       key=lambda d: len(per_day[d]))
        for d in order[:2]:
            uids = [u for _, u in per_day[d] if u not in state.pinned]
            if not uids or len(uids) != len(per_day[d]):
                continue
            before = snapshot(state)
            lessons = [state.by_uid[u] for u in dict.fromkeys(uids)]

            # أخرِج كل درس من هذا اليوم بتبديله مع درس في يوم آخر
            cleared = True
            for L in lessons:
                pos = state.place.get(L.uid)
                if pos is None or pos[0] != d:
                    continue
                moved = False
                for M in swap_partners(state, L, rng, limit=60):
                    pos_m = state.place.get(M.uid)
                    if pos_m is None or pos_m[0] == d:
                        continue                       # لا فائدة، نفس اليوم
                    if exchange(state, L, M):
                        moved = True
                        break
                if not moved:
                    cleared = False
                    break

            if cleared and not any(dd == d for (dd, _) in state.tea[tid]):
                break                                  # نجح الإخلاء
            restore(state, before)                     # تراجع كامل


def gap_pass(state, opt, rng):
    """ضغط أيام المعلمين: انقل الدرس المعزول ليلتصق ببقية يومه."""
    if not opt["minimize_gaps"]:
        return
    for tid in list(state.tea.keys()):
        per_day = teacher_days(state, tid)
        for d, items in per_day.items():
            if len(items) < 2:
                continue
            ps = [p for p, _ in items]
            if ps[-1] - ps[0] + 1 == len(set(ps)):
                continue                               # اليوم مضغوط أصلاً
            for _, uid in list(items):
                L = state.by_uid.get(uid)
                if L is None or uid in state.pinned or uid not in state.place:
                    continue
                old = state.place[uid]

                # نقل لخانة فارغة أفضل
                state.pull(L)
                best_cost, best_pos = local_cost(state, L, old[0], old[1], opt), old
                for (dd, pp) in candidate_slots(state, L):
                    c = local_cost(state, L, dd, pp, opt)
                    if c < best_cost - 1e-9:
                        best_cost, best_pos = c, (dd, pp)
                state.put(L, best_pos[0], best_pos[1])
                if best_pos != old:
                    continue

                # وإلا: تبديل يقلّل الفراغ
                best_gain, best_partner = 1e-9, None
                for M in swap_partners(state, L, rng):
                    g = swap_gain(state, L, M, opt)
                    if g is not None and g > best_gain:
                        best_gain, best_partner = g, M
                if best_partner is not None:
                    exchange(state, L, best_partner)


def perturb(state, opt, rng, n=8):
    """ركلة عشوائية للخروج من القاع المحلي: تبديلات بلا نظر للتكلفة."""
    pool = movable(state)
    if not pool:
        return
    for _ in range(min(n, len(pool))):
        L = pool[rng.randrange(len(pool))]
        if L.uid not in state.place:
            continue
        for M in swap_partners(state, L, rng, limit=8):
            if exchange(state, L, M):      # آمن: يتراجع تلقائياً إن تعذّر
                break


def improve(state, opt, rng, deadline, unplaced):
    """
    بحث محلي متكرّر: تسلّق التل، ثم تمريرات موجّهة (اليوم الذهبي والفراغات)،
    ثم ركلة عشوائية - ونحتفظ دائماً بأفضل حالة رأيناها.
    """
    still = repair_unplaced(state, opt, rng, unplaced)
    if not state.place:
        return still

    hill_climb(state, opt, rng, deadline)
    golden_day_pass(state, opt, rng)
    gap_pass(state, opt, rng)

    # اللقطة وقائمة غير الموضوع يجب أن تُحفظا وتُستعادا معاً،
    # وإلا ضاع درس: نعود لحالة ينقصه فيها درس بينما القائمة فارغة.
    best_snap = snapshot(state)
    best_still = list(still)
    best_score = total_cost(state, opt)

    while time.time() < deadline:
        perturb(state, opt, rng, n=rng.randrange(5, 14))
        hill_climb(state, opt, rng, min(deadline, time.time() + 2.0), stale_limit=400)
        golden_day_pass(state, opt, rng)
        gap_pass(state, opt, rng)
        if still:
            still = repair_unplaced(state, opt, rng, still)

        score = total_cost(state, opt)
        # الأولوية لتقليل غير الموضوع، ثم لتقليل العقوبة
        if (len(still), score) < (len(best_still), best_score - 1e-9):
            best_score, best_snap, best_still = score, snapshot(state), list(still)
        else:
            restore(state, best_snap)
            still = list(best_still)

    restore(state, best_snap)
    still = repair_unplaced(state, opt, rng, list(best_still))
    return still


def generate(data, opt, pinned=None, seed=None):
    """يشغّل عدة محاولات ويعيد أفضل حالة."""
    opt = merge_options(opt)
    pinned = pinned or {}
    deadline = time.time() + max(3, int(opt["time_limit_seconds"]))

    best = None
    attempts = 0
    restarts = max(1, int(opt["restarts"]))
    for i in range(restarts):
        if time.time() >= deadline and best is not None:
            break
        rng = random.Random((seed or 0) + i * 7919)
        attempts += 1
        state, unplaced = construct(data, opt, rng, pinned)
        # وزّع ما تبقّى من الوقت على المحاولات المتبقية
        share = max(1.0, (deadline - time.time()) / max(1, restarts - i))
        left = improve(state, opt, rng, min(deadline, time.time() + share), unplaced)
        score = (len(left), total_cost(state, opt))
        if best is None or score < best[0]:
            best = (score, state, left)
        if score[0] == 0 and score[1] == 0:
            break

    (nleft, cost), state, left = best

    # شبكة أمان: أي درس ليس في الجدول ولا في قائمة المتعذّر يُضاف إليها،
    # فلا يختفي درس بصمت من التقرير.
    reported = {L.uid for L in left}
    for L in data.lessons:
        if L.uid not in state.place and L.uid not in reported:
            left.append(L)

    return {
        "state": state,
        "unplaced": left,
        "cost": cost,
        "attempts": attempts,
        "placed": len(state.place),
        "total": len(data.lessons),
    }


def explain_unplaced(state, L):
    """لماذا لم تُوضع هذه الحصة - رسالة عربية مفهومة."""
    data = state.data
    sec_names = "، ".join(
        "%s/%s" % (data.sections.get(s, {}).get("grade_name", ""),
                   data.sections.get(s, {}).get("name", ""))
        for s in L.sections)
    tea_names = "، ".join(data.teachers.get(t, {}).get("name", "") for t in L.teachers)
    free_sec = free_tea = 0
    base = None
    for sid in L.sections:
        s = data.section_slots.get(sid, set())
        base = set(s) if base is None else (base & s)
    base = base or set()
    for (d, p) in base:
        if all(state.sec[sid].get((d, p)) is None for sid in L.sections):
            free_sec += 1
        if all(state.tea[tid].get((d, p)) is None
               and (d, p) not in data.unavailable.get(tid, ())
               for tid in L.teachers):
            free_tea += 1
    if free_sec == 0:
        why = "لا توجد خانة فارغة في الشعبة."
    elif free_tea == 0:
        why = "المعلم مشغول أو غير متاح في كل الخانات الممكنة."
    else:
        why = ("لا توجد خانة يخلو فيها المعلم والشعبة معاً. الخيارات: تقليل نصاب "
               "المادة، أو رفع استثناء عن المعلم، أو إعادة التوليد.")
    return {
        "subject": L.label,
        "sections": sec_names,
        "teachers": tea_names,
        "length": L.length,
        "reason": why,
    }


# ------------------------------------------------------------ كشف التعارض

def detect_conflicts(conn, version_id):
    """تعارضات الجدول الحالي - تُستعمل بعد التعديل اليدوي."""
    rows = [dict(r) for r in conn.execute(
        "SELECT sc.*, t.name teacher_name, sb.name subject_name, "
        "       se.name section_name, g.name grade_name, "
            "       CASE WHEN se.is_default = 1 THEN g.name ELSE g.name || ' / ' || se.name END section_label "
        "FROM schedule sc "
        "JOIN teachers t  ON t.id  = sc.teacher_id "
        "JOIN subjects sb ON sb.id = sc.subject_id "
        "JOIN sections se ON se.id = sc.section_id "
        "JOIN grades   g  ON g.id  = se.grade_id "
        "WHERE sc.version_id = ?", (version_id,))]

    out = []
    by_teacher = defaultdict(list)
    by_section = defaultdict(list)
    for r in rows:
        by_teacher[(r["teacher_id"], r["day"], r["period_number"])].append(r)
        by_section[(r["section_id"], r["day"], r["period_number"])].append(r)

    for (tid, d, p), items in by_teacher.items():
        groups = {i.get("merge_group_id") or i["id"] for i in items}
        if len(items) > 1 and len(groups) > 1:
            out.append({
                "type": "teacher",
                "title": "تعارض معلم",
                "detail": "المعلم «%s» لديه %d حصص في %s الحصة %d"
                          % (items[0]["teacher_name"], len(items),
                             db.DAY_NAMES[d], p),
                "day": d, "period": p,
                "ids": [i["id"] for i in items],
            })
    for (sid, d, p), items in by_section.items():
        if len(items) > 1:
            out.append({
                "type": "section",
                "title": "تعارض شعبة",
                "detail": "الفصل «%s» لديه %d حصص في %s الحصة %d"
                          % (items[0]["section_label"],
                             len(items), db.DAY_NAMES[d], p),
                "day": d, "period": p,
                "ids": [i["id"] for i in items],
            })

    # عدم إتاحة المعلم
    unav = defaultdict(set)
    for r in conn.execute("SELECT * FROM teacher_unavailable"):
        unav[r["teacher_id"]].add((r["day"], r["period_number"]))
    for r in rows:
        if (r["day"], r["period_number"]) in unav.get(r["teacher_id"], ()):
            out.append({
                "type": "unavailable",
                "title": "خانة غير متاحة",
                "detail": "المعلم «%s» غير متاح في %s الحصة %d"
                          % (r["teacher_name"], db.DAY_NAMES[r["day"]],
                             r["period_number"]),
                "day": r["day"], "period": r["period_number"],
                "ids": [r["id"]],
            })
    return out
