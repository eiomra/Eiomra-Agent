"""
AI Browser Agent — agent.py
Multi-provider AI + structured task planning + rich page extraction
"""

import asyncio, base64, json, os, re, sys, time, traceback
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import httpx, uvicorn
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright

from attachment_reader import AttachmentReader
from desktop_automation_portable import (
    click_text_on_screen_action,
    close_window_action,
    desktop_click_action,
    desktop_hotkey_action,
    desktop_press_key_action,
    desktop_scroll_action,
    desktop_screenshot_action,
    desktop_screenshot_b64,
    desktop_type_action,
    desktop_type_verified_action,
    gmail_compose_action,
    list_installed_apps_action,
    drag_mouse_action,
    find_text_on_screen_action,
    focus_window_action,
    list_windows_action,
    move_mouse_action,
    open_browser_url_action,
    open_application_action,
    select_menu_item_action,
)
from page_reader  import extract_page_content, get_capabilities, rank_candidates, format_candidates
from action_protocol import action_to_history_entry, normalize_action_payload
from agent_sessions import AgentOrchestrator, AgentSessionStore
from git_context import GitContextProvider
from method_memory import MethodMemoryStore
from repo_retrieval import RepoRetrievalIndex
from workspace_actions import (
    copy_paths_action,
    convert_image_format_action,
    create_csv_action,
    create_directory_action,
    create_file_action,
    create_json_action,
    create_markdown_report_action,
    create_pdf_action,
    delete_paths_action,
    download_url_action,
    extract_archive_action,
    extract_pdf_text_action,
    list_directory_action,
    move_paths_action,
    ocr_image_to_text_action,
    patch_file_action,
    prepare_command_action,
    read_csv_action,
    read_file_action,
    read_json_action,
    rename_path_action,
    resolve_allowed_path,
    run_command_action,
    search_in_files_action,
    write_yaml_action,
    write_file_action,
    zip_paths_action,
)
from task_planner import (
    TaskTracker, SubTask,
    PLANNER_SYSTEM, build_plan_prompt,
    EXECUTOR_SYSTEM, build_execution_prompt,
    REPORT_SYSTEM, build_report_prompt,
    REPLAN_SYSTEM, build_replan_prompt,
    REFLECTION_SYSTEM, build_reflection_prompt,
    parse_agent_response,
)

app = FastAPI(title="AI Browser Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(BASE_DIR)
RESULTS_DIR = os.path.join(BASE_DIR, "results")
LOGS_DIR    = os.path.join(BASE_DIR, "logs")
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
MEMORY_DIR = os.path.join(BASE_DIR, "memory")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
for d in (RESULTS_DIR, LOGS_DIR, SESSIONS_DIR, UPLOADS_DIR, MEMORY_DIR): os.makedirs(d, exist_ok=True)

session_store = AgentSessionStore(SESSIONS_DIR)
orchestrator = AgentOrchestrator(session_store)
repo_index = RepoRetrievalIndex(WORKSPACE_ROOT)
git_context_provider = GitContextProvider(WORKSPACE_ROOT)
attachment_reader = AttachmentReader()
method_memory = MethodMemoryStore(os.path.join(MEMORY_DIR, "successful_methods.json"))

# ══════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════
DEFAULT_CONFIG = {
    "ollama_local_url": "http://localhost:11434",
    "ollama_cloud_url": "https://ollama.com/api",
    "ollama_cloud_key": "",
    "google_api_key":   "",
    "openai_api_key":   "",
    "active_provider":  "ollama_local",
    "active_model":     "qwen3:4b",
    "fallback_enabled": True,
    "fallback_order":   ["ollama_local","ollama_cloud","google","openai"],
    "fallback_models":  {
        "ollama_local": "qwen3:4b",
        "ollama_cloud": "gpt-oss:120b",
        "google":       "gemini-2.0-flash",
        "openai":       "gpt-4o-mini"
    },
    "max_steps_enabled":  False,
    "max_steps":          100,
    "max_time_enabled":   True,
    "max_time_minutes":   30,
    "stuck_detection":    True,
    "stuck_threshold":    3,
    "max_text_chars":     8000,
    "deep_read":          False,
    "ocr_enabled":        False,
    "ocr_max_images":     5,
    "pdf_enabled":        True,
    "replan_interval":    8,    # replan every N steps (0 = off)
    "local_file_access_enabled": True,
    "local_file_write_enabled": True,
    "filesystem_scope":   "workspace",  # workspace | full_computer
    "filesystem_root":    "",
    "desktop_automation_enabled": False,
    "desktop_execution_mode": "manual",  # disabled | manual | auto
    "desktop_autonomy_scope": "browser_only",  # browser_only | browser_and_desktop
    "command_execution_mode": "manual",  # disabled | manual | auto
    "command_timeout_seconds": 120,
    "multi_agent_enabled": True,
    "multi_agent_mode": "auto",  # off | auto
    "max_parallel_agents": 4,
    "central_agent_approval": "auto",  # auto | review
}

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ══════════════════════════════════════════════════════════════════════════
# SESSION LOGGER
# ══════════════════════════════════════════════════════════════════════════
class SessionLogger:
    def __init__(self, goal, provider, model):
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = re.sub(r'[^a-zA-Z0-9 ]','',goal)[:35].strip().replace(' ','_')
        self.filename = f"{ts}_{safe or 'session'}.log"
        self.path     = os.path.join(LOGS_DIR, self.filename)
        self._f = open(self.path, "w", encoding="utf-8", buffering=1)
        self._header(goal, provider, model, ts)
        print(f"[LOG] {self.path}", flush=True)

    def plan(self, tasks):
        self._section("TASK PLAN")
        for t in tasks:
            self._raw(f"  [{t['id']}] {t['description']}")

    def step_start(self, step, url, title, provider, model, current_task, progress, sources):
        self._section(f"STEP {step}  [{provider}/{model}]  {progress}")
        self._kv("URL", url); self._kv("Title", title)
        if current_task:
            self._kv("Current task", f"[{current_task.id}] {current_task.description}")
        self._kv("DOM chars", str(sources.get("dom_chars",0)))
        self._kv("OCR chars", str(sources.get("ocr_chars",0)))

    def page_text(self, text):
        self._heading("PAGE TEXT"); self._raw(text[:1500])

    def query(self, provider, model, system, user):
        self._heading(f"QUERY → {provider}/{model}")
        self._sub("SYSTEM"); self._raw(system[:600])
        self._sub("USER");   self._raw(user)

    def raw_reply(self, raw):   self._heading("RAW REPLY"); self._raw(raw)
    def decision(self, d):
        self._heading("DECISION")
        for k,v in d.items(): self._kv(f"  {k}", str(v)[:300])

    def task_completed(self, task_id, finding):
        self._heading(f"✓ TASK COMPLETED: {task_id}")
        self._raw(f"  Finding: {finding}")

    def task_skipped(self, task_id, reason):
        self._heading(f"⊘ TASK SKIPPED: {task_id}")
        self._raw(f"  Reason: {reason}")

    def done_rejected(self, reason):
        self._heading(f"✗ DONE REJECTED: {reason}")

    def action(self, act, params):
        self._heading(f"ACTION: {act.upper()}")
        for k,v in params.items(): self._kv(f"  {k}", str(v))

    def result(self, r): self._heading("RESULT"); self._raw(f"  {r}")
    def error(self, kind, msg): self._heading(f"ERROR [{kind}]"); self._raw(msg)
    def stuck(self, n): self._heading(f"STUCK ({n}x) — rethink forced")

    def session_end(self, reason, steps, elapsed):
        self._section("SESSION END")
        self._kv("Reason", reason); self._kv("Steps", str(steps))
        self._kv("Duration", f"{elapsed:.1f}s"); self._divider("═"); self.close()

    def close(self):
        try: self._f.flush(); self._f.close()
        except Exception: pass

    def _ts(self): return datetime.now().strftime("%H:%M:%S.%f")[:-3]
    def _write(self, line):
        try: self._f.write(line+"\n"); self._f.flush(); os.fsync(self._f.fileno())
        except Exception: pass
        print(line, flush=True)
    def _divider(self, c="─", w=72): self._write(c*w)
    def _section(self, t):
        self._write(""); self._divider("═")
        self._write(f"  [{self._ts()}]  {t}"); self._divider("═")
    def _heading(self, t): self._write(""); self._write(f"  ── {t}")
    def _sub(self, t):     self._write(f"     ▸ {t}")
    def _kv(self, k, v):   self._write(f"  {k:<22}: {v}")
    def _raw(self, text):
        for line in (text or "(empty)").splitlines(): self._write("    "+line)
    def _header(self, goal, provider, model, ts):
        self._divider("═"); self._write("  AI BROWSER AGENT — SESSION LOG"); self._divider("═")
        self._kv("Started", ts); self._kv("Goal", goal)
        self._kv("Provider", f"{provider}/{model}"); self._kv("Log", self.path)
        self._divider("═")

session_log: SessionLogger = None

# ══════════════════════════════════════════════════════════════════════════
# PROVIDER LABELS
# ══════════════════════════════════════════════════════════════════════════
PROVIDER_LABELS = {
    "ollama_local": "Ollama Local",
    "ollama_cloud": "Ollama Cloud",
    "google":       "Google Gemini",
    "openai":       "ChatGPT / OpenAI",
}


async def fetch_ollama_model_names(base_url: str, api_key: str = "", timeout: float = 4.0) -> list[str]:
    normalized_base = base_url.rstrip("/")
    if not normalized_base.endswith("/api"):
        normalized_base += "/api"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(normalized_base + "/tags", headers=headers)
        response.raise_for_status()
    payload = response.json()
    names: list[str] = []
    for model in payload.get("models", []):
        if not isinstance(model, dict):
            continue
        name = str(model.get("name") or model.get("model") or "").strip()
        if name and name not in names:
            names.append(name)
    return names

# ══════════════════════════════════════════════════════════════════════════
# MULTI-PROVIDER CALLER
# ══════════════════════════════════════════════════════════════════════════
def _make_image_part_ollama(b64: str) -> dict:
    """Ollama image part — raw base64 string in the images array."""
    return b64  # ollama takes images as a list of raw base64 strings

def _make_image_part_openai(b64: str) -> dict:
    """OpenAI / compatible vision content part."""
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}}

def _make_image_part_google(b64: str) -> dict:
    """Google Gemini inline image part."""
    return {"inline_data": {"mime_type": "image/jpeg", "data": b64}}


async def call_provider(provider, model, system_prompt, user_prompt, cfg,
                        image_b64: str | None = None) -> str:
    """Call a model provider.  If *image_b64* is set, include the screenshot
    as a vision input so the model can see the current screen state."""

    if provider == "ollama_local":
        url = cfg["ollama_local_url"].rstrip("/") + "/api/chat"
        user_msg: dict = {"role": "user", "content": user_prompt}
        if image_b64:
            user_msg["images"] = [image_b64]
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0)) as c:
            r = await c.post(url, json={
                "model": model,
                "messages": [{"role": "system", "content": system_prompt}, user_msg],
                "stream": False,
                "options": {"temperature": 0.2, "num_ctx": 8192}
            })
            r.raise_for_status()
            return r.json()["message"]["content"].strip()

    elif provider == "ollama_cloud":
        if not cfg.get("ollama_cloud_key"): raise ValueError("Ollama Cloud key not set")
        url = cfg["ollama_cloud_url"].rstrip("/") + "/chat"
        user_msg = {"role": "user", "content": user_prompt}
        if image_b64:
            user_msg["images"] = [image_b64]
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0)) as c:
            r = await c.post(url,
                headers={"Authorization": f"Bearer {cfg['ollama_cloud_key']}"},
                json={"model": model,
                      "messages": [{"role": "system", "content": system_prompt}, user_msg],
                      "stream": False})
            r.raise_for_status()
            return r.json()["message"]["content"].strip()

    elif provider == "google":
        if not cfg.get("google_api_key"): raise ValueError("Google API key not set")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        parts: list = [{"text": f"{system_prompt}\n\n{user_prompt}"}]
        if image_b64:
            parts.insert(0, _make_image_part_google(image_b64))
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0)) as c:
            r = await c.post(url,
                headers={"x-goog-api-key": cfg["google_api_key"],
                         "Content-Type": "application/json"},
                json={"contents": [{"role": "user", "parts": parts}],
                      "generationConfig": {"temperature": 0.2}})
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

    elif provider == "openai":
        if not cfg.get("openai_api_key"): raise ValueError("OpenAI key not set")
        if image_b64:
            user_content: list | str = [
                {"type": "text", "text": user_prompt},
                _make_image_part_openai(image_b64),
            ]
        else:
            user_content = user_prompt
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0)) as c:
            r = await c.post("https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {cfg['openai_api_key']}",
                         "Content-Type": "application/json"},
                json={"model": model, "temperature": 0.2,
                      "messages": [{"role": "system", "content": system_prompt},
                                    {"role": "user",   "content": user_content}]})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()

    elif provider == "anthropic":
        if not cfg.get("anthropic_api_key"): raise ValueError("Anthropic key not set")
        if image_b64:
            user_content = [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": user_prompt},
            ]
        else:
            user_content = user_prompt
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0)) as c:
            r = await c.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": cfg["anthropic_api_key"],
                         "anthropic-version": "2023-06-01",
                         "Content-Type": "application/json"},
                json={"model": model, "max_tokens": 4096, "temperature": 0.2,
                      "system": system_prompt,
                      "messages": [{"role": "user", "content": user_content}]})
            r.raise_for_status()
            return r.json()["content"][0]["text"].strip()

    else:
        raise ValueError(f"Unknown provider: {provider}")


async def call_with_fallback(system_prompt, user_prompt,
                              preferred_provider, preferred_model,
                              cfg, log_query=True,
                              image_b64: str | None = None) -> tuple:
    providers = [(preferred_provider, preferred_model)]
    if cfg.get("fallback_enabled"):
        for p in cfg.get("fallback_order", []):
            if p != preferred_provider and cfg.get("fallback_models",{}).get(p):
                providers.append((p, cfg["fallback_models"][p]))

    last_error = None
    for provider, model in providers:
        try:
            if log_query and session_log:
                session_log.query(provider, model, system_prompt, user_prompt)
            raw = await call_provider(provider, model, system_prompt, user_prompt, cfg,
                                      image_b64=image_b64)
            if log_query and session_log:
                session_log.raw_reply(raw)
            return raw, provider, model
        except Exception as e:
            last_error = e
            if session_log: session_log.error(f"{provider} failed", str(e))
            await broadcast({"type":"error",
                             "message":f"[{PROVIDER_LABELS.get(provider,provider)}] {e}"
                             + (" — trying fallback..." if cfg.get("fallback_enabled") else "")})
            if not cfg.get("fallback_enabled"): break
            await asyncio.sleep(1)

    raise RuntimeError(f"All providers failed. Last error: {last_error}")


# ══════════════════════════════════════════════════════════════════════════
# BROWSER STATE
# ══════════════════════════════════════════════════════════════════════════
browser = page = playwright_instance = None
active_ws: list = []
agent_running   = False
agent_task      = None
inject_queue: list = []   # user-injected tasks waiting to be added
current_tracker = None    # exposed for /inject endpoint
current_session_task_id = None

# Last desktop screenshot captured during this step, as base64 JPEG.
# Passed to the next model call so the model sees the actual screen.
_last_screenshot_b64: str | None = None


