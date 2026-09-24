# -*- coding: utf-8 -*-
"""أيّ خط يغطّي أشكال العرض العربية التي يحتاجها تصدير PDF."""
import glob
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

from fontTools.ttLib import TTFont as FT

# عيّنة من أشكال العرض التي ينتجها arabic_reshaper
PROBE = [0xFE8D, 0xFEE2, 0xFEDF, 0xFE94, 0xFEB3,
         0xFEE4, 0xFE9F, 0xFEC9, 0xFEEB, 0xFE70]


def report(path):
    try:
        ft = FT(path, fontNumber=0, lazy=True)
        cmap = ft.getBestCmap()
        have = sum(1 for c in PROBE if c in cmap)
        basic = sum(1 for c in range(0x0600, 0x0700) if c in cmap)
        ft.close()
        return have, basic
    except Exception as e:
        return None, str(e)[:45]


def main():
    paths = sorted(glob.glob(r"C:/Users/lenovo/AppData/Local/Temp/claude/C--Users-lenovo-OneDrive---Alrashed--1--Desktop/0a32e7ff-a82b-4ee6-b9cb-51e986b9aed6/scratchpad/fonts/*.ttf")) + \
        sorted(glob.glob("static/fonts/*.ttf"))
    for p in paths:
        have, basic = report(p)
        if have is None:
            print("  %-32s خطأ: %s" % (os.path.basename(p), basic))
        else:
            mark = "✓ صالح" if have >= 8 else "✗ ناقص"
            print("  %-32s أشكال العرض %2d/%d · عربية %3d  %s"
                  % (os.path.basename(p), have, len(PROBE), basic, mark))
    return 0


if __name__ == "__main__":
    sys.exit(main())
