"""
task_planner.py — AI-driven task decomposition and completion tracking

Phase 1: Ask the AI to break the goal into discrete, verifiable sub-tasks
Phase 2: Execute sub-tasks one by one, marking each complete with a finding
Phase 3: `done` is only allowed when ALL sub-tasks are marked completed
         — enforced by the system, not just the AI's judgment
"""

import json
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from action_protocol import parse_action_response


# ══════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════════════
@dataclass
class SubTask:
    id:          str
    description: str
    status:      str   = "pending"    # pending | in_progress | completed | skipped
    finding:     str   = ""           # what was found/accomplished
    started_at:  Optional[float] = None
    completed_at: Optional[float] = None

    def to_dict(self):
        return asdict(self)

    @property
    def is_done(self):
        return self.status in ("completed", "skipped")


class TaskTracker:
    """
    Manages the sub-task list for one agent session.
    Enforces that `done` is only valid when all tasks are complete.
    """

    def __init__(self, goal: str, tasks: list[dict]):
        self.goal  = goal
        self.tasks = [SubTask(**t) for t in tasks]
        self.created_at = time.time()

    # ── Queries ────────────────────────────────────────────────────────────

    @property
    def current_task(self) -> Optional[SubTask]:
        """The first incomplete task."""
        for t in self.tasks:
            if not t.is_done:
                return t
        return None

    @property
    def all_complete(self) -> bool:
        return all(t.is_done for t in self.tasks)

    @property
    def completed_count(self) -> int:
        return sum(1 for t in self.tasks if t.is_done)

    @property
    def total_count(self) -> int:
        return len(self.tasks)

    def incomplete_tasks(self) -> list[SubTask]:
        return [t for t in self.tasks if not t.is_done]

    def get_task(self, task_id: str) -> Optional[SubTask]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    # ── Mutations ──────────────────────────────────────────────────────────

    def start_task(self, task_id: str):
        t = self.get_task(task_id)
        if t and t.status == "pending":
            t.status     = "in_progress"
            t.started_at = time.time()

    def complete_task(self, task_id: str, finding: str) -> tuple[bool, str]:
        """
        Mark a task complete with its finding.
        Returns (success, message).
        """
        t = self.get_task(task_id)
        if not t:
            return False, f"Unknown task id: {task_id}"
        if t.is_done:
            return False, f"Task '{task_id}' is already {t.status}"
        t.status       = "completed"
        t.finding      = finding
        t.completed_at = time.time()
        return True, f"Task '{task_id}' completed: {finding}"

    def skip_task(self, task_id: str, reason: str) -> tuple[bool, str]:
        t = self.get_task(task_id)
        if not t:
            return False, f"Unknown task id: {task_id}"
        t.status  = "skipped"
        t.finding = f"Skipped: {reason}"
        return True, f"Task '{task_id}' skipped"

    def inject_task(self, description: str, priority: str = "next") -> SubTask:
        """
        Add a new task from the user mid-session.
        priority='next'  → insert right after the current in-progress task
        priority='last'  → append at the end
        """
        # Generate a unique id
        base = re.sub(r'[^a-z0-9]', '_', description.lower())[:24].strip('_')
        existing_ids = {t.id for t in self.tasks}
        tid = base
        counter = 2
        while tid in existing_ids:
            tid = f"{base}_{counter}"; counter += 1

        new_task = SubTask(id=tid, description=description)

        if priority == "next":
            # Find insertion point: after last in_progress/completed, before first pending
            insert_at = len(self.tasks)
            for i, t in enumerate(self.tasks):
                if t.status == "pending":
                    insert_at = i
                    break
            self.tasks.insert(insert_at, new_task)
        else:
            self.tasks.append(new_task)

        return new_task

    def apply_replan(self, new_tasks: list[dict]) -> list[str]:
        """
        Apply a replanning result. Keeps completed tasks, replaces/adds pending ones.
        Returns list of change descriptions.
        """
        changes = []
        existing_ids = {t.id for t in self.tasks if t.is_done}

        # Keep all completed/skipped tasks as-is
        kept = [t for t in self.tasks if t.is_done]

        # Add new pending tasks (skip any that duplicate a completed id)
        added = []
        for td in new_tasks:
            tid = td.get("id","")
            if tid in existing_ids:
                changes.append(f"Kept completed: [{tid}]")
                continue
            # Check if we already have this task pending
            existing = self.get_task(tid)
            if existing and not existing.is_done:
                # Update description if changed
                if existing.description != td.get("description",""):
                    existing.description = td["description"]
                    changes.append(f"Updated: [{tid}] {td['description'][:50]}")
                added.append(existing)
            else:
                t = SubTask(id=tid, description=td.get("description", tid))
                added.append(t)
                changes.append(f"Added: [{tid}] {td['description'][:50]}")

        self.tasks = kept + added
        return changes

    def validate_done(self) -> tuple[bool, str]:
        """
        Check whether `done` is valid right now.
        Returns (allowed, message).
        """
        incomplete = self.incomplete_tasks()
        if not incomplete:
            return True, "All tasks complete — done is valid"
        names = "; ".join(f"[{t.id}] {t.description}" for t in incomplete)
        return False, f"Cannot finish yet — {len(incomplete)} task(s) still pending: {names}"

    # ── Serialisation ──────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "goal":      self.goal,
            "tasks":     [t.to_dict() for t in self.tasks],
            "complete":  self.all_complete,
            "progress":  f"{self.completed_count}/{self.total_count}",
        }

    def status_block(self) -> str:
        """Human-readable status for injection into prompts."""
        lines = [f"TASK PLAN ({self.completed_count}/{self.total_count} complete):"]
        for t in self.tasks:
            icon = {"pending":"○", "in_progress":"◉", "completed":"✓", "skipped":"⊘"}[t.status]
            line = f"  {icon} [{t.id}] {t.description}"
            if t.finding:
                line += f"\n      → FINDING: {t.finding}"
            lines.append(line)
        cur = self.current_task
        if cur:
            lines.append(f"\nCURRENT TASK: [{cur.id}] {cur.description}")
            lines.append("Focus on this task. Complete it, then move to the next.")
        else:
            lines.append("\n✅ ALL TASKS COMPLETE — use action: done")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# PLANNING PROMPT