def make_task_id(goal: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", goal.strip().lower()).strip("_")[:40]
    return f"{ts}_{safe or 'session'}"


def safe_attachment_name(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9._ -]", "_", (name or "attachment").strip())
    return clean[:120] or "attachment"


def list_uploaded_attachments() -> list[dict]:
    items = []
    for name in sorted(os.listdir(UPLOADS_DIR)):
        path = os.path.join(UPLOADS_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            summary = attachment_reader.summarize(path, max_chars=2500)
            items.append({
                "id": name,
                "name": summary.name,
                "kind": summary.kind,
                "mime_type": summary.mime_type,
                "size": summary.size,
                "summary": summary.summary,
                "excerpt": summary.content_excerpt[:800],
                "metadata": summary.metadata or {},
            })
        except Exception:
            items.append({
                "id": name,
                "name": name,
                "kind": "unknown",
                "mime_type": "application/octet-stream",
                "size": os.path.getsize(path),
                "summary": "Could not read attachment",
                "excerpt": "",
                "metadata": {},
            })
    return items


def build_attachment_context(attachment_ids: list[str] | None) -> tuple[str, list[dict]]:
    if not attachment_ids:
        return "", []
    blocks = []
    resolved = []
    for attachment_id in attachment_ids:
        safe_id = os.path.basename(str(attachment_id))
        path = os.path.join(UPLOADS_DIR, safe_id)
        if not os.path.isfile(path):
            continue
        try:
            summary = attachment_reader.summarize(path, max_chars=3000)
        except Exception:
            continue
        blocks.append(summary.to_prompt_block())
        resolved.append({
            "id": safe_id,
            "name": summary.name,
            "kind": summary.kind,
            "mime_type": summary.mime_type,
            "size": summary.size,
            "summary": summary.summary,
        })
    return "\n\n".join(blocks), resolved


async def broadcast(msg: dict):
    dead = []
    seen = set()
    for ws in active_ws:
        ws_id = id(ws)
        if ws_id in seen:
            dead.append(ws)  # duplicate connection — remove it
            continue
        seen.add(ws_id)
        try: await ws.send_json(msg)
        except Exception: dead.append(ws)
    for ws in dead:
        if ws in active_ws: active_ws.remove(ws)


async def broadcast_session_state(task_id: str | None):
    if not task_id:
        return
    session = orchestrator.resume_session(task_id)
    if session:
        await broadcast({"type": "session_state", "session": session.to_dict()})


def build_repo_and_git_context(query: str, repo_limit: int = 3) -> tuple[str, str, list[str]]:
    retrieval = repo_index.retrieve(query, limit=repo_limit)
    repo_context = repo_index.format_context(retrieval)
    git_context = git_context_provider.format_context(git_context_provider.get_context())
    repo_paths = [item.file.relative_path for item in retrieval]
    return repo_context, git_context, repo_paths


def build_agent_lane_plan(goal: str, cfg: dict, tasks: list[dict] | None = None) -> tuple[list[dict], dict]:
    base_lanes = [lane.to_dict() for lane in AgentOrchestrator.default_lanes()]
    if not cfg.get("multi_agent_enabled", True) or str(cfg.get("multi_agent_mode", "auto")) == "off":
        return base_lanes, {
            "enabled": False,
            "worker_count": 1,
            "approval_mode": str(cfg.get("central_agent_approval", "auto")),
        }

    task_count = len(tasks or [])
    goal_lower = goal.lower()
    desired_workers = 1
    if task_count >= 6:
        desired_workers += 1
    if task_count >= 10:
        desired_workers += 1
    if any(token in goal_lower for token in ("monitor", "keep checking", "interval", "watch", "background")):
        desired_workers += 1
    if any(token in goal_lower for token in ("desktop", "software", "app", "command", "terminal", "program")):
        desired_workers += 1
    desired_workers = max(1, min(int(cfg.get("max_parallel_agents", 4)), desired_workers))

    lanes = list(base_lanes)
    specialist_roles = [
        ("worker_desktop", "Desktop/software specialist", "waiting for assignment"),
        ("worker_command", "Terminal/command specialist", "waiting for assignment"),
        ("worker_monitor", "Monitoring/follow-up specialist", "waiting for assignment"),
    ]
    for lane_id, role, step in specialist_roles[: max(0, desired_workers - 1)]:
        lanes.append({
            "id": lane_id,
            "role": role,
            "status": "idle",
            "current_step": step,
            "owned_resources": [],
        })
    return lanes, {
        "enabled": True,
        "worker_count": desired_workers,
        "approval_mode": str(cfg.get("central_agent_approval", "auto")),
    }


def _current_session_goal_and_task() -> tuple[str, str]:
    goal = ""
    task_description = ""
    if current_session_task_id:
        session = orchestrator.resume_session(current_session_task_id)
        if session:
            goal = str(session.request or "")
            task_description = str(session.metadata.get("current_task_description") or "")
    return goal, task_description


def _is_external_browser_goal_task(goal: str, task_description: str = "") -> bool:
    text = f"{goal}\n{task_description}".lower()
    has_browser = "browser" in text
    wants_edge = "edge" in text or "microsoft edge" in text or "msedge" in text
    wants_chrome = "chrome" in text or "google chrome" in text
    return has_browser and (wants_edge or wants_chrome)


def _goal_prefers_real_browser(goal: str, task_description: str = "") -> bool:
    text = f"{goal}\n{task_description}".lower()
    browser_phrases = (
        "my browser",
        "my edge",
        "my chrome",
        "edge browser",
        "chrome browser",
        "already-open",
        "already open",
        "logged in",
        "logged-in",
        "real session",
        "desktop software",
        "native ui",
        "extension",
        "extensions",
    )
    return _is_external_browser_goal_task(goal, task_description) or any(phrase in text for phrase in browser_phrases)


def _task_requires_real_browser(goal: str, task_description: str = "") -> bool:
    text = f"{goal}\n{task_description}".lower()
    external_only_tokens = (
        "launch",
        "open my",
        "focus",
        "gmail",
        "log in",
        "login",
        "sign in",
        "recipient",
        "to field",
        "email address",
        "subject",
        "body",
        "send",
        "submit",
        "upload",
        "download",
        "file chooser",
        "popup",
        "extension",
        "captcha",
        "native ui",
    )
    return _goal_prefers_real_browser(goal, task_description) and any(token in text for token in external_only_tokens)


def _choose_task_environment(goal: str, task_description: str = "") -> str:
    if not _goal_prefers_real_browser(goal, task_description):
        return "internal"
    if _task_requires_real_browser(goal, task_description) or _is_external_browser_launch_task(task_description):
        return "external"
    return "hybrid"


def _current_session_environment() -> str:
    goal, task_description = _current_session_goal_and_task()
    return _choose_task_environment(goal, task_description)


def _is_external_browser_launch_task(task_description: str) -> bool:
    text = (task_description or "").lower()
    return (
        ("launch" in text or "open" in text or "focus" in text)
        and ("chrome browser" in text or "edge browser" in text or "google chrome" in text or "microsoft edge" in text)
    )


def _task_browser_tokens(task_description: str) -> tuple[str, ...]:
    text = (task_description or "").lower()
    if "edge" in text:
        return ("edge", "microsoft edge", "msedge")
    if "chrome" in text:
        return ("chrome", "google chrome")
    return ()


def _external_browser_command(goal: str, task_description: str = "") -> str:
    text = f"{goal}\n{task_description}".lower()
    if "edge" in text:
        return "msedge.exe" if sys.platform.startswith("win") else "microsoft-edge"
    if "chrome" in text:
        return "chrome.exe" if sys.platform.startswith("win") else "google-chrome"
    return ""


def _result_confirms_browser_window(task_description: str, result: str) -> bool:
    lowered = (result or "").lower()
    tokens = _task_browser_tokens(task_description)
    if lowered.startswith("action failed"):
        return False
    if not any(token in lowered for token in tokens):
        return False
    return "focused window:" in lowered or "focused existing window before launch:" in lowered


def _has_verified_external_browser_history(history: list[dict], task_description: str) -> bool:
    for item in reversed(history[-6:]):
        action_text = str(item.get("action", "")).lower()
        result_text = str(item.get("result", ""))
        if action_text.startswith(("open_application", "focus_window")) and _result_confirms_browser_window(task_description, result_text):
            return True
    return False


def _external_browser_text_entry_task(task_description: str) -> bool:
    # Return True only for tasks that involve typing *content* into a browser field.
    # Kept narrow on purpose — over-broad matching causes the verification guards to
    # block navigation/click tasks that have nothing to do with text entry.
    # Rules:
    #   "type " / "fill "  — explicit typing instructions
    #   "subject" / "to field" / "recipient" / "email body" — Gmail compose fields
    # Deliberately excluded:
    #   "address bar", "url", "search" — these are click/navigate tasks, not typing tasks;
    #   the URL bar typing is self-verified by navigate/desktop_type result already.
    text = (task_description or "").lower()
    return any(token in text for token in ("type ", "fill ", "subject", "to field", "recipient", "email body"))


def _is_gmail_task(goal: str, task_description: str = "") -> bool:
    text = f"{goal}\n{task_description}".lower()
    return "gmail" in text or "compose new mail" in text or "email body" in text


def _gmail_field_kind(task_description: str) -> str:
    text = (task_description or "").lower()
    if any(token in text for token in ("recipient", "to field", "email address", "to:")):
        return "recipient"
    if "subject" in text:
        return "subject"
    if any(token in text for token in ("email body", "message body", "body")):
        return "body"
    return ""


def _extract_expected_task_text(task_description: str, finding: str = "") -> str:
    task_text = task_description or ""
    finding_text = finding or ""
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", task_text) or re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", finding_text)
    if email_match:
        return email_match.group(0)
    quoted_match = re.search(r"'([^']{2,120})'", task_text) or re.search(r"'([^']{2,120})'", finding_text)
    if quoted_match:
        return quoted_match.group(1)
    return ""


def _iter_recent_artifact_paths(history: list[dict], limit: int = 8) -> list[str]:
    paths: list[str] = []
    pattern = re.compile(r'"(?:path|destination|source)"\s*:\s*"([^"]+)"')
    for item in reversed(history[-limit:]):
        action_text = str(item.get("action", ""))
        for match in pattern.finditer(action_text):
            path = match.group(1).strip()
            if path and path not in paths:
                paths.append(path)
    return paths


def _artifact_text_contains(path: str, expected: str) -> bool:
    if not expected:
        return False
    candidate = path
    if not os.path.isabs(candidate):
        candidate = os.path.join(WORKSPACE_ROOT, candidate)
    if not os.path.isfile(candidate):
        return False
    if not candidate.lower().endswith((".txt", ".md", ".log")):
        return False
    try:
        content = open(candidate, "r", encoding="utf-8", errors="ignore").read().lower()
    except Exception:
        return False
    return expected.lower() in content


def _text_verification_query(task_description: str, typed_text: str) -> str:
    typed = str(typed_text or "").strip()
    if not typed:
        return ""
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", typed)
    if email_match:
        return email_match.group(0)
    task_lower = (task_description or "").lower()
    first_line = next((line.strip() for line in typed.splitlines() if line.strip()), "")
    if any(token in task_lower for token in ("recipient", "to field", "email address", "subject")):
        return first_line[:80]
    if "\n" not in typed and len(typed) <= 80:
        return typed
    return ""


def _gmail_verification_query(task_description: str, typed_text: str) -> str:
    typed = str(typed_text or "").strip()
    if not typed:
        return ""
    field_kind = _gmail_field_kind(task_description)
    if field_kind == "recipient":
        email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", typed)
        return email_match.group(0) if email_match else typed.splitlines()[0][:80]
    if field_kind == "subject":
        return next((line.strip() for line in typed.splitlines() if line.strip()), "")[:120]
    if field_kind == "body":
        return next((line.strip() for line in typed.splitlines() if line.strip()), "")[:120]
    return ""


def _gmail_post_type_commit(goal: str, task_description: str, typed_text: str) -> str:
    if not _is_gmail_task(goal, task_description):
        return ""
    field_kind = _gmail_field_kind(task_description)
    typed = str(typed_text or "").strip()
    if not field_kind or not typed:
        return ""
    steps: list[str] = []
    try:
        if field_kind == "recipient":
            steps.append(desktop_press_key_action("enter"))
            steps.append(desktop_press_key_action("tab"))
        elif field_kind == "subject":
            steps.append(desktop_press_key_action("tab"))
        elif field_kind == "body":
            if "\n" in typed:
                steps.append("Gmail body left focused after multiline entry")
    except Exception as exc:
        return f"Gmail commit failed: {exc}"
    return " | ".join(step for step in steps if step)


def _result_contains_verified_text(result: str, expected_text: str) -> bool:
    result_lower = str(result or "").lower()
    expected_lower = str(expected_text or "").lower().strip()
    if not expected_lower:
        return False
    return "verified on screen" in result_lower and expected_lower in result_lower


def _auto_verify_external_typed_text(task_description: str, typed_text: str) -> str:
    query = _text_verification_query(task_description, typed_text)
    if not query:
        return ""
    try:
        verification = find_text_on_screen_action(query)
    except Exception as exc:
        return f"Auto-verify unavailable for '{query}': {exc}"
    if "No screen text matched" in verification:
        return f"Auto-verify pending for '{query}'"
    return f"Verified on screen: {verification}"


def _auto_verify_gmail_typed_text(goal: str, task_description: str, typed_text: str) -> str:
    if not _is_gmail_task(goal, task_description):
        return ""
    query = _gmail_verification_query(task_description, typed_text)
    if not query:
        return ""
    try:
        verification = find_text_on_screen_action(query)
    except Exception as exc:
        return f"Gmail verify unavailable for '{query}': {exc}"
    if "No screen text matched" in verification:
        field_kind = _gmail_field_kind(task_description) or "field"
        return f"Gmail verify pending for {field_kind}: '{query}'"
    field_kind = _gmail_field_kind(task_description) or "field"
    return f"Gmail {field_kind} verified on screen: {verification}"


def _recent_expected_text_verified(history: list[dict], expected_text: str) -> bool:
    expected_lower = (expected_text or "").lower().strip()
    if not expected_lower:
        return False
    started_typing = False
    for item in reversed(history[-12:]):
        action_text = str(item.get("action", "")).lower()
        result_text = str(item.get("result", "")).lower()
        if not started_typing and action_text.startswith("desktop_type"):
            if _result_contains_verified_text(result_text, expected_lower):
                return True
            started_typing = True
            continue
        if not started_typing:
            continue
        if action_text.startswith("find_text_on_screen"):
            if "no screen text matched" not in result_text and expected_lower in result_text:
                return True
        if expected_lower in result_text:
            return True
        if action_text.startswith("complete_task"):
            break
    if not started_typing:
        return False
    for path in _iter_recent_artifact_paths(history, limit=12):
        if _artifact_text_contains(path, expected_lower):
            return True
    return False


def _recent_error_indicator(history: list[dict]) -> str:
    terms = ("error", "failed", "please specify", "required", "invalid", "could not")
    for item in reversed(history[-10:]):
        result_text = str(item.get("result", "")).lower()
        if any(term in result_text for term in terms):
            return result_text[:200]
    for path in _iter_recent_artifact_paths(history, limit=10):
        candidate = path
        if not os.path.isabs(candidate):
            candidate = os.path.join(WORKSPACE_ROOT, candidate)
        if not os.path.isfile(candidate) or not candidate.lower().endswith(".txt"):
            continue
        try:
            content = open(candidate, "r", encoding="utf-8", errors="ignore").read().lower()
        except Exception:
            continue
        for term in terms:
            if term in content:
                return content[:200]
    return ""


def _recent_external_text_entry_verified(history: list[dict], task_description: str) -> bool:
    # Return True if there is evidence in recent history that typed text landed.
    # Searches across task boundaries (no complete_task break) because typing often
    # happens in a prior task and verification in the current one.
    for item in reversed(history[-12:]):
        action_text = str(item.get("action", "")).lower()
        result_text = str(item.get("result", "")).lower()
        # desktop_type that already carried inline OCR verification
        if action_text.startswith("desktop_type") and "verified on screen" in result_text:
            return True
        # explicit OCR / screenshot taken after typing
        if action_text.startswith(("desktop_screenshot", "take_desktop_screenshot",
                                    "find_text_on_screen", "ocr_image_to_text")):
            return True
        # navigate action that succeeded = URL bar was filled correctly
        if action_text.startswith("navigate") and "focused window" in result_text:
            return True
    return False


def _recent_focus_established_for_text_entry(history: list[dict]) -> bool:
    # Search across task boundaries (last 10 entries). The agent often clicks
    # in one task then types in the next, so breaking at complete_task was wrong.
    for item in reversed(history[-10:]):
        action_text = str(item.get("action", "")).lower()
        if action_text.startswith(("desktop_click", "desktop_press_key", "desktop_hotkey", "focus_window", "navigate")):
            return True
        if action_text.startswith("desktop_type") and "blocked" not in action_text:
            return True
    return False


def _recent_strong_verification_signal(history: list[dict]) -> bool:
    for item in reversed(history[-10:]):
        action_text = str(item.get("action", "")).lower()
        if action_text.startswith(("find_text_on_screen", "ocr_image_to_text", "read_page", "read_selection")):
            return True
        if action_text.startswith("complete_task"):
            break
    return False


def _recent_repeated_action_count(history: list[dict], prefix: str, match_text: str = "", match_coords: tuple[int, int] | None = None) -> int:
    count = 0
    lowered_text = match_text.lower()
    for item in reversed(history[-12:]):
        action_text = str(item.get("action", "")).lower()
        if not action_text.startswith(prefix):
            continue
        if lowered_text and lowered_text not in action_text:
            continue
        if match_coords is not None:
            coord_text = f"\"x\": {match_coords[0]}"
            coord_text_y = f"\"y\": {match_coords[1]}"
            if coord_text not in action_text or coord_text_y not in action_text:
                continue
        count += 1
    return count


def _try_remembered_open_method(command: str, cfg: dict, from_manual: bool = False) -> str | None:
    goal, task_description = _current_session_goal_and_task()
    query = "\n".join(part for part in (goal, task_description, command) if part).strip()
    if not query:
        return None
    query_lower = query.lower()
    already_open = any(token in query_lower for token in ("already open", "already launched", "already running", "is already open"))
    candidates = method_memory.retrieve(query, limit=6)
    preferred_order = ["focus_window", "run_command", "open_application"] if already_open else ["run_command", "open_application", "focus_window"]
    ordered_candidates = sorted(
        candidates,
        key=lambda entry: preferred_order.index(entry.action_name) if entry.action_name in preferred_order else len(preferred_order),
    )
    for entry in ordered_candidates:
        payload = entry.action_payload or {}
        action_name = entry.action_name
        try:
            if action_name == "focus_window":
                text = str(payload.get("text", "")).strip()
                if not text:
                    continue
                result = focus_window_action(text)
                if _result_confirms_browser_window(query, result) or not _is_external_browser_goal_task(goal, task_description):
                    return "Reused remembered method: " + result
                continue
            if action_name == "open_application":
                remembered_command = str(payload.get("command", "")).strip()
                if not remembered_command:
                    continue
                result = open_application_action(remembered_command, str(payload.get("cwd", "")))
                # Sanity-check: the opened/focused window title must contain at least
                # one token from the requested command — otherwise we landed on the
                # wrong window (e.g. Edge when asked to open WhatsApp).
                app_tokens = [t for t in (command or "").lower().replace(".", " ").split() if len(t) > 2]
                result_lower = result.lower()
                window_matches_app = not app_tokens or any(t in result_lower for t in app_tokens)
                if not window_matches_app:
                    continue  # wrong window — skip this remembered method, try fresh launch
                if _result_confirms_browser_window(query, result) or not _is_external_browser_goal_task(goal, task_description):
                    return "Reused remembered method: " + result
                continue
            if action_name == "run_command":
                mode = str(cfg.get("command_execution_mode", "manual"))
                if mode == "disabled":
                    continue
                if mode == "manual" and not from_manual:
                    continue
                remembered_command = str(payload.get("command", "")).strip()
                if not remembered_command:
                    continue
                request = prepare_command_action(
                    remembered_command,
                    str(payload.get("cwd", "")),
                    cfg,
                    WORKSPACE_ROOT,
                )
                return "Reused remembered method:\n" + run_command_action(
                    request,
                    timeout=int(cfg.get("command_timeout_seconds", 120)),
                )
        except Exception:
            continue
    return None


async def screenshot_b64() -> str:
    if not page: return ""
    try:
        buf = await page.screenshot(type="jpeg", quality=60, full_page=False)
        return base64.b64encode(buf).decode()
    except Exception: return ""


def _extract_first_url(text: str) -> str:
    match = re.search(r"https?://[^\s'\"<>]+", text or "")
    return match.group(0) if match else ""


# ══════════════════════════════════════════════════════════════════════════
# ACTION EXECUTOR
# ══════════════════════════════════════════════════════════════════════════
async def execute_action(action: dict, from_manual: bool = False) -> str:
    global page, _last_screenshot_b64
    act    = action.get("action", "")
    params = {k:v for k,v in action.items()
              if k not in ("action","thought","summary","task_id","finding","reason")}
    if session_log: session_log.action(act, params)
    try:
        cfg = load_config()

        def _resolve_upload_paths(single_path: str = "", multiple_paths: list[str] | None = None) -> list[str]:
            if not cfg.get("local_file_access_enabled", True):
                raise ValueError("Local file access is disabled in settings.")
            raw_paths = list(multiple_paths or [])
            if single_path:
                raw_paths.insert(0, single_path)
            if not raw_paths:
                raise ValueError("A local file path is required")
            resolved_paths: list[str] = []
            for item in raw_paths:
                resolved = resolve_allowed_path(str(item), cfg, WORKSPACE_ROOT)
                if not os.path.isfile(resolved):
                    raise ValueError(f"File not found for upload: {resolved}")
                resolved_paths.append(resolved)
            return resolved_paths

        def _desktop_policy(interactive: bool) -> str | None:
            if not cfg.get("desktop_automation_enabled", False):
                return "Desktop automation is disabled in settings."
            scope = str(cfg.get("desktop_autonomy_scope", "browser_only"))
            if not from_manual and scope != "browser_and_desktop":
                return ("Desktop actions are blocked during autonomous runs because "
                        "desktop_autonomy_scope=browser_only.")
            if interactive:
                mode = str(cfg.get("desktop_execution_mode", "manual"))
                if mode == "disabled":
                    return "Interactive desktop control is disabled in settings."
                if mode == "manual" and not from_manual:
                    return ("Interactive desktop action not auto-run because "
                            "desktop_execution_mode=manual. Use manual mode if desired.")
            return None

        if act == "open_application":
            blocked = _desktop_policy(interactive=True)
            command = action.get("command", "")
            cwd = action.get("cwd", "")
            if blocked:
                result = blocked
            else:
                remembered = _try_remembered_open_method(command, cfg, from_manual=from_manual)
                result = remembered or open_application_action(command, cwd)

        elif act == "list_windows":
            blocked = _desktop_policy(interactive=False)
            result = blocked or list_windows_action()

        elif act == "focus_window":
            blocked = _desktop_policy(interactive=True)
            result = blocked or focus_window_action(action.get("text",""))

        elif act in ("desktop_click", "click_desktop"):
            blocked = _desktop_policy(interactive=True)
            if blocked:
                result = blocked
            else:
                button = (action.get("button", "") or "left").lower()
                clicks = max(1, int(action.get("amount", 1) or 1))
                if button == "double":
                    button = "left"
                    clicks = max(clicks, 2)
                result = desktop_click_action(
                    int(action.get("x", 0)),
                    int(action.get("y", 0)),
                    button=button,
                    clicks=clicks,
                )

        elif act == "move_mouse":
            blocked = _desktop_policy(interactive=True)
            result = blocked or move_mouse_action(
                int(action.get("x", 0)),
                int(action.get("y", 0)),
            )

        elif act == "drag_mouse":
            blocked = _desktop_policy(interactive=True)
            result = blocked or drag_mouse_action(
                int(action.get("x", 0)),
                int(action.get("y", 0)),
                int(action.get("end_x", 0)),
                int(action.get("end_y", 0)),
                button=(action.get("button", "") or "left"),
            )

        elif act in ("desktop_type", "type_desktop"):
            blocked = _desktop_policy(interactive=True)
            if blocked:
                result = blocked
            else:
                typed_text = str(action.get("text", ""))
                goal, task_description = _current_session_goal_and_task()
                task_environment = _choose_task_environment(goal, task_description)
                task_lower = task_description.lower()
                typing_mode = str(action.get("typing_mode", "auto") or "auto").lower()
                if typing_mode not in {"auto", "raw", "paste", "char"}:
                    typing_mode = "auto"
                if typing_mode == "auto" and task_environment in {"external", "hybrid"}:
                    if "gmail" in f"{goal}\n{task_description}".lower() and any(
                        token in task_lower for token in ("recipient", "to field", "email address", "subject")
                    ):
                        # Gmail To/Subject in Chrome/Edge blocks paste and unicode injection.
                        # Use verified typing which auto-retries with slower delays if OCR shows nothing landed.
                        typing_mode = "char"
                # For external/hybrid Gmail tasks use the verified variant which retries on failure
                is_gmail_field = (
                    task_environment in {"external", "hybrid"}
                    and "gmail" in f"{goal}\n{task_description}".lower()
                    and any(token in task_lower for token in ("recipient", "to field", "email address", "subject", "body", "compose"))
                )
                if is_gmail_field:
                    result = desktop_type_verified_action(typed_text, typing_mode=typing_mode)
                else:
                    result = desktop_type_action(typed_text, typing_mode=typing_mode)
                if task_environment in {"external", "hybrid"}:
                    gmail_commit_result = _gmail_post_type_commit(goal, task_description, typed_text)
                    if gmail_commit_result:
                        result = f"{result} | {gmail_commit_result}"
                    elif (
                        typed_text
                        and "@" in typed_text
                        and any(token in task_lower for token in ("recipient", "to field", "email address"))
                    ):
                        # Generic recipient/token fields often need an extra commit keystroke.
                        commit_result = desktop_press_key_action("tab")
                        result = f"{result} | {commit_result}"
                    gmail_verification_result = _auto_verify_gmail_typed_text(goal, task_description, typed_text)
                    if gmail_verification_result:
                        result = f"{result} | {gmail_verification_result}"
                    verification_result = _auto_verify_external_typed_text(task_description, typed_text)
                    if verification_result:
                        result = f"{result} | {verification_result}"

        elif act in ("desktop_scroll", "scroll_desktop"):
            blocked = _desktop_policy(interactive=True)
            result = blocked or desktop_scroll_action(int(action.get("amount", 0)))

        elif act in ("desktop_hotkey", "hotkey"):
            blocked = _desktop_policy(interactive=True)
            result = blocked or desktop_hotkey_action(action.get("keys", []) or [])

        elif act in ("desktop_press_key", "press_key"):
            blocked = _desktop_policy(interactive=True)
            result = blocked or desktop_press_key_action(action.get("key","Enter"))

        elif act == "select_menu_item":
            blocked = _desktop_policy(interactive=True)
            if blocked:
                result = blocked
            elif action.get("text", "").strip():
                result = select_menu_item_action(action.get("text",""))
            else:
                raise ValueError("select_menu_item requires a menu path in text, for example 'File>Open'")

        elif act == "close_window":
            blocked = _desktop_policy(interactive=True)
            result = blocked or close_window_action(action.get("text",""))

        elif act in ("desktop_screenshot", "take_desktop_screenshot"):
            blocked = _desktop_policy(interactive=False)
            if blocked:
                result = blocked
            else:
                # Always capture base64 in memory so the model can see the screen
                try:
                    _last_screenshot_b64, sw, sh = desktop_screenshot_b64()
                    result = f"Desktop screenshot captured ({sw}x{sh}) — image will be attached to next model query"
                except Exception as exc:
                    _last_screenshot_b64 = None
                    result = f"Screenshot capture failed: {exc}"
                # Also save to file if a path was given and file writes are enabled
                if action.get("path") and cfg.get("local_file_write_enabled", True):
                    try:
                        target_path = resolve_allowed_path(action.get("path",""), cfg, WORKSPACE_ROOT)
                        parent = os.path.dirname(target_path)
                        if parent:
                            os.makedirs(parent, exist_ok=True)
                        desktop_screenshot_action(target_path)
                        result += f" | Also saved to {target_path}"
                        repo_index.invalidate()
                    except Exception as exc:
                        result += f" | File save failed: {exc}"

        elif act in ("desktop_screenshot_query", "screenshot_and_ask"):
            # Take a screenshot and immediately query the model about what it sees.
            # The model's answer is returned as the action result so it can be used
            # in the next step without a separate round-trip.
            blocked = _desktop_policy(interactive=False)
            if blocked:
                result = blocked
            else:
                try:
                    _last_screenshot_b64, sw, sh = desktop_screenshot_b64()
                    question = str(action.get("question") or "Describe what you see on screen. What UI elements are visible? What is the current state of the application?")
                    vision_prompt = f"You are looking at a screenshot of the current desktop ({sw}x{sh} pixels).\n\n{question}"


                    vision_answer, _, _ = await call_with_fallback(
                        "You are a precise desktop UI analyst. Describe exactly what you see.",
                        vision_prompt,
                        current_provider, current_model, cfg,
                        log_query=False,
                        image_b64=_last_screenshot_b64,
                    )
                    result = f"Screenshot vision query result ({sw}x{sh}):\n{vision_answer}"

                except Exception as exc:
                    _last_screenshot_b64 = None
                    result = f"Screenshot vision query failed: {exc}"

        elif act == "find_text_on_screen":
            blocked = _desktop_policy(interactive=False)
            result = blocked or find_text_on_screen_action(action.get("text",""))

        elif act in ("list_installed_apps", "list_apps"):
            blocked = _desktop_policy(interactive=False)
            result = blocked or list_installed_apps_action(action.get("query", ""))

        elif act == "gmail_compose":
            # Keyboard-driven Gmail compose: fills To/Subject/Body without any
            # coordinate guessing.  Requires Gmail to be the active foreground tab.
            blocked = _desktop_policy(interactive=True)
            if blocked:
                result = blocked
            else:
                result = gmail_compose_action(
                    to=action.get("to", ""),
                    subject=action.get("subject", ""),
                    body=action.get("body", ""),
                    window_focused=True,
                )

        elif act == "navigate":
            url = action.get("url","")
            if not url.startswith("http"): url = "https://"+url
            goal, task_description = _current_session_goal_and_task()
            browser_command = _external_browser_command(goal, task_description)
            if _current_session_environment() == "external" and browser_command:
                blocked = _desktop_policy(interactive=True)
                result = blocked or open_browser_url_action(browser_command, url)
            else:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(1); result = f"Navigated to {url}"

        elif act == "click":
            x,y  = action.get("x"), action.get("y")
            sel  = action.get("selector"); txt = action.get("text","")
            if sel:
                await page.click(sel, timeout=5000)
            elif x is not None and y is not None:
                # Reject clearly off-screen coordinates
                if y < -100 or y > 900 or x < -100 or x > 1400:
                    result = (f"REJECTED: coordinate ({x},{y}) is off-screen. "
                              f"Use visible elements only (y: 0-720).")
                    if session_log: session_log.result(result)
                    return result
                # Use native Playwright mouse events — JS dispatchEvent ignored by React/WhatsApp
                await page.mouse.move(x, y)
                await asyncio.sleep(0.05)
                await page.mouse.click(x, y)
            elif txt:
                # Try JS find-by-text click first (more reliable than Playwright's)
                js_ok = await page.evaluate("""(searchText) => {
                    const walk = (el) => {
                        if (!el) return null;
                        if (el.nodeType === 3) return null;
                        const t = (el.innerText || el.textContent || '').trim();
                        if (t.includes(searchText) && el.getBoundingClientRect().width > 0) {
                            return el;
                        }
                        for (const c of el.children) {
                            const found = walk(c);
                            if (found) return found;
                        }
                        return null;
                    };
                    const el = walk(document.body);
                    if (el) { el.click(); return true; }
                    return false;
                }""", txt[:50])
                if not js_ok:
                    await page.get_by_text(txt, exact=False).first.click(timeout=5000)
            await asyncio.sleep(0.8); result = f"Clicked ({x},{y})"

        elif act == "upload_file_to_page":
            upload_paths = _resolve_upload_paths(action.get("path",""))
            selector = action.get("selector", "")
            if not selector:
                raise ValueError("selector is required for upload_file_to_page")
            await page.set_input_files(selector, upload_paths[0], timeout=10000)
            await asyncio.sleep(0.8)
            result = f"Uploaded file to page via {selector}: {upload_paths[0]}"

        elif act == "upload_files_to_page":
            upload_paths = _resolve_upload_paths(multiple_paths=action.get("paths", []) or [])
            selector = action.get("selector", "")
            if not selector:
                raise ValueError("selector is required for upload_files_to_page")
            await page.set_input_files(selector, upload_paths, timeout=10000)
            await asyncio.sleep(0.8)
            result = f"Uploaded {len(upload_paths)} files to page via {selector}"

        elif act == "click_and_upload":
            upload_paths = _resolve_upload_paths(
                single_path=action.get("path",""),
                multiple_paths=action.get("paths", []) or [],
            )
            selector = action.get("selector", "")
            txt = action.get("text", "")
            x, y = action.get("x"), action.get("y")
            async with page.expect_file_chooser(timeout=10000) as chooser_info:
                if selector:
                    await page.click(selector, timeout=5000)
                elif txt:
                    await page.get_by_text(txt, exact=False).first.click(timeout=5000)
                elif x is not None and y is not None:
                    await page.mouse.click(x, y)
                else:
                    raise ValueError("click_and_upload requires selector, text, or x/y")
            chooser = await chooser_info.value
            await chooser.set_files(upload_paths)
            await asyncio.sleep(0.8)
            result = f"Uploaded {len(upload_paths)} file{'s' if len(upload_paths) != 1 else ''} via file chooser"

        elif act == "click_text":
            # Find element by visible text → get its coordinates → use real Playwright click
            # DO NOT use JS dispatchEvent — React/WhatsApp ignores synthetic events.
            # Real native mouse events from Playwright are the only reliable method.
            search = action.get("text","")
            if _current_session_environment() == "external":
                blocked = _desktop_policy(interactive=True)
                if not search:
                    result = "click_text requires a 'text' field"
                else:
                    result = blocked or click_text_on_screen_action(search, int(action.get("occurrence", 1) or 1))
            elif search:
                # Step 1: use JS only to FIND coordinates, not to click
                coords = await page.evaluate("""(searchText) => {
                    const needle = searchText.toLowerCase().trim();

                    // Strategy 1: find an element whose text STARTS WITH the search term
                    // (contact name rows start with the name, so this is most precise)
                    let exactMatch = null;
                    const allEls = document.querySelectorAll('*');
                    for (const el of allEls) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width === 0 || rect.height === 0) continue;
                        if (rect.top < -10 || rect.top > window.innerHeight + 10) continue;
                        if (el.children.length > 5) continue; // skip containers
                        const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                        // Exact start match — "thompson me" starts with "thompson me"
                        if (t === needle || t.startsWith(needle)) {
                            exactMatch = el;
                            break;
                        }
                    }

                    // Strategy 2: fallback to contains match if no start match
                    if (!exactMatch) {
                        let bestScore = 999999;
                        for (const el of allEls) {
                            const rect = el.getBoundingClientRect();
                            if (rect.width === 0 || rect.height === 0) continue;
                            if (rect.top < -10 || rect.top > window.innerHeight + 10) continue;
                            if (el.children.length > 5) continue;
                            const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                            if (t.includes(needle) && t.length < bestScore) {
                                bestScore = t.length;
                                exactMatch = el;
                            }
                        }
                    }

                    if (!exactMatch) return null;

                    // Walk UP to find the clickable row container
                    // Stop at role=listitem/row, or when height > 40px (a real row)
                    let target = exactMatch;
                    for (let i = 0; i < 15; i++) {
                        if (!target || target === document.body) break;
                        const role = (target.getAttribute('role') || '').toLowerCase();
                        const tag  = target.tagName;
                        const rect = target.getBoundingClientRect();
                        if (role === 'listitem' || role === 'row' || role === 'option' ||
                            role === 'gridcell' || tag === 'LI' || tag === 'TR') {
                            break;
                        }
                        // Stop when we hit something row-sized
                        if (rect.height >= 48 && rect.height <= 120 && rect.width > 200) {
                            break;
                        }
                        target = target.parentElement;
                    }
                    if (!target) target = exactMatch;

                    const rect = target.getBoundingClientRect();
                    // Click at 30% from left (avoids avatar/icon on the right)
                    return {
                        x: Math.round(rect.left + rect.width * 0.4),
                        y: Math.round(rect.top  + rect.height / 2),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                        matched: (exactMatch.innerText||'').trim().slice(0, 80),
                        container_role: (target.getAttribute('role')||target.tagName)
                    };
                }""", search)

                if coords:
                    cx, cy = coords["x"], coords["y"]
                    # Clamp to viewport
                    cx = max(10, min(cx, 1270))
                    cy = max(10, min(cy, 710))
                    # Full native Playwright pointer sequence
                    await page.mouse.move(cx, cy)
                    await asyncio.sleep(0.15)
                    await page.mouse.down()
                    await asyncio.sleep(0.05)
                    await page.mouse.up()
                    await asyncio.sleep(1.2)   # wait for React to render new view
                    result = (f"click_text '{search}' → matched:'{coords.get('matched','')}' "
                              f"container:{coords.get('container_role','')} "
                              f"at ({cx},{cy}) size={coords.get('w')}x{coords.get('h')}")
                else:
                    # Fallback: use Playwright's built-in locator
                    try:
                        await page.get_by_text(search, exact=False).first.click(timeout=5000)
                        await asyncio.sleep(1.0)
                        result = f"click_text '{search}' via Playwright locator"
                    except Exception as e:
                        result = f"click_text: could not find '{search}' — {e}"
            else:
                result = "click_text requires a 'text' field"

        elif act == "type":
            text = action.get("text",""); sel = action.get("selector")
            x,y  = action.get("x"), action.get("y")

            async def _smart_type(px=None, py=None, psel=None):
                """
                Handles three field types:
                  1. CSS selector → use fill() (standard inputs)
                  2. x,y click → detect if contenteditable or standard input, handle both
                  3. No target → use active element
                """
                if psel:
                    await page.fill(psel, text, timeout=5000)
                    return

                if px is not None:
                    await page.mouse.click(px, py)
                    await asyncio.sleep(0.25)

                # Detect what type of element is focused
                el_info = await page.evaluate("""() => {
                    const el = document.activeElement;
                    if (!el) return {type:'unknown'};
                    const ce = el.getAttribute('contenteditable');
                    if (ce === 'true' || ce === '') return {type:'contenteditable'};
                    if (el.tagName === 'INPUT')    return {type:'input', itype: el.type};
                    if (el.tagName === 'TEXTAREA') return {type:'textarea'};
                    // Check parent contenteditable (WhatsApp wraps in a p inside a div)
                    let p = el.parentElement;
                    for (let i=0; i<4; i++) {
                        if (!p) break;
                        const pce = p.getAttribute('contenteditable');
                        if (pce === 'true' || pce === '') return {type:'contenteditable'};
                        p = p.parentElement;
                    }
                    return {type:'unknown'};
                }""")

                el_type = el_info.get("type", "unknown")

                if el_type == "contenteditable":
                    # Clear via JS + set text via clipboard paste (most reliable for contenteditable)
                    await page.evaluate("""(txt) => {
                        const el = document.activeElement;
                        // Walk up to find actual contenteditable root
                        let target = el;
                        for (let i=0; i<4; i++) {
                            if (!target) break;
                            const ce = target.getAttribute('contenteditable');
                            if (ce === 'true' || ce === '') break;
                            target = target.parentElement;
                        }
                        if (target) {
                            // Select all and delete existing content
                            const range = document.createRange();
                            range.selectNodeContents(target);
                            const sel = window.getSelection();
                            sel.removeAllRanges();
                            sel.addRange(range);
                        }
                    }""", text)
                    await page.keyboard.press("Backspace")
                    await asyncio.sleep(0.1)
                    # Type character by character for contenteditable
                    await page.keyboard.type(text, delay=30)
                    await asyncio.sleep(0.1)
                else:
                    # Standard input/textarea — clear with JS then type
                    try:
                        await page.evaluate("""() => {
                            const el = document.activeElement;
                            if (el && ('value' in el)) {
                                el.value = '';
                                el.dispatchEvent(new Event('input', {bubbles:true}));
                            }
                        }""")
                    except Exception:
                        await page.keyboard.press("Control+a")
                        await asyncio.sleep(0.05)
                        await page.keyboard.press("Backspace")
                        await asyncio.sleep(0.05)
                    await page.keyboard.type(text, delay=40)

            if sel:
                await _smart_type(psel=sel)
            elif x is not None and y is not None:
                await _smart_type(px=x, py=y)
            else:
                await _smart_type()
            result = f"Typed: '{text}'"

        elif act == "type_and_submit":
            # Type text then press Enter — for chat inputs, search boxes, any field
            # where submit is triggered by Enter rather than a button
            text = action.get("text",""); sel = action.get("selector")
            x,y  = action.get("x"), action.get("y")
            # Reuse type logic by recursing with action="type"
            type_action = {**action, "action": "type"}
            type_result = await execute_action(type_action)
            await asyncio.sleep(0.3)
            await page.keyboard.press("Enter")
            await asyncio.sleep(0.5)
            result = f"Typed and submitted: '{text[:80]}'"

        elif act == "press":
            key = action.get("key","Enter"); await page.keyboard.press(key)
            await asyncio.sleep(0.5); result = f"Pressed {key}"

        elif act == "scroll":
            direction = action.get("direction","down"); amount = action.get("amount",300)
            await page.mouse.wheel(0, amount if direction=="down" else -amount)
            await asyncio.sleep(0.4); result = f"Scrolled {direction} {amount}px"

        elif act == "wait":
            secs = min(action.get("seconds",1), 10)
            await asyncio.sleep(secs); result = f"Waited {secs}s"

        elif act == "back":
            await page.go_back(); await asyncio.sleep(1); result = "Navigated back"

        elif act == "focus_field":
            # Find ANY input/textarea by id, name, placeholder, or label text,
            # scroll it into view, and focus it — even if it's hidden in a tab/panel
            field_hint = action.get("hint","")  # id, name, placeholder, or label text
            found = await page.evaluate("""(hint) => {
                const h = hint.toLowerCase();
                // Try id, name, placeholder attributes
                let el = document.getElementById(hint)
                      || document.querySelector(`[name="${hint}"]`)
                      || document.querySelector(`[placeholder*="${hint}"]`);
                // Try label text match
                if (!el) {
                    for (const label of document.querySelectorAll('label')) {
                        if ((label.innerText||'').toLowerCase().includes(h)) {
                            const forId = label.getAttribute('for');
                            if (forId) el = document.getElementById(forId);
                            if (!el) el = label.nextElementSibling;
                            break;
                        }
                    }
                }
                // Try input placeholder/aria-label fuzzy match
                if (!el) {
                    for (const inp of document.querySelectorAll('input,textarea,select')) {
                        const ph = (inp.placeholder||inp.getAttribute('aria-label')||
                                    inp.getAttribute('data-placeholder')||'').toLowerCase();
                        if (ph.includes(h)) { el = inp; break; }
                    }
                }
                if (!el) return null;
                // Make visible: expand any collapsed parent, activate tab if needed
                let p = el.parentElement;
                while (p && p !== document.body) {
                    if (p.style.display === 'none')  p.style.display = '';
                    if (p.style.visibility === 'hidden') p.style.visibility = 'visible';
                    if (p.hidden) p.hidden = false;
                    // Click tab if element is inside a tab panel that isn't active
                    if (p.getAttribute('role') === 'tabpanel' && 
                        !p.classList.contains('active') && !p.classList.contains('selected')) {
                        const tabId = p.getAttribute('aria-labelledby') || p.id;
                        if (tabId) {
                            const tab = document.querySelector(`[href="#${tabId}"], [data-link="${tabId}"]`);
                            if (tab) tab.click();
                        }
                    }
                    p = p.parentElement;
                }
                el.scrollIntoView({behavior:'smooth', block:'center'});
                el.focus();
                const rect = el.getBoundingClientRect();
                return {x: Math.round(rect.left+rect.width/2),
                        y: Math.round(rect.top+rect.height/2),
                        tag: el.tagName, type: el.type||'', id: el.id, name: el.name||''};
            }""", field_hint)
            if found:
                await asyncio.sleep(0.5)
                # Click it natively to fully activate
                cx, cy = found["x"], found["y"]
                if 0 < cy < 900:
                    await page.mouse.click(cx, cy)
                result = (f"Focused field hint='{field_hint}' → "
                          f"<{found['tag']} id={found['id']} name={found['name']} type={found['type']}> "
                          f"at ({cx},{cy})")
            else:
                result = f"focus_field: could not find field matching '{field_hint}'"

        elif act == "read_page":
            from page_reader import extract_full_page_text
            full = await extract_full_page_text(page, cfg.get("max_text_chars",8000)*2)
            result = f"Full page read ({len(full)} chars): {full[:300]}..."

        elif act == "screenshot_page_to_file":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                screenshot_path = action.get("path", "")
                if not screenshot_path:
                    raise ValueError("path is required")
                target_path = resolve_allowed_path(screenshot_path, cfg, WORKSPACE_ROOT)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                await page.screenshot(path=target_path, full_page=bool(action.get("full_page", False)))
                result = f"Saved page screenshot: {target_path}"
                repo_index.invalidate()

        elif act == "read_file":
            if not cfg.get("local_file_access_enabled", True):
                result = "Local file access is disabled in settings."
            else:
                result = read_file_action(action.get("path",""), cfg, WORKSPACE_ROOT)

        elif act == "read_json":
            if not cfg.get("local_file_access_enabled", True):
                result = "Local file access is disabled in settings."
            else:
                result = read_json_action(
                    action.get("path",""),
                    cfg,
                    WORKSPACE_ROOT,
                    max_chars=int(cfg.get("max_text_chars", 8000)),
                )

        elif act == "read_csv":
            if not cfg.get("local_file_access_enabled", True):
                result = "Local file access is disabled in settings."
            else:
                result = read_csv_action(action.get("path",""), cfg, WORKSPACE_ROOT)

        elif act == "list_directory":
            if not cfg.get("local_file_access_enabled", True):
                result = "Local file access is disabled in settings."
            else:
                result = list_directory_action(action.get("path","."), cfg, WORKSPACE_ROOT)

        elif act == "create_directory":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = create_directory_action(action.get("path",""), cfg, WORKSPACE_ROOT)
                repo_index.invalidate()

        elif act == "create_file":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = create_file_action(action.get("path",""), action.get("content",""), cfg, WORKSPACE_ROOT)
                repo_index.invalidate()

        elif act == "create_json":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = create_json_action(action.get("path",""), action.get("content",""), cfg, WORKSPACE_ROOT)
                repo_index.invalidate()

        elif act == "create_csv":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = create_csv_action(action.get("path",""), action.get("content",""), cfg, WORKSPACE_ROOT)
                repo_index.invalidate()

        elif act == "create_pdf":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = create_pdf_action(
                    action.get("path",""),
                    action.get("title",""),
                    action.get("content",""),
                    cfg,
                    WORKSPACE_ROOT,
                )
                repo_index.invalidate()

        elif act == "create_markdown_report":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = create_markdown_report_action(
                    action.get("path",""),
                    action.get("title",""),
                    action.get("content",""),
                    cfg,
                    WORKSPACE_ROOT,
                )
                repo_index.invalidate()

        elif act == "write_yaml":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = write_yaml_action(
                    action.get("path",""),
                    action.get("content",""),
                    cfg,
                    WORKSPACE_ROOT,
                )
                repo_index.invalidate()

        elif act == "write_file":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = write_file_action(
                    action.get("path",""),
                    action.get("content",""),
                    bool(action.get("append", False)),
                    cfg,
                    WORKSPACE_ROOT,
                )
                repo_index.invalidate()

        elif act == "extract_pdf_text":
            if not cfg.get("local_file_access_enabled", True):
                result = "Local file access is disabled in settings."
            else:
                result = extract_pdf_text_action(
                    action.get("path",""),
                    action.get("destination",""),
                    cfg,
                    WORKSPACE_ROOT,
                    max_chars=int(cfg.get("max_text_chars", 8000)),
                )
                if action.get("destination"):
                    repo_index.invalidate()

        elif act == "ocr_image_to_text":
            if not cfg.get("local_file_access_enabled", True):
                result = "Local file access is disabled in settings."
            else:
                result = ocr_image_to_text_action(
                    action.get("path",""),
                    action.get("destination",""),
                    cfg,
                    WORKSPACE_ROOT,
                    max_chars=int(cfg.get("max_text_chars", 8000)),
                )
                if action.get("destination"):
                    repo_index.invalidate()

        elif act == "patch_file":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = patch_file_action(
                    action.get("path",""),
                    action.get("old_text",""),
                    action.get("new_text",""),
                    bool(action.get("replace_all", False)),
                    cfg,
                    WORKSPACE_ROOT,
                )
                repo_index.invalidate()

        elif act == "save_page_html":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                target_path = resolve_allowed_path(action.get("path",""), cfg, WORKSPACE_ROOT)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                html = await page.content()
                with open(target_path, "w", encoding="utf-8") as handle:
                    handle.write(html)
                result = f"Saved page HTML: {target_path}"
                repo_index.invalidate()

        elif act == "print_page_to_pdf":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                target_path = resolve_allowed_path(action.get("path",""), cfg, WORKSPACE_ROOT)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                await page.pdf(path=target_path, print_background=True)
                result = f"Saved page PDF: {target_path}"
                repo_index.invalidate()

        elif act == "download_url":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = download_url_action(
                    action.get("url",""),
                    action.get("destination",""),
                    cfg,
                    WORKSPACE_ROOT,
                    timeout=min(int(cfg.get("command_timeout_seconds", 120)), 300),
                )
                repo_index.invalidate()

        elif act == "extract_archive":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = extract_archive_action(
                    action.get("source",""),
                    action.get("destination",""),
                    cfg,
                    WORKSPACE_ROOT,
                )
                repo_index.invalidate()

        elif act == "search_in_files":
            if not cfg.get("local_file_access_enabled", True):
                result = "Local file access is disabled in settings."
            else:
                result = search_in_files_action(
                    action.get("path","."),
                    action.get("pattern",""),
                    cfg,
                    WORKSPACE_ROOT,
                    recursive=bool(action.get("recursive", True)),
                )

        elif act == "convert_image_format":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = convert_image_format_action(
                    action.get("source",""),
                    action.get("destination",""),
                    action.get("format",""),
                    cfg,
                    WORKSPACE_ROOT,
                )
                repo_index.invalidate()

        elif act == "rename_path":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = rename_path_action(action.get("source",""), action.get("destination",""), cfg, WORKSPACE_ROOT)
                repo_index.invalidate()

        elif act == "move_paths":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = move_paths_action(action.get("sources", []) or [], action.get("destination",""), cfg, WORKSPACE_ROOT)
                repo_index.invalidate()

        elif act == "copy_paths":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = copy_paths_action(action.get("sources", []) or [], action.get("destination",""), cfg, WORKSPACE_ROOT)
                repo_index.invalidate()

        elif act == "delete_paths":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = delete_paths_action(
                    action.get("paths", []) or [],
                    bool(action.get("recursive", False)),
                    cfg,
                    WORKSPACE_ROOT,
                )
                repo_index.invalidate()

        elif act == "zip_paths":
            if not cfg.get("local_file_write_enabled", True):
                result = "Local file writes are disabled in settings."
            else:
                result = zip_paths_action(action.get("sources", []) or [], action.get("destination",""), cfg, WORKSPACE_ROOT)
                repo_index.invalidate()

        elif act == "run_command":
            mode = str(cfg.get("command_execution_mode", "manual"))
            request = prepare_command_action(
                action.get("command",""),
                action.get("cwd","."),
                cfg,
                WORKSPACE_ROOT,
            )
            if mode == "disabled":
                result = "Command execution is disabled in settings."
            elif mode == "manual" and not from_manual:
                result = (f"Command not auto-run because command_execution_mode=manual. "
                          f"Review and run manually if desired: {request.command} (cwd={request.resolved_cwd})")
            else:
                result = run_command_action(
                    request,
                    timeout=int(cfg.get("command_timeout_seconds", 120)),
                )

        elif act in ("complete_task","skip_task","done"):
            result = f"TASK_ACTION:{act}"  # handled in agent loop

        else:
            result = f"Unknown action: {act}"

    except Exception as e:
        result = f"Action failed: {e}"

    if session_log and act not in ("complete_task","skip_task","done"):
        session_log.result(result)
    return result


# ══════════════════════════════════════════════════════════════════════════
# STUCK DETECTOR
# ══════════════════════════════════════════════════════════════════════════
class StuckDetector:
    def __init__(self, threshold=3):
        self.threshold = threshold
        self.recent: list = []

    def check(self, action, url, extra="") -> str | None:
        # Track both full key and action-only key to catch loops at different coords/urls
        full_key   = (action, url)
        action_key = action  # action type only

        self.recent.append((full_key, action_key))
        if len(self.recent) > self.threshold * 3:
            self.recent = self.recent[-self.threshold*3:]

        last_n = self.recent[-self.threshold:]

        # Same action+url N times
        if len(last_n) == self.threshold and all(r[0]==last_n[0][0] for r in last_n):
            return (f"You've done '{action}' on '{url}' {self.threshold} times. "
                    "Nothing is changing. Try a completely different approach — "
                    "use navigate with a direct URL instead of clicking.")

        # Same action type N times (even if url/coords differ) — except navigate which is fine
        if action not in ("navigate", "wait", "scroll") and len(last_n) == self.threshold:
            if all(r[1]==action_key for r in last_n):
                return (f"You've used the '{action}' action {self.threshold} times in a row "
                        "without progress. The element coordinates may be wrong. "
                        "Try using navigate with a direct URL instead.")
        return None

    def reset(self): self.recent = []


# ══════════════════════════════════════════════════════════════════════════
# SAVE RESULT
# ══════════════════════════════════════════════════════════════════════════
def save_result(tracker: TaskTracker, report: str, history: list,
                provider: str, model: str) -> str:
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r'[^a-zA-Z0-9 ]','',tracker.goal)[:40].strip().replace(' ','_')
    fp   = os.path.join(RESULTS_DIR, f"{ts}_{safe or 'result'}.txt")
    task_summary = "\n".join([
        f"  [{t.status.upper()}] {t.description}"
        + (f"\n    → {t.finding}" if t.finding else "")
        for t in tracker.tasks
    ])
    hist_text = "\n".join([f"  Step {i+1}: {h['action']} => {h['result']}"
                           for i,h in enumerate(history)])
    with open(fp, "w", encoding="utf-8") as f:
        f.write(f"AI BROWSER AGENT — SESSION RESULT\n{'='*60}\n")
        f.write(f"Date    : {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        f.write(f"Goal    : {tracker.goal}\n")
        f.write(f"Provider: {provider} / {model}\n")
        f.write(f"Tasks   : {tracker.completed_count}/{tracker.total_count} completed\n")
        f.write(f"{'='*60}\n\nTASK RESULTS\n{'-'*60}\n{task_summary}\n\n")
        f.write(f"{'='*60}\nFINAL REPORT\n{'-'*60}\n{report}\n\n")
        f.write(f"{'='*60}\nACTION LOG\n{'-'*60}\n{hist_text}\n")
    if session_log: session_log._heading(f"Result saved: {fp}")
    return fp


def _collect_artifact_context(executed_actions: list[dict[str, Any]]) -> str:
    snippets: list[str] = []
    for item in executed_actions:
        decision = item.get("decision") or {}
        action = decision.get("action")
        if action not in {"write_file", "create_file", "create_markdown_report", "write_yaml"}:
            continue
        path = decision.get("path") or "(unknown path)"
        content = decision.get("content") or ""
        if not isinstance(content, str) or not content.strip():
            continue
        snippets.append(
            f"File: {path}\n"
            f"Content:\n{content[:2000]}"
        )
    return "\n\n".join(snippets[:3])


# ══════════════════════════════════════════════════════════════════════════
# PHASE 1 — PLANNING
# ══════════════════════════════════════════════════════════════════════════
async def plan_tasks(goal: str, provider: str, model: str, cfg: dict,
                     task_id: str | None = None, attachment_context: str = "") -> TaskTracker:
    """Ask the AI to decompose the goal into sub-tasks. Returns a TaskTracker."""
    await broadcast({"type":"planning","message":f"🧠 Planning tasks for: {goal}"})
    repo_context, git_context, repo_paths = build_repo_and_git_context(goal, repo_limit=2)
    method_context = method_memory.format_context_for_planner(method_memory.retrieve(goal, limit=3))
    plan_prompt = build_plan_prompt(
        goal,
        repo_context=repo_context,
        git_context=git_context,
        attachment_context=attachment_context,
        method_context=method_context,
    )
    if task_id:
        orchestrator.set_metadata(task_id, initial_repo_paths=repo_paths, workspace_root=WORKSPACE_ROOT)

    def _parse_tasks(raw: str) -> list:
        """Extract and validate a task list from model output."""
        cleaned = re.sub(r'<think>.*?</think>','',raw,flags=re.DOTALL).strip()
        cleaned = re.sub(r'^```(?:json)?\s*','',cleaned,flags=re.MULTILINE)
        cleaned = re.sub(r'\s*```$','',cleaned,flags=re.MULTILINE).strip()
        m = re.search(r'\[.*?\]', cleaned, re.DOTALL)
        if not m:
            raise ValueError(f"No JSON array in response: {cleaned[:200]}")
        tasks_raw = json.loads(m.group())
        if not tasks_raw or not isinstance(tasks_raw, list):
            raise ValueError("Empty or non-list response")
        # Must have more than 1 task OR at least be different from the raw goal
        tasks = [{"id": t.get("id", f"task_{i+1}"),
                  "description": t.get("description", str(t))}
                 for i, t in enumerate(tasks_raw)]
        if len(tasks) == 1 and tasks[0]["description"].strip() == goal.strip():
            raise ValueError("Planner returned goal as single task — not decomposed")
        return tasks

    def _postprocess_tasks(goal_text: str, tasks: list[dict]) -> list[dict]:
        if not tasks:
            return tasks
        goal_lower = (goal_text or "").lower()
        is_external_browser_goal = "browser" in goal_lower and any(token in goal_lower for token in ("edge", "chrome"))
        if not is_external_browser_goal:
            return tasks

        compact: list[dict] = []
        previous_desc = ""
        for task in tasks:
            description = str(task.get("description", "")).strip()
            desc_lower = description.lower()
            if not description:
                continue
            if compact and desc_lower == previous_desc:
                continue
            if compact:
                prior_lower = str(compact[-1].get("description", "")).lower()
                launch_tokens = ("launch", "open")
                focus_tokens = ("focus", "ensure it is active", "ensure the browser window is active")
                browser_tokens = ("edge browser", "chrome browser", "microsoft edge", "google chrome")
                if (
                    any(token in prior_lower for token in launch_tokens)
                    and any(token in prior_lower for token in browser_tokens)
                    and any(token in desc_lower for token in focus_tokens)
                    and any(token in desc_lower for token in browser_tokens)
                ):
                    continue
            compact.append(task)
            previous_desc = desc_lower
        return compact

    last_error = None
    for attempt in range(3):
        try:
            if task_id:
                orchestrator.append_event(task_id, "planning_attempt", {"attempt": attempt + 1})
            raw, used_provider, used_model = await call_with_fallback(
                PLANNER_SYSTEM, plan_prompt, provider, model, cfg, log_query=True)
            if session_log: session_log._heading(f"PLANNER RAW (attempt {attempt+1})")
            if session_log: session_log._raw(raw[:1000])
            tasks = _postprocess_tasks(goal, _parse_tasks(raw))
            tracker = TaskTracker(goal, tasks)
            if task_id:
                lane_plan, agent_plan = build_agent_lane_plan(goal, cfg, tracker.to_dict()["tasks"])
                orchestrator.set_lanes(task_id, lane_plan)
                orchestrator.set_metadata(
                    task_id,
                    planned_tasks=tracker.to_dict()["tasks"],
                    multi_agent_plan=agent_plan,
                )
                orchestrator.append_event(task_id, "plan_ready", {
                    "provider": used_provider,
                    "model": used_model,
                    "tasks": tracker.to_dict()["tasks"],
                    "multi_agent_plan": agent_plan,
                })
            if session_log: session_log.plan([t.to_dict() for t in tracker.tasks])
            await broadcast({
                "type":"plan_ready","tasks":tracker.to_dict()["tasks"],
                "goal":goal,"provider":used_provider,"model":used_model,
            })
            return tracker
        except Exception as e:
            last_error = e
            if task_id:
                orchestrator.append_event(task_id, "planning_failed", {"attempt": attempt + 1, "error": str(e)})
            if session_log: session_log.error(f"Planning attempt {attempt+1}", str(e))
            await broadcast({"type":"warning","message":f"Planning attempt {attempt+1} failed: {e}"})
            await asyncio.sleep(1)

    # All attempts failed — build a sensible default plan from goal keywords
    if session_log: session_log.error("Planning", f"All attempts failed: {last_error}")
    await broadcast({"type":"warning","message":"Planning failed — using default task breakdown"})

    # Smart fallback: detect common task types and build a minimal plan
    goal_lower = goal.lower()
    fallback_tasks = []
    if any(w in goal_lower for w in ("login","log in","sign in","username","password")):
        fallback_tasks.append({"id":"login","description":"Navigate to the login page and log in with the provided credentials"})
    if any(w in goal_lower for w in ("write","create","publish","post","article","content","blog")):
        fallback_tasks.append({"id":"navigate_create","description":"Navigate to the content creation page"})
        fallback_tasks.append({"id":"fill_content","description":"Fill in the title and body content"})
        fallback_tasks.append({"id":"submit_content","description":"Submit or publish the content"})
    if any(w in goal_lower for w in ("count","find","check","how many","list","get","search")):
        fallback_tasks.append({"id":"navigate_target","description":"Navigate to the page containing the required data"})
        fallback_tasks.append({"id":"find_data","description":"Find and record the required data from the page"})
    if not fallback_tasks:
        fallback_tasks.append({"id":"main_task","description":goal.replace(chr(10)," | ")})

    tracker = TaskTracker(goal, fallback_tasks)
    if task_id:
        lane_plan, agent_plan = build_agent_lane_plan(goal, cfg, tracker.to_dict()["tasks"])
        orchestrator.set_lanes(task_id, lane_plan)
        orchestrator.set_metadata(
            task_id,
            planned_tasks=tracker.to_dict()["tasks"],
            used_fallback_plan=True,
            multi_agent_plan=agent_plan,
        )
        orchestrator.append_event(task_id, "plan_ready", {
            "provider": provider,
            "model": model,
            "fallback": True,
            "tasks": tracker.to_dict()["tasks"],
            "multi_agent_plan": agent_plan,
        })
    await broadcast({"type":"plan_ready","tasks":tracker.to_dict()["tasks"],"goal":goal})
    return tracker


# ══════════════════════════════════════════════════════════════════════════
# MAIN AGENT LOOP
# ══════════════════════════════════════════════════════════════════════════
async def run_agent(goal: str, provider: str, model: str, cfg: dict,
                    auto_restart=False, restart_goal="", attachment_ids: list[str] | None = None):
    global agent_running, session_log, current_session_task_id, _last_screenshot_b64

    global current_tracker
    agent_running = True
    history: list       = []
    executed_actions: list[dict[str, Any]] = []
    failed_attempts: list = []
    task_error_counts: dict[str, int] = {}
    start_time    = time.time()

    # Prune any duplicate/dead websocket connections before broadcasting
    seen_ids = set()
    fresh_ws = []
    for ws in active_ws:
        if id(ws) not in seen_ids:
            seen_ids.add(id(ws))
            fresh_ws.append(ws)
    active_ws.clear()
    active_ws.extend(fresh_ws)
    inject_queue.clear()
    replan_interval = cfg.get("replan_interval", 8)
    next_replan_step = replan_interval if replan_interval > 0 else 999999

    if session_log: session_log.close()
    session_log = SessionLogger(goal, provider, model)

    max_steps_on  = cfg.get("max_steps_enabled", False)
    max_steps     = cfg.get("max_steps", 100)
    max_time_on   = cfg.get("max_time_enabled", True)
    max_time_secs = cfg.get("max_time_minutes", 30) * 60
    stuck_on      = cfg.get("stuck_detection", True)
    stuck_thresh  = cfg.get("stuck_threshold", 3)

    stuck            = StuckDetector(stuck_thresh) if stuck_on else None
    current_provider = provider
    current_model    = model
    _last_screenshot_b64 = None
    stop_reason      = None
    task_id          = make_task_id(goal)
    current_session_task_id = task_id
    attachment_context, resolved_attachments = build_attachment_context(attachment_ids)

    async def _skip_current_task_after_repeated_errors(cur_task, error_message: str, step: int, category: str) -> bool:
        if not cur_task:
            return False
        if task_error_counts.get(cur_task.id, 0) < max(stuck_thresh, 3):
            return False
        reason = f"Repeated {category} errors prevented progress: {error_message[:160]}"
        ok, msg = tracker.skip_task(cur_task.id, reason)
        if session_log:
            if ok:
                session_log.task_skipped(cur_task.id, reason)
            else:
                session_log.error("skip_task", msg)
        orchestrator.append_event(task_id, "task_skipped_after_errors", {
            "task_id": cur_task.id,
            "step": step,
            "message": msg,
        })
        await broadcast({
            "type":"task_skipped",
            "task_id":cur_task.id,
            "reason":reason,
            "tasks":tracker.to_dict()["tasks"],
            "progress":f"{tracker.completed_count}/{tracker.total_count}",
        })
        await broadcast({"type":"warning","message":msg})
        await broadcast_session_state(task_id)
        failed_attempts.clear()
        task_error_counts[cur_task.id] = 0
        return True

    initial_lanes, initial_agent_plan = build_agent_lane_plan(goal, cfg, [])
    orchestrator.create_session(task_id, goal, {
        "provider": provider,
        "model": model,
        "auto_restart": auto_restart,
        "restart_goal": restart_goal,
        "attachments": resolved_attachments,
        "lanes": initial_lanes,
        "multi_agent_plan": initial_agent_plan,
    })
    orchestrator.update_lane(task_id, "explorer", status="running", current_step="planning tasks")
    orchestrator.update_lane(task_id, "implementation", status="idle", current_step="waiting for plan")
    orchestrator.update_lane(task_id, "review", status="idle", current_step="waiting for plan")

    await broadcast({"type":"agent_start","goal":goal,"provider":provider,"model":model})
    await broadcast_session_state(task_id)

    # ── Phase 1: Plan ────────────────────────────────────────────────────
    tracker = await plan_tasks(
        goal, provider, model, cfg,
        task_id=task_id,
        attachment_context=attachment_context,
    )
    current_tracker = tracker
    orchestrator.update_phase(task_id, "inspect")
    orchestrator.update_lane(task_id, "explorer", status="running", current_step="reading page context")
    await broadcast_session_state(task_id)

    step = 0
    while True:
        step += 1

        # ── Stop conditions ──────────────────────────────────────────────
        if not agent_running:
            stop_reason = "stopped by user"
            orchestrator.update_phase(task_id, "failed", status="failed")
            orchestrator.append_event(task_id, "stopped_by_user", {"step": step})
            if session_log: session_log._heading("STOPPED BY USER")
            await broadcast({"type":"agent_stopped","message":"Stopped by user"})
            break

        elapsed = time.time() - start_time
        if max_time_on and elapsed >= max_time_secs:
            stop_reason = f"time limit ({cfg.get('max_time_minutes')}min)"
            orchestrator.append_event(task_id, "stop_condition", {"reason": stop_reason, "step": step})
            await broadcast({"type":"thinking","message":"Time limit — writing report..."})
            break

        if max_steps_on and step > max_steps:
            stop_reason = f"max steps ({max_steps})"
            orchestrator.append_event(task_id, "stop_condition", {"reason": stop_reason, "step": step})
            await broadcast({"type":"thinking","message":f"Max steps reached — writing report..."})
            break

        # ── Process user-injected tasks ──────────────────────────────────
        while inject_queue:
            inj = inject_queue.pop(0)
            new_t = tracker.inject_task(inj["description"], inj.get("priority","next"))
            orchestrator.append_event(task_id, "task_injected", {
                "task_id": new_t.id,
                "description": new_t.description,
                "priority": inj.get("priority", "next"),
            })
            if session_log: session_log._heading(f"USER INJECTED TASK: [{new_t.id}] {new_t.description}")
            await broadcast({
                "type":    "task_injected",
                "task_id": new_t.id,
                "description": new_t.description,
                "tasks":   tracker.to_dict()["tasks"],
                "progress": f"{tracker.completed_count}/{tracker.total_count}",
            })

        # ── Read page ────────────────────────────────────────────────────
        orchestrator.update_phase(task_id, "inspect")
        orchestrator.update_lane(task_id, "explorer", status="running", current_step="extracting page context")
        orchestrator.update_lane(task_id, "implementation", status="idle", current_step="waiting for model decision")
        context    = await extract_page_content(page, cfg)
        screenshot = await screenshot_b64()

        cur_task = tracker.current_task
        progress = f"{tracker.completed_count}/{tracker.total_count}"
        retrieval_query = "\n".join(filter(None, [
            goal,
            cur_task.description if cur_task else "",
            context.get("title", ""),
            context.get("url", ""),
            context.get("text", "")[:800],
            attachment_context[:1200],
            "\n".join(f"{item['action']} => {item['result']}" for item in history[-4:]),
        ]))
        repo_context, git_context, repo_paths = build_repo_and_git_context(retrieval_query, repo_limit=3)
        method_context = method_memory.format_context(method_memory.retrieve(retrieval_query, limit=3))

        # ── Reset failed_attempts when task changes ───────────────────────
        if cur_task and cur_task.id != getattr(run_agent, '_last_task_id', None):
            failed_attempts.clear()
            task_error_counts[cur_task.id] = 0
            run_agent._last_task_id = cur_task.id

        if session_log:
            session_log.step_start(step, context.get("url",""), context.get("title",""),
                                   current_provider, current_model,
                                   cur_task, progress, context.get("sources",{}))
            session_log.page_text(context.get("text",""))
        orchestrator.set_metadata(
            task_id,
            step=step,
            current_url=context.get("url", ""),
            current_title=context.get("title", ""),
            current_task=cur_task.id if cur_task else None,
            current_task_description=cur_task.description if cur_task else "",
            current_task_environment=_choose_task_environment(goal, cur_task.description if cur_task else ""),
            progress=progress,
            repo_paths=repo_paths,
        )
        orchestrator.append_event(task_id, "step_started", {
            "step": step,
            "url": context.get("url", ""),
            "title": context.get("title", ""),
            "task_id": cur_task.id if cur_task else "",
            "progress": progress,
        })

        elapsed_str = f"{int(elapsed//60)}m{int(elapsed%60)}s"
        await broadcast({
            "type":"screenshot","data":screenshot,
            "url":context.get("url",""),"title":context.get("title",""),
            "step":step,"elapsed":elapsed_str,
            "provider":current_provider,"model":current_model,
            "char_count":context.get("char_count",0),
            "tasks":tracker.to_dict()["tasks"],
            "progress":progress,
        })
        await broadcast({
            "type":"thinking",
            "message":(f"Step {step} [{PROVIDER_LABELS.get(current_provider,current_provider)}] "
                       f"{elapsed_str} — Task {progress}"
                       + (f": {cur_task.description[:50]}" if cur_task else " — ALL DONE"))
        })

        # ── Adaptive reflection + replanning ────────────────────────────
        should_replan = (
            replan_interval > 0 and step >= next_replan_step and not tracker.all_complete
        ) or (len(failed_attempts) > 4 and not tracker.all_complete)

        if should_replan and len(history) >= 2:
            orchestrator.update_phase(task_id, "review")
            orchestrator.update_lane(task_id, "review", status="running", current_step="reflecting on the plan")
            await broadcast({"type":"replanning",
                             "message":f"🔄 Reflecting on plan at step {step}..."})
            try:
                steps_since = step - (next_replan_step - replan_interval)
                refl_prompt = build_reflection_prompt(
                    tracker, context, history, max(steps_since, 1),
                    repo_context=repo_context, git_context=git_context,
                    attachment_context=attachment_context,
                    method_context=method_context)
                raw_refl, _, _ = await call_with_fallback(
                    REFLECTION_SYSTEM, refl_prompt,
                    current_provider, current_model, cfg, log_query=False)

                cleaned = re.sub(r"<think>.*?</think>","",raw_refl,flags=re.DOTALL).strip()
                cleaned = re.sub(r"^```(?:json)?\s*","",cleaned,flags=re.MULTILINE)
                cleaned = re.sub(r"\s*```$","",cleaned,flags=re.MULTILINE).strip()

                # Parse the reflection JSON object
                m = re.search(r"\{.*\}", cleaned, re.DOTALL)
                if m:
                    refl = json.loads(m.group())
                    assessment = refl.get("assessment","")
                    plan_valid = refl.get("plan_valid", True)
                    new_pending = refl.get("new_pending_tasks", [])

                    if session_log:
                        session_log._heading("REFLECTION")
                        session_log._kv("Assessment", assessment)
                        session_log._kv("Plan valid", str(plan_valid))

                    if not plan_valid and new_pending:
                        changes = tracker.apply_replan(new_pending)
                        current_tracker = tracker
                        if changes:
                            orchestrator.append_event(task_id, "replan_applied", {
                                "assessment": assessment,
                                "changes": changes,
                                "tasks": tracker.to_dict()["tasks"],
                            })
                            if session_log:
                                session_log._heading("REPLAN APPLIED")
                                for c in changes: session_log._raw(f"  {c}")
                            await broadcast({
                                "type": "replan_applied",
                                "assessment": assessment,
                                "changes": changes,
                                "tasks": tracker.to_dict()["tasks"],
                                "progress": f"{tracker.completed_count}/{tracker.total_count}",
                            })
                            failed_attempts.clear()
                        else:
                            await broadcast({"type":"replanning",
                                             "message":f"✅ {assessment or 'Plan still valid'}"})
                    else:
                        await broadcast({"type":"replanning",
                                         "message":f"✅ {assessment or 'Plan looks good'}"})

                next_replan_step = step + replan_interval
                orchestrator.update_lane(task_id, "review", status="idle", current_step="waiting")
            except Exception as e:
                orchestrator.append_event(task_id, "reflection_error", {"error": str(e), "step": step})
                orchestrator.update_lane(task_id, "review", status="blocked", current_step="reflection error")
                if session_log: session_log.error("Reflection", str(e))
                await broadcast({"type":"warning","message":f"Reflection error: {e}"})

        # ── Auto-done if all tasks complete ──────────────────────────────
        if tracker.all_complete:
            stop_reason = "all tasks complete"
            orchestrator.append_event(task_id, "all_tasks_complete", {"step": step})
            await broadcast({"type":"thinking","message":"✅ All tasks complete — generating report..."})
            break

        # ── Stuck check ──────────────────────────────────────────────────
        stuck_hint = ""
        if stuck and history:
            last_act = history[-1]["action"].split(":")[0].strip()
            stuck_hint = stuck.check(last_act, context.get("url",""), history[-1].get("result","")) or ""
            if stuck_hint:
                orchestrator.append_event(task_id, "stuck_detected", {"step": step, "hint": stuck_hint})
                if session_log: session_log.stuck(stuck_thresh)
                await broadcast({"type":"warning","message":"⚠ Stuck detected — forcing rethink"})

        # ── Mark current task in_progress ────────────────────────────────
        if cur_task:
            tracker.start_task(cur_task.id)

        # ── Build ranked candidates for current task ─────────────────────
        candidates_str = ""
        if cur_task:
            candidates = rank_candidates(
                context.get("elements", []),
                cur_task.description,
                already_tried=failed_attempts,
                max_results=6,
            )
            candidates_str = format_candidates(candidates, cur_task.description)
            if session_log:
                session_log._heading("RANKED CANDIDATES")
                session_log._raw(candidates_str)
            # Send top candidates to UI
            await broadcast({
                "type": "candidates",
                "candidates": [
                    {
                        "rank":    c["rank"],
                        "tag":     c["element"].get("tag",""),
                        "text":    c["element"].get("text","")[:60],
                        "x":       c["element"].get("x",0),
                        "y":       c["element"].get("y",0),
                        "score":   c["score"],
                        "reasons": c["reasons"][:3],
                        "tried":   c["score"] < 0,
                    }
                    for c in candidates
                ],
                "task": cur_task.description[:60] if cur_task else "",
            })

        # ── Query model ──────────────────────────────────────────────────
        user_prompt = build_execution_prompt(
            tracker, context, history, stuck_hint, candidates_str,
            repo_context=repo_context, git_context=git_context,
            attachment_context=attachment_context,
            method_context=method_context,
            task_environment=_choose_task_environment(goal, cur_task.description if cur_task else ""))
        try:
            orchestrator.update_lane(task_id, "explorer", status="done", current_step="context ready")
            orchestrator.update_phase(task_id, "act")
            orchestrator.update_lane(task_id, "implementation", status="running", current_step="waiting for model action")
            # Attach last screenshot if available so the model sees the screen
            screenshot_for_query = _last_screenshot_b64
            _last_screenshot_b64 = None  # consume it — one screenshot per step
            if screenshot_for_query:
                await broadcast({"type": "info", "message": "📸 Sending screenshot to model for visual context"})
            raw, current_provider, current_model = await call_with_fallback(
                EXECUTOR_SYSTEM, user_prompt, current_provider, current_model, cfg,
                image_b64=screenshot_for_query)
        except RuntimeError as e:
            error_message = str(e)
            history.append({"action":"model_error","result":error_message})
            if cur_task:
                task_error_counts[cur_task.id] = task_error_counts.get(cur_task.id, 0) + 1
            orchestrator.append_event(task_id, "executor_error", {"error": error_message, "step": step})
            await broadcast({"type":"error","message":error_message})
            if await _skip_current_task_after_repeated_errors(cur_task, error_message, step, "model/provider"):
                continue
            await asyncio.sleep(3); continue
        except Exception as e:
            error_message = f"{type(e).__name__}: {e}"
            history.append({"action":"internal_error","result":error_message})
            if cur_task:
                task_error_counts[cur_task.id] = task_error_counts.get(cur_task.id, 0) + 1
            orchestrator.append_event(task_id, "executor_error", {"error": error_message, "step": step})
            await broadcast({"type":"error","message":error_message})
            if await _skip_current_task_after_repeated_errors(cur_task, error_message, step, "internal"):
                continue
            await asyncio.sleep(2); continue

        decision = normalize_action_payload(parse_agent_response(raw))
        if cur_task:
            task_error_counts[cur_task.id] = 0
        if (
            cur_task
            and decision.get("action") == "wait"
            and context.get("url", "about:blank") == "about:blank"
            and not context.get("title")
        ):
            target_url = _extract_first_url(cur_task.description) or _extract_first_url(goal)
            if target_url:
                decision = {
                    "action": "navigate",
                    "url": target_url,
                    "thought": "The current task is a direct URL navigation and the browser is still blank.",
                    "summary": f"Fallback: navigate directly to {target_url}",
                }
        if session_log: session_log.decision(decision)
        orchestrator.append_event(task_id, "decision", {
            "step": step,
            "provider": current_provider,
            "model": current_model,
            "decision": decision,
        })

        # If parse fell back to wait, log the raw response for debugging
        if decision.get("action") == "wait" and decision.get("summary","").startswith("Could not"):
            if session_log:
                session_log._heading("⚠ PARSE FAILURE — raw response was:")
                session_log._raw(raw[:500])
            await broadcast({"type":"warning",
                             "message":f"⚠ Model response could not be parsed — raw: {raw[:200]}"})

        thought = decision.get("thought","")
        summary = decision.get("summary","")
        action  = decision.get("action","wait")

        if stuck and action not in ("wait","done","complete_task","skip_task"):
            stuck.reset()

        await broadcast({
            "type":"ai_decision","thought":thought,"action":action,
            "summary":summary,"step":step,"decision":decision,
            "provider":current_provider,"model":current_model,
        })

        task_environment = _choose_task_environment(goal, cur_task.description if cur_task else "")
        external_browser_launch_task = bool(
            cur_task
            and task_environment in {"external", "hybrid"}
            and _is_external_browser_launch_task(cur_task.description)
        )
        external_browser_mode = task_environment == "external"
        if external_browser_launch_task and action in {
            "click", "click_text", "type", "type_and_submit",
            "press", "scroll", "read_page",
        }:
            block_message = (
                "External browser launch task is still active. "
                "Do not use the internal browser until Chrome/Edge is visibly focused on the desktop."
            )
            if session_log:
                session_log.error("external_browser_guard", block_message)
            history.append({"action": f"{action} (blocked)", "result": block_message})
            orchestrator.append_event(task_id, "external_browser_guard_blocked", {
                "step": step,
                "action": decision,
                "message": block_message,
            })
            await broadcast({"type":"warning", "message": block_message})
            continue
        if external_browser_mode and action in {
            "click", "click_text", "type", "type_and_submit",
            "press", "scroll", "read_page", "back",
        }:
            block_message = (
                "This task is in external browser mode. "
                "Use desktop actions or external-browser navigation instead of the internal Playwright browser."
            )
            if session_log:
                session_log.error("external_browser_guard", block_message)
            history.append({"action": f"{action} (blocked)", "result": block_message})
            orchestrator.append_event(task_id, "external_browser_mode_blocked", {
                "step": step,
                "action": decision,
                "message": block_message,
            })
            await broadcast({"type":"warning", "message": block_message})
            continue
        if (
            cur_task
            and task_environment in {"external", "hybrid"}
            and _external_browser_text_entry_task(cur_task.description)
            and action == "desktop_type"
        ):
            if not _recent_focus_established_for_text_entry(history):
                block_message = (
                    "Desktop typing was blocked because the target field was not clearly focused yet. "
                    "Use a desktop click or another focus action on the intended control before typing."
                )
                if session_log:
                    session_log.error("external_browser_guard", block_message)
                history.append({"action": "desktop_type (blocked)", "result": block_message})
                orchestrator.append_event(task_id, "external_type_without_focus_blocked", {
                    "step": step,
                    "action": decision,
                    "message": block_message,
                })
                await broadcast({"type":"warning", "message": block_message})
                continue
            repeated_types = _recent_repeated_action_count(
                history,
                "desktop_type",
                match_text=str(decision.get("text", "")),
            )
            if repeated_types >= 2 and not _recent_external_text_entry_verified(history, cur_task.description):
                block_message = (
                    "Repeated desktop typing was blocked. The previous click may have opened a chooser, popup, or different control "
                    "instead of a normal text field. Inspect the current state with screenshot/OCR, or dismiss the popup before typing again."
                )
                if session_log:
                    session_log.error("external_browser_guard", block_message)
                history.append({"action": "desktop_type (blocked)", "result": block_message})
                orchestrator.append_event(task_id, "external_repeated_type_blocked", {
                    "step": step,
                    "action": decision,
                    "message": block_message,
                })
                await broadcast({"type":"warning", "message": block_message})
                continue
        if (
            cur_task
            and task_environment in {"external", "hybrid"}
            and _external_browser_text_entry_task(cur_task.description)
            and action == "desktop_click"
            and decision.get("x") is not None
            and decision.get("y") is not None
        ):
            repeated_clicks = _recent_repeated_action_count(
                history,
                "desktop_click",
                match_coords=(int(decision.get("x")), int(decision.get("y"))),
            )
            if repeated_clicks >= 2 and not _recent_external_text_entry_verified(history, cur_task.description):
                block_message = (
                    "Repeated desktop clicking was blocked. Reusing the same coordinates for a text-entry task is brittle and may be reopening "
                    "a popup or chooser. Reinspect the UI state and pick a new target instead of repeating the same click."
                )
                if session_log:
                    session_log.error("external_browser_guard", block_message)
                history.append({"action": "desktop_click (blocked)", "result": block_message})
                orchestrator.append_event(task_id, "external_repeated_click_blocked", {
                    "step": step,
                    "action": decision,
                    "message": block_message,
                })
                await broadcast({"type":"warning", "message": block_message})
                continue

        # ── Handle complete_task ─────────────────────────────────────────
        if action == "complete_task":
            completed_task_id = decision.get("task_id","")
            finding = decision.get("finding","")
            expected_text = _extract_expected_task_text(cur_task.description if cur_task else "", finding)
            if external_browser_launch_task and not _has_verified_external_browser_history(history, cur_task.description if cur_task else ""):
                guard_message = (
                    "Launch task not completed yet: no verified external browser window focus/open result was recorded."
                )
                if session_log:
                    session_log.error("complete_task", guard_message)
                history.append({"action":f"complete_task: {completed_task_id} (blocked)","result":guard_message})
                orchestrator.append_event(task_id, "external_browser_complete_blocked", {
                    "task_id": completed_task_id,
                    "step": step,
                    "message": guard_message,
                })
                await broadcast({"type":"warning","message":guard_message})
                continue
            if (
                cur_task
                and task_environment in {"external", "hybrid"}
                and _external_browser_text_entry_task(cur_task.description)
                and not _recent_external_text_entry_verified(history, cur_task.description)
            ):
                guard_message = (
                    "Text-entry task not verified yet: a successful `desktop_type` alone is not enough. "
                    "Take a desktop screenshot or use OCR/find_text_on_screen to confirm the typed text landed before completing the task."
                )
                if session_log:
                    session_log.error("complete_task", guard_message)
                history.append({"action":f"complete_task: {completed_task_id} (blocked)","result":guard_message})
                orchestrator.append_event(task_id, "external_text_entry_complete_blocked", {
                    "task_id": completed_task_id,
                    "step": step,
                    "message": guard_message,
                })
                await broadcast({"type":"warning","message":guard_message})
                continue
            if (
                cur_task
                and task_environment in {"external", "hybrid"}
                and expected_text
                and _external_browser_text_entry_task(cur_task.description)
                and not _recent_expected_text_verified(history, expected_text)
            ):
                guard_message = (
                    f"Expected text was not verified yet: '{expected_text}'. "
                    "Do not complete the task until OCR, readback, or text detection confirms that exact text appeared in the target field."
                )
                if session_log:
                    session_log.error("complete_task", guard_message)
                history.append({"action":f"complete_task: {completed_task_id} (blocked)","result":guard_message})
                orchestrator.append_event(task_id, "external_expected_text_complete_blocked", {
                    "task_id": completed_task_id,
                    "step": step,
                    "message": guard_message,
                })
                await broadcast({"type":"warning","message":guard_message})
                continue
            if cur_task and "send" in cur_task.description.lower():
                if task_environment in {"external", "hybrid"} and not _recent_strong_verification_signal(history):
                    guard_message = (
                        "Send task not verified strongly enough yet. A screenshot alone is not enough for a final desktop action. "
                        "Use OCR, readback, or on-screen text detection after the send attempt before completing the task."
                    )
                    if session_log:
                        session_log.error("complete_task", guard_message)
                    history.append({"action":f"complete_task: {completed_task_id} (blocked)","result":guard_message})
                    orchestrator.append_event(task_id, "send_complete_blocked_on_weak_verification", {
                        "task_id": completed_task_id,
                        "step": step,
                        "message": guard_message,
                    })
                    await broadcast({"type":"warning","message":guard_message})
                    continue
                error_hint = _recent_error_indicator(history)
                if error_hint:
                    guard_message = (
                        "Send task cannot be completed because a recent error indicator was detected after the send attempt. "
                        f"Latest error hint: {error_hint}"
                    )
                    if session_log:
                        session_log.error("complete_task", guard_message)
                    history.append({"action":f"complete_task: {completed_task_id} (blocked)","result":guard_message})
                    orchestrator.append_event(task_id, "send_complete_blocked_on_error", {
                        "task_id": completed_task_id,
                        "step": step,
                        "message": guard_message,
                    })
                    await broadcast({"type":"warning","message":guard_message})
                    continue
            ok, msg = tracker.complete_task(completed_task_id, finding)
            if session_log:
                if ok: session_log.task_completed(completed_task_id, finding)
                else:  session_log.error("complete_task", msg)
            history.append({"action":f"complete_task: {completed_task_id}","result":msg})
            orchestrator.append_event(task_id, "task_completed", {
                "task_id": completed_task_id,
                "finding": finding,
                "message": msg,
                "step": step,
            })
            await broadcast({
                "type":"task_completed",
                "task_id":completed_task_id,
                "finding":finding,
                "tasks":tracker.to_dict()["tasks"],
                "progress":f"{tracker.completed_count}/{tracker.total_count}",
                "message":msg,
            })
            # Take post-action screenshot
            await asyncio.sleep(0.2)
            ss = await screenshot_b64()
            await broadcast({"type":"screenshot","data":ss,
                             "url":context.get("url",""),"title":context.get("title",""),
                             "step":step,"tasks":tracker.to_dict()["tasks"]})
            await broadcast_session_state(task_id)
            continue

        # ── Handle skip_task ─────────────────────────────────────────────
        if action == "skip_task":
            skipped_task_id = decision.get("task_id","")
            reason  = decision.get("reason","No reason given")
            ok, msg = tracker.skip_task(skipped_task_id, reason)
            if session_log:
                if ok: session_log.task_skipped(skipped_task_id, reason)
                else:  session_log.error("skip_task", msg)
            history.append({"action":f"skip_task: {skipped_task_id}","result":msg})
            orchestrator.append_event(task_id, "task_skipped", {
                "task_id": skipped_task_id,
                "reason": reason,
                "message": msg,
                "step": step,
            })
            await broadcast({
                "type":"task_skipped","task_id":skipped_task_id,"reason":reason,
                "tasks":tracker.to_dict()["tasks"],
                "progress":f"{tracker.completed_count}/{tracker.total_count}",
            })
            await broadcast_session_state(task_id)
            continue

        # ── Handle done ──────────────────────────────────────────────────
        if action == "done":
            allowed, check_msg = tracker.validate_done()
            if not allowed:
                # Reject done — tell AI what's missing
                if session_log: session_log.done_rejected(check_msg)
                await broadcast({"type":"warning",
                                 "message":f"🚫 Done rejected: {check_msg}"})
                history.append({"action":"done (rejected)","result":check_msg})
                orchestrator.append_event(task_id, "done_rejected", {"step": step, "message": check_msg})
                # Continue loop — AI will get the updated task status next step
                continue
            stop_reason = "goal accomplished"
            orchestrator.append_event(task_id, "done_accepted", {"step": step})
            await broadcast({"type":"thinking","message":"✅ All done — generating report..."})
            break

        # ── Execute browser action ───────────────────────────────────────
        url_before = page.url if page else ""
        orchestrator.update_lane(task_id, "implementation", status="running", current_step=f"executing {action}")
        try:
            result = await execute_action(decision)
        except Exception as e:
            error_message = f"{type(e).__name__}: {e}"
            history.append({"action": f"{action} (error)", "result": error_message})
            if cur_task:
                task_error_counts[cur_task.id] = task_error_counts.get(cur_task.id, 0) + 1
            orchestrator.append_event(task_id, "action_error", {
                "step": step,
                "action": decision,
                "error": error_message,
            })
            await broadcast({"type":"error","message":error_message})
            if await _skip_current_task_after_repeated_errors(cur_task, error_message, step, "action"):
                continue
            await asyncio.sleep(2)
            continue
        history.append({
            "action": action_to_history_entry(decision),
            "result": result
        })
        executed_actions.append({
            "decision": dict(decision),
            "result": result,
        })
        if cur_task:
            task_error_counts[cur_task.id] = 0
        if cur_task and not str(result).lower().startswith("action failed"):
            should_remember = True
            if _choose_task_environment(goal, cur_task.description) == "external" and action in {
                "navigate", "click", "click_text", "type", "type_and_submit",
                "press", "scroll", "read_page", "back",
            }:
                should_remember = False
            if should_remember:
                method_memory.remember_success(goal, cur_task.description, decision, result)
        orchestrator.append_event(task_id, "action_executed", {
            "step": step,
            "action": decision,
            "result": result,
        })

        await asyncio.sleep(0.5)
        ss   = await screenshot_b64()
        ctx2 = await extract_page_content(page, {**cfg,"ocr_enabled":False,"deep_read":False})
        url_after = ctx2.get("url","")

        # ── Track failed click attempts ──────────────────────────────────
        if action in ("click", "type"):
            x_tried = decision.get("x")
            y_tried = decision.get("y")
            if x_tried is not None and y_tried is not None:
                coord_key = (x_tried, y_tried)
                if "REJECTED" in result:
                    if coord_key not in failed_attempts:
                        failed_attempts.append(coord_key)
                    if session_log:
                        session_log._heading(f"⚠ Marking ({x_tried},{y_tried}) as failed (REJECTED)")
                else:
                    # Track coords we've tried — deprioritise repeats
                    if coord_key not in failed_attempts:
                        failed_attempts.append(coord_key)

        # ── Smart completion detection ────────────────────────────────────
        if cur_task and not cur_task.is_done:
            task_desc = cur_task.description.lower()
            auto_complete = None
            auto_finding  = None
            url_a     = url_after.lower()
            url_b     = url_before.lower()
            page_text = ctx2.get("text","").lower()
            typed_text = decision.get("text","")  # what was actually typed this step

            # ── ANY SUCCESSFUL TYPE ACTION → complete if task involves typing ──
            # Only auto-complete if the task description is about typing/entering/filling
            typing_task = any(w in task_desc for w in
                ("type","enter","fill","input","write","send","compose","respond","reply"))
            if action == "type" and "REJECTED" not in result and typed_text and typing_task:
                auto_complete = cur_task.id
                auto_finding  = f"Entered: '{typed_text[:80]}'"

            # ── URL CHANGED → navigation or form submit worked ───────────────
            elif url_before != url_after and url_after and action not in ("navigate","click_text"):
                url_ok = not any(s in url_a for s in ("error","fail","invalid","denied"))
                if url_ok:
                    auto_complete = cur_task.id
                    auto_finding  = f"Action succeeded — now at {url_after}"

            # ── LOGIN SUCCESS (URL left auth page) ────────────────────────
            elif action in ("click","press"):
                if any(s in url_b for s in ("login","signin","sign-in","auth","wp-login")):
                    if not any(s in url_a for s in ("login","signin","sign-in","auth","wp-login")):
                        auto_complete = cur_task.id
                        auto_finding  = f"Login successful — redirected to {url_after}"

            # ── SUCCESS MESSAGE on page after click/press ─────────────────
            if not auto_complete and action in ("click","press"):
                success_phrases = ("published","submitted","posted","sent","saved","created",
                                   "updated","success","successfully","confirmed","is now live")
                if any(s in page_text[:800] for s in success_phrases):
                    auto_complete = cur_task.id
                    auto_finding  = "Success confirmed on page"

            if auto_complete:
                ok, msg = tracker.complete_task(auto_complete, auto_finding)
                if ok:
                    if session_log: session_log.task_completed(auto_complete, auto_finding)
                    failed_attempts.clear()
                    orchestrator.append_event(task_id, "task_auto_completed", {
                        "task_id": auto_complete,
                        "finding": auto_finding,
                        "step": step,
                    })
                    await broadcast({
                        "type": "task_completed",
                        "task_id": auto_complete,
                        "finding": auto_finding,
                        "tasks": tracker.to_dict()["tasks"],
                        "progress": f"{tracker.completed_count}/{tracker.total_count}",
                        "message": f"✅ Auto-detected: {auto_finding}",
                        "auto": True,
                    })
                    history.append({"action": f"auto_complete: {auto_complete}", "result": auto_finding})

        await broadcast({
            "type":"screenshot","data":ss,
            "url":url_after,"title":ctx2.get("title",""),
            "step":step,"action_result":result,
            "tasks":tracker.to_dict()["tasks"],
        })
        await broadcast_session_state(task_id)

    # ── Generate final report ────────────────────────────────────────────
    if stop_reason != "stopped by user":
        await broadcast({"type":"thinking","message":"Writing final report..."})
        try:
            orchestrator.update_phase(task_id, "review")
            orchestrator.update_lane(task_id, "review", status="running", current_step="writing final report")
            artifact_context = _collect_artifact_context(executed_actions)
            report_prompt = build_report_prompt(tracker, history, artifact_context=artifact_context)
            raw_rep, _, _ = await call_with_fallback(
                REPORT_SYSTEM, report_prompt, current_provider, current_model, cfg)
            report = re.sub(r'<think>.*?</think>','',raw_rep,flags=re.DOTALL).strip()
        except Exception as e:
            report = f"Report generation failed: {e}"

        filename = ""
        try:
            fp = save_result(tracker, report, history, current_provider, current_model)
            filename = os.path.basename(fp)
        except Exception: pass

        elapsed = time.time() - start_time
        orchestrator.update_phase(task_id, "done", status="completed")
        orchestrator.update_lane(task_id, "explorer", status="done", current_step="completed")
        orchestrator.update_lane(task_id, "implementation", status="done", current_step="completed")
        orchestrator.update_lane(task_id, "review", status="done", current_step="completed")
        orchestrator.set_metadata(
            task_id,
            stop_reason=stop_reason,
            elapsed_seconds=round(elapsed, 2),
            final_report=report,
            result_filename=filename,
            final_tasks=tracker.to_dict()["tasks"],
        )
        orchestrator.append_event(task_id, "session_completed", {
            "reason": stop_reason,
            "steps": step,
            "elapsed_seconds": round(elapsed, 2),
            "filename": filename,
        })
        if session_log:
            session_log.session_end(stop_reason, step, elapsed)
            session_log = None

        await broadcast({
            "type":"agent_done",
            "message":stop_reason,
            "report":report,
            "filename":filename,
            "goal":tracker.goal,
            "steps":step,
            "elapsed":f"{int(elapsed//60)}m{int(elapsed%60)}s",
            "tasks":tracker.to_dict()["tasks"],
            "auto_restart":auto_restart,
            "restart_goal":restart_goal,
        })
        await broadcast_session_state(task_id)
    else:
        elapsed = time.time() - start_time
        orchestrator.set_metadata(task_id, stop_reason="stopped by user", elapsed_seconds=round(elapsed, 2))
        if session_log:
            session_log.session_end("stopped by user", step, elapsed)
            session_log = None

    agent_running = False
    current_session_task_id = None


# ══════════════════════════════════════════════════════════════════════════
# LIFESPAN (replaces deprecated on_event startup/shutdown)
# ══════════════════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app):
    # ── STARTUP ──────────────────────────────────────────────────────────
    global browser, page, playwright_instance
    playwright_instance = await async_playwright().start()

    # Persistent browser profile — saves cookies/sessions to disk
    profile_name = load_config().get("browser_profile", "default")
    profile_dir  = os.path.join(BASE_DIR, "profiles", profile_name)
    os.makedirs(profile_dir, exist_ok=True)

    browser = await playwright_instance.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=True,
        viewport={"width": 1280, "height": 720},
        # Spoof a real Chrome user-agent so sites don't reject "HeadlessChrome"
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/132.0.0.0 Safari/537.36"
        ),
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            # Hide headless indicators that sites check for
            "--disable-blink-features=AutomationControlled",
        ],
        ignore_default_args=["--enable-automation"],
    )
    page = browser.pages[0] if browser.pages else await browser.new_page()

    caps = get_capabilities()
    print(f"Browser ready | Profile: {profile_name!r} | "
          f"OCR:{'yes' if caps['ocr'] else 'no'} PDF:{'yes' if caps['pdf'] else 'no'}", flush=True)
    print(f"   Sessions persist in: {profile_dir}", flush=True)

    yield  # ← server runs here

    # ── SHUTDOWN ─────────────────────────────────────────────────────────
    global session_log
    if session_log: session_log.close()
    if browser: await browser.close()      # saves profile to disk
    if playwright_instance: await playwright_instance.stop()


