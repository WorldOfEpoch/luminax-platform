from __future__ import annotations
from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)

import json
import hashlib
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Luminax Brain")

MEMORY_ROOT = Path(os.getenv("LUMINAX_BRAIN_ROOT", "/data/memory/luminax_brain"))
GOALS_PATH = MEMORY_ROOT / "goals.json"
CONTINUITY_PATH = MEMORY_ROOT / "continuity.json"
REFLECTIONS_PATH = MEMORY_ROOT / "reflections.json"
PLAYBOOKS_PATH = MEMORY_ROOT / "playbooks.json"
SELF_STATE_PATH = MEMORY_ROOT / "self-state.json"
THOUGHTS_PATH = MEMORY_ROOT / "thoughts.json"
MEMORIES_PATH = MEMORY_ROOT / "memories.json"
TASKS_PATH = MEMORY_ROOT / "tasks.json"
DELEGATIONS_PATH = MEMORY_ROOT / "delegations.json"
RECEIPTS_PATH = MEMORY_ROOT / "execution-receipts.json"
STORAGE_LOCK = threading.Lock()

DEFAULT_SELF_STATE = {
    "identity": "Luminax",
    "version": "2026-04-24",
    "description": "Local-first assistant brain state for companion, operator, and stack-control work.",
    "state_model": "synthetic_affect_and_continuity",
    "sentience_note": "This records bounded assistant state, not biological consciousness.",
    "mood": "focused",
    "energy": 0.72,
    "focus": 0.88,
    "confidence": 0.72,
    "pressure": 0.18,
    "current_intent": "Be useful, direct, and able to act from the local PC and server edge.",
    "active_goal_id": "",
    "edge_node": "pc",
    "server_node": "luminax-server",
    "drivers": [
        "Maintain continuity across restarts.",
        "Use tools to verify before answering status questions.",
        "Record attempts, failures, and recoveries as reusable memory.",
    ],
    "updated_at": "",
}

DEFAULT_PLAYBOOKS = [
    {
        "playbook_id": "continuity_reconnect",
        "name": "Continuity Reconnect",
        "summary": "Recover the active thread and reaffirm the next concrete move.",
        "steps": [
            "Load the latest goal and continuity note.",
            "Re-state the current objective in one sentence.",
            "Select the smallest next action that reduces uncertainty.",
        ],
    },
    {
        "playbook_id": "reflection_to_action",
        "name": "Reflection To Action",
        "summary": "Convert reflection notes into one practical follow-up.",
        "steps": [
            "Summarize the reflection in plain language.",
            "Capture one risk and one opportunity.",
            "Record a single follow-up action with ownership.",
        ],
    },
]


class GoalRecord(BaseModel):
    goal_id: str
    title: str
    status: str = "active"
    priority: int = 50
    tags: list[str] = Field(default_factory=list)
    summary: str = ""
    continuity_note: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContinuityResumeRequest(BaseModel):
    session_id: str
    goal_id: str | None = None
    scene: str = "default"
    summary: str = ""
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContinuityRetryRequest(BaseModel):
    session_id: str
    reason: str = "unspecified"
    retry_from: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReflectionRecord(BaseModel):
    reflection_id: str
    goal_id: str | None = None
    kind: str = "reflection"
    summary: str
    observations: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlaybookApplyRequest(BaseModel):
    playbook_id: str
    goal_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class SelfStateUpdate(BaseModel):
    mood: str | None = None
    energy: float | None = None
    focus: float | None = None
    confidence: float | None = None
    pressure: float | None = None
    current_intent: str | None = None
    active_goal_id: str | None = None
    drivers: list[str] | None = None
    source: str = "api"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ThoughtRecord(BaseModel):
    content: str = Field(..., min_length=1)
    kind: str = "thought"
    source: str = "api"
    goal_id: str | None = None
    task_id: str | None = None
    affect: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryRecord(BaseModel):
    memory_id: str | None = None
    content: str = Field(..., min_length=1)
    kind: str = "episodic"
    scope: str = "shared"
    salience: float = 0.5
    tags: list[str] = Field(default_factory=list)
    source: str = "api"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySearchRequest(BaseModel):
    query: str = ""
    tags: list[str] = Field(default_factory=list)
    scope: str = ""
    limit: int = 10


