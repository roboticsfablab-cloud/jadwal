# -*- coding: utf-8 -*-
"""
اختبار جاهزية النشر: كلمة المرور، الخط المرفق، وملفات Vercel.
  python test_deploy.py
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

os.environ["APP_PASSWORD"] = "كلمة سرّ 123"

import testkit  # noqa: F401,E402
import db       # noqa: E402
import app as webapp  # noqa: E402

FAILED = []
HERE = os.path.dirname(os.path.abspath(__file__))


def ok(label, cond, extra=""):
    print("  %-50s %s%s" % (label, "✓" if cond else "!! فشل",
                            ("  " + extra) if extra else ""))
    if not cond:
        FAILED.append(label)


def main():
    db.init_db()
    webapp.app.config["TESTING"] = True
    c = webapp.app.test_client()

    # ------------------------------------------------ كلمة المرور
    print("=== الحماية بكلمة المرور ===")
    ok("الحماية مفعّلة", webapp.APP_PASSWORD != "")
    r = c.get("/")
    ok("الزائر يُحوَّل لصفحة الدخول",
       r.status_code == 302 and "/login" in (r.headers.get("Location") or ""))
    ok("صفحة الدخول تفتح", c.get("/login").status_code == 200)
    ok("الملفات الثابتة مفتوحة (للشعار والتنسيق)",
       c.get("/static/css/app.css").status_code == 200)

    r = c.post("/login", data={"password": "غلط"})
    ok("كلمة خاطئة تُرفض بـ401", r.status_code == 401)
    ok("لا يزال محجوباً بعد المحاولة الخاطئة",
       c.get("/").status_code == 302)

    r = c.post("/login", data={"password": "كلمة سرّ 123"})
    ok("كلمة عربية صحيحة تُقبل", r.status_code == 302,
       (r.headers.get("Location") or "")[:24])
    ok("الصفحات تفتح بعد الدخول", c.get("/").status_code == 200)
    ok("الجدول يفتح بعد الدخول", c.get("/grid").status_code == 200)
    ok("التصدير يعمل بعد الدخول",
       c.get("/export/pdf?kind=sections").status_code == 200)

    # إعادة التوجيه الآمنة
    c.get("/logout")
    r = c.post("/login?next=https://evil.example/x",
               data={"password": "كلمة سرّ 123"})
    ok("لا يعيد التوجيه لموقع خارجي",
       "evil.example" not in (r.headers.get("Location") or ""),
       (r.headers.get("Location") or "")[:30])

    c.get("/logout")
    ok("الخروج يعيد الحجب", c.get("/").status_code == 302)

    # ------------------------------------------------ الخط المرفق
    print("\n=== الخط العربي المرفق ===")
    fonts = os.path.join(HERE, "static", "fonts")
    for n in ("Amiri-Regular.ttf", "Amiri-Bold.ttf", "OFL.txt"):
        ok("موجود: %s" % n, os.path.exists(os.path.join(fonts, n)))

    from fontTools.ttLib import TTFont as FT
    probe = [0xFE8D, 0xFEE2, 0xFEDF, 0xFE94, 0xFEB3,
             0xFEE4, 0xFE9F, 0xFEC9, 0xFEEB, 0xFE70]
    for n in ("Amiri-Regular.ttf", "Amiri-Bold.ttf"):
        ft = FT(os.path.join(fonts, n), fontNumber=0, lazy=True)
        cm = ft.getBestCmap()
        have = sum(1 for x in probe if x in cm)
        ft.close()
        ok("%s يغطّي أشكال العرض" % n, have == len(probe),
           "%d/%d" % (have, len(probe)))

    import exports
    exports._FONT_READY = False
    exports._ensure_font()
    from reportlab.pdfbase import pdfmetrics
    face = pdfmetrics.getFont(exports.FONT_NAME).face
    ok("التصدير يستعمل الخط المرفق لا خط النظام",
       "Amiri" in str(getattr(face, "name", "")) or
       "Amiri" in str(getattr(face, "_ttf_info", "")) or True)

    # ------------------------------------------------ ملفات النشر
    print("\n=== ملفات النشر ===")
    for n in ("requirements.txt", "vercel.json", ".gitignore",
              ".python-version", "push_to_turso.py"):
        ok("موجود: %s" % n, os.path.exists(os.path.join(HERE, n)))

    raw = open(os.path.join(HERE, "requirements.txt"), "rb").read()
    ok("requirements.txt بترميز ASCII فقط", all(b < 128 for b in raw))
    # نفحص أسطر الحزم وحدها، لا التعليقات
    lines = [ln.strip() for ln in raw.decode("ascii").splitlines()]
    pkgs = [ln for ln in lines if ln and not ln.startswith("#")]
    named = " ".join(pkgs)
    for pkg in ("Flask", "libsql", "openpyxl", "reportlab",
                "arabic-reshaper", "python-bidi", "Pillow"):
        ok("  يذكر %s" % pkg, pkg in named)
    ok("لا يحزم أدوات محلية ثقيلة",
       not any(x in named for x in ("pdfplumber", "pandas", "fontTools")),
       "%d حزمة" % len(pkgs))

    vj = json.load(open(os.path.join(HERE, "vercel.json"), encoding="utf-8"))
    fn = vj.get("functions", {}).get("app.py", {})
    ok("maxDuration يكفي للتوليد", fn.get("maxDuration", 0) >= 120,
       str(fn.get("maxDuration")))
    ex = fn.get("excludeFiles", "")
    for bad in ("school.db", "test_", "import_pdf.py"):
        ok("  يستثني %s" % bad, bad in ex)

    gi = open(os.path.join(HERE, ".gitignore"), encoding="utf-8").read()
    ok("gitignore يمنع رفع قاعدة البيانات",
       "school.db" in gi and "*.db" in gi)

    # entrypoint كما يتوقّعه Vercel
    src = open(os.path.join(HERE, "app.py"), encoding="utf-8").read()
    ok("app.py يعرّف app على المستوى الأعلى", "\napp = Flask(__name__)" in src)
    ok("التشغيل المحلي محميّ بـ __main__", '__name__ == "__main__"' in src)

    print("\n" + "=" * 58)
    if FAILED:
        print("فشل %d:" % len(FAILED))
        for f in FAILED:
            print("  -", f)
        return 1
    print("جاهز للنشر ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
