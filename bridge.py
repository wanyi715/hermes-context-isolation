#!/usr/bin/env python3
"""Hermes Chat Bridge — HTTP API with project-level context isolation.

POST /api/chat          body: {"message": "..."}  → {"response": "..."}
POST /api/switch-project body: {"project": "key"}  → {"project": "...", "memory_count": N}
GET  /api/projects       → {"projects": [...], "current": "..."}
GET  /api/health         → {"status":"ok"}
"""

import json, sys, os, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

# Load .env so tools like web_extract can find API keys (FIRECRAWL_API_KEY, etc.)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.expanduser("~/.hermes/.env"))
except ImportError:
    pass

sys.path.insert(0, os.path.expanduser("~/.hermes/hermes-agent"))
from run_agent import AIAgent

PROJECTS_DIR = os.path.expanduser("~/.hermes/projects")
GLOBAL_MEMORY = os.path.expanduser("~/.hermes/memories/MEMORY.md")
GLOBAL_SKILLS = os.path.expanduser("~/.hermes/skills")

MODEL = os.environ.get("HERMES_MODEL", "deepseek-chat")
PROVIDER = "custom"
BASE_URL = "https://api.deepseek.com"
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# ── Project manager ────────────────────────────────────────────
class ProjectManager:
    def __init__(self):
        self.projects = {}
        self.sessions = {}  # key -> {"agent": AIAgent, "messages": [...]}
        self.current = "main"
        self._sessions_dir = os.path.expanduser("~/.hermes/sessions")
        os.makedirs(self._sessions_dir, exist_ok=True)
        self._load_projects()

    def _session_path(self, key):
        return os.path.join(self._sessions_dir, f"{key}.json")

    def _topics_path(self, key):
        return os.path.join(self._sessions_dir, f"topics_{key}.json")

    def _topic_msgs_path(self, topic_id):
        return os.path.join(self._sessions_dir, f"topic_msgs_{topic_id}.json")

    def save_topic_messages(self, topic_id, messages):
        """Persist topic messages to disk for cross-device sync."""
        try:
            with open(self._topic_msgs_path(topic_id), "w") as f:
                json.dump(messages[-100:], f, ensure_ascii=False)
            return True
        except Exception:
            return False

    def load_topic_messages(self, topic_id):
        """Load topic messages from disk."""
        tp = self._topic_msgs_path(topic_id)
        if not os.path.exists(tp):
            return []
        try:
            with open(tp) as f:
                return json.load(f)
        except Exception:
            return []

    def _save_session(self, key):
        """Persist session messages to disk so they survive bridge restarts."""
        if key not in self.sessions:
            return
        try:
            msgs = self.sessions[key]["messages"][-200:]  # keep last 200
            with open(self._session_path(key), "w") as f:
                json.dump(msgs, f, ensure_ascii=False)
        except Exception:
            pass

    def _load_session(self, key):
        """Restore session messages from disk."""
        try:
            sp = self._session_path(key)
            if os.path.exists(sp):
                with open(sp) as f:
                    return json.load(f)
        except Exception:
            pass
        return []

    def _load_projects(self):
        """Scan ~/.hermes/projects/ for project directories."""
        if not os.path.isdir(PROJECTS_DIR):
            return
        for name in os.listdir(PROJECTS_DIR):
            path = os.path.join(PROJECTS_DIR, name)
            if not os.path.isdir(path):
                continue
            # Skip tracking/hidden dirs
            if name.startswith(".") or name == "hermes-context-isolation":
                continue
            memory_path = os.path.join(path, "MEMORY.md")
            soul_path = os.path.join(path, "SOUL.md")
            skills_path = os.path.join(path, "skills")
            workspace_path = os.path.join(path, "workspace")

            # Extract Chinese display name from SOUL.md (e.g. "# SOUL — 半导体财报分析")
            display_name = name.replace("-", " ").title()
            soul_content = self._read_file(soul_path)
            if soul_content:
                first_line = soul_content.split("\n")[0]
                if "—" in first_line:
                    display_name = first_line.split("—", 1)[1].strip()
                elif " - " in first_line:
                    display_name = first_line.split(" - ", 1)[1].strip()

            self.projects[name] = {
                "name": display_name,
                "memory": self._read_file(memory_path),
                "soul": self._read_file(soul_path),
                "skills": skills_path if os.path.isdir(skills_path) else None,
                "workspace": workspace_path if os.path.isdir(workspace_path) else None,
                "memory_lines": self._count_lines(memory_path),
            }
        print(f"[bridge] Loaded {len(self.projects)} projects: {list(self.projects.keys())}")

    def _read_file(self, path):
        if path and os.path.isfile(path):
            with open(path) as f:
                return f.read().strip()
        return ""

    def _count_lines(self, path):
        if path and os.path.isfile(path):
            return len(open(path).readlines())
        return 0

    def switch_to(self, project_key):
        """Switch active project. Returns project info dict."""
        if project_key == "main":
            self.current = "main"
            # Ensure main session exists and restore from disk
            self.get_agent("main")
            return {"project": "main", "name": "主窗口", "memory_count": 0}

        if project_key not in self.projects:
            return {"error": f"Unknown project: {project_key}"}

        self.current = project_key
        # Trigger session creation and restore from disk
        self.get_agent(project_key)
        info = self.projects[project_key]
        return {
            "project": project_key,
            "name": info["name"],
            "memory_count": info.get("memory_lines", 0),
            "has_skills": info["skills"] is not None,
            "has_workspace": info["workspace"] is not None,
        }

    def get_agent(self, project_key=None):
        """Get or create AIAgent for a project."""
        key = project_key or self.current
        if key not in self.sessions:
            # Restore messages from disk if available
            saved_msgs = self._load_session(key)
            self.sessions[key] = {
                "agent": self._create_agent(key),
                "messages": saved_msgs,
            }
            if saved_msgs:
                print(f"[bridge] Restored {len(saved_msgs)} messages for {key}")
        return self.sessions[key]["agent"]

    def _create_agent(self, project_key):
        """Create a fresh AIAgent with project-specific context."""
        info = self.projects.get(project_key, {})
        soul = info.get("soul", "")
        memory = info.get("memory", "")

        # Build project context for ephemeral system prompt
        context_parts = []
        if soul:
            context_parts.append(soul)
        if memory:
            context_parts.append("【项目记忆——请优先使用以下信息回答：】\n" + memory)
        context = "\n\n".join(context_parts) if context_parts else None

        agent = AIAgent(
            model=MODEL,
            provider=PROVIDER,
            base_url=BASE_URL,
            api_key=API_KEY,
            max_iterations=30,
            skip_memory=True,  # Skip global memory, we inject project memory
            ephemeral_system_prompt=context,
            disabled_toolsets=[],
        )
        return agent

    def add_message(self, role, text, project_key=None):
        key = project_key or self.current
        if key not in self.sessions:
            self.get_agent(key)  # ensure session exists
        self.sessions[key]["messages"].append({"role": role, "content": text})
        # Trim to last 30 messages to prevent memory bloat
        if len(self.sessions[key]["messages"]) > 30:
            self.sessions[key]["messages"] = self.sessions[key]["messages"][-30:]
        # Persist to disk so sessions survive bridge restarts
        self._save_session(key)

    def get_context_data(self, project_key=None):
        """Return structured context for UI display."""
        key = project_key or self.current
        if key == "main" or key not in self.projects:
            return {"project": "main", "memory_items": [], "files": [], "skills": []}
        info = self.projects[key]
        # Parse memory into items (split by § or blank lines)
        memory_text = info.get("memory", "")
        raw_items = [s.strip() for s in memory_text.replace("\n§", "\n\n").split("\n\n") if s.strip()]
        memory_items = [{"text": item[:200] + ("…" if len(item) > 200 else "")} for item in raw_items[:10]]
        # Scan workspace files
        files = self._list_files(info.get("workspace")) if info.get("workspace") else []
        # Scan skills
        skills = self._list_files(info.get("skills")) if info.get("skills") else []
        return {
            "project": key,
            "name": info["name"],
            "memory_items": memory_items,
            "files": files,
            "skills": skills,
            "memory_count": info.get("memory_lines", 0),
        }

    def _list_files(self, dir_path):
        """List files in a directory (expand symlinks one level, max 30)."""
        if not dir_path or not os.path.isdir(dir_path):
            return []
        items = []
        for f in os.listdir(dir_path):
            fp = os.path.join(dir_path, f)
            if os.path.isfile(fp):
                size = os.path.getsize(fp)
                items.append({"name": f, "size": size})
            elif os.path.isdir(fp):
                # If it's a symlink, show its contents instead
                if os.path.islink(fp):
                    target = os.path.realpath(fp)
                    if os.path.isdir(target):
                        for sub in sorted(os.listdir(target))[:15]:
                            subp = os.path.join(target, sub)
                            if os.path.isfile(subp):
                                items.append({"name": sub, "size": os.path.getsize(subp)})
                            elif os.path.isdir(subp):
                                items.append({"name": sub + "/", "size": 0, "is_dir": True})
                    else:
                        items.append({"name": f, "size": os.path.getsize(target)})
                else:
                    items.append({"name": f + "/", "size": 0, "is_dir": True})
        return sorted(items, key=lambda x: (not x.get("is_dir", False), x["name"]))[:30]

    def get_context_for_chat(self, project_key=None):
        """Build system context string for the current project."""
        key = project_key or self.current
        if key == "main":
            main_dir = os.path.join(PROJECTS_DIR, "main")
            soul = self._read_file(os.path.join(main_dir, "SOUL.md"))
            memory = self._read_file(os.path.join(main_dir, "MEMORY.md"))
            parts = []
            if soul:
                parts.append(soul)
            if memory:
                parts.append("【主窗口背景知识：】\n" + memory)
            return "\n\n".join(parts)
        info = self.projects.get(key, {})
        parts = []
        soul = info.get("soul", "")
        memory = info.get("memory", "")
        if soul:
            parts.append(soul)
        if memory:
            parts.append("【本项目专属记忆：】\n" + memory)
        return "\n\n".join(parts)


