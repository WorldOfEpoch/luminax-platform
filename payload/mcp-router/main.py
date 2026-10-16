from __future__ import annotations
from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="MCP Router")

PROJECT_ROOT = Path(os.getenv("LUMINAX_PROJECT_ROOT", "/workspace/project")).resolve()
RUNTIME_ROOT = Path(os.getenv("LUMINAX_PC_RUNTIME_ROOT", "/data/runtime/mcp-router")).resolve()
BRAIN_URL = os.getenv("LUMINAX_BRAIN_URL", "http://luminax-brain:8501").rstrip("/")
ASSISTANT_CORE_URL = os.getenv("ASSISTANT_CORE_URL", "http://assistant-core:8000").rstrip("/")
CONTROL_TOKEN = os.getenv("CONTROL_TOKEN", "")
SERVER_PUBLIC_IP = os.getenv("LUMINAX_SERVER_PUBLIC_IP", "").strip()
SERVER_SSH_USER = os.getenv("LUMINAX_SERVER_SSH_USER", os.getenv("LUMINAX_USERNAME", "")).strip()
SERVER_SSH_KEY = Path(os.getenv("LUMINAX_SERVER_SSH_KEY") or os.getenv("LUMINAX_KEY_PATH")).expanduser() if (os.getenv("LUMINAX_SERVER_SSH_KEY") or os.getenv("LUMINAX_KEY_PATH")) else None
CODEX_CLI_MODEL = os.getenv("CODEX_CLI_MODEL", "gpt-5.5").strip() or "gpt-5.5"
CODEX_CLI_REASONING_EFFORT = os.getenv("CODEX_CLI_REASONING_EFFORT", "xhigh").strip() or "xhigh"

READABLE_ROOTS = [PROJECT_ROOT, RUNTIME_ROOT]
BRAIN_MEMORY_ROOT = Path(os.getenv("LUMINAX_BRAIN_MEMORY_ROOT", str(RUNTIME_ROOT / "memory" / "luminax-brain"))).resolve()

TRUST_ZONES = {
    "owner-control",
    "pc-companion",
    "dev-control",
    "home-read",
    "home-write",
    "camera-security",
    "browser-use",
    "public-external",
}

ACTION_CLASSES = {
    "safe_auto",
    "checkpoint_required",
    "sentinel_blocked",
    "owner_confirmed",
}

