/* ═══════════════════════════════════════════════
   AGENTS — Material Price Tracker & Bid Estimator
   Multi-step AI agents for construction intelligence.
   ═══════════════════════════════════════════════ */

// ═══ API ═══

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
  if (!activeProjectId) { showToast('Select a project first'); return; }
  if (!projectDocuments.length) { showToast('Upload documents first'); return; }

  var html = '<div class="disc-modal-content">' +
    '<h3 style="margin:0 0 0.5rem;font-size:1rem;font-weight:500">Material Price Analysis</h3>' +
    '<p style="color:var(--text-muted);font-size:0.78rem;margin:0 0 1rem">Select documents to extract materials and estimate pricing from Halifax suppliers</p>' +
    '<div id="materialDocCheckboxes">' +
    projectDocuments.map(function(d) {
      return '<label class="disc-check"><input type="checkbox" value="' + d.id + '" checked> ' + esc(d.filename) + '</label>';
    }).join('') +
    '</div>' +
    '<div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:1rem">' +
    '<button class="disc-btn disc-btn-cancel" onclick="closeMaterialModal()">Cancel</button>' +
    '<button class="disc-btn disc-btn-run" onclick="runMaterialAnalysis()">Analyze Materials</button>' +
    '</div></div>';

  document.getElementById('agentModal').innerHTML = html;
  document.getElementById('agentModal').classList.add('open');
}

function closeMaterialModal() {
  document.getElementById('agentModal').classList.remove('open');
}

async function runMaterialAnalysis() {
  var docIds = [];
  document.querySelectorAll('#materialDocCheckboxes input:checked').forEach(function(cb) { docIds.push(parseInt(cb.value)); });
  if (!docIds.length) { showToast('Select at least one document'); return; }

  closeMaterialModal();
  showToast('Analyzing materials... this takes 30-60 seconds');

  try {
    var result = await apiRunMaterialAnalysis(docIds, activeProjectId);
    renderMaterialResults(result);
    showAgentView();
  } catch (e) {
    showToast('Material analysis failed: ' + e.message);
  }
}

function renderMaterialResults(result) {
  var view = document.getElementById('agentView');

  var html = '<div class="disc-report">' +
    '<div class="disc-report-header">' +
    '<button class="disc-back" onclick="showChat()">← Back to chat</button>' +
    '<h2>Material Price Analysis</h2>' +
    '<div class="disc-status disc-status-' + result.status + '">' + result.status + '</div>' +
    '</div>';

  if (result.error) {
    html += '<div class="disc-summary" style="color:var(--red)">' + esc(result.error) + '</div>';
  }

  // Summary
  var s = result.summary || {};
  if (s.total_mid) {
    html += '<div class="disc-stats">' +
      '<div class="disc-stat"><span class="disc-stat-num" style="color:var(--green)">$' + formatNum(s.total_low) + '</span><span>Low Estimate</span></div>' +
      '<div class="disc-stat"><span class="disc-stat-num" style="color:var(--blue)">$' + formatNum(s.total_mid) + '</span><span>Mid Estimate</span></div>' +
      '<div class="disc-stat"><span class="disc-stat-num" style="color:var(--orange)">$' + formatNum(s.total_high) + '</span><span>High Estimate</span></div>' +
      '<div class="disc-stat"><span class="disc-stat-num">' + (result.materials_found || 0) + '</span><span>Materials</span></div>' +
      '</div>';
    if (s.tax_note) html += '<p style="font-size:0.72rem;color:var(--text-dim);margin:-0.5rem 0 1rem">' + esc(s.tax_note) + '</p>';
  }

  // Materials table
  var mats = result.materials || [];
  if (mats.length) {
    html += '<h3 style="font-size:0.88rem;margin:1rem 0 0.5rem">Material Pricing</h3>' +
      '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:0.78rem">' +
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
        '<td style="padding:0.4rem 0.5rem">' + esc(m.quantity || '-') + '</td>' +
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
  if (!activeProjectId) { showToast('Select a project first'); return; }
  if (!projectDocuments.length) { showToast('Upload ITB/RFP documents first'); return; }

  var html = '<div class="disc-modal-content">' +
    '<h3 style="margin:0 0 0.5rem;font-size:1rem;font-weight:500">Bid Estimating Assistant</h3>' +
    '<p style="color:var(--text-muted);font-size:0.78rem;margin:0 0 1rem">Upload your ITB/RFP documents and get a complete bid proposal with Halifax-specific pricing</p>' +
    '<div id="bidDocCheckboxes">' +
    projectDocuments.map(function(d) {
      return '<label class="disc-check"><input type="checkbox" value="' + d.id + '" checked> ' + esc(d.filename) + '</label>';
    }).join('') +
    '</div>' +
    '<div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:1rem">' +
    '<button class="disc-btn disc-btn-cancel" onclick="closeBidModal()">Cancel</button>' +
    '<button class="disc-btn disc-btn-run" onclick="runBidAnalysis()">Generate Bid</button>' +
    '</div></div>';

  document.getElementById('agentModal').innerHTML = html;
  document.getElementById('agentModal').classList.add('open');
}

function closeBidModal() {
  document.getElementById('agentModal').classList.remove('open');
}

async function runBidAnalysis() {
  var docIds = [];
  document.querySelectorAll('#bidDocCheckboxes input:checked').forEach(function(cb) { docIds.push(parseInt(cb.value)); });
  if (!docIds.length) { showToast('Select at least one document'); return; }

  closeBidModal();
  showToast('Generating bid proposal... this takes 1-2 minutes');

  try {
    var result = await apiRunBidAnalysis(docIds, activeProjectId);
    renderBidResults(result);
    showAgentView();
  } catch (e) {
    showToast('Bid analysis failed: ' + e.message);
  }
}

function renderBidResults(result) {
  var view = document.getElementById('agentView');

  var html = '<div class="disc-report">' +
    '<div class="disc-report-header">' +
    '<button class="disc-back" onclick="showChat()">← Back to chat</button>' +
    '<h2>Bid Proposal</h2>' +
    '<div class="disc-status disc-status-' + result.status + '">' + result.status + '</div>' +
    '</div>';

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

  // Compliance
  var comp = result.compliance || {};
  if (comp.issues && comp.issues.length) {
    html += '<h3 style="font-size:0.88rem;margin:1rem 0 0.5rem">Code Compliance Issues (' + comp.issues.length + ')</h3>';
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
      '<div style="border:1px solid var(--border);border-radius:10px;padding:1.2rem;background:var(--bg-chat)">' +
      result.proposal_html + '</div>';
  }

  // Job sources
  if (result.job_sources) {
    html += '<h3 style="font-size:0.88rem;margin:1.5rem 0 0.5rem">Find More Projects</h3>' +
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
