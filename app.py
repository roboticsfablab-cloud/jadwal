# -*- coding: utf-8 -*-
"""
جدول المدرسة - نسخة محلية للمدير
تشغيل: python app.py   ثم افتح http://127.0.0.1:5000
"""
import io
import json
import webbrowser
from collections import defaultdict
from datetime import datetime, timedelta
from threading import Timer

from flask import (Flask, render_template, request, redirect, url_for,
                   jsonify, flash, send_file, abort)

import db
import solver
import exports
import edits

app = Flask(__name__)
app.jinja_env.globals["DAY_NAMES"] = db.DAY_NAMES
app.jinja_env.globals["now"] = lambda: datetime.now()

# ---------------------------------------------------------------- الحماية
#
# محلياً: لا كلمة مرور (لا حاجة لها على جهازك).
# على الإنترنت: اضبط APP_PASSWORD فيُطلب تسجيل الدخول لكل الصفحات.
import hashlib
import hmac
import os
from functools import wraps

from flask import session

APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
app.secret_key = os.environ.get("SECRET_KEY") or (
    "jadwal-" + hashlib.sha256(
        (APP_PASSWORD or "local").encode("utf-8")).hexdigest())
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("VERCEL")),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

OPEN_ENDPOINTS = {"login", "static"}

_schema_ready = False


@app.before_request
def ensure_schema():
    """
    على الإنترنت قد تبدأ نسخة جديدة من التطبيق في أي لحظة. نتأكد مرة
    واحدة لكل نسخة أن الجداول موجودة — استعلام واحد رخيص، وإن غابت
    الجداول ننشئها. محلياً init_db يتكفّل بذلك عند التشغيل.
    """
    global _schema_ready
    if _schema_ready:
        return None
    try:
        conn = db.connect()
        conn.execute("SELECT 1 FROM settings LIMIT 1").fetchone()
        conn.close()
    except Exception:
        try:
            db.init_db()
        except Exception as e:
            app.logger.warning("تعذّر تجهيز قاعدة البيانات: %s", e)
    _schema_ready = True
    return None


