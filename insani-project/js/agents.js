/* ═══════════════════════════════════════════════
   AGENTS — Material Price Tracker & Bid Estimator
   Multi-step AI agents for construction intelligence.
   ═══════════════════════════════════════════════ */

var agentHistory = [];

// ═══ API ═══

async function apiGetAgentHistory(projectId) {
  return apiFetch('/v1/agents/history?project_id=' + projectId);
}

async function apiGetAgentRun(runId) {
  return apiFetch('/v1/agents/history/' + runId);
}

async function apiDeleteAgentRun(runId) {
  return apiFetch('/v1/agents/history/' + runId, { method: 'DELETE' });
}

async function apiRunMaterialAnalysis(docIds, projectId) {
  return apiFetch('/v1/agents/materials', {
    method: 'POST',
    body: JSON.stringify({ doc_ids: docIds, project_id: projectId })
  });
}

async function apiRunBidAnalysis(docIds, projectId) {
  return apiFetch('/v1/agents/bid', {
    method: 'POST',
    body: JSON.stringify({ doc_ids: docIds, project_id: projectId })
  });
}

// ═══ MATERIAL PRICE TRACKER ═══

function showMaterialModal() {
  if (!activeProjectId) { showToast('Create a project first'); return; }

  var docsHtml = projectDocuments.length ?
    projectDocuments.map(function(d) {
      return '<label class="agent-doc-check"><input type="checkbox" value="' + d.id + '"><span class="agent-doc-name">' + esc(d.filename) + '</span><span class="agent-doc-meta">' + d.page_count + ' pages</span></label>';
    }).join('') :
    '<div style="color:var(--text-dim);font-size:0.78rem;padding:0.5rem">No documents yet</div>';

  var html = '<div class="agent-modal-inner">' +
    '<div class="agent-modal-header">' +
    '<h3>Material Price Analysis</h3>' +
    '<button class="dv-close" onclick="closeAgentModal()">&#10005;</button>' +
    '</div>' +
    '<p class="agent-modal-desc">Select documents or upload new ones. The agent will extract materials, find pricing from Halifax suppliers, and recommend cost savings.</p>' +
    '<div class="agent-upload-inline"><label class="disc-btn disc-btn-cancel" style="cursor:pointer;display:inline-flex;align-items:center;gap:0.3rem">+ Upload file<input type="file" accept=".pdf" onchange="agentInlineUpload(this,\'materialDocCheckboxes\')" style="display:none"></label></div>' +
    '<div class="agent-doc-list" id="materialDocCheckboxes">' + docsHtml + '</div>' +
    '<div class="agent-select-actions">' +
    '<button class="agent-select-btn" onclick="toggleAllChecks(\'materialDocCheckboxes\', true)">Select all</button>' +
    '<button class="agent-select-btn" onclick="toggleAllChecks(\'materialDocCheckboxes\', false)">Deselect all</button>' +
    '</div>' +
    '<div class="agent-modal-footer">' +
    '<button class="disc-btn disc-btn-cancel" onclick="closeAgentModal()">Cancel</button>' +
    '<button class="disc-btn disc-btn-run" id="materialRunBtn" onclick="runMaterialAnalysis()">Analyze Materials</button>' +
    '</div></div>';

  document.getElementById('agentModal').innerHTML = html;
  document.getElementById('agentModal').classList.add('open');
}

function closeAgentModal() {
  document.getElementById('agentModal').classList.remove('open');
}

async function agentInlineUpload(inputEl, checkboxContainerId) {
  var file = inputEl.files[0];
  if (!file) return;
  inputEl.value = '';

  if (!activeProjectId) { showToast('No project selected'); return; }
  if (file.size > 50 * 1024 * 1024) { showToast('File too large — max 50MB'); return; }

  showToast('Uploading ' + file.name + '...');

  var formData = new FormData();
  formData.append('file', file);
  formData.append('project_id', activeProjectId);

  try {
    var res = await fetch(API_BASE + '/v1/documents/upload', {
      method: 'POST',
      headers: accessToken ? { 'Authorization': 'Bearer ' + accessToken } : {},
      body: formData
    });

    if (!res.ok) throw new Error('Upload failed');
    var doc = await res.json();

    // Reload project documents
    await loadProjectDocuments();

    // Add the new doc to the checkbox list as checked
    var container = document.getElementById(checkboxContainerId);
    if (container) {
      // Remove "No documents" placeholder
      var placeholder = container.querySelector('div[style]');
      if (placeholder) placeholder.remove();

      var label = document.createElement('label');
      label.className = 'agent-doc-check';
      label.innerHTML = '<input type="checkbox" value="' + doc.id + '" checked><span class="agent-doc-name">' + esc(doc.filename) + '</span><span class="agent-doc-meta">' + doc.page_count + ' pages</span>';
      container.appendChild(label);
    }

    showToast(file.name + ' uploaded');
  } catch (e) {
    showToast('Upload failed: ' + e.message);
  }
}

