from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
import re
import ssl
import time
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

from meshy_cli import (
    api_headers,
    infer_extension,
    load_env,
    project_root,
    request_bytes,
    request_json,
    resolve_import_folder,
    task_store_root,
    write_task_snapshot,
)


TEXT_TO_3D_API_BASE = "https://api.meshy.ai/openapi/v2"
DEFAULT_DATABASE_ASSET_PATH = "Assets/Data/DariusDatabase.json"
DEFAULT_LEGACY_DATABASE_ASSET_PATH = "Assets/Data/EpochPortalDatabase.json"
DEFAULT_QUEUE_PATH = "server/runtime/darius_requests.jsonl"
DEFAULT_LEGACY_QUEUE_PATH = "server/runtime/epoch_portal_requests.jsonl"
DEFAULT_STATE_PATH = "server/runtime/darius_state.json"
DEFAULT_PROJECT_MEMORY_PATH = "shared/darius_project_memory.json"
DEFAULT_REPO_RULES_PATH = "shared/darius_repo_rules.md"
DEFAULT_SERVER_REFS_PATH = "shared/darius_server_refs.json"
DEFAULT_WORKFLOW_PATH = "shared/darius_workflow.json"
DEFAULT_THREAD_REGISTRY_PATH = "server/runtime/darius_thread_registry.json"
DEFAULT_ASSIGNMENTS_PATH = "server/runtime/darius_assignments.json"
DEFAULT_RELAY_URL = "https://127.0.0.1:6969"
DEFAULT_RELAY_TIMEOUT_SECONDS = 45
DEFAULT_RETRY_LIMIT = 8
DEFAULT_OWNERSHIP_CACHE_PATH = Path(os.getenv("APPDATA", "")) / "Unity" / "Asset Store" / "Cache" / "AssetStoreCache.json"
DEFAULT_STORE_CACHE_ROOTS = (
    Path(os.getenv("APPDATA", "")) / "Unity" / "Asset Store-5.x",
    Path(os.getenv("APPDATA", "")) / "Unity" / "Asset Store",
)


def env_value(*keys: str, default: str = "") -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return default


def env_path(default_value: str, *keys: str, base_root: str = "project") -> Path:
    raw = env_value(*keys, default=default_value).strip()
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    root = unity_project_path() if base_root == "unity" else project_root()
    return root / candidate


def project_key() -> str:
    return env_value("DARIUS_PROJECT_KEY", "UNDERFOOT_DARIUS_PROJECT_KEY", "UNDERFOOT_PROJECT_NAME", default="underfoot") or "underfoot"


def project_title() -> str:
    return env_value("DARIUS_PROJECT_TITLE", "UNDERFOOT_PROJECT_TITLE", default=project_key().replace("-", " ").title())


def project_category() -> str:
    return env_value("DARIUS_PROJECT_CATEGORY", "UNDERFOOT_PROJECT_CATEGORY", default="generic")


def unity_project_path() -> Path:
    value = env_value("DARIUS_UNITY_PROJECT_PATH", "UNDERFOOT_UNITY_PROJECT_PATH")
    if value:
        candidate = Path(value)
        return candidate if candidate.is_absolute() else project_root() / candidate
    return project_root() / "unity" / "Underfoot"


def database_asset_path() -> Path:
    return env_path(
        DEFAULT_DATABASE_ASSET_PATH,
        "DARIUS_DATABASE_ASSET_PATH",
        "UNDERFOOT_DARIUS_DATABASE_ASSET_PATH",
        "UNDERFOOT_EPOCH_PORTAL_DATABASE_ASSET_PATH",
        base_root="unity",
    )


def queue_file_path() -> Path:
    return env_path(DEFAULT_QUEUE_PATH, "DARIUS_QUEUE_PATH", "UNDERFOOT_DARIUS_QUEUE_PATH", "UNDERFOOT_EPOCH_PORTAL_QUEUE_PATH")


def legacy_database_asset_path() -> Path:
    return env_path(DEFAULT_LEGACY_DATABASE_ASSET_PATH, "UNDERFOOT_EPOCH_PORTAL_DATABASE_ASSET_PATH", base_root="unity")


def legacy_queue_file_path() -> Path:
    return env_path(DEFAULT_LEGACY_QUEUE_PATH, "UNDERFOOT_EPOCH_PORTAL_QUEUE_PATH")


def darius_state_path() -> Path:
    return env_path(DEFAULT_STATE_PATH, "DARIUS_STATE_PATH", "UNDERFOOT_DARIUS_STATE_PATH")


def project_memory_path() -> Path:
    return env_path(DEFAULT_PROJECT_MEMORY_PATH, "DARIUS_PROJECT_MEMORY_PATH", "UNDERFOOT_DARIUS_PROJECT_MEMORY_PATH")


def repo_rules_path() -> Path:
    return env_path(DEFAULT_REPO_RULES_PATH, "DARIUS_REPO_RULES_PATH", "UNDERFOOT_DARIUS_REPO_RULES_PATH")


def server_refs_path() -> Path:
    return env_path(DEFAULT_SERVER_REFS_PATH, "DARIUS_SERVER_REFS_PATH", "UNDERFOOT_DARIUS_SERVER_REFS_PATH")


def workflow_path() -> Path:
    return env_path(DEFAULT_WORKFLOW_PATH, "DARIUS_WORKFLOW_PATH", "UNDERFOOT_DARIUS_WORKFLOW_PATH")


def thread_registry_path() -> Path:
    return env_path(DEFAULT_THREAD_REGISTRY_PATH, "DARIUS_THREAD_REGISTRY_PATH", "UNDERFOOT_DARIUS_THREAD_REGISTRY_PATH")


def assignments_path() -> Path:
    return env_path(DEFAULT_ASSIGNMENTS_PATH, "DARIUS_ASSIGNMENTS_PATH", "UNDERFOOT_DARIUS_ASSIGNMENTS_PATH")


def relay_url() -> str:
    return env_value("DARIUS_RELAY_URL", "UNDERFOOT_DARIUS_RELAY_URL", default=DEFAULT_RELAY_URL).strip() or DEFAULT_RELAY_URL


def relay_token() -> str:
    return env_value("DARIUS_RELAY_TOKEN", "UNDERFOOT_DARIUS_RELAY_TOKEN", "CONTROL_TOKEN")


def relay_basic_user() -> str:
    return env_value("DARIUS_RELAY_BASIC_USER", "UNDERFOOT_DARIUS_RELAY_BASIC_USER")


def relay_basic_password() -> str:
    return env_value("DARIUS_RELAY_BASIC_PASSWORD", "UNDERFOOT_DARIUS_RELAY_BASIC_PASSWORD")


def relay_host_header() -> str:
    return env_value("DARIUS_RELAY_HOST_HEADER", "UNDERFOOT_DARIUS_RELAY_HOST_HEADER")


def relay_timeout_seconds() -> int:
    raw = env_value("DARIUS_RELAY_TIMEOUT_SECONDS", "UNDERFOOT_DARIUS_RELAY_TIMEOUT_SECONDS", default=str(DEFAULT_RELAY_TIMEOUT_SECONDS))
    try:
        return max(5, int(raw))
    except ValueError:
        return DEFAULT_RELAY_TIMEOUT_SECONDS


def relay_verify_tls() -> bool:
    # Legacy option names remain recognized by callers; verification is mandatory.
    return True


def retry_limit() -> int:
    raw = env_value("DARIUS_RETRY_LIMIT", "UNDERFOOT_DARIUS_RETRY_LIMIT", "UNDERFOOT_EPOCH_PORTAL_RETRY_LIMIT", default=str(DEFAULT_RETRY_LIMIT)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_RETRY_LIMIT


def ownership_cache_path() -> Path:
    raw = env_value("DARIUS_UNITY_ASSET_STORE_OWNERSHIP_CACHE_PATH", "UNDERFOOT_UNITY_ASSET_STORE_OWNERSHIP_CACHE_PATH").strip()
    if raw:
        candidate = Path(raw)
        return candidate if candidate.is_absolute() else project_root() / candidate
    return DEFAULT_OWNERSHIP_CACHE_PATH


def resolve_output_folder(path_value: str) -> Path:
    candidate = Path(ensure_string(path_value))
    if candidate.is_absolute():
        return candidate
    return unity_project_path() / candidate


def load_text_to_3d_json(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{TEXT_TO_3D_API_BASE}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url=url, data=data, method=method, headers=api_headers())
    try:
        with _open_request(request, timeout=180) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Meshy text-to-3d request failed ({error.code}) at {path}:\n{body}") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"Meshy text-to-3d request failed at {path}: {error.reason}") from error


def current_epoch() -> int:
    return int(time.time())


def local_machine_label() -> str:
    return env_value("DARIUS_MACHINE_LABEL", "UNDERFOOT_DARIUS_MACHINE_LABEL", "COMPUTERNAME", default="darius-local") or "darius-local"


def local_agent_id() -> str:
    return env_value("DARIUS_AGENT_ID", "UNDERFOOT_DARIUS_AGENT_ID", default=slugify(local_machine_label() or "darius-local")) or slugify(local_machine_label() or "darius-local")


def local_thread_id() -> str:
    return env_value("DARIUS_LOCAL_THREAD_ID", "UNDERFOOT_DARIUS_LOCAL_THREAD_ID", default=f"darius_{slugify(project_key())}") or f"darius_{slugify(project_key())}"


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value.strip())
    return cleaned.strip("_") or f"epoch_{current_epoch()}"