@app.before_request
def require_login():
    if not APP_PASSWORD:
        return None                       # تشغيل محلي: مفتوح
    if request.endpoint in OPEN_ENDPOINTS:
        return None
    if session.get("ok"):
        return None
    return redirect(url_for("login", next=request.full_path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not APP_PASSWORD:
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        given = (request.form.get("password") or "").strip()
        # نقارن بالبايتات: compare_digest يرفض النصوص غير اللاتينية،
        # وكلمة المرور قد تكون عربية.
        if hmac.compare_digest(given.encode("utf-8"),
                               APP_PASSWORD.encode("utf-8")):
            session.permanent = True
            session["ok"] = True
            nxt = request.args.get("next") or request.form.get("next") or ""
            # لا نعيد التوجيه إلا لمسار داخلي
            if nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            return redirect(url_for("home"))
        error = "كلمة المرور غير صحيحة"
    return render_template("login.html", error=error,
                           next=request.args.get("next", "")), (401 if error else 200)


@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.context_processor
def inject_auth():
    return {"auth_on": bool(APP_PASSWORD)}


# ------------------------------------------------------------------ helpers

def q(sql, args=()):
    conn = db.connect()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def one(sql, args=()):
    conn = db.connect()
    row = conn.execute(sql, args).fetchone()
    conn.close()
    return dict(row) if row else None


def run(sql, args=()):
    conn = db.connect()
    cur = conn.execute(sql, args)
    conn.commit()
    last = cur.lastrowid
    conn.close()
    return last


def current_version():
    v = db.active_version()
    return v["id"] if v else None


def i(name, default=0):
    try:
        return int(request.form.get(name, default) or default)
    except (TypeError, ValueError):
        return default


def s(name, default=""):
    return (request.form.get(name, default) or default).strip()


def grid_context():
    """كل ما تحتاجه شاشات الجدول: الأيام والحصص والشعب."""
    st = db.get_settings()
    days = db.working_days(st)
    conn = db.connect()
    slots = [dict(r) for r in conn.execute(
        "SELECT * FROM time_slots ORDER BY period_number")]
    conn.close()
    max_p = max([int(st.get("periods_per_day") or 7)] +
                [x["period_number"] for x in slots] or [7])
    return st, days, slots, max_p


NAV_TABLES = ("stages", "grades", "sections", "subjects", "teachers", "assignments")


@app.context_processor
def inject_nav():
    """
    يُستدعى لكل صفحة. كان يفتح ثلاثة اتصالات ويُصدر عشرة استعلامات —
    وهذا لا يُلاحظ محلياً، لكنه يعني ثانية كاملة عندما تكون القاعدة
    على الإنترنت. الآن: اتصال واحد وثلاثة استعلامات.
    """
    conn = db.connect()
    versions = [dict(r) for r in conn.execute("SELECT * FROM versions ORDER BY id")]
    active = next((v for v in versions if v.get("is_active")), None) \
        or (versions[0] if versions else None)
    vid = active["id"] if active else 0

    # كل الأعداد في استعلام واحد بدل سبعة
    parts = ", ".join("(SELECT COUNT(*) FROM %s) %s" % (t, t) for t in NAV_TABLES)
    row = conn.execute(
        "SELECT %s, (SELECT COUNT(*) FROM schedule WHERE version_id = ?) schedule"
        % parts, (vid,)).fetchone()
    counts = {k: row[k] for k in list(NAV_TABLES) + ["schedule"]}

    settings = db.get_settings(conn)
    conn.close()
    return {"nav_counts": counts, "active_version": active,
            "all_versions": versions,
            "school_name": settings.get("school_name") or "مدرستي"}


# ------------------------------------------------------------------- الرئيسية

@app.route("/")
def home():
    st = db.get_settings()
    vid = current_version()
    steps = []
    c = inject_nav()["nav_counts"]
    steps.append(("إعدادات المدرسة والأيام والحصص",
                  bool(st.get("school_name")), url_for("settings_page")))
    steps.append(("المراحل والصفوف والشعب", c["sections"] > 0, url_for("structure")))
    steps.append(("المواد الدراسية", c["subjects"] > 0, url_for("subjects_page")))
    steps.append(("المعلمون", c["teachers"] > 0, url_for("teachers_page")))
    steps.append(("إسناد المواد للمعلمين", c["assignments"] > 0, url_for("assignments_page")))
    steps.append(("توليد الجدول", c["schedule"] > 0, url_for("generate_page")))

    conn = db.connect()
    data = solver.Data(conn, vid) if vid else None
    conn.close()
    errors, warnings = solver.check_feasibility(data) if data else ([], [])

    done = sum(1 for _, ok, _ in steps if ok)
    return render_template("home.html", steps=steps, done=done,
                           total=len(steps), errors=errors, warnings=warnings)


# ------------------------------------------------------------------ الإعدادات

@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        conn = db.connect()
        db.set_setting("school_name", s("school_name"), conn)
        db.set_setting("manager_name", s("manager_name"), conn)
        db.set_setting("school_stage_label", s("school_stage_label"), conn)
        days = request.form.getlist("working_days")
        db.set_setting("working_days", ",".join(days) if days else "0,1,2,3,4", conn)
        db.set_setting("periods_per_day", max(1, i("periods_per_day", 7)), conn)

        per_day = {}
        for d in range(7):
            raw = request.form.get("pd_%d" % d, "").strip()
            if raw:
                try:
                    per_day[str(d)] = int(raw)
                except ValueError:
                    pass
        db.set_setting("periods_per_day_map", json.dumps(per_day), conn)

        gmap = {}
        for g in conn.execute("SELECT id FROM grades"):
            raw = request.form.get("gp_%d" % g["id"], "").strip()
            if raw:
                try:
                    gmap[str(g["id"])] = int(raw)
                except ValueError:
                    pass
        db.set_setting("grade_periods_map", json.dumps(gmap), conn)
        db.set_setting("core_subject_ids",
                       json.dumps([int(x) for x in request.form.getlist("core_subjects")]),
                       conn)
        conn.commit()

        # تقليل حصص يوم قد يترك حصصاً مجدولة خارج الدوام - نبلّغ فوراً
        ctx = edits.Ctx(conn, current_version())
        orphans = [c for c in edits.conflicts(ctx) if c["type"] == "out_of_range"]
        conn.close()
        flash("تم حفظ الإعدادات", "ok")
        if orphans:
            flash("تنبيه: %d حصة مجدولة صارت خارج حدود أيامها بعد هذا التغيير. "
                  "افتح الجدول العام واضغط «حل كل التعارضات تلقائياً»، أو أعد "
                  "التوليد — وإن لم تتسع الخانات فقلّل نصاب مادة أولاً."
                  % len(orphans), "err")
        return redirect(url_for("settings_page"))

    st = db.get_settings()
    return render_template(
        "settings.html", st=st,
        days=db.working_days(st),
        per_day=json.loads(st.get("periods_per_day_map") or "{}"),
        gmap=json.loads(st.get("grade_periods_map") or "{}"),
        core=set(json.loads(st.get("core_subject_ids") or "[]")),
        grades=q("SELECT g.*, st.name stage_name FROM grades g "
                 "JOIN stages st ON st.id = g.stage_id ORDER BY st.sort_order, g.sort_order"),
        subjects=q("SELECT * FROM subjects ORDER BY sort_order, id"),
        slots=q("SELECT * FROM time_slots ORDER BY period_number"))


@app.route("/settings/slots", methods=["POST"])
def save_slots():
    count = max(1, i("count", 7))
    conn = db.connect()
    conn.execute("DELETE FROM time_slots WHERE period_number > ?", (count,))
    for p in range(1, count + 1):
        conn.execute(
            "INSERT INTO time_slots(period_number, name) VALUES (?, ?) "
            "ON CONFLICT(period_number) DO NOTHING", (p, "الحصة %d" % p))
        conn.execute(
            "UPDATE time_slots SET start_time = ?, end_time = ?, is_break = ?, name = ? "
            "WHERE period_number = ?",
            (request.form.get("start_%d" % p, ""), request.form.get("end_%d" % p, ""),
             1 if request.form.get("break_%d" % p) else 0,
             request.form.get("name_%d" % p, "") or "الحصة %d" % p, p))
    db.set_setting("periods_per_day", count, conn)
    conn.commit()
    conn.close()
    flash("تم حفظ توقيت الحصص", "ok")
    return redirect(url_for("settings_page"))


# --------------------------------------------- المراحل / الصفوف / الشعب

@app.route("/structure")
def structure():
    grades = q("SELECT g.*, st.name stage_name FROM grades g "
               "JOIN stages st ON st.id = g.stage_id "
               "ORDER BY st.sort_order, g.sort_order, g.id")
    secs = q("SELECT se.*, g.name grade_name, g.stage_id FROM sections se "
             "JOIN grades g ON g.id = se.grade_id "
             "ORDER BY g.sort_order, se.sort_order, se.id")
    by_grade = {}
    for s in secs:
        by_grade.setdefault(s["grade_id"], []).append(s)
    return render_template(
        "structure.html",
        stages=q("SELECT * FROM stages ORDER BY sort_order, id"),
        grades=grades, sections=secs, by_grade=by_grade,
        total_classes=len(secs))


@app.route("/structure/stage", methods=["POST"])
def add_stage():
    for name in [x.strip() for x in s("names").splitlines() if x.strip()]:
        run("INSERT INTO stages(name, sort_order) VALUES (?, "
            "(SELECT COALESCE(MAX(sort_order),0)+1 FROM stages))", (name,))
    return redirect(url_for("structure"))


@app.route("/structure/grade", methods=["POST"])
def add_grade():
    """كل صف جديد يُحسب فصلاً مستقلاً - تُنشأ له شعبة ضمنية تلقائياً."""
    sid = i("stage_id")
    conn = db.connect()
    stage = conn.execute("SELECT name FROM stages WHERE id = ?", (sid,)).fetchone()
    stage_name = stage["name"] if stage else ""
    added = 0
    for raw in [x.strip() for x in s("names").splitlines() if x.strip()]:
        name = db.grade_display_name(raw, stage_name)
        gid = conn.execute(
            "INSERT INTO grades(stage_id, name, sort_order) VALUES (?, ?, "
            "(SELECT COALESCE(MAX(sort_order),0)+1 FROM grades))",
            (sid, name)).lastrowid
        conn.execute("INSERT INTO sections(grade_id, name, is_default, sort_order) "
                     "VALUES (?, '', 1, 0)", (gid,))
        added += 1
    conn.commit()
    conn.close()
    flash("أُضيف %d صف — كل صف يُحسب فصلاً، وإضافة الشعب اختيارية" % added, "ok")
    return redirect(url_for("structure"))


@app.route("/structure/section", methods=["POST"])
def add_section():
    """
    أول شعبة تُسمّى تحلّ محلّ الشعبة الضمنية للصف (بإعادة تسميتها)،
    فلا تضيع إسناداتها ولا حصصها. وما بعدها شعب جديدة.
    """
    gid = i("grade_id")
    names = [x.strip() for x in s("names").splitlines() if x.strip()]
    if not names:
        return redirect(url_for("structure"))
    count = i("student_count")
    conn = db.connect()
    placeholder = conn.execute(
        "SELECT id FROM sections WHERE grade_id = ? AND is_default = 1",
        (gid,)).fetchone()
    for n, name in enumerate(names):
        if n == 0 and placeholder:
            conn.execute(
                "UPDATE sections SET name = ?, is_default = 0, student_count = ?, "
                "sort_order = 1 WHERE id = ?", (name, count, placeholder["id"]))
        else:
            conn.execute(
                "INSERT INTO sections(grade_id, name, student_count, sort_order) "
                "VALUES (?, ?, ?, (SELECT COALESCE(MAX(sort_order),0)+1 FROM sections))",
                (gid, name, count))
    conn.commit()
    conn.close()
    flash("أُضيفت %d شعبة" % len(names), "ok")
    return redirect(url_for("structure"))


@app.route("/structure/delete/<kind>/<int:rid>", methods=["POST"])
def delete_structure(kind, rid):
    if kind not in ("stages", "grades", "sections"):
        abort(404)
    conn = db.connect()
    conn.execute("DELETE FROM %s WHERE id = ?" % kind, (rid,))
    conn.commit()
    # صف بقي بلا شعب يعود ليمثّل نفسه
    n = db.ensure_default_sections(conn)
    conn.commit()
    conn.close()
    flash("تم الحذف" + (" — الصف صار يُحسب فصلاً واحداً" if n else ""), "ok")
    return redirect(url_for("structure"))


# ------------------------------------------------------------------- المواد

@app.route("/subjects")
def subjects_page():
    subjects = q("SELECT * FROM subjects ORDER BY sort_order, id")
    links = {}
    for r in q("SELECT * FROM subject_teachers"):
        links.setdefault(r["subject_id"], set()).add(r["teacher_id"])
    used = {r["subject_id"]: r["n"] for r in q(
        "SELECT subject_id, COUNT(*) n FROM assignments GROUP BY subject_id")}
    return render_template("subjects.html", subjects=subjects, links=links,
                           used=used,
                           teachers=q("SELECT * FROM teachers ORDER BY sort_order, id"))


@app.route("/subjects/row", methods=["POST"])
def add_subject_row():
    n = i("count", 1)
    conn = db.connect()
    nxt = conn.execute("SELECT COALESCE(MAX(sort_order),0) s FROM subjects").fetchone()["s"]
    for k in range(max(1, min(n, 50))):
        conn.execute("INSERT INTO subjects(name, color_index, sort_order) "
                     "VALUES ('', ?, ?)", ((nxt + k) % 12, nxt + k + 1))
    conn.commit()
    conn.close()
    return redirect(url_for("subjects_page") + "#new")


@app.route("/subjects/save", methods=["POST"])
def save_subjects():
    """حفظ كل الصفوف دفعة واحدة، مع روابط المعلمين لكل مادة."""
    payload = request.get_json(silent=True)
    rows = payload.get("rows", []) if payload else []
    conn = db.connect()
    saved = 0
    for r in rows:
        try:
            rid = int(r.get("id"))
        except (TypeError, ValueError):
            continue
        name = (r.get("name") or "").strip()
        if not name:
            continue
        def num(key, default=0, lo=0, hi=40):
            try:
                return max(lo, min(hi, int(r.get(key) or default)))
            except (TypeError, ValueError):
                return default
        conn.execute(
            "UPDATE subjects SET name=?, short_name=?, default_periods=?, "
            "color_index=? WHERE id=?",
            (name, (r.get("short_name") or "").strip() or name,
             num("default_periods"), num("color_index", 0, 0, 11), rid))
        conn.execute("DELETE FROM subject_teachers WHERE subject_id = ?", (rid,))
        for tid in r.get("teachers") or []:
            try:
                conn.execute("INSERT OR IGNORE INTO subject_teachers"
                             "(subject_id, teacher_id) VALUES (?, ?)", (rid, int(tid)))
            except (TypeError, ValueError):
                pass
        saved += 1
    conn.execute("DELETE FROM subjects WHERE TRIM(name) = ''")
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "saved": saved, "message": "تم حفظ %d مادة" % saved})