# Wire lifespan into app (defined after lifespan function to avoid NameError)
app.router.lifespan_context = lifespan

# ══════════════════════════════════════════════════════════════════════════
# FASTAPI ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════

@app.get("/capabilities")
async def capabilities(): return get_capabilities()


@app.get("/sessions")
async def list_sessions():
    sessions = [session.to_dict() for session in orchestrator.list_sessions()]
    return {"sessions": sessions}


@app.get("/sessions/current")
async def current_session():
    if not current_session_task_id:
        return {"session": None}
    session = orchestrator.resume_session(current_session_task_id)
    return {"session": session.to_dict() if session else None}


@app.get("/repo/index")
async def repo_index_summary():
    return {
        "workspace_root": WORKSPACE_ROOT,
        "summary": repo_index.format_index_summary(),
    }


@app.post("/repo/retrieve")
async def repo_retrieve(body: dict):
    query = (body or {}).get("query", "")
    limit = max(1, min(int((body or {}).get("limit", 3)), 8))
    results = repo_index.retrieve(query, limit=limit)
    return {
        "workspace_root": WORKSPACE_ROOT,
        "results": [
            {
                "path": item.file.relative_path,
                "language": item.file.language,
                "score": round(item.score, 2),
                "reasons": item.reasons,
                "summary": item.file.summary,
                "preview": item.file.preview[:1200],
            }
            for item in results
        ],
        "context": repo_index.format_context(results),
    }


