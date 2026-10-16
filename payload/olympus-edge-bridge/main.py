import asyncio
import json
import os
import platform
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlsplit, urlunsplit
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response


app = FastAPI(title="Olympus Edge Bridge")


def _env_url(name: str, default: str = "", *, allow_query: bool = False) -> str:
    value = ((os.getenv(name) or "").strip() or default).rstrip("/")
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        port = parts.port
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or (parts.query and not allow_query)
            or (parts.query and any(key not in {"skip_edge_bridge", "edge_safe"} for key, _ in parse_qsl(parts.query, keep_blank_values=True)))
            or any(character.isspace() for character in value)
            or "\\" in value
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError
    except ValueError:
        raise ValueError(f"Invalid URL configuration: {name}") from None
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"Invalid boolean configuration: {name}")
    return value == "true"


def _heartbeat_interval() -> int:
    try:
        value = int(os.getenv("EDGE_HEARTBEAT_INTERVAL_SEC", "60"))
        if not 20 <= value <= 86400:
            raise ValueError
        return value
    except ValueError:
        raise ValueError("Invalid interval configuration: EDGE_HEARTBEAT_INTERVAL_SEC") from None


NODE_KEY = (os.getenv("EDGE_NODE_KEY", "olympus-edge-bridge") or "olympus-edge-bridge").strip()
AUTHORITY_NODE_KEY = (os.getenv("EDGE_AUTHORITY_NODE_KEY", "olympus-edge-gpu") or "olympus-edge-gpu").strip()
NODE_HOSTNAME = (
    os.getenv("EDGE_NODE_HOSTNAME")
    or os.getenv("COMPUTERNAME")
    or socket.gethostname()
    or "edge-node"
).strip()
CONTROL_TOKEN = (os.getenv("CONTROL_TOKEN", "") or "").strip()
ADMIN_API_URL = _env_url("ADMIN_API_URL")
PUBLIC_APP_URL = _env_url("PUBLIC_APP_URL")
EDGE_CONTROL_ROUTE_MODE = (os.getenv("EDGE_CONTROL_ROUTE_MODE", "configured") or "configured").strip()
OLLAMA_HOST = _env_url("OLLAMA_HOST")
EDGE_VOICE_URL = _env_url("EDGE_VOICE_URL")
VOICE_HEALTH_URL = _env_url("VOICE_HEALTH_URL", f"{ADMIN_API_URL}/voice/health?skip_edge_bridge=1&edge_safe=1" if ADMIN_API_URL else "", allow_query=True)
ADMIN_HEALTH_URL = _env_url("EDGE_OLYMPUS_ADMIN_HEALTH_URL", f"{ADMIN_API_URL}/olympus/admin/health?skip_edge_bridge=1&edge_safe=1" if ADMIN_API_URL else "", allow_query=True)
EDGE_VISION_URL = _env_url("EDGE_VISION_URL")
EDGE_IMAGE_URL = _env_url("EDGE_IMAGE_URL")
LUMINAX_BRAIN_LOCAL_URL = _env_url("EDGE_LUMINAX_BRAIN_LOCAL_URL")
AFFECT_CONTROLLER_LOCAL_URL = _env_url("EDGE_AFFECT_CONTROLLER_LOCAL_URL")
EDGE_HEARTBEAT_INTERVAL_SEC = _heartbeat_interval()
EDGE_BRIDGE_HEARTBEAT_ENABLED = _env_bool("EDGE_BRIDGE_HEARTBEAT_ENABLED")
EDGE_GPU_PROBE_ENABLED = _env_bool("EDGE_GPU_PROBE_ENABLED")
EDGE_BRIDGE_VERSION = "2026-03-13"
HEALTH_CACHE: dict[str, Any] = {"ts": 0, "payload": {}}
_diagnostics_path = os.getenv("EDGE_DIAGNOSTICS_PATH", "").strip()
EDGE_DIAGNOSTICS_PATH = Path(_diagnostics_path) if _diagnostics_path else None
TOKEN_SOURCE = (os.getenv("EDGE_CONTROL_TOKEN_SOURCE", "unknown") or "unknown").strip()
LAST_HEARTBEAT: dict[str, Any] = {"status": "pending", "detail": "Heartbeat has not run yet.", "ts": 0}
HEARTBEAT_TASK: asyncio.Task | None = None


