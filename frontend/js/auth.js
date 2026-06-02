/* ═══════════════════════════════════════════
   AUTHENTICATION
   ═══════════════════════════════════════════ */

(function() {
  // Inject auth modal HTML if not present
  function ensureAuthModal() {
    if (document.getElementById('auth-modal')) return;

    const html = `
<div id="auth-modal" class="modal" style="display:none;position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.6);align-items:center;justify-content:center;">
  <div class="modal-card" style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:2rem;max-width:360px;width:90%;box-shadow:var(--shadow-xl);">
    <div class="modal-header" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1.5rem;">
      <h3 id="auth-title" style="font-size:1.25rem;font-weight:600;color:var(--text-primary);">登录</h3>
      <button onclick="hideAuthModal()" style="background:none;border:none;color:var(--text-secondary);font-size:1.25rem;cursor:pointer;">✕</button>
    </div>
    <div id="auth-error" style="color:var(--danger);font-size:0.875rem;margin-bottom:0.75rem;display:none;"></div>
    <form id="auth-form" onsubmit="return false;">
      <div style="margin-bottom:1rem;">
        <label style="display:block;font-size:0.75rem;color:var(--text-secondary);margin-bottom:0.25rem;text-transform:uppercase;letter-spacing:0.05em;">用户名</label>
        <input id="auth-username" type="text" required style="width:100%;padding:0.625rem 0.875rem;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);color:var(--text-primary);font-size:0.9375rem;outline:none;" placeholder="username">
      </div>
      <div id="auth-email-wrap" style="margin-bottom:1rem;display:none;">
        <label style="display:block;font-size:0.75rem;color:var(--text-secondary);margin-bottom:0.25rem;text-transform:uppercase;letter-spacing:0.05em;">邮箱</label>
        <input id="auth-email" type="email" style="width:100%;padding:0.625rem 0.875rem;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);color:var(--text-primary);font-size:0.9375rem;outline:none;" placeholder="email@example.com">
      </div>
      <div style="margin-bottom:1.5rem;">
        <label style="display:block;font-size:0.75rem;color:var(--text-secondary);margin-bottom:0.25rem;text-transform:uppercase;letter-spacing:0.05em;">密码</label>
        <input id="auth-password" type="password" required style="width:100%;padding:0.625rem 0.875rem;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);color:var(--text-primary);font-size:0.9375rem;outline:none;" placeholder="••••••">
      </div>
      <button id="auth-submit" type="submit" style="width:100%;padding:0.75rem;border:none;border-radius:var(--radius-md);background:var(--accent);color:#fff;font-weight:500;cursor:pointer;font-size:0.9375rem;">登录</button>
    </form>
    <div style="text-align:center;margin-top:1rem;font-size:0.875rem;color:var(--text-secondary);">
      <span id="auth-toggle-text">还没有账号？</span>
      <a href="#" id="auth-toggle" style="color:var(--accent);text-decoration:none;">立即注册</a>
    </div>
  </div>
</div>`;

    const div = document.createElement('div');
    div.innerHTML = html;
    document.body.appendChild(div.firstElementChild);

    // Bind events
    document.getElementById('auth-form').addEventListener('submit', onAuthSubmit);
    document.getElementById('auth-toggle').addEventListener('click', function(e) {
      e.preventDefault();
      toggleAuthMode();
    });
  }

  let authMode = 'login'; // 'login' | 'register'

  window.showAuthModal = function() {
    ensureAuthModal();
    authMode = 'login';
    updateAuthUI();
    document.getElementById('auth-modal').style.display = 'flex';
    document.getElementById('auth-username').focus();
  };

  window.hideAuthModal = function() {
    const m = document.getElementById('auth-modal');
    if (m) m.style.display = 'none';
  };

  function toggleAuthMode() {
    authMode = authMode === 'login' ? 'register' : 'login';
    updateAuthUI();
  }

  function updateAuthUI() {
    const title = document.getElementById('auth-title');
    const submit = document.getElementById('auth-submit');
    const emailWrap = document.getElementById('auth-email-wrap');
    const toggleText = document.getElementById('auth-toggle-text');
    const toggleLink = document.getElementById('auth-toggle');
    const error = document.getElementById('auth-error');
    if (error) error.style.display = 'none';

    if (authMode === 'login') {
      title.textContent = '登录';
      submit.textContent = '登录';
      emailWrap.style.display = 'none';
      document.getElementById('auth-email').required = false;
      toggleText.textContent = '还没有账号？';
      toggleLink.textContent = '立即注册';
    } else {
      title.textContent = '注册';
      submit.textContent = '注册';
      emailWrap.style.display = 'block';
      document.getElementById('auth-email').required = true;
      toggleText.textContent = '已有账号？';
      toggleLink.textContent = '立即登录';
    }
  }

  async function onAuthSubmit(e) {
    e.preventDefault();
    const username = document.getElementById('auth-username').value.trim();
    const password = document.getElementById('auth-password').value;
    const email = document.getElementById('auth-email').value.trim();
    const errorEl = document.getElementById('auth-error');
    const submit = document.getElementById('auth-submit');

    errorEl.style.display = 'none';
    submit.disabled = true;
    submit.textContent = '请稍候...';

    const endpoint = authMode === 'login' ? '/auth/login' : '/auth/register';
    const body = authMode === 'login'
      ? { username, password }
      : { username, email, password };

    try {
      const resp = await fetch(API + endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.detail || (authMode === 'login' ? '登录失败' : '注册失败'));
      }
      setToken(data.access_token, data.user);
      hideAuthModal();
      updateAuthHeader();
      showToast(authMode === 'login' ? '登录成功' : '注册成功', 'success');
    } catch (err) {
      errorEl.textContent = err.message;
      errorEl.style.display = 'block';
    } finally {
      submit.disabled = false;
      submit.textContent = authMode === 'login' ? '登录' : '注册';
    }
  }

  window.updateAuthHeader = function() {
    const user = getUser();
    const headerRight = document.querySelector('.header-right');
    if (!headerRight) return;

    let authBtn = document.getElementById('rt-auth-btn');
    if (!authBtn) {
      authBtn = document.createElement('button');
      authBtn.id = 'rt-auth-btn';
      authBtn.className = 'icon-btn';
      authBtn.style.marginLeft = '0.5rem';
      headerRight.insertBefore(authBtn, headerRight.firstChild);
    }

    if (user) {
      authBtn.textContent = (user.username || '?')[0].toUpperCase();
      authBtn.title = (user.username || '用户');
      authBtn.onclick = function(e) {
        showUserDropdown(e.target);
      };
    } else {
      authBtn.textContent = '登录';
      authBtn.title = '登录 / 注册';
      authBtn.onclick = showAuthModal;
    }

    // Show/hide sidebar admin link
    const adminLink = document.getElementById('adminLink');
    if (adminLink) {
      adminLink.style.display = (user && user.is_admin) ? 'block' : 'none';
    }
  };



  window.showUserDropdown = function(trigger) {
    var existing = document.getElementById('user-dropdown');
    if (existing) { existing.remove(); return; }
    var dd = document.createElement('div');
    dd.id = 'user-dropdown';
    dd.style.cssText = 'position:absolute;top:40px;right:0;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-md);padding:0.5rem 0;min-width:140px;z-index:9999;box-shadow:var(--shadow-lg);';
    dd.innerHTML = '<div style="padding:0.5rem 1rem;cursor:pointer;color:var(--text-primary);" onclick="loadUserHistory();document.getElementById(\'user-dropdown\').remove();">我的会话</div>' +
                   '<div style="padding:0.5rem 1rem;cursor:pointer;color:var(--danger);" onclick="if(confirm(\'确认退出？\')){setToken(null,null);updateAuthHeader();showToast(\'已退出\',\'info\');}document.getElementById(\'user-dropdown\').remove();">退出登录</div>';
    trigger.parentElement.style.position = 'relative';
    trigger.parentElement.appendChild(dd);
    document.addEventListener('click', function close(e) {
      if (!dd.contains(e.target) && e.target !== trigger) { dd.remove(); document.removeEventListener('click', close); }
    });
  };

  window.loadUserHistory = async function() {
    try {
      var r = await apiFetch(API + '/user/sessions?limit=50');
      var d = await r.json();
      renderHistoryModal(d.sessions || []);
    } catch (e) {
      showToast('加载历史记录失败', 'error');
    }
  };

  function escHtml(s) {
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  window.renderHistoryModal = function(sessions) {
    var existing = document.getElementById('history-modal');
    if (existing) existing.remove();
    var modal = document.createElement('div');
    modal.id = 'history-modal';
    modal.style.cssText = 'position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    var content = '<div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);max-width:480px;width:90%;max-height:70vh;overflow:auto;padding:1.5rem;">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">' +
        '<h3 style="margin:0;color:var(--text-primary);">我的会话</h3><button onclick="document.getElementById(\'history-modal\').remove()" style="background:none;border:none;color:var(--text-secondary);font-size:1.25rem;cursor:pointer;">✕</button></div>';
    if (!sessions.length) {
      content += '<div style="color:var(--text-secondary);text-align:center;padding:2rem;">暂无会话</div>';
    } else {
      sessions.forEach(function(s) {
        content += '<div style="padding:0.75rem;border:1px solid var(--border);border-radius:var(--radius-md);margin-bottom:0.5rem;cursor:pointer;" onclick="state.sessionId=\'\'+s.session_id+\'\';goStep(5);document.getElementById(\'history-modal\').remove();">' +
            '<div style="font-weight:500;color:var(--text-primary);">' + escHtml(s.title || '未命名会话') + '</div>' +
            '<div style="font-size:0.75rem;color:var(--text-secondary);margin-top:0.25rem;">' + s.session_id + ' · ' + (s.status || 'unknown') + '</div>' +
            '</div>';
      });
    }
    content += '</div>';
    modal.innerHTML = content;
    document.body.appendChild(modal);
  };
  // Auto-update header on load
  document.addEventListener('DOMContentLoaded', function() {
    ensureAuthModal();
    updateAuthHeader();
  });
})();

