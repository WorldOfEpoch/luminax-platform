import json
import os
import hmac
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Hermes Ops Relay")

IDENTITY_PATH = Path(os.getenv("OLYMPUS_IDENTITY_PATH", "/workspace/luminax/config/olympus-identity.json"))
POLICY_PATH = Path(os.getenv("REVIEW_CYCLE_POLICY_PATH", "/workspace/luminax/config/review-cycle-policy.json"))
BLUEPRINTS_PATH = Path(os.getenv("DISCORD_OPS_BLUEPRINTS_PATH", "/workspace/luminax/config/discord-ops-blueprints.json"))
CORE_URL = (os.getenv("CORE_URL", "") or "").strip().rstrip("/")
CONTROL_TOKEN = (os.getenv("CONTROL_TOKEN", "") or "").strip()


class BlueprintPlanRequest(BaseModel):
    guild_name: str = ""
    project_key: str = ""


class RevampInjectionRequest(BaseModel):
    token: str = ""
    prompt: str = Field(min_length=3)
    discord_user_id: str = ""
    guild_id: str = ""
    channel_id: str = ""
    project_key: str = "mount-olympus-web"
    blueprint_key: str = "revamp_staging"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _identity_role() -> dict[str, Any]:
    identity = _read_json(IDENTITY_PATH)
    roles = identity.get("roles") if isinstance(identity.get("roles"), dict) else {}
    return roles.get("hermes_ops") if isinstance(roles.get("hermes_ops"), dict) else {}


def _default_blueprints() -> dict[str, Any]:
    return {
        "version": "fallback",
        "templates": [
            {
                "key": "mount_olympus_control_guild",
                "title": "Mount Olympus Control Guild",
                "project_key": "mount-olympus-core",
                "guild_name": "Olympus",
                "roles": ["Olympus Owner", "Zeus", "Hermes", "Hermes Ops", "Oracle of Siwa", "Oracle of Delphi", "Operator"],
                "categories": [{"name": "Zeus Deck", "channels": ["zeus-briefing", "hermes-dispatch", "oracle-chamber", "ops-audit"]}],
                "sticky_messages": [],
            },
            {
                "key": "revamp_staging",
                "title": "Revamp Staging Guild",
                "project_key": "mount-olympus-web",
                "guild_name": "Revamp // Olympus Staging",
                "roles": ["Olympus Owner", "Zeus", "Hermes", "Hermes Ops", "Website Admin", "Builder"],
                "categories": [{"name": "Launch Surface", "channels": ["welcome-briefing", "olympus-web-changes", "visual-forge"]}],
                "sticky_messages": [],
            },
        ],
    }


def load_blueprints() -> dict[str, Any]:
    payload = _default_blueprints()
    disk = _read_json(BLUEPRINTS_PATH)
    if isinstance(disk.get("templates"), list):
        payload["templates"] = disk["templates"]
    if disk != payload:
        BLUEPRINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        BLUEPRINTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _blueprint_templates() -> list[dict[str, Any]]:
    payload = load_blueprints()
    templates = payload.get("templates")
    return templates if isinstance(templates, list) else []


def blueprint_by_key(blueprint_key: str = "", guild_name: str = "", project_key: str = "") -> dict[str, Any]:
    normalized_key = str(blueprint_key or "").strip().lower()
    normalized_guild = str(guild_name or "").strip().lower()
    normalized_project = str(project_key or "").strip().lower()
    for row in _blueprint_templates():
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip().lower()
        aliases = [str(alias or "").strip().lower() for alias in row.get("aliases", [])] if isinstance(row.get("aliases"), list) else []
        if normalized_key and (key == normalized_key or normalized_key in aliases):
            return row
    for row in _blueprint_templates():
        if not isinstance(row, dict):
            continue
        if normalized_guild and normalized_guild in {str(row.get("title") or "").strip().lower(), str(row.get("guild_name") or "").strip().lower()}:
            return row
    for row in _blueprint_templates():
        if not isinstance(row, dict):
            continue
        if normalized_project and str(row.get("project_key") or "").strip().lower() == normalized_project:
            return row
    return {}


def plan_blueprint(blueprint: dict[str, Any]) -> dict[str, Any]:
    categories = blueprint.get("categories") if isinstance(blueprint.get("categories"), list) else []
    sticky_messages = blueprint.get("sticky_messages") if isinstance(blueprint.get("sticky_messages"), list) else []
    roles = blueprint.get("roles") if isinstance(blueprint.get("roles"), list) else []
    planned_channels: list[dict[str, Any]] = []
    for category in categories:
        if not isinstance(category, dict):
            continue
        category_name = str(category.get("name") or "Category").strip()
        for item in category.get("channels", []) if isinstance(category.get("channels"), list) else []:
            if isinstance(item, dict):
                planned_channels.append(
                    {
                        "category": category_name,
                        "name": str(item.get("name") or "").strip(),
                        "type": str(item.get("type") or "text").strip().lower() or "text",
                        "topic": str(item.get("topic") or "").strip(),
                    }
                )
            else:
                name = str(item or "").strip()
                if name:
                    planned_channels.append({"category": category_name, "name": name, "type": "voice" if name.lower().startswith("voice:") else "text", "topic": ""})
    return {
        "blueprint_key": str(blueprint.get("key") or "").strip(),
        "title": str(blueprint.get("title") or blueprint.get("key") or "Blueprint").strip(),
        "project_key": str(blueprint.get("project_key") or "").strip(),
        "guild_name": str(blueprint.get("guild_name") or "").strip(),
        "description": str(blueprint.get("description") or "").strip(),
        "icon_asset": str(blueprint.get("icon_asset") or "").strip(),
        "banner_asset": str(blueprint.get("banner_asset") or "").strip(),
        "onboarding_copy": str(blueprint.get("onboarding_copy") or "").strip(),
        "roles": [
            str(item.get("name") if isinstance(item, dict) else item).strip()
            for item in roles
            if str(item.get("name") if isinstance(item, dict) else item).strip()
        ],
        "categories": [str(category.get("name") or "").strip() for category in categories if isinstance(category, dict) and str(category.get("name") or "").strip()],
        "channels": planned_channels,
        "sticky_messages": [
            {
                "channel": str(item.get("channel") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "body": str(item.get("body") or "").strip(),
            }
            for item in sticky_messages
            if isinstance(item, dict)
        ],
        "counts": {
            "roles": len(roles),
            "categories": len(categories),
            "channels": len(planned_channels),
            "sticky_messages": len(sticky_messages),
        },
    }


async def _post_core_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not CORE_URL:
        raise HTTPException(status_code=503, detail="CORE_URL is not configured")
    async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=5.0)) as client:
        response = await client.post(f"{CORE_URL}{path}", json=payload)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {"status": "ok", "data": data}