@app.route("/subjects/<int:rid>/delete", methods=["POST"])
def delete_subject(rid):
    run("DELETE FROM subjects WHERE id = ?", (rid,))
    flash("تم حذف المادة", "ok")
    return redirect(url_for("subjects_page"))


# ----------------------------------------------------------------- المعلمون

@app.route("/teachers")
def teachers_page():
    teachers = q("SELECT * FROM teachers ORDER BY sort_order, id")
    load = {r["teacher_id"]: r["n"] for r in q(
        "SELECT teacher_id, SUM(periods_per_week) n FROM assignments GROUP BY teacher_id")}
    return render_template("teachers.html", teachers=teachers, load=load)


@app.route("/teachers/row", methods=["POST"])
def add_teacher_row():
    """صف جديد فارغ في الجدول - تُملأ خاناته مباشرة ثم يُحفظ."""
    n = i("count", 1)
    for _ in range(max(1, min(n, 50))):
        run("INSERT INTO teachers(name, sort_order) VALUES ('', "
            "(SELECT COALESCE(MAX(sort_order),0)+1 FROM teachers))")
    return redirect(url_for("teachers_page") + "#new")


@app.route("/teachers/save", methods=["POST"])
def save_teachers():
    """حفظ كل الصفوف دفعة واحدة."""
    ids = request.form.getlist("row_id")
    conn = db.connect()
    saved = 0
    for rid in ids:
        try:
            rid = int(rid)
        except (TypeError, ValueError):
            continue
        name = (request.form.get("name_%d" % rid, "") or "").strip()
        if not name:
            continue                      # صف فارغ: يُترك كما هو
        def num(field, default):
            try:
                return int(request.form.get("%s_%d" % (field, rid), default) or default)
            except (TypeError, ValueError):
                return default
        conn.execute(
            "UPDATE teachers SET name=?, phone=?, specialization=?, "
            "max_periods_per_week=?, max_periods_per_day=?, is_administrative=?, "
            "admin_reduction_periods=?, notes=? WHERE id=?",
            (name,
             (request.form.get("phone_%d" % rid, "") or "").strip(),
             (request.form.get("specialization_%d" % rid, "") or "").strip(),
             num("max_periods_per_week", 24), num("max_periods_per_day", 6),
             1 if request.form.get("is_administrative_%d" % rid) else 0,
             num("admin_reduction_periods", 0),
             (request.form.get("notes_%d" % rid, "") or "").strip(), rid))
        saved += 1
    # احذف الصفوف التي بقيت بلا اسم
    conn.execute("DELETE FROM teachers WHERE TRIM(name) = ''")
    conn.commit()
    conn.close()
    flash("تم حفظ %d معلم" % saved, "ok")
    return redirect(url_for("teachers_page"))


@app.route("/teachers/<int:rid>/delete", methods=["POST"])
def delete_teacher(rid):
    run("DELETE FROM teachers WHERE id = ?", (rid,))
    return redirect(url_for("teachers_page"))


@app.route("/teachers/<int:rid>/availability", methods=["GET", "POST"])
def teacher_availability(rid):
    t = one("SELECT * FROM teachers WHERE id = ?", (rid,))
    if not t:
        abort(404)
    st, days, slots, max_p = grid_context()
    if request.method == "POST":
        conn = db.connect()
        conn.execute("DELETE FROM teacher_unavailable WHERE teacher_id = ?", (rid,))
        for key in request.form.getlist("cell"):
            d, p = key.split("_")
            conn.execute("INSERT OR IGNORE INTO teacher_unavailable"
                         "(teacher_id, day, period_number) VALUES (?, ?, ?)",
                         (rid, int(d), int(p)))
        conn.commit()
        conn.close()
        flash("تم حفظ خانات عدم الإتاحة", "ok")
        return redirect(url_for("teachers_page"))

    blocked = {(r["day"], r["period_number"]) for r in
               q("SELECT * FROM teacher_unavailable WHERE teacher_id = ?", (rid,))}
    return render_template("availability.html", t=t, days=days,
                           max_p=max_p, slots=slots, blocked=blocked)


# ------------------------------------------------------------------ الإسناد

ASSIGN_SQL = (
    "SELECT a.*, sb.name subject_name, sb.default_periods, t.name teacher_name, "
    "se.name section_name, g.name grade_name, g.id grade_id, r.name room_name, "
    "CASE WHEN se.is_default = 1 THEN g.name ELSE g.name || ' / ' || se.name END section_label "
    "FROM assignments a "
    "JOIN subjects sb ON sb.id = a.subject_id "
    "JOIN teachers t  ON t.id  = a.teacher_id "
    "JOIN sections se ON se.id = a.section_id "
    "JOIN grades   g  ON g.id  = se.grade_id "
    "LEFT JOIN rooms r ON r.id = a.room_id "
    "ORDER BY g.sort_order, se.sort_order, sb.sort_order")

SECTIONS_SQL = (
    "SELECT se.*, g.name grade_name, g.sort_order gsort, "
    "CASE WHEN se.is_default = 1 THEN g.name ELSE g.name || ' / ' || se.name END section_label "
    "FROM sections se JOIN grades g ON g.id = se.grade_id "
    "ORDER BY g.sort_order, se.sort_order")


