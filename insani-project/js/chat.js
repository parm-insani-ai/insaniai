/* ═══════════════════════════════════════════════
   CHAT — Message handling and session management.
   ═══════════════════════════════════════════════ */

/**
 * Sanitize HTML from AI responses to prevent XSS.
 * Allows safe formatting tags, strips scripts, event handlers, iframes.
 */
function sanitizeHTML(html) {
  var temp = document.createElement('div');
  temp.innerHTML = html;
  // Remove dangerous elements
  var dangerous = temp.querySelectorAll('script,iframe,object,embed,form,input,textarea,select,button[type=submit],link,meta,base');
  dangerous.forEach(function(el) { el.remove(); });
  // Remove event handler attributes from all elements
  var all = temp.querySelectorAll('*');
  all.forEach(function(el) {
    var attrs = Array.from(el.attributes);
    attrs.forEach(function(attr) {
      if (attr.name.startsWith('on') || attr.name === 'srcdoc' || attr.name === 'formaction') {
        el.removeAttribute(attr.name);
      }
      if (attr.name === 'href' && attr.value.trim().toLowerCase().startsWith('javascript:')) {
        el.removeAttribute(attr.name);
      }
      if (attr.name === 'src' && attr.value.trim().toLowerCase().startsWith('javascript:')) {
        el.removeAttribute(attr.name);
      }
    });
  });
  return temp.innerHTML;
}

const IC = {
  search: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  dollar: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/></svg>',
  doc:    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
  warn:   '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'
};

function ask(t) {
  document.getElementById('input').value = t;
  showChat();
  send();
}

async function send() {
  const el = document.getElementById('input');
  const t = el.value.trim();
  const f = [...files];
  if ((!t && !f.length) || busy) return;

  busy = true;
  el.value = '';
  resize(el);
  files = [];
  chips();

  const w = document.getElementById('welcomeScreen');
  if (w) w.style.display = 'none';

  userMsg(t, f);

  const apiFiles = f.map(x => ({ name: x.name, base64: x.b64, media_type: x.mt }));

  const aiBubble = createEmptyAiMsg();
  const searchEl = showSearchingInline(aiBubble);

  // Collect raw text during streaming, then replace with formatted HTML at the end
  let streamingText = '';

  try {
    await apiAskStream(t, activeProjectId, activeChatId, apiFiles, {
      onSession(data) {
        activeChatId = data.session_id;
      },

      onToken(data) {
        if (searchEl.parentNode) searchEl.remove();
        // Accumulate raw text and show a clean plain-text preview
        streamingText += data.text;
        showStreamingPreview(aiBubble, streamingText);
      },

      onDone(data) {
        if (searchEl.parentNode) searchEl.remove();
        // Replace the streaming preview with the fully formatted HTML
        aiBubble.innerHTML = sanitizeHTML(data.full_response);
        scrollDown();
        loadRecentChats();
      },

      onError(data) {
        if (searchEl.parentNode) searchEl.remove();
        aiBubble.innerHTML = '<p style="color:var(--red)"><strong>Error</strong> — ' + esc(data.message) + '</p>';
      }
    });
  } catch (e) {
    if (searchEl.parentNode) searchEl.remove();
    aiBubble.innerHTML = '<p style="color:var(--red)"><strong>Error</strong> — ' + esc(e.message) + '</p>';
  }

  busy = false;
}

/**
 * Show a clean plain-text preview during streaming.
 * Strips markdown syntax so the user sees readable text,
 * not raw ## headers and **bold** markers.
 */