@app.get("/git/context")
async def git_context():
    ctx = git_context_provider.get_context()
    return {
        "available": ctx.available,
        "recent_commits": [
            {
                "hash": commit.hash,
                "author": commit.author,
                "date": commit.date,
                "message": commit.message,
            }
            for commit in ctx.recent_commits
        ],
        "diff_head": ctx.diff_head,
        "staged_diff": ctx.staged_diff,
        "formatted": git_context_provider.format_context(ctx),
    }


@app.get("/attachments")
async def list_attachments():
    return {"attachments": list_uploaded_attachments()}


@app.post("/attachments/upload")
async def upload_attachments(files: list[UploadFile] = File(...)):
    saved = []
    for upload in files:
        base = safe_attachment_name(upload.filename or "attachment")
        prefix = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        stored_name = f"{prefix}_{base}"
        path = os.path.join(UPLOADS_DIR, stored_name)
        data = await upload.read()
        with open(path, "wb") as handle:
            handle.write(data)
        summary = attachment_reader.summarize(path, max_chars=2500)
        saved.append({
            "id": stored_name,
            "name": summary.name,
            "kind": summary.kind,
            "mime_type": summary.mime_type,
            "size": summary.size,
            "summary": summary.summary,
            "excerpt": summary.content_excerpt[:800],
            "metadata": summary.metadata or {},
        })
    return {"attachments": saved}


