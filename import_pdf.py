# -*- coding: utf-8 -*-
"""
استخراج الإسنادات من ملف PDF قديم وإضافتها إلى الإسناد الحالي.

الملف مكتوب بأشكال العرض العربية (Presentation Forms) وبترتيب بصري مقلوب،
فنعكس النص ثم نعيده إلى العربية القياسية، ونقرأ الجدول بأعمدته الخمسة:
  النصاب | الشعب وعدد الحصص | الصف | المادة | المعلم

  python import_pdf.py                 معاينة ومطابقة فقط (لا يكتب شيئاً)
  python import_pdf.py --apply         تطبيق فعلي، مع نسخة احتياطية أولاً
  python import_pdf.py --apply --wipe  يحذف الإسنادات الحالية أولاً
"""
import glob
import os
import re
import shutil
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import pdfplumber

import db

# --------------------------------------------------------------- نصّ عربي

DIACRITICS = re.compile(r"[ً-ٰٟ]")
LATIN_RUN = re.compile(r"[A-Za-z]+")
NUM_RUN = re.compile(r"\d+(?:\s*/\s*\d+)?")
# عند عكس النص تنعكس الأقواس أيضاً فيجب ردّها
MIRROR = str.maketrans("()[]{}<>«»", ")(][}{><»«")


def fix(text):
    """
    نصّ PDF بصريّ مقلوب: نعكسه ونعكس أقواسه، ثم نعيد عكس المقاطع اللاتينية
    والأرقام (فهي تُكتب من اليسار)، ثم نحوّل أشكال العرض إلى عربية قياسية.
    """
    if not text:
        return ""
    r = text[::-1].translate(MIRROR)
    r = LATIN_RUN.sub(lambda m: m.group(0)[::-1], r)
    r = NUM_RUN.sub(lambda m: m.group(0)[::-1], r)
    r = unicodedata.normalize("NFKC", r)
    r = DIACRITICS.sub("", r).replace("ـ", "")
    # حروف فارسية/أردية تسرّبت من أشكال العرض
    for a, b in (("ی", "ي"), ("ک", "ك"),
                 ("ھ", "ه"), ("ہ", "ه"),
                 ("ە", "ه"), ("ى", "ي")):
        r = r.replace(a, b)
    return " ".join(r.split())


STOP = {"ال", "في", "من", "و"}


def fold(text):
    """صيغة مطابقة: توحيد الهمزات والهاء والتاء المربوطة، وحذف غير الحروف."""
    t = (text or "").lower()
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ٱ", "ا"),
                 ("ة", "ه"), ("ى", "ي"), ("ؤ", "و"), ("ئ", "ي"),
                 ("ھ", "ه"), ("ی", "ي")):
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9؀-ۿ]+", "", t)


def tokens(text):
    """كلمات دالّة للمطابقة، بلا «ال» التعريف ولا الكلمات القصيرة."""
    out = set()
    for w in re.split(r"[^a-zA-Z؀-ۿ]+", (text or "")):
        w = fold(w)
        if len(w) > 3 and w.startswith("ال"):
            w = w[2:]
        if len(w) >= 2 and w not in STOP:
            out.add(w)
    return out


# ------------------------------------------------------- قواعد المطابقة
#
# الملف القديم يسمّي المسار الابتدائي «(عام)» بينما يسمّيه النظام الحالي
# «ثنائي لغة». هذا مؤكَّد من إسنادات المستخدم نفسه: فـ«أريج عبدالله ←
# اللغة العربية ← ثاني ابتدائي (عام) ← أ، 6 حصص» في الملف هي نفسها
# «اريج الودعان ← اللغة العربية ← ثاني ثنائي لغة/أ ← 6» في النظام.
# عدّل هذه القواعد إن اختلفت التسمية عندك.