def assignment_stats():
    """المطلوب/المُسند/المُدرج لكل فصل."""
    vid = current_version()
    rows = q(ASSIGN_SQL)
    conn = db.connect()
    data = solver.Data(conn, vid)
    scheduled = {r["section_id"]: r["n"] for r in conn.execute(
        "SELECT section_id, COUNT(*) n FROM schedule WHERE version_id = ? "
        "GROUP BY section_id", (vid,))}
    conn.close()
    per_section = {}
    for sid, sec in data.sections.items():
        slots = len(data.section_slots.get(sid, ()))
        per_section[sid] = {
            "assigned": sum(a["periods_per_week"] for a in rows
                            if a["section_id"] == sid),
            "scheduled": scheduled.get(sid, 0),
            "required": int(sec.get("required_periods") or 0) or slots,
            "slots": slots,
            "auto": not int(sec.get("required_periods") or 0),
        }
    return rows, per_section


@app.route("/assignments")
def assignments_page():
    """لوحة أيقونات - كل أيقونة تفتح شاشتها المستقلة."""
    rows, per_section = assignment_stats()
    problems = sum(1 for b in per_section.values()
                   if b["assigned"] != b["required"])
    return render_template("assignments_hub.html", rows=rows,
                           per_section=per_section, problems=problems,
                           teachers=q("SELECT COUNT(*) n FROM teachers")[0]["n"],
                           rooms=q("SELECT COUNT(*) n FROM rooms")[0]["n"])


@app.route("/assignments/teacher")
def assign_by_teacher():
    """إسناد حسب المعلم: المادة من مواده، ثم الفصول وحصص كل فصل."""
    return render_template(
        "assign_teacher.html",
        teachers=q("SELECT t.*, "
                   "(SELECT COUNT(*) FROM subject_teachers st WHERE st.teacher_id=t.id) nsub, "
                   "(SELECT COALESCE(SUM(periods_per_week),0) FROM assignments a "
                   " WHERE a.teacher_id=t.id) load "
                   "FROM teachers t ORDER BY t.sort_order, t.id"))


@app.route("/assignments/list")
def assign_list():
    rows, _ = assignment_stats()
    return render_template(
        "assign_list.html", rows=rows,
        teachers=q("SELECT * FROM teachers ORDER BY sort_order, id"),
        rooms=q("SELECT * FROM rooms ORDER BY id"))


@app.route("/assignments/balance")
def assign_balance():
    rows, per_section = assignment_stats()
    return render_template("assign_balance.html", per_section=per_section,
                           sections=q(SECTIONS_SQL))


@app.route("/assignments/rooms")
def assign_rooms():
    return render_template(
        "assign_rooms.html",
        rooms=q("SELECT r.*, sb.name subject_name FROM rooms r "
                "LEFT JOIN subjects sb ON sb.id = r.subject_id ORDER BY r.id"),
        subjects=q("SELECT * FROM subjects ORDER BY sort_order, id"))


# ----------------------------------------------------- واجهات JSON للإسناد

@app.route("/api/teacher/<int:tid>/subjects")
def api_teacher_subjects(tid):
    """المواد التي يدرّسها هذا المعلم فقط - كما حُدّدت في شاشة المواد."""
    return jsonify(q(
        "SELECT sb.id, sb.name, sb.default_periods "
        "FROM subject_teachers st JOIN subjects sb ON sb.id = st.subject_id "
        "WHERE st.teacher_id = ? ORDER BY sb.sort_order, sb.id", (tid,)))


@app.route("/api/teacher/<int:tid>/subject/<int:sub>/sections")
def api_subject_sections(tid, sub):
    """
    كل الفصول مع: هل هي مُسندة لهذا المعلم في هذه المادة، وكم حصة،
    وهل أسندها معلم آخر (فلا تُسند مرتين).
    """
    sections = q(SECTIONS_SQL)
    mine = {r["section_id"]: r for r in q(
        "SELECT * FROM assignments WHERE teacher_id = ? AND subject_id = ?",
        (tid, sub))}
    others = {r["section_id"]: r["teacher_name"] for r in q(
        "SELECT a.section_id, t.name teacher_name FROM assignments a "
        "JOIN teachers t ON t.id = a.teacher_id "
        "WHERE a.subject_id = ? AND a.teacher_id <> ?", (sub, tid))}
    subject = one("SELECT * FROM subjects WHERE id = ?", (sub,)) or {}
    out = []
    for s_ in sections:
        a = mine.get(s_["id"])
        out.append({
            "id": s_["id"], "label": s_["section_label"],
            "checked": bool(a),
            "periods": (a["periods_per_week"] if a
                        else int(subject.get("default_periods") or 0)),
            "doubles": a["double_periods"] if a else 0,
            "taken_by": others.get(s_["id"]),
        })
    return jsonify({"sections": out,
                    "default_periods": int(subject.get("default_periods") or 0),
                    "subject_name": subject.get("name", "")})


@app.route("/api/assign/save", methods=["POST"])
def api_assign_save():
    """حفظ إسنادات معلم في مادة واحدة دفعة واحدة."""
    p = request.get_json(silent=True) or {}
    try:
        tid, sub = int(p["teacher_id"]), int(p["subject_id"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "message": "بيانات ناقصة"})

    subject = one("SELECT * FROM subjects WHERE id = ?", (sub,)) or {}
    # نصاب المادة «مقترح» لا سقف صارم: نفس المادة قد تختلف حصصها من صف
    # لآخر (العلوم 5 في الدولي و2 في السادس و1 في المتوسط). ننبّه على
    # الاختلاف ولا نغيّر ما أدخله المستخدم أبداً.
    suggested = int(subject.get("default_periods") or 0)

    conn = db.connect()
    keep, over = set(), []
    for item in p.get("sections", []):
        try:
            sid = int(item["id"])
            n = max(1, int(item.get("periods") or 1))
            dbl = max(0, int(item.get("doubles") or 0))
        except (KeyError, TypeError, ValueError):
            continue
        if suggested and n != suggested:
            over.append("%s: %d" % (item.get("label") or str(sid), n))
        keep.add(sid)
        row = conn.execute(
            "SELECT id FROM assignments WHERE teacher_id=? AND subject_id=? "
            "AND section_id=?", (tid, sub, sid)).fetchone()
        if row:
            conn.execute("UPDATE assignments SET periods_per_week=?, "
                         "double_periods=? WHERE id=?", (n, dbl, row["id"]))
        else:
            conn.execute(
                "INSERT INTO assignments(section_id, subject_id, teacher_id, "
                "periods_per_week, double_periods) VALUES (?,?,?,?,?)",
                (sid, sub, tid, n, dbl))
    # أزل ما أُلغي تحديده
    existing = conn.execute(
        "SELECT id, section_id FROM assignments WHERE teacher_id=? AND subject_id=?",
        (tid, sub)).fetchall()
    removed = 0
    for r in existing:
        if r["section_id"] not in keep:
            conn.execute("DELETE FROM assignments WHERE id = ?", (r["id"],))
            removed += 1
    conn.commit()
    conn.close()

    msg = "تم حفظ %d فصل" % len(keep)
    if removed:
        msg += " · أُلغي %d" % removed
    if over:
        msg += " · يختلف عن المقترح (%d): %s" % (suggested, "، ".join(over[:3]))
    return jsonify({"ok": True, "message": msg, "over": over,
                    "suggested": suggested})