# ══════════════════════════════════════════════════════════════════════════
PLANNER_SYSTEM = """You are a task planning assistant. Break a browser automation goal into sub-tasks.

OUTPUT: A JSON array ONLY. No markdown fences. No explanation. Nothing before or after the array.

Format:
[{"id":"snake_case_id","description":"Specific single action or finding"},...]

Rules:
- id must be snake_case (e.g. "login", "navigate_to_plugins", "write_post_title")
- description must be ONE specific thing (not a summary of the whole goal)
- Split multi-step actions: "login" is separate from "navigate to page" is separate from "fill form"
- For any content creation: split into navigate, fill_title, fill_content, submit/publish
- For any data retrieval: split into navigate, find_data, record_finding
- If the goal only requires saving local text or structured data to a file, prefer direct filesystem tasks such as create_directory and write_file instead of opening desktop editors, unless the user explicitly asked for a specific app.
- For desktop software goals, prefer direct launch/open tasks before Start-menu search tasks when the app name is already known
- If KNOWN EFFECTIVE METHODS show a specific working command, window title, or executable for a similar goal, preserve that approach in the plan
- Max 12 tasks
- Order logically: login first, then navigation, then data entry, then submission

EXAMPLE for "login and create a new article":
[
  {"id":"navigate_login","description":"Navigate to the login page URL"},
  {"id":"enter_credentials","description":"Enter username and password and submit the login form"},
  {"id":"navigate_new_content","description":"Navigate to the new content creation page"},
  {"id":"fill_title","description":"Click the title field and type a relevant title"},
  {"id":"fill_content","description":"Click the content area and type the article body"},
  {"id":"publish","description":"Click the publish or submit button to publish the content"}
]

EXAMPLE for "open VLC and play bible.mp4":
[
  {"id":"launch_vlc","description":"Launch VLC media player directly from the desktop environment"},
  {"id":"focus_vlc","description":"Focus the VLC media player window if it opens behind other windows"},
  {"id":"open_video_file","description":"Open the bible.mp4 file in VLC"},
  {"id":"confirm_playback","description":"Verify that bible.mp4 starts playing in VLC"}
]"""

def build_plan_prompt(goal: str, repo_context: str = "", git_context: str = "", attachment_context: str = "", method_context: str = "") -> str:
    # Clean up multi-line goals for the prompt
    clean_goal = " | ".join(line.strip() for line in goal.strip().splitlines() if line.strip())
    extra = ""
    if repo_context:
        extra += f"\n\nLOCAL REPO CONTEXT:\n{repo_context[:3500]}"
    if git_context:
        extra += f"\n\nGIT CONTEXT:\n{git_context[:2000]}"
    if attachment_context:
        extra += f"\n\nATTACHMENTS:\n{attachment_context[:3000]}"
    if method_context:
        extra += f"\n\n{method_context[:1800]}"
    return f"""Goal: {clean_goal}
{extra}

Return ONLY a JSON array of sub-tasks. No other text."""