GRADE_RULES = [
    (r"\s*\(\s*عام\s*\)\s*", " "),        # «(عام)» علامة مسار لا جزء من الاسم
    (r"\bابتدائي\b", "ثنائي لغة"),        # المسار الابتدائي العام = ثنائي لغة
]

# أسماء يتعذّر حسمها آلياً - محسومة من إسنادات المستخدم القائمة
TEACHER_ALIASES = {
    "اسماء": "اسماء اشرف",                # «أسماء (رياض أطفال)» في الملف
}

SUBJECT_ALIASES = {
    "ielts": "ILETS",
    "نافس عربي": "نافس لغتي",
    # «قرآن» وحدها تلتبس بـ«القرآن الكريم والدراسات الإسلامية» بعد إنشائها
    "قرآن": "القران",
    "قرآن كريم": "القران",
}


def apply_grade_rules(text):
    t = text or ""
    for pat, rep in GRADE_RULES:
        t = re.sub(pat, rep, t)
    return " ".join(t.split())


def alias(text, table):
    k = fold(text)
    for a, b in table.items():
        if fold(a) == k:
            return b
    return text


# ----------------------------------------------------------- قراءة الجدول

# «أ , 6 حصص»  «ب , 1 حصة»  «أ , 2 حصتان»  مفصولة بـ  -
CHUNK = re.compile(r"([^\s,،\-]+)\s*[,،]\s*(\d+)")


def parse_sections(cell):
    """«أ , 4 حصص - ب , 4 حصص» -> [('أ', 4), ('ب', 4)]"""
    out = []
    for part in re.split(r"\s+-\s+|\s+–\s+", cell or ""):
        m = CHUNK.search(part)
        if m:
            name = m.group(1).strip("()")
            out.append((name, int(m.group(2))))
        else:                      # «1 حصة» بلا اسم شعبة -> الصف كاملاً
            m2 = re.search(r"(\d+)", part or "")
            if m2 and part.strip():
                out.append(("", int(m2.group(1))))
    return out


def read_pdf(path):
    """
    يعيد صفوفاً: {teacher, subject, grade, sections, nisab}

    عمود المعلم خلية مدموجة تمتدّ على صفوف المعلمة كلها، واسمها يظهر في
    وسط الخلية لا في أولها. لذلك لا يصحّ «املأ للأسفل» من نصّ الصف:
    نعتمد هندسة الخلايا نفسها -
      * خلية = None  ->  استمرار لنفس المعلمة
      * خلية بنصّ    ->  كتلة معلمة جديدة
      * خلية بلا نصّ ->  كتلة انقطعت بنهاية الصفحة، وتُكمَّل باسم أول
                          كتلة مسمّاة بعدها (في الصفحة التالية)
    """
    out = []
    pending = []          # صفوف كتلة بلا اسم، تنتظر اسم الكتلة التالية
    current = ""
    orphan_note = []

    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            tables = page.find_tables()
            if not tables:
                continue
            tbl = tables[0]
            nrows = len(tbl.rows)
            for ri, row in enumerate(tbl.rows):
                cells = list(row.cells) + [None] * 5
                vals = []
                for c in cells[:5]:
                    vals.append(fix(page.crop(c).extract_text() or "") if c else None)
                nisab, secs, grade, subject, name = vals
                text = [v for v in vals if v]
                if not text:
                    continue
                if (subject or "").strip() == "المادة":
                    continue                                   # سطر العناوين

                if name is not None:                           # خلية معلم جديدة
                    clean = re.sub(r"\s*\(.*?\)\s*", " ", name).strip()
                    if clean:
                        current = clean
                        for p in pending:                      # كتلة مقطوعة قبلها
                            p["teacher"] = current
                            out.append(p)
                        pending = []
                    elif ri == 0:
                        # كتلة بلا اسم في رأس الصفحة = بقيّة معلمة الصفحة
                        # السابقة، فاسمها ذُكر هناك. نُبقي current كما هو.
                        pass
                    else:
                        # كتلة بلا اسم في ذيل الصفحة = بدايةُ معلمةٍ يظهر
                        # اسمها في رأس الصفحة التالية.
                        current = None

                if not subject or not grade:
                    continue
                parsed = parse_sections(secs)
                if not parsed:
                    continue
                rec = {"teacher": current, "subject": subject, "grade": grade,
                       "sections": parsed, "nisab": nisab or "", "page": pno}
                if current is None:
                    pending.append(rec)
                else:
                    out.append(rec)

    for p in pending:                                          # لم يأتِ اسم بعدها
        p["teacher"] = ""
        out.append(p)
    if orphan_note:
        print("تنبيه: كتل بلا اسم في وسط الصفحة: %s" % orphan_note)
    return out


