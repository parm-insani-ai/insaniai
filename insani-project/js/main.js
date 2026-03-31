/* ═══════════════════════════════════════════════
   MAIN — App initialization (fully integrated).
   
   Boot flow:
   1. Show loader
   2. Check for existing auth → if none, show login
   3. On login/signup → init app → load projects → load chats
   4. Show the chat interface
   ═══════════════════════════════════════════════ */

// ── Global state ──
let activeProjectId = null;
let activeProjectName = '';
let activeProjectData = {};
let allProjects = [];
let busy = false;
let files = [];
let activeChatId = null;
let currentUser = null;
let currentUserInitials = 'U';

// ── Boot ──
(async function boot() {
  // Show loading animation
  const loaderSub = document.getElementById('loaderSub');
  const steps = ["Connecting to backend", "Checking authentication", "Ready."];
  let si = 0;
  const iv = setInterval(() => {
    si++;
    if (si < steps.length) loaderSub.textContent = steps[si];
    if (si >= steps.length) clearInterval(iv);
  }, 500);

  // Give the loader a moment to render
  await new Promise(r => setTimeout(r, 800));

  // Check if we can reach the backend
  try {
    const health = await fetch(API_BASE + '/health');
    if (!health.ok) throw new Error('Backend unreachable');
  } catch {
    clearInterval(iv);
    document.getElementById('loader').classList.add('off');
    showAuthScreen();
    showToast('Cannot reach backend — check that the server is running on ' + API_BASE);
    return;
  }

  clearInterval(iv);
  document.getElementById('loader').classList.add('off');

  // No token → show login
  if (!getAccessToken()) {
    showAuthScreen();
    return;
  }

  // Token exists → try to init
  try {
    await initApp();
  } catch {
    showAuthScreen();
  }
})();

// ── Auth screen (login + signup) ──

function showAuthScreen() {
  document.getElementById('authOverlay').classList.add('open');
  document.getElementById('appContainer').classList.add('vh');
}

function hideAuthScreen() {
  document.getElementById('authOverlay').classList.remove('open');
  document.getElementById('appContainer').classList.remove('vh');
}

function showSignupForm() {
  document.getElementById('loginForm').classList.add('vh');
  document.getElementById('signupForm').classList.remove('vh');
}

function showLoginForm() {
  document.getElementById('signupForm').classList.add('vh');
  document.getElementById('loginForm').classList.remove('vh');
}

async function handleLogin(e) {
  e.preventDefault();
  const email = document.getElementById('loginEmail').value.trim();
  const pass = document.getElementById('loginPass').value;
  const errEl = document.getElementById('loginError');
  errEl.textContent = '';

  if (!email || !pass) { errEl.textContent = 'Enter email and password'; return; }

  try {
    const btn = document.getElementById('loginBtn');
    btn.disabled = true; btn.textContent = 'Logging in…';
    await apiLogin(email, pass);
    await initApp();
    btn.disabled = false; btn.textContent = 'Log in';
  } catch (err) {
    const btn = document.getElementById('loginBtn');
    btn.disabled = false; btn.textContent = 'Log in';
    errEl.textContent = err.message;
  }
}

async function handleSignup(e) {
  e.preventDefault();
  const name = document.getElementById('signupName').value.trim();
  const org = document.getElementById('signupOrg').value.trim();
  const email = document.getElementById('signupEmail').value.trim();
  const pass = document.getElementById('signupPass').value;
  const errEl = document.getElementById('signupError');
  errEl.textContent = '';

  if (!name || !org || !email || !pass) { errEl.textContent = 'All fields are required'; return; }

  try {
    const btn = document.getElementById('signupBtn');
    btn.disabled = true; btn.textContent = 'Creating account…';
    await apiSignup(email, pass, name, org);

    await initApp();
    btn.disabled = false; btn.textContent = 'Create account';
  } catch (err) {
    const btn = document.getElementById('signupBtn');
    btn.disabled = false; btn.textContent = 'Create account';
    errEl.textContent = err.message;
  }
}

// ── Init app (after successful auth) ──

async function initApp() {
  // Load user
  currentUser = await apiGetMe();
  currentUserInitials = currentUser.name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();

  // Update sidebar user display
  const nameEl = document.querySelector('.user-name');
  const roleEl = document.querySelector('.user-role');
  const avEl = document.querySelector('.user-av');
  if (nameEl) nameEl.textContent = currentUser.name;
  if (roleEl) roleEl.textContent = currentUser.role;
  if (avEl) avEl.textContent = currentUserInitials;

  // Load projects from backend
  await loadProjects();

  // Load first project's data for dashboard + notifications
  if (activeProjectId) {
    try {
      const full = await apiGetProject(activeProjectId);
      activeProjectData = full.data_json || {};
      renderDashboard(full);
      loadNotificationsFromProject();
    } catch { /* silent — project data is optional for chat */ }
  }

  // Load recent chats
  await loadRecentChats();

  // Load project documents, blueprints, and discrepancy reports
  await loadProjectDocuments();
  await loadDrawingDocuments();
  await loadDiscrepancyReports();

  // Check for OAuth callback and load integration status
  checkIntegrationCallback();
  updateSidebarSources();

  // Show the app
  hideAuthScreen();
  showChat();
}

// ── Utilities ──

function onKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
}

function resize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

function esc(t) {
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}
