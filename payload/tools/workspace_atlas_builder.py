import argparse
import json
import os
import time
from pathlib import Path
from typing import Any


VERSION = "2026-03-20"
COMMON_IGNORED_DIRS = {
    ".git",
    ".github",
    ".next",
    ".nuxt",
    ".venv",
    "__pycache__",
    "node_modules",
    "Library",
    "Temp",
    "Logs",
    "PackageCache",
    "packages",
    "dist",
    "build",
    "coverage",
    "tmp",
    "tmp_codex_host_probe",
    "_local_memory",
    "_local_runtime",
    "reports",
    "runtime",
    "memory",
    "models",
    "certs",
    "secrets",
    ".codex",
    ".ssh",
    ".aws",
    "generated_media",
    "upgrades",
}
IMPORTANT_FILE_SUFFIXES = {
    ".css",
    ".example",
    ".html",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".sql",
    ".svg",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
IMPORTANT_FILE_NAMES = {
    ".env.example",
    ".env.template",
    "docker-compose.yml",
    "docker-compose.yaml",
    "dockerfile",
    "nameplate.txt",
    "details.txt",
    "package.json",
    "package-lock.json",
    "portal.json",
    "project.json",
    "readme.md",
    "server.js",
}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.resolve().is_relative_to(_resolve_roots()["payload_root"].resolve()):
        raise ValueError("Atlas configuration must stay inside this payload.")
    try:
        if path.exists() and path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        pass
    return {}


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _normalize_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return "".join(ch if ch.isalnum() else "-" for ch in text).strip("-")


def _resolve_roots() -> dict[str, Path]:
    payload_root = Path(__file__).resolve().parents[1]
    luminax_root = payload_root.parent
    # Source discovery is bounded to this payload, never the machine's project parent.
    workspace_root = payload_root
    return {
        "payload_root": payload_root,
        "luminax_root": luminax_root,
        "workspace_root": workspace_root,
        "config_root": payload_root / "config",
        "output_root": payload_root / "reports" / "atlas",
    }


def _load_profiles(profile_root: Path, kind: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = profile_root / kind
    if not root.exists():
        return rows
    for path in sorted(root.glob("*.json")):
        payload = _read_json(path)
        if not payload:
            continue
        row = {**payload, "profile_path": str(path)}
        row["key"] = str(row.get("key") or path.stem).strip() or path.stem
        rows.append(row)
    return rows


def _normalize_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    return text.rstrip("/")


def _profile_match_score(project_row: dict[str, Any], profile: dict[str, Any]) -> int:
    score = 0
    project_key = _normalize_key(project_row.get("project_key"))
    profile_key = _normalize_key(profile.get("key"))
    aliases = {_normalize_key(alias) for alias in profile.get("aliases", []) if str(alias or "").strip()}
    project_root = _normalize_path(project_row.get("root_path"))
    profile_root = _normalize_path(profile.get("root_path"))
    domains = {_normalize_path(item) for item in project_row.get("domains", []) if str(item or "").strip()}
    profile_domains = {_normalize_path(item) for item in profile.get("domains", []) if str(item or "").strip()}
    public_url = _normalize_path(profile.get("public_url"))

    if project_key and project_key == profile_key:
        score += 80
    if project_key and project_key in aliases:
        score += 60
    if project_root and profile_root and project_root == profile_root:
        score += 120
    if domains and profile_domains and domains.intersection(profile_domains):
        score += 40
    if domains and public_url and public_url in domains:
        score += 40
    if project_key and profile_key.startswith(project_key):
        score += 10
    return score


def _resolve_project_profile(project_row: dict[str, Any], project_profiles: list[dict[str, Any]]) -> dict[str, Any]:
    best: dict[str, Any] = {}
    best_score = -1
    for profile in project_profiles:
        score = _profile_match_score(project_row, profile)
        if score > best_score:
            best = profile
            best_score = score
    return best if best_score > 0 else {}


def _resolve_project_root(path_text: str, workspace_root: Path, project_row: dict[str, Any]) -> Path:
    root = workspace_root.resolve()
    raw = str(path_text or "").strip().replace("\\", "/")
    if raw == "/workspace/luminax":
        raw = "."
    elif raw.startswith("/workspace/luminax/"):
        raw = raw[len("/workspace/luminax/"):]
    candidate = (root / (raw or ".")).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Atlas project roots must stay inside this payload.")
    return candidate


def _infer_file_purpose(rel_path: str) -> str:
    lower = rel_path.replace("\\", "/").lower()
    name = Path(rel_path).name.lower()
    if lower == ".codex/thread.json":
        return "Project Codex thread manifest and implementation source of truth."
    if lower == ".codex/instructions.md":
        return "Project-specific Codex execution instructions and worker mandate."
    if name == "nameplate.txt":
        return "Quick status card for the project."
    if name == "details.txt":
        return "Running work log and change journal."
    if name == "readme.md":
        return "Project overview and operator entrypoint."
    if name == "portal.json":
        return "Project metadata consumed by Olympus surfaces."
    if name == "project.json":
        return "Internal task/bootstrap record."
    if name == "package.json":
        return "Node dependency and script manifest."
    if name in {"docker-compose.yml", "docker-compose.yaml"}:
        return "Container orchestration definition."
    if name == "dockerfile":
        return "Container build instructions."
    if name.endswith(".env.example") or name == ".env.template":
        return "Environment variable template without live secrets."
    if name == "server.js":
        return "Primary Node server entrypoint."
    if name == "main.py":
        return "Primary Python entrypoint for the service or tool."
    if lower.endswith("index.html"):
        return "Main web shell or landing page."
    if lower.endswith("app.js"):
        return "Primary client-side behavior and rendering logic."
    if lower.endswith("styles.css"):
        return "Primary visual system and layout rules."
    if "config/" in lower or lower.endswith(".json"):
        return "Structured configuration or machine-readable project data."
    if lower.endswith(".md"):
        return "Human-readable documentation or implementation notes."
    if lower.endswith(".ps1") or lower.endswith(".sh"):
        return "Automation or deployment script."
    return "Important repo-tracked implementation file."


def _infer_file_type(rel_path: str) -> str:
    name = Path(rel_path).name.lower()
    suffix = Path(rel_path).suffix.lower()
    if name in {"docker-compose.yml", "docker-compose.yaml"}:
        return "compose"
    if name == "dockerfile":
        return "container-build"
    if name.endswith(".env.example") or name == ".env.template" or name == ".env":
        return "env-template"
    if suffix == ".py":
        return "python"
    if suffix in {".js", ".ts", ".tsx"}:
        return "application-code"
    if suffix in {".html", ".css"}:
        return "frontend"
    if suffix in {".json", ".yaml", ".yml"}:
        return "config"
    if suffix in {".md", ".txt"}:
        return "documentation"
    if suffix in {".ps1", ".sh"}:
        return "automation"
    return "file"


def _infer_deploy_relevance(rel_path: str) -> str:
    lower = rel_path.replace("\\", "/").lower()
    name = Path(lower).name
    if "docker" in lower or "nginx" in lower or name in {"server.js", "main.py"}:
        return "high"
    if any(fragment in lower for fragment in {"config/", "public/", "web/", "luminax-ui/"}):
        return "medium"
    return "reference"


def _infer_sensitivity(rel_path: str) -> str:
    lower = rel_path.replace("\\", "/").lower()
    if lower.endswith(".env") or "secret" in lower or "/cert" in lower or lower.endswith(".key"):
        return "high"
    if "config/" in lower or lower.endswith(".json"):
        return "medium"
    return "low"


def _excluded_dir_names(project_row: dict[str, Any]) -> set[str]:
    out = set(COMMON_IGNORED_DIRS)
    for value in project_row.get("excluded_paths", []):
        if not str(value or "").strip():
            continue
        parts = Path(str(value).replace("\\", "/")).parts
        if len(parts) == 1:
            out.add(parts[0])
    return out


def _excluded_path_specs(project_row: dict[str, Any]) -> list[str]:
    rows = []
    for value in project_row.get("excluded_paths", []):
        clean = str(value or "").strip().replace("\\", "/").strip("/")
        if clean:
            rows.append(clean)
    return rows


def _is_excluded_rel_path(rel_path: str, ignored_dirs: set[str], excluded_specs: list[str]) -> bool:
    normalized = rel_path.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if any(part in ignored_dirs for part in parts[:-1]):
        return True
    for spec in excluded_specs:
        if normalized == spec or normalized.startswith(spec + "/"):
            return True
    return False


def _collect_file_index(project_root: Path, project_row: dict[str, Any], profile: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    root = project_root.resolve()
    ignored = {name.lower() for name in _excluded_dir_names(project_row)}
    excluded_specs = _excluded_path_specs(project_row)
    entries: list[dict[str, Any]] = []
    summarized_dirs: list[str] = []
    if not root.is_dir():
        return entries, summarized_dirs
    for current, dirs, files in os.walk(root, followlinks=False):
        parent = Path(current)
        kept = []
        for name in sorted(dirs):
            path = parent / name
            rel = path.relative_to(root).as_posix()
            if (name.lower() in ignored or path.is_symlink()
                    or not path.resolve().is_relative_to(root)
                    or _is_excluded_rel_path(rel, ignored, excluded_specs)
                    or len(path.relative_to(root).parts) >= 4):
                summarized_dirs.append(rel)
            else:
                kept.append(name)
        dirs[:] = kept
        for name in sorted(files):
            path = parent / name
            rel = path.relative_to(root).as_posix()
            lower = name.lower()
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                continue
            if lower.startswith('.env') and lower not in {'.env.example', '.env.template'}:
                continue
            if _is_excluded_rel_path(rel, ignored, excluded_specs):
                continue
            if lower not in IMPORTANT_FILE_NAMES and path.suffix.lower() not in IMPORTANT_FILE_SUFFIXES:
                continue
            if len(path.relative_to(root).parts) > 4:
                continue
            entries.append({"path": rel, "purpose": _infer_file_purpose(rel),
                            "type": _infer_file_type(rel), "deploy_relevance": _infer_deploy_relevance(rel),
                            "sensitivity": _infer_sensitivity(rel)})
            if len(entries) >= 36:
                return entries, summarized_dirs
    return entries, summarized_dirs


def _candidate_project_dirs(workspace_root: Path) -> list[Path]:
    rows: list[Path] = []
    for child in sorted(workspace_root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir() or child.is_symlink() or not child.resolve().is_relative_to(workspace_root.resolve()) or child.name.lower() in {name.lower() for name in COMMON_IGNORED_DIRS}:
            continue
        if child.name.startswith("Feedback "):
            continue
        if child.name in {"_deploy", "tools", "SCPFILES"}:
            continue
        if (child / "nameplate.txt").exists() or (child / "details.txt").exists() or (child / "portal.json").exists():
            rows.append(child)
    return rows


def _project_snapshot(
    workspace_root: Path,
    project_row: dict[str, Any],
    profile: dict[str, Any],
    connections_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    project_root = _resolve_project_root(str(project_row.get("root_path") or ""), workspace_root, project_row)
    file_index_entries, summarized_dirs = _collect_file_index(project_root, project_row, profile)
    connection_refs = []
    for key in project_row.get("connections_ref", []):
        if str(key or "").strip() and key in connections_map:
            connection_refs.append(connections_map[key])
    deploy_target = project_row.get("deploy_target", {}) if isinstance(project_row.get("deploy_target"), dict) else {}
    owner_lane = str(profile.get("owner") or project_row.get("role_owner") or "zeus").strip() or "zeus"
    verification_profile = {
        "local_test_commands": project_row.get("local_test_commands", []) if isinstance(project_row.get("local_test_commands"), list) else [],
        "remote_test_commands": project_row.get("remote_test_commands", []) if isinstance(project_row.get("remote_test_commands"), list) else [],
        "health_urls": project_row.get("health_urls", []) if isinstance(project_row.get("health_urls"), list) else [],
        "smoke_prompts": profile.get("smoke_prompts", []) if isinstance(profile.get("smoke_prompts"), list) else [],
        "rollback_notes": profile.get("rollback_notes", []) if isinstance(profile.get("rollback_notes"), list) else [],
    }
    open_first = []
    codex_thread = project_row.get("codex_thread") if isinstance(project_row.get("codex_thread"), dict) else {}
    if not codex_thread and isinstance(profile.get("codex_thread"), dict):
        codex_thread = profile.get("codex_thread")
    review_state_summary = str(project_row.get("review_state_summary") or project_row.get("codex_review_state_summary") or profile.get("review_state_summary") or "").strip()
    active_thread_status = str(project_row.get("active_thread_status") or project_row.get("codex_thread_status") or profile.get("active_thread_status") or "").strip()
    waking_hours_policy = (
        project_row.get("waking_hours_notification_policy")
        if isinstance(project_row.get("waking_hours_notification_policy"), dict)
        else (profile.get("waking_hours_notification_policy") if isinstance(profile.get("waking_hours_notification_policy"), dict) else {})
    )
    life_ops_policy = (
        project_row.get("life_ops_notification_policy")
        if isinstance(project_row.get("life_ops_notification_policy"), dict)
        else (profile.get("life_ops_notification_policy") if isinstance(profile.get("life_ops_notification_policy"), dict) else {})
    )
    for candidate in (profile.get("required_context_files") or []) + [".codex/thread.json", ".codex/instructions.md", "README.md", "nameplate.txt", "details.txt", "portal.json"]:
        if not str(candidate or "").strip():
            continue
        path = project_root / str(candidate)
        if path.exists() and path.is_file():
            rel = path.relative_to(project_root).as_posix()
            if rel not in open_first:
                open_first.append(rel)
    return {
        "project_key": str(project_row.get("project_key") or "").strip(),
        "display_name": str(project_row.get("display_name") or project_row.get("project_key") or "").strip(),
        "purpose": str(project_row.get("purpose") or profile.get("summary") or "").strip(),
        "audience": str(project_row.get("audience") or "").strip(),
        "status_summary": str(project_row.get("status_summary") or "").strip(),
        "owner_lane": owner_lane,
        "stack": str(project_row.get("stack") or "").strip(),
        "runtime_shape": str(project_row.get("runtime_shape") or "").strip(),
        "root_path": str(project_root),
        "domains": [str(item).strip() for item in project_row.get("domains", []) if str(item or "").strip()],
        "mutable_paths": [str(item).strip() for item in project_row.get("mutable_paths", []) if str(item or "").strip()],
        "excluded_paths": [str(item).strip() for item in project_row.get("excluded_paths", []) if str(item or "").strip()],
        "deploy_target": deploy_target,
        "sync_targets": [str(item).strip() for item in project_row.get("sync_targets", []) if str(item or "").strip()],
        "verification_profile": verification_profile,
        "connections_ref_keys": [str(item).strip() for item in project_row.get("connections_ref", []) if str(item or "").strip()],
        "connections": connection_refs,
        "profile_key": str(profile.get("key") or "").strip(),
        "profile_path": str(profile.get("profile_path") or "").strip(),
        "profile_summary": str(profile.get("summary") or "").strip(),
        "capability_bundles": [str(item).strip() for item in profile.get("capability_bundles", []) if str(item or "").strip()],
        "deploy_policy": profile.get("deploy_policy", {}) if isinstance(profile.get("deploy_policy"), dict) else {},
        "live_change_policy": profile.get("live_change_policy", {}) if isinstance(profile.get("live_change_policy"), dict) else {},
        "arrangement": profile.get("arrangement", {}) if isinstance(profile.get("arrangement"), dict) else {},
        "required_context_files": [str(item).strip() for item in profile.get("required_context_files", []) if str(item or "").strip()],
        "related_assets": [str(item).strip() for item in profile.get("related_assets", []) if str(item or "").strip()],
        "discord_branding": profile.get("discord_branding", {}) if isinstance(profile.get("discord_branding"), dict) else {},
        "codex_thread_required": bool(project_row.get("codex_thread_required") if project_row.get("codex_thread_required") is not None else profile.get("codex_thread_required")),
        "active_thread_status": active_thread_status,
        "review_state_summary": review_state_summary,
        "preferred_escalation_model": str(project_row.get("preferred_escalation_model") or profile.get("preferred_escalation_model") or "").strip(),
        "codex_thread": codex_thread if isinstance(codex_thread, dict) else {},
        "waking_hours_notification_policy": waking_hours_policy,
        "life_ops_notification_policy": life_ops_policy,
        "atlas_paths": {
            "project_atlas": str(_resolve_roots()["output_root"] / (_normalize_key(project_row.get("project_key")) or "project") / "PROJECT_ATLAS.md"),
            "file_index": str(_resolve_roots()["output_root"] / (_normalize_key(project_row.get("project_key")) or "project") / "FILE_INDEX.md"),
        },
        "open_first_files": open_first[:10],
        "file_index_entries": file_index_entries,
        "summarized_dirs": summarized_dirs,
        "project_root_exists": project_root.exists(),
    }


def _build_snapshot(project_key: str = "") -> dict[str, Any]:
    roots = _resolve_roots()
    config_root = roots["config_root"]
    workspace_root = roots["workspace_root"]
    registry = _read_json(config_root / "workspace-registry.json")
    identity = _read_json(config_root / "olympus-identity.json")
    connections = _read_json(config_root / "workspace-connections.json")
    review_cycle = _read_json(config_root / "review-cycle-policy.json")
    project_profiles = _load_profiles(config_root / "olympus", "projects")
    connections_map: dict[str, dict[str, Any]] = {}
    for row in connections.get("nodes", []):
        if not isinstance(row, dict) or not str(row.get("key") or "").strip():
            continue
        connections_map[str(row.get("key") or "").strip()] = {**row, "kind": row.get("kind") or "node"}
    for row in connections.get("connections", []):
        if not isinstance(row, dict) or not str(row.get("key") or "").strip():
            continue
        connections_map[str(row.get("key") or "").strip()] = row

    projects: list[dict[str, Any]] = []
    registry_projects = registry.get("projects", []) if isinstance(registry.get("projects"), list) else []
    wanted_key = _normalize_key(project_key)
    for row in registry_projects:
        if not isinstance(row, dict):
            continue
        if wanted_key and _normalize_key(row.get("project_key")) != wanted_key:
            continue
        profile = _resolve_project_profile(row, project_profiles)
        projects.append(_project_snapshot(workspace_root, row, profile, connections_map))

    registered_roots = {_normalize_path(project.get("root_path")) for project in projects if str(project.get("root_path") or "").strip()}
    unregistered = [str(path) for path in _candidate_project_dirs(workspace_root) if _normalize_path(path) not in registered_roots]

    audit_rows = []
    for project in projects:
        issues = []
        if not project.get("project_root_exists"):
            issues.append("Project root is missing.")
        if not project.get("purpose"):
            issues.append("Purpose is missing.")
        if not project.get("audience"):
            issues.append("Audience is missing.")
        if not project.get("profile_key"):
            issues.append("Olympus project profile is missing.")
        codex_thread = project.get("codex_thread") if isinstance(project.get("codex_thread"), dict) else {}
        if not codex_thread and project.get("codex_thread_required"):
            issues.append("Project Codex thread metadata is missing.")
        elif codex_thread:
            thread_path = str(codex_thread.get("thread_path") or "").strip()
            instructions_path = str(codex_thread.get("instructions_path") or "").strip()
            if not thread_path or not Path(thread_path).exists():
                issues.append("Project Codex thread manifest is missing on disk.")
            if not instructions_path or not Path(instructions_path).exists():
                issues.append("Project Codex thread instructions are missing on disk.")
        if str(project.get("owner_lane") or "").strip() == "zeus" and "life-" in str(project.get("project_key") or "") and not project.get("waking_hours_notification_policy"):
            issues.append("Life Ops waking-hours notification policy is missing.")
        if not project.get("verification_profile", {}).get("local_test_commands"):
            issues.append("Local verification commands are missing.")
        if project.get("deploy_target", {}).get("verify_after_apply") and not project.get("verification_profile", {}).get("remote_test_commands") and not project.get("verification_profile", {}).get("health_urls"):
            issues.append("Remote verification is required but remote checks are missing.")
        for ref_key in project.get("connections_ref_keys", []):
            if ref_key not in connections_map:
                issues.append(f"Missing connection ref: {ref_key}")
        audit_rows.append(
            {
                "project_key": project.get("project_key"),
                "display_name": project.get("display_name"),
                "status": "ok" if not issues else "warn",
                "issues": issues,
            }
        )

    if unregistered:
        audit_rows.append(
            {
                "project_key": "__workspace__",
                "display_name": "Workspace Drift",
                "status": "warn",
                "issues": ["Unregistered top-level project roots: " + ", ".join(unregistered)],
            }
        )

    ok_count = sum(1 for row in audit_rows if row.get("status") == "ok")
    warn_count = sum(1 for row in audit_rows if row.get("status") == "warn")
    return {
        "version": VERSION,
        "generated_at": int(time.time()),
        "workspace_name": str(registry.get("workspace_name") or "Local Workspace").strip() or "Local Workspace",
        "workspace_root": str(workspace_root),
        "authoring_node": next((row for row in connections.get("nodes", []) if isinstance(row, dict) and str(row.get("key") or "").strip() == "authoring_node"), {}),
        "server_brain": next((row for row in connections.get("nodes", []) if isinstance(row, dict) and str(row.get("key") or "").strip() == "server_brain"), {}),
        "edge_node": next((row for row in connections.get("nodes", []) if isinstance(row, dict) and str(row.get("key") or "").strip() == "edge_node"), {}),
        "nodes": connections.get("nodes", []) if isinstance(connections.get("nodes"), list) else [],
        "delivery_defaults": connections.get("delivery_defaults", {}) if isinstance(connections.get("delivery_defaults"), dict) else {},
        "identity": {
            "public_brand": str(identity.get("public_brand") or "").strip(),
            "control_surface_name": str(identity.get("control_surface_name") or "").strip(),
            "operator_name": str(identity.get("operator_name") or "").strip(),
            "tagline": str(identity.get("tagline") or "").strip(),
        },
        "review_cycle": {
            "campaign_hours": int(review_cycle.get("campaign_hours") or 8),
            "wave_count": int(review_cycle.get("wave_count") or 0),
            "stop_phrase": str(review_cycle.get("stop_phrase") or "stop").strip() or "stop",
        },
        "connections": connections.get("connections", []) if isinstance(connections.get("connections"), list) else [],
        "projects": projects,
        "counts": {
            "projects": len(projects),
            "connection_refs": len(connections.get("connections", []) if isinstance(connections.get("connections"), list) else []),
        },
        "audit": {
            "ok_count": ok_count,
            "warn_count": warn_count,
            "rows": audit_rows,
            "unregistered_roots": unregistered,
        },
        "paths": {
            "workspace_atlas_md": str(roots["output_root"] / "WORKSPACE_ATLAS.md"),
            "workspace_atlas_json": str(roots["output_root"] / "WORKSPACE_ATLAS.json"),
            "payload_atlas_json": str(roots["output_root"] / "WORKSPACE_ATLAS.json"),
            "connections_path": str(config_root / "workspace-connections.json"),
        },
    }


def _markdown_list(values: list[str], fallback: str = "- None listed.") -> str:
    rows = [f"- `{value}`" for value in values if str(value or "").strip()]
    return "\n".join(rows) if rows else fallback


def _project_table(snapshot: dict[str, Any]) -> str:
    lines = [
        "| Project | Purpose | Owner | Stack | Deploy target | Domains |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in snapshot.get("projects", []):
        domains = ", ".join(row.get("domains", [])[:2]) or "-"
        deploy = row.get("deploy_target", {})
        deploy_kind = str(deploy.get("kind") or "manual").strip() or "manual"
        lines.append(
            "| {name} | {purpose} | {owner} | {stack} | {deploy} | {domains} |".format(
                name=str(row.get("display_name") or row.get("project_key") or "").replace("|", "/"),
                purpose=str(row.get("purpose") or "").replace("|", "/"),
                owner=str(row.get("owner_lane") or "").replace("|", "/"),
                stack=str(row.get("stack") or "").replace("|", "/"),
                deploy=deploy_kind.replace("|", "/"),
                domains=domains.replace("|", "/"),
            )
        )
    return "\n".join(lines)


def _connection_table(snapshot: dict[str, Any]) -> str:
    lines = [
        "| Connection Ref | Kind | Owner | Host | Notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    node_lookup = {}
    for node in snapshot.get("nodes", []):
        if isinstance(node, dict):
            key = str(node.get("key") or "").strip()
            if key:
                node_lookup[key] = node
    for row in snapshot.get("connections", []):
        if not isinstance(row, dict):
            continue
        host = node_lookup.get(str(row.get("host_ref") or "").strip(), {})
        host_label = str(host.get("display_name") or host.get("network_address") or row.get("host_ref") or "").strip()
        note = str(row.get("public_url") or row.get("summary") or row.get("service_name") or row.get("runtime_path") or "").strip()
        lines.append(
            "| {key} | {kind} | {owner} | {host} | {note} |".format(
                key=str(row.get("key") or "").replace("|", "/"),
                kind=str(row.get("kind") or "").replace("|", "/"),
                owner=str(row.get("owner") or "").replace("|", "/"),
                host=host_label.replace("|", "/"),
                note=note.replace("|", "/"),
            )
        )
    return "\n".join(lines)


def _render_workspace_atlas_md(snapshot: dict[str, Any]) -> str:
    identity = snapshot.get("identity", {})
    authoring = snapshot.get("authoring_node", {})
    server_brain = snapshot.get("server_brain", {})
    edge_node = snapshot.get("edge_node", {})
    audit = snapshot.get("audit", {})
    delivery = snapshot.get("delivery_defaults", {})
    return "\n".join(
        [
            "# WORKSPACE_ATLAS",
            "",
        "> Auto-generated from the Mount Olympus core registry files under `Luminax/payload/config/`, Olympus project profiles, and workspace connection refs.",
            "",
            "## What This Workspace Is",
            "",
            f"`{snapshot.get('workspace_name')}` is the shared authoring workspace behind `{identity.get('control_surface_name')}` and `{identity.get('public_brand')}`.",
            f"The operator-facing system is coordinated by `{identity.get('operator_name')}` and deployed through the Ubuntu server brain after local validation succeeds.",
            "",
            "## Delivery Rule",
            "",
            "1. Author and test code on the local authoring node.",
            "2. Push or apply the tested change to the Ubuntu server brain.",
            "3. Run remote verification and internal probes.",
            "4. Mark the change `ok` only after the live server confirms it.",
            "",
            "Delivery defaults:",
            _markdown_list([f"{key}={value}" for key, value in delivery.items()]),
            "",
            "## Runtime Nodes",
            "",
            f"- Local authoring node: `{authoring.get('display_name')}` at `{authoring.get('network_address')}`.",
            f"- Server brain: `{server_brain.get('display_name')}` at `{server_brain.get('network_address')}`.",
            f"- Edge worker node: `{edge_node.get('display_name')}` at `{edge_node.get('network_address')}`.",
            "",
            "## Projects",
            "",
            _project_table(snapshot),
            "",
            "## Connection References",
            "",
            _connection_table(snapshot),
            "",
            "## How To Add A New Project",
            "",
            "1. Create the project through the Olympus project bootstrap flow so registry, profile, atlas, and metadata files are created together.",
            "2. Add a workspace registry entry with purpose, audience, deploy target, and verification commands.",
            "3. Add or update the Olympus project profile with role ownership, arrangement, live-change policy, and connection refs.",
            "4. Rebuild the atlas and confirm the project no longer appears as workspace drift.",
            "",
            "## Audit",
            "",
            f"- Registered projects: `{snapshot.get('counts', {}).get('projects', 0)}`",
            f"- Audit ok rows: `{audit.get('ok_count', 0)}`",
            f"- Audit warning rows: `{audit.get('warn_count', 0)}`",
            _markdown_list(audit.get("unregistered_roots", []), fallback="- No unregistered top-level project roots detected."),
            "",
        ]
    ) + "\n"


def _render_project_atlas_md(project: dict[str, Any], snapshot: dict[str, Any]) -> str:
    deploy_target = project.get("deploy_target", {}) if isinstance(project.get("deploy_target"), dict) else {}
    verification = project.get("verification_profile", {}) if isinstance(project.get("verification_profile"), dict) else {}
    arrangement = project.get("arrangement", {}) if isinstance(project.get("arrangement"), dict) else {}
    live_change_policy = project.get("live_change_policy", {}) if isinstance(project.get("live_change_policy"), dict) else {}
    return "\n".join(
        [
            f"# PROJECT_ATLAS: {project.get('display_name')}",
            "",
            "> Auto-generated workspace atlas entry for fast operator and agent onboarding.",
            "",
            "## Summary",
            "",
            f"- Project key: `{project.get('project_key')}`",
            f"- Purpose: {project.get('purpose') or 'Not documented yet.'}",
            f"- Audience: {project.get('audience') or 'Not documented yet.'}",
            f"- Owner lane: `{project.get('owner_lane') or 'zeus'}`",
            f"- Stack: `{project.get('stack') or 'unknown'}`",
            f"- Runtime shape: `{project.get('runtime_shape') or 'unknown'}`",
            f"- Status summary: {project.get('status_summary') or 'Not documented yet.'}",
            "",
            "## Domains And Routes",
            "",
            _markdown_list(project.get("domains", []), fallback="- No public domains documented."),
            "",
            "## Deploy Target",
            "",
            f"- Kind: `{deploy_target.get('kind') or 'manual'}`",
            f"- Host ref: `{deploy_target.get('host_ref') or 'unspecified'}`",
            f"- Workdir/apply path: `{deploy_target.get('workdir') or deploy_target.get('apply_path') or 'unspecified'}`",
            f"- Verify after apply: `{bool(deploy_target.get('verify_after_apply', False))}`",
            "",
            "## Verification",
            "",
            "Local test commands:",
            _markdown_list(verification.get("local_test_commands", [])),
            "",
            "Remote test commands:",
            _markdown_list(verification.get("remote_test_commands", [])),
            "",
            "Health URLs:",
            _markdown_list(verification.get("health_urls", [])),
            "",
            "## Connections",
            "",
            _markdown_list(project.get("connections_ref_keys", []), fallback="- No connection refs documented."),
            "",
            "## Open Here First",
            "",
            _markdown_list(project.get("open_first_files", [])),
            "",
            "## Arrangement",
            "",
            _markdown_list([f"{key}: {value}" for key, value in arrangement.items() if value not in ({}, [], None, "")], fallback="- No explicit arrangement block documented."),
            "",
            "## Live Change Policy",
            "",
            _markdown_list([f"{key}: {value}" for key, value in live_change_policy.items() if value not in ({}, [], None, "")], fallback="- No explicit live-change policy documented."),
            "",
            "## Atlas Files",
            "",
            f"- Project atlas: `{project.get('atlas_paths', {}).get('project_atlas', '')}`",
            f"- File index: `{project.get('atlas_paths', {}).get('file_index', '')}`",
            f"- Workspace atlas: `{snapshot.get('paths', {}).get('workspace_atlas_md', '')}`",
            "",
        ]
    ) + "\n"


def _render_file_index_md(project: dict[str, Any]) -> str:
    lines = [
        f"# FILE_INDEX: {project.get('display_name')}",
        "",
        "> Auto-generated list of the most important repo-tracked files to open first.",
        "",
        "| Path | Purpose | Type | Deploy relevance | Sensitivity | Owner lane |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in project.get("file_index_entries", []):
        lines.append(
            "| {path} | {purpose} | {kind} | {deploy} | {sensitivity} | {owner} |".format(
                path=str(entry.get("path") or "").replace("|", "/"),
                purpose=str(entry.get("purpose") or "").replace("|", "/"),
                kind=str(entry.get("type") or "").replace("|", "/"),
                deploy=str(entry.get("deploy_relevance") or "").replace("|", "/"),
                sensitivity=str(entry.get("sensitivity") or "").replace("|", "/"),
                owner=str(project.get("owner_lane") or "zeus").replace("|", "/"),
            )
        )
    lines.extend(
        [
            "",
            "## Summarized Directories",
            "",
            _markdown_list(project.get("summarized_dirs", []), fallback="- No summarized directories."),
            "",
            "## Notes",
            "",
            "- This index is intentionally shallow. Generated/vendor/cache trees are summarized instead of exploded file-by-file.",
            "- Secret values must stay out of tracked files; connection refs point to env keys and host paths instead.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def build_and_write(project_key: str = "") -> dict[str, Any]:
    snapshot = _build_snapshot(project_key=project_key)
    roots = _resolve_roots()
    output = roots["output_root"].resolve()
    if not output.is_relative_to(roots["payload_root"].resolve()):
        raise ValueError("Atlas output must stay inside this payload.")
    _write_text(output / "WORKSPACE_ATLAS.md", _render_workspace_atlas_md(snapshot))
    _write_json(output / "WORKSPACE_ATLAS.json", snapshot)
    for project in snapshot.get("projects", []):
        for key, renderer in (("project_atlas", lambda: _render_project_atlas_md(project, snapshot)),
                              ("file_index", lambda: _render_file_index_md(project))):
            target = Path(project["atlas_paths"][key]).resolve()
            if not target.is_relative_to(output):
                raise ValueError("Atlas project output escaped its reports directory.")
            _write_text(target, renderer())
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Local Workspace workspace atlas.")
    parser.add_argument("--project-key", default="", help="Only rebuild one project atlas entry.")
    parser.add_argument("--apply", action="store_true", help="Write reports/atlas; the default only inspects source.")
    args = parser.parse_args()
    snapshot = build_and_write(project_key=args.project_key) if args.apply else _build_snapshot(project_key=args.project_key)
    print(
        json.dumps(
            {
                "status": "written" if args.apply else "plan",
                "projects": snapshot.get("counts", {}).get("projects", 0),
                "warn_count": snapshot.get("audit", {}).get("warn_count", 0),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