pm = ProjectManager()


def _build_user_message(data):
    """Build user_message from request data.
    If an image is attached, return a multimodal content array
    (Hermes natively supports this — see codex_responses_adapter.py).
    Otherwise return a plain string.
    """
    text = data.get("message", "").strip()
    image = data.get("image")
    if image:
        parts = []
        parts.append({"type": "text", "text": text or "请看这张图片"})
        parts.append({"type": "image_url", "image_url": {"url": image}})
        return parts
    return text


def _message_preview(msg):
    """Safe preview string for logging — handles both str and list."""
    if isinstance(msg, str):
        return msg
    if isinstance(msg, list):
        text = next((p.get("text", "") for p in msg if isinstance(p, dict) and p.get("type") == "text"), "")
        has_img = any(isinstance(p, dict) and p.get("type") == "image_url" for p in msg)
        if has_img:
            return ("🖼 " + text) if text else "🖼 [图片]"
        return text
    return str(msg)


import re as _re
_FILE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".txt", ".md",
                    ".py", ".json", ".csv", ".xlsx", ".docx", ".pptx", ".mp3", ".mp4",
                    ".html", ".zip", ".tar.gz", ".log"}

def _detect_files(text):
    """Scan AI response for file paths the user might want to download.
    Returns a list of {path, name, type} dicts."""
    if not isinstance(text, str):
        return []
    files = []
    seen = set()
    # Match absolute paths under safe directories
    _HOME = os.path.expanduser("~") + "/"
    for m in _re.finditer(r'(/tmp/[\w./-]+\.\w{2,5}|' + _re.escape(_HOME) + r'[\w./-]+\.\w{2,5})', text):
        fp = m.group(0).rstrip('.,;:!?）)')
        if fp in seen:
            continue
        ext = os.path.splitext(fp)[1].lower()
        if ext in _FILE_EXTENSIONS and os.path.isfile(fp):
            seen.add(fp)
            files.append({
                "path": fp,
                "name": os.path.basename(fp),
                "type": "image" if ext in {".png", ".jpg", ".jpeg", ".gif", ".svg"} else "file"
            })
    return files