def default_database() -> dict[str, Any]:
    return {
        "version": 2,
        "updatedAtEpoch": current_epoch(),
        "retryLimit": retry_limit(),
        "modelJobs": [],
        "imageJobs": [],
        "storeAssets": [],
        "ownedAssetHints": [],
        "dariusMessages": [],
        "route": {},
        "luminaxMessages": [],
        "projectMemory": {},
        "projectPack": {},
        "thread": {},
        "serviceAssignments": [],
        "assignmentSummary": {},
        "activeAssignment": {},
    }


def ensure_database_shape(database: dict[str, Any]) -> dict[str, Any]:
    result = default_database()
    result.update(database)
    result["version"] = 2
    for key in ("modelJobs", "imageJobs", "storeAssets", "ownedAssetHints", "dariusMessages", "luminaxMessages", "serviceAssignments"):
        if not isinstance(result.get(key), list):
            result[key] = []
    if not isinstance(result.get("route"), dict):
        result["route"] = {}
    if not isinstance(result.get("projectMemory"), dict):
        result["projectMemory"] = {}
    if not isinstance(result.get("projectPack"), dict):
        result["projectPack"] = {}
    if not isinstance(result.get("thread"), dict):
        result["thread"] = {}
    if not isinstance(result.get("assignmentSummary"), dict):
        result["assignmentSummary"] = {}
    if not isinstance(result.get("activeAssignment"), dict):
        result["activeAssignment"] = {}
    result["retryLimit"] = retry_limit()
    return result


def load_database() -> dict[str, Any]:
    path = database_asset_path()
    if not path.exists():
        legacy_path = legacy_database_asset_path()
        if legacy_path.exists():
            return ensure_database_shape(json.loads(legacy_path.read_text(encoding="utf-8")))
    if not path.exists():
        return default_database()
    return ensure_database_shape(json.loads(path.read_text(encoding="utf-8")))


def save_database(database: dict[str, Any]) -> Path:
    path = database_asset_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    database["updatedAtEpoch"] = current_epoch()
    path.write_text(json.dumps(database, indent=2), encoding="utf-8")
    return path


def to_project_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root().resolve()))
    except ValueError:
        return str(path)


def sanitize_name(value: str, fallback: str) -> str:
    trimmed = value.strip()
    return trimmed if trimmed else fallback


def stringify_list(values: list[str]) -> list[str]:
    return [str(item) for item in values if str(item).strip()]