async def _review_discord_action(req: RevampInjectionRequest, action_kind: str) -> dict[str, Any]:
    token = str(req.token or "").strip()
    if len(CONTROL_TOKEN) < 32 or not hmac.compare_digest(token.encode("utf-8"), CONTROL_TOKEN.encode("utf-8")):
        raise HTTPException(status_code=403, detail="A configured connector token is required")
    if not token:
        return {"status": "skipped", "approved": False, "detail": "CONTROL_TOKEN is not configured for review."}
    if not str(req.discord_user_id or "").strip():
        return {"status": "skipped", "approved": False, "detail": "discord_user_id is required for Oracle review."}
    return await _post_core_json(
        "/discord/action/review",
        {
            "token": token,
            "goal": req.prompt,
            "discord_user_id": str(req.discord_user_id or "").strip(),
            "guild_id": str(req.guild_id or "").strip(),
            "channel_id": str(req.channel_id or "").strip(),
            "project_key": str(req.project_key or "").strip(),
            "action_kind": action_kind,
            "allow_write": action_kind in {"implement", "branding_sync"},
            "meta": {"source": "discord_ops_bot_test", "blueprint_key": req.blueprint_key},
        },
    )


@app.get("/health")
async def health():
    role = _identity_role()
    return {
        "status": "ok",
        "service": "discord-ops-bot",
        "display_name": str(role.get("display_name") or "Hermes Ops"),
        "summary": str(role.get("summary") or "Hermes governance and Discord operations lane ready."),
        "ts": int(time.time()),
    }


@app.get("/manifest")
async def manifest():
    role = _identity_role()
    return {
        "status": "ok",
        "service": "discord-ops-bot",
        "display_name": str(role.get("display_name") or "Hermes Ops"),
        "mission": str(role.get("summary") or "Discord setup, roles, provisioning, and admin workflows under Hermes."),
        "review_cycle_policy_path": str(POLICY_PATH),
        "identity_path": str(IDENTITY_PATH),
        "blueprints_path": str(BLUEPRINTS_PATH),
        "supported_actions": [
            "discord.server.bootstrap",
            "discord.roles.sync",
            "discord.permissions.audit",
            "discord.channel.template.apply",
            "discord.branding.sync",
            "discord.tests.revamp_injection",
        ],
    }


@app.get("/blueprints")
async def blueprints():
    payload = load_blueprints()
    return {"status": "ok", "templates": payload.get("templates", [])}


@app.get("/blueprints/{blueprint_key}")
async def blueprint_detail(blueprint_key: str):
    blueprint = blueprint_by_key(blueprint_key=blueprint_key)
    if not blueprint:
        raise HTTPException(status_code=404, detail="Blueprint not found")
    return {"status": "ok", "blueprint": blueprint, "plan": plan_blueprint(blueprint)}


@app.post("/blueprints/{blueprint_key}/plan")
async def blueprint_plan(blueprint_key: str, req: BlueprintPlanRequest):
    blueprint = blueprint_by_key(blueprint_key=blueprint_key, guild_name=req.guild_name, project_key=req.project_key)
    if not blueprint:
        raise HTTPException(status_code=404, detail="Blueprint not found")
    return {"status": "ok", "plan": plan_blueprint(blueprint)}


@app.post("/tests/revamp-injection")
async def revamp_injection(req: RevampInjectionRequest):
    blueprint = blueprint_by_key(blueprint_key=req.blueprint_key, project_key=req.project_key)
    if not blueprint:
        raise HTTPException(status_code=404, detail="Revamp blueprint not found")
    plan = plan_blueprint(blueprint)
    implement_review = await _review_discord_action(req, "implement")
    branding_review = await _review_discord_action(req, "branding_sync")
    return {
        "status": "ok",
        "blueprint": {
            "key": plan.get("blueprint_key"),
            "title": plan.get("title"),
            "project_key": plan.get("project_key"),
            "guild_name": plan.get("guild_name"),
        },
        "prompt": req.prompt,
        "channel_changes": plan.get("channels", []),
        "sticky_messages": plan.get("sticky_messages", []),
        "branding_assets": {
            "icon_asset": plan.get("icon_asset"),
            "banner_asset": plan.get("banner_asset"),
        },
        "reviews": {
            "branding_sync": branding_review,
            "implementation": implement_review,
        },
        "success_ready": bool(branding_review.get("approved")) and bool(implement_review.get("approved")),
    }
