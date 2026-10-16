from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import contextvars
import base64
import hashlib
import json
import os
import time

import httpx
import mysql.connector
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

app = FastAPI(title="Luminax Account Worker")
web_auth_user_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("web_auth_user", default="")

@app.middleware("http")
async def capture_web_auth_user(request: Request, call_next):
    username = (request.headers.get("X-Web-Auth-User", "") or "").strip().lower()
    token = web_auth_user_ctx.set(username)
    try:
        return await call_next(request)
    finally:
        web_auth_user_ctx.reset(token)

MYSQL_HOST = os.getenv("MYSQL_HOST", "mysql")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "luminax")
MYSQL_USER = os.getenv("MYSQL_USER", "")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
CONTROL_TOKEN = os.getenv("CONTROL_TOKEN", "")
TRUSTED_WEB_CONTROL_USER = os.getenv("TRUSTED_WEB_CONTROL_USER", "").strip().lower()
TESTER_ACCOUNT_VAULT_KEY = (os.getenv("TESTER_ACCOUNT_VAULT_KEY", "") or os.getenv("CONTROL_TOKEN", "") or "").strip()

TESTER_STATUSES = {"active", "testing", "paused", "disabled", "archived", "deleted"}
REQUEST_STATUSES = {"pending", "connected", "failed", "rejected"}
BRAND_ACCOUNT_STATUSES = {"planned", "pending_signup", "pending_oauth", "connected", "monitoring", "paused", "disabled", "archived", "deleted"}
BRAND_MONITOR_STATUSES = {"active", "paused", "disabled", "error"}
BRAND_ONBOARDING_MODES = {"manual_signup", "workspace_user", "workspace_alias", "oauth_connect", "discord_bot_app", "manual_app_connect"}
BRAND_AUTH_TYPES = {"oauth_token", "bearer_token", "bot_token", "app_password", "custom"}


class AccountRequest(BaseModel):
    provider: str
    purpose: str
    scope: str = "project"
    project_key: str | None = None


class AccountStatusUpdate(BaseModel):
    request_id: int
    status: str
    note: str = ""


class TesterAccountUpsert(BaseModel):
    token: str = ""
    account_key: str
    provider: str = "discord"
    account_type: str = "tester"
    display_name: str = ""
    project_key: str | None = None
    status: str = "active"
    tags: list[str] = []
    scopes: list[str] = []
    persona: dict = {}
    note: str = ""


class TesterAccountTokenSet(BaseModel):
    control_token: str = ""
    account_key: str
    token: str
    refresh_token: str = ""
    provider: str = "discord"
    account_type: str = "tester"
    expires_unix: int | None = None
    scopes: list[str] = []
    note: str = ""


class TesterAccountStatusSet(BaseModel):
    token: str = ""
    account_key: str
    status: str
    note: str = ""


class TesterAccountDelete(BaseModel):
    token: str = ""
    account_key: str
    purge_tokens: bool = True
    note: str = ""


class BrandAccountBootstrapRequest(BaseModel):
    token: str = ""
    business_key: str
    business_name: str = ""
    project_key: str | None = None
    public_domain: str = ""
    luminax_email: str = "luminax@gmail.com"
    support_email: str = ""
    promotions_email: str = ""
    create_records: bool = True


class BrandAccountUpsert(BaseModel):
    token: str = ""
    account_key: str
    provider: str
    account_kind: str = "social"
    owner_key: str = ""
    display_name: str = ""
    handle_or_address: str = ""
    project_key: str | None = None
    status: str = "planned"
    onboarding_mode: str = "manual_signup"
    tags: list[str] = []
    scopes: list[str] = []
    meta: dict = {}
    note: str = ""


class BrandAccountSecretSet(BaseModel):
    control_token: str = ""
    account_key: str
    secret: str
    refresh_token: str = ""
    auth_type: str = "oauth_token"
    expires_unix: int | None = None
    scopes: list[str] = []
    note: str = ""


class BrandAccountStatusSet(BaseModel):
    token: str = ""
    account_key: str
    status: str
    note: str = ""


class BrandMonitorUpsert(BaseModel):
    token: str = ""
    monitor_key: str
    account_key: str
    provider: str = ""
    monitor_mode: str = "poll"
    status: str = "active"
    interval_minutes: int = 120
    source_query: str = ""
    channel_ref: str = ""
    meta: dict = {}
    note: str = ""


class BrandMonitorRunRequest(BaseModel):
    token: str = ""
    monitor_key: str = ""
    account_key: str = ""
    due_only: bool = False
    limit: int = 20


def db_conn():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
    )


def normalize_scope(scope: str | None) -> str:
    return "luminax" if (scope or "").strip().lower() == "luminax" else "project"


def normalize_project_key(project_key: str | None) -> str | None:
    raw = (project_key or "").strip().lower()
    if not raw:
        return None
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_").strip("-_")
    return safe[:128] if safe else None


def normalize_provider(provider: str | None) -> str:
    raw = (provider or "").strip().lower()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_").strip("-_")
    return safe[:64] or "unknown"


def normalize_account_type(account_type: str | None) -> str:
    raw = (account_type or "").strip().lower()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_").strip("-_")
    return safe[:32] or "tester"


def normalize_account_key(account_key: str | None) -> str:
    raw = (account_key or "").strip().lower()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_").strip("-_")
    if not safe:
        raise HTTPException(status_code=400, detail="account_key is required")
    return safe[:128]


def normalize_status(status: str | None, allowed: set[str], default: str) -> str:
    val = (status or "").strip().lower()
    if not val:
        return default
    if val not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid status (allowed: {sorted(allowed)})")
    return val


def normalize_account_kind(account_kind: str | None) -> str:
    raw = (account_kind or "").strip().lower()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_").strip("-_")
    return safe[:32] or "social"


def normalize_owner_key(owner_key: str | None) -> str:
    raw = (owner_key or "").strip().lower()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_").strip("-_")
    return safe[:128] or "business"


def normalize_onboarding_mode(mode: str | None) -> str:
    value = (mode or "").strip().lower()
    if not value:
        return "manual_signup"
    if value not in BRAND_ONBOARDING_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid onboarding_mode (allowed: {sorted(BRAND_ONBOARDING_MODES)})")
    return value


def normalize_auth_type(auth_type: str | None) -> str:
    value = (auth_type or "").strip().lower()
    if not value:
        return "oauth_token"
    if value not in BRAND_AUTH_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid auth_type (allowed: {sorted(BRAND_AUTH_TYPES)})")
    return value


def normalize_monitor_key(monitor_key: str | None) -> str:
    raw = (monitor_key or "").strip().lower()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_").strip("-_")
    if not safe:
        raise HTTPException(status_code=400, detail="monitor_key is required")
    return safe[:128]


def normalize_interval_minutes(value: int | None) -> int:
    try:
        parsed = int(value or 120)
    except Exception:
        parsed = 120
    return max(15, min(parsed, 24 * 60))


def normalize_list(values: list[str] | None, max_items: int = 64, max_len: int = 80) -> list[str]:
    out: list[str] = []
    for item in values or []:
        raw = str(item or "").strip()
        if not raw:
            continue
        clean = "".join(ch for ch in raw if ch.isalnum() or ch in "-_ .:/").strip()
        if not clean:
            continue
        out.append(clean[:max_len])
    uniq: list[str] = []
    seen = set()
    for item in out:
        key = item.lower()
        if key in seen:
            continue
        uniq.append(item)
        seen.add(key)
    return uniq[:max_items]


def parse_json_value(value, default):
    if isinstance(value, dict) or isinstance(value, list):
        return value
    if not value:
        return default