// Override: admin-aware dropdown
window.showUserDropdown = function(trigger) {
  var existing = document.getElementById('user-dropdown');
  if (existing) { existing.remove(); return; }
  var user = getUser();
  var dd = document.createElement('div');
  dd.id = 'user-dropdown';
  dd.style.cssText = 'position:absolute;top:40px;right:0;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-md);padding:0.5rem 0;min-width:140px;z-index:9999;box-shadow:var(--shadow-lg);';
  var items = '<div style="padding:0.5rem 1rem;cursor:pointer;color:var(--text-primary);" onclick="loadUserHistory();document.getElementById(\'user-dropdown\').remove();">我的会话</div>';
  if (user && user.is_admin) {
    items += '<div style="padding:0.5rem 1rem;cursor:pointer;color:var(--accent);" onclick="goAdmin();document.getElementById(\'user-dropdown\').remove();">管理后台</div>';
  }
  items += '<div style="padding:0.5rem 1rem;cursor:pointer;color:var(--danger);" onclick="if(confirm(\'确认退出？\')){setToken(null,null);updateAuthHeader();showToast(\'已退出\',\'info\');}document.getElementById(\'user-dropdown\').remove();">退出登录</div>';
  dd.innerHTML = items;
  trigger.parentElement.style.position = 'relative';
  trigger.parentElement.appendChild(dd);
  document.addEventListener('click', function close(e) {
    if (!dd.contains(e.target) && e.target !== trigger) { dd.remove(); document.removeEventListener('click', close); }
  });
};