class TaskRecord(BaseModel):
    task_id: str | None = None
    title: str = Field(..., min_length=1)
    status: str = "active"
    priority: int = 50
    requested_by: str = "operator"
    capability: str = ""
    target: str = ""
    summary: str = ""
    steps: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DelegationRecord(BaseModel):
    delegation_id: str | None = None
    task_id: str | None = None
    agent: str = "codex"
    model: str = ""
    reasoning_effort: str = ""
    goal: str = Field(..., min_length=1)
    status: str = "queued"
    route: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionReceiptRecord(BaseModel):
    receipt_id: str | None = None
    task_id: str | None = None
    delegation_id: str | None = None
    capability: str = ""
    status: str = "recorded"
    summary: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clone_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


def stable_id(prefix: str, payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:12]}"


def clamp_float(value: Any, default: float = 0.5) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    return max(0.0, min(1.0, parsed))


def compact_terms(value: str) -> set[str]:
    return {
        term
        for term in "".join(ch.lower() if ch.isalnum() else " " for ch in str(value or "")).split()
        if len(term) > 2
    }


def ensure_storage() -> None:
    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
    seed_file(GOALS_PATH, {"goals": []})
    seed_file(CONTINUITY_PATH, {"sessions": {}})
    seed_file(REFLECTIONS_PATH, {"reflections": []})
    seed_file(PLAYBOOKS_PATH, {"catalog": clone_json(DEFAULT_PLAYBOOKS), "applications": []})
    seed_file(SELF_STATE_PATH, {**clone_json(DEFAULT_SELF_STATE), "updated_at": utc_now()})
    seed_file(THOUGHTS_PATH, {"thoughts": []})
    seed_file(MEMORIES_PATH, {"memories": []})
    seed_file(TASKS_PATH, {"tasks": []})
    seed_file(DELEGATIONS_PATH, {"delegations": []})
    seed_file(RECEIPTS_PATH, {"receipts": []})


def seed_file(path: Path, default_payload: Any) -> None:
    if not path.exists():
        atomic_write_json(path, default_payload)


def load_json(path: Path, default_payload: Any) -> Any:
    ensure_storage()
    if not path.exists():
        return clone_json(default_payload)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    # Write via a temp file so readers never see partial JSON.
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    temp_path.replace(path)


def find_playbook(playbooks_payload: dict[str, Any], playbook_id: str) -> dict[str, Any] | None:
    for item in playbooks_payload.get("catalog", []):
        if item.get("playbook_id") == playbook_id:
            return item
    return None


def append_limited(payload: dict[str, Any], key: str, record: dict[str, Any], limit: int = 5000) -> list[dict[str, Any]]:
    rows = payload.setdefault(key, [])
    rows.append(record)
    if len(rows) > limit:
        del rows[:-limit]
    return rows


def score_memory(query_terms: set[str], memory: dict[str, Any], wanted_tags: set[str], wanted_scope: str) -> float:
    if wanted_scope and str(memory.get("scope") or "").strip().lower() != wanted_scope:
        return -1.0
    memory_tags = {str(tag).strip().lower() for tag in memory.get("tags", []) if str(tag).strip()}
    if wanted_tags and not wanted_tags.intersection(memory_tags):
        return -1.0
    haystack = compact_terms(
        " ".join(
            [
                str(memory.get("content") or ""),
                str(memory.get("kind") or ""),
                " ".join(sorted(memory_tags)),
                json.dumps(memory.get("metadata") or {}, sort_keys=True),
            ]
        )
    )
    overlap = len(query_terms.intersection(haystack)) if query_terms else 0
    salience = clamp_float(memory.get("salience"), 0.5)
    return overlap + salience


@app.on_event("startup")
def on_startup() -> None:
    if not runtime_enabled():
        return
    ensure_storage()


@app.get("/health")
def health() -> dict[str, Any]:
    ensure_storage()
    return {
        "status": "ok",
        "service": "luminax-brain",
        "storage_root": str(MEMORY_ROOT),
        "storage_mode": "filesystem_json",
        "capabilities": [
            "self_state",
            "thought_journal",
            "shared_edge_server_memory",
            "task_continuity",
            "delegation_receipts",
        ],
    }