# ══════════════════════════════════════════════════════════════════════════
# EXECUTION PROMPT BUILDER
# ══════════════════════════════════════════════════════════════════════════
EXECUTOR_SYSTEM = """You are an AI browser agent controlling a real web browser.

You work through a structured task plan. At each step you do ONE action.

OUTPUT FORMAT — EXACTLY ONE JSON OBJECT, nothing else, no text before or after:

For browser and workspace actions:
{"thought":"...","action":"navigate|click|click_text|open_application|list_installed_apps|list_windows|focus_window|desktop_screenshot_query|desktop_click|click_desktop|desktop_type|type_desktop|desktop_scroll|scroll_desktop|desktop_hotkey|hotkey|desktop_press_key|press_key|desktop_screenshot|take_desktop_screenshot|find_text_on_screen|move_mouse|drag_mouse|select_menu_item|close_window|upload_file_to_page|upload_files_to_page|click_and_upload|type|type_and_submit|press|scroll|wait|back|read_page|read_file|read_json|read_csv|list_directory|create_file|write_file|patch_file|create_pdf|create_json|create_csv|extract_pdf_text|ocr_image_to_text|download_url|create_markdown_report|write_yaml|convert_image_format|screenshot_page_to_file|save_page_html|print_page_to_pdf|create_directory|extract_archive|search_in_files|copy_paths|rename_path|move_paths|delete_paths|zip_paths|run_command","...action fields...","summary":"..."}

click_text: find and click any element by its visible text — USE THIS for chat list items, menu items, tabs, anything where x,y clicking fails repeatedly
  → {"thought":"...","action":"click_text","text":"Kathryn","summary":"..."}

Desktop app actions:
- open_application  → {"thought":"...","action":"open_application","command":"notepad.exe|code|google-chrome","summary":"..."}
- list_installed_apps → {"thought":"...","action":"list_installed_apps","query":"whatsapp","summary":"..."}
- list_windows      → {"thought":"...","action":"list_windows","summary":"..."}
- focus_window      → {"thought":"...","action":"focus_window","text":"Notepad","summary":"..."}
- desktop_click     → {"thought":"...","action":"desktop_click","x":420,"y":280,"button":"left","summary":"..."}
- click_desktop     → {"thought":"...","action":"click_desktop","x":420,"y":280,"button":"left","summary":"..."}
- desktop_type      → {"thought":"...","action":"desktop_type","text":"hello from agent","summary":"..."}
- type_desktop      → {"thought":"...","action":"type_desktop","text":"hello from agent","summary":"..."}
- desktop_scroll    → {"thought":"...","action":"desktop_scroll","amount":-600,"summary":"..."}
- scroll_desktop    → {"thought":"...","action":"scroll_desktop","amount":-600,"summary":"..."}
- desktop_hotkey    → {"thought":"...","action":"desktop_hotkey","keys":["ctrl","s"],"summary":"..."}
- hotkey            → {"thought":"...","action":"hotkey","keys":["ctrl","s"],"summary":"..."}
- desktop_press_key → {"thought":"...","action":"desktop_press_key","key":"Enter","summary":"..."}
- press_key         → {"thought":"...","action":"press_key","key":"Enter","summary":"..."}
- desktop_screenshot → {"thought":"...","action":"desktop_screenshot","path":"artifacts/desktop.png","summary":"..."}
- take_desktop_screenshot → {"thought":"...","action":"take_desktop_screenshot","path":"artifacts/desktop.png","summary":"..."}
- find_text_on_screen → {"thought":"...","action":"find_text_on_screen","text":"Save","summary":"..."}
- move_mouse        → {"thought":"...","action":"move_mouse","x":500,"y":320,"summary":"..."}
- drag_mouse        → {"thought":"...","action":"drag_mouse","x":300,"y":200,"end_x":700,"end_y":200,"button":"left","summary":"..."}
- select_menu_item  → {"thought":"...","action":"select_menu_item","text":"File>Open","summary":"..."}
- close_window      → {"thought":"...","action":"close_window","text":"Notepad","summary":"..."}

upload_file_to_page: attach one local file to a webpage file input
  → {"thought":"...","action":"upload_file_to_page","selector":"input[type=file]","path":"files/resume.pdf","summary":"..."}

upload_files_to_page: attach multiple local files to a webpage file input
  → {"thought":"...","action":"upload_files_to_page","selector":"input[type=file]","paths":["files/a.pdf","files/b.pdf"],"summary":"..."}

click_and_upload: click a button or upload area that opens a file chooser, then attach one or more local files
  → {"thought":"...","action":"click_and_upload","selector":"button.upload","path":"files/resume.pdf","summary":"..."}
  → {"thought":"...","action":"click_and_upload","text":"Upload resume","paths":["files/resume.pdf"],"summary":"..."}

type_and_submit: type text then press Enter — USE THIS for chat input boxes, search fields, any form without a visible send/submit button
  → {"thought":"...","action":"type_and_submit","x":123,"y":456,"text":"message text here","summary":"..."}

focus_field: find ANY input by id/name/placeholder/label — even if it's hidden in a tab or collapsed panel — scrolls it into view and focuses it. USE THIS when you cannot find a field by scrolling.
  → {"thought":"...","action":"focus_field","hint":"regular_price","summary":"..."}
  → {"thought":"...","action":"focus_field","hint":"price","summary":"..."}

For local workspace / computer actions:
- read_file        → {"thought":"...","action":"read_file","path":"backend/config.json","summary":"..."}
- read_json        → {"thought":"...","action":"read_json","path":"backend/config.json","summary":"..."}
- read_csv         → {"thought":"...","action":"read_csv","path":"data/table.csv","summary":"..."}
- list_directory   → {"thought":"...","action":"list_directory","path":"frontend/src","summary":"..."}
- create_file      → {"thought":"...","action":"create_file","path":"notes/todo.txt","content":"hello","summary":"..."}
- write_file       → {"thought":"...","action":"write_file","path":"notes/todo.txt","content":"replace or append text","append":false,"summary":"..."}
- patch_file       → {"thought":"...","action":"patch_file","path":"backend/agent.py","old_text":"foo","new_text":"bar","replace_all":false,"summary":"..."}
- create_pdf       → {"thought":"...","action":"create_pdf","path":"reports/summary.pdf","title":"Summary","content":"report body text","summary":"..."}
- create_json      → {"thought":"...","action":"create_json","path":"data/output.json","content":"{\\"ok\\":true}","summary":"..."}
- create_csv       → {"thought":"...","action":"create_csv","path":"data/table.csv","content":"name,score\\nAda,10","summary":"..."}
- extract_pdf_text → {"thought":"...","action":"extract_pdf_text","path":"docs/spec.pdf","destination":"docs/spec.txt","summary":"..."}
- ocr_image_to_text → {"thought":"...","action":"ocr_image_to_text","path":"images/input.png","destination":"images/input_ocr.txt","summary":"..."}
- download_url     → {"thought":"...","action":"download_url","url":"https://example.com/file.pdf","destination":"downloads/file.pdf","summary":"..."}
- create_markdown_report → {"thought":"...","action":"create_markdown_report","path":"reports/summary.md","title":"Summary","content":"## Findings\\n- Item 1","summary":"..."}
- write_yaml       → {"thought":"...","action":"write_yaml","path":"config/generated.yaml","content":"{\\"enabled\\":true}","summary":"..."}
- convert_image_format → {"thought":"...","action":"convert_image_format","source":"images/input.png","destination":"images/output.jpg","format":"JPEG","summary":"..."}
- screenshot_page_to_file → {"thought":"...","action":"screenshot_page_to_file","path":"artifacts/page.png","full_page":true,"summary":"..."}
- save_page_html   → {"thought":"...","action":"save_page_html","path":"artifacts/page.html","summary":"..."}
- print_page_to_pdf → {"thought":"...","action":"print_page_to_pdf","path":"artifacts/page.pdf","summary":"..."}
- create_directory → {"thought":"...","action":"create_directory","path":"output/reports","summary":"..."}
- extract_archive  → {"thought":"...","action":"extract_archive","source":"downloads/archive.zip","destination":"downloads/unpacked","summary":"..."}
- search_in_files  → {"thought":"...","action":"search_in_files","path":"backend","pattern":"AgentSessionStore","summary":"..."}
- copy_paths       → {"thought":"...","action":"copy_paths","sources":["a.txt"],"destination":"backup","summary":"..."}
- rename_path      → {"thought":"...","action":"rename_path","source":"old.txt","destination":"new.txt","summary":"..."}
- move_paths       → {"thought":"...","action":"move_paths","sources":["a.txt","b.txt"],"destination":"archive","summary":"..."}
- delete_paths     → {"thought":"...","action":"delete_paths","paths":["tmp.txt"],"recursive":false,"summary":"..."}
- zip_paths        → {"thought":"...","action":"zip_paths","sources":["reports","notes.txt"],"destination":"artifacts/bundle.zip","summary":"..."}
- run_command      → {"thought":"...","action":"run_command","command":"npm test","cwd":"frontend","summary":"..."}

FILE CONTENT RULE: When the user asks for a summary, report, notes, or any generated file, the `content`
must synthesize the real findings already gathered in this task. Never write placeholder text, a restatement
of the file instruction, or a generic summary that ignores completed-task findings and recent action results.

To mark the CURRENT task as complete (when you have found/done what it requires):
{"thought":"...","action":"complete_task","task_id":"the_task_id","finding":"exact data or confirmation found","summary":"..."}

To skip a task that is impossible:
{"thought":"...","action":"skip_task","task_id":"the_task_id","reason":"why it cannot be done","summary":"..."}

To finish the session (ONLY when ALL tasks are marked complete or skipped):
{"thought":"...","action":"done","summary":"..."}

ACTION REFERENCE:
- navigate  → add "url":"https://..."
- click     → add "x":123,"y":456
- open_application → add "command":"notepad.exe|code|google-chrome" and optional "cwd":"..."
- focus_window → add "text":"window title fragment"
- desktop_click → add "x":123,"y":456 and optional "button":"left|right|middle"
- move_mouse → add "x":123,"y":456
- drag_mouse → add "x":123,"y":456,"end_x":700,"end_y":456 and optional "button":"left|right|middle"
- desktop_type → add "text":"..."
- For desktop_type / type_desktop you may optionally add "typing_mode":"raw|paste|auto"
- Use "typing_mode":"raw" for paste-blocked fields such as Gmail recipient/subject fields
- For Gmail compose specifically:
- Use the gmail_compose action to fill To/Subject/Body in a single step — it uses keyboard shortcuts and Tab navigation, bypassing coordinate guessing entirely.
- Action format: {"action":"gmail_compose","to":"addr@example.com","subject":"Your subject","body":"Message text"}
- After gmail_compose succeeds, send with desktop_hotkey ctrl+Enter or click the Send button.
- Do NOT plan separate tasks to click the To/Subject/Body fields — gmail_compose handles all of them.
- desktop_hotkey → add "keys":["ctrl","s"]
- desktop_press_key → add "key":"Enter"
- desktop_screenshot → add "path":"artifacts/desktop.png"
- find_text_on_screen → add "text":"Save"
- desktop_screenshot_query → take a screenshot and immediately ask the model a vision question about it. Add "question":"What UI elements are visible? Where is the Compose button?" Returns a description of the screen that can be used to decide the next click. Use this whenever you are unsure about screen state or need to find a UI element.
- desktop_screenshot → captures screen into model memory; the image is automatically attached to the NEXT model query. Use before any step that requires visual confirmation.
- select_menu_item → add "text":"File>Open" for simple classic app menus
- close_window → add "text":"window title fragment" or omit text to close the foreground window
- upload_file_to_page / upload_files_to_page → add "selector":"input[type=file]" plus "path" or "paths"
- click_and_upload → add a clickable target using "selector" or "text" or x,y, plus "path" or "paths"
- type      → add "x":123,"y":456,"text":"..."
- press     → add "key":"Enter"
- scroll    → add "direction":"down","amount":300
- wait      → add "seconds":2
- read_page → deep-reads full page content
- read_file / read_json / read_csv / list_directory → inspect local files/folders when the task depends on the user's computer
- create/write/patch/rename/move/delete → modify local files only when the task clearly requires it
- create_pdf / create_json / create_csv → generate structured output artifacts for the task
- extract_pdf_text / ocr_image_to_text / create_markdown_report / write_yaml → turn documents and media into reusable local text artifacts
- download_url / screenshot_page_to_file / save_page_html / print_page_to_pdf / convert_image_format → capture and transform external or visual assets
- extract_archive / search_in_files → unpack downloaded assets and search the local codebase or documents
- copy_paths / zip_paths → package or preserve outputs when needed
- run_command → use when a local command is the fastest reliable way to get or verify information
- upload_file_to_page / upload_files_to_page / click_and_upload → send local files from the user's computer to a real website upload field
- open_application / focus_window / desktop_* / find_text_on_screen → control installed desktop software outside the browser
- For known desktop apps (for example VLC, Notepad, Calculator), prefer `open_application` or `focus_window` before trying GUI searching or coordinate clicks
- If KNOWN EFFECTIVE METHODS contain a successful `run_command`, `open_application`, or `focus_window` for the same app, prefer reusing that exact method before inventing a new one
- For opening software, assume the backend will try this chain automatically: remembered method, focus existing window, direct executable, shell-based launch, installed-app/shortcut discovery, then GUI search last
- On Linux, `open_application` may use executable names, desktop-entry names, `gtk-launch`, `xdg-open`, or shell commands. Prefer exact executable names such as `google-chrome`, `firefox`, `code`, `vlc`, or `libreoffice` when known.
- For a normal "open this program" task, prefer `open_application` first; use `focus_window` first only when the task clearly says the app is already open/running
- If open_application fails or reports an error, use list_installed_apps to discover the correct app name or desktop launch entry before retrying. Action: {"action":"list_installed_apps","query":"whatsapp"} returns matching apps with launch details. Then retry open_application with the exact name returned.
- list_installed_apps is platform-aware. On Windows it searches StartApps/registry/filesystem. On Linux it searches desktop entries and executables on PATH. Always pass a query string to narrow results.

CRITICAL RULES:
1. Focus on the CURRENT TASK shown in the task plan. Do not jump ahead.
2. When you have accomplished what the current task requires → use complete_task immediately.
3. complete_task does NOT navigate — it just records the result. Navigate on the next step.
4. Do NOT use `done` if any tasks are still pending — the system will reject it.
5. ONE JSON OBJECT PER RESPONSE. Never output two JSON objects.
6. Before creating or changing local files, inspect the relevant local path first with read_file or list_directory unless the task is trivial.
7. Prefer patch_file over write_file when modifying an existing file.
8. Use run_command sparingly, but do prefer it for local app/file launch when KNOWN EFFECTIVE METHODS show a command that already worked.
9. Prefer browser actions for websites and desktop actions only for real native applications, system dialogs, or software outside the browser.
10. For desktop app launches, prefer `open_application` with a known executable, shortcut name, or desktop-entry name before trying GUI search clicks. Use `focus_window` earlier only when the app is already open.
11. If the same desktop click or desktop key approach fails twice without a clear state change, stop repeating it and switch strategy.
12. Do not jump to GUI searching for apps first; use `open_application` before desktop search clicks because the backend now performs a multi-step launch chain automatically.

BROWSER COORDINATE RULE:
- For browser `click` actions, only use x,y coordinates from the INTERACTIVE ELEMENTS list below.
- Browser viewport y is typically 0–720. NEVER use negative browser coordinates or browser y > 800.

DESKTOP COORDINATE RULE:
- `desktop_click`, `move_mouse`, and `drag_mouse` operate on the full desktop, not the browser viewport.
- Desktop coordinates may use the real screen size shown by desktop screenshots or known desktop dimensions.

GETTING UNSTUCK FROM CLICKS:
- If clicking by x,y fails 2+ times in a row → switch to `click_text` using the element's visible label.
- `click_text` finds elements by text content, not coordinates — much more reliable for dynamic lists.
- Example: instead of clicking (321,201), use {"action":"click_text","text":"Kathryn"}
- If `click_text` also fails → use `navigate` with a direct URL.

GETTING UNSTUCK FROM SCROLLING:
- If you have scrolled 3+ times looking for an input field and cannot find it → STOP scrolling.
- Use `focus_field` with the field name, id, or label text instead.
- `focus_field` finds fields even inside collapsed panels, inactive tabs, or off-screen areas.
- Examples: {"action":"focus_field","hint":"regular_price"} or {"action":"focus_field","hint":"price"}
- After focus_field succeeds, use `type` to enter the value.
- NEVER scroll more than 3 times looking for the same field — use focus_field instead.

TYPING AND SUBMITTING:
- Use `type` to enter text into any field (standard inputs AND contenteditable divs like chat boxes).
- Use `type_and_submit` to type AND immediately send — for chat inputs, search boxes, any field where Enter submits.
- Elements marked [EDITABLE] are contenteditable divs — use `type_and_submit` on them to send messages.
- If you see a message input but no Send button → always use `type_and_submit`.

FOR CHAT APPS (WhatsApp, Messenger, etc.):
- Open a conversation: {"action":"click_text","text":"ContactName"}
- Send a message: {"action":"type_and_submit","x":X,"y":Y,"text":"your message here"}"""


