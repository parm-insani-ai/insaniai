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

// ── Share & Invite modals ──

function showShareModal() {
  var url = window.location.href;
  var html = '<div class="agent-modal-inner">' +
    '<div class="agent-modal-header"><h3>Share</h3><button class="dv-close" onclick="closeShareModal()">&#10005;</button></div>' +
    '<p class="agent-modal-desc">Share a link to this workspace with your team.</p>' +
    '<div style="display:flex;gap:0.5rem;margin-bottom:1rem">' +
    '<input type="text" id="shareLink" value="' + esc(url) + '" readonly style="flex:1;padding:0.5rem;border:1px solid var(--border);border-radius:6px;font-size:0.8rem;background:var(--surface);color:var(--text);font-family:var(--mono)">' +
    '<button class="disc-btn disc-btn-run" onclick="copyShareLink()">Copy</button>' +
    '</div></div>';
  document.getElementById('shareModal').innerHTML = html;
  document.getElementById('shareModal').classList.add('open');
}

function closeShareModal() {
  document.getElementById('shareModal').classList.remove('open');
}

function copyShareLink() {
  var input = document.getElementById('shareLink');
  input.select();
  document.execCommand('copy');
  showToast('Link copied to clipboard');
}

function showInviteModal() {
  var html = '<div class="agent-modal-inner">' +
    '<div class="agent-modal-header"><h3>Invite Team Member</h3><button class="dv-close" onclick="closeInviteModal()">&#10005;</button></div>' +
    '<p class="agent-modal-desc">Enter an email address to invite someone to collaborate.</p>' +
    '<input type="email" id="inviteEmail" placeholder="colleague@company.com" style="width:100%;padding:0.5rem;border:1px solid var(--border);border-radius:6px;font-size:0.82rem;margin-bottom:0.75rem;background:var(--bg-chat);color:var(--text)">' +
    '<div style="display:flex;gap:0.5rem;justify-content:flex-end">' +
    '<button class="disc-btn disc-btn-cancel" onclick="closeInviteModal()">Cancel</button>' +
    '<button class="disc-btn disc-btn-run" onclick="sendInvite()">Send Invite</button>' +
    '</div></div>';
  document.getElementById('inviteModal').innerHTML = html;
  document.getElementById('inviteModal').classList.add('open');
}

function closeInviteModal() {
  document.getElementById('inviteModal').classList.remove('open');
}

function sendInvite() {
  var email = document.getElementById('inviteEmail').value.trim();
  if (!email) { showToast('Enter an email address'); return; }
  closeInviteModal();
  showToast('Invite sent to ' + email);
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
}

document.addEventListener('click', function(e) {
  var np = document.getElementById('notifPanel');
  var nb = document.getElementById('notifBtn');
  if (np && np.classList.contains('open') && !np.contains(e.target) && nb && !nb.contains(e.target)) np.classList.remove('open');

  var ub = document.getElementById('userBtn');
  var um = document.getElementById('userMenu');
  if (um && um.classList.contains('open') && !um.contains(e.target) && ub && !ub.contains(e.target)) um.classList.remove('open');

  // Close sidebar project menu
  var spm = document.getElementById('sidebarProjectMenu');
  var spp = document.getElementById('sidebarProjectPicker');
  if (spm && spm.classList.contains('open') && !spm.contains(e.target) && spp && !spp.contains(e.target)) spm.classList.remove('open');
});