@app.delete("/attachments/{attachment_id}")
async def delete_attachment(attachment_id: str):
    safe_id = os.path.basename(attachment_id)
    path = os.path.join(UPLOADS_DIR, safe_id)
    if os.path.exists(path):
        os.remove(path)
        return {"status": "deleted", "id": safe_id}
    return {"status": "not_found", "id": safe_id}


@app.get("/profiles")
async def list_profiles():
    """List all saved browser profiles."""
    profiles_dir = os.path.join(BASE_DIR, "profiles")
    os.makedirs(profiles_dir, exist_ok=True)
    profiles = []
    for name in sorted(os.listdir(profiles_dir)):
        path = os.path.join(profiles_dir, name)
        if os.path.isdir(path):
            # Count cookies as a proxy for "has saved sessions"
            cookies_file = os.path.join(path, "Default", "Cookies")
            has_sessions = os.path.exists(cookies_file) and os.path.getsize(cookies_file) > 8192
            profiles.append({
                "name":         name,
                "path":         path,
                "has_sessions": has_sessions,
                "active":       name == load_config().get("browser_profile", "default"),
            })
    return {"profiles": profiles}


@app.post("/profiles/switch")
async def switch_profile(body: dict):
    """Switch to a different profile (takes effect on next backend restart)."""
    name = body.get("name", "default")
    # Sanitise: only allow alphanumeric + dash/underscore
    import re as _re
    name = _re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:32]
    cfg = load_config()
    cfg["browser_profile"] = name
    save_config(cfg)
    # Create the profile dir now so it shows up immediately
    profile_dir = os.path.join(BASE_DIR, "profiles", name)
    os.makedirs(profile_dir, exist_ok=True)
    return {"status": "saved", "name": name,
            "note": "Restart the backend for the new profile to take effect"}