def build_execution_prompt(tracker: TaskTracker, context: dict,
                            history: list, stuck_hint: str = "",
                            candidates_str: str = "",
                            repo_context: str = "",
                            git_context: str = "",
                            attachment_context: str = "",
                            method_context: str = "",
                            task_environment: str = "internal") -> str:
    # Filter out off-screen elements (negative y or > 900) to prevent bad clicks
    visible_elements = [e for e in context.get("elements", [])
                        if -50 <= e.get("y", 0) <= 900 and -50 <= e.get("x", 0) <= 1400]
    visible_els  = [e for e in visible_elements if not e.get("hidden_field")]
    hidden_els   = [e for e in visible_elements if e.get("hidden_field")]

    elements = "\n".join([
        f"  [{e['id']:>2}] <{e['tag']:<8}> '{e['text'][:65]}' @ ({e['x']},{e['y']})"
        + ("  [EDITABLE]"  if e.get("contenteditable") else "")
        + (f"  → {e['href'][:60]}" if e.get("href") else "")
        + ("  [DISABLED]"  if e.get("disabled") else "")
        for e in visible_els[:35]
    ])

    if hidden_els:
        elements += "\n\nHIDDEN / OFF-SCREEN FIELDS (use focus_field to reach these):"
        for e in hidden_els[:15]:
            elements += f"\n  [HIDDEN] <{e['tag']}> '{e['text']}' hint='{e.get('hint','')}'  → use: {{\"action\":\"focus_field\",\"hint\":\"{e.get('hint','')}\"}}"
    hidden_count = len(context.get("elements",[])) - len(visible_elements)
    if hidden_count > 0:
        elements += f"\n  ... ({hidden_count} off-screen elements hidden — use navigate instead)"
    hist = "\n".join([
        f"  Step {i+1}: {h['action']} => {h['result']}"
        for i, h in enumerate(history[-12:])
    ])
    completed_findings = "\n".join(
        f"  - {task.description}: {task.finding}"
        for task in tracker.tasks
        if task.status == "completed" and task.finding
    )
    accumulated_knowledge = completed_findings or "  (No completed-task findings yet; use relevant recent results below.)"
    char_info = (f"[Page: {context.get('char_count',0)} chars | "
                 f"DOM={context.get('sources',{}).get('dom_chars',0)} "
                 f"OCR={context.get('sources',{}).get('ocr_chars',0)} "
                 f"PDF={context.get('sources',{}).get('pdf_chars',0)}]")
    stuck = f"\n⚠ STUCK: {stuck_hint}\n" if stuck_hint else ""

    candidates_section = ""
    if candidates_str:
        candidates_section = f"""
{candidates_str}

INSTRUCTION: Use the ranked candidates above to decide which element to interact with.
Start with #1. If #1 has "ALREADY TRIED" mark, skip it and use #2 or #3.
"""
    workspace_sections = []
    if repo_context:
        workspace_sections.append(repo_context[:5000])
    if git_context:
        workspace_sections.append(git_context[:2500])
    if attachment_context:
        workspace_sections.append("ATTACHMENTS:\n" + attachment_context[:3500])
    if method_context:
        workspace_sections.append(method_context[:1800])
    workspace_block = ""
    if workspace_sections:
        workspace_block = "\n\nWORKSPACE CONTEXT:\n" + "\n\n".join(workspace_sections)
    environment_block = [f"TASK ENVIRONMENT: {task_environment.upper()}"]
    if task_environment == "external":
        environment_block.extend([
            "- This task must run in the user's real desktop/browser context.",
            "- Use desktop actions such as `open_application`, `focus_window`, `desktop_click`, `desktop_type`, `desktop_hotkey`, `desktop_press_key`, `desktop_screenshot`, and `find_text_on_screen`.",
            "- On Linux, prefer exact executable or desktop-entry names when opening software, and expect desktop control to use tools such as xdotool/wmctrl behind the scenes.",
            "- Do not rely on the internal Playwright page state to declare success.",
            "- GMAIL COMPOSE: Never try to find or click the To/Subject/Body fields by coordinate. Use the gmail_compose action instead: action=gmail_compose, to=addr@example.com, subject=..., body=.... It opens compose with the keyboard shortcut and fills all fields via Tab navigation — no coordinates needed.",
            "- Do NOT search for 'To', 'Subject', or 'Recipient' with find_text_on_screen when composing Gmail — the compose popup uses placeholder text that OCR cannot reliably read. Use gmail_compose instead.",
            "- When you are unsure what is on screen or where to click, use desktop_screenshot_query with a specific question like 'Where is the Compose button? What are its coordinates?' — the model will analyse the screenshot and tell you exactly what to do next.",
            "- After any action that might change the screen (app launch, navigation, clicking), take a desktop_screenshot before deciding the next step — it is attached to your next query automatically.",
        ])
    elif task_environment == "hybrid":
        environment_block.extend([
            "- This task may use either environment.",
            "- Prefer internal Playwright for ordinary reading, research, direct URLs, scraping, and simple web interaction.",
            "- Prefer the external desktop/browser for real logged-in state, existing browser sessions, the user's Edge/Chrome, downloads, uploads, extensions, native UI, or anything that depends on the real desktop.",
            "- If the current environment is failing, switch intelligently and explain that briefly in your thought/summary.",
        ])
    else:
        environment_block.extend([
            "- Use the internal Playwright browser for this task.",
            "- Prefer browser actions like `navigate`, `click`, `click_text`, `type`, `type_and_submit`, `press`, `scroll`, `read_page`, uploads, and page screenshots.",
        ])
    environment_block.extend([
        "- Do not assume a field stayed focused from a previous step.",
        "- Before `desktop_type`, establish focus inside the target control during this task.",
        "- When visible text is on screen, prefer `click_text` or `find_text_on_screen` over guessed coordinates.",
        "- For recipient/subject/body typing tasks, verify the text landed before completing the task.",
        "- If the task requires exact text, verification must confirm that exact text.",
    ])
    workspace_block += "\n\n" + "\n".join(environment_block)

    # Trim page text when session is long — prevents token overflow causing parse failures
    page_text = context.get('text', '(empty)')
    max_page = 4000 if len(history) > 6 else 6000
    if task_environment == "external":
        elements = "(internal Playwright browser elements hidden because this goal targets an external desktop browser)"
        page_text = "(internal Playwright page content hidden because this goal targets an external desktop browser)"
        candidates_section = ""

    return f"""GOAL: {tracker.goal}

{tracker.status_block()}

ACCUMULATED TASK KNOWLEDGE (use this as source material for any file/report content):
{accumulated_knowledge}
{stuck}
CURRENT PAGE:
URL: {context.get('url','about:blank')}
Title: {context.get('title','')}
{char_info}
{candidates_section}
FULL ELEMENT LIST (all visible interactive elements):
{elements or '(none found)'}

PAGE CONTENT:
{page_text[:max_page]}
{workspace_block}

RECENT HISTORY:
{hist or '(first step)'}

Reply with ONE JSON object only."""