function toggleAllChecks(containerId, checked) {
  document.querySelectorAll('#' + containerId + ' input[type=checkbox]').forEach(function(cb) {
    cb.checked = checked;
  });
}

async function runMaterialAnalysis() {
  var docIds = [];
  document.querySelectorAll('#materialDocCheckboxes input:checked').forEach(function(cb) { docIds.push(parseInt(cb.value)); });
  if (!docIds.length) { showToast('Select at least one document'); return; }

  // Disable button and show loading
  var btn = document.getElementById('materialRunBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Analyzing...'; }

  closeAgentModal();
  showAgentView();
  document.getElementById('agentView').innerHTML = '<div class="agent-loading"><div class="agent-loading-spin"></div><div>Analyzing materials...<br><span style="font-size:0.75rem;color:var(--text-dim)">Step 1: Extracting materials from documents<br>Step 2: Estimating pricing from Halifax suppliers<br>Step 3: Generating cost recommendations</span></div></div>';

  try {
    var result = await apiRunMaterialAnalysis(docIds, activeProjectId);
    renderMaterialResults(result);
    await loadAgentHistory();
  } catch (e) {
    document.getElementById('agentView').innerHTML = '<div class="disc-report"><div class="disc-report-header"><button class="disc-back" onclick="showChat()">← Back to chat</button><h2>Material Price Analysis</h2></div><div class="disc-summary" style="color:var(--red)">Analysis failed: ' + esc(e.message) + '<br><br>Make sure your .env file has the ANTHROPIC_API_KEY set.</div></div>';
  }
}

