from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import contextvars, json, os, socket, subprocess, sys, time, uuid
from datetime import datetime, timezone
import mysql.connector, psutil, redis
import shutil
from pathlib import Path
from typing import Any
import httpx
from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

app = FastAPI(title="Luminax Admin API")
web_auth_user_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("web_auth_user", default="")

@app.middleware("http")
async def capture_web_auth_user(request: Request, call_next):
    username = (request.headers.get("X-Web-Auth-User", "") or "").strip().lower()
    token = web_auth_user_ctx.set(username)
    try:
        return await call_next(request)
    finally:
        web_auth_user_ctx.reset(token)

CONTROL_TOKEN = os.getenv("CONTROL_TOKEN", "")
TRUSTED_WEB_CONTROL_USER = os.getenv("TRUSTED_WEB_CONTROL_USER", "").strip().lower()
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
MYSQL_HOST = os.getenv("MYSQL_HOST", "mysql")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "luminax")
MYSQL_USER = os.getenv("MYSQL_USER", "")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
WINDOWS_NODE_STALE_SEC = int(os.getenv("WINDOWS_NODE_STALE_SEC", "150"))
CORE_API_URL = os.getenv("CORE_API_URL", "http://assistant-core:8000/api").rstrip("/")
UPGRADE_API_URL = os.getenv("UPGRADE_API_URL", "http://lumi-upgrade-agent:8130").rstrip("/")
BROWSER_AGENT_URL = os.getenv("BROWSER_AGENT_URL", "http://browser-agent:8010").rstrip("/")
VISION_AGENT_URL = os.getenv("VISION_AGENT_URL", os.getenv("EDGE_VISION_URL", "http://vision-agent:8020")).rstrip("/")
VOICE_AGENT_URL_DEFAULT = "http://voice-agent:8030"
VOICE_AGENT_URL_ENV = (os.getenv("VOICE_AGENT_URL", "") or "").strip()
EDGE_VOICE_URL_ENV = (os.getenv("EDGE_VOICE_URL", "") or "").strip()
VOICE_AGENT_URL = (VOICE_AGENT_URL_ENV or EDGE_VOICE_URL_ENV or VOICE_AGENT_URL_DEFAULT).rstrip("/")
UNDERFOOT_VOICE_GATEWAY_URL = os.getenv("UNDERFOOT_VOICE_GATEWAY_URL", "http://underfoot-voice-gateway:8470").rstrip("/")
UNDERFOOT_WORLD_URL = os.getenv("UNDERFOOT_WORLD_URL", "http://underfoot-world:8460").rstrip("/")
UNDERFOOT_VOICE_TURN_HOST = os.getenv("UNDERFOOT_VOICE_TURN_HOST", "underfoot-voice-turn").strip() or "underfoot-voice-turn"
UNDERFOOT_VOICE_TURN_PORT = int(os.getenv("UNDERFOOT_VOICE_TURN_PORT", "3478"))
UNDERFOOT_VOICE_TURN_TLS_PORT = int(os.getenv("UNDERFOOT_VOICE_TURN_TLS_PORT", "5349"))
EDGE_BRIDGE_URL = os.getenv("EDGE_BRIDGE_URL", "http://olympus-edge-bridge:8150").rstrip("/")
LUMINAX_BRAIN_URL = os.getenv("LUMINAX_BRAIN_URL", f"{EDGE_BRIDGE_URL}/luminax-brain").rstrip("/")
AFFECT_CONTROLLER_URL = os.getenv("AFFECT_CONTROLLER_URL", f"{EDGE_BRIDGE_URL}/affect-controller").rstrip("/")
MCP_ROUTER_URL = os.getenv("MCP_ROUTER_URL", "http://mcp-router:8162").rstrip("/")
LANGGRAPH_URL = os.getenv("LANGGRAPH_URL", "http://langgraph-orchestrator:8163").rstrip("/")
WINDOWS_EDGE_NODE_KEY = (os.getenv("WINDOWS_EDGE_NODE_KEY", "olympus-edge-gpu") or "olympus-edge-gpu").strip()
MOUNT_OLYMPUS_UI_URL = os.getenv("MOUNT_OLYMPUS_UI_URL", "https://luminax-ui").rstrip("/")
BACKUP_ROOT = Path("/host-opt")
BACKUP_PREFIX = "luminax_backup_"
MAINTENANCE_MODE_PATH = Path("/workspace/luminax/config/maintenance-mode.json")
RUNTIME_POLICY_PATH = Path("/workspace/luminax/config/runtime-policy.json")
OLYMPUS_IDENTITY_PATH = Path("/workspace/luminax/config/olympus-identity.json")
WORKSPACE_REGISTRY_PATH = Path("/workspace/luminax/config/workspace-registry.json")
WORKSPACE_ATLAS_ROOT = Path(os.getenv("WORKSPACE_ATLAS_ROOT", "/workspace/project-p"))
WORKSPACE_ATLAS_MD_PATH = Path(os.getenv("WORKSPACE_ATLAS_MD_PATH", "/workspace/project-p/WORKSPACE_ATLAS.md"))
WORKSPACE_ATLAS_JSON_PATH = Path(os.getenv("WORKSPACE_ATLAS_JSON_PATH", "/workspace/luminax/config/workspace-atlas.json"))
WORKSPACE_CONNECTIONS_PATH = Path(os.getenv("WORKSPACE_CONNECTIONS_PATH", "/workspace/luminax/config/workspace-connections.json"))
PRIVATE_SECRET_REFS_PATH = Path(os.getenv("PRIVATE_SECRET_REFS_PATH", "/workspace/luminax/config/private-secret-refs.json"))
PRIVATE_SECRET_REFS_TEMPLATE_PATH = Path(os.getenv("PRIVATE_SECRET_REFS_TEMPLATE_PATH", "/workspace/luminax/config/private-secret-refs.template.json"))
WORKSPACE_ATLAS_BUILDER_PATH = Path(os.getenv("WORKSPACE_ATLAS_BUILDER_PATH", "/workspace/luminax/tools/workspace_atlas_builder.py"))
REVIEW_CYCLE_POLICY_PATH = Path("/workspace/luminax/config/review-cycle-policy.json")
REVIEW_CYCLE_STATE_PATH = Path("/workspace/luminax/reports/review-cycles/state.json")
OLYMPUS_PROFILE_ROOT = Path("/workspace/luminax/config/olympus")
LUMINAX_ROOT = Path("/workspace/luminax")
ALLOW_LIVE_BACKUP_RESTORE = os.getenv("ALLOW_LIVE_BACKUP_RESTORE", "false").strip().lower() == "true"
OPTIONAL_CREDENTIAL_GATED_SERVICES: dict[str, list[str]] = {
    "zeus-discord": ["ZEUS_DISCORD_TOKEN"],
    "community-discord": ["COMMUNITY_DISCORD_TOKEN"],
}
redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
SERVICE_CATALOG = {
    "assistant-core": {"readiness_key": "assistant_core", "url": "http://assistant-core:8000/api/health/light", "startup": "core"},
    "browser-agent": {"readiness_key": "browser_agent", "url": f"{BROWSER_AGENT_URL}/health", "startup": "interactive"},
    "vision-agent": {"readiness_key": "vision_agent", "url": f"{VISION_AGENT_URL}/health", "startup": "interactive", "external": True},
    "voice-agent": {"readiness_key": "voice_agent", "url": f"{VOICE_AGENT_URL}/health", "startup": "interactive", "external": True},
    "underfoot-voice-gateway": {"readiness_key": "underfoot_voice_gateway", "url": f"{UNDERFOOT_VOICE_GATEWAY_URL}/health", "startup": "core"},
    "olympus-edge-bridge": {"readiness_key": "edge_bridge", "url": f"{EDGE_BRIDGE_URL}/health", "startup": "interactive", "external": True},
    "realtime-gateway": {"readiness_key": "realtime_gateway", "url": "http://realtime-gateway:8050/session/info", "startup": "interactive"},
    "research-worker": {"readiness_key": "research_worker", "url": "http://research-worker:8060/health", "startup": "interactive"},
    "site-manager": {"readiness_key": "site_manager", "url": "http://site-manager:8070/health", "startup": "on_demand"},
    "account-worker": {"readiness_key": "account_worker", "url": "http://account-worker:8085/health", "startup": "on_demand"},
    "network-manager": {"readiness_key": "network_manager", "url": "http://network-manager:8090/health", "startup": "on_demand"},
    "luminax-brain": {"readiness_key": "luminax_brain", "url": f"{LUMINAX_BRAIN_URL}/health", "startup": "interactive", "external": True},
    "affect-controller": {"readiness_key": "affect_controller", "url": f"{AFFECT_CONTROLLER_URL}/health", "startup": "interactive", "external": True},
    "mcp-router": {"readiness_key": "mcp_router", "url": f"{MCP_ROUTER_URL}/health", "startup": "core"},
    "langgraph-orchestrator": {"readiness_key": "langgraph_orchestrator", "url": f"{LANGGRAPH_URL}/health", "startup": "core"},
    "lumi-hive-dreamer": {"readiness_key": "hive_dreamer", "url": "http://lumi-hive-dreamer:8101/health", "startup": "on_demand"},
    "lumi-hive-engineer": {"readiness_key": "hive_engineer", "url": "http://lumi-hive-engineer:8102/health", "startup": "on_demand"},
    "lumi-hive-critic": {"readiness_key": "hive_critic", "url": "http://lumi-hive-critic:8103/health", "startup": "on_demand"},
    "lumi-3d-engineer": {"readiness_key": "three_d_engineer", "url": "http://lumi-3d-engineer:8110/health", "startup": "on_demand"},
    "lumi-finance-swarm": {"readiness_key": "finance_swarm", "url": "http://lumi-finance-swarm:8120/health", "startup": "on_demand"},
    "lumi-upgrade-agent": {"readiness_key": "upgrade_agent", "url": "http://lumi-upgrade-agent:8130/health", "startup": "maintenance"},
    "discord-ops-bot": {"readiness_key": "hermes_ops", "url": "http://discord-ops-bot:8140/health", "startup": "core"},
    "luminax-ui": {"readiness_key": "mount_olympus_ui", "url": f"{MOUNT_OLYMPUS_UI_URL}/", "startup": "core", "healthy_status_codes": [200, 401]},
}
SERVICE_WATCH_CATALOG = {
    "docker": {
        "startup": "core",
        "container": "docker",
        "probe": "platform",
        "severity": "critical",
        "repair_action": "docker",
        "dependencies": [],
    },
    "mysql": {
        "startup": "core",
        "container": "mysql",
        "probe": "mysql",
        "severity": "critical",
        "repair_action": "mysql",
        "dependencies": [],
    },
    "redis": {
        "startup": "core",
        "container": "redis",
        "probe": "redis",
        "severity": "high",
        "repair_action": "service",
        "dependencies": [],
    },
    "qdrant": {
        "startup": "core",
        "container": "qdrant",
        "probe": "http",
        "url": f"http://{QDRANT_HOST}:6333/collections",
        "severity": "high",
        "repair_action": "service",
        "dependencies": [],
    },
    "assistant-core": {
        "startup": "core",
        "container": "assistant-core",
        "probe": "http",
        "url": "http://assistant-core:8000/api/health/light",
        "severity": "critical",
        "repair_action": "service",
        "dependencies": ["mysql", "redis"],
    },
    "zeus-discord": {
        "startup": "core",
        "container": "zeus-discord",
        "probe": "docker",
        "severity": "high",
        "repair_action": "service",
        "dependencies": ["assistant-core", "mysql", "redis"],
    },
    "community-discord": {
        "startup": "core",
        "container": "community-discord",
        "probe": "docker",
        "severity": "medium",
        "repair_action": "service",
        "dependencies": ["assistant-core", "mysql", "redis"],
    },
    "admin-api": {
        "startup": "core",
        "container": "admin-api",
        "probe": "docker",
        "severity": "critical",
        "repair_action": "service",
        "dependencies": ["mysql", "redis"],
    },
    "lumi-daemon": {
        "startup": "core",
        "container": "lumi-daemon",
        "probe": "docker",
        "severity": "critical",
        "repair_action": "service",
        "dependencies": ["mysql", "redis"],
    },
    "lumi-upgrade-agent": {
        "startup": "maintenance",
        "container": "lumi-upgrade-agent",
        "probe": "http",
        "url": "http://lumi-upgrade-agent:8130/health",
        "severity": "medium",
        "repair_action": "service",
        "dependencies": ["mysql"],
    },
    "discord-bot": {
        "startup": "core",
        "container": "discord-bot",
        "probe": "docker",
        "severity": "high",
        "repair_action": "service",
        "dependencies": ["mysql"],
    },
    "discord-ops-bot": {
        "startup": "core",
        "container": "discord-ops-bot",
        "probe": "http",
        "url": "http://discord-ops-bot:8140/health",
        "severity": "medium",
        "repair_action": "service",
        "dependencies": ["admin-api", "discord-bot"],
    },
    "broadcast-conductor": {
        "startup": "core",
        "container": "broadcast-conductor",
        "probe": "docker",
        "severity": "medium",
        "repair_action": "service",
        "dependencies": ["redis"],
    },
    "vision-agent": {
        "startup": "interactive",
        "container": "vision-agent",
        "probe": "http",
        "url": f"{VISION_AGENT_URL}/health",
        "severity": "medium",
        "repair_action": "external-edge",
        "dependencies": [],
        "external": True,
    },
    "voice-agent": {
        "startup": "interactive",
        "container": "voice-agent",
        "probe": "http",
        "url": f"{VOICE_AGENT_URL}/health",
        "severity": "medium",
        "repair_action": "external-edge",
        "dependencies": [],
        "external": True,
    },
    "underfoot-voice-gateway": {
        "startup": "core",
        "container": "underfoot-voice-gateway",
        "probe": "http",
        "url": f"{UNDERFOOT_VOICE_GATEWAY_URL}/health",
        "severity": "high",
        "repair_action": "service",
        "dependencies": ["underfoot-world"],
    },
    "underfoot-voice-turn": {
        "startup": "core",
        "container": "underfoot-voice-turn",
        "probe": "tcp",
        "host": UNDERFOOT_VOICE_TURN_HOST,
        "port": UNDERFOOT_VOICE_TURN_PORT,
        "severity": "high",
        "repair_action": "service",
        "dependencies": [],
    },
    "olympus-edge-bridge": {
        "startup": "interactive",
        "container": "olympus-edge-bridge",
        "probe": "http",
        "url": f"{EDGE_BRIDGE_URL}/health",
        "severity": "medium",
        "repair_action": "external-edge",
        "dependencies": [],
        "external": True,
    },
    "luminax-brain": {
        "startup": "interactive",
        "container": "luminax-brain",
        "probe": "http",
        "url": f"{LUMINAX_BRAIN_URL}/health",
        "severity": "medium",
        "repair_action": "external-edge",
        "dependencies": ["olympus-edge-bridge"],
        "external": True,
    },
    "affect-controller": {
        "startup": "interactive",
        "container": "affect-controller",
        "probe": "http",
        "url": f"{AFFECT_CONTROLLER_URL}/health",
        "severity": "medium",
        "repair_action": "external-edge",
        "dependencies": ["olympus-edge-bridge"],
        "external": True,
    },
    "mcp-router": {
        "startup": "core",
        "container": "mcp-router",
        "probe": "http",
        "url": f"{MCP_ROUTER_URL}/health",
        "severity": "medium",
        "repair_action": "service",
        "dependencies": ["assistant-core"],
    },
    "langgraph-orchestrator": {
        "startup": "core",
        "container": "langgraph-orchestrator",
        "probe": "http",
        "url": f"{LANGGRAPH_URL}/health",
        "severity": "medium",
        "repair_action": "service",
        "dependencies": ["assistant-core"],
    },
    "realtime-gateway": {
        "startup": "core",
        "container": "realtime-gateway",
        "probe": "http",
        "url": "http://realtime-gateway:8050/session/info",
        "severity": "medium",
        "repair_action": "service",
        "dependencies": ["assistant-core", "redis"],
    },
    "luminax-ui": {
        "startup": "core",
        "container": "luminax-ui",
        "probe": "http",
        "url": f"{MOUNT_OLYMPUS_UI_URL}/",
        "healthy_status_codes": [200, 401],
        "severity": "high",
        "repair_action": "service",
        "dependencies": ["assistant-core", "admin-api"],
    },
    "underfoot-world": {
        "startup": "core",
        "container": "underfoot-world",
        "probe": "http",
        "url": "http://underfoot-world:8460/api/health",
        "severity": "high",
        "repair_action": "underfoot-world",
        "dependencies": ["mysql"],
    },
}

VOICE_DIAGNOSTICS_PATH = Path("/data/logs/olympus-voice-diagnostics.jsonl")
VOICE_HEALTH_SCENE = "olympus-web-voice"
VOICE_HEALTH_ROUTE = "/admin/voice/health"
OLYMPUS_ADMIN_DIAGNOSTICS_PATH = Path("/data/logs/olympus-admin-diagnostics.jsonl")
OLYMPUS_ADMIN_HEALTH_SCENE = "olympus-admin-health"
OLYMPUS_ADMIN_HEALTH_ROUTE = "/olympus/admin/health"
OLYMPUS_COMMUNITY_BOOTSTRAP_SCENE = "olympus-community-bootstrap"
OLYMPUS_COMMUNITY_BOOTSTRAP_ROUTE = "/olympus/community/bootstrap/validate"

def _write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _voice_breadcrumb(stage: str, severity: str, route: str, summary: str, connection: str = "") -> dict[str, str]:
    return {
        "stage": stage,
        "severity": severity,
        "scene": VOICE_HEALTH_SCENE,
        "route": route,
        "summary": str(summary or "").strip()[:240],
        "connection": str(connection or "").strip()[:240],
    }

def _voice_lane_row(name: str, source: str, status: str, detail: str, route: str, connection: str) -> dict[str, Any]:
    return {
        "name": name,
        "source": source,
        "status": status,
        "detail": detail,
        "route": route,
        "connection": connection,
    }

def _olympus_admin_breadcrumb(stage: str, severity: str, route: str, summary: str, connection: str = "") -> dict[str, str]:
    return {
        "stage": stage,
        "severity": severity,
        "scene": OLYMPUS_ADMIN_HEALTH_SCENE,
        "route": route,
        "summary": str(summary or "").strip()[:240],
        "connection": str(connection or "").strip()[:240],
    }

def _olympus_community_breadcrumb(stage: str, severity: str, route: str, summary: str, connection: str = "") -> dict[str, str]:
    return {
        "stage": stage,
        "severity": severity,
        "scene": OLYMPUS_COMMUNITY_BOOTSTRAP_SCENE,
        "route": route,
        "summary": str(summary or "").strip()[:240],
        "connection": str(connection or "").strip()[:240],
    }

def _olympus_admin_lane_row(name: str, source: str, status: str, detail: str, route: str, connection: str) -> dict[str, Any]:
    return {
        "name": name,
        "source": source,
        "status": status,
        "detail": detail,
        "route": route,
        "connection": connection,
    }

def _watch_connection(row: dict[str, Any] | None) -> str:
    if not isinstance(row, dict):
        return "unknown"
    state = str(row.get("container_state") or "unknown").strip()
    detail = str(row.get("detail") or "").strip()
    return f"{state} | {detail}".strip(" |")


def _status_is_healthyish(status: str) -> bool:
    return str(status or "").strip().lower() in {"ok", "idle", "disabled", "unconfigured"}


def _service_credentials_configured(service_name: str) -> bool:
    keys = OPTIONAL_CREDENTIAL_GATED_SERVICES.get(service_name) or []
    if not keys:
        return True
    return all(bool((os.getenv(key, "") or "").strip()) for key in keys)