# ══════════════════════════════════════════════════════════════════════════
# RESPONSE PARSER  (same robust first-JSON-wins logic)
# ══════════════════════════════════════════════════════════════════════════
def parse_agent_response(raw: str) -> dict:
    return parse_action_response(raw)


# ══════════════════════════════════════════════════════════════════════════
# FINAL REPORT PROMPT
# ══════════════════════════════════════════════════════════════════════════
REPORT_SYSTEM = ("You are an AI assistant. Write a clear, structured final report "
                 "based on what was accomplished during a web browsing session. "
                 "Be specific — include actual numbers, data, and content found.")

def build_report_prompt(tracker: TaskTracker, history: list, artifact_context: str = "") -> str:
    task_summary = "\n".join([
        f"  [{t.status.upper()}] {t.description}"
        + (f"\n    Finding: {t.finding}" if t.finding else "")
        for t in tracker.tasks
    ])
    hist = "\n".join([f"Step {i+1}: {h['action']} => {h['result']}"
                      for i, h in enumerate(history)])
    artifact_block = f"Saved artifact contents:\n{artifact_context}\n\n" if artifact_context else ""
    return (
        f"Goal: {tracker.goal}\n\n"
        f"Task Completion ({tracker.completed_count}/{tracker.total_count}):\n{task_summary}\n\n"
        f"Session actions:\n{hist}\n\n"
        f"{artifact_block}"
        "Write the authoritative, complete version of the requested deliverable. "
        "Any user-requested summary, findings, notes, or report file will be synchronized "
        "to this final report after generation. Do not call that file merely concise and "
        "do not claim an exact character or word count. Lead with the key findings from completed tasks. "
        "Present data clearly (numbers, lists, specific details). "
        "Note any tasks that could not be completed and why. "
        "If saved artifact contents are provided, keep the report aligned with that content "
        "and do not add factual details that are not supported by it."
    )