function renderMaterialResults(result) {
  var view = document.getElementById('agentView');

  var html = '<div class="disc-report">' +
    '<div class="disc-report-header">' +
    '<button class="disc-back" onclick="showChat()">← Back to chat</button>' +
    '<h2>Material Price Analysis</h2>' +
    '<div style="display:flex;gap:0.4rem">' +
    '<div class="disc-status disc-status-' + result.status + '">' + result.status + '</div>' +
    '<button class="disc-btn disc-btn-cancel" onclick="exportToPDF()" style="font-size:0.65rem">Export PDF</button>' +
    '</div></div>';

  if (result.error) {
    html += '<div class="disc-summary" style="color:var(--red)">' + esc(result.error) + '</div>';
  }

  // Summary stats
  var s = result.summary || {};
  if (s.total_mid) {
    html += '<div class="disc-stats">' +
      '<div class="disc-stat"><span class="disc-stat-num" style="color:var(--green)">$' + formatNum(s.total_low) + '</span><span>Low Estimate</span></div>' +
      '<div class="disc-stat"><span class="disc-stat-num" style="color:var(--blue)">$' + formatNum(s.total_mid) + '</span><span>Mid Estimate</span></div>' +
      '<div class="disc-stat"><span class="disc-stat-num" style="color:var(--orange)">$' + formatNum(s.total_high) + '</span><span>High Estimate</span></div>' +
      '<div class="disc-stat"><span class="disc-stat-num">' + (result.materials_found || 0) + '</span><span>Materials</span></div>' +
      '</div>';
    if (s.tax_note) html += '<p style="font-size:0.72rem;color:var(--text-dim);margin:-0.5rem 0 1rem">' + esc(s.tax_note) + ' | All prices in CAD</p>';
  }

  // Materials table
  var mats = result.materials || [];
  if (mats.length) {
    html += '<h3 style="font-size:0.88rem;margin:1rem 0 0.5rem">Material Pricing (' + mats.length + ' items)</h3>' +
      '<div style="overflow-x:auto" id="materialTable"><table style="width:100%;border-collapse:collapse;font-size:0.78rem">' +
      '<thead><tr style="background:var(--surface);text-align:left">' +
      '<th style="padding:0.5rem">Material</th>' +
      '<th style="padding:0.5rem">Qty</th>' +
      '<th style="padding:0.5rem">Low</th>' +
      '<th style="padding:0.5rem">Mid</th>' +
      '<th style="padding:0.5rem">High</th>' +
      '<th style="padding:0.5rem">Best Supplier</th>' +
      '</tr></thead><tbody>';
    mats.forEach(function(m) {
      html += '<tr style="border-bottom:1px solid var(--border)">' +
        '<td style="padding:0.4rem 0.5rem"><strong>' + esc(m.name || '') + '</strong><br><span style="color:var(--text-dim);font-size:0.7rem">' + esc(m.specification || '') + '</span></td>' +
        '<td style="padding:0.4rem 0.5rem">' + esc(String(m.quantity || '-')) + '</td>' +
        '<td style="padding:0.4rem 0.5rem;color:var(--green)">$' + formatNum(m.total_low) + '</td>' +
        '<td style="padding:0.4rem 0.5rem;color:var(--blue)">$' + formatNum(m.total_mid) + '</td>' +
        '<td style="padding:0.4rem 0.5rem;color:var(--orange)">$' + formatNum(m.total_high) + '</td>' +
        '<td style="padding:0.4rem 0.5rem;font-size:0.72rem">' + esc(m.best_supplier || '-') + '</td>' +
        '</tr>';
    });
    html += '</tbody></table></div>';
  }

  // Savings tips
  if (s.savings_tips && s.savings_tips.length) {
    html += '<h3 style="font-size:0.88rem;margin:1.2rem 0 0.5rem">Cost Saving Tips</h3><ul style="font-size:0.8rem;color:var(--text-secondary)">';
    s.savings_tips.forEach(function(tip) { html += '<li style="margin:0.3rem 0">' + esc(tip) + '</li>'; });
    html += '</ul>';
  }

  // Web price research
  var webResults = result.web_results || [];
  if (webResults.length) {
    html += '<h3 style="font-size:0.88rem;margin:1.2rem 0 0.5rem">Live Web Price Research (' + webResults.length + ' materials searched)</h3>';
    webResults.forEach(function(wr) {
      html += '<div class="disc-item" style="margin-bottom:0.5rem">' +
        '<div class="disc-item-header"><span class="disc-item-title">' + esc(wr.material) + '</span></div>' +
        '<div class="disc-item-body" style="font-size:0.75rem">';
      (wr.sources || []).forEach(function(src) {
        var prices = (src.prices_found || []).join(', ') || 'No prices found on page';
        html += '<div style="margin:0.3rem 0"><a href="' + esc(src.source) + '" target="_blank" style="color:var(--blue);text-decoration:none">' + esc(src.title || src.source).substring(0, 80) + '</a>' +
          '<br><span style="color:var(--green)">' + esc(prices) + '</span></div>';
      });
      html += '</div></div>';
    });
  }

  // AI Recommendations
  if (result.recommendations) {
    html += '<h3 style="font-size:0.88rem;margin:1.2rem 0 0.5rem">AI Recommendations</h3>' +
      '<div class="disc-summary">' + result.recommendations + '</div>';
  }

  html += '</div>';
  view.innerHTML = html;
}

// ═══ BID ESTIMATING ASSISTANT ═══

function showBidModal() {
  if (!activeProjectId) { showToast('Create a project first'); return; }

  var bidDocsHtml = projectDocuments.length ?
    projectDocuments.map(function(d) {
      return '<label class="agent-doc-check"><input type="checkbox" value="' + d.id + '"><span class="agent-doc-name">' + esc(d.filename) + '</span><span class="agent-doc-meta">' + d.page_count + ' pages</span></label>';
    }).join('') :
    '<div style="color:var(--text-dim);font-size:0.78rem;padding:0.5rem">No documents yet</div>';

  var html = '<div class="agent-modal-inner">' +
    '<div class="agent-modal-header">' +
    '<h3>Bid Estimating Assistant</h3>' +
    '<button class="dv-close" onclick="closeAgentModal()">&#10005;</button>' +
    '</div>' +
    '<p class="agent-modal-desc">Upload your ITB/RFP documents or select existing ones. The agent will extract scope, estimate costs with Halifax rates, check NS Building Code compliance, and generate a bid proposal.</p>' +
    '<div class="agent-upload-inline"><label class="disc-btn disc-btn-cancel" style="cursor:pointer;display:inline-flex;align-items:center;gap:0.3rem">+ Upload file<input type="file" accept=".pdf" onchange="agentInlineUpload(this,\'bidDocCheckboxes\')" style="display:none"></label></div>' +
    '<div class="agent-doc-list" id="bidDocCheckboxes">' + bidDocsHtml + '</div>' +
    '<div class="agent-select-actions">' +
    '<button class="agent-select-btn" onclick="toggleAllChecks(\'bidDocCheckboxes\', true)">Select all</button>' +
    '<button class="agent-select-btn" onclick="toggleAllChecks(\'bidDocCheckboxes\', false)">Deselect all</button>' +
    '</div>' +
    '<div class="agent-modal-footer">' +
    '<button class="disc-btn disc-btn-cancel" onclick="closeAgentModal()">Cancel</button>' +
    '<button class="disc-btn disc-btn-run" id="bidRunBtn" onclick="runBidAnalysis()">Generate Bid</button>' +
    '</div></div>';

  document.getElementById('agentModal').innerHTML = html;
  document.getElementById('agentModal').classList.add('open');
}