TOOL_REGISTRY = [
    {
        "tool_name": "luminax.self.status",
        "tool_domain": "dev-tools",
        "trust_zone": "pc-companion",
        "action_class": "safe_auto",
        "target_service": "luminax-brain",
        "description": "Read Luminax self-state, affect, and continuity status.",
    },
    {
        "tool_name": "luminax.self.update",
        "tool_domain": "dev-tools",
        "trust_zone": "pc-companion",
        "action_class": "safe_auto",
        "target_service": "luminax-brain",
        "description": "Update bounded self-state/affect metadata.",
    },
    {
        "tool_name": "luminax.goals.list",
        "tool_domain": "dev-tools",
        "trust_zone": "dev-control",
        "action_class": "safe_auto",
        "target_service": "luminax-brain",
        "description": "List persisted goals.",
    },
    {
        "tool_name": "luminax.goals.upsert",
        "tool_domain": "dev-tools",
        "trust_zone": "dev-control",
        "action_class": "safe_auto",
        "target_service": "luminax-brain",
        "description": "Create or update a persisted goal.",
    },
    {
        "tool_name": "luminax.continuity.resume",
        "tool_domain": "dev-tools",
        "trust_zone": "dev-control",
        "action_class": "safe_auto",
        "target_service": "luminax-brain",
        "description": "Resume a continuity session.",
    },
    {
        "tool_name": "luminax.memory.upsert",
        "tool_domain": "dev-tools",
        "trust_zone": "pc-companion",
        "action_class": "safe_auto",
        "target_service": "luminax-brain",
        "description": "Persist edge/server/shared memory.",
    },
    {
        "tool_name": "luminax.memory.search",
        "tool_domain": "dev-tools",
        "trust_zone": "pc-companion",
        "action_class": "safe_auto",
        "target_service": "luminax-brain",
        "description": "Search persisted Luminax memory.",
    },
    {
        "tool_name": "luminax.memory.sync_server",
        "tool_domain": "dev-tools",
        "trust_zone": "pc-companion",
        "action_class": "safe_auto",
        "target_service": "assistant-core.memory",
        "description": "Mirror edge brain JSON memory into the server memory store.",
    },
    {
        "tool_name": "luminax.tasks.list",
        "tool_domain": "dev-tools",
        "trust_zone": "pc-companion",
        "action_class": "safe_auto",
        "target_service": "luminax-brain",
        "description": "List active and historical Luminax tasks.",
    },
    {
        "tool_name": "luminax.tasks.upsert",
        "tool_domain": "dev-tools",
        "trust_zone": "pc-companion",
        "action_class": "safe_auto",
        "target_service": "luminax-brain",
        "description": "Create or update a Luminax task.",
    },
    {
        "tool_name": "luminax.delegations.list",
        "tool_domain": "dev-tools",
        "trust_zone": "pc-companion",
        "action_class": "safe_auto",
        "target_service": "luminax-brain",
        "description": "List recorded agent delegations.",
    },
    {
        "tool_name": "pc.status.inspect",
        "tool_domain": "dev-tools",
        "trust_zone": "pc-companion",
        "action_class": "safe_auto",
        "target_service": "pc-edge",
        "description": "Inspect local PC resources and Luminax edge process health.",
    },
    {
        "tool_name": "server.docker.status",
        "tool_domain": "admin-ops",
        "trust_zone": "dev-control",
        "action_class": "safe_auto",
        "target_service": "server-ssh",
        "description": "Inspect server Docker containers through SSH.",
    },
    {
        "tool_name": "olympus.stack.status",
        "tool_domain": "admin-ops",
        "trust_zone": "dev-control",
        "action_class": "safe_auto",
        "target_service": "pc-edge+server-ssh",
        "description": "Inspect Olympus/Luminax local services, private control, server containers, and bot containers.",
    },
    {
        "tool_name": "codex.task.run",
        "tool_domain": "dev-tools",
        "trust_zone": "dev-control",
        "action_class": "checkpoint_required",
        "target_service": "assistant-core.codex-bridge",
        "description": "Delegate a bounded coding or repair task to Codex bridge.",
    },
    {
        "tool_name": "shell.powershell.run",
        "tool_domain": "dev-tools",
        "trust_zone": "dev-control",
        "action_class": "checkpoint_required",
        "target_service": "pc-shell",
        "description": "Run a PowerShell command on the local PC.",
    },
    {
        "tool_name": "files.list",
        "tool_domain": "dev-tools",
        "trust_zone": "dev-control",
        "action_class": "safe_auto",
        "target_service": "pc-filesystem",
        "description": "List files under an allowed local workspace root.",
    },
    {
        "tool_name": "files.read",
        "tool_domain": "dev-tools",
        "trust_zone": "dev-control",
        "action_class": "safe_auto",
        "target_service": "pc-filesystem",
        "description": "Read a text file under an allowed local workspace root.",
    },
    {
        "tool_name": "files.write",
        "tool_domain": "dev-tools",
        "trust_zone": "dev-control",
        "action_class": "checkpoint_required",
        "target_service": "pc-filesystem",
        "description": "Write a text file under an allowed local workspace root.",
    },
    {
        "tool_name": "affect.resolve",
        "tool_domain": "browser-tools",
        "trust_zone": "pc-companion",
        "action_class": "safe_auto",
        "target_service": "affect-controller",
        "description": "Resolve bounded affect hints for voice output.",
    },
    {
        "tool_name": "voice.style",
        "tool_domain": "browser-tools",
        "trust_zone": "pc-companion",
        "action_class": "safe_auto",
        "target_service": "affect-controller",
        "description": "Resolve bounded voice style hints.",
    },
    {
        "tool_name": "browser.read.navigate",
        "tool_domain": "browser-tools",
        "trust_zone": "browser-use",
        "action_class": "safe_auto",
        "target_service": "browser-agent",
        "description": "Navigate for read-only browser tasks.",
    },
    {
        "tool_name": "browser.write.submit",
        "tool_domain": "browser-tools",
        "trust_zone": "browser-use",
        "action_class": "checkpoint_required",
        "target_service": "browser-agent",
        "description": "Submit or mutate browser state.",
    },
    {
        "tool_name": "home.state.list",
        "tool_domain": "home-read",
        "trust_zone": "home-read",
        "action_class": "safe_auto",
        "target_service": "network-manager",
        "description": "List home entities, rooms, or groups.",
    },
    {
        "tool_name": "home.device.command",
        "tool_domain": "home-write",
        "trust_zone": "home-write",
        "action_class": "checkpoint_required",
        "target_service": "network-manager",
        "description": "Issue a bounded non-sentinel home control command.",
    },
    {
        "tool_name": "camera.export.clip",
        "tool_domain": "camera-security",
        "trust_zone": "camera-security",
        "action_class": "sentinel_blocked",
        "target_service": "frigate",
        "description": "Export or share camera media.",
    },
    {
        "tool_name": "admin.deploy.production",
        "tool_domain": "admin-ops",
        "trust_zone": "public-external",
        "action_class": "sentinel_blocked",
        "target_service": "admin-api",
        "description": "Perform a production deploy or public exposure change.",
    },
]