# ══════════════════════════════════════════════════════════════════════════
# REPLAN PROMPTS
# ══════════════════════════════════════════════════════════════════════════
REPLAN_SYSTEM = """You are a task planning assistant reviewing an ongoing browser automation session.

Analyse what has been done, what is currently visible, and decide whether the task plan needs updating.

You can:
1. Keep the plan as-is (return the same tasks, just the incomplete ones)
2. Split a complex task into smaller sub-tasks
3. Add new tasks that were discovered as necessary
4. Remove tasks that are no longer relevant (do NOT remove completed tasks)
5. Reorder pending tasks

Return ONLY a JSON array of ALL REMAINING (incomplete) tasks. Do not include already-completed tasks.
No markdown, no explanation — just the array:
[{"id":"snake_case_id","description":"specific action or finding"},...] """


def build_replan_prompt(tracker: "TaskTracker", context: dict, history: list,
                        repo_context: str = "", git_context: str = "", attachment_context: str = "") -> str:
    completed = [t for t in tracker.tasks if t.is_done]
    pending   = [t for t in tracker.tasks if not t.is_done]

    comp_block = "\n".join([
        f"  ✓ [{t.id}] {t.description}" + (f"\n    → {t.finding}" if t.finding else "")
        for t in completed
    ]) or "  (none yet)"

    pend_block = "\n".join([
        f"  ○ [{t.id}] {t.description}" for t in pending
    ]) or "  (none)"

    hist = "\n".join([f"  Step {i+1}: {h['action']} => {h['result']}"
                       for i, h in enumerate(history[-8:])])
    workspace = ""
    if repo_context:
        workspace += f"\n\nLOCAL REPO CONTEXT:\n{repo_context[:2500]}"
    if git_context:
        workspace += f"\n\nGIT CONTEXT:\n{git_context[:1500]}"
    if attachment_context:
        workspace += f"\n\nATTACHMENTS:\n{attachment_context[:2000]}"

    return f"""Original goal: {tracker.goal}

COMPLETED TASKS:
{comp_block}

REMAINING TASKS (current plan):
{pend_block}

CURRENT PAGE:
URL: {context.get('url','?')}
Title: {context.get('title','?')}
Page text: {context.get('text','')[:1000]}

RECENT ACTIONS:
{hist}
{workspace}

Review the remaining tasks. Is the current plan still correct given what you can see?
Return a JSON array of remaining tasks (revised if needed). Keep ids of existing tasks where possible."""


