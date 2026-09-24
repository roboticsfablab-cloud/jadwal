# -*- coding: utf-8 -*-
"""
تصدير الجداول: Excel عبر openpyxl، وPDF عربي عبر reportlab
مع تشكيل الحروف (arabic_reshaper) واتجاه النص (bidi).
"""
import io
import os

import db

# ----------------------------------------------------------------- Excel

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

PALETTE = ["DBEAFE", "DCFCE7", "FEF3C7", "FCE7F3", "E0E7FF", "FFE4E6",
           "CCFBF1", "F3E8FF", "FFEDD5", "ECFCCB", "CFFAFE", "FEE2E2"]

# حدود واضحة بين كل خانة وخانة، وخط أغمق عند حدود الأيام
THIN = Side(style="thin", color="7A8496")
MED = Side(style="medium", color="3A4354")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAVY = Border(left=MED, right=MED, top=THIN, bottom=THIN)


def pick(colored, tinted, grey):
    """اللون في الوضع الملوّن، ورمادي محايد في وضع بلا ألوان."""
    return tinted if colored else grey


def edge_border(is_day_edge):
    """حدّ عادي، مع خط أغمق على يسار الخانة عند نهاية اليوم."""
    if not is_day_edge:
        return BORDER
    return Border(left=MED, right=THIN, top=THIN, bottom=THIN)


def _fetch(version_id, kind):
    """يعيد (owners, cells, days, max_p, slots, settings)."""
    conn = db.connect()
    st = db.get_settings(conn)
    days = db.working_days(st)
    slots = [dict(r) for r in conn.execute(
        "SELECT * FROM time_slots ORDER BY period_number")]
    max_p = max([int(st.get("periods_per_day") or 7)] +
                [x["period_number"] for x in slots])

    rows = [dict(r) for r in conn.execute(
        "SELECT sc.*, sb.name subject_name, sb.short_name, sb.color_index, "
        "t.name teacher_name, se.name section_name, g.name grade_name, "
        "CASE WHEN se.is_default = 1 THEN g.name ELSE g.name || ' / ' || se.name END section_label "
        "FROM schedule sc "
        "JOIN subjects sb ON sb.id = sc.subject_id "
        "JOIN teachers t  ON t.id  = sc.teacher_id "
        "JOIN sections se ON se.id = sc.section_id "
        "JOIN grades   g  ON g.id  = se.grade_id "
        "WHERE sc.version_id = ?", (version_id,))]

    if kind == "sections":
        owners = [{"id": o["id"], "title": o["section_label"]}
                  for o in conn.execute(
                      "SELECT se.id, se.name, g.name grade_name, CASE WHEN se.is_default = 1 THEN g.name ELSE g.name || ' / ' || se.name END section_label FROM sections se "
                      "JOIN grades g ON g.id = se.grade_id "
                      "ORDER BY g.sort_order, se.sort_order")]
    else:
        owners = [{"id": t["id"], "title": t["name"]}
                  for t in conn.execute(
                      "SELECT * FROM teachers ORDER BY sort_order, id")]
    conn.close()

    cells = {}
    for r in rows:
        key = (r["section_id"] if kind == "sections" else r["teacher_id"],
               r["day"], r["period_number"])
        cells.setdefault(key, []).append(r)
    return owners, cells, days, max_p, slots, st


def _cell_text(items, kind):
    """نص الخانة: المادة + (المعلم أو الشعبة)."""
    out = []
    for r in items:
        if kind == "sections":
            out.append("%s\n%s" % (r["subject_name"], r["teacher_name"]))
        else:
            out.append("%s\n%s" % (r["subject_name"], r["section_label"]))
    return "\n—\n".join(out)


