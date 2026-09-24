# -*- coding: utf-8 -*-
"""
عزل الاختبارات عن بياناتك.

الاختبارات تضيف وتحذف وتعدّل — فلا يجوز أن تلمس school.db الحقيقي.
استورد هذا الملف **قبل** أي استيراد لـ app، فينسخ قاعدة البيانات إلى ملف
مؤقّت ويحوّل كل شيء إليه، ويحذفه عند انتهاء الاختبار.
"""
import atexit
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

# --------------------------------------------------------------------
#  حاسم: نُبطل وضع Turso قبل استيراد db.
#  بعد إنشاء ملف .env صار البرنامج يتصل بالقاعدة السحابية تلقائياً —
#  ولو تُركت الاختبارات على حالها لكتبت في قاعدة المدرسة الحقيقية
#  على الإنترنت. القيمة الفارغة تمنع .env من تجاوزها أيضاً، لأن
#  محمّل .env لا يكتب فوق متغيّر موجود.
# --------------------------------------------------------------------
os.environ["TURSO_DATABASE_URL"] = ""
os.environ["TURSO_AUTH_TOKEN"] = ""

import db  # noqa: E402

assert not db.using_turso(), "الاختبارات يجب أن تعمل على SQLite محلي لا على Turso"

_REAL = db.DB_PATH
_TMP = os.path.join(tempfile.gettempdir(),
                    "jadwal_test_%d.db" % os.getpid())

if os.path.exists(_REAL):
    # أغلق دفتر WAL حتى تكون النسخة مكتملة
    try:
        c = db.connect()
        db.checkpoint(c)
        c.close()
    except Exception:
        pass
    shutil.copy2(_REAL, _TMP)

db.DB_PATH = _TMP


def _cleanup():
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(_TMP + suffix)
        except OSError:
            pass


atexit.register(_cleanup)

print("قاعدة اختبار مؤقّتة: %s  (بياناتك الحقيقية غير مُمَسّة)\n"
      % os.path.basename(_TMP))
