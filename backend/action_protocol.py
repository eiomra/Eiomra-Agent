"""
Backend action protocol helpers.

This mirrors the Nexus-style action layer in a backend-friendly Python form:
- typed action names
- normalization/coercion of model output
- resilient response parsing
- compact action summaries for history/session logs
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

ActionName = Literal[
    "navigate",
    "click",
    "click_text",
    "open_application",
    "list_windows",
    "focus_window",
    "desktop_click",
    "click_desktop",
    "desktop_type",
    "type_desktop",
    "desktop_scroll",
    "scroll_desktop",
    "desktop_hotkey",
    "hotkey",
    "desktop_press_key",
    "press_key",
    "desktop_screenshot",
    "take_desktop_screenshot",
    "find_text_on_screen",
    "move_mouse",
    "drag_mouse",
    "select_menu_item",
    "close_window",
    "upload_file_to_page",
    "upload_files_to_page",
    "click_and_upload",
    "type",
    "type_and_submit",
    "press",
    "scroll",
    "wait",
    "back",
    "read_page",
    "focus_field",
    "read_file",
    "read_json",
    "read_csv",
    "list_directory",
    "create_file",
    "write_file",
    "patch_file",
    "create_pdf",
    "create_json",
    "create_csv",
    "extract_pdf_text",
    "ocr_image_to_text",
    "download_url",
    "create_markdown_report",
    "write_yaml",
    "convert_image_format",
    "screenshot_page_to_file",
    "save_page_html",
    "print_page_to_pdf",
    "create_directory",
    "extract_archive",
    "search_in_files",
    "copy_paths",
    "rename_path",
    "move_paths",
    "delete_paths",
    "zip_paths",
    "run_command",
    "complete_task",
    "skip_task",
    "done",
]

ACTION_NAMES: set[str] = {
    "navigate",
    "click",
    "click_text",
    "open_application",
    "list_windows",
    "focus_window",
    "desktop_click",
    "click_desktop",
    "desktop_type",
    "type_desktop",
    "desktop_scroll",
    "scroll_desktop",
    "desktop_hotkey",
    "hotkey",
    "desktop_press_key",
    "press_key",
    "desktop_screenshot",
    "take_desktop_screenshot",
    "find_text_on_screen",
    "move_mouse",
    "drag_mouse",
    "select_menu_item",
    "close_window",
    "upload_file_to_page",
    "upload_files_to_page",
    "click_and_upload",
    "type",
    "type_and_submit",
    "press",
    "scroll",
    "wait",
    "back",
    "read_page",
    "focus_field",
    "read_file",
    "read_json",
    "read_csv",
    "list_directory",
    "create_file",
    "write_file",
    "patch_file",
    "create_pdf",
    "create_json",
    "create_csv",
    "extract_pdf_text",
    "ocr_image_to_text",
    "download_url",
    "create_markdown_report",
    "write_yaml",
    "convert_image_format",
    "screenshot_page_to_file",
    "save_page_html",
    "print_page_to_pdf",
    "create_directory",
    "extract_archive",
    "search_in_files",
    "copy_paths",
    "rename_path",
    "move_paths",
    "delete_paths",
    "zip_paths",
    "run_command",
    "complete_task",
    "skip_task",
    "done",
}


@dataclass(slots=True)
class ActionDecision:
    action: str
    thought: str = ""
    summary: str = ""
    url: str = ""
    text: str = ""
    selector: str = ""
    path: str = ""
    source: str = ""
    destination: str = ""
    key: str = ""
    direction: str = ""
    hint: str = ""
    cwd: str = ""
    command: str = ""
    content: str = ""
    title: str = ""
    format: str = ""
    button: str = ""
    pattern: str = ""
    old_text: str = ""
    new_text: str = ""
    task_id: str = ""
    finding: str = ""
    reason: str = ""
    x: int | None = None
    y: int | None = None
    end_x: int | None = None
    end_y: int | None = None
    amount: int | None = None
    seconds: int | None = None
    append: bool | None = None
    full_page: bool | None = None
    recursive: bool | None = None
    replace_all: bool | None = None
    value: str = ""
    paths: list[str] | None = None
    sources: list[str] | None = None
    keys: list[str] | None = None
    extras: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        extras = raw.pop("extras") or {}
        merged = {**raw, **extras}
        return {k: v for k, v in merged.items() if v not in ("", None, {}, [])}


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on"}


def _clean_raw_response(raw: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    return cleaned


def _extract_first_balanced_json_object(text: str) -> dict[str, Any] | None:
    depth = 0
    start = None
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                candidate = text[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except Exception:
                    continue
                if isinstance(parsed, dict):
                    return parsed
    return None


def normalize_action_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload or {})
    action = str(normalized.get("action", "")).strip()
    if not action:
        action = "wait"
    normalized["action"] = action

    for field in ("thought", "summary", "url", "text", "selector", "path", "source",
                  "destination", "key", "direction", "hint", "cwd", "command",
                  "content", "title", "format", "button", "pattern", "old_text", "new_text", "task_id", "finding", "reason", "value"):
        if field in normalized and normalized[field] is None:
            normalized[field] = ""
        elif field in normalized:
            normalized[field] = str(normalized[field]).strip()

    for field in ("append", "full_page", "recursive", "replace_all"):
        if field in normalized:
            normalized[field] = _coerce_bool(normalized[field])

    for field in ("paths", "sources", "keys"):
        if field in normalized:
            value = normalized[field]
            if isinstance(value, list):
                normalized[field] = [str(item).strip() for item in value if str(item).strip()]
            elif isinstance(value, str) and value.strip():
                normalized[field] = [value.strip()]
            else:
                normalized[field] = []

    for field in ("x", "y", "end_x", "end_y", "amount", "seconds"):
        if field in normalized:
            normalized[field] = _coerce_int(normalized[field])

    alias_map = {
        "click_desktop": "desktop_click",
        "type_desktop": "desktop_type",
        "scroll_desktop": "desktop_scroll",
        "hotkey": "desktop_hotkey",
        "press_key": "desktop_press_key",
        "take_desktop_screenshot": "desktop_screenshot",
    }
    normalized["action"] = alias_map.get(normalized["action"], normalized["action"])

    if normalized["action"] == "wait" and normalized.get("seconds") is None:
        normalized["seconds"] = 2
    if normalized["action"] == "scroll" and not normalized.get("direction"):
        normalized["direction"] = "down"
    if normalized["action"] == "press" and not normalized.get("key"):
        normalized["key"] = "Enter"

    return normalized


def action_from_payload(payload: dict[str, Any]) -> ActionDecision:
    normalized = normalize_action_payload(payload)
    known_fields = {
        "action", "thought", "summary", "url", "text", "selector", "path", "source",
        "destination", "key", "direction", "hint", "cwd", "command", "content", "title", "format", "button",
        "pattern", "old_text", "new_text", "task_id", "finding", "reason", "x", "y",
        "end_x", "end_y", "amount", "seconds", "append", "full_page", "recursive", "replace_all", "value", "paths", "sources", "keys",
    }
    extras = {k: v for k, v in normalized.items() if k not in known_fields}
    return ActionDecision(
        action=normalized.get("action", "wait"),
        thought=normalized.get("thought", ""),
        summary=normalized.get("summary", ""),
        url=normalized.get("url", ""),
        text=normalized.get("text", ""),
        selector=normalized.get("selector", ""),
        path=normalized.get("path", ""),
        source=normalized.get("source", ""),
        destination=normalized.get("destination", ""),
        key=normalized.get("key", ""),
        direction=normalized.get("direction", ""),
        hint=normalized.get("hint", ""),
        cwd=normalized.get("cwd", ""),
        command=normalized.get("command", ""),
        content=normalized.get("content", ""),
        title=normalized.get("title", ""),
        format=normalized.get("format", ""),
        button=normalized.get("button", ""),
        pattern=normalized.get("pattern", ""),
        old_text=normalized.get("old_text", ""),
        new_text=normalized.get("new_text", ""),
        task_id=normalized.get("task_id", ""),
        finding=normalized.get("finding", ""),
        reason=normalized.get("reason", ""),
        x=normalized.get("x"),
        y=normalized.get("y"),
        end_x=normalized.get("end_x"),
        end_y=normalized.get("end_y"),
        amount=normalized.get("amount"),
        seconds=normalized.get("seconds"),
        append=normalized.get("append"),
        full_page=normalized.get("full_page"),
        recursive=normalized.get("recursive"),
        replace_all=normalized.get("replace_all"),
        value=normalized.get("value", ""),
        paths=normalized.get("paths"),
        sources=normalized.get("sources"),
        keys=normalized.get("keys"),
        extras=extras or None,
    )


def action_to_history_entry(action: dict[str, Any]) -> str:
    name = str(action.get("action", "wait"))
    details = {
        key: value for key, value in action.items()
        if key not in {"action", "thought", "summary"} and value not in ("", None, [], {})
    }
    if "content" in details and isinstance(details["content"], str):
        details["content"] = f"<{len(details['content'])} chars>"
    if "old_text" in details and isinstance(details["old_text"], str):
        details["old_text"] = details["old_text"][:80]
    if "new_text" in details and isinstance(details["new_text"], str):
        details["new_text"] = details["new_text"][:80]
    if not details:
        return name
    return f"{name}: {json.dumps(details, ensure_ascii=True)}"


def parse_action_response(raw: str) -> dict[str, Any]:
    """
    Extract the first action object from model output and normalize it.

    Recovery strategy:
    1. Parse a balanced JSON object directly
    2. Retry after fixing trailing commas
    3. Reconstruct from commonly emitted fields
    4. Infer a minimal action from plain text
    """
    cleaned = _clean_raw_response(raw)

    direct = _extract_first_balanced_json_object(cleaned)
    if direct and "action" in direct:
        return action_from_payload(direct).to_dict()

    fixed = re.sub(r",\s*([}\]])", r"\1", cleaned)
    direct = _extract_first_balanced_json_object(fixed)
    if direct and "action" in direct:
        return action_from_payload(direct).to_dict()

    action_match = re.search(r'"action"\s*:\s*"([^"]+)"', cleaned)
    thought_match = re.search(r'"thought"\s*:\s*"([^"]*)"', cleaned)
    summary_match = re.search(r'"summary"\s*:\s*"([^"]*)"', cleaned)
    if action_match:
        payload: dict[str, Any] = {"action": action_match.group(1)}
        if thought_match:
            payload["thought"] = thought_match.group(1)
        if summary_match:
            payload["summary"] = summary_match.group(1)
        for field in (
            "url", "text", "selector", "path", "source", "destination", "key",
            "direction", "hint", "cwd", "command", "content", "title", "old_text", "new_text",
            "task_id", "finding", "reason", "value", "x", "y", "amount", "seconds",
        ):
            match = re.search(rf'"{field}"\s*:\s*"?([^",}}\n]+)"?', cleaned)
            if match:
                payload[field] = match.group(1).strip().strip('"')
        return action_from_payload(payload).to_dict()

    lowered = cleaned.lower()
    if "click_text" in lowered:
        match = re.search(r'click_text[^"]*"([^"]+)"', cleaned, re.IGNORECASE)
        return action_from_payload({
            "action": "click_text",
            "text": match.group(1) if match else "",
            "thought": cleaned,
            "summary": "Inferred click_text",
        }).to_dict()
    if "navigate" in lowered:
        match = re.search(r"""https?://[^\s'"]+""", cleaned)
        return action_from_payload({
            "action": "navigate",
            "url": match.group(0) if match else "",
            "thought": cleaned,
            "summary": "Inferred navigate",
        }).to_dict()
    if "type_and_submit" in lowered or "send message" in lowered:
        match = re.search(r'"([^"]{10,})"', cleaned)
        return action_from_payload({
            "action": "type_and_submit",
            "text": match.group(1) if match else "",
            "thought": cleaned,
            "summary": "Inferred type_and_submit",
        }).to_dict()

    return action_from_payload({
        "thought": cleaned[:300],
        "action": "wait",
        "summary": "Could not parse model response - waiting",
        "seconds": 2,
    }).to_dict()
