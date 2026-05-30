/* ═══════════════════════════════════════════
   LOADING
   ═══════════════════════════════════════════ */
function showLoading(t){document.getElementById('loadingText').textContent=t;document.getElementById('loadingOverlay').classList.add('show')}
function hideLoading(){document.getElementById('loadingOverlay').classList.remove('show')}
