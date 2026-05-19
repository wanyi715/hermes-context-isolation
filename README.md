# Hermes Context Isolation

> A lightweight LLM conversation context isolation system — solving memory contamination in multi-project collaboration.

---

## TL;DR

If you juggle multiple projects in a single LLM chat window, you've likely seen AI confuse project A's context into project B's reply. This system achieves **per-project context isolation** through file-level memories + API-level system_message injection — each conversation sees only its own project's context.

---

## The Problem

### Single Window = Memory Contamination

When using WeChat, Feishu, Discord, or any single-window chat to discuss multiple projects, everything gets mixed together:

```
You: "Let's debug the login bug in [Project A]"       ← Project A
AI: Analyzing login logic...

You: "Analyze [Project B]'s latest report"              ← Project B (context switch)
AI: Analyzing... but occasionally mixes in login bug   ← memory contamination
```

**LLM memory is global.** No matter how cleanly you switch topics, everything in the Transformer's context window is visible simultaneously. This isn't a bug — it's a fundamental property of the architecture.

### Multiple Windows = Management Overhead

People open separate chat windows to cope — Project A in WeChat, Project B in Feishu. But this creates new problems:

- Switching between platforms constantly
- Can't reference or compare across windows
- AI toolchains aren't shared
- Conversation history is scattered across platforms

---

## The Insight

The core insight is simple: **don't let the model handle context switching on its own. Do it at the API layer.**

```
┌─────────────────────────────────┐
│           User Entrance          │
│   WeChat / Web / Feishu / TG    │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│      bridge.py (HTTP API)        │
│                                  │
│  Injected before every call:     │
│  ├─ Current project SOUL.md      │
│  └─ Current project MEMORY.md    │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│     LLM API (DeepSeek/etc.)     │
│     ← Only sees current context │
└─────────────────────────────────┘
```

---

## Architecture

![Architecture Diagram](architecture.html)
*Open the [interactive architecture diagram](./architecture.html) in your browser — dark theme, full SVG layout.*

### Component Layout

```
~/.hermes/
├── config.yaml              # Hermes main config
├── hermes-agent/            # Hermes Agent core
├── projects/                # Project directories
│   ├── main/                # Default project
│   ├── alpha/              # Sample project A
│   │   ├── SOUL.md          # Persona definition
│   │   ├── MEMORY.md        # Project memory
│   │   ├── workspace/       # Project working dir
│   │   └── ...              # Project files
│   ├── beta/                # Sample project B
│   ├── gamma/               # Sample project C
│   └── hermes-context-isolation/  # This project
├── sessions/                # Conversation persistence
│   ├── main.json
│   ├── alpha.json
│   └── ...
├── memories/                # Global memory (not for project isolation)
└── skills/                  # Global skills
```

### Per-Project Structure

Each project has three core files:

| File | Purpose | Example Content |
|------|---------|-----------------|
| `SOUL.md` | AI persona and tone | "You are a game system architect... consider trade-offs before answering" |
| `MEMORY.md` | Stable project facts | "Uses pnpm + React 18, SQLite database" |
| `workspace/` | Symlink to real project dir | Points to actual project directory |

### Isolation Mechanism

**Two-layer isolation:**

1. **Layer 1 — API Layer (bridge.py)**: Before every LLM call, reads the current project's `SOUL.md` and `MEMORY.md` from disk and appends them to `system_message`. **Other project files are never read.**
2. **Layer 2 — Application Layer (Hermes Agent)**: Hermes' memory/skills/tools mechanisms operate within the current project context. Data written via the `memory` tool is also isolated per-project.

### Comparison

| Aspect | Single Window | Multiple Windows | This System |
|--------|--------------|-----------------|-------------|
| Context Isolation | ❌ Fully mixed | ✅ Natural (but fragmented) | ✅ Architecture-level |
| Conversation Mgmt | ✅ Single entry | ❌ Platform switching | ✅ Single entry, multi-context |
| Memory Persistence | ❌ Platform-dependent | ❌ Scattered | ✅ Local file persistence |
| AI Toolchain | ❌ None | ❌ Not shared | ✅ Unified toolchain |
| Resource Cost | ✅ Zero | ✅ Zero | Very low (~200MB resident) |

---

## Implementation

*See [`bridge.py`](bridge.py) for full source. Below are the core logic snippets.*

### Step 1: Project Discovery

```python
# Scan ~/.hermes/projects/ for all directories
# Each directory = one independent project
for name in os.listdir(PROJECTS_DIR):
    project = {
        "key": name,
        "memory_count": count_lines(MEMORY.md),
        "soul_count": count_lines(SOUL.md),
    }
```

### Step 2: system_message Injection

```python
# Dynamically build system_message before each /api/chat call
system_msg = "You are an AI assistant..."
if has_soul:
    system_msg += f"\n\n## Persona\n{read_file(soul_path)}"
if has_memory:
    system_msg += f"\n\n## Project Memory\n{read_file(memory_path)}"
```

### Step 3: Conversation Persistence

Conversation history is saved per-project to `~/.hermes/sessions/{project_key}.json`. bridge.py auto-restores the latest session on restart.

---

## Deployment

### Prerequisites

- Python 3.10+
- Hermes Agent (see [Hermes Agent Docs](https://hermes-agent.nousresearch.com/docs))

### Start bridge

```bash
# Option 1: Direct run
python bridge.py

# Option 2: systemd user service (recommended)
systemctl --user enable hermes-bridge
systemctl --user start hermes-bridge
```

### nginx Reverse Proxy

```nginx
# Frontend
location /chat/ {
    alias /var/www/html/chat/;
    try_files $uri $uri/ /chat/index.html;
}
# API
location /api/ {
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $host;
}
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | Send a message |
| `/api/projects` | GET | List all projects |
| `/api/switch-project` | POST | Switch current project |
| `/api/health` | GET | Health check |

---

## Status

This project is in **production validation**. The core isolation mechanism has been running stably on the author's production environment (1.6GB RAM VPS), passing two rounds of verification:

- ✅ SOUL and MEMORY are fully isolated between projects
- ✅ bridge's system_message injection doesn't leak other project content
- ✅ Dual entrance (Web + WeChat) uses the same isolation logic
- ✅ Conversation persistence survives bridge restarts

---

## License

MIT © [wanyi715](https://github.com/wanyi715)

---

*Building a multi-project LLM collaboration system? I'd love to hear your approach.*
