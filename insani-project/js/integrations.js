/* ═══════════════════════════════════════════════
   DASHBOARD — Data overview + integration management
   ═══════════════════════════════════════════════ */

var integrationProviders = [];
var integrationConnections = [];
var dashboardStats = null;

var PROVIDER_BRANDS = {
  gmail:      { color: '#EA4335', bg: 'rgba(234,67,53,0.08)', label: 'Gmail', svg: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#EA4335" stroke-width="2" stroke-linecap="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>' },
  quickbooks: { color: '#2CA01C', bg: 'rgba(44,160,28,0.08)', label: 'QuickBooks', svg: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#2CA01C" stroke-width="2" stroke-linecap="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/></svg>' },
  procore:    { color: '#F47E20', bg: 'rgba(244,126,32,0.08)', label: 'Procore', svg: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#F47E20" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>' },
  autodesk:   { color: '#0696D7', bg: 'rgba(6,150,215,0.08)', label: 'Autodesk', svg: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0696D7" stroke-width="2" stroke-linecap="round"><polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5 12 2"/><line x1="12" y1="22" x2="12" y2="15.5"/><polyline points="22 8.5 12 15.5 2 8.5"/></svg>' },
  outlook:    { color: '#0078D4', bg: 'rgba(0,120,212,0.08)', label: 'Outlook', svg: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0078D4" stroke-width="2" stroke-linecap="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>' },
  dropbox:    { color: '#0061FF', bg: 'rgba(0,97,255,0.08)', label: 'Dropbox', svg: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0061FF" stroke-width="2" stroke-linecap="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>' },
  sharepoint: { color: '#038387', bg: 'rgba(3,131,135,0.08)', label: 'SharePoint', svg: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#038387" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>' },
  primavera:  { color: '#E30613', bg: 'rgba(227,6,19,0.08)', label: 'Primavera P6', svg: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#E30613" stroke-width="2" stroke-linecap="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>' },
  sage:       { color: '#00B140', bg: 'rgba(0,177,64,0.08)', label: 'Sage 300', svg: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#00B140" stroke-width="2" stroke-linecap="round"><rect x="1" y="4" width="22" height="16" rx="2" ry="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>' },
};

var TYPE_LABELS = { email:'Emails', invoice:'Invoices', bill:'Bills', payment:'Payments', estimate:'Estimates', vendor:'Vendors', customer:'Customers', account:'Accounts', document:'Documents', drawing:'Drawings', photo:'Photos', spreadsheet:'Spreadsheets', rfi:'RFIs', submittal:'Submittals', change_order:'Change Orders', daily_log:'Daily Logs', calendar_event:'Calendar', schedule_project:'Schedules', schedule_activity:'Activities', job_cost:'Job Costs', purchase_order:'POs' };
var TYPE_COLORS = { email:'#EA4335', invoice:'#2CA01C', bill:'#F47E20', vendor:'#0696D7', customer:'#0078D4', account:'#038387', drawing:'#0061FF', document:'#8B5CF6', photo:'#EC4899' };

// ═══ LOAD ═══
async function loadDashboard() {
  try { integrationProviders = await apiFetch('/v1/integrations/providers'); } catch(e) { integrationProviders = []; }
  try { integrationConnections = await apiFetch('/v1/integrations/connections'); } catch(e) { integrationConnections = []; }
  try { dashboardStats = await apiFetch('/v1/integrations/dashboard/stats'); } catch(e) { dashboardStats = null; }
  renderDash();
}

function getConnection(p) { return integrationConnections.find(function(c) { return c.provider === p; }); }

// ═══ RENDER ═══
function renderDash() {
  var el = document.getElementById('integrationsView');
  if (!el) return;
  var s = dashboardStats || {};
  var cc = integrationConnections.filter(function(c) { return c.status === 'connected'; }).length;
  var connected = integrationProviders.filter(function(p) { var c = getConnection(p.provider); return c && c.status === 'connected'; });
  var available = integrationProviders.filter(function(p) { var c = getConnection(p.provider); return !c || c.status !== 'connected'; });

  var h = '<div class="dash-page">';

  // Back
  h += '<button class="dash-back-btn" onclick="showChat()"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="15 18 9 12 15 6"/></svg> Back to chat</button>';

  // Header
  h += '<div class="dash-pg-header"><div class="dash-pg-title">Dashboard</div>';
  h += '<div class="dash-pg-sub">Your connected data at a glance</div></div>';

  // ── Stats ──
  h += '<div class="ds-row">';
  h += '<div class="ds-card ds-blue"><div class="ds-label">Integrations</div><div class="ds-val">' + cc + '<span style="font-size:0.7rem;color:var(--text-dim);font-family:var(--body)"> / ' + integrationProviders.length + '</span></div></div>';
  h += '<div class="ds-card ds-green"><div class="ds-label">Synced Items</div><div class="ds-val">' + fmtN(s.total_synced_items || 0) + '</div></div>';
  h += '<div class="ds-card ds-orange"><div class="ds-label">Documents</div><div class="ds-val">' + fmtN(s.documents || 0) + '</div></div>';
  h += '<div class="ds-card ds-dark"><div class="ds-label">Conversations</div><div class="ds-val">' + fmtN(s.chat_sessions || 0) + '</div></div>';
  h += '</div>';

  // ── Integrations ──
  h += '<div class="ds-section">';
  h += '<div class="ds-section-head"><div class="ds-section-title">Integrations</div>';
  if (cc > 0) h += '<button class="ds-section-btn" onclick="syncAll()">Sync all</button>';
  h += '</div>';
  h += '<div class="ds-integ-list">';
  connected.forEach(function(p) { h += tile(p, true); });
  available.forEach(function(p) { h += tile(p, false); });
  h += '</div></div>';

  // ── Data breakdown ──
  var types = s.items_by_type || {};
  var keys = Object.keys(types).sort(function(a,b) { return types[b] - types[a]; });
  var maxVal = keys.length > 0 ? types[keys[0]] : 1;
  if (keys.length > 0) {
    h += '<div class="ds-section">';
    h += '<div class="ds-section-head"><div class="ds-section-title">Your Data</div></div>';
    h += '<div class="ds-data-list">';
    keys.forEach(function(k) {
      var pct = Math.max(4, Math.round((types[k] / maxVal) * 100));
      var col = TYPE_COLORS[k] || 'var(--text-muted)';
      h += '<div class="ds-data-row">';
      h += '<div class="ds-data-label">' + (TYPE_LABELS[k] || k) + '</div>';
      h += '<div class="ds-data-bar-wrap"><div class="ds-data-bar" style="width:' + pct + '%;background:' + col + '"></div></div>';
      h += '<div class="ds-data-count">' + types[k] + '</div>';
      h += '</div>';
    });
    h += '</div></div>';
  }

  // ── Activity ──
  var syncs = s.recent_syncs || [];
  if (syncs.length > 0) {
    h += '<div class="ds-section">';
    h += '<div class="ds-section-head"><div class="ds-section-title">Recent Activity</div></div>';
    h += '<div class="ds-timeline">';
    syncs.slice(0, 6).forEach(function(sy) {
      var b = PROVIDER_BRANDS[sy.provider] || { label: sy.provider };
      var dotClass = sy.status === 'success' ? 'tl-green' : sy.status === 'error' ? 'tl-red' : 'tl-gray';
      h += '<div class="ds-tl-item">';
      h += '<div class="ds-tl-dot ' + dotClass + '"></div>';
      h += '<div class="ds-tl-content">';
      h += '<div class="ds-tl-text"><strong>' + esc(b.label) + '</strong> synced ' + sy.items_fetched + ' items</div>';
      h += '<div class="ds-tl-time">' + fmtT(sy.completed_at) + '</div>';
      h += '</div></div>';
    });
    h += '</div></div>';
  }

  h += '</div>';
  el.innerHTML = h;
}

function tile(provider, on) {
  var b = PROVIDER_BRANDS[provider.provider] || { color:'#888', bg:'rgba(0,0,0,0.04)', label: provider.name, svg:'' };
  var conn = getConnection(provider.provider);
  var s = dashboardStats || {};
  var cnt = (s.items_by_provider || {})[provider.provider] || 0;

  var h = '<div class="ds-tile ' + (on ? 'ds-on' : 'ds-off') + '">';

  // Icon
  h += '<div class="ds-tile-icon" style="background:' + b.bg + '">' + b.svg + '</div>';

  // Info
  h += '<div class="ds-tile-info">';
  h += '<div class="ds-tile-name">' + esc(provider.name) + '</div>';
  h += '<div class="ds-tile-meta">';

  if (on && conn) {
    if (cnt > 0) h += '<span class="ds-tile-badge badge-green">' + cnt + ' items</span>';
    else h += '<span class="ds-tile-badge badge-green">Connected</span>';
    if (conn.last_sync_at) { h += '<span class="ds-tile-dot"></span><span style="font-family:var(--mono);font-size:0.6rem;color:var(--text-dim)">' + fmtT(conn.last_sync_at) + '</span>'; }
  } else {
    h += '<span style="font-size:0.68rem;color:var(--text-dim)">' + esc(provider.description) + '</span>';
  }

  h += '</div></div>';

  // Actions
  h += '<div class="ds-tile-actions">';
  if (on) {
    h += '<button class="ds-btn ds-btn-light" onclick="event.stopPropagation();testConnection(\'' + provider.provider + '\')">Test</button>';
    h += '<button class="ds-btn ds-btn-light" onclick="event.stopPropagation();viewSyncedData(\'' + provider.provider + '\')">View data</button>';
    h += '<button class="ds-btn ds-btn-light" onclick="event.stopPropagation();syncIntegration(\'' + provider.provider + '\')">Sync</button>';
    h += '<button class="ds-btn ds-btn-ghost" onclick="event.stopPropagation();disconnectIntegration(\'' + provider.provider + '\')">×</button>';
  } else {
    h += '<button class="ds-btn ds-btn-dark" onclick="event.stopPropagation();connectIntegration(\'' + provider.provider + '\')">Connect</button>';
  }
  h += '</div></div>';
  return h;
}

function fmtN(n) { return n >= 1000 ? (n/1000).toFixed(1)+'k' : String(n); }
function fmtT(d) {
  if (!d) return 'never';
  try { var s = Math.floor((new Date() - new Date(d+'Z'))/1000); return s<60?'just now':s<3600?Math.floor(s/60)+'m ago':s<86400?Math.floor(s/3600)+'h ago':Math.floor(s/86400)+'d ago'; } catch(e) { return d; }
}

// ═══ ACTIONS ═══
async function connectIntegration(provider) {
  try {
    var data = await apiFetch('/v1/integrations/connect/' + provider);
    if (!data.auth_url) return;
    var w=600, h=700;
    var popup = window.open(data.auth_url, 'insani_oauth', 'width='+w+',height='+h+',left='+(screen.width-w)/2+',top='+(screen.height-h)/2+',toolbar=no,menubar=no');
    var poll = setInterval(function() {
      try {
        if (!popup || popup.closed) { clearInterval(poll); setTimeout(function(){ loadDashboard(); updateSidebarSources(); }, 1000); }
        if (popup && popup.location && popup.location.hostname === 'localhost') {
          var url = popup.location.href;
          if (url.indexOf('integration_connected') !== -1) { popup.close(); clearInterval(poll); showToast((PROVIDER_BRANDS[provider]||{}).label+' connected!'); loadDashboard(); updateSidebarSources(); }
          else if (url.indexOf('integration_error') !== -1) { popup.close(); clearInterval(poll); showToast('Connection failed'); loadDashboard(); }
        }
      } catch(e) {}
    }, 500);
  } catch(e) { showToast('Failed to connect'); }
}

async function disconnectIntegration(provider) {
  var label = (PROVIDER_BRANDS[provider]||{}).label || provider;
  if (!confirm('Disconnect '+label+'?')) return;
  try { await apiFetch('/v1/integrations/'+provider, {method:'DELETE'}); showToast(label+' disconnected'); await loadDashboard(); updateSidebarSources(); }
  catch(e) { showToast('Failed'); }
}

async function syncIntegration(provider) {
  var btn = event.target; btn.textContent = '…'; btn.disabled = true;
  try {
    var r = await apiFetch('/v1/integrations/sync/'+provider, {method:'POST'});
    showToast(r.status==='success' ? r.items_fetched+' items synced' : 'Sync error');
    await loadDashboard();
  } catch(e) { showToast('Sync failed'); }
  btn.textContent = 'Sync'; btn.disabled = false;
}

async function syncAll() {
  showToast('Syncing all…');
  try { await apiFetch('/v1/integrations/sync-all', {method:'POST'}); showToast('All synced'); await loadDashboard(); }
  catch(e) { showToast('Sync failed'); }
}

async function testConnection(provider) {
  var label = (PROVIDER_BRANDS[provider]||{}).label || provider;
  showToast('Testing ' + label + '...');
  try {
    var result = await apiFetch('/v1/integrations/test/' + provider);
    var msg = '';
    if (result.reachable) {
      msg = '<p style="color:var(--green);font-weight:600">✓ ' + esc(label) + ' is reachable</p>';
      if (result.account && result.account.email) msg += '<p><strong>Account:</strong> ' + esc(result.account.email) + '</p>';
      if (result.account && result.account.name) msg += '<p><strong>Name:</strong> ' + esc(result.account.name) + '</p>';
      if (result.last_sync_at) msg += '<p><strong>Last sync:</strong> ' + esc(result.last_sync_at) + '</p>';
      if (result.last_sync_status) msg += '<p><strong>Last status:</strong> ' + esc(result.last_sync_status) + '</p>';
    } else {
      msg = '<p style="color:var(--red);font-weight:600">✗ ' + esc(label) + ' not reachable</p>';
      if (result.error) msg += '<p><strong>Error:</strong> ' + esc(result.error) + '</p>';
    }
    showIntegrationModal(label + ' — Connection Test', msg);
  } catch(e) {
    showToast('Test failed: ' + e.message);
  }
}

async function viewSyncedData(provider) {
  var label = (PROVIDER_BRANDS[provider]||{}).label || provider;
  showToast('Loading ' + label + ' data...');
  try {
    var result = await apiFetch('/v1/integrations/data/' + provider + '?limit=25');
    var msg = '<p><strong>' + result.total + '</strong> recent items synced from ' + esc(label) + '</p>';
    if (!result.items.length) {
      msg += '<p style="color:var(--text-dim)">No items synced yet. Click "Sync" to pull data.</p>';
    } else {
      msg += '<div style="max-height:60vh;overflow-y:auto;margin-top:0.5rem">';
      result.items.forEach(function(it) {
        msg += '<div style="padding:0.6rem;border:1px solid var(--border);border-radius:8px;margin-bottom:0.4rem">';
        msg += '<div style="font-size:0.7rem;color:var(--text-dim);text-transform:uppercase;margin-bottom:0.15rem">' + esc(it.item_type) + '</div>';
        msg += '<div style="font-weight:500;font-size:0.85rem;margin-bottom:0.25rem">' + esc(it.title || '(no title)') + '</div>';
        if (it.summary) msg += '<div style="font-size:0.75rem;color:var(--text-secondary);line-height:1.4">' + esc(it.summary) + '</div>';
        if (it.source_url) msg += '<div style="margin-top:0.3rem"><a href="' + esc(it.source_url) + '" target="_blank" style="font-size:0.7rem;color:var(--blue)">Open in ' + esc(label) + ' →</a></div>';
        if (it.item_date) msg += '<div style="font-size:0.65rem;color:var(--text-dim);font-family:var(--mono);margin-top:0.2rem">' + esc(it.item_date) + '</div>';
        msg += '</div>';
      });
      msg += '</div>';
    }
    showIntegrationModal(label + ' — Synced Data', msg);
  } catch(e) {
    showToast('Failed to load data: ' + e.message);
  }
}

function showIntegrationModal(title, bodyHtml) {
  var html = '<div class="agent-modal-inner">' +
    '<div class="agent-modal-header"><h3>' + esc(title) + '</h3><button class="dv-close" onclick="closeIntegrationModal()">&#10005;</button></div>' +
    '<div style="font-size:0.82rem;color:var(--text-secondary)">' + bodyHtml + '</div>' +
    '<div style="display:flex;justify-content:flex-end;margin-top:1rem">' +
    '<button class="disc-btn disc-btn-cancel" onclick="closeIntegrationModal()">Close</button>' +
    '</div></div>';
  document.getElementById('integrationModal').innerHTML = html;
  document.getElementById('integrationModal').classList.add('open');
}

function closeIntegrationModal() {
  document.getElementById('integrationModal').classList.remove('open');
}

// ═══ VIEW ═══
function showIntegrations() {
  var cv = document.getElementById('chatView');
  var dv = document.getElementById('dashView');
  var iv = document.getElementById('integrationsView');
  var ia = document.querySelector('.input-area');
  if (cv) cv.classList.add('vh');
  if (dv) dv.classList.add('vh');
  if (iv) { iv.classList.remove('vh'); iv.style.display = ''; }
  if (ia) { ia.classList.add('vh'); ia.style.display = 'none'; }
  loadDashboard();
}

// ═══ CALLBACK ═══
function checkIntegrationCallback() {
  var p = new URLSearchParams(window.location.search);
  if (p.get('integration_connected')) { showToast((PROVIDER_BRANDS[p.get('integration_connected')]||{}).label+' connected!'); window.history.replaceState({},'',window.location.pathname); updateSidebarSources(); }
  if (p.get('integration_error')) { showToast('Integration error'); window.history.replaceState({},'',window.location.pathname); }
}

// ═══ SIDEBAR ═══
async function updateSidebarSources() {
  try { integrationConnections = await apiFetch('/v1/integrations/connections'); } catch(e) { integrationConnections = []; }
  renderSidebarSources();
}
function renderSidebarSources() {
  var el = document.getElementById('sourcesList'); if (!el) return;
  var on = integrationConnections.filter(function(c) { return c.status==='connected'; });
  if (!on.length) { el.innerHTML = '<div style="color:var(--text-dim);font-size:0.75rem;padding:0.3rem 0.6rem">No integrations connected</div>'; return; }
  el.innerHTML = on.map(function(c) {
    var b = PROVIDER_BRANDS[c.provider] || { color:'#888', label: c.provider };
    return '<div class="sb-item" onclick="showIntegrations()"><span class="sb-icon" style="color:'+b.color+'">●</span><span class="sb-text">'+esc(b.label)+'</span><span class="sb-live-dot"></span></div>';
  }).join('');
}