@app.route("/assignments/required", methods=["POST"])
def save_required():
    """المطلوب من الحصص لكل فصل. الفارغ أو 0 = احسبه من الخانات المتاحة."""
    payload = request.get_json(silent=True)
    conn = db.connect()
    n = 0

    def put(sid, raw):
        try:
            val = max(0, int(raw)) if str(raw).strip() else 0
        except (TypeError, ValueError):
            val = 0
        conn.execute("UPDATE sections SET required_periods = ? WHERE id = ?", (val, sid))

    if payload:                                   # نداء JSON من الصفحة
        for r in payload.get("rows", []):
            try:
                put(int(r.get("id")), r.get("required"))
                n += 1
            except (TypeError, ValueError):
                continue
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "message": "تم حفظ المطلوب لـ %d فصل" % n})

    for sid in request.form.getlist("section_id"):   # نداء نموذج عادي
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            continue
        put(sid, request.form.get("req_%d" % sid, ""))
        n += 1
    conn.commit()
    conn.close()
    flash("تم حفظ المطلوب لـ %d فصل" % n, "ok")
    return redirect(url_for("assign_balance"))


@app.route("/api/assign/bulk", methods=["POST"])
def api_assign_bulk():
    """حفظ كل صفوف جدول الإسنادات دفعة واحدة."""
    payload = request.get_json(silent=True) or {}
    conn = db.connect()
    n = 0
    for r in payload.get("rows", []):
        try:
            rid = int(r.get("id"))
            tid = int(r.get("teacher_id"))
            per = max(1, int(r.get("periods_per_week") or 1))
            dbl = max(0, int(r.get("double_periods") or 0))
        except (TypeError, ValueError):
            continue
        room = r.get("room_id")
        try:
            room = int(room) if room else None
        except (TypeError, ValueError):
            room = None
        conn.execute("UPDATE assignments SET teacher_id=?, periods_per_week=?, "
                     "double_periods=?, room_id=? WHERE id=?",
                     (tid, per, dbl, room, rid))
        n += 1
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "تم حفظ %d إسناد" % n})


@app.route("/assignments/add", methods=["POST"])
def add_assignment():
    section_ids = request.form.getlist("section_ids") or [request.form.get("section_id")]
    for sid in section_ids:
        if not sid:
            continue
        run("INSERT INTO assignments(section_id, subject_id, teacher_id, "
            "periods_per_week, double_periods, room_id) VALUES (?, ?, ?, ?, ?, ?)",
            (int(sid), i("subject_id"), i("teacher_id"),
             max(1, i("periods_per_week", 1)), i("double_periods"),
             i("room_id") or None))
    flash("تم الإسناد", "ok")
    return redirect(request.referrer or url_for("assign_by_teacher"))


@app.route("/assignments/<int:rid>/edit", methods=["POST"])
def edit_assignment(rid):
    run("UPDATE assignments SET teacher_id=?, periods_per_week=?, double_periods=?, "
        "room_id=? WHERE id=?",
        (i("teacher_id"), max(1, i("periods_per_week", 1)), i("double_periods"),
         i("room_id") or None, rid))
    return redirect(request.referrer or url_for("assign_list"))


@app.route("/assignments/<int:rid>/delete", methods=["POST"])
def delete_assignment(rid):
    run("DELETE FROM assignments WHERE id = ?", (rid,))
    flash("تم حذف الإسناد", "ok")
    return redirect(request.referrer or url_for("assign_list"))


@app.route("/rooms/add", methods=["POST"])
def add_room():
    run("INSERT INTO rooms(name, subject_id, capacity) VALUES (?, ?, ?)",
        (s("name"), i("subject_id") or None, max(1, i("capacity", 1))))
    flash("تمت إضافة المعمل", "ok")
    return redirect(url_for("assign_rooms"))


@app.route("/rooms/<int:rid>/delete", methods=["POST"])
def delete_room(rid):
    run("DELETE FROM rooms WHERE id = ?", (rid,))
    flash("تم حذف المعمل", "ok")
    return redirect(url_for("assign_rooms"))


# ------------------------------------------------------------------ التوليد

@app.route("/generate", methods=["GET", "POST"])
def generate_page():
    vid = current_version()
    conn = db.connect()
    saved = conn.execute("SELECT options_json FROM gen_options WHERE version_id = ?",
                         (vid,)).fetchone()
    conn.close()
    opt = solver.merge_options(json.loads(saved["options_json"]) if saved else None)

    conn = db.connect()
    data = solver.Data(conn, vid)
    conn.close()
    errors, warnings = solver.check_feasibility(data)

    return render_template("generate.html", opt=opt, errors=errors,
                           warnings=warnings, lesson_count=len(data.lessons))


@app.route("/generate/run", methods=["POST"])
def generate_run():
    vid = current_version()
    payload = request.get_json(silent=True) or {}
    opt = solver.merge_options(payload.get("options"))
    keep_pinned = bool(payload.get("keep_pinned", True))

    conn = db.connect()
    conn.execute(
        "INSERT INTO gen_options(version_id, options_json) VALUES (?, ?) "
        "ON CONFLICT(version_id) DO UPDATE SET options_json = excluded.options_json",
        (vid, json.dumps(opt, ensure_ascii=False)))
    conn.commit()

    data = solver.Data(conn, vid)
    errors, _ = solver.check_feasibility(data)
    if errors and not bool(payload.get("skip_checks")):
        conn.close()
        return jsonify({"ok": False, "errors": errors, "skippable": True})

    # الحصص المثبّتة: أعِد بناء خريطة uid من الإسناد + الخانة
    pinned = {}
    if keep_pinned:
        rows = conn.execute(
            "SELECT * FROM schedule WHERE version_id = ? AND is_pinned = 1", (vid,)
        ).fetchall()
        by_assignment = {}
        for L in data.lessons:
            by_assignment.setdefault(L.assignment_id, []).append(L)
        for r in rows:
            pool = by_assignment.get(r["assignment_id"]) or []
            for L in pool:
                if L.uid not in pinned:
                    pinned[L.uid] = (r["day"], r["period_number"])
                    break

    db.save_snapshot(vid, "قبل التوليد", conn=conn)
    result = solver.generate(data, opt, pinned=pinned,
                             seed=int(datetime.now().timestamp()))
    state = result["state"]

    conn.execute("DELETE FROM schedule WHERE version_id = ?", (vid,))
    # إدراج جماعي: 858 حصة في بضع عبارات بدل 858 عبارة — يهمّ كثيراً
    # عندما تكون قاعدة البيانات على الإنترنت.
    batch = []
    for uid, (d, p) in state.place.items():
        L = state.by_uid[uid]
        for k in range(L.length):
            adj = ("adj_%d" % uid) if L.length == 2 else None
            for sid in L.sections:
                for tid in L.teachers:
                    batch.append((vid, sid, L.subject_id, tid, L.assignment_id,
                                  L.room_id, d, p + k, L.merge_group_id, adj,
                                  L.co_group_id, 1 if uid in state.pinned else 0))
    db.insert_many(conn,
                   "INSERT INTO schedule(version_id, section_id, subject_id, "
                   "teacher_id, assignment_id, room_id, day, period_number, "
                   "merge_group_id, adjacency_group_id, co_group_id, is_pinned) "
                   "VALUES", batch)
    conn.commit()

    unplaced = [solver.explain_unplaced(state, L) for L in result["unplaced"]]

    # لا نعتبر التوليد ناجحاً قبل التأكد: لا تعارضات ولا حصص فارغة
    ctx = edits.Ctx(conn, vid)
    conflicts = edits.conflicts(ctx)
    empty = edits.empty_sessions(ctx)
    conn.close()
    return jsonify({
        "ok": True,
        "placed": result["placed"],
        "total": result["total"],
        "cost": round(result["cost"], 1),
        "attempts": result["attempts"],
        "unplaced": unplaced,
        "conflicts": conflicts,
        "empty": empty,
        "clean": not conflicts and not empty and not unplaced,
    })


