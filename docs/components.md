# Component List — Hermes Context Isolation

## Core Components

### 1. bridge.py (HTTP API Server)
**Port:** 8765 (production), 8766 (test)
**Role:** HTTP API server handling all chat requests, topic management, project switching, and context injection.

**Key Functions:**
- `POST /api/chat` — Send message, get response (supports polling and streaming)
- `GET /api/messages` — Fetch session messages (supports project and topic_id parameters)
- `POST /api/topics` — Create/delete/update topics (global, independent)
- `GET /api/topics` — List all topics
- `POST /api/switch-project` — Switch active project context
- `POST /api/memory` — CRUD for white-box memory items

**Session Key Architecture:**
- `"main"` — Main window (global persona)
- `"project-name"` — Project (per-project SOUL + MEMORY)
- `"topic-{id}"` — Topic (independent, lightweight)

### 2. ProjectManager (in-memory state)
**Role:** Manages all project and topic state, session persistence, and context injection.

**Key Data Structures:**
- `self.projects` — Dict of project info (name, memory, soul, skills, workspace)
- `self.sessions` — Dict of session data (agent, messages) keyed by session key
- `self.topics` — Global list of topic dicts (id, name, createdAt)
- `self.session_files` — Dict of session-generated files

**Persistence:**
- Project data: `~/.hermes/projects/{project}/` (SOUL.md, MEMORY.md, memory.json, workspace/)
- Topic data: `~/.hermes/projects/_topics/` (topics.json, topic_{id}_chat.json, topic_{id}_archive/)

### 3. AIAgent (LLM interaction)
**Role:** Wraps the LLM API (DeepSeek Chat) for conversation handling.

**Key Features:**
- Injects SOUL + MEMORY into system_message (for projects)
- Supports boundary reminders (for topics and projects)
- Handles conversation history and context management

### 4. Nginx (Reverse Proxy)
**Role:** SSL termination, basic auth, and routing.

**Configuration:**
- `/chat/` → Port 8765 (production)
- `/chat_test/` → Port 8766 (test)
- `/api/` → Port 8765 (production)
- `/api_test/` → Port 8766 (test)

### 5. Frontend (index.html)
**Role:** Web UI for chat interface.

**Key Features:**
- Three-layer sidebar (Main Window, Projects, Topics)
- Independent topic management (create, delete, switch)
- Real-time message rendering with markdown support
- Context panel showing memory, files, skills

## File Structure

```
~/.hermes/projects/
├── _topics/                    # Global topic storage
│   ├── topics.json             # Topic list
│   ├── topic-t_123_chat.json   # Topic chat history
│   └── topic-t_123_archive/    # Topic message archives
├── main/                       # Main window
│   ├── SOUL.md
│   ├── MEMORY.md
│   ├── memory.json
│   ├── chat_history.json
│   └── workspace/ → ~/
├── hermes-context-isolation/   # This project
│   ├── SOUL.md
│   ├── MEMORY.md
│   ├── bridge.py               # HTTP API server
│   └── docs/
│       └── adr/                # Architecture Decision Records
│           └── 001-three-layer-isolation.md
└── [other projects...]
```

## API Endpoints

### Chat
- `POST /api/chat` — Send message (mode: "poll" or "stream")
- `GET /api/task/{task_id}` — Poll for task result
- `GET /api/messages?project={key}&topic_id={id}` — Fetch messages

### Topics (Global, Independent)
- `POST /api/topics` — Create/delete/update topics
- `GET /api/topics` — List all topics

### Projects
- `POST /api/switch-project` — Switch active project
- `GET /api/projects` — List all projects
- `POST /api/create-project` — Create new project
- `POST /api/delete-project` — Delete project

### Memory
- `POST /api/memory` — CRUD for memory items
- `GET /api/memory?project={key}` — List memory items

### Context
- `GET /api/context?topic_id={id}` — Get context for UI display
- `GET /api/search?q={query}&project={key}` — Search messages

## Deployment

**Production:**
- Port 8765 (Nginx: `/chat/`, `/api/`)
- Systemd user service (optional)
- WeChat integration via iLink Bot API

**Test:**
- Port 8766 (Nginx: `/chat_test/`, `/api_test/`)
- Same codebase as production
- Independent session state