def _apply_optional_service_semantics(service_name: str, row: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(row or {})
    if _service_credentials_configured(service_name):
        return payload
    state = str(payload.get("container_state") or "disabled").strip() or "disabled"
    payload["status"] = "disabled"
    payload["detail"] = "credential not configured"
    payload["container_state"] = state
    payload["connection"] = f"{state} | credential not configured".strip()
    payload["credential_gated"] = True
    return payload


def _pc_voice_lane_state() -> dict[str, Any]:
    probe = http_probe(f"{EDGE_BRIDGE_URL}/local-runtime", timeout=4)
    if probe.get("ok"):
        payload = probe.get("data") if isinstance(probe.get("data"), dict) else {}
        services = payload.get("services") if isinstance(payload.get("services"), dict) else {}
        voice = services.get("voice") if isinstance(services.get("voice"), dict) else {}
        if str(voice.get("status") or "").lower() == "ok":
            return {
                "status": "ok",
                "detail": "PC edge voice agent healthy through the edge bridge local runtime route.",
                "container_state": "windows-edge",
                "connection": "windows-edge | voice-agent healthy",
            }
        detail = str(voice.get("detail") or probe.get("detail") or "PC edge voice authority is unavailable.")
        return {
            "status": "degraded",
            "detail": detail,
            "container_state": "windows-edge",
            "connection": f"windows-edge | {detail}".strip(),
        }
    return {
        "status": "degraded",
        "detail": str(probe.get("detail") or "PC edge voice authority is unavailable."),
        "container_state": "windows-edge",
        "connection": f"windows-edge | {str(probe.get('detail') or 'PC edge voice authority is unavailable.')}".strip(),
    }


def _fast_service_state(
    service_name: str,
    docker_rows: list[dict[str, Any]],
    *,
    probe_url: str = "",
    timeout: int = 4,
    external: bool = False,
    healthy_status_codes: list[int] | None = None,
) -> dict[str, Any]:
    row = _service_row(service_name, docker_rows=docker_rows)
    running = docker_service_running(row)
    container_state = "external" if external else ("running" if running else "stopped")
    if probe_url:
        probe = http_probe(probe_url, timeout=timeout, healthy_status_codes=healthy_status_codes)
        if probe.get("ok"):
            detail = str(probe.get("detail") or "http probe ok")
            return {
                "status": "ok",
                "detail": detail,
                "container_state": container_state,
                "probe_payload": probe.get("data"),
                "connection": f"{container_state} | {detail}".strip(),
            }
        if not external and not running:
            return {
                "status": "failed",
                "detail": "container not running",
                "container_state": container_state,
                "probe_payload": probe.get("data"),
                "connection": f"{container_state} | container not running",
            }
        detail = str(probe.get("detail") or "probe failed")
        return {
            "status": "failed" if external or running else "degraded",
            "detail": detail,
            "container_state": container_state,
            "probe_payload": probe.get("data"),
            "connection": f"{container_state} | {detail}".strip(),
        }
    if running:
        return {
            "status": "ok",
            "detail": "container running",
            "container_state": container_state,
            "connection": f"{container_state} | container running",
        }
    return {
        "status": "failed",
        "detail": "container not running",
        "container_state": container_state,
        "connection": f"{container_state} | container not running",
    }


def build_olympus_voice_health_fast(skip_edge_bridge: bool = False) -> dict[str, Any]:
    docker_rows = docker_services()
    has_server_voice_agent = bool(_service_row("voice-agent", docker_rows=docker_rows))
    if VOICE_AGENT_URL_ENV or EDGE_VOICE_URL_ENV or has_server_voice_agent:
        voice_agent = _fast_service_state(
            "voice-agent",
            docker_rows,
            probe_url=f"{VOICE_AGENT_URL}/health",
            timeout=5,
            external=not has_server_voice_agent,
        )
        voice_agent_route = str(VOICE_AGENT_URL)
    else:
        voice_agent = _pc_voice_lane_state()
        voice_agent_route = f"{EDGE_BRIDGE_URL}/local-runtime"
    realtime_gateway = _fast_service_state("realtime-gateway", docker_rows, probe_url="http://realtime-gateway:8050/session/info", timeout=4)
    discord_voice = _fast_service_state("discord-bot", docker_rows)
    web_voice = _fast_service_state("luminax-ui", docker_rows, probe_url=f"{MOUNT_OLYMPUS_UI_URL}/", timeout=4, healthy_status_codes=[200, 401])
    edge_bridge = (
        {
            "status": "ok",
            "detail": "Edge bridge self-probe skipped for an edge-safe authority check.",
            "container_state": "self-skip",
            "connection": "self-skip",
        }
        if skip_edge_bridge
        else _fast_service_state("olympus-edge-bridge", docker_rows, probe_url=f"{EDGE_BRIDGE_URL}/health", timeout=5, external=True)
    )

    services = {
        "voice-agent": _voice_lane_row(
            "voice-agent",
            "voice-agent",
            str(voice_agent.get("status") or "unknown"),
            str(voice_agent.get("detail") or "Voice agent status not reported."),
            voice_agent_route,
            str(voice_agent.get("connection") or ""),
        ),
        "realtime-gateway": _voice_lane_row(
            "realtime-gateway",
            "realtime-gateway",
            str(realtime_gateway.get("status") or "unknown"),
            str(realtime_gateway.get("detail") or "Realtime gateway status not reported."),
            "http://realtime-gateway:8050/session/info",
            str(realtime_gateway.get("connection") or ""),
        ),
        "discord-voice": _voice_lane_row(
            "discord-voice",
            "discord-bot",
            str(discord_voice.get("status") or "unknown"),
            str(discord_voice.get("detail") or "Discord voice status not reported."),
            "docker://discord-bot",
            str(discord_voice.get("connection") or ""),
        ),
        "web-voice": _voice_lane_row(
            "web-voice",
            "luminax-ui",
            "ok" if str(web_voice.get("status") or "") == "ok" and str(realtime_gateway.get("status") or "") == "ok" and _status_is_healthyish(str(voice_agent.get("status") or "")) else ("failed" if any(str(row.get("status") or "").lower() == "failed" for row in [web_voice, realtime_gateway, voice_agent]) else "degraded"),
            "Browser realtime turn uses the active Olympus voice authority; typed chat is the fallback when the lane is degraded.",
            "/realtime/session/turn",
            f"ui={web_voice.get('container_state') or 'unknown'} | rt={realtime_gateway.get('status') or 'unknown'}",
        ),
        "edge-bridge": _voice_lane_row(
            "edge-bridge",
            "olympus-edge-bridge",
            str(edge_bridge.get("status") or "unknown"),
            str(edge_bridge.get("detail") or "Edge bridge status not reported."),
            str(EDGE_BRIDGE_URL),
            str(edge_bridge.get("connection") or ""),
        ),
    }

    rows = list(services.values())
    status_rank = {"ok": 0, "idle": 1, "disabled": 0, "unconfigured": 0, "degraded": 2, "failed": 3, "error": 3, "unknown": 3}
    overall = max((status_rank.get(str(row.get("status") or "unknown").lower(), 3) for row in rows), default=0)
    overall_status = "ok" if overall == 0 else "degraded" if overall <= 2 else "failed"
    generated_at = int(time.time())
    breadcrumbs = [
        _voice_breadcrumb("route-resolution", "info" if overall_status == "ok" else "warn", VOICE_HEALTH_ROUTE, "Resolved Olympus voice lane through the edge-safe authority probe.", f"voice-agent={services['voice-agent']['status']} | realtime-gateway={services['realtime-gateway']['status']}"),
        _voice_breadcrumb("connect-attempt", "info" if str(realtime_gateway.get("status") or "").lower() == "ok" else "warn", "/realtime/session/start", "Realtime gateway probe completed via the fast authority path.", services["realtime-gateway"]["connection"]),
        _voice_breadcrumb("join-context-submission", "info" if _status_is_healthyish(str(voice_agent.get("status") or "")) else "warn", "/admin/voice/speak", "Voice authority route available for Olympus audio turns.", services["voice-agent"]["connection"]),
        _voice_breadcrumb("runtime-snapshot-receipt", "info", "/admin/telemetry/latest", "Captured Olympus voice health across voice-agent, realtime-gateway, discord-voice, web-voice, and edge-bridge.", f"voice-health={overall_status}"),
        _voice_breadcrumb("avatar-body-handoff", "info" if str(discord_voice.get("status") or "").lower() == "ok" else "warn", "docker://discord-bot", "Discord voice handoff state recorded.", services["discord-voice"]["connection"]),
        _voice_breadcrumb("disconnect-retry", "warn" if overall_status != "ok" else "info", VOICE_HEALTH_ROUTE, "Typed fallback remains available when the voice lane is degraded.", f"overall={overall_status}"),
    ]
    receipt = {
        "receipt_id": f"voice_receipt_{generated_at}_{uuid.uuid4().hex[:8]}",
        "status": overall_status,
        "scene": VOICE_HEALTH_SCENE,
        "generated_at": generated_at,
        "services": rows,
        "breadcrumbs": breadcrumbs,
        "edge_safe": True,
    }
    if overall_status != "ok":
        _write_jsonl(VOICE_DIAGNOSTICS_PATH, receipt)
    return {
        "status": overall_status,
        "scene": VOICE_HEALTH_SCENE,
        "route": VOICE_HEALTH_ROUTE,
        "generated_at": generated_at,
        "receipt_id": receipt["receipt_id"],
        "diagnostic_path": str(VOICE_DIAGNOSTICS_PATH),
        "services": services,
        "breadcrumbs": breadcrumbs,
        "edge_safe": True,
        "receipt": receipt,
    }

def build_olympus_voice_health(
    watch: dict[str, Any] | None = None,
    skip_edge_bridge: bool = False,
) -> dict[str, Any]:
    watch = watch or evaluate_service_watch_state()
    by_name = watch.get("by_name") or {}
    has_server_voice_agent = bool(by_name.get("voice-agent"))
    if VOICE_AGENT_URL_ENV or EDGE_VOICE_URL_ENV or has_server_voice_agent:
        voice_agent = dict(by_name.get("voice-agent") or {})
        voice_agent_route = str(VOICE_AGENT_URL)
        voice_agent_connection = f"{voice_agent.get('container_state') or 'unknown'} | {voice_agent.get('detail') or ''}".strip()
    else:
        voice_agent = _pc_voice_lane_state()
        voice_agent_route = f"{EDGE_BRIDGE_URL}/local-runtime"
        voice_agent_connection = str(voice_agent.get("connection") or "")
    realtime_gateway = dict(by_name.get("realtime-gateway") or {})
    discord_voice = dict(by_name.get("discord-bot") or {})
    web_voice = dict(by_name.get("luminax-ui") or {})
    edge_bridge = dict(by_name.get("olympus-edge-bridge") or {})

    services = {
        "voice-agent": _voice_lane_row(
            "voice-agent",
            "voice-agent",
            str(voice_agent.get("status") or "unknown"),
            str(voice_agent.get("detail") or "Voice agent status not reported."),
            voice_agent_route,
            voice_agent_connection,
        ),
        "realtime-gateway": _voice_lane_row(
            "realtime-gateway",
            "realtime-gateway",
            str(realtime_gateway.get("status") or "unknown"),
            str(realtime_gateway.get("detail") or "Realtime gateway status not reported."),
            "http://realtime-gateway:8050/session/info",
            f"{realtime_gateway.get('container_state') or 'unknown'} | {realtime_gateway.get('detail') or ''}".strip(),
        ),
        "discord-voice": _voice_lane_row(
            "discord-voice",
            "discord-bot",
            str(discord_voice.get("status") or "unknown"),
            str(discord_voice.get("detail") or "Discord voice status not reported."),
            "docker://discord-bot",
            f"{discord_voice.get('container_state') or 'unknown'} | {discord_voice.get('detail') or ''}".strip(),
        ),
        "web-voice": _voice_lane_row(
            "web-voice",
            "luminax-ui",
            "ok" if str(web_voice.get("status") or "") == "ok" and str(realtime_gateway.get("status") or "") == "ok" and _status_is_healthyish(str(voice_agent.get("status") or "")) else ("failed" if any(str(row.get("status") or "").lower() == "failed" for row in [web_voice, realtime_gateway, voice_agent]) else "degraded"),
            "Browser realtime turn uses the active Olympus voice authority; typed chat is the fallback when the lane is degraded.",
            "/realtime/session/turn",
            f"ui={web_voice.get('container_state') or 'unknown'} | rt={realtime_gateway.get('status') or 'unknown'}",
        ),
        "edge-bridge": _voice_lane_row(
            "edge-bridge",
            "olympus-edge-bridge",
            "ok" if skip_edge_bridge else str(edge_bridge.get("status") or "unknown"),
            "Edge bridge self-probe skipped for an edge-safe authority check."
            if skip_edge_bridge
            else str(edge_bridge.get("detail") or "Edge bridge status not reported."),
            str(EDGE_BRIDGE_URL),
            "self-skip"
            if skip_edge_bridge
            else f"{edge_bridge.get('container_state') or 'unknown'} | {edge_bridge.get('detail') or ''}".strip(),
        ),
    }

    rows = list(services.values())
    status_rank = {"ok": 0, "idle": 1, "disabled": 0, "unconfigured": 0, "degraded": 2, "failed": 3, "error": 3, "unknown": 3}
    overall = max((status_rank.get(str(row.get("status") or "unknown").lower(), 3) for row in rows), default=0)
    overall_status = "ok" if overall == 0 else "degraded" if overall <= 2 else "failed"

    breadcrumbs = [
        _voice_breadcrumb("route-resolution", "info" if overall_status == "ok" else "warn", VOICE_HEALTH_ROUTE, "Resolved Olympus voice lane through the admin-backed browser voice proxy.", f"voice-agent={services['voice-agent']['status']} | realtime-gateway={services['realtime-gateway']['status']}"),
        _voice_breadcrumb("connect-attempt", "info" if str(realtime_gateway.get("status") or "").lower() == "ok" else "warn", "/realtime/session/start", "Realtime gateway probe completed.", services["realtime-gateway"]["connection"]),
        _voice_breadcrumb("join-context-submission", "info" if _status_is_healthyish(str(voice_agent.get("status") or "")) else "warn", "/admin/voice/speak", "Voice authority route available for Olympus audio turns.", services["voice-agent"]["connection"]),
        _voice_breadcrumb("runtime-snapshot-receipt", "info", "/admin/telemetry/latest", "Captured Olympus voice health across voice-agent, realtime-gateway, discord-voice, web-voice, and edge-bridge.", f"voice-health={overall_status}"),
        _voice_breadcrumb("avatar-body-handoff", "info" if str(discord_voice.get("status") or "").lower() == "ok" else "warn", "docker://discord-bot", "Discord voice handoff state recorded.", services["discord-voice"]["connection"]),
        _voice_breadcrumb("disconnect-retry", "warn" if overall_status != "ok" else "info", VOICE_HEALTH_ROUTE, "Typed fallback remains available when the voice lane is degraded.", f"overall={overall_status}"),
    ]

    generated_at = int(time.time())
    receipt = {
        "receipt_id": f"voice_receipt_{generated_at}_{uuid.uuid4().hex[:8]}",
        "status": overall_status,
        "scene": VOICE_HEALTH_SCENE,
        "generated_at": generated_at,
        "services": rows,
        "breadcrumbs": breadcrumbs,
    }
    if overall_status != "ok":
        _write_jsonl(VOICE_DIAGNOSTICS_PATH, receipt)
    return {
        "status": overall_status,
        "scene": VOICE_HEALTH_SCENE,
        "route": VOICE_HEALTH_ROUTE,
        "generated_at": generated_at,
        "receipt_id": receipt["receipt_id"],
        "diagnostic_path": str(VOICE_DIAGNOSTICS_PATH),
        "services": services,
        "breadcrumbs": breadcrumbs,
        "receipt": receipt,
    }

def build_olympus_admin_health(
    watch: dict[str, Any] | None = None,
    voice_health: dict[str, Any] | None = None,
    skip_edge_bridge: bool = False,
) -> dict[str, Any]:
    watch = watch or evaluate_service_watch_state()
    voice_health = voice_health or build_olympus_voice_health(watch=watch, skip_edge_bridge=skip_edge_bridge)
    by_name = watch.get("by_name") or {}
    zeus_core = dict(by_name.get("assistant-core") or {})
    zeus_discord = _apply_optional_service_semantics("zeus-discord", by_name.get("zeus-discord") or {})
    community_discord = _apply_optional_service_semantics("community-discord", by_name.get("community-discord") or {})
    hermes = dict(by_name.get("discord-bot") or {})
    hermes_ops = dict(by_name.get("discord-ops-bot") or {})
    admin_api = dict(by_name.get("admin-api") or {})
    windows_edge = load_latest_windows_node_status(node_key=WINDOWS_EDGE_NODE_KEY)

    services = {
        "zeus-core": _olympus_admin_lane_row(
            "zeus-core",
            "assistant-core",
            str(zeus_core.get("status") or "unknown"),
            str(zeus_core.get("detail") or "Zeus core status not reported."),
            "http://assistant-core:8000/api/health/light",
            _watch_connection(zeus_core),
        ),
        "zeus-discord": _olympus_admin_lane_row(
            "zeus-discord",
            "zeus-discord",
            str(zeus_discord.get("status") or "unknown"),
            str(zeus_discord.get("detail") or "Zeus Discord status not reported."),
            "docker://zeus-discord",
            _watch_connection(zeus_discord),
        ),
        "community-discord": _olympus_admin_lane_row(
            "community-discord",
            "community-discord",
            str(community_discord.get("status") or "unknown"),
            str(community_discord.get("detail") or "Community Discord relay status not reported."),
            "docker://community-discord",
            _watch_connection(community_discord),
        ),
        "hermes": _olympus_admin_lane_row(
            "hermes",
            "discord-bot",
            str(hermes.get("status") or "unknown"),
            str(hermes.get("detail") or "Hermes Discord relay status not reported."),
            "docker://discord-bot",
            _watch_connection(hermes),
        ),
        "hermes-ops": _olympus_admin_lane_row(
            "hermes-ops",
            "discord-ops-bot",
            str(hermes_ops.get("status") or "unknown"),
            str(hermes_ops.get("detail") or "Hermes ops relay status not reported."),
            "http://discord-ops-bot:8140/health",
            _watch_connection(hermes_ops),
        ),
        "admin-api": _olympus_admin_lane_row(
            "admin-api",
            "admin-api",
            str(admin_api.get("status") or "unknown"),
            str(admin_api.get("detail") or "Admin API status not reported."),
            OLYMPUS_ADMIN_HEALTH_ROUTE,
            _watch_connection(admin_api),
        ),
        "windows-edge-node": _olympus_admin_lane_row(
            "windows-edge-node",
            "windows-node-monitor",
            str(windows_edge.get("status") or "unknown"),
            str(windows_edge.get("detail") or "Windows edge node status not reported."),
            "/windows-node/latest",
            f"{(windows_edge.get('row') or {}).get('node_key') or 'unknown'} | stale={windows_edge.get('stale')}",
        ),
        "voice": _olympus_admin_lane_row(
            "voice",
            "voice-health",
            str(voice_health.get("status") or "unknown"),
            "Olympus voice aggregation across voice-agent, realtime-gateway, Discord voice, browser voice, and edge bridge.",
            str(voice_health.get("route") or VOICE_HEALTH_ROUTE),
            f"receipt={voice_health.get('receipt_id') or 'unknown'} | status={voice_health.get('status') or 'unknown'}",
        ),
    }

    rows = list(services.values())
    status_rank = {"ok": 0, "idle": 1, "disabled": 0, "unconfigured": 0, "degraded": 2, "failed": 3, "error": 3, "unknown": 3}
    overall = max((status_rank.get(str(row.get("status") or "unknown").lower(), 3) for row in rows), default=0)
    overall_status = "ok" if overall == 0 else "degraded" if overall <= 2 else "failed"
    generated_at = int(time.time())
    breadcrumbs = [
        _olympus_admin_breadcrumb(
            "route-resolution",
            "info" if overall_status == "ok" else "warn",
            OLYMPUS_ADMIN_HEALTH_ROUTE,
            "Resolved Olympus admin health across Zeus core, Zeus Discord, the community relay, Hermes, Hermes Ops, admin-api, the Windows edge node, and voice.",
            f"zeus-core={services['zeus-core']['status']} | zeus-discord={services['zeus-discord']['status']} | community-discord={services['community-discord']['status']} | windows-edge={services['windows-edge-node']['status']}",
        ),
        _olympus_admin_breadcrumb(
            "connect-attempt",
            "info" if _status_is_healthyish(str(zeus_core.get("status") or "")) and _status_is_healthyish(str(zeus_discord.get("status") or "")) else "warn",
            "docker://zeus-discord",
            "Zeus control lanes probed through assistant-core and zeus-discord health endpoints.",
            f"assistant-core={services['zeus-core']['connection']} | zeus-discord={services['zeus-discord']['connection']}",
        ),
        _olympus_admin_breadcrumb(
            "join-context-submission",
            "info" if _status_is_healthyish(str(hermes.get("status") or "")) and _status_is_healthyish(str(hermes_ops.get("status") or "")) and _status_is_healthyish(str(community_discord.get("status") or "")) else "warn",
            "docker://discord-bot",
            "Hermes, Hermes Ops, and the community Discord relay availability recorded for Olympus admin handoff.",
            f"hermes={services['hermes']['connection']} | hermes-ops={services['hermes-ops']['connection']} | community={services['community-discord']['connection']}",
        ),
        _olympus_admin_breadcrumb(
            "runtime-snapshot-receipt",
            "info",
            "/telemetry/latest",
            "Captured a combined Olympus admin and voice runtime snapshot.",
            f"overall={overall_status} | voice={services['voice']['status']}",
        ),
        _olympus_admin_breadcrumb(
            "avatar-body-handoff",
            "info" if str(admin_api.get("status") or "").lower() == "ok" else "warn",
            OLYMPUS_ADMIN_HEALTH_ROUTE,
            "Admin API control-plane handoff recorded alongside Zeus, Hermes, and the Windows edge node heartbeat lane.",
            f"{services['admin-api']['connection']} | windows-edge={services['windows-edge-node']['connection']}",
        ),
        _olympus_admin_breadcrumb(
            "disconnect-retry",
            "warn" if overall_status != "ok" else "info",
            OLYMPUS_ADMIN_HEALTH_ROUTE,
            "Admin aggregation remains diagnostically available when any Olympus lane degrades.",
            f"overall={overall_status}",
        ),
    ]

    receipt = {
        "receipt_id": f"olympus_admin_receipt_{generated_at}_{uuid.uuid4().hex[:8]}",
        "status": overall_status,
        "scene": OLYMPUS_ADMIN_HEALTH_SCENE,
        "generated_at": generated_at,
        "services": rows,
        "breadcrumbs": breadcrumbs,
    }
    if overall_status != "ok":
        _write_jsonl(OLYMPUS_ADMIN_DIAGNOSTICS_PATH, receipt)
    return {
        "status": overall_status,
        "scene": OLYMPUS_ADMIN_HEALTH_SCENE,
        "route": OLYMPUS_ADMIN_HEALTH_ROUTE,
        "generated_at": generated_at,
        "receipt_id": receipt["receipt_id"],
        "diagnostic_path": str(OLYMPUS_ADMIN_DIAGNOSTICS_PATH),
        "services": services,
        "breadcrumbs": breadcrumbs,
        "receipt": receipt,
    }


def build_olympus_admin_health_fast(skip_edge_bridge: bool = False) -> dict[str, Any]:
    docker_rows = docker_services()
    zeus_core = _fast_service_state("assistant-core", docker_rows, probe_url="http://assistant-core:8000/api/health/light", timeout=4)
    zeus_discord = _apply_optional_service_semantics("zeus-discord", _fast_service_state("zeus-discord", docker_rows))
    community_discord = _apply_optional_service_semantics("community-discord", _fast_service_state("community-discord", docker_rows))
    hermes = _fast_service_state("discord-bot", docker_rows)
    hermes_ops = _fast_service_state("discord-ops-bot", docker_rows, probe_url="http://discord-ops-bot:8140/health", timeout=4)
    admin_api = _fast_service_state("admin-api", docker_rows)
    windows_edge = load_latest_windows_node_status(node_key=WINDOWS_EDGE_NODE_KEY)
    voice_health = build_olympus_voice_health_fast(skip_edge_bridge=skip_edge_bridge)

    services = {
        "zeus-core": _olympus_admin_lane_row(
            "zeus-core",
            "assistant-core",
            str(zeus_core.get("status") or "unknown"),
            str(zeus_core.get("detail") or "Zeus core status not reported."),
            "http://assistant-core:8000/api/health/light",
            str(zeus_core.get("connection") or ""),
        ),
        "zeus-discord": _olympus_admin_lane_row(
            "zeus-discord",
            "zeus-discord",
            str(zeus_discord.get("status") or "unknown"),
            str(zeus_discord.get("detail") or "Zeus Discord status not reported."),
            "docker://zeus-discord",
            str(zeus_discord.get("connection") or ""),
        ),
        "community-discord": _olympus_admin_lane_row(
            "community-discord",
            "community-discord",
            str(community_discord.get("status") or "unknown"),
            str(community_discord.get("detail") or "Community Discord relay status not reported."),
            "docker://community-discord",
            str(community_discord.get("connection") or ""),
        ),
        "hermes": _olympus_admin_lane_row(
            "hermes",
            "discord-bot",
            str(hermes.get("status") or "unknown"),
            str(hermes.get("detail") or "Hermes Discord relay status not reported."),
            "docker://discord-bot",
            str(hermes.get("connection") or ""),
        ),
        "hermes-ops": _olympus_admin_lane_row(
            "hermes-ops",
            "discord-ops-bot",
            str(hermes_ops.get("status") or "unknown"),
            str(hermes_ops.get("detail") or "Hermes ops relay status not reported."),
            "http://discord-ops-bot:8140/health",
            str(hermes_ops.get("connection") or ""),
        ),
        "admin-api": _olympus_admin_lane_row(
            "admin-api",
            "admin-api",
            str(admin_api.get("status") or "unknown"),
            str(admin_api.get("detail") or "Admin API status not reported."),
            OLYMPUS_ADMIN_HEALTH_ROUTE,
            str(admin_api.get("connection") or ""),
        ),
        "windows-edge-node": _olympus_admin_lane_row(
            "windows-edge-node",
            "windows-node-monitor",
            str(windows_edge.get("status") or "unknown"),
            str(windows_edge.get("detail") or "Windows edge node status not reported."),
            "/windows-node/latest",
            f"{(windows_edge.get('row') or {}).get('node_key') or 'unknown'} | stale={windows_edge.get('stale')}",
        ),
        "voice": _olympus_admin_lane_row(
            "voice",
            "voice-health",
            str(voice_health.get("status") or "unknown"),
            "Olympus voice aggregation across voice-agent, realtime-gateway, Discord voice, browser voice, and edge bridge.",
            str(voice_health.get("route") or VOICE_HEALTH_ROUTE),
            f"receipt={voice_health.get('receipt_id') or 'unknown'} | status={voice_health.get('status') or 'unknown'}",
        ),
    }

    rows = list(services.values())
    status_rank = {"ok": 0, "idle": 1, "disabled": 0, "unconfigured": 0, "degraded": 2, "failed": 3, "error": 3, "unknown": 3}
    overall = max((status_rank.get(str(row.get("status") or "unknown").lower(), 3) for row in rows), default=0)
    overall_status = "ok" if overall == 0 else "degraded" if overall <= 2 else "failed"
    generated_at = int(time.time())
    breadcrumbs = [
        _olympus_admin_breadcrumb(
            "route-resolution",
            "info" if overall_status == "ok" else "warn",
            OLYMPUS_ADMIN_HEALTH_ROUTE,
            "Resolved Olympus admin health through the edge-safe authority probe.",
            f"zeus-core={services['zeus-core']['status']} | zeus-discord={services['zeus-discord']['status']} | community-discord={services['community-discord']['status']} | windows-edge={services['windows-edge-node']['status']}",
        ),
        _olympus_admin_breadcrumb(
            "connect-attempt",
            "info" if _status_is_healthyish(str(zeus_core.get("status") or "")) and _status_is_healthyish(str(zeus_discord.get("status") or "")) else "warn",
            "docker://zeus-discord",
            "Zeus control lanes probed through the edge-safe authority path.",
            f"assistant-core={services['zeus-core']['connection']} | zeus-discord={services['zeus-discord']['connection']}",
        ),
        _olympus_admin_breadcrumb(
            "join-context-submission",
            "info" if _status_is_healthyish(str(hermes.get("status") or "")) and _status_is_healthyish(str(hermes_ops.get("status") or "")) and _status_is_healthyish(str(community_discord.get("status") or "")) else "warn",
            "docker://discord-bot",
            "Hermes, Hermes Ops, and the community Discord relay availability recorded for Olympus admin handoff.",
            f"hermes={services['hermes']['connection']} | hermes-ops={services['hermes-ops']['connection']} | community={services['community-discord']['connection']}",
        ),
        _olympus_admin_breadcrumb(
            "runtime-snapshot-receipt",
            "info",
            "/telemetry/latest",
            "Captured a combined Olympus admin and voice runtime snapshot.",
            f"overall={overall_status} | voice={services['voice']['status']}",
        ),
        _olympus_admin_breadcrumb(
            "avatar-body-handoff",
            "info" if str(admin_api.get("status") or "").lower() == "ok" else "warn",
            OLYMPUS_ADMIN_HEALTH_ROUTE,
            "Admin API control-plane handoff recorded alongside Zeus, Hermes, and the Windows edge node heartbeat lane.",
            f"{services['admin-api']['connection']} | windows-edge={services['windows-edge-node']['connection']}",
        ),
        _olympus_admin_breadcrumb(
            "disconnect-retry",
            "warn" if overall_status != "ok" else "info",
            OLYMPUS_ADMIN_HEALTH_ROUTE,
            "Admin aggregation remains diagnostically available when any Olympus lane degrades.",
            f"overall={overall_status}",
        ),
    ]

    receipt = {
        "receipt_id": f"olympus_admin_receipt_{generated_at}_{uuid.uuid4().hex[:8]}",
        "status": overall_status,
        "scene": OLYMPUS_ADMIN_HEALTH_SCENE,
        "generated_at": generated_at,
        "services": rows,
        "breadcrumbs": breadcrumbs,
        "edge_safe": True,
    }
    if overall_status != "ok":
        _write_jsonl(OLYMPUS_ADMIN_DIAGNOSTICS_PATH, receipt)
    return {
        "status": overall_status,
        "scene": OLYMPUS_ADMIN_HEALTH_SCENE,
        "route": OLYMPUS_ADMIN_HEALTH_ROUTE,
        "generated_at": generated_at,
        "receipt_id": receipt["receipt_id"],
        "diagnostic_path": str(OLYMPUS_ADMIN_DIAGNOSTICS_PATH),
        "services": services,
        "breadcrumbs": breadcrumbs,
        "edge_safe": True,
        "receipt": receipt,
    }

async def _proxy_voice_json(path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    timeout = httpx.Timeout(30.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(method, f"{VOICE_AGENT_URL}{path}", json=payload)
    if response.status_code >= 400:
        detail = (response.text or "").strip()
        raise HTTPException(status_code=response.status_code, detail=detail[:600] or f"Voice agent request failed: {response.status_code}")
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        data = response.json()
        return data if isinstance(data, dict) else {"status": "ok", "data": data}
    text = (response.text or "").strip()
    return {"status": "ok", "detail": text[:1200]}

async def _proxy_core_json(
    path: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timeout = httpx.Timeout(30.0, connect=5.0)
    request_kwargs: dict[str, Any] = {}
    safe_method = str(method or "GET").upper()
    if safe_method == "GET":
        request_kwargs["params"] = params or payload
    else:
        request_kwargs["json"] = payload if payload is not None else params
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(safe_method, f"{CORE_API_URL}{path}", **request_kwargs)
    if response.status_code >= 400:
        detail = (response.text or "").strip()
        raise HTTPException(status_code=response.status_code, detail=detail[:600] or f"Assistant-core request failed: {response.status_code}")
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        data = response.json()
        return data if isinstance(data, dict) else {"status": "ok", "data": data}
    text = (response.text or "").strip()
    return {"status": "ok", "detail": text[:1200]}

async def _proxy_voice_audio(path: str, payload: dict[str, Any]) -> Response:
    timeout = httpx.Timeout(90.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{VOICE_AGENT_URL}{path}", json=payload)
    if response.status_code >= 400:
        detail = (response.text or "").strip()
        raise HTTPException(status_code=response.status_code, detail=detail[:600] or f"Voice audio request failed: {response.status_code}")
    return Response(content=response.content, media_type=response.headers.get("content-type", "audio/mpeg"))

@app.get("/voice/health")
async def voice_health(skip_edge_bridge: bool = Query(default=False), edge_safe: bool = Query(default=False)) -> dict[str, Any]:
    if edge_safe:
        return build_olympus_voice_health_fast(skip_edge_bridge=skip_edge_bridge)
    return build_olympus_voice_health(skip_edge_bridge=skip_edge_bridge)

@app.get("/olympus/admin/health")
async def olympus_admin_health(skip_edge_bridge: bool = Query(default=False), edge_safe: bool = Query(default=False)) -> dict[str, Any]:
    if edge_safe:
        return build_olympus_admin_health_fast(skip_edge_bridge=skip_edge_bridge)
    watch = evaluate_service_watch_state()
    voice_health = build_olympus_voice_health(watch=watch, skip_edge_bridge=skip_edge_bridge)
    return build_olympus_admin_health(watch=watch, voice_health=voice_health, skip_edge_bridge=skip_edge_bridge)

@app.get(OLYMPUS_COMMUNITY_BOOTSTRAP_ROUTE)
async def olympus_community_bootstrap_validate(
    project_key: str = Query(default="underfoot"),
    guild_id: str = Query(default=""),
    guild_name: str = Query(default=""),
    token: str = Query(default=""),
) -> dict[str, Any]:
    validate_watch_status(token)
    safe_project_key = str(project_key or "").strip().lower() or "underfoot"
    safe_guild_id = str(guild_id or "").strip()
    safe_guild_name = str(guild_name or "").strip()
    validation = await _proxy_core_json(
        "/discord/community-guilds/validate",
        params={
            "token": CONTROL_TOKEN,
            "project_key": safe_project_key,
            "guild_id": safe_guild_id,
            "guild_name": safe_guild_name,
        },
    )
    details = validation.get("validation") if isinstance(validation.get("validation"), dict) else {}
    summary = details.get("summary") if isinstance(details.get("summary"), dict) else {}
    validation_status = str(details.get("status") or validation.get("status") or "unknown").strip().lower() or "unknown"
    connection = (
        f"assistant-core={CORE_API_URL}/discord/community-guilds/validate | "
        f"project={safe_project_key} | guild={safe_guild_id or safe_guild_name or 'auto'}"
    )[:240]
    breadcrumbs = [
        _olympus_community_breadcrumb(
            "route-resolution",
            "info" if validation_status == "ready" else "warn",
            OLYMPUS_COMMUNITY_BOOTSTRAP_ROUTE,
            f"Validated public community bootstrap contract for {safe_project_key}.",
            connection,
        ),
        _olympus_community_breadcrumb(
            "runtime-snapshot-receipt",
            "info" if validation_status == "ready" else "warn",
            "/api/discord/community-guilds/validate",
            "Captured Olympus mirror, patch-note routing, receipt contract, and Alex owner-route binding.",
            f"mirror_bound={summary.get('mirror_bound')} | owner_route_bound={summary.get('owner_route_bound')}",
        ),
    ]
    if validation_status != "ready":
        breadcrumbs.append(
            _olympus_community_breadcrumb(
                "degraded-bootstrap-state",
                "warn",
                OLYMPUS_COMMUNITY_BOOTSTRAP_ROUTE,
                "Community bootstrap contract still needs attention before public launch handoff.",
                f"required_failed={summary.get('required_failed', 'unknown')}",
            )
        )
    generated_at = datetime.now(timezone.utc).isoformat()
    receipt = {
        "receipt_id": f"olympus_community_bootstrap_{int(time.time())}_{uuid.uuid4().hex[:8]}",
        "scene": OLYMPUS_COMMUNITY_BOOTSTRAP_SCENE,
        "route": OLYMPUS_COMMUNITY_BOOTSTRAP_ROUTE,
        "generated_at": generated_at,
        "project_key": safe_project_key,
        "guild_id": safe_guild_id,
        "guild_name": safe_guild_name,
        "validation_status": validation_status,
        "validation_summary": summary,
        "breadcrumbs": breadcrumbs,
    }
    _write_jsonl(OLYMPUS_ADMIN_DIAGNOSTICS_PATH, receipt)
    return {
        "status": "ok",
        "scene": OLYMPUS_COMMUNITY_BOOTSTRAP_SCENE,
        "route": OLYMPUS_COMMUNITY_BOOTSTRAP_ROUTE,
        "generated_at": generated_at,
        "diagnostic_path": str(OLYMPUS_ADMIN_DIAGNOSTICS_PATH),
        "validation": details,
        "breadcrumbs": breadcrumbs,
        "receipt": receipt,
    }

@app.get("/voice/voices/list")
async def voice_voices_list() -> dict[str, Any]:
    return await _proxy_voice_json("/voices/list")

@app.get("/voice/voices/catalog")
async def voice_voices_catalog() -> dict[str, Any]:
    return await _proxy_voice_json("/voices/catalog")

@app.post("/voice/voices/upload")
async def voice_voices_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read()
    timeout = httpx.Timeout(90.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{VOICE_AGENT_URL}/voices/upload",
            files={"file": (file.filename or "voice.webm", data, file.content_type or "application/octet-stream")},
        )
    if response.status_code >= 400:
        detail = (response.text or "").strip()
        raise HTTPException(status_code=response.status_code, detail=detail[:600] or "Voice upload failed")
    data = response.json()
    return data if isinstance(data, dict) else {"status": "ok", "data": data}

@app.post("/voice/speak")
async def voice_speak(payload: dict[str, Any]) -> Response:
    return await _proxy_voice_audio("/speak", payload)

@app.post("/voice/clone")
async def voice_clone(payload: dict[str, Any]) -> Response:
    return await _proxy_voice_audio("/clone", payload)

@app.post("/voice/transcribe")
async def voice_transcribe(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read()
    timeout = httpx.Timeout(90.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{VOICE_AGENT_URL}/transcribe",
            files={"file": (file.filename or "audio.bin", data, file.content_type or "application/octet-stream")},
        )
    if response.status_code >= 400:
        detail = (response.text or "").strip()
        raise HTTPException(status_code=response.status_code, detail=detail[:600] or "Voice transcription failed")
    data = response.json()
    return data if isinstance(data, dict) else {"status": "ok", "data": data}

@app.post("/voice/talk_turn")
async def voice_talk_turn(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read()
    timeout = httpx.Timeout(90.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{VOICE_AGENT_URL}/talk_turn",
            files={"file": (file.filename or "audio.bin", data, file.content_type or "application/octet-stream")},
        )
    if response.status_code >= 400:
        detail = (response.text or "").strip()
        raise HTTPException(status_code=response.status_code, detail=detail[:600] or "Voice turn processing failed")
    data = response.json()
    return data if isinstance(data, dict) else {"status": "ok", "data": data}

def db_conn():
    return mysql.connector.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE)

def sh(cmd: str, tail: int | None = 12000):
    require_feature("LUMINAX_HOST_EXECUTION_ENABLED")
    proc = subprocess.run(["/bin/sh", "-lc", cmd], capture_output=True, text=True)
    stdout = proc.stdout if tail is None else proc.stdout[-tail:]
    stderr = proc.stderr if tail is None else proc.stderr[-tail:]
    return {"returncode": proc.returncode, "stdout": stdout, "stderr": stderr}

def valid_service_name(name: str) -> bool:
    return bool(name) and len(name) <= 64 and all(ch.isalnum() or ch in "-_" for ch in name)

def validate_service_or_400(name: str):
    if not valid_service_name(name):
        raise HTTPException(status_code=400, detail="Invalid service name")

def current_web_auth_user() -> str:
    return (web_auth_user_ctx.get("") or "").strip().lower()

def request_has_trusted_web_control() -> bool:
    return feature_enabled("TRUST_PROXY_AUTH_HEADERS") and bool(TRUSTED_WEB_CONTROL_USER) and current_web_auth_user() == TRUSTED_WEB_CONTROL_USER

def validate(token: str):
    if request_has_trusted_web_control():
        return
    if not token_matches(token, CONTROL_TOKEN) or redis_client.get("luminax_control_granted") != "true":
        raise HTTPException(status_code=403, detail="Control not granted")

def validate_internal_service(token: str):
    if not token_matches(token, CONTROL_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid internal service token")

def validate_watch_status(token: str):
    if request_has_trusted_web_control():
        return
    if not token_matches(token, CONTROL_TOKEN):
        raise HTTPException(status_code=403, detail="Control not granted")

class SvcRequest(BaseModel):
    token: str
    name: str

class ServiceRepairRequest(BaseModel):
    token: str
    name: str
    reason: str = ""
    requested_by: str = "operator"

class NodeHeartbeatRequest(BaseModel):
    token: str
    node_key: str
    hostname: str
    os_version: str | None = None
    capabilities: dict = Field(default_factory=dict)
    services: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)

class BackupUpdateRequest(BaseModel):
    token: str
    name: str
    keep: bool | None = None

class AutopilotConfigRequest(BaseModel):
    token: str
    enabled: bool | None = None
    allow_service_actions: bool | None = None
    ui_failure_threshold: int | None = None
    ui_restart_cooldown_sec: int | None = None
    talk_to_user: bool | None = None

class MaintenanceModeRequest(BaseModel):
    token: str
    mode: str = "normal"
    reason: str = ""
    note: str = ""
    random_health_checks: bool | None = None

class RuntimePolicyRequest(BaseModel):
    token: str
    execution_profile: str = ""
    workers_paused: bool | None = None
    worker_pause_reason: str = ""

class ReviewCycleActionRequest(BaseModel):
    token: str
    note: str = ""

class ReviewBallotRequest(BaseModel):
    token: str
    project_key: str
    candidate_id: str
    title: str = ""
    vote_signature: str = ""
    critic_vote: str = ""
    status: str = "approved"
    notes: str = ""

class AtlasRebuildRequest(BaseModel):
    token: str
    project_key: str = ""

class TokenRequest(BaseModel):
    token: str

class IncidentActionRequest(BaseModel):
    token: str
    incident_key: str
    actor: str = ""
    note: str = ""

class IncidentNotifyRequest(BaseModel):
    token: str
    incident_key: str
    delivered_via: list[str] = Field(default_factory=list)
    escalation_level: int = 1
    next_reminder_sec: int = 0
    note: str = ""

class MoneyActionRequest(BaseModel):
    token: str
    action: str
    account_id: str = ""
    amount_usd: float = 0.0
    note: str = ""
    consent_token: str = ""
    mode: str = "dry_run"

class WindowsNodeActionRequest(BaseModel):
    token: str
    node_key: str
    action_key: str
    args: dict = Field(default_factory=dict)
    dry_run: bool = True
    note: str = ""

class WindowsNodePollRequest(BaseModel):
    token: str
    node_key: str
    max_jobs: int = 8

class WindowsNodeCompleteRequest(BaseModel):
    token: str
    node_key: str
    job_id: int
    status: str = "completed"
    result: dict = Field(default_factory=dict)
    error: str = ""

class SelfUpgradeAnalyzeRequest(BaseModel):
    service: str
    error_excerpt: str
    log_path: str = ""
    trigger_kind: str = "manual"
    context: dict = Field(default_factory=dict)
    file_hints: list[str] = Field(default_factory=list)
    apply_if_safe: bool = False
    initiative_goal: str = ""

class SelfUpgradeApplyRequest(BaseModel):
    token: str
    proposal_id: str
    allow_medium_risk: bool = False

def docker_services():
    # Use docker ps with compose labels for stable JSON lines across compose versions.
    res = sh("docker ps -a --format '{{json .}}'", tail=None)
    raw = (res.get("stdout") or "").strip()
    if not raw:
        return []
    out = []
    for line in [l for l in raw.splitlines() if l.strip()]:
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                labels = str(item.get("Labels") or "")
                service_name = str(item.get("Service") or "").strip()
                project_name = ""
                if not service_name:
                    for part in labels.split(","):
                        if part.startswith("com.docker.compose.service="):
                            service_name = part.split("=", 1)[1].strip()
                            break
                for part in labels.split(","):
                    if part.startswith("com.docker.compose.project="):
                        project_name = part.split("=", 1)[1].strip()
                        break
                if not service_name:
                    container_name = str(item.get("Name") or item.get("Names") or "").strip()
                    if container_name.startswith("luminax-"):
                        rest = container_name[len("luminax-"):]
                        base, _, suffix = rest.rpartition("-")
                        if base and suffix.isdigit():
                            service_name = base
                if service_name:
                    item["Service"] = service_name
                if project_name:
                    item["Project"] = project_name
                if not item.get("State"):
                    status_text = str(item.get("Status") or "").lower()
                    if "up" in status_text:
                        item["State"] = "running"
                    elif "exited" in status_text or "dead" in status_text:
                        item["State"] = "exited"
                out.append(item)
        except Exception:
            pass
    return out

def compose_manifest_path() -> Path | None:
    for candidate in (
        Path("/workspace/luminax/server-compose.yml"),
        Path("/workspace/luminax/docker-compose.yml"),
        Path("/workspace/luminax/docker-compose.yaml"),
    ):
        if candidate.exists():
            return candidate
    return None

def preferred_compose_project_name(docker_rows: list[dict[str, Any]] | None = None) -> str:
    rows = docker_rows or docker_services()
    counts: dict[str, int] = {}
    for row in rows:
        project_name = str(row.get("Project") or "").strip()
        service_name = str(row.get("Service") or "").strip()
        if not project_name or not service_name:
            continue
        if service_name in SERVICE_WATCH_CATALOG:
            counts[project_name] = counts.get(project_name, 0) + 1
    if counts:
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    for row in rows:
        project_name = str(row.get("Project") or "").strip()
        if project_name:
            counts[project_name] = counts.get(project_name, 0) + 1
    if counts:
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return ""

def _service_rows_by_name(service_name: str, docker_rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    source_rows = docker_rows or docker_services()
    rows = [row for row in source_rows if str(row.get("Service") or "").strip() == service_name]
    preferred_project = preferred_compose_project_name(source_rows)
    if preferred_project:
        preferred = [row for row in rows if str(row.get("Project") or "").strip() == preferred_project]
        if preferred:
            return preferred
    return rows

def _service_row(service_name: str, docker_rows: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    rows = _service_rows_by_name(service_name, docker_rows=docker_rows)
    return rows[0] if rows else None

def _service_container_names(service_names: list[str], docker_rows: list[dict[str, Any]] | None = None) -> list[str]:
    rows = docker_rows or docker_services()
    out: list[str] = []
    seen: set[str] = set()
    for service_name in service_names:
        for row in _service_rows_by_name(service_name, docker_rows=rows):
            container_name = str(row.get("Name") or row.get("Names") or "").strip()
            if not container_name or container_name in seen:
                continue
            seen.add(container_name)
            out.append(container_name)
    return out

def compose_command(action: str) -> str:
    manifest = compose_manifest_path()
    project_name = preferred_compose_project_name()
    parts = ["docker", "compose"]
    if project_name:
        parts.extend(["-p", project_name])
    if manifest:
        parts.extend(["-f", str(manifest)])
    if action:
        parts.append(action)
    return " ".join(parts)

def systemctl_available() -> bool:
    probe = sh("command -v systemctl >/dev/null 2>&1 && test -d /run/systemd/system && echo yes || echo no")
    return probe.get("stdout", "").strip() == "yes"

def run_system_service(action: str, name: str):
    if not systemctl_available():
        return {"status": "unsupported", "detail": "systemctl unavailable from admin-api runtime", "name": name, "action": action}
    return sh(f"systemctl {action} {name}")

def validate_node_token(token: str):
    if not token_matches(token, CONTROL_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid node token")

def validate_control_or_node_token(token: str, node_key: str = ""):
    try:
        validate(token)
        return "control"
    except HTTPException:
        if node_key:
            validate_node_token(token)
            return "node"
        raise

def valid_node_key(name: str) -> bool:
    return bool(name) and len(name) <= 128 and all(ch.isalnum() or ch in "-_:.@" for ch in name)

def validate_node_key_or_400(name: str):
    if not valid_node_key(name):
        raise HTTPException(status_code=400, detail="Invalid node key")

def decode_jsonish(value: Any):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return value
    return value

def unix_ts_from_value(value: Any) -> int:
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

def load_latest_windows_node_status(node_key: str = "") -> dict[str, Any]:
    try:
        ensure_windows_link_tables()
        conn = db_conn(); cur = conn.cursor(dictionary=True)
        if node_key:
            validate_node_key_or_400(node_key)
            cur.execute(
                "SELECT node_key, hostname, os_version, status, last_seen_at, capabilities_json, services_json, meta_json FROM windows_nodes WHERE node_key=%s ORDER BY last_seen_at DESC, updated_at DESC LIMIT 1",
                (node_key,),
            )
        else:
            cur.execute(
                "SELECT node_key, hostname, os_version, status, last_seen_at, capabilities_json, services_json, meta_json FROM windows_nodes ORDER BY last_seen_at DESC, updated_at DESC LIMIT 1"
            )
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            return {"status": "failed", "detail": "No Windows node heartbeat is recorded yet.", "row": None}
        row["capabilities_json"] = decode_jsonish(row.get("capabilities_json"))
        row["services_json"] = decode_jsonish(row.get("services_json"))
        row["meta_json"] = decode_jsonish(row.get("meta_json"))
        last_seen_unix = unix_ts_from_value(row.get("last_seen_at"))
        stale = bool(last_seen_unix and int(time.time()) - last_seen_unix > WINDOWS_NODE_STALE_SEC)
        base_status = str(row.get("status") or "unknown").strip().lower()
        status = "ok" if base_status == "online" and not stale else "degraded"
        detail = "Windows node heartbeat is current." if status == "ok" else "Windows node heartbeat is stale or degraded."
        return {
            "status": status,
            "detail": detail,
            "row": row,
            "stale": stale,
            "last_seen_unix": last_seen_unix,
        }
    except Exception as e:
        return {"status": "failed", "detail": str(e), "row": None}

def ensure_table_column(cur, table_name: str, column_name: str, ddl: str):
    cur.execute(
        "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s LIMIT 1",
        (MYSQL_DATABASE, table_name, column_name),
    )
    if cur.fetchone():
        return
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")

def mysql_probe(timeout_sec: int = 3) -> dict[str, Any]:
    try:
        conn = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            connection_timeout=max(1, int(timeout_sec)),
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        conn.close()
        return {"ok": True, "detail": "mysql auth probe ok"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}

def redis_probe() -> dict[str, Any]:
    try:
        redis_client.ping()
        return {"ok": True, "detail": "redis ping ok"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}

def tcp_probe(host: str, port: int, timeout_sec: int = 3) -> dict[str, Any]:
    try:
        with socket.create_connection((str(host or "").strip(), int(port or 0)), timeout=max(1, int(timeout_sec or 3))):
            return {"ok": True, "detail": f"tcp probe ok {host}:{port}"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}

def http_probe(url: str, timeout: int = 3, healthy_status_codes: list[int] | None = None) -> dict[str, Any]:
    parsed = fetch_json(url, timeout=timeout)
    ok_statuses = {int(code) for code in (healthy_status_codes or []) if str(code).strip()}
    http_status = int(parsed.get("http_status") or 0)
    if http_status and http_status in ok_statuses:
        return {"ok": True, "detail": f"http probe ok via status {http_status}", "data": parsed}
    if parsed.get("status") == "error":
        return {"ok": False, "detail": str(parsed.get("detail") or "probe failed"), "data": parsed}
    return {"ok": True, "detail": "http probe ok", "data": parsed}

def docker_runtime_status() -> dict[str, Any]:
    res = sh("docker info >/dev/null 2>&1 && echo active || echo inactive")
    active = (res.get("stdout") or "").strip() == "active"
    return {"ok": active, "status": "ok" if active else "failed", "detail": "docker info probe" if active else "docker runtime unavailable"}

def ensure_service_watch_repairs_table():
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS service_watch_repairs (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP NULL DEFAULT NULL,
                service_name VARCHAR(120) NOT NULL,
                action_name VARCHAR(64) NOT NULL DEFAULT 'repair',
                requested_by VARCHAR(120) NULL,
                repair_status VARCHAR(32) NOT NULL DEFAULT 'queued',
                detail_text MEDIUMTEXT NULL,
                meta_json LONGTEXT NULL,
                KEY idx_service_watch_repairs_service (service_name, created_at),
                KEY idx_service_watch_repairs_status (repair_status, created_at)
            )
            """
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass

def record_service_watch_repair(service_name: str, action_name: str, requested_by: str, detail_text: str = "", meta: dict | None = None) -> int:
    ensure_service_watch_repairs_table()
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO service_watch_repairs (service_name, action_name, requested_by, repair_status, detail_text, meta_json) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                str(service_name or "").strip()[:120],
                str(action_name or "repair").strip()[:64],
                str(requested_by or "system").strip()[:120],
                "running",
                str(detail_text or "").strip()[:16000],
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
        repair_id = int(cur.lastrowid or 0)
        conn.commit()
        cur.close()
        conn.close()
        return repair_id
    except Exception:
        return 0

def complete_service_watch_repair(repair_id: int, status: str, detail_text: str = "", meta: dict | None = None):
    if int(repair_id or 0) <= 0:
        return
    ensure_service_watch_repairs_table()
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE service_watch_repairs SET repair_status=%s, detail_text=%s, meta_json=%s, completed_at=NOW() WHERE id=%s",
            (
                str(status or "completed").strip()[:32],
                str(detail_text or "").strip()[:16000],
                json.dumps(meta or {}, ensure_ascii=False),
                int(repair_id),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass

def recent_service_watch_repairs(limit: int = 12) -> list[dict[str, Any]]:
    ensure_service_watch_repairs_table()
    try:
        conn = db_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id, created_at, completed_at, service_name, action_name, requested_by, repair_status, detail_text, meta_json "
            "FROM service_watch_repairs ORDER BY id DESC LIMIT %s",
            (max(1, min(int(limit or 12), 100)),),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        for row in rows:
            row["meta_json"] = decode_jsonish(row.get("meta_json")) or {}
        return rows
    except Exception:
        return []

def windows_tool_catalog():
    return [
        {"action_key": "local.tool_probe", "title": "Probe Local Tools", "description": "Refresh local services/process/tool capability scan.", "args_schema": {"include_inventory": "bool"}},
        {"action_key": "local.service_status", "title": "Collect Service Status", "description": "Return status for named local Windows services.", "args_schema": {"patterns": "string[]"}},
        {"action_key": "local.open_url", "title": "Open URL", "description": "Open URL in default browser on Windows node.", "args_schema": {"url": "string"}},
        {"action_key": "local.file_list", "title": "List Files", "description": "List files under approved local path.", "args_schema": {"path": "string", "max_items": "int"}},
        {"action_key": "local.ps", "title": "Run Guarded PowerShell", "description": "Run allowlisted PowerShell command on Windows node.", "args_schema": {"command": "string"}},
        {"action_key": "local.capture_screen", "title": "Capture Screen", "description": "Capture a screenshot from local desktop and return metadata/path.", "args_schema": {"save_path": "string"}},
        {"action_key": "local.darius_dispatch", "title": "Forward Darius Dispatch", "description": "Forward a queued Darius request into the local project agent and start the agent if needed.", "args_schema": {"project_key": "string", "dispatch": "object", "local_url": "string", "agent_start_script": "string"}},
        {"action_key": "audio.device_inventory", "title": "Audio Device Inventory", "description": "Return playback/capture device inventory, defaults, and audio board state.", "args_schema": {}},
        {"action_key": "audio.device_set_default", "title": "Set Default Audio Device", "description": "Set a Windows playback/capture endpoint as the default for one or more roles.", "args_schema": {"device_id": "string", "roles": "string[]", "flow": "string"}},
        {"action_key": "audio.sample_list", "title": "List Audio Samples", "description": "List saved audio board samples, slots, and Stream Deck exports.", "args_schema": {}},
        {"action_key": "audio.sample_play", "title": "Play Audio Sample", "description": "Play a saved local sample through the selected/default audio route.", "args_schema": {"sample_name": "string", "device_id": "string", "roles": "string[]"}},
        {"action_key": "audio.tts_render", "title": "Render TTS Sample", "description": "Generate a spoken sample on the Windows node, optionally save it, and optionally play it immediately.", "args_schema": {"text": "string", "voice": "string", "emotion": "string", "save_name": "string", "auto_play": "bool", "device_id": "string", "roles": "string[]"}},
        {"action_key": "audio.clone_render", "title": "Render Cloned Voice Sample", "description": "Generate a cloned-voice sample from a saved voice ref, optionally save it, and optionally play it immediately.", "args_schema": {"text": "string", "voice_ref": "string", "save_name": "string", "auto_play": "bool", "device_id": "string", "roles": "string[]"}},
        {"action_key": "audio.soundboard_slot_save", "title": "Save Soundboard Slot", "description": "Save a reusable soundboard slot definition for TTS, clone, or sample playback.", "args_schema": {"slot_key": "string", "label": "string", "mode": "string", "sample_name": "string", "text": "string", "voice": "string", "emotion": "string", "voice_ref": "string", "device_id": "string", "roles": "string[]"}},
        {"action_key": "audio.soundboard_slot_fire", "title": "Fire Soundboard Slot", "description": "Execute a saved soundboard slot immediately.", "args_schema": {"slot_key": "string"}},
        {"action_key": "audio.streamdeck_export", "title": "Export Stream Deck Launcher", "description": "Create a launcher script for a soundboard slot that can be mapped in Stream Deck.", "args_schema": {"slot_key": "string", "label": "string"}},
    ]

def ensure_windows_link_tables():
    conn = db_conn(); cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS windows_nodes (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          node_key VARCHAR(255) NOT NULL,
          hostname VARCHAR(255) NOT NULL,
          os_version VARCHAR(255) NULL,
          status VARCHAR(32) NOT NULL DEFAULT 'online',
          last_seen_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
          capabilities_json JSON NULL,
          services_json JSON NULL,
          meta_json JSON NULL,
          UNIQUE KEY uq_node_key (node_key),
          INDEX idx_status_updated (status, updated_at)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS windows_node_heartbeats (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          node_key VARCHAR(255) NOT NULL,
          hostname VARCHAR(255) NOT NULL,
          capabilities_json JSON NULL,
          services_json JSON NULL,
          metrics_json JSON NULL,
          meta_json JSON NULL,
          INDEX idx_node_created (node_key, created_at)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS windows_node_jobs (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          node_key VARCHAR(255) NOT NULL,
          requested_by VARCHAR(255) NULL,
          requested_via VARCHAR(64) NOT NULL DEFAULT 'ui',
          job_type VARCHAR(64) NOT NULL DEFAULT 'tool',
          action_key VARCHAR(128) NOT NULL,
          payload_json JSON NULL,
          status VARCHAR(32) NOT NULL DEFAULT 'queued',
          result_json JSON NULL,
          error_text TEXT NULL,
          dispatched_at TIMESTAMP NULL DEFAULT NULL,
          completed_at TIMESTAMP NULL DEFAULT NULL,
          meta_json JSON NULL,
          INDEX idx_node_status (node_key, status, id),
          INDEX idx_status_created (status, created_at)
        )
        """
    )
    conn.commit(); cur.close(); conn.close()

def backup_dirs():
    rows = []
    for p in sorted(BACKUP_ROOT.glob(f"{BACKUP_PREFIX}*"), key=lambda x: x.name, reverse=True):
        if not p.is_dir():
            continue
        keep = (p / ".keep").exists()
        created = None
        try:
            created = p.stat().st_mtime
        except Exception:
            pass
        rows.append({"name": p.name, "path": str(p), "keep": keep, "created_epoch": created})
    return rows

def backup_path_or_400(name: str) -> Path:
    if not name.startswith(BACKUP_PREFIX) or "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid backup name")
    p = (BACKUP_ROOT / name).resolve()
    if not str(p).startswith(str(BACKUP_ROOT.resolve())):
        raise HTTPException(status_code=400, detail="Invalid backup path")
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=404, detail="Backup not found")
    return p

def _urlopen_with_scheme(request_or_url, timeout: int):
    import ssl
    import urllib.request
    import urllib.parse

    target = request_or_url.full_url if hasattr(request_or_url, "full_url") else str(request_or_url)
    scheme = (urllib.parse.urlparse(target).scheme or "http").lower()
    if scheme == "https":
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context())
        )
        return opener.open(request_or_url, timeout=timeout)
    return urllib.request.urlopen(request_or_url, timeout=timeout)


def fetch_json(url: str, timeout: int = 8):
    import urllib.error
    try:
        with _urlopen_with_scheme(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="ignore")
            try:
                return json.loads(body)
            except Exception:
                return {"status": "ok", "raw": body[:4000]}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            body = ""
        detail = f"HTTP Error {int(getattr(e, 'code', 0) or 0)}: {str(getattr(e, 'reason', '') or e).strip()}".strip()
        payload = {
            "status": "error",
            "detail": detail,
            "url": url,
            "http_status": int(getattr(e, "code", 0) or 0),
        }
        if body.strip():
            payload["raw"] = body[:4000]
        return payload
    except Exception as e:
        return {"status": "error", "detail": str(e), "url": url}

def post_json(url: str, payload: dict, timeout: int = 20):
    import urllib.request
    try:
        req_obj = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _urlopen_with_scheme(req_obj, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="ignore")
            try:
                return json.loads(body)
            except Exception:
                return {"status": "ok", "raw": body[:4000]}
    except Exception as e:
        return {"status": "error", "detail": str(e), "url": url}

def readiness_probe_map():
    return {
        meta["readiness_key"]: {
            "compose_service": service,
            "url": meta["url"],
            "startup": meta["startup"],
            "external": bool(meta.get("external", False)),
            "healthy_status_codes": [int(code) for code in (meta.get("healthy_status_codes") or []) if str(code).strip()],
        }
        for service, meta in SERVICE_CATALOG.items()
    }

def docker_service_running(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    state = (row.get("State") or row.get("Status") or "").lower()
    return "running" in state or "up" in state

def latest_windows_nodes(limit: int = 20):
    try:
        conn = db_conn(); cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT node_key, hostname, os_version, status, last_seen_at, capabilities_json, services_json "
            "FROM windows_nodes ORDER BY updated_at DESC LIMIT %s",
            (max(1, min(int(limit or 20), 200)),),
        )
        rows = cur.fetchall(); cur.close(); conn.close()
        return rows
    except Exception:
        return []

def load_autopilot_config():
    path = Path("/workspace/luminax/config/autopilot-config.json")
    data = {"enabled": False, "allow_service_actions": False, "ui_failure_threshold": 3, "ui_restart_cooldown_sec": 180, "talk_to_user": True}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"path": str(path), "config": data}

def _maintenance_mode_key(value: Any) -> str:
    mode = str(value or "normal").strip().lower()
    if mode not in {"normal", "diagnose", "repair_safe", "freeze"}:
        return "normal"
    return mode

def _maintenance_policy(mode: str) -> dict[str, Any]:
    key = _maintenance_mode_key(mode)
    policies = {
        "normal": {
            "write_allowed": True,
            "service_actions_allowed": True,
            "self_apply_allowed": True,
            "autopilot_actions_allowed": True,
            "description": "Normal runtime. Write lanes and operator actions are enabled.",
        },
        "diagnose": {
            "write_allowed": False,
            "service_actions_allowed": False,
            "self_apply_allowed": False,
            "autopilot_actions_allowed": False,
            "description": "Diagnostics only. Inspect and verify, but do not mutate runtime state.",
        },
        "repair_safe": {
            "write_allowed": True,
            "service_actions_allowed": True,
            "self_apply_allowed": True,
            "autopilot_actions_allowed": False,
            "description": "Controlled repair mode. Manual fixes are allowed while autopilot stays passive.",
        },
        "freeze": {
            "write_allowed": False,
            "service_actions_allowed": False,
            "self_apply_allowed": False,
            "autopilot_actions_allowed": False,
            "description": "Hard freeze. Preserve the current state until the operator reopens change lanes.",
        },
    }
    return {"mode": key, **policies[key]}

def load_maintenance_mode():
    now_ts = int(time.time())
    data = {
        "mode": "normal",
        "reason": "",
        "note": "",
        "random_health_checks": True,
        "set_by": "system",
        "started_at": now_ts,
        "updated_at": now_ts,
    }
    if MAINTENANCE_MODE_PATH.exists():
        try:
            disk = json.loads(MAINTENANCE_MODE_PATH.read_text(encoding="utf-8"))
            if isinstance(disk, dict):
                data.update(disk)
        except Exception:
            pass
    data["mode"] = _maintenance_mode_key(data.get("mode"))
    data["policy"] = _maintenance_policy(data["mode"])
    data["random_health_checks"] = bool(data.get("random_health_checks", True))
    return data

def save_maintenance_mode(mode: str, reason: str = "", note: str = "", random_health_checks: bool | None = None, set_by: str = ""):
    current = load_maintenance_mode()
    now_ts = int(time.time())
    mode_key = _maintenance_mode_key(mode)
    started_at = int(current.get("started_at") or now_ts)
    if mode_key != str(current.get("mode") or "normal").strip().lower():
        started_at = now_ts
    payload = {
        "mode": mode_key,
        "reason": str(reason or "").strip()[:240],
        "note": str(note or "").strip()[:2400],
        "random_health_checks": bool(current.get("random_health_checks", True) if random_health_checks is None else random_health_checks),
        "set_by": str(set_by or current.get("set_by") or "operator").strip()[:120] or "operator",
        "started_at": started_at,
        "updated_at": now_ts,
        "policy": _maintenance_policy(mode_key),
    }
    MAINTENANCE_MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAINTENANCE_MODE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload

def _default_runtime_policy() -> dict[str, Any]:
    return {
        "version": "2026-03-12",
        "execution_profile": "pro_only",
        "workers_paused": False,
        "worker_pause_reason": "",
        "cloud_model_floor": "gpt-5.2",
        "openai_models": {
            "fast": "gpt-5.2",
            "reasoning": "gpt-5.3",
            "coder": "gpt-5.3-codex",
            "self_repair": "gpt-5.4",
            "research_fast": "gpt-5.2",
            "research_reasoning": "gpt-5.4",
        },
        "codex_models": {
            "readonly": "gpt-5.2",
            "implement": "gpt-5.3-codex",
            "review": "gpt-5.4",
            "repair": "gpt-5.4",
        },
        "rate_limits": {
            "codex_runs_per_hour": 12,
            "codex_runs_per_10_min": 3,
            "research_jobs_per_hour": 8,
            "research_jobs_per_10_min": 2,
        },
        "darius_queue": {
            "max_parallel_write_assignments_per_service": 1,
            "max_parallel_cloud_runs": 1,
            "base_cooldown_sec": 600,
            "max_backoff_sec": 7200,
        },
    }

def load_runtime_policy() -> dict[str, Any]:
    payload = _default_runtime_policy()
    if RUNTIME_POLICY_PATH.exists():
        try:
            disk = json.loads(RUNTIME_POLICY_PATH.read_text(encoding="utf-8"))
            if isinstance(disk, dict):
                for key, value in disk.items():
                    if isinstance(value, dict) and isinstance(payload.get(key), dict):
                        payload[key].update(value)
                    else:
                        payload[key] = value
        except Exception:
            pass
    execution_profile = str(payload.get("execution_profile") or "pro_only").strip().lower()
    payload["execution_profile"] = execution_profile if execution_profile in {"pro_only", "api_enabled"} else "pro_only"
    payload["workers_paused"] = bool(payload.get("workers_paused", False))
    payload["worker_pause_reason"] = str(payload.get("worker_pause_reason", "")).strip()[:240]
    return payload

def save_runtime_policy(workers_paused: bool | None = None, worker_pause_reason: str = "", execution_profile: str = "") -> dict[str, Any]:
    payload = load_runtime_policy()
    if execution_profile.strip():
        payload["execution_profile"] = execution_profile.strip().lower()
    if workers_paused is not None:
        payload["workers_paused"] = bool(workers_paused)
    if worker_pause_reason.strip():
        payload["worker_pause_reason"] = worker_pause_reason.strip()[:240]
    elif workers_paused is False:
        payload["worker_pause_reason"] = ""
    RUNTIME_POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_POLICY_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload

def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}

def _write_json_file(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload

def load_olympus_identity() -> dict[str, Any]:
    default = {
        "version": "2026-03-13",
        "technical_platform_name": "MountOlympus",
        "public_brand": "Luminax Platform",
        "control_surface_name": "Mount Olympus",
        "operator_name": "Zeus",
        "ui_subtitle": "Zeus coordinates Hermes, Hermes Ops, Darius, the Oracle trio, and the worker pantheon.",
        "service_labels": {},
        "roles": {},
        "assets": {},
        "naming_guards": {"must_appear": ["Luminax Platform", "Mount Olympus", "Zeus", "Hermes", "Hermes Ops", "Darius", "Oracle of Dodona", "Oracle of Siwa", "Oracle of Delphi"]},
    }
    disk = _read_json_file(OLYMPUS_IDENTITY_PATH)
    payload = {**default, **disk}
    if disk != payload:
        _write_json_file(OLYMPUS_IDENTITY_PATH, payload)
    return payload

def _read_olympus_profile_collection(kind: str) -> list[dict[str, Any]]:
    root = OLYMPUS_PROFILE_ROOT / str(kind or "").strip().lower()
    rows: list[dict[str, Any]] = []
    try:
        if not root.exists():
            return rows
        for path in sorted(root.glob("*.json")):
            payload = _read_json_file(path)
            if not isinstance(payload, dict):
                continue
            row = {**payload}
            row["profile_path"] = str(path)
            row["key"] = str(row.get("key") or path.stem).strip() or path.stem
            rows.append(row)
    except Exception:
        return rows
    return rows

def load_olympus_service_profiles() -> list[dict[str, Any]]:
    return _read_olympus_profile_collection("services")

def load_olympus_agent_profiles() -> list[dict[str, Any]]:
    return _read_olympus_profile_collection("agents")

def load_olympus_project_profiles() -> list[dict[str, Any]]:
    return _read_olympus_profile_collection("projects")

def _olympus_profile_by_key(kind: str, key: str) -> dict[str, Any]:
    normalized = str(key or "").strip().lower()
    if not normalized:
        return {}
    for row in _read_olympus_profile_collection(kind):
        row_key = str(row.get("key") or "").strip().lower()
        aliases = [str(alias).strip().lower() for alias in row.get("aliases", [])] if isinstance(row.get("aliases"), list) else []
        if row_key == normalized or normalized in aliases:
            return row
    return {}

def load_workspace_registry() -> dict[str, Any]:
    default = {"version": "2026-03-12", "workspace_name": "Project P", "projects": []}
    disk = _read_json_file(WORKSPACE_REGISTRY_PATH)
    payload = {**default, **disk}
    if not isinstance(payload.get("projects"), list):
        payload["projects"] = []
    if disk != payload:
        _write_json_file(WORKSPACE_REGISTRY_PATH, payload)
    return payload


def load_workspace_connections() -> dict[str, Any]:
    default = {
        "version": "2026-03-13",
        "workspace_name": "Project P",
        "nodes": [],
        "connections": [],
        "delivery_defaults": {},
    }
    disk = _read_json_file(WORKSPACE_CONNECTIONS_PATH)
    payload = {**default, **disk}
    if not isinstance(payload.get("nodes"), list):
        payload["nodes"] = []
    if not isinstance(payload.get("connections"), list):
        payload["connections"] = []
    if not isinstance(payload.get("delivery_defaults"), dict):
        payload["delivery_defaults"] = {}
    if disk != payload:
        _write_json_file(WORKSPACE_CONNECTIONS_PATH, payload)
    return payload


def load_private_secret_ref_template() -> dict[str, Any]:
    payload = _read_json_file(PRIVATE_SECRET_REFS_TEMPLATE_PATH)
    return payload if isinstance(payload, dict) else {}


def load_workspace_atlas() -> dict[str, Any]:
    default = {
        "version": "2026-03-13",
        "workspace_name": "Project P",
        "workspace_root": str(WORKSPACE_ATLAS_ROOT),
        "projects": [],
        "counts": {"projects": 0, "connection_refs": 0},
        "audit": {"ok_count": 0, "warn_count": 0, "rows": [], "unregistered_roots": []},
        "paths": {
            "workspace_atlas_md": str(WORKSPACE_ATLAS_MD_PATH),
            "workspace_atlas_json": str(WORKSPACE_ATLAS_ROOT / "WORKSPACE_ATLAS.json"),
            "payload_atlas_json": str(WORKSPACE_ATLAS_JSON_PATH),
            "connections_path": str(WORKSPACE_CONNECTIONS_PATH),
        },
    }
    disk = _read_json_file(WORKSPACE_ATLAS_JSON_PATH)
    payload = {**default, **disk}
    if not isinstance(payload.get("projects"), list):
        payload["projects"] = []
    if not isinstance(payload.get("counts"), dict):
        payload["counts"] = default["counts"]
    if not isinstance(payload.get("audit"), dict):
        payload["audit"] = default["audit"]
    if not isinstance(payload.get("paths"), dict):
        payload["paths"] = default["paths"]
    return payload


def workspace_atlas_project(project_key: str) -> dict[str, Any]:
    wanted = str(project_key or "").strip().lower()
    if not wanted:
        return {}
    for row in load_workspace_atlas().get("projects", []):
        if not isinstance(row, dict):
            continue
        current = str(row.get("project_key") or "").strip().lower()
        if current == wanted:
            return row
    return {}


def rebuild_workspace_atlas(project_key: str = "") -> dict[str, Any]:
    require_feature("LUMINAX_HOST_EXECUTION_ENABLED")
    if not WORKSPACE_ATLAS_BUILDER_PATH.exists():
        raise HTTPException(status_code=404, detail=f"Atlas builder not found: {WORKSPACE_ATLAS_BUILDER_PATH}")
    command = [sys.executable, str(WORKSPACE_ATLAS_BUILDER_PATH), "--apply"]
    wanted = str(project_key or "").strip().lower()
    if wanted:
        command.extend(["--project-key", wanted])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=500, detail="atlas_rebuild_failed") from exc
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="atlas_rebuild_failed")
    return load_workspace_atlas()

def load_review_cycle_policy() -> dict[str, Any]:
    default = {
        "version": "2026-03-12",
        "campaign_hours": 8,
        "wave_count": 8,
        "wave_minutes": 55,
        "bootstrap_minutes": 40,
        "stop_phrase": "stop",
        "approval_rule": {"require_approve_votes": 2, "critic_reject_blocks": True},
        "waves": [],
    }
    disk = _read_json_file(REVIEW_CYCLE_POLICY_PATH)
    payload = {**default, **disk}
    if not isinstance(payload.get("waves"), list):
        payload["waves"] = []
    if disk != payload:
        _write_json_file(REVIEW_CYCLE_POLICY_PATH, payload)
    return payload

def _default_review_cycle_state() -> dict[str, Any]:
    policy = load_review_cycle_policy()
    waves = []
    for row in policy.get("waves", []):
        if not isinstance(row, dict):
            continue
        waves.append(
            {
                "index": int(row.get("index") or len(waves) + 1),
                "title": str(row.get("title") or f"Wave {len(waves) + 1}").strip(),
                "owner": str(row.get("owner") or "zeus").strip(),
                "target": str(row.get("target") or "luminax").strip(),
                "goal": str(row.get("goal") or "").strip(),
                "status": "pending",
            }
        )
    return {
        "cycle_id": "",
        "status": "idle",
        "started_at": 0,
        "updated_at": int(time.time()),
        "current_wave": 0,
        "current_target": "",
        "current_title": "",
        "stop_requested": False,
        "pause_requested": False,
        "notes": [],
        "ballots": [],
        "ledger": [],
        "waves": waves,
    }

def load_review_cycle_state() -> dict[str, Any]:
    default = _default_review_cycle_state()
    disk = _read_json_file(REVIEW_CYCLE_STATE_PATH)
    payload = {**default, **disk}
    if not isinstance(payload.get("notes"), list):
        payload["notes"] = []
    if not isinstance(payload.get("ballots"), list):
        payload["ballots"] = []
    if not isinstance(payload.get("ledger"), list):
        payload["ledger"] = []
    if not isinstance(payload.get("waves"), list):
        payload["waves"] = default["waves"]
    return payload

def save_review_cycle_state(payload: dict[str, Any]) -> dict[str, Any]:
    current = load_review_cycle_state()
    merged = {**current, **(payload if isinstance(payload, dict) else {})}
    merged["updated_at"] = int(time.time())
    return _write_json_file(REVIEW_CYCLE_STATE_PATH, merged)

def _runtime_pause_owned_by_review_cycle(reason: str) -> bool:
    normalized = str(reason or "").strip().lower()
    if not normalized:
        return False
    return any(
        fragment in normalized
        for fragment in {
            "olympus review cycle",
            "operator sent stop signal",
            "block new ballots",
            "drain the current atomic block",
        }
    )

def _normalize_review_vote(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"approve", "approved", "yes", "ship", "run", "create", "spawn"}:
        return "approve"
    if normalized in {"reject", "rejected", "deny", "block", "hold", "no"}:
        return "reject"
    return "revise"

def _parse_vote_signature(signature: str) -> tuple[int, int, int] | None:
    raw = str(signature or "").strip()
    parts = raw.split("-")
    if len(parts) != 3:
        return None
    try:
        approve, revise, reject = [max(int(part), 0) for part in parts]
    except ValueError:
        return None
    return approve, revise, reject

def _review_cycle_start_index(policy: dict[str, Any], current: dict[str, Any]) -> int:
    waves = [row for row in policy.get("waves", []) if isinstance(row, dict)]
    if not waves:
        return 0
    current_index = int(current.get("current_wave") or 0)
    valid_indexes = [int(row.get("index") or idx + 1) for idx, row in enumerate(waves)]
    if current_index in valid_indexes:
        return current_index
    for row in current.get("waves", []):
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "pending").strip().lower()
        if status not in {"completed", "verified"}:
            idx = int(row.get("index") or 0)
            if idx in valid_indexes:
                return idx
    return valid_indexes[0]

