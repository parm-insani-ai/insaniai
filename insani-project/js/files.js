/* ═══════════════════════════════════════════════
   FILES — File upload and drag-and-drop.
   
   Handles file selection, base64 conversion,
   MIME type detection, chip rendering, and the
   full-page drop zone overlay.
   ═══════════════════════════════════════════════ */

/** Handle file input change event. */
function onFiles(e) {
  Array.from(e.target.files).forEach(addFile);
  e.target.value = '';  // Reset so same file can be re-selected
}

/**
 * Process a single file: validate size, read as base64, detect MIME type.
 * Adds to the global `files` array and re-renders chips.
 */
function addFile(f) {
  if (f.size > 50 * 1024 * 1024) {
    showToast('File too large — max 50MB');
    return;
  }

  const r = new FileReader();
  r.onload = e => {
    // Detect MIME type (browser detection can be unreliable, so we also check extension)
    let mt = f.type;
    if (f.name.endsWith('.pdf')) mt = 'application/pdf';
    else if (f.name.match(/\.png$/i)) mt = 'image/png';
    else if (f.name.match(/\.(jpg|jpeg)$/i)) mt = 'image/jpeg';
    else if (f.name.match(/\.webp$/i)) mt = 'image/webp';

    files.push({ name: f.name, size: f.size, b64: e.target.result.split(',')[1], mt });
    chips();
  };
  r.readAsDataURL(f);
}

/** Render file preview chips above the input area. */
function chips() {
  const c = document.getElementById('chipBox');
  if (!files.length) {
    c.style.display = 'none';
    c.innerHTML = '';
    return;
  }
  c.style.display = 'flex';

  const fmt = b => b < 1024 ? b + 'B' : b < 1048576 ? (b / 1024).toFixed(1) + 'KB' : (b / 1048576).toFixed(1) + 'MB';

  c.innerHTML = files.map((f, i) => `
    <div class="fchip">
      <span>${f.name.endsWith('.pdf') ? 'PDF' : 'File'}</span>
      <span>${f.name}</span>
      <span class="fchip-sz">${fmt(f.size)}</span>
      <button class="fchip-x" onclick="rmFile(${i})">×</button>
    </div>
  `).join('');
}

/** Remove a file from the pending list by index. */
function rmFile(i) {
  files.splice(i, 1);
  chips();
}

// ── Drag & drop listeners ──
// Show overlay when dragging files over the page (but not over viewers)
document.addEventListener('dragover', e => {
  e.preventDefault();
  // Don't show drop zone if a viewer panel is open
  var drawingOpen = document.getElementById('drawingViewerOverlay') && document.getElementById('drawingViewerOverlay').classList.contains('open');
  var pdfOpen = document.getElementById('pdfViewerOverlay') && document.getElementById('pdfViewerOverlay').classList.contains('open');
  if (drawingOpen || pdfOpen) return;
  document.getElementById('dropZone').classList.add('on');
});

// Hide overlay when dragging leaves the drop zone
document.getElementById('dropZone').addEventListener('dragleave', e => {
  e.preventDefault();
  document.getElementById('dropZone').classList.remove('on');
});

// Process dropped files
document.getElementById('dropZone').addEventListener('drop', e => {
  e.preventDefault();
  document.getElementById('dropZone').classList.remove('on');
  Array.from(e.dataTransfer.files).forEach(addFile);
});
