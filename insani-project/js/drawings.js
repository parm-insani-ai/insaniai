/* ═══════════════════════════════════════════════
   DRAWINGS — Blueprint upload, sheet browser,
   and drawing viewer with zoom/pan/scroll.
   ═══════════════════════════════════════════════ */

var drawingDocuments = [];
var activeDrawingDoc = null;
var drawingSheets = [];
var drawingCurrentPage = 1;
var drawingTotalPages = 1;
var drawingZoom = 1;
var MIN_ZOOM = 0.25;
var MAX_ZOOM = 5;
var ZOOM_STEP = 0.25;

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
    drawingZoom = 1;

    renderSheetThumbs();
    loadDrawingPage(1);

    document.getElementById('drawingViewerOverlay').classList.add('open');
  } catch (e) {
    showToast('Failed to load drawing: ' + e.message);
  }
}

function renderSheetThumbs() {
  var html = drawingSheets.map(function(s, i) {
    var label = s.sheet_number || ('P' + s.page_number);
    var title = s.sheet_title || ('Page ' + s.page_number);
    return '<button class="dv-sheet' + (i === 0 ? ' active' : '') + '" ' +
      'onclick="loadDrawingPage(' + s.page_number + ')" ' +
      'title="' + esc(label + ' — ' + title) + '">' +
      '<span class="dv-sheet-num">' + esc(label) + '</span>' +
      '<span class="dv-sheet-title">' + esc(title) + '</span>' +
    '</button>';
  }).join('');

  document.getElementById('dvSheets').innerHTML = html;
}

function loadDrawingPage(pageNum) {
  drawingCurrentPage = pageNum;
  drawingZoom = 1;

  var img = document.getElementById('dvImage');
  var container = document.getElementById('dvScrollArea');

  // Show loading state
  img.style.display = 'none';
  img.onload = function() {
    img.style.display = 'block';
    applyZoom();
    // Scroll to top-left on page load
    container.scrollTop = 0;
    container.scrollLeft = 0;
  };
  img.onerror = function() {
    img.style.display = 'none';
    showToast('Failed to load page image');
  };
  img.src = API_BASE + '/v1/drawings/' + activeDrawingDoc + '/page/' + pageNum + '/image';

  // Update active sheet thumb
  var thumbs = document.querySelectorAll('.dv-sheet');
  thumbs.forEach(function(t, i) {
    t.classList.toggle('active', (i + 1) === pageNum);
  });

  updatePageInfo();
}

function updatePageInfo() {
  var sheet = drawingSheets[drawingCurrentPage - 1];
  var pageLabel = drawingCurrentPage + ' / ' + drawingTotalPages;
  if (sheet && sheet.sheet_number) {
    pageLabel = sheet.sheet_number + '  (' + drawingCurrentPage + '/' + drawingTotalPages + ')';
  }
  document.getElementById('dvPageInfo').textContent = pageLabel;

  var title = 'Drawing';
  if (sheet) title = sheet.sheet_title || 'Page ' + drawingCurrentPage;
  document.getElementById('dvTitle').textContent = title;

  document.getElementById('dvZoomLevel').textContent = Math.round(drawingZoom * 100) + '%';
}

// ── Zoom ──

function applyZoom() {
  var img = document.getElementById('dvImage');
  img.style.width = (drawingZoom * 100) + '%';
  img.style.height = 'auto';
  document.getElementById('dvZoomLevel').textContent = Math.round(drawingZoom * 100) + '%';
}

function dvZoomIn() {
  drawingZoom = Math.min(drawingZoom + ZOOM_STEP, MAX_ZOOM);
  applyZoom();
}

function dvZoomOut() {
  drawingZoom = Math.max(drawingZoom - ZOOM_STEP, MIN_ZOOM);
  applyZoom();
}

function dvZoomFit() {
  drawingZoom = 1;
  applyZoom();
  var container = document.getElementById('dvScrollArea');
  container.scrollTop = 0;
  container.scrollLeft = 0;
}

// Mouse wheel zoom
function dvWheelZoom(e) {
  if (!e.ctrlKey) return; // Only zoom with Ctrl+scroll
  e.preventDefault();

  var delta = e.deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP;
  drawingZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, drawingZoom + delta));
  applyZoom();
}

// ── Navigation ──

function dvPrevPage() {
  if (drawingCurrentPage > 1) loadDrawingPage(drawingCurrentPage - 1);
}

function dvNextPage() {
  if (drawingCurrentPage < drawingTotalPages) loadDrawingPage(drawingCurrentPage + 1);
}

function closeDrawingViewer() {
  document.getElementById('drawingViewerOverlay').classList.remove('open');
}

// ═══ EVENT LISTENERS ═══

// Drawing citation click handler
document.addEventListener('click', function(e) {
  var cite = e.target.closest('.drawing-cite');
  if (!cite) return;

  var docId = parseInt(cite.getAttribute('data-doc-id'));
  var page = parseInt(cite.getAttribute('data-page')) || 1;
  if (!docId) return;

  (async function() {
    try {
      drawingSheets = await apiGetSheets(docId);
      activeDrawingDoc = docId;
      drawingTotalPages = drawingSheets.length;
      renderSheetThumbs();
      loadDrawingPage(page);
      document.getElementById('drawingViewerOverlay').classList.add('open');
    } catch (err) {
      showToast('Could not open drawing');
    }
  })();
});

// Close on Escape
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

// Attach wheel zoom after DOM is ready
document.addEventListener('DOMContentLoaded', function() {
  var scrollArea = document.getElementById('dvScrollArea');
  if (scrollArea) {
    scrollArea.addEventListener('wheel', dvWheelZoom, { passive: false });
  }
});
