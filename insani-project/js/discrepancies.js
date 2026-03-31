/* ═══════════════════════════════════════════════
   DISCREPANCIES — Spec vs Submittal comparison.
   Upload specs + submittals, run comparison,
   view findings, update statuses.
   ═══════════════════════════════════════════════ */

var discrepancyReports = [];
var activeReport = null;

// ═══ API ═══

async function apiAnalyzeDiscrepancies(projectId, specDocIds, submittalDocIds, title) {
  return apiFetch('/v1/discrepancies/analyze', {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      spec_doc_ids: specDocIds,
      submittal_doc_ids: submittalDocIds,
      title: title || ''
    })
  });
}

async function apiListDiscrepancyReports(projectId) {
  return apiFetch('/v1/discrepancies?project_id=' + projectId);
}

async function apiGetDiscrepancyReport(reportId) {
  return apiFetch('/v1/discrepancies/' + reportId);
}

async function apiUpdateDiscrepancyItem(itemId, status) {
  return apiFetch('/v1/discrepancies/items/' + itemId, {
    method: 'PATCH',
    body: JSON.stringify({ status: status })
  });
}

async function apiDeleteDiscrepancyReport(reportId) {
  return apiFetch('/v1/discrepancies/' + reportId, { method: 'DELETE' });
}

// ═══ REPORT LIST ═══

async function loadDiscrepancyReports() {
  if (!activeProjectId) return;
  try {
    discrepancyReports = await apiListDiscrepancyReports(activeProjectId);
    renderDiscrepancyList();
  } catch (e) {
    discrepancyReports = [];
    renderDiscrepancyList();
  }
}

function renderDiscrepancyList() {
  var container = document.getElementById('discrepancyList');
  if (!container) return;

  if (!discrepancyReports.length) {
    container.innerHTML = '<div style="color:var(--text-dim);font-size:0.75rem;padding:0.3rem 0.6rem">No comparisons yet</div>';
    return;
  }

  container.innerHTML = discrepancyReports.map(function(r) {
    var badge = r.status === 'complete' ? r.discrepancy_count + ' findings' : r.status;
    var color = r.discrepancy_count > 0 ? 'var(--red)' : 'var(--green)';
    if (r.status !== 'complete') color = 'var(--text-dim)';
    return '<div class="doc-item" onclick="viewDiscrepancyReport(' + r.id + ')">' +
      '<span class="doc-item-icon" style="color:' + color + '">DC</span>' +
      '<span class="doc-item-name">' + esc(r.title) + '</span>' +
      '<span class="doc-item-pages" style="color:' + color + '">' + badge + '</span>' +
      '<button class="sb-delete" onclick="event.stopPropagation();deleteDiscrepancyReport(' + r.id + ')" title="Delete report">×</button>' +
    '</div>';
  }).join('');
}

async function deleteDiscrepancyReport(reportId) {
  try {
    await apiDeleteDiscrepancyReport(reportId);
    await loadDiscrepancyReports();
    if (activeReport && activeReport.id === reportId) {
      activeReport = null;
      showChat();
    }
    showToast('Report deleted');
  } catch (e) {
    showToast('Failed to delete report');
  }
}

// ═══ DOCUMENT SELECTOR MODAL ═══

function showCompareModal() {
  if (!activeProjectId) { showToast('Select a project first'); return; }
  if (!projectDocuments.length) { showToast('Upload documents first'); return; }

  var html = '<div class="disc-modal-content">' +
    '<h3 style="margin:0 0 0.5rem;font-size:1rem;font-weight:500">Compare Spec vs Submittal</h3>' +
    '<p style="color:var(--text-muted);font-size:0.78rem;margin:0 0 1rem">Select which documents are specs and which are submittals</p>' +
    '<div class="disc-doc-grid">' +
    '<div class="disc-col"><div class="disc-col-label">Specifications</div><div id="specCheckboxes">' +
    projectDocuments.map(function(d) {
      return '<label class="disc-check"><input type="checkbox" value="' + d.id + '" name="spec"> ' + esc(d.filename) + '</label>';
    }).join('') +
    '</div></div>' +
    '<div class="disc-col"><div class="disc-col-label">Submittals</div><div id="submittalCheckboxes">' +
    projectDocuments.map(function(d) {
      return '<label class="disc-check"><input type="checkbox" value="' + d.id + '" name="submittal"> ' + esc(d.filename) + '</label>';
    }).join('') +
    '</div></div>' +
    '</div>' +
    '<div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:1rem">' +
    '<button class="disc-btn disc-btn-cancel" onclick="closeCompareModal()">Cancel</button>' +
    '<button class="disc-btn disc-btn-run" onclick="runComparison()">Run Comparison</button>' +
    '</div></div>';

  document.getElementById('compareModal').innerHTML = html;
  document.getElementById('compareModal').classList.add('open');
}

function closeCompareModal() {
  document.getElementById('compareModal').classList.remove('open');
}

async function runComparison() {
  var specIds = [];
  var submittalIds = [];
  document.querySelectorAll('#specCheckboxes input:checked').forEach(function(cb) { specIds.push(parseInt(cb.value)); });
  document.querySelectorAll('#submittalCheckboxes input:checked').forEach(function(cb) { submittalIds.push(parseInt(cb.value)); });

  if (!specIds.length) { showToast('Select at least one spec document'); return; }
  if (!submittalIds.length) { showToast('Select at least one submittal document'); return; }

  closeCompareModal();
  showToast('Running comparison... this may take a moment');

  try {
    var report = await apiAnalyzeDiscrepancies(activeProjectId, specIds, submittalIds);
    activeReport = report;
    await loadDiscrepancyReports();
    renderDiscrepancyReport(report);
    showDiscrepancyView();
    showToast('Comparison complete — ' + report.discrepancy_count + ' findings');
  } catch (e) {
    showToast('Comparison failed: ' + e.message);
  }
}