function showStreamingPreview(bubble, rawText) {
  let clean = rawText;
  // Strip markdown headers
  clean = clean.replace(/^#{1,4}\s+/gm, '');
  // Strip bold markers
  clean = clean.replace(/\*\*(.+?)\*\*/g, '$1');
  // Strip italic markers
  clean = clean.replace(/\*(.+?)\*/g, '$1');
  // Strip bullet markers
  clean = clean.replace(/^[\-\*]\s+/gm, '  \u2022 ');
  // Convert newlines to <br>
  clean = clean.replace(/\n/g, '<br>');
  bubble.innerHTML = clean;
  scrollDown();
}

// ── Session management ──

async function loadChatSession(id) {
  try {
    const session = await apiGetSession(id);
    activeChatId = id;

    const inner = document.getElementById('chatInner');
    inner.innerHTML = '';

    for (const msg of session.messages) {
      if (msg.role === 'user') {
        const fileNames = (msg.files_json || []).map(f => f.name).filter(Boolean);
        userMsg(msg.content, fileNames.map(n => ({ name: n })));
      } else {
        aiMsg(msg.content);
      }
    }

    showChat();
    await loadRecentChats();
    scrollDown();
  } catch (e) {
    showToast('Failed to load chat: ' + e.message);
  }
}

async function loadRecentChats() {
  const container = document.getElementById('recentList');
  if (!container) return;

  try {
    const sessions = await apiListSessions(activeProjectId);

    if (!sessions.length) {
      container.innerHTML = '<div class="sb-item" style="color:var(--text-dim);font-size:0.78rem;pointer-events:none">No recent chats</div>';
      return;
    }

    container.innerHTML = sessions.slice(0, 10).map(s =>
      '<div class="sb-item ' + (s.id === activeChatId ? 'active' : '') + '" onclick="loadChatSession(' + s.id + ')">' +
        '<span class="sb-icon" style="color:var(--text-dim)">-</span>' +
        '<span class="sb-text">' + esc(s.title) + '</span>' +
        '<button class="sb-delete" onclick="event.stopPropagation();deleteChat(' + s.id + ')" title="Delete chat">×</button>' +
      '</div>'
    ).join('');
  } catch (e) {
    container.innerHTML = '<div class="sb-item" style="color:var(--text-dim);font-size:0.78rem;pointer-events:none">Log in to see chats</div>';
  }
}

function renderRecentChats() { loadRecentChats(); }

async function deleteChat(sessionId) {
  try {
    await apiDeleteSession(sessionId);
    if (activeChatId === sessionId) {
      activeChatId = null;
      newChat();
    }
    await loadRecentChats();
    showToast('Chat deleted');
  } catch (e) {
    showToast('Failed to delete chat');
  }
}

// ── DOM rendering ──

function createEmptyAiMsg() {
  const c = document.getElementById('chatInner');
  const d = document.createElement('div');
  d.className = 'msg ai';
  d.innerHTML = '<div class="msg-av ai"></div><div class="msg-body"><div class="bubble"></div></div>';
  c.appendChild(d);
  scrollDown();
  return d.querySelector('.bubble');
}

function userMsg(t, f) {
  const c = document.getElementById('chatInner');
  const d = document.createElement('div');
  d.className = 'msg user';
  var fh = '';
  if (f && f.length) {
    fh = '<div class="msg-files">' + f.map(function(x) { return '<span class="msg-ftag">' + (x.name || x) + '</span>'; }).join('') + '</div>';
  }
  d.innerHTML = '<div class="msg-av human">' + (currentUserInitials || 'U') + '</div><div class="msg-body"><div class="bubble">' + fh + (t ? esc(t) : '<span style="opacity:0.6">Analyze uploaded document(s)</span>') + '</div></div>';
  c.appendChild(d);
  scrollDown();
}

function aiMsg(html) {
  const c = document.getElementById('chatInner');
  const d = document.createElement('div');
  d.className = 'msg ai';
  d.innerHTML = '<div class="msg-av ai"></div><div class="msg-body"><div class="bubble">' + sanitizeHTML(html) + '</div></div>';
  c.appendChild(d);
  requestAnimationFrame(scrollDown);
}

function showSearchingInline(bubble) {
  var srcs = ['Procore', 'Autodesk', 'Sage 300', 'Primavera', 'Email'].sort(function() { return Math.random() - 0.5; });
  var el = document.createElement('div');
  el.innerHTML =
    '<div class="searching-bar"><span>Searching</span>' +
    srcs.slice(0, 3).map(function(s, i) { return '<span class="s-tag" style="animation-delay:' + (i * 0.12) + 's">' + s + '</span>'; }).join('') +
    '</div><div class="dots"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>';
  bubble.appendChild(el);
  scrollDown();
  return el;
}

function welcomeHTML() {
  return '<div class="welcome" id="welcomeScreen">' +
    '<div class="welcome-mark"></div>' +
    '<h2>What can I find for you?</h2>' +
    '<p>Ask anything about your project. I will search across all your connected tools and documents.</p>' +
    '<div class="starters">' +
      '<div class="starter" onclick="ask(\'Are there any open RFIs that could delay the concrete pour on Level 12?\')"><div class="starter-icon">' + IC.search + '</div><div><div class="starter-title">Open RFI risks</div><div class="starter-sub">Check for RFIs impacting schedule</div></div></div>' +
      '<div class="starter" onclick="ask(\'What is the current budget status?\')"><div class="starter-icon">' + IC.dollar + '</div><div><div class="starter-title">Budget status</div><div class="starter-sub">Cost performance and variances</div></div></div>' +
      '<div class="starter" onclick="ask(\'Summarize all submittals pending review\')"><div class="starter-icon">' + IC.doc + '</div><div><div class="starter-title">Pending submittals</div><div class="starter-sub">Outstanding submittals by status</div></div></div>' +
      '<div class="starter" onclick="ask(\'What are the top 3 schedule risks for next month?\')"><div class="starter-icon">' + IC.warn + '</div><div><div class="starter-title">Schedule risks</div><div class="starter-sub">AI-detected risks to milestones</div></div></div>' +
    '</div></div>';
}

function scrollDown() {
  var s = document.getElementById('chatView');
  s.scrollTop = s.scrollHeight;
}