# ── HTTP Handler ───────────────────────────────────────────────
class ChatHandler(BaseHTTPRequestHandler):
    # Task buffer for polling mode (like iLink Bot's buffer layer)
    tasks = {}  # task_id -> {"status": "processing"|"done"|"error", "response": str, "error": str, "project": str, "created": float}
    _task_lock = threading.Lock()

    # Project display names for LLM routing
    PROJECT_DISPLAY = {
        "alpha": "Alpha 项目", "beta": "Beta 项目", "gamma": "Gamma 项目",
        "hermes-agent": "Hermes Agent"
    }

    def do_OPTIONS(self):
        self._cors(); self.send_response(204); self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try: data = json.loads(body)
        except: self.send_error(400); return

        if path == "/api/chat":
            self._handle_chat(data)
        elif path == "/api/switch-project":
            self._handle_switch(data)
        elif path == "/api/detect-topic":
            self._handle_detect_topic(data)
        elif path == "/api/create-project":
            self._handle_create_project(data)
        elif path == "/api/topics":
            self._handle_topics(data)
        elif path == "/api/topic-messages":
            self._handle_topic_messages(data)
        else:
            self.send_error(404)

    def _handle_topics(self, data):
        """POST /api/topics — save topics to server for cross-device sync.
        Body: {"project": "key", "topics": [...]}"""
        project_key = data.get("project", pm.current)
        topics = data.get("topics", [])
        tp = pm._topics_path(project_key)
        try:
            with open(tp, "w") as f:
                json.dump(topics, f, ensure_ascii=False)
            self._respond_json({"status": "ok", "count": len(topics)})
            print(f"[bridge] Saved {len(topics)} topics for {project_key}")
        except Exception as e:
            self._respond_json({"error": str(e)})

    def _handle_topic_messages(self, data):
        """POST /api/topic-messages — save topic messages to server for cross-device sync.
        Body: {"project": "key", "topicId": "t_xxx", "messages": [...]}"""
        topic_id = data.get("topicId", "")
        if not topic_id:
            self.send_error(400); return
        msgs = data.get("messages", [])
        ok = pm.save_topic_messages(topic_id, msgs)
        self._respond_json({"status": "ok" if ok else "error", "count": len(msgs)})

    def _handle_detect_topic(self, data):
        """Unified intent detection: existing project / new project / new topic / none."""
        messages = data.get("messages", [])
        if len(messages) < 4:
            self._respond_json({"action": "none", "reason": "not enough messages"})
            return

        # Build project list for LLM
        proj_list = []
        for k, v in pm.projects.items():
            cn = self.PROJECT_DISPLAY.get(k, v["name"])
            proj_list.append(f"- {k}: {cn}")
        proj_str = "\n".join(proj_list) if proj_list else "(none)"

        # Build existing topic list
        existing_topics = data.get("existing_topics", [])
        topic_str = "\n".join([f"- {t}" for t in existing_topics]) if existing_topics else "(none)"

        recent = "\n".join([f"- {m['role']}: {m['text'][:200]}" for m in messages[-8:]])
        prompt = f"""Analyze this conversation and decide what to do.

Available projects:
{proj_str}

Existing topics (created by user earlier):
{topic_str}

Return EXACTLY one line in one of these formats (no explanation):
- SWITCH:project_key — if clearly discussing an existing project above
- SWITCH_TOPIC:exact_topic_name — if this conversation belongs to an EXISTING topic (copy the name from the list above)
- PROJECT:name — if this is a new project that needs persistent context
- TOPIC:name — if this is a focused NEW discussion topic (short-term)
- NONE — ONLY if truly casual/greeting/scattered (use sparingly!)

CRITICAL RULES:
- PRIORITY 1: If this conversation is about the SAME subject as an existing topic → SWITCH_TOPIC:copy_exact_name_from_list
- PRIORITY 2: If 3+ messages on ONE subject and NO matching topic → TOPIC:new_name
- If it involves code/files/repeated work → PROJECT
- ONLY use NONE for greetings, jokes, completely scattered chat
- IMPORTANT: "美国国防部长访华" and "中美关系" ARE the same subject — be generous when matching!

Conversation:
{recent}"""

        try:
            agent = pm.get_agent("main")
            result = agent.run_conversation(
                user_message=prompt,
                system_message="You are a conversation router. ONLY output one line: SWITCH:key, PROJECT:name, TOPIC:name, or NONE. No explanation.",
                conversation_history=[],
            )
            raw = result.get("final_response", "NONE").strip()
            self._respond_json(self._parse_intent(raw))
        except Exception as e:
            print(f"[bridge] detect error: {e}")
            self._respond_json({"action": "none", "error": str(e)})

    def _handle_create_project(self, data):
        """Create a new project directory with MEMORY.md and SOUL.md."""
        import re, subprocess
        name = data.get("name", "").strip()
        if not name or len(name) < 2 or len(name) > 30:
            self._respond_json({"error": "Invalid name", "message": "项目名需 2-30 字符"})
            return

        # Generate project key from name (English slug or use AI)
        key = re.sub(r'[^a-z0-9-]', '', name.lower().replace(' ', '-'))[:30]
        if not key or len(key) < 2:
            # Use a hash-based key if name is all-Chinese
            key = "proj-" + str(abs(hash(name)) % 100000)

        proj_dir = os.path.join(PROJECTS_DIR, key)
        if os.path.exists(proj_dir):
            self._respond_json({"error": "exists", "message": f"项目 {key} 已存在"})
            return

        os.makedirs(proj_dir, exist_ok=True)
        os.makedirs(os.path.join(proj_dir, "skills"), exist_ok=True)
        os.makedirs(os.path.join(proj_dir, "workspace"), exist_ok=True)

        # Write MEMORY.md
        mem = f"# {name} 项目记忆\n\n## 创建时间\n{data.get('timestamp', '')}\n\n## 背景\n从主窗口对话中自动创建。\n\n## 待记录\n<!-- 后续对话中的重要信息会自动记录到这里 -->\n"
        with open(os.path.join(proj_dir, "MEMORY.md"), "w") as f:
            f.write(mem)

        # Write SOUL.md
        soul = f"# SOUL — {name}\n\n## 人格\n你是 {name} 项目的专属 AI 助手。\n\n## 口吻\n待根据项目特点定义。\n\n## 关键规则\n- 在这个项目空间内，专注于 {name} 相关的任务\n"
        with open(os.path.join(proj_dir, "SOUL.md"), "w") as f:
            f.write(soul)

        # Reload projects dynamically
        pm._load_projects()

        self._respond_json({
            "project_key": key,
            "name": name,
            "message": f"项目「{name}」已创建（key: {key}）。MEMORY.md 和 SOUL.md 已就绪。"
        })
        print(f"[bridge] Created project: {key} ({name})")

    def _parse_intent(self, raw):
        """Parse LLM output into structured intent."""
        raw = raw.strip()
        if raw.upper() == "NONE" or not raw:
            return {"action": "none"}
        if ":" in raw:
            action_part, _, name = raw.partition(":")
            action = action_part.strip().upper()
            name = name.strip()
            if action == "SWITCH":
                # Validate project key exists
                if name in pm.projects:
                    cn = self.PROJECT_DISPLAY.get(name, pm.projects[name]["name"])
                    return {"action": "switch", "project_key": name, "name": cn}
                return {"action": "none", "reason": f"unknown project: {name}"}
            elif action == "PROJECT":
                return {"action": "new_project", "name": name} if 2 <= len(name) <= 20 else {"action": "none"}
            elif action == "TOPIC":
                return {"action": "new_topic", "name": name} if 2 <= len(name) <= 20 else {"action": "none"}
            elif action == "SWITCH_TOPIC":
                return {"action": "switch_topic", "topic_name": name} if 2 <= len(name) <= 20 else {"action": "none"}
        return {"action": "none", "raw": raw}

    def _respond_json(self, data):
        self.send_response(200); self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _handle_chat(self, data):
        message = _build_user_message(data)
        text_only = data.get("message", "").strip()
        if not message:
            self.send_error(400); return

        # Polling mode: return task_id immediately, frontend polls for result
        if data.get("mode") == "poll":
            self._handle_chat_polling(data)
            return

        # Streaming mode: SSE events
        if data.get("stream"):
            self._handle_chat_streaming(data)
            return

        # Support explicit project override (for cross-project imports)
        explicit_project = data.get("project")
        project_key = explicit_project if explicit_project else pm.current
        is_main = (project_key == "main")
        print(f"[bridge] Chat ({project_key}): {_message_preview(message)[:60]}...")

        try:
            agent = pm.get_agent(project_key)
            # Use frontend-supplied history (for topics) or session messages
            custom_history = data.get("history")
            if custom_history and isinstance(custom_history, list):
                messages = custom_history
            else:
                sess = pm.sessions.get(project_key, {})
                messages = sess.get("messages", [])

            # Always inject project context (SOUL + MEMORY) every turn
            context = pm.get_context_for_chat(project_key) if not is_main else ""
            result = agent.run_conversation(
                user_message=message,
                system_message=context if context else None,
                conversation_history=messages if messages else None,
            )
            response = result.get("final_response", "")

        except Exception as e:
            print(f"[bridge] Error: {e}")
            response = f"抱歉，出错了：{e}"

        pm.add_message("user", text_only or "🖼️ 图片", project_key)
        pm.add_message("assistant", response, project_key)

        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps({"response": response, "files": _detect_files(response), "project": project_key}, ensure_ascii=False).encode("utf-8"))

    def _handle_chat_polling(self, data):
        """Polling mode: create a task, run agent in background, return task_id immediately.
        Frontend polls GET /api/task/{task_id} every 2s until done.
        This is the reliable mode — no long-lived connections, like iLink Bot's buffer."""
        import uuid, time as _time

        message = _build_user_message(data)
        text_only = data.get("message", "").strip()
        if not message:
            self.send_error(400); return
        explicit_project = data.get("project")
        project_key = explicit_project if explicit_project else pm.current
        is_main = (project_key == "main")
        task_id = uuid.uuid4().hex[:12]

        with self._task_lock:
            self.tasks[task_id] = {
                "status": "processing",
                "response": "",
                "files": [],
                "error": "",
                "project": project_key,
                "created": _time.time()
            }

        def run_task():
            try:
                agent = pm.get_agent(project_key)
                # Use frontend-supplied history (for topics) or session messages
                custom_history = data.get("history")
                if custom_history and isinstance(custom_history, list):
                    messages = custom_history
                else:
                    sess = pm.sessions.get(project_key, {})
                    messages = sess.get("messages", [])
                context = pm.get_context_for_chat(project_key) if not is_main else ""
                result = agent.run_conversation(
                    user_message=message,
                    system_message=context if context else None,
                    conversation_history=messages if messages else None,
                )
                response = result.get("final_response", "")
                pm.add_message("user", text_only or "🖼️ 图片", project_key)
                pm.add_message("assistant", response, project_key)
                with self._task_lock:
                    self.tasks[task_id]["status"] = "done"
                    self.tasks[task_id]["response"] = response
                    self.tasks[task_id]["files"] = _detect_files(response)
            except Exception as e:
                print(f"[bridge] Task {task_id} error: {e}")
                with self._task_lock:
                    self.tasks[task_id]["status"] = "error"
                    self.tasks[task_id]["error"] = str(e)

        print(f"[bridge] Polling task {task_id} ({project_key}): {_message_preview(message)[:60]}...")
        t = threading.Thread(target=run_task, daemon=True)
        t.start()

        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps({"task_id": task_id}, ensure_ascii=False).encode("utf-8"))

    def _handle_chat_streaming(self, data):
        """Stream chat response with SSE heartbeats so the browser doesn't disconnect."""
        import time as _time
        message = _build_user_message(data)
        text_only = data.get("message", "").strip()
        explicit_project = data.get("project")
        project_key = explicit_project if explicit_project else pm.current
        is_main = (project_key == "main")
        print(f"[bridge] Chat SSE ({project_key}): {_message_preview(message)[:60]}...")

        # SSE headers
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")  # disable nginx buffering
        self.end_headers()

        def sse_send(self, event_type, data_dict):
            """Write an SSE event to the client."""
            payload = json.dumps(data_dict, ensure_ascii=False)
            self.wfile.write(f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()

        result_holder = {"response": "", "error": None}
        agent_done = threading.Event()

        def run_agent():
            try:
                agent = pm.get_agent(project_key)
                sess = pm.sessions.get(project_key, {})
                messages = sess.get("messages", [])
                context = pm.get_context_for_chat(project_key) if not is_main else ""
                result = agent.run_conversation(
                    user_message=message,
                    system_message=context if context else None,
                    conversation_history=messages if messages else None,
                )
                result_holder["response"] = result.get("final_response", "")
            except Exception as e:
                result_holder["error"] = str(e)
            finally:
                agent_done.set()

        t = threading.Thread(target=run_agent, daemon=True)
        t.start()

        # Send initial "thinking" event
        try:
            sse_send(self, "thinking", {"message": "正在思考..."})
        except BrokenPipeError:
            return

        # Heartbeat loop — send every 2s while agent is running
        heartbeat = 0
        while not agent_done.wait(2.0):
            heartbeat += 1
            try:
                sse_send(self, "heartbeat", {"count": heartbeat})
            except BrokenPipeError:
                return

        t.join(timeout=1)

        # Record messages
        pm.add_message("user", text_only or "🖼️ 图片", project_key)
        if result_holder["error"]:
            response = f"抱歉，出错了：{result_holder['error']}"
            sse_send(self, "error", {"message": response})
        else:
            response = result_holder["response"]
            pm.add_message("assistant", response, project_key)
            sse_send(self, "done", {"response": response, "project": project_key})
            # Send files if detected
            files = _detect_files(response)
            if files:
                sse_send(self, "files", {"files": files})

        # Send close event and terminate connection
        try:
            self.wfile.write("event: close\ndata: {}\n\n".encode("utf-8"))
            self.wfile.flush()
        except BrokenPipeError:
            pass
        self.close_connection = True

    def _handle_switch(self, data):
        project_key = data.get("project", "main")
        result = pm.switch_to(project_key)
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        print(f"[bridge] Switched to: {project_key}")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            self.send_response(200); self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers(); self.wfile.write(b'{"status":"ok","current":"' + pm.current.encode() + b'"}')
        elif path == "/api/projects":
            self.send_response(200); self._cors()
            proj_list = [{"key": k, "name": v["name"], "memory_count": v.get("memory_lines", 0)}
                         for k, v in pm.projects.items()]
            resp = json.dumps({"projects": proj_list, "current": pm.current}, ensure_ascii=False)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers(); self.wfile.write(resp.encode("utf-8"))
        elif path == "/api/context":
            self.send_response(200); self._cors()
            data = pm.get_context_data()
            resp = json.dumps(data, ensure_ascii=False)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers(); self.wfile.write(resp.encode("utf-8"))
        elif path == "/api/messages":
            # Return server-side session messages (for recovery when localStorage is lost)
            # Supports ?project=xxx query parameter
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            key = qs.get("project", [pm.current])[0]
            self.send_response(200); self._cors()
            sess = pm.sessions.get(key, {})
            msgs = sess.get("messages", [])
            resp = json.dumps({"project": key, "messages": msgs}, ensure_ascii=False)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers(); self.wfile.write(resp.encode("utf-8"))
        elif path == "/api/topics":
            # Return server-side topics (GET) for cross-device sync
            # Supports ?project=xxx query parameter
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            key = qs.get("project", [pm.current])[0]
            topics = []
            tp = pm._topics_path(key)
            if os.path.exists(tp):
                try:
                    with open(tp) as f:
                        topics = json.load(f)
                except Exception:
                    pass
            self.send_response(200); self._cors()
            resp = json.dumps({"project": key, "topics": topics}, ensure_ascii=False)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers(); self.wfile.write(resp.encode("utf-8"))
        elif path == "/api/topic-messages":
            # Return server-side topic messages (GET) — cross-device sync
            # Supports ?topicId=xxx query parameter
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            topic_id = qs.get("topicId", [""])[0]
            msgs = pm.load_topic_messages(topic_id) if topic_id else []
            self.send_response(200); self._cors()
            resp = json.dumps({"topicId": topic_id, "messages": msgs}, ensure_ascii=False)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers(); self.wfile.write(resp.encode("utf-8"))
        elif path.startswith("/api/download"):
            self._handle_download()
        elif path.startswith("/api/task/"):
            self._handle_task_poll(path)
        else:
            self._cors()
            self.send_error(404)

    def _handle_task_poll(self, path):
        """GET /api/task/{task_id} — poll for task result."""
        import time as _time
        task_id = path.split("/api/task/")[-1]
        with self._task_lock:
            task = self.tasks.get(task_id)
        if not task:
            self.send_response(404); self._cors(); self.end_headers()
            self.wfile.write(json.dumps({"error": "task not found"}).encode())
            return

        self.send_response(200); self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(task, ensure_ascii=False).encode("utf-8"))

        # Clean up completed tasks older than 60s
        now = _time.time()
        with self._task_lock:
            stale = [tid for tid, t in self.tasks.items()
                      if t["status"] in ("done", "error") and now - t["created"] > 60]
            for tid in stale:
                del self.tasks[tid]

    def _handle_download(self):
        """GET /api/download?path=<absolute_path> — serve a file for download.
        Only allows paths under safe directories (/tmp, ~, /var/www/html)."""
        from urllib.parse import parse_qs, unquote
        qs = parse_qs(urlparse(self.path).query)
        raw = qs.get("path", [""])[0]
        filepath = os.path.abspath(unquote(raw))
        # Security: only allow safe directories
        allowed = ["/tmp/", os.path.expanduser("~") + "/", "/var/www/html/"]
        if not any(filepath.startswith(d) for d in allowed):
            self.send_error(403, "Access denied")
            return
        if not os.path.isfile(filepath):
            self.send_error(404, "File not found")
            return
        try:
            filename = os.path.basename(filepath)
            # Determine MIME type
            import mimetypes
            mime, _ = mimetypes.guess_type(filepath)
            if mime is None:
                mime = "application/octet-stream"
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", mime)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(os.path.getsize(filepath)))
            self.end_headers()
            with open(filepath, "rb") as f:
                self.wfile.write(f.read())
        except Exception as e:
            self.send_error(500, str(e))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format, *args):
        pass

# ── Main ───────────────────────────────────────────────────────
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server so SSE connections don't block others."""
    daemon_threads = True

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    server = ThreadedHTTPServer(("127.0.0.1", port), ChatHandler)
    print(f"[bridge] Listening on http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[bridge] Shutting down.")
        server.shutdown()
