/* جدول المدرسة — سلوكيات الواجهة */

/* إخفاء الرسائل تلقائياً بعد قليل */
setTimeout(function () {
  document.querySelectorAll('.flash').forEach(function (el) {
    el.style.transition = 'opacity .4s, transform .4s';
    el.style.opacity = '0';
    el.style.transform = 'translateY(-6px)';
    setTimeout(function () { el.remove(); }, 420);
  });
}, 5000);

/* موجة ضغط خفيفة داخل الأزرار - تعطي إحساساً بالاستجابة */
document.addEventListener('pointerdown', function (e) {
  var btn = e.target.closest('.btn, .iconcard');
  if (!btn || btn.disabled) return;
  var rect = btn.getBoundingClientRect();
  var r = document.createElement('span');
  r.className = 'ripple';
  var size = Math.max(rect.width, rect.height);
  r.style.width = r.style.height = size + 'px';
  r.style.left = (e.clientX - rect.left - size / 2) + 'px';
  r.style.top = (e.clientY - rect.top - size / 2) + 'px';
  if (getComputedStyle(btn).position === 'static') btn.style.position = 'relative';
  btn.appendChild(r);
  setTimeout(function () { r.remove(); }, 520);
});

/* زر الإرسال يصير «جارٍ…» فلا يُضغط مرتين */
document.addEventListener('submit', function (e) {
  var form = e.target;
  if (form.dataset.noBusy !== undefined) return;
  var btn = form.querySelector('button:not([type=button]):not(.danger)');
  if (!btn || btn.dataset.busy) return;
  setTimeout(function () {
    if (!form.checkValidity || form.checkValidity()) {
      btn.dataset.busy = '1';
      btn.dataset.label = btn.textContent;
      btn.classList.add('is-busy');
      btn.disabled = true;
    }
  }, 0);
});

/* تلميح على الأزرار التي تحمل title */
document.querySelectorAll('.btn[title]').forEach(function (b) {
  b.setAttribute('aria-label', b.getAttribute('title'));
});