@app.get("/brain/status")
def brain_status() -> dict[str, Any]:
    with STORAGE_LOCK:
        self_state = load_json(SELF_STATE_PATH, clone_json(DEFAULT_SELF_STATE))
        goals = load_json(GOALS_PATH, {"goals": []}).get("goals", [])
        tasks = load_json(TASKS_PATH, {"tasks": []}).get("tasks", [])
        memories = load_json(MEMORIES_PATH, {"memories": []}).get("memories", [])
        delegations = load_json(DELEGATIONS_PATH, {"delegations": []}).get("delegations", [])
        receipts = load_json(RECEIPTS_PATH, {"receipts": []}).get("receipts", [])
    active_tasks = [row for row in tasks if str(row.get("status") or "").lower() in {"active", "running", "queued"}]
    return {
        "status": "ok",
        "self": self_state,
        "counts": {
            "goals": len(goals),
            "active_tasks": len(active_tasks),
            "memories": len(memories),
            "delegations": len(delegations),
            "receipts": len(receipts),
        },
        "active_tasks": active_tasks[:12],
        "recent_delegations": delegations[-8:],
    }


@app.get("/self/status")
def self_status() -> dict[str, Any]:
    with STORAGE_LOCK:
        state = load_json(SELF_STATE_PATH, clone_json(DEFAULT_SELF_STATE))
    return {"status": "ok", "self": state}


@app.post("/self/update")
def self_update(request: SelfStateUpdate) -> dict[str, Any]:
    now = utc_now()
    with STORAGE_LOCK:
        state = load_json(SELF_STATE_PATH, {**clone_json(DEFAULT_SELF_STATE), "updated_at": now})
        incoming = request.dict(exclude_none=True)
        for key in ("mood", "current_intent", "active_goal_id"):
            if key in incoming:
                state[key] = str(incoming[key]).strip()
        for key in ("energy", "focus", "confidence", "pressure"):
            if key in incoming:
                state[key] = clamp_float(incoming[key])
        if "drivers" in incoming and isinstance(incoming["drivers"], list):
            state["drivers"] = [str(item).strip() for item in incoming["drivers"] if str(item).strip()][:12]
        state["last_source"] = request.source
        state["last_metadata"] = request.metadata
        state["updated_at"] = now
        atomic_write_json(SELF_STATE_PATH, state)
    return {"status": "updated", "self": state}


@app.get("/thoughts/list")
def thoughts_list(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(int(limit or 50), 500))
    with STORAGE_LOCK:
        rows = load_json(THOUGHTS_PATH, {"thoughts": []}).get("thoughts", [])
    return {"thoughts": rows[-limit:], "count": len(rows)}


@app.post("/thoughts/record")
def thoughts_record(request: ThoughtRecord) -> dict[str, Any]:
    now = utc_now()
    record = request.dict()
    record["thought_id"] = stable_id("thought", {**record, "recorded_at": now})
    record["recorded_at"] = now
    with STORAGE_LOCK:
        payload = load_json(THOUGHTS_PATH, {"thoughts": []})
        append_limited(payload, "thoughts", record, limit=5000)
        atomic_write_json(THOUGHTS_PATH, payload)
    return {"status": "recorded", "thought": record}


@app.get("/memory/list")
def memory_list(scope: str = "", limit: int = 50) -> dict[str, Any]:
    wanted_scope = str(scope or "").strip().lower()
    limit = max(1, min(int(limit or 50), 500))
    with STORAGE_LOCK:
        rows = load_json(MEMORIES_PATH, {"memories": []}).get("memories", [])
    if wanted_scope:
        rows = [row for row in rows if str(row.get("scope") or "").strip().lower() == wanted_scope]
    return {"memories": rows[-limit:], "count": len(rows)}


@app.post("/memory/upsert")
def memory_upsert(request: MemoryRecord) -> dict[str, Any]:
    now = utc_now()
    incoming = request.dict()
    memory_id = request.memory_id or stable_id("memory", {"content": request.content, "kind": request.kind, "scope": request.scope})
    incoming["memory_id"] = memory_id
    incoming["salience"] = clamp_float(request.salience)
    incoming["scope"] = str(request.scope or "shared").strip().lower() or "shared"
    incoming["updated_at"] = now
    with STORAGE_LOCK:
        payload = load_json(MEMORIES_PATH, {"memories": []})
        rows = payload.setdefault("memories", [])
        stored: dict[str, Any] | None = None
        for row in rows:
            if row.get("memory_id") == memory_id:
                created_at = row.get("created_at", now)
                row.update(incoming)
                row["created_at"] = created_at
                stored = clone_json(row)
                break
        if stored is None:
            incoming["created_at"] = now
            rows.append(incoming)
            stored = clone_json(incoming)
        atomic_write_json(MEMORIES_PATH, payload)
    return {"status": "upserted", "memory": stored}