SENTINEL_TOKENS = (
    "purchase",
    "buy",
    "lock",
    "unlock",
    "alarm",
    "credential",
    "secret",
    "password",
    "public_exposure",
    "port forward",
    "camera_export",
    "camera_share",
    "delete",
    "remove-item",
    "format",
    "wipe",
    "prod_deploy",
    "production deploy",
)

MUTATING_TOOLS = {
    "luminax.goals.upsert",
    "luminax.self.update",
    "luminax.memory.upsert",
    "luminax.memory.sync_server",
    "luminax.tasks.upsert",
    "codex.task.run",
    "shell.powershell.run",
    "files.write",
    "browser.write.submit",
    "home.device.command",
    "camera.export.clip",
    "admin.deploy.production",
}

LOCAL_STATUS_ENDPOINTS = (
    ("assistant-core", "http://127.0.0.1:18000/api/ping"),
    ("admin-api", "http://127.0.0.1:18040/routes/catalog"),
    ("realtime-gateway", "http://127.0.0.1:18050/session/info"),
    ("voice-agent", "http://127.0.0.1:8030/health"),
    ("edge-bridge", "http://127.0.0.1:8150/health"),
    ("luminax-brain", "http://127.0.0.1:8501/health"),
    ("mcp-router", "http://127.0.0.1:8503/health"),
    ("langgraph-orchestrator", "http://127.0.0.1:8504/health"),
    ("affect-controller", "http://127.0.0.1:8502/health"),
    ("ollama", "http://127.0.0.1:11434/api/tags"),
)

BOT_SERVICE_HINTS = ("discord-bot", "discord-ops-bot", "zeus-discord", "community-discord")


class PolicyCheckRequest(BaseModel):
    tool_name: str
    caller_zone: str = "pc-companion"
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolInvokeRequest(PolicyCheckRequest):
    dry_run: bool = True
    checkpoint_granted: bool = False