@app.post("/profiles/clear")
async def clear_profile(body: dict):
    """Delete all saved session data for a profile (logs the user out everywhere)."""
    import shutil
    name = body.get("name", load_config().get("browser_profile", "default"))
    profile_dir = os.path.join(BASE_DIR, "profiles", name)
    if os.path.exists(profile_dir):
        shutil.rmtree(profile_dir)
        os.makedirs(profile_dir, exist_ok=True)
        return {"status": "cleared", "name": name}
    return {"status": "not_found", "name": name}

@app.get("/config")
async def get_config():
    cfg = load_config(); safe = dict(cfg)
    for k in ("ollama_cloud_key","google_api_key","openai_api_key"):
        if safe.get(k): safe[k] = "••••••••" + safe[k][-4:]
    return safe

@app.get("/memory/methods")
async def get_method_memory(query: str = "", limit: int = 10):
    entries = method_memory.retrieve(query, limit=max(1, min(limit, 20))) if query else method_memory._entries[-max(1, min(limit, 20)):]
    return {
        "count": len(entries),
        "items": [entry.to_dict() for entry in entries],
    }

@app.post("/config")
async def update_config(body: dict):
    cfg = load_config()
    for k,v in body.items():
        if k in cfg:
            if isinstance(v,str) and v.startswith("••"): continue
            cfg[k] = v
    save_config(cfg)
    return {"status":"saved"}