def _safe_url(url: str) -> str:
    """Diagnostic URLs omit userinfo, queries, and fragments entirely."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parts.port is not None:
            host += f":{parts.port}"
        result = urlunsplit((parts.scheme, host, parts.path, "", ""))
        if CONTROL_TOKEN:
            result = result.replace(CONTROL_TOKEN, "[redacted]").replace(quote_plus(CONTROL_TOKEN), "[redacted]")
        return result
    except ValueError:
        return "[invalid-url]"


def _safe_data(value: Any) -> Any:
    sensitive_keys = {"token", "control_token", "access_token", "refresh_token", "password", "authorization", "api_key", "client_secret"}
    if isinstance(value, dict):
        return {
            key: "[redacted]" if str(key).lower().replace("-", "_") in sensitive_keys else _safe_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_data(item) for item in value]
    if isinstance(value, str) and CONTROL_TOKEN:
        return value.replace(CONTROL_TOKEN, "[redacted]").replace(quote_plus(CONTROL_TOKEN), "[redacted]")
    return value


def _write_diag(payload: dict[str, Any]) -> None:
    if EDGE_DIAGNOSTICS_PATH is None:
        return
    try:
        EDGE_DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with EDGE_DIAGNOSTICS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_safe_data(payload), ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[edge-bridge] diagnostics write failed: {type(exc).__name__}")


def _exc_detail(exc: Exception) -> str:
    # HTTP exception text can contain the full credential-bearing request URL.
    return type(exc).__name__


async def _probe_json(url: str, timeout: float = 8.0) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=3.0), trust_env=False, follow_redirects=False) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"status": "ok", "data": payload}


async def _probe_status(url: str, timeout: float = 8.0) -> dict[str, Any]:
    try:
        payload = await _probe_json(url, timeout=timeout)
        return {"status": "ok", "url": _safe_url(url), "payload": _safe_data(payload)}
    except Exception as exc:
        return {"status": "error", "url": _safe_url(url), "detail": _exc_detail(exc)}


async def _probe_optional_status(url: str, timeout: float = 8.0, disabled_detail: str = "not configured") -> dict[str, Any]:
    if not str(url or "").strip():
        return {"status": "disabled", "url": "", "detail": disabled_detail}
    return await _probe_status(url, timeout=timeout)


def _gpu_summary() -> list[dict[str, Any]]:
    if not EDGE_GPU_PROBE_ENABLED:
        return [{"status": "disabled", "detail": "GPU inventory probe is disabled."}]
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception as exc:
        return [{"status": "unavailable", "detail": _exc_detail(exc)}]
    if result.returncode != 0:
        return [{"status": "unavailable", "detail": "GPU inventory probe failed."}]
    rows: list[dict[str, Any]] = []
    for raw in (result.stdout or "").splitlines():
        parts = [part.strip() for part in raw.split(",")]
        if not parts or not parts[0]:
            continue
        rows.append(
            {
                "name": parts[0],
                "memory_total_mb": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else parts[1] if len(parts) > 1 else "",
                "driver_version": parts[2] if len(parts) > 2 else "",
            }
        )
    return rows or [{"status": "unavailable", "detail": "No GPU rows returned."}]


def _edge_breadcrumb(stage: str, severity: str, route: str, summary: str, connection: str = "") -> dict[str, str]:
    return {
        "stage": stage,
        "severity": severity,
        "scene": "olympus-edge-bridge",
        "route": route,
        "summary": str(summary or "")[:240],
        "connection": str(connection or "")[:240],
    }


async def _probe_windows_node_authority() -> dict[str, Any]:
    if not CONTROL_TOKEN or not ADMIN_API_URL:
        return {
            "status": "skipped",
            "url": f"{ADMIN_API_URL}/windows-node/latest" if ADMIN_API_URL else "",
            "detail": "ADMIN_API_URL and CONTROL_TOKEN are required to verify node authority.",
        }
    url = f"{ADMIN_API_URL}/windows-node/latest?summary=1&node_key={quote_plus(AUTHORITY_NODE_KEY)}&token={quote_plus(CONTROL_TOKEN)}"
    return await _probe_status(url, timeout=12.0)


def _windows_gpu_rows(windows_probe: dict[str, Any]) -> list[dict[str, Any]]:
    payload = windows_probe.get("payload") if isinstance(windows_probe, dict) else {}
    row = payload.get("row") if isinstance(payload, dict) else {}
    meta = row.get("meta_json") if isinstance(row, dict) and isinstance(row.get("meta_json"), dict) else {}
    inventory = meta.get("inventory") if isinstance(meta, dict) and isinstance(meta.get("inventory"), dict) else {}
    gpus = inventory.get("gpus") if isinstance(inventory, dict) and isinstance(inventory.get("gpus"), list) else []
    return [gpu for gpu in gpus if isinstance(gpu, dict)]


async def collect_edge_status() -> dict[str, Any]:
    (
        ollama,
        admin,
        voice_lane,
        voice,
        vision,
        image,
        luminax_brain,
        affect_controller,
        windows_node,
    ) = await asyncio.gather(
        _probe_optional_status(f"{OLLAMA_HOST}/api/tags" if OLLAMA_HOST else "", timeout=4.0),
        _probe_optional_status(ADMIN_HEALTH_URL, timeout=12.0),
        _probe_optional_status(VOICE_HEALTH_URL, timeout=12.0),
        _probe_optional_status(f"{EDGE_VOICE_URL}/health" if EDGE_VOICE_URL else "", timeout=4.0),
        _probe_optional_status(
            f"{EDGE_VISION_URL}/health" if EDGE_VISION_URL else "",
            timeout=4.0,
            disabled_detail="vision lane not configured on this edge node.",
        ),
        _probe_optional_status(
            f"{EDGE_IMAGE_URL}/health" if EDGE_IMAGE_URL else "",
            timeout=4.0,
            disabled_detail="image lane not configured on this edge node.",
        ),
        _probe_optional_status(f"{LUMINAX_BRAIN_LOCAL_URL}/health" if LUMINAX_BRAIN_LOCAL_URL else "", timeout=4.0),
        _probe_optional_status(f"{AFFECT_CONTROLLER_LOCAL_URL}/health" if AFFECT_CONTROLLER_LOCAL_URL else "", timeout=4.0),
        _probe_windows_node_authority(),
    )
    vision_lane_configured = bool(EDGE_VISION_URL)
    image_lane_configured = bool(EDGE_IMAGE_URL)
    image_payload = image.get("payload") if isinstance(image.get("payload"), dict) else {}
    windows_gpu_rows = _windows_gpu_rows(windows_node)
    image_cuda_ready = bool(
        image_lane_configured
        and image.get("status") == "ok"
        and image_payload.get("cuda_available")
        and image_payload.get("ready")
        and str(image_payload.get("runtime_device") or "").lower() == "cuda"
    )
    gpu_edge_ready = bool(windows_gpu_rows) and (image_cuda_ready if image_lane_configured else True)
    heartbeat = dict(LAST_HEARTBEAT)
    breadcrumbs = [
        _edge_breadcrumb(
            "edge-bootstrap-start",
            "info" if EDGE_BRIDGE_HEARTBEAT_ENABLED else "warn",
            ADMIN_API_URL,
            "Olympus edge bridge bootstrap has started.",
            f"heartbeat_enabled={EDGE_BRIDGE_HEARTBEAT_ENABLED} | token_source={TOKEN_SOURCE}",
        ),
        {
            **_edge_breadcrumb(
                "admin-api-resolution",
                "info" if admin.get("status") == "ok" else "warn",
                ADMIN_HEALTH_URL,
                "Resolved the Ubuntu admin authority route for Olympus edge supervision.",
                str(admin.get("detail") or admin.get("url") or "")[:240],
            ),
        },
        _edge_breadcrumb(
            "voice-health-probe",
            "info" if voice_lane.get("status") == "ok" and voice.get("status") == "ok" else "warn",
            VOICE_HEALTH_URL,
            "Voice lane and voice-agent probes completed on the edge bridge.",
            f"voice_lane={voice_lane.get('status')} | voice_agent={voice.get('status')}",
        ),
        _edge_breadcrumb(
            "windows-monitor-presence",
            "info" if windows_node.get("status") == "ok" else "warn",
            f"{ADMIN_API_URL}/windows-node/latest",
            "Queried the server-authoritative Windows node heartbeat for the GPU edge worker.",
            str(windows_node.get("detail") or windows_node.get("url") or "")[:240],
        ),
        _edge_breadcrumb(
            "image-cuda-probe",
            "info" if (not image_lane_configured or image_cuda_ready) else "warn",
            f"{EDGE_IMAGE_URL}/health" if EDGE_IMAGE_URL else "unconfigured://image-agent",
            "Image CUDA readiness was checked against the image-agent health payload." if image_lane_configured else "Image lane is not configured on this edge node.",
            f"image_status={image.get('status')} | runtime_device={image_payload.get('runtime_device') or 'unknown'} | cuda_available={image_payload.get('cuda_available')}" if image_lane_configured else str(image.get("detail") or "not configured"),
        ),
        _edge_breadcrumb(
            "heartbeat-post-result",
            "info" if heartbeat.get("status") == "ok" else "warn",
            f"{ADMIN_API_URL}/windows-node/heartbeat",
            "Recorded the latest edge heartbeat result.",
            str(heartbeat.get("detail") or heartbeat.get("status") or "")[:240],
        ),
        _edge_breadcrumb(
            "local-ollama-reachability",
            "info" if ollama.get("status") == "ok" else "warn",
            f"{OLLAMA_HOST}/api/tags",
            "Checked configured Ollama reachability on the edge node.",
            str(ollama.get("detail") or ollama.get("url") or "")[:240],
        ),
    ]
    if (
        admin.get("status") != "ok"
        or voice_lane.get("status") != "ok"
        or voice.get("status") != "ok"
        or windows_node.get("status") not in {"ok", "skipped"}
        or heartbeat.get("status") != "ok"
        or (image_lane_configured and not gpu_edge_ready)
    ):
        _write_diag(
            {
                "receipt_id": f"edge_bridge_{int(time.time())}_{uuid.uuid4().hex[:8]}",
                "status": "failed" if heartbeat.get("status") == "failed" or admin.get("status") != "ok" else "degraded",
                "scene": "olympus-edge-bridge",
                "generated_at": int(time.time()),
                "breadcrumbs": breadcrumbs,
                "admin": admin,
                "voice_lane": voice_lane,
                "voice_agent": voice,
                "windows_node": windows_node,
                "image": image,
                "heartbeat": heartbeat,
            }
        )
    local_core_rows = [ollama, voice, luminax_brain, affect_controller]
    local_core_ready = all(row.get("status") == "ok" for row in local_core_rows)
    control_plane_ready = (
        admin.get("status") == "ok"
        and voice_lane.get("status") == "ok"
        and windows_node.get("status") in {"ok", "skipped"}
        and heartbeat.get("status") in {"ok", "skipped", "pending"}
    )
    payload_status = "ok" if local_core_ready and control_plane_ready else ("degraded" if local_core_ready else "failed")
    payload = {
        "status": payload_status,
        "service": "olympus-edge-bridge",
        "node_key": NODE_KEY,
        "hostname": NODE_HOSTNAME,
        "role": "edge_bridge_worker",
        "ts": int(time.time()),
        "version": EDGE_BRIDGE_VERSION,
        "workers": {
            "ollama": ollama,
            "admin": admin,
            "voice_lane": voice_lane,
            "voice": voice,
            "vision": vision,
            "image": image,
            "luminax_brain": luminax_brain,
            "affect_controller": affect_controller,
            "windows_node": windows_node,
            "heartbeat": heartbeat,
        },
        "capabilities": {
            "gpu_edge": gpu_edge_ready,
            "gpu_inventory_reported": bool(windows_gpu_rows),
            "image_cuda_ready": image_cuda_ready,
            "vision_lane_configured": vision_lane_configured,
            "image_lane_configured": image_lane_configured,
            "control_token_configured": bool(CONTROL_TOKEN),
            "control_route_mode": EDGE_CONTROL_ROUTE_MODE,
            "discord_production": False,
            "public_web_host": False,
            "assistant_core_local": False,
            "mysql_local": False,
            "redis_local": False,
            "qdrant_local": False,
            "ollama_remote": OLLAMA_HOST,
            "public_app_url": PUBLIC_APP_URL,
            "admin_health": ADMIN_HEALTH_URL,
            "voice_lane_health": VOICE_HEALTH_URL,
            "voice_remote": EDGE_VOICE_URL,
            "vision_remote": EDGE_VISION_URL,
            "image_remote": EDGE_IMAGE_URL,
        },
        "metrics": {
            "gpus": windows_gpu_rows or _gpu_summary(),
        },
        "meta": {
            "os": sys.platform,
            "python": platform.python_version(),
            "heartbeat_enabled": EDGE_BRIDGE_HEARTBEAT_ENABLED,
            "token_source": TOKEN_SOURCE,
            "control_route_mode": EDGE_CONTROL_ROUTE_MODE,
            "public_app_url": PUBLIC_APP_URL,
            "diagnostic_path": str(EDGE_DIAGNOSTICS_PATH) if EDGE_DIAGNOSTICS_PATH else "",
            "voice_health_route": VOICE_HEALTH_URL,
            "admin_health_route": ADMIN_HEALTH_URL,
        },
        "breadcrumbs": breadcrumbs,
    }
    payload = _safe_data(payload)
    HEALTH_CACHE["ts"] = payload["ts"]
    HEALTH_CACHE["payload"] = payload
    return payload


async def collect_local_runtime_status() -> dict[str, Any]:
    ollama, voice, luminax_brain, affect_controller = await asyncio.gather(
        _probe_optional_status(f"{OLLAMA_HOST}/api/tags" if OLLAMA_HOST else "", timeout=4.0),
        _probe_optional_status(f"{EDGE_VOICE_URL}/health" if EDGE_VOICE_URL else "", timeout=4.0),
        _probe_optional_status(f"{LUMINAX_BRAIN_LOCAL_URL}/health" if LUMINAX_BRAIN_LOCAL_URL else "", timeout=4.0),
        _probe_optional_status(f"{AFFECT_CONTROLLER_LOCAL_URL}/health" if AFFECT_CONTROLLER_LOCAL_URL else "", timeout=4.0),
    )
    services = {
        "ollama": ollama,
        "voice": voice,
        "luminax_brain": luminax_brain,
        "affect_controller": affect_controller,
    }
    local_core_ready = all(str(item.get("status") or "").lower() == "ok" for item in services.values())
    return {
        "status": "ok" if local_core_ready else "degraded",
        "node_key": NODE_KEY,
        "hostname": NODE_HOSTNAME,
        "generated_at": int(time.time()),
        "services": services,
    }


def _worker_probe_summary(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {"status": "unknown", "detail": "probe row missing"}
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    summary: dict[str, Any] = {
        "status": str(row.get("status") or "unknown"),
    }
    if row.get("url"):
        summary["url"] = row.get("url")
    if row.get("detail"):
        summary["detail"] = str(row.get("detail") or "")[:320]
    if isinstance(payload, dict):
        payload_status = str(payload.get("status") or "").strip()
        if payload_status:
            summary["payload_status"] = payload_status
        payload_detail = str(payload.get("detail") or payload.get("health_reason") or "").strip()
        if payload_detail:
            summary["payload_detail"] = payload_detail[:320]
        if "ready" in payload:
            summary["ready"] = bool(payload.get("ready"))
        if "runtime_device" in payload:
            summary["runtime_device"] = payload.get("runtime_device")
        if "cuda_available" in payload:
            summary["cuda_available"] = bool(payload.get("cuda_available"))
        if "stale" in payload:
            summary["stale"] = bool(payload.get("stale"))
    return summary


async def send_heartbeat() -> dict[str, Any]:
    global LAST_HEARTBEAT
    if not CONTROL_TOKEN or not ADMIN_API_URL:
        LAST_HEARTBEAT = {
            "status": "skipped",
            "detail": "ADMIN_API_URL and CONTROL_TOKEN are required for heartbeat delivery.",
            "ts": int(time.time()),
        }
        return dict(LAST_HEARTBEAT)
    payload = await collect_edge_status()
    body = {
        "token": CONTROL_TOKEN,
        "node_key": NODE_KEY,
        "hostname": NODE_HOSTNAME,
        "os_version": payload.get("meta", {}).get("os"),
        "capabilities": payload.get("capabilities", {}),
        "services": {
            name: _worker_probe_summary(row)
            for name, row in (payload.get("workers", {}) or {}).items()
            if isinstance(name, str)
        },
        "metrics": payload.get("metrics", {}),
        "meta": {
            "role": "edge_bridge_worker",
            "bridge_url": os.getenv("EDGE_BRIDGE_PUBLIC_URL", ""),
            "version": EDGE_BRIDGE_VERSION,
            "control_token_source": TOKEN_SOURCE,
            "authority_node_key": AUTHORITY_NODE_KEY,
        },
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=3.0), trust_env=False, follow_redirects=False) as client:
        response = await client.post(f"{ADMIN_API_URL}/windows-node/heartbeat", json=body)
        response.raise_for_status()
        result = _safe_data(response.json())
    LAST_HEARTBEAT = {
        "status": str(result.get("status") or "ok") if isinstance(result, dict) else "ok",
        "detail": str(result.get("hostname") or result.get("node_key") or "heartbeat accepted") if isinstance(result, dict) else "heartbeat accepted",
        "ts": int(time.time()),
    }
    return result if isinstance(result, dict) else {"status": "ok", "data": result}


async def _heartbeat_loop() -> None:
    global LAST_HEARTBEAT
    while True:
        try:
            result = await send_heartbeat()
            print(f"[edge-bridge] heartbeat={result.get('status', 'ok')}")
        except Exception as exc:
            LAST_HEARTBEAT = {"status": "failed", "detail": _exc_detail(exc), "ts": int(time.time())}
            _write_diag(
                {
                    "receipt_id": f"edge_bridge_hb_{int(time.time())}_{uuid.uuid4().hex[:8]}",
                    "status": "failed",
                    "scene": "olympus-edge-bridge",
                    "generated_at": int(time.time()),
                    "summary": "Edge heartbeat post failed.",
                    "detail": _exc_detail(exc),
                }
            )
            print(f"[edge-bridge] heartbeat failed: {_exc_detail(exc)}")
        await asyncio.sleep(float(EDGE_HEARTBEAT_INTERVAL_SEC))


@app.on_event("startup")
async def startup_event() -> None:
    global HEARTBEAT_TASK
    if EDGE_BRIDGE_HEARTBEAT_ENABLED:
        if not ADMIN_API_URL or not CONTROL_TOKEN:
            raise RuntimeError("Heartbeat requires ADMIN_API_URL and CONTROL_TOKEN.")
        if HEARTBEAT_TASK is None or HEARTBEAT_TASK.done():
            HEARTBEAT_TASK = asyncio.create_task(_heartbeat_loop())
    else:
        print("[edge-bridge] heartbeat loop disabled by EDGE_BRIDGE_HEARTBEAT_ENABLED=false")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global HEARTBEAT_TASK
    if HEARTBEAT_TASK is not None:
        HEARTBEAT_TASK.cancel()
        try:
            await HEARTBEAT_TASK
        except asyncio.CancelledError:
            pass
        HEARTBEAT_TASK = None


@app.get("/health")
async def health() -> dict[str, Any]:
    if not HEALTH_CACHE.get("payload") or int(time.time()) - int(HEALTH_CACHE.get("ts") or 0) > 20:
        return await collect_edge_status()
    return HEALTH_CACHE["payload"]


@app.get("/workers")
async def workers() -> dict[str, Any]:
    payload = await collect_edge_status()
    return {
        "status": payload.get("status"),
        "node_key": payload.get("node_key"),
        "workers": payload.get("workers", {}),
        "metrics": payload.get("metrics", {}),
    }


@app.get("/local-runtime")
async def local_runtime() -> dict[str, Any]:
    return await collect_local_runtime_status()


@app.post("/heartbeat/now")
async def heartbeat_now() -> dict[str, Any]:
    return await send_heartbeat()


async def _proxy_local_service(base_url: str, path: str, request: Request) -> Response:
    if not base_url:
        return Response(content='{"detail":"Upstream service is not configured."}', status_code=503, media_type="application/json")
    clean_path = str(path or "").lstrip("/")
    target = f"{base_url}/{clean_path}" if clean_path else base_url
    body = await request.body()
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length"}
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=3.0), trust_env=False, follow_redirects=False) as client:
        upstream = await client.request(
            request.method,
            target,
            params=dict(request.query_params),
            content=body if body else None,
            headers=headers,
        )
    content_type = upstream.headers.get("content-type", "application/json")
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=content_type)


@app.api_route("/luminax-brain", methods=["GET", "POST"])
@app.api_route("/luminax-brain/{path:path}", methods=["GET", "POST"])
async def proxy_luminax_brain(request: Request, path: str = "") -> Response:
    return await _proxy_local_service(LUMINAX_BRAIN_LOCAL_URL, path, request)


@app.api_route("/affect-controller", methods=["GET", "POST"])
@app.api_route("/affect-controller/{path:path}", methods=["GET", "POST"])
async def proxy_affect_controller(request: Request, path: str = "") -> Response:
    return await _proxy_local_service(AFFECT_CONTROLLER_LOCAL_URL, path, request)
