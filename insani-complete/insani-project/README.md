# insani — Construction AI Copilot

> A chat-first AI interface that connects siloed construction data (Procore, Autodesk, Sage, email, drawings) into one conversation. Users ask questions in plain English and get citation-backed answers about projects, risks, and operations.

---

## Quick Start

Open `index.html` in a browser. The app calls the Claude API for AI responses — no build step, no server, no dependencies beyond the browser.

---

## Project Structure

```
insani/
├── index.html              ← Entry point. Loads CSS + JS, contains all HTML
├── css/
│   ├── tokens.css          ← Design tokens (colors, fonts, spacing, radii)
│   ├── layout.css          ← Page skeleton: sidebar, main, topbar, chat area
│   ├── components.css      ← Reusable pieces: buttons, badges, pills, cards
│   ├── chat.css            ← Chat-specific: messages, bubbles, citations, typing
│   ├── panels.css          ← Overlay panels: notifications, source modal, user menu
│   └── responsive.css      ← Breakpoint overrides (≤900px)
├── js/
│   ├── data.js             ← Project database, source configs, notification seed data
│   ├── api.js              ← Claude API call, system prompt builder, response formatter
│   ├── chat.js             ← Send/receive messages, session management, recent chats
│   ├── files.js            ← File upload, drag-and-drop, chip rendering
│   ├── panels.js           ← Notifications, source detail, user menu, toasts
│   ├── nav.js              ← Sidebar toggle, project picker, view switching, dashboard
│   └── main.js             ← Boot sequence, keyboard shortcuts, initialization
├── docs/
│   └── ARCHITECTURE.md     ← This file (you are here)
└── README.md               ← This file
```

---

## Architecture Overview

### Design Philosophy

- **Chat-first.** The conversation is the entire product, not a feature inside a dashboard. The chat area takes center stage with a centered 740px column.
- **Sidebar is navigation, not content.** It holds project switching, recent chat history, and connected source status. It collapses to give the chat full width.
- **Light theme for construction.** Users range from PMs at desks to superintendents on job sites in sunlight. Light backgrounds are readable everywhere.

### Data Flow

```
User types question
        ↓
chat.js → send()
        ↓
api.js → callAI() builds multimodal message
        ↓  (includes file attachments if any)
Claude API (/v1/messages)
        ↓
api.js → fmtResp() converts markdown→HTML
        ↓
chat.js → aiMsg() renders in DOM
        ↓
chat.js → saves session to chatSessions[]
```

### State Management

All state lives in plain JS variables (no framework):

| Variable         | Type     | Purpose                                      |
|-----------------|----------|----------------------------------------------|
| `proj`          | string   | Current project key ('midtown', 'harbor'...)  |
| `history`       | array    | Claude API conversation history (last 20 msgs)|
| `busy`          | boolean  | Prevents double-sends while API is in-flight  |
| `files`         | array    | Pending file attachments (base64 + metadata)  |
| `chatSessions`  | array    | All saved chat sessions with HTML + history   |
| `activeChatId`  | number   | Which session is currently displayed          |

### File-by-File Guide

#### `css/tokens.css`
Design tokens only. Every color, font, radius, and shadow used across the app. Change the brand here and it cascades everywhere.

#### `css/layout.css`
The page skeleton — sidebar (collapsible, 260px), main column (flex: 1), topbar (52px), chat scroll area, input area pinned to bottom. No component-level styling here.

#### `css/components.css`
Reusable UI pieces that appear in multiple contexts: `.sb-item` (sidebar items), `.top-btn` (header buttons), `.project-pill`, `.starter` (quick action cards), `.dash-card`, `.da` (dashboard alerts), badges, file chips.

#### `css/chat.css`
Everything inside the chat column: message rows, avatars, bubbles (user vs AI), citation tags, risk boxes, action boxes, typing dots, searching animation, welcome screen.

#### `css/panels.css`
Overlay/dropdown panels that sit on top of the main UI: notification dropdown, source detail modal, user settings menu, toast notifications. Each has `.open` class toggle.

#### `css/responsive.css`
Single breakpoint at 900px. Shows hamburger menu, adjusts dashboard grid to 2 columns.

#### `js/data.js`
The simulated project database. In production, this would be replaced by API calls to Procore/Autodesk/Sage. Also contains source connection metadata and seed notification data.

#### `js/api.js`
- `sysPrompt()` — builds the Claude system prompt with current project data injected
- `callAI(msg, files)` — sends message + optional file attachments to Claude API, manages conversation history
- `fmtResp(raw)` — converts markdown bold/newlines to HTML

#### `js/chat.js`
- `send()` — main send handler: captures input + files, creates session, calls API, renders response
- `ask(text)` — programmatic send (used by quick actions, dashboard alerts, notifications)
- `userMsg()` / `aiMsg()` — DOM rendering for messages
- `searching()` — animated "searching sources" indicator
- `createChatSession()` / `loadChatSession()` / `renderRecentChats()` — session persistence
- `welcomeHTML()` — generates the welcome screen with starter cards

#### `js/files.js`
- `addFile()` — reads file to base64, detects MIME type
- `chips()` — renders file preview chips above input
- Drag-and-drop listeners for the full-page drop zone

#### `js/panels.js`
- `toggleNotifications()` / `renderNotifications()` / `markAllRead()` — notification dropdown
- `showSourceDetail(key)` — source connection modal
- `toggleUserMenu()` — user settings menu
- `showToast(msg)` — ephemeral feedback toasts
- `closeAllPanels()` — ensures only one panel is open at a time

#### `js/nav.js`
- `toggleSidebar()` — collapse/expand sidebar
- `showChat()` / `showDash()` — view switching
- `selectProject()` / `pickProject()` / `toggleProjectMenu()` — project picker
- `dashCardClick(topic)` — handles clicks on dashboard stat cards
- `newChat()` — saves current session, resets to welcome screen

#### `js/main.js`
- Boot sequence animation
- Keyboard shortcut listener (Enter to send)
- Initial render calls (notifications, recent chats, badge count)
- Utility functions: `resize()`, `esc()`

---

## Key Conventions

1. **No build step.** Plain HTML/CSS/JS. Files load via `<link>` and `<script>` tags.
2. **No framework.** Vanilla JS with direct DOM manipulation. Keeps the bundle at zero and the cognitive overhead low.
3. **CSS variables for everything.** All colors, fonts, radii defined in `tokens.css`. Never hardcode a hex value in component CSS.
4. **`.open` / `.vh` class toggles.** Panels and views show/hide by adding/removing CSS classes, not by manipulating `style.display` directly.
5. **`esc()` for user input.** Any user-provided text rendered into the DOM goes through `esc()` to prevent XSS.
6. **Session-based chat.** Each conversation is a session object with its own history, HTML snapshot, and metadata. Switching between chats restores the full state.

---

## Replacing Simulated Data with Real APIs

The `js/data.js` file contains the `DB` object with hardcoded project data. To connect to real construction APIs:

1. Replace `DB` lookups with `fetch()` calls to your backend
2. The `sysPrompt()` function in `api.js` injects project data into the Claude prompt — swap the `JSON.stringify(DB[proj])` with your API response
3. Notifications should come from a WebSocket or polling endpoint
4. Source sync status should ping each integration's health endpoint

The rest of the app (chat, UI, sessions) works the same regardless of where the data comes from.
