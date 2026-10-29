"""Provider-neutral Gmail tool definitions and safe function-call decoding.

All tool calls use the existing single-action execution loop. A Gmail "send"
call creates a reviewable pending message; sending is only possible through
the separate human approval API.
"""
from __future__ import annotations

import json
from typing import Any

FIELDS = {
    "gmail_search": {
        "description": "Search the connected user's Gmail inbox and other messages. Use Gmail search syntax.",
        "properties": {"query": {"type": "string", "description": "Gmail search query"},
                       "limit": {"type": "integer", "minimum": 1, "maximum": 10}},
        "required": ["query"],
    },
    "gmail_read": {
        "description": "Read the plain-text body of a Gmail message returned by gmail_search.",
        "properties": {"message_id": {"type": "string", "description": "Gmail message ID"}},
        "required": ["message_id"],
    },
    "gmail_list_labels": {
        "description": "List labels available in the connected Gmail mailbox.",
        "properties": {},
        "required": [],
    },
    "gmail_modify_labels": {
        "description": "Add or remove labels from a message, e.g. UNREAD or INBOX. Never delete permanently.",
        "properties": {
            "message_id": {"type": "string"},
            "add_labels": {"type": "array", "items": {"type": "string"}},
            "remove_labels": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["message_id"],
    },
    "gmail_create_draft": {
        "description": "Save an email as a Gmail draft, without sending it.",
        "properties": {
            "to": {"type": "string", "description": "One email address"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "subject", "body"],
    },
    "gmail_queue_reply": {
        "description": "Prepare a reply to a selected Gmail message for user review. Does not send.",
        "properties": {"message_id": {"type": "string"}, "body": {"type": "string"}},
        "required": ["message_id", "body"],
    },
    "gmail_send": {
        "description": "Prepare an outgoing email for explicit human approval. Never actually sends until approved in Settings.",
        "properties": {
            "to": {"type": "string", "description": "One email address"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "subject", "body"],
    },
}


def openai_tools() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": name,
            "description": definition["description"],
            "parameters": {
                "type": "object",
                "properties": definition["properties"],
                "required": definition["required"],
                "additionalProperties": False,
            },
        }} for name, definition in FIELDS.items()
    ]


def gemini_tools() -> list[dict]:
    """Translate JSON-schema-ish OpenAI declarations to Gemini parameters."""
    def adapt(value: dict) -> dict:
        # Gemini supports a subset of JSON Schema; omit OpenAI-only keys.
        converted = {k: v for k, v in value.items() if k != "additionalProperties"}
        if "type" in converted:
            converted["type"] = str(converted["type"]).upper()
        if "items" in converted:
            converted["items"] = adapt(converted["items"])
        if "properties" in converted:
            converted["properties"] = {key: adapt(item) for key, item in converted["properties"].items()}
        return converted

    return [{"functionDeclarations": [
        {"name": item["function"]["name"], "description": item["function"]["description"],
         "parameters": adapt(item["function"]["parameters"])}
        for item in openai_tools()
    ]}]


def anthropic_tools() -> list[dict]:
    return [{"name": item["function"]["name"], "description": item["function"]["description"],
             "input_schema": item["function"]["parameters"]} for item in openai_tools()]


def _verified_call(name: str, arguments: Any) -> str | None:
    if name not in FIELDS:
        return None
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return None
    if not isinstance(arguments, dict):
        return None
    definition = FIELDS[name]
    if any(key not in arguments for key in definition["required"]):
        return None
    if any(key not in definition["properties"] for key in arguments):
        return None
    if name == "gmail_send":
        name = "gmail_queue_send"  # Intentional approval gate.
    if name == "gmail_search":
        try:
            arguments["limit"] = max(1, min(int(arguments.get("limit", 5)), 10))
        except (TypeError, ValueError):
            return None
    return json.dumps({"action": name, **arguments}, ensure_ascii=False)


def from_openai_message(message: dict) -> str | None:
    for call in (message.get("tool_calls") or [])[:1]:
        func = call.get("function") or {}
        result = _verified_call(func.get("name", ""), func.get("arguments"))
        if result:
            return result
    return None


def from_ollama_message(message: dict) -> str | None:
    return from_openai_message(message)


def from_gemini_response(payload: dict) -> str | None:
    for candidate in payload.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            fn = part.get("functionCall") or {}
            result = _verified_call(fn.get("name", ""), fn.get("args"))
            if result:
                return result
    return None


def from_anthropic_response(payload: dict) -> str | None:
    for item in payload.get("content") or []:
        if item.get("type") == "tool_use":
            result = _verified_call(item.get("name", ""), item.get("input"))
            if result:
                return result
    return None