# ------------------------------------------------------------- المطابقة

def build_index(conn):
    idx = {}
    idx["teachers"] = {fold(r["name"]): r["id"] for r in
                       conn.execute("SELECT id, name FROM teachers")}
    idx["subjects"] = {fold(r["name"]): r["id"] for r in
                       conn.execute("SELECT id, name FROM subjects")}
    idx["subject_names"] = {r["id"]: r["name"] for r in
                            conn.execute("SELECT id, name FROM subjects")}
    idx["teacher_names"] = {r["id"]: r["name"] for r in
                            conn.execute("SELECT id, name FROM teachers")}
    grades, sections = {}, defaultdict(dict)
    for r in conn.execute(
            "SELECT se.id sid, se.name sname, se.is_default, g.id gid, g.name gname "
            "FROM sections se JOIN grades g ON g.id = se.grade_id"):
        grades.setdefault(fold(r["gname"]), r["gid"])
        key = fold(r["sname"]) if not r["is_default"] else ""
        sections[r["gid"]][key] = r["sid"]
    idx["grades"] = grades
    idx["grade_names"] = {r["id"]: r["name"] for r in
                          conn.execute("SELECT id, name FROM grades")}
    idx["sections"] = sections
    # فهارس الكلمات الدالّة للمطابقة التقريبية
    idx["tok_teachers"] = {i: tokens(n) for i, n in idx["teacher_names"].items()}
    idx["tok_subjects"] = {i: tokens(n) for i, n in idx["subject_names"].items()}
    idx["tok_grades"] = {i: tokens(n) for i, n in idx["grade_names"].items()}
    return idx


def first_token(text):
    for w in re.split(r"[^a-zA-Z؀-ۿ]+", (text or "")):
        w = fold(w)
        if len(w) >= 2:
            return w
    return ""


def sim(a, b):
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def match_person(needle, names_by_id):
    """
    مطابقة أسماء الأشخاص: الاسم الأول هو الهوية في التسمية العربية،
    وما بعده اسم الأب أو العائلة وقد يختلف بين الملفين.
    نرجّح تشابه الاسم الأول، ثم تقاطع بقية الكلمات، ونرفض المتساوي.
    """
    want_first = first_token(needle)
    want = tokens(needle)
    if not want_first:
        return None, "فارغ"
    scored = []
    for vid, name in names_by_id.items():
        have = tokens(name)
        f = sim(want_first, first_token(name))
        overlap = len(want & have) / float(len(want | have)) if (want | have) else 0
        scored.append((round(2.0 * f + overlap, 4), vid, name))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < 0.9:
        return None, "غير موجود"
    top = scored[0]
    ties = [x for x in scored[1:] if abs(x[0] - top[0]) < 0.12]
    if ties:
        return None, "ملتبس: %s / %s" % (top[2], ties[0][2])
    return top[1], "تقريبي"