def maybe_pretty_label(key: str | None) -> str:
    raw = str(key or "").strip()
    if not raw:
        return ""
    return " ".join(part.capitalize() for part in raw.replace("_", "-").split("-") if part)
    try:
        return json.loads(value)
    except Exception:
        return default


def current_web_auth_user() -> str:
    return (web_auth_user_ctx.get("") or "").strip().lower()

def request_has_trusted_web_control() -> bool:
    return feature_enabled("TRUST_PROXY_AUTH_HEADERS") and bool(TRUSTED_WEB_CONTROL_USER) and current_web_auth_user() == TRUSTED_WEB_CONTROL_USER

def require_control_token(token: str | None):
    if request_has_trusted_web_control():
        return
    if not token_matches((token or "").strip(), CONTROL_TOKEN):
        raise HTTPException(status_code=403, detail="Control token required")

def ensure_scope_columns():
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='accounts_requested' AND COLUMN_NAME IN ('integration_scope','project_key')",
            (MYSQL_DATABASE,),
        )
        cols = {row[0] for row in cur.fetchall()}
        if "integration_scope" not in cols:
            cur.execute("ALTER TABLE accounts_requested ADD COLUMN integration_scope VARCHAR(32) NOT NULL DEFAULT 'project' AFTER status")
        if "project_key" not in cols:
            cur.execute("ALTER TABLE accounts_requested ADD COLUMN project_key VARCHAR(128) NULL AFTER integration_scope")
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def ensure_tester_accounts_table():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS managed_tester_accounts (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          account_key VARCHAR(128) NOT NULL,
          provider VARCHAR(64) NOT NULL DEFAULT 'discord',
          account_type VARCHAR(32) NOT NULL DEFAULT 'tester',
          display_name VARCHAR(255) NULL,
          project_key VARCHAR(128) NULL,
          status VARCHAR(32) NOT NULL DEFAULT 'active',
          token_ciphertext LONGTEXT NULL,
          refresh_ciphertext LONGTEXT NULL,
          token_hint VARCHAR(64) NULL,
          token_updated_at TIMESTAMP NULL DEFAULT NULL,
          expires_at TIMESTAMP NULL DEFAULT NULL,
          last_active_at TIMESTAMP NULL DEFAULT NULL,
          tags_json JSON NULL,
          scopes_json JSON NULL,
          persona_json JSON NULL,
          meta_json JSON NULL,
          UNIQUE KEY uq_managed_tester_account_key (account_key),
          KEY idx_managed_tester_provider_status (provider, status),
          KEY idx_managed_tester_project (project_key)
        )
        """
    )
    conn.commit()
    cur.close()
    conn.close()


def ensure_brand_account_tables():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS managed_brand_accounts (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          account_key VARCHAR(128) NOT NULL,
          provider VARCHAR(64) NOT NULL,
          account_kind VARCHAR(32) NOT NULL DEFAULT 'social',
          owner_key VARCHAR(128) NOT NULL DEFAULT 'business',
          display_name VARCHAR(255) NULL,
          handle_or_address VARCHAR(255) NULL,
          project_key VARCHAR(128) NULL,
          status VARCHAR(32) NOT NULL DEFAULT 'planned',
          onboarding_mode VARCHAR(32) NOT NULL DEFAULT 'manual_signup',
          auth_type VARCHAR(32) NULL DEFAULT NULL,
          token_ciphertext LONGTEXT NULL,
          refresh_ciphertext LONGTEXT NULL,
          secret_hint VARCHAR(64) NULL,
          token_updated_at TIMESTAMP NULL DEFAULT NULL,
          expires_at TIMESTAMP NULL DEFAULT NULL,
          last_checked_at TIMESTAMP NULL DEFAULT NULL,
          last_error_at TIMESTAMP NULL DEFAULT NULL,
          tags_json JSON NULL,
          scopes_json JSON NULL,
          meta_json JSON NULL,
          UNIQUE KEY uq_managed_brand_account_key (account_key),
          KEY idx_managed_brand_provider_status (provider, status),
          KEY idx_managed_brand_project (project_key),
          KEY idx_managed_brand_owner (owner_key)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS managed_brand_monitors (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          monitor_key VARCHAR(128) NOT NULL,
          account_key VARCHAR(128) NOT NULL,
          provider VARCHAR(64) NOT NULL,
          monitor_mode VARCHAR(64) NOT NULL DEFAULT 'poll',
          status VARCHAR(32) NOT NULL DEFAULT 'active',
          interval_minutes INT NOT NULL DEFAULT 120,
          source_query VARCHAR(512) NULL,
          channel_ref VARCHAR(255) NULL,
          last_run_at TIMESTAMP NULL DEFAULT NULL,
          next_run_at TIMESTAMP NULL DEFAULT NULL,
          last_status VARCHAR(32) NULL,
          last_result_json JSON NULL,
          meta_json JSON NULL,
          UNIQUE KEY uq_managed_brand_monitor_key (monitor_key),
          KEY idx_managed_brand_monitor_account (account_key),
          KEY idx_managed_brand_monitor_next (status, next_run_at)
        )
        """
    )
    conn.commit()
    cur.close()
    conn.close()


def brand_account_row_payload(row: dict) -> dict:
    row["tags"] = normalize_list(parse_json_value(row.pop("tags_json", []), []), max_items=120, max_len=80)
    row["scopes"] = normalize_list(parse_json_value(row.pop("scopes_json", []), []), max_items=200, max_len=120)
    row["meta"] = parse_json_value(row.pop("meta_json", {}), {})
    row["has_secret"] = bool(row.get("has_secret"))
    return row


def brand_monitor_row_payload(row: dict) -> dict:
    row["meta"] = parse_json_value(row.pop("meta_json", {}), {})
    row["last_result"] = parse_json_value(row.pop("last_result_json", {}), {})
    return row


def _gmail_previews(messages: list[dict], previews: list[dict]) -> list[dict]:
    out: list[dict] = []
    for msg, meta in zip(messages[:5], previews[:5]):
        headers = {str(item.get("name", "")).lower(): item.get("value", "") for item in (meta.get("payload", {}) or {}).get("headers", []) if isinstance(item, dict)}
        out.append(
            {
                "id": msg.get("id"),
                "thread_id": msg.get("threadId"),
                "subject": headers.get("subject", ""),
                "from": headers.get("from", ""),
                "date": headers.get("date", ""),
            }
        )
    return out