@app.get("/models")
async def get_models():
    result = {}
    cfg = load_config()
    try:
        result["ollama_local"] = await fetch_ollama_model_names(
            cfg["ollama_local_url"]
        )
    except Exception as e:
        result["ollama_local"] = []; result["ollama_local_error"] = str(e)
    try:
        result["ollama_cloud"] = await fetch_ollama_model_names(
            cfg["ollama_cloud_url"],
            api_key=cfg.get("ollama_cloud_key", ""),
        )
    except Exception as e:
        result["ollama_cloud"] = []; result["ollama_cloud_error"] = str(e)
    result["google"]       = ["gemini-3-flash-preview","gemini-2.0-flash",
                               "gemini-2.0-flash-lite","gemini-1.5-pro","gemini-1.5-flash"]
    result["openai"]       = ["gpt-4o","gpt-4o-mini","gpt-4-turbo","gpt-3.5-turbo"]
    return result

@app.get("/results")
async def list_results():
    files = [{"filename":f,"size":os.path.getsize(os.path.join(RESULTS_DIR,f)),
              "modified":os.path.getmtime(os.path.join(RESULTS_DIR,f))}
             for f in sorted(os.listdir(RESULTS_DIR),reverse=True) if f.endswith(".txt")]
    return {"files":files[:50]}

