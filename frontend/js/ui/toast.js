/* ═══════════════════════════════════════════
   TOAST — Error & Success Notifications
   ═══════════════════════════════════════════ */
(function(){
  const toastEl = document.createElement('div');
  toastEl.id = 'toast';
  toastEl.className = 'toast';
  toastEl.setAttribute('role', 'alert');
  toastEl.setAttribute('aria-live', 'assertive');
  document.body.appendChild(toastEl);
})();

function showToast(message, type) {
  type = type || 'error';
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = message;
  el.className = 'toast show toast-' + type;
  if (el._toastTimer) clearTimeout(el._toastTimer);
  el._toastTimer = setTimeout(function() {
    el.classList.remove('show');
  }, 4000);
}
