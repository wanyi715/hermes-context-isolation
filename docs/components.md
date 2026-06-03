# Component List — Hermes Context Isolation

## Core Components

### 1. bridge.py (HTTP API Server)
**Port:** 8765 (production), 8766 (test)
**Role:** HTTP API server handling all chat requests, topic management, project switching, and context injection.

**Key Functions:**
- `POST /api/chat` — Send message, get response (supports polling and streaming/SSE)
- `GET /api/messages` — Fetch session messages (supports project and topic_id parameters)
- `POST /api/topics` — Create/delete/update topics (global, independent)
- `GET /api/topics` — List all topics
- `POST /api/switch-project` — Switch active project context
- `POST /api/create-project` — Create new project
- `POST /api/delete-project` — Delete project
- `POST /api/memory` — CRUD for white-box memory items (add/edit/delete/pin)
- `GET /api/memory` — List memory items for a project
- `GET /api/context` — Get context data for UI display (files, memory, skills)
- `GET /api/search` — Full-text search across session messages and memory
- `GET /api/download` — Download a file by path (RFC 5987 encoding for non-ASCII filenames)
- `GET /api/session-history` — List session history
- `GET /api/session-messages` — Load messages from a session file
- `GET /api/health` — Health check

**Session Key Architecture:**
- `"main"` — Main window (global persona)
- `"project-name"` — Project (per-project SOUL + MEMORY)
- `"topic-{id}"` — Topic (independent, lightweight)

### 2. ProjectManager (in-memory state)
**Role:** Manages all project and topic state, session persistence, and context injection.

**Key Data Structures:**
- `self.projects` — Dict of project info (name, memory, soul, skills, workspace)
- `self.sessions` — Dict of session data (agent, messages) keyed by session key
- `self.topics` — Global list of topic dicts (id, title, createdAt)
- `self.session_files` — Dict of session-generated files (persisted to disk)

**Persistence:**
- Project data: `~/.hermes/projects/{project}/` (SOUL.md, MEMORY.md, memory.json, workspace/)
- Topic data: `~/.hermes/projects/_topics/` (topics.json, topic_{id}_chat.json, topic_{id}_files.json, topic_{id}_archive/)
- Session files: `{project_dir}/{key}_files.json` — survives restarts

### 3. AIAgent (LLM Interaction)
**Role:** Wraps the LLM API for conversation handling.
**Model:** mimo-v2.5-pro (via MiMo API, base_url: api.xiaomimimo.com/v1)

**Key Features:**
- Injects SOUL + MEMORY into ephemeral_system_prompt (per project)
- Boundary reminders enforce project scope
- Vision support: mimo-v2.5 for image description (HEIF→JPEG conversion via pillow_heif)
- Skip global memory (skip_memory=True) — project memory injected separately
- Max 40 iterations per conversation turn

### 4. Frontend (index.html)
**Role:** Web UI for chat interface with three-layer architecture.

**Key Features:**
- Three-layer sidebar (Main Window, Projects, Topics)
- Independent topic management (create, delete, switch)
- Real-time SSE streaming with markdown rendering
- Context panel: memory CRUD, file list with download, skill viewer
- HEIF→JPEG canvas conversion for Safari browsers
- Search across sessions
- localStorage for UI state (per-environment prefix)

### 5. Nginx (Reverse Proxy)
**Role:** Routing, basic auth, static file serving.

**Configuration (see nginx.conf.example):**
- `/chat/` → Static files (index.html)
- `/api/` → Port 8765 (production)
- `/api_test/` → Port 8766 (test, rewritten to /api/)
- `/files/` → Static file browser with auth

## File Structure

```
~/.hermes/projects/
├── _topics/                         # Global topic storage
│   ├── topics.json                  # Topic list
│   ├── topic-t_{id}_chat.json       # Topic chat history
│   ├── topic-t_{id}_files.json      # Topic associated files (persisted)
│   └── topic-t_{id}_archive/        # Topic message archives
├── main/                            # Main window
│   ├── SOUL.md                      # Concierge persona
│   ├── MEMORY.md                    # Project registry + global facts
│   ├── memory.json                  # Structured memory items
│   ├── chat_history.json            # Recent messages
│   └── workspace/ → ~/              # Home directory
├── hermes-context-isolation/        # This project
│   ├── bridge.py                    # HTTP API server
│   ├── index.html                   # Web Chat frontend
│   ├── SOUL.md / MEMORY.md          # Project config
│   └── docs/                        # Documentation
│       ├── adr/001-three-layer-isolation.md
│       └── components.md
└── [other projects...]              # Each has SOUL.md + MEMORY.md
```

## Context Injection Flow

```
User message → bridge.py
  ↓
1. Identify session (main / project / topic)
  ↓
2. Build ephemeral_system_prompt:
   - Identity boundary ("你正在 X 项目窗口内")
   - SOUL.md (persona + rules)
   - MEMORY.md (project facts)
   - Skills list (if available)
  ↓
3. Call LLM API (mimo-v2.5-pro) with:
   - ephemeral_system_prompt
   - conversation history
   - available tools
  ↓
4. Detect file paths in response → persist to session_files
  ↓
5. Save messages to chat_history.json
```

## Deployment

**Prerequisites:**
- Python 3.11+
- MiMo API key (in .env)
- pillow_heif (for HEIF image conversion)

**Start:**
```bash
python bridge.py  # reads PORT from env, default 8765
```

**Environment (.env):**
```
HERMES_MODEL=mimo-v2.5-pro
MIMO_API_KEY=sk-xxxxx
HERMES_BASE_URL=https://api.xiaomimimo.com/v1
```
