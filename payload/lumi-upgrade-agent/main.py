from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import asyncio
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import redis
from fastapi import FastAPI, HTTPException, Query
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

app = FastAPI(title="Luminax Upgrade Agent")
CONTROL_TOKEN = os.getenv("CONTROL_TOKEN", "")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspace/luminax"))
LOG_ROOT = Path(os.getenv("LOG_ROOT", "/data/logs/new"))
UPGRADE_ROOT = Path(os.getenv("UPGRADE_ROOT", "/workspace/luminax/upgrades"))
UPGRADE_PENDING_DIR = UPGRADE_ROOT / "pending"
UPGRADE_HISTORY_DIR = UPGRADE_ROOT / "history"
UPGRADE_REJECTED_DIR = UPGRADE_ROOT / "rejected"
UPGRADE_TMP_DIR = UPGRADE_ROOT / "tmp"
AGENT_LOG_FILE = LOG_ROOT / "self_upgrade_agent.log"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_CODEX_MODEL = os.getenv("OPENAI_CODEX_MODEL", "gpt-5.3-codex")
UPGRADE_CODEX_USE_CLI = feature_enabled("UPGRADE_CODEX_USE_CLI")
UPGRADE_CODEX_CLI = os.getenv("UPGRADE_CODEX_CLI", "codex")
UPGRADE_CODEX_TIMEOUT_SEC = int(os.getenv("UPGRADE_CODEX_TIMEOUT_SEC", "240"))
UPGRADE_CODEX_SANDBOX = os.getenv("UPGRADE_CODEX_SANDBOX", "read-only")
UPGRADE_CODEX_AUTH_READY = False
UPGRADE_CODEX_AUTH_LAST_ERROR = ""
UPGRADE_CODEX_AUTH_LAST_CHECK_TS = 0
UPGRADE_CODEX_AUTH_LAST_MODE = ""
UPGRADE_CODEX_AUTH_RECHECK_SEC = max(15, int(os.getenv("UPGRADE_CODEX_AUTH_RECHECK_SEC", "60") or 60))
UPGRADE_CODEX_EXEC_READY = True
UPGRADE_CODEX_EXEC_LAST_ERROR = ""
UPGRADE_CODEX_EXEC_LAST_FAILURE_TS = 0
UPGRADE_CODEX_EXEC_ERROR_TTL_SEC = max(300, int(os.getenv("UPGRADE_CODEX_EXEC_ERROR_TTL_SEC", "1800") or 1800))
SELF_UPGRADE_ENABLED = feature_enabled("SELF_UPGRADE_ENABLED")
UPGRADE_AUTO_APPLY_AFTER_N_OK = int(os.getenv("UPGRADE_AUTO_APPLY_AFTER_N_OK", "3"))
SELF_UPGRADE_MAX_PATCH_LINES = int(os.getenv("SELF_UPGRADE_MAX_PATCH_LINES", "240"))
ERROR_THRESHOLD_LINES = int(os.getenv("ERROR_THRESHOLD_LINES", "80"))
redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL) if runtime_enabled() and OPENAI_API_KEY else None

if runtime_enabled():
    for directory in [UPGRADE_PENDING_DIR, UPGRADE_HISTORY_DIR, UPGRADE_REJECTED_DIR, UPGRADE_TMP_DIR, LOG_ROOT]:
        directory.mkdir(parents=True, exist_ok=True)

SERVICE_FILE_HINTS = {
    "assistant-core": ["assistant-core/main.py", "assistant-core/requirements.txt", "docker-compose.yml", "config/skills-registry.json"],
    "admin-api": ["admin-api/main.py", "admin-api/requirements.txt", "docker-compose.yml"],
    "lumi-daemon": ["lumi-daemon/daemon.py", "lumi-daemon/requirements.txt", "config/autopilot-config.json"],
    "lumi-upgrade-agent": ["lumi-upgrade-agent/main.py", "lumi-upgrade-agent/requirements.txt", "docker-compose.yml"],
    "luminax-ui": ["luminax-ui/index.html", "nginx/default.conf"],
    "realtime-gateway": ["realtime-gateway/main.py", "docker-compose.yml"],
    "voice-agent": ["voice-agent/main.py", "voice-agent/requirements.txt"],
    "discord-bot": ["discord-bot/bot.py", "discord-bot/requirements.txt"],
}

SERVICE_HEALTH_URLS = {
    "assistant-core": "http://assistant-core:8000/api/health/light",
    "admin-api": "http://admin-api:8040/telemetry/latest",
    "lumi-daemon": "http://admin-api:8040/autopilot/status",
    "lumi-upgrade-agent": "http://lumi-upgrade-agent:8130/health",
    "realtime-gateway": "http://realtime-gateway:8050/session/info",
    "voice-agent": "http://voice-agent:8030/health",
    "discord-bot": "http://admin-api:8040/logs/tail?name=discord-bot",
    "luminax-ui": "http://luminax-ui/",
}


class AnalyzeRequest(BaseModel):
    service: str
    error_excerpt: str
    log_path: str = ""
    trigger_kind: str = "error"
    context: dict[str, Any] = Field(default_factory=dict)
    file_hints: list[str] = Field(default_factory=list)
    apply_if_safe: bool = False
    initiative_goal: str = ""


class CapabilityProposalRequest(BaseModel):
    capability_name: str
    objective: str
    target_service: str = "assistant-core"
    context: dict[str, Any] = Field(default_factory=dict)
    file_hints: list[str] = Field(default_factory=list)


