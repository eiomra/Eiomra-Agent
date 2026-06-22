"""
Backend session/orchestrator model for the browser agent.

This is intentionally backend-native, but it borrows the core ideas from
the Nexus extension:
- durable session records
- explicit phases/status
- lane state updates
- event log for auditing/replay
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

AgentLaneId = str
AgentPhase = Literal[
    "plan",
    "inspect",
    "act",
    "verify",
    "repair",
    "review",
    "done",
    "failed",
    "awaiting-approval",
]
SessionStatus = Literal["running", "awaiting-approval", "completed", "failed"]
LaneStatus = Literal["idle", "running", "blocked", "done", "failed"]


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


@dataclass(slots=True)
class AgentLaneRecord:
    id: AgentLaneId
    role: str
    status: LaneStatus
    current_step: str
    owned_resources: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SessionEvent:
    at: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentSessionRecord:
    id: str
    task_id: str
    request: str
    phase: AgentPhase
    status: SessionStatus
    created_at: str
    updated_at: str
    lanes: list[AgentLaneRecord]
    metadata: dict[str, Any] = field(default_factory=dict)
    file_owners: dict[str, AgentLaneId] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    events: list[SessionEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentSessionStore:
    def __init__(self, storage_dir: str, max_sessions: int = 25):
        self.storage_dir = storage_dir
        self.max_sessions = max_sessions
        os.makedirs(self.storage_dir, exist_ok=True)
        self.index_path = os.path.join(self.storage_dir, "index.json")

    def list_sessions(self) -> list[AgentSessionRecord]:
        sessions = []
        for item in self._read_index():
            path = item.get("path")
            if not path or not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                sessions.append(self._from_dict(payload))
            except Exception:
                continue
        return sessions

    def get_session(self, task_id: str) -> AgentSessionRecord | None:
        for session in self.list_sessions():
            if session.task_id == task_id:
                return session
        return None

    def save(self, session: AgentSessionRecord) -> None:
        path = self._session_path(session.id)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(session.to_dict(), handle, indent=2)

        index = [item for item in self._read_index() if item.get("id") != session.id and item.get("task_id") != session.task_id]
        index.insert(0, {
            "id": session.id,
            "task_id": session.task_id,
            "request": session.request,
            "status": session.status,
            "phase": session.phase,
            "updated_at": session.updated_at,
            "path": path,
        })
        trimmed = index[: self.max_sessions]
        with open(self.index_path, "w", encoding="utf-8") as handle:
            json.dump(trimmed, handle, indent=2)

    def _session_path(self, session_id: str) -> str:
        return os.path.join(self.storage_dir, f"{session_id}.json")

    def _read_index(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.index_path):
            return []
        try:
            with open(self.index_path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            return raw if isinstance(raw, list) else []
        except Exception:
            return []

    def _from_dict(self, payload: dict[str, Any]) -> AgentSessionRecord:
        lanes = [AgentLaneRecord(**lane) for lane in payload.get("lanes", [])]
        events = [SessionEvent(**event) for event in payload.get("events", [])]
        return AgentSessionRecord(
            id=payload["id"],
            task_id=payload["task_id"],
            request=payload["request"],
            phase=payload["phase"],
            status=payload["status"],
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
            lanes=lanes,
            metadata=payload.get("metadata", {}),
            file_owners=payload.get("file_owners", {}),
            conflicts=payload.get("conflicts", []),
            events=events,
        )


class AgentOrchestrator:
    def __init__(self, store: AgentSessionStore):
        self.store = store

    @staticmethod
    def default_lanes() -> list[AgentLaneRecord]:
        return [
            AgentLaneRecord("explorer", "Explore page context and choose the next target", "running", "planning"),
            AgentLaneRecord("implementation", "Execute browser actions and task mutations", "idle", "waiting"),
            AgentLaneRecord("review", "Review progress, replans, and completion state", "idle", "waiting"),
        ]

    def create_session(self, task_id: str, request: str, metadata: dict[str, Any] | None = None) -> AgentSessionRecord:
        now = utc_now()
        lane_payloads = (metadata or {}).get("lanes") or []
        lanes = []
        for item in lane_payloads:
            if not isinstance(item, dict):
                continue
            try:
                lanes.append(AgentLaneRecord(**item))
            except TypeError:
                continue
        if not lanes:
            lanes = self.default_lanes()
        session = AgentSessionRecord(
            id=f"session-{int(time.time() * 1000)}",
            task_id=task_id,
            request=request,
            phase="plan",
            status="running",
            created_at=now,
            updated_at=now,
            lanes=lanes,
            metadata=metadata or {},
        )
        session.events.append(SessionEvent(at=utc_now(), kind="session_created", payload={"request": request, **(metadata or {})}))
        self.store.save(session)
        return session

    def resume_session(self, task_id: str) -> AgentSessionRecord | None:
        return self.store.get_session(task_id)

    def list_sessions(self) -> list[AgentSessionRecord]:
        return self.store.list_sessions()

    def update_phase(self, task_id: str, phase: AgentPhase, status: SessionStatus | None = None) -> AgentSessionRecord | None:
        session = self.resume_session(task_id)
        if not session:
            return None
        session.phase = phase
        if status:
            session.status = status
        session.updated_at = utc_now()
        self.store.save(session)
        return session

    def update_lane(self, task_id: str, lane_id: AgentLaneId, **patch: Any) -> AgentSessionRecord | None:
        session = self.resume_session(task_id)
        if not session:
            return None
        lane = next((item for item in session.lanes if item.id == lane_id), None)
        if not lane:
            role = str(patch.pop("role", lane_id.replace("_", " ").title() or "Worker"))
            status = str(patch.get("status", "idle"))
            current_step = str(patch.get("current_step", "waiting"))
            lane = AgentLaneRecord(lane_id, role, status, current_step)
            session.lanes.append(lane)
        for key, value in patch.items():
            if hasattr(lane, key):
                setattr(lane, key, value)
        lane.updated_at = utc_now()
        session.updated_at = utc_now()
        self.store.save(session)
        return session

    def set_lanes(self, task_id: str, lanes: list[dict[str, Any]]) -> AgentSessionRecord | None:
        session = self.resume_session(task_id)
        if not session:
            return None
        rebuilt: list[AgentLaneRecord] = []
        for item in lanes:
            if not isinstance(item, dict):
                continue
            try:
                rebuilt.append(AgentLaneRecord(**item))
            except TypeError:
                continue
        if not rebuilt:
            return session
        session.lanes = rebuilt
        session.updated_at = utc_now()
        self.store.save(session)
        return session

    def set_metadata(self, task_id: str, **metadata: Any) -> AgentSessionRecord | None:
        session = self.resume_session(task_id)
        if not session:
            return None
        session.metadata.update(metadata)
        session.updated_at = utc_now()
        self.store.save(session)
        return session

    def append_event(self, task_id: str, kind: str, payload: dict[str, Any] | None = None) -> AgentSessionRecord | None:
        session = self.resume_session(task_id)
        if not session:
            return None
        session.events.append(SessionEvent(at=utc_now(), kind=kind, payload=payload or {}))
        session.events = session.events[-200:]
        session.updated_at = utc_now()
        self.store.save(session)
        return session

    def claim_resource(self, task_id: str, lane_id: AgentLaneId, resource: str) -> tuple[bool, str | None]:
        session = self.resume_session(task_id)
        if not session:
            return True, None
        owner = session.file_owners.get(resource)
        if owner and owner != lane_id:
            session.conflicts.append({
                "resource": resource,
                "owner": owner,
                "attempted_by": lane_id,
                "at": utc_now(),
            })
            session.updated_at = utc_now()
            self.store.save(session)
            return False, owner

        session.file_owners[resource] = lane_id
        lane = next((item for item in session.lanes if item.id == lane_id), None)
        if lane and resource not in lane.owned_resources:
            lane.owned_resources.append(resource)
            lane.updated_at = utc_now()
        session.updated_at = utc_now()
        self.store.save(session)
        return True, None