async def run_brand_monitor(provider: str, account_row: dict, monitor_row: dict) -> dict:
    secret = decrypt_secret(account_row.get("token_ciphertext"))
    if not secret:
        return {
            "status": "manual_required",
            "provider": provider,
            "detail": "Connect the provider token or app secret first.",
            "account_key": account_row.get("account_key"),
        }
    provider = normalize_provider(provider)
    async with httpx.AsyncClient(timeout=25.0) as client:
        if provider == "gmail":
            params = {"maxResults": 10}
            query = (monitor_row.get("source_query") or "").strip()
            if query:
                params["q"] = query
            resp = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers={"Authorization": f"Bearer {secret}"},
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            messages = data.get("messages") or []
            previews: list[dict] = []
            for msg in messages[:5]:
                msg_id = str(msg.get("id", "")).strip()
                if not msg_id:
                    continue
                detail = await client.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                    headers={"Authorization": f"Bearer {secret}"},
                    params=[
                        ("format", "metadata"),
                        ("metadataHeaders", "Subject"),
                        ("metadataHeaders", "From"),
                        ("metadataHeaders", "Date"),
                    ],
                )
                detail.raise_for_status()
                previews.append(detail.json())
            return {
                "status": "ok",
                "provider": provider,
                "account_key": account_row.get("account_key"),
                "query": query,
                "result_size_estimate": int(data.get("resultSizeEstimate") or 0),
                "message_count": len(messages),
                "messages": _gmail_previews(messages, previews),
            }
        if provider == "x":
            query = (monitor_row.get("source_query") or "").strip()
            if not query:
                handle = str(account_row.get("handle_or_address", "")).strip().lstrip("@")
                query = f"@{handle}" if handle else ""
            if not query:
                return {"status": "manual_required", "provider": provider, "detail": "Set a source_query or handle first."}
            resp = await client.get(
                "https://api.x.com/2/tweets/search/recent",
                headers={"Authorization": f"Bearer {secret}"},
                params={
                    "query": query,
                    "max_results": 10,
                    "tweet.fields": "created_at,author_id,lang,public_metrics",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            rows = []
            for item in (data.get("data") or [])[:5]:
                rows.append(
                    {
                        "id": item.get("id"),
                        "created_at": item.get("created_at"),
                        "text": str(item.get("text", "")).strip()[:280],
                    }
                )
            return {
                "status": "ok",
                "provider": provider,
                "account_key": account_row.get("account_key"),
                "query": query,
                "count": len(data.get("data") or []),
                "items": rows,
            }
        if provider == "discord":
            auth_type = normalize_auth_type(account_row.get("auth_type") or "bot_token")
            auth_header = secret if auth_type == "custom" else f"Bot {secret}" if auth_type == "bot_token" else secret
            me = await client.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": auth_header},
            )
            me.raise_for_status()
            me_data = me.json()
            result = {
                "status": "ok",
                "provider": provider,
                "account_key": account_row.get("account_key"),
                "identity": {"id": me_data.get("id"), "username": me_data.get("username"), "global_name": me_data.get("global_name")},
            }
            channel_ref = str(monitor_row.get("channel_ref", "")).strip()
            if channel_ref:
                msgs = await client.get(
                    f"https://discord.com/api/v10/channels/{channel_ref}/messages",
                    headers={"Authorization": auth_header},
                    params={"limit": 5},
                )
                msgs.raise_for_status()
                payload = msgs.json()
                result["recent_messages"] = [
                    {
                        "id": row.get("id"),
                        "author": ((row.get("author") or {}).get("username") or ""),
                        "content": str(row.get("content", "")).strip()[:240],
                        "timestamp": row.get("timestamp"),
                    }
                    for row in payload[:5]
                    if isinstance(row, dict)
                ]
            return result
        if provider == "instagram":
            return {
                "status": "manual_required",
                "provider": provider,
                "detail": "Instagram monitoring requires Graph OAuth plus business account identifiers. Store the account here, then finish the provider connection before live polling.",
            }
    return {"status": "manual_required", "provider": provider, "detail": "Unsupported provider monitor."}


def default_brand_account_plan(
    business_key: str,
    business_name: str,
    project_key: str | None,
    public_domain: str,
    luminax_email: str,
    support_email: str,
    promotions_email: str,
) -> dict:
    brand_label = business_name or maybe_pretty_label(business_key) or business_key
    safe_key = normalize_project_key(business_key) or normalize_owner_key(business_key)
    support_email = (support_email or f"{safe_key}support@gmail.com").strip().lower()
    promotions_email = (promotions_email or f"{safe_key}promotions@gmail.com").strip().lower()
    public_domain = (public_domain or "").strip().lower()
    handle_seed = (public_domain.split(".")[0] if public_domain else safe_key).strip("@").replace(".", "")
    x_handle = f"@{handle_seed}" if handle_seed else f"@{safe_key}"
    instagram_handle = handle_seed or safe_key
    domain_accounts = []
    if public_domain:
        domain_accounts = [
            {
                "account_key": f"{safe_key}-support-domain-email",
                "provider": "gmail",
                "account_kind": "email",
                "owner_key": safe_key,
                "display_name": f"{brand_label} Support",
                "handle_or_address": f"support@{public_domain}",
                "status": "planned",
                "onboarding_mode": "workspace_alias",
                "tags": ["support", "domain", "email"],
                "scopes": ["gmail.readonly", "gmail.modify"],
                "meta": {"purpose": "Primary domain support inbox", "monitor_defaults": {"interval_minutes": 120, "source_query": "label:unread newer_than:14d"}},
            },
            {
                "account_key": f"{safe_key}-promotions-domain-email",
                "provider": "gmail",
                "account_kind": "email",
                "owner_key": safe_key,
                "display_name": f"{brand_label} Promotions",
                "handle_or_address": f"promotions@{public_domain}",
                "status": "planned",
                "onboarding_mode": "workspace_alias",
                "tags": ["promotions", "domain", "email"],
                "scopes": ["gmail.readonly", "gmail.modify"],
                "meta": {"purpose": "Campaign/promotions inbox", "monitor_defaults": {"interval_minutes": 240, "source_query": "label:unread newer_than:30d"}},
            },
        ]
    accounts = [
        {
            "account_key": "luminax-gmail",
            "provider": "gmail",
            "account_kind": "email",
            "owner_key": "luminax",
            "display_name": "Luminax Primary",
            "handle_or_address": (luminax_email or "luminax@gmail.com").strip().lower(),
            "status": "planned",
            "onboarding_mode": "manual_signup",
            "tags": ["luminax", "primary", "email"],
            "scopes": ["gmail.readonly", "gmail.modify"],
            "meta": {"purpose": "Luminax direct identity inbox", "monitor_defaults": {"interval_minutes": 120, "source_query": "label:unread newer_than:14d"}},
        },
        {
            "account_key": f"{safe_key}-support-gmail",
            "provider": "gmail",
            "account_kind": "email",
            "owner_key": safe_key,
            "display_name": f"{brand_label} Support Gmail",
            "handle_or_address": support_email,
            "status": "planned",
            "onboarding_mode": "manual_signup",
            "tags": ["support", "gmail", "email"],
            "scopes": ["gmail.readonly", "gmail.modify"],
            "meta": {"purpose": "Backup support inbox", "monitor_defaults": {"interval_minutes": 120, "source_query": "label:unread newer_than:14d"}},
        },
        {
            "account_key": f"{safe_key}-promotions-gmail",
            "provider": "gmail",
            "account_kind": "email",
            "owner_key": safe_key,
            "display_name": f"{brand_label} Promotions Gmail",
            "handle_or_address": promotions_email,
            "status": "planned",
            "onboarding_mode": "manual_signup",
            "tags": ["promotions", "gmail", "email"],
            "scopes": ["gmail.readonly", "gmail.modify"],
            "meta": {"purpose": "Promotions/campaign inbox", "monitor_defaults": {"interval_minutes": 240, "source_query": "label:unread newer_than:30d"}},
        },
        {
            "account_key": f"{safe_key}-x",
            "provider": "x",
            "account_kind": "social",
            "owner_key": safe_key,
            "display_name": f"{brand_label} on X",
            "handle_or_address": x_handle,
            "status": "planned",
            "onboarding_mode": "oauth_connect",
            "tags": ["social", "x", "marketing"],
            "scopes": ["tweet.read", "users.read", "offline.access"],
            "meta": {"purpose": "Brand account on X", "monitor_defaults": {"interval_minutes": 240, "source_query": x_handle}},
        },
        {
            "account_key": f"{safe_key}-instagram",
            "provider": "instagram",
            "account_kind": "social",
            "owner_key": safe_key,
            "display_name": f"{brand_label} on Instagram",
            "handle_or_address": instagram_handle,
            "status": "planned",
            "onboarding_mode": "manual_app_connect",
            "tags": ["social", "instagram", "marketing"],
            "scopes": ["instagram_basic"],
            "meta": {"purpose": "Brand account on Instagram", "monitor_defaults": {"interval_minutes": 240, "source_query": ""}},
        },
        {
            "account_key": f"{safe_key}-discord-bot",
            "provider": "discord",
            "account_kind": "bot",
            "owner_key": safe_key,
            "display_name": f"{brand_label} Discord Bot",
            "handle_or_address": f"{brand_label} Bot",
            "status": "planned",
            "onboarding_mode": "discord_bot_app",
            "tags": ["discord", "bot", "support"],
            "scopes": ["bot", "applications.commands"],
            "meta": {"purpose": "Business bot for support and ops", "monitor_defaults": {"interval_minutes": 120, "source_query": "", "channel_ref": ""}},
        },
        {
            "account_key": "luminax-discord-bot",
            "provider": "discord",
            "account_kind": "bot",
            "owner_key": "luminax",
            "display_name": "Luminax Discord Bot",
            "handle_or_address": "Luminax",
            "status": "planned",
            "onboarding_mode": "discord_bot_app",
            "tags": ["discord", "bot", "luminax"],
            "scopes": ["bot", "applications.commands"],
            "meta": {"purpose": "Luminax direct Discord identity", "monitor_defaults": {"interval_minutes": 120, "source_query": "", "channel_ref": ""}},
        },
    ] + domain_accounts
    commands = [
        {
            "label": "Generate bootstrap plan",
            "route": "/accounts/brand_accounts/bootstrap_plan",
            "method": "POST",
            "payload": {
                "business_key": safe_key,
                "business_name": brand_label,
                "project_key": project_key,
                "public_domain": public_domain,
                "luminax_email": (luminax_email or "luminax@gmail.com").strip().lower(),
                "support_email": support_email,
                "promotions_email": promotions_email,
                "create_records": True,
            },
        },
        {
            "label": "Connect provider secret/token",
            "route": "/accounts/brand_accounts/set_secret",
            "method": "POST",
            "payload_template": {"account_key": f"{safe_key}-support-gmail", "auth_type": "oauth_token", "secret": "<provider token>", "refresh_token": "<optional refresh token>"},
        },
        {
            "label": "Save monitor policy",
            "route": "/accounts/brand_accounts/monitors/upsert",
            "method": "POST",
            "payload_template": {"monitor_key": f"{safe_key}-support-gmail-monitor", "account_key": f"{safe_key}-support-gmail", "provider": "gmail", "interval_minutes": 120, "source_query": "label:unread newer_than:14d"},
        },
        {
            "label": "Run monitor now",
            "route": "/accounts/brand_accounts/monitors/run",
            "method": "POST",
            "payload_template": {"monitor_key": f"{safe_key}-support-gmail-monitor"},
        },
    ]
    return {
        "business_key": safe_key,
        "business_name": brand_label,
        "project_key": project_key,
        "public_domain": public_domain or None,
        "accounts": accounts,
        "commands": commands,
        "notes": [
            "Consumer Gmail, X, Instagram, and personal Discord identities still require manual signup or provider OAuth; Luminax can manage the records, tokens, and monitors once those credentials exist.",
            "For branded email, custom-domain inboxes such as support@yourdomain are more durable than relying only on consumer Gmail availability.",
        ],
    }


def vault_enabled() -> bool:
    return bool(TESTER_ACCOUNT_VAULT_KEY)


def get_vault() -> Fernet:
    if not TESTER_ACCOUNT_VAULT_KEY:
        raise HTTPException(status_code=503, detail="TESTER_ACCOUNT_VAULT_KEY is not configured")
    key_raw = TESTER_ACCOUNT_VAULT_KEY.encode("utf-8")
    try:
        return Fernet(key_raw)
    except Exception:
        derived = base64.urlsafe_b64encode(hashlib.sha256(key_raw).digest())
        return Fernet(derived)


def encrypt_secret(secret: str) -> str:
    return get_vault().encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str | None) -> str:
    if not ciphertext:
        return ""
    return get_vault().decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def token_hint(token: str) -> str:
    clean = (token or "").strip()
    if len(clean) <= 8:
        return "***"
    return f"{clean[:4]}...{clean[-4:]}"


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "worker": "account-workflow-manager",
        "tester_vault_enabled": vault_enabled(),
        "tester_account_manager": "available",
        "brand_account_manager": "available",
    }