class ProposalApplyRequest(BaseModel):
    token: str = ""
    proposal_id: str
    allow_medium_risk: bool = False


def agent_log(message: str):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {message}"
    try:
        with open(AGENT_LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def format_exc(exc: Exception) -> str:
    text = str(exc).strip()
    return text or f"{exc.__class__.__name__}: {repr(exc)}"


def shell(cmd: str, cwd: Path | None = None, timeout: int | None = None, env_overrides: dict[str, str] | None = None):
    require_feature("LUMINAX_HOST_EXECUTION_ENABLED")
    env = None
    if env_overrides:
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in env_overrides.items() if v is not None})
    try:
        proc = subprocess.run(
            ["/bin/sh", "-lc", cmd],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout[-30000:],
            "stderr": proc.stderr[-30000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout": str(exc.stdout or "")[-30000:],
            "stderr": (str(exc.stderr or "") + f"\nCommand timed out after {timeout} seconds.")[-30000:],
        }


def slug(value: str) -> str:
    safe = "".join(ch for ch in (value or "").strip().lower() if ch.isalnum() or ch in "-_")
    return safe[:80] or "upgrade"


def proposal_json_path(proposal_id: str) -> Path:
    return UPGRADE_PENDING_DIR / f"{proposal_id}.json"


def proposal_patch_path(proposal_id: str) -> Path:
    return UPGRADE_PENDING_DIR / f"{proposal_id}.patch"


def history_json_path(proposal_id: str) -> Path:
    return UPGRADE_HISTORY_DIR / f"{proposal_id}.json"


def history_patch_path(proposal_id: str) -> Path:
    return UPGRADE_HISTORY_DIR / f"{proposal_id}.patch"


def rejected_json_path(proposal_id: str) -> Path:
    return UPGRADE_REJECTED_DIR / f"{proposal_id}.json"


def rejected_patch_path(proposal_id: str) -> Path:
    return UPGRADE_REJECTED_DIR / f"{proposal_id}.patch"


def control_granted() -> bool:
    try:
        return redis_client.get("luminax_control_granted") == "true"
    except Exception:
        return False


def validate_apply_control_or_403(token: str):
    if not token_matches(token, CONTROL_TOKEN) or not control_granted():
        raise HTTPException(status_code=403, detail="Control not granted")


def service_file_hints(service: str, extra: list[str] | None = None) -> list[str]:
    hints = list(SERVICE_FILE_HINTS.get((service or "").strip(), []))
    for item in extra or []:
        if item and item not in hints:
            hints.append(item)
    return hints[:8]


def read_file_context(rel_path: str, max_chars: int = 3200) -> str:
    try:
        path = (WORKSPACE_ROOT / rel_path).resolve()
        if not str(path).startswith(str(WORKSPACE_ROOT.resolve())):
            return ""
        if not path.exists() or not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        return text[:max_chars]
    except Exception:
        return ""


def collect_file_contexts(paths: list[str]) -> dict[str, str]:
    out = {}
    for rel_path in paths:
        text = read_file_context(rel_path)
        if text:
            out[rel_path] = text
    return out


def patch_line_count(patch_text: str) -> int:
    count = 0
    for line in (patch_text or "").splitlines():
        if not line:
            continue
        if line.startswith(("+++", "---", "@@")):
            continue
        if line[0] in "+-":
            count += 1
    return count


def parse_patch_files(patch_text: str) -> list[str]:
    out = []
    for line in (patch_text or "").splitlines():
        if line.startswith("+++ "):
            raw = line[4:].strip()
            if raw == "/dev/null":
                continue
            if raw.startswith("b/"):
                raw = raw[2:]
            if raw and raw not in out:
                out.append(raw)
    return out


def latest_records(root: Path, limit: int = 20) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[: max(1, min(limit, 200))]:
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return rows


def latest_timeline_records(limit: int = 20) -> list[dict[str, Any]]:
    rows = latest_records(UPGRADE_HISTORY_DIR, limit=limit) + latest_records(UPGRADE_REJECTED_DIR, limit=limit)
    rows.sort(key=lambda item: int(item.get("created_at") or 0), reverse=True)
    return rows[: max(1, min(limit, 200))]


def load_proposal_by_id(proposal_id: str) -> dict[str, Any] | None:
    for path in [proposal_json_path(proposal_id), history_json_path(proposal_id), rejected_json_path(proposal_id)]:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def load_latest_proposal() -> dict[str, Any] | None:
    rows = latest_records(UPGRADE_PENDING_DIR, limit=1)
    if rows:
        return rows[0]
    rows = latest_records(UPGRADE_HISTORY_DIR, limit=1)
    return rows[0] if rows else None


def auto_apply_history_count(service: str, touched_files: list[str]) -> int:
    target_exts = sorted({Path(path).suffix.lower() for path in touched_files if Path(path).suffix})
    count = 0
    for row in latest_records(UPGRADE_HISTORY_DIR, limit=200):
        if (row.get("status") or "") != "applied":
            continue
        if (row.get("service") or "") != service:
            continue
        row_exts = sorted({Path(path).suffix.lower() for path in (row.get("files_touched") or []) if Path(path).suffix})
        if row_exts == target_exts:
            count += 1
    return count


def build_error_analysis_messages(req: AnalyzeRequest, file_contexts: dict[str, str]) -> list[dict[str, str]]:
    system = (
        "You are Codex integrated into the Luminax Ubuntu self-upgrade loop. "
        "You diagnose runtime failures and propose the smallest safe code or config change that fixes the issue. "
        "Return strict JSON with keys: root_cause, proposed_patch, confidence, risk, reasoning_trace, summary, validation_steps, files_touched. "
        "Use unified diff format for proposed_patch. Risk must be one of: low, medium, high. Confidence must be an integer 0-100. "
        "Never propose deleting capabilities. Prefer additive or corrective fixes. "
        "If context is insufficient, say so explicitly in root_cause and keep risk high."
    )
    file_blob = []
    for rel_path, text in file_contexts.items():
        file_blob.append(f"FILE: {rel_path}\n{text}")
    user = (
        f"TRIGGER KIND: {req.trigger_kind}\n"
        f"SERVICE: {req.service}\n"
        f"LOG PATH: {req.log_path or 'unknown'}\n"
        f"ERROR EXCERPT:\n{req.error_excerpt[:12000]}\n\n"
        f"INITIATIVE GOAL: {req.initiative_goal[:1200]}\n\n"
        f"CONTEXT JSON:\n{json.dumps(req.context or {}, indent=2)[:6000]}\n\n"
        f"RELEVANT FILE CONTEXT:\n{chr(10).join(file_blob)[:24000]}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_capability_messages(req: CapabilityProposalRequest, file_contexts: dict[str, str]) -> list[dict[str, str]]:
    system = (
        "You are Codex integrated into the Luminax Ubuntu self-upgrade loop. "
        "Design and emit a minimal patch to add a new capability while preserving all existing behavior. "
        "Return strict JSON with keys: root_cause, proposed_patch, confidence, risk, reasoning_trace, summary, validation_steps, files_touched. "
        "Use unified diff format for proposed_patch. Risk must be one of: low, medium, high. Confidence must be 0-100. "
        "Do not delete incomplete skills; extend them or add placeholders safely."
    )
    file_blob = []
    for rel_path, text in file_contexts.items():
        file_blob.append(f"FILE: {rel_path}\n{text}")
    user = (
        f"NEW CAPABILITY: {req.capability_name}\n"
        f"TARGET SERVICE: {req.target_service}\n"
        f"OBJECTIVE:\n{req.objective[:5000]}\n\n"
        f"CONTEXT JSON:\n{json.dumps(req.context or {}, indent=2)[:6000]}\n\n"
        f"RELEVANT FILE CONTEXT:\n{chr(10).join(file_blob)[:24000]}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


UPGRADE_CODEX_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "proposed_patch": {"type": "string"},
        "confidence": {"type": "integer"},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "reasoning_trace": {"type": "string"},
        "summary": {"type": "string"},
        "validation_steps": {"type": "array", "items": {"type": "string"}},
        "files_touched": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["root_cause", "proposed_patch", "confidence", "risk", "reasoning_trace", "summary", "validation_steps", "files_touched"],
    "additionalProperties": False,
}


def codex_cli_available() -> bool:
    return UPGRADE_CODEX_USE_CLI and bool(shutil.which(UPGRADE_CODEX_CLI))


def ensure_codex_cli_authenticated(force: bool = False) -> dict[str, Any]:
    global UPGRADE_CODEX_AUTH_READY, UPGRADE_CODEX_AUTH_LAST_ERROR, UPGRADE_CODEX_AUTH_LAST_CHECK_TS, UPGRADE_CODEX_AUTH_LAST_MODE
    if not codex_cli_available():
        UPGRADE_CODEX_AUTH_READY = False
        UPGRADE_CODEX_AUTH_LAST_MODE = ""
        UPGRADE_CODEX_AUTH_LAST_ERROR = "Codex CLI not available"
        return {"status": "unavailable", "detail": UPGRADE_CODEX_AUTH_LAST_ERROR}
    now = int(time.time())
    if UPGRADE_CODEX_AUTH_READY and not force and (now - int(UPGRADE_CODEX_AUTH_LAST_CHECK_TS or 0)) < UPGRADE_CODEX_AUTH_RECHECK_SEC:
        return {"status": "ok", "detail": "already_authenticated", "auth_mode": UPGRADE_CODEX_AUTH_LAST_MODE or "session"}

    def _auth_mode(detail: str) -> str:
        lower = str(detail or "").lower()
        if "chatgpt" in lower:
            return "chatgpt"
        if "api key" in lower:
            return "api_key"
        if "logged in" in lower:
            return "session"
        return ""

    status_env = {"OPENAI_BASE_URL": OPENAI_BASE_URL} if OPENAI_BASE_URL else {}
    status_result = shell(
        f"{shlex.quote(UPGRADE_CODEX_CLI)} login status",
        WORKSPACE_ROOT,
        timeout=30,
        env_overrides=status_env,
    )
    status_stdout = str(status_result.get("stdout") or "")
    status_stderr = str(status_result.get("stderr") or "")
    status_detail = (status_stdout or status_stderr or "").strip()
    if int(status_result.get("returncode") or 0) == 0:
        UPGRADE_CODEX_AUTH_READY = True
        UPGRADE_CODEX_AUTH_LAST_ERROR = ""
        UPGRADE_CODEX_AUTH_LAST_CHECK_TS = now
        UPGRADE_CODEX_AUTH_LAST_MODE = _auth_mode(status_detail)
        return {
            "status": "ok",
            "detail": "already_authenticated",
            "auth_mode": UPGRADE_CODEX_AUTH_LAST_MODE or "session",
            "stdout_tail": status_stdout[-2000:],
            "stderr_tail": status_stderr[-2000:],
        }

    if OPENAI_API_KEY:
        result = shell(
            f"printenv OPENAI_API_KEY | {shlex.quote(UPGRADE_CODEX_CLI)} login --with-api-key && {shlex.quote(UPGRADE_CODEX_CLI)} login status",
            WORKSPACE_ROOT,
            timeout=60,
            env_overrides={"OPENAI_API_KEY": OPENAI_API_KEY, "OPENAI_BASE_URL": OPENAI_BASE_URL},
        )
        detail = (str(result.get("stdout") or "") or str(result.get("stderr") or "")).strip()
        ok = int(result.get("returncode") or 0) == 0
        UPGRADE_CODEX_AUTH_READY = ok
        UPGRADE_CODEX_AUTH_LAST_ERROR = "" if ok else detail[:1200] or "Codex CLI authentication failed"
        UPGRADE_CODEX_AUTH_LAST_CHECK_TS = int(time.time())
        UPGRADE_CODEX_AUTH_LAST_MODE = _auth_mode(detail)
        return {
            "status": "ok" if ok else "failed",
            "detail": "" if ok else UPGRADE_CODEX_AUTH_LAST_ERROR,
            "auth_mode": UPGRADE_CODEX_AUTH_LAST_MODE or ("api_key" if ok else ""),
            "stdout_tail": str(result.get("stdout") or "")[-2000:],
            "stderr_tail": str(result.get("stderr") or "")[-2000:],
        }

    UPGRADE_CODEX_AUTH_READY = False
    UPGRADE_CODEX_AUTH_LAST_MODE = ""
    UPGRADE_CODEX_AUTH_LAST_CHECK_TS = now
    if "not logged in" in status_detail.lower():
        UPGRADE_CODEX_AUTH_LAST_ERROR = (
            "Codex CLI is not logged in. Complete `codex login --device-auth` inside lumi-upgrade-agent "
            "or use a shared ChatGPT-authenticated Codex profile."
        )
        return {
            "status": "needs_login",
            "detail": UPGRADE_CODEX_AUTH_LAST_ERROR,
            "stdout_tail": status_stdout[-2000:],
            "stderr_tail": status_stderr[-2000:],
        }
    UPGRADE_CODEX_AUTH_LAST_ERROR = status_detail[:1200] or "Codex CLI authentication failed"
    return {
        "status": "failed",
        "detail": UPGRADE_CODEX_AUTH_LAST_ERROR,
        "stdout_tail": status_stdout[-2000:],
        "stderr_tail": status_stderr[-2000:],
    }


def set_codex_cli_exec_error(detail: str):
    global UPGRADE_CODEX_EXEC_READY, UPGRADE_CODEX_EXEC_LAST_ERROR, UPGRADE_CODEX_EXEC_LAST_FAILURE_TS
    UPGRADE_CODEX_EXEC_READY = False
    UPGRADE_CODEX_EXEC_LAST_ERROR = str(detail or "").strip()[:1200]
    UPGRADE_CODEX_EXEC_LAST_FAILURE_TS = int(time.time())


def clear_codex_cli_exec_error():
    global UPGRADE_CODEX_EXEC_READY, UPGRADE_CODEX_EXEC_LAST_ERROR, UPGRADE_CODEX_EXEC_LAST_FAILURE_TS
    UPGRADE_CODEX_EXEC_READY = True
    UPGRADE_CODEX_EXEC_LAST_ERROR = ""
    UPGRADE_CODEX_EXEC_LAST_FAILURE_TS = 0


def current_codex_cli_exec_error() -> str:
    if not UPGRADE_CODEX_EXEC_LAST_ERROR:
        return ""
    age = int(time.time()) - int(UPGRADE_CODEX_EXEC_LAST_FAILURE_TS or 0)
    if age > UPGRADE_CODEX_EXEC_ERROR_TTL_SEC:
        return ""
    return UPGRADE_CODEX_EXEC_LAST_ERROR[:300]


def classify_codex_cli_exec_failure(stdout_tail: str, stderr_tail: str, response_text: str = "") -> str:
    combined = "\n".join(part for part in [stderr_tail, stdout_tail, response_text[:800]] if part).strip()
    lower = combined.lower()
    if "missing scopes:" in lower or "insufficient permissions" in lower or "api.responses.write" in lower or "api.model.read" in lower:
        return (
            "Codex CLI is logged in, but the linked OpenAI account does not currently have the API scopes needed "
            "to execute model runs (`api.model.read` and `api.responses.write`). ChatGPT login status alone is not "
            "enough until the linked API organization/project permissions are correct."
        )
    if "not logged in" in lower:
        return (
            "Codex CLI is not logged in. Complete `codex login --device-auth` inside lumi-upgrade-agent "
            "or use a shared ChatGPT-authenticated Codex profile."
        )
    if response_text.strip() == "" and combined:
        return combined[:3000]
    if response_text.strip() == "":
        return "Codex CLI completed without a structured final response."
    return f"Codex CLI returned a non-JSON final response: {response_text[:600]}"


def codex_messages_to_prompt(messages: list[dict[str, str]]) -> str:
    parts = [
        "You are Codex operating inside the Luminax self-upgrade loop.",
        "Return strict JSON that matches the provided schema.",
        "Keep patches minimal and grounded in the supplied files and logs.",
    ]
    for item in messages:
        role = str(item.get("role") or "user").upper()
        content = str(item.get("content") or "").strip()
        if content:
            parts.append(f"{role}:\n{content}")
    return "\n\n".join(parts)


async def call_codex_cli_json(messages: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not codex_cli_available():
        raise RuntimeError("Codex CLI is not available")
    auth = ensure_codex_cli_authenticated()
    if auth.get("status") != "ok":
        raise RuntimeError(str(auth.get("detail") or "Codex CLI authentication failed"))
    run_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    run_dir = UPGRADE_TMP_DIR / f"codex_cli_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    schema_path = run_dir / "schema.json"
    response_path = run_dir / "response.json"
    schema_path.write_text(json.dumps(UPGRADE_CODEX_RESPONSE_SCHEMA, indent=2), encoding="utf-8")
    prompt_text = codex_messages_to_prompt(messages)[:24000]
    cmd = " ".join(
        [
            shlex.quote(UPGRADE_CODEX_CLI),
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            shlex.quote(UPGRADE_CODEX_SANDBOX),
            "--model",
            shlex.quote(OPENAI_CODEX_MODEL),
            "--output-schema",
            shlex.quote(str(schema_path)),
            "-o",
            shlex.quote(str(response_path)),
            shlex.quote(prompt_text),
        ]
    )
    codex_env = {}
    if OPENAI_API_KEY:
        codex_env["OPENAI_API_KEY"] = OPENAI_API_KEY
    if OPENAI_BASE_URL:
        codex_env["OPENAI_BASE_URL"] = OPENAI_BASE_URL
    result = await asyncio.to_thread(shell, cmd, WORKSPACE_ROOT, UPGRADE_CODEX_TIMEOUT_SEC, codex_env)
    response_text = ""
    if response_path.exists():
        response_text = response_path.read_text(encoding="utf-8", errors="ignore")
        if response_text.strip():
            try:
                payload = json.loads(response_text)
                if isinstance(payload, dict):
                    clear_codex_cli_exec_error()
                    return payload, {
                        "model": UPGRADE_CODEX_CLI,
                        "provider": "codex_cli",
                        "returncode": result.get("returncode"),
                        "artifact_dir": str(run_dir),
                        "stdout_tail": result.get("stdout", ""),
                        "stderr_tail": result.get("stderr", ""),
                    }
            except Exception:
                pass
    failure_detail = classify_codex_cli_exec_failure(
        str(result.get("stdout") or ""),
        str(result.get("stderr") or ""),
        response_text,
    )
    set_codex_cli_exec_error(failure_detail)
    raise RuntimeError(failure_detail)


async def call_codex_json(messages: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
    cli_error = ""
    if codex_cli_available():
        try:
            return await call_codex_cli_json(messages)
        except Exception as exc:
            cli_error = format_exc(exc)
            agent_log(f"codex cli fallback engaged detail={cli_error}")
    if not OPENAI_API_KEY:
        raise RuntimeError(cli_error or "Codex CLI is not authenticated and OPENAI_API_KEY is missing for fallback.")
    response = await openai_client.chat.completions.create(
        model=OPENAI_CODEX_MODEL,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.15,
    )
    content = response.choices[0].message.content or "{}"
    data = json.loads(content)
    meta = {
        "model": OPENAI_CODEX_MODEL,
        "provider": "openai",
        "response_id": getattr(response, "id", ""),
        "usage": getattr(response, "usage", None).model_dump() if getattr(response, "usage", None) else {},
    }
    return data if isinstance(data, dict) else {}, meta


def normalize_proposal(raw: dict[str, Any], *, service: str, trigger_kind: str, error_excerpt: str, log_path: str, file_contexts: dict[str, str], initiative_goal: str = "") -> dict[str, Any]:
    risk = str(raw.get("risk") or "high").strip().lower()
    if risk not in {"low", "medium", "high"}:
        risk = "high"
    proposed_patch = str(raw.get("proposed_patch") or "").strip()
    files_touched = raw.get("files_touched") or parse_patch_files(proposed_patch)
    if not isinstance(files_touched, list):
        files_touched = []
    files_touched = [str(item).strip() for item in files_touched if str(item).strip()][:24]
    proposal_id = f"{int(time.time())}_{slug(service)}_{uuid.uuid4().hex[:8]}"
    patch_lines = patch_line_count(proposed_patch)
    prior_ok = auto_apply_history_count(service, files_touched)
    auto_eligible = (
        SELF_UPGRADE_ENABLED
        and risk == "low"
        and int(raw.get("confidence") or 0) >= 78
        and patch_lines <= SELF_UPGRADE_MAX_PATCH_LINES
        and prior_ok >= max(0, UPGRADE_AUTO_APPLY_AFTER_N_OK)
    )
    proposal = {
        "proposal_id": proposal_id,
        "service": service,
        "trigger_kind": trigger_kind,
        "initiative_goal": initiative_goal,
        "log_path": log_path,
        "created_at": int(time.time()),
        "status": "pending",
        "error_excerpt": error_excerpt[:16000],
        "root_cause": str(raw.get("root_cause") or "No root cause returned").strip()[:6000],
        "proposed_patch": proposed_patch[:80000],
        "confidence": max(0, min(100, int(raw.get("confidence") or 0))),
        "risk": risk,
        "summary": str(raw.get("summary") or "").strip()[:4000],
        "reasoning_trace": str(raw.get("reasoning_trace") or "").strip()[:12000],
        "validation_steps": raw.get("validation_steps") if isinstance(raw.get("validation_steps"), list) else [],
        "files_touched": files_touched,
        "file_contexts": file_contexts,
        "patch_line_count": patch_lines,
        "auto_apply_after_n_ok": UPGRADE_AUTO_APPLY_AFTER_N_OK,
        "prior_success_same_shape": prior_ok,
        "auto_apply_eligible": auto_eligible,
    }
    return proposal


def persist_pending_proposal(proposal: dict[str, Any]):
    proposal_json_path(proposal["proposal_id"]).write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    proposal_patch_path(proposal["proposal_id"]).write_text(proposal.get("proposed_patch", ""), encoding="utf-8")


def archive_proposal(proposal: dict[str, Any], rejected: bool = False):
    target_json = rejected_json_path(proposal["proposal_id"]) if rejected else history_json_path(proposal["proposal_id"])
    target_patch = rejected_patch_path(proposal["proposal_id"]) if rejected else history_patch_path(proposal["proposal_id"])
    target_json.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    target_patch.write_text(proposal.get("proposed_patch", ""), encoding="utf-8")
    pending_json = proposal_json_path(proposal["proposal_id"])
    pending_patch = proposal_patch_path(proposal["proposal_id"])
    if pending_json.exists():
        pending_json.unlink()
    if pending_patch.exists():
        pending_patch.unlink()


def validate_patch_shape_or_raise(proposal: dict[str, Any], allow_medium_risk: bool = False):
    if not proposal.get("proposed_patch"):
        raise HTTPException(status_code=400, detail="Proposal did not contain a patch")
    if proposal.get("patch_line_count", 0) > SELF_UPGRADE_MAX_PATCH_LINES:
        raise HTTPException(status_code=400, detail="Patch exceeds SELF_UPGRADE_MAX_PATCH_LINES")
    risk = str(proposal.get("risk") or "high").lower()
    if risk == "high":
        raise HTTPException(status_code=400, detail="High-risk patch rejected")
    if risk == "medium" and not allow_medium_risk:
        raise HTTPException(status_code=400, detail="Medium-risk patch requires explicit override")


def copy_shadow_workspace(proposal_id: str) -> Path:
    temp_root = UPGRADE_TMP_DIR / proposal_id
    if temp_root.exists():
        shutil.rmtree(temp_root, ignore_errors=True)
    target = temp_root / "workspace"
    shutil.copytree(
        WORKSPACE_ROOT,
        target,
        ignore=shutil.ignore_patterns("memory", "logs", "games", "sites", "published_sites", "mysql_data", "redis_data", "qdrant_data", "upgrades", "__pycache__", ".git"),
    )
    return target


def apply_patch_file(root: Path, patch_file: Path):
    return shell(f"git apply --unsafe-paths --whitespace=nowarn '{patch_file.as_posix()}'", cwd=root)


def validate_shadow_workspace(root: Path, touched_files: list[str]) -> dict[str, Any]:
    checks = []
    py_files = [path for path in touched_files if path.endswith(".py")]
    sh_files = [path for path in touched_files if path.endswith(".sh")]
    html_files = [path for path in touched_files if path.endswith(".html")]
    compose_touched = any(Path(path).name == "docker-compose.yml" for path in touched_files)
    if py_files:
        quoted = " ".join([f"'{path}'" for path in py_files])
        checks.append(("python_compile", shell(f"python3 -m py_compile {quoted}", cwd=root)))
    if sh_files:
        for path in sh_files:
            checks.append((f"shell_syntax:{path}", shell(f"bash -n '{path}'", cwd=root)))
    if html_files:
        for path in html_files:
            checks.append(
                (
                    f"html_script_markers:{path}",
                    shell(
                        "python3 - <<'PY'\n"
                        f"from pathlib import Path\n"
                        f"text = Path('{path}').read_text(encoding='utf-8', errors='ignore')\n"
                        "assert text.count('<script') >= 1\n"
                        "assert text.count('</script>') >= 1\n"
                        "print('ok')\n"
                        "PY",
                        cwd=root,
                    ),
                )
            )
    if compose_touched or (root / "docker-compose.yml").exists():
        checks.append(("docker_compose_config", shell("docker compose -f docker-compose.yml config >/tmp/luminax_upgrade_compose.txt", cwd=root)))
    passed = all(item.get("returncode") == 0 for _, item in checks)
    return {
        "status": "ok" if passed else "failed",
        "checks": [{"name": name, **result} for name, result in checks],
    }


def backup_live_files(touched_files: list[str], proposal_id: str) -> Path:
    backup_root = UPGRADE_TMP_DIR / f"{proposal_id}_live_backup"
    if backup_root.exists():
        shutil.rmtree(backup_root, ignore_errors=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    for rel_path in touched_files:
        source = (WORKSPACE_ROOT / rel_path).resolve()
        if not str(source).startswith(str(WORKSPACE_ROOT.resolve())):
            continue
        if not source.exists():
            continue
        target = backup_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return backup_root


def restore_live_files(backup_root: Path):
    if not backup_root.exists():
        return
    for source in backup_root.rglob("*"):
        if not source.is_file():
            continue
        rel_path = source.relative_to(backup_root)
        target = WORKSPACE_ROOT / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


async def verify_service_health(service: str) -> dict[str, Any]:
    url = SERVICE_HEALTH_URLS.get(service)
    if not url:
        return {"status": "unknown", "detail": "No health URL mapped"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for _ in range(12):
                try:
                    response = await client.get(url)
                    if response.status_code < 400:
                        try:
                            parsed = response.json()
                        except Exception:
                            parsed = {"raw": response.text[:2000]}
                        return {"status": "ok", "url": url, "data": parsed}
                except Exception:
                    pass
                await asyncio.sleep(5)
    except Exception as exc:
        return {"status": "error", "detail": format_exc(exc), "url": url}
    return {"status": "error", "detail": "Service did not become healthy in time", "url": url}


def commit_live_change(service: str, proposal_id: str, files_touched: list[str]):
    if shell("git rev-parse --is-inside-work-tree", cwd=WORKSPACE_ROOT).get("returncode") != 0:
        return {"status": "skipped", "detail": "workspace is not a git repository"}
    quoted = " ".join([f"'{path}'" for path in files_touched]) or "."
    message = f"Luminax self-upgrade {proposal_id} ({service})"
    add_res = shell(f"git add {quoted}", cwd=WORKSPACE_ROOT)
    commit_res = shell(f"git commit -m \"{message}\" || true", cwd=WORKSPACE_ROOT)
    return {"status": "ok", "add": add_res, "commit": commit_res}


async def restart_after_apply(service: str, compose_changed: bool) -> dict[str, Any]:
    if compose_changed or not service:
        restart = shell("docker compose up -d --build", cwd=WORKSPACE_ROOT)
    else:
        restart = shell(f"docker compose up -d --build {service}", cwd=WORKSPACE_ROOT)
    verify = await verify_service_health(service)
    return {"restart": restart, "verify": verify}


async def apply_proposal_internal(proposal: dict[str, Any], allow_medium_risk: bool = False, auto_triggered: bool = False) -> dict[str, Any]:
    require_feature("SELF_UPGRADE_ENABLED")
    require_feature("LUMINAX_HOST_EXECUTION_ENABLED")
    validate_patch_shape_or_raise(proposal, allow_medium_risk=allow_medium_risk)
    patch_file = proposal_patch_path(proposal["proposal_id"])
    if not patch_file.exists():
        patch_file.write_text(proposal.get("proposed_patch", ""), encoding="utf-8")
    shadow_root = copy_shadow_workspace(proposal["proposal_id"])
    shadow_apply = apply_patch_file(shadow_root, patch_file)
    if shadow_apply.get("returncode") != 0:
        proposal["status"] = "rejected"
        proposal["rejection_reason"] = "shadow_apply_failed"
        proposal["apply_result"] = {"shadow_apply": shadow_apply}
        archive_proposal(proposal, rejected=True)
        return {"status": "rejected", "detail": "Shadow patch apply failed", "proposal": proposal}
    validation = validate_shadow_workspace(shadow_root, proposal.get("files_touched") or parse_patch_files(proposal.get("proposed_patch", "")))
    if validation.get("status") != "ok":
        proposal["status"] = "rejected"
        proposal["rejection_reason"] = "validation_failed"
        proposal["apply_result"] = {"validation": validation, "shadow_apply": shadow_apply}
        archive_proposal(proposal, rejected=True)
        return {"status": "rejected", "detail": "Integrity validation failed", "proposal": proposal}
    live_backup = backup_live_files(proposal.get("files_touched") or [], proposal["proposal_id"])
    live_apply = apply_patch_file(WORKSPACE_ROOT, patch_file)
    if live_apply.get("returncode") != 0:
        proposal["status"] = "rejected"
        proposal["rejection_reason"] = "live_apply_failed"
        proposal["apply_result"] = {"live_apply": live_apply, "validation": validation}
        archive_proposal(proposal, rejected=True)
        return {"status": "rejected", "detail": "Live patch apply failed", "proposal": proposal}
    git_commit = commit_live_change(proposal.get("service", ""), proposal["proposal_id"], proposal.get("files_touched") or [])
    restart = await restart_after_apply(
        proposal.get("service", ""),
        any(Path(path).name == "docker-compose.yml" for path in (proposal.get("files_touched") or [])),
    )
    if restart.get("verify", {}).get("status") != "ok":
        restore_live_files(live_backup)
        shell("docker compose up -d --build", cwd=WORKSPACE_ROOT)
        proposal["status"] = "rolled_back"
        proposal["rejection_reason"] = "post_apply_health_failed"
        proposal["apply_result"] = {"validation": validation, "live_apply": live_apply, "git_commit": git_commit, "restart": restart}
        archive_proposal(proposal, rejected=True)
        return {"status": "rolled_back", "detail": "Service health failed after apply; rollback executed", "proposal": proposal}
    proposal["status"] = "applied"
    proposal["apply_result"] = {
        "validation": validation,
        "shadow_apply": shadow_apply,
        "live_apply": live_apply,
        "git_commit": git_commit,
        "restart": restart,
        "auto_triggered": auto_triggered,
    }
    archive_proposal(proposal, rejected=False)
    return {"status": "applied", "proposal": proposal}


@app.get("/health")
async def health():
    if codex_cli_available():
        ensure_codex_cli_authenticated()
    exec_error = current_codex_cli_exec_error()
    return {
        "status": "ok",
        "service": "lumi-upgrade-agent",
        "self_upgrade_enabled": SELF_UPGRADE_ENABLED,
        "model": OPENAI_CODEX_MODEL,
        "provider_mode": "codex_cli" if codex_cli_available() else "openai",
        "codex_auth_ready": bool(UPGRADE_CODEX_AUTH_READY) and not bool(exec_error),
        "codex_auth_error": exec_error or UPGRADE_CODEX_AUTH_LAST_ERROR[:300],
        "codex_auth_mode": UPGRADE_CODEX_AUTH_LAST_MODE or None,
        "codex_login_ready": bool(UPGRADE_CODEX_AUTH_READY),
        "codex_exec_ready": bool(UPGRADE_CODEX_EXEC_READY) and not bool(exec_error),
        "codex_exec_error": exec_error or None,
        "pending_count": len(list(UPGRADE_PENDING_DIR.glob("*.json"))),
        "history_count": len(list(UPGRADE_HISTORY_DIR.glob("*.json"))),
        "rejected_count": len(list(UPGRADE_REJECTED_DIR.glob("*.json"))),
    }


@app.get("/history")
async def history(limit: int = Query(default=20)):
    return {"status": "ok", "rows": latest_timeline_records(limit=max(1, min(limit, 200)))}


@app.get("/latest")
async def latest():
    return {"status": "ok", "row": load_latest_proposal()}


@app.get("/logs")
async def logs(lines: int = Query(default=200)):
    if not AGENT_LOG_FILE.exists():
        return {"status": "missing", "path": str(AGENT_LOG_FILE), "output": ""}
    try:
        all_lines = AGENT_LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        tail = "\n".join(all_lines[-max(10, min(lines, 1000)):])
        return {"status": "ok", "path": str(AGENT_LOG_FILE), "output": tail}
    except Exception as exc:
        return {"status": "error", "path": str(AGENT_LOG_FILE), "detail": format_exc(exc), "output": ""}


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    if not SELF_UPGRADE_ENABLED:
        return {"status": "disabled", "detail": "SELF_UPGRADE_ENABLED=false"}
    file_paths = service_file_hints(req.service, req.file_hints)
    file_contexts = collect_file_contexts(file_paths)
    try:
        raw, meta = await call_codex_json(build_error_analysis_messages(req, file_contexts))
        proposal = normalize_proposal(
            raw,
            service=req.service,
            trigger_kind=req.trigger_kind,
            error_excerpt=req.error_excerpt,
            log_path=req.log_path,
            file_contexts=file_contexts,
            initiative_goal=req.initiative_goal,
        )
        proposal["provider_meta"] = meta
        persist_pending_proposal(proposal)
        agent_log(f"proposal generated id={proposal['proposal_id']} service={req.service} risk={proposal['risk']} confidence={proposal['confidence']}")
        auto_apply_result = None
        if feature_enabled("LUMINAX_AUTO_APPLY_ENABLED") and req.apply_if_safe and proposal.get("auto_apply_eligible") and control_granted():
            auto_apply_result = await apply_proposal_internal(proposal, allow_medium_risk=False, auto_triggered=True)
            agent_log(f"auto-apply attempted id={proposal['proposal_id']} status={auto_apply_result.get('status')}")
        return {"status": "ok", "proposal": proposal, "auto_apply_result": auto_apply_result}
    except Exception as exc:
        agent_log(f"proposal generation failed service={req.service} detail={format_exc(exc)}")
        return {
            "status": "error",
            "error_excerpt": req.error_excerpt[:12000],
            "root_cause": f"Codex proposal generation failed: {format_exc(exc)}",
            "proposed_patch": "",
            "confidence": 0,
            "risk": "high",
        }


@app.post("/capability/propose")
async def capability_propose(req: CapabilityProposalRequest):
    if not SELF_UPGRADE_ENABLED:
        return {"status": "disabled", "detail": "SELF_UPGRADE_ENABLED=false"}
    file_paths = service_file_hints(req.target_service, req.file_hints)
    file_contexts = collect_file_contexts(file_paths)
    try:
        raw, meta = await call_codex_json(build_capability_messages(req, file_contexts))
        proposal = normalize_proposal(
            raw,
            service=req.target_service,
            trigger_kind="capability",
            error_excerpt=f"Capability request: {req.capability_name}\nObjective:\n{req.objective}",
            log_path="capability://request",
            file_contexts=file_contexts,
            initiative_goal=req.objective,
        )
        proposal["capability_name"] = req.capability_name
        proposal["provider_meta"] = meta
        persist_pending_proposal(proposal)
        agent_log(f"capability proposal generated id={proposal['proposal_id']} service={req.target_service} capability={req.capability_name}")
        return {"status": "ok", "proposal": proposal}
    except Exception as exc:
        agent_log(f"capability proposal failed target={req.target_service} detail={format_exc(exc)}")
        return {"status": "error", "detail": format_exc(exc)}


@app.post("/proposal/apply")
async def proposal_apply(req: ProposalApplyRequest):
    validate_apply_control_or_403(req.token)
    proposal = load_proposal_by_id(req.proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    result = await apply_proposal_internal(proposal, allow_medium_risk=bool(req.allow_medium_risk), auto_triggered=False)
    agent_log(f"manual apply requested id={req.proposal_id} status={result.get('status')}")
    return result


install_runtime_guard(app, 'lumi-upgrade-agent', require_auth=True, required_feature='SELF_UPGRADE_ENABLED')