def path_to_data_uri(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    mime_type = mime_type or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def unique_path_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def find_job(database: dict[str, Any], collection_key: str, job_id: str) -> dict[str, Any] | None:
    for record in database.get(collection_key, []):
        if str(record.get("id", "")) == job_id:
            return record
    return None


def upsert_record(records: list[dict[str, Any]], record_id: str, defaults: dict[str, Any]) -> dict[str, Any]:
    for existing in records:
        if str(existing.get("id", "")) == record_id:
            existing.update(defaults)
            return existing
    created = {"id": record_id}
    created.update(defaults)
    records.append(created)
    return created


def find_record(records: list[dict[str, Any]], record_id: str) -> dict[str, Any] | None:
    for record in records:
        if ensure_string(record.get("id")) == record_id:
            return record
    return None


def ensure_string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(has_value(item) for item in value)
    return True


def parse_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_payload_file(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def relay_request_json(method: str, path: str, payload: dict[str, Any] | None = None, query: dict[str, Any] | None = None) -> dict[str, Any]:
    base = relay_url().rstrip("/")
    if not base:
        raise SystemExit("Darius relay URL is not configured.")
    safe_path = path if path.startswith("/") else f"/{path}"
    url = f"{base}{safe_path}"
    if isinstance(query, dict):
        query_pairs: list[tuple[str, str]] = []
        for key, value in query.items():
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            query_pairs.append((str(key), text))
        if query_pairs:
            url = f"{url}?{urllib.parse.urlencode(query_pairs)}"
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    host_header = relay_host_header()
    if host_header:
        headers["Host"] = host_header
    basic_user = relay_basic_user()
    if basic_user:
        basic_password = relay_basic_password()
        token = base64.b64encode(f"{basic_user}:{basic_password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(url=url, data=body, method=method.upper(), headers=headers)
    context = None
    if url.lower().startswith("https://"):
        context = ssl.create_default_context()
    try:
        with _open_request(request, timeout=relay_timeout_seconds(), context=context) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        body_text = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Darius relay request failed ({error.code}) at {safe_path}:\n{body_text}") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"Darius relay request failed at {safe_path}: {error.reason}") from error


def manifest_model_output_name(downloads: list[str], fallback: str) -> str:
    for item in downloads:
        suffix = Path(item).suffix.lower()
        if suffix in {".fbx", ".glb", ".obj", ".usd", ".usdz"}:
            return Path(item).stem
    return fallback


def normalize_snapshot_task_id(stem: str, prefix: str) -> str:
    value = stem.replace(prefix, "", 1)
    for known_prefix in ("request_", "result_", "status_"):
        if value.startswith(known_prefix):
            return value[len(known_prefix) :]
    return value


def merge_unique_list(existing_values: list[str] | None, incoming_values: list[str] | None) -> list[str]:
    results: list[str] = []
    for value in stringify_list(existing_values or []) + stringify_list(incoming_values or []):
        if value not in results:
            results.append(value)
    return results


def status_rank(status: str) -> int:
    lookup = {
        "FAILED": 5,
        "CANCELED": 4,
        "SUCCEEDED": 3,
        "IN_PROGRESS": 2,
        "RUNNING": 2,
        "PROCESSING": 2,
        "QUEUED": 1,
        "PENDING": 1,
    }
    return lookup.get(status.upper(), 0)


def prefer_value(existing_value: Any, incoming_value: Any, *, force: bool = False) -> Any:
    if force and has_value(incoming_value):
        return incoming_value
    if has_value(existing_value):
        return existing_value
    return incoming_value


def is_manifest_source(path_value: str) -> bool:
    normalized = ensure_string(path_value).replace("\\", "/").lower()
    return (
        normalized.endswith("_manifest.json")
        or normalized.endswith("_generation.json")
        or "generation_manifest.json" in normalized
    )


def extract_task_id_from_source_path(path_value: str) -> str:
    if not path_value:
        return ""
    stem = Path(path_value).stem
    for prefix in (
        "image_to_3d_",
        "image_",
        "text_to_image_",
        "text-image_",
        "epoch_portal_text3d_preview_request_",
        "epoch_portal_text3d_refine_request_",
        "epoch_portal_text3d_status_",
    ):
        if stem.startswith(prefix):
            return normalize_snapshot_task_id(stem, prefix)
    return ""


def canonical_job_id(record: dict[str, Any]) -> str:
    for key in ("taskId", "id"):
        value = ensure_string(record.get(key))
        stripped = normalize_snapshot_task_id(value, "") if value else ""
        if stripped:
            return stripped
    extracted = extract_task_id_from_source_path(ensure_string(record.get("sourceTaskFile")))
    return extracted or ensure_string(record.get("id"))


def merge_model_record(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    incoming_source = ensure_string(incoming.get("sourceTaskFile"))
    force_source = is_manifest_source(incoming_source)
    existing["id"] = canonical_job_id(existing) or canonical_job_id(incoming)
    existing["taskId"] = canonical_job_id(existing) or canonical_job_id(incoming)
    for key in (
        "displayName",
        "assetKind",
        "requestMode",
        "previewTaskId",
        "prompt",
        "negativePrompt",
        "texturePrompt",
        "referenceImagePath",
        "expectedAssetName",
        "outputFolder",
    ):
        existing[key] = prefer_value(existing.get(key), incoming.get(key), force=force_source)
    incoming_status = ensure_string(incoming.get("status"))
    if status_rank(incoming_status) >= status_rank(ensure_string(existing.get("status"))):
        existing["status"] = incoming_status or ensure_string(existing.get("status"))
    existing["retryCount"] = max(int(existing.get("retryCount", 0) or 0), int(incoming.get("retryCount", 0) or 0))
    existing["lastProgress"] = max(int(existing.get("lastProgress", 0) or 0), int(incoming.get("lastProgress", 0) or 0))
    existing["downloadedFiles"] = merge_unique_list(existing.get("downloadedFiles"), incoming.get("downloadedFiles"))
    existing["historyTaskIdsCsv"] = ",".join(
        item for item in merge_unique_list(
            ensure_string(existing.get("historyTaskIdsCsv")).split(","),
            ensure_string(incoming.get("historyTaskIdsCsv")).split(","),
        )
        if item
    )
    existing["thumbnailPath"] = prefer_value(existing.get("thumbnailPath"), incoming.get("thumbnailPath"), force=bool(incoming.get("thumbnailPath")))
    existing["lastError"] = prefer_value(existing.get("lastError"), incoming.get("lastError"), force=bool(incoming.get("lastError")))
    existing["sourceTaskFile"] = prefer_value(existing.get("sourceTaskFile"), incoming_source, force=force_source)
    existing["createdAtEpoch"] = int(existing.get("createdAtEpoch", 0) or incoming.get("createdAtEpoch", current_epoch()) or current_epoch())
    existing["updatedAtEpoch"] = max(int(existing.get("updatedAtEpoch", 0) or 0), int(incoming.get("updatedAtEpoch", 0) or current_epoch()))
    return existing


def merge_image_record(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    incoming_source = ensure_string(incoming.get("sourceTaskFile"))
    force_source = is_manifest_source(incoming_source)
    existing["id"] = canonical_job_id(existing) or canonical_job_id(incoming)
    existing["taskId"] = canonical_job_id(existing) or canonical_job_id(incoming)
    for key in ("displayName", "prompt", "negativePrompt", "aspectRatio", "outputFolder"):
        existing[key] = prefer_value(existing.get(key), incoming.get(key), force=force_source)
    incoming_status = ensure_string(incoming.get("status"))
    if status_rank(incoming_status) >= status_rank(ensure_string(existing.get("status"))):
        existing["status"] = incoming_status or ensure_string(existing.get("status"))
    existing["downloadedFiles"] = merge_unique_list(existing.get("downloadedFiles"), incoming.get("downloadedFiles"))
    existing["lastError"] = prefer_value(existing.get("lastError"), incoming.get("lastError"), force=bool(incoming.get("lastError")))
    existing["sourceTaskFile"] = prefer_value(existing.get("sourceTaskFile"), incoming_source, force=force_source)
    existing["createdAtEpoch"] = int(existing.get("createdAtEpoch", 0) or incoming.get("createdAtEpoch", current_epoch()) or current_epoch())
    existing["updatedAtEpoch"] = max(int(existing.get("updatedAtEpoch", 0) or 0), int(incoming.get("updatedAtEpoch", 0) or current_epoch()))
    return existing


def upsert_model_job(database: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    records = database["modelJobs"]
    canonical_id = canonical_job_id(record)
    record["id"] = canonical_id
    record["taskId"] = canonical_id
    existing = find_record(records, canonical_id)
    if existing is None:
        records.append(record)
        return record
    return merge_model_record(existing, record)


def upsert_image_job(database: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    records = database["imageJobs"]
    canonical_id = canonical_job_id(record)
    record["id"] = canonical_id
    record["taskId"] = canonical_id
    existing = find_record(records, canonical_id)
    if existing is None:
        records.append(record)
        return record
    return merge_image_record(existing, record)


def dedupe_jobs(database: dict[str, Any]) -> None:
    merged_models: list[dict[str, Any]] = []
    for record in database.get("modelJobs", []):
        candidate = dict(record)
        candidate["id"] = canonical_job_id(candidate)
        candidate["taskId"] = candidate["id"]
        existing = find_record(merged_models, candidate["id"])
        if existing is None:
            merged_models.append(candidate)
        else:
            merge_model_record(existing, candidate)
    merged_models.sort(key=lambda item: int(item.get("updatedAtEpoch", item.get("createdAtEpoch", 0)) or 0), reverse=True)
    database["modelJobs"] = merged_models

    merged_images: list[dict[str, Any]] = []
    for record in database.get("imageJobs", []):
        candidate = dict(record)
        candidate["id"] = canonical_job_id(candidate)
        candidate["taskId"] = candidate["id"]
        existing = find_record(merged_images, candidate["id"])
        if existing is None:
            merged_images.append(candidate)
        else:
            merge_image_record(existing, candidate)
    merged_images.sort(key=lambda item: int(item.get("updatedAtEpoch", item.get("createdAtEpoch", 0)) or 0), reverse=True)
    database["imageJobs"] = merged_images


def hydrate_model_catalog_assets(database: dict[str, Any]) -> None:
    for job in database.get("modelJobs", []):
        output_root = resolve_output_folder(ensure_string(job.get("outputFolder")))
        if not output_root.exists():
            continue

        thumbnail_path = ensure_string(job.get("thumbnailPath"))
        if thumbnail_path and Path(thumbnail_path).exists():
            continue

        expected_asset_name = ensure_string(job.get("expectedAssetName"))
        preferred_candidates: list[Path] = []
        if expected_asset_name:
            for suffix in ("_thumbnail.png", "_preview.png"):
                candidate = output_root / f"{expected_asset_name}{suffix}"
                if candidate.exists():
                    preferred_candidates.append(candidate)

        if preferred_candidates:
            job["thumbnailPath"] = str(preferred_candidates[0])
            continue

        downloaded_files = [Path(item) for item in stringify_list(job.get("downloadedFiles")) if Path(item).exists()]
        if downloaded_files:
            continue

        generic_candidates = sorted(output_root.glob("*_thumbnail.png")) + sorted(output_root.glob("*_preview.png"))
        if len(generic_candidates) == 1:
            job["thumbnailPath"] = str(generic_candidates[0])


def create_model_record(
    *,
    record_id: str,
    display_name: str,
    asset_kind: str,
    request_mode: str,
    task_id: str,
    status: str,
    prompt: str,
    negative_prompt: str,
    texture_prompt: str,
    reference_image_path: str,
    expected_asset_name: str,
    output_folder: str,
    preview_task_id: str = "",
    retry_count: int = 0,
    downloaded_files: list[str] | None = None,
    thumbnail_path: str = "",
    source_task_file: str = "",
    last_error: str = "",
    last_progress: int = 0,
    history_task_ids_csv: str = "",
) -> dict[str, Any]:
    return {
        "id": record_id,
        "createdAtEpoch": current_epoch(),
        "updatedAtEpoch": current_epoch(),
        "displayName": display_name,
        "assetKind": asset_kind,
        "requestMode": request_mode,
        "status": status,
        "taskId": task_id,
        "previewTaskId": preview_task_id,
        "retryCount": retry_count,
        "prompt": prompt,
        "negativePrompt": negative_prompt,
        "texturePrompt": texture_prompt,
        "referenceImagePath": reference_image_path,
        "expectedAssetName": expected_asset_name,
        "outputFolder": output_folder,
        "downloadedFiles": downloaded_files or [],
        "thumbnailPath": thumbnail_path,
        "sourceTaskFile": source_task_file,
        "lastError": last_error,
        "lastProgress": last_progress,
        "historyTaskIdsCsv": history_task_ids_csv,
    }


def create_image_record(
    *,
    record_id: str,
    display_name: str,
    prompt: str,
    negative_prompt: str,
    aspect_ratio: str,
    task_id: str,
    status: str,
    output_folder: str,
    downloaded_files: list[str] | None = None,
    source_task_file: str = "",
    last_error: str = "",
) -> dict[str, Any]:
    return {
        "id": record_id,
        "createdAtEpoch": current_epoch(),
        "updatedAtEpoch": current_epoch(),
        "displayName": display_name,
        "prompt": prompt,
        "negativePrompt": negative_prompt,
        "aspectRatio": aspect_ratio,
        "taskId": task_id,
        "status": status,
        "outputFolder": output_folder,
        "downloadedFiles": downloaded_files or [],
        "sourceTaskFile": source_task_file,
        "lastError": last_error,
    }


def create_store_record(
    *,
    record_id: str,
    display_name: str,
    publisher: str,
    local_package_path: str,
    file_size_bytes: int,
    last_modified_epoch: int,
    source_root: str,
) -> dict[str, Any]:
    return {
        "id": record_id,
        "displayName": display_name,
        "publisher": publisher,
        "localPackagePath": local_package_path,
        "fileSizeBytes": file_size_bytes,
        "lastModifiedEpoch": last_modified_epoch,
        "status": "downloaded",
        "sourceRoot": source_root,
    }


def create_owned_asset_hint(asset_version_id: str, is_asset_project: bool) -> dict[str, Any]:
    return {
        "id": f"owned_{asset_version_id}",
        "assetVersionId": asset_version_id,
        "isAssetProject": is_asset_project,
    }


def create_luminax_message_record(subject: str, body: str, file_paths: list[str]) -> dict[str, Any]:
    message_id = f"luminax_{current_epoch()}_{slugify(subject or 'request')}"
    return {
        "id": message_id,
        "createdAtEpoch": current_epoch(),
        "subject": subject,
        "body": body,
        "filePaths": file_paths,
        "status": "queued",
        "queueFilePath": to_project_relative(queue_file_path()),
    }


def create_darius_message_record(text: str, attachments: list[str], requested_action: str, expect_verification: bool) -> dict[str, Any]:
    ts = current_epoch()
    current_project_key = project_key()
    return {
        "id": f"darius_{ts}_{slugify(requested_action or 'chat')}",
        "createdAtEpoch": ts,
        "updatedAtEpoch": ts,
        "role": "operator",
        "text": text,
        "status": "queued",
        "projectKey": current_project_key,
        "activeSessionId": "",
        "agentId": local_agent_id(),
        "machineLabel": local_machine_label(),
        "source": "unity_darius",
        "verificationSummary": "Verification requested." if expect_verification else "",
        "error": "",
        "attachments": attachments,
        "artifactPaths": [],
    }


def sync_title_foot_manifest(database: dict[str, Any], manifest_path: Path) -> None:
    manifest = parse_json_file(manifest_path)
    task_id = ensure_string(manifest.get("task_id"))
    if not task_id:
        return

    downloads = stringify_list(manifest.get("downloads", []))
    defaults = create_model_record(
        record_id=task_id,
        display_name="Startup Title Foot",
        asset_kind="props",
        request_mode="image-to-3d",
        task_id=task_id,
        status="SUCCEEDED",
        prompt="",
        negative_prompt=ensure_string(manifest.get("negative_prompt")),
        texture_prompt=ensure_string(manifest.get("texture_prompt")),
        reference_image_path=ensure_string(manifest.get("reference_image")),
        expected_asset_name=manifest_model_output_name(downloads, "SM_BiggunFoot_Title_01"),
        output_folder=os.getenv("UNDERFOOT_MESHY_PROPS_IMPORT", "Assets/Models/MeshyIncoming/Props"),
        downloaded_files=downloads,
        thumbnail_path=next((item for item in downloads if item.lower().endswith(".png")), ""),
        source_task_file=to_project_relative(manifest_path),
    )
    upsert_model_job(database, defaults)


def sync_character_manifest(database: dict[str, Any], manifest_path: Path) -> None:
    manifest = parse_json_file(manifest_path)
    characters = manifest.get("characters", [])
    if not isinstance(characters, list):
        return

    for character in characters:
        if not isinstance(character, dict):
            continue

        task_id = ensure_string(character.get("refine_task_id") or character.get("task_id"))
        if not task_id:
            continue

        key = ensure_string(character.get("key"))
        asset_kind = "warble" if "warble" in key else "biggun" if "biggun" in key else "props"
        downloads = stringify_list(character.get("downloads", []))
        defaults = create_model_record(
            record_id=task_id,
            display_name=key.replace("_", " ").title(),
            asset_kind=asset_kind,
            request_mode="text-to-3d",
            task_id=task_id,
            preview_task_id=ensure_string(character.get("preview_task_id")),
            status="SUCCEEDED",
            prompt=ensure_string(character.get("prompt")),
            negative_prompt=ensure_string(character.get("negative_prompt")),
            texture_prompt=ensure_string(character.get("texture_prompt")),
            reference_image_path="",
            expected_asset_name=manifest_model_output_name(downloads, slugify(key or task_id)),
            output_folder=os.getenv(
                "UNDERFOOT_MESHY_WARBLE_IMPORT" if asset_kind == "warble" else "UNDERFOOT_MESHY_BIGGUN_IMPORT",
                "Assets/Models/MeshyIncoming/Warble" if asset_kind == "warble" else "Assets/Models/MeshyIncoming/Biggun",
            ),
            downloaded_files=downloads,
            thumbnail_path=next((item for item in downloads if item.lower().endswith(".png")), ""),
            source_task_file=to_project_relative(manifest_path),
        )
        upsert_model_job(database, defaults)


def sync_image_to_3d_snapshots(database: dict[str, Any], task_root: Path) -> None:
    for snapshot_path in sorted(task_root.glob("image_to_3d_*.json")):
        snapshot = parse_json_file(snapshot_path)
        task_id = ensure_string(snapshot.get("id"))
        if task_id:
            status = ensure_string(snapshot.get("status")) or "QUEUED"
            asset_kind = ensure_string(snapshot.get("asset_kind")) or "props"
            name = ensure_string(snapshot.get("name")) or normalize_snapshot_task_id(snapshot_path.stem, "image_to_3d_")
            upsert_model_job(
                database,
                create_model_record(
                    record_id=task_id,
                    display_name=name,
                    asset_kind=asset_kind,
                    request_mode="image-to-3d",
                    task_id=task_id,
                    status=status,
                    prompt=ensure_string(snapshot.get("object_prompt") or snapshot.get("style_prompt")),
                    negative_prompt=ensure_string(snapshot.get("negative_prompt")),
                    texture_prompt=ensure_string(snapshot.get("texture_prompt")),
                    reference_image_path="",
                    expected_asset_name=sanitize_name(name, slugify(name)),
                    output_folder=os.getenv(
                        {
                            "warble": "UNDERFOOT_MESHY_WARBLE_IMPORT",
                            "biggun": "UNDERFOOT_MESHY_BIGGUN_IMPORT",
                            "props": "UNDERFOOT_MESHY_PROPS_IMPORT",
                        }.get(asset_kind, "UNDERFOOT_MESHY_PROPS_IMPORT"),
                        "Assets/Models/MeshyIncoming/Props",
                    ),
                    source_task_file=to_project_relative(snapshot_path),
                    last_progress=int(snapshot.get("progress", 0) or 0),
                    last_error=ensure_string((snapshot.get("task_error") or {}).get("message")),
                ),
            )
            continue

        response = snapshot.get("response", {})
        request = snapshot.get("request", {})
        task_id = ensure_string(response.get("result")) or normalize_snapshot_task_id(snapshot_path.stem, "image_to_3d_")
        asset_kind = ensure_string(snapshot.get("asset_kind")) or "props"
        name = ensure_string(request.get("displayName") or request.get("name")) or task_id
        reference_image_path = ensure_string(request.get("referenceImagePath"))
        if reference_image_path.startswith("data:"):
            reference_image_path = ""
        upsert_model_job(
            database,
            create_model_record(
                record_id=task_id,
                display_name=name,
                asset_kind=asset_kind,
                request_mode="image-to-3d",
                task_id=task_id,
                status="QUEUED",
                prompt=ensure_string(request.get("prompt") or request.get("name")),
                negative_prompt=ensure_string(request.get("negativePrompt") or request.get("negative_prompt")),
                texture_prompt=ensure_string(request.get("texturePrompt") or request.get("texture_prompt")),
                reference_image_path=reference_image_path,
                expected_asset_name=sanitize_name(ensure_string(request.get("expectedAssetName")), slugify(name)),
                output_folder=os.getenv(
                    {
                        "warble": "UNDERFOOT_MESHY_WARBLE_IMPORT",
                        "biggun": "UNDERFOOT_MESHY_BIGGUN_IMPORT",
                        "props": "UNDERFOOT_MESHY_PROPS_IMPORT",
                    }.get(asset_kind, "UNDERFOOT_MESHY_PROPS_IMPORT"),
                    "Assets/Models/MeshyIncoming/Props",
                ),
                source_task_file=to_project_relative(snapshot_path),
            ),
        )

    for snapshot_path in sorted(task_root.glob("image_*.json")):
        payload = parse_json_file(snapshot_path)
        task_id = snapshot_path.stem.replace("image_", "", 1)
        existing = find_job(database, "modelJobs", task_id)
        if existing is None:
            continue
        existing["status"] = ensure_string(payload.get("status")) or existing.get("status", "QUEUED")
        existing["lastProgress"] = int(payload.get("progress", existing.get("lastProgress", 0)) or 0)
        existing["lastError"] = ensure_string((payload.get("task_error") or {}).get("message"))
        existing["updatedAtEpoch"] = current_epoch()


def sync_text_to_image_snapshots(database: dict[str, Any], task_root: Path) -> None:
    for snapshot_path in sorted(task_root.glob("text_to_image_*.json")):
        snapshot = parse_json_file(snapshot_path)
        response = snapshot.get("response", {})
        request = snapshot.get("request", {})
        task_id = ensure_string(response.get("result")) or normalize_snapshot_task_id(snapshot_path.stem, "text_to_image_")
        name = ensure_string(request.get("displayName") or request.get("name")) or task_id
        upsert_image_job(
            database,
            create_image_record(
                record_id=task_id,
                display_name=name,
                prompt=ensure_string(request.get("prompt")),
                negative_prompt=ensure_string(request.get("negativePrompt") or request.get("negative_prompt")),
                aspect_ratio=ensure_string(request.get("aspectRatio") or request.get("aspect_ratio")) or "16:9",
                task_id=task_id,
                status="QUEUED",
                output_folder=os.getenv("UNDERFOOT_MESHY_BACKDROP_IMPORT", "Assets/Textures/Generated"),
                source_task_file=to_project_relative(snapshot_path),
            ),
        )

    for snapshot_path in sorted(task_root.glob("text-image_*.json")):
        payload = parse_json_file(snapshot_path)
        task_id = snapshot_path.stem.replace("text-image_", "", 1)
        existing = find_job(database, "imageJobs", task_id)
        if existing is None:
            continue
        existing["status"] = ensure_string(payload.get("status")) or existing.get("status", "QUEUED")
        existing["lastError"] = ensure_string((payload.get("task_error") or {}).get("message"))
        existing["updatedAtEpoch"] = current_epoch()


def sync_store_assets(database: dict[str, Any]) -> None:
    results: list[dict[str, Any]] = []
    configured_root = ensure_string(os.getenv("UNDERFOOT_UNITY_ASSET_STORE_CACHE_PATH"))
    roots: list[Path] = []
    if configured_root:
        roots.append(Path(configured_root))
    roots.extend(path for path in DEFAULT_STORE_CACHE_ROOTS if str(path).strip())

    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for package_path in sorted(root.rglob("*.unitypackage")):
            resolved_path = str(package_path.resolve())
            if resolved_path in seen:
                continue
            seen.add(resolved_path)
            publisher = package_path.parent.name
            display_name = package_path.stem.replace("_", " ").replace("-", " ").strip()
            record_id = f"store_{unique_path_hash(resolved_path)}"
            results.append(
                create_store_record(
                    record_id=record_id,
                    display_name=display_name or package_path.stem,
                    publisher=publisher,
                    local_package_path=resolved_path,
                    file_size_bytes=package_path.stat().st_size,
                    last_modified_epoch=int(package_path.stat().st_mtime),
                    source_root=str(root),
                )
            )

    results.sort(key=lambda item: int(item.get("lastModifiedEpoch", 0)), reverse=True)
    database["storeAssets"] = results


def sync_owned_asset_hints(database: dict[str, Any]) -> None:
    path = ownership_cache_path()
    if not path.exists():
        database["ownedAssetHints"] = []
        return

    payload = parse_json_file(path)
    asset_version_ids = payload.get("assetVersionIds", {})
    results: list[dict[str, Any]] = []
    if isinstance(asset_version_ids, dict):
        for asset_version_id, metadata in asset_version_ids.items():
            is_asset_project = bool(metadata.get("isAssetProject")) if isinstance(metadata, dict) else False
            results.append(create_owned_asset_hint(str(asset_version_id), is_asset_project))
    results.sort(key=lambda item: int(item.get("assetVersionId", "0") or 0), reverse=True)
    database["ownedAssetHints"] = results


def sync_luminax_messages(database: dict[str, Any]) -> None:
    path = legacy_queue_file_path()
    if not path.exists():
        database["luminaxMessages"] = []
        return

    results: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        file_paths = payload.get("filePaths", payload.get("file_paths", []))
        created_epoch = int(payload.get("createdAtEpoch", payload.get("created_at_epoch", 0)) or 0)
        results.append(
            {
                "id": ensure_string(payload.get("id")) or f"luminax_{created_epoch}",
                "createdAtEpoch": created_epoch,
                "subject": ensure_string(payload.get("subject")),
                "body": ensure_string(payload.get("body")),
                "filePaths": stringify_list(file_paths if isinstance(file_paths, list) else []),
                "status": ensure_string(payload.get("status")) or "queued",
                "queueFilePath": to_project_relative(path),
            }
        )
    results.sort(key=lambda item: int(item.get("createdAtEpoch", 0) or 0), reverse=True)
    database["luminaxMessages"] = results


def sync_darius_runtime_state(database: dict[str, Any]) -> None:
    state_path = darius_state_path()
    if not state_path.exists():
        database["route"] = database.get("route", {}) if isinstance(database.get("route"), dict) else {}
        database["dariusMessages"] = database.get("dariusMessages", []) if isinstance(database.get("dariusMessages"), list) else []
        return

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return

    route = payload.get("route")
    if isinstance(route, dict):
        database["route"] = route
    project_pack = payload.get("projectPack")
    if isinstance(project_pack, dict):
        database["projectPack"] = project_pack
    thread = payload.get("thread")
    if isinstance(thread, dict):
        database["thread"] = thread
    messages = payload.get("messages")
    if isinstance(messages, list):
        database["dariusMessages"] = [row for row in messages if isinstance(row, dict)]


def sync_project_memory(database: dict[str, Any]) -> None:
    path = project_memory_path()
    if not path.exists():
        database["projectMemory"] = database.get("projectMemory", {}) if isinstance(database.get("projectMemory"), dict) else {}
        return

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        database["projectMemory"] = payload


def sync_project_pack(database: dict[str, Any]) -> None:
    pack = database.get("projectPack", {}) if isinstance(database.get("projectPack"), dict) else {}
    pack["projectKey"] = ensure_string(pack.get("projectKey")) or project_key()
    pack["projectTitle"] = ensure_string(pack.get("projectTitle")) or project_title()
    pack["projectCategory"] = ensure_string(pack.get("projectCategory")) or project_category()
    pack["projectMemoryPath"] = to_project_relative(project_memory_path())
    pack["repoRulesPath"] = to_project_relative(repo_rules_path())
    pack["serverRefsPath"] = to_project_relative(server_refs_path())
    pack["workflowPath"] = to_project_relative(workflow_path())
    pack["threadRegistryPath"] = to_project_relative(thread_registry_path())
    pack["assignmentsPath"] = to_project_relative(assignments_path())
    pack["relayUrl"] = relay_url()
    if repo_rules_path().exists():
        pack["repoRulesPreview"] = repo_rules_path().read_text(encoding="utf-8", errors="ignore")[:1600]
    if server_refs_path().exists():
        try:
            refs_payload = parse_json_file(server_refs_path())
            if isinstance(refs_payload, dict):
                pack["verificationCommands"] = stringify_list(
                    refs_payload.get("verification", {}).get("localTestCommands", [])
                    if isinstance(refs_payload.get("verification"), dict)
                    else []
                )
                pack["requiredContextFiles"] = stringify_list(refs_payload.get("requiredContextFiles", []))
                pack["restartServices"] = stringify_list(
                    refs_payload.get("deployPolicy", {}).get("restartServices", [])
                    if isinstance(refs_payload.get("deployPolicy"), dict)
                    else []
                )
        except Exception:
            pass
    if workflow_path().exists():
        try:
            workflow_payload = parse_json_file(workflow_path())
            if isinstance(workflow_payload, dict):
                pack["bootstrapStatus"] = ensure_string(workflow_payload.get("bootstrapStatus")) or ensure_string(pack.get("bootstrapStatus")) or "bootstrapped"
                pack["lastBootstrapEpoch"] = int(workflow_payload.get("lastBootstrapEpoch", pack.get("lastBootstrapEpoch", 0)) or 0)
                pack["workflowTaskCount"] = len(workflow_payload.get("tasks", [])) if isinstance(workflow_payload.get("tasks"), list) else 0
        except Exception:
            pass
    database["projectPack"] = pack


def sync_thread_registry(database: dict[str, Any]) -> None:
    if not thread_registry_path().exists():
        database["thread"] = database.get("thread", {}) if isinstance(database.get("thread"), dict) else {}
        return
    try:
        payload = parse_json_file(thread_registry_path())
    except Exception:
        return
    if isinstance(payload, dict):
        database["thread"] = payload


def sync_service_assignments(database: dict[str, Any]) -> None:
    path = assignments_path()
    if not path.exists():
        database["serviceAssignments"] = []
        database["assignmentSummary"] = {"count": 0, "openCount": 0, "reviewCount": 0, "blockedCount": 0}
        database["activeAssignment"] = {}
        return
    try:
        payload = parse_json_file(path)
    except Exception:
        return
    rows = [row for row in (payload.get("rows") or []) if isinstance(row, dict)]
    rows.sort(key=lambda item: (-int(item.get("priority", 0) or 0), int(item.get("created_at", 0) or 0)))
    database["serviceAssignments"] = rows[:40]
    active_rows = [row for row in rows if ensure_string(row.get("status")).lower() in {"queued", "claimed", "running", "review", "blocked"}]
    database["assignmentSummary"] = {
        "count": len(rows),
        "openCount": len(active_rows),
        "reviewCount": len([row for row in rows if ensure_string(row.get("status")).lower() == "review"]),
        "blockedCount": len([row for row in rows if ensure_string(row.get("status")).lower() == "blocked"]),
        "path": to_project_relative(path),
    }
    database["activeAssignment"] = active_rows[0] if active_rows else {}


def sync_database(database: dict[str, Any]) -> None:
    task_root = task_store_root()
    task_root.mkdir(parents=True, exist_ok=True)

    title_foot_manifest = task_root / "underfoot_title_foot_generation.json"
    if title_foot_manifest.exists():
        sync_title_foot_manifest(database, title_foot_manifest)

    character_manifest = task_root / "underfoot_character_generation_manifest.json"
    if character_manifest.exists():
        sync_character_manifest(database, character_manifest)

    sync_image_to_3d_snapshots(database, task_root)
    sync_text_to_image_snapshots(database, task_root)
    sync_store_assets(database)
    sync_owned_asset_hints(database)
    sync_darius_runtime_state(database)
    sync_project_memory(database)
    sync_project_pack(database)
    sync_thread_registry(database)
    sync_service_assignments(database)
    sync_luminax_messages(database)
    dedupe_jobs(database)
    hydrate_model_catalog_assets(database)


def poll_text_to_3d_preview(task_id: str, timeout_seconds: int = 900) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while True:
        payload = load_text_to_3d_json("GET", f"/text-to-3d/{task_id}")
        status = ensure_string(payload.get("status"))
        if status == "SUCCEEDED":
            return payload
        if status in {"FAILED", "CANCELED"}:
            message = ensure_string((payload.get("task_error") or {}).get("message")) or "Meshy preview task failed."
            raise SystemExit(message)
        if time.time() >= deadline:
            raise SystemExit(f"Timed out waiting for preview task {task_id}")
        time.sleep(10)


def create_text_to_3d_preview(prompt: str, negative_prompt: str) -> str:
    payload = {
        "mode": "preview",
        "ai_model": "latest",
        "art_style": "realistic",
        "topology": "quad",
        "target_polycount": 16000,
        "should_remesh": True,
        "symmetry_mode": "auto",
        "prompt": prompt,
        "negative_prompt": negative_prompt,
    }
    response = load_text_to_3d_json("POST", "/text-to-3d", payload)
    task_id = ensure_string(response.get("result"))
    if not task_id:
        raise SystemExit("Meshy did not return a preview task id.")
    write_task_snapshot("epoch_portal_text3d_preview_request", task_id, {"request": payload, "response": response})
    return task_id


def create_text_to_3d_refine(preview_task_id: str, texture_prompt: str) -> str:
    payload = {
        "mode": "refine",
        "ai_model": "latest",
        "preview_task_id": preview_task_id,
        "texture_prompt": texture_prompt,
        "enable_pbr": True,
    }
    response = load_text_to_3d_json("POST", "/text-to-3d", payload)
    task_id = ensure_string(response.get("result"))
    if not task_id:
        raise SystemExit("Meshy did not return a refine task id.")
    write_task_snapshot("epoch_portal_text3d_refine_request", task_id, {"request": payload, "response": response})
    return task_id


def create_model_job(database: dict[str, Any], payload: dict[str, Any], *, persist_record: bool = True) -> dict[str, Any]:
    display_name = sanitize_name(ensure_string(payload.get("displayName")), f"Epoch Model {current_epoch()}")
    asset_kind = ensure_string(payload.get("assetKind")) or "props"
    request_mode = ensure_string(payload.get("requestMode")) or ("image-to-3d" if ensure_string(payload.get("referenceImagePath")) else "text-to-3d")
    prompt = ensure_string(payload.get("prompt"))
    negative_prompt = ensure_string(payload.get("negativePrompt"))
    texture_prompt = ensure_string(payload.get("texturePrompt"))
    reference_image_path = ensure_string(payload.get("referenceImagePath"))
    expected_asset_name = sanitize_name(ensure_string(payload.get("expectedAssetName")), slugify(display_name))

    output_folder = os.getenv(
        {
            "warble": "UNDERFOOT_MESHY_WARBLE_IMPORT",
            "biggun": "UNDERFOOT_MESHY_BIGGUN_IMPORT",
            "props": "UNDERFOOT_MESHY_PROPS_IMPORT",
        }.get(asset_kind, "UNDERFOOT_MESHY_PROPS_IMPORT"),
        "Assets/Models/MeshyIncoming/Props",
    )

    preview_task_id = ""
    if request_mode == "image-to-3d":
        if not reference_image_path:
            raise SystemExit("Image-to-3d model requests require referenceImagePath.")
        response = request_json(
            "POST",
            "/image-to-3d",
            {
                "image_url": path_to_data_uri(Path(reference_image_path)),
                "name": display_name,
                "ai_model": "latest",
                "enable_pbr": True,
                "should_texture": True,
                "should_remesh": True,
                "should_validate": True,
                "negative_prompt": negative_prompt,
                "texture_prompt": texture_prompt,
            },
        )
        task_id = ensure_string(response.get("result"))
        if not task_id:
            raise SystemExit("Meshy did not return an image-to-3d task id.")
        write_task_snapshot("image_to_3d", task_id, {"task_type": "image-to-3d", "asset_kind": asset_kind, "request": payload, "response": response})
    else:
        if not prompt:
            raise SystemExit("Text-to-3d model requests require a prompt.")
        preview_task_id = create_text_to_3d_preview(prompt, negative_prompt)
        poll_text_to_3d_preview(preview_task_id)
        task_id = create_text_to_3d_refine(preview_task_id, texture_prompt)

    record = create_model_record(
        record_id=task_id,
        display_name=display_name,
        asset_kind=asset_kind,
        request_mode=request_mode,
        task_id=task_id,
        preview_task_id=preview_task_id,
        status="QUEUED",
        prompt=prompt,
        negative_prompt=negative_prompt,
        texture_prompt=texture_prompt,
        reference_image_path=reference_image_path,
        expected_asset_name=expected_asset_name,
        output_folder=output_folder,
    )
    if persist_record:
        upsert_model_job(database, record)
    return record


def refresh_model_job(database: dict[str, Any], job_id: str) -> dict[str, Any]:
    job = find_job(database, "modelJobs", job_id)
    if job is None:
        raise SystemExit(f"Model job not found: {job_id}")
    task_id = ensure_string(job.get("taskId"))
    if not task_id:
        raise SystemExit(f"Model job has no task id: {job_id}")

    if ensure_string(job.get("requestMode")) == "text-to-3d":
        payload = load_text_to_3d_json("GET", f"/text-to-3d/{task_id}")
        write_task_snapshot("epoch_portal_text3d_status", task_id, payload)
    else:
        payload = request_json("GET", f"/image-to-3d/{task_id}")
        write_task_snapshot("image", task_id, payload)

    status = ensure_string(payload.get("status")) or ensure_string(job.get("status")) or "QUEUED"
    job["status"] = status
    job["lastProgress"] = int(payload.get("progress", job.get("lastProgress", 0)) or 0)
    job["lastError"] = ensure_string((payload.get("task_error") or {}).get("message"))
    job["updatedAtEpoch"] = current_epoch()

    if status == "SUCCEEDED":
        model_urls = payload.get("model_urls") or {}
        if isinstance(model_urls, dict) and model_urls:
            output_root = resolve_import_folder(ensure_string(job.get("assetKind")) or "props")
            name_prefix = sanitize_name(ensure_string(job.get("expectedAssetName")), slugify(ensure_string(job.get("displayName")) or task_id))
            output_root.mkdir(parents=True, exist_ok=True)
            downloads: list[str] = []
            for format_key in ("fbx", "glb", "obj", "usd"):
                url = model_urls.get(format_key)
                if not isinstance(url, str) or not url:
                    continue
                file_path = output_root / f"{name_prefix}{infer_extension(url, f'.{format_key}')}"
                file_path.write_bytes(request_bytes(url))
                downloads.append(str(file_path))
            thumbnail_url = ensure_string(payload.get("thumbnail_url"))
            if thumbnail_url:
                preview_root = project_root() / "Media" / "MeshyPreviews" / "EpochPortal"
                preview_root.mkdir(parents=True, exist_ok=True)
                thumb_path = preview_root / f"{name_prefix}_thumbnail.png"
                thumb_path.write_bytes(request_bytes(thumbnail_url))
                job["thumbnailPath"] = str(thumb_path)
                downloads.append(str(thumb_path))
            job["downloadedFiles"] = downloads

    return job


def retry_model_job(database: dict[str, Any], job_id: str) -> dict[str, Any]:
    job = find_job(database, "modelJobs", job_id)
    if job is None:
        raise SystemExit(f"Model job not found: {job_id}")
    current_retries = int(job.get("retryCount", 0) or 0)
    if current_retries >= retry_limit():
        raise SystemExit(f"Retry limit reached for {job_id}.")

    payload = {
        "displayName": ensure_string(job.get("displayName")),
        "assetKind": ensure_string(job.get("assetKind")),
        "requestMode": ensure_string(job.get("requestMode")),
        "prompt": ensure_string(job.get("prompt")),
        "negativePrompt": ensure_string(job.get("negativePrompt")),
        "texturePrompt": ensure_string(job.get("texturePrompt")),
        "referenceImagePath": ensure_string(job.get("referenceImagePath")),
        "expectedAssetName": ensure_string(job.get("expectedAssetName")),
    }
    previous_task_id = ensure_string(job.get("taskId"))
    new_record = create_model_job(database, payload, persist_record=False)
    history_values = [ensure_string(job.get("historyTaskIdsCsv"))]
    if previous_task_id:
        history_values.append(previous_task_id)
    job.update(new_record)
    job["id"] = ensure_string(job.get("taskId"))
    job["retryCount"] = current_retries + 1
    job["historyTaskIdsCsv"] = ",".join(item for item in history_values if item)
    return job


def create_image_job(database: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    display_name = sanitize_name(ensure_string(payload.get("displayName")), f"Epoch Image {current_epoch()}")
    prompt = ensure_string(payload.get("prompt"))
    if not prompt:
        raise SystemExit("Image generation requires a prompt.")
    negative_prompt = ensure_string(payload.get("negativePrompt"))
    aspect_ratio = ensure_string(payload.get("aspectRatio")) or "16:9"
    response = request_json(
        "POST",
        "/text-to-image",
        {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "ai_model": "nano-banana-pro",
            "enable_refine": True,
            "aspect_ratio": aspect_ratio,
            "name": display_name,
        },
    )
    task_id = ensure_string(response.get("result"))
    if not task_id:
        raise SystemExit("Meshy did not return a text-to-image task id.")
    write_task_snapshot("text_to_image", task_id, {"task_type": "text-to-image", "request": payload, "response": response})

    record = create_image_record(
        record_id=task_id,
        display_name=display_name,
        prompt=prompt,
        negative_prompt=negative_prompt,
        aspect_ratio=aspect_ratio,
        task_id=task_id,
        status="QUEUED",
        output_folder=os.getenv("UNDERFOOT_MESHY_BACKDROP_IMPORT", "Assets/Textures/Generated"),
    )
    upsert_image_job(database, record)
    return record


def refresh_image_job(database: dict[str, Any], job_id: str) -> dict[str, Any]:
    job = find_job(database, "imageJobs", job_id)
    if job is None:
        raise SystemExit(f"Image job not found: {job_id}")
    task_id = ensure_string(job.get("taskId"))
    if not task_id:
        raise SystemExit(f"Image job has no task id: {job_id}")

    payload = request_json("GET", f"/text-to-image/{task_id}")
    write_task_snapshot("text-image", task_id, payload)
    status = ensure_string(payload.get("status")) or ensure_string(job.get("status")) or "QUEUED"
    job["status"] = status
    job["lastError"] = ensure_string((payload.get("task_error") or {}).get("message"))
    job["updatedAtEpoch"] = current_epoch()

    if status == "SUCCEEDED":
        output_root = resolve_import_folder("backdrop")
        output_root.mkdir(parents=True, exist_ok=True)
        image_urls = payload.get("image_urls") or payload.get("result_urls") or payload.get("outputs") or []
        if isinstance(image_urls, dict):
            image_urls = list(image_urls.values())
        if isinstance(image_urls, list):
            downloads: list[str] = []
            for index, url in enumerate(image_urls):
                if not isinstance(url, str) or not url:
                    continue
                suffix = "" if len(image_urls) == 1 else f"_{index + 1:02d}"
                file_path = output_root / f"{sanitize_name(ensure_string(job.get('displayName')), task_id)}{suffix}{infer_extension(url, '.png')}"
                file_path.write_bytes(request_bytes(url))
                downloads.append(str(file_path))
            job["downloadedFiles"] = downloads

    return job


def queue_luminax_message(database: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    subject = sanitize_name(ensure_string(payload.get("subject")), "Darius Legacy Request")
    body = ensure_string(payload.get("body"))
    file_paths = stringify_list(payload.get("filePaths", []))
    message = create_luminax_message_record(subject, body, file_paths)

    path = legacy_queue_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(message, ensure_ascii=False) + "\n")

    database["luminaxMessages"].insert(0, message)
    return message


def queue_darius_message(database: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    text = ensure_string(payload.get("message"))
    if not text:
        raise SystemExit("Darius queue requires a message.")
    attachments = stringify_list(payload.get("attachments", []))
    requested_action = ensure_string(payload.get("requestedAction")) or "implement"
    expect_verification = bool(payload.get("expectVerification", True))
    message = create_darius_message_record(text, attachments, requested_action, expect_verification)

    path = queue_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(message, ensure_ascii=False) + "\n")

    database["dariusMessages"] = [row for row in database.get("dariusMessages", []) if isinstance(row, dict)]
    database["dariusMessages"].insert(0, message)
    return message


def command_sync(_: argparse.Namespace) -> int:
    database = load_database()
    sync_database(database)
    path = save_database(database)
    print(
        json.dumps(
            {
                "status": "ok",
                "database": str(path),
                "modelJobs": len(database["modelJobs"]),
                "imageJobs": len(database["imageJobs"]),
                "storeAssets": len(database["storeAssets"]),
                "ownedAssetHints": len(database["ownedAssetHints"]),
                "dariusMessages": len(database["dariusMessages"]),
                "luminaxMessages": len(database["luminaxMessages"]),
                "serviceAssignments": len(database["serviceAssignments"]),
            },
            indent=2,
        )
    )
    return 0


def command_create_model(args: argparse.Namespace) -> int:
    database = load_database()
    sync_database(database)
    record = create_model_job(database, load_payload_file(args.payload_file))
    path = save_database(database)
    print(json.dumps({"status": "ok", "database": str(path), "jobId": record["id"], "taskId": record["taskId"]}, indent=2))
    return 0


def command_refresh_model(args: argparse.Namespace) -> int:
    database = load_database()
    record = refresh_model_job(database, args.job_id)
    path = save_database(database)
    print(json.dumps({"status": "ok", "database": str(path), "jobId": record["id"], "taskId": record["taskId"], "state": record["status"]}, indent=2))
    return 0


def command_retry_model(args: argparse.Namespace) -> int:
    database = load_database()
    record = retry_model_job(database, args.job_id)
    path = save_database(database)
    print(json.dumps({"status": "ok", "database": str(path), "jobId": record["id"], "taskId": record["taskId"], "retryCount": record["retryCount"]}, indent=2))
    return 0


def command_create_image(args: argparse.Namespace) -> int:
    database = load_database()
    sync_database(database)
    record = create_image_job(database, load_payload_file(args.payload_file))
    path = save_database(database)
    print(json.dumps({"status": "ok", "database": str(path), "jobId": record["id"], "taskId": record["taskId"]}, indent=2))
    return 0


def command_refresh_image(args: argparse.Namespace) -> int:
    database = load_database()
    record = refresh_image_job(database, args.job_id)
    path = save_database(database)
    print(json.dumps({"status": "ok", "database": str(path), "jobId": record["id"], "taskId": record["taskId"], "state": record["status"]}, indent=2))
    return 0


def command_scan_store(_: argparse.Namespace) -> int:
    database = load_database()
    sync_store_assets(database)
    sync_owned_asset_hints(database)
    path = save_database(database)
    print(
        json.dumps(
            {
                "status": "ok",
                "database": str(path),
                "storeAssets": len(database["storeAssets"]),
                "ownedAssetHints": len(database["ownedAssetHints"]),
            },
            indent=2,
        )
    )
    return 0


def command_queue_luminax(args: argparse.Namespace) -> int:
    database = load_database()
    message = queue_luminax_message(database, load_payload_file(args.payload_file))
    path = save_database(database)
    print(json.dumps({"status": "ok", "database": str(path), "messageId": message["id"], "queueFile": str(legacy_queue_file_path())}, indent=2))
    return 0


def command_queue_darius(args: argparse.Namespace) -> int:
    database = load_database()
    message = queue_darius_message(database, load_payload_file(args.payload_file))
    path = save_database(database)
    print(json.dumps({"status": "ok", "database": str(path), "messageId": message["id"], "queueFile": str(queue_file_path())}, indent=2))
    return 0


def _default_capabilities() -> list[str]:
    return [
        "unity_connector",
        "assignment_worker",
        "service_thread_executor",
        "meshy_asset_ops",
        "local_codex_delegate",
    ]


def _register_payload(args: argparse.Namespace) -> dict[str, Any]:
    local_port = int(env_value("DARIUS_LOCAL_AGENT_PORT", default="8462") or 8462)
    local_host = env_value("DARIUS_LOCAL_AGENT_HOST", default="127.0.0.1") or "127.0.0.1"
    return {
        "token": relay_token(),
        "project_key": ensure_string(getattr(args, "project_key", "")) or project_key(),
        "project_title": ensure_string(getattr(args, "project_title", "")) or project_title(),
        "project_category": ensure_string(getattr(args, "project_category", "")) or project_category(),
        "agent_id": ensure_string(getattr(args, "agent_id", "")) or local_agent_id(),
        "machine_label": local_machine_label(),
        "workspace_root": str(project_root()),
        "unity_project_path": str(unity_project_path()),
        "active_local_thread_id": ensure_string(getattr(args, "local_thread_id", "")) or local_thread_id(),
        "bootstrap_status": ensure_string(getattr(args, "bootstrap_status", "")) or "ready",
        "thread_registry_path": str(thread_registry_path()),
        "last_bootstrap_epoch": current_epoch(),
        "capabilities": _default_capabilities(),
        "detail": ensure_string(getattr(args, "detail", "")) or "Darius is online for Pro-only delegated execution.",
        "relay_node_key": ensure_string(getattr(args, "relay_node_key", "")) or local_agent_id(),
        "local_agent_port": local_port,
        "local_agent_url": ensure_string(getattr(args, "local_agent_url", "")) or f"http://{local_host}:{local_port}",
        "agent_start_script": ensure_string(getattr(args, "agent_start_script", "")),
        "route_status": ensure_string(getattr(args, "route_status", "")) or "ready",
    }


def _merge_assignment_payload(database: dict[str, Any], payload: dict[str, Any]) -> None:
    route = payload.get("route")
    if isinstance(route, dict):
        database["route"] = route
    rows = payload.get("serviceAssignments")
    if isinstance(rows, list):
        clean_rows = [row for row in rows if isinstance(row, dict)]
        database["serviceAssignments"] = clean_rows[:40]
        database["assignmentSummary"] = {
            "count": len(clean_rows),
            "openCount": len([row for row in clean_rows if ensure_string(row.get("status")).lower() in {"queued", "claimed", "running", "review", "blocked"}]),
            "reviewCount": len([row for row in clean_rows if ensure_string(row.get("status")).lower() == "review"]),
            "blockedCount": len([row for row in clean_rows if ensure_string(row.get("status")).lower() == "blocked"]),
            "source": "relay",
        }
        database["activeAssignment"] = next((row for row in clean_rows if ensure_string(row.get("status")).lower() in {"queued", "claimed", "running", "review", "blocked"}), {})
    service_thread = payload.get("serviceThread")
    if isinstance(service_thread, dict):
        current_thread = database.get("thread", {}) if isinstance(database.get("thread"), dict) else {}
        current_thread["activeServiceThread"] = service_thread
        database["thread"] = current_thread


def command_register_agent(args: argparse.Namespace) -> int:
    payload = relay_request_json("POST", "/api/darius/agents/register", _register_payload(args))
    print(json.dumps(payload, indent=2))
    return 0


def command_heartbeat(args: argparse.Namespace) -> int:
    payload = relay_request_json("POST", "/api/darius/agents/heartbeat", _register_payload(args))
    print(json.dumps(payload, indent=2))
    return 0


def command_list_assignments(args: argparse.Namespace) -> int:
    payload = relay_request_json(
        "GET",
        "/api/darius/assignments",
        query={
            "service_key": ensure_string(args.service_key),
            "status": ensure_string(args.status),
            "limit": int(args.limit),
            "include_completed": str(bool(args.include_completed)).lower(),
        },
    )
    print(json.dumps(payload, indent=2))
    return 0


def command_fetch_assignment(args: argparse.Namespace) -> int:
    payload = relay_request_json(
        "GET",
        f"/api/darius/agents/{urllib.parse.quote(ensure_string(args.agent_id) or local_agent_id())}/assignment",
        query={"project_key": ensure_string(args.project_key) or project_key(), "token": relay_token()},
    )
    database = load_database()
    sync_database(database)
    _merge_assignment_payload(database, payload if isinstance(payload, dict) else {})
    path = save_database(database)
    print(json.dumps({"status": "ok", "database": str(path), "payload": payload}, indent=2))
    return 0


def command_claim_assignment(args: argparse.Namespace) -> int:
    assignment_id = ensure_string(args.assignment_id)
    if not assignment_id:
        raise SystemExit("claim-assignment requires --assignment-id")
    payload = relay_request_json(
        "POST",
        f"/api/darius/assignment/{urllib.parse.quote(assignment_id)}/claim",
        {
            "token": relay_token(),
            "agent_id": ensure_string(args.agent_id) or local_agent_id(),
            "thread_id": ensure_string(args.thread_id),
            "local_thread_id": ensure_string(args.local_thread_id) or local_thread_id(),
            "project_key": ensure_string(args.project_key) or project_key(),
        },
    )
    print(json.dumps(payload, indent=2))
    return 0


def command_claim_next(args: argparse.Namespace) -> int:
    listing = relay_request_json(
        "GET",
        "/api/darius/assignments",
        query={"status": "queued", "include_completed": "false", "limit": int(args.limit)},
    )
    rows = [row for row in (listing.get("rows") or []) if isinstance(row, dict)]
    if ensure_string(args.service_key):
        rows = [row for row in rows if ensure_string(row.get("service_key")).lower() == ensure_string(args.service_key).lower()]
    if ensure_string(args.project_key):
        rows = [row for row in rows if ensure_string(row.get("project_key")).lower() == ensure_string(args.project_key).lower()]
    rows.sort(key=lambda row: (-int(row.get("priority", 0) or 0), int(row.get("created_at", 0) or 0)))
    if not rows:
        print(json.dumps({"status": "empty", "detail": "No queued Darius assignment matched the filters."}, indent=2))
        return 0
    args.assignment_id = ensure_string(rows[0].get("assignment_id"))
    return command_claim_assignment(args)


def command_complete_assignment(args: argparse.Namespace) -> int:
    assignment_id = ensure_string(args.assignment_id)
    if not assignment_id:
        raise SystemExit("complete-assignment requires --assignment-id")
    payload = relay_request_json(
        "POST",
        f"/api/darius/assignment/{urllib.parse.quote(assignment_id)}/complete",
        {
            "token": relay_token(),
            "agent_id": ensure_string(args.agent_id) or local_agent_id(),
            "status": ensure_string(args.status) or "review",
            "summary": ensure_string(args.summary),
            "verification_summary": ensure_string(args.verification_summary),
            "artifact_paths": [str(item).strip() for item in (args.artifact_path or []) if str(item).strip()],
            "review_status": ensure_string(args.review_status) or "pending",
            "message": ensure_string(args.message),
        },
    )
    print(json.dumps(payload, indent=2))
    return 0


def command_reject_assignment(args: argparse.Namespace) -> int:
    assignment_id = ensure_string(args.assignment_id)
    if not assignment_id:
        raise SystemExit("reject-assignment requires --assignment-id")
    payload = relay_request_json(
        "POST",
        f"/api/darius/assignment/{urllib.parse.quote(assignment_id)}/reject",
        {
            "token": relay_token(),
            "agent_id": ensure_string(args.agent_id) or local_agent_id(),
            "reason": ensure_string(args.reason),
            "review_status": ensure_string(args.review_status) or "rejected",
        },
    )
    print(json.dumps(payload, indent=2))
    return 0


def command_report_result(args: argparse.Namespace) -> int:
    meta: dict[str, Any] = {}
    if ensure_string(args.meta_file):
        meta = load_payload_file(ensure_string(args.meta_file))
    if ensure_string(args.assignment_id):
        meta["assignment_id"] = ensure_string(args.assignment_id)
    if ensure_string(args.dispatch_id):
        meta["dispatchId"] = ensure_string(args.dispatch_id)
    payload = relay_request_json(
        "POST",
        f"/api/darius/agents/{urllib.parse.quote(ensure_string(args.agent_id) or local_agent_id())}/result",
        {
            "token": relay_token(),
            "project_key": ensure_string(args.project_key) or project_key(),
            "agent_id": ensure_string(args.agent_id) or local_agent_id(),
            "session_id": ensure_string(args.session_id),
            "status": ensure_string(args.status) or "completed",
            "summary": ensure_string(args.summary),
            "verification_summary": ensure_string(args.verification_summary),
            "artifact_paths": [str(item).strip() for item in (args.artifact_path or []) if str(item).strip()],
            "message": ensure_string(args.message),
            "meta": meta,
            "source": "darius_agent_cli",
            "auto_create": True,
        },
    )
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codename Darius backend helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync = subparsers.add_parser("sync", help="Refresh the Darius database from Meshy task files, local store caches, and runtime route state.")
    sync.set_defaults(func=command_sync)

    create_model = subparsers.add_parser("create-model", help="Create a Meshy model request and store it in the Darius database.")
    create_model.add_argument("--payload-file", required=True)
    create_model.set_defaults(func=command_create_model)

    refresh_model = subparsers.add_parser("refresh-model", help="Refresh a model job from Meshy and download outputs when available.")
    refresh_model.add_argument("--job-id", required=True)
    refresh_model.set_defaults(func=command_refresh_model)

    retry_model = subparsers.add_parser("retry-model", help="Retry a model generation request while preserving Darius history.")
    retry_model.add_argument("--job-id", required=True)
    retry_model.set_defaults(func=command_retry_model)

    create_image = subparsers.add_parser("create-image", help="Create a Meshy text-to-image request and track it in the Darius database.")
    create_image.add_argument("--payload-file", required=True)
    create_image.set_defaults(func=command_create_image)

    refresh_image = subparsers.add_parser("refresh-image", help="Refresh an image job from Meshy and download outputs when available.")
    refresh_image.add_argument("--job-id", required=True)
    refresh_image.set_defaults(func=command_refresh_image)

    scan_store = subparsers.add_parser("scan-store", help="Scan the local Unity Asset Store cache directories for downloaded packages.")
    scan_store.set_defaults(func=command_scan_store)

    queue_luminax = subparsers.add_parser("queue-luminax", help="Queue a legacy Epoch-style request for compatibility.")
    queue_luminax.add_argument("--payload-file", required=True)
    queue_luminax.set_defaults(func=command_queue_luminax)

    queue_darius = subparsers.add_parser("queue-darius", help="Queue a local Darius chat/request payload.")
    queue_darius.add_argument("--payload-file", required=True)
    queue_darius.set_defaults(func=command_queue_darius)

    register_agent = subparsers.add_parser("register-agent", help="Register the local Darius worker with Mount Olympus.")
    register_agent.add_argument("--agent-id", default="")
    register_agent.add_argument("--project-key", default="")
    register_agent.add_argument("--project-title", default="")
    register_agent.add_argument("--project-category", default="")
    register_agent.add_argument("--local-thread-id", default="")
    register_agent.add_argument("--bootstrap-status", default="ready")
    register_agent.add_argument("--detail", default="")
    register_agent.add_argument("--relay-node-key", default="")
    register_agent.add_argument("--local-agent-url", default="")
    register_agent.add_argument("--agent-start-script", default="")
    register_agent.add_argument("--route-status", default="ready")
    register_agent.set_defaults(func=command_register_agent)

    heartbeat = subparsers.add_parser("heartbeat", help="Send a heartbeat for the local Darius worker.")
    heartbeat.add_argument("--agent-id", default="")
    heartbeat.add_argument("--project-key", default="")
    heartbeat.add_argument("--project-title", default="")
    heartbeat.add_argument("--project-category", default="")
    heartbeat.add_argument("--local-thread-id", default="")
    heartbeat.add_argument("--bootstrap-status", default="ready")
    heartbeat.add_argument("--detail", default="")
    heartbeat.add_argument("--relay-node-key", default="")
    heartbeat.add_argument("--local-agent-url", default="")
    heartbeat.add_argument("--agent-start-script", default="")
    heartbeat.add_argument("--route-status", default="ready")
    heartbeat.set_defaults(func=command_heartbeat)

    list_assignments = subparsers.add_parser("list-assignments", help="List Darius assignments from Mount Olympus.")
    list_assignments.add_argument("--service-key", default="")
    list_assignments.add_argument("--status", default="")
    list_assignments.add_argument("--limit", type=int, default=20)
    list_assignments.add_argument("--include-completed", action="store_true")
    list_assignments.set_defaults(func=command_list_assignments)

    fetch_assignment = subparsers.add_parser("fetch-assignment", help="Fetch the current Darius assignment payload and sync it into the local database.")
    fetch_assignment.add_argument("--agent-id", default="")
    fetch_assignment.add_argument("--project-key", default="")
    fetch_assignment.set_defaults(func=command_fetch_assignment)

    claim_assignment = subparsers.add_parser("claim-assignment", help="Claim a specific Darius assignment.")
    claim_assignment.add_argument("--assignment-id", required=True)
    claim_assignment.add_argument("--agent-id", default="")
    claim_assignment.add_argument("--thread-id", default="")
    claim_assignment.add_argument("--local-thread-id", default="")
    claim_assignment.add_argument("--project-key", default="")
    claim_assignment.set_defaults(func=command_claim_assignment)

    claim_next = subparsers.add_parser("claim-next", help="Claim the highest-priority queued Darius assignment.")
    claim_next.add_argument("--service-key", default="")
    claim_next.add_argument("--project-key", default="")
    claim_next.add_argument("--agent-id", default="")
    claim_next.add_argument("--thread-id", default="")
    claim_next.add_argument("--local-thread-id", default="")
    claim_next.add_argument("--limit", type=int, default=40)
    claim_next.set_defaults(func=command_claim_next)

    complete_assignment = subparsers.add_parser("complete-assignment", help="Complete or review a claimed Darius assignment.")
    complete_assignment.add_argument("--assignment-id", required=True)
    complete_assignment.add_argument("--agent-id", default="")
    complete_assignment.add_argument("--status", default="review")
    complete_assignment.add_argument("--summary", default="")
    complete_assignment.add_argument("--verification-summary", default="")
    complete_assignment.add_argument("--artifact-path", action="append", default=[])
    complete_assignment.add_argument("--review-status", default="pending")
    complete_assignment.add_argument("--message", default="")
    complete_assignment.set_defaults(func=command_complete_assignment)

    reject_assignment = subparsers.add_parser("reject-assignment", help="Reject a Darius assignment and release its checkout.")
    reject_assignment.add_argument("--assignment-id", required=True)
    reject_assignment.add_argument("--agent-id", default="")
    reject_assignment.add_argument("--reason", default="")
    reject_assignment.add_argument("--review-status", default="rejected")
    reject_assignment.set_defaults(func=command_reject_assignment)

    report_result = subparsers.add_parser("report-result", help="Publish a structured Darius result back to Mount Olympus.")
    report_result.add_argument("--agent-id", default="")
    report_result.add_argument("--project-key", default="")
    report_result.add_argument("--session-id", default="")
    report_result.add_argument("--assignment-id", default="")
    report_result.add_argument("--dispatch-id", default="")
    report_result.add_argument("--status", default="completed")
    report_result.add_argument("--summary", default="")
    report_result.add_argument("--verification-summary", default="")
    report_result.add_argument("--artifact-path", action="append", default=[])
    report_result.add_argument("--message", default="")
    report_result.add_argument("--meta-file", default="")
    report_result.set_defaults(func=command_report_result)

    return parser


def main() -> int:
    parser = build_parser()
    parser.add_argument("--apply", action="store_true", help="Explicitly allow this command's configured file/network/model actions")
    args = parser.parse_args()
    if not args.apply:
        print(json.dumps({"status": "plan", "action": getattr(args, "command", "selected command"),
                          "detail": "No configuration loaded or action performed. Put --apply before the subcommand to execute."}))
        return 0
    load_env()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