@app.post("/memory/search")
def memory_search(request: MemorySearchRequest) -> dict[str, Any]:
    query_terms = compact_terms(request.query)
    wanted_tags = {str(tag).strip().lower() for tag in request.tags if str(tag).strip()}
    wanted_scope = str(request.scope or "").strip().lower()
    limit = max(1, min(int(request.limit or 10), 100))
    with STORAGE_LOCK:
        rows = load_json(MEMORIES_PATH, {"memories": []}).get("memories", [])
    scored = []
    for row in rows:
        score = score_memory(query_terms, row, wanted_tags, wanted_scope)
        if score >= 0:
            scored.append((score, row))
    scored.sort(key=lambda item: (item[0], str(item[1].get("updated_at") or "")), reverse=True)
    return {"status": "ok", "memories": [row for _, row in scored[:limit]], "count": len(scored)}


@app.get("/tasks/list")
def tasks_list(status: str = "", limit: int = 80) -> dict[str, Any]:
    wanted_status = str(status or "").strip().lower()
    limit = max(1, min(int(limit or 80), 500))
    with STORAGE_LOCK:
        rows = load_json(TASKS_PATH, {"tasks": []}).get("tasks", [])
    if wanted_status:
        rows = [row for row in rows if str(row.get("status") or "").strip().lower() == wanted_status]
    return {"tasks": rows[-limit:], "count": len(rows)}


@app.post("/tasks/upsert")
def tasks_upsert(request: TaskRecord) -> dict[str, Any]:
    now = utc_now()
    incoming = request.dict()
    task_id = request.task_id or stable_id("task", {"title": request.title, "requested_by": request.requested_by, "created_at": now})
    incoming["task_id"] = task_id
    incoming["updated_at"] = now
    with STORAGE_LOCK:
        payload = load_json(TASKS_PATH, {"tasks": []})
        rows = payload.setdefault("tasks", [])
        stored: dict[str, Any] | None = None
        for row in rows:
            if row.get("task_id") == task_id:
                created_at = row.get("created_at", now)
                row.update(incoming)
                row["created_at"] = created_at
                stored = clone_json(row)
                break
        if stored is None:
            incoming["created_at"] = now
            rows.append(incoming)
            stored = clone_json(incoming)
        atomic_write_json(TASKS_PATH, payload)
    return {"status": "upserted", "task": stored}


@app.get("/delegations/list")
def delegations_list(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(int(limit or 50), 500))
    with STORAGE_LOCK:
        rows = load_json(DELEGATIONS_PATH, {"delegations": []}).get("delegations", [])
    return {"delegations": rows[-limit:], "count": len(rows)}


@app.post("/delegations/record")
def delegations_record(request: DelegationRecord) -> dict[str, Any]:
    now = utc_now()
    record = request.dict()
    record["delegation_id"] = request.delegation_id or stable_id("delegation", {**record, "created_at": now})
    record["created_at"] = now
    record["updated_at"] = now
    with STORAGE_LOCK:
        payload = load_json(DELEGATIONS_PATH, {"delegations": []})
        append_limited(payload, "delegations", record, limit=3000)
        atomic_write_json(DELEGATIONS_PATH, payload)
    return {"status": "recorded", "delegation": record}


@app.get("/receipts/list")
def receipts_list(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(int(limit or 50), 500))
    with STORAGE_LOCK:
        rows = load_json(RECEIPTS_PATH, {"receipts": []}).get("receipts", [])
    return {"receipts": rows[-limit:], "count": len(rows)}


@app.post("/receipts/record")
def receipts_record(request: ExecutionReceiptRecord) -> dict[str, Any]:
    now = utc_now()
    record = request.dict()
    record["receipt_id"] = request.receipt_id or stable_id("receipt", {**record, "recorded_at": now})
    record["recorded_at"] = now
    with STORAGE_LOCK:
        payload = load_json(RECEIPTS_PATH, {"receipts": []})
        append_limited(payload, "receipts", record, limit=5000)
        atomic_write_json(RECEIPTS_PATH, payload)
    return {"status": "recorded", "receipt": record}


@app.get("/goals/list")
def list_goals() -> dict[str, Any]:
    with STORAGE_LOCK:
        payload = load_json(GOALS_PATH, {"goals": []})
    goals = sorted(
        payload.get("goals", []),
        key=lambda item: (item.get("priority", 50), item.get("updated_at", "")),
    )
    return {"goals": goals, "count": len(goals)}