@app.route("/generate/clear", methods=["POST"])
def generate_clear():
    vid = current_version()
    conn = db.connect()
    db.save_snapshot(vid, "قبل مسح الجدول", conn=conn)
    conn.execute("DELETE FROM schedule WHERE version_id = ?", (vid,))
    conn.commit()
    conn.close()
    flash("تم مسح الجدول", "ok")
    return redirect(url_for("grid"))


# ------------------------------------------------------------- الجدول العام

def want_colors():
    """خيار الألوان: 1 = ملوّن (الافتراضي)، 0 = حدود ونصّ فقط."""
    return request.args.get("colored", "1") != "0"


def schedule_matrix(version_id, kind):
    """
    بيانات الجدول المجمّع: صف لكل فصل (أو معلمة) وعمود لكل (يوم، حصة).
    يعيد (owners, cells, days, max_p, slots, limits).
    """
    st, days, slots, max_p = grid_context()
    conn = db.connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT sc.*, sb.name subject_name, sb.short_name, sb.color_index, "
        "t.name teacher_name, se.name section_name, g.name grade_name, "
        + db.SECTION_LABEL_SQL + " section_label "
        "FROM schedule sc "
        "JOIN subjects sb ON sb.id = sc.subject_id "
        "JOIN teachers t  ON t.id  = sc.teacher_id "
        "JOIN sections se ON se.id = sc.section_id "
        "JOIN grades   g  ON g.id  = se.grade_id "
        "WHERE sc.version_id = ?", (version_id,))]
    grade_of = {r["id"]: r["grade_id"] for r in
                conn.execute("SELECT id, grade_id FROM sections")}
    if kind == "sections":
        owners = [{"id": o["id"], "title": o["section_label"], "grade_id": o["grade_id"]}
                  for o in conn.execute(
                      "SELECT se.id, se.grade_id, " + db.SECTION_LABEL_SQL +
                      " section_label FROM sections se "
                      "JOIN grades g ON g.id = se.grade_id "
                      "ORDER BY g.sort_order, se.sort_order")]
    else:
        owners = [{"id": t["id"], "title": t["name"], "grade_id": None}
                  for t in conn.execute("SELECT * FROM teachers ORDER BY sort_order, id")]
    conn.close()

    cells = {}
    for r in rows:
        key = (r["section_id"] if kind == "sections" else r["teacher_id"],
               r["day"], r["period_number"])
        cells.setdefault(key, []).append(r)

    # حدّ الحصص لكل (مالك، يوم)
    limits = {}
    for o in owners:
        if kind == "sections":
            limits[o["id"]] = {d: db.periods_for_grade_day(st, o["grade_id"], d)
                               for d in days}
        else:
            limits[o["id"]] = {d: db.periods_for_day(st, d) for d in days}

    # أوسع عدد حصص في كل يوم = عدد أعمدة ذلك اليوم
    per_day = {d: max([limits[o["id"]].get(d, max_p) for o in owners] or [max_p])
               for d in days}
    # مجموع حصص كل مالك - يُعرض في أول الجدول بجانب الاسم
    totals = defaultdict(int)
    for (oid, _d, _p), items in cells.items():
        totals[oid] += len(items)
    return owners, cells, days, max_p, slots, limits, st, per_day, dict(totals)


@app.route("/grid")
def grid():
    vid = current_version()
    view = request.args.get("view", "sections")       # sections | teachers
    layout = request.args.get("layout", "separate")   # separate | combined
    st, days, slots, max_p = grid_context()

    colored = want_colors()
    if layout == "combined":
        owners, cells, days, max_p, slots, limits, st, per_day, totals =             schedule_matrix(vid, view)
        conn = db.connect()
        ctx = edits.Ctx(conn, vid)
        conflicts = edits.conflicts(ctx)
        empty = edits.empty_sessions(ctx)
        conn.close()
        return render_template(
            "grid_combined.html", view=view, layout=layout, owners=owners,
            cells=cells, days=days, max_p=max_p, slots=slots,
            owner_limits=limits, per_day=per_day, totals=totals,
            conflicts=conflicts, empty=empty, colored=colored,
            breaks={x["period_number"] for x in slots if x["is_break"]})

    rows = q("SELECT sc.*, sb.name subject_name, sb.short_name, sb.color_index, "
             "t.name teacher_name, se.name section_name, g.name grade_name, "
        "CASE WHEN se.is_default = 1 THEN g.name ELSE g.name || ' / ' || se.name END section_label "
             "FROM schedule sc "
             "JOIN subjects sb ON sb.id = sc.subject_id "
             "JOIN teachers t  ON t.id  = sc.teacher_id "
             "JOIN sections se ON se.id = sc.section_id "
             "JOIN grades   g  ON g.id  = se.grade_id "
             "WHERE sc.version_id = ?", (vid,))

    cells = {}
    for r in rows:
        key = (r["section_id"] if view == "sections" else r["teacher_id"],
               r["day"], r["period_number"])
        cells.setdefault(key, []).append(r)

    if view == "sections":
        owners = q("SELECT se.id, se.name, g.name grade_name, CASE WHEN se.is_default = 1 THEN g.name ELSE g.name || ' / ' || se.name END section_label FROM sections se "
                   "JOIN grades g ON g.id = se.grade_id "
                   "ORDER BY g.sort_order, se.sort_order")
        owners = [{"id": o["id"], "title": o["section_label"]} for o in owners]
    else:
        owners = [{"id": t["id"], "title": t["name"]}
                  for t in q("SELECT * FROM teachers ORDER BY sort_order, id")]

    conn = db.connect()
    ctx = edits.Ctx(conn, vid)
    conflicts = edits.conflicts(ctx)
    empty = edits.empty_sessions(ctx)
    conn.close()

    # حدود الحصص لكل يوم - الخانات بعدها تُعطَّل في الشبكة
    limits = db.day_period_limits(st)
    if view == "sections":
        # لكل شعبة حدّها الخاص (قد يكون لصفها تجاوز)
        owner_limits = {o["id"]: {d: db.periods_for_grade_day(
            st, ctx.grade_of.get(o["id"]), d) for d in days} for o in owners}
    else:
        owner_limits = {o["id"]: dict(limits) for o in owners}

    return render_template("grid.html", view=view, layout=layout,
                           colored=colored, owners=owners, cells=cells,
                           days=days, max_p=max_p, slots=slots,
                           conflicts=conflicts, empty=empty,
                           limits=limits, owner_limits=owner_limits,
                           breaks={x["period_number"] for x in slots if x["is_break"]},
                           subjects=q("SELECT * FROM subjects ORDER BY sort_order"),
                           teachers=q("SELECT * FROM teachers ORDER BY sort_order"))


