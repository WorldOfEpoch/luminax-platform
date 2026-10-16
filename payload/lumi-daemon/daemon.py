from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import mysql.connector
import psutil
import redis

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
PRESENCE_MODEL = os.getenv("OLLAMA_CHAT_MODEL_PRESENCE", "qwen2.5:14b")
INTERVAL = int(os.getenv("LUMI_DAEMON_INTERVAL_SEC", "45"))
AUTOPILOT_ENABLED_ENV = os.getenv("AUTOPILOT_ENABLED", "true").lower() == "true"
AUTOPILOT_ALLOW_ACTIONS_ENV = os.getenv("AUTOPILOT_ALLOW_SERVICE_ACTIONS", "true").lower() == "true"
AUTOPILOT_UI_FAILURE_THRESHOLD_ENV = int(os.getenv("AUTOPILOT_UI_FAILURE_THRESHOLD", "3"))
AUTOPILOT_UI_RESTART_COOLDOWN_SEC_ENV = int(os.getenv("AUTOPILOT_UI_RESTART_COOLDOWN_SEC", "180"))
SELF_UPGRADE_ENABLED = feature_enabled("SELF_UPGRADE_ENABLED")
SELF_UPGRADE_WATCH_INTERVAL_SEC = int(os.getenv("SELF_UPGRADE_WATCH_INTERVAL_SEC", "12"))
ERROR_THRESHOLD_LINES = int(os.getenv("ERROR_THRESHOLD_LINES", "80"))
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
MYSQL_HOST = os.getenv("MYSQL_HOST", "mysql")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "luminax")
MYSQL_USER = os.getenv("MYSQL_USER", "")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
CONTROL_TOKEN = os.getenv("CONTROL_TOKEN", "")
ADMIN_API_URL = os.getenv("ADMIN_API_URL", "http://admin-api:8040").rstrip("/")
UPGRADE_API_URL = os.getenv("UPGRADE_API_URL", "http://lumi-upgrade-agent:8130").rstrip("/")
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspace/luminax"))
WATCH_REPAIR_COOLDOWN_SEC = int(os.getenv("WATCH_REPAIR_COOLDOWN_SEC", "420"))
redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
LOG_FILE = os.getenv("LOG_ROOT", "/data/logs").rstrip("/") + "/guardian_runtime.log"
AUTOPILOT_CFG_PATH = "/workspace/luminax/config/autopilot-config.json"
MAINTENANCE_MODE_PATH = "/workspace/luminax/config/maintenance-mode.json"
RUNTIME_POLICY_PATH = "/workspace/luminax/config/runtime-policy.json"
CORE_API_URL = os.getenv("CORE_API_URL", "http://assistant-core:8000/api")
CONTROL_PLANE_UI_URL = os.getenv("CONTROL_PLANE_UI_URL", "http://luminax-ui/")
CONTROL_PLANE_CORE_URL = os.getenv("CONTROL_PLANE_CORE_URL", f"{CORE_API_URL}/ping")
CONTROL_PLANE_TIMEOUT_SEC = float(os.getenv("CONTROL_PLANE_TIMEOUT_SEC", "4"))
HIVE_DIRECTOR_ENABLED = feature_enabled("HIVE_DIRECTOR_ENABLED")
HIVE_DIRECTOR_AUTO_CREATE_PROJECT = feature_enabled("HIVE_DIRECTOR_AUTO_CREATE_PROJECT")
FEATURE_CONTRACT_CHECK_INTERVAL_SEC = int(os.getenv("FEATURE_CONTRACT_CHECK_INTERVAL_SEC", "300"))
AGENT_SUPERVISION_INTERVAL_SEC = int(os.getenv("AGENT_SUPERVISION_INTERVAL_SEC", "240"))
AGENT_SUPERVISION_STALE_SEC = int(os.getenv("AGENT_SUPERVISION_STALE_SEC", "900"))
ZEUS_HEAVY_SWEEP_INTERVAL_SEC = int(os.getenv("ZEUS_HEAVY_SWEEP_SEC", "7200"))
LIFE_OPS_INTERNAL_REVIEW_INTERVAL_SEC = int(os.getenv("LIFE_OPS_INTERNAL_REVIEW_SEC", "3600"))
LIFE_OPS_PROJECT_KEYS = [
    "life-business-wealth",
    "life-food-health-family",
    "life-motivation-fitness",
]
UPGRADE_PATTERNS = [
    re.compile(r"traceback", re.I),
    re.compile(r"\bexception\b", re.I),
    re.compile(r"\berror\b", re.I),
    re.compile(r"status[= :]+5\d\d", re.I),
    re.compile(r"restarting", re.I),
    re.compile(r"crash(loop)?", re.I),
]
UPGRADE_NOISE_PATTERNS = [
    re.compile(r"self-upgrade watcher trigger", re.I),
    re.compile(r"self-upgrade proposal", re.I),
    re.compile(r"self-upgrade analyze failed", re.I),
    re.compile(r"feature contract health check failed", re.I),
]
LOG_GLOBS = [
    "/workspace/luminax/logs/new/*.log",
    "/workspace/luminax/logs/new/guardian_runtime.log",
    "/workspace/luminax/logs/guardian_runtime.log",
    "/data/logs/new/*.log",
]
ui_fail_count = 0
last_ui_restart_ts = 0
restart_history = []
autopilot_disabled_logged = False
last_contract_check_ts = 0
last_agent_supervision_ts = 0
last_zeus_project_sweep_ts = 0
last_life_ops_review_ts = 0
last_brain_tick_ts = 0
last_memory_maintenance_day = ""
brain_session_state: dict = {}


def db_conn():
    return mysql.connector.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE)


def sh(cmd: str):
    require_feature("LUMINAX_HOST_EXECUTION_ENABLED")
    proc = subprocess.run(["/bin/sh", "-lc", cmd], capture_output=True, text=True)
    return {"returncode": proc.returncode, "stdout": proc.stdout[-12000:], "stderr": proc.stderr[-12000:]}

def trim_text(value: str, max_chars: int = 900) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    keep = max(0, max_chars - 32)
    return text[:keep].rstrip() + "\n...[truncated]"