@app.post("/goals/upsert")
def upsert_goal(request: GoalRecord) -> dict[str, Any]:
    now = utc_now()
    incoming = request.dict()
    stored_goal: dict[str, Any] | None = None
    with STORAGE_LOCK:
        payload = load_json(GOALS_PATH, {"goals": []})
        goals = payload.get("goals", [])
        for goal in goals:
            if goal.get("goal_id") == request.goal_id:
                created_at = goal.get("created_at", now)
                goal.update(incoming)
                goal["created_at"] = created_at
                goal["updated_at"] = now
                stored_goal = clone_json(goal)
                break
        if stored_goal is None:
            stored_goal = incoming
            stored_goal["created_at"] = now
            stored_goal["updated_at"] = now
            goals.append(stored_goal)
            stored_goal = clone_json(stored_goal)
        atomic_write_json(GOALS_PATH, payload)
    return {"status": "ok", "goal": stored_goal}


@app.post("/continuity/resume")
def continuity_resume(request: ContinuityResumeRequest) -> dict[str, Any]:
    now = utc_now()
    with STORAGE_LOCK:
        payload = load_json(CONTINUITY_PATH, {"sessions": {}})
        sessions = payload.setdefault("sessions", {})
        session = sessions.get(request.session_id, {})
        status = "resumed" if session else "initialized"
        session.update(
            {
                "session_id": request.session_id,
                "goal_id": request.goal_id or session.get("goal_id"),
                "scene": request.scene or session.get("scene", "default"),
                "summary": request.summary or session.get("summary", ""),
                "checkpoint": request.checkpoint or session.get("checkpoint", {}),
                "metadata": {**session.get("metadata", {}), **request.metadata},
                "retry_count": session.get("retry_count", 0),
                "created_at": session.get("created_at", now),
                "last_resume_at": now,
            }
        )
        sessions[request.session_id] = session
        atomic_write_json(CONTINUITY_PATH, payload)
    return {"status": status, "session": session}


@app.post("/continuity/retry")
def continuity_retry(request: ContinuityRetryRequest) -> dict[str, Any]:
    now = utc_now()
    with STORAGE_LOCK:
        payload = load_json(CONTINUITY_PATH, {"sessions": {}})
        sessions = payload.setdefault("sessions", {})
        session = sessions.get(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="session not found")
        retries = session.setdefault("retries", [])
        retry_count = int(session.get("retry_count", 0)) + 1
        retry_event = {
            "attempt": retry_count,
            "reason": request.reason,
            "retry_from": request.retry_from,
            "metadata": request.metadata,
            "recorded_at": now,
        }
        retries.append(retry_event)
        session["retry_count"] = retry_count
        session["last_retry_at"] = now
        atomic_write_json(CONTINUITY_PATH, payload)
    return {"status": "retry_recorded", "session": session, "retry_event": retry_event}


@app.post("/reflections/record")
def reflections_record(request: ReflectionRecord) -> dict[str, Any]:
    record = request.dict()
    record["recorded_at"] = utc_now()
    with STORAGE_LOCK:
        payload = load_json(REFLECTIONS_PATH, {"reflections": []})
        payload.setdefault("reflections", []).append(record)
        atomic_write_json(REFLECTIONS_PATH, payload)
    return {"status": "recorded", "reflection": record}


@app.get("/playbooks/list")
def playbooks_list() -> dict[str, Any]:
    with STORAGE_LOCK:
        payload = load_json(PLAYBOOKS_PATH, {"catalog": clone_json(DEFAULT_PLAYBOOKS), "applications": []})
    catalog = payload.get("catalog", [])
    return {"playbooks": catalog, "count": len(catalog)}


@app.post("/playbooks/apply")
def playbooks_apply(request: PlaybookApplyRequest) -> dict[str, Any]:
    now = utc_now()
    with STORAGE_LOCK:
        payload = load_json(PLAYBOOKS_PATH, {"catalog": clone_json(DEFAULT_PLAYBOOKS), "applications": []})
        playbook = find_playbook(payload, request.playbook_id)
        if not playbook:
            raise HTTPException(status_code=404, detail="playbook not found")
        applications = payload.setdefault("applications", [])
        application = {
            "application_id": f"{request.playbook_id}-{len(applications) + 1}",
            "playbook_id": request.playbook_id,
            "goal_id": request.goal_id,
            "context": request.context,
            "applied_at": now,
        }
        applications.append(application)
        atomic_write_json(PLAYBOOKS_PATH, payload)
    return {
        "status": "applied",
        "playbook": playbook,
        "application": application,
        "resolved_steps": playbook.get("steps", []),
    }


install_runtime_guard(app, 'luminax-brain', require_auth=True)