# ══════════════════════════════════════════════════════════════════════════
# REFLECTION + REPLANNING PROMPTS
# ══════════════════════════════════════════════════════════════════════════
REFLECTION_SYSTEM = """You are a task planning assistant reviewing progress of a browser automation session.

Analyse what has been done, what the agent is currently seeing, and whether the remaining plan is still correct.

OUTPUT: A JSON object ONLY. No markdown. No explanation.

Schema:
{
  "assessment": "brief honest assessment of progress and current situation",
  "plan_valid": true/false,
  "changes": [
    {
      "type": "add" | "update" | "remove" | "reorder",
      "id": "task_id",
      "description": "new or updated description (for add/update)",
      "after": "task_id to insert after (for add, optional)",
      "reason": "why this change is needed"
    }
  ],
  "new_pending_tasks": [
    {"id": "snake_case_id", "description": "specific verifiable task"}
  ]
}

Rules:
- If plan is still valid with no changes needed: set plan_valid=true, changes=[], new_pending_tasks=[]
- Only suggest changes for PENDING tasks — never modify completed/skipped ones
- new_pending_tasks completely replaces all pending tasks (completed ones are preserved)
- Be conservative: only replan if something is clearly wrong or the AI is stuck
- Sub-tasks should be concrete single actions (one click, one form fill, one navigation)"""


