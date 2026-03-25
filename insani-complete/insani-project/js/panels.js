/* ═══════════════════════════════════════════════
   PANELS — Notifications, source detail, user menu, toasts.
   Integrated with backend auth (sign out = real logout).
   ═══════════════════════════════════════════════ */

// ── Notifications ──
// TODO: In production, load from a backend /notifications endpoint.
// For now, derived from project data after it's loaded.

let notifications = [];

function loadNotificationsFromProject() {
  const d = activeProjectData || {};
  notifications = (d.rfis || [])
    .filter(r => r.status === 'Open')
    .map((r, i) => ({
      id: i + 1,
      title: `${r.id} — ${r.title}`,
      body: r.impact || '',
      time: `${r.days}d open`,
      read: false,
      severity: r.pri === 'Critical' ? 'high' : 'medium'
    }));
  renderNotifications();
  updateNotifBadge();
}

function toggleNotifications() {
  closeAllPanels('notifPanel');
  document.getElementById('notifPanel').classList.toggle('open');
}

function markNotifRead(id) {
  const n = notifications.find(x => x.id === id);
  if (n) n.read = true;
  renderNotifications();
  updateNotifBadge();
}

function markAllRead() {
  notifications.forEach(n => n.read = true);
  renderNotifications();
  updateNotifBadge();
}

function renderNotifications() {
  const list = document.getElementById('notifList');
  if (!notifications.length) {
    list.innerHTML = '<div style="padding:1.5rem;text-align:center;color:var(--text-dim);font-size:0.82rem">No notifications</div>';
    return;
  }
  list.innerHTML = notifications.map(n => `
    <div class="notif-item ${n.read ? 'read' : ''}" onclick="markNotifRead(${n.id});ask('${esc(n.title)}')">
      <div class="notif-dot ${n.read ? '' : 'notif-dot-' + n.severity}"></div>
      <div class="notif-body">
        <div class="notif-title">${esc(n.title)}</div>
        <div class="notif-desc">${esc(n.body)}</div>
        <div class="notif-time">${n.time}</div>
      </div>
    </div>
  `).join('');
}

function updateNotifBadge() {
  const count = notifications.filter(n => !n.read).length;
  const badge = document.getElementById('notifBadge');
  if (count > 0) { badge.textContent = count; badge.style.display = 'flex'; }
  else { badge.style.display = 'none'; }
}

// ── Source detail modal ──

function showSourceDetail(key) {
  const s = SOURCES[key];
  if (!s) return;
  closeAllPanels('sourceModal');
  document.getElementById('srcName').textContent = s.name;
  document.getElementById('srcStatus').textContent = s.status;
  document.getElementById('srcStatus').style.color = s.status === 'Connected' ? 'var(--green)' : 'var(--red)';
  document.getElementById('srcSync').textContent = s.lastSync;
  document.getElementById('srcItems').textContent = s.items;
  document.getElementById('srcDocs').textContent = s.docs;
  document.getElementById('srcDot').style.background = s.color;
  document.getElementById('sourceModal').classList.add('open');
}

function closeSourceModal() {
  document.getElementById('sourceModal').classList.remove('open');
}

// ── User menu ──

function toggleUserMenu() {
  closeAllPanels('userMenu');
  document.getElementById('userMenu').classList.toggle('open');
}

async function handleSignOut() {
  toggleUserMenu();
  await apiLogout();
  currentUser = null;
  showAuthScreen();
  showToast('Signed out');
}

// ── Toast ──

function showToast(msg) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, 2500);
}

// ── Close all panels ──

function closeAllPanels(except) {
  if (except !== 'notifPanel') document.getElementById('notifPanel').classList.remove('open');
  if (except !== 'sourceModal') document.getElementById('sourceModal').classList.remove('open');
  if (except !== 'userMenu') document.getElementById('userMenu').classList.remove('open');
  if (except !== 'projectMenu') document.getElementById('projectMenu').classList.remove('open');
}

document.addEventListener('click', e => {
  const np = document.getElementById('notifPanel');
  const nb = document.getElementById('notifBtn');
  if (np.classList.contains('open') && !np.contains(e.target) && !nb.contains(e.target)) np.classList.remove('open');

  const pk = document.getElementById('projectPicker');
  if (pk && !pk.contains(e.target)) document.getElementById('projectMenu').classList.remove('open');

  const ub = document.getElementById('userBtn');
  const um = document.getElementById('userMenu');
  if (um && um.classList.contains('open') && !um.contains(e.target) && !ub.contains(e.target)) um.classList.remove('open');
});