async function runBidAnalysis() {
  var docIds = [];
  document.querySelectorAll('#bidDocCheckboxes input:checked').forEach(function(cb) { docIds.push(parseInt(cb.value)); });
  if (!docIds.length) { showToast('Select at least one document'); return; }

  var btn = document.getElementById('bidRunBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Generating...'; }

  closeAgentModal();
  showAgentView();
  document.getElementById('agentView').innerHTML = '<div class="agent-loading"><div class="agent-loading-spin"></div><div>Generating bid proposal...<br><span style="font-size:0.75rem;color:var(--text-dim)">Step 1: Extracting scope from ITB/RFP<br>Step 2: Estimating costs with Halifax rates<br>Step 3: Checking NS Building Code compliance<br>Step 4: Writing bid proposal</span></div></div>';

  try {
    var result = await apiRunBidAnalysis(docIds, activeProjectId);
    renderBidResults(result);
    await loadAgentHistory();
  } catch (e) {
    document.getElementById('agentView').innerHTML = '<div class="disc-report"><div class="disc-report-header"><button class="disc-back" onclick="showChat()">← Back to chat</button><h2>Bid Proposal</h2></div><div class="disc-summary" style="color:var(--red)">Bid analysis failed: ' + esc(e.message) + '<br><br>Make sure your .env file has the ANTHROPIC_API_KEY set.</div></div>';
  }
}