def match_one(needle, table, tok_table=None):
    """
    مطابقة تامّة، ثم احتواء، ثم تقاطع الكلمات الدالّة.
    ما كان ملتبساً يُرفض ويُبلَّغ عنه بدل تخمينه.
    """
    k = fold(needle)
    if not k:
        return None, "فارغ"
    if k in table:
        return table[k], "تام"

    hits = {v for kk, v in table.items() if kk and (k in kk or kk in k)}
    if len(hits) == 1:
        return hits.pop(), "احتواء"

    if tok_table:
        want = tokens(needle)
        if want:
            scored = []
            for vid, have in tok_table.items():
                common = want & have
                if common:
                    scored.append((len(common), len(common) / float(len(want | have)),
                                   vid))
            scored.sort(reverse=True)
            if scored:
                best = scored[0]
                rest = [s for s in scored[1:] if s[0] == best[0]]
                if not rest:
                    return best[2], "كلمات(%d)" % best[0]
                return None, "ملتبس بين %d" % (len(rest) + 1)
    if len(hits) > 1:
        return None, "ملتبس (%d احتمال)" % len(hits)
    return None, "غير موجود"


def main():
    apply_it = "--apply" in sys.argv
    wipe = "--wipe" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else None
    if not path:
        cand = [x for x in glob.glob(os.path.expanduser("~/Downloads/*.pdf"))
                if "سناد" in x]
        if not cand:
            print("حدّد مسار ملف الـ PDF")
            return 1
        path = cand[0]

    print("الملف: %s\n" % os.path.basename(path))
    rows = read_pdf(path)
    print("سطور الإسناد المقروءة: %d\n" % len(rows))

    # النسخة الاحتياطية أولاً - قبل أي كتابة، بما فيها إنشاء المواد
    backup = None
    if apply_it:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(os.path.dirname(db.DB_PATH),
                              "نسخة-قبل-استيراد-الإسناد-%s.db" % stamp)
        c0 = db.connect()
        try:
            db.checkpoint(c0)
        except Exception:
            pass
        c0.close()
        shutil.copy2(db.DB_PATH, backup)
        print("نسخة احتياطية: %s\n" % os.path.basename(backup))

    conn = db.connect()
    idx = build_index(conn)

    # مواد الملف غير الموجودة تُنشأ كما هي - أفضل من إسقاط إسناداتها
    wanted = set()
    for r in rows:
        sname = alias(r["subject"], SUBJECT_ALIASES)
        if match_one(sname, idx["subjects"], idx["tok_subjects"])[0] is None:
            wanted.add(sname)
    created = []
    if wanted and apply_it:
        nxt = conn.execute(
            "SELECT COALESCE(MAX(sort_order),0) s FROM subjects").fetchone()["s"]
        for k, name in enumerate(sorted(wanted), 1):
            conn.execute(
                "INSERT INTO subjects(name, short_name, color_index, sort_order) "
                "VALUES (?, ?, ?, ?)", (name, name[:14], (nxt + k) % 12, nxt + k))
            created.append(name)
        conn.commit()
        idx = build_index(conn)

    resolved, problems = [], []
    miss = {"teachers": defaultdict(int), "subjects": defaultdict(int),
            "grades": defaultdict(int), "sections": defaultdict(int)}

    for r in rows:
        tname = alias(r["teacher"], TEACHER_ALIASES)
        sname = alias(r["subject"], SUBJECT_ALIASES)
        gname = apply_grade_rules(r["grade"])
        tid, twhy = match_person(tname, idx["teacher_names"])
        sbid, swhy = match_one(sname, idx["subjects"], idx["tok_subjects"])
        gid, gwhy = match_one(gname, idx["grades"], idx["tok_grades"])
        if tid is None:
            miss["teachers"][tname] += 1
        if sbid is None:
            miss["subjects"][sname] += 1
        if gid is None:
            miss["grades"][gname] += 1
        if tid is None or sbid is None or gid is None:
            problems.append((r, "معلم:%s مادة:%s صف:%s" % (twhy, swhy, gwhy)))
            continue

        pool = idx["sections"].get(gid, {})
        for sec_name, periods in r["sections"]:
            key = fold(sec_name)
            sid = pool.get(key)
            if sid is None and len(pool) == 1:
                sid = list(pool.values())[0]        # الصف بلا شعب
            if sid is None:
                miss["sections"]["%s / %s" % (r["grade"], sec_name or "—")] += 1
                problems.append((r, "شعبة «%s» غير موجودة في «%s»"
                                 % (sec_name, idx["grade_names"][gid])))
                continue
            resolved.append({"section_id": sid, "subject_id": sbid,
                             "teacher_id": tid, "periods": periods,
                             "label": "%s | %s | %s %s | %d"
                                      % (idx["teacher_names"][tid],
                                         idx["subject_names"][sbid],
                                         idx["grade_names"][gid],
                                         sec_name, periods)})

    print("=" * 66)
    print("إسنادات جاهزة للإضافة: %d" % len(resolved))
    print("سطور لم تُطابَق:        %d" % len(problems))
    for kind, title in (("teachers", "معلمات"), ("subjects", "مواد"),
                        ("grades", "صفوف"), ("sections", "شعب")):
        if miss[kind]:
            print("\n%s غير موجودة في النظام (%d):" % (title, len(miss[kind])))
            for name, n in sorted(miss[kind].items(), key=lambda x: -x[1]):
                print("   %-42s %d سطر" % (name or "(فارغ)", n))

    if created:
        print("\nمواد أُنشئت (%d): %s" % (len(created), "، ".join(created)))
    elif wanted:
        print("\nمواد ستُنشأ عند التطبيق (%d): %s"
              % (len(wanted), "، ".join(sorted(wanted))))

    if resolved:
        print("\nعيّنة مما سيُضاف:")
        for r in resolved[:12]:
            print("   " + r["label"])
        total = sum(r["periods"] for r in resolved)
        print("\nإجمالي الحصص: %d" % total)

    if not apply_it:
        print("\n(معاينة فقط — أضف --apply للتطبيق)")
        conn.close()
        return 0

    # ---------------------------------------------------------- التطبيق
    if wipe:
        n = conn.execute("SELECT COUNT(*) c FROM assignments").fetchone()["c"]
        conn.execute("DELETE FROM assignments")
        print("حُذفت %d إسناد سابق" % n)

    added = updated = 0
    for r in resolved:
        row = conn.execute(
            "SELECT id, periods_per_week FROM assignments WHERE section_id=? "
            "AND subject_id=? AND teacher_id=?",
            (r["section_id"], r["subject_id"], r["teacher_id"])).fetchone()
        if row:
            if row["periods_per_week"] != r["periods"]:
                conn.execute("UPDATE assignments SET periods_per_week=? WHERE id=?",
                             (r["periods"], row["id"]))
                updated += 1
        else:
            conn.execute(
                "INSERT INTO assignments(section_id, subject_id, teacher_id, "
                "periods_per_week) VALUES (?,?,?,?)",
                (r["section_id"], r["subject_id"], r["teacher_id"], r["periods"]))
            added += 1
        conn.execute("INSERT OR IGNORE INTO subject_teachers(subject_id, teacher_id) "
                     "VALUES (?,?)", (r["subject_id"], r["teacher_id"]))

    # نصاب المادة الافتراضي = الأكثر شيوعاً بعد الاستيراد
    for s_ in conn.execute("SELECT id FROM subjects").fetchall():
        row = conn.execute(
            "SELECT periods_per_week n, COUNT(*) c FROM assignments "
            "WHERE subject_id=? GROUP BY periods_per_week "
            "ORDER BY c DESC, n DESC LIMIT 1", (s_["id"],)).fetchone()
        if row:
            conn.execute("UPDATE subjects SET default_periods=? WHERE id=?",
                         (row["n"], s_["id"]))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) c FROM assignments").fetchone()["c"]
    conn.close()
    print("أُضيف %d · حُدّث %d · المجموع الآن %d إسناد" % (added, updated, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