def _review_cycle_guard_ballot_writes(current: dict[str, Any]) -> None:
    status = str(current.get("status") or "").strip().lower()
    if bool(current.get("stop_requested")) or status == "draining":
        raise HTTPException(status_code=409, detail="Review cycle is draining. Resume the cycle before recording another ballot.")
    if bool(current.get("pause_requested")) or status == "paused":
        raise HTTPException(status_code=409, detail="Review cycle is paused. Resume the cycle before recording another ballot.")

def _review_cycle_validate_ballot(ballot: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    approval_rule = policy.get("approval_rule") if isinstance(policy.get("approval_rule"), dict) else {}
    require_approve_votes = max(int(approval_rule.get("require_approve_votes") or 0), 0)
    critic_reject_blocks = bool(approval_rule.get("critic_reject_blocks", False))
    raw_critic_vote = str(ballot.get("critic_vote") or "").strip()
    normalized_status = _normalize_review_vote(ballot.get("status") or "approved")
    normalized_critic_vote = _normalize_review_vote(raw_critic_vote) if raw_critic_vote else ""
    approve_count = 0
    revise_count = 0
    reject_count = 0
    parsed_signature = _parse_vote_signature(ballot.get("vote_signature") or "")
    if parsed_signature is not None:
        approve_count, revise_count, reject_count = parsed_signature
    if normalized_status == "approve":
        if parsed_signature is None:
            raise HTTPException(status_code=400, detail="Approved ballots require vote_signature in approve-revise-reject format.")
        if require_approve_votes and approve_count < require_approve_votes:
            raise HTTPException(
                status_code=409,
                detail=f"Approved ballots require at least {require_approve_votes} approve votes. Received {approve_count}.",
            )
        if critic_reject_blocks and not raw_critic_vote:
            raise HTTPException(status_code=400, detail="Approved ballots must include critic_vote under the current review cycle policy.")
        if critic_reject_blocks and normalized_critic_vote == "reject":
            raise HTTPException(status_code=409, detail="Critic reject blocks approval under the current review cycle policy.")
    ballot["status"] = "approved" if normalized_status == "approve" else ("rejected" if normalized_status == "reject" else "revise")
    ballot["critic_vote"] = normalized_critic_vote
    ballot["vote_breakdown"] = {"approve": approve_count, "revise": revise_count, "reject": reject_count} if parsed_signature is not None else {}
    return ballot

def append_review_cycle_ledger(kind: str, title: str, detail: str = "", meta: dict[str, Any] | None = None) -> dict[str, Any]:
    state = load_review_cycle_state()
    entry = {
        "kind": str(kind or "event").strip() or "event",
        "title": str(title or "Review cycle event").strip()[:180],
        "detail": str(detail or "").strip()[:2000],
        "created_at": int(time.time()),
        "meta": meta if isinstance(meta, dict) else {},
    }
    state.setdefault("ledger", []).append(entry)
    state["ledger"] = state["ledger"][-200:]
    save_review_cycle_state(state)
    return entry

def review_cycle_candidates() -> list[dict[str, Any]]:
    registry = load_workspace_registry()
    rows = [row for row in registry.get("projects", []) if isinstance(row, dict)]
    ranked = sorted(rows, key=lambda item: int(item.get("priority") or 0), reverse=True)
    out = []
    for idx, row in enumerate(ranked, start=1):
        out.append(
            {
                "candidate_id": f"{str(row.get('project_key') or 'workspace').strip()}-priority",
                "priority_rank": idx,
                "project_key": str(row.get("project_key") or "").strip(),
                "display_name": str(row.get("display_name") or row.get("project_key") or "").strip(),
                "role_owner": str(row.get("role_owner") or "zeus").strip(),
                "stack": str(row.get("stack") or "unknown").strip(),
                "default_workdir": str(row.get("default_workdir") or ".").strip(),
                "review_commands": row.get("review_commands", []) if isinstance(row.get("review_commands"), list) else [],
                "test_commands": row.get("test_commands", []) if isinstance(row.get("test_commands"), list) else [],
            }
        )
    return out

def naming_audit_rows() -> list[dict[str, Any]]:
    identity = load_olympus_identity()
    public_brand = str(identity.get("public_brand") or "Luminax Platform").strip()
    control_surface_name = str(identity.get("control_surface_name") or "Mount Olympus").strip()
    operator_name = str(identity.get("operator_name") or "Zeus").strip()
    roles = identity.get("roles") if isinstance(identity.get("roles"), dict) else {}
    role_names = []
    for key in ("hermes", "hermes_ops", "darius", "oracle_dodona", "oracle_siwa", "oracle_delphi"):
        row = roles.get(key) if isinstance(roles.get(key), dict) else {}
        name = str(row.get("display_name") or "").strip()
        if name and name not in role_names:
            role_names.append(name)
    targets = [
        {"path": LUMINAX_ROOT / "nameplate.txt", "must": [control_surface_name, operator_name, *role_names]},
        {
            "path": LUMINAX_ROOT / "details.txt",
            "must": [public_brand, control_surface_name, operator_name, *role_names],
            "must_not": ["Luminax is the Discord connector"],
        },
        {"path": LUMINAX_ROOT / "payload" / "luminax-ui" / "index.html", "must": [control_surface_name, operator_name, public_brand]},
        {"path": LUMINAX_ROOT / "payload" / "config" / "olympus-identity.json", "must": [public_brand, *role_names]},
        {"path": LUMINAX_ROOT / "payload" / "config" / "project-light-personality.json", "must": [operator_name, "Hermes Ops", "Oracle of Delphi"]},
        {"path": LUMINAX_ROOT / "payload" / "config" / "project-light-employees.json", "must": role_names},
    ]
    rows = []
    for target in targets:
        path = target["path"]
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            rows.append({"path": str(path), "status": "error", "detail": f"Unreadable: {exc}"})
            continue
        missing = [item for item in target.get("must", []) if str(item).lower() not in text.lower()]
        forbidden = [item for item in target.get("must_not", []) if str(item).lower() in text.lower()]
        issues = []
        if missing:
            issues.append("Missing: " + ", ".join(missing))
        if forbidden:
            issues.append("Forbidden legacy text: " + ", ".join(forbidden))
        rows.append(
            {
                "path": str(path),
                "status": "ok" if not missing and not forbidden else "warn",
                "detail": "All expected names present." if not issues else " | ".join(issues),
                "missing": missing,
                "forbidden": forbidden,
            }
        )
    return rows

def review_cycle_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok_count = sum(1 for row in rows if str(row.get("status") or "").lower() == "ok")
    warn_count = sum(1 for row in rows if str(row.get("status") or "").lower() == "warn")
    error_count = sum(1 for row in rows if str(row.get("status") or "").lower() == "error")
    return {
        "total": len(rows),
        "ok_count": ok_count,
        "warn_count": warn_count,
        "error_count": error_count,
    }


def workspace_deploy_verification_rows() -> list[dict[str, Any]]:
    registry = load_workspace_registry()
    atlas = load_workspace_atlas()
    atlas_rows = {
        str(row.get("project_key") or "").strip().lower(): row
        for row in atlas.get("projects", [])
        if isinstance(row, dict) and str(row.get("project_key") or "").strip()
    }
    rows = []
    for project in registry.get("projects", []):
        if not isinstance(project, dict):
            continue
        key = str(project.get("project_key") or "").strip().lower()
        atlas_row = atlas_rows.get(key, {})
        deploy_target = atlas_row.get("deploy_target") if isinstance(atlas_row.get("deploy_target"), dict) else {}
        verification = atlas_row.get("verification_profile") if isinstance(atlas_row.get("verification_profile"), dict) else {}
        local_tests = verification.get("local_test_commands", []) if isinstance(verification.get("local_test_commands"), list) else []
        remote_tests = verification.get("remote_test_commands", []) if isinstance(verification.get("remote_test_commands"), list) else []
        health_urls = verification.get("health_urls", []) if isinstance(verification.get("health_urls"), list) else []
        verify_after_apply = bool(deploy_target.get("verify_after_apply", False))
        status = "ready"
        issues = []
        if not local_tests:
            status = "warn"
            issues.append("Missing local test commands.")
        if verify_after_apply and not remote_tests and not health_urls:
            status = "warn"
            issues.append("Missing remote verification for a deploy target that requires it.")
        rows.append(
            {
                "project_key": str(project.get("project_key") or "").strip(),
                "display_name": str(project.get("display_name") or project.get("project_key") or "").strip(),
                "deploy_target": deploy_target,
                "local_test_commands": local_tests,
                "remote_test_commands": remote_tests,
                "health_urls": health_urls,
                "status": status,
                "issues": issues,
            }
        )
    return rows

def _service_identity_meta(name: str) -> dict[str, Any]:
    profile = _olympus_profile_by_key("services", name)
    if profile:
        fallback = str(profile.get("display_name") or profile.get("public_name") or profile.get("key") or name or "service").strip()
        return {
            "display_name": fallback,
            "public_name": str(profile.get("public_name") or fallback).strip() or fallback,
            "role": str(profile.get("role") or profile.get("summary") or "").strip(),
        }
    identity = load_olympus_identity()
    service_labels = identity.get("service_labels") if isinstance(identity.get("service_labels"), dict) else {}
    row = service_labels.get(str(name or "").strip()) if isinstance(service_labels.get(str(name or "").strip()), dict) else {}
    fallback = " ".join(part.capitalize() for part in str(name or "").replace("_", "-").split("-") if part).strip() or str(name or "service").strip() or "service"
    return {
        "display_name": str(row.get("display_name") or fallback).strip(),
        "public_name": str(row.get("public_name") or row.get("display_name") or fallback).strip(),
        "role": str(row.get("role") or "").strip(),
    }

def enforce_maintenance_guard(action: str, *, write_intent: bool = False, service_action: bool = False, self_apply: bool = False):
    state = load_maintenance_mode()
    policy = state.get("policy") if isinstance(state.get("policy"), dict) else _maintenance_policy(state.get("mode", "normal"))
    mode = str(state.get("mode") or "normal").strip().lower()
    blocked = False
    if write_intent and not bool(policy.get("write_allowed", False)):
        blocked = True
    if service_action and not bool(policy.get("service_actions_allowed", False)):
        blocked = True
    if self_apply and not bool(policy.get("self_apply_allowed", False)):
        blocked = True
    if blocked:
        raise HTTPException(status_code=409, detail=f"{action} blocked while maintenance mode '{mode}' is active. {policy.get('description', '').strip()}")
    return state

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

def open_incidents(limit: int = 50):
    ensure_system_incidents_table()
    try:
        conn = db_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT incident_key, source_service, severity, status, summary, detail_text, occurrence_count, "
            "acknowledged_by, acknowledged_at, escalation_level, last_notified_at, next_notification_at, notification_state_json, last_seen_at "
            "FROM system_incidents WHERE status='open' "
            "ORDER BY FIELD(severity, 'critical', 'high', 'medium', 'low'), last_seen_at DESC LIMIT %s",
            (max(1, min(int(limit or 50), 200)),),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception:
        rows = []
    for row in rows:
        row["notification_state_json"] = decode_jsonish(row.get("notification_state_json")) or {}
    return rows

def incident_summary(limit: int = 12):
    rows = open_incidents(limit=limit)
    return {
        "open_count": len(rows),
        "critical_count": len([row for row in rows if str(row.get("severity") or "").strip().lower() == "critical"]),
        "acked_count": len([row for row in rows if str(row.get("acknowledged_by") or "").strip()]),
        "rows": rows,
    }

def acknowledge_system_incident(incident_key: str, actor: str = "", note: str = ""):
    clean_key = str(incident_key or "").strip()[:191]
    if not clean_key:
        return
    ensure_system_incidents_table()
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE system_incidents SET acknowledged_by=%s, acknowledged_at=NOW(), next_notification_at=NULL, notification_state_json=%s WHERE incident_key=%s",
            (
                str(actor or "operator").strip()[:120] or "operator",
                json.dumps({"note": str(note or "").strip()[:1200]}, ensure_ascii=False),
                clean_key,
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass

def record_incident_notification(incident_key: str, delivered_via: list[str] | None = None, escalation_level: int = 1, next_reminder_sec: int = 0, note: str = ""):
    clean_key = str(incident_key or "").strip()[:191]
    if not clean_key:
        return
    ensure_system_incidents_table()
    try:
        conn = db_conn()
        cur = conn.cursor()
        if int(next_reminder_sec or 0) > 0:
            cur.execute(
                "UPDATE system_incidents SET escalation_level=%s, last_notified_at=NOW(), next_notification_at=DATE_ADD(NOW(), INTERVAL %s SECOND), notification_state_json=%s WHERE incident_key=%s",
                (
                    max(0, int(escalation_level or 0)),
                    max(0, int(next_reminder_sec or 0)),
                    json.dumps({"delivered_via": delivered_via or [], "note": str(note or "").strip()[:1200]}, ensure_ascii=False),
                    clean_key,
                ),
            )
        else:
            cur.execute(
                "UPDATE system_incidents SET escalation_level=%s, last_notified_at=NOW(), next_notification_at=NULL, notification_state_json=%s WHERE incident_key=%s",
                (
                    max(0, int(escalation_level or 0)),
                    json.dumps({"delivered_via": delivered_via or [], "note": str(note or "").strip()[:1200]}, ensure_ascii=False),
                    clean_key,
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

def evaluate_windows_nodes():
    rows = latest_windows_nodes(limit=50)
    filtered_rows = []
    seen_identity_keys = set()
    for row in rows:
        hostname = str(row.get("hostname") or "").strip().lower()
        node_key = str(row.get("node_key") or "").strip().lower()
        identity_key = hostname or node_key
        if identity_key and identity_key in seen_identity_keys:
            continue
        if identity_key:
            seen_identity_keys.add(identity_key)
        filtered_rows.append(row)
    rows = filtered_rows
    now_ts = int(time.time())
    evaluated = []
    stale_count = 0
    degraded_count = 0
    for row in rows:
        capabilities = decode_jsonish(row.get("capabilities_json")) or {}
        services = decode_jsonish(row.get("services_json")) or {}
        last_seen_epoch = unix_ts_from_value(row.get("last_seen_at"))
        age_sec = max(0, now_ts - last_seen_epoch) if last_seen_epoch else None
        issues = []
        edge_only = bool(capabilities.get("gpu_edge")) and not bool(capabilities.get("assistant_core_local")) and not bool(capabilities.get("public_web_host"))
        if capabilities.get("docker_local") is False and not edge_only:
            issues.append("local docker unavailable")
        if capabilities.get("mysql_local") is False and not edge_only:
            issues.append("local mysql unavailable")
        status_eval = "ok"
        if age_sec is None or age_sec >= max(60, WINDOWS_NODE_STALE_SEC):
            status_eval = "stale"
            stale_count += 1
        elif issues:
            status_eval = "degraded"
            degraded_count += 1
        evaluated.append(
            {
                "node_key": row.get("node_key"),
                "hostname": row.get("hostname"),
                "os_version": row.get("os_version"),
                "status": row.get("status"),
                "evaluated_status": status_eval,
                "age_sec": age_sec,
                "issues": issues,
                "capabilities": capabilities,
                "services": services,
                "last_seen_at": row.get("last_seen_at"),
            }
        )
    return {
        "rows": evaluated,
        "stale_count": stale_count,
        "degraded_count": degraded_count,
    }

def evaluate_service_watch_state():
    docker_rows = docker_services()
    docker_runtime = docker_runtime_status()
    rows = []
    by_name = {}
    for name, meta in SERVICE_WATCH_CATALOG.items():
        container_name = str(meta.get("container") or name).strip()
        row = _service_row(container_name, docker_rows=docker_rows)
        running = docker_service_running(row)
        startup = str(meta.get("startup") or "core").strip()
        probe_type = str(meta.get("probe") or "docker").strip()
        external = bool(meta.get("external", False))
        status = "ok"
        detail = ""
        probe_payload = None
        if probe_type == "mysql":
            probe_payload = mysql_probe()
            if probe_payload.get("ok"):
                status = "ok"
                detail = str(probe_payload.get("detail") or "")
            else:
                status = "degraded" if running else "failed"
                detail = str(probe_payload.get("detail") or "mysql unavailable")
        elif probe_type == "redis":
            probe_payload = redis_probe()
            if probe_payload.get("ok"):
                status = "ok"
                detail = str(probe_payload.get("detail") or "")
            else:
                status = "degraded" if running else "failed"
                detail = str(probe_payload.get("detail") or "redis unavailable")
        elif probe_type == "http":
            if external:
                probe_payload = http_probe(
                    str(meta.get("url") or ""),
                    timeout=4 if startup == "core" else 3,
                    healthy_status_codes=[int(code) for code in (meta.get("healthy_status_codes") or []) if str(code).strip()],
                )
                if probe_payload.get("ok"):
                    status = "ok"
                    detail = str(probe_payload.get("detail") or "")
                else:
                    status = "degraded"
                    detail = str(probe_payload.get("detail") or "http probe failed")
            elif not running and startup != "core":
                status = "idle"
                detail = "service not active"
            elif not running:
                status = "failed"
                detail = "container not running"
            else:
                probe_payload = http_probe(
                    str(meta.get("url") or ""),
                    timeout=4 if startup == "core" else 3,
                    healthy_status_codes=[int(code) for code in (meta.get("healthy_status_codes") or []) if str(code).strip()],
                )
                if probe_payload.get("ok"):
                    status = "ok"
                    detail = str(probe_payload.get("detail") or "")
                else:
                    status = "degraded"
                    detail = str(probe_payload.get("detail") or "http probe failed")
        elif probe_type == "tcp":
            if external:
                probe_payload = tcp_probe(str(meta.get("host") or ""), int(meta.get("port") or 0))
                if probe_payload.get("ok"):
                    status = "ok"
                    detail = str(probe_payload.get("detail") or "")
                else:
                    status = "degraded"
                    detail = str(probe_payload.get("detail") or "tcp probe failed")
            elif not running and startup != "core":
                status = "idle"
                detail = "service not active"
            elif not running:
                status = "failed"
                detail = "container not running"
            else:
                probe_payload = tcp_probe(str(meta.get("host") or ""), int(meta.get("port") or 0))
                if probe_payload.get("ok"):
                    status = "ok"
                    detail = str(probe_payload.get("detail") or "")
                else:
                    status = "degraded"
                    detail = str(probe_payload.get("detail") or "tcp probe failed")
        elif probe_type == "platform":
            status = str(docker_runtime.get("status") or "failed")
            detail = str(docker_runtime.get("detail") or "docker runtime unavailable")
        else:
            if running:
                status = "ok"
                detail = "container running"
            else:
                status = "failed" if startup == "core" else "idle"
                detail = "container not running" if startup == "core" else "service not active"
        if not docker_runtime.get("ok") and status == "failed":
            detail = f"{detail}; {docker_runtime.get('detail')}"
        row_payload = {
            "name": name,
            "container": container_name,
            "startup": startup,
            "severity": str(meta.get("severity") or "medium"),
            "repair_action": str(meta.get("repair_action") or "service"),
            "kind": "edge" if external else "docker",
            "probe": probe_type,
            "dependencies": list(meta.get("dependencies") or []),
            "status": status,
            "detail": detail,
            "container_state": "external" if external else ("running" if running else "stopped"),
        }
        if probe_payload and probe_payload.get("data") is not None:
            row_payload["probe_data"] = probe_payload.get("data")
        row_payload = _apply_optional_service_semantics(name, row_payload)
        rows.append(row_payload)
        by_name[name] = row_payload
    for row in rows:
        blocking = [dep for dep in row.get("dependencies", []) if str(by_name.get(dep, {}).get("status") or "") not in {"ok", "idle"}]
        if blocking and row["status"] == "ok":
            row["status"] = "degraded"
            row["detail"] = ((row.get("detail") or "") + f"; dependency issue: {', '.join(blocking)}").strip("; ")
            row["dependency_issues"] = blocking
    windows = evaluate_windows_nodes()
    failing = [row for row in rows if row.get("status") in {"failed", "stale"}]
    degraded = [row for row in rows if row.get("status") == "degraded"]
    overall = "ok"
    if failing or int(windows.get("stale_count") or 0) > 0:
        overall = "failed"
    elif degraded or int(windows.get("degraded_count") or 0) > 0:
        overall = "degraded"
    return {
        "status": overall,
        "generated_at": int(time.time()),
        "docker_runtime": docker_runtime,
        "rows": rows,
        "by_name": by_name,
        "failing_services": [row["name"] for row in failing],
        "degraded_services": [row["name"] for row in degraded],
        "windows_nodes": windows,
        "recent_repairs": recent_service_watch_repairs(limit=10),
        "incidents": incident_summary(limit=8),
    }

def compose_background_restart(service_names: list[str]) -> dict[str, Any]:
    safe = [name for name in service_names if valid_service_name(name)]
    if not safe:
        return {"status": "skipped", "detail": "no valid services"}
    joined = " ".join(safe)
    container_names = _service_container_names(safe)
    container_joined = " ".join(container_names)
    compose_restart = compose_command(f"restart {joined}")
    fallback = f"docker restart {container_joined} >/tmp/luminax_watch_restart.log 2>&1" if container_joined else "true"
    return sh(
        "cd /workspace/luminax && "
        f"(nohup /bin/sh -lc 'sleep 2; {compose_restart} >/tmp/luminax_watch_restart.log 2>&1 || {fallback}' >/dev/null 2>&1 &)"
    )

def wait_for_watch_state(service_name: str, desired: set[str], timeout_sec: int = 45) -> dict[str, Any]:
    deadline = time.time() + max(5, int(timeout_sec or 45))
    last_row = None
    while time.time() < deadline:
        summary = evaluate_service_watch_state()
        last_row = (summary.get("by_name") or {}).get(service_name)
        if isinstance(last_row, dict) and str(last_row.get("status") or "") in desired:
            return {"ok": True, "row": last_row}
        time.sleep(3)
    return {"ok": False, "row": last_row}

def perform_watch_repair(service_name: str, requested_by: str = "operator", reason: str = "") -> dict[str, Any]:
    if service_name not in SERVICE_WATCH_CATALOG:
        raise HTTPException(status_code=404, detail="Unknown watch service")
    repair_id = record_service_watch_repair(service_name, "repair", requested_by, reason, {"requested_by": requested_by, "reason": reason})
    try:
        if service_name == "mysql":
            mysql_containers = " ".join(_service_container_names(["mysql"]))
            result = sh(
                "cd /workspace/luminax && "
                f"({compose_command('up -d mysql')} || "
                f"{('docker start ' + mysql_containers + ' || docker restart ' + mysql_containers) if mysql_containers else 'true'} || true)"
            )
            wait = wait_for_watch_state("mysql", {"ok"}, timeout_sec=45)
            sync_services = ["assistant-core", "zeus-discord", "community-discord", "underfoot-world", "discord-bot", "realtime-gateway", "broadcast-conductor"]
            sync_containers = " ".join(_service_container_names(sync_services))
            sync_restart = sh(
                "cd /workspace/luminax && "
                f"({compose_command('restart assistant-core zeus-discord community-discord underfoot-world discord-bot realtime-gateway broadcast-conductor')} || "
                f"{('docker restart ' + sync_containers) if sync_containers else 'true'} || true)"
            )
            async_restart = compose_background_restart(["admin-api", "lumi-daemon"])
            verification = wait_for_watch_state("underfoot-world", {"ok"}, timeout_sec=30)
            verified = bool(wait.get("ok")) and bool(verification.get("ok"))
            payload = {
                "status": "verified" if verified else "failed",
                "service": service_name,
                "repair_id": repair_id,
                "bootstrap": result,
                "mysql": wait.get("row"),
                "dependent_restart": sync_restart,
                "control_plane_restart": async_restart,
                "underfoot_world": verification.get("row"),
            }
            complete_service_watch_repair(repair_id, "verified" if verified else "failed", json.dumps(payload, ensure_ascii=False)[:16000], payload)
            return payload
        if service_name == "underfoot-world":
            underfoot_containers = _service_container_names(["underfoot-world"])
            logs_target = underfoot_containers[0] if underfoot_containers else "underfoot-world"
            restart_fallback = f"docker restart {' '.join(underfoot_containers)}" if underfoot_containers else "true"
            logs = sh(f"cd /workspace/luminax && (docker logs --tail 160 {logs_target} 2>&1 || true)")
            restart = sh(f"cd /workspace/luminax && ({compose_command('restart underfoot-world')} || {restart_fallback} || true)")
            wait = wait_for_watch_state("underfoot-world", {"ok"}, timeout_sec=40)
            payload = {
                "status": "verified" if wait.get("ok") else "failed",
                "service": service_name,
                "repair_id": repair_id,
                "logs_excerpt": ((logs.get("stdout") or "") + "\n" + (logs.get("stderr") or "")).strip()[-4000:],
                "restart": restart,
                "verification": wait.get("row"),
            }
            complete_service_watch_repair(repair_id, "verified" if wait.get("ok") else "failed", json.dumps(payload, ensure_ascii=False)[:16000], payload)
            return payload
        if service_name == "docker":
            result = run_system_service("start", "docker")
            payload = {"status": "queued", "service": service_name, "repair_id": repair_id, "result": result}
            complete_service_watch_repair(repair_id, "queued", json.dumps(payload, ensure_ascii=False)[:16000], payload)
            return payload
        container_names = " ".join(_service_container_names([service_name]))
        restart = sh(
            "cd /workspace/luminax && "
            f"({compose_command(f'restart {service_name}')} || "
            f"{('docker restart ' + container_names) if container_names else 'true'} || true)"
        )
        wait = wait_for_watch_state(service_name, {"ok"}, timeout_sec=35)
        payload = {
            "status": "verified" if wait.get("ok") else "failed",
            "service": service_name,
            "repair_id": repair_id,
            "restart": restart,
            "verification": wait.get("row"),
        }
        complete_service_watch_repair(repair_id, "verified" if wait.get("ok") else "failed", json.dumps(payload, ensure_ascii=False)[:16000], payload)
        return payload
    except Exception as exc:
        payload = {"status": "failed", "service": service_name, "repair_id": repair_id, "detail": str(exc)}
        complete_service_watch_repair(repair_id, "failed", str(exc)[:16000], payload)
        raise

@app.get("/telemetry/latest")
async def telemetry_latest():
    cpu = psutil.cpu_percent(interval=0.2)
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    services = docker_services()
    watch = evaluate_service_watch_state()
    voice_health = build_olympus_voice_health(watch=watch)
    olympus_admin_health = build_olympus_admin_health(watch=watch, voice_health=voice_health)
    underfoot_health = fetch_json(f"{UNDERFOOT_WORLD_URL}/api/health", timeout=8)
    underfoot_voice = fetch_json(f"{UNDERFOOT_VOICE_GATEWAY_URL}/api/voice/overview", timeout=6)
    underfoot_reports = fetch_json(f"{UNDERFOOT_WORLD_URL}/api/reports/recent?limit=20", timeout=8)
    health = {row["name"]: {"status": row.get("status"), "detail": row.get("detail"), "startup": row.get("startup")} for row in watch.get("rows", [])}
    watch.pop("by_name", None)
    runtime_policy = load_runtime_policy()
    return {
        "cpu_percent": cpu,
        "memory_percent": vm.percent,
        "disk_percent": disk.percent,
        "docker_services": services,
        "health": health,
        "watch": watch,
        "voice_health": voice_health,
        "olympus_admin_health": olympus_admin_health,
        "underfoot_health": underfoot_health,
        "underfoot_voice": underfoot_voice,
        "underfoot_reports": underfoot_reports,
        "maintenance": load_maintenance_mode(),
        "runtime_policy": runtime_policy,
        "worker_pause": {
            "paused": bool(runtime_policy.get("workers_paused", False)),
            "reason": str(runtime_policy.get("worker_pause_reason", "")).strip(),
        },
        "incidents": incident_summary(limit=8),
    }

@app.get("/underfoot/reports/overview")
async def underfoot_reports_overview(limit: int = Query(default=20)):
    return fetch_json(f"{UNDERFOOT_WORLD_URL}/api/reports/recent?limit={max(1, min(int(limit or 20), 200))}", timeout=8)


@app.get("/underfoot/health/overview")
async def underfoot_health_overview():
    return fetch_json(f"{UNDERFOOT_WORLD_URL}/api/health", timeout=8)

@app.get("/daemon/state")
async def daemon_state():
    try:
        conn = db_conn(); cur = conn.cursor(dictionary=True)
        cur.execute("SELECT state_key, state_value, meta_json, created_at FROM daemon_state ORDER BY id DESC LIMIT 25")
        rows = cur.fetchall(); cur.close(); conn.close()
        return {"rows": rows}
    except Exception as e:
        return {"rows": [], "error": str(e)}

@app.get("/hive/debate/latest")
async def hive_debate_latest():
    return fetch_json(f"{CORE_API_URL}/hive/debate/latest", timeout=8)

@app.get("/hive/status")
async def hive_status():
    status = fetch_json(f"{CORE_API_URL}/hive/status", timeout=8)
    latest = fetch_json(f"{CORE_API_URL}/hive/debate/latest", timeout=8)
    service_rows = [row for row in (await services_status()).get("services", []) if (row.get("name") or "") in {"lumi-hive-dreamer", "lumi-hive-engineer", "lumi-hive-critic"}]
    degraded = status.get("status") == "error" or latest.get("status") == "error" or any((row.get("status") or "") not in {"running", "ok"} for row in service_rows)
    return {
        "status": "degraded" if degraded else "ok",
        "hive": status,
        "latest_debate": latest,
        "service_rows": service_rows,
    }

@app.get("/games/overview")
async def games_overview():
    return fetch_json(f"{CORE_API_URL}/games/overview", timeout=10)

@app.get("/finance/overview")
async def finance_overview():
    return fetch_json(f"{CORE_API_URL}/finance/overview", timeout=10)

@app.get("/money/overview")
async def money_overview(window: str = Query(default="30"), view: str = Query(default="all")):
    qs = f"?window={window}&view={view}"
    return fetch_json(f"{CORE_API_URL}/money/overview{qs}", timeout=10)

@app.get("/money/accounts")
async def money_accounts(view: str = Query(default="all")):
    return fetch_json(f"{CORE_API_URL}/money/accounts?view={view}", timeout=10)

@app.get("/money/upcoming-payments")
async def money_upcoming_payments(window: str = Query(default="30")):
    return fetch_json(f"{CORE_API_URL}/money/upcoming-payments?window={window}", timeout=10)

@app.post("/money/action")
async def money_action(req: MoneyActionRequest):
    validate(req.token)
    import urllib.request
    payload = json.dumps(req.model_dump()).encode("utf-8")
    url = f"{CORE_API_URL}/money/action"
    req_obj = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req_obj, timeout=20) as r:
            body = r.read().decode("utf-8", errors="ignore")
            try:
                return json.loads(body)
            except Exception:
                return {"status": "ok", "raw": body[:4000]}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/empire/overview")
async def empire_overview():
    empire = fetch_json(f"{CORE_API_URL}/empire/overview", timeout=12)
    windows_rows = latest_windows_nodes(limit=20)
    autopilot = load_autopilot_config()
    return {
        "status": "degraded" if empire.get("status") == "error" else "ok",
        "empire": empire,
        "windows_nodes": windows_rows,
        "autopilot": autopilot.get("config", {}),
    }

@app.get("/services/status")
async def services_status():
    watch = evaluate_service_watch_state()
    items = [
        {
            "name": row.get("name"),
            **_service_identity_meta(str(row.get("name") or "")),
            "kind": row.get("kind") or "docker",
            "status": row.get("status"),
            "info": row.get("detail"),
            "startup": row.get("startup"),
            "severity": row.get("severity"),
            "dependencies": row.get("dependencies"),
        }
        for row in watch.get("rows", [])
    ]
    return {"services": items}

@app.get("/services/activity")
async def services_activity():
    rows = []
    raw = sh("cd /workspace/luminax && (docker stats --no-stream --format '{{json .}}' || true)")["stdout"]
    for line in [l for l in raw.splitlines() if l.strip()]:
        try:
            item = json.loads(line)
        except Exception:
            continue
        rows.append({
            "container": item.get("Name"),
            "cpu": item.get("CPUPerc"),
            "mem": item.get("MemUsage"),
            "mem_perc": item.get("MemPerc"),
            "net_io": item.get("NetIO"),
            "block_io": item.get("BlockIO"),
            "pids": item.get("PIDs"),
        })
    return {"rows": rows}

@app.get("/logs/tail")
async def logs_tail(name: str = Query(...)):
    validate_service_or_400(name)
    res = sh(f"cd /workspace/luminax && (docker logs --tail 200 {name} 2>&1 || true)")
    return {"name": name, "output": (res["stdout"] + "\n" + res["stderr"]).strip()[-20000:]}

@app.post("/services/restart")
async def services_restart(req: SvcRequest):
    validate(req.token); validate_service_or_400(req.name)
    enforce_maintenance_guard("Service restart", service_action=True)
    return run_system_service("restart", req.name) if req.name in {"nginx", "docker"} else sh(f"cd /workspace/luminax && (docker compose restart {req.name} || docker restart {req.name})")

@app.post("/services/start")
async def services_start(req: SvcRequest):
    validate(req.token); validate_service_or_400(req.name)
    enforce_maintenance_guard("Service start", service_action=True)
    return run_system_service("start", req.name) if req.name in {"nginx", "docker"} else sh(f"cd /workspace/luminax && (docker compose up -d {req.name} || true)")

@app.post("/services/stop")
async def services_stop(req: SvcRequest):
    validate(req.token); validate_service_or_400(req.name)
    enforce_maintenance_guard("Service stop", service_action=True)
    return run_system_service("stop", req.name) if req.name in {"nginx", "docker"} else sh(f"cd /workspace/luminax && (docker compose stop {req.name} || docker stop {req.name})")

@app.get("/services/watch/status")
async def services_watch_status(token: str = Query(default="")):
    validate_internal_service(token)
    summary = evaluate_service_watch_state()
    summary.pop("by_name", None)
    return summary

@app.post("/services/watch/repair")
async def services_watch_repair(req: ServiceRepairRequest):
    validate_internal_service(req.token)
    validate_service_or_400(req.name)
    enforce_maintenance_guard("Service repair", service_action=True)
    result = perform_watch_repair(req.name, requested_by=req.requested_by or "operator", reason=req.reason or "")
    return result

@app.post("/windows-node/heartbeat")
async def windows_node_heartbeat(req: NodeHeartbeatRequest):
    validate_node_token(req.token)
    validate_node_key_or_400(req.node_key)
    ensure_windows_link_tables()
    conn = db_conn(); cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO windows_nodes (node_key, hostname, os_version, status, capabilities_json, services_json, last_seen_at, meta_json)
        VALUES (%s,%s,%s,%s,%s,%s,NOW(),%s)
        ON DUPLICATE KEY UPDATE
          hostname=VALUES(hostname),
          os_version=VALUES(os_version),
          status='online',
          capabilities_json=VALUES(capabilities_json),
          services_json=VALUES(services_json),
          last_seen_at=NOW(),
          meta_json=VALUES(meta_json)
        """,
        (
            req.node_key,
            req.hostname,
            req.os_version,
            'online',
            json.dumps(req.capabilities or {}),
            json.dumps(req.services or {}),
            json.dumps(req.meta or {}),
        ),
    )
    cur.execute(
        "INSERT INTO windows_node_heartbeats (node_key, hostname, capabilities_json, services_json, metrics_json, meta_json) VALUES (%s,%s,%s,%s,%s,%s)",
        (
            req.node_key,
            req.hostname,
            json.dumps(req.capabilities or {}),
            json.dumps(req.services or {}),
            json.dumps(req.metrics or {}),
            json.dumps(req.meta or {}),
        ),
    )
    conn.commit(); cur.close(); conn.close()
    return {"status": "ok", "node_key": req.node_key, "hostname": req.hostname}

@app.get("/windows-node/list")
async def windows_node_list(token: str = Query(default="")):
    validate(token)
    try:
        ensure_windows_link_tables()
        conn = db_conn(); cur = conn.cursor(dictionary=True)
        cur.execute("SELECT node_key, hostname, os_version, status, last_seen_at, capabilities_json, services_json, meta_json FROM windows_nodes ORDER BY last_seen_at DESC, updated_at DESC LIMIT 200")
        rows = cur.fetchall(); cur.close(); conn.close()
        for row in rows:
            row["capabilities_json"] = decode_jsonish(row.get("capabilities_json"))
            row["services_json"] = decode_jsonish(row.get("services_json"))
            row["meta_json"] = decode_jsonish(row.get("meta_json"))
        return {"rows": rows}
    except Exception as e:
        return {"rows": [], "error": str(e)}

@app.get("/windows-node/latest")
async def windows_node_latest(
    node_key: str = Query(default=""),
    token: str = Query(default=""),
    summary: bool = Query(default=False),
):
    validate_control_or_node_token(token, node_key=node_key)
    try:
        if summary:
            return load_latest_windows_node_status(node_key=node_key)
        ensure_windows_link_tables()
        if node_key:
            validate_node_key_or_400(node_key)
        conn = db_conn(); cur = conn.cursor(dictionary=True)
        if node_key:
            cur.execute("SELECT * FROM windows_node_heartbeats WHERE node_key=%s ORDER BY id DESC LIMIT 1", (node_key,))
        else:
            cur.execute("SELECT * FROM windows_node_heartbeats ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        cur.close(); conn.close()
        if row:
            row["capabilities_json"] = decode_jsonish(row.get("capabilities_json"))
            row["services_json"] = decode_jsonish(row.get("services_json"))
            row["metrics_json"] = decode_jsonish(row.get("metrics_json"))
            row["meta_json"] = decode_jsonish(row.get("meta_json"))
        return {"row": row}
    except Exception as e:
        return {"row": None, "error": str(e)}

@app.get("/windows-node/tool-catalog")
async def windows_node_tool_catalog(token: str = Query(default="")):
    validate(token)
    return {"rows": windows_tool_catalog()}

@app.get("/windows-node/jobs")
async def windows_node_jobs(node_key: str = Query(default=""), status: str = Query(default=""), limit: int = Query(default=120), token: str = Query(default="")):
    validate(token)
    try:
        ensure_windows_link_tables()
        lim = max(1, min(int(limit or 120), 500))
        conn = db_conn(); cur = conn.cursor(dictionary=True)
        clauses = []
        params: list[Any] = []
        if node_key:
            validate_node_key_or_400(node_key)
            clauses.append("(node_key=%s OR node_key='*')")
            params.append(node_key)
        if status:
            clauses.append("status=%s")
            params.append(status.strip().lower())
        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cur.execute(
            "SELECT id, node_key, action_key, status, payload_json, result_json, error_text, requested_by, requested_via, created_at, dispatched_at, completed_at "
            f"FROM windows_node_jobs{where_sql} ORDER BY id DESC LIMIT %s",
            tuple(params + [lim]),
        )
        rows = cur.fetchall(); cur.close(); conn.close()
        for row in rows:
            row["payload_json"] = decode_jsonish(row.get("payload_json"))
            row["result_json"] = decode_jsonish(row.get("result_json"))
        return {"rows": rows}
    except Exception as e:
        return {"rows": [], "error": str(e)}

@app.post("/windows-node/action/request")
async def windows_node_action_request(req: WindowsNodeActionRequest):
    validate(req.token)
    validate_node_key_or_400(req.node_key)
    ensure_windows_link_tables()
    allowed = {r["action_key"] for r in windows_tool_catalog()}
    if req.action_key not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported windows-node action")
    payload = {
        "args": req.args or {},
        "dry_run": bool(req.dry_run),
        "note": (req.note or "")[:500],
    }
    conn = db_conn(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO windows_node_jobs (node_key, requested_by, requested_via, job_type, action_key, payload_json, status, meta_json) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            req.node_key,
            "operator",
            "ui",
            "tool",
            req.action_key,
            json.dumps(payload),
            "queued",
            json.dumps({"created_via": "admin-api"}),
        ),
    )
    job_id = cur.lastrowid
    conn.commit(); cur.close(); conn.close()
    return {"status": "queued", "job_id": job_id, "node_key": req.node_key, "action_key": req.action_key}

@app.post("/windows-node/action/poll")
async def windows_node_action_poll(req: WindowsNodePollRequest):
    validate_node_token(req.token)
    validate_node_key_or_400(req.node_key)
    ensure_windows_link_tables()
    limit = max(1, min(int(req.max_jobs or 8), 25))
    conn = db_conn(); cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT id, node_key, action_key, payload_json, requested_by, requested_via, created_at "
        "FROM windows_node_jobs "
        "WHERE status='queued' AND (node_key=%s OR node_key='*') "
        "ORDER BY id ASC LIMIT %s",
        (req.node_key, limit),
    )
    jobs = cur.fetchall() or []
    job_ids = [int(j["id"]) for j in jobs if j.get("id") is not None]
    if job_ids:
        in_sql = ",".join(["%s"] * len(job_ids))
        cur.execute(
            f"UPDATE windows_node_jobs SET status='dispatched', dispatched_at=NOW(), updated_at=NOW() WHERE id IN ({in_sql})",
            tuple(job_ids),
        )
    conn.commit(); cur.close(); conn.close()
    out = []
    for j in jobs:
        out.append({
            "job_id": j.get("id"),
            "node_key": j.get("node_key"),
            "action_key": j.get("action_key"),
            "payload": decode_jsonish(j.get("payload_json")) or {},
            "requested_by": j.get("requested_by"),
            "requested_via": j.get("requested_via"),
            "created_at": j.get("created_at"),
        })
    return {"rows": out}

@app.post("/windows-node/action/complete")
async def windows_node_action_complete(req: WindowsNodeCompleteRequest):
    validate_node_token(req.token)
    validate_node_key_or_400(req.node_key)
    ensure_windows_link_tables()
    normalized = (req.status or "completed").strip().lower()
    if normalized not in {"completed", "failed", "rejected", "cancelled"}:
        normalized = "completed"
    conn = db_conn(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, node_key, status FROM windows_node_jobs WHERE id=%s LIMIT 1", (req.job_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Job not found")
    row_node = str(row.get("node_key") or "")
    if row_node not in {"*", req.node_key}:
        cur.close(); conn.close()
        raise HTTPException(status_code=403, detail="Job not assigned to this node")
    cur = conn.cursor()
    cur.execute(
        "UPDATE windows_node_jobs SET status=%s, result_json=%s, error_text=%s, completed_at=NOW(), updated_at=NOW() WHERE id=%s",
        (
            normalized,
            json.dumps(req.result or {}),
            (req.error or "")[:4000] if normalized != "completed" else "",
            req.job_id,
        ),
    )
    conn.commit(); cur.close(); conn.close()
    return {"status": "ok", "job_id": req.job_id, "final_status": normalized}

@app.get("/incidents/open")
async def incidents_open(token: str = Query(default=""), limit: int = Query(default=50)):
    validate_internal_service(token)
    return {"rows": open_incidents(limit=limit)}

@app.post("/incidents/ack")
async def incidents_ack(req: IncidentActionRequest):
    validate_internal_service(req.token)
    acknowledge_system_incident(req.incident_key, actor=req.actor or current_web_auth_user() or "operator", note=req.note or "")
    return {"status": "ok", "incident_key": req.incident_key, "actor": req.actor or current_web_auth_user() or "operator"}

@app.post("/incidents/resolve")
async def incidents_resolve(req: IncidentActionRequest):
    validate_internal_service(req.token)
    resolve_system_incident(req.incident_key, resolution_note=req.note or "")
    return {"status": "ok", "incident_key": req.incident_key}

@app.post("/incidents/notify")
async def incidents_notify(req: IncidentNotifyRequest):
    validate_internal_service(req.token)
    record_incident_notification(
        req.incident_key,
        delivered_via=req.delivered_via or [],
        escalation_level=max(0, int(req.escalation_level or 0)),
        next_reminder_sec=max(0, int(req.next_reminder_sec or 0)),
        note=req.note or "",
    )
    return {"status": "ok", "incident_key": req.incident_key}

@app.get("/backups/list")
async def backups_list():
    return {"rows": backup_dirs(), "unpinned_limit": 2}

@app.post("/backups/keep")
async def backups_keep(req: BackupUpdateRequest):
    validate(req.token)
    path = backup_path_or_400(req.name)
    keep_file = path / ".keep"
    keep_value = bool(req.keep)
    if keep_value:
        keep_file.write_text("keep=true\n", encoding="utf-8")
    else:
        if keep_file.exists():
            keep_file.unlink()
    return {"status": "ok", "name": req.name, "keep": keep_value}

@app.post("/backups/delete")
async def backups_delete(req: BackupUpdateRequest):
    validate(req.token)
    path = backup_path_or_400(req.name)
    shutil.rmtree(path)
    return {"status": "ok", "deleted": req.name}

@app.post("/backups/restore")
async def backups_restore(req: BackupUpdateRequest):
    validate(req.token)
    if redis_client.get("luminax_maintenance_mode") != "true":
        raise HTTPException(status_code=409, detail="Backup restore requires maintenance mode")
    if not ALLOW_LIVE_BACKUP_RESTORE:
        raise HTTPException(status_code=403, detail="Live backup restore is disabled until the guarded restore flow is enabled")
    path = backup_path_or_400(req.name)
    target = Path("/workspace/luminax")
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=500, detail="Workspace path missing")
    stamp = subprocess.run(["/bin/sh", "-lc", "date +%Y%m%d_%H%M%S"], capture_output=True, text=True).stdout.strip() or "restore"
    restore_log = target / "logs" / f"restore_{stamp}.txt"
    cmd = (
        f"cd /workspace/luminax && "
        f"(docker compose down --remove-orphans || docker-compose down --remove-orphans || true) && "
        f"rsync -a --delete --exclude 'mysql_data' --exclude 'redis_data' --exclude 'qdrant_data' "
        f"'{str(path)}/' '/workspace/luminax/' && "
        f"(docker compose up -d --build || docker-compose up -d --build)"
    )
    result = sh(cmd)
    try:
        restore_log.parent.mkdir(parents=True, exist_ok=True)
        restore_log.write_text((result.get("stdout", "") + "\n" + result.get("stderr", ""))[-30000:], encoding="utf-8")
    except Exception:
        pass
    return {"status": "ok" if result.get("returncode") == 0 else "failed", "backup": req.name, "returncode": result.get("returncode"), "restore_log": str(restore_log)}

@app.post("/backups/prune")
async def backups_prune(req: BackupUpdateRequest):
    validate(req.token)
    keep_unpinned = 2
    kept_unpinned = 0
    removed = []
    for row in backup_dirs():
        p = backup_path_or_400(row["name"])
        if row.get("keep"):
            continue
        kept_unpinned += 1
        if kept_unpinned > keep_unpinned:
            shutil.rmtree(p, ignore_errors=True)
            removed.append(row["name"])
    return {"status": "ok", "removed": removed, "keep_unpinned": keep_unpinned}

@app.get("/readiness/aggregate")
async def readiness_aggregate():
    summary = {"status": "ok", "checks": []}
    docker_rows = docker_services()
    service_map = {(row.get("Service") or row.get("Name") or "").strip(): row for row in docker_rows if isinstance(row, dict)}
    for name, meta in readiness_probe_map().items():
        compose_service = meta.get("compose_service", "")
        startup = meta.get("startup", "core")
        external = bool(meta.get("external", False))
        if startup != "core" and not external and not docker_service_running(service_map.get(compose_service)):
            summary["checks"].append({"name": name, "status": "idle", "startup": startup, "service": compose_service})
            continue
        probe = http_probe(
            str(meta.get("url") or ""),
            timeout=3 if startup == "core" else 2,
            healthy_status_codes=[int(code) for code in (meta.get("healthy_status_codes") or []) if str(code).strip()],
        )
        if probe.get("ok"):
            data = probe.get("data") or {}
            code = int(data.get("http_status") or 200)
            summary["checks"].append({"name": name, "status": "ok", "code": code, "startup": startup, "service": compose_service})
        else:
            summary["checks"].append({"name": name, "status": "error", "detail": str(probe.get("detail") or "probe failed"), "startup": startup, "service": compose_service})
            summary["status"] = "degraded"
    summary["maintenance"] = load_maintenance_mode()
    summary["incidents"] = incident_summary(limit=8)
    return summary

@app.get("/autopilot/status")
async def autopilot_status():
    path = Path("/workspace/luminax/config/autopilot-config.json")
    data = {"enabled": False, "allow_service_actions": False, "ui_failure_threshold": 3, "ui_restart_cooldown_sec": 180, "talk_to_user": True}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"status": "ok", "path": str(path), "config": data}

@app.post("/autopilot/config")
async def autopilot_config_set(req: AutopilotConfigRequest):
    validate(req.token)
    path = Path("/workspace/luminax/config/autopilot-config.json")
    current = {"enabled": False, "allow_service_actions": False, "ui_failure_threshold": 3, "ui_restart_cooldown_sec": 180, "talk_to_user": True}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    update = req.dict()
    update.pop("token", None)
    for k, v in update.items():
        if v is not None:
            current[k] = v
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return {"status": "ok", "path": str(path), "config": current}

@app.get("/autopilot/logs")
async def autopilot_logs(lines: int = Query(default=200)):
    log_root = (os.getenv("LOG_ROOT", "/data/logs/new") or "/data/logs/new").rstrip("/")
    candidates = [
        Path(f"{log_root}/guardian_runtime.log"),
        Path("/workspace/luminax/logs/new/guardian_runtime.log"),
        Path("/workspace/luminax/logs/guardian_runtime.log"),
    ]
    path = next((p for p in candidates if p.exists()), candidates[0])
    if not path.exists():
        return {"status": "missing", "path": str(path), "output": ""}
    try:
        all_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        tail = "\n".join(all_lines[-max(10, min(lines, 1000)):])
        return {"status": "ok", "path": str(path), "output": tail}
    except Exception as e:
        return {"status": "error", "path": str(path), "detail": str(e), "output": ""}

@app.get("/self-upgrade/history")
async def self_upgrade_history(limit: int = Query(default=30)):
    return fetch_json(f"{UPGRADE_API_URL}/history?limit={max(1, min(int(limit or 30), 200))}", timeout=20)

@app.get("/self-upgrade/health")
async def self_upgrade_health():
    payload = fetch_json(f"{UPGRADE_API_URL}/health", timeout=20)
    profile = str(load_runtime_policy().get("execution_profile") or "pro_only").strip().lower() or "pro_only"
    if not isinstance(payload, dict):
        payload = {"status": "error", "detail": "Invalid self-upgrade health payload"}
    payload["execution_profile"] = profile
    payload["active_executor"] = "darius" if profile == "pro_only" else "server_codex"
    payload["recommended_executor"] = "darius" if profile == "pro_only" else ("server_codex" if bool(payload.get("codex_exec_ready")) else "darius")
    return payload

@app.get("/self-upgrade/latest-proposal")
async def self_upgrade_latest_proposal():
    return fetch_json(f"{UPGRADE_API_URL}/latest", timeout=20)

@app.get("/self-upgrade/logs")
async def self_upgrade_logs(lines: int = Query(default=200)):
    return fetch_json(f"{UPGRADE_API_URL}/logs?lines={max(10, min(int(lines or 200), 1000))}", timeout=20)

@app.post("/self-upgrade/analyze")
async def self_upgrade_analyze(req: SelfUpgradeAnalyzeRequest):
    return post_json(f"{UPGRADE_API_URL}/analyze", req.model_dump(), timeout=240)

@app.post("/self-upgrade/apply")
async def self_upgrade_apply(req: SelfUpgradeApplyRequest):
    validate(req.token)
    enforce_maintenance_guard("Self-upgrade apply", write_intent=True, self_apply=True, service_action=True)
    profile = str(load_runtime_policy().get("execution_profile") or "pro_only").strip().lower() or "pro_only"
    if profile == "pro_only":
        latest = fetch_json(f"{UPGRADE_API_URL}/latest", timeout=20)
        row = latest.get("row", {}) if isinstance(latest, dict) and isinstance(latest.get("row"), dict) else {}
        title = str(row.get("title") or row.get("summary") or row.get("service") or "self-upgrade proposal").strip()
        goal = (
            f"Review and execute self-upgrade proposal {req.proposal_id}. "
            f"Service: {str(row.get('service') or 'lumi-upgrade-agent').strip() or 'lumi-upgrade-agent'}. "
            f"Summary: {title}"
        )
        assignment = post_json(
            f"{CORE_API_URL}/darius/assign",
            {
                "token": req.token,
                "service_key": "lumi-upgrade-agent",
                "project_key": "mount-olympus-core",
                "goal": goal,
                "scope": "repair",
                "requested_by": web_auth_user_ctx.get() or "operator",
                "source": "self_upgrade_apply",
                "allow_write": True,
                "priority": 90,
                "meta": {
                    "proposal_id": req.proposal_id,
                    "allow_medium_risk": bool(req.allow_medium_risk),
                    "proposal_snapshot": row,
                },
            },
            timeout=30,
        )
        return {
            "status": "advisory",
            "detail": "Execution profile is pro_only, so self-upgrade apply was delegated to Darius instead of running server-side.",
            "execution_profile": profile,
            "active_executor": "darius",
            "recommended_executor": "darius",
            "assignment": assignment.get("assignment") if isinstance(assignment, dict) else None,
            "service_thread": assignment.get("service_thread") if isinstance(assignment, dict) else None,
        }
    return post_json(
        f"{UPGRADE_API_URL}/proposal/apply",
        {"token": req.token, "proposal_id": req.proposal_id, "allow_medium_risk": bool(req.allow_medium_risk)},
        timeout=600,
    )

@app.post("/ui/recover")
async def ui_recover(req: TokenRequest):
    validate(req.token)
    enforce_maintenance_guard("UI recover", service_action=True)
    result = sh("cd /workspace/luminax && (docker compose restart luminax-ui || docker restart luminax-ui || true)")
    return {"status": "ok" if result.get("returncode") == 0 else "failed", **result}

@app.get("/maintenance/mode")
async def maintenance_mode_get():
    return {"status": "ok", "path": str(MAINTENANCE_MODE_PATH), "data": load_maintenance_mode()}

@app.post("/maintenance/mode")
async def maintenance_mode_set(req: MaintenanceModeRequest):
    validate(req.token)
    payload = save_maintenance_mode(
        req.mode,
        reason=req.reason,
        note=req.note,
        random_health_checks=req.random_health_checks,
        set_by=current_web_auth_user() or "operator",
    )
    return {"status": "ok", "path": str(MAINTENANCE_MODE_PATH), "data": payload}

@app.get("/runtime/policy")
async def runtime_policy_get():
    return {"status": "ok", "path": str(RUNTIME_POLICY_PATH), "data": load_runtime_policy()}

@app.post("/runtime/policy")
async def runtime_policy_set(req: RuntimePolicyRequest):
    validate(req.token)
    payload = save_runtime_policy(req.workers_paused, req.worker_pause_reason, req.execution_profile)
    return {"status": "ok", "path": str(RUNTIME_POLICY_PATH), "data": payload}

@app.get("/olympus/identity")
async def olympus_identity_get():
    return {"status": "ok", "path": str(OLYMPUS_IDENTITY_PATH), "data": load_olympus_identity()}

@app.get("/olympus/registry")
async def olympus_registry_get():
    return {
        "status": "ok",
        "profile_root": str(OLYMPUS_PROFILE_ROOT),
        "identity": load_olympus_identity(),
        "services": load_olympus_service_profiles(),
        "agents": load_olympus_agent_profiles(),
        "projects": load_olympus_project_profiles(),
    }

@app.get("/workspace/registry")
async def workspace_registry_get():
    registry = load_workspace_registry()
    return {
        "status": "ok",
        "path": str(WORKSPACE_REGISTRY_PATH),
        "data": registry,
        "project_count": len(registry.get("projects", [])) if isinstance(registry.get("projects"), list) else 0,
    }

@app.get("/workspace/atlas")
async def workspace_atlas_get():
    atlas = load_workspace_atlas()
    connections = load_workspace_connections()
    secret_template = load_private_secret_ref_template()
    return {
        "status": "ok",
        "path": str(WORKSPACE_ATLAS_JSON_PATH),
        "markdown_path": str(WORKSPACE_ATLAS_MD_PATH),
        "data": atlas,
        "connections_path": str(WORKSPACE_CONNECTIONS_PATH),
        "connection_count": len(connections.get("connections", [])) if isinstance(connections.get("connections"), list) else 0,
        "secret_refs": {
            "template_path": str(PRIVATE_SECRET_REFS_TEMPLATE_PATH),
            "runtime_path": str(PRIVATE_SECRET_REFS_PATH),
            "template_ref_count": len(secret_template.get("refs", [])) if isinstance(secret_template.get("refs"), list) else 0,
            "runtime_file_present": PRIVATE_SECRET_REFS_PATH.exists(),
        },
    }

@app.get("/workspace/atlas/project")
async def workspace_atlas_project_get(project_key: str = Query(default="")):
    project = workspace_atlas_project(project_key)
    if not project:
        raise HTTPException(status_code=404, detail="Project atlas entry not found")
    return {"status": "ok", "project": project, "project_key": project_key}

@app.get("/workspace/atlas/audit")
async def workspace_atlas_audit_get():
    atlas = load_workspace_atlas()
    audit = atlas.get("audit") if isinstance(atlas.get("audit"), dict) else {}
    return {
        "status": "ok",
        "path": str(WORKSPACE_ATLAS_JSON_PATH),
        "audit": audit,
        "workspace_registry_path": str(WORKSPACE_REGISTRY_PATH),
        "connections_path": str(WORKSPACE_CONNECTIONS_PATH),
    }

@app.post("/workspace/atlas/rebuild")
async def workspace_atlas_rebuild(req: AtlasRebuildRequest):
    validate(req.token)
    atlas = rebuild_workspace_atlas(req.project_key)
    return {
        "status": "ok",
        "path": str(WORKSPACE_ATLAS_JSON_PATH),
        "markdown_path": str(WORKSPACE_ATLAS_MD_PATH),
        "data": atlas,
    }

@app.get("/workspace/deploy/verification")
async def workspace_deploy_verification_get():
    rows = workspace_deploy_verification_rows()
    warn_count = sum(1 for row in rows if str(row.get("status") or "").lower() != "ready")
    return {
        "status": "ok",
        "path": str(WORKSPACE_ATLAS_JSON_PATH),
        "rows": rows,
        "summary": {
            "total": len(rows),
            "ready_count": len(rows) - warn_count,
            "warn_count": warn_count,
        },
    }

@app.get("/review/cycle/policy")
async def review_cycle_policy_get():
    policy = load_review_cycle_policy()
    return {"status": "ok", "path": str(REVIEW_CYCLE_POLICY_PATH), "data": policy}

@app.get("/review/cycle/status")
async def review_cycle_status_get():
    policy = load_review_cycle_policy()
    state = load_review_cycle_state()
    candidates = review_cycle_candidates()
    audit_rows = naming_audit_rows()
    runtime_policy = load_runtime_policy()
    current_wave = {}
    for row in state.get("waves", []):
        if not isinstance(row, dict):
            continue
        if int(row.get("index") or 0) == int(state.get("current_wave") or 0):
            current_wave = row
            break
    return {
        "status": "ok",
        "path": str(REVIEW_CYCLE_STATE_PATH),
        "state": state,
        "policy": policy,
        "current_wave": current_wave,
        "candidate_count": len(candidates),
        "candidates": candidates[:24],
        "naming_audit": {
            "rows": audit_rows,
            "summary": review_cycle_summary(audit_rows),
        },
        "runtime_policy": runtime_policy,
        "worker_pause": {
            "paused": bool(runtime_policy.get("workers_paused", False)),
            "reason": str(runtime_policy.get("worker_pause_reason", "")).strip(),
        },
    }

@app.post("/review/cycle/start")
async def review_cycle_start(req: ReviewCycleActionRequest):
    validate(req.token)
    policy = load_review_cycle_policy()
    current = load_review_cycle_state()
    now_ts = int(time.time())
    runtime_policy = load_runtime_policy()
    if bool(runtime_policy.get("workers_paused", False)):
        pause_reason = str(runtime_policy.get("worker_pause_reason") or "").strip()
        if not _runtime_pause_owned_by_review_cycle(pause_reason):
            raise HTTPException(
                status_code=409,
                detail=f"Workers are paused for another reason. Clear that pause before starting the Olympus review cycle. Current reason: {pause_reason or 'unspecified'}",
            )
    current_wave_index = _review_cycle_start_index(policy, current)
    waves = []
    selected_wave = {}
    for row in policy.get("waves", []):
        if not isinstance(row, dict):
            continue
        idx = int(row.get("index") or len(waves) + 1)
        previous = {}
        for existing in current.get("waves", []):
            if isinstance(existing, dict) and int(existing.get("index") or 0) == idx:
                previous = existing
                break
        status = str(previous.get("status") or "pending").strip() or "pending"
        if idx == current_wave_index:
            status = "running"
        elif idx > current_wave_index and status in {"running", "paused", "draining"}:
            status = "pending"
        wave_row = {
            "index": idx,
            "title": str(row.get("title") or previous.get("title") or f"Wave {idx}").strip(),
            "owner": str(row.get("owner") or previous.get("owner") or "zeus").strip(),
            "target": str(row.get("target") or previous.get("target") or "luminax").strip(),
            "goal": str(row.get("goal") or previous.get("goal") or "").strip(),
            "status": status,
        }
        if idx == current_wave_index:
            selected_wave = wave_row
        waves.append(wave_row)
    note = str(req.note or "").strip()
    if bool(runtime_policy.get("workers_paused", False)) and _runtime_pause_owned_by_review_cycle(str(runtime_policy.get("worker_pause_reason") or "")):
        runtime_policy = save_runtime_policy(False, "")
    state = save_review_cycle_state(
        {
            "cycle_id": str(current.get("cycle_id") or f"review_cycle_{now_ts}").strip(),
            "status": "running",
            "started_at": int(current.get("started_at") or now_ts),
            "current_wave": current_wave_index,
            "current_target": str(selected_wave.get("target") or "").strip(),
            "current_title": str(selected_wave.get("title") or "").strip(),
            "stop_requested": False,
            "pause_requested": False,
            "waves": waves,
            "notes": (current.get("notes") or []) + ([{"created_at": now_ts, "note": note, "kind": "start"}] if note else []),
        }
    )
    entry = append_review_cycle_ledger(
        "cycle_started",
        "Olympus review cycle running",
        note or "Review cycle started or resumed by operator.",
        {
            "cycle_id": state.get("cycle_id"),
            "wave_index": state.get("current_wave"),
            "wave_title": state.get("current_title"),
            "target": state.get("current_target"),
        },
    )
    return {"status": "ok", "state": state, "runtime_policy": runtime_policy, "ledger_entry": entry}

@app.post("/review/cycle/pause")
async def review_cycle_pause(req: ReviewCycleActionRequest):
    validate(req.token)
    current = load_review_cycle_state()
    note = str(req.note or "").strip()
    waves = []
    for row in current.get("waves", []):
        if not isinstance(row, dict):
            continue
        next_row = dict(row)
        if int(next_row.get("index") or 0) == int(current.get("current_wave") or 0) and str(next_row.get("status") or "").strip() == "running":
            next_row["status"] = "paused"
        waves.append(next_row)
    reason = note or "Olympus review cycle paused by operator."
    runtime_policy = save_runtime_policy(True, reason)
    state = save_review_cycle_state(
        {
            "status": "paused",
            "pause_requested": True,
            "stop_requested": False,
            "waves": waves,
            "notes": (current.get("notes") or []) + [{"created_at": int(time.time()), "note": reason, "kind": "pause"}],
        }
    )
    entry = append_review_cycle_ledger("cycle_paused", "Olympus review cycle paused", reason, {"cycle_id": state.get("cycle_id"), "wave_index": state.get("current_wave")})
    return {"status": "ok", "state": state, "runtime_policy": runtime_policy, "ledger_entry": entry}

@app.post("/review/cycle/stop")
async def review_cycle_stop(req: ReviewCycleActionRequest):
    validate(req.token)
    current = load_review_cycle_state()
    note = str(req.note or "").strip()
    reason = note or "Operator sent stop signal. Drain the current atomic block and block new ballots."
    waves = []
    for row in current.get("waves", []):
        if not isinstance(row, dict):
            continue
        next_row = dict(row)
        if int(next_row.get("index") or 0) == int(current.get("current_wave") or 0) and str(next_row.get("status") or "").strip() in {"running", "paused"}:
            next_row["status"] = "draining"
        waves.append(next_row)
    runtime_policy = save_runtime_policy(True, reason)
    state = save_review_cycle_state(
        {
            "status": "draining",
            "stop_requested": True,
            "pause_requested": False,
            "waves": waves,
            "notes": (current.get("notes") or []) + [{"created_at": int(time.time()), "note": reason, "kind": "stop"}],
        }
    )
    entry = append_review_cycle_ledger("cycle_stop_requested", "Olympus review cycle draining", reason, {"cycle_id": state.get("cycle_id"), "wave_index": state.get("current_wave")})
    return {"status": "ok", "state": state, "runtime_policy": runtime_policy, "ledger_entry": entry}

@app.get("/review/cycle/candidates")
async def review_cycle_candidates_get():
    rows = review_cycle_candidates()
    return {"status": "ok", "rows": rows, "count": len(rows)}

@app.get("/review/cycle/ledger")
async def review_cycle_ledger_get(limit: int = Query(default=80, ge=1, le=400)):
    state = load_review_cycle_state()
    rows = state.get("ledger", []) if isinstance(state.get("ledger"), list) else []
    return {"status": "ok", "path": str(REVIEW_CYCLE_STATE_PATH), "rows": rows[-limit:]}

@app.get("/review/cycle/naming_audit")
async def review_cycle_naming_audit_get():
    rows = naming_audit_rows()
    return {
        "status": "ok",
        "rows": rows,
        "summary": review_cycle_summary(rows),
        "identity_path": str(OLYMPUS_IDENTITY_PATH),
    }

@app.post("/review/ballots")
async def review_ballots_post(req: ReviewBallotRequest):
    validate(req.token)
    policy = load_review_cycle_policy()
    current = load_review_cycle_state()
    _review_cycle_guard_ballot_writes(current)
    ballot = {
        "project_key": str(req.project_key or "").strip(),
        "candidate_id": str(req.candidate_id or "").strip(),
        "title": str(req.title or "").strip(),
        "vote_signature": str(req.vote_signature or "").strip(),
        "critic_vote": str(req.critic_vote or "").strip(),
        "status": str(req.status or "approved").strip() or "approved",
        "notes": str(req.notes or "").strip(),
        "created_at": int(time.time()),
    }
    ballot = _review_cycle_validate_ballot(ballot, policy)
    ballots = [row for row in current.get("ballots", []) if isinstance(row, dict)]
    ballots.append(ballot)
    state = save_review_cycle_state({"ballots": ballots[-240:]})
    entry = append_review_cycle_ledger(
        "ballot",
        ballot["title"] or f"Ballot for {ballot['candidate_id']}",
        ballot["notes"],
        {
            "project_key": ballot["project_key"],
            "candidate_id": ballot["candidate_id"],
            "vote_signature": ballot["vote_signature"],
            "critic_vote": ballot["critic_vote"],
            "status": ballot["status"],
        },
    )
    return {"status": "ok", "ballot": ballot, "state": state, "ledger_entry": entry}

@app.get("/routes/catalog")
async def routes_catalog():
    return {"status": "ok", "routes": sorted({str(getattr(route, 'path', '') or '').strip() for route in app.routes if str(getattr(route, 'path', '') or '').strip()})}

@app.get("/project-light/personality")
async def project_light_personality_get():
    path = Path("/workspace/luminax/config/project-light-personality.json")
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    try:
        return {"status": "ok", "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as e:
        return {"status": "error", "path": str(path), "detail": str(e)}

@app.post("/project-light/personality")
async def project_light_personality_set(payload: dict):
    path = Path("/workspace/luminax/config/project-light-personality.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"status": "ok", "path": str(path)}

@app.get("/project-light/employees/profiles")
async def project_light_employees_profiles_get():
    path = Path("/workspace/luminax/config/project-light-employees.json")
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    try:
        return {"status": "ok", "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as e:
        return {"status": "error", "path": str(path), "detail": str(e)}

@app.post("/project-light/employees/profiles")
async def project_light_employees_profiles_set(payload: dict):
    path = Path("/workspace/luminax/config/project-light-employees.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"status": "ok", "path": str(path)}

@app.get("/project-light/employees/state")
async def project_light_employees_state_get():
    path = Path("/workspace/luminax/memory/employees/employee-state.json")
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    try:
        return {"status": "ok", "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as e:
        return {"status": "error", "path": str(path), "detail": str(e)}

@app.post("/project-light/employees/state")
async def project_light_employees_state_set(payload: dict):
    path = Path("/workspace/luminax/memory/employees/employee-state.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"status": "ok", "path": str(path)}

@app.post("/project-light/employees/assign")
async def project_light_employees_assign(payload: dict):
    path = Path("/workspace/luminax/memory/employees/employee-state.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {}
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    state.setdefault("queues", {})
    state["queues"].setdefault("pending_assignments", [])
    state["queues"].setdefault("in_progress", [])
    state["queues"].setdefault("completed", [])
    state.setdefault("agents", [])
    assignment = {
        "agent_key": payload.get("agent_key"),
        "task": payload.get("task"),
        "project": payload.get("project", "default"),
        "priority": payload.get("priority", "normal"),
        "assigned_at": payload.get("assigned_at"),
    }
    state["queues"]["pending_assignments"].append(assignment)
    for agent in state["agents"]:
        if agent.get("agent_key") == payload.get("agent_key"):
            agent["status"] = "assigned"
            agent["active_project"] = payload.get("project", "default")
            break
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return {"status": "ok", "assignment": assignment}

@app.post("/project-light/employees/idle/run")
async def project_light_employees_idle_run(payload: dict = {}):
    state_path = Path("/workspace/luminax/memory/employees/employee-state.json")
    profile_path = Path("/workspace/luminax/config/project-light-employees.json")
    state = {"agents": [], "queues": {"pending_assignments": [], "in_progress": [], "completed": []}}
    profiles = {"employees": []}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if profile_path.exists():
        try:
            profiles = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    profile_map = {e.get("agent_key"): e for e in (profiles.get("employees") or [])}
    acted = []
    for agent in state.get("agents", []):
        if agent.get("status") not in {"idle", "registered", None}:
            continue
        p = profile_map.get(agent.get("agent_key"), {})
        if not bool(p.get("allow_idle_autonomy", False)):
            continue
        auto_task = {
            "agent_key": agent.get("agent_key"),
            "task": payload.get("task_template", "Run autonomous maintenance/check cycle."),
            "project": payload.get("project", "autonomous"),
            "priority": "background",
        }
        state.setdefault("queues", {}).setdefault("pending_assignments", []).append(auto_task)
        agent["status"] = "assigned"
        agent["active_project"] = auto_task["project"]
        acted.append(auto_task)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return {"status": "ok", "assigned": len(acted), "tasks": acted}





install_runtime_guard(app, 'admin-api', require_auth=False)