def _matrix(version_id, kind):
    """أعمدة الجدول المجمّع: قائمة (يوم، حصة) بترتيب العرض + حدود كل مالك."""
    owners, cells, days, max_p, slots, st = _fetch(version_id, kind)
    conn = db.connect()
    grade_of = {r["id"]: r["grade_id"] for r in
                conn.execute("SELECT id, grade_id FROM sections")}
    conn.close()
    limits = {}
    for o in owners:
        if kind == "sections":
            limits[o["id"]] = {d: db.periods_for_grade_day(
                st, grade_of.get(o["id"]), d) for d in days}
        else:
            limits[o["id"]] = {d: db.periods_for_day(st, d) for d in days}
    per_day = {d: max([limits[o["id"]].get(d, max_p) for o in owners] or [max_p])
               for d in days}
    columns = [(d, p) for d in days for p in range(1, per_day[d] + 1)]
    totals = {}
    for o in owners:
        totals[o["id"]] = sum(
            len(cells.get((o["id"], d, p), ())) for d, p in columns)
    return owners, cells, days, slots, st, limits, per_day, columns, totals


def build_excel_combined(version_id, kind="sections", colored=True):
    """جدول واحد: صف لكل فصل (أو معلمة) وعمود لكل (يوم، حصة)."""
    owners, cells, days, slots, st, limits, per_day, columns, totals = _matrix(
        version_id, kind)
    breaks = {x["period_number"] for x in slots if x["is_break"]}

    wb = Workbook()
    ws = wb.active
    ws.title = "المجمّع"
    ws.sheet_view.rightToLeft = True

    ws.cell(row=1, column=1,
            value=st.get("school_name") or "جدول المدرسة").font = Font(size=15, bold=True)
    ws.cell(row=2, column=1,
            value="الجدول العام المجمّع — %s"
                  % ("حسب الفصول" if kind == "sections" else "حسب المعلمات")
            ).font = Font(size=10)

    # العمود 1 = الاسم، العمود 2 = المجموع، ثم أعمدة الأيام
    head1, head2, first = 4, 5, 3
    ws.cell(row=head1, column=1, value="الفصل" if kind == "sections" else "المعلمة")
    ws.cell(row=head1, column=2, value="المجموع")
    ws.merge_cells(start_row=head1, start_column=1, end_row=head2, end_column=1)
    ws.merge_cells(start_row=head1, start_column=2, end_row=head2, end_column=2)

    col = first
    day_edges = []                       # آخر عمود في كل يوم - لخطّ فاصل أغمق
    for d in days:
        n = per_day[d]
        ws.cell(row=head1, column=col, value=db.DAY_NAMES[d])
        if n > 1:
            ws.merge_cells(start_row=head1, start_column=col,
                           end_row=head1, end_column=col + n - 1)
        for k in range(n):
            c = ws.cell(row=head2, column=col + k,
                        value=("فسحة" if (k + 1) in breaks else k + 1))
            c.font = Font(bold=True, size=9)
            c.alignment = Alignment(horizontal="center")
            c.border = edge_border(col + k == col + n - 1)
        cc = ws.cell(row=head1, column=col)
        cc.font = Font(bold=True)
        cc.alignment = Alignment(horizontal="center")
        cc.fill = PatternFill("solid", fgColor=pick(colored, "E0E7FF", "E6E6E6"))
        col += n
        day_edges.append(col - 1)
    last_col = col - 1

    for cell in (ws.cell(row=head1, column=1), ws.cell(row=head1, column=2)):
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E5E7EB")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = HEAVY

    r = head2 + 1
    for o in owners:
        nm = ws.cell(row=r, column=1, value=o["title"])
        nm.font = Font(bold=True, size=9)
        nm.border = BORDER
        nm.alignment = Alignment(vertical="center", wrap_text=True)

        t = ws.cell(row=r, column=2, value=totals.get(o["id"], 0))
        t.font = Font(bold=True, size=10)
        t.border = HEAVY
        t.fill = PatternFill("solid", fgColor=pick(colored, "EEF1F6", "F0F0F0"))
        t.alignment = Alignment(horizontal="center", vertical="center")

        col = first
        for d in days:
            lim = limits[o["id"]].get(d, per_day[d])
            for p in range(1, per_day[d] + 1):
                c = ws.cell(row=r, column=col)
                c.border = edge_border(col in day_edges)
                c.alignment = Alignment(horizontal="center", vertical="center",
                                        wrap_text=True)
                c.font = Font(size=8)
                if p > lim:
                    c.fill = PatternFill("solid", fgColor="E2E5EC")
                elif p in breaks:
                    c.fill = PatternFill("solid", fgColor="F3F4F6")
                else:
                    items = cells.get((o["id"], d, p))
                    if items:
                        c.value = _cell_text(items, kind)
                        if colored:
                            idx = int(items[0].get("color_index") or 0) % len(PALETTE)
                            c.fill = PatternFill("solid", fgColor=PALETTE[idx])
                col += 1
        ws.row_dimensions[r].height = 30
        r += 1

    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 9
    for k in range(first, last_col + 1):
        ws.column_dimensions[get_column_letter(k)].width = 11
    ws.freeze_panes = ws.cell(row=head2 + 1, column=first)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def build_excel(version_id, kind="sections", colored=True):
    owners, cells, days, max_p, slots, st = _fetch(version_id, kind)
    breaks = {x["period_number"] for x in slots if x["is_break"]}

    wb = Workbook()
    ws = wb.active
    ws.title = "الفصول" if kind == "sections" else "المعلمون"
    ws.sheet_view.rightToLeft = True

    title = st.get("school_name") or "جدول المدرسة"
    ws.cell(row=1, column=1, value=title).font = Font(size=16, bold=True)
    ws.cell(row=2, column=1,
            value="الجدول العام حسب %s" % ("الفصول" if kind == "sections"
                                            else "المعلمين")).font = Font(size=11)

    r = 4
    for o in owners:
        ws.cell(row=r, column=1, value=o["title"]).font = Font(size=13, bold=True)
        r += 1
        ws.cell(row=r, column=1, value="اليوم").font = Font(bold=True)
        ws.cell(row=r, column=1).fill = PatternFill("solid", fgColor="E5E7EB")
        ws.cell(row=r, column=1).border = BORDER
        for p in range(1, max_p + 1):
            c = ws.cell(row=r, column=p + 1,
                        value=("فسحة" if p in breaks else "الحصة %d" % p))
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="E5E7EB")
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border = BORDER
        r += 1

        for d in days:
            ws.cell(row=r, column=1, value=db.DAY_NAMES[d]).font = Font(bold=True)
            ws.cell(row=r, column=1).border = BORDER
            ws.cell(row=r, column=1).alignment = Alignment(
                horizontal="center", vertical="center")
            for p in range(1, max_p + 1):
                c = ws.cell(row=r, column=p + 1)
                c.border = BORDER
                c.alignment = Alignment(horizontal="center", vertical="center",
                                        wrap_text=True)
                if p in breaks:
                    c.fill = PatternFill("solid", fgColor="F3F4F6")
                    continue
                items = cells.get((o["id"], d, p))
                if items:
                    c.value = _cell_text(items, kind)
                    if colored:
                        idx = int(items[0].get("color_index") or 0) % len(PALETTE)
                        c.fill = PatternFill("solid", fgColor=PALETTE[idx])
            ws.row_dimensions[r].height = 34
            r += 1
        r += 2

    ws.column_dimensions["A"].width = 14
    for p in range(1, max_p + 1):
        ws.column_dimensions[get_column_letter(p + 1)].width = 18

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ------------------------------------------------------------------- PDF