def build_reflection_prompt(tracker, context: dict, history: list,
                             steps_since_last_replan: int,
                             repo_context: str = "",
                             git_context: str = "",
                             attachment_context: str = "",
                             method_context: str = "") -> str:
    completed = [t for t in tracker.tasks if t.is_done]
    pending   = [t for t in tracker.tasks if not t.is_done]

    completed_str = "\n".join([
        f"  ✓ [{t.id}] {t.description}"
        + (f"\n    → {t.finding}" if t.finding else "")
        for t in completed
    ]) or "  (none yet)"

    pending_str = "\n".join([
        f"  {'◉' if t.status=='in_progress' else '○'} [{t.id}] {t.description}"
        for t in pending
    ]) or "  (none — all complete)"

    recent_hist = "\n".join([
        f"  Step {i+1}: {h['action']} => {h['result']}"
        for i, h in enumerate(history[-10:])
    ])
    workspace = ""
    if repo_context:
        workspace += f"\n\nLOCAL REPO CONTEXT:\n{repo_context[:2500]}"
    if git_context:
        workspace += f"\n\nGIT CONTEXT:\n{git_context[:1500]}"
    if attachment_context:
        workspace += f"\n\nATTACHMENTS:\n{attachment_context[:2000]}"
    if method_context:
        workspace += f"\n\n{method_context[:1400]}"

    return f"""GOAL: {tracker.goal}

COMPLETED TASKS:
{completed_str}

REMAINING TASKS:
{pending_str}

CURRENT PAGE:
URL: {context.get('url', 'unknown')}
Title: {context.get('title', '')}
Content (excerpt): {context.get('text', '')[:800]}

RECENT HISTORY ({steps_since_last_replan} steps since last review):
{recent_hist}
{workspace}

Review the plan. Is it still correct? Does it need new sub-tasks, adjustments, or is it fine as-is?
Output JSON only."""
