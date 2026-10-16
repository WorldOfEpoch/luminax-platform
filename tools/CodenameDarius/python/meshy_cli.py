from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_request(request, *, timeout, context=None):
    handlers = [_NoRedirect()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers).open(request, timeout=timeout)

from pathlib import Path
from typing import Any


MESHY_API_BASE = "https://api.meshy.ai/openapi/v2"


def _env_value(*keys: str, default: str = "") -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return default


def project_root() -> Path:
    raw = _env_value("DARIUS_PROJECT_ROOT", "UNDERFOOT_DARIUS_PROJECT_ROOT")
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else (Path(__file__).resolve().parent / path).resolve()
    return Path(__file__).resolve().parent.parent


def load_env() -> None:
    configured = _env_value("DARIUS_ENV_FILE", "LUMINAX_TOOLS_ENV_FILE")
    candidates = [Path(configured).expanduser()] if configured else []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"").strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def api_headers() -> dict[str, str]:
    token = _env_value("MESHY_API_KEY", "DARIUS_MESHY_API_KEY", "UNDERFOOT_MESHY_API_KEY")
    headers = {"Accept": "application/json"}
    if not token:
        raise RuntimeError("Set an explicit MESHY_API_KEY for provider requests")
    headers["Authorization"] = f"Bearer {token}"
    return headers


def infer_extension(url: str, default: str = "") -> str:
    parsed = urllib.parse.urlparse(url or "")
    suffix = Path(parsed.path).suffix
    return suffix or default


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> urllib.request.Request:
    safe_path = path if path.startswith("/") else f"/{path}"
    url = f"{_env_value('MESHY_API_BASE', 'DARIUS_MESHY_API_BASE', 'UNDERFOOT_MESHY_API_BASE', default=MESHY_API_BASE).rstrip('/')}{safe_path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = api_headers()
    if data is not None:
        headers["Content-Type"] = "application/json"
    return urllib.request.Request(url=url, data=data, method=method.upper(), headers=headers)


def request_json(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = _request(method, path, payload=payload)
    try:
        with _open_request(request, timeout=180) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Meshy request failed ({error.code}) at {path}:\n{body}") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"Meshy request failed at {path}: {error.reason}") from error


def request_bytes(url: str) -> bytes:
    request = urllib.request.Request(url=url, headers={"Accept": "*/*"})
    try:
        with _open_request(request, timeout=180) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Download failed ({error.code}) at {url}:\n{body}") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"Download failed at {url}: {error.reason}") from error


def resolve_import_folder(asset_kind: str) -> Path:
    normalized = str(asset_kind or "props").strip().lower()
    env_map = {
        "warble": "UNDERFOOT_MESHY_WARBLE_IMPORT",
        "biggun": "UNDERFOOT_MESHY_BIGGUN_IMPORT",
        "backdrop": "UNDERFOOT_MESHY_BACKDROP_IMPORT",
        "props": "UNDERFOOT_MESHY_PROPS_IMPORT",
    }
    defaults = {
        "warble": "Assets/Models/MeshyIncoming/Warble",
        "biggun": "Assets/Models/MeshyIncoming/Biggun",
        "backdrop": "Assets/Textures/Generated",
        "props": "Assets/Models/MeshyIncoming/Props",
    }
    raw = _env_value(env_map.get(normalized, ""), default=defaults.get(normalized, defaults["props"]))
    path = Path(raw)
    if path.is_absolute():
        return path
    return (project_root() / "unity" / "Underfoot" / path).resolve()


def task_store_root() -> Path:
    raw = _env_value("DARIUS_TASK_STORE_ROOT", "UNDERFOOT_DARIUS_TASK_STORE_ROOT", default="server/runtime/tasks")
    path = Path(raw)
    if path.is_absolute():
        return path
    return (project_root() / path).resolve()


def write_task_snapshot(kind: str, task_id: str, payload: dict[str, Any]) -> Path:
    root = task_store_root()
    root.mkdir(parents=True, exist_ok=True)
    safe_kind = str(kind or "task").strip().replace(" ", "_").replace("/", "_")
    safe_task = str(task_id or "unknown").strip().replace(" ", "_").replace("/", "_")
    path = root / f"{safe_kind}_{safe_task}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