@app.route("/grid/cell", methods=["POST"])
def grid_cell():
    """
    تعديل يدوي لخانة: نقل، حذف، تثبيت، تبديل، أو إضافة.
    كل عملية تُفحص أولاً: حدود حصص اليوم، الفسحات، عدم الإتاحة، والتعارض.
    المرفوض لا يُنفَّذ ويُعاد سببه.
    """
    vid = current_version()
    payload = request.get_json(silent=True) or {}
    action = payload.get("action")
    force = bool(payload.get("force"))
    conn = db.connect()
    ctx = edits.Ctx(conn, vid)

    # ---- الفحص قبل التنفيذ
    if action == "move":
        ok, why = edits.validate_move(ctx, int(payload["id"]),
                                      int(payload["day"]), int(payload["period"]))
    elif action == "swap":
        ok, why = edits.validate_swap(ctx, int(payload["id"]), int(payload["other"]))
    elif action == "add":
        ok, why = edits.validate_add(ctx, int(payload["section_id"]),
                                     int(payload["teacher_id"]),
                                     int(payload["day"]), int(payload["period"]))
    else:
        ok, why = True, ""

    if not ok and not force:
        conn.close()
        return jsonify({"ok": False, "reason": why})

    db.save_snapshot(vid, "قبل تعديل يدوي", conn=conn)

    if action == "move":
        conn.execute("UPDATE schedule SET day = ?, period_number = ? WHERE id = ? "
                     "AND version_id = ?",
                     (payload["day"], payload["period"], payload["id"], vid))
    elif action == "delete":
        conn.execute("DELETE FROM schedule WHERE id = ? AND version_id = ?",
                     (payload["id"], vid))
    elif action == "pin":
        conn.execute("UPDATE schedule SET is_pinned = ? WHERE id = ? AND version_id = ?",
                     (1 if payload.get("pinned") else 0, payload["id"], vid))
    elif action == "swap":
        a = conn.execute("SELECT * FROM schedule WHERE id = ?", (payload["id"],)).fetchone()
        b = conn.execute("SELECT * FROM schedule WHERE id = ?", (payload["other"],)).fetchone()
        if a and b:
            conn.execute("UPDATE schedule SET day=?, period_number=? WHERE id=?",
                         (b["day"], b["period_number"], a["id"]))
            conn.execute("UPDATE schedule SET day=?, period_number=? WHERE id=?",
                         (a["day"], a["period_number"], b["id"]))
    elif action == "add":
        conn.execute(
            "INSERT INTO schedule(version_id, section_id, subject_id, teacher_id, "
            "assignment_id, day, period_number, is_pinned) VALUES (?,?,?,?,?,?,?,1)",
            (vid, payload["section_id"], payload["subject_id"], payload["teacher_id"],
             payload.get("assignment_id"), payload["day"], payload["period"]))
    conn.commit()

    fresh = edits.Ctx(conn, vid)
    out = {"ok": True, "conflicts": edits.conflicts(fresh)}
    conn.close()
    return jsonify(out)


@app.route("/grid/targets/<int:entry_id>")
def grid_targets(entry_id):
    """الخانات الآمنة والحصص القابلة للتبديل مع هذه الحصة."""
    conn = db.connect()
    ctx = edits.Ctx(conn, current_version())
    out = {"targets": edits.safe_targets(ctx, entry_id),
           "swappable": edits.swappable(ctx, entry_id)}
    conn.close()
    return jsonify(out)


@app.route("/grid/autofix", methods=["POST"])
def grid_autofix():
    """الحل التلقائي: ينقل الحصص المتعارضة إلى خانات آمنة."""
    vid = current_version()
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids")
    conn = db.connect()
    db.save_snapshot(vid, "قبل الحل التلقائي للتعارضات", conn=conn)
    ctx = edits.Ctx(conn, vid)
    result = edits.autofix(ctx, set(ids) if ids else None)
    conn.close()
    return jsonify({"ok": True, **result})


@app.route("/api/cell-options")
def api_cell_options():
    """
    خيارات ملء خانة فارغة — مبنية على الإسناد وحده:
    في عرض الفصول تظهر مواد هذا الفصل ومعلموها فقط،
    وفي عرض المعلمين تظهر مواد هذا المعلم وفصوله فقط.
    ويُعرض المتبقي من نصاب كل إسناد حتى لا يزيد عن المطلوب.
    """
    vid = current_version()
    view = request.args.get("view", "sections")
    try:
        owner = int(request.args.get("owner"))
    except (TypeError, ValueError):
        return jsonify({"options": []})

    where = "a.section_id = ?" if view == "sections" else "a.teacher_id = ?"
    rows = q(ASSIGN_SQL.replace("ORDER BY", "WHERE " + where + " ORDER BY"), (owner,))
    placed = {(r["section_id"], r["subject_id"], r["teacher_id"]): r["n"] for r in q(
        "SELECT section_id, subject_id, teacher_id, COUNT(*) n FROM schedule "
        "WHERE version_id = ? GROUP BY section_id, subject_id, teacher_id", (vid,))}

    out = []
    for a in rows:
        done = placed.get((a["section_id"], a["subject_id"], a["teacher_id"]), 0)
        out.append({
            "assignment_id": a["id"],
            "section_id": a["section_id"], "section_label": a["section_label"],
            "subject_id": a["subject_id"], "subject_name": a["subject_name"],
            "teacher_id": a["teacher_id"], "teacher_name": a["teacher_name"],
            "total": a["periods_per_week"], "placed": done,
            "left": a["periods_per_week"] - done,
        })
    return jsonify({"options": out, "view": view})


@app.route("/grid/validate")
def grid_validate():
    """تقرير التعارضات والحصص الفارغة."""
    conn = db.connect()
    ctx = edits.Ctx(conn, current_version())
    out = {"conflicts": edits.conflicts(ctx), "empty": edits.empty_sessions(ctx)}
    conn.close()
    return jsonify(out)


# ------------------------------------------------------------------- النسخ

@app.route("/versions/new", methods=["POST"])
def new_version():
    name = s("name") or "نسخة %s" % datetime.now().strftime("%m-%d %H:%M")
    copy_from = i("copy_from")
    vid = run("INSERT INTO versions(name, is_active, created_at) VALUES (?, 0, ?)",
              (name, datetime.now().isoformat(timespec="seconds")))
    if copy_from:
        conn = db.connect()
        conn.execute(
            "INSERT INTO schedule(version_id, section_id, subject_id, teacher_id, "
            "assignment_id, room_id, day, period_number, merge_group_id, "
            "adjacency_group_id, co_group_id, is_pinned) "
            "SELECT ?, section_id, subject_id, teacher_id, assignment_id, room_id, "
            "day, period_number, merge_group_id, adjacency_group_id, co_group_id, "
            "is_pinned FROM schedule WHERE version_id = ?", (vid, copy_from))
        conn.commit()
        conn.close()
    db.set_active_version(vid)
    flash("تم إنشاء النسخة وتفعيلها", "ok")
    return redirect(url_for("grid"))


@app.route("/versions/<int:vid>/activate", methods=["POST"])
def activate_version(vid):
    db.set_active_version(vid)
    return redirect(request.referrer or url_for("grid"))


@app.route("/versions/<int:vid>/delete", methods=["POST"])
def delete_version(vid):
    n = one("SELECT COUNT(*) c FROM versions")["c"]
    if n <= 1:
        flash("لا يمكن حذف النسخة الوحيدة", "err")
        return redirect(url_for("grid"))
    run("DELETE FROM versions WHERE id = ?", (vid,))
    v = one("SELECT id FROM versions ORDER BY id LIMIT 1")
    if v:
        db.set_active_version(v["id"])
    flash("تم حذف النسخة", "ok")
    return redirect(url_for("grid"))