@app.get("/results/{filename}")
async def get_result(filename: str):
    p = os.path.join(RESULTS_DIR, filename)
    if not os.path.exists(p): return {"error":"Not found"}
    with open(p,encoding="utf-8") as f: return {"content":f.read(),"filename":filename}

@app.get("/logs")
async def list_logs():
    files = [{"filename":f,"size":os.path.getsize(os.path.join(LOGS_DIR,f)),
              "modified":os.path.getmtime(os.path.join(LOGS_DIR,f))}
             for f in sorted(os.listdir(LOGS_DIR),reverse=True) if f.endswith(".log")]
    return {"files":files[:50]}

@app.get("/logs/{filename}")
async def get_log(filename: str):
    p = os.path.join(LOGS_DIR, filename)
    if not os.path.exists(p): return {"error":"Not found"}
    with open(p,encoding="utf-8") as f: return {"content":f.read(),"filename":filename}

@app.get("/logs/current/tail")
async def tail_current(lines: int = 200):
    if not session_log: return {"content":"(no active session)","filename":""}
    with open(session_log.path,encoding="utf-8") as f: all_lines = f.readlines()
    return {"content":"".join(all_lines[-lines:]),"filename":session_log.filename,
            "total_lines":len(all_lines)}

@app.get("/screenshot")
async def get_screenshot():
    img = await screenshot_b64()
    ctx = await extract_page_content(page,{"max_text_chars":500,"ocr_enabled":False,
                                            "pdf_enabled":False,"deep_read":False})
    return {"screenshot":img,"url":ctx.get("url"),"title":ctx.get("title")}

@app.post("/start")
async def start_agent(body: dict):
    global agent_running, agent_task
    if agent_task and not agent_task.done():
        agent_running = False; agent_task.cancel()
        try: await asyncio.sleep(0.5)
        except Exception: pass
    cfg = load_config()
    for k in ("max_steps_enabled","max_steps","max_time_enabled","max_time_minutes",
               "stuck_detection","stuck_threshold","fallback_enabled",
               "max_text_chars","deep_read","ocr_enabled","ocr_max_images","pdf_enabled",
               "local_file_access_enabled","local_file_write_enabled","filesystem_scope",
               "filesystem_root","desktop_automation_enabled","desktop_execution_mode",
               "desktop_autonomy_scope","command_execution_mode","command_timeout_seconds",
               "multi_agent_enabled","multi_agent_mode","max_parallel_agents",
               "central_agent_approval"):
        if k in body: cfg[k] = body[k]
    selected_provider = body.get("provider", cfg["active_provider"])
    selected_model = body.get("model", cfg["active_model"])
    cfg["active_provider"] = selected_provider
    cfg["active_model"] = selected_model
    save_config(cfg)
    agent_task = asyncio.create_task(run_agent(
        body.get("goal",""),
        selected_provider,
        selected_model,
        cfg,
        body.get("auto_restart", False),
        body.get("restart_goal",""),
        body.get("attachments", []) or [],
    ))
    return {"status":"started"}

@app.post("/stop")
async def stop_agent():
    global agent_running; agent_running = False; return {"status":"stopping"}

@app.post("/manual")
async def manual_action(body: dict):
    result     = await execute_action(body, from_manual=True)
    screenshot = await screenshot_b64()
    ctx        = await extract_page_content(page,{"max_text_chars":500,"ocr_enabled":False,
                                                   "pdf_enabled":False,"deep_read":False})
    return {"result":result,"screenshot":screenshot,"url":ctx.get("url"),"title":ctx.get("title")}


@app.post("/inject")
async def inject_task(body: dict):
    """Add a new task mid-session without stopping the agent."""
    global inject_queue, current_tracker
    description = body.get("description","").strip()
    priority    = body.get("priority","next")  # "next" | "last"
    if not description:
        return {"error": "description required"}
    if not agent_running:
        return {"error": "No agent running — start an agent first"}
    inject_queue.append({"description": description, "priority": priority})
    return {"status": "queued", "description": description, "priority": priority}


@app.post("/replan")
async def force_replan():
    """Force an immediate reflection + replan on the next step."""
    global next_replan_step
    if not agent_running:
        return {"error": "No agent running"}
    next_replan_step = 0  # triggers replan on next loop iteration
    return {"status": "replan scheduled"}


@app.get("/tasks")
async def get_tasks():
    """Get current task plan state."""
    if current_tracker:
        return current_tracker.to_dict()
    return {"tasks": [], "goal": "", "complete": False, "progress": "0/0"}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    if ws not in active_ws: active_ws.append(ws)
    img = await screenshot_b64()
    ctx = await extract_page_content(page,{"max_text_chars":500,"ocr_enabled":False,
                                            "pdf_enabled":False,"deep_read":False})
    await ws.send_json({"type":"screenshot","data":img,
                        "url":ctx.get("url"),"title":ctx.get("title")})
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect:
        if ws in active_ws: active_ws.remove(ws)

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("PORT", "8765"))
    except ValueError:
        port = 8765
    uvicorn.run(app, host=host, port=port, log_level="warning")
