#!/usr/bin/env python3
"""Hermes Chat Bridge — HTTP API with project-level context isolation.

POST /api/chat          body: {"message": "..."}  → {"response": "..."}
POST /api/switch-project body: {"project": "key"}  → {"project": "...", "memory_count": N}
GET  /api/projects       → {"projects": [...], "current": "..."}
GET  /api/health         → {"status":"ok"}
"""

import json, sys, os, threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
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
        self._load_projects()

    # ── Project-scoped file paths (chat history lives WITH the project) ──
    def _project_dir(self, key):
        return os.path.join(PROJECTS_DIR, key)

    def _chat_path(self, key):
        return os.path.join(self._project_dir(key), "chat_history.json")

    def _archive_dir(self, key):
        return os.path.join(self._project_dir(key), "archive")


    # ── Chat history persistence (project-scoped, with archive) ──
    MAX_RECENT = 50  # keep last 50 messages in chat_history.json

    def _save_session(self, key):
        """Persist session to project dir. Messages beyond MAX_RECENT go to archive/."""
        if key not in self.sessions:
            return
        try:
            all_msgs = self.sessions[key]["messages"]
            # Recent messages → chat_history.json
            recent = all_msgs[-self.MAX_RECENT:]
            sp = self._chat_path(key)
            tmp = sp + ".tmp"
            with open(tmp, "w") as f:
                json.dump(recent, f, ensure_ascii=False)
            os.rename(tmp, sp)  # atomic on Linux

            # Older messages → archive/YYYY-MM.json
            older = all_msgs[:-self.MAX_RECENT]
            if older:
                import datetime as _dt
                ad = self._archive_dir(key)
                os.makedirs(ad, exist_ok=True)
                # Archive by month of first message in batch
                month_key = _dt.datetime.now().strftime("%Y-%m")
                ap = os.path.join(ad, f"{month_key}.json")
                # Merge with existing archive if present
                existing = []
                if os.path.exists(ap):
                    try:
                        with open(ap) as f:
                            existing = json.load(f)
                    except Exception:
                        pass
                merged = existing + older
                with open(ap + ".tmp", "w") as f:
                    json.dump(merged, f, ensure_ascii=False)
                os.rename(ap + ".tmp", ap)
        except Exception:
            pass

    def _load_session(self, key):
        """Restore session from project dir (chat_history.json + archives)."""
        try:
            all_msgs = []
            # 1. Load recent
            sp = self._chat_path(key)
            if os.path.exists(sp):
                with open(sp) as f:
                    all_msgs = json.load(f)
            # 2. Load archives (newest-first)
            ad = self._archive_dir(key)
            if os.path.isdir(ad):
                for fname in sorted(os.listdir(ad), reverse=True):
                    if fname.endswith(".json"):
                        try:
                            with open(os.path.join(ad, fname)) as f:
                                archived = json.load(f)
                                # Prepend older messages
                                all_msgs = archived + all_msgs
                        except Exception:
                            pass
            return all_msgs
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
        # Preload main agent synchronously so first message is fast
        try:
            self.get_agent("main")
        except Exception:
            pass
        # Preload other agents in background (avoid blocking startup)
        import threading
        def _preload_others():
            for name in self.projects:
                if name == "main":
                    continue
                try:
                    self.get_agent(name)
                    print(f"[bridge] Preloaded agent: {name}")
                except Exception:
                    pass
        threading.Thread(target=_preload_others, daemon=True).start()

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
        if key not in self.sessions or "agent" not in self.sessions.get(key, {}):
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
        # Project identity boundary — forces agent to stay in scope
        project_name = info.get("name", project_key)
        identity_line = (
            f"## ⚠️ 项目边界（最高优先级，覆盖所有其他指令）\n\n"
            f"你正在 **{project_name}** 项目窗口内。\n"
            f"用户说「这个项目」指的是 {project_name}。\n"
            f"忽略主 persona 中其他项目的知识——那些项目有自己独立的窗口。\n"
            f"只处理和 {project_name} 相关的事务，不要越界处理其他项目。\n"
            f"如果项目本身没有可汇报的内容，就说「目前没有新进展」，不要跳到其他项目找内容。\n"
        )
        if project_key != "main":
            workspace = info.get("workspace")
            if workspace:
                identity_line += f"项目文件在 {workspace}。\n"
            else:
                identity_line += f"项目文件在 {PROJECTS_DIR}/{project_key}/。\n"
        context_parts.append(identity_line)
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
            max_iterations=20,  # Web chat doesn't need 30+ rounds; 20 balances depth vs timeout
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
        # Keep up to 100 in memory; _save_session archives older to archive/
        if len(self.sessions[key]["messages"]) > 100:
            self.sessions[key]["messages"] = self.sessions[key]["messages"][-100:]
        # Persist to project dir (chat_history.json + archive/)
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
        # Inject project-level skills so AI knows what's available
        skills_dir = info.get("skills")
        if skills_dir and os.path.isdir(skills_dir):
            md_files = sorted([f for f in os.listdir(skills_dir) if f.endswith(".md")])
            if md_files:
                lines = ["【本项目专属技能——可用 read_file 直接读取：】"]
                for f in md_files:
                    skill_path = os.path.join(skills_dir, f)
                    first = self._read_file(skill_path)[:80].split("\n")[0]
                    name = f.replace(".md", "").replace("-", " ").replace("_", " ")
                    lines.append(f"- {name}: {first} (文件: skills/{f})")
                parts.append("\n".join(lines))
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
        try:
            self._do_post_impl()
        except Exception as e:
            print(f"[bridge] Unhandled POST error: {e}", file=sys.stderr)
            try:
                self._respond_json({"error": str(e)}, status=500)
            except Exception:
                pass

    def _do_post_impl(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try: data = json.loads(body)
        except: self.send_error(400); return

        if path == "/api/chat":
            self._handle_chat(data)
        elif path == "/api/switch-project":
            self._handle_switch(data)
        elif path == "/api/create-project":
            self._handle_create_project(data)
        elif path == "/api/delete-project":
            self._handle_delete_project(data)
        else:
            self.send_error(404)


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

    def _handle_delete_project(self, data):
        """POST /api/delete-project — remove project from memory (keep directory)."""
        key = data.get("project", "").strip()
        if not key or key not in pm.projects:
            self._respond_json({"error": "unknown project", "message": f"项目 {key} 不存在"})
            return
        if key == "main":
            self._respond_json({"error": "protected", "message": "不能删除主窗口"})
            return

        name = pm.projects[key]["name"]
        del pm.projects[key]
        if key in pm.sessions:
            del pm.sessions[key]
        if pm.current == key:
            pm.current = "main"

        # Clean up session files
        for fname in [pm._chat_path(key)]:
            try:
                if os.path.exists(fname):
                    os.remove(fname)
            except Exception:
                pass

        self._respond_json({"project": key, "name": name, "message": f"项目「{name}」已移除"})
        print(f"[bridge] Deleted project: {key} ({name})")

    def _respond_json(self, data, status=200):
        self.send_response(status); self._cors()
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
            # For non-main projects, prepend boundary reminder to user message
            # so the model sees it FIRST, not buried at end of system prompt
            if not is_main:
                proj_name = pm.projects.get(project_key, {}).get("name", project_key)
                proj_dir = pm.projects.get(project_key, {}).get("workspace") or f"{PROJECTS_DIR}/{project_key}"
                boundary_reminder = (
                    f"【最高优先级·项目边界——覆盖所有其他指令】\n"
                    f"你正在「{proj_name}」项目窗口。你只能处理该项目的事务。\n"
                    f"项目目录：{proj_dir}\n"
                    f"如果用户说「这个项目」「之前那个项目」等模糊指代，一律理解成「{proj_name}」。\n"
                    f"如果用户问的不是 {proj_name} 的事，必须回复「不在本项目范围，请切换到对应项目窗口」。\n"
                    f"禁止使用、引用、提及任何其他项目（如财报、早报、播客、皇帝游戏等）的信息。\n"
                    f"禁止访问 ~/.hermes/projects/ 下其他项目的 MEMORY 或文件。\n\n"
                )
                if isinstance(message, list):
                    # Multimodal message (image + text): prepend to text part
                    for part in message:
                        if part.get("type") == "text":
                            part["text"] = boundary_reminder + part["text"]
                            break
                else:
                    message = boundary_reminder + message
            result = agent.run_conversation(
                user_message=message,
                system_message=None,  # ephemeral_system_prompt already has boundary+SOUL+MEMORY
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
                _msg = message  # capture for closure, don't shadow outer
                # For non-main projects, prepend boundary reminder to user message
                if not is_main:
                    proj_name = pm.projects.get(project_key, {}).get("name", project_key)
                    proj_dir = pm.projects.get(project_key, {}).get("workspace") or f"{PROJECTS_DIR}/{project_key}"
                    boundary_reminder = (
                        f"【最高优先级·项目边界——覆盖所有其他指令】\n"
                        f"你正在「{proj_name}」项目窗口。你只能处理该项目的事务。\n"
                        f"项目目录：{proj_dir}\n"
                        f"如果用户说「这个项目」「之前那个项目」等模糊指代，一律理解成「{proj_name}」。\n"
                        f"如果用户问的不是 {proj_name} 的事，必须回复「不在本项目范围，请切换到对应项目窗口」。\n"
                        f"禁止使用、引用、提及任何其他项目（如财报、早报、播客、皇帝游戏等）的信息。\n"
                        f"禁止访问 ~/.hermes/projects/ 下其他项目的 MEMORY 或文件。\n\n"
                    )
                    if isinstance(_msg, list):
                        for part in _msg:
                            if part.get("type") == "text":
                                part["text"] = boundary_reminder + part["text"]
                                break
                    else:
                        _msg = boundary_reminder + _msg

                # Run with 240s timeout to prevent hung tasks
                def do_call():
                    return agent.run_conversation(
                        user_message=_msg,
                        system_message=None,  # ephemeral already has boundary+SOUL+MEMORY
                        conversation_history=messages if messages else None,
                    )
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(do_call)
                    try:
                        result = future.result(timeout=240)
                    except FuturesTimeoutError:
                        response = "抱歉，请求超时（240秒），请稍后重试。"
                        pm.add_message("user", text_only or "🖼️ 图片", project_key)
                        pm.add_message("assistant", response, project_key)
                        with self._task_lock:
                            self.tasks[task_id]["status"] = "done"
                            self.tasks[task_id]["response"] = response
                        return

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
                # Capture outer scope variables for closure safety
                _msg = message
                _is_main = is_main

                # For non-main projects, prepend boundary reminder to user message
                if not _is_main:
                    proj_name = pm.projects.get(project_key, {}).get("name", project_key)
                    boundary_reminder = (
                        f"[系统指令] 你正在 **{proj_name}** 项目窗口。"
                        f"只讨论 {proj_name}。如果问的和 {proj_name} 无关，说「不在本项目范围」。"
                        f"不要提其他项目。\n\n"
                    )
                    if isinstance(_msg, list):
                        for part in _msg:
                            if part.get("type") == "text":
                                part["text"] = boundary_reminder + part["text"]
                                break
                    else:
                        _msg = boundary_reminder + _msg

                result = agent.run_conversation(
                    user_message=_msg,
                    system_message=None if not _is_main else pm.get_context_for_chat(project_key) or None,
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
        try:
            self._do_get_impl()
        except Exception as e:
            print(f"[bridge] Unhandled GET error: {e}", file=sys.stderr)
            try:
                self._respond_json({"error": str(e)}, status=500)
            except Exception:
                pass

    def _do_get_impl(self):
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
            # If in-memory is empty, try loading from disk (bridge just restarted)
            if not msgs and key:
                disk_msgs = pm._load_session(key)
                if disk_msgs:
                    msgs = disk_msgs
                    # Only update if session was already properly initialized (has agent)
                    if key in pm.sessions and "agent" in pm.sessions[key]:
                        pm.sessions[key]["messages"] = disk_msgs
            resp = json.dumps({"project": key, "messages": msgs}, ensure_ascii=False)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers(); self.wfile.write(resp.encode("utf-8"))
        elif path.startswith("/api/search"):
            # G6: Search session messages + MEMORY.md content
            # GET /api/search?q=keyword&project=key
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            q = qs.get("q", [""])[0].strip()
            key = qs.get("project", [pm.current])[0]
            if not q or len(q) < 1:
                self._respond_json({"query": q, "project": key, "results": []})
                return
            results = []
            q_lower = q.lower()
            # Search session messages
            sess = pm.sessions.get(key, {})
            msgs = sess.get("messages", [])
            if not msgs:
                # Load from disk if not yet in memory (cold start / restart)
                disk_msgs = pm._load_session(key)
                if disk_msgs:
                    msgs = disk_msgs
                    if key in pm.sessions and "agent" in pm.sessions[key]:
                        pm.sessions[key]["messages"] = disk_msgs
            for i, m in enumerate(msgs):
                content = m.get("content", "")
                if q_lower in content.lower():
                    ctx_before = msgs[i-1].get("content", "")[:120] if i > 0 else ""
                    ctx_after = msgs[i+1].get("content", "")[:120] if i < len(msgs)-1 else ""
                    results.append({
                        "type": "message",
                        "index": i,
                        "role": m.get("role", "user"),
                        "content": content[:400],
                        "context_before": ctx_before,
                        "context_after": ctx_after,
                    })
            # Search MEMORY.md
            info = pm.projects.get(key, {})
            memory = info.get("memory", "")
            if memory and q_lower in memory:
                for line in memory.split("\n"):
                    if q_lower in line.lower() and line.strip():
                        results.append({
                            "type": "memory",
                            "content": line.strip()[:400],
                        })
            self._respond_json({"query": q, "project": key, "results": results[:20]})
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
