/* ═══════════════════════════════════════════════
   DOCUMENTS — Upload, list, PDF viewer, citation clicks.
   ═══════════════════════════════════════════════ */

var projectDocuments = [];

// ═══ UPLOAD ═══

async function uploadDocument(inputEl) {
  var file = inputEl.files[0];
  if (!file) return;
  inputEl.value = '';

  if (!activeProjectId) {
    showToast('Select a project first');
    return;
  }

  if (file.size > 50 * 1024 * 1024) {
    showToast('File too large - max 50MB');
    return;
  }

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

    if (!res.ok) {
      var body = await res.json().catch(function() { return null; });
      throw new Error((body && body.error && body.error.message) || 'Upload failed');
    }

    var doc = await res.json();
    showToast(file.name + ' uploaded - ' + doc.page_count + ' pages parsed');
    await loadProjectDocuments();

  } catch (e) {
    showToast('Upload failed: ' + e.message);
  }
}

// ═══ DOCUMENT LIST ═══

async function loadProjectDocuments() {
  if (!activeProjectId) return;

  try {
    projectDocuments = await apiFetch('/v1/documents?project_id=' + activeProjectId);
    renderDocumentList();
  } catch (e) {
    projectDocuments = [];
    renderDocumentList();
  }
}

function renderDocumentList() {
  var container = document.getElementById('docList');
  if (!container) return;

  if (!projectDocuments.length) {
    container.innerHTML = '<div style="color:var(--text-dim);font-size:0.75rem;padding:0.3rem 0.6rem">No documents uploaded</div>';
    return;
  }

  container.innerHTML = projectDocuments.map(function(d) {
    return '<div class="doc-item" onclick="openDocViewer(' + d.id + ', \'' + esc(d.filename).replace(/'/g, "\\'") + '\', 1)">' +
      '<span class="doc-item-icon">PDF</span>' +
      '<span class="doc-item-name">' + esc(d.filename) + '</span>' +
      '<span class="doc-item-pages">' + d.page_count + 'p</span>' +
    '</div>';
  }).join('');
}

// ═══ PDF VIEWER ═══

function openDocViewer(docId, filename, page) {
  page = page || 1;
  var overlay = document.getElementById('pdfViewerOverlay');
  var title = document.getElementById('pdfViewerTitle');
  var pageLabel = document.getElementById('pdfViewerPage');
  var iframe = document.getElementById('pdfViewerFrame');

  title.textContent = filename;
  pageLabel.textContent = 'Page ' + page;

  // Build PDF URL — append auth token as query param since iframes
  // cannot set Authorization headers
  var fileUrl = API_BASE + '/v1/documents/' + docId + '/file';
  iframe.src = fileUrl + '#page=' + page;

  overlay.classList.add('open');
}

function closeDocViewer() {
  var overlay = document.getElementById('pdfViewerOverlay');
  var iframe = document.getElementById('pdfViewerFrame');
  overlay.classList.remove('open');
  setTimeout(function() { iframe.src = ''; }, 300);
}

// ═══ CITATION CLICK HANDLER ═══
// Catches clicks on both .doc-cite elements (from document citations)
// and .cite elements (from project data citations)

document.addEventListener('click', function(e) {
  // Email/integration citations: <a class="email-cite" href="..." target="_blank">
  var emailCite = e.target.closest('.email-cite');
  if (emailCite) {
    e.preventDefault();
    var url = emailCite.getAttribute('href');
    if (url && url !== '#' && url !== '') {
      window.open(url, '_blank');
    }
    return;
  }

  // Document citations: <span class="doc-cite" data-doc-id="5" data-page="47">
  var cite = e.target.closest('.doc-cite');
  if (cite) {
    var docId = parseInt(cite.getAttribute('data-doc-id'));
    var page = parseInt(cite.getAttribute('data-page')) || 1;

    if (!docId) return;

    var doc = projectDocuments.find(function(d) { return d.id === docId; });
    var filename = doc ? doc.filename : 'Document #' + docId;

    openDocViewer(docId, filename, page);
    return;
  }

  // Source detail citations: <span class="cite cite-default">Procore</span>
  var srcCite = e.target.closest('.cite');
  if (srcCite) {
    var text = srcCite.textContent.toLowerCase().trim();
    if (text.includes('procore')) showSourceDetail('procore');
    else if (text.includes('autodesk') || text.includes('bim')) showSourceDetail('autodesk');
    else if (text.includes('sage')) showSourceDetail('sage');
    else if (text.includes('email') || text.includes('outlook')) showSourceDetail('email');
  }
});

// Close viewer on backdrop click
document.addEventListener('click', function(e) {
  if (e.target === document.getElementById('pdfViewerOverlay')) {
    closeDocViewer();
  }
});

// Close viewer with Escape
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    var overlay = document.getElementById('pdfViewerOverlay');
    if (overlay && overlay.classList.contains('open')) {
      closeDocViewer();
    }
  }
});