def stable_hash(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def trim(value: Any, limit: int = 800) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def get_tool(tool_name: str) -> dict[str, Any] | None:
    for tool in TOOL_REGISTRY:
        if tool["tool_name"] == tool_name:
            return tool
    return None


def flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(flatten_text(item) for item in value)
    return str(value)


def is_sentinel_payload(tool_name: str, arguments: dict[str, Any]) -> bool:
    normalized = f"{tool_name} {flatten_text(arguments)}".lower()
    return any(token in normalized for token in SENTINEL_TOKENS)


def evaluate_policy(tool_name: str, caller_zone: str, arguments: dict[str, Any]) -> dict[str, Any]:
    tool = get_tool(tool_name)
    if not tool:
        return {"allowed": False, "requires_checkpoint": False, "reason": "tool_not_found", "tool_name": tool_name}
    zone = (caller_zone or "pc-companion").strip().lower()
    if zone not in TRUST_ZONES:
        return {"allowed": False, "requires_checkpoint": False, "reason": "invalid_caller_zone", "tool_name": tool_name}

    owner_mode = zone == "owner-control"
    sentinel = tool["action_class"] == "sentinel_blocked" or is_sentinel_payload(tool_name, arguments)
    if owner_mode:
        return {
            "allowed": True,
            "requires_checkpoint": bool(sentinel),
            "reason": "owner_checkpoint_required" if sentinel else "owner_full_access",
            "tool_name": tool_name,
            "caller_zone": zone,
            "tool": tool,
        }

    if zone != tool["trust_zone"]:
        return {
            "allowed": False,
            "requires_checkpoint": False,
            "reason": "wrong_trust_zone",
            "tool_name": tool_name,
            "caller_zone": zone,
            "required_zone": tool["trust_zone"],
        }
    if sentinel:
        return {"allowed": False, "requires_checkpoint": False, "reason": "sentinel_blocked", "tool_name": tool_name, "caller_zone": zone}
    if tool["action_class"] == "checkpoint_required":
        return {
            "allowed": True,
            "requires_checkpoint": True,
            "reason": "checkpoint_required",
            "tool_name": tool_name,
            "caller_zone": zone,
            "tool": tool,
        }
    return {
        "allowed": True,
        "requires_checkpoint": False,
        "reason": "safe_auto",
        "tool_name": tool_name,
        "caller_zone": zone,
        "tool": tool,
    }


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method.upper(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw.strip() else {}
            return parsed if isinstance(parsed, dict) else {"raw": parsed}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=exc.code, detail=trim(detail, 1200)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{url} unavailable: {trim(exc, 400)}") from exc


def run_process(args: list[str], cwd: str | None = None, timeout: float = 30.0, stdout_limit: int = 6000, stderr_limit: int = 6000) -> dict[str, Any]:
    require_feature("LUMINAX_HOST_EXECUTION_ENABLED")
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "stdout": trim(completed.stdout, stdout_limit),
            "stderr": trim(completed.stderr, stderr_limit),
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "stdout": "",
            "stderr": trim(exc, 2000),
        }


def probe_http(name: str, url: str, timeout: float = 4.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read(4096).decode("utf-8", errors="replace")
            status = "ok" if response.status < 400 else "degraded"
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and str(parsed.get("status") or "").lower() in {"down", "failed", "error"}:
                    status = "down"
            except Exception:
                pass
            return {"name": name, "url": url, "status": status, "code": response.status, "elapsed_ms": int((time.perf_counter() - started) * 1000)}
    except Exception as exc:
        return {"name": name, "url": url, "status": "down", "elapsed_ms": int((time.perf_counter() - started) * 1000), "detail": trim(exc, 240)}


def resolve_allowed_path(path_value: str) -> Path:
    raw = str(path_value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="path is required")
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve()
    for root in READABLE_ROOTS:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise HTTPException(status_code=403, detail=f"path is outside allowed roots: {resolved}")


def parse_docker_rows(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                rows.append(parsed)
                continue
        except Exception:
            pass
        rows.append({"raw": line})
    return rows


def container_state(row: dict[str, Any]) -> str:
    state = str(row.get("State") or "").strip().lower()
    status = str(row.get("Status") or "").strip().lower()
    if state == "running" or status.startswith("up "):
        return "running"
    if state == "exited" or "exited" in status:
        return "stopped"
    return state or "unknown"


def server_docker_status() -> dict[str, Any]:
    if not SERVER_PUBLIC_IP or not SERVER_SSH_USER or SERVER_SSH_KEY is None or not SERVER_SSH_KEY.is_file():
        return {"status": "down", "detail": f"SSH key missing: {SERVER_SSH_KEY}", "containers": []}
    result = run_process(
        [
            "ssh",
            "-i",
            str(SERVER_SSH_KEY),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=6",
            f"{SERVER_SSH_USER}@{SERVER_PUBLIC_IP}",
            "docker ps -a --format '{{json .}}'",
        ],
        timeout=28,
        stdout_limit=160000,
        stderr_limit=12000,
    )
    if not result.get("ok"):
        return {"status": "down", "target": f"{SERVER_SSH_USER}@{SERVER_PUBLIC_IP}", "detail": result.get("stderr"), "containers": []}
    containers = parse_docker_rows(str(result.get("stdout") or ""))
    return {"status": "ok", "target": f"{SERVER_SSH_USER}@{SERVER_PUBLIC_IP}", "containers": containers}


def sync_brain_memory_to_server() -> dict[str, Any]:
    if not SERVER_PUBLIC_IP or not SERVER_SSH_USER or SERVER_SSH_KEY is None or not SERVER_SSH_KEY.is_file():
        return {"status": "down", "detail": f"SSH key missing: {SERVER_SSH_KEY}"}
    if not BRAIN_MEMORY_ROOT.exists():
        return {"status": "down", "detail": f"Brain memory root missing: {BRAIN_MEMORY_ROOT}"}
    files = [path for path in BRAIN_MEMORY_ROOT.glob("*.json") if path.is_file()]
    if not files:
        return {"status": "ok", "synced": 0, "detail": "No JSON brain files to sync yet."}
    mkdir = run_process(
        [
            "ssh",
            "-i",
            str(SERVER_SSH_KEY),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=6",
            f"{SERVER_SSH_USER}@{SERVER_PUBLIC_IP}",
            "docker exec luminax-assistant-core-1 mkdir -p /data/memory/luminax-brain-edge",
        ],
        timeout=20,
    )
    if not mkdir.get("ok"):
        return {"status": "down", "detail": mkdir.get("stderr") or "Failed to create server mirror directory."}
    synced: list[str] = []
    failed: list[dict[str, Any]] = []
    for path in files:
        remote_tmp = f"/tmp/luminax-brain-{path.name}"
        scp = run_process(
            [
                "scp",
                "-i",
                str(SERVER_SSH_KEY),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=accept-new",
                str(path),
                f"{SERVER_SSH_USER}@{SERVER_PUBLIC_IP}:{remote_tmp}",
            ],
            timeout=30,
        )
        if not scp.get("ok"):
            failed.append({"file": path.name, "stage": "scp", "detail": scp.get("stderr")})
            continue
        cp = run_process(
            [
                "ssh",
                "-i",
                str(SERVER_SSH_KEY),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "ConnectTimeout=6",
                f"{SERVER_SSH_USER}@{SERVER_PUBLIC_IP}",
                f"docker cp {remote_tmp} luminax-assistant-core-1:/data/memory/luminax-brain-edge/{path.name} && rm -f {remote_tmp}",
            ],
            timeout=30,
        )
        if cp.get("ok"):
            synced.append(path.name)
        else:
            failed.append({"file": path.name, "stage": "docker_cp", "detail": cp.get("stderr")})
    return {
        "status": "ok" if not failed else "degraded",
        "server_path": "/data/memory/luminax-brain-edge",
        "synced": len(synced),
        "files": synced,
        "failed": failed,
    }


def pc_status() -> dict[str, Any]:
    ps = (
        "$os=Get-CimInstance Win32_OperatingSystem;"
        "$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1;"
        "$memTotal=[math]::Round($os.TotalVisibleMemorySize/1MB,1);"
        "$memFree=[math]::Round($os.FreePhysicalMemory/1MB,1);"
        "$procs=@('dotnet','python','ollama','ssh') | ForEach-Object {"
        "  $p=Get-Process -Name $_ -ErrorAction SilentlyContinue;"
        "  [pscustomobject]@{name=$_;count=@($p).Count}"
        "};"
        "[pscustomobject]@{computer=$env:COMPUTERNAME;cpu=$cpu.Name;load=$cpu.LoadPercentage;memory_total_gb=$memTotal;memory_free_gb=$memFree;processes=$procs} | ConvertTo-Json -Depth 4"
    )
    result = run_process(["powershell.exe", "-NoProfile", "-Command", ps], timeout=12)
    if not result.get("ok"):
        return {"status": "down", "detail": result.get("stderr")}
    try:
        parsed = json.loads(str(result.get("stdout") or "{}"))
        return {"status": "ok", **parsed}
    except Exception as exc:
        return {"status": "degraded", "detail": trim(exc), "raw": result.get("stdout")}


def olympus_stack_status() -> dict[str, Any]:
    local_rows = [probe_http(name, url, timeout=8.0 if name == "edge-bridge" else 4.0) for name, url in LOCAL_STATUS_ENDPOINTS]
    server = server_docker_status()
    containers = server.get("containers") if isinstance(server.get("containers"), list) else []
    running = [row for row in containers if container_state(row) == "running"]
    stopped = [row for row in containers if container_state(row) != "running"]
    bot_rows = []
    for row in containers:
        name = str(row.get("Names") or row.get("Name") or row.get("raw") or "").lower()
        image = str(row.get("Image") or "").lower()
        if any(hint in name or hint in image for hint in BOT_SERVICE_HINTS):
            bot_rows.append(row)
    local_bad = [row for row in local_rows if row.get("status") not in {"ok", "skipped", "disabled"}]
    summary = (
        f"Local services healthy: {len(local_rows) - len(local_bad)} of {len(local_rows)}. "
        f"Server containers running: {len(running)}; stopped: {len(stopped)}. "
        f"Bot containers running: {len([row for row in bot_rows if container_state(row) == 'running'])} of {len(bot_rows)}."
    )
    return {
        "status": "ok" if not local_bad and server.get("status") == "ok" else "degraded",
        "summary": summary,
        "local": local_rows,
        "server": server,
        "bot_containers": bot_rows,
    }


def invoke_tool(tool_name: str, arguments: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    if dry_run and tool_name in MUTATING_TOOLS:
        return {"status": "planned", "accepted_arguments": arguments, "dry_run": True}

    if tool_name == "luminax.self.status":
        return http_json("GET", f"{BRAIN_URL}/self/status")
    if tool_name == "luminax.self.update":
        return http_json("POST", f"{BRAIN_URL}/self/update", arguments)
    if tool_name == "luminax.goals.list":
        return http_json("GET", f"{BRAIN_URL}/goals/list")
    if tool_name == "luminax.goals.upsert":
        return http_json("POST", f"{BRAIN_URL}/goals/upsert", arguments)
    if tool_name == "luminax.continuity.resume":
        return http_json("POST", f"{BRAIN_URL}/continuity/resume", arguments)
    if tool_name == "luminax.memory.upsert":
        return http_json("POST", f"{BRAIN_URL}/memory/upsert", arguments)
    if tool_name == "luminax.memory.search":
        return http_json("POST", f"{BRAIN_URL}/memory/search", arguments)
    if tool_name == "luminax.memory.sync_server":
        return sync_brain_memory_to_server()
    if tool_name == "luminax.tasks.list":
        suffix = ""
        if arguments:
            suffix = "?" + urllib.parse.urlencode({k: v for k, v in arguments.items() if v is not None})
        return http_json("GET", f"{BRAIN_URL}/tasks/list{suffix}")
    if tool_name == "luminax.tasks.upsert":
        return http_json("POST", f"{BRAIN_URL}/tasks/upsert", arguments)
    if tool_name == "luminax.delegations.list":
        return http_json("GET", f"{BRAIN_URL}/delegations/list")
    if tool_name == "pc.status.inspect":
        return pc_status()
    if tool_name == "server.docker.status":
        return server_docker_status()
    if tool_name == "olympus.stack.status":
        return olympus_stack_status()
    if tool_name == "files.list":
        root = resolve_allowed_path(str(arguments.get("path") or PROJECT_ROOT))
        pattern = str(arguments.get("pattern") or "*")
        recursive = bool(arguments.get("recursive", False))
        files = list(root.rglob(pattern) if recursive else root.glob(pattern)) if root.is_dir() else [root]
        return {"status": "ok", "path": str(root), "files": [str(path) for path in files[:500]], "count": len(files)}
    if tool_name == "files.read":
        path = resolve_allowed_path(str(arguments.get("path") or ""))
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {path}")
        text = path.read_text(encoding=str(arguments.get("encoding") or "utf-8"), errors="replace")
        return {"status": "ok", "path": str(path), "content": text[: int(arguments.get("max_chars") or 12000)]}
    if tool_name == "files.write":
        path = resolve_allowed_path(str(arguments.get("path") or ""))
        content = str(arguments.get("content") or "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=str(arguments.get("encoding") or "utf-8"))
        return {"status": "ok", "path": str(path), "bytes": len(content.encode("utf-8"))}
    if tool_name == "shell.powershell.run":
        command = str(arguments.get("command") or "").strip()
        if not command:
            raise HTTPException(status_code=400, detail="command is required")
        cwd = str(arguments.get("cwd") or PROJECT_ROOT)
        timeout = max(1, min(float(arguments.get("timeout_sec") or 60), 600))
        return run_process(["powershell.exe", "-NoProfile", "-Command", command], cwd=cwd, timeout=timeout)
    if tool_name == "codex.task.run":
        if not CONTROL_TOKEN:
            raise HTTPException(status_code=500, detail="CONTROL_TOKEN is not configured for Codex delegation.")
        goal = str(arguments.get("goal") or arguments.get("prompt") or "").strip()
        if not goal:
            raise HTTPException(status_code=400, detail="goal is required")
        task_payload = {
            "title": trim(goal, 180),
            "status": "delegated",
            "priority": int(arguments.get("priority") or 40),
            "requested_by": "luminax",
            "capability": "codex.task.run",
            "target": str(arguments.get("workdir") or PROJECT_ROOT),
            "summary": goal,
            "metadata": {
                "model": str(arguments.get("model") or CODEX_CLI_MODEL),
                "reasoning_effort": str(arguments.get("reasoning_effort") or CODEX_CLI_REASONING_EFFORT),
            },
        }
        task = http_json("POST", f"{BRAIN_URL}/tasks/upsert", task_payload)
        bridge_payload = {
            "token": CONTROL_TOKEN,
            "goal": goal,
            "project_key": arguments.get("project_key") or "",
            "mode": arguments.get("mode") or "project",
            "scope": arguments.get("scope") or "implement",
            "workdir": str(arguments.get("workdir") or PROJECT_ROOT),
            "allow_write": bool(arguments.get("allow_write", True)),
            "update_task_status": True,
            "model": str(arguments.get("model") or CODEX_CLI_MODEL),
            "service_key": arguments.get("service_key") or "",
            "target_lane": arguments.get("target_lane") or "",
        }
        result = http_json("POST", f"{ASSISTANT_CORE_URL}/api/codex/bridge/run", bridge_payload, timeout=360)
        http_json(
            "POST",
            f"{BRAIN_URL}/delegations/record",
            {
                "task_id": ((task.get("task") or {}) if isinstance(task, dict) else {}).get("task_id"),
                "agent": "codex",
                "model": bridge_payload["model"],
                "reasoning_effort": str(arguments.get("reasoning_effort") or CODEX_CLI_REASONING_EFFORT),
                "goal": goal,
                "status": str(result.get("status") or result.get("outcome") or "recorded"),
                "route": f"{ASSISTANT_CORE_URL}/api/codex/bridge/run",
                "metadata": {"bridge_result": result},
            },
            timeout=15,
        )
        return result

    if tool_name == "affect.resolve":
        return {"resolved": True, "requested_mood": arguments.get("mood", "neutral")}
    if tool_name == "voice.style":
        return {"style": "steady_balanced", "requested_context": arguments.get("context", "default")}
    if tool_name == "home.state.list":
        return {"status": "not_configured", "rooms": [], "devices": [], "detail": "Home Assistant/MQTT adapter is not configured yet."}
    return {"status": "accepted", "accepted_arguments": arguments}


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "mcp-router",
        "tool_count": len(TOOL_REGISTRY),
        "trust_zones": sorted(TRUST_ZONES),
        "action_classes": sorted(ACTION_CLASSES),
        "owner_mode": "available_on_localhost",
        "codex_model": CODEX_CLI_MODEL,
        "codex_reasoning_effort": CODEX_CLI_REASONING_EFFORT,
    }


@app.get("/tools/list")
def tools_list() -> dict[str, Any]:
    return {"tools": TOOL_REGISTRY, "count": len(TOOL_REGISTRY)}


@app.post("/policy/check")
def policy_check(request: PolicyCheckRequest) -> dict[str, Any]:
    decision = evaluate_policy(request.tool_name, request.caller_zone, request.arguments)
    return {"status": "ok", "decision": decision}


@app.post("/tools/invoke")
def tools_invoke(request: ToolInvokeRequest) -> dict[str, Any]:
    decision = evaluate_policy(request.tool_name, request.caller_zone, request.arguments)
    if decision["reason"] == "tool_not_found":
        raise HTTPException(status_code=404, detail=decision)
    if decision["reason"] in {"invalid_caller_zone", "wrong_trust_zone"}:
        raise HTTPException(status_code=403, detail=decision)
    if decision["reason"] == "sentinel_blocked":
        raise HTTPException(status_code=403, detail=decision)
    if decision.get("requires_checkpoint") and not (request.checkpoint_granted or request.dry_run):
        raise HTTPException(status_code=409, detail={**decision, "reason": "checkpoint_required_before_execution"})

    receipt = {
        "invocation_id": f"invoke-{stable_hash({'tool': request.tool_name, 'arguments': request.arguments, 'dry_run': request.dry_run})}",
        "tool_name": request.tool_name,
        "dry_run": request.dry_run,
        "caller_zone": request.caller_zone,
        "action_class": decision.get("tool", {}).get("action_class", ""),
        "target_service": decision.get("tool", {}).get("target_service", ""),
        "created_at": int(time.time()),
    }
    result = invoke_tool(request.tool_name, request.arguments, dry_run=request.dry_run)
    try:
        http_json(
            "POST",
            f"{BRAIN_URL}/receipts/record",
            {
                "capability": request.tool_name,
                "status": str(result.get("status") or "ok") if isinstance(result, dict) else "ok",
                "summary": trim((result or {}).get("summary") if isinstance(result, dict) else "", 500),
                "evidence": {"receipt": receipt, "policy": decision},
                "metadata": {"caller_zone": request.caller_zone},
            },
            timeout=8,
        )
    except Exception:
        pass
    return {"status": "accepted", "policy": decision, "receipt": receipt, "result": result}


install_runtime_guard(app, 'mcp-router', require_auth=True)