@app.get("/queue")
async def queue(
    scope: str = Query(default=""),
    project_key: str = Query(default=""),
    provider: str = Query(default=""),
    status: str = Query(default=""),
):
    ensure_scope_columns()
    scope_filter = normalize_scope(scope) if (scope or "").strip() else ""
    project_filter = normalize_project_key(project_key)
    provider_filter = (provider or "").strip().lower()
    status_filter = (status or "").strip().lower()
    try:
        conn = db_conn()
        cur = conn.cursor(dictionary=True)
        clauses = []
        args = []
        if scope_filter:
            clauses.append("integration_scope=%s")
            args.append(scope_filter)
        if project_filter and scope_filter == "project":
            clauses.append("project_key=%s")
            args.append(project_filter)
        if provider_filter:
            clauses.append("provider=%s")
            args.append(provider_filter)
        if status_filter:
            clauses.append("status=%s")
            args.append(status_filter)
        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = (
            "SELECT id, created_at, provider, purpose, status, integration_scope, project_key, details_json "
            f"FROM accounts_requested{where_sql} ORDER BY id DESC LIMIT 200"
        )
        try:
            cur.execute(query, tuple(args))
        except Exception:
            cur.execute("SELECT id, created_at, provider, purpose, status, details_json FROM accounts_requested ORDER BY id DESC LIMIT 200")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return {
            "status": "ok",
            "rows": rows,
            "filters": {
                "scope": scope_filter or None,
                "project_key": project_filter,
                "provider": provider_filter or None,
                "status": status_filter or None,
            },
        }
    except Exception as e:
        return {"status": "error", "rows": [], "error": str(e)}


@app.post("/request")
async def request_account(req: AccountRequest):
    ensure_scope_columns()
    provider = normalize_provider(req.provider)
    purpose = (req.purpose or "").strip()
    if not purpose:
        raise HTTPException(status_code=400, detail="Purpose is required")
    scope = normalize_scope(req.scope)
    project_key = normalize_project_key(req.project_key)
    if scope == "luminax":
        project_key = None
    details = {"requested_via": "account-worker", "scope": scope}
    if project_key:
        details["project_key"] = project_key
    conn = db_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO accounts_requested (provider, purpose, status, integration_scope, project_key, details_json) VALUES (%s,%s,%s,%s,%s,%s)",
            (provider, purpose[:1000], "pending", scope, project_key, json.dumps(details)),
        )
    except Exception:
        cur.execute(
            "INSERT INTO accounts_requested (provider, purpose, status, details_json) VALUES (%s,%s,%s,%s)",
            (provider, purpose[:1000], "pending", json.dumps(details)),
        )
    conn.commit()
    row_id = cur.lastrowid
    cur.close()
    conn.close()
    return {"status": "pending", "request_id": row_id, "provider": provider, "scope": scope, "project_key": project_key}