function renderBidResults(result) {
  var view = document.getElementById('agentView');

  var html = '<div class="disc-report">' +
    '<div class="disc-report-header">' +
    '<button class="disc-back" onclick="showChat()">← Back to chat</button>' +
    '<h2>Bid Proposal</h2>' +
    '<div style="display:flex;gap:0.4rem">' +
    '<div class="disc-status disc-status-' + result.status + '">' + result.status + '</div>' +
    '<button class="disc-btn disc-btn-cancel" onclick="exportToPDF()" style="font-size:0.65rem">Export PDF</button>' +
    '</div></div>';

  if (result.error) {
    html += '<div class="disc-summary" style="color:var(--red)">' + esc(result.error) + '</div>';
  }

  // Scope summary
  var scope = result.scope || {};
  if (scope.project_name) {
    html += '<div class="disc-summary">' +
      '<strong>' + esc(scope.project_name) + '</strong><br>' +
      (scope.owner ? 'Owner: ' + esc(scope.owner) + '<br>' : '') +
      (scope.location ? 'Location: ' + esc(scope.location) + '<br>' : '') +
      (scope.project_type ? 'Type: ' + esc(scope.project_type) + '<br>' : '') +
      (scope.bid_deadline ? 'Deadline: <strong style="color:var(--red)">' + esc(scope.bid_deadline) + '</strong>' : '') +
      '</div>';
  }

  // Cost summary
  var est = result.estimate || {};
  var sub = est.subtotals || {};
  if (sub.total_before_tax) {
    html += '<div class="disc-stats">' +
      '<div class="disc-stat"><span class="disc-stat-num" style="color:var(--blue)">$' + formatNum(sub.direct_costs) + '</span><span>Direct Costs</span></div>' +
      '<div class="disc-stat"><span class="disc-stat-num" style="color:var(--orange)">$' + formatNum(sub.total_before_tax) + '</span><span>Total (pre-tax)</span></div>' +
      '<div class="disc-stat"><span class="disc-stat-num">$' + formatNum(sub.hst) + '</span><span>HST (15%)</span></div>' +
      '<div class="disc-stat"><span class="disc-stat-num" style="color:var(--green)">$' + formatNum(sub.total_with_tax) + '</span><span>Total Bid</span></div>' +
      '</div>';
  }

  // Compliance issues
  var comp = result.compliance || {};
  if (comp.issues && comp.issues.length) {
    html += '<h3 style="font-size:0.88rem;margin:1rem 0 0.5rem">NS Building Code Compliance (' + comp.issues.length + ' items)</h3>';
    comp.issues.forEach(function(issue) {
      var color = issue.severity === 'critical' ? 'var(--red)' : issue.severity === 'warning' ? 'var(--orange)' : 'var(--blue)';
      html += '<div class="disc-item" style="border-left-color:' + color + ';margin-bottom:0.5rem">' +
        '<div class="disc-item-header"><span class="disc-sev-badge" style="color:' + color + '">' + issue.severity + '</span>' +
        '<span class="disc-item-title">' + esc(issue.issue) + '</span></div>' +
        '<div class="disc-item-body"><p>' + esc(issue.recommendation) + '</p>' +
        (issue.cost_impact ? '<p style="color:var(--orange)">Cost impact: ' + esc(issue.cost_impact) + '</p>' : '') +
        '</div></div>';
    });
  }

  // Risks
  var risks = est.risks || [];
  if (risks.length) {
    html += '<h3 style="font-size:0.88rem;margin:1rem 0 0.5rem">Risk Assessment</h3>';
    risks.forEach(function(r) {
      html += '<div class="disc-recommendation" style="margin-bottom:0.5rem">' +
        '<strong>' + esc(r.risk) + '</strong><br>' +
        '<span style="color:var(--orange)">Impact: ' + esc(r.impact) + '</span><br>' +
        'Mitigation: ' + esc(r.mitigation) + '</div>';
    });
  }

  // Full proposal
  if (result.proposal_html) {
    html += '<h3 style="font-size:0.88rem;margin:1.5rem 0 0.5rem">Full Proposal Document</h3>' +
      '<div style="border:1px solid var(--border);border-radius:10px;padding:1.2rem;background:var(--bg-chat)" id="proposalContent">' +
      result.proposal_html + '</div>';
  }

  // Live job postings from web search
  var liveJobs = result.live_jobs || [];
  if (liveJobs.length) {
    html += '<h3 style="font-size:0.88rem;margin:1.5rem 0 0.5rem">Live Halifax Construction Tenders (' + liveJobs.length + ' found)</h3>';
    liveJobs.forEach(function(job) {
      html += '<div class="disc-item" style="margin-bottom:0.5rem">' +
        '<div class="disc-item-header"><a href="' + esc(job.source) + '" target="_blank" style="color:var(--blue);text-decoration:none;font-size:0.8rem">' + esc(job.title || 'View Tender').substring(0, 100) + '</a></div>' +
        '<div class="disc-item-body" style="font-size:0.72rem;color:var(--text-dim);max-height:60px;overflow:hidden">' + esc((job.content || '').substring(0, 200)) + '</div></div>';
    });
  }

  // Job sources directory
  if (result.job_sources) {
    html += '<h3 style="font-size:0.88rem;margin:1.5rem 0 0.5rem">Halifax Job Source Directory</h3>' +
      '<div style="font-size:0.78rem;color:var(--text-secondary)">';
    result.job_sources.forEach(function(src) {
      html += '<div style="margin:0.4rem 0"><strong>' + esc(src.name) + '</strong> — ' + esc(src.type) + '<br>' +
        '<span style="color:var(--text-dim)">' + esc(src.coverage) + '</span></div>';
    });
    html += '</div>';
  }

  html += '</div>';
  view.innerHTML = html;
}

// ═══ PDF EXPORT ═══