from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                Paragraph, Spacer, PageBreak, Image as RLImage)

import arabic_reshaper
from bidi.algorithm import get_display

_FONT_READY = False
FONT_NAME = "ArabicFont"
FONT_BOLD = "ArabicFont-Bold"


def _ensure_font():
    """
    خط عربي للـPDF. نبدأ بالخط المرفق مع البرنامج (Amiri برخصة OFL)
    فيعمل على أي نظام — ويندوز أو خادم Linux عند النشر. وإن غاب نلجأ
    لخطوط النظام.
    """
    global _FONT_READY
    if _FONT_READY:
        return
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts")
    regular = bold = None

    # 1) الخط المرفق
    for name, slot in (("Amiri-Regular.ttf", "r"), ("Amiri-Bold.ttf", "b")):
        p = os.path.join(here, name)
        if os.path.exists(p):
            if slot == "r":
                regular = p
            else:
                bold = p

    # 2) خطوط النظام (ويندوز أو Linux)
    if regular is None:
        folders = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                   "/usr/share/fonts/truetype/dejavu",
                   "/usr/share/fonts/truetype/liberation",
                   "/usr/share/fonts"]
        for folder in folders:
            for cand in ("arial.ttf", "Tahoma.ttf", "tahoma.ttf", "segoeui.ttf",
                         "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
                p = os.path.join(folder, cand)
                if regular is None and os.path.exists(p):
                    regular = p
            for cand in ("arialbd.ttf", "tahomabd.ttf", "segoeuib.ttf",
                         "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"):
                p = os.path.join(folder, cand)
                if bold is None and os.path.exists(p):
                    bold = p

    if regular is None:
        raise RuntimeError("لم يُعثر على خط عربي لتصدير PDF — "
                           "تأكد من وجود static/fonts/Amiri-Regular.ttf")
    pdfmetrics.registerFont(TTFont(FONT_NAME, regular))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, bold or regular))
    _FONT_READY = True


LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "static", "img", "mark.png")


def logo_flowable(height_mm=13):
    """شعار المدرسة أعلى الصفحة، إن وُجد ملفه."""
    if not os.path.exists(LOGO_PATH):
        return None
    try:
        from PIL import Image as PILImage
        w, h = PILImage.open(LOGO_PATH).size
        hh = height_mm * mm
        img = RLImage(LOGO_PATH, width=hh * (float(w) / h), height=hh)
        img.hAlign = "CENTER"
        return img
    except Exception:
        return None


def ar(text):
    """تشكيل الحروف العربية وعكس الاتجاه ليظهر النص صحيحاً في PDF."""
    if text is None:
        return ""
    text = str(text)
    if not text:
        return ""
    try:
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def build_pdf(version_id, kind="sections", signature=False, colored=True):
    _ensure_font()
    owners, cells, days, max_p, slots, st = _fetch(version_id, kind)
    breaks = {x["period_number"] for x in slots if x["is_break"]}

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            rightMargin=10 * mm, leftMargin=10 * mm,
                            topMargin=10 * mm, bottomMargin=10 * mm)

    h1 = ParagraphStyle("h1", fontName=FONT_BOLD, fontSize=16, alignment=1,
                        leading=20)
    h2 = ParagraphStyle("h2", fontName=FONT_NAME, fontSize=10, alignment=1,
                        textColor=colors.HexColor("#555555"), leading=14)
    subj_style = ParagraphStyle("subj", fontName=FONT_BOLD, fontSize=9.5,
                                alignment=1, leading=11.5,
                                textColor=colors.HexColor("#15202F"))
    who_style = ParagraphStyle("who", fontName=FONT_NAME, fontSize=7.8,
                               alignment=1, leading=9.5,
                               textColor=colors.HexColor("#414B5C"))
    cell_style = ParagraphStyle("cell", fontName=FONT_NAME, fontSize=8,
                                alignment=1, leading=10)
    head_style = ParagraphStyle("head", fontName=FONT_BOLD, fontSize=9,
                                alignment=1, leading=11)
    day_style = ParagraphStyle("dy", fontName=FONT_BOLD, fontSize=10,
                               alignment=1, leading=12)
    brk_style = ParagraphStyle("brk", fontName=FONT_NAME, fontSize=7.5,
                               alignment=1, leading=9,
                               textColor=colors.HexColor("#8A93A3"))

    story = []
    school = st.get("school_name") or "جدول المدرسة"

    for n, o in enumerate(owners):
        if n:
            story.append(PageBreak())
        mark = logo_flowable(13)
        if mark is not None:
            story.append(mark)
            story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph(ar(school), h1))
        story.append(Paragraph(
            ar("الجدول الأسبوعي — %s" % o["title"]), h2))
        story.append(Spacer(1, 5 * mm))

        # ترتيب الأعمدة بصرياً من اليسار: الحصة الأخيرة ... الحصة 1، ثم اليوم
        # (فتُقرأ من اليمين: اليوم ثم الحصة 1 فما بعدها)
        header = [Paragraph(ar("فسحة" if p in breaks else "الحصة %d" % p), head_style)
                  for p in range(max_p, 0, -1)]
        header.append(Paragraph(ar("اليوم"), day_style))
        table_data = [header]
        tints = {}                        # (صف، عمود) -> لون خلفية المادة

        for ri, d in enumerate(days, start=1):
            row = []
            for p in range(max_p, 0, -1):
                ci = max_p - p            # موقع العمود في هذا الترتيب
                if p in breaks:
                    row.append(Paragraph(ar("فسحة"), brk_style))
                    continue
                items = cells.get((o["id"], d, p))
                if not items:
                    row.append("")
                    continue
                r0 = items[0]
                who = (r0["teacher_name"] if kind == "sections"
                       else r0["section_label"])
                row.append(Paragraph(
                    '%s<br/><font size="7.8" color="#414B5C">%s</font>'
                    % (ar(r0["subject_name"]), ar(who)), subj_style))
                tints[(ri, ci)] = PALETTE[
                    int(r0.get("color_index") or 0) % len(PALETTE)]
            row.append(Paragraph(ar(db.DAY_NAMES[d]), day_style))
            table_data.append(row)

        col_w = [(doc.width - 22 * mm) / max_p] * max_p + [22 * mm]
        t = Table(table_data, colWidths=col_w,
                  rowHeights=[10 * mm] + [17 * mm] * len(days))
        style = [
            ("GRID", (0, 0), (-1, -1), 0.9, colors.HexColor("#5B6577")),
            ("BOX", (0, 0), (-1, -1), 1.8, colors.HexColor("#2B3444")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BACKGROUND", (0, 0), (-1, 0),
             colors.HexColor(pick(colored, "#DDE3EE", "#E6E6E6"))),
            ("BACKGROUND", (-1, 1), (-1, -1),
             colors.HexColor(pick(colored, "#EDF0F6", "#F0F0F0"))),
            ("LINEBELOW", (0, 0), (-1, 0), 1.8, colors.HexColor("#2B3444")),
            ("LINEBEFORE", (-1, 0), (-1, -1), 1.8, colors.HexColor("#2B3444")),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]
        # ألوان المواد (تُلغى في وضع بلا ألوان)
        if colored:
            for (ri, ci), hexcol in tints.items():
                style.append(("BACKGROUND", (ci, ri), (ci, ri),
                              colors.HexColor("#" + hexcol)))
        for ci, p in enumerate(range(max_p, 0, -1)):
            if p in breaks:
                style.append(("BACKGROUND", (ci, 1), (ci, -1),
                              colors.HexColor("#EFF1F5")))
        t.setStyle(TableStyle(style))
        story.append(t)

        if signature:
            story.append(Spacer(1, 8 * mm))
            sig = Table(
                [[Paragraph(ar("توقيع رئيسة الإشراف التعليمي"), cell_style),
                  Paragraph(ar("الاسم: %s" % (st.get("manager_name") or "")), cell_style),
                  Paragraph(ar("التوقيع: ........................"), cell_style)]],
                colWidths=[doc.width / 3.0] * 3, rowHeights=[14 * mm])
            sig.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B8C0CC")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(sig)

    if not owners:
        story.append(Paragraph(ar("لا توجد بيانات للتصدير"), h1))

    doc.build(story)
    buf.seek(0)
    return buf


