/* ═══════════════════════════════════════════════
   PANELS — Notifications, source detail, user menu, toasts.
   Integrated with backend auth (sign out = real logout).
   ═══════════════════════════════════════════════ */

// ── Notifications ──

let notifications = [];

function loadNotificationsFromProject() {
  // Notifications will come from a backend endpoint in the future.
  // For now, show empty state.
  notifications = [];
  renderNotifications();
  updateNotifBadge();
}

function toggleNotifications() {
  closeAllPanels('notifPanel');
  document.getElementById('notifPanel').classList.toggle('open');
}

function markNotifRead(id) {
  var n = notifications.find(function(x) { return x.id === id; });
  if (n) n.read = true;
  renderNotifications();
  updateNotifBadge();
}

function markAllRead() {
  notifications.forEach(function(n) { n.read = true; });
  renderNotifications();
  updateNotifBadge();
}

function renderNotifications() {
  var list = document.getElementById('notifList');
  if (!notifications.length) {
    list.innerHTML = '<div style="padding:1.5rem;text-align:center;color:var(--text-dim);font-size:0.82rem">No notifications</div>';
    return;
  }
  list.innerHTML = notifications.map(function(n) {
    return '<div class="notif-item ' + (n.read ? 'read' : '') + '" onclick="markNotifRead(' + n.id + ')">' +
      '<div class="notif-dot ' + (n.read ? '' : 'notif-dot-' + n.severity) + '"></div>' +
      '<div class="notif-body">' +
        '<div class="notif-title">' + esc(n.title) + '</div>' +
        '<div class="notif-desc">' + esc(n.body) + '</div>' +
        '<div class="notif-time">' + n.time + '</div>' +
      '</div>' +
    '</div>';
  }).join('');
}

function updateNotifBadge() {
  var count = notifications.filter(function(n) { return !n.read; }).length;
  var badge = document.getElementById('notifBadge');
  if (count > 0) { badge.textContent = count; badge.style.display = 'flex'; }
  else { badge.style.display = 'none'; }
}

// ── Source detail modal ──

function showSourceDetail(key) {
  closeSourceModal();
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
  var t = document.createElement('div');
  t.className = 'toast'; t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(function() { t.classList.add('show'); });
  setTimeout(function() { t.classList.remove('show'); setTimeout(function() { t.remove(); }, 300); }, 2500);
}

// ── Close all panels ──

function closeAllPanels(except) {
  if (except !== 'notifPanel') document.getElementById('notifPanel').classList.remove('open');
  if (except !== 'sourceModal') document.getElementById('sourceModal').classList.remove('open');
  if (except !== 'userMenu') document.getElementById('userMenu').classList.remove('open');
  if (except !== 'projectMenu') document.getElementById('projectMenu').classList.remove('open');
}

document.addEventListener('click', function(e) {
  var np = document.getElementById('notifPanel');
  var nb = document.getElementById('notifBtn');
  if (np.classList.contains('open') && !np.contains(e.target) && !nb.contains(e.target)) np.classList.remove('open');

  var pk = document.getElementById('projectPicker');
  if (pk && !pk.contains(e.target)) document.getElementById('projectMenu').classList.remove('open');

  var ub = document.getElementById('userBtn');
  var um = document.getElementById('userMenu');
  if (um && um.classList.contains('open') && !um.contains(e.target) && !ub.contains(e.target)) um.classList.remove('open');
});
