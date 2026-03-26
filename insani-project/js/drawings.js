/* ═══════════════════════════════════════════════
   DRAWINGS — Blueprint upload, sheet browser,
   drawing viewer with zoom/nav, and vision Q&A.
   ═══════════════════════════════════════════════ */

var drawingDocuments = [];   // Drawing docs for current project
var activeDrawingDoc = null;  // Currently viewed drawing
var drawingSheets = [];       // Sheets for active drawing
var drawingCurrentPage = 1;
var drawingTotalPages = 1;
var drawingZoom = 1;

// ═══ API ═══

async function apiUploadDrawing(file, projectId) {
  var formData = new FormData();
  formData.append('file', file);
  formData.append('project_id', projectId);

  var res = await fetch(API_BASE + '/v1/drawings/upload', {
    method: 'POST',
    headers: accessToken ? { 'Authorization': 'Bearer ' + accessToken } : {},
    body: formData
  });

  if (!res.ok) {
    var body = await res.json().catch(function() { return null; });
    throw new Error((body && body.detail) || 'Upload failed');
  }
  return res.json();
}

async function apiGetSheets(docId) {
  return apiFetch('/v1/drawings/' + docId + '/sheets');
}

async function apiAskDrawing(docId, question, projectId) {
  return apiFetch('/v1/drawings/ask', {
    method: 'POST',
    body: JSON.stringify({ doc_id: docId, question: question, project_id: projectId })
  });
}

// ═══ UPLOAD ═══

async function uploadDrawing(inputEl) {
  var file = inputEl.files[0];
  if (!file) return;
  inputEl.value = '';

  if (!activeProjectId) {
    showToast('Select a project first');
    return;
  }

  if (file.size > 50 * 1024 * 1024) {
    showToast('File too large — max 50MB');
    return;
  }

  var ext = file.name.split('.').pop().toLowerCase();
  if (['pdf', 'png', 'jpg', 'jpeg', 'tif', 'tiff'].indexOf(ext) === -1) {
    showToast('Upload PDF or image files only');
    return;
  }

  showToast('Uploading blueprint: ' + file.name + '...');

  try {
    var result = await apiUploadDrawing(file, activeProjectId);
    showToast(file.name + ' uploaded — ' + result.page_count + ' sheets indexed');
    await loadDrawingDocuments();

    // Auto-open the viewer
    if (result.id) {
      await openDrawingDoc(result.id);
    }
  } catch (e) {
    showToast('Upload failed: ' + e.message);
  }
}

// ═══ DRAWING LIST ═══

async function loadDrawingDocuments() {
  if (!activeProjectId) return;

  try {
    var allDocs = await apiFetch('/v1/documents?project_id=' + activeProjectId);
    // Also check drawing-specific docs by trying sheets endpoint
    drawingDocuments = [];
    for (var i = 0; i < allDocs.length; i++) {
      try {
        var sheets = await apiGetSheets(allDocs[i].id);
        if (sheets && sheets.length > 0) {
          allDocs[i]._sheets = sheets;
          drawingDocuments.push(allDocs[i]);
        }
      } catch (e) { /* not a drawing doc */ }
    }
    renderDrawingList();
  } catch (e) {
    drawingDocuments = [];
    renderDrawingList();
  }
}

function renderDrawingList() {
  var container = document.getElementById('drawingList');
  if (!container) return;

  if (!drawingDocuments.length) {
    container.innerHTML = '<div style="color:var(--text-dim);font-size:0.75rem;padding:0.3rem 0.6rem">No blueprints uploaded</div>';
    return;
  }

  container.innerHTML = drawingDocuments.map(function(d) {
    var sheetCount = d._sheets ? d._sheets.length : d.page_count;
    return '<div class="doc-item drawing-doc-item" onclick="openDrawingDoc(' + d.id + ')">' +
      '<span class="doc-item-icon" style="color:var(--blue)">BP</span>' +
      '<span class="doc-item-name">' + esc(d.filename) + '</span>' +
      '<span class="doc-item-pages">' + sheetCount + 's</span>' +
      '<button class="sb-delete" onclick="event.stopPropagation();deleteDrawing(' + d.id + ')" title="Delete blueprint">×</button>' +
    '</div>';
  }).join('');
}

async function deleteDrawing(docId) {
  try {
    await apiFetch('/v1/documents/' + docId, { method: 'DELETE' });
    if (activeDrawingDoc === docId) {
      activeDrawingDoc = null;
      closeDrawingViewer();
    }
    await loadDrawingDocuments();
    showToast('Blueprint deleted');
  } catch (e) {
    showToast('Failed to delete blueprint');
  }
}

// ═══ DRAWING VIEWER ═══