// ═══ REPORT VIEW ═══

async function viewDiscrepancyReport(reportId) {
  try {
    var report = await apiGetDiscrepancyReport(reportId);
    activeReport = report;
    renderDiscrepancyReport(report);
    showDiscrepancyView();
  } catch (e) {
    showToast('Failed to load report: ' + e.message);
  }
}

function showDiscrepancyView() {
  document.getElementById('chatView').classList.add('vh');
  document.getElementById('dashView').classList.add('vh');
  document.getElementById('discrepancyView').classList.remove('vh');
  document.getElementById('discrepancyView').style.display = '';
  document.querySelector('.input-area').style.display = 'none';
}

function renderDiscrepancyReport(report) {
  var view = document.getElementById('discrepancyView');

  var critCount = 0, majCount = 0, minCount = 0, infoCount = 0;
  (report.items || []).forEach(function(item) {
    if (item.severity === 'critical') critCount++;
    else if (item.severity === 'major') majCount++;
    else if (item.severity === 'minor') minCount++;
    else infoCount++;
  });

  var html = '<div class="disc-report">' +
    '<div class="disc-report-header">' +
    '<button class="disc-back" onclick="showChat();loadDiscrepancyReports()">← Back to chat</button>' +
    '<h2>' + esc(report.title) + '</h2>' +
    '<div class="disc-status disc-status-' + report.status + '">' + report.status + '</div>' +
    '</div>';

  if (report.summary) {
    html += '<div class="disc-summary">' + esc(report.summary) + '</div>';
  }

  html += '<div class="disc-stats">' +
    '<div class="disc-stat disc-stat-critical"><span class="disc-stat-num">' + critCount + '</span><span>Critical</span></div>' +
    '<div class="disc-stat disc-stat-major"><span class="disc-stat-num">' + majCount + '</span><span>Major</span></div>' +
    '<div class="disc-stat disc-stat-minor"><span class="disc-stat-num">' + minCount + '</span><span>Minor</span></div>' +
    '<div class="disc-stat disc-stat-info"><span class="disc-stat-num">' + infoCount + '</span><span>Info</span></div>' +
    '</div>';

  if (report.items && report.items.length) {
    html += '<div class="disc-items">';
    report.items.forEach(function(item) {
      html += renderDiscrepancyItem(item);
    });
    html += '</div>';
  } else {
    html += '<div style="padding:2rem;text-align:center;color:var(--text-dim)">No discrepancies found — documents appear to be in compliance.</div>';
  }

  html += '</div>';
  view.innerHTML = html;
}

function renderDiscrepancyItem(item) {
  var sevClass = 'disc-sev-' + item.severity;
  var sevLabel = item.severity.charAt(0).toUpperCase() + item.severity.slice(1);
  var catLabel = item.category.replace(/_/g, ' ');

  var specCite = item.spec_reference ? '<span class="doc-cite" data-doc-id="' + item.spec_doc_id + '" data-page="' + (item.spec_page || 1) + '">' + esc(item.spec_reference) + '</span>' : '';
  var submitCite = item.submittal_reference ? '<span class="doc-cite" data-doc-id="' + item.submittal_doc_id + '" data-page="' + (item.submittal_page || 1) + '">' + esc(item.submittal_reference) + '</span>' : '';

  var statusBtns = '';
  if (item.status === 'open') {
    statusBtns = '<button class="disc-action-btn" onclick="updateItemStatus(' + item.id + ',\'resolved\')">Resolve</button>' +
      '<button class="disc-action-btn disc-action-dismiss" onclick="updateItemStatus(' + item.id + ',\'dismissed\')">Dismiss</button>';
  } else {
    statusBtns = '<span class="disc-item-resolved">' + item.status + '</span>';
  }

  return '<div class="disc-item ' + sevClass + ' disc-item-' + item.status + '">' +
    '<div class="disc-item-header">' +
    '<span class="disc-sev-badge ' + sevClass + '">' + sevLabel + '</span>' +
    '<span class="disc-cat">' + catLabel + '</span>' +
    '<span class="disc-item-title">' + esc(item.title) + '</span>' +
    '<div class="disc-item-actions">' + statusBtns + '</div>' +
    '</div>' +
    '<div class="disc-item-body">' +
    '<p>' + esc(item.description) + '</p>' +
    (item.spec_excerpt || item.submittal_excerpt ? '<div class="disc-excerpts">' +
      (item.spec_excerpt ? '<div class="disc-excerpt"><div class="disc-excerpt-label">Spec says: ' + specCite + '</div><div class="disc-excerpt-text">' + esc(item.spec_excerpt) + '</div></div>' : '') +
      (item.submittal_excerpt ? '<div class="disc-excerpt disc-excerpt-sub"><div class="disc-excerpt-label">Submittal says: ' + submitCite + '</div><div class="disc-excerpt-text">' + esc(item.submittal_excerpt) + '</div></div>' : '') +
    '</div>' : '') +
    (item.recommendation ? '<div class="disc-recommendation"><strong>Recommendation:</strong> ' + esc(item.recommendation) + '</div>' : '') +
    '</div></div>';
}

async function updateItemStatus(itemId, newStatus) {
  try {
    await apiUpdateDiscrepancyItem(itemId, newStatus);
    if (activeReport) {
      var report = await apiGetDiscrepancyReport(activeReport.id);
      activeReport = report;
      renderDiscrepancyReport(report);
    }
    showToast('Item ' + newStatus);
  } catch (e) {
    showToast('Failed to update: ' + e.message);
  }
}
