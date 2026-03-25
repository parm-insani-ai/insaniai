/* ═══════════════════════════════════════════════
   API — Backend API client (integrated).
   
   Handles auth tokens (access + refresh), all
   API calls, and automatic token refresh on 401.
   ═══════════════════════════════════════════════ */

const API_BASE = 'http://localhost:8000';

// ── Token storage ──
let accessToken = null;
let refreshToken = null;

function setTokens(access, refresh) {
  accessToken = access;
  if (refresh) refreshToken = refresh;
}
function getAccessToken() { return accessToken; }
function clearTokens() { accessToken = null; refreshToken = null; }

/**
 * Core fetch wrapper. Attaches JWT, handles backend error format,
 * and automatically refreshes expired access tokens.
 */
async function apiFetch(path, options = {}, isRetry = false) {
  const headers = {
    'Content-Type': 'application/json',
    ...(accessToken ? { 'Authorization': `Bearer ${accessToken}` } : {}),
    ...options.headers
  };

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

  // Auto-refresh on 401 (expired access token)
  if (res.status === 401 && refreshToken && !isRetry) {
    const refreshed = await tryRefreshToken();
    if (refreshed) {
      return apiFetch(path, options, true); // Retry with new token
    }
    clearTokens();
    showAuthScreen();
    throw new Error('Session expired. Please log in again.');
  }

  if (!res.ok) {
    // Backend returns {"error": {"code": "...", "message": "..."}}
    const body = await res.json().catch(() => null);
    const msg = body?.error?.message || body?.detail || `Error ${res.status}`;
    throw new Error(msg);
  }

  return res.json();
}

/** Try to refresh the access token using the refresh token. */
async function tryRefreshToken() {
  try {
    const res = await fetch(`${API_BASE}/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken })
    });
    if (!res.ok) return false;
    const data = await res.json();
    accessToken = data.access_token;
    return true;
  } catch {
    return false;
  }
}

// ═══ AUTH ═══

async function apiSignup(email, password, name, orgName) {
  const data = await apiFetch('/v1/auth/signup', {
    method: 'POST',
    body: JSON.stringify({ email, password, name, org_name: orgName })
  });
  setTokens(data.access_token, data.refresh_token);
  return data;
}

async function apiLogin(email, password) {
  const data = await apiFetch('/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password })
  });
  setTokens(data.access_token, data.refresh_token);
  return data;
}

async function apiLogout() {
  if (refreshToken) {
    try {
      await apiFetch('/v1/auth/logout', {
        method: 'POST',
        body: JSON.stringify({ refresh_token: refreshToken })
      });
    } catch { /* ignore — we're logging out anyway */ }
  }
  clearTokens();
}

async function apiGetMe() {
  return apiFetch('/v1/auth/me');
}

// ═══ PROJECTS ═══

async function apiListProjects() {
  return apiFetch('/v1/projects');
}

async function apiGetProject(projectId) {
  return apiFetch(`/v1/projects/${projectId}`);
}

async function apiCreateProject(name, type, location, dataJson) {
  return apiFetch('/v1/projects', {
    method: 'POST',
    body: JSON.stringify({ name, type, location, data_json: dataJson })
  });
}

// ═══ CHAT ═══

async function apiListSessions(projectId = null) {
  const query = projectId ? `?project_id=${projectId}` : '';
  return apiFetch(`/v1/chat/sessions${query}`);
}

async function apiGetSession(sessionId) {
  return apiFetch(`/v1/chat/sessions/${sessionId}`);
}

async function apiDeleteSession(sessionId) {
  return apiFetch(`/v1/chat/sessions/${sessionId}`, { method: 'DELETE' });
}

// ═══ AI ═══

async function apiAskStream(message, projectId, sessionId, files, callbacks) {
  const res = await fetch(`${API_BASE}/v1/ai/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(accessToken ? { 'Authorization': `Bearer ${accessToken}` } : {}),
    },
    body: JSON.stringify({
      message,
      project_id: projectId,
      session_id: sessionId,
      files: files || []
    })
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.error?.message || `Error ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();

    let eventType = null;
    for (const line of lines) {
      if (line.startsWith('event: ')) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith('data: ') && eventType) {
        try {
          const data = JSON.parse(line.slice(6));
          if (eventType === 'session' && callbacks.onSession) callbacks.onSession(data);
          else if (eventType === 'token' && callbacks.onToken) callbacks.onToken(data);
          else if (eventType === 'done' && callbacks.onDone) callbacks.onDone(data);
          else if (eventType === 'error' && callbacks.onError) callbacks.onError(data);
        } catch { /* skip malformed */ }
        eventType = null;
      }
    }
  }
}

/** Non-streaming fallback */
async function apiAsk(message, projectId, sessionId = null, files = []) {
  return apiFetch('/v1/ai/ask', {
    method: 'POST',
    body: JSON.stringify({ message, project_id: projectId, session_id: sessionId, files })
  });
}

function fmtResp(r) {
  let h = r;
  h = h.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  h = h.replace(/\n\n/g, '<br><br>');
  h = h.replace(/\n/g, '<br>');
  return h;
}
