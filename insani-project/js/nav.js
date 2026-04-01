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

    // Auto-select first project
    if (!activeProjectId && projects.length) {
      activeProjectId = projects[0].id;
      activeProjectName = projects[0].name;
    }

    // Update sidebar project picker
    renderSidebarProjectMenu();
    updateSidebarProjectName();
  } catch (e) {
    console.warn('Failed to load projects:', e.message);
  }
}

// ── Sidebar project picker ──

function toggleSidebarProjectMenu() {
  document.getElementById('sidebarProjectMenu').classList.toggle('open');
}

function renderSidebarProjectMenu() {
  var container = document.getElementById('sidebarProjectList');
  if (!container) return;

  if (!allProjects.length) {
    container.innerHTML = '<div class="sidebar-project-item" style="color:var(--text-dim);pointer-events:none">No projects</div>';
    return;
  }

  container.innerHTML = allProjects.map(function(p) {
    return '<div class="sidebar-project-item' + (p.id === activeProjectId ? ' active' : '') + '" onclick="switchProject(' + p.id + ')">' + esc(p.name) + '</div>';
  }).join('');
}

function updateSidebarProjectName() {
  var el = document.getElementById('sidebarProjectName');
  if (el) el.textContent = activeProjectName || 'Select project';
}

async function switchProject(id) {
  document.getElementById('sidebarProjectMenu').classList.remove('open');
  await selectProject(id);
}

async function createNewProject() {
  document.getElementById('sidebarProjectMenu').classList.remove('open');
  var name = prompt('Project name:');
  if (!name || !name.trim()) return;

  try {
    await apiCreateProject(name.trim(), '', '', {});
    await loadProjects();
    // Select the newly created project (last one)
    if (allProjects.length) {
      await selectProject(allProjects[0].id);
    }
    showToast('Project created: ' + name.trim());
  } catch (e) {
    showToast('Failed to create project: ' + e.message);
  }
}

async function selectProject(id) {
  activeProjectId = id;
  activeChatId = null;

  // Update sidebar project picker
  const project = allProjects.find(p => p.id === id);
  if (project) {
    activeProjectName = project.name;
  }
  updateSidebarProjectName();
  renderSidebarProjectMenu();
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