@app.post("/update_status")
async def update_status(req: AccountStatusUpdate):
    wanted = normalize_status(req.status, REQUEST_STATUSES, "pending")
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT details_json FROM accounts_requested WHERE id=%s", (req.request_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Request not found")
    details = parse_json_value(row[0], {})
    details["status_note"] = (req.note or "").strip()[:1000]
    details["updated_at_unix"] = int(time.time())
    cur.execute("UPDATE accounts_requested SET status=%s, details_json=%s WHERE id=%s", (wanted, json.dumps(details), req.request_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "ok", "request_id": req.request_id, "updated_status": wanted}


@app.get("/summary")
async def summary():
    ensure_tester_accounts_table()
    try:
        conn = db_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT provider, status, COUNT(*) AS total FROM accounts_requested GROUP BY provider, status ORDER BY provider, status")
        rows = cur.fetchall()
        cur.execute(
            "SELECT provider, status, account_type, COUNT(*) AS total, "
            "SUM(CASE WHEN token_ciphertext IS NULL OR token_ciphertext='' THEN 0 ELSE 1 END) AS with_token "
            "FROM managed_tester_accounts GROUP BY provider, status, account_type ORDER BY provider, status, account_type"
        )
        tester_rows = cur.fetchall()
        cur.close()
        conn.close()
        return {"status": "ok", "rows": rows, "tester_rows": tester_rows, "tester_vault_enabled": vault_enabled()}
    except Exception as e:
        return {"status": "error", "rows": [], "tester_rows": [], "error": str(e), "tester_vault_enabled": vault_enabled()}


@app.get("/tester_accounts/list")
async def tester_accounts_list(
    provider: str = Query(default=""),
    status: str = Query(default=""),
    project_key: str = Query(default=""),
    tag: str = Query(default=""),
    include_archived: bool = Query(default=False),
    token: str = Query(default=""),
):
    require_control_token(token)
    ensure_tester_accounts_table()
    provider_filter = normalize_provider(provider) if (provider or "").strip() else ""
    status_filter = normalize_status(status, TESTER_STATUSES, "") if (status or "").strip() else ""
    project_filter = normalize_project_key(project_key)
    tag_filter = (tag or "").strip().lower()
    conn = db_conn()
    cur = conn.cursor(dictionary=True)
    clauses = []
    args = []
    if provider_filter:
        clauses.append("provider=%s")
        args.append(provider_filter)
    if status_filter:
        clauses.append("status=%s")
        args.append(status_filter)
    elif not include_archived:
        clauses.append("status<>'deleted'")
    if project_filter:
        clauses.append("project_key=%s")
        args.append(project_filter)
    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    cur.execute(
        "SELECT id, created_at, updated_at, account_key, provider, account_type, display_name, project_key, "
        "status, token_hint, token_updated_at, expires_at, last_active_at, "
        "tags_json, scopes_json, persona_json, meta_json, "
        "CASE WHEN token_ciphertext IS NULL OR token_ciphertext='' THEN 0 ELSE 1 END AS has_token "
        f"FROM managed_tester_accounts{where_sql} ORDER BY updated_at DESC LIMIT 500",
        tuple(args),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    for row in rows:
        row["tags"] = normalize_list(parse_json_value(row.pop("tags_json", []), []), max_items=120, max_len=80)
        row["scopes"] = normalize_list(parse_json_value(row.pop("scopes_json", []), []), max_items=120, max_len=120)
        row["persona"] = parse_json_value(row.pop("persona_json", {}), {})
        row["meta"] = parse_json_value(row.pop("meta_json", {}), {})
        row["has_token"] = bool(row.get("has_token"))
    if tag_filter:
        rows = [r for r in rows if tag_filter in {str(t).lower() for t in (r.get("tags") or [])}]
    return {
        "status": "ok",
        "rows": rows,
        "filters": {
            "provider": provider_filter or None,
            "status": status_filter or None,
            "project_key": project_filter,
            "tag": tag_filter or None,
            "include_archived": include_archived,
        },
        "tester_vault_enabled": vault_enabled(),
    }


@app.post("/tester_accounts/upsert")
async def tester_accounts_upsert(req: TesterAccountUpsert):
    require_control_token(req.token)
    ensure_tester_accounts_table()
    account_key = normalize_account_key(req.account_key)
    provider = normalize_provider(req.provider)
    account_type = normalize_account_type(req.account_type)
    project_key = normalize_project_key(req.project_key)
    status = normalize_status(req.status, TESTER_STATUSES, "active")
    display_name = (req.display_name or "").strip()[:255] or account_key
    tags = normalize_list(req.tags)
    scopes = normalize_list(req.scopes, max_items=200, max_len=120)
    persona = req.persona if isinstance(req.persona, dict) else {}
    meta = {"note": (req.note or "").strip()[:1000], "updated_via": "account-worker:upsert", "updated_at_unix": int(time.time())}
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO managed_tester_accounts
        (account_key, provider, account_type, display_name, project_key, status, tags_json, scopes_json, persona_json, meta_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          provider=VALUES(provider),
          account_type=VALUES(account_type),
          display_name=VALUES(display_name),
          project_key=VALUES(project_key),
          status=VALUES(status),
          tags_json=VALUES(tags_json),
          scopes_json=VALUES(scopes_json),
          persona_json=VALUES(persona_json),
          meta_json=VALUES(meta_json)
        """,
        (
            account_key,
            provider,
            account_type,
            display_name,
            project_key,
            status,
            json.dumps(tags),
            json.dumps(scopes),
            json.dumps(persona),
            json.dumps(meta),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {
        "status": "ok",
        "account_key": account_key,
        "provider": provider,
        "account_type": account_type,
        "project_key": project_key,
        "tester_vault_enabled": vault_enabled(),
    }


@app.post("/tester_accounts/set_token")
async def tester_accounts_set_token(req: TesterAccountTokenSet):
    require_control_token(req.control_token)
    ensure_tester_accounts_table()
    account_key = normalize_account_key(req.account_key)
    token = (req.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token is required")
    provider = normalize_provider(req.provider)
    account_type = normalize_account_type(req.account_type)
    scopes = normalize_list(req.scopes, max_items=200, max_len=120)
    note = (req.note or "").strip()[:1000]
    now_unix = int(time.time())
    cipher = encrypt_secret(token)
    refresh_cipher = encrypt_secret(req.refresh_token.strip()) if (req.refresh_token or "").strip() else None
    hint = token_hint(token)
    expires_unix = req.expires_unix if (req.expires_unix and req.expires_unix > 0) else None
    meta = {"token_note": note, "token_set_at_unix": now_unix, "updated_via": "account-worker:set_token"}
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO managed_tester_accounts
        (account_key, provider, account_type, status, token_ciphertext, refresh_ciphertext, token_hint, token_updated_at, expires_at, scopes_json, meta_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP,FROM_UNIXTIME(%s),%s,%s)
        ON DUPLICATE KEY UPDATE
          provider=VALUES(provider),
          account_type=VALUES(account_type),
          status='active',
          token_ciphertext=VALUES(token_ciphertext),
          refresh_ciphertext=VALUES(refresh_ciphertext),
          token_hint=VALUES(token_hint),
          token_updated_at=CURRENT_TIMESTAMP,
          expires_at=VALUES(expires_at),
          scopes_json=VALUES(scopes_json),
          meta_json=VALUES(meta_json)
        """,
        (
            account_key,
            provider,
            account_type,
            "active",
            cipher,
            refresh_cipher,
            hint,
            expires_unix,
            json.dumps(scopes),
            json.dumps(meta),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {
        "status": "ok",
        "account_key": account_key,
        "token_hint": hint,
        "expires_unix": expires_unix,
        "tester_vault_enabled": vault_enabled(),
    }


@app.post("/tester_accounts/set_status")
async def tester_accounts_set_status(req: TesterAccountStatusSet):
    require_control_token(req.token)
    ensure_tester_accounts_table()
    account_key = normalize_account_key(req.account_key)
    status = normalize_status(req.status, TESTER_STATUSES, "active")
    note = (req.note or "").strip()[:1000]
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE managed_tester_accounts
        SET status=%s, meta_json=%s
        WHERE account_key=%s
        """,
        (
            status,
            json.dumps({"status_note": note, "updated_at_unix": int(time.time()), "updated_via": "account-worker:set_status"}),
            account_key,
        ),
    )
    conn.commit()
    affected = cur.rowcount
    cur.close()
    conn.close()
    if affected < 1:
        raise HTTPException(status_code=404, detail="Tester account not found")
    return {"status": "ok", "account_key": account_key, "updated_status": status}


@app.post("/tester_accounts/delete")
async def tester_accounts_delete(req: TesterAccountDelete):
    require_control_token(req.token)
    ensure_tester_accounts_table()
    account_key = normalize_account_key(req.account_key)
    note = (req.note or "").strip()[:1000]
    conn = db_conn()
    cur = conn.cursor()
    if req.purge_tokens:
        cur.execute(
            """
            UPDATE managed_tester_accounts
            SET status='deleted', token_ciphertext=NULL, refresh_ciphertext=NULL, token_hint=NULL, meta_json=%s
            WHERE account_key=%s
            """,
            (json.dumps({"delete_note": note, "purged_tokens": True, "updated_at_unix": int(time.time())}), account_key),
        )
    else:
        cur.execute(
            """
            UPDATE managed_tester_accounts
            SET status='deleted', meta_json=%s
            WHERE account_key=%s
            """,
            (json.dumps({"delete_note": note, "purged_tokens": False, "updated_at_unix": int(time.time())}), account_key),
        )
    conn.commit()
    affected = cur.rowcount
    cur.close()
    conn.close()
    if affected < 1:
        raise HTTPException(status_code=404, detail="Tester account not found")
    return {"status": "ok", "account_key": account_key, "purged_tokens": bool(req.purge_tokens)}


@app.get("/tester_accounts/summary")
async def tester_accounts_summary(token: str = Query(default="")):
    require_control_token(token)
    ensure_tester_accounts_table()
    conn = db_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT provider, account_type, status, COUNT(*) AS total, "
        "SUM(CASE WHEN token_ciphertext IS NULL OR token_ciphertext='' THEN 0 ELSE 1 END) AS with_token "
        "FROM managed_tester_accounts "
        "GROUP BY provider, account_type, status "
        "ORDER BY provider, account_type, status"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    total = sum(int(r.get("total") or 0) for r in rows)
    with_token = sum(int(r.get("with_token") or 0) for r in rows)
    return {
        "status": "ok",
        "rows": rows,
        "totals": {
            "accounts": total,
            "with_token": with_token,
            "without_token": max(0, total - with_token),
        },
        "tester_vault_enabled": vault_enabled(),
    }


@app.post("/brand_accounts/bootstrap_plan")
async def brand_accounts_bootstrap_plan(req: BrandAccountBootstrapRequest):
    require_control_token(req.token)
    ensure_brand_account_tables()
    business_key = normalize_owner_key(req.business_key)
    business_name = (req.business_name or "").strip()[:255]
    project_key = normalize_project_key(req.project_key)
    plan = default_brand_account_plan(
        business_key=business_key,
        business_name=business_name,
        project_key=project_key,
        public_domain=(req.public_domain or "").strip(),
        luminax_email=(req.luminax_email or "luminax@gmail.com").strip().lower(),
        support_email=(req.support_email or "").strip().lower(),
        promotions_email=(req.promotions_email or "").strip().lower(),
    )
    created_accounts: list[str] = []
    created_monitors: list[str] = []
    if req.create_records:
        conn = db_conn()
        cur = conn.cursor()
        try:
            now_unix = int(time.time())
            for account in plan["accounts"]:
                tags = normalize_list(account.get("tags", []))
                scopes = normalize_list(account.get("scopes", []), max_items=200, max_len=120)
                meta = account.get("meta", {}) if isinstance(account.get("meta"), dict) else {}
                meta["bootstrapped_at_unix"] = now_unix
                meta["bootstrapped_via"] = "brand_accounts/bootstrap_plan"
                cur.execute(
                    """
                    INSERT INTO managed_brand_accounts
                    (account_key, provider, account_kind, owner_key, display_name, handle_or_address, project_key, status, onboarding_mode, tags_json, scopes_json, meta_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                      provider=VALUES(provider),
                      account_kind=VALUES(account_kind),
                      owner_key=VALUES(owner_key),
                      display_name=VALUES(display_name),
                      handle_or_address=VALUES(handle_or_address),
                      project_key=VALUES(project_key),
                      status=VALUES(status),
                      onboarding_mode=VALUES(onboarding_mode),
                      tags_json=VALUES(tags_json),
                      scopes_json=VALUES(scopes_json),
                      meta_json=VALUES(meta_json)
                    """,
                    (
                        normalize_account_key(account.get("account_key")),
                        normalize_provider(account.get("provider")),
                        normalize_account_kind(account.get("account_kind")),
                        normalize_owner_key(account.get("owner_key")),
                        str(account.get("display_name", "")).strip()[:255],
                        str(account.get("handle_or_address", "")).strip()[:255],
                        project_key,
                        normalize_status(account.get("status"), BRAND_ACCOUNT_STATUSES, "planned"),
                        normalize_onboarding_mode(account.get("onboarding_mode")),
                        json.dumps(tags),
                        json.dumps(scopes),
                        json.dumps(meta),
                    ),
                )
                created_accounts.append(normalize_account_key(account.get("account_key")))
                defaults = meta.get("monitor_defaults", {}) if isinstance(meta, dict) else {}
                monitor_key = f"{normalize_account_key(account.get('account_key'))}-monitor"
                cur.execute(
                    """
                    INSERT INTO managed_brand_monitors
                    (monitor_key, account_key, provider, monitor_mode, status, interval_minutes, source_query, channel_ref, next_run_at, meta_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,FROM_UNIXTIME(%s),%s)
                    ON DUPLICATE KEY UPDATE
                      account_key=VALUES(account_key),
                      provider=VALUES(provider),
                      monitor_mode=VALUES(monitor_mode),
                      status=VALUES(status),
                      interval_minutes=VALUES(interval_minutes),
                      source_query=VALUES(source_query),
                      channel_ref=VALUES(channel_ref),
                      meta_json=VALUES(meta_json)
                    """,
                    (
                        monitor_key,
                        normalize_account_key(account.get("account_key")),
                        normalize_provider(account.get("provider")),
                        "poll",
                        "active",
                        normalize_interval_minutes(defaults.get("interval_minutes", 120)),
                        str(defaults.get("source_query", "")).strip()[:512],
                        str(defaults.get("channel_ref", "")).strip()[:255],
                        now_unix,
                        json.dumps({"bootstrapped": True, "updated_at_unix": now_unix}),
                    ),
                )
                created_monitors.append(monitor_key)
            conn.commit()
        finally:
            cur.close()
            conn.close()
    return {
        "status": "ok",
        "plan": plan,
        "created_accounts": created_accounts,
        "created_monitors": created_monitors,
    }


@app.get("/brand_accounts/list")
async def brand_accounts_list(
    provider: str = Query(default=""),
    status: str = Query(default=""),
    owner_key: str = Query(default=""),
    project_key: str = Query(default=""),
    tag: str = Query(default=""),
    include_archived: bool = Query(default=False),
    token: str = Query(default=""),
):
    require_control_token(token)
    ensure_brand_account_tables()
    provider_filter = normalize_provider(provider) if (provider or "").strip() else ""
    status_filter = normalize_status(status, BRAND_ACCOUNT_STATUSES, "") if (status or "").strip() else ""
    owner_filter = normalize_owner_key(owner_key) if (owner_key or "").strip() else ""
    project_filter = normalize_project_key(project_key)
    tag_filter = (tag or "").strip().lower()
    conn = db_conn()
    cur = conn.cursor(dictionary=True)
    clauses = []
    args = []
    if provider_filter:
        clauses.append("provider=%s")
        args.append(provider_filter)
    if status_filter:
        clauses.append("status=%s")
        args.append(status_filter)
    elif not include_archived:
        clauses.append("status<>'deleted'")
    if owner_filter:
        clauses.append("owner_key=%s")
        args.append(owner_filter)
    if project_filter:
        clauses.append("project_key=%s")
        args.append(project_filter)
    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    cur.execute(
        "SELECT id, created_at, updated_at, account_key, provider, account_kind, owner_key, display_name, handle_or_address, "
        "project_key, status, onboarding_mode, auth_type, secret_hint, token_updated_at, expires_at, last_checked_at, last_error_at, "
        "tags_json, scopes_json, meta_json, "
        "CASE WHEN token_ciphertext IS NULL OR token_ciphertext='' THEN 0 ELSE 1 END AS has_secret "
        f"FROM managed_brand_accounts{where_sql} ORDER BY updated_at DESC LIMIT 500",
        tuple(args),
    )
    rows = [brand_account_row_payload(row) for row in cur.fetchall()]
    cur.close()
    conn.close()
    if tag_filter:
        rows = [r for r in rows if tag_filter in {str(t).lower() for t in (r.get("tags") or [])}]
    return {"status": "ok", "rows": rows, "vault_enabled": vault_enabled()}


@app.post("/brand_accounts/upsert")
async def brand_accounts_upsert(req: BrandAccountUpsert):
    require_control_token(req.token)
    ensure_brand_account_tables()
    account_key = normalize_account_key(req.account_key)
    provider = normalize_provider(req.provider)
    account_kind = normalize_account_kind(req.account_kind)
    owner_key = normalize_owner_key(req.owner_key or ("luminax" if account_key.startswith("luminax") else "business"))
    project_key = normalize_project_key(req.project_key)
    status = normalize_status(req.status, BRAND_ACCOUNT_STATUSES, "planned")
    onboarding_mode = normalize_onboarding_mode(req.onboarding_mode)
    display_name = (req.display_name or "").strip()[:255] or account_key
    handle_or_address = (req.handle_or_address or "").strip()[:255]
    tags = normalize_list(req.tags)
    scopes = normalize_list(req.scopes, max_items=200, max_len=120)
    meta = req.meta if isinstance(req.meta, dict) else {}
    meta["note"] = (req.note or "").strip()[:1000]
    meta["updated_via"] = "account-worker:brand_accounts/upsert"
    meta["updated_at_unix"] = int(time.time())
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO managed_brand_accounts
        (account_key, provider, account_kind, owner_key, display_name, handle_or_address, project_key, status, onboarding_mode, tags_json, scopes_json, meta_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          provider=VALUES(provider),
          account_kind=VALUES(account_kind),
          owner_key=VALUES(owner_key),
          display_name=VALUES(display_name),
          handle_or_address=VALUES(handle_or_address),
          project_key=VALUES(project_key),
          status=VALUES(status),
          onboarding_mode=VALUES(onboarding_mode),
          tags_json=VALUES(tags_json),
          scopes_json=VALUES(scopes_json),
          meta_json=VALUES(meta_json)
        """,
        (
            account_key,
            provider,
            account_kind,
            owner_key,
            display_name,
            handle_or_address,
            project_key,
            status,
            onboarding_mode,
            json.dumps(tags),
            json.dumps(scopes),
            json.dumps(meta),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {
        "status": "ok",
        "account_key": account_key,
        "provider": provider,
        "owner_key": owner_key,
        "project_key": project_key,
        "vault_enabled": vault_enabled(),
    }


@app.post("/brand_accounts/set_secret")
async def brand_accounts_set_secret(req: BrandAccountSecretSet):
    require_control_token(req.control_token)
    ensure_brand_account_tables()
    account_key = normalize_account_key(req.account_key)
    secret = (req.secret or "").strip()
    if not secret:
        raise HTTPException(status_code=400, detail="secret is required")
    auth_type = normalize_auth_type(req.auth_type)
    scopes = normalize_list(req.scopes, max_items=200, max_len=120)
    note = (req.note or "").strip()[:1000]
    now_unix = int(time.time())
    cipher = encrypt_secret(secret)
    refresh_cipher = encrypt_secret(req.refresh_token.strip()) if (req.refresh_token or "").strip() else None
    hint = token_hint(secret)
    expires_unix = req.expires_unix if (req.expires_unix and req.expires_unix > 0) else None
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE managed_brand_accounts
        SET auth_type=%s,
            token_ciphertext=%s,
            refresh_ciphertext=%s,
            secret_hint=%s,
            token_updated_at=CURRENT_TIMESTAMP,
            expires_at=FROM_UNIXTIME(%s),
            status='connected',
            scopes_json=%s,
            meta_json=%s
        WHERE account_key=%s
        """,
        (
            auth_type,
            cipher,
            refresh_cipher,
            hint,
            expires_unix,
            json.dumps(scopes),
            json.dumps({"secret_note": note, "updated_via": "account-worker:brand_accounts/set_secret", "updated_at_unix": now_unix, "scopes": scopes}),
            account_key,
        ),
    )
    conn.commit()
    affected = cur.rowcount
    cur.close()
    conn.close()
    if affected < 1:
        raise HTTPException(status_code=404, detail="Brand account not found")
    return {"status": "ok", "account_key": account_key, "secret_hint": hint, "expires_unix": expires_unix, "vault_enabled": vault_enabled()}


@app.post("/brand_accounts/set_status")
async def brand_accounts_set_status(req: BrandAccountStatusSet):
    require_control_token(req.token)
    ensure_brand_account_tables()
    account_key = normalize_account_key(req.account_key)
    status = normalize_status(req.status, BRAND_ACCOUNT_STATUSES, "planned")
    note = (req.note or "").strip()[:1000]
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE managed_brand_accounts SET status=%s, meta_json=%s WHERE account_key=%s",
        (
            status,
            json.dumps({"status_note": note, "updated_via": "account-worker:brand_accounts/set_status", "updated_at_unix": int(time.time())}),
            account_key,
        ),
    )
    conn.commit()
    affected = cur.rowcount
    cur.close()
    conn.close()
    if affected < 1:
        raise HTTPException(status_code=404, detail="Brand account not found")
    return {"status": "ok", "account_key": account_key, "updated_status": status}


@app.get("/brand_accounts/monitors/list")
async def brand_monitors_list(
    provider: str = Query(default=""),
    account_key: str = Query(default=""),
    status: str = Query(default=""),
    token: str = Query(default=""),
):
    require_control_token(token)
    ensure_brand_account_tables()
    provider_filter = normalize_provider(provider) if (provider or "").strip() else ""
    account_filter = normalize_account_key(account_key) if (account_key or "").strip() else ""
    status_filter = normalize_status(status, BRAND_MONITOR_STATUSES, "") if (status or "").strip() else ""
    conn = db_conn()
    cur = conn.cursor(dictionary=True)
    clauses = []
    args = []
    if provider_filter:
        clauses.append("provider=%s")
        args.append(provider_filter)
    if account_filter:
        clauses.append("account_key=%s")
        args.append(account_filter)
    if status_filter:
        clauses.append("status=%s")
        args.append(status_filter)
    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    cur.execute(
        "SELECT id, created_at, updated_at, monitor_key, account_key, provider, monitor_mode, status, interval_minutes, "
        "source_query, channel_ref, last_run_at, next_run_at, last_status, last_result_json, meta_json "
        f"FROM managed_brand_monitors{where_sql} ORDER BY updated_at DESC LIMIT 500",
        tuple(args),
    )
    rows = [brand_monitor_row_payload(row) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return {"status": "ok", "rows": rows}


@app.post("/brand_accounts/monitors/upsert")
async def brand_monitors_upsert(req: BrandMonitorUpsert):
    require_control_token(req.token)
    ensure_brand_account_tables()
    monitor_key = normalize_monitor_key(req.monitor_key)
    account_key = normalize_account_key(req.account_key)
    provider = normalize_provider(req.provider)
    status = normalize_status(req.status, BRAND_MONITOR_STATUSES, "active")
    interval_minutes = normalize_interval_minutes(req.interval_minutes)
    meta = req.meta if isinstance(req.meta, dict) else {}
    meta["note"] = (req.note or "").strip()[:1000]
    meta["updated_via"] = "account-worker:brand_accounts/monitors/upsert"
    meta["updated_at_unix"] = int(time.time())
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO managed_brand_monitors
        (monitor_key, account_key, provider, monitor_mode, status, interval_minutes, source_query, channel_ref, next_run_at, meta_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP,%s)
        ON DUPLICATE KEY UPDATE
          account_key=VALUES(account_key),
          provider=VALUES(provider),
          monitor_mode=VALUES(monitor_mode),
          status=VALUES(status),
          interval_minutes=VALUES(interval_minutes),
          source_query=VALUES(source_query),
          channel_ref=VALUES(channel_ref),
          meta_json=VALUES(meta_json)
        """,
        (
            monitor_key,
            account_key,
            provider,
            str(req.monitor_mode or "poll").strip()[:64] or "poll",
            status,
            interval_minutes,
            str(req.source_query or "").strip()[:512],
            str(req.channel_ref or "").strip()[:255],
            json.dumps(meta),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "ok", "monitor_key": monitor_key, "account_key": account_key, "interval_minutes": interval_minutes}


@app.post("/brand_accounts/monitors/run")
async def brand_monitors_run(req: BrandMonitorRunRequest):
    require_control_token(req.token)
    ensure_brand_account_tables()
    limit = max(1, min(int(req.limit or 20), 50))
    monitor_filter = normalize_monitor_key(req.monitor_key) if (req.monitor_key or "").strip() else ""
    account_filter = normalize_account_key(req.account_key) if (req.account_key or "").strip() else ""
    conn = db_conn()
    cur = conn.cursor(dictionary=True)
    clauses = ["m.status='active'"]
    args = []
    if monitor_filter:
        clauses.append("m.monitor_key=%s")
        args.append(monitor_filter)
    if account_filter:
        clauses.append("m.account_key=%s")
        args.append(account_filter)
    if req.due_only:
        clauses.append("(m.next_run_at IS NULL OR m.next_run_at<=CURRENT_TIMESTAMP)")
    where_sql = f" WHERE {' AND '.join(clauses)}"
    cur.execute(
        "SELECT m.id AS monitor_id, m.monitor_key, m.account_key, m.provider, m.monitor_mode, m.status, m.interval_minutes, "
        "m.source_query, m.channel_ref, m.last_run_at, m.next_run_at, m.last_status, m.last_result_json, m.meta_json, "
        "a.token_ciphertext, a.refresh_ciphertext, a.handle_or_address, a.display_name, a.owner_key, a.auth_type "
        f"FROM managed_brand_monitors m JOIN managed_brand_accounts a ON a.account_key=m.account_key{where_sql} "
        "ORDER BY COALESCE(m.next_run_at, m.created_at) ASC LIMIT %s",
        tuple(args + [limit]),
    )
    rows = cur.fetchall()
    results = []
    now_unix = int(time.time())
    for row in rows:
        monitor_key = row.get("monitor_key")
        account_row = {
            "account_key": row.get("account_key"),
            "token_ciphertext": row.get("token_ciphertext"),
            "refresh_ciphertext": row.get("refresh_ciphertext"),
            "handle_or_address": row.get("handle_or_address"),
            "display_name": row.get("display_name"),
            "owner_key": row.get("owner_key"),
            "auth_type": row.get("auth_type"),
        }
        monitor_row = {
            "monitor_key": monitor_key,
            "source_query": row.get("source_query"),
            "channel_ref": row.get("channel_ref"),
            "meta": parse_json_value(row.get("meta_json"), {}),
        }
        try:
            result = await run_brand_monitor(str(row.get("provider") or ""), account_row, monitor_row)
            last_status = str(result.get("status", "ok")).strip() or "ok"
            cur.execute(
                "UPDATE managed_brand_monitors SET last_run_at=CURRENT_TIMESTAMP, next_run_at=DATE_ADD(CURRENT_TIMESTAMP, INTERVAL %s MINUTE), last_status=%s, last_result_json=%s WHERE monitor_key=%s",
                (int(row.get("interval_minutes") or 120), last_status, json.dumps(result), monitor_key),
            )
            cur.execute(
                "UPDATE managed_brand_accounts SET last_checked_at=CURRENT_TIMESTAMP, last_error_at=NULL, status=IF(status='planned','connected',status) WHERE account_key=%s",
                (row.get("account_key"),),
            )
            results.append({"monitor_key": monitor_key, "account_key": row.get("account_key"), "provider": row.get("provider"), "result": result})
        except Exception as e:
            error_payload = {"status": "error", "detail": str(e), "provider": row.get("provider"), "account_key": row.get("account_key")}
            cur.execute(
                "UPDATE managed_brand_monitors SET last_run_at=CURRENT_TIMESTAMP, next_run_at=DATE_ADD(CURRENT_TIMESTAMP, INTERVAL %s MINUTE), last_status='error', last_result_json=%s WHERE monitor_key=%s",
                (int(row.get("interval_minutes") or 120), json.dumps(error_payload), monitor_key),
            )
            cur.execute(
                "UPDATE managed_brand_accounts SET last_checked_at=CURRENT_TIMESTAMP, last_error_at=CURRENT_TIMESTAMP WHERE account_key=%s",
                (row.get("account_key"),),
            )
            results.append({"monitor_key": monitor_key, "account_key": row.get("account_key"), "provider": row.get("provider"), "result": error_payload})
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "ok", "run_count": len(results), "ran_at_unix": now_unix, "results": results}


@app.get("/brand_accounts/summary")
async def brand_accounts_summary(token: str = Query(default="")):
    require_control_token(token)
    ensure_brand_account_tables()
    conn = db_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT provider, owner_key, account_kind, status, COUNT(*) AS total, "
        "SUM(CASE WHEN token_ciphertext IS NULL OR token_ciphertext='' THEN 0 ELSE 1 END) AS with_secret "
        "FROM managed_brand_accounts GROUP BY provider, owner_key, account_kind, status ORDER BY provider, owner_key, account_kind, status"
    )
    rows = cur.fetchall()
    cur.execute(
        "SELECT provider, status, COUNT(*) AS total FROM managed_brand_monitors GROUP BY provider, status ORDER BY provider, status"
    )
    monitor_rows = cur.fetchall()
    cur.close()
    conn.close()
    total = sum(int(r.get("total") or 0) for r in rows)
    with_secret = sum(int(r.get("with_secret") or 0) for r in rows)
    return {
        "status": "ok",
        "rows": rows,
        "monitor_rows": monitor_rows,
        "totals": {
            "accounts": total,
            "with_secret": with_secret,
            "without_secret": max(0, total - with_secret),
            "monitors": sum(int(r.get("total") or 0) for r in monitor_rows),
        },
        "vault_enabled": vault_enabled(),
    }



install_runtime_guard(app, 'account-worker', require_auth=False)