function exportToPDF() {
  // Get the current report content
  var reportEl = document.querySelector('.disc-report');
  if (!reportEl) { showToast('No report to export'); return; }

  // Open print dialog — the browser's Print to PDF handles formatting
  var printWindow = window.open('', '_blank');
  printWindow.document.write('<!DOCTYPE html><html><head><title>insani Report</title>' +
    '<style>' +
    'body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 800px; margin: 0 auto; padding: 2rem; color: #1a1a1a; font-size: 12px; }' +
    'h2 { font-size: 18px; margin-bottom: 0.5rem; }' +
    'h3 { font-size: 14px; margin: 1rem 0 0.5rem; }' +
    'table { width: 100%; border-collapse: collapse; margin: 0.5rem 0; }' +
    'th, td { padding: 6px 8px; border-bottom: 1px solid #e0e0e0; text-align: left; font-size: 11px; }' +
    'th { background: #f5f5f5; font-weight: 600; }' +
    'strong { font-weight: 600; }' +
    '.disc-stats { display: flex; gap: 8px; margin: 0.5rem 0; }' +
    '.disc-stat { flex: 1; text-align: center; padding: 8px; border: 1px solid #e0e0e0; border-radius: 6px; }' +
    '.disc-stat-num { display: block; font-size: 18px; font-weight: 400; }' +
    '.disc-summary { background: #f8f8f8; padding: 10px; border-radius: 6px; margin: 0.5rem 0; }' +
    '.disc-back, .disc-status, .disc-btn, .sb-delete, button { display: none !important; }' +
    '.disc-item { border: 1px solid #e0e0e0; border-radius: 6px; margin: 0.4rem 0; overflow: hidden; }' +
    '.disc-item-header { padding: 6px 10px; background: #f8f8f8; }' +
    '.disc-item-body { padding: 8px 10px; }' +
    '.disc-recommendation { background: #f8f8f8; padding: 8px; border-radius: 6px; margin: 0.3rem 0; }' +
    'p { margin: 0.3rem 0; }' +
    '@media print { body { padding: 0; } }' +
    '</style></head><body>');
  printWindow.document.write('<div style="text-align:center;margin-bottom:1rem"><h1 style="font-size:20px;margin:0">insani</h1><p style="color:#888;font-size:11px">Construction Intelligence Report — ' + new Date().toLocaleDateString() + '</p></div>');
  printWindow.document.write(reportEl.innerHTML);
  printWindow.document.write('</body></html>');
  printWindow.document.close();

  // Trigger print after content loads
  printWindow.onload = function() { printWindow.print(); };
}

// ═══ VIEW MANAGEMENT ═══

function showAgentView() {
  document.getElementById('chatView').classList.add('vh');
  document.getElementById('dashView').classList.add('vh');
  var dcv = document.getElementById('discrepancyView');
  if (dcv) { dcv.classList.add('vh'); dcv.style.display = 'none'; }
  document.getElementById('agentView').classList.remove('vh');
  document.getElementById('agentView').style.display = '';
  document.querySelector('.input-area').style.display = 'none';
}

function formatNum(n) {
  if (!n && n !== 0) return '-';
  return Number(n).toLocaleString('en-CA', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

// ═══ AGENT HISTORY ═══

async function loadAgentHistory() {
  if (!activeProjectId) return;
  try {
    agentHistory = await apiGetAgentHistory(activeProjectId);
    renderAgentHistory();
  } catch (e) {
    agentHistory = [];
    renderAgentHistory();
  }
}

function renderAgentHistory() {
  var container = document.getElementById('agentHistoryList');
  if (!container) return;

  if (!agentHistory.length) {
    container.innerHTML = '<div style="color:var(--text-dim);font-size:0.72rem;padding:0.2rem 0.4rem">No previous runs</div>';
    return;
  }

  container.innerHTML = agentHistory.map(function(r) {
    var icon = r.agent_type === 'materials' ? 'MT' : 'BID';
    var color = r.status === 'complete' ? 'var(--green)' : 'var(--red)';
    return '<div class="doc-item" onclick="viewAgentRun(' + r.id + ',\'' + r.agent_type + '\')">' +
      '<span class="doc-item-icon" style="color:' + color + '">' + icon + '</span>' +
      '<span class="doc-item-name" style="font-size:0.72rem">' + esc(r.title) + '</span>' +
      '<button class="sb-delete" onclick="event.stopPropagation();deleteAgentRun(' + r.id + ')" title="Delete">×</button>' +
    '</div>';
  }).join('');
}

async function viewAgentRun(runId, agentType) {
  try {
    showAgentView();
    document.getElementById('agentView').innerHTML = '<div class="agent-loading"><div class="agent-loading-spin"></div><div>Loading report...</div></div>';
    var result = await apiGetAgentRun(runId);
    if (agentType === 'materials') {
      renderMaterialResults(result);
    } else {
      renderBidResults(result);
    }
  } catch (e) {
    showToast('Failed to load report: ' + e.message);
  }
}

async function deleteAgentRun(runId) {
  try {
    await apiDeleteAgentRun(runId);
    await loadAgentHistory();
    showToast('Report deleted');
  } catch (e) {
    showToast('Failed to delete');
  }
}
