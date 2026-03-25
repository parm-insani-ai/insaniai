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
  var ia = document.querySelector('.input-area');
  if (cv) { cv.classList.remove('vh'); cv.style.display = ''; }
  if (dv) dv.classList.add('vh');
  if (iv) { iv.classList.add('vh'); iv.style.display = 'none'; }
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

/** Load projects from backend and render the picker dropdown. */
async function loadProjects() {
  try {
    const projects = await apiListProjects();
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

  // Load documents for this project
  await loadProjectDocuments();
}

// ── Dashboard (dynamic from project data) ──

function renderDashboard(project) {
  const d = project.data_json || {};
  const openRFIs = (d.rfis || []).filter(r => r.status === 'Open').length;
  const dashHTML = `
    <div class="dash-h">
      <h1>Project Overview</h1>
      <p>${esc(project.name)} — real-time intelligence across all sources</p>
    </div>
    <div class="dash-grid">
      <div class="dash-card" onclick="dashCardClick('rfis')"><div class="dash-card-label">Open RFIs</div><div class="dash-card-val" style="color:var(--orange)">${openRFIs}</div></div>
      <div class="dash-card" onclick="dashCardClick('budget')"><div class="dash-card-label">Budget Variance</div><div class="dash-card-val" style="color:var(--red)">${d.variance || '—'}</div></div>
      <div class="dash-card" onclick="dashCardClick('schedule')"><div class="dash-card-label">Schedule</div><div class="dash-card-val" style="color:var(--orange)">${d.schedule || '—'}</div></div>
      <div class="dash-card" onclick="dashCardClick('completion')"><div class="dash-card-label">Completion</div><div class="dash-card-val" style="color:var(--green)">${d.pct || 0}%</div></div>
    </div>
    <div class="dash-alerts-title">AI-Detected Alerts</div>
    <div class="dash-alerts">
      ${(d.rfis || []).filter(r => r.status === 'Open').map(r => `
        <div class="da" onclick="ask('Tell me about ${esc(r.id)} — ${esc(r.title)}')">
          <div class="da-dot ${r.pri === 'Critical' ? 'da-dot-red' : 'da-dot-orange'}"></div>
          <div>
            <div class="da-title">${esc(r.id)} — ${esc(r.title)}</div>
            <div class="da-meta">${esc(r.impact || '')}</div>
            <div class="da-src">${esc(r.src || 'Procore')} • ${r.days}d open</div>
          </div>
        </div>
      `).join('')}
      ${(d.rfis || []).filter(r => r.status === 'Open').length === 0 ? '<div style="color:var(--text-dim);font-size:0.85rem;padding:1rem">No active alerts</div>' : ''}
    </div>
  `;
  document.getElementById('dashView').innerHTML = dashHTML;
}

// ── Dashboard card clicks ──
function dashCardClick(topic) {
  const questions = {
    rfis:       'Give me a full breakdown of all open RFIs, their priority, assignees, and days open.',
    budget:     'Break down the full budget status by trade, including all change orders.',
    schedule:   'Walk me through every milestone and its current risk level.',
    completion: 'What work has been completed and what are the major remaining scopes?'
  };
  ask(questions[topic] || 'Summarize the project status.');
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
