/* ═══════════════════════════════════════════
   CONFIG
   ═══════════════════════════════════════════ */
const CLOUD_SVG='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"><path d="M4 16C4 10 8 6 14 6C10 6 6 10 6 16"/><path d="M6 13C6 8.5 9 5 14 5"/></svg>';
const API = window.location.port === '8000'
  ? ''
  : (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
    ? 'http://127.0.0.1:8000'
    : window.location.origin;  // 同源部署时用相对路径，避免 localhost/127.0.0.1 混用
const PIPE_IDS=['pipe-evidence','pipe-agents','pipe-review','pipe-memory','pipe-report'];
const REVIEW_PIPELINE=['构建证据','分派','审查','记忆','报告'];
const DEBATE_PIPELINE=['构建证据','Round 1','Round 2','共识','报告'];

/* ═══════════════════════════════════════════
   AUTH HELPERS
   ═══════════════════════════════════════════ */

function getToken() {
  return localStorage.getItem('rt_token') || '';
}

function getUser() {
  try {
    return JSON.parse(localStorage.getItem('rt_user') || 'null');
  } catch (e) {
    return null;
  }
}

function setToken(token, user) {
  if (token) {
    localStorage.setItem('rt_token', token);
    localStorage.setItem('rt_user', JSON.stringify(user));
  } else {
    localStorage.removeItem('rt_token');
    localStorage.removeItem('rt_user');
  }
}

/**
 * Unified fetch wrapper that injects Authorization header.
 * On 401, triggers the login modal.
 * Clones options to avoid mutating caller's object.
 */
async function apiFetch(url, options) {
  const token = getToken();
  const opts = {
    ...(options || {}),
    headers: {
      ...((options && options.headers) || {}),
      ...(token ? { Authorization: 'Bearer ' + token } : {}),
    },
  };

  const resp = await fetch(url, opts);
  if (resp.status === 401) {
    setToken(null, null);
    if (typeof showAuthModal === 'function') {
      showAuthModal();
    }
    const err = new Error('登录已过期，请重新登录');
    err.status = 401;
    throw err;
  }
  if (resp.status === 429) {
    var data;
    try { data = await resp.json(); } catch (e) { data = {}; }
    if (data && data.error === 'quota_exceeded') {
      showToast('月度配额已用完，请升级套餐', 'warning', 5000);
      if (typeof showUpgradeModal === 'function') showUpgradeModal(data);
    } else if (data && data.error === 'rate_limited') {
      showToast(data.message || '请求过于频繁，请稍后再试', 'warning', 3000);
    } else {
      showToast('请求过于频繁，请稍后再试', 'warning', 3000);
    }
    const err = new Error(data.message || '请求过于频繁');
    err.status = 429;
    throw err;
  }
  return resp;
}

/**
 * Create an EventSource.
 * The stream_url from backend already contains a one-time stream_key.
 * No JWT is exposed in the URL.
 */
function apiEventSource(url) {
  return new EventSource(url);
}

/* ═══════════════════════════════════════════
   MOBILE SIDEBAR
   ═══════════════════════════════════════════ */

function toggleMobileSidebar() {
  var sb = document.querySelector('.sidebar');
  if (sb) sb.classList.toggle('open');
}

// Close sidebar when clicking a step on mobile
document.addEventListener('click', function(e) {
  var sb = document.querySelector('.sidebar');
  if (!sb || !sb.classList.contains('open')) return;
  if (e.target.closest('.step-item')) {
    sb.classList.remove('open');
  }
});