@app.route("/doctor")
def doctor_page():
    """فحص سلامة النظام: يكشف التناقضات الصامتة بين الإسناد والجدول."""
    import doctor
    conn = db.connect()
    results = doctor.run_checks(conn, current_version())
    conn.close()
    return render_template(
        "doctor.html", results=results,
        errors=[r for r in results if r["level"] == "err"],
        warnings=[r for r in results if r["level"] == "warn"])


@app.route("/doctor/fix", methods=["POST"])
def doctor_fix():
    import doctor
    vid = current_version()
    conn = db.connect()
    db.save_snapshot(vid, "قبل الإصلاح التلقائي", conn=conn)
    results = doctor.run_checks(conn, vid)
    done = doctor.apply_fixes(conn, vid, results)
    conn.close()
    flash("؛ ".join(done) if done else "لا يوجد ما يمكن إصلاحه تلقائياً", "ok")
    return redirect(url_for("doctor_page"))


@app.route("/history")
def history_page():
    vid = current_version()
    rows = q("SELECT id, number, change_summary, created_at, "
             "LENGTH(snapshot_json) size FROM history "
             "WHERE version_id = ? ORDER BY number DESC", (vid,))
    return render_template("history.html", rows=rows)


@app.route("/history/<int:hid>/restore", methods=["POST"])
def history_restore(hid):
    db.restore_snapshot(hid)
    flash("تم الاسترجاع — واللقطة السابقة محفوظة أيضاً", "ok")
    return redirect(url_for("grid"))


# ----------------------------------------------------------------- التصدير

@app.route("/print")
def print_view():
    vid = current_version()
    kind = request.args.get("kind", "sections")
    layout = request.args.get("layout", "separate")
    colored = want_colors()

    if layout == "combined":
        owners, cells, days, max_p, slots, limits, st, per_day, totals =             schedule_matrix(vid, kind)
        return render_template(
            "print_combined.html", owners=owners, cells=cells, days=days,
            max_p=max_p, slots=slots, owner_limits=limits, kind=kind, st=st,
            per_day=per_day, totals=totals, colored=colored,
            breaks={x["period_number"] for x in slots if x["is_break"]},
            signature=request.args.get("sig") == "1")

    st, days, slots, max_p = grid_context()
    rows = q("SELECT sc.*, sb.name subject_name, sb.short_name, t.name teacher_name, "
             "se.name section_name, g.name grade_name FROM schedule sc "
             "JOIN subjects sb ON sb.id = sc.subject_id "
             "JOIN teachers t ON t.id = sc.teacher_id "
             "JOIN sections se ON se.id = sc.section_id "
             "JOIN grades g ON g.id = se.grade_id WHERE sc.version_id = ?", (vid,))
    cells = {}
    for r in rows:
        key = (r["section_id"] if kind == "sections" else r["teacher_id"],
               r["day"], r["period_number"])
        cells.setdefault(key, []).append(r)
    if kind == "sections":
        owners = [{"id": o["id"], "title": o["section_label"]}
                  for o in q("SELECT se.id, se.name, g.name grade_name, CASE WHEN se.is_default = 1 THEN g.name ELSE g.name || ' / ' || se.name END section_label FROM sections se "
                             "JOIN grades g ON g.id = se.grade_id "
                             "ORDER BY g.sort_order, se.sort_order")]
    else:
        owners = [{"id": t["id"], "title": t["name"]}
                  for t in q("SELECT * FROM teachers ORDER BY sort_order, id")]
    # حدود حصص كل يوم لكل مالك - لتطابق المعاينة الشاشة والـPDF
    conn = db.connect()
    grade_of = {r["id"]: r["grade_id"] for r in
                conn.execute("SELECT id, grade_id FROM sections")}
    conn.close()
    if kind == "sections":
        own_lim = {o["id"]: {d: db.periods_for_grade_day(
            st, grade_of.get(o["id"]), d) for d in days} for o in owners}
    else:
        own_lim = {o["id"]: {d: db.periods_for_day(st, d) for d in days}
                   for o in owners}

    return render_template("print.html", owners=owners, cells=cells, days=days,
                           max_p=max_p, slots=slots, kind=kind, st=st,
                           colored=colored, owner_limits=own_lim,
                           breaks={x["period_number"] for x in slots if x["is_break"]},
                           signature=request.args.get("sig") == "1")


@app.route("/export/excel")
def export_excel():
    vid = current_version()
    kind = request.args.get("kind", "sections")
    combined = request.args.get("layout") == "combined"
    colored = want_colors()
    buf = (exports.build_excel_combined(vid, kind, colored=colored) if combined
           else exports.build_excel(vid, kind, colored=colored))
    name = "الجدول%s%s-%s.xlsx" % ("-المجمّع" if combined else "",
                                    "" if colored else "-أبيض",
                                    "الفصول" if kind == "sections" else "المعلمات")
    return send_file(buf, as_attachment=True, download_name=name,
                     mimetype="application/vnd.openxmlformats-officedocument."
                              "spreadsheetml.sheet")


@app.route("/export/pdf")
def export_pdf():
    vid = current_version()
    kind = request.args.get("kind", "sections")
    sig = request.args.get("sig") == "1"
    combined = request.args.get("layout") == "combined"
    colored = want_colors()
    buf = (exports.build_pdf_combined(vid, kind, signature=sig, colored=colored)
           if combined
           else exports.build_pdf(vid, kind, signature=sig, colored=colored))
    name = "الجدول%s%s-%s.pdf" % ("-المجمّع" if combined else "",
                                   "" if colored else "-أبيض",
                                   "الفصول" if kind == "sections" else "المعلمات")
    return send_file(buf, as_attachment=True, download_name=name,
                     mimetype="application/pdf")


@app.route("/export/backup")
def export_backup():
    """نسخة احتياطية كاملة كملف JSON."""
    conn = db.connect()
    out = {"exported_at": datetime.now().isoformat(timespec="seconds"), "tables": {}}
    for t in ("settings", "stages", "grades", "sections", "subjects", "teachers",
              "teacher_unavailable", "assignments", "time_slots", "rooms",
              "versions", "schedule", "gen_options"):
        out["tables"][t] = [dict(r) for r in conn.execute("SELECT * FROM %s" % t)]
    conn.close()
    buf = io.BytesIO(json.dumps(out, ensure_ascii=False, indent=1).encode("utf-8"))
    return send_file(buf, as_attachment=True, mimetype="application/json",
                     download_name="نسخة-احتياطية-%s.json"
                                   % datetime.now().strftime("%Y%m%d-%H%M"))


@app.route("/import/backup", methods=["POST"])
def import_backup():
    f = request.files.get("file")
    if not f:
        flash("اختر ملفاً أولاً", "err")
        return redirect(url_for("settings_page"))
    try:
        payload = json.load(f.stream)
    except (ValueError, UnicodeDecodeError):
        flash("الملف غير صالح", "err")
        return redirect(url_for("settings_page"))
    conn = db.connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    for t, rows in payload.get("tables", {}).items():
        conn.execute("DELETE FROM %s" % t)
        for r in rows:
            cols = ", ".join(r.keys())
            marks = ", ".join("?" * len(r))
            conn.execute("INSERT INTO %s(%s) VALUES (%s)" % (t, cols, marks),
                         list(r.values()))
    conn.commit()
    conn.close()
    flash("تم استيراد النسخة الاحتياطية", "ok")
    return redirect(url_for("home"))


# ------------------------------------------------------------------- تشغيل

def open_browser():
    webbrowser.open("http://127.0.0.1:5000/")


if __name__ == "__main__":
    db.init_db()
    Timer(1.2, open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