def build_pdf_combined(version_id, kind="sections", signature=False, colored=True):
    """الجدول المجمّع في صفحة واحدة عريضة (A3 أفقي)."""
    _ensure_font()
    owners, cells, days, slots, st, limits, per_day, columns, totals = _matrix(
        version_id, kind)
    breaks = {x["period_number"] for x in slots if x["is_break"]}

    page = landscape(A3)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=page, rightMargin=8 * mm,
                            leftMargin=8 * mm, topMargin=8 * mm,
                            bottomMargin=8 * mm)

    h1 = ParagraphStyle("h1", fontName=FONT_BOLD, fontSize=15, alignment=1, leading=19)
    h2 = ParagraphStyle("h2", fontName=FONT_NAME, fontSize=9, alignment=1,
                        textColor=colors.HexColor("#555555"), leading=12)
    cs = ParagraphStyle("cs", fontName=FONT_BOLD, fontSize=6.6, alignment=1,
                        leading=7.6, textColor=colors.HexColor("#15202F"))
    hs = ParagraphStyle("hs", fontName=FONT_BOLD, fontSize=7.4, alignment=1, leading=9)
    ns = ParagraphStyle("ns", fontName=FONT_BOLD, fontSize=7.2, alignment=2, leading=8.6)

    story = []
    mark = logo_flowable(12)
    if mark is not None:
        story += [mark, Spacer(1, 1.2 * mm)]
    story += [Paragraph(ar(st.get("school_name") or "جدول المدرسة"), h1),
              Paragraph(ar("الجدول العام المجمّع — %s"
                           % ("حسب الفصول" if kind == "sections" else "حسب المعلمات")),
                        h2),
              Spacer(1, 4 * mm)]

    # صفّا العنوان: الأيام ثم أرقام الحصص. الأعمدة تُعكس لأن الجدول عربي.
    row_day, row_num = [], []
    for d in days:
        n = per_day[d]
        # اسم اليوم في آخر خانات مجموعته: بعد عكس الصف يصير أول خانة في
        # الدمج، وهي وحدها التي يعرض ReportLab محتواها.
        row_day.extend([""] * (n - 1))
        row_day.append(Paragraph(ar(db.DAY_NAMES[d]), hs))
        for p in range(1, n + 1):
            row_num.append(Paragraph(ar("فسحة" if p in breaks else str(p)), hs))
    head_label = Paragraph(ar("الفصل" if kind == "sections" else "المعلمة"), hs)
    total_label = Paragraph(ar("المجموع"), hs)

    # الترتيب المنطقي: الاسم، ثم المجموع، ثم أعمدة الأيام
    data = [[head_label, total_label] + row_day,
            ["", ""] + row_num]

    body_rows = []
    tints = {}                     # (صف، عمود بعد العكس) -> لون المادة
    ncols_tmp = 2 + len(columns)
    for ri, o in enumerate(owners, start=2):
        line = []
        ci = 0                     # ترتيب العمود قبل العكس (0 = أول حصة)
        for d in days:
            lim = limits[o["id"]].get(d, per_day[d])
            for p in range(1, per_day[d] + 1):
                if p > lim or p in breaks:
                    line.append("")
                    ci += 1
                    continue
                items = cells.get((o["id"], d, p))
                if items:
                    r0 = items[0]
                    who = (r0["teacher_name"] if kind == "sections"
                           else r0["section_label"])
                    line.append(Paragraph(
                        '%s<br/><font size="5.8" color="#414B5C">%s</font>'
                        % (ar(r0.get("short_name") or r0["subject_name"]), ar(who)),
                        cs))
                    # بعد عكس الصف كاملاً: العمود ci يصير ncols-1-(ci+2)
                    tints[(ri, ncols_tmp - 3 - ci)] = PALETTE[
                        int(r0.get("color_index") or 0) % len(PALETTE)]
                else:
                    line.append("")
                ci += 1
        data.append([Paragraph(ar(o["title"]), ns),
                     Paragraph(str(totals.get(o["id"], 0)), hs)] + line)
        body_rows.append(o)

    name_w = 30 * mm
    total_w = 12 * mm
    cell_w = (doc.width - name_w - total_w) / max(1, len(columns))
    widths = [name_w, total_w] + [cell_w] * len(columns)

    # العربية من اليمين: نعكس الأعمدة وعروضها معاً
    data = [list(reversed(r)) for r in data]
    widths = list(reversed(widths))

    heights = [6.5 * mm, 5.5 * mm] + [11 * mm] * len(owners)
    t = Table(data, colWidths=widths, rowHeights=heights, repeatRows=2)

    # بعد العكس: آخر عمودين هما المجموع ثم الاسم (من اليمين)
    ncols = len(widths)
    col_total = ncols - 2
    style = [
        ("GRID", (0, 0), (-1, -1), 0.8, colors.HexColor("#5B6577")),
        ("BOX", (0, 0), (-1, -1), 1.8, colors.HexColor("#2B3444")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 1),
         colors.HexColor(pick(colored, "#DDE3F5", "#E6E6E6"))),
        ("BACKGROUND", (col_total, 2), (col_total, -1),
         colors.HexColor(pick(colored, "#E7EBF3", "#F0F0F0"))),
        ("BACKGROUND", (ncols - 1, 2), (ncols - 1, -1),
         colors.HexColor(pick(colored, "#F2F4F9", "#F7F7F7"))),
        ("LINEBELOW", (0, 1), (-1, 1), 1.8, colors.HexColor("#2B3444")),
        # خط أغمق يفصل عمودي الاسم والمجموع عن أعمدة الأيام
        ("LINEBEFORE", (col_total, 0), (col_total, -1), 1.8,
         colors.HexColor("#2B3444")),
        ("SPAN", (ncols - 1, 0), (ncols - 1, 1)),      # الاسم
        ("SPAN", (col_total, 0), (col_total, 1)),      # المجموع
        ("TOPPADDING", (0, 0), (-1, -1), 0.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5),
        ("LEFTPADDING", (0, 2), (col_total - 1, -1), 1),
        ("RIGHTPADDING", (0, 2), (col_total - 1, -1), 1),
    ]
    # ألوان المواد - نفس ألوان الشاشة وExcel (تُلغى في وضع بلا ألوان)
    if colored:
        for (ri, ci), hexcol in tints.items():
            style.append(("BACKGROUND", (ci, ri), (ci, ri),
                          colors.HexColor("#" + hexcol)))
    # دمج أسماء الأيام + خط فاصل أغمق بين كل يوم والذي يليه
    left = 0
    for d in reversed(days):
        n = per_day[d]
        style.append(("SPAN", (left, 0), (left + n - 1, 0)))
        if left > 0:
            style.append(("LINEBEFORE", (left, 0), (left, -1), 1.4,
                          colors.HexColor("#3A4354")))
        left += n
    # تظليل الخانات خارج الدوام
    for ri, o in enumerate(body_rows, start=2):
        ci = 0
        for d in reversed(days):
            n = per_day[d]
            lim = limits[o["id"]].get(d, n)
            for p in range(n, 0, -1):
                if p > lim:
                    style.append(("BACKGROUND", (ci, ri), (ci, ri),
                                  colors.HexColor("#E2E5EC")))
                elif p in breaks:
                    style.append(("BACKGROUND", (ci, ri), (ci, ri),
                                  colors.HexColor("#F3F4F6")))
                ci += 1
    t.setStyle(TableStyle(style))
    story.append(t)

    if signature:
        story.append(Spacer(1, 6 * mm))
        sig = Table([[Paragraph(ar("رئيسة الإشراف التعليمي: %s"
                                   % (st.get("manager_name") or "")), cs),
                      Paragraph(ar("التوقيع: ................"), cs),
                      Paragraph(ar("التاريخ:     /     / 14   هـ"), cs)]],
                    colWidths=[doc.width / 3.0] * 3, rowHeights=[12 * mm])
        sig.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B8C0CC")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        story.append(sig)

    if not owners:
        story.append(Paragraph(ar("لا توجد بيانات للتصدير"), h1))

    doc.build(story)
    buf.seek(0)
    return buf
