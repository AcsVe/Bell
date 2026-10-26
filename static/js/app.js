// Admin and sign-in pages (templates/admin/base.html, templates/auth/_layout.html).
// ── layout: off-canvas sidebar on phones, user menu, password eye ─────────
(function () {
  var body = document.body;
  function closeNav() { body.classList.remove('nav-open'); }
  document.addEventListener('click', function (e) {
    var t = e.target;
    if (t.closest('[data-nav-toggle]')) { body.classList.toggle('nav-open'); return; }
    if (t.closest('[data-nav-close]')) { closeNav(); return; }
    if (t.closest('.sidebar a')) closeNav();
    // a click outside an open menu closes it
    document.querySelectorAll('details.user-menu[open]').forEach(function (d) {
      if (!d.contains(t)) d.removeAttribute('open');
    });
    var eye = t.closest('[data-eye]');
    if (eye) {
      var input = document.getElementById(eye.getAttribute('data-eye'));
      if (!input) return;
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      eye.setAttribute('aria-pressed', show ? 'true' : 'false');
      var i = eye.querySelector('i');
      if (i) { i.classList.toggle('fa-eye', !show); i.classList.toggle('fa-eye-slash', show); }
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      closeNav();
      document.querySelectorAll('details.user-menu[open]').forEach(function (d) { d.removeAttribute('open'); });
    }
  });
  // the active link stays in view in a long sidebar
  var active = document.querySelector('.side-nav .nav-item.active');
  if (active && active.scrollIntoView) active.scrollIntoView({ block: 'nearest' });
})();
