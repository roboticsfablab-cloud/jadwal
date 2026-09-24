# -*- coding: utf-8 -*-
"""
يجهّز ملفات الشعار من صورة المصدر:
  static/img/logo.png     الشعار كاملاً (القوس + الاسم) بخلفية شفافة
  static/img/mark.png     القوس وحده - للشريط العلوي والطباعة
  static/img/favicon.png  أيقونة التبويب

  python make_logo.py "<مسار الصورة>"
"""
import glob
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "static", "img")


def is_bg(px, tol=26):
    """خلفية فاتحة شبه رمادية."""
    r, g, b = px[:3]
    return r > 255 - tol - 18 and g > 255 - tol - 18 and b > 255 - tol - 18 \
        and max(r, g, b) - min(r, g, b) < 14


def bbox(im, want_color=False, step=2):
    """حدود المحتوى: كل ما ليس خلفية، أو الملوّن وحده."""
    w, h = im.size
    x0, y0, x1, y1 = w, h, 0, 0
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = im.getpixel((x, y))[:3]
            if is_bg((r, g, b)):
                continue
            if want_color and max(r, g, b) - min(r, g, b) < 45:
                continue          # نتجاهل النص الأسود
            x0, y0 = min(x0, x), min(y0, y)
            x1, y1 = max(x1, x), max(y1, y)
    return (x0, y0, x1 + 1, y1 + 1)


def transparent(im, tol=26):
    """يجعل الخلفية الفاتحة شفافة مع تنعيم الحواف."""
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            mx, mn = max(r, g, b), min(r, g, b)
            if mx - mn < 14:                    # رمادي: خلفية أو نص
                if mx > 236:
                    px[x, y] = (r, g, b, 0)     # خلفية -> شفاف
                elif mx > 200:                  # حافة -> شفافية جزئية
                    px[x, y] = (r, g, b, int((236 - mx) / 36.0 * 255))
    return im


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        src = args[0]
    else:
        cand = [f for f in glob.glob(os.path.expanduser("~/Downloads/*.jpeg")) +
                glob.glob(os.path.expanduser("~/Downloads/*.jpg"))
                if "WhatsApp" in f]
        if not cand:
            print("حدّد مسار صورة الشعار")
            return 1
        src = max(cand, key=os.path.getmtime)

    print("المصدر:", os.path.basename(src))
    os.makedirs(OUT, exist_ok=True)
    im = Image.open(src).convert("RGB")

    full = im.crop(bbox(im))
    mark = im.crop(bbox(im, want_color=True))
    print("الشعار كاملاً:", full.size, " القوس:", mark.size)

    f = transparent(full)
    f.thumbnail((720, 720), Image.LANCZOS)
    f.save(os.path.join(OUT, "logo.png"))

    m = transparent(mark)
    m.thumbnail((512, 512), Image.LANCZOS)
    m.save(os.path.join(OUT, "mark.png"))

    ic = m.copy()
    side = max(ic.size)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(ic, ((side - ic.size[0]) // 2, (side - ic.size[1]) // 2), ic)
    sq.resize((128, 128), Image.LANCZOS).save(os.path.join(OUT, "favicon.png"))

    for n in ("logo.png", "mark.png", "favicon.png"):
        p = os.path.join(OUT, n)
        print("  %-14s %s  %d بايت"
              % (n, Image.open(p).size, os.path.getsize(p)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
