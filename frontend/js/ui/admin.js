/* ═══════════════════════════════════════════
   ADMIN DASHBOARD
   ═══════════════════════════════════════════ */

function showAdminTab(tab, btn) {
  document.querySelectorAll('.admin-tab').forEach(function(t) { t.classList.remove('active'); });
  document.querySelectorAll('.admin-panel').forEach(function(p) { p.classList.remove('active'); });
  if (btn) btn.classList.add('active');
  document.getElementById('admin-panel-' + tab)?.classList.add('active');
  if (tab === 'health') loadAdminHealth();
  if (tab === 'alerts') loadAdminAlerts();
  if (tab === 'backups') loadAdminBackups();
}

async function loadAdminHealth() {
  const grid = document.getElementById('adminHealthGrid');
  if (!grid) return;
  grid.innerHTML = '<div class="empty-state">加载中...</div>';
  try {
    const r = await apiFetch(API + '/system/agent-health');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    const agents = d.agents || [];
    if (!agents.length) {
      grid.innerHTML = '<div class="empty-state">暂无 Agent 健康数据</div>';
      return;
    }
    grid.innerHTML = agents.map(function(a) {
      const statusColor = a.status === 'healthy' ? 'var(--success)' : a.circuit_state === 'open' ? 'var(--danger)' : 'var(--warning)';
      const statusText = a.circuit_state === 'open' ? '熔断中' : a.status === 'healthy' ? '健康' : '异常';
      return '<div class="admin-card">' +
        '<div class="admin-card-header"><span class="admin-card-title">' + escHtml(a.agent_id) + '</span>' +
        '<span class="admin-status" style="color:' + statusColor + '">' + statusText + '</span></div>' +
        '<div class="admin-card-body">' +
        '<div class="admin-metric"><span class="admin-metric-label">成功/失败</span><span class="admin-metric-value">' + (a.success_count || 0) + ' / ' + (a.failure_count || 0) + '</span></div>' +
        '<div class="admin-metric"><span class="admin-metric-label">熔断状态</span><span class="admin-metric-value">' + (a.circuit_state || 'closed') + '</span></div>' +
        '<div class="admin-metric"><span class="admin-metric-label">幻觉总数</span><span class="admin-metric-value">' + (a.total_hallucinations || 0) + '</span></div>' +
        '<div class="admin-metric"><span class="admin-metric-label">平均置信度</span><span class="admin-metric-value">' + ((a.avg_confidence || 0) * 100).toFixed(1) + '%</span></div>' +
        '</div>' +
        '<div class="admin-card-actions">' +
        '<button class="btn-ghost btn-sm" onclick="resetAgentHealth(\'' + escHtml(a.agent_id) + '\')">重置</button>' +
        '</div></div>';
    }).join('');
  } catch (e) {
    grid.innerHTML = '<div class="empty-state">加载失败: ' + escHtml(e.message) + '</div>';
  }
}

async function resetAgentHealth(agentId) {
  try {
    const r = await apiFetch(API + '/system/agent-health/' + encodeURIComponent(agentId) + '/reset', { method: 'POST' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    showToast('Agent ' + agentId + ' 已重置', 'success');
    loadAdminHealth();
  } catch (e) {
    showToast('重置失败: ' + e.message, 'error');
  }
}

async function loadAdminAlerts() {
  const tbody = document.querySelector('#adminAlertsTable tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="6" class="empty-cell">加载中...</td></tr>';
  try {
    const r = await apiFetch(API + '/system/alerts?acknowledged=false&limit=100');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    const alerts = d.alerts || [];
    if (!alerts.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty-cell">暂无未确认告警</td></tr>';
      return;
    }
    tbody.innerHTML = alerts.map(function(a) {
      const sevColor = a.severity === 'critical' ? 'var(--danger)' : a.severity === 'high' ? 'var(--danger)' : a.severity === 'medium' ? 'var(--warning)' : 'var(--text-3)';
      return '<tr>' +
        '<td>' + escHtml((a.created_at || '').slice(0, 19).replace('T', ' ')) + '</td>' +
        '<td>' + escHtml(a.alert_type || '') + '</td>' +
        '<td style="color:' + sevColor + '">' + escHtml(a.severity || '') + '</td>' +
        '<td>' + escHtml(a.agent_id || '—') + '</td>' +
        '<td>' + escHtml(a.message || '') + '</td>' +
        '<td><button class="btn-ghost btn-sm" onclick="ackAlert(\'' + escHtml(a.alert_id) + '\')">确认</button></td>' +
        '</tr>';
    }).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-cell">加载失败: ' + escHtml(e.message) + '</td></tr>';
  }
}

async function ackAlert(alertId) {
  try {
    const r = await apiFetch(API + '/system/alerts/' + encodeURIComponent(alertId) + '/acknowledge', { method: 'POST' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    showToast('告警已确认', 'success');
    loadAdminAlerts();
  } catch (e) {
    showToast('确认失败: ' + e.message, 'error');
  }
}

async function loadAdminBackups() {
  const grid = document.getElementById('adminBackupGrid');
  if (!grid) return;
  grid.innerHTML = '<div class="empty-state">加载中...</div>';
  try {
    const r = await apiFetch(API + '/system/backups');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    const backups = d.backups || [];
    if (!backups.length) {
      grid.innerHTML = '<div class="empty-state">暂无备份</div>';
      return;
    }
    grid.innerHTML = backups.map(function(b) {
      return '<div class="admin-card">' +
        '<div class="admin-card-header"><span class="admin-card-title">' + escHtml(b.filename || 'backup') + '</span></div>' +
        '<div class="admin-card-body">' +
        '<div class="admin-metric"><span class="admin-metric-label">大小</span><span class="admin-metric-value">' + (b.size || '?') + '</span></div>' +
        '<div class="admin-metric"><span class="admin-metric-label">时间</span><span class="admin-metric-value">' + escHtml(b.created_at || '') + '</span></div>' +
        '</div></div>';
    }).join('');
  } catch (e) {
    grid.innerHTML = '<div class="empty-state">加载失败: ' + escHtml(e.message) + '</div>';
  }
}