def write_state(key: str, value: str, meta: dict | None = None):
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO daemon_state (state_key, state_value, meta_json) VALUES (%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE state_value=VALUES(state_value), meta_json=VALUES(meta_json), created_at=CURRENT_TIMESTAMP",
            (key, value, json.dumps(meta or {})),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def cognition_log(source: str, summary: str, content: str, meta: dict | None = None):
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cognition_stream (source, source_kind, thought_type, summary, content, meta_json) VALUES (%s,%s,%s,%s,%s,%s)",
            (source, "daemon", "presence", summary[:255], content, json.dumps(meta or {})),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def activity_log(title: str, details: str, meta: dict | None = None):
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO agent_activity (agent_key, activity_type, status, title, details, meta_json) VALUES (%s,%s,%s,%s,%s,%s)",
            ("lumi-daemon", "presence_loop", "ok", title[:255], details, json.dumps(meta or {})),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def guardian_log(msg: str):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def format_exc(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return text
    return f"{exc.__class__.__name__}: {repr(exc)}"

def unix_ts_from_value(value) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(text)
    except Exception:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except Exception:
        return 0

def ensure_table_column(cur, table_name: str, column_name: str, ddl: str):
    cur.execute(
        "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s LIMIT 1",
        (MYSQL_DATABASE, table_name, column_name),
    )
    if cur.fetchone():
        return
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")

async def admin_get_json(path: str, params: dict | None = None, timeout: float = 20.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(f"{ADMIN_API_URL}{path}", params=params or {})
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

async def admin_post_json(path: str, payload: dict, timeout: float = 40.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{ADMIN_API_URL}{path}", json=payload)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

async def core_get_json(path: str, params: dict | None = None, timeout: float = 20.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(f"{CORE_API_URL}{path}", params=params or {})
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

async def core_post_json(path: str, payload: dict, timeout: float = 30.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{CORE_API_URL}{path}", json=payload)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

def watch_repair_allowed(service_name: str) -> bool:
    key = f"luminax:watch-repair:{service_name}"
    try:
        return not bool(redis_client.get(key))
    except Exception:
        return True

def mark_watch_repair_attempt(service_name: str, detail: str = ""):
    key = f"luminax:watch-repair:{service_name}"
    try:
        redis_client.set(key, detail[:500], ex=max(120, WATCH_REPAIR_COOLDOWN_SEC))
    except Exception:
        pass


def load_autopilot_config() -> dict:
    cfg = {
        "enabled": AUTOPILOT_ENABLED_ENV,
        "allow_service_actions": AUTOPILOT_ALLOW_ACTIONS_ENV,
        "ui_failure_threshold": AUTOPILOT_UI_FAILURE_THRESHOLD_ENV,
        "ui_restart_cooldown_sec": AUTOPILOT_UI_RESTART_COOLDOWN_SEC_ENV,
        "talk_to_user": True,
    }
    try:
        if os.path.exists(AUTOPILOT_CFG_PATH):
            with open(AUTOPILOT_CFG_PATH, "r", encoding="utf-8") as handle:
                disk = json.load(handle)
                if isinstance(disk, dict):
                    cfg.update(disk)
    except Exception:
        pass
    return cfg

def _maintenance_mode_key(value: str) -> str:
    mode = str(value or "normal").strip().lower()
    if mode not in {"normal", "diagnose", "repair_safe", "freeze"}:
        return "normal"
    return mode

def _maintenance_policy(mode: str) -> dict:
    key = _maintenance_mode_key(mode)
    policies = {
        "normal": {"autopilot_actions_allowed": True, "self_apply_allowed": True, "service_actions_allowed": True},
        "diagnose": {"autopilot_actions_allowed": False, "self_apply_allowed": False, "service_actions_allowed": False},
        "repair_safe": {"autopilot_actions_allowed": False, "self_apply_allowed": True, "service_actions_allowed": True},
        "freeze": {"autopilot_actions_allowed": False, "self_apply_allowed": False, "service_actions_allowed": False},
    }
    return {"mode": key, **policies[key]}

def load_maintenance_mode() -> dict:
    now_ts = int(time.time())
    payload = {
        "mode": "normal",
        "reason": "",
        "note": "",
        "random_health_checks": True,
        "set_by": "system",
        "started_at": now_ts,
        "updated_at": now_ts,
    }
    try:
        if os.path.exists(MAINTENANCE_MODE_PATH):
            with open(MAINTENANCE_MODE_PATH, "r", encoding="utf-8") as handle:
                disk = json.load(handle)
                if isinstance(disk, dict):
                    payload.update(disk)
    except Exception:
        pass
    payload["mode"] = _maintenance_mode_key(payload.get("mode"))
    payload["policy"] = _maintenance_policy(payload["mode"])
    payload["random_health_checks"] = bool(payload.get("random_health_checks", True))
    return payload

def load_runtime_policy() -> dict:
    payload = {
        "workers_paused": False,
        "worker_pause_reason": "",
        "orchestration": {
            "zeus_heavy_sweep_sec": ZEUS_HEAVY_SWEEP_INTERVAL_SEC,
            "life_ops_internal_review_sec": LIFE_OPS_INTERNAL_REVIEW_INTERVAL_SEC,
            "life_ops_user_outreach_mode": "waking_hours_only",
            "life_ops_waking_hours": {
                "timezone": "America/New_York",
                "start_hour": 9,
                "end_hour": 21,
            },
            "brain": {
                "enabled": True,
                "provider": "ollama",
                "model": PRESENCE_MODEL,
                "interval_sec": 180,
                "persist_memory": True,
                "persist_state": True,
                "hermes_local_enabled": True,
            },
            "memory_maintenance": {
                "enabled": True,
                "timezone": "America/New_York",
                "run_hour_local": 4,
                "run_minute_local": 15,
                "compact_service_threads": True,
                "bootstrap_service_threads": True,
                "rebuild_workspace_atlas": True,
                "save_memory_note": True,
                "reset_brain_session": True,
            },
        },
    }
    try:
        if os.path.exists(RUNTIME_POLICY_PATH):
            with open(RUNTIME_POLICY_PATH, "r", encoding="utf-8-sig") as handle:
                disk = json.load(handle)
                if isinstance(disk, dict):
                    payload.update(disk)
    except Exception:
        pass
    payload["workers_paused"] = bool(payload.get("workers_paused", False))
    payload["worker_pause_reason"] = str(payload.get("worker_pause_reason", "")).strip()[:240]
    orchestration = payload.get("orchestration") if isinstance(payload.get("orchestration"), dict) else {}
    defaults = {
        "zeus_heavy_sweep_sec": ZEUS_HEAVY_SWEEP_INTERVAL_SEC,
        "life_ops_internal_review_sec": LIFE_OPS_INTERNAL_REVIEW_INTERVAL_SEC,
        "life_ops_user_outreach_mode": "waking_hours_only",
        "life_ops_waking_hours": {
            "timezone": "America/New_York",
            "start_hour": 9,
            "end_hour": 21,
        },
        "brain": {
            "enabled": True,
            "provider": "ollama",
            "model": PRESENCE_MODEL,
            "interval_sec": 180,
            "persist_memory": True,
            "persist_state": True,
            "hermes_local_enabled": True,
        },
        "memory_maintenance": {
            "enabled": True,
            "timezone": "America/New_York",
            "run_hour_local": 4,
            "run_minute_local": 15,
            "compact_service_threads": True,
            "bootstrap_service_threads": True,
            "rebuild_workspace_atlas": True,
            "save_memory_note": True,
            "reset_brain_session": True,
        },
    }
    merged = {**defaults, **orchestration}
    waking = merged.get("life_ops_waking_hours") if isinstance(merged.get("life_ops_waking_hours"), dict) else {}
    merged["life_ops_waking_hours"] = {
        "timezone": str(waking.get("timezone") or "America/New_York").strip() or "America/New_York",
        "start_hour": max(0, min(int(waking.get("start_hour") or 9), 23)),
        "end_hour": max(0, min(int(waking.get("end_hour") or 21), 23)),
    }
    try:
        merged["zeus_heavy_sweep_sec"] = max(3600, min(int(merged.get("zeus_heavy_sweep_sec") or ZEUS_HEAVY_SWEEP_INTERVAL_SEC), 86400))
    except Exception:
        merged["zeus_heavy_sweep_sec"] = ZEUS_HEAVY_SWEEP_INTERVAL_SEC
    try:
        merged["life_ops_internal_review_sec"] = max(900, min(int(merged.get("life_ops_internal_review_sec") or LIFE_OPS_INTERNAL_REVIEW_INTERVAL_SEC), 86400))
    except Exception:
        merged["life_ops_internal_review_sec"] = LIFE_OPS_INTERNAL_REVIEW_INTERVAL_SEC
    brain = merged.get("brain") if isinstance(merged.get("brain"), dict) else {}
    brain_defaults = defaults["brain"]
    brain = {**brain_defaults, **brain}
    brain["enabled"] = bool(brain.get("enabled", True))
    provider = str(brain.get("provider") or "ollama").strip().lower()
    brain["provider"] = provider if provider in {"ollama", "openai", "custom"} else "ollama"
    brain["model"] = str(brain.get("model") or PRESENCE_MODEL).strip() or PRESENCE_MODEL
    try:
        brain["interval_sec"] = max(30, min(int(brain.get("interval_sec") or 180), 3600))
    except Exception:
        brain["interval_sec"] = 180
    brain["persist_memory"] = bool(brain.get("persist_memory", True))
    brain["persist_state"] = bool(brain.get("persist_state", True))
    brain["hermes_local_enabled"] = bool(brain.get("hermes_local_enabled", True))
    merged["brain"] = brain
    memory_maintenance = merged.get("memory_maintenance") if isinstance(merged.get("memory_maintenance"), dict) else {}
    maintenance_defaults = defaults["memory_maintenance"]
    memory_maintenance = {**maintenance_defaults, **memory_maintenance}
    memory_maintenance["enabled"] = bool(memory_maintenance.get("enabled", True))
    memory_maintenance["timezone"] = str(memory_maintenance.get("timezone") or "America/New_York").strip() or "America/New_York"
    try:
        memory_maintenance["run_hour_local"] = max(0, min(int(memory_maintenance.get("run_hour_local") or 4), 23))
    except Exception:
        memory_maintenance["run_hour_local"] = 4
    try:
        memory_maintenance["run_minute_local"] = max(0, min(int(memory_maintenance.get("run_minute_local") or 15), 59))
    except Exception:
        memory_maintenance["run_minute_local"] = 15
    for flag in (
        "compact_service_threads",
        "bootstrap_service_threads",
        "rebuild_workspace_atlas",
        "save_memory_note",
        "reset_brain_session",
    ):
        memory_maintenance[flag] = bool(memory_maintenance.get(flag, maintenance_defaults.get(flag, True)))
    merged["memory_maintenance"] = memory_maintenance
    payload["orchestration"] = merged
    return payload

def _runtime_brain_policy(runtime_policy: dict) -> dict:
    orchestration = runtime_policy.get("orchestration") if isinstance(runtime_policy.get("orchestration"), dict) else {}
    brain = orchestration.get("brain") if isinstance(orchestration.get("brain"), dict) else {}
    return {
        "enabled": bool(brain.get("enabled", True)),
        "provider": str(brain.get("provider") or "ollama").strip().lower() or "ollama",
        "model": str(brain.get("model") or PRESENCE_MODEL).strip() or PRESENCE_MODEL,
        "interval_sec": max(30, min(int(brain.get("interval_sec") or 180), 3600)),
        "persist_memory": bool(brain.get("persist_memory", True)),
        "persist_state": bool(brain.get("persist_state", True)),
        "hermes_local_enabled": bool(brain.get("hermes_local_enabled", True)),
    }

def _runtime_memory_maintenance_policy(runtime_policy: dict) -> dict:
    orchestration = runtime_policy.get("orchestration") if isinstance(runtime_policy.get("orchestration"), dict) else {}
    policy = orchestration.get("memory_maintenance") if isinstance(orchestration.get("memory_maintenance"), dict) else {}
    return {
        "enabled": bool(policy.get("enabled", True)),
        "timezone": str(policy.get("timezone") or "America/New_York").strip() or "America/New_York",
        "run_hour_local": max(0, min(int(policy.get("run_hour_local") or 4), 23)),
        "run_minute_local": max(0, min(int(policy.get("run_minute_local") or 15), 59)),
        "compact_service_threads": bool(policy.get("compact_service_threads", True)),
        "bootstrap_service_threads": bool(policy.get("bootstrap_service_threads", True)),
        "rebuild_workspace_atlas": bool(policy.get("rebuild_workspace_atlas", True)),
        "save_memory_note": bool(policy.get("save_memory_note", True)),
        "reset_brain_session": bool(policy.get("reset_brain_session", True)),
    }

def _timezone_now(timezone_name: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(timezone_name))
    except Exception:
        return datetime.now(timezone.utc)

def _brain_session_payload(reason: str = "", note: str = "") -> dict:
    now_ts = int(time.time())
    return {
        "session_id": uuid.uuid4().hex[:12],
        "started_at": now_ts,
        "updated_at": now_ts,
        "reason": str(reason or "startup").strip()[:120] or "startup",
        "last_note": str(note or "").strip(),
        "note_count": 1 if str(note or "").strip() else 0,
        "last_memory_maintenance_at": 0,
    }

def ensure_brain_session(reason: str = "startup", note: str = "") -> dict:
    global brain_session_state
    if isinstance(brain_session_state, dict) and brain_session_state.get("session_id"):
        return brain_session_state
    brain_session_state = _brain_session_payload(reason=reason, note=note)
    write_state("brain.session.current", json.dumps(brain_session_state), {"ts": int(time.time()), "reason": reason})
    return brain_session_state

def ensure_system_incidents_table():
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS system_incidents (
                id INT AUTO_INCREMENT PRIMARY KEY,
                incident_key VARCHAR(191) NOT NULL UNIQUE,
                source_service VARCHAR(120) NOT NULL,
                feature_ids_json LONGTEXT NULL,
                fingerprint VARCHAR(191) NOT NULL,
                severity VARCHAR(32) NOT NULL DEFAULT 'medium',
                status VARCHAR(32) NOT NULL DEFAULT 'open',
                summary VARCHAR(255) NOT NULL,
                detail_text MEDIUMTEXT NULL,
                evidence_json LONGTEXT NULL,
                suspected_owner VARCHAR(120) NULL,
                linked_run_id VARCHAR(120) NULL,
                linked_proposal_id VARCHAR(120) NULL,
                occurrence_count INT NOT NULL DEFAULT 1,
                resolution_note TEXT NULL,
                acknowledged_by VARCHAR(120) NULL,
                acknowledged_at TIMESTAMP NULL DEFAULT NULL,
                escalation_level INT NOT NULL DEFAULT 0,
                last_notified_at TIMESTAMP NULL DEFAULT NULL,
                next_notification_at TIMESTAMP NULL DEFAULT NULL,
                notification_state_json LONGTEXT NULL,
                first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP NULL DEFAULT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                KEY idx_system_incidents_status (status, last_seen_at),
                KEY idx_system_incidents_source (source_service)
            )
            """
        )
        ensure_table_column(cur, "system_incidents", "acknowledged_by", "VARCHAR(120) NULL")
        ensure_table_column(cur, "system_incidents", "acknowledged_at", "TIMESTAMP NULL DEFAULT NULL")
        ensure_table_column(cur, "system_incidents", "escalation_level", "INT NOT NULL DEFAULT 0")
        ensure_table_column(cur, "system_incidents", "last_notified_at", "TIMESTAMP NULL DEFAULT NULL")
        ensure_table_column(cur, "system_incidents", "next_notification_at", "TIMESTAMP NULL DEFAULT NULL")
        ensure_table_column(cur, "system_incidents", "notification_state_json", "LONGTEXT NULL")
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass

def upsert_system_incident(
    incident_key: str,
    *,
    source_service: str,
    severity: str,
    summary: str,
    detail: str = "",
    feature_ids: list[str] | None = None,
    evidence: dict | None = None,
    linked_proposal_id: str = "",
):
    clean_key = str(incident_key or "").strip()[:191]
    if not clean_key:
        return
    ensure_system_incidents_table()
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO system_incidents (
                incident_key, source_service, feature_ids_json, fingerprint, severity, status,
                summary, detail_text, evidence_json, linked_proposal_id, occurrence_count, resolution_note,
                first_seen_at, last_seen_at, resolved_at
            )
            VALUES (%s,%s,%s,%s,%s,'open',%s,%s,%s,%s,1,NULL,NOW(),NOW(),NULL)
            ON DUPLICATE KEY UPDATE
                source_service=VALUES(source_service),
                feature_ids_json=VALUES(feature_ids_json),
                fingerprint=VALUES(fingerprint),
                severity=VALUES(severity),
                status='open',
                summary=VALUES(summary),
                detail_text=VALUES(detail_text),
                evidence_json=VALUES(evidence_json),
                linked_proposal_id=VALUES(linked_proposal_id),
                occurrence_count=occurrence_count+1,
                resolution_note=NULL,
                acknowledged_by=NULL,
                acknowledged_at=NULL,
                escalation_level=0,
                last_notified_at=NULL,
                next_notification_at=NULL,
                notification_state_json=NULL,
                last_seen_at=NOW(),
                resolved_at=NULL
            """,
            (
                clean_key,
                str(source_service or "lumi-daemon").strip()[:120] or "lumi-daemon",
                json.dumps(feature_ids or [], ensure_ascii=False),
                clean_key,
                str(severity or "medium").strip().lower() or "medium",
                str(summary or "System incident").strip()[:255],
                str(detail or "").strip()[:16000],
                json.dumps(evidence or {}, ensure_ascii=False),
                str(linked_proposal_id or "").strip()[:120],
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass

def resolve_system_incident(incident_key: str, resolution_note: str = ""):
    clean_key = str(incident_key or "").strip()[:191]
    if not clean_key:
        return
    ensure_system_incidents_table()
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE system_incidents SET status='resolved', resolution_note=%s, resolved_at=NOW(), last_seen_at=NOW(), next_notification_at=NULL WHERE incident_key=%s",
            (str(resolution_note or "").strip()[:2400], clean_key),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass

def incident_feature_ids_for_service(service: str) -> list[str]:
    mapping = {
        "assistant-core": ["codex_bridge", "file_checkout", "ops_control_plane"],
        "docker": ["ops_control_plane"],
        "admin-api": ["ops_control_plane"],
        "luminax-ui": ["ops_control_plane"],
        "mysql": ["ops_control_plane"],
        "redis": ["ops_control_plane"],
        "qdrant": ["ops_control_plane"],
        "underfoot-world": ["games", "ops_control_plane"],
        "discord-bot": ["ops_control_plane"],
        "voice-agent": ["media_music_studio"],
        "broadcast-conductor": ["broadcast_studio"],
        "site-manager": ["sites_publish"],
        "lumi-hive-dreamer": ["hive_agents"],
        "lumi-hive-engineer": ["hive_agents"],
        "lumi-hive-critic": ["hive_agents"],
    }
    return mapping.get(str(service or "").strip(), [])


def restart_ui_service() -> dict:
    return sh("cd /workspace/luminax && (docker compose restart luminax-ui || docker restart luminax-ui || true)")


async def check_control_plane() -> tuple[bool, str, bool]:
    try:
        async with httpx.AsyncClient(timeout=CONTROL_PLANE_TIMEOUT_SEC) as client:
            ui_resp = await client.get(CONTROL_PLANE_UI_URL)
            core_resp = await client.get(CONTROL_PLANE_CORE_URL)
            ui_ok = ui_resp.status_code < 500
            core_ok = core_resp.status_code < 500
            if ui_ok and core_ok:
                return True, "ui+core healthy", True
            detail = f"ui={ui_resp.status_code} core={core_resp.status_code}"
            return False, detail, not ui_ok
    except Exception as exc:
        detail = format_exc(exc)
        restart_safe = "luminax-ui" in detail.lower() or "name or service not known" in detail.lower()
        return False, detail, restart_safe

async def run_service_watch_tick(maintenance: dict, cfg: dict):
    try:
        watch = await admin_get_json("/services/watch/status", params={"token": CONTROL_TOKEN}, timeout=25.0)
    except Exception as exc:
        detail = format_exc(exc)
        guardian_log(f"service watch status fetch failed: {detail}")
        upsert_system_incident(
            "service-watch-status-unreachable",
            source_service="admin-api",
            severity="high",
            summary="Service watch status endpoint unreachable",
            detail=detail,
            feature_ids=["ops_control_plane"],
            evidence={"admin_api_url": ADMIN_API_URL},
        )
        return

    resolve_system_incident("service-watch-status-unreachable", "Service watch status reachable again.")
    rows = watch.get("rows") if isinstance(watch.get("rows"), list) else []
    windows_nodes = watch.get("windows_nodes") if isinstance(watch.get("windows_nodes"), dict) else {}
    allow_actions = bool(cfg.get("allow_service_actions", False)) and bool((maintenance.get("policy") or {}).get("autopilot_actions_allowed", False))

    for row in rows:
        if not isinstance(row, dict):
            continue
        service_name = str(row.get("name") or "").strip()
        if not service_name:
            continue
        status = str(row.get("status") or "").strip().lower()
        detail = str(row.get("detail") or "").strip()
        incident_key = f"service-watch:{service_name}"
        if status in {"failed", "degraded"}:
            upsert_system_incident(
                incident_key,
                source_service=service_name,
                severity=str(row.get("severity") or "medium").strip().lower() or "medium",
                summary=f"Service watch detected {status} state for {service_name}",
                detail=detail,
                feature_ids=incident_feature_ids_for_service(service_name),
                evidence={
                    "watch_row": row,
                    "watch_status": watch.get("status"),
                },
            )
        else:
            resolve_system_incident(incident_key, f"{service_name} recovered to {status}.")

    for node in windows_nodes.get("rows", []) if isinstance(windows_nodes.get("rows"), list) else []:
        if not isinstance(node, dict):
            continue
        node_key = str(node.get("node_key") or "").strip()
        if not node_key:
            continue
        node_status = str(node.get("evaluated_status") or "").strip().lower()
        issues = ", ".join([str(item).strip() for item in (node.get("issues") or []) if str(item).strip()])
        stale_key = f"windows-node-stale:{node_key}"
        degraded_key = f"windows-node-health:{node_key}"
        if node_status == "stale":
            upsert_system_incident(
                stale_key,
                source_service="windows-node-monitor",
                severity="high",
                summary=f"Windows node stale: {node_key}",
                detail=f"Last check-in age={node.get('age_sec')} sec hostname={node.get('hostname')}",
                feature_ids=["ops_control_plane"],
                evidence=node,
            )
        else:
            resolve_system_incident(stale_key, "Windows node check-in recovered.")
        if node_status == "degraded":
            upsert_system_incident(
                degraded_key,
                source_service="windows-node-monitor",
                severity="medium",
                summary=f"Windows node degraded: {node_key}",
                detail=issues or f"Node reported degraded status age={node.get('age_sec')} sec",
                feature_ids=["ops_control_plane"],
                evidence=node,
            )
        else:
            resolve_system_incident(degraded_key, "Windows node healthy again.")

    if not allow_actions:
        return

    def severity_rank(value: str) -> int:
        mapping = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        return mapping.get(str(value or "medium").strip().lower(), 4)

    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip().lower()
        startup = str(row.get("startup") or "core").strip().lower()
        if status not in {"failed", "degraded"}:
            continue
        if startup != "core" and status != "failed":
            continue
        candidates.append(row)
    candidates.sort(key=lambda item: (severity_rank(item.get("severity", "medium")), 0 if item.get("name") == "mysql" else 1, str(item.get("name") or "")))

    for row in candidates:
        service_name = str(row.get("name") or "").strip()
        detail = str(row.get("detail") or "").strip()
        lower_detail = detail.lower()
        if not service_name or not watch_repair_allowed(service_name):
            continue
        if any(marker in lower_detail for marker in ["access denied", "unknown database", "doesn't exist", "authentication", "schema"]):
            guardian_log(f"service watch withheld auto-repair for {service_name}: possible config/schema drift")
            if not seen_digest_recently(hashlib.sha1(f"watch:{service_name}:{detail}".encode('utf-8', errors='ignore')).hexdigest()):
                await trigger_upgrade_analysis(service_name, f"service-watch://{service_name}", detail, trigger_kind="service_watch", apply_if_safe_override=False)
            mark_watch_repair_attempt(service_name, f"withheld:{detail[:200]}")
            break
        try:
            guardian_log(f"service watch auto-repair: service={service_name} status={row.get('status')} detail={detail}")
            result = await admin_post_json(
                "/services/watch/repair",
                {
                    "token": CONTROL_TOKEN,
                    "name": service_name,
                    "reason": f"autopilot repair after {row.get('status')} state",
                    "requested_by": "lumi-daemon",
                },
                timeout=90.0,
            )
            mark_watch_repair_attempt(service_name, str(result.get("status") or "queued"))
            guardian_log(f"service watch repair result: service={service_name} status={result.get('status')} repair_id={result.get('repair_id')}")
        except Exception as exc:
            detail = format_exc(exc)
            guardian_log(f"service watch repair failed: service={service_name} detail={detail}")
            mark_watch_repair_attempt(service_name, f"failed:{detail[:200]}")
            upsert_system_incident(
                f"service-watch-repair:{service_name}",
                source_service=service_name,
                severity="high",
                summary=f"Auto-repair failed for {service_name}",
                detail=detail,
                feature_ids=incident_feature_ids_for_service(service_name),
                evidence={"service": service_name},
            )
        break


async def presence_note(telemetry: dict, services: str, *, model: str = "", prior_note: str = "", session_meta: dict | None = None):
    prompt = (
        "You are the local background brain loop for Mount Olympus and Hermes. You are not sentient. "
        "Think like an always-on operations aide running on the PC edge node. "
        "Write a short practical state note under 120 words. Focus on what matters next, local-model readiness, memory hygiene, and whether Hermes can keep operating locally.\n"
        f"Session:\n{json.dumps(session_meta or {}, ensure_ascii=False)}\n"
        f"Previous note:\n{trim_text(prior_note, 700)}\n"
        f"Telemetry:\n{json.dumps(telemetry)}\n"
        f"Services:\n{trim_text(services, 2400)}"
    )
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{OLLAMA_HOST}/api/chat",
            json={"model": model or PRESENCE_MODEL, "messages": [{"role": "user", "content": prompt}], "stream": False},
        )
        response.raise_for_status()
        return trim_text(response.json().get("message", {}).get("content", "No note"), 900)

async def run_continuous_brain_tick(runtime_policy: dict, telemetry: dict, services: str) -> dict:
    global brain_session_state
    brain_cfg = _runtime_brain_policy(runtime_policy)
    session = ensure_brain_session(reason="brain_tick")
    provider = str(brain_cfg.get("provider") or "ollama").strip().lower() or "ollama"
    model = str(brain_cfg.get("model") or PRESENCE_MODEL).strip() or PRESENCE_MODEL
    fallback_used = False
    if provider != "ollama":
        provider = "ollama"
        model = PRESENCE_MODEL
        fallback_used = True
    note = await presence_note(
        telemetry,
        services,
        model=model,
        prior_note=str(session.get("last_note") or "").strip(),
        session_meta={
            "session_id": session.get("session_id"),
            "started_at": session.get("started_at"),
            "note_count": session.get("note_count"),
            "last_memory_maintenance_at": session.get("last_memory_maintenance_at"),
        },
    )
    session["updated_at"] = int(time.time())
    session["last_note"] = note
    session["note_count"] = int(session.get("note_count") or 0) + 1
    session["model"] = model
    session["provider"] = provider
    session["fallback_used"] = fallback_used
    brain_session_state = session
    write_state("brain.session.current", json.dumps(session), {"ts": int(time.time()), "model": model, "provider": provider})
    return {
        "status": "ok",
        "note": note,
        "session": session,
        "model": model,
        "provider": provider,
        "fallback_used": fallback_used,
    }

async def run_daily_memory_maintenance(runtime_policy: dict) -> dict:
    global brain_session_state, last_memory_maintenance_day
    policy = _runtime_memory_maintenance_policy(runtime_policy)
    if not policy.get("enabled", True):
        return {"status": "disabled"}
    if not CONTROL_TOKEN:
        return {"status": "blocked", "detail": "missing_control_token"}
    local_now = _timezone_now(str(policy.get("timezone") or "America/New_York"))
    today_key = local_now.strftime("%Y-%m-%d")
    if last_memory_maintenance_day == today_key:
        return {"status": "already_ran", "day": today_key}
    if (local_now.hour, local_now.minute) < (int(policy.get("run_hour_local") or 4), int(policy.get("run_minute_local") or 15)):
        return {"status": "waiting", "day": today_key}
    payload = {
        "token": CONTROL_TOKEN,
        "reason": "daily_memory_maintenance",
        "force": False,
        "compact_service_threads": bool(policy.get("compact_service_threads", True)),
        "bootstrap_service_threads": bool(policy.get("bootstrap_service_threads", True)),
        "rebuild_workspace_atlas": bool(policy.get("rebuild_workspace_atlas", True)),
        "save_memory_note": bool(policy.get("save_memory_note", True)),
    }
    response = await core_post_json("/memory/maintenance/run", payload, timeout=180.0)
    receipt = response.get("maintenance") if isinstance(response.get("maintenance"), dict) else response
    if policy.get("reset_brain_session", True):
        previous = dict(ensure_brain_session(reason="pre_maintenance_archive"))
        archive_key = f"brain.session.archive.{today_key}"
        write_state(archive_key, json.dumps(previous), {"ts": int(time.time()), "reason": "daily_memory_maintenance"})
        brain_session_state = _brain_session_payload(reason="daily_memory_maintenance_reset")
        brain_session_state["last_memory_maintenance_at"] = int(time.time())
        write_state("brain.session.current", json.dumps(brain_session_state), {"ts": int(time.time()), "reason": "daily_memory_maintenance_reset"})
    elif isinstance(brain_session_state, dict) and brain_session_state.get("session_id"):
        brain_session_state["last_memory_maintenance_at"] = int(time.time())
        write_state("brain.session.current", json.dumps(brain_session_state), {"ts": int(time.time()), "reason": "daily_memory_maintenance"})
    last_memory_maintenance_day = today_key
    write_state("brain.maintenance.latest", json.dumps(receipt), {"ts": int(time.time()), "day": today_key})
    summary = (
        f"Daily memory maintenance completed for {today_key}: "
        f"service_threads={receipt.get('service_thread_count', 0)} atlas={((receipt.get('atlas') or {}).get('status') or 'unknown')}"
    )
    guardian_log(summary)
    cognition_log("lumi-daemon", "Daily memory maintenance", summary, {"day": today_key, "receipt_path": receipt.get("receipt_path")})
    activity_log("Daily memory maintenance", summary, {"day": today_key, "receipt_path": receipt.get("receipt_path")})
    return {"status": "ok", "day": today_key, "receipt": receipt}


async def run_hive_director_tick(note: str, telemetry: dict):
    if not HIVE_DIRECTOR_ENABLED:
        return {"status": "disabled"}
    payload = {
        "goal": note[:1200] or "Run hive director evaluation tick.",
        "auto_create_project": HIVE_DIRECTOR_AUTO_CREATE_PROJECT,
        "create_tasks": True,
        "telemetry": telemetry,
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{CORE_API_URL}/hive/director/tick", json=payload)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {"status": "error", "detail": "invalid director payload"}
    except Exception as exc:
        return {"status": "error", "detail": format_exc(exc)}


def collect_tail_from_file(path: Path, max_lines: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception:
        return ""


def suspicious_excerpt(raw_text: str, max_lines: int) -> str:
    lines = [line for line in (raw_text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    hits = []
    for idx, line in enumerate(lines):
        if any(pattern.search(line) for pattern in UPGRADE_NOISE_PATTERNS):
            continue
        if any(pattern.search(line) for pattern in UPGRADE_PATTERNS):
            start = max(0, idx - 4)
            end = min(len(lines), idx + 8)
            for context_line in lines[start:end]:
                if any(pattern.search(context_line) for pattern in UPGRADE_NOISE_PATTERNS):
                    continue
                hits.append(context_line)
    if not hits:
        return ""
    out = []
    for line in hits:
        if line not in out:
            out.append(line)
    return "\n".join(out[-max_lines:])


def seen_digest_recently(digest: str) -> bool:
    try:
        key = f"luminax:upgrade_seen:{digest}"
        if redis_client.get(key):
            return True
        redis_client.set(key, "1", ex=max(300, SELF_UPGRADE_WATCH_INTERVAL_SEC * 15))
    except Exception:
        pass
    return False


def infer_service_from_source(source: str, excerpt: str) -> str:
    source_lower = (source or "").lower()
    if "/assistant-core/" in source_lower or "assistant-core" in source_lower:
        return "assistant-core"
    if "/admin-api/" in source_lower or "admin-api" in source_lower:
        return "admin-api"
    if "/lumi-daemon/" in source_lower or "guardian_runtime" in source_lower:
        return "lumi-daemon"
    if "/lumi-upgrade-agent/" in source_lower or "self_upgrade_agent" in source_lower:
        return "lumi-upgrade-agent"
    excerpt_lower = (excerpt or "").lower()
    for service in [
        "assistant-core",
        "admin-api",
        "lumi-daemon",
        "lumi-upgrade-agent",
        "voice-agent",
        "discord-bot",
        "realtime-gateway",
        "luminax-ui",
    ]:
        if service in excerpt_lower:
            return service
    return "assistant-core"


async def trigger_upgrade_analysis(service: str, source: str, excerpt: str, trigger_kind: str = "error_watch", apply_if_safe_override: bool | None = None):
    maintenance = load_maintenance_mode()
    policy = maintenance.get("policy") if isinstance(maintenance.get("policy"), dict) else _maintenance_policy(maintenance.get("mode", "normal"))
    fingerprint = hashlib.sha1(f"{service}\n{source}\n{excerpt[:4000]}".encode("utf-8", errors="ignore")).hexdigest()
    severity = "high" if re.search(r"traceback|exception|crash|status[= :]+5\\d\\d", excerpt, re.I) else "medium"
    upsert_system_incident(
        f"daemon-watch:{fingerprint}",
        source_service=service,
        severity=severity,
        summary=f"Daemon watch detected instability in {service}",
        detail=excerpt[:6000],
        feature_ids=incident_feature_ids_for_service(service),
        evidence={"source": source, "trigger_kind": trigger_kind},
    )
    payload = {
        "service": service,
        "error_excerpt": excerpt[:16000],
        "log_path": source,
        "trigger_kind": trigger_kind,
        "context": {"source": source, "detected_at": int(time.time())},
        "apply_if_safe": bool(policy.get("self_apply_allowed", False)) if apply_if_safe_override is None else bool(apply_if_safe_override),
    }
    try:
        async with httpx.AsyncClient(timeout=240) as client:
            response = await client.post(f"{UPGRADE_API_URL}/analyze", json=payload)
            data = response.json()
        proposal = data.get("proposal", {}) if isinstance(data, dict) else {}
        pid = proposal.get("proposal_id", "--")
        risk = proposal.get("risk", "unknown")
        confidence = proposal.get("confidence", 0)
        guardian_log(f"self-upgrade proposal: service={service} proposal_id={pid} risk={risk} confidence={confidence}")
        cognition_log("lumi-daemon", "Self-upgrade proposal generated", proposal.get("summary", "Codex generated a repair proposal."), {"proposal_id": pid, "risk": risk, "service": service})
        activity_log("Self-upgrade proposal generated", proposal.get("root_cause", "")[:1200], {"proposal_id": pid, "service": service})
        upsert_system_incident(
            f"daemon-watch:{fingerprint}",
            source_service=service,
            severity=severity,
            summary=f"Repair proposal queued for {service}",
            detail=proposal.get("summary", excerpt[:3000]),
            feature_ids=incident_feature_ids_for_service(service),
            evidence={"source": source, "proposal_id": pid, "risk": risk, "confidence": confidence},
            linked_proposal_id=str(pid),
        )
        if data.get("auto_apply_result"):
            auto_result = data["auto_apply_result"]
            guardian_log(f"self-upgrade auto-apply result: service={service} proposal_id={pid} status={auto_result.get('status')}")
        write_state("self_upgrade.latest", json.dumps(data), {"service": service, "source": source, "ts": int(time.time())})
    except Exception as exc:
        guardian_log(f"self-upgrade analyze failed: service={service} detail={format_exc(exc)}")

async def run_feature_contract_health_check():
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            response = await client.get(f"{CORE_API_URL}/contracts/verify")
            response.raise_for_status()
            data = response.json()
        write_state("contracts.verify.latest", json.dumps(data.get("summary", {})), {"ts": int(time.time())})
        if str(data.get("status") or "ok").strip().lower() == "ok":
            resolve_system_incident("feature-contract-drift", "Feature contracts verified clean.")
            resolve_system_incident("feature-contract-health-unreachable", "Feature contract verifier reachable again.")
            return
        degraded_ids = [str(row.get("id") or "").strip() for row in data.get("rows", []) if isinstance(row, dict) and str(row.get("status") or "").strip().lower() != "ok"]
        upsert_system_incident(
            "feature-contract-drift",
            source_service="assistant-core",
            severity="high",
            summary="Feature contract verification detected drift",
            detail=json.dumps(data.get("summary", {}), ensure_ascii=False),
            feature_ids=[item for item in degraded_ids if item],
            evidence={"summary": data.get("summary", {}), "rows": data.get("rows", [])[:12]},
        )
    except Exception as exc:
        detail = format_exc(exc)
        guardian_log(f"feature contract health check failed: {detail}")
        upsert_system_incident(
            "feature-contract-health-unreachable",
            source_service="assistant-core",
            severity="medium",
            summary="Feature contract health check failed",
            detail=detail,
            feature_ids=["ops_control_plane"],
            evidence={"core_api_url": CORE_API_URL},
        )


def _supervision_agent_status(value: str) -> str:
    status = str(value or "").strip().lower()
    return status or "unknown"


def _supervision_agent_brief(row: dict, now_ts: int) -> dict:
    last_seen_ts = unix_ts_from_value(
        row.get("last_seen_at")
        or row.get("updated_at")
        or row.get("created_at")
    )
    age_sec = max(0, now_ts - last_seen_ts) if last_seen_ts > 0 else 0
    return {
        "agent_key": str(row.get("agent_key") or row.get("key") or "").strip(),
        "display_name": str(row.get("display_name") or row.get("public_name") or row.get("agent_key") or "").strip(),
        "status": _supervision_agent_status(row.get("status")),
        "full_time": bool(row.get("full_time", True)),
        "role_type": str(row.get("role_type") or row.get("rank") or "").strip(),
        "last_seen_ts": last_seen_ts,
        "age_sec": age_sec,
    }


def _summarize_agent_supervision(rows: list[dict], now_ts: int) -> dict:
    status_counts: dict[str, int] = {}
    idle_ready: list[dict] = []
    stale_full_time: list[dict] = []
    failing: list[dict] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        brief = _supervision_agent_brief(row, now_ts)
        status = brief["status"]
        status_counts[status] = int(status_counts.get(status, 0)) + 1
        if not brief["full_time"]:
            continue
        if status in {"idle", "registered", "ready"}:
            idle_ready.append(brief)
        if status in {"failed", "error", "offline", "unreachable", "crashed"}:
            failing.append(brief)
        if brief["last_seen_ts"] > 0 and brief["age_sec"] >= max(300, AGENT_SUPERVISION_STALE_SEC) and status not in {"profile_only"}:
            stale_full_time.append(brief)

    idle_ready.sort(key=lambda item: (item.get("status") != "idle", item.get("age_sec") or 0, item.get("agent_key") or ""))
    stale_full_time.sort(key=lambda item: (-(item.get("age_sec") or 0), item.get("agent_key") or ""))
    failing.sort(key=lambda item: (item.get("status") or "", item.get("agent_key") or ""))
    return {
        "status_counts": status_counts,
        "idle_ready": idle_ready,
        "stale_full_time": stale_full_time,
        "failing": failing,
    }


async def run_agent_supervision_tick(runtime_policy: dict, maintenance: dict):
    now_ts = int(time.time())
    paused_by_policy = bool(runtime_policy.get("workers_paused", False))
    pause_reason = str(runtime_policy.get("worker_pause_reason") or "").strip()
    try:
        health = await core_get_json("/health/light", timeout=20.0)
        review = await core_post_json(
            "/agents/supervision/review",
            {
                "token": CONTROL_TOKEN,
                "stale_after_sec": AGENT_SUPERVISION_STALE_SEC,
            },
            timeout=25.0,
        )
    except Exception as exc:
        detail = format_exc(exc)
        guardian_log(f"agent supervision fetch failed: {detail}")
        write_state(
            "olympus.worker.supervision.latest",
            json.dumps({"status": "error", "detail": detail, "ts": now_ts}),
            {"ts": now_ts, "source": "assistant-core", "phase": "fetch"},
        )
        upsert_system_incident(
            "assistant-core-agent-supervision-unreachable",
            source_service="assistant-core",
            severity="medium",
            summary="Assistant-core worker supervision fetch failed",
            detail=detail,
            feature_ids=["ops_control_plane"],
            evidence={"core_api_url": CORE_API_URL, "paused_by_policy": paused_by_policy},
        )
        return

    resolve_system_incident(
        "assistant-core-agent-supervision-unreachable",
        "Assistant-core worker supervision endpoints reachable again.",
    )

    worker_pause = health.get("worker_pause") if isinstance(health.get("worker_pause"), dict) else {}
    review_cycle = health.get("review_cycle") if isinstance(health.get("review_cycle"), dict) else {}
    review_rows = review.get("rows") if isinstance(review.get("rows"), list) else []
    review_summary = review.get("summary") if isinstance(review.get("summary"), dict) else {}
    derived = _summarize_agent_supervision(review_rows, now_ts)
    review_status = str(review_cycle.get("status") or "idle").strip().lower() or "idle"
    core_paused = bool(worker_pause.get("paused", False))
    effective_paused = paused_by_policy or core_paused
    supervision_signal = "quiet"
    if effective_paused:
        supervision_signal = "paused"
    elif derived["failing"]:
        supervision_signal = "attention_needed"
    elif review_summary.get("check_queue") or review_summary.get("check_supervisor") or review_summary.get("nudge_assignee"):
        supervision_signal = "review_actions_waiting"
    elif review_status == "idle" and derived["idle_ready"]:
        supervision_signal = "idle_workers_available"
    elif review_status not in {"idle", ""}:
        supervision_signal = f"review_{review_status}"

    snapshot = {
        "status": "ok",
        "ts": now_ts,
        "source": "assistant-core",
        "paused": effective_paused,
        "pause_reason": pause_reason or str(worker_pause.get("reason") or "").strip(),
        "review_cycle": {
            "status": review_status,
            "current_wave": int(review_cycle.get("current_wave") or 0),
            "current_title": str(review_cycle.get("current_title") or "").strip(),
            "stop_requested": bool(review_cycle.get("stop_requested", False)),
            "pause_requested": bool(review_cycle.get("pause_requested", False)),
        },
        "registry_summary": review_summary,
        "status_counts": derived["status_counts"],
        "idle_ready_count": len(derived["idle_ready"]),
        "stale_full_time_count": len(derived["stale_full_time"]),
        "failing_count": len(derived["failing"]),
        "queue_review_counts": {
            "check_queue": int(review_summary.get("check_queue") or 0),
            "check_supervisor": int(review_summary.get("check_supervisor") or 0),
            "nudge_assignee": int(review_summary.get("nudge_assignee") or 0),
            "self_improvement": int(review_summary.get("self_improvement") or 0),
        },
        "idle_ready_workers": derived["idle_ready"][:8],
        "stale_full_time_workers": derived["stale_full_time"][:8],
        "failing_workers": derived["failing"][:8],
        "signal": supervision_signal,
        "maintenance_mode": str(maintenance.get("mode") or "normal").strip(),
    }
    write_state(
        "olympus.worker.supervision.latest",
        json.dumps(snapshot),
        {
            "ts": now_ts,
            "source": "assistant-core",
            "signal": supervision_signal,
            "paused": effective_paused,
            "review_status": review_status,
        },
    )
    guardian_log(
        "agent supervision tick: "
        f"paused={effective_paused} review={review_status} "
        f"idle_ready={len(derived['idle_ready'])} stale={len(derived['stale_full_time'])} "
        f"failing={len(derived['failing'])} agents={review_summary.get('count', len(review_rows))} "
        f"signal={supervision_signal}"
    )
    activity_log(
        "Assistant-core agent supervision",
        f"review={review_status} idle_ready={len(derived['idle_ready'])} stale={len(derived['stale_full_time'])} "
        f"failing={len(derived['failing'])} paused={effective_paused}",
        {
            "signal": supervision_signal,
            "paused": effective_paused,
            "review_status": review_status,
            "status_counts": derived["status_counts"],
        },
    )
    cognition_log(
        "lumi-daemon",
        "Agent supervision tick",
        f"signal={supervision_signal} review={review_status} idle_ready={len(derived['idle_ready'])} "
        f"stale={len(derived['stale_full_time'])} failing={len(derived['failing'])}",
        {
            "signal": supervision_signal,
            "paused": effective_paused,
            "review_cycle": snapshot["review_cycle"],
            "status_counts": derived["status_counts"],
        },
    )

    if effective_paused:
        resolve_system_incident("assistant-core-agent-supervision-stale", "Worker pause active; stale worker supervision held.")
        resolve_system_incident("assistant-core-agent-supervision-failing", "Worker pause active; failure escalation held.")
        return

    if derived["stale_full_time"]:
        upsert_system_incident(
            "assistant-core-agent-supervision-stale",
            source_service="assistant-core",
            severity="medium",
            summary="Olympus full-time workers appear stale",
            detail=trim_text(
                "\n".join(
                    [
                        f"{row.get('agent_key') or row.get('display_name')}: status={row.get('status')} age_sec={row.get('age_sec')}"
                        for row in derived["stale_full_time"][:12]
                    ]
                ),
                2400,
            ),
            feature_ids=["ops_control_plane"],
            evidence={"rows": derived["stale_full_time"][:12], "review_cycle": snapshot["review_cycle"]},
        )
    else:
        resolve_system_incident("assistant-core-agent-supervision-stale", "No stale full-time workers detected.")

    if derived["failing"]:
        upsert_system_incident(
            "assistant-core-agent-supervision-failing",
            source_service="assistant-core",
            severity="high",
            summary="Olympus workers reported failing or offline states",
            detail=trim_text(
                "\n".join(
                    [
                        f"{row.get('agent_key') or row.get('display_name')}: status={row.get('status')} age_sec={row.get('age_sec')}"
                        for row in derived["failing"][:12]
                    ]
                ),
                2400,
            ),
            feature_ids=["ops_control_plane"],
            evidence={"rows": derived["failing"][:12], "review_cycle": snapshot["review_cycle"]},
        )
    else:
        resolve_system_incident("assistant-core-agent-supervision-failing", "No failing Olympus worker states detected.")


async def run_project_codex_sweep_tick(runtime_policy: dict, *, project_key: str = "", reason: str = "zeus_heavy_sweep", state_key: str = "olympus.codex.sweep.latest"):
    now_ts = int(time.time())
    try:
        result = await core_post_json(
            "/codex/threads/sweep",
            {
                "token": CONTROL_TOKEN,
                "project_key": project_key,
                "limit": 80,
                "bootstrap_missing": True,
                "repair_sessions": True,
                "reason": reason,
            },
            timeout=45.0,
        )
    except Exception as exc:
        detail = format_exc(exc)
        guardian_log(f"{reason} failed: {detail}")
        write_state(
            state_key,
            json.dumps({"status": "error", "detail": detail, "project_key": project_key or None, "ts": now_ts}),
            {"ts": now_ts, "project_key": project_key or None, "reason": reason, "phase": "fetch"},
        )
        upsert_system_incident(
            f"codex-thread-sweep-{project_key or 'all'}",
            source_service="assistant-core",
            severity="medium",
            summary="Project Codex thread sweep failed",
            detail=detail,
            feature_ids=["ops_control_plane"],
            evidence={"project_key": project_key or None, "reason": reason},
        )
        return

    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    snapshot = {
        "status": "ok",
        "ts": now_ts,
        "project_key": project_key or None,
        "reason": reason,
        "summary": summary,
        "rows": result.get("rows", [])[:20] if isinstance(result.get("rows"), list) else [],
        "session_repair": result.get("session_repair", {}),
        "orchestration": runtime_policy.get("orchestration", {}) if isinstance(runtime_policy.get("orchestration"), dict) else {},
    }
    write_state(state_key, json.dumps(snapshot), {"ts": now_ts, "project_key": project_key or None, "reason": reason})
    guardian_log(
        f"{reason} completed project={project_key or '__all__'} ready={summary.get('ready', 0)} "
        f"attention={summary.get('attention_needed', 0)} missing={summary.get('missing', 0)}"
    )
    activity_log("Project Codex thread sweep", f"{reason} completed for {project_key or 'all projects'}", {"summary": summary, "project_key": project_key or None})
    cognition_log("lumi-daemon", "Project Codex thread sweep", f"{reason} completed for {project_key or 'all projects'}", {"summary": summary})
    if int(summary.get("attention_needed") or 0) or int(summary.get("missing") or 0):
        upsert_system_incident(
            f"codex-thread-attention-{project_key or 'all'}",
            source_service="assistant-core",
            severity="medium",
            summary="Project Codex threads need review attention",
            detail=json.dumps(summary),
            feature_ids=["ops_control_plane"],
            evidence={"project_key": project_key or None, "reason": reason, "rows": snapshot["rows"][:8]},
        )
    else:
        resolve_system_incident(
            f"codex-thread-attention-{project_key or 'all'}",
            "Project Codex thread sweep returned to ready state.",
        )


def scan_log_files() -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen_paths = set()
    for pattern in LOG_GLOBS:
        for path in sorted(Path("/").glob(pattern.lstrip("/")) if pattern.startswith("/") else Path(".").glob(pattern)):
            try:
                resolved = str(path.resolve())
            except Exception:
                resolved = str(path)
            if resolved in seen_paths or not path.is_file():
                continue
            normalized = resolved.replace("\\", "/").lower()
            if normalized.endswith("/guardian_runtime.log"):
                continue
            seen_paths.add(resolved)
            excerpt = suspicious_excerpt(collect_tail_from_file(path, ERROR_THRESHOLD_LINES), ERROR_THRESHOLD_LINES)
            if excerpt:
                candidates.append((resolved, excerpt))
    return candidates


def scan_compose_logs() -> list[tuple[str, str]]:
    rows = []
    command = (
        "cd /workspace/luminax && "
        f"(docker compose logs --tail {max(20, ERROR_THRESHOLD_LINES)} 2>&1 || docker-compose logs --tail {max(20, ERROR_THRESHOLD_LINES)} 2>&1 || true)"
    )
    raw = sh(command)
    excerpt = suspicious_excerpt("\n".join([raw.get("stdout", ""), raw.get("stderr", "")]), ERROR_THRESHOLD_LINES)
    if excerpt:
        rows.append(("docker-compose://logs", excerpt))
    return rows


async def self_upgrade_watch_loop():
    if not SELF_UPGRADE_ENABLED:
        guardian_log("self-upgrade watcher disabled by config")
        return
    await asyncio.sleep(8)
    while True:
        try:
            runtime_policy = load_runtime_policy()
            if bool(runtime_policy.get("workers_paused", False)):
                guardian_log(f"self-upgrade watcher paused: {runtime_policy.get('worker_pause_reason') or 'operator pause'}")
                await asyncio.sleep(max(5, SELF_UPGRADE_WATCH_INTERVAL_SEC))
                continue
            findings = scan_log_files() + scan_compose_logs()
            for source, excerpt in findings:
                digest = hashlib.sha1(f"{source}\n{excerpt}".encode("utf-8", errors="ignore")).hexdigest()
                if seen_digest_recently(digest):
                    continue
                service = infer_service_from_source(source, excerpt)
                guardian_log(f"self-upgrade watcher trigger: service={service} source={source}")
                await trigger_upgrade_analysis(service, source, excerpt, trigger_kind="daemon_watch")
        except Exception as exc:
            guardian_log(f"self-upgrade watcher loop error: {format_exc(exc)}")
        await asyncio.sleep(max(5, SELF_UPGRADE_WATCH_INTERVAL_SEC))


async def presence_loop():
    global ui_fail_count, last_ui_restart_ts, restart_history, autopilot_disabled_logged, last_contract_check_ts, last_agent_supervision_ts, last_zeus_project_sweep_ts, last_life_ops_review_ts, last_brain_tick_ts
    while True:
        try:
            telemetry = {
                "ts": int(time.time()),
                "cpu_percent": psutil.cpu_percent(interval=0.2),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_percent": psutil.disk_usage("/").percent,
            }
            services = sh("cd /workspace/luminax && docker compose ps --format json || docker-compose ps --format json || true")["stdout"][-4000:]
            maintenance = load_maintenance_mode()
            write_state("maintenance.mode", json.dumps(maintenance), {"ts": int(time.time())})
            runtime_policy = load_runtime_policy()
            write_state("runtime.policy", json.dumps(runtime_policy), {"ts": int(time.time())})
            workers_paused = bool(runtime_policy.get("workers_paused", False))
            orchestration = runtime_policy.get("orchestration") if isinstance(runtime_policy.get("orchestration"), dict) else {}
            now = int(time.time())
            brain_cfg = _runtime_brain_policy(runtime_policy)
            note = str(ensure_brain_session(reason="presence_loop").get("last_note") or "").strip() or "Local brain standing by."
            if bool(brain_cfg.get("enabled", True)) and (now - int(last_brain_tick_ts or 0)) >= max(30, int(brain_cfg.get("interval_sec") or 180)):
                brain_tick = await run_continuous_brain_tick(runtime_policy, telemetry, services)
                note = str(brain_tick.get("note") or note).strip() or note
                last_brain_tick_ts = now
            maintenance_result = await run_daily_memory_maintenance(runtime_policy)
            if str(maintenance_result.get("status") or "").strip().lower() == "ok":
                note = str(ensure_brain_session(reason="post_maintenance").get("last_note") or note).strip() or note
            write_state("presence.note", note, telemetry)
            cognition_log("lumi-daemon", "Continuous cycle note", note, telemetry)
            activity_log("Continuous cycle completed", note, telemetry)
            redis_client.set("luminax_presence_note", note, ex=max(INTERVAL * 3, 120))
            if (now - int(last_agent_supervision_ts or 0)) >= max(60, AGENT_SUPERVISION_INTERVAL_SEC):
                last_agent_supervision_ts = now
                await run_agent_supervision_tick(runtime_policy, maintenance)
            zeus_sweep_sec = max(3600, int(orchestration.get("zeus_heavy_sweep_sec") or ZEUS_HEAVY_SWEEP_INTERVAL_SEC))
            if not workers_paused and (now - int(last_zeus_project_sweep_ts or 0)) >= zeus_sweep_sec:
                last_zeus_project_sweep_ts = now
                await run_project_codex_sweep_tick(runtime_policy, reason="zeus_heavy_sweep", state_key="olympus.codex.sweep.latest")
            life_ops_review_sec = max(900, int(orchestration.get("life_ops_internal_review_sec") or LIFE_OPS_INTERNAL_REVIEW_INTERVAL_SEC))
            if not workers_paused and (now - int(last_life_ops_review_ts or 0)) >= life_ops_review_sec:
                last_life_ops_review_ts = now
                for project_key in LIFE_OPS_PROJECT_KEYS:
                    await run_project_codex_sweep_tick(
                        runtime_policy,
                        project_key=project_key,
                        reason="life_ops_hourly_review",
                        state_key=f"olympus.life_ops.review.{project_key}",
                    )
            if workers_paused:
                director = {"status": "paused", "detail": runtime_policy.get("worker_pause_reason") or "operator pause"}
            else:
                director = await run_hive_director_tick(note, telemetry)
            write_state("hive.director.last", json.dumps(director), {"ts": int(time.time())})
            if str(director.get("status", "")).strip().lower() == "ok":
                debate = director.get("debate", {}) if isinstance(director.get("debate"), dict) else {}
                if debate:
                    guardian_log(
                        f"hive director tick: Hive voted {debate.get('vote_signature', '--')} "
                        f"consensus={debate.get('consensus_passed')} goal={str(debate.get('goal', ''))[:180]}"
                    )
                    cognition_log(
                        "lumi-daemon",
                        "Hive Director vote",
                        debate.get("summary", "Hive debate completed."),
                        {"vote_signature": debate.get("vote_signature"), "consensus_passed": debate.get("consensus_passed")},
                    )
                    activity_log(
                        "Hive Director tick",
                        debate.get("summary", ""),
                        {"vote_signature": debate.get("vote_signature"), "consensus_passed": debate.get("consensus_passed")},
                    )
            elif director.get("status") not in {"disabled", "paused", None}:
                detail = str(director.get("detail", "")).strip() or "unknown error"
                guardian_log(f"hive director error: {detail}")
            cfg = load_autopilot_config()
            write_state("autopilot.config", json.dumps(cfg), {"ts": int(time.time())})
            if bool(maintenance.get("random_health_checks", True)):
                if (now - int(last_contract_check_ts or 0)) >= max(60, FEATURE_CONTRACT_CHECK_INTERVAL_SEC):
                    last_contract_check_ts = now
                    await run_feature_contract_health_check()
            await run_service_watch_tick(maintenance, cfg)
            if workers_paused:
                if not autopilot_disabled_logged:
                    guardian_log(f"worker pause active: {runtime_policy.get('worker_pause_reason') or 'operator pause'}")
                    autopilot_disabled_logged = True
            elif bool(cfg.get("enabled", False)):
                autopilot_disabled_logged = False
                ok, detail, restart_safe = await check_control_plane()
                allow_actions = bool(cfg.get("allow_service_actions", False)) and bool((maintenance.get("policy") or {}).get("autopilot_actions_allowed", False))
                if ok:
                    if ui_fail_count > 0:
                        guardian_log(f"autopilot recovered after failures: {ui_fail_count}")
                    resolve_system_incident("control-plane-health", "Control plane recovered.")
                    ui_fail_count = 0
                else:
                    ui_fail_count += 1
                    guardian_log(f"autopilot detected issue fail_count={ui_fail_count} detail={detail}")
                    activity_log("Autopilot detected control-plane issue", detail, {"fail_count": ui_fail_count})
                    upsert_system_incident(
                        "control-plane-health",
                        source_service="luminax-ui" if restart_safe else "assistant-core",
                        severity="high" if ui_fail_count >= 3 else "medium",
                        summary="Control-plane health check degraded",
                        detail=detail,
                        feature_ids=["ops_control_plane"],
                        evidence={"fail_count": ui_fail_count, "restart_safe": restart_safe},
                    )
                    if bool(cfg.get("talk_to_user", True)):
                        redis_client.set("luminax_operator_notice", f"Autopilot warning: {detail}", ex=3600)
                        cognition_log("lumi-daemon", "Autopilot warning", detail, {"fail_count": ui_fail_count})
                    threshold = int(cfg.get("ui_failure_threshold", AUTOPILOT_UI_FAILURE_THRESHOLD_ENV))
                    cooldown = int(cfg.get("ui_restart_cooldown_sec", AUTOPILOT_UI_RESTART_COOLDOWN_SEC_ENV))
                    restart_history = [ts for ts in restart_history if now - ts <= 3600]
                    if (
                        allow_actions
                        and restart_safe
                        and ui_fail_count >= max(1, threshold)
                        and (now - last_ui_restart_ts) >= max(30, cooldown)
                        and len(restart_history) < 4
                    ):
                        result = restart_ui_service()
                        last_ui_restart_ts = now
                        restart_history.append(now)
                        ui_fail_count = 0
                        guardian_log(f"autopilot action: restarted luminax-ui rc={result.get('returncode')}")
                        activity_log("Autopilot restarted luminax-ui", result.get("stdout", "")[-1000:], {"returncode": result.get("returncode")})
                        cognition_log(
                            "lumi-daemon",
                            "Autopilot action",
                            "Restarted luminax-ui after repeated health failures.",
                            {"returncode": result.get("returncode")},
                        )
                    elif allow_actions and not restart_safe:
                        guardian_log(f"autopilot withheld UI restart: upstream issue detail={detail}")
            else:
                if not autopilot_disabled_logged:
                    guardian_log("autopilot disabled by config")
                    autopilot_disabled_logged = True
        except Exception as exc:
            detail = format_exc(exc)
            guardian_log(f"presence loop error: {detail}")
            activity_log("Presence loop error", detail, {"error": detail})
            cognition_log("lumi-daemon", "Presence loop error", detail, {"error": detail})
        await asyncio.sleep(INTERVAL)


async def main():
    if not runtime_enabled() or not (AUTOPILOT_ENABLED or SELF_UPGRADE_ENABLED or HIVE_DIRECTOR_ENABLED):
        print("lumi-daemon: runtime_or_background_features_disabled")
        return
    if len(CONTROL_TOKEN) < 32:
        raise RuntimeError("operator_token_not_configured")
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    guardian_log("autopilot runtime started; existing diagnostics preserved")
    tasks = [asyncio.create_task(presence_loop())]
    if SELF_UPGRADE_ENABLED:
        tasks.append(asyncio.create_task(self_upgrade_watch_loop()))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
