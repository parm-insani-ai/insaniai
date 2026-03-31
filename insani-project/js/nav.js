/* ═══════════════════════════════════════════════
   NAV — Navigation and view management (integrated).
   
   Project picker loads from backend API.
   Dashboard dynamically rendered from project data.
   ═══════════════════════════════════════════════ */

// ── Sidebar ──
function toggleSidebar() {
  const sb = document.getElementById('sidebar');
  sb.classList.toggle('collapsed');
  document.getElementById('menuBtn').classList.toggle('visible', sb.classList.contains('collapsed'));
}

// ── Views ──
function showChat() {
  var cv = document.getElementById('chatView');
  var dv = document.getElementById('dashView');
  var iv = document.getElementById('integrationsView');
  var dcv = document.getElementById('discrepancyView');
  var ia = document.querySelector('.input-area');
  if (cv) { cv.classList.remove('vh'); cv.style.display = ''; }
  if (dv) dv.classList.add('vh');
  if (iv) { iv.classList.add('vh'); iv.style.display = 'none'; }
  if (dcv) { dcv.classList.add('vh'); dcv.style.display = 'none'; }
  var agv = document.getElementById('agentView');
  if (agv) { agv.classList.add('vh'); agv.style.display = 'none'; }
  if (ia) { ia.classList.remove('vh'); ia.style.display = ''; }
}

function showDash() {
  var cv = document.getElementById('chatView');
  var dv = document.getElementById('dashView');
  var iv = document.getElementById('integrationsView');
  var ia = document.querySelector('.input-area');
  if (cv) cv.classList.add('vh');
  if (dv) dv.classList.remove('vh');
  if (iv) { iv.classList.add('vh'); iv.style.display = 'none'; }
  if (ia) ia.style.display = 'none';
}

// ── Project picker (dynamic from API) ──

/** Load projects from backend and render the picker dropdown. Auto-creates a default project if none exist. */
async function loadProjects() {
  try {
    let projects = await apiListProjects();

    // Auto-create a default project if user has none
    if (!projects.length) {
      try {
        await apiCreateProject('My Project', '', '', {});
        projects = await apiListProjects();
      } catch (e) {
        console.warn('Failed to create default project:', e.message);
      }
    }

    allProjects = projects;

    const menu = document.getElementById('projectMenu');
    if (!projects.length) {
      menu.innerHTML = '<div class="pm-item" style="color:var(--text-dim);pointer-events:none">No projects yet</div>';
      return;
    }

    menu.innerHTML = projects.map((p, i) => `
      <div class="pm-item ${p.id === activeProjectId ? 'active' : ''}" data-id="${p.id}" onclick="pickProject(${p.id})">
        <span class="pm-name">${esc(p.name)}</span>
      </div>
    `).join('');

    // Select first project if none selected
    if (!activeProjectId && projects.length) {
      activeProjectId = projects[0].id;
      activeProjectName = projects[0].name;
      document.getElementById('projName').textContent = activeProjectName;
    }
  } catch (e) {
    console.warn('Failed to load projects:', e.message);
  }
}

function toggleProjectMenu() {
  document.getElementById('projectMenu').classList.toggle('open');
}

async function pickProject(id) {
  document.getElementById('projectMenu').classList.remove('open');
  await selectProject(id);
}

async function selectProject(id) {
  activeProjectId = id;
  activeChatId = null;

  // Update picker UI
  const project = allProjects.find(p => p.id === id);
  if (project) {
    activeProjectName = project.name;
    document.getElementById('projName').textContent = project.name;
  }
  document.querySelectorAll('.pm-item').forEach(el =>
    el.classList.toggle('active', parseInt(el.dataset.id) === id)
  );

  // Load project data for dashboard
  try {
    const full = await apiGetProject(id);
    activeProjectData = full.data_json || {};
    renderDashboard(full);
  } catch (e) {
    console.warn('Failed to load project:', e.message);
  }

  // Reset chat
  newChat();

  // Load documents, blueprints, and discrepancy reports for this project
  await loadProjectDocuments();
  await loadDrawingDocuments();
  await loadDiscrepancyReports();
  await loadAgentHistory();
}

// ── Dashboard (dynamic from project data) ──

function renderDashboard(project) {
  const d = project.data_json || {};
  const dashHTML = `
    <div class="dash-h">
      <h1>Project Overview</h1>
      <p>${esc(project.name)} — real-time intelligence across all sources</p>
    </div>
    <div style="padding:2rem;text-align:center;color:var(--text-dim);font-size:0.9rem">
      <p>No project data yet. Connect integrations or upload documents to see insights here.</p>
    </div>
  `;
  document.getElementById('dashView').innerHTML = dashHTML;
}

// ── New chat ──
function newChat() {
  files = [];
  chips();
  activeChatId = null;
  document.getElementById('chatInner').innerHTML = welcomeHTML();
  showChat();
  loadRecentChats();
}