async function openDrawingDoc(docId) {
  try {
    drawingSheets = await apiGetSheets(docId);
    activeDrawingDoc = docId;
    drawingCurrentPage = 1;
    drawingTotalPages = drawingSheets.length;

    renderDrawingViewer();
    loadDrawingPage(1);

    document.getElementById('drawingViewerOverlay').classList.add('open');
  } catch (e) {
    showToast('Failed to load drawing: ' + e.message);
  }
}

function renderDrawingViewer() {
  var sheetsHtml = drawingSheets.map(function(s, i) {
    var label = s.sheet_number || ('P' + s.page_number);
    var title = s.sheet_title || ('Page ' + s.page_number);
    var discClass = s.discipline ? ' disc-' + s.discipline : '';
    return '<div class="drawing-sheet-thumb' + discClass + (i === 0 ? ' active' : '') + '" ' +
      'onclick="loadDrawingPage(' + s.page_number + ')" ' +
      'title="' + esc(label + ' — ' + title) + '">' +
      '<span class="sheet-label">' + esc(label) + '</span>' +
      '<span class="sheet-title">' + esc(title) + '</span>' +
    '</div>';
  }).join('');

  document.getElementById('drawingSheets').innerHTML = sheetsHtml;
  updateDrawingPageLabel();
}

function loadDrawingPage(pageNum) {
  drawingCurrentPage = pageNum;
  drawingZoom = 1;

  var img = document.getElementById('drawingImage');
  img.src = API_BASE + '/v1/drawings/' + activeDrawingDoc + '/page/' + pageNum + '/image';
  img.style.transform = 'scale(1)';

  // Update active sheet thumb
  var thumbs = document.querySelectorAll('.drawing-sheet-thumb');
  thumbs.forEach(function(t, i) {
    t.classList.toggle('active', (i + 1) === pageNum);
  });

  // Clear highlights
  document.getElementById('drawingHighlights').innerHTML = '';

  updateDrawingPageLabel();
}

function updateDrawingPageLabel() {
  var sheet = drawingSheets[drawingCurrentPage - 1];
  var label = 'Sheet ' + drawingCurrentPage + ' of ' + drawingTotalPages;
  if (sheet && sheet.sheet_number) {
    label = sheet.sheet_number + ' — ' + drawingCurrentPage + '/' + drawingTotalPages;
  }
  document.getElementById('drawingPageLabel').textContent = label;

  // Update title
  if (sheet) {
    document.getElementById('drawingViewerTitle').textContent = sheet.sheet_title || 'Drawing';
  }
}

function drawingPrevPage() {
  if (drawingCurrentPage > 1) loadDrawingPage(drawingCurrentPage - 1);
}

function drawingNextPage() {
  if (drawingCurrentPage < drawingTotalPages) loadDrawingPage(drawingCurrentPage + 1);
}

function drawingZoomIn() {
  drawingZoom = Math.min(drawingZoom + 0.25, 4);
  document.getElementById('drawingImage').style.transform = 'scale(' + drawingZoom + ')';
}

function drawingZoomOut() {
  drawingZoom = Math.max(drawingZoom - 0.25, 0.25);
  document.getElementById('drawingImage').style.transform = 'scale(' + drawingZoom + ')';
}

function drawingZoomFit() {
  drawingZoom = 1;
  document.getElementById('drawingImage').style.transform = 'scale(1)';
}

function closeDrawingViewer() {
  document.getElementById('drawingViewerOverlay').classList.remove('open');
}

// ═══ DRAWING CITATION CLICK HANDLER ═══

document.addEventListener('click', function(e) {
  var cite = e.target.closest('.drawing-cite');
  if (!cite) return;

  var docId = parseInt(cite.getAttribute('data-doc-id'));
  var page = parseInt(cite.getAttribute('data-page')) || 1;

  if (!docId) return;

  // Open the drawing viewer at the cited page
  (async function() {
    try {
      drawingSheets = await apiGetSheets(docId);
      activeDrawingDoc = docId;
      drawingTotalPages = drawingSheets.length;
      renderDrawingViewer();
      loadDrawingPage(page);
      document.getElementById('drawingViewerOverlay').classList.add('open');
    } catch (err) {
      showToast('Could not open drawing');
    }
  })();
});

// Close drawing viewer on Escape
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    var overlay = document.getElementById('drawingViewerOverlay');
    if (overlay && overlay.classList.contains('open')) {
      closeDrawingViewer();
      e.stopPropagation();
    }
  }
});

// Close on backdrop click
document.addEventListener('click', function(e) {
  if (e.target === document.getElementById('drawingViewerOverlay')) {
    closeDrawingViewer();
  }
});
