"""
Compact persistent memory of successful methods the agent has used before.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, asdict
from typing import Any


STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "for", "of", "on", "in", "at", "my", "your",
    "with", "using", "use", "open", "go", "into", "from", "by", "is", "be", "it",
}

IMPORTANT_ACTIONS = {
    "open_application",
    "focus_window",
    "list_windows",
    "desktop_hotkey",
    "desktop_press_key",
    "desktop_click",
    "desktop_type",
    "select_menu_item",
    "run_command",
    "navigate",
    "click_text",
    "upload_file_to_page",
    "click_and_upload",
}

ACTION_PRIORITY = {
    "run_command": 3.2,
    "open_application": 3.0,
    "focus_window": 2.2,
    "select_menu_item": 2.2,
    "desktop_hotkey": 2.0,
    "desktop_press_key": 1.9,
    "desktop_click": 1.7,
    "desktop_type": 1.6,
    "list_windows": 0.3,
}

GENERIC_TOKENS = {
    "app", "application", "desktop", "software", "window", "windows", "launch", "focus",
    "open", "message", "messages", "chat", "reply", "player", "file",
}


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_\.:-]+", (text or "").lower())
    return [word for word in words if len(word) > 1 and word not in STOPWORDS]


def _compact_payload(action: dict[str, Any]) -> dict[str, Any]:
    keep = ("action", "command", "text", "key", "keys", "selector", "path", "paths", "button", "x", "y")
    compact = {k: action.get(k) for k in keep if action.get(k) not in ("", None, [], {})}
    if "text" in compact and isinstance(compact["text"], str):
        compact["text"] = compact["text"][:80]
    if "command" in compact and isinstance(compact["command"], str):
        compact["command"] = compact["command"][:120]
    return compact


def _entity_tokens(*values: str) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        for token in _tokens(value):
            if token in GENERIC_TOKENS:
                continue
            tokens.add(token)
    return tokens


def _payload_subject_tokens(action: dict[str, Any]) -> set[str]:
    return _entity_tokens(
        str(action.get("command", "")),
        str(action.get("text", "")),
        str(action.get("selector", "")),
        str(action.get("path", "")),
    )


def _is_text_entry_task(query: str, task: str) -> bool:
    text = f"{query}\n{task}".lower()
    return any(token in text for token in (
        "type ",
        "enter ",
        "fill ",
        "recipient",
        "subject",
        "search",
        "field",
        "body",
        "email address",
    ))


def _is_finalization_task(query: str, task: str) -> bool:
    text = f"{query}\n{task}".lower()
    return any(token in text for token in (
        "send",
        "submit",
        "deliver",
        "confirm",
        "post",
    ))


@dataclass(slots=True)
class MethodEntry:
    query: str
    task: str
    action_name: str
    action_payload: dict[str, Any]
    result: str
    success_count: int = 1
    last_used_at: float = 0.0
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MethodMemoryStore:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._entries: list[MethodEntry] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            self._entries = []
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            self._entries = [MethodEntry(**item) for item in raw if isinstance(item, dict)]
        except Exception:
            self._entries = []

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump([entry.to_dict() for entry in self._entries[-300:]], handle, indent=2)

    def remember_success(self, query: str, task: str, action: dict[str, Any], result: str) -> None:
        action_name = str(action.get("action", "")).strip()
        if action_name not in IMPORTANT_ACTIONS:
            return
        result_text = (result or "").strip()
        lowered = result_text.lower()
        if (
            not result_text
            or lowered.startswith("reused remembered method:")
            or lowered.startswith("action failed")
            or "disabled in settings" in lowered
            or "not auto-run" in lowered
            or "blocked during autonomous runs" in lowered
        ):
            return
        subject_tokens = _payload_subject_tokens(action) or _entity_tokens(query, task)
        if action_name == "open_application" and "focused window:" not in lowered and "focused existing window before launch:" not in lowered:
            return
        if action_name == "focus_window":
            target_text = str(action.get("text", "")).strip().lower()
            if "properties [" in lowered:
                return
            if any(ext in lowered for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff")):
                return
            if target_text and target_text not in lowered:
                return
        if action_name == "run_command" and "exit code: 0" not in lowered:
            return
        if action_name == "desktop_click" and _is_text_entry_task(query, task):
            return
        if action_name == "desktop_type" and _is_text_entry_task(query, task):
            return
        if action_name == "desktop_hotkey" and _is_finalization_task(query, task):
            return
        if action_name in {"open_application", "focus_window"} and subject_tokens:
            if not any(token in lowered for token in subject_tokens):
                return
        if action_name == "list_windows":
            query_entities = _entity_tokens(
                query,
                task,
                str(action.get("text", "")),
                str(action.get("command", "")),
            )
            if query_entities and not any(token in lowered for token in query_entities):
                return
        payload = _compact_payload(action)
        now = time.time()
        for entry in self._entries:
            if (
                entry.action_name == action_name
                and entry.task.strip().lower() == task.strip().lower()
                and entry.action_payload == payload
            ):
                entry.success_count += 1
                entry.last_used_at = now
                entry.result = result_text[:160]
                self._save()
                return
        self._entries.append(MethodEntry(
            query=query[:240],
            task=task[:180],
            action_name=action_name,
            action_payload=payload,
            result=result_text[:160],
            success_count=1,
            last_used_at=now,
            created_at=now,
        ))
        self._save()

    def retrieve(self, query: str, limit: int = 3) -> list[MethodEntry]:
        query_tokens = set(_tokens(query))
        if not query_tokens:
            return []
        query_entities = _entity_tokens(query)
        query_lower = (query or "").lower()
        wants_opening = any(token in query_lower for token in ("open ", "launch", "start ", "run "))
        already_open = any(token in query_lower for token in ("already open", "already launched", "already running", "is open"))
        text_entry_query = _is_text_entry_task(query, query)
        scored: list[tuple[float, MethodEntry]] = []
        now = time.time()
        for entry in self._entries:
            result_lower = (entry.result or "").lower()
            if entry.action_name == "run_command" and "exit code: 0" not in result_lower:
                continue
            entry_tokens = set(_tokens(entry.query + " " + entry.task + " " + json.dumps(entry.action_payload)))
            overlap = len(query_tokens & entry_tokens)
            if overlap <= 0:
                continue
            entry_entities = _entity_tokens(
                entry.query,
                entry.task,
                json.dumps(entry.action_payload, ensure_ascii=True),
                entry.result,
            )
            task_entities = _entity_tokens(
                entry.task,
                json.dumps(entry.action_payload, ensure_ascii=True),
            )
            entity_overlap = len(query_entities & entry_entities)
            if entry.action_name in {"open_application", "focus_window", "run_command"} and query_entities and entity_overlap <= 0:
                continue
            if entry.action_name == "list_windows" and query_entities and entity_overlap <= 0:
                continue
            if text_entry_query and entry.action_name in {"desktop_click", "desktop_type", "desktop_hotkey"}:
                if query_entities and not (query_entities & task_entities):
                    continue
            recency_days = max((now - (entry.last_used_at or entry.created_at or now)) / 86400.0, 0.0)
            recency_bonus = max(0.0, 4.0 - min(recency_days, 4.0)) * 0.1
            action_bonus = ACTION_PRIORITY.get(entry.action_name, 1.0)
            generic_penalty = 0.6 if entry.action_name == "list_windows" else 0.0
            query_intent_bonus = 0.0
            if wants_opening and not already_open:
                if entry.action_name == "run_command":
                    query_intent_bonus += 1.2
                elif entry.action_name == "open_application":
                    query_intent_bonus += 0.9
                elif entry.action_name == "focus_window":
                    query_intent_bonus -= 0.6
            if already_open:
                if entry.action_name == "focus_window":
                    query_intent_bonus += 1.0
                elif entry.action_name in {"run_command", "open_application"}:
                    query_intent_bonus -= 0.2
            if text_entry_query:
                if entry.action_name == "desktop_click":
                    query_intent_bonus -= 1.4
                elif entry.action_name == "desktop_type":
                    query_intent_bonus -= 1.0
            score = (
                overlap
                + (entity_overlap * 3.0)
                + min(entry.success_count, 5) * 0.25
                + recency_bonus
                + action_bonus
                + query_intent_bonus
                - generic_penalty
            )
            scored.append((score, entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in scored[:limit]]

    def format_context(self, entries: list[MethodEntry]) -> str:
        if not entries:
            return ""
        lines = []
        for entry in entries:
            payload = json.dumps(entry.action_payload, ensure_ascii=True)
            lines.append(
                f"- task='{entry.task[:80]}' -> {entry.action_name} {payload} | result={entry.result[:90]}"
            )
        return "KNOWN EFFECTIVE METHODS:\n" + "\n".join(lines)

    def format_context_for_planner(self, entries: list[MethodEntry]) -> str:
        """Like format_context but strips desktop_click entries with hardcoded x/y
        coordinates.  Coordinates are screen-state-dependent and go stale immediately;
        showing them to the planner causes it to bake stale coords into task descriptions
        which then get reused verbatim every session.  Launch/focus/navigate actions are
        kept because they are stable across sessions."""
        if not entries:
            return ""
        _COORD_ACTIONS = {"desktop_click", "click_desktop", "move_mouse"}
        lines = []
        for entry in entries:
            payload = entry.action_payload or {}
            if entry.action_name in _COORD_ACTIONS and ("x" in payload or "y" in payload):
                # Omit coordinate-based clicks — they go stale and poison the planner
                continue
            payload_str = json.dumps(payload, ensure_ascii=True)
            lines.append(
                f"- task='{entry.task[:80]}' -> {entry.action_name} {payload_str} | result={entry.result[:90]}"
            )
        if not lines:
            return ""
        return "KNOWN EFFECTIVE METHODS:\n" + "\n".join(lines)
