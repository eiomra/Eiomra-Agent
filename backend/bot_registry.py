"""Small durable registry for user-visible bot workspaces."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BotRegistry:
    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(path):
            self._save([self._default_bot()])

    @staticmethod
    def _default_bot() -> dict[str, Any]:
        now = _now()
        return {
            "id": "primary",
            "name": "Primary Browser Agent",
            "role": "Web research, local files, desktop tools, and execution",
            "status": "ready",
            "current_task_id": "",
            "current_goal": "",
            "last_url": "",
            "last_title": "",
            "created_at": now,
            "updated_at": now,
        }

    def list(self) -> list[dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
            bots = value if isinstance(value, list) else []
        except Exception:
            bots = []
        if not any(item.get("id") == "primary" for item in bots):
            bots.insert(0, self._default_bot())
            self._save(bots)
        return bots

    def get(self, bot_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list() if item.get("id") == bot_id), None)

    def create(self, name: str, role: str = "Browser and workspace automation") -> dict[str, Any]:
        bots = self.list()
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "bot"
        bot_id = base
        suffix = 2
        existing = {item.get("id") for item in bots}
        while bot_id in existing:
            bot_id = f"{base}-{suffix}"
            suffix += 1
        now = _now()
        bot = {
            "id": bot_id,
            "name": name.strip() or "New Bot",
            "role": role.strip() or "Browser and workspace automation",
            "status": "ready",
            "current_task_id": "",
            "current_goal": "",
            "last_url": "",
            "last_title": "",
            "created_at": now,
            "updated_at": now,
        }
        bots.append(bot)
        self._save(bots)
        return bot

    def update(self, bot_id: str, **patch: Any) -> dict[str, Any] | None:
        bots = self.list()
        bot = next((item for item in bots if item.get("id") == bot_id), None)
        if not bot:
            return None
        allowed = {"name", "role", "status", "current_task_id", "current_goal", "last_url", "last_title"}
        bot.update({key: value for key, value in patch.items() if key in allowed})
        bot["updated_at"] = _now()
        self._save(bots)
        return bot

    def _save(self, bots: list[dict[str, Any]]) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temp_path = self.path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(bots, handle, indent=2)
        os.replace(temp_path, self.path)
