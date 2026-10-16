import asyncio
import io
import json
import os
import random
import re
import socket
import sys
import tempfile
import time
import uuid
import wave
from datetime import datetime, timedelta, timezone
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import discord
import httpx
import mysql.connector
import redis
from discord import app_commands
from discord.ext import commands, voice_recv

try:
    discord.opus._load_default()
except Exception:
    pass

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True
intents.members = True
intents.dm_messages = True
CORE_URL = (os.getenv("CORE_URL", "") or "").strip().rstrip("/")
VOICE_URL = (os.getenv("VOICE_AGENT_URL", "") or "").strip().rstrip("/")
ACCOUNT_WORKER_URL = (os.getenv("ACCOUNT_WORKER_URL", "") or "").strip().rstrip("/")
ADMIN_API_URL = (os.getenv("ADMIN_API_URL", "") or "").strip().rstrip("/")
EDGE_BRIDGE_URL = (os.getenv("EDGE_BRIDGE_URL", "") or "").strip().rstrip("/")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379") or 6379)
DISCORD_APPLICATION_ID = int(os.getenv("DISCORD_APPLICATION_ID", os.getenv("DISCORD_APP_ID", "0")) or 0)
DISCORD_PUBLIC_KEY = (os.getenv("DISCORD_PUBLIC_KEY", "") or "").strip()


def _parse_owner_discord_ids(*raw_values: Any) -> tuple[int, ...]:
    ids: list[int] = []
    seen: set[int] = set()
    for raw in raw_values:
        for chunk in str(raw or "").replace(";", ",").split(","):
            digits = "".join(ch for ch in str(chunk).strip() if ch.isdigit())
            if not digits:
                continue
            try:
                parsed = int(digits)
            except Exception:
                continue
            if parsed <= 0 or parsed in seen:
                continue
            seen.add(parsed)
            ids.append(parsed)
    return tuple(ids)


OWNER_DISCORD_IDS = _parse_owner_discord_ids(
    os.getenv("YOUR_USER_IDS", ""),
    os.getenv("OLYMPUS_OWNER_DISCORD_IDS", ""),
    os.getenv("YOUR_USER_ID", "0"),
)
YOUR_USER_ID = OWNER_DISCORD_IDS[0] if OWNER_DISCORD_IDS else int(os.getenv("YOUR_USER_ID", "0") or 0)
CONTROL_TOKEN = (os.getenv("CONTROL_TOKEN", "") or "").strip()
MAIN_CONNECTOR_NAME = (os.getenv("MAIN_CONNECTOR_NAME", "Zeus") or "Zeus").strip() or "Zeus"
DISCORD_CONNECTOR_NAME = (os.getenv("DISCORD_CONNECTOR_NAME", "Hermes") or "Hermes").strip() or "Hermes"
UI_BRAND_NAME = (os.getenv("UI_BRAND_NAME", "Mount Olympus") or "Mount Olympus").strip() or "Mount Olympus"
DISCORD_RUNTIME_ROLE = (os.getenv("DISCORD_RUNTIME_ROLE", "disabled") or "disabled").strip().lower()
DISCORD_PERSONA_MODE = (os.getenv("DISCORD_PERSONA_MODE", "hermes") or "hermes").strip().lower() or "hermes"
DISCORD_SERVICE_LABEL_KEY = (os.getenv("DISCORD_SERVICE_LABEL_KEY", "discord-bot") or "discord-bot").strip() or "discord-bot"
DISCORD_LEADER_LEASE_KEY = (os.getenv("DISCORD_LEADER_LEASE_KEY", "discord:hermes:leader") or "discord:hermes:leader").strip()
DISCORD_LEADER_LEASE_TTL_SEC = max(15, int(os.getenv("DISCORD_LEADER_LEASE_TTL_SEC", "45") or 45))
DISCORD_MESSAGE_DEDUPE_TTL_SEC = max(15, int(os.getenv("DISCORD_MESSAGE_DEDUPE_TTL_SEC", "120") or 120))
DISCORD_RESPONSE_HOLD_SEC = max(2.0, float(os.getenv("DISCORD_RESPONSE_HOLD_SEC", "5") or 5))
DISCORD_PROGRESS_FIRST_SEC = max(DISCORD_RESPONSE_HOLD_SEC, float(os.getenv("DISCORD_PROGRESS_FIRST_SEC", "7") or 7))
DISCORD_PROGRESS_SECOND_SEC = max(DISCORD_PROGRESS_FIRST_SEC + 5.0, float(os.getenv("DISCORD_PROGRESS_SECOND_SEC", "45") or 45))
DISCORD_PROGRESS_REPEAT_SEC = max(30.0, float(os.getenv("DISCORD_PROGRESS_REPEAT_SEC", "60") or 60))
DISCORD_ZEUS_MODEL_PLANNER = os.getenv("DISCORD_ZEUS_MODEL_PLANNER", "false").lower() == "true"
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None, application_id=DISCORD_APPLICATION_ID or None)
DEFAULT_VOICE = os.getenv("DISCORD_TTS_VOICE", os.getenv("EDGE_TTS_VOICE", "en-US-GuyNeural"))
PCM_CHANNELS = 2
PCM_SAMPLE_WIDTH = 2
PCM_SAMPLE_RATE = 48000
PCM_BYTES_PER_SEC = PCM_CHANNELS * PCM_SAMPLE_WIDTH * PCM_SAMPLE_RATE
UTTERANCE_SILENCE_SEC = float(os.getenv("DISCORD_VOICE_SILENCE_SEC", "0.22"))
MIN_PCM_BYTES = int(os.getenv("DISCORD_VOICE_MIN_PCM_BYTES", "7680"))
MAX_BUFFER_SEC = float(os.getenv("DISCORD_VOICE_MAX_BUFFER_SEC", "0.95"))
MAX_BUFFER_BYTES = max(MIN_PCM_BYTES * 2, int(PCM_BYTES_PER_SEC * MAX_BUFFER_SEC))
MIN_FLUSH_INTERVAL_SEC = float(os.getenv("DISCORD_VOICE_MIN_FLUSH_INTERVAL_SEC", "0.06"))
VOICE_DEBUG_EVERY_SEC = float(os.getenv("DISCORD_VOICE_DEBUG_EVERY_SEC", "12"))
TEXT_CONTEXT_WINDOW_SEC = 300
TEXT_REPLY_MIN_INTERVAL_SEC = float(os.getenv("DISCORD_TEXT_REPLY_MIN_INTERVAL_SEC", "4"))
TEXT_AMBIENT_MIN_INTERVAL_SEC = float(os.getenv("DISCORD_TEXT_AMBIENT_MIN_INTERVAL_SEC", "15"))
DISCORD_HTTP_TIMEOUT_SEC = float(os.getenv("DISCORD_HTTP_TIMEOUT_SEC", "30"))
DISCORD_HTTP_CONNECT_TIMEOUT_SEC = float(os.getenv("DISCORD_HTTP_CONNECT_TIMEOUT_SEC", "5"))
DISCORD_ASSISTANT_TIMEOUT_SEC = float(os.getenv("DISCORD_ASSISTANT_TIMEOUT_SEC", "120"))
DISCORD_FAST_REPLY_TIMEOUT_SEC = float(os.getenv("DISCORD_FAST_REPLY_TIMEOUT_SEC", str(DISCORD_RESPONSE_HOLD_SEC)))
DISCORD_VOICE_FAST_REPLY_TIMEOUT_SEC = float(os.getenv("DISCORD_VOICE_FAST_REPLY_TIMEOUT_SEC", "8"))
DISCORD_LOOKUP_TIMEOUT_SEC = float(os.getenv("DISCORD_LOOKUP_TIMEOUT_SEC", "45"))
DISCORD_FAST_ACK_ENABLED = os.getenv("DISCORD_FAST_ACK_ENABLED", "true").lower() == "true"
DISCORD_FAST_ACK_MIN_INTERVAL_SEC = float(os.getenv("DISCORD_FAST_ACK_MIN_INTERVAL_SEC", "25"))
DISCORD_TEXT_BACKGROUND_LOOKUPS = os.getenv("DISCORD_TEXT_BACKGROUND_LOOKUPS", "true").lower() == "true"
DISCORD_TEXT_PROGRESS_UPDATE_SEC = float(os.getenv("DISCORD_TEXT_PROGRESS_UPDATE_SEC", str(DISCORD_PROGRESS_FIRST_SEC)))
DISCORD_VOICE_PROGRESS_UPDATE_SEC = float(os.getenv("DISCORD_VOICE_PROGRESS_UPDATE_SEC", "6"))
DISCORD_CODEX_PROGRESS_UPDATE_SEC = float(os.getenv("DISCORD_CODEX_PROGRESS_UPDATE_SEC", str(DISCORD_PROGRESS_FIRST_SEC)))
TEXT_NAME_ALIASES: list[str] = []
TEXT_CHANNEL_ALLOWLIST = {s.strip() for s in os.getenv("DISCORD_TEXT_CHANNELS", "").split(",") if s.strip()}
DISCORD_BUG_CHANNEL = os.getenv("DISCORD_BUG_CHANNEL", "")
DISCORD_INCIDENT_CHANNEL = (os.getenv("DISCORD_INCIDENT_CHANNEL", DISCORD_BUG_CHANNEL) or DISCORD_BUG_CHANNEL).strip()
DISCORD_AMBIENT_DMS = os.getenv("DISCORD_AMBIENT_DMS", "true").lower() == "true"
DISCORD_ACTIONS_ENABLED = os.getenv("DISCORD_ACTIONS_ENABLED", "true").lower() == "true"
DISCORD_ALWAYS_WATCH = os.getenv("DISCORD_ALWAYS_WATCH", "false").lower() == "true"
DISCORD_WELCOME_ENABLED = os.getenv("DISCORD_WELCOME_ENABLED", "true").lower() == "true"
DISCORD_VOICE_REPLY_ENABLED = os.getenv("DISCORD_VOICE_REPLY_ENABLED", "true").lower() == "true"
DISCORD_VOICE_TTS_REPLY = os.getenv("DISCORD_VOICE_TTS_REPLY", "true").lower() == "true"
DISCORD_VOICE_TRANSCRIPT_ECHO = os.getenv("DISCORD_VOICE_TRANSCRIPT_ECHO", "false").lower() == "true"
DISCORD_VOICE_FAST_ACK_ENABLED = os.getenv("DISCORD_VOICE_FAST_ACK_ENABLED", "true").lower() == "true"
DISCORD_VOICE_FAST_ACK_TTS = os.getenv("DISCORD_VOICE_FAST_ACK_TTS", "true").lower() == "true"
DISCORD_VOICE_BACKGROUND_LOOKUPS = os.getenv("DISCORD_VOICE_BACKGROUND_LOOKUPS", "true").lower() == "true"
DISCORD_VOICE_STATUS_TTS = os.getenv("DISCORD_VOICE_STATUS_TTS", "true").lower() == "true"
DISCORD_VOICE_REPLY_MAX_CHARS = max(120, min(int(os.getenv("DISCORD_VOICE_REPLY_MAX_CHARS", "220")), 900))
DISCORD_DEFAULT_MODE = os.getenv("DISCORD_DEFAULT_MODE", "engaged").strip().lower()
DISCORD_EVENT_TIMEZONE = os.getenv("DISCORD_EVENT_TIMEZONE", "America/New_York").strip() or "America/New_York"
DISCORD_EVENT_TEMPLATE_CHECK_INTERVAL_SEC = float(os.getenv("DISCORD_EVENT_TEMPLATE_CHECK_INTERVAL_SEC", "900"))
DISCORD_CONTINUOUS_STEWARD_ENABLED = os.getenv("DISCORD_CONTINUOUS_STEWARD_ENABLED", "false").lower() == "true"
DISCORD_CONTINUOUS_STEWARD_INTERVAL_SEC = max(300.0, float(os.getenv("DISCORD_CONTINUOUS_STEWARD_INTERVAL_SEC", "1800") or 1800))
OLYMPUS_SITE_URL = (os.getenv("OLYMPUS_SITE_URL", "") or "").strip().rstrip("/")
PAPR_SITE_URL = (os.getenv("PAPR_SITE_URL", "") or "").strip().rstrip("/")
UNDERFOOT_SITE_URL = (os.getenv("UNDERFOOT_SITE_URL", "") or "").strip().rstrip("/")
UNDERFOOT_BRIDGE_URL = (os.getenv("UNDERFOOT_BRIDGE_URL", "") or "").strip().rstrip("/")
UNDERFOOT_BRIDGE_TOKEN = (os.getenv("UNDERFOOT_BRIDGE_TOKEN", "") or "").strip()
UNDERFOOT_BRIDGE_TIMEOUT_SEC = float(os.getenv("UNDERFOOT_BRIDGE_TIMEOUT_SEC", "300"))
MYSQL_HOST = os.getenv("MYSQL_HOST", "mysql")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "luminax")
MYSQL_USER = (os.getenv("MYSQL_USER", "") or "").strip()
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
DISCORD_ARCHIVE_RETENTION_DAYS = max(30, int(os.getenv("DISCORD_ARCHIVE_RETENTION_DAYS", "180") or 180))
DISCORD_ARCHIVE_COMPACT_INTERVAL_SEC = max(600.0, float(os.getenv("DISCORD_ARCHIVE_COMPACT_INTERVAL_SEC", "3600") or 3600))
DISCORD_PROJECT_CATEGORY_PREFIX = (os.getenv("DISCORD_PROJECT_CATEGORY_PREFIX", "Projects") or "Projects").strip() or "Projects"
DISCORD_STATUS_PUBLIC_PREFIX = (os.getenv("DISCORD_STATUS_PUBLIC_PREFIX", DISCORD_CONNECTOR_NAME) or DISCORD_CONNECTOR_NAME).strip() or DISCORD_CONNECTOR_NAME
DISCORD_LINK_HELP_TEXT = (os.getenv("DISCORD_LINK_HELP_TEXT", "Link your Mount Olympus identity inside welcome2olympus and Discord to unlock Olympus project access.") or "Link your Mount Olympus identity inside welcome2olympus and Discord to unlock Olympus project access.").strip()
DISCORD_UI_ACTIVITY_URL = (os.getenv("DISCORD_UI_ACTIVITY_URL", "") or "").strip().rstrip("/")
DISCORD_PAIRING_MAX_ATTEMPTS = max(1, min(int(os.getenv("DISCORD_PAIRING_MAX_ATTEMPTS", "3") or 3), 10))
DISCORD_PAIRING_REQUEST_COOLDOWN_SEC = max(1.0, float(os.getenv("DISCORD_PAIRING_REQUEST_COOLDOWN_SEC", "2.0") or 2.0))
DISCORD_SLASH_SYNC_ON_BOOT = os.getenv("DISCORD_SLASH_SYNC_ON_BOOT", "true").lower() == "true"
DISCORD_SLASH_SYNC_MIN_INTERVAL_SEC = max(900.0, float(os.getenv("DISCORD_SLASH_SYNC_MIN_INTERVAL_SEC", "21600") or 21600))
DISCORD_INVITE_PERMISSIONS = str(os.getenv("DISCORD_INVITE_PERMISSIONS", "8") or "8").strip() or "8"
DISCORD_INVITE_SCOPES = [part.strip() for part in (os.getenv("DISCORD_INVITE_SCOPES", "bot applications.commands") or "bot applications.commands").replace(",", " ").split() if part.strip()]
DISCORD_RUNTIME_STATE_PATH = Path(
    os.getenv(
        "DISCORD_RUNTIME_STATE_PATH",
        os.path.join(os.getenv("MEMORY_ROOT", "/data/memory"), "discord", "connector-state.json"),
    )
)
DISCORD_CHANNEL_MODES_PATH = Path(
    os.getenv("DISCORD_CHANNEL_MODES_PATH", "/workspace/luminax/config/discord-channel-modes.json")
)
OLYMPUS_IDENTITY_PATH = Path(os.getenv("OLYMPUS_IDENTITY_PATH", "/workspace/luminax/config/olympus-identity.json"))
DISCORD_OPS_BLUEPRINTS_PATH = Path(os.getenv("DISCORD_OPS_BLUEPRINTS_PATH", "/workspace/luminax/config/discord-ops-blueprints.json"))
DISCORD_RUNTIME_DIAGNOSTICS_PATH = Path(
    os.getenv(
        "DISCORD_RUNTIME_DIAGNOSTICS_PATH",
        str(DISCORD_RUNTIME_STATE_PATH.with_name("runtime-diagnostics.jsonl")),
    )
)
DISCORD_MODERATION_RECEIPTS_PATH = Path(
    os.getenv(
        "DISCORD_MODERATION_RECEIPTS_PATH",
        str(DISCORD_RUNTIME_STATE_PATH.with_name("moderation-receipts.jsonl")),
    )
)
WORKSPACE_ROOT_PATH = Path(os.getenv("WORKSPACE_ROOT", "/workspace/luminax"))
PAIR_CODE_RE = re.compile(r"\b(\d{6})\b")
RUNTIME_INSTANCE_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
DARIUS_BACKED_PROJECT_KEYS = {
    normalize.strip().lower()
    for normalize in (os.getenv("DARIUS_BACKED_PROJECT_KEYS", "mount-olympus-web,underfoot") or "mount-olympus-web,underfoot").split(",")
    if normalize.strip()
}

def _read_json_path(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def _read_olympus_identity() -> dict[str, Any]:
    try:
        if OLYMPUS_IDENTITY_PATH.exists():
            data = json.loads(OLYMPUS_IDENTITY_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def olympus_ui_name() -> str:
    identity = _read_olympus_identity()
    return (
        str(identity.get("control_surface_name") or identity.get("ui_name") or UI_BRAND_NAME).strip()
        or UI_BRAND_NAME
    )

def olympus_operator_name() -> str:
    identity = _read_olympus_identity()
    return str(identity.get("operator_name") or MAIN_CONNECTOR_NAME).strip() or MAIN_CONNECTOR_NAME

def olympus_discord_name() -> str:
    identity = _read_olympus_identity()
    service_labels = identity.get("service_labels") if isinstance(identity.get("service_labels"), dict) else {}
    row = service_labels.get(DISCORD_SERVICE_LABEL_KEY) if isinstance(service_labels.get(DISCORD_SERVICE_LABEL_KEY), dict) else {}
    return str(row.get("public_name") or row.get("display_name") or DISCORD_CONNECTOR_NAME).strip() or DISCORD_CONNECTOR_NAME

def olympus_public_brand() -> str:
    identity = _read_olympus_identity()
    return str(identity.get("public_brand") or "").strip()

def olympus_link_help_text() -> str:
    public_brand = olympus_public_brand()
    if public_brand:
        return f"Link your {olympus_ui_name()} identity in {public_brand} and Discord to unlock Olympus project access."
    return f"Link your {olympus_ui_name()} identity and Discord to unlock Olympus project access."

def olympus_text_aliases() -> list[str]:
    aliases = {
        olympus_discord_name().strip().lower(),
        olympus_operator_name().strip().lower(),
        olympus_ui_name().strip().lower(),
        "hermes",
        "zeus",
        "olympus",
        "mountolympus",
        "mount olympus",
        "hermes ops",
        "welcome2olympus",
        "zeus ops",
        "mount olympus ops",
    }
    return sorted([alias for alias in aliases if alias])

TEXT_NAME_ALIASES = olympus_text_aliases()
DISCORD_LINK_HELP_TEXT = (os.getenv("DISCORD_LINK_HELP_TEXT", olympus_link_help_text()) or olympus_link_help_text()).strip()


def load_discord_ops_blueprints_payload() -> dict[str, Any]:
    return _read_json_path(DISCORD_OPS_BLUEPRINTS_PATH)


def load_discord_channel_modes_payload() -> dict[str, Any]:
    return _read_json_path(DISCORD_CHANNEL_MODES_PATH)


def discord_ops_blueprint(blueprint_key: str = "", guild_name: str = "", project_key: str = "") -> dict[str, Any]:
    payload = load_discord_ops_blueprints_payload()
    templates = payload.get("templates") if isinstance(payload.get("templates"), list) else []
    normalized_key = str(blueprint_key or "").strip().lower()
    normalized_guild = str(guild_name or "").strip().lower()
    normalized_project = normalize_project_key_local(project_key)
    for row in templates:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip().lower()
        aliases = [str(alias or "").strip().lower() for alias in row.get("aliases", [])] if isinstance(row.get("aliases"), list) else []
        if normalized_key and (key == normalized_key or normalized_key in aliases):
            return row
    for row in templates:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip().lower()
        public_guild = str(row.get("guild_name") or "").strip().lower()
        if normalized_guild and normalized_guild in {title, public_guild}:
            return row
    for row in templates:
        if not isinstance(row, dict):
            continue
        current_project = normalize_project_key_local(row.get("project_key") or "")
        if normalized_project and current_project == normalized_project:
            return row
    return {}


def default_blueprint_key_for_guild(guild: Optional[discord.Guild]) -> str:
    blueprint = discord_ops_blueprint(guild_name=(guild.name if guild else ""))
    return str(blueprint.get("key") or "").strip()


async def sync_live_discord_identity() -> None:
    desired_name = olympus_discord_name()
    if not bot.user:
        return
    if desired_name and bot.user.name != desired_name:
        try:
            await bot.user.edit(username=desired_name)
            print(f"[discord-identity] username synced to {desired_name}")
        except Exception as exc:
            print(f"[discord-identity] username sync skipped: {exc}")
    try:
        await bot.change_presence(activity=discord.Game(name=f"Serving {olympus_ui_name()}"))
    except Exception as exc:
        print(f"[discord-identity] presence sync skipped: {exc}")
    for guild in bot.guilds:
        me = guild.me or guild.get_member(bot.user.id)
        if me is None:
            continue
        try:
            if desired_name and me.nick != desired_name and getattr(me.guild_permissions, "change_nickname", False):
                await me.edit(nick=desired_name[:32], reason="Hermes identity sync")
                print(f"[discord-identity] nickname synced to {desired_name} in guild={guild.name}")
        except Exception as exc:
            print(f"[discord-identity] nickname sync skipped guild={guild.name}: {exc}")

guild_voice_prefs: dict[int, str] = {}
guild_sessions: dict[int, "GuildVoiceSession"] = {}
discord_codex_sessions: dict[str, str] = {}
text_channel_state: dict[object, dict] = {}
guild_modes: dict[int, str] = {}
guild_project_plans: dict[int, dict] = {}
active_polls: dict[int, dict[str, Any]] = {}
active_raffles: dict[int, dict[str, Any]] = {}
active_trivia: dict[int, dict[str, Any]] = {}
admin_loop_task: Optional[asyncio.Task] = None
leader_lease_task: Optional[asyncio.Task] = None
http_client: Optional[httpx.AsyncClient] = None
http_client_lock = asyncio.Lock()
last_event_template_sync_ts = 0.0
last_archive_compaction_ts = 0.0
last_continuous_steward_ts = 0.0
discord_runtime_breadcrumbs: list[dict[str, Any]] = []
pairing_request_cooldowns: dict[int, float] = {}
incident_sync_backoff_until = 0.0
incident_sync_backoff_sec = 0.0
incident_sync_last_error = ""
incident_sync_last_ok_ts = 0.0
discord_runtime_is_leader = False
discord_message_policy_cache: dict[str, dict[str, Any]] = {}
discord_seen_messages_local: dict[str, float] = {}
discord_runtime_client: Optional[redis.Redis] = None
POLL_EMOJIS = ["\u0031\ufe0f\u20e3", "\u0032\ufe0f\u20e3", "\u0033\ufe0f\u20e3", "\u0034\ufe0f\u20e3", "\u0035\ufe0f\u20e3", "\u0036\ufe0f\u20e3", "\u0037\ufe0f\u20e3", "\u0038\ufe0f\u20e3"]
RAFFLE_EMOJI = "\U0001f39f\ufe0f"
TRIVIA_OPTIONS = ["A", "B", "C", "D"]
TRIVIA_BANK = [
    {"q": "Which protocol secures web traffic?", "options": ["FTP", "HTTPS", "SMTP", "SNMP"], "answer": 1},
    {"q": "What does SQL stand for?", "options": ["Structured Query Language", "Simple Queue List", "Secure Query Link", "System Query Logic"], "answer": 0},
    {"q": "Which is a vector database used in Mount Olympus?", "options": ["MongoDB", "Qdrant", "SQLite", "Neo4j"], "answer": 1},
    {"q": "What is Docker Compose primarily used for?", "options": ["Image editing", "Container orchestration by service definitions", "Encrypting files", "Routing DNS"], "answer": 1},
    {"q": "Which model type is best for speech-to-text?", "options": ["Whisper", "Redis", "Nginx", "Qdrant"], "answer": 0},
    {"q": "What does CI/CD automate?", "options": ["Continuous integration and delivery/deployment", "Credit issuing and card distribution", "Central identity and customer data", "Content indexing and copy design"], "answer": 0},
]

OWNER_ONLY_COMMANDS = {
    "selfupdate",
    "discordmode",
    "welcome",
    "testertoken",
    "joinvc",
    "leavevc",
    "vcdiag",
    "vcclear",
    "voice",
    "announcefeature",
}
SERVER_OPERATOR_COMMANDS = {
    "paprsetup",
    "paprevents",
    "underfootsetup",
    "underfootevents",
    "guildsync",
    "communitysync",
    "communityannounce",
    "patchnotes",
    "revampsync",
    "incidentack",
    "incidentresolve",
    "poll",
    "pollclose",
    "raffle",
    "raffleclose",
    "feedbackreview",
    "interview",
    "interviewclose",
    "onboard",
    "recruitpost",
    "purge",
    "purgehermes",
    "testerlink",
    "testerstatus",
    "testerdelete",
    "testerlist",
    "testersummary",
}
PROJECT_SCOPED_COMMANDS = {
    "projectplan",
    "projectapply",
    "projectsync",
    "codex",
    "revamptest",
    "websitecreate",
    "image",
    "logo",
    "underfootbridge",
    "underfootunity",
    "underfootdariusroute",
    "underfootdariussession",
    "underfootscreenshot",
}
DM_ONLY_COMMANDS = {"testertoken", "announcefeature"}


def _redis_client() -> Optional[redis.Redis]:
    global discord_runtime_client
    if discord_runtime_client is None:
        try:
            discord_runtime_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        except Exception as exc:
            print(f"[discord-runtime] redis client unavailable: {exc}")
            discord_runtime_client = None
    return discord_runtime_client


def _message_runtime_key(message: discord.Message) -> str:
    guild_id = getattr(message.guild, "id", 0) if getattr(message, "guild", None) else 0
    channel_id = getattr(message.channel, "id", 0)
    return f"{guild_id}:{channel_id}:{getattr(message, 'id', 0)}"


def _cleanup_local_message_cache(now_ts: Optional[float] = None) -> None:
    now_val = now_ts if now_ts is not None else time.time()
    expired = [
        key
        for key, seen_ts in discord_seen_messages_local.items()
        if now_val - float(seen_ts or 0.0) > float(DISCORD_MESSAGE_DEDUPE_TTL_SEC)
    ]
    for key in expired:
        discord_seen_messages_local.pop(key, None)


def _policy_cache_key_for_message(message: discord.Message, requested_project_key: str = "", command_key: str = "") -> str:
    return f"{_message_runtime_key(message)}::{_policy_cache_key(requested_project_key, command_key)}"


def _cached_message_policy(message: discord.Message, requested_project_key: str = "", command_key: str = "") -> dict[str, Any]:
    cache_key = _policy_cache_key_for_message(message, requested_project_key=requested_project_key, command_key=command_key)
    cached = discord_message_policy_cache.get(cache_key)
    return dict(cached) if isinstance(cached, dict) else {}


def _store_message_policy(message: discord.Message, policy: dict[str, Any], requested_project_key: str = "", command_key: str = "") -> None:
    cache_key = _policy_cache_key_for_message(message, requested_project_key=requested_project_key, command_key=command_key)
    discord_message_policy_cache[cache_key] = dict(policy or {})


def refresh_discord_leader_lease() -> bool:
    if DISCORD_RUNTIME_ROLE != "primary":
        return False
    client = _redis_client()
    if client is None:
        print("[discord-runtime] leader lease unavailable: redis client missing; standing by")
        return False
    try:
        claimed = client.set(DISCORD_LEADER_LEASE_KEY, RUNTIME_INSTANCE_ID, nx=True, ex=DISCORD_LEADER_LEASE_TTL_SEC)
        holder = str(client.get(DISCORD_LEADER_LEASE_KEY) or "").strip()
        if claimed or holder == RUNTIME_INSTANCE_ID:
            client.expire(DISCORD_LEADER_LEASE_KEY, DISCORD_LEADER_LEASE_TTL_SEC)
            return True
        return False
    except Exception as exc:
        print(f"[discord-runtime] leader lease unavailable: {exc}; standing by")
        return False


async def maintain_discord_leader_lease() -> None:
    global discord_runtime_is_leader
    while True:
        current = refresh_discord_leader_lease()
        if current != discord_runtime_is_leader:
            print(f"[discord-runtime] leader={'active' if current else 'standby'} role={DISCORD_RUNTIME_ROLE} instance={RUNTIME_INSTANCE_ID}")
        discord_runtime_is_leader = current
        await asyncio.sleep(max(5.0, float(DISCORD_LEADER_LEASE_TTL_SEC) / 3.0))


def discord_runtime_allows_processing() -> bool:
    if DISCORD_RUNTIME_ROLE != "primary":
        return False
    return bool(discord_runtime_is_leader or refresh_discord_leader_lease())


def claim_message_delivery_slot(message: discord.Message) -> bool:
    now_ts = time.time()
    _cleanup_local_message_cache(now_ts)
    dedupe_key = _message_runtime_key(message)
    if dedupe_key in discord_seen_messages_local:
        return False
    client = _redis_client()
    if client is None:
        print(f"[discord-runtime] message dedupe unavailable for {dedupe_key}: redis client missing; standing by")
        return False
    try:
        claimed = client.set(
            f"discord:processed_message:{dedupe_key}",
            RUNTIME_INSTANCE_ID,
            nx=True,
            ex=DISCORD_MESSAGE_DEDUPE_TTL_SEC,
        )
        if claimed:
            discord_seen_messages_local[dedupe_key] = now_ts
            return True
        return False
    except Exception as exc:
        print(f"[discord-runtime] message dedupe unavailable for {dedupe_key}: {exc}; standing by")
        return False


def db_conn():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
    )


def owner_discord_ids() -> tuple[int, ...]:
    return OWNER_DISCORD_IDS


def primary_owner_discord_id() -> int:
    return OWNER_DISCORD_IDS[0] if OWNER_DISCORD_IDS else 0


async def fetch_owner_members_for_guild(guild: discord.Guild) -> list[discord.Member]:
    members: list[discord.Member] = []
    seen: set[int] = set()
    for owner_id in owner_discord_ids():
        if owner_id <= 0 or owner_id in seen:
            continue
        seen.add(owner_id)
        member = guild.get_member(owner_id)
        if member is None:
            try:
                member = await guild.fetch_member(owner_id)
            except Exception:
                member = None
        if member is not None:
            members.append(member)
    return members


async def resolve_owner_users() -> list[discord.User]:
    users: list[discord.User] = []
    seen: set[int] = set()
    for owner_id in owner_discord_ids():
        if owner_id <= 0 or owner_id in seen:
            continue
        seen.add(owner_id)
        owner_user = bot.get_user(owner_id)
        if owner_user is None:
            try:
                owner_user = await bot.fetch_user(owner_id)
            except Exception:
                owner_user = None
        if owner_user is not None:
            users.append(owner_user)
    return users


def is_owner_member(member: Optional[discord.Member]) -> bool:
    return member is not None and getattr(member, "id", 0) in owner_discord_ids()


def is_admin_member(member: Optional[discord.Member]) -> bool:
    if member is None:
        return False
    if is_owner_member(member):
        return True
    perms = getattr(member, "guild_permissions", None)
    if perms is None:
        return False
    return bool(perms.administrator or perms.manage_guild or perms.manage_channels or perms.manage_roles)


def is_admin_ctx(ctx) -> bool:
    if is_owner(ctx):
        return True
    policy = getattr(ctx, "_luminax_policy", {}) if ctx else {}
    return bool(policy.get("server_operator") or policy.get("can_command"))


def normalize_project_key_local(value: str) -> str:
    cleaned = "".join(ch.lower() for ch in str(value or "").strip() if ch.isalnum() or ch in "-_")
    return cleaned.strip("-_")[:80]


def pretty_project_label(value: str) -> str:
    cleaned = normalize_project_key_local(value).replace("-", " ").replace("_", " ").strip()
    return cleaned.title() if cleaned else "Project"


def parse_pipe_parts(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").split("|") if p.strip()]


def _safe_int(raw: str, fallback: int) -> int:
    try:
        return int(str(raw).strip())
    except Exception:
        return fallback


def ensure_discord_admin_tables():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS discord_feedback (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          guild_id VARCHAR(64) NULL,
          channel_id VARCHAR(64) NULL,
          user_id VARCHAR(64) NULL,
          user_name VARCHAR(255) NULL,
          feedback_type VARCHAR(32) NOT NULL DEFAULT 'general',
          content LONGTEXT NOT NULL,
          status VARCHAR(32) NOT NULL DEFAULT 'open',
          meta_json JSON NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS discord_polls (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          guild_id VARCHAR(64) NULL,
          channel_id VARCHAR(64) NULL,
          message_id VARCHAR(64) NOT NULL,
          question TEXT NOT NULL,
          options_json JSON NULL,
          status VARCHAR(32) NOT NULL DEFAULT 'open',
          closes_at BIGINT NULL,
          result_json JSON NULL,
          UNIQUE KEY uq_discord_poll_message (message_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS discord_raffles (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          guild_id VARCHAR(64) NULL,
          channel_id VARCHAR(64) NULL,
          message_id VARCHAR(64) NOT NULL,
          prize TEXT NOT NULL,
          winners INT NOT NULL DEFAULT 1,
          status VARCHAR(32) NOT NULL DEFAULT 'open',
          closes_at BIGINT NULL,
          entrants_json JSON NULL,
          result_json JSON NULL,
          UNIQUE KEY uq_discord_raffle_message (message_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS discord_interviews (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          guild_id VARCHAR(64) NULL,
          channel_id VARCHAR(64) NULL,
          candidate_id VARCHAR(64) NOT NULL,
          candidate_name VARCHAR(255) NOT NULL,
          requested_by VARCHAR(64) NULL,
          role_name VARCHAR(255) NULL,
          notes LONGTEXT NULL,
          status VARCHAR(32) NOT NULL DEFAULT 'open',
          decision VARCHAR(64) NULL,
          meta_json JSON NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS discord_trivia_scores (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          guild_id VARCHAR(64) NOT NULL,
          user_id VARCHAR(64) NOT NULL,
          user_name VARCHAR(255) NULL,
          score INT NOT NULL DEFAULT 0,
          correct_count INT NOT NULL DEFAULT 0,
          UNIQUE KEY uq_discord_trivia_score (guild_id, user_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS discord_event_templates (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          guild_id VARCHAR(64) NOT NULL,
          template_key VARCHAR(128) NOT NULL,
          name VARCHAR(255) NOT NULL,
          description TEXT NULL,
          frequency VARCHAR(32) NOT NULL DEFAULT 'weekly',
          weekday INT NULL,
          start_hour INT NOT NULL DEFAULT 12,
          start_minute INT NOT NULL DEFAULT 0,
          duration_min INT NOT NULL DEFAULT 30,
          channel_name VARCHAR(128) NULL,
          location VARCHAR(255) NULL,
          status VARCHAR(32) NOT NULL DEFAULT 'active',
          meta_json JSON NULL,
          UNIQUE KEY uq_discord_event_template (guild_id, template_key)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS discord_message_archive (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          message_created_at BIGINT NULL,
          expires_at DATETIME NOT NULL,
          message_id VARCHAR(64) NOT NULL,
          guild_id VARCHAR(64) NULL,
          channel_id VARCHAR(64) NULL,
          channel_name VARCHAR(255) NULL,
          project_key VARCHAR(128) NULL,
          discord_user_id VARCHAR(64) NOT NULL,
          discord_name VARCHAR(255) NULL,
          portal_user_id VARCHAR(128) NULL,
          surface VARCHAR(32) NOT NULL DEFAULT 'public_channel',
          content LONGTEXT NOT NULL,
          meta_json JSON NULL,
          UNIQUE KEY uq_discord_message_archive (message_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS discord_user_stats (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          discord_user_id VARCHAR(64) NOT NULL,
          portal_user_id VARCHAR(128) NULL,
          discord_name VARCHAR(255) NULL,
          first_seen_at BIGINT NULL,
          last_seen_at BIGINT NULL,
          message_count BIGINT NOT NULL DEFAULT 0,
          dm_count BIGINT NOT NULL DEFAULT 0,
          guild_count BIGINT NOT NULL DEFAULT 0,
          active_hours_json JSON NULL,
          active_weekdays_json JSON NULL,
          top_channels_json JSON NULL,
          recent_projects_json JSON NULL,
          usual_windows_json JSON NULL,
          UNIQUE KEY uq_discord_user_stats (discord_user_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS discord_user_quotes (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          discord_user_id VARCHAR(64) NOT NULL,
          portal_user_id VARCHAR(128) NULL,
          quote_text TEXT NOT NULL,
          source_message_id VARCHAR(64) NULL,
          source_created_at BIGINT NULL,
          quote_score DECIMAL(8,3) NOT NULL DEFAULT 0,
          status VARCHAR(32) NOT NULL DEFAULT 'saved',
          meta_json JSON NULL,
          UNIQUE KEY uq_discord_quote_source (discord_user_id, source_message_id)
        )
        """
    )
    conn.commit()
    cur.close()
    conn.close()


def trivia_score_upsert(guild_id: int, user_id: int, user_name: str):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO discord_trivia_scores (guild_id, user_id, user_name, score, correct_count)
        VALUES (%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          user_name=VALUES(user_name),
          score=score+1,
          correct_count=correct_count+1
        """,
        (str(guild_id), str(user_id), user_name[:255], 1, 1),
    )
    conn.commit()
    cur.close()
    conn.close()


def trivia_leaderboard(guild_id: int, limit: int = 10) -> list[dict[str, Any]]:
    conn = db_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT user_id, user_name, score, correct_count, updated_at FROM discord_trivia_scores WHERE guild_id=%s ORDER BY score DESC, updated_at ASC LIMIT %s",
        (str(guild_id), max(1, min(limit, 50))),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def is_owner(ctx) -> bool:
    return is_owner_member(getattr(ctx, "author", None))


def is_dm(message: discord.Message) -> bool:
    return isinstance(message.channel, discord.DMChannel)

def guild_mode(guild: Optional[discord.Guild]) -> str:
    if guild is None:
        return "engaged"
    return guild_modes.get(guild.id, DISCORD_DEFAULT_MODE)


def channel_label(message: discord.Message) -> str:
    if is_dm(message):
        return f"dm:{message.author.id}"
    return f"#{getattr(message.channel, 'name', 'unknown')}"

def channel_trace_meta(message: discord.Message) -> dict[str, Any]:
    author_name = getattr(message.author, "display_name", getattr(message.author, "name", "user"))
    if is_dm(message):
        room_key = f"discord:dm:{getattr(message.author, 'id', 0)}"
        room_label = f"Discord DM with {author_name}"
        return {
            "room_key": room_key,
            "room_label": room_label,
            "room_kind": "discord_dm",
            "session_id": room_key,
            "speaker": author_name,
            "surface": "discord",
            "channel_id": getattr(message.channel, "id", 0),
            "channel_name": "dm",
            "channel_label": channel_label(message),
            "author_id": getattr(message.author, "id", 0),
        }
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    guild_name = getattr(guild, "name", "Discord")
    channel_name = getattr(channel, "name", "channel")
    room_kind = "discord_thread" if isinstance(channel, discord.Thread) else "discord"
    room_key = f"discord:{getattr(guild, 'id', 0)}:{getattr(channel, 'id', 0)}"
    room_label = f"{guild_name} / #{channel_name}"
    parent = getattr(channel, "parent", None)
    if isinstance(channel, discord.Thread) and getattr(parent, "name", ""):
        room_label = f"{guild_name} / #{getattr(parent, 'name', 'thread')} / {channel_name}"
    return {
        "room_key": room_key,
        "room_label": room_label,
        "room_kind": room_kind,
        "session_id": room_key,
        "speaker": author_name,
        "surface": "discord",
        "guild_id": getattr(guild, "id", 0),
        "guild_name": guild_name,
        "channel_id": getattr(channel, "id", 0),
        "channel_name": channel_name,
        "channel_label": channel_label(message),
        "author_id": getattr(message.author, "id", 0),
    }


def channel_state(channel_id) -> dict:
    state = text_channel_state.get(channel_id)
    if not state:
        state = {
            "last_reply_ts": 0.0,
            "last_fast_ack_ts": 0.0,
            "last_user_id": None,
            "last_topic": "",
            "last_bot_reply": "",
            "engaged_until": 0.0,
        }
        text_channel_state[channel_id] = state
    return state


def _json_mysql_value(value: Any, default: Any):
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return default
    try:
        parsed = json.loads(text)
        return parsed if parsed is not None else default
    except Exception:
        return default


def _public_status_text(text: str) -> str:
    cleaned = compact_discord_text(text, 220)
    return f"{DISCORD_STATUS_PUBLIC_PREFIX}: {cleaned}" if cleaned else f"{DISCORD_STATUS_PUBLIC_PREFIX}: working"


def _project_safe_completion_text(dm_sent: bool = True) -> str:
    return "Task completed. Detailed update sent to DM." if dm_sent else f"Task completed. DM delivery failed, so check {UI_BRAND_NAME}."


def _project_blocked_text(reason: str = "") -> str:
    base = "Blocked for now."
    if reason:
        base += " " + compact_discord_text(reason, 160)
    return base


@dataclass
class HermesIntent:
    intent_kind: str = "conversation"
    action_kind: str = "conversation"
    goal: str = ""
    surface: str = "public_channel"
    priority: str = "immediate"
    resolved_project_key: str = ""
    attachments: list[str] = field(default_factory=list)
    media_request: dict[str, Any] = field(default_factory=dict)
    requires_operator: bool = False
    correlation_id: str = ""
    job_id: str = ""


@dataclass
class HermesRouteDecision:
    executor_kind: str = "conversation"
    resolved_project_key: str = ""
    route_status: str = ""
    route_mode: str = ""
    review_action_kind: str = "conversation"
    requires_review: bool = False
    route_detail: str = ""


@dataclass
class HermesVerificationResult:
    status: str = "pending"
    summary: str = ""
    details: str = ""
    artifact_paths: list[str] = field(default_factory=list)
    touched_targets: list[str] = field(default_factory=list)


@dataclass
class HermesActionPlan:
    summary: str = ""
    plan_steps: list[str] = field(default_factory=list)
    executor_hint: str = ""
    requested_action: str = "implement"
    project_key_override: str = ""
    risk: str = "medium"
    requires_confirmation: bool = False
    public_ack: str = ""
    private_note: str = ""


def _policy_cache_key(project_key: str = "", command_key: str = "") -> str:
    return f"{normalize_project_key_local(project_key)}::{str(command_key or '').strip().lower()}"


async def fetch_discord_channel_binding(message: discord.Message, project_key: str = "") -> dict[str, Any]:
    if is_dm(message):
        return {}
    try:
        data = await core_get(
            "/discord/channel-bindings/resolve",
            {
                "token": CONTROL_TOKEN,
                "guild_id": getattr(message.guild, "id", 0),
                "channel_id": getattr(message.channel, "id", 0),
                "project_key": normalize_project_key_local(project_key),
            },
        )
    except Exception:
        return {}
    binding = data.get("binding", {}) if isinstance(data, dict) else {}
    return binding if isinstance(binding, dict) else {}


async def fetch_project_access_rows(project_key: str) -> list[dict[str, Any]]:
    normalized = normalize_project_key_local(project_key)
    if not normalized:
        return []
    try:
        data = await core_get("/discord/project-access", {"token": CONTROL_TOKEN, "project_key": normalized})
    except Exception:
        return []
    rows = data.get("rows", []) if isinstance(data, dict) else []
    return [row for row in rows if isinstance(row, dict)]


async def upsert_discord_channel_binding_core(guild: discord.Guild, project_key: str, category: Optional[discord.CategoryChannel], text_channel: Optional[discord.TextChannel], voice_channel: Optional[discord.VoiceChannel], visibility_mode: str = "project_sanitized") -> dict[str, Any]:
    normalized = normalize_project_key_local(project_key)
    if not normalized:
        return {}
    try:
        data = await core_post(
            "/discord/channel-bindings/upsert",
            {
                "token": CONTROL_TOKEN,
                "guild_id": str(guild.id),
                "project_key": normalized,
                "category_id": str(getattr(category, "id", "") or ""),
                "category_name": getattr(category, "name", "") or "",
                "text_channel_id": str(getattr(text_channel, "id", "") or ""),
                "text_channel_name": getattr(text_channel, "name", "") or "",
                "voice_channel_id": str(getattr(voice_channel, "id", "") or ""),
                "voice_channel_name": getattr(voice_channel, "name", "") or "",
                "visibility_mode": visibility_mode,
                "status": "active",
                "meta": {"managed_by": "discord-bot"},
            },
        )
    except Exception as e:
        print(f"[discord-policy] binding upsert failed: {e}")
        return {}
    binding = data.get("binding", {}) if isinstance(data, dict) else {}
    return binding if isinstance(binding, dict) else {}

async def resolve_discord_community_guild_core(project_key: str = "", guild: Optional[discord.Guild] = None) -> dict[str, Any]:
    try:
        data = await core_get(
            "/discord/community-guilds/resolve",
            {
                "token": CONTROL_TOKEN,
                "project_key": normalize_project_key_local(project_key),
                "guild_id": str(getattr(guild, "id", 0) or ""),
            },
        )
    except Exception as exc:
        print(f"[discord-policy] community guild resolve failed: {exc}")
        return {}
    row = data.get("guild", {}) if isinstance(data, dict) else {}
    return row if isinstance(row, dict) else {}

async def fetch_discord_community_packet(project_key: str, *, guild: Optional[discord.Guild] = None, mode: str = "preview") -> dict[str, Any]:
    try:
        data = await core_post(
            "/discord/community-guilds/packet",
            {
                "token": CONTROL_TOKEN,
                "project_key": normalize_project_key_local(project_key),
                "guild_id": str(getattr(guild, "id", 0) or ""),
                "guild_name": str(getattr(guild, "name", "") or ""),
                "mode": mode,
            },
        )
    except Exception as exc:
        print(f"[discord-policy] community packet failed: {exc}")
        return {}
    packet = data.get("packet", {}) if isinstance(data, dict) else {}
    return packet if isinstance(packet, dict) else {}

async def fetch_discord_community_validation(project_key: str, *, guild: Optional[discord.Guild] = None) -> dict[str, Any]:
    try:
        data = await core_get(
            "/discord/community-guilds/validate",
            {
                "token": CONTROL_TOKEN,
                "project_key": normalize_project_key_local(project_key),
                "guild_id": str(getattr(guild, "id", 0) or ""),
                "guild_name": str(getattr(guild, "name", "") or ""),
            },
        )
    except Exception as exc:
        print(f"[discord-policy] community validation failed: {exc}")
        return {}
    validation = data.get("validation", {}) if isinstance(data, dict) else {}
    return validation if isinstance(validation, dict) else {}

def _candidate_olympus_guild_names() -> set[str]:
    blueprint = discord_ops_blueprint(blueprint_key="mount_olympus_control_guild")
    names = {
        str(blueprint.get("guild_name") or "").strip().lower(),
        str(blueprint.get("title") or "").strip().lower(),
    }
    aliases = blueprint.get("aliases") if isinstance(blueprint.get("aliases"), list) else []
    for alias in aliases:
        clean = str(alias or "").strip().lower()
        if clean:
            names.add(clean)
    return {name for name in names if name}

def resolve_olympus_mirror_channel_ref(packet: Optional[dict[str, Any]]) -> dict[str, str]:
    payload = packet if isinstance(packet, dict) else {}
    configured_id = str(payload.get("olympus_mirror_channel_id") or "").strip()
    if configured_id.isdigit():
        channel = bot.get_channel(int(configured_id))
        if isinstance(channel, discord.TextChannel):
            return {
                "guild_id": str(channel.guild.id),
                "guild_name": str(channel.guild.name),
                "channel_id": str(channel.id),
                "channel_name": str(channel.name),
            }
    mirror_name = str(payload.get("olympus_mirror_channel") or "").strip().lower()
    if not mirror_name:
        return {}
    preferred_guilds = _candidate_olympus_guild_names()
    candidates: list[tuple[int, discord.TextChannel]] = []
    for guild in bot.guilds:
        guild_name = str(getattr(guild, "name", "") or "").strip().lower()
        for channel in guild.text_channels:
            if str(getattr(channel, "name", "") or "").strip().lower() != mirror_name:
                continue
            score = 0
            if guild_name in preferred_guilds:
                score += 100
            category_name = str(getattr(getattr(channel, "category", None), "name", "") or "").strip().lower()
            if category_name.startswith(DISCORD_PROJECT_CATEGORY_PREFIX.strip().lower()):
                score += 20
            candidates.append((score, channel))
    if not candidates:
        return {}
    candidates.sort(key=lambda item: item[0], reverse=True)
    chosen = candidates[0][1]
    return {
        "guild_id": str(chosen.guild.id),
        "guild_name": str(chosen.guild.name),
        "channel_id": str(chosen.id),
        "channel_name": str(chosen.name),
    }

def resolve_text_channel_from_packet(
    guild: Optional[discord.Guild],
    packet: Optional[dict[str, Any]],
    *,
    channel_id_key: str,
    channel_name_key: str,
) -> Optional[discord.TextChannel]:
    if guild is None:
        return None
    payload = packet if isinstance(packet, dict) else {}
    channel_id = str(payload.get(channel_id_key) or "").strip()
    if channel_id.isdigit():
        resolved = guild.get_channel(int(channel_id))
        if isinstance(resolved, discord.TextChannel):
            return resolved
    channel_name = str(payload.get(channel_name_key) or "").strip().lower()
    if not channel_name:
        return None
    return discord.utils.get(guild.text_channels, name=channel_name)

async def upsert_discord_community_guild_core(
    guild: discord.Guild,
    project_key: str,
    packet: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload = packet if isinstance(packet, dict) else {}
    welcome_name = str(payload.get("welcome_channel") or "").strip().lower()
    announcement_name = str(payload.get("announcement_channel") or "").strip().lower()
    patch_notes_name = str(payload.get("patch_notes_channel") or "").strip().lower()
    feedback_name = str(payload.get("feedback_channel") or "").strip().lower()
    bugs_name = str(payload.get("bugs_channel") or "").strip().lower()
    events_name = str(payload.get("events_channel") or "").strip().lower()
    channel_map = {str(channel.name or "").strip().lower(): channel for channel in guild.text_channels}
    mirror_binding = resolve_olympus_mirror_channel_ref(payload)
    try:
        data = await core_post(
            "/discord/community-guilds/upsert",
            {
                "token": CONTROL_TOKEN,
                "project_key": normalize_project_key_local(project_key),
                "guild_id": str(guild.id),
                "guild_name": guild.name,
                "community_guild_status": str(payload.get("community_guild_status") or "live_public").strip() or "live_public",
                "blueprint_key": str(payload.get("community_guild_blueprint_key") or "").strip(),
                "community_bot_role": str(payload.get("community_bot_role") or "olympus_guide").strip() or "olympus_guide",
                "olympus_mirror_guild_id": str(mirror_binding.get("guild_id") or "").strip(),
                "olympus_mirror_channel_id": str(mirror_binding.get("channel_id") or payload.get("olympus_mirror_channel_id") or "").strip(),
                "public_summary_channel_id": str(getattr(channel_map.get(welcome_name), "id", "") or ""),
                "feedback_channel_id": str(getattr(channel_map.get(feedback_name), "id", "") or ""),
                "bugs_channel_id": str(getattr(channel_map.get(bugs_name), "id", "") or ""),
                "announcements_channel_id": str(getattr(channel_map.get(announcement_name), "id", "") or ""),
                "patch_notes_channel_id": str(getattr(channel_map.get(patch_notes_name), "id", "") or ""),
                "events_channel_id": str(getattr(channel_map.get(events_name), "id", "") or ""),
                "meta": {
                    "managed_by": DISCORD_SERVICE_LABEL_KEY,
                    "welcome_channel_name": welcome_name,
                    "announcement_channel_name": announcement_name,
                    "patch_notes_channel_name": patch_notes_name,
                    "feedback_channel_name": feedback_name,
                    "bugs_channel_name": bugs_name,
                    "events_channel_name": events_name,
                    "olympus_mirror_channel_name": str(mirror_binding.get("channel_name") or payload.get("olympus_mirror_channel") or "").strip(),
                    "olympus_mirror_guild_name": str(mirror_binding.get("guild_name") or "").strip(),
                },
            },
        )
    except Exception as exc:
        print(f"[discord-policy] community guild upsert failed: {exc}")
        return {}
    row = data.get("guild", {}) if isinstance(data, dict) else {}
    return row if isinstance(row, dict) else {}

async def upsert_discord_community_feedback_receipt_core(
    policy: Optional[dict[str, Any]],
    *,
    receipt_kind: str,
    summary: str,
    detail_text: str,
    message: Optional[discord.Message] = None,
    status: str = "open",
    meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    current_policy = policy if isinstance(policy, dict) else {}
    guild = getattr(message, "guild", None) if message is not None else None
    author = getattr(message, "author", None) if message is not None else None
    try:
        data = await core_post(
            "/discord/community-feedback/upsert",
            {
                "token": CONTROL_TOKEN,
                "project_key": str(current_policy.get("project_key") or default_project_key_from_guild(guild)).strip(),
                "guild_id": str(current_policy.get("guild_id") or getattr(guild, "id", 0) or ""),
                "channel_id": str(current_policy.get("channel_id") or getattr(getattr(message, "channel", None), "id", 0) or ""),
                "message_id": str(getattr(message, "id", 0) or ""),
                "discord_user_id": str(current_policy.get("discord_user_id") or current_policy.get("author_id") or getattr(author, "id", 0) or ""),
                "discord_name": str(getattr(author, "display_name", getattr(author, "name", "")) or ""),
                "portal_user_id": str(((current_policy.get("identity") or {}).get("portal_user_id") or "")).strip(),
                "receipt_kind": receipt_kind,
                "status": status,
                "summary": summary,
                "detail_text": detail_text,
                "mirror_channel_id": str(((current_policy.get("community_guild") or {}).get("olympus_mirror_channel_id") or "")).strip(),
                "meta": meta or {},
            },
        )
    except Exception as exc:
        print(f"[discord-policy] community feedback upsert failed: {exc}")
        return {}
    row = data.get("receipt", {}) if isinstance(data, dict) else {}
    return row if isinstance(row, dict) else {}


async def upsert_discord_job_core(policy: dict[str, Any], *, job_id: str, status: str, public_summary: str = "", private_summary: str = "", dm_status: str = "", detail_text: str = "", command_key: str = "", intent_kind: str = "", executor_kind: str = "", resolved_project_key: str = "", route_status: str = "", verification_status: str = "", correlation_id: str = "", meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    try:
        data = await core_post(
            "/discord/jobs/upsert",
            {
                "token": CONTROL_TOKEN,
                "job_id": job_id,
                "project_key": policy.get("project_key") or "",
                "discord_user_id": str((policy.get("discord_user_id") or policy.get("author_id") or "")).strip(),
                "portal_user_id": str(((policy.get("identity") or {}).get("portal_user_id") or "")).strip(),
                "guild_id": str(policy.get("guild_id") or "").strip(),
                "channel_id": str(policy.get("channel_id") or "").strip(),
                "command_key": command_key or policy.get("command_key") or "",
                "surface": policy.get("surface") or "",
                "visibility_mode": policy.get("visibility_mode") or "",
                "status": status,
                "public_summary": public_summary,
                "private_summary": private_summary,
                "dm_status": dm_status,
                "detail_text": detail_text,
                "intent_kind": intent_kind,
                "executor_kind": executor_kind,
                "resolved_project_key": resolved_project_key,
                "route_status": route_status,
                "verification_status": verification_status,
                "correlation_id": correlation_id,
                "meta": meta or {},
            },
        )
        return data.get("job", {}) if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[discord-policy] job log failed: {e}")
        return {}


async def send_detail_dm(user: discord.abc.User, text: str, *, file_bytes: bytes | None = None, filename: str = "") -> tuple[bool, str]:
    try:
        if file_bytes is not None:
            await user.send(compact_discord_text(text, 1800), file=discord.File(io.BytesIO(file_bytes), filename=filename or "artifact.bin"))
        else:
            await user.send(compact_discord_text(text, 1900))
        return True, "sent"
    except Exception as e:
        return False, compact_discord_text(str(e), 240)


def mount_olympus_url() -> str:
    configured = str(DISCORD_UI_ACTIVITY_URL or OLYMPUS_SITE_URL or "").strip()
    if configured:
        return configured
    domain = str(_read_olympus_identity().get("public_domain") or "").strip()
    return f"https://{domain}" if domain else ""


def pairing_instruction_text() -> str:
    ui_name = olympus_ui_name()
    base = f"In {ui_name}, open the Discord Pairing Bridge, generate the 6-digit code, and send it here."
    url = mount_olympus_url()
    if url:
        base = f"Open {ui_name} at {url} and use the Discord Pairing Bridge to generate the 6-digit code, then send it here."
    return base


def discord_install_url() -> str:
    app_id = DISCORD_APPLICATION_ID or int(getattr(bot, "application_id", 0) or 0)
    if app_id <= 0:
        return ""
    scope = quote(" ".join(DISCORD_INVITE_SCOPES))
    return f"https://discord.com/oauth2/authorize?client_id={app_id}&scope={scope}&permissions={quote(DISCORD_INVITE_PERMISSIONS)}"


def _read_runtime_state() -> dict[str, Any]:
    try:
        if DISCORD_RUNTIME_STATE_PATH.exists():
            return json.loads(DISCORD_RUNTIME_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_runtime_state(payload: dict[str, Any]) -> None:
    try:
        DISCORD_RUNTIME_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        DISCORD_RUNTIME_STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[discord-runtime] state write failed: {e}")


def runtime_connection_summary(
    *,
    surface: str = "",
    project_key: str = "",
    guild_id: Any = "",
    channel_id: Any = "",
    route_status: str = "",
    voice_connected: Optional[bool] = None,
    speaker: str = "",
    request_kind: str = "",
    priority: str = "",
) -> str:
    parts: list[str] = []
    for label, value in (
        ("surface", surface),
        ("project", project_key),
        ("guild", guild_id),
        ("channel", channel_id),
        ("route", route_status),
        ("speaker", speaker),
        ("kind", request_kind),
        ("priority", priority),
    ):
        text = str(value or "").strip()
        if text:
            parts.append(f"{label}={text}")
    if voice_connected is not None:
        parts.append(f"voice={'up' if voice_connected else 'down'}")
    return " | ".join(parts) or "no-connection-context"


def record_runtime_breadcrumb(
    stage: str,
    *,
    severity: str = "info",
    scene: str = "",
    route: str = "",
    connection: str = "",
    **fields: Any,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "instance_id": RUNTIME_INSTANCE_ID,
        "stage": str(stage or "").strip() or "unknown",
        "severity": str(severity or "info").strip() or "info",
        "scene": str(scene or "").strip() or "unknown",
        "route": str(route or "").strip(),
        "connection": str(connection or "").strip(),
    }
    for key, value in fields.items():
        if value is None or value == "":
            continue
        if isinstance(value, (str, int, float, bool, list, dict)):
            entry[key] = value
        else:
            entry[key] = str(value)
    discord_runtime_breadcrumbs.append(entry)
    if len(discord_runtime_breadcrumbs) > 120:
        del discord_runtime_breadcrumbs[:-120]
    compact = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    print(f"[discord-runtime] {compact}")
    try:
        DISCORD_RUNTIME_DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DISCORD_RUNTIME_DIAGNOSTICS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(compact + "\n")
    except Exception as exc:
        print(f"[discord-runtime] breadcrumb write failed: {exc}")
    return entry


async def submit_runtime_diagnostic_receipt(
    policy: Optional[dict[str, Any]],
    *,
    stage: str,
    severity: str,
    scene: str,
    route: str,
    reason: str,
    meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    current_policy = policy if isinstance(policy, dict) else {}
    connection = runtime_connection_summary(
        surface=str(current_policy.get("surface") or scene or "").strip(),
        project_key=str(current_policy.get("project_key") or "").strip(),
        guild_id=current_policy.get("guild_id") or "",
        channel_id=current_policy.get("channel_id") or "",
        route_status=str(current_policy.get("route_status") or route or "").strip(),
        speaker=str((meta or {}).get("speaker") or "").strip(),
        request_kind=str((meta or {}).get("request_kind") or "").strip(),
        priority=str((meta or {}).get("priority") or "").strip(),
    )
    recent = discord_runtime_breadcrumbs[-8:]
    record_runtime_breadcrumb(
        "receipt",
        severity=severity,
        scene=scene,
        route=route,
        connection=connection,
        reason=compact_discord_text(reason, 220),
        recent_breadcrumbs=recent,
    )
    return await upsert_discord_job_core(
        current_policy,
        job_id=f"discord_diag_{uuid.uuid4().hex[:12]}",
        status="diagnostic",
        public_summary=f"{scene} diagnostic receipt",
        private_summary=compact_discord_text(reason, 500),
        dm_status="not_needed",
        detail_text=compact_discord_text(reason, 800),
        command_key="runtime_diagnostic",
        intent_kind=scene,
        executor_kind=route,
        resolved_project_key=str(current_policy.get("project_key") or ""),
        route_status="diagnostic_receipt",
        verification_status=severity,
        correlation_id=str((meta or {}).get("correlation_id") or new_hermes_correlation_id()),
        meta={
            "stage": stage,
            "scene": scene,
            "severity": severity,
            "reason": compact_discord_text(reason, 320),
            "breadcrumbs": recent,
            **(meta or {}),
        },
    )


def _incident_poll_backoff_for_error(exc: Exception) -> float:
    text = str(exc or "").strip().lower()
    if "429" in text or ("rate" in text and "limit" in text):
        return 300.0
    if "403" in text or "forbidden" in text or "invalid control token" in text:
        return 900.0
    if "401" in text or "unauthorized" in text:
        return 900.0
    return 120.0


def _schedule_incident_sync_backoff(exc: Exception) -> None:
    global incident_sync_backoff_until, incident_sync_backoff_sec, incident_sync_last_error
    wait_sec = max(30.0, float(_incident_poll_backoff_for_error(exc)))
    prior_until = incident_sync_backoff_until
    incident_sync_backoff_until = time.monotonic() + wait_sec
    incident_sync_backoff_sec = wait_sec
    message = compact_discord_text(str(exc or "unknown incident poll error"), 260)
    if message != incident_sync_last_error or time.monotonic() >= prior_until:
        print(f"[discord-admin] incident poll paused for {int(wait_sec)}s: {message}")
    incident_sync_last_error = message


def consume_pairing_cooldown(discord_user_id: int) -> tuple[bool, float]:
    now = time.monotonic()
    last = float(pairing_request_cooldowns.get(int(discord_user_id or 0), 0.0) or 0.0)
    wait = DISCORD_PAIRING_REQUEST_COOLDOWN_SEC - (now - last)
    if wait > 0:
        return False, wait
    pairing_request_cooldowns[int(discord_user_id or 0)] = now
    return True, 0.0


async def maybe_sync_app_commands() -> None:
    app_id = DISCORD_APPLICATION_ID or int(getattr(bot, "application_id", 0) or 0)
    if app_id <= 0 or not DISCORD_SLASH_SYNC_ON_BOOT:
        return
    state = _read_runtime_state()
    last_sync_ts = float(state.get("last_tree_sync_ts", 0.0) or 0.0)
    last_sync_app_id = int(state.get("last_tree_sync_app_id", 0) or 0)
    now = time.time()
    if last_sync_app_id == app_id and (now - last_sync_ts) < DISCORD_SLASH_SYNC_MIN_INTERVAL_SEC:
        print(f"[discord-app] slash sync skipped; cooldown {(DISCORD_SLASH_SYNC_MIN_INTERVAL_SEC - (now - last_sync_ts)):.0f}s remaining")
        return
    synced = await bot.tree.sync()
    state["last_tree_sync_ts"] = now
    state["last_tree_sync_app_id"] = app_id
    state["last_tree_sync_count"] = len(synced)
    _write_runtime_state(state)
    print(f"[discord-app] synced {len(synced)} application commands for app_id={app_id}")


def extract_pairing_code(text: str) -> str:
    match = PAIR_CODE_RE.search(str(text or ""))
    return match.group(1) if match else ""


def looks_like_pairing_request(text: str) -> bool:
    cleaned = str(text or "").strip().lower()
    if not cleaned:
        return False
    markers = ["pair", "link discord", "connect discord", "sync discord", "link my discord"]
    return any(marker in cleaned for marker in markers)


async def start_pairing_session_via_core(user: discord.abc.User, reset: bool = False) -> dict[str, Any]:
    return await core_post(
        "/discord/pairing/session/start",
        {
            "token": CONTROL_TOKEN,
            "discord_user_id": str(getattr(user, "id", 0)),
            "discord_name": getattr(user, "display_name", getattr(user, "name", "discord-user")),
            "reset": bool(reset),
            "meta": {"source": "discord_dm"},
        },
    )


async def redeem_pairing_code_via_core(user: discord.abc.User, code: str) -> dict[str, Any]:
    return await core_post(
        "/discord/pairing/redeem",
        {
            "token": CONTROL_TOKEN,
            "discord_user_id": str(getattr(user, "id", 0)),
            "discord_name": getattr(user, "display_name", getattr(user, "name", "discord-user")),
            "code": code,
            "meta": {"source": "discord_dm"},
        },
    )


async def handle_dm_pairing(message: discord.Message, policy: Optional[dict[str, Any]] = None) -> bool:
    if not is_dm(message):
        return False
    current_policy = policy if isinstance(policy, dict) else {}
    content = (message.content or "").strip()
    linked = bool(current_policy.get("linked"))
    pairing_code = extract_pairing_code(content)
    if linked and looks_like_pairing_request(content):
        identity = current_policy.get("identity") or {}
        portal_user_id = str(identity.get("portal_user_id") or "").strip()
        gmail = str(identity.get("gmail") or "").strip()
        await message.reply(
            compact_discord_text(
                f"You're already paired to {portal_user_id or 'your Mount Olympus identity'}"
                + (f" ({gmail})" if gmail else "")
                + ". If you need the link changed, ask the owner to rotate it from the UI.",
                1800,
            ),
            mention_author=False,
        )
        return True
    if not looks_like_pairing_request(content) and not pairing_code:
        return False
    allowed, wait = consume_pairing_cooldown(int(getattr(message.author, "id", 0) or 0))
    if not allowed:
        await message.reply(
            compact_discord_text(f"Hold one beat. Pairing is throttled for {wait:.1f} more seconds so we do not spam Discord or the core bridge.", 1800),
            mention_author=False,
        )
        return True
    if pairing_code:
        try:
            result = await redeem_pairing_code_via_core(message.author, pairing_code)
        except Exception as e:
            await message.reply(f"Pairing failed: {compact_discord_text(str(e), 500)}", mention_author=False)
            return True
        status = str(result.get("status") or "").strip().lower()
        session = result.get("session", {}) if isinstance(result, dict) else {}
        if status == "linked":
            link = result.get("link", {}) if isinstance(result, dict) else {}
            portal_user_id = str(link.get("portal_user_id") or "").strip() or str(session.get("linked_portal_user_id") or "").strip() or "your operator identity"
            gmail = str(link.get("gmail") or "").strip() or str(session.get("linked_gmail") or "").strip()
            await message.reply(
                compact_discord_text(
                    f"Pairing complete. This Discord account is now linked to {portal_user_id}"
                    + (f" ({gmail})" if gmail else "")
                    + f". You can work with {DISCORD_CONNECTOR_NAME} by DM now, and project access still follows the Mount Olympus access lists.",
                    1800,
                ),
                mention_author=False,
            )
            return True
        if status == "already_linked":
            await message.reply("This Discord account is already linked.", mention_author=False)
            return True
        attempts_remaining = int(session.get("attempts_remaining") or 0)
        if status == "locked":
            await message.reply(
                f"That pairing session is locked after {DISCORD_PAIRING_MAX_ATTEMPTS} attempts. Say `pair` again to start a fresh session, then use a new code from {UI_BRAND_NAME}.",
                mention_author=False,
            )
            return True
        await message.reply(
            compact_discord_text(
                f"That code didn't match. Attempts remaining: {attempts_remaining}. "
                f"{pairing_instruction_text()}",
                1800,
            ),
            mention_author=False,
        )
        return True
    try:
        result = await start_pairing_session_via_core(message.author, reset=True)
    except Exception as e:
        await message.reply(f"I couldn't open a pairing session right now: {compact_discord_text(str(e), 400)}", mention_author=False)
        return True
    status = str(result.get("status") or "").strip().lower()
    if status == "already_linked":
        await message.reply("This Discord account is already linked.", mention_author=False)
        return True
    if status == "locked":
        await message.reply(
            f"Your last pairing session is still locked. Wait for the lock to clear or have the owner generate a fresh code from {UI_BRAND_NAME}.",
            mention_author=False,
        )
        return True
    await message.reply(
        compact_discord_text(
            f"Pairing session opened. {pairing_instruction_text()} "
            f"You have {DISCORD_PAIRING_MAX_ATTEMPTS} tries before this session locks.",
            1800,
        ),
        mention_author=False,
    )
    return True


async def fetch_discord_actor_policy(message: discord.Message, requested_project_key: str = "", command_key: str = "") -> dict[str, Any]:
    cache_key = _policy_cache_key(requested_project_key, command_key)
    existing = _cached_message_policy(message, requested_project_key=requested_project_key, command_key=command_key)
    if existing:
        return existing
    binding = await fetch_discord_channel_binding(message, project_key=requested_project_key)
    resolved_project_key = infer_project_key_from_message(message, requested_project_key=requested_project_key, binding=binding)
    community_guild: dict[str, Any] = {}
    if is_owner_member(message.author):
        policy = {
            "discord_user_id": getattr(message.author, "id", 0),
            "author_id": getattr(message.author, "id", 0),
            "identity": {"portal_user_id": "owner"},
            "linked": True,
            "server_operator": True,
            "project_key": resolved_project_key,
            "project_access": {"access_role": "platform_admin", "can_command": True, "can_view": True, "can_announce": True},
            "can_view": True,
            "can_command": True,
            "can_announce": True,
            "council_allowed": True,
            "authorized_capabilities": ["project_task", "project_lookup", "codex_task", "media_job", "darius_route", "server_poll", "server_raffle", "discord_admin", "project_announce", "branding_sync", "channel_template_sync", "sticky_sync", "deploy_request", "deploy_execute"],
            "actor_role": "owner",
            "job_template": "platform_admin",
            "approval_sensitivity": "critical",
            "deploy_rights": {"allowed": True, "mode": "project_auto_if_green"},
        }
    else:
        try:
            policy = await core_get(
                "/discord/actor/resolve",
                {
                    "token": CONTROL_TOKEN,
                    "discord_user_id": getattr(message.author, "id", 0),
                    "project_key": resolved_project_key,
                    "guild_id": getattr(message.guild, "id", 0) if message.guild else "",
                    "channel_id": getattr(message.channel, "id", 0),
                },
            )
        except Exception as e:
            print(f"[discord-policy] actor resolve failed: {e}")
            policy = {
                "linked": False,
                "server_operator": False,
                "can_view": False,
                "can_command": False,
                "can_announce": False,
                "authorized_capabilities": [],
                "actor_role": "degraded",
                "project_key": resolved_project_key,
                "degraded_resolution": True,
                "degraded_reason": str(e),
            }
    community_guild = policy.get("community_guild") if isinstance(policy.get("community_guild"), dict) else {}
    if not community_guild and not is_dm(message):
        community_guild = await resolve_discord_community_guild_core(resolved_project_key, message.guild)
    is_public_community = bool(isinstance(community_guild, dict) and str(community_guild.get("guild_id") or "").strip())
    surface = "dm" if is_dm(message) else ("public_channel" if is_public_community else ("project_channel" if resolved_project_key else "public_channel"))
    visibility_mode = "private_detail" if surface == "dm" else ("project_sanitized" if surface == "project_channel" else "public_ack_only")
    if isinstance(binding, dict) and str(binding.get("visibility_mode") or "").strip():
        visibility_mode = str(binding.get("visibility_mode") or "").strip()
    policy["project_key"] = resolved_project_key
    policy["channel_binding"] = binding
    policy["community_guild"] = community_guild if isinstance(community_guild, dict) else {}
    policy["surface"] = surface
    policy["visibility_mode"] = policy.get("visibility_mode") or visibility_mode
    policy["channel_policy"] = "dm" if surface == "dm" else ("community_public" if is_public_community else ("locked_project_channel" if surface == "project_channel" else "public_mention_only"))
    policy["guild_id"] = getattr(message.guild, "id", 0) if message.guild else ""
    policy["channel_id"] = getattr(message.channel, "id", 0)
    policy["command_key"] = command_key
    access_role = str(((policy.get("project_access") or {}).get("access_role") or "")).strip().lower()
    policy["can_write"] = bool(policy.get("can_command")) and access_role not in {"viewer", "observer", "read_only"}
    _store_message_policy(message, policy, requested_project_key=requested_project_key, command_key=command_key)
    return policy


def policy_resolution_degraded(policy: Optional[dict[str, Any]]) -> bool:
    return bool(isinstance(policy, dict) and policy.get("degraded_resolution"))


def build_policy_resolution_reply(surface: str = "dm") -> str:
    if surface == "project_channel":
        return "I couldn't verify Olympus access for this channel right now because policy resolution is degraded. Try again shortly."
    if surface == "public_channel":
        return "I couldn't verify Olympus access right now because policy resolution is degraded. Try again shortly."
    return "I couldn't verify your Olympus access right now because policy resolution is degraded. Try again shortly."


def build_operator_access_reply(policy: Optional[dict[str, Any]], surface: str = "dm") -> str:
    current_policy = policy if isinstance(policy, dict) else {}
    if policy_resolution_degraded(current_policy):
        return build_policy_resolution_reply(surface)
    if surface == "dm" and not current_policy.get("linked"):
        return DISCORD_LINK_HELP_TEXT
    return "You need project access before I can execute that work from DM."


def build_media_access_reply(policy: Optional[dict[str, Any]], surface: str = "dm") -> str:
    current_policy = policy if isinstance(policy, dict) else {}
    if policy_resolution_degraded(current_policy):
        return build_policy_resolution_reply(surface)
    if surface == "dm" and not current_policy.get("linked"):
        return DISCORD_LINK_HELP_TEXT
    return "You need Olympus media access before I can generate images or logos here."


async def ensure_project_channels_for_guild(guild: discord.Guild, project_key: str, title: str = "") -> dict[str, Any]:
    normalized = normalize_project_key_local(project_key)
    if not normalized:
        return {}
    binding = {}
    try:
        data = await core_get("/discord/channel-bindings/resolve", {"token": CONTROL_TOKEN, "guild_id": guild.id, "project_key": normalized})
        binding = data.get("binding", {}) if isinstance(data, dict) else {}
    except Exception:
        binding = {}
    access_rows = await fetch_project_access_rows(normalized)
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False, connect=False),
    }
    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, manage_messages=True, connect=True, speak=True)
    for owner in await fetch_owner_members_for_guild(guild):
        overwrites[owner] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True, manage_channels=True, connect=True, speak=True)
    for row in access_rows:
        user_id = _safe_int(row.get("discord_user_id"), 0)
        if user_id <= 0:
            continue
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                member = None
        if member is None:
            continue
        can_view = str(row.get("status") or "active").strip().lower() == "active" and bool(row.get("can_view"))
        can_command = str(row.get("status") or "active").strip().lower() == "active" and bool(row.get("can_command"))
        if not can_view:
            continue
        overwrites[member] = discord.PermissionOverwrite(
            read_messages=True,
            send_messages=can_command,
            read_message_history=True,
            connect=True,
            speak=can_command,
        )
    category_name = f"{DISCORD_PROJECT_CATEGORY_PREFIX} - {pretty_project_label(title or normalized)}"
    category = guild.get_channel(_safe_int(binding.get("category_id"), 0)) if isinstance(binding, dict) else None
    if category is None:
        category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        category = await guild.create_category(category_name[:100], overwrites=overwrites, reason=f"{DISCORD_CONNECTOR_NAME} project channel binding")
    else:
        try:
            await category.edit(overwrites=overwrites, name=category_name[:100], reason=f"{DISCORD_CONNECTOR_NAME} project channel binding sync")
        except Exception:
            pass
    text_name = normalize_project_key_local(binding.get("text_channel_name") or f"{normalized}-ops") or f"{normalized}-ops"
    voice_name = (binding.get("voice_channel_name") or f"{pretty_project_label(normalized)} Voice").strip()[:100]
    text_channel = guild.get_channel(_safe_int(binding.get("text_channel_id"), 0)) if isinstance(binding, dict) else None
    if text_channel is None or not isinstance(text_channel, discord.TextChannel):
        text_channel = discord.utils.get(guild.text_channels, name=text_name)
    if text_channel is None:
        text_channel = await guild.create_text_channel(text_name[:100], category=category, overwrites=overwrites, reason=f"{DISCORD_CONNECTOR_NAME} project channel binding")
    voice_channel = guild.get_channel(_safe_int(binding.get("voice_channel_id"), 0)) if isinstance(binding, dict) else None
    if voice_channel is None or not isinstance(voice_channel, discord.VoiceChannel):
        voice_channel = discord.utils.get(guild.voice_channels, name=voice_name)
    if voice_channel is None:
        voice_channel = await guild.create_voice_channel(voice_name[:100], category=category, overwrites=overwrites, reason=f"{DISCORD_CONNECTOR_NAME} project channel binding")
    binding = await upsert_discord_channel_binding_core(guild, normalized, category, text_channel, voice_channel)
    return {"binding": binding, "category": category, "text_channel": text_channel, "voice_channel": voice_channel}


async def ensure_ctx_policy(ctx, requested_project_key: str = "", command_key: str = "") -> dict[str, Any]:
    policy = await fetch_discord_actor_policy(ctx.message, requested_project_key=requested_project_key, command_key=command_key or getattr(ctx.command, "name", ""))
    setattr(ctx, "_luminax_policy", policy)
    return policy


def _quote_candidate_score(text: str) -> float:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) < 18 or len(cleaned) > 220:
        return 0.0
    if cleaned.startswith("!"):
        return 0.0
    score = 0.2
    if any(token in cleaned.lower() for token in ["always", "never", "build", "create", "music", "project", "world", "future", "dream", "goal"]):
        score += 0.35
    if any(ch in cleaned for ch in ".!?"):
        score += 0.15
    if len(cleaned.split()) >= 6:
        score += 0.15
    if cleaned.count(",") or cleaned.count(";"):
        score += 0.1
    return round(score, 3)


def archive_discord_message(message: discord.Message, policy: Optional[dict[str, Any]] = None):
    if message.author.bot:
        return
    conn = db_conn()
    cur = conn.cursor(dictionary=True)
    now_ts = int(time.time())
    created_ts = int(message.created_at.replace(tzinfo=timezone.utc).timestamp()) if getattr(message, "created_at", None) else now_ts
    expires_at = datetime.now(timezone.utc) + timedelta(days=DISCORD_ARCHIVE_RETENTION_DAYS)
    active_hours = {}
    active_weekdays = {}
    top_channels = {}
    recent_projects = {}
    discord_user_id = str(getattr(message.author, "id", 0))
    portal_user_id = str((((policy or {}).get("identity") or {}).get("portal_user_id") or "")).strip() or None
    cur.execute(
        "SELECT active_hours_json, active_weekdays_json, top_channels_json, recent_projects_json, first_seen_at, message_count, dm_count, guild_count FROM discord_user_stats WHERE discord_user_id=%s LIMIT 1",
        (discord_user_id,),
    )
    existing = cur.fetchone() or {}
    active_hours = _json_mysql_value(existing.get("active_hours_json"), {})
    active_weekdays = _json_mysql_value(existing.get("active_weekdays_json"), {})
    top_channels = _json_mysql_value(existing.get("top_channels_json"), {})
    recent_projects = _json_mysql_value(existing.get("recent_projects_json"), {})
    hour_key = str((message.created_at or datetime.now(timezone.utc)).hour)
    weekday_key = str((message.created_at or datetime.now(timezone.utc)).weekday())
    channel_key = str(getattr(message.channel, "id", "dm"))
    project_key = normalize_project_key_local((policy or {}).get("project_key") or "")
    active_hours[hour_key] = int(active_hours.get(hour_key, 0) or 0) + 1
    active_weekdays[weekday_key] = int(active_weekdays.get(weekday_key, 0) or 0) + 1
    top_channels[channel_key] = int(top_channels.get(channel_key, 0) or 0) + 1
    if project_key:
        recent_projects[project_key] = int(now_ts)
    sorted_windows = sorted(active_hours.items(), key=lambda item: int(item[1]), reverse=True)[:3]
    usual_windows = [f"{int(key):02d}:00" for key, _ in sorted_windows]
    cur.execute(
        """
        INSERT INTO discord_message_archive
          (message_created_at, expires_at, message_id, guild_id, channel_id, channel_name, project_key, discord_user_id, discord_name, portal_user_id, surface, content, meta_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          guild_id=VALUES(guild_id),
          channel_id=VALUES(channel_id),
          channel_name=VALUES(channel_name),
          project_key=VALUES(project_key),
          discord_name=VALUES(discord_name),
          portal_user_id=VALUES(portal_user_id),
          surface=VALUES(surface),
          content=VALUES(content),
          meta_json=VALUES(meta_json),
          expires_at=VALUES(expires_at)
        """,
        (
            created_ts,
            expires_at.replace(tzinfo=None),
            str(message.id),
            str(getattr(message.guild, "id", "")) or None,
            str(getattr(message.channel, "id", "")) or None,
            getattr(message.channel, "name", "dm") if not is_dm(message) else "dm",
            project_key or None,
            discord_user_id,
            getattr(message.author, "display_name", getattr(message.author, "name", "user"))[:255],
            portal_user_id,
            str((policy or {}).get("surface") or ("dm" if is_dm(message) else "public_channel")),
            message.content or "",
            json.dumps({"jump_url": getattr(message, "jump_url", ""), "attachments": [att.url for att in getattr(message, "attachments", [])]}),
        ),
    )
    cur.execute(
        """
        INSERT INTO discord_user_stats
          (discord_user_id, portal_user_id, discord_name, first_seen_at, last_seen_at, message_count, dm_count, guild_count, active_hours_json, active_weekdays_json, top_channels_json, recent_projects_json, usual_windows_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          portal_user_id=VALUES(portal_user_id),
          discord_name=VALUES(discord_name),
          first_seen_at=LEAST(COALESCE(first_seen_at, VALUES(first_seen_at)), VALUES(first_seen_at)),
          last_seen_at=VALUES(last_seen_at),
          message_count=VALUES(message_count),
          dm_count=VALUES(dm_count),
          guild_count=VALUES(guild_count),
          active_hours_json=VALUES(active_hours_json),
          active_weekdays_json=VALUES(active_weekdays_json),
          top_channels_json=VALUES(top_channels_json),
          recent_projects_json=VALUES(recent_projects_json),
          usual_windows_json=VALUES(usual_windows_json)
        """,
        (
            discord_user_id,
            portal_user_id,
            getattr(message.author, "display_name", getattr(message.author, "name", "user"))[:255],
            int(existing.get("first_seen_at") or created_ts),
            created_ts,
            int(existing.get("message_count", 0) or 0) + 1,
            int(existing.get("dm_count", 0) or 0) + (1 if is_dm(message) else 0),
            int(existing.get("guild_count", 0) or 0) + (0 if is_dm(message) else 1),
            json.dumps(active_hours),
            json.dumps(active_weekdays),
            json.dumps(top_channels),
            json.dumps(recent_projects),
            json.dumps(usual_windows),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()


def compact_discord_archive():
    conn = db_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """
        SELECT id, message_id, discord_user_id, portal_user_id, message_created_at, content
        FROM discord_message_archive
        WHERE expires_at <= UTC_TIMESTAMP()
        ORDER BY expires_at ASC
        LIMIT 250
        """
    )
    rows = cur.fetchall() or []
    if not rows:
        cur.close()
        conn.close()
        return {"trimmed": 0, "quotes": 0}
    quote_count = 0
    trim_ids: list[int] = []
    for row in rows:
        trim_ids.append(int(row.get("id") or 0))
        score = _quote_candidate_score(row.get("content") or "")
        if score < 0.55:
            continue
        cur.execute(
            """
            INSERT IGNORE INTO discord_user_quotes
              (discord_user_id, portal_user_id, quote_text, source_message_id, source_created_at, quote_score, status, meta_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                str(row.get("discord_user_id") or "").strip(),
                str(row.get("portal_user_id") or "").strip() or None,
                compact_discord_text(row.get("content") or "", 500),
                str(row.get("message_id") or "").strip() or None,
                int(row.get("message_created_at") or 0) or None,
                score,
                "saved",
                json.dumps({"trimmed_from_archive": True}),
            ),
        )
        if cur.rowcount:
            quote_count += 1
    if trim_ids:
        placeholders = ",".join(["%s"] * len(trim_ids))
        cur.execute(f"DELETE FROM discord_message_archive WHERE id IN ({placeholders})", tuple(trim_ids))
    conn.commit()
    cur.close()
    conn.close()
    return {"trimmed": len(trim_ids), "quotes": quote_count}


def message_directed_to_hermes(message: discord.Message) -> bool:
    content = (message.content or "").strip().lower()
    if not content:
        return False
    if message_mentions_hermes(message) or message_is_reply_to_bot(message):
        return True
    return (
        content.startswith("hermes ")
        or content.startswith("zeus ")
        or content.startswith("olympus ")
        or content.startswith("mount olympus ")
    )


def _channel_mode_lookup(message: discord.Message) -> str:
    payload = load_discord_channel_modes_payload()
    if not isinstance(payload, dict):
        payload = {}
    channel_id = str(getattr(message.channel, "id", "") or "").strip()
    channel_name = str(getattr(message.channel, "name", "") or "").strip().lower()
    category_name = ""
    try:
        category_name = str(getattr(getattr(message.channel, "category", None), "name", "") or "").strip().lower()
    except Exception:
        category_name = ""
    guild_name = str(getattr(getattr(message, "guild", None), "name", "") or "").strip().lower()
    channel_ids = payload.get("channel_ids") if isinstance(payload.get("channel_ids"), dict) else {}
    channel_names = payload.get("channel_names") if isinstance(payload.get("channel_names"), dict) else {}
    category_names = payload.get("category_names") if isinstance(payload.get("category_names"), dict) else {}
    guild_channel_names = payload.get("guild_channel_names") if isinstance(payload.get("guild_channel_names"), dict) else {}
    if channel_id and str(channel_ids.get(channel_id) or "").strip():
        return str(channel_ids.get(channel_id) or "").strip().lower()
    if guild_name and channel_name:
        scoped = guild_channel_names.get(f"{guild_name}:{channel_name}")
        if str(scoped or "").strip():
            return str(scoped or "").strip().lower()
    if channel_name and str(channel_names.get(channel_name) or "").strip():
        return str(channel_names.get(channel_name) or "").strip().lower()
    if category_name and str(category_names.get(category_name) or "").strip():
        return str(category_names.get(category_name) or "").strip().lower()
    return ""


def infer_project_key_from_message(message: discord.Message, requested_project_key: str = "", binding: Optional[dict[str, Any]] = None) -> str:
    requested = normalize_project_key_local(requested_project_key)
    if requested:
        return requested
    if isinstance(binding, dict):
        bound = normalize_project_key_local(binding.get("project_key") or "")
        if bound:
            return bound
    if is_dm(message):
        return ""

    channel = getattr(message, "channel", None)
    guild = getattr(message, "guild", None)
    category = getattr(channel, "category", None)
    parent = getattr(channel, "parent", None)
    candidates = [
        str(getattr(channel, "name", "") or "").strip().lower(),
        str(getattr(category, "name", "") or "").strip().lower(),
        str(getattr(parent, "name", "") or "").strip().lower(),
        str(getattr(guild, "name", "") or "").strip().lower(),
    ]
    message_content = str(getattr(message, "content", "") or "").strip().lower()
    guild_default_project = normalize_project_key_local(default_project_key_from_guild(guild))

    if looks_like_mount_olympus_web_request(message_content):
        return "mount-olympus-web"
    if guild_default_project == "mount-olympus-core" and looks_like_olympus_web_surface_request(message_content):
        return "mount-olympus-web"

    for candidate in candidates:
        if not candidate:
            continue
        if "underfoot" in candidate:
            return "underfoot"
        if "world-of-epoch" in candidate or "worldofepoch" in candidate:
            return "worldofepoch"
        if "growbudds" in candidate:
            return "growbudds"
        if "groupup" in candidate:
            return "groupup"
        if "music" in candidate:
            return "music"
        if "olympus-web" in candidate or candidate == "visual-forge" or candidate == "launch surface":
            return "mount-olympus-web"

    if _channel_mode_lookup(message) == "project_control":
        return default_project_key_from_guild(guild)
    return ""


def looks_like_olympus_web_surface_request(text: str) -> bool:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return False
    web_targets = [
        "website",
        "webpage",
        "site",
        "landing",
        "layout",
        "motif",
        "style",
        "styles",
        "graphic",
        "graphics",
        "visual",
        "visuals",
        "look",
        "feel",
        "aesthetic",
        "brand",
        "branding",
        "banner",
        "hero",
        "parallax",
        "button",
        "buttons",
        "header",
        "footer",
        "landing page",
        "public gateway",
        "color",
        "palette",
        "theme",
    ]
    return any(target in cleaned for target in web_targets)


def looks_like_mount_olympus_web_request(text: str) -> bool:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return False
    brand_context = [
        "mount olympus",
        "olympus web",
        "welcome2olympus",
        "welcome2olympus.com",
        "zeus deck",
        "olympus portal",
        "olympus website",
    ]
    web_targets = [
        "website",
        "webpage",
        "site",
        "landing",
        "layout",
        "motif",
        "style",
        "styles",
        "graphic",
        "graphics",
        "visual",
        "visuals",
        "look",
        "feel",
        "aesthetic",
        "brand",
        "branding",
        "banner",
        "hero",
        "parallax",
        "button",
        "buttons",
        "website link",
        "discord link",
        "streamline",
        "header",
        "footer",
        "landing page",
        "public gateway",
        "color",
        "palette",
        "theme",
    ]
    return any(context in cleaned for context in brand_context) and any(target in cleaned for target in web_targets)


def channel_mode_for_message(message: discord.Message, policy: Optional[dict[str, Any]] = None) -> str:
    configured = _channel_mode_lookup(message)
    if configured in {"mention_only", "ambient_allowed", "project_control", "ops_only"}:
        return configured
    if is_dm(message):
        return "ambient_allowed"
    current = policy if isinstance(policy, dict) else {}
    community = community_guild_from_policy(current)
    surface = str(current.get("surface") or "").strip().lower()
    channel_name = str(getattr(message.channel, "name", "") or "").strip().lower()
    channel_id = str(getattr(message.channel, "id", "") or "").strip()
    if community and is_owner(getattr(message, "author", None)):
        return "mention_only"
    if community:
        if channel_id and channel_id in {
            str(community.get("feedback_channel_id") or "").strip(),
            str(community.get("bugs_channel_id") or "").strip(),
        }:
            return "ambient_allowed"
        if channel_id and channel_id in {
            str(community.get("announcements_channel_id") or "").strip(),
            str(community.get("public_summary_channel_id") or "").strip(),
        }:
            return "mention_only"
    if surface == "project_channel":
        return "project_control"
    if "hermes" in channel_name or "oracle" in channel_name:
        return "ambient_allowed"
    if "ops" in channel_name or "briefing" in channel_name:
        return "ops_only"
    return "mention_only"


def looks_like_message_delete_request(text: str) -> bool:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return False
    markers = [
        "clear the chat",
        "clear chat",
        "clear the channel",
        "clean up the chat",
        "delete messages",
        "purge messages",
        "purge chat",
        "wipe chat",
    ]
    return any(marker in cleaned for marker in markers)


def parse_purge_confirmation(text: str) -> tuple[bool, int, bool]:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return False, 0, False
    confirmed = "confirm purge" in cleaned or cleaned.startswith("!purge")
    hermes_only = "hermes" in cleaned or "bot" in cleaned
    match = re.search(r"\b(\d{1,3})\b", cleaned)
    count = max(1, min(int(match.group(1)), 100)) if match else 20
    return confirmed, count, hermes_only


def build_access_denied_reply(project_key: str = "") -> str:
    if project_key:
        return f"You are not authorized for project `{project_key}` here. {DISCORD_LINK_HELP_TEXT}"
    return DISCORD_LINK_HELP_TEXT


def build_owner_access_denied_reply(project_key: str = "") -> str:
    base = build_access_denied_reply(project_key)
    return compact_discord_text(f"{base} Talk to the owner for approval.", 1800)

def community_guild_from_policy(policy: Optional[dict[str, Any]]) -> dict[str, Any]:
    current = policy if isinstance(policy, dict) else {}
    row = current.get("community_guild") if isinstance(current.get("community_guild"), dict) else {}
    return row if isinstance(row, dict) and str(row.get("guild_id") or "").strip() else {}

def policy_is_public_community(policy: Optional[dict[str, Any]]) -> bool:
    return bool(community_guild_from_policy(policy))

def infer_community_receipt_kind(text: str) -> str:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return "feedback"
    if cleaned.startswith("bug:") or " bug " in f" {cleaned} ":
        return "bug_report"
    if cleaned.startswith("idea:") or cleaned.startswith("feature:") or "feature request" in cleaned or "please add" in cleaned or "would be cool" in cleaned:
        return "feature_request"
    return "feedback"


def policy_channel_meta(message: discord.Message, policy: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    meta = channel_trace_meta(message)
    current = policy or {}
    meta["project_key"] = current.get("project_key") or ""
    meta["channel_policy"] = current.get("channel_policy") or ""
    meta["actor_role"] = current.get("actor_role") or ""
    meta["authorized_capabilities"] = current.get("authorized_capabilities") or []
    meta["visibility_mode"] = current.get("visibility_mode") or ""
    meta["request_source"] = current.get("command_key") or "discord_text"
    community = community_guild_from_policy(current)
    if community:
        meta["community_guild_status"] = str(community.get("community_guild_status") or "").strip()
        meta["community_guild_id"] = str(community.get("guild_id") or "").strip()
    if current.get("summary_mode"):
        meta["summary_mode"] = current.get("summary_mode")
    return meta


def build_private_activity_footer(job_id: str = "") -> str:
    if DISCORD_UI_ACTIVITY_URL and job_id:
        return f"\n\nActivity: {DISCORD_UI_ACTIVITY_URL.rstrip('/')}/{job_id}"
    return ""


def http_timeout(total_sec: float) -> httpx.Timeout:
    total = max(5.0, float(total_sec or DISCORD_HTTP_TIMEOUT_SEC))
    return httpx.Timeout(total, connect=max(1.0, DISCORD_HTTP_CONNECT_TIMEOUT_SEC), pool=5.0)


async def get_http_client() -> httpx.AsyncClient:
    global http_client
    if http_client and not http_client.is_closed:
        return http_client
    async with http_client_lock:
        if http_client and not http_client.is_closed:
            return http_client
        http_client = httpx.AsyncClient(
            timeout=http_timeout(DISCORD_HTTP_TIMEOUT_SEC),
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64, keepalive_expiry=30.0),
            headers={"User-Agent": f"{DISCORD_CONNECTOR_NAME}DiscordBot/1.0"},
        )
        return http_client


def compact_discord_text(text: str, limit: int = 1800) -> str:
    cleaned = " ".join(str(text or "").replace("```", "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."

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

def incident_reminder_seconds(severity: str) -> int:
    key = str(severity or "medium").strip().lower()
    if key == "critical":
        return 15 * 60
    if key == "high":
        return 30 * 60
    if key == "low":
        return 3 * 60 * 60
    return 60 * 60

async def admin_api_get(path: str, params: Optional[dict[str, Any]] = None, timeout_sec: float = 30.0) -> dict[str, Any]:
    client = await get_http_client()
    r = await client.get(f"{ADMIN_API_URL}{path}", params=params or {}, timeout=http_timeout(timeout_sec))
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {"status": "ok", "data": data}

async def admin_api_post(path: str, payload: dict[str, Any], timeout_sec: float = 30.0) -> dict[str, Any]:
    client = await get_http_client()
    r = await client.post(f"{ADMIN_API_URL}{path}", json=payload, timeout=http_timeout(timeout_sec))
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {"status": "ok", "data": data}

async def resolve_incident_channel() -> Optional[discord.abc.Messageable]:
    raw = str(DISCORD_INCIDENT_CHANNEL or DISCORD_BUG_CHANNEL or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        channel = bot.get_channel(int(raw))
        if channel is not None:
            return channel
        try:
            return await bot.fetch_channel(int(raw))
        except Exception:
            return None
    target = raw.lower()
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if str(channel.name or "").strip().lower() == target:
                return channel
    return None

async def sync_incident_notifications():
    global incident_sync_backoff_until, incident_sync_backoff_sec, incident_sync_last_error, incident_sync_last_ok_ts
    if not CONTROL_TOKEN:
        return
    if time.monotonic() < incident_sync_backoff_until:
        return
    try:
        incidents = await admin_api_get("/incidents/open", {"token": CONTROL_TOKEN, "limit": 30}, timeout_sec=20)
        watch = await admin_api_get("/services/watch/status", {"token": CONTROL_TOKEN}, timeout_sec=20)
    except Exception as exc:
        _schedule_incident_sync_backoff(exc)
        return
    if incident_sync_last_error:
        print("[discord-admin] incident polling recovered")
    incident_sync_backoff_until = 0.0
    incident_sync_backoff_sec = 0.0
    incident_sync_last_error = ""
    incident_sync_last_ok_ts = time.time()

    now_ts = int(time.time())
    incident_rows = incidents.get("rows") if isinstance(incidents.get("rows"), list) else []
    watch_status = str(watch.get("status") or "unknown").strip().lower()
    failing_services = ", ".join([str(item) for item in (watch.get("failing_services") or [])[:6]])
    stale_nodes = int(((watch.get("windows_nodes") or {}).get("stale_count") or 0))
    incident_channel = await resolve_incident_channel()
    owner_users = await resolve_owner_users()

    for row in incident_rows:
        if not isinstance(row, dict):
            continue
        incident_key = str(row.get("incident_key") or "").strip()
        if not incident_key:
            continue
        acknowledged = bool(str(row.get("acknowledged_by") or "").strip())
        next_due = unix_ts_from_value(row.get("next_notification_at"))
        last_notified = unix_ts_from_value(row.get("last_notified_at"))
        if acknowledged and last_notified:
            continue
        if next_due and now_ts < next_due:
            continue
        severity = str(row.get("severity") or "medium").strip().lower()
        escalation_level = max(0, int(row.get("escalation_level") or 0)) + 1
        detail = compact_discord_text(str(row.get("detail_text") or "No detail captured."), 700)
        summary = compact_discord_text(str(row.get("summary") or incident_key), 240)
        chain = f"watch={watch_status}"
        if failing_services:
            chain += f" | failing={failing_services}"
        if stale_nodes:
            chain += f" | stale_nodes={stale_nodes}"
        message = (
            f"[Incident:{severity.upper()}] {summary}\n"
            f"Key: `{incident_key}`\n"
            f"Source: `{row.get('source_service')}` | Escalation: {escalation_level}\n"
            f"Detail: {detail}\n"
            f"Chain: {chain}"
        )
        delivered_via: list[str] = []
        if incident_channel is not None:
            try:
                await incident_channel.send(message)
                delivered_via.append("ops_channel")
            except Exception as exc:
                print(f"[discord-admin] incident channel send failed: {exc}")
        if severity in {"high", "critical"} and owner_users:
            owner_dm_sent = False
            for owner_user in owner_users:
                try:
                    await owner_user.send(message)
                    owner_dm_sent = True
                except Exception as exc:
                    print(f"[discord-admin] incident dm failed for {getattr(owner_user, 'id', 'unknown')}: {exc}")
            if owner_dm_sent:
                delivered_via.append("owner_dm")
        if not delivered_via:
            delivered_via = ["no_delivery_route"]
        try:
            await admin_api_post(
                "/incidents/notify",
                {
                    "token": CONTROL_TOKEN,
                    "incident_key": incident_key,
                    "delivered_via": delivered_via,
                    "escalation_level": escalation_level,
                    "next_reminder_sec": 0 if acknowledged else incident_reminder_seconds(severity),
                    "note": f"discord bot delivery state: {', '.join(delivered_via)}",
                },
                timeout_sec=20,
            )
        except Exception as exc:
            print(f"[discord-admin] incident notify update failed: {exc}")


def build_voice_lookup_ack(display_name: str, transcript: str) -> str:
    variants = [
        "I heard you. Give me a second to check that and I'll come right back.",
        "On it. I'm checking that now and I'll follow with the useful answer.",
        "Let me verify that quickly. I'll come back with the clean result.",
        "I'm looking that up now. I'll follow up with what matters.",
    ]
    seed = abs(hash((display_name.lower(), transcript.lower()[:120], int(time.time() // 60)))) % len(variants)
    return variants[seed]


def build_voice_turn_ack(display_name: str) -> str:
    variants = [
        "Got it. One second.",
        "On it. One sec.",
        "I heard you. Give me a second.",
        "Got you. Let me answer that cleanly.",
    ]
    seed = abs(hash((display_name.lower(), int(time.time() // 60)))) % len(variants)
    return variants[seed]


def build_voice_progress_update(display_name: str) -> str:
    variants = [
        "Still working through that now. ETA about ten more seconds.",
        "I'm still on it. This should land in about ten seconds.",
        "Still checking that for you. I should have the answer shortly.",
        "I'm in the middle of it now. Give me roughly ten more seconds.",
    ]
    seed = abs(hash((display_name.lower(), int(time.time() // 60)))) % len(variants)
    return variants[seed]


def build_text_progress_update(speaker: str) -> str:
    variants = [
        "Still checking that now. ETA about 10 seconds.",
        "I'm still on it. I should have the useful answer shortly.",
        "Working through that now. I'll follow with the clean result as soon as it lands.",
        "Still digging. This one is taking a bit longer, but I'm on it.",
    ]
    seed = abs(hash((speaker.lower(), int(time.time() // 60)))) % len(variants)
    return variants[seed]


def build_voice_fallback_ack(display_name: str) -> str:
    variants = [
        "I heard you. I'm switching to a deeper pass and I'll come right back.",
        "I've got it. I'm doing a deeper check now and I'll follow up.",
        "I'm on it. Let me work through that cleanly and I'll come back with the answer.",
    ]
    seed = abs(hash((display_name.lower(), int(time.time() // 60)))) % len(variants)
    return variants[seed]


def build_codex_progress_update(goal: str) -> str:
    variants = [
        "Codex is still working through that. ETA about 30 to 60 seconds.",
        "Still in progress with Codex now. I'll report back as soon as the result is ready.",
        "Codex is still running the pass. I'll bring the outcome back here when it lands.",
    ]
    seed = abs(hash((goal.lower()[:120], int(time.time() // 60)))) % len(variants)
    return variants[seed]


def build_codex_dispatch_ack(goal: str) -> str:
    variants = [
        "Handing that to Codex now. I'll post the result here.",
        "Queued with Codex. I'll bring the outcome back into this channel.",
        "Codex is on it. I'll follow with the useful result.",
        "I've passed that to Codex and I'll report back here.",
    ]
    seed = abs(hash((goal.lower()[:120], int(time.time() // 60)))) % len(variants)
    return variants[seed]


async def run_progress_notifier(send_update, done_event: asyncio.Event, timeline: list[tuple[float, str]]) -> int:
    sent = 0
    elapsed = 0.0
    for target_sec, text in timeline:
        wait_sec = max(0.1, float(target_sec) - elapsed)
        try:
            await asyncio.wait_for(done_event.wait(), timeout=wait_sec)
            return sent
        except asyncio.TimeoutError:
            try:
                await send_update(text)
                sent += 1
            except Exception as e:
                print(f"[discord] progress update failed: {e}")
            elapsed = float(target_sec)
    return sent


def build_text_progress_update(speaker: str) -> str:
    variants = [
        "Looking into it now.",
        "I'm still working through that now.",
        "Still checking it cleanly before I answer.",
        "I'm on it and I'll follow with the useful result.",
    ]
    seed = abs(hash((speaker.lower(), int(time.time() // 60)))) % len(variants)
    return variants[seed]


def build_codex_progress_update(goal: str) -> str:
    variants = [
        "Looking into it now with Codex.",
        "Still in progress with Codex now.",
        "Codex is still running the pass and I'll report back when it lands.",
    ]
    seed = abs(hash((goal.lower()[:120], int(time.time() // 60)))) % len(variants)
    return variants[seed]


async def run_progress_notifier(
    send_update,
    done_event: asyncio.Event,
    timeline: list[tuple[float, str]],
    repeat_text: str = "",
    repeat_every_sec: float = 0.0,
) -> int:
    sent = 0
    elapsed = 0.0
    for target_sec, text in timeline:
        wait_sec = max(0.1, float(target_sec) - elapsed)
        try:
            await asyncio.wait_for(done_event.wait(), timeout=wait_sec)
            return sent
        except asyncio.TimeoutError:
            try:
                await send_update(text)
                sent += 1
            except Exception as e:
                print(f"[discord] progress update failed: {e}")
            elapsed = float(target_sec)
    if repeat_text and float(repeat_every_sec or 0.0) > 0.0:
        while True:
            try:
                await asyncio.wait_for(done_event.wait(), timeout=max(1.0, float(repeat_every_sec)))
                return sent
            except asyncio.TimeoutError:
                try:
                    await send_update(repeat_text)
                    sent += 1
                except Exception as e:
                    print(f"[discord] progress update failed: {e}")
    return sent


def looks_like_operational_change_request(content: str) -> bool:
    cleaned = (content or "").strip().lower()
    if not cleaned:
        return False
    action_hints = [
        "can you make",
        "can you change",
        "can you update",
        "can you fix",
        "can you redo",
        "can you restyle",
        "can you redesign",
        "please change",
        "please update",
        "please fix",
        "please redo",
        "please restyle",
        "please redesign",
        "build ",
        "create ",
        "make ",
        "change ",
        "update ",
        "fix ",
        "redo ",
        "restyle ",
        "rework ",
        "polish ",
        "deploy ",
        "publish ",
        "redesign ",
        "retheme ",
        "implement ",
        "handle ",
        "finish ",
        "complete ",
        "revamp ",
        "rename ",
        "sync ",
        "apply ",
        "turn on ",
        "turn off ",
        "shut down ",
        "continue ",
        "keep going",
        "work on ",
        "move forward",
        "follow through",
        "take care of",
        "keep improving",
        "ship ",
    ]
    target_hints = [
        "website",
        "webpage",
        "site",
        "page",
        "theme",
        "style",
        "styles",
        "graphic",
        "graphics",
        "visual",
        "visuals",
        "motif",
        "look",
        "feel",
        "brand",
        "branding",
        "logo",
        "image",
        "discord",
        "bot",
        "project",
        "workflow",
        "automation",
        "stack",
        "server",
        "dashboard",
        "landing",
        "banner",
        "parallax",
        "channel",
        "guild",
        "sticky",
        "logo",
        "icon",
        "role",
        "page",
    ]
    if not any(hint in cleaned for hint in action_hints):
        return False
    return any(target in cleaned for target in target_hints)


def looks_like_contextual_followthrough_request(content: str) -> bool:
    cleaned = " ".join(str(content or "").split()).strip().lower()
    if not cleaned:
        return False
    markers = [
        "continue",
        "keep going",
        "keep working",
        "carry on",
        "move forward",
        "push forward",
        "follow through",
        "get it done",
        "finish it",
        "finish this",
        "complete it",
        "complete this",
        "work on it",
        "work on this",
        "take it from here",
        "keep improving",
        "let's improve",
        "lets improve",
    ]
    return any(marker in cleaned for marker in markers)


async def fetch_tts_mp3(text: str, voice: Optional[str] = None) -> str:
    fd, out_path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    client = await get_http_client()
    r = await client.post(
        f"{VOICE_URL}/speak",
        json={"text": text[:1800], "voice": voice or DEFAULT_VOICE},
        timeout=http_timeout(120),
    )
    r.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(r.content)
    return out_path

async def play_tts_in_voice(session, text: str):
    if not session.voice_client or not session.voice_client.is_connected():
        return
    out_path = None
    try:
        record_runtime_breadcrumb(
            "dispatch",
            severity="info",
            scene="voice",
            route="tts",
            connection=runtime_connection_summary(
                surface="voice",
                project_key=default_project_key_from_guild(session.guild),
                guild_id=getattr(session.guild, "id", ""),
                channel_id=getattr(session.text_channel, "id", ""),
                voice_connected=True,
            ),
            text_preview=compact_discord_text(text, 140),
        )
        out_path = await fetch_tts_mp3(text[:500], voice=guild_voice_prefs.get(session.guild.id, DEFAULT_VOICE))
        if session.voice_client.is_playing():
            session.voice_client.stop()
        session.voice_client.play(discord.FFmpegPCMAudio(out_path))
    except Exception as e:
        record_runtime_breadcrumb(
            "fallback",
            severity="warn",
            scene="voice",
            route="tts",
            connection=runtime_connection_summary(
                surface="voice",
                project_key=default_project_key_from_guild(session.guild),
                guild_id=getattr(session.guild, "id", ""),
                channel_id=getattr(session.text_channel, "id", ""),
                voice_connected=False,
            ),
            error=compact_discord_text(str(e), 180),
        )
        print(f"[discord-voice] tts play failed: {e}")
    finally:
        if out_path:
            try:
                os.remove(out_path)
            except Exception:
                pass


async def transcribe_wav_bytes(filename: str, wav_bytes: bytes) -> str:
    files = {"file": (filename, wav_bytes, "audio/wav")}
    client = await get_http_client()
    r = await client.post(f"{VOICE_URL}/transcribe", files=files, timeout=http_timeout(120))
    r.raise_for_status()
    return (r.json().get("text") or "").strip()


async def post_core_json(path: str, payload: dict[str, Any], timeout_sec: float, label: str) -> dict[str, Any]:
    client = await get_http_client()
    started = time.perf_counter()
    r = await client.post(f"{CORE_URL}{path}", json=payload, timeout=http_timeout(timeout_sec))
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    print(f"[discord] core {label} {elapsed_ms:.0f}ms status={r.status_code}")
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {"raw": data}


async def review_discord_action(goal: str, policy: Optional[dict[str, Any]] = None, *, action_kind: str = "implement", allow_write: bool = False, meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    current_policy = policy if isinstance(policy, dict) else {}
    review_meta = dict(meta or {})
    correlation_id = str(review_meta.get("correlation_id") or new_hermes_correlation_id()).strip()
    review_meta["correlation_id"] = correlation_id
    request_kind = str(review_meta.get("request_kind") or "action").strip().lower() or "action"
    if request_kind not in {"conversation", "lookup", "suggestion", "action", "block"}:
        request_kind = "action"
    summary_mode = infer_summary_mode_from_text(str(review_meta.get("summary_seed") or goal))
    raw_surface = str(review_meta.get("scene") or current_policy.get("surface") or "discord").strip().lower() or "discord"
    review_surface = "discord_voice" if raw_surface in {"voice", "discord_voice", "voice_channel"} else "discord_text"
    project_key = normalize_project_key_local(current_policy.get("project_key") or "")
    authorized_capabilities = current_policy.get("authorized_capabilities") if isinstance(current_policy.get("authorized_capabilities"), list) else []
    actor_role = str(current_policy.get("actor_role") or ("operator" if bool(current_policy.get("can_command") or current_policy.get("server_operator")) else "participant")).strip() or "participant"
    record_runtime_breadcrumb(
        "review",
        severity="info",
        scene=str(review_meta.get("scene") or current_policy.get("surface") or "discord").strip(),
        route=str(action_kind or "").strip(),
        connection=runtime_connection_summary(
            surface=str(current_policy.get("surface") or review_meta.get("scene") or "").strip(),
            project_key=str(current_policy.get("project_key") or "").strip(),
            guild_id=current_policy.get("guild_id") or "",
            channel_id=current_policy.get("channel_id") or "",
            route_status=str(review_meta.get("route_status") or ""),
            speaker=str(review_meta.get("speaker") or ""),
            request_kind=str(review_meta.get("request_kind") or ""),
            priority=str(review_meta.get("priority") or ""),
        ),
        action_kind=action_kind,
        allow_write=bool(allow_write),
    )
    payload: dict[str, Any] = {
        "surface": review_surface,
        "actor": {
            "discord_user_id": str(current_policy.get("discord_user_id") or current_policy.get("author_id") or "").strip(),
            "guild_id": str(current_policy.get("guild_id") or "").strip(),
            "channel_id": str(current_policy.get("channel_id") or "").strip(),
            "project_key": project_key,
            "actor_role": actor_role,
            "authorized_capabilities": authorized_capabilities,
            "can_command": bool(current_policy.get("can_command")),
            "server_operator": bool(current_policy.get("server_operator")),
            "visibility_mode": str(current_policy.get("visibility_mode") or "direct").strip() or "direct",
            "council_allowed": bool(current_policy.get("council_allowed")),
            "surface": raw_surface,
        },
        "project": project_key,
        "utterance": goal,
        "request_kind": request_kind,
        "summary_mode": summary_mode,
        "moderation_context": review_meta.get("moderation_context") if isinstance(review_meta.get("moderation_context"), dict) else {},
        "meta": {
            **review_meta,
            "action_kind": action_kind,
            "allow_write": bool(allow_write),
            "summary_mode": summary_mode,
            "source": "discord-bot",
        },
    }
    try:
        result = await post_core_json("/api/zeus/review", payload, max(20.0, DISCORD_LOOKUP_TIMEOUT_SEC), f"discord-review-{action_kind}")
        classification = str(result.get("classification") or "").strip().lower()
        approved = bool(result.get("status") == "ok" and classification not in {"blocked"})
        detail = compact_discord_text(str(result.get("user_reply") or result.get("plan_summary") or result.get("detail") or "Zeus review completed."), 1800)
        return {
            **result,
            "approved": approved,
            "detail": detail,
            "allow_write": bool(allow_write),
            "policy": result.get("access") if isinstance(result.get("access"), dict) else current_policy,
            "required_capabilities": [],
            "correlation_id": correlation_id,
        }
    except Exception as e:
        print(f"[discord-review] review failed: {e}")
        record_runtime_breadcrumb(
            "fallback",
            severity="error",
            scene=str(review_meta.get("scene") or current_policy.get("surface") or "discord").strip(),
            route=str(action_kind or "").strip(),
            connection=runtime_connection_summary(
                surface=str(current_policy.get("surface") or review_meta.get("scene") or "").strip(),
                project_key=str(current_policy.get("project_key") or "").strip(),
                guild_id=current_policy.get("guild_id") or "",
                channel_id=current_policy.get("channel_id") or "",
            ),
            error=compact_discord_text(str(e), 180),
        )
        await submit_runtime_diagnostic_receipt(
            current_policy,
            stage="review",
            severity="error",
            scene=str(review_meta.get("scene") or current_policy.get("surface") or "discord").strip(),
            route=str(action_kind or "").strip(),
            reason=str(e),
            meta={"request_kind": review_meta.get("request_kind") or "review", "priority": review_meta.get("priority") or "", "speaker": review_meta.get("speaker") or ""},
        )
        error_kind = getattr(getattr(e, "__class__", None), "__name__", "ReviewError")
        detail = {
            "error_kind": error_kind,
            "source_endpoint": f"{CORE_URL}/api/zeus/review",
            "retryable": True,
            "correlation_id": correlation_id,
            "message": compact_discord_text(str(e) or "Unknown review failure", 600),
        }
        return {
            "status": "error",
            "approved": False,
            "detail": json.dumps(detail, ensure_ascii=False),
            "allow_write": False,
            "policy": current_policy,
            "required_capabilities": [],
            "correlation_id": correlation_id,
        }


async def log_discord_denial(policy: Optional[dict[str, Any]], *, command_key: str, reason: str, goal: str = "", meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    current_policy = policy if isinstance(policy, dict) else {}
    return await upsert_discord_job_core(
        current_policy,
        job_id=f"discord_job_{uuid.uuid4().hex[:12]}",
        status="blocked",
        public_summary="Blocked",
        private_summary="",
        dm_status="blocked",
        detail_text=compact_discord_text(reason, 900),
        command_key=command_key,
        meta={
            "goal": compact_discord_text(goal, 280),
            "reason": compact_discord_text(reason, 320),
            **(meta or {}),
        },
    )


def hermes_attachment_refs(message: discord.Message) -> list[str]:
    refs: list[str] = []
    for attachment in getattr(message, "attachments", []) or []:
        url = str(getattr(attachment, "url", "") or "").strip()
        if url:
            refs.append(url)
    return refs


def new_hermes_job_id() -> str:
    return f"discord_job_{uuid.uuid4().hex[:12]}"


def new_hermes_correlation_id() -> str:
    return f"hermes_{uuid.uuid4().hex[:12]}"


def project_is_darius_backed(project_key: str) -> bool:
    normalized = normalize_project_key_local(project_key)
    return bool(normalized and normalized in DARIUS_BACKED_PROJECT_KEYS)


def build_hermes_intent(message: discord.Message, content: str, policy: Optional[dict[str, Any]] = None, *, priority: str = "immediate", media_request: Optional[dict[str, Any]] = None) -> HermesIntent:
    current_policy = policy if isinstance(policy, dict) else {}
    surface = str(current_policy.get("surface") or ("dm" if is_dm(message) else "public_channel")).strip() or "public_channel"
    resolved_project_key = infer_project_key_from_message(message, requested_project_key=str(current_policy.get("project_key") or ""), binding=current_policy.get("binding") if isinstance(current_policy.get("binding"), dict) else None)
    if looks_like_mount_olympus_web_request(content):
        resolved_project_key = "mount-olympus-web"
    elif normalize_project_key_local(resolved_project_key or current_policy.get("project_key") or "") in {"", "mount-olympus-core"} and looks_like_olympus_web_surface_request(content):
        resolved_project_key = "mount-olympus-web"
    elif not resolved_project_key and "underfoot" in str(content or "").lower():
        resolved_project_key = "underfoot"
    attachments = hermes_attachment_refs(message)
    media_payload = media_request if isinstance(media_request, dict) else {}
    authorized_operator = bool(current_policy.get("can_command") or current_policy.get("server_operator") or is_owner_member(message.author))
    intent_kind = "conversation"
    action_kind = "conversation"
    if looks_like_message_delete_request(content):
        intent_kind = "discord_admin"
        action_kind = "safe_cleanup"
    elif should_route_operational_request_to_discord_admin(content):
        intent_kind = "discord_admin"
        action_kind = "discord_admin"
    elif media_payload:
        intent_kind = "media"
        action_kind = str(media_payload.get("kind") or "image").strip() or "image"
    elif looks_like_operational_change_request(content) or (
        looks_like_contextual_followthrough_request(content)
        and bool(
            resolved_project_key
            or current_policy.get("project_key")
            or current_policy.get("binding")
            or str(current_policy.get("surface") or "").strip() == "project_channel"
        )
    ):
        intent_kind = "project_write"
        action_kind = "project_write"
    elif looks_like_slow_lookup_request(content):
        intent_kind = "lookup"
        action_kind = "lookup"
    return HermesIntent(
        intent_kind=intent_kind,
        action_kind=action_kind,
        goal=content,
        surface=surface,
        priority=priority,
        resolved_project_key=resolved_project_key,
        attachments=attachments,
        media_request=media_payload,
        requires_operator=bool(intent_kind in {"discord_admin", "project_write"}),
        correlation_id=new_hermes_correlation_id(),
        job_id=new_hermes_job_id(),
    )


def build_hermes_route_decision(intent: HermesIntent, policy: Optional[dict[str, Any]] = None) -> HermesRouteDecision:
    current_policy = policy if isinstance(policy, dict) else {}
    resolved_project_key = normalize_project_key_local(intent.resolved_project_key or current_policy.get("project_key") or "")
    if intent.intent_kind == "discord_admin":
        return HermesRouteDecision(
            executor_kind="discord_admin",
            resolved_project_key=resolved_project_key,
            route_status="direct_discord",
            route_mode="discord_native",
            review_action_kind="branding_sync" if "blueprint" in intent.goal.lower() or "branding" in intent.goal.lower() else "discord_admin",
            requires_review=True,
        )
    if intent.intent_kind == "media":
        return HermesRouteDecision(
            executor_kind="media",
            resolved_project_key=resolved_project_key,
            route_status="media_queue",
            route_mode="discord_media",
            review_action_kind="implement",
            requires_review=False,
        )
    if intent.intent_kind == "project_write":
        if project_is_darius_backed(resolved_project_key):
            return HermesRouteDecision(
                executor_kind="darius_project",
                resolved_project_key=resolved_project_key,
                route_status="route_pending",
                route_mode="local_darius",
                review_action_kind="deploy" if "deploy" in intent.goal.lower() or "restart" in intent.goal.lower() else "implement",
                requires_review=True,
            )
        return HermesRouteDecision(
            executor_kind="server_codex",
            resolved_project_key=resolved_project_key,
            route_status="codex_session_pending",
            route_mode="server_codex",
            review_action_kind="deploy" if "deploy" in intent.goal.lower() or "restart" in intent.goal.lower() else "implement",
            requires_review=True,
        )
    if intent.intent_kind == "lookup":
        return HermesRouteDecision(
            executor_kind="lookup",
            resolved_project_key=resolved_project_key,
            route_status="lookup_pending",
            route_mode="assistant_lookup",
        )
    return HermesRouteDecision(
        executor_kind="conversation",
        resolved_project_key=resolved_project_key,
        route_status="conversation_pending",
        route_mode="assistant_conversation",
    )


async def upsert_hermes_job(policy: dict[str, Any], *, intent: HermesIntent, route: HermesRouteDecision, status: str, public_summary: str = "", private_summary: str = "", dm_status: str = "", detail_text: str = "", verification: Optional[HermesVerificationResult] = None, command_key: str = "", extra_meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    verification_payload = verification if isinstance(verification, HermesVerificationResult) else HermesVerificationResult()
    meta = {
        "intent": asdict(intent),
        "route": asdict(route),
        "verification": asdict(verification_payload),
        **(extra_meta or {}),
    }
    return await upsert_discord_job_core(
        policy,
        job_id=intent.job_id,
        status=status,
        public_summary=public_summary,
        private_summary=private_summary,
        dm_status=dm_status,
        detail_text=detail_text,
        command_key=command_key or route.executor_kind or intent.action_kind,
        intent_kind=intent.intent_kind,
        executor_kind=route.executor_kind,
        resolved_project_key=route.resolved_project_key,
        route_status=route.route_status,
        verification_status=verification_payload.status,
        correlation_id=intent.correlation_id,
        meta=meta,
    )


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def looks_like_destructive_change_request(text: str) -> bool:
    cleaned = str(text or "").strip().lower()
    if not cleaned:
        return False
    if cleaned.startswith("confirm purge") or cleaned.startswith("confirm delete"):
        return False
    markers = [
        "delete",
        "remove",
        "purge",
        "wipe",
        "clear the chat",
        "clear chat",
        "nuke",
        "destroy",
        "drop",
    ]
    return any(marker in cleaned for marker in markers)


def _route_defaults_for_executor(executor_kind: str, requested_action: str = "implement") -> dict[str, Any]:
    executor = str(executor_kind or "conversation").strip() or "conversation"
    action = str(requested_action or "implement").strip().lower() or "implement"
    review_action = "conversation"
    requires_review = False
    route_status = "conversation_pending"
    route_mode = "assistant_conversation"
    if executor == "discord_admin":
        review_action = "branding_sync" if action == "branding_sync" else "discord_admin"
        requires_review = True
        route_status = "direct_discord"
        route_mode = "discord_native"
    elif executor == "media":
        review_action = "implement"
        requires_review = False
        route_status = "media_queue"
        route_mode = "discord_media"
    elif executor == "darius_project":
        review_action = "deploy" if action == "deploy" else "implement"
        requires_review = True
        route_status = "route_pending"
        route_mode = "local_darius"
    elif executor == "server_codex":
        review_action = "deploy" if action == "deploy" else "implement"
        requires_review = True
        route_status = "codex_session_pending"
        route_mode = "server_codex"
    elif executor == "lookup":
        route_status = "lookup_pending"
        route_mode = "assistant_lookup"
    return {
        "executor_kind": executor,
        "review_action_kind": review_action,
        "requires_review": requires_review,
        "route_status": route_status,
        "route_mode": route_mode,
    }


def fallback_hermes_action_plan(intent: HermesIntent, route: HermesRouteDecision, policy: Optional[dict[str, Any]] = None) -> HermesActionPlan:
    current_policy = policy if isinstance(policy, dict) else {}
    resolved_project = pretty_project_label(route.resolved_project_key or current_policy.get("project_key") or "Mount Olympus")
    destructive = looks_like_destructive_change_request(intent.goal)
    if intent.intent_kind == "discord_admin":
        return HermesActionPlan(
            summary=f"Zeus mapped a guarded Discord steward pass for {resolved_project}.",
            plan_steps=[
                "Inspect the live guild lane, blueprint, and requested Discord change.",
                "Apply only the safe channel/header/sticky mutation that matches the request.",
                "Read back the live Discord state and only mark success when it verifies.",
            ],
            executor_hint="discord_admin",
            requested_action="discord_admin",
            project_key_override=route.resolved_project_key,
            risk="high" if destructive else "low",
            requires_confirmation=destructive,
            public_ack=f"{resolved_project}: Zeus mapped the Discord steward pass and Hermes is handling it now.",
            private_note="Guarded auto applies safe Discord mutations directly. Destructive cleanup requires explicit confirmation.",
        )
    if intent.intent_kind == "media":
        kind = str(intent.media_request.get("kind") or intent.action_kind or "image").strip() or "image"
        return HermesActionPlan(
            summary=f"Zeus mapped a {kind} generation pass for {resolved_project}.",
            plan_steps=[
                "Turn the operator request and attachments into a focused media brief.",
                "Run the selected media model with the requested style and target.",
                "Return the created asset with prompt notes and delivery links.",
            ],
            executor_hint="media",
            requested_action=kind,
            project_key_override=route.resolved_project_key,
            risk="low",
            requires_confirmation=False,
            public_ack=f"{resolved_project}: Zeus mapped the media pass and Hermes is generating it now.",
            private_note="Preserve the operator references and return the generated asset with the revised prompt when available.",
        )
    if intent.intent_kind == "project_write":
        executor_label = "Darius" if route.executor_kind == "darius_project" else "Codex"
        return HermesActionPlan(
            summary=f"Zeus mapped the project work for {resolved_project} and assigned it to {executor_label}.",
            plan_steps=[
                "Turn the request into scoped implementation work with the right project route.",
                "Preserve existing features, update the workflow/todo, and use checkout-safe edits.",
                "Restart affected services, verify health, and report live status with proof.",
            ],
            executor_hint=route.executor_kind or "server_codex",
            requested_action="deploy" if route.review_action_kind == "deploy" else "implement",
            project_key_override=route.resolved_project_key,
            risk="high" if destructive else "medium",
            requires_confirmation=destructive,
            public_ack=f"{resolved_project}: Zeus mapped the work and {executor_label} is taking the pass now.",
            private_note="Do not delete features. Keep the todo/workflow updated, send live updates, restart affected services, and verify everything after changes.",
        )
    if intent.intent_kind == "lookup":
        return HermesActionPlan(
            summary="Zeus mapped an information pass.",
            plan_steps=[
                "Collect the relevant details from the right Olympus surfaces.",
                "Cross-check the answer before replying.",
            ],
            executor_hint="lookup",
            requested_action="lookup",
            project_key_override=route.resolved_project_key,
            risk="low",
            requires_confirmation=False,
            public_ack="Zeus mapped the lookup and Hermes is checking it now.",
            private_note="Return the useful answer without unnecessary execution chatter.",
        )
    return HermesActionPlan(
        summary="Zeus mapped a conversation response.",
        plan_steps=[
            "Read the request in context.",
            "Reply clearly and stay within the active project lane.",
        ],
        executor_hint="conversation",
        requested_action="conversation",
        project_key_override=route.resolved_project_key,
        risk="low",
        requires_confirmation=False,
        public_ack="Zeus mapped the reply and Hermes is handling it now.",
        private_note="Keep the reply concise and grounded in the active Olympus context.",
    )


async def build_zeus_execution_plan(intent: HermesIntent, route: HermesRouteDecision, policy: Optional[dict[str, Any]] = None) -> HermesActionPlan:
    fallback = fallback_hermes_action_plan(intent, route, policy)
    if intent.intent_kind == "conversation" or not DISCORD_ZEUS_MODEL_PLANNER:
        return fallback
    try:
        prompt = (
            "You are Zeus, chief planner for Mount Olympus. Return ONLY strict JSON with keys "
            "\"summary\",\"plan_steps\",\"executor_hint\",\"requested_action\",\"project_key_override\","
            "\"risk\",\"requires_confirmation\",\"public_ack\",\"private_note\".\n"
            "Rules:\n"
            "- executor_hint must be one of: discord_admin, darius_project, server_codex, media, lookup, conversation.\n"
            "- Keep plan_steps to 2-5 concrete steps.\n"
            "- For project write work, preserve existing features, update the workflow/todo, use the checkout-safe edit flow, send live updates, restart affected services, and verify health.\n"
            "- For Darius-backed local projects, prefer darius_project.\n"
            "- Guard destructive requests by setting requires_confirmation true.\n"
            "- public_ack should be short enough for Discord.\n\n"
            f"Intent kind: {intent.intent_kind}\n"
            f"Action kind: {intent.action_kind}\n"
            f"Current executor: {route.executor_kind}\n"
            f"Current project key: {route.resolved_project_key or 'none'}\n"
            f"Surface: {intent.surface}\n"
            f"Priority: {intent.priority}\n"
            f"Attachments: {json.dumps(intent.attachments)}\n"
            f"Goal: {intent.goal}"
        )
        response_text = await query_assistant_fast(
            prompt,
            mode="conversation",
            speaker=olympus_operator_name(),
            timeout_sec=min(45.0, DISCORD_LOOKUP_TIMEOUT_SEC),
            channel_meta={
                "project_key": route.resolved_project_key,
                "request_source": "zeus_planner",
                "room_kind": "planner",
                "room_label": "Zeus Planning",
            },
        )
        parsed = _extract_json_object(response_text)
        if not parsed:
            parsed = _extract_json_object(await query_assistant(prompt))
    except Exception as exc:
        print(f"[hermes-zeus] planning fallback: {exc}")
        return fallback
    if not parsed:
        return fallback
    allowed_executors = {"discord_admin", "darius_project", "server_codex", "media", "lookup", "conversation"}
    summary = compact_discord_text(str(parsed.get("summary") or fallback.summary), 260)
    raw_steps = parsed.get("plan_steps")
    if not isinstance(raw_steps, list):
        raw_steps = fallback.plan_steps
    plan_steps = [compact_discord_text(str(step), 220) for step in raw_steps if str(step).strip()][:5] or list(fallback.plan_steps)
    executor_hint = str(parsed.get("executor_hint") or fallback.executor_hint).strip()
    if executor_hint not in allowed_executors:
        executor_hint = fallback.executor_hint
    requested_action = str(parsed.get("requested_action") or fallback.requested_action).strip().lower() or fallback.requested_action
    if requested_action not in {"implement", "deploy", "discord_admin", "lookup", "image", "logo", "conversation"}:
        requested_action = fallback.requested_action
    override_project = normalize_project_key_local(parsed.get("project_key_override") or fallback.project_key_override)
    risk = str(parsed.get("risk") or fallback.risk).strip().lower()
    if risk not in {"low", "medium", "high"}:
        risk = fallback.risk
    requires_confirmation = bool(parsed.get("requires_confirmation")) or bool(fallback.requires_confirmation)
    public_ack = compact_discord_text(str(parsed.get("public_ack") or fallback.public_ack), 220)
    private_note = compact_discord_text(str(parsed.get("private_note") or fallback.private_note), 500)
    return HermesActionPlan(
        summary=summary,
        plan_steps=plan_steps,
        executor_hint=executor_hint,
        requested_action=requested_action,
        project_key_override=override_project,
        risk=risk,
        requires_confirmation=requires_confirmation,
        public_ack=public_ack,
        private_note=private_note,
    )


def apply_zeus_plan_to_route(intent: HermesIntent, route: HermesRouteDecision, plan: HermesActionPlan) -> HermesRouteDecision:
    updated = {**asdict(route)}
    override_project = normalize_project_key_local(plan.project_key_override or updated.get("resolved_project_key") or intent.resolved_project_key)
    if override_project:
        updated["resolved_project_key"] = override_project
    desired_executor = str(plan.executor_hint or updated.get("executor_kind") or route.executor_kind or "conversation").strip() or "conversation"
    if intent.intent_kind == "discord_admin":
        desired_executor = "discord_admin"
    elif intent.intent_kind == "media":
        desired_executor = "media"
    elif intent.intent_kind == "lookup":
        desired_executor = "lookup"
    elif intent.intent_kind == "conversation":
        desired_executor = "conversation"
    elif project_is_darius_backed(str(updated.get("resolved_project_key") or "")):
        desired_executor = "darius_project"
    updated.update(_route_defaults_for_executor(desired_executor, plan.requested_action))
    return HermesRouteDecision(**updated)


async def query_assistant(prompt: str) -> str:
    data = await post_core_json("/chat", {"prompt": prompt}, DISCORD_ASSISTANT_TIMEOUT_SEC, "planner")
    return data.get("response", "I could not produce a response.")


async def query_assistant_fast(
    prompt: str,
    mode: str = "conversation",
    speaker: str = "",
    timeout_sec: Optional[float] = None,
    channel_meta: Optional[dict[str, Any]] = None,
) -> str:
    route_timeout = timeout_sec if timeout_sec is not None else (DISCORD_VOICE_FAST_REPLY_TIMEOUT_SEC if mode == "voice" else DISCORD_FAST_REPLY_TIMEOUT_SEC)
    payload: dict[str, Any] = {"prompt": prompt, "mode": mode, "speaker": speaker}
    if channel_meta:
        payload["meta"] = channel_meta
        for key in ("room_key", "room_label", "room_kind", "session_id", "project_key", "channel_policy", "actor_role", "authorized_capabilities", "visibility_mode", "request_source", "summary_mode"):
            value = channel_meta.get(key)
            if value or (isinstance(value, list) and value == []):
                payload[key] = value
    data = await post_core_json(
        "/chat/discord",
        payload,
        route_timeout,
        f"discord-{mode}",
    )
    return data.get("response", "I could not produce a response.")


async def query_assistant_lookup(prompt: str, speaker: str = "", channel_meta: Optional[dict[str, Any]] = None) -> str:
    payload: dict[str, Any] = {"prompt": prompt, "mode": "lookup", "speaker": speaker}
    if channel_meta:
        payload["meta"] = channel_meta
        for key in ("room_key", "room_label", "room_kind", "session_id", "project_key", "channel_policy", "actor_role", "authorized_capabilities", "visibility_mode", "request_source", "summary_mode"):
            value = channel_meta.get(key)
            if value:
                payload[key] = value
    data = await post_core_json(
        "/chat/discord",
        payload,
        DISCORD_LOOKUP_TIMEOUT_SEC,
        "discord-lookup",
    )
    return data.get("response", "I could not produce a response.")


async def query_assistant_lookup_deep(prompt: str, speaker: str = "", channel_meta: Optional[dict[str, Any]] = None) -> str:
    payload: dict[str, Any] = {"prompt": prompt, "mode": "lookup_deep", "speaker": speaker}
    if channel_meta:
        payload["meta"] = channel_meta
        for key in ("room_key", "room_label", "room_kind", "session_id", "project_key", "channel_policy", "actor_role", "authorized_capabilities", "visibility_mode", "request_source", "summary_mode"):
            value = channel_meta.get(key)
            if value:
                payload[key] = value
    data = await post_core_json(
        "/chat/discord",
        payload,
        DISCORD_LOOKUP_TIMEOUT_SEC,
        "discord-lookup-deep",
    )
    return data.get("response", "I could not produce a response.")

def looks_like_slow_lookup_request(text: str) -> bool:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return False
    hints = [
        "look up",
        "check",
        "find",
        "search",
        "what is",
        "what are",
        "how much",
        "how many",
        "status of",
        "debug",
        "investigate",
        "compare",
        "summarize",
        "review",
        "analyze",
        "latest",
    ]
    return len(cleaned) > 140 or any(hint in cleaned for hint in hints)


def looks_like_suggestion_request(text: str) -> bool:
    cleaned = " ".join(str(text or "").split()).strip().lower()
    if not cleaned:
        return False
    markers = [
        "suggest",
        "suggestion",
        "idea",
        "proposal",
        "maybe we should",
        "what if we",
        "could we",
        "should we",
        "it would be nice",
        "it might help",
        "worthwhile",
        "improve",
        "make it better",
        "feature request",
    ]
    return any(marker in cleaned for marker in markers)


def classify_zeus_review_request(text: str, media_request: Optional[dict[str, Any]] = None) -> str:
    if looks_like_suggestion_request(text):
        return "suggestion"
    if media_request:
        return "action"
    if (
        looks_like_message_delete_request(text)
        or looks_like_discord_admin_request(text)
        or looks_like_operational_change_request(text)
        or looks_like_contextual_followthrough_request(text)
    ):
        return "action"
    return ""

def infer_summary_mode_from_text(text: str) -> str:
    lowered = " ".join(str(text or "").split()).strip().lower()
    if any(marker in lowered for marker in ("full detail", "all the detail", "everything", "deep dive", "full breakdown", "complete detail")):
        return "full_detail"
    if any(marker in lowered for marker in ("more detail", "more in depth", "expand on", "go deeper", "little more in depth", "in depth")):
        return "expanded"
    return "need_to_know"

SEVERE_MODERATION_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("severe_hate_slur", re.compile(r"\bn[\W_]*i[\W_]*g[\W_]*g(?:[\W_]*e[\W_]*r|[\W_]*a)\b", re.IGNORECASE)),
    ("severe_hate_slur", re.compile(r"\bk[\W_]*i[\W_]*k[\W_]*e\b", re.IGNORECASE)),
    ("severe_hate_slur", re.compile(r"\bf[\W_]*a[\W_]*g(?:[\W_]*g[\W_]*o[\W_]*t)?\b", re.IGNORECASE)),
    ("severe_hate_slur", re.compile(r"\bch[\W_]*i[\W_]*n[\W_]*k\b", re.IGNORECASE)),
]

def severe_moderation_matches(text: str) -> list[str]:
    cleaned = str(text or "")
    matched: list[str] = []
    for key, pattern in SEVERE_MODERATION_RULES:
        if pattern.search(cleaned):
            matched.append(key)
    return list(dict.fromkeys(matched))

def _write_moderation_receipt(entry: dict[str, Any]) -> None:
    try:
        DISCORD_MODERATION_RECEIPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(entry)
        payload.setdefault("ts", datetime.now(timezone.utc).isoformat())
        with DISCORD_MODERATION_RECEIPTS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[discord-moderation] receipt write failed: {exc}")

async def handle_severe_moderation(message: discord.Message, policy: Optional[dict[str, Any]], *, surface: str) -> bool:
    current_policy = policy if isinstance(policy, dict) else {}
    matches = severe_moderation_matches(message.content or "")
    if not matches:
        return False
    deleted = False
    delete_error = ""
    try:
        await message.delete()
        deleted = True
    except Exception as exc:
        delete_error = compact_discord_text(str(exc), 220)
    receipt = {
        "guild_id": str(getattr(getattr(message, "guild", None), "id", "") or "").strip(),
        "channel_id": str(getattr(getattr(message, "channel", None), "id", "") or "").strip(),
        "message_id": str(getattr(message, "id", "") or "").strip(),
        "discord_user_id": str(getattr(getattr(message, "author", None), "id", "") or "").strip(),
        "discord_name": str(getattr(getattr(message, "author", None), "display_name", getattr(getattr(message, "author", None), "name", "")) or "").strip(),
        "project_key": normalize_project_key_local(current_policy.get("project_key") or default_project_key_from_guild(message.guild)),
        "rules": matches,
        "deleted": deleted,
        "delete_error": delete_error,
        "surface": surface,
        "content_redacted": "[redacted severe moderation content]",
    }
    _write_moderation_receipt(receipt)
    record_runtime_breadcrumb(
        "moderation",
        severity="warn",
        scene=surface,
        route="severe_content",
        connection=runtime_connection_summary(
            surface=surface,
            project_key=receipt["project_key"],
            guild_id=receipt["guild_id"],
            channel_id=receipt["channel_id"],
            route_status="deleted" if deleted else "delete_failed",
        ),
        receipt_path=str(DISCORD_MODERATION_RECEIPTS_PATH),
        rules=",".join(matches),
    )
    await submit_runtime_diagnostic_receipt(
        current_policy,
        stage="moderation",
        severity="warn",
        scene=surface,
        route="severe_content",
        reason="Severe moderation rule triggered.",
        meta={"rules": matches, "deleted": deleted, "message_id": receipt["message_id"], "delete_error": delete_error},
    )
    await notify_owner_about_turn(
        f"Moderation receipt for {receipt['discord_name'] or 'user'} in {receipt['project_key'] or 'discord'}: severe policy trigger recorded.",
        include_project_lane=bool(receipt["project_key"] and (current_policy.get("can_view") or current_policy.get("can_command") or current_policy.get("server_operator"))),
        project_key=receipt["project_key"],
        requested_by=f"discord:{receipt['discord_user_id'] or 'unknown'}",
    )
    if policy_is_public_community(current_policy):
        await upsert_discord_community_feedback_receipt_core(
            current_policy,
            receipt_kind="moderation",
            summary=f"Severe moderation receipt for {receipt['discord_name'] or 'user'}",
            detail_text="Public community moderation triggered and the offending content was redacted from the Olympus-facing receipt.",
            message=message,
            status="closed" if deleted else "open",
            meta={"rules": matches, "deleted": deleted, "delete_error": delete_error},
        )
    return True

async def maybe_handle_public_community_feedback_turn(
    message: discord.Message,
    content: str,
    policy: Optional[dict[str, Any]],
    *,
    surface: str,
    priority: str,
    trace_meta: Optional[dict[str, Any]] = None,
    send_reply=None,
) -> tuple[bool, dict[str, Any]]:
    current_policy = policy if isinstance(policy, dict) else {}
    if surface != "public_channel" or not policy_is_public_community(current_policy):
        return False, {}
    if bool(current_policy.get("can_command") or current_policy.get("server_operator") or is_owner_member(message.author)):
        return False, {}
    channel_mode = channel_mode_for_message(message, current_policy)
    directed = bool(message_directed_to_hermes(message) or message_is_reply_to_bot(message))
    suggestion_like = bool(is_suggestion_prompt(content) or looks_like_operational_change_request(content))
    if not directed and channel_mode != "ambient_allowed" and not suggestion_like:
        return False, {}
    receipt_kind = infer_community_receipt_kind(content)
    if suggestion_like and receipt_kind == "feedback":
        receipt_kind = "feature_request"
    project_key = normalize_project_key_local(current_policy.get("project_key") or default_project_key_from_guild(message.guild))
    summary = compact_discord_text(content, 220)
    detail_text = str(content or "").strip()
    record_runtime_breadcrumb(
        "community_feedback_receipt",
        severity="info",
        scene=surface,
        route="community_feedback",
        connection=runtime_connection_summary(
            surface=surface,
            project_key=project_key,
            guild_id=current_policy.get("guild_id") or "",
            channel_id=current_policy.get("channel_id") or "",
            route_status="receipt_logged",
            priority=priority,
        ),
        receipt_kind=receipt_kind,
        summary_seed=summary,
    )
    receipt = await upsert_discord_community_feedback_receipt_core(
        current_policy,
        receipt_kind=receipt_kind,
        summary=summary,
        detail_text=detail_text,
        message=message,
        status="open",
        meta={"priority": priority, "trace": trace_meta or {}, "channel_mode": channel_mode},
    )
    if project_key and receipt_kind in {"bug_report", "feature_request"}:
        await queue_project_task(
            goal=f"Review community {receipt_kind.replace('_', ' ')}",
            project_key=project_key,
            entails=summary,
            source="discord_community_receipt",
            requested_by=f"discord:{getattr(message.author, 'id', 0)}",
        )
    await route_public_community_owner_notice(
        current_policy,
        event_key=receipt_kind,
        summary=f"Public {pretty_project_label(project_key)} {receipt_kind.replace('_', ' ')} receipt: {summary}",
        requested_by=f"discord:{getattr(message.author, 'id', 0)}",
    )
    if receipt_kind == "bug_report":
        reply = "Bug report logged. Olympus has the receipt and the project lane can review it."
    elif receipt_kind == "feature_request":
        reply = "Feature request logged. Olympus has the receipt and it will be reviewed through the project workflow."
    else:
        reply = "Feedback logged. Olympus has the receipt and the project lane can review it."
    if send_reply is not None:
        await send_reply(reply)
    else:
        await message.reply(compact_discord_text(reply, 1800), mention_author=False)
    return True, {"action": "community_receipt", "reply": reply, "receipt": receipt}


async def notify_owner_about_turn(message: str, *, include_project_lane: bool = False, project_key: str = "", requested_by: str = "") -> list[str]:
    delivered: list[str] = []
    owner_message = compact_discord_text(message, 1800)
    for owner_user in await resolve_owner_users():
        try:
            await owner_user.send(owner_message)
            delivered.append(str(getattr(owner_user, "id", 0)))
        except Exception as exc:
            print(f"[discord-runtime] owner notify failed for {getattr(owner_user, 'id', 'unknown')}: {exc}")
    if include_project_lane and project_key:
        try:
            await queue_project_task(
                goal=message[:240],
                project_key=project_key,
                entails=compact_discord_text(message, 800),
                source="discord_suggestion",
                requested_by=requested_by or "discord-bot",
            )
        except Exception as exc:
            print(f"[discord-runtime] project lane notify failed: {exc}")
    return delivered

async def route_public_community_owner_notice(
    policy: Optional[dict[str, Any]],
    *,
    event_key: str,
    summary: str,
    requested_by: str = "",
    include_project_lane: bool = False,
) -> list[str]:
    current_policy = policy if isinstance(policy, dict) else {}
    project_key = normalize_project_key_local(current_policy.get("project_key") or "")
    delivered = await notify_owner_about_turn(
        summary,
        include_project_lane=include_project_lane,
        project_key=project_key,
        requested_by=requested_by or "discord-bot",
    )
    record_runtime_breadcrumb(
        "community_owner_route",
        severity="info" if delivered else "warn",
        scene="discord",
        route=f"community_{event_key}",
        connection=runtime_connection_summary(
            surface=str(current_policy.get("surface") or "public_channel"),
            project_key=project_key,
            guild_id=str(current_policy.get("guild_id") or ""),
            channel_id=str(current_policy.get("channel_id") or ""),
            route_status="owner_notified" if delivered else "owner_unresolved",
        ),
        event_key=event_key,
        delivered=len(delivered),
    )
    return delivered

async def list_discord_community_feedback_receipts_core(
    *,
    project_key: str,
    guild_id: str = "",
    status: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    try:
        data = await core_get(
            "/discord/community-feedback/list",
            {
                "token": CONTROL_TOKEN,
                "project_key": normalize_project_key_local(project_key),
                "guild_id": str(guild_id or "").strip(),
                "status": str(status or "").strip(),
                "limit": max(1, min(int(limit or 10), 40)),
            },
        )
    except Exception as exc:
        print(f"[discord-policy] community feedback list failed: {exc}")
        return []
    rows = data.get("rows", []) if isinstance(data, dict) else []
    return [row for row in rows if isinstance(row, dict)]


async def handle_zeus_suggestion_turn(
    message: discord.Message,
    content: str,
    policy: Optional[dict[str, Any]],
    *,
    surface: str,
    priority: str,
    trace_meta: Optional[dict[str, Any]] = None,
    send_reply=None,
) -> tuple[bool, dict[str, Any]]:
    current_policy = policy if isinstance(policy, dict) else {}
    if classify_zeus_review_request(content) != "suggestion":
        return False, {}
    authorized = bool(current_policy.get("can_command") or current_policy.get("server_operator") or is_owner_member(message.author))
    project_key = normalize_project_key_local(current_policy.get("project_key") or default_project_key_from_guild(message.guild))
    review = await review_discord_action(
        content,
        current_policy,
        action_kind="implement",
        allow_write=authorized,
        meta={
            "scene": surface,
            "request_kind": "suggestion",
            "priority": priority,
            "summary_seed": content,
            "trace": trace_meta or {},
        },
    )
    if not review.get("approved"):
        note = compact_discord_text(str(review.get("user_reply") or "I noted that suggestion, but I did not promote it to the project lane yet."), 1800)
        record_runtime_breadcrumb(
            "block",
            severity="warn",
            scene=surface,
            route="suggestion",
            connection=runtime_connection_summary(
                surface=surface,
                project_key=project_key,
                guild_id=current_policy.get("guild_id") or "",
                channel_id=current_policy.get("channel_id") or "",
                route_status="review_blocked",
                priority=priority,
            ),
            reason=compact_discord_text(str(review.get("detail") or note), 220),
        )
        if send_reply is not None:
            await send_reply(note)
        else:
            await message.reply(note, mention_author=False)
        return True, {"action": "reply", "reply": note, "review": review}
    if authorized:
        await notify_owner_about_turn(
            f"Suggestion from {getattr(message.author, 'display_name', getattr(message.author, 'name', 'user'))}: {content}",
            include_project_lane=True,
            project_key=project_key,
            requested_by=f"discord:{getattr(message.author, 'id', 0)}",
        )
        note = compact_discord_text(str(review.get("user_reply") or "I sent that to the owner and the project lane."), 1800)
    else:
        await notify_owner_about_turn(
            f"Suggestion from {getattr(message.author, 'display_name', getattr(message.author, 'name', 'user'))}: {content}",
            include_project_lane=False,
            project_key=project_key,
            requested_by=f"discord:{getattr(message.author, 'id', 0)}",
        )
        note = compact_discord_text(str(review.get("user_reply") or "I sent that to the owner."), 1800)
    record_runtime_breadcrumb(
        "dispatch",
        severity="info",
        scene=surface,
        route="suggestion",
        connection=runtime_connection_summary(
            surface=surface,
            project_key=project_key,
            guild_id=current_policy.get("guild_id") or "",
            channel_id=current_policy.get("channel_id") or "",
            route_status="notified",
            priority=priority,
        ),
        notified_project_lane=bool(authorized),
    )
    if send_reply is not None:
        await send_reply(note)
    else:
        await message.reply(note, mention_author=False)
    return True, {"action": "suggestion_notified", "reply": note, "review": review}


async def gate_zeus_action_turn(
    message: discord.Message,
    content: str,
    policy: Optional[dict[str, Any]],
    *,
    surface: str,
    priority: str,
    action_kind: str,
    trace_meta: Optional[dict[str, Any]] = None,
    send_reply=None,
) -> tuple[bool, dict[str, Any]]:
    current_policy = policy if isinstance(policy, dict) else {}
    if classify_zeus_review_request(content) != "action":
        return False, {}
    authorized = bool(current_policy.get("can_command") or current_policy.get("server_operator") or is_owner_member(message.author))
    project_key = normalize_project_key_local(current_policy.get("project_key") or default_project_key_from_guild(message.guild))
    review = await review_discord_action(
        content,
        current_policy,
        action_kind=action_kind,
        allow_write=authorized,
        meta={
            "scene": surface,
            "request_kind": "action",
            "priority": priority,
            "summary_seed": content,
            "trace": trace_meta or {},
        },
    )
    if not review.get("approved") or not authorized:
        reply = build_owner_access_denied_reply(project_key) if not authorized else compact_discord_text(str(review.get("user_reply") or build_owner_access_denied_reply(project_key)), 1800)
        record_runtime_breadcrumb(
            "block",
            severity="warn",
            scene=surface,
            route=action_kind,
            connection=runtime_connection_summary(
                surface=surface,
                project_key=project_key,
                guild_id=current_policy.get("guild_id") or "",
                channel_id=current_policy.get("channel_id") or "",
                route_status="denied",
                priority=priority,
            ),
            reason=compact_discord_text(str(review.get("detail") or reply), 220),
        )
        await log_discord_denial(current_policy, command_key=action_kind, reason=reply, goal=content, meta={"review": review, "surface": surface, "priority": priority, "stage": "review_block"})
        await submit_runtime_diagnostic_receipt(
            current_policy,
            stage="review_block",
            severity="warn",
            scene=surface,
            route=action_kind,
            reason=reply,
            meta={"request_kind": "action", "priority": priority, "speaker": getattr(message.author, "display_name", getattr(message.author, "name", "user"))},
        )
        if send_reply is not None:
            await send_reply(reply)
        else:
            await message.reply(reply, mention_author=False)
        await notify_owner_about_turn(
            f"Denied {action_kind} request from {getattr(message.author, 'display_name', getattr(message.author, 'name', 'user'))}: {content}",
            include_project_lane=False,
            project_key=project_key,
            requested_by=f"discord:{getattr(message.author, 'id', 0)}",
        )
        return True, {"action": "reply", "reply": reply, "review": review}
    return False, {"review": review, "project_key": project_key}

def build_fast_lookup_ack(message: discord.Message) -> str:
    user_text = (message.content or "").strip()
    variants = [
        "On it. I'm checking that now and I'll come back with the useful answer.",
        "Let me look that up properly. I'll follow with the clean result.",
        "I'm pulling that together now. I'll reply with the answer once I have it.",
        "Give me a moment. I'm checking the details so I can answer cleanly.",
    ]
    seed = abs(hash((user_text.lower(), getattr(message.author, "id", 0), int(time.time() // 60)))) % len(variants)
    return variants[seed]


def looks_like_discord_admin_request(text: str) -> bool:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return False
    admin_markers = [
        "create channel",
        "make channel",
        "new channel",
        "rename channel",
        "channel name",
        "change the name of the",
        "change the general channel",
        "rename the general",
        "general channel",
        "channel header",
        "change the header",
        "rename the header",
        "server header",
        "change the theme of the channels",
        "theme of the channels",
        "theme the channels",
        "clear the chat",
        "clear chat",
        "delete channel",
        "delete messages",
        "lock channel",
        "unlock channel",
        "purge messages",
        "purge chat",
        "set permissions",
        "change permissions",
        "allow role",
        "deny role",
        "private channel",
        "category",
        "discord server",
        "discord channel",
        "server permissions",
        "rename server",
        "rename guild",
        "sync branding",
        "sync channels",
        "sticky message",
        "pinned message",
        "server icon",
        "server banner",
        "fix the discord",
        "fix discord",
        "discord channel",
        "discord channels",
        "redesign the discord",
        "redesign discord",
        "papr influence",
        "papr branding",
        "remove papr",
        "lose the papr influence",
        "raffles section",
        "rename the raffles",
        "change the raffles",
        "fix the raffles",
    ]
    if any(marker in cleaned for marker in admin_markers):
        return True
    if ("channel" in cleaned or "channels" in cleaned or "header" in cleaned) and any(
        marker in cleaned for marker in ["rename", "change", "fix", "redesign", "revamp", "theme", "clean up", "update"]
    ):
        return True
    if "papr" in cleaned and any(marker in cleaned for marker in ["discord", "channel", "channels", "guild", "server", "branding", "raffles", "section"]):
        return True
    return False


def should_route_operational_request_to_discord_admin(text: str) -> bool:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return False
    if looks_like_discord_admin_request(cleaned):
        return True
    operation_markers = [
        "change",
        "rename",
        "fix",
        "redesign",
        "revamp",
        "theme",
        "update",
        "sync",
        "clean up",
        "rework",
        "adjust",
    ]
    discord_targets = [
        "channel",
        "channels",
        "header",
        "stickies",
        "sticky",
        "pinned",
        "branding",
        "server",
        "guild",
        "category",
        "section",
        "discord",
        "general",
        "raffles",
    ]
    return any(marker in cleaned for marker in operation_markers) and any(target in cleaned for target in discord_targets)

async def maybe_send_fast_ack(message: discord.Message, state: dict, priority: str) -> bool:
    if not DISCORD_FAST_ACK_ENABLED or priority == "ambient":
        return False
    if not looks_like_slow_lookup_request(message.content or ""):
        return False
    now = time.time()
    if now - float(state.get("last_fast_ack_ts", 0.0) or 0.0) < DISCORD_FAST_ACK_MIN_INTERVAL_SEC:
        return False
    await message.reply(build_fast_lookup_ack(message), mention_author=False)
    state["last_fast_ack_ts"] = now
    return True


async def finish_text_background_lookup(
    channel: discord.abc.Messageable,
    requester: discord.abc.User,
    speaker: str,
    content: str,
    request_kind: str = "lookup",
    channel_meta: Optional[dict[str, Any]] = None,
    policy: Optional[dict[str, Any]] = None,
    job_id: str = "",
):
    started = time.perf_counter()
    current_policy = policy if isinstance(policy, dict) else {}
    if not normalize_project_key_local(current_policy.get("project_key") or ""):
        inferred_project_key = ""
        default_project_key = normalize_project_key_local(default_project_key_from_guild(getattr(channel, "guild", None)))
        if looks_like_mount_olympus_web_request(content):
            inferred_project_key = "mount-olympus-web"
        elif default_project_key == "mount-olympus-core" and looks_like_olympus_web_surface_request(content):
            inferred_project_key = "mount-olympus-web"
        elif "underfoot" in str(content or "").strip().lower():
            inferred_project_key = "underfoot"
        if inferred_project_key:
            current_policy = dict(current_policy)
            current_policy["project_key"] = inferred_project_key
    surface = str(current_policy.get("surface") or ("dm" if isinstance(channel, discord.DMChannel) else "public_channel")).strip()
    record_runtime_breadcrumb(
        "dispatch",
        severity="info",
        scene="text",
        route=str(request_kind or "lookup").strip(),
        connection=runtime_connection_summary(
            surface=surface,
            project_key=str(current_policy.get("project_key") or ""),
            guild_id=current_policy.get("guild_id") or "",
            channel_id=current_policy.get("channel_id") or "",
            request_kind=request_kind,
            speaker=speaker,
        ),
        job_id=job_id,
        source="text_background_lookup",
    )
    done_event = asyncio.Event()
    progress_state = {"sent": 0}

    async def _send_progress(text: str):
        progress_state["sent"] += 1
        if surface == "dm":
            await channel.send(compact_discord_text(text, 240))
        elif surface == "project_channel":
            await channel.send(compact_discord_text(text, 240))
        else:
            await channel.send(_public_status_text(text))

    progress_task = asyncio.create_task(
        run_progress_notifier(
            _send_progress,
            done_event,
            [
                (DISCORD_PROGRESS_FIRST_SEC, build_text_progress_update(speaker)),
                (DISCORD_PROGRESS_SECOND_SEC, "Still working through that now."),
            ],
            repeat_text="Still working through that now.",
            repeat_every_sec=DISCORD_PROGRESS_REPEAT_SEC,
        )
    )
    try:
        normalized_kind = str(request_kind or "lookup").strip().lower()
        if normalized_kind == "conversation":
            raw_reply = await query_assistant_fast(content, mode="conversation", speaker=speaker, channel_meta=channel_meta, timeout_sec=DISCORD_LOOKUP_TIMEOUT_SEC)
        elif normalized_kind == "lookup":
            raw_reply = await query_assistant_lookup(content, speaker=speaker, channel_meta=channel_meta)
        else:
            raw_reply = await query_assistant_lookup_deep(content, speaker=speaker, channel_meta=channel_meta)
        reply = compact_discord_text((raw_reply or "").strip(), 1900)
        if not reply:
            return
        dm_status = "not_needed"
        if surface == "dm":
            if progress_state["sent"]:
                reply = compact_discord_text(f"Done. {reply}", 1900)
            await channel.send(reply)
        else:
            dm_sent, dm_status = await send_detail_dm(requester, compact_discord_text(reply + build_private_activity_footer(job_id), 1900))
            if surface == "project_channel":
                await channel.send(_project_safe_completion_text(dm_sent=dm_sent))
            else:
                await channel.send(_public_status_text("Task completed. Detailed DM sent." if dm_sent else "Task completed. DM delivery failed."))
        if job_id:
            await upsert_discord_job_core(
                current_policy,
                job_id=job_id,
                status="completed",
                public_summary="Completed",
                private_summary=reply,
                dm_status=dm_status,
                detail_text=reply,
                command_key=f"text_{request_kind}",
                meta={"request_kind": request_kind, "surface": surface},
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        print(f"[discord] background {request_kind} delivered ms={elapsed_ms:.0f}")
    except Exception as e:
        print(f"[discord] background {request_kind} failed: {e}")
        record_runtime_breadcrumb(
            "fallback",
            severity="error",
            scene="text",
            route=str(request_kind or "lookup").strip(),
            connection=runtime_connection_summary(
                surface=surface,
                project_key=str(current_policy.get("project_key") or ""),
                guild_id=current_policy.get("guild_id") or "",
                channel_id=current_policy.get("channel_id") or "",
                request_kind=request_kind,
                speaker=speaker,
            ),
            error=compact_discord_text(str(e), 220),
        )
        if surface == "dm":
            await channel.send(compact_discord_text(f"I hit a block while working that through: {e}", 1800))
        elif surface == "project_channel":
            await channel.send(_project_blocked_text("Detailed status stays in DM/UI."))
        else:
            await channel.send(_public_status_text("Blocked for now."))
        if job_id:
            await upsert_discord_job_core(
                current_policy,
                job_id=job_id,
                status="failed",
                public_summary="Blocked",
                private_summary="",
                dm_status="error",
                detail_text=compact_discord_text(str(e), 600),
                command_key=f"text_{request_kind}",
                meta={"request_kind": request_kind, "surface": surface, "error": compact_discord_text(str(e), 300)},
            )
        await submit_runtime_diagnostic_receipt(
            current_policy,
            stage="dispatch",
            severity="error",
            scene="text",
            route=str(request_kind or "lookup").strip(),
            reason=str(e),
            meta={"speaker": speaker, "job_id": job_id, "request_kind": request_kind},
        )
    finally:
        done_event.set()
        if not progress_task.done():
            progress_task.cancel()
def default_project_key_from_guild(guild: Optional[discord.Guild]) -> str:
    blueprint = discord_ops_blueprint(guild_name=(guild.name if guild else ""))
    mapped = normalize_project_key_local(blueprint.get("project_key") if isinstance(blueprint, dict) else "")
    if mapped:
        return mapped
    raw = (guild.name if guild else "discord").strip().lower()
    clean = "".join(ch for ch in raw if ch.isalnum() or ch in "-_").strip("-_")
    return clean[:80] or "discord"


def project_site_url(project_key: str = "") -> str:
    normalized = normalize_project_key_local(project_key)
    if normalized == "papr":
        return PAPR_SITE_URL
    if normalized in {"underfoot", "underfoot-world"}:
        return UNDERFOOT_SITE_URL
    return OLYMPUS_SITE_URL


def project_event_label(project_key: str = "") -> str:
    normalized = normalize_project_key_local(project_key)
    if normalized == "papr":
        return "PAPR"
    if normalized in {"underfoot", "underfoot-world"}:
        return "Underfoot"
    return pretty_project_label(normalized or "Olympus")


async def core_post(path: str, payload: dict) -> dict:
    client = await get_http_client()
    r = await client.post(f"{CORE_URL}{path}", json=payload, timeout=http_timeout(900))
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {"status": "ok", "data": data}


async def core_put(path: str, payload: dict) -> dict:
    client = await get_http_client()
    r = await client.put(f"{CORE_URL}{path}", json=payload, timeout=http_timeout(900))
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {"status": "ok", "data": data}


async def core_get(path: str, params: Optional[dict[str, Any]] = None) -> dict:
    client = await get_http_client()
    url = f"{CORE_URL.removesuffix('/api')}{path}" if str(path or "").startswith("/api/") else f"{CORE_URL}{path}"
    r = await client.get(url, params=params or {}, timeout=http_timeout(900))
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {"status": "ok", "data": data}


async def core_get_bytes(path: str, params: Optional[dict[str, Any]] = None) -> tuple[bytes, str]:
    client = await get_http_client()
    url = f"{CORE_URL.removesuffix('/api')}{path}" if str(path or "").startswith("/api/") else f"{CORE_URL}{path}"
    r = await client.get(url, params=params or {}, timeout=http_timeout(900))
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "")


def underfoot_bridge_configured() -> bool:
    return bool(UNDERFOOT_BRIDGE_URL and UNDERFOOT_BRIDGE_TOKEN)


def underfoot_bridge_headers(user_id: int, user_name: str = "") -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {UNDERFOOT_BRIDGE_TOKEN}",
        "X-Underfoot-Bridge-Token": UNDERFOOT_BRIDGE_TOKEN,
        "X-Discord-User-Id": str(user_id),
    }
    clean_name = (user_name or "").strip()
    if clean_name:
        headers["X-Discord-User-Name"] = clean_name[:120]
    return headers


async def underfoot_bridge_get(path: str, user_id: int, user_name: str = "", params: Optional[dict[str, Any]] = None) -> dict:
    if not underfoot_bridge_configured():
        raise RuntimeError("Underfoot bridge env is not configured on the Discord bot.")
    client = await get_http_client()
    r = await client.get(
        f"{UNDERFOOT_BRIDGE_URL}{path}",
        params=params or {},
        headers=underfoot_bridge_headers(user_id, user_name),
        timeout=http_timeout(UNDERFOOT_BRIDGE_TIMEOUT_SEC),
    )
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {"status": "ok", "data": data}


async def underfoot_bridge_post(path: str, user_id: int, user_name: str = "", payload: Optional[dict[str, Any]] = None) -> dict:
    if not underfoot_bridge_configured():
        raise RuntimeError("Underfoot bridge env is not configured on the Discord bot.")
    client = await get_http_client()
    r = await client.post(
        f"{UNDERFOOT_BRIDGE_URL}{path}",
        json=payload or {},
        headers=underfoot_bridge_headers(user_id, user_name),
        timeout=http_timeout(UNDERFOOT_BRIDGE_TIMEOUT_SEC),
    )
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {"status": "ok", "data": data}


async def underfoot_bridge_get_file(path: str, user_id: int, user_name: str = "") -> tuple[bytes, str, str]:
    if not underfoot_bridge_configured():
        raise RuntimeError("Underfoot bridge env is not configured on the Discord bot.")
    client = await get_http_client()
    r = await client.get(
        f"{UNDERFOOT_BRIDGE_URL}{path}",
        headers=underfoot_bridge_headers(user_id, user_name),
        timeout=http_timeout(UNDERFOOT_BRIDGE_TIMEOUT_SEC),
    )
    r.raise_for_status()
    content_type = r.headers.get("content-type", "application/octet-stream")
    filename = "underfoot_capture.png"
    disposition = r.headers.get("content-disposition", "")
    if "filename=" in disposition:
        filename = disposition.split("filename=", 1)[1].strip().strip('"')
    return r.content, content_type, filename


def render_underfoot_bridge_result(tool_name: str, payload: dict[str, Any]) -> str:
    result = payload.get("result", {}) if isinstance(payload, dict) else {}
    ok = bool(result.get("ok", payload.get("status") == "ok"))
    duration = result.get("duration_sec", 0)
    returncode = result.get("returncode", 0)
    lines = [f"Underfoot tool `{tool_name}` {'completed' if ok else 'failed'} in {duration}s (rc={returncode})."]
    preview = (
        result.get("stdout")
        or result.get("unity_log")
        or result.get("stderr")
        or payload.get("detail")
        or ""
    )
    preview = compact_discord_text(str(preview or ""), 1300)
    if preview:
        lines.append(f"```text\n{preview.replace('```', '')}\n```")
    return compact_discord_text("\n".join(lines), 1800)


async def create_media_job(
    media_type: str,
    prompt: str,
    output_target: str = "",
    source_asset: str = "",
    settings: Optional[dict[str, Any]] = None,
    model_key: str = "",
    wait_for_completion: bool = True,
) -> dict[str, Any]:
    return await core_post(
        "/media/jobs/create",
        {
            "token": CONTROL_TOKEN,
            "media_type": media_type,
            "prompt": prompt,
            "model_key": model_key,
            "source_asset": source_asset,
            "output_target": output_target,
            "settings": settings or {},
            "wait_for_completion": wait_for_completion,
        },
    )


def parse_image_request_parts(details: str) -> tuple[str, str, dict[str, Any]]:
    parts = parse_pipe_parts(details)
    prompt = parts[0] if parts else ""
    output_target = ""
    settings: dict[str, Any] = {}
    candidate_tail = parts[1:] if len(parts) > 1 else []
    if candidate_tail:
        first_tail = candidate_tail[0]
        if first_tail.startswith("{") and first_tail.endswith("}"):
            settings = json.loads(first_tail)
        else:
            output_target = first_tail
            if len(candidate_tail) > 1 and candidate_tail[1].startswith("{") and candidate_tail[1].endswith("}"):
                settings = json.loads(candidate_tail[1])
    return prompt, output_target, settings


def infer_media_style_key(prompt: str, media_kind: str = "image") -> str:
    lowered = str(prompt or "").strip().lower()
    if not lowered:
        return ""
    if any(token in lowered for token in ["olympus", "zeus", "Operator", "greek", "myth", "titan", "thunder", "lightning"]):
        return "olympus_zeus_arcade_fantasy"
    if any(token in lowered for token in ["gate", "landing", "parallax", "sunrise", "sunset", "forest", "banner", "hero image"]):
        return "olympus_gate_parallax"
    if media_kind == "logo":
        return "olympus_zeus_arcade_fantasy"
    return ""


def infer_discord_media_request(text: str) -> dict[str, Any] | None:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return None
    lowered = cleaned.lower()
    action_markers = [
        "generate ",
        "create ",
        "make ",
        "design ",
        "draw ",
        "render ",
        "illustrate ",
        "paint ",
        "craft ",
    ]
    target_markers = [
        "image",
        "picture",
        "art",
        "artwork",
        "illustration",
        "portrait",
        "logo",
        "wordmark",
        "banner",
        "background",
        "cover",
        "poster",
        "avatar",
        "icon",
    ]
    if not any(marker in lowered for marker in action_markers):
        return None
    if not any(marker in lowered for marker in target_markers):
        return None
    media_kind = "logo" if any(marker in lowered for marker in ["logo", "wordmark", "brand mark", "icon"]) else "image"
    prompt = cleaned
    trim_patterns = [
        r"^(?:hey|hi|hello)\s+hermes[,:]?\s*",
        r"^(?:please\s+)?(?:can you|could you|would you|will you)\s+",
        r"^(?:please\s+)?(?:generate|create|make|design|draw|render|illustrate|paint|craft)\s+(?:me\s+|us\s+)?",
        r"^(?:an?\s+)?(?:image|picture|photo|art(?:work)?|illustration|portrait|logo|wordmark|banner|background|cover|poster|avatar|icon)\s+",
        r"^(?:of|for)\s+",
    ]
    for pattern in trim_patterns:
        prompt = re.sub(pattern, "", prompt, flags=re.IGNORECASE).strip()
    prompt = re.sub(r"\s+(?:please|for me)\s*$", "", prompt, flags=re.IGNORECASE).strip(" .!?")
    if len(prompt) < 4:
        prompt = cleaned
    settings: dict[str, Any] = {}
    if media_kind == "logo":
        settings["background"] = "transparent"
        settings["output_format"] = "png"
        settings["size"] = "1024x1024"
        model_key = "image_logo_fast"
    else:
        if any(marker in lowered for marker in ["banner", "background", "cover", "hero image", "landing page"]):
            settings["size"] = "1536x864"
        else:
            settings["size"] = "1024x1024"
        model_key = ""
    style_key = infer_media_style_key(prompt, media_kind=media_kind)
    if style_key:
        settings["style_key"] = style_key
    return {
        "kind": media_kind,
        "prompt": prompt,
        "settings": settings,
        "model_key": model_key,
        "raw_text": cleaned,
    }


def discord_media_access_allowed(policy: Optional[dict[str, Any]], author: discord.abc.User, surface: str = "dm") -> bool:
    current_policy = policy if isinstance(policy, dict) else {}
    if is_owner_member(author):
        return True
    if bool(current_policy.get("server_operator") or current_policy.get("can_command")):
        return True
    capabilities = {str(item).strip().lower() for item in (current_policy.get("authorized_capabilities") or []) if str(item).strip()}
    if "media_job" not in capabilities:
        return False
    if surface == "dm":
        return bool(current_policy.get("linked"))
    return bool(current_policy.get("can_view") or current_policy.get("linked"))


def build_media_progress_update(media_kind: str = "image") -> str:
    label = "logo" if str(media_kind or "").strip().lower() == "logo" else "image"
    variants = [
        f"Rendering that {label} now.",
        f"Still working on that {label} now.",
        f"I'm still generating that {label}.",
        f"Still building that {label} for you.",
    ]
    seed = abs(hash((label, int(time.time() // 60)))) % len(variants)
    return variants[seed]


async def deliver_media_job_result(channel: discord.abc.Messageable, requester: discord.abc.User, job: dict[str, Any], intro: str = "Generated image ready.", policy: Optional[dict[str, Any]] = None) -> None:
    current_policy = policy if isinstance(policy, dict) else {}
    surface = str(current_policy.get("surface") or ("dm" if isinstance(channel, discord.DMChannel) else "public_channel")).strip()
    asset_url = str(job.get("asset_url", "")).strip()
    if not asset_url:
        await channel.send(compact_discord_text(f"{intro} Job finished without a downloadable asset. Status: {job.get('status', 'unknown')}", 1800))
        return
    file_bytes, _ = await core_get_bytes(asset_url)
    filename = str(job.get("asset_name", "")).strip() or f"{job.get('job_id', 'image')}.png"
    summary = [intro]
    if job.get("site_asset_url"):
        summary.append(f"Site asset: {job['site_asset_url']}")
    if job.get("default_site_asset_url"):
        summary.append(f"Default site: {job['default_site_asset_url']}")
    if surface == "dm":
        if len(file_bytes) <= 7_500_000:
            await channel.send(compact_discord_text("\n".join(summary), 1600), file=discord.File(io.BytesIO(file_bytes), filename=filename))
            return
        summary.append("Asset is larger than Discord's comfortable upload size, so I left it on the server instead of spamming a failed upload.")
        await channel.send(compact_discord_text("\n".join(summary), 1800))
        return
    dm_body = compact_discord_text("\n".join(summary) + build_private_activity_footer(str(job.get("job_id") or "")), 1600)
    dm_sent, _ = await send_detail_dm(requester, dm_body, file_bytes=file_bytes if len(file_bytes) <= 7_500_000 else None, filename=filename)
    if surface == "project_channel":
        await channel.send(_project_safe_completion_text(dm_sent=dm_sent))
    else:
        await channel.send(_public_status_text("Task completed. Detailed DM sent." if dm_sent else "Task completed. DM delivery failed."))


async def run_media_job_and_report(
    channel: discord.abc.Messageable,
    requester: discord.abc.User,
    prompt: str,
    *,
    media_kind: str = "image",
    settings: Optional[dict[str, Any]] = None,
    output_target: str = "",
    model_key: str = "",
    policy: Optional[dict[str, Any]] = None,
    job_id: str = "",
    plan: Optional[HermesActionPlan] = None,
):
    started = time.perf_counter()
    current_policy = policy if isinstance(policy, dict) else {}
    surface = str(current_policy.get("surface") or ("dm" if isinstance(channel, discord.DMChannel) else "public_channel")).strip()
    normalized_kind = "logo" if str(media_kind or "").strip().lower() == "logo" else "image"
    create_kind = "image"
    intro = "Generated logo ready." if normalized_kind == "logo" else "Generated image ready."
    effective_model_key = str(model_key or ("image_logo_fast" if normalized_kind == "logo" else "")).strip()
    effective_settings = dict(settings or {})
    resolved_plan = plan if isinstance(plan, HermesActionPlan) else HermesActionPlan(
        summary=f"Zeus mapped a {normalized_kind} generation pass.",
        public_ack=build_media_progress_update(normalized_kind),
        private_note="Return the generated asset and prompt notes.",
    )
    done_event = asyncio.Event()

    async def _send_progress(text: str):
        if surface == "dm":
            await channel.send(compact_discord_text(text, 240))
        elif surface == "project_channel":
            await channel.send(compact_discord_text(text, 240))
        else:
            await channel.send(_public_status_text(text))

    progress_task = asyncio.create_task(
        run_progress_notifier(
            _send_progress,
            done_event,
            [
                (DISCORD_PROGRESS_FIRST_SEC, resolved_plan.public_ack or build_media_progress_update(normalized_kind)),
                (DISCORD_PROGRESS_SECOND_SEC, f"Still working on that {normalized_kind} now."),
            ],
            repeat_text=f"Still working on that {normalized_kind} now.",
            repeat_every_sec=DISCORD_PROGRESS_REPEAT_SEC,
        )
    )
    try:
        result = await create_media_job(
            create_kind,
            prompt,
            output_target=output_target,
            settings=effective_settings,
            model_key=effective_model_key,
            wait_for_completion=True,
        )
        job = result.get("job", {}) if isinstance(result, dict) else {}
        status = str(job.get("status", "")).strip().lower()
        if status == "failed":
            raise RuntimeError(str(job.get("notes", f"{normalized_kind} generation failed")))
        await deliver_media_job_result(channel, requester, job, intro=intro, policy=current_policy)
        if job_id:
            await upsert_discord_job_core(
                current_policy,
                job_id=job_id,
                status="completed",
                public_summary=f"{normalized_kind.title()} generated",
                private_summary=compact_discord_text(str(job.get("notes") or intro), 700),
                dm_status="not_needed" if surface == "dm" else "sent",
                detail_text=compact_discord_text(str(job.get("revised_prompt") or prompt), 900),
                command_key="media_job",
                meta={
                    "surface": surface,
                    "media_kind": normalized_kind,
                    "model_key": effective_model_key,
                    "style_key": str(((job.get("style_key") if isinstance(job, dict) else "") or effective_settings.get("style_key") or "")).strip(),
                    "asset_url": str(job.get("asset_url", "")).strip(),
                    "zeus_plan": asdict(resolved_plan),
                },
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        print(f"[discord] media {normalized_kind} delivered ms={elapsed_ms:.0f}")
    except Exception as e:
        detail = compact_discord_text(str(e), 1200)
        if surface == "dm":
            await channel.send(f"{normalized_kind.title()} generation failed: {detail}")
        elif surface == "project_channel":
            await channel.send(_project_blocked_text(f"{normalized_kind.title()} generation failed. Detailed status stays in DM/UI."))
        else:
            await channel.send(_public_status_text(f"{normalized_kind.title()} generation failed."))
        if job_id:
            await upsert_discord_job_core(
                current_policy,
                job_id=job_id,
                status="failed",
                public_summary=f"{normalized_kind.title()} failed",
                private_summary="",
                dm_status="error",
                detail_text=detail,
                command_key="media_job",
                meta={
                    "surface": surface,
                    "media_kind": normalized_kind,
                    "error": compact_discord_text(str(e), 300),
                    "zeus_plan": asdict(resolved_plan),
                },
            )
        print(f"[discord] media {normalized_kind} failed: {e}")
    finally:
        done_event.set()
        if not progress_task.done():
            progress_task.cancel()


async def account_worker_get(path: str, params: dict | None = None) -> dict:
    query = dict(params or {})
    query["token"] = CONTROL_TOKEN
    client = await get_http_client()
    r = await client.get(f"{ACCOUNT_WORKER_URL}{path}", params=query, timeout=http_timeout(900))
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {"status": "ok", "data": data}


async def account_worker_post(path: str, payload: dict) -> dict:
    body = dict(payload or {})
    if "control_token" in body:
        body["control_token"] = CONTROL_TOKEN
    elif "token" in body:
        body["token"] = CONTROL_TOKEN
    else:
        body["token"] = CONTROL_TOKEN
    client = await get_http_client()
    r = await client.post(f"{ACCOUNT_WORKER_URL}{path}", json=body, timeout=http_timeout(900))
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {"status": "ok", "data": data}


async def create_codex_session(project_key: str, title: str = "", goal: str = "", allow_write: bool = False) -> dict:
    return await core_post(
        "/codex/bridge/session/create",
        {
            "token": CONTROL_TOKEN,
            "project_key": project_key,
            "title": title,
            "goal": goal,
            "allow_write": allow_write,
        },
    )


async def get_darius_route(project_key: str) -> dict:
    return await core_get(f"/darius/projects/{project_key}/route", {"token": CONTROL_TOKEN})


async def assign_darius_session(project_key: str, session_id: str = "", title: str = "", goal: str = "", allow_write: bool = True) -> dict:
    return await core_put(
        f"/darius/projects/{project_key}/session",
        {
            "token": CONTROL_TOKEN,
            "project_key": project_key,
            "session_id": session_id,
            "title": title,
            "goal": goal,
            "allow_write": allow_write,
            "assigned_by": "discord_underfoot",
        },
    )


async def queue_darius_dispatch(project_key: str, *, text: str, requested_action: str = "implement", attachments: Optional[list[str]] = None, requested_by: str = "discord", source: str = "discord", expect_verification: bool = True, allow_write: bool = True) -> dict:
    record_runtime_breadcrumb(
        "dispatch",
        severity="info",
        scene="project",
        route="darius_dispatch",
        connection=runtime_connection_summary(project_key=project_key, route_status="queued"),
        action=requested_action,
        source=source,
    )
    return await core_post(
        f"/darius/projects/{project_key}/dispatch",
        {
            "token": CONTROL_TOKEN,
            "project_key": project_key,
            "text": text,
            "requested_action": requested_action,
            "attachments": attachments or [],
            "requested_by": requested_by,
            "source": source,
            "expect_verification": expect_verification,
            "allow_write": allow_write,
        },
    )


def should_route_project_work_to_darius(policy: Optional[dict[str, Any]]) -> bool:
    current = policy if isinstance(policy, dict) else {}
    project_key = normalize_project_key_local(current.get("project_key") or "")
    if not project_key:
        return False
    return project_is_darius_backed(project_key)


async def codex_session_message(session_id: str, prompt: str, scope: str = "implement", allow_write: Optional[bool] = None) -> dict:
    payload: dict[str, Any] = {
        "token": CONTROL_TOKEN,
        "session_id": session_id,
        "prompt": prompt,
        "scope": scope,
        "update_task_status": True,
    }
    if allow_write is not None:
        payload["allow_write"] = bool(allow_write)
    return await core_post("/codex/bridge/session/message", payload)


def codex_session_binding_key(
    channel: Optional[discord.abc.Messageable],
    guild: Optional[discord.Guild],
    project_key: str = "",
    requester_id: int = 0,
) -> str:
    normalized_project = normalize_project_key_local(project_key or default_project_key_from_guild(guild) or "mount-olympus-core")
    if isinstance(channel, discord.Thread):
        return f"thread:{channel.id}:project:{normalized_project}"
    if isinstance(channel, discord.DMChannel):
        return f"dm:{int(requester_id or 0)}:project:{normalized_project or 'mount-olympus-core'}"
    channel_id = int(getattr(channel, "id", 0) or 0)
    guild_id = int(getattr(guild, "id", 0) or 0)
    if channel_id:
        return f"guild:{guild_id}:channel:{channel_id}:project:{normalized_project}"
    return f"guild:{guild_id}:project:{normalized_project}"


async def ensure_codex_session_binding(
    channel: Optional[discord.abc.Messageable],
    guild: Optional[discord.Guild],
    project_key: str = "",
    requester_id: int = 0,
    allow_write: bool = False,
    title: str = "",
) -> str:
    resolved_project_key = normalize_project_key_local(project_key or default_project_key_from_guild(guild))
    key = codex_session_binding_key(channel, guild, resolved_project_key, requester_id=requester_id)
    existing = discord_codex_sessions.get(key)
    if existing:
        return existing
    if isinstance(channel, discord.Thread):
        label = title or f"Discord Codex session for {pretty_project_label(resolved_project_key)} / {channel.name}"
    elif guild is not None:
        label = title or f"Discord Codex session for {pretty_project_label(resolved_project_key)} / {guild.name}"
    else:
        label = title or f"Discord Codex session for {pretty_project_label(resolved_project_key or 'mount-olympus-core')}"
    result = await create_codex_session(project_key=resolved_project_key, title=label, goal=label, allow_write=allow_write)
    session = result.get("session", {}) if isinstance(result, dict) else {}
    session_id = str(session.get("session_id") or "").strip()
    if not session_id:
        raise RuntimeError("Codex session creation returned no session_id")
    discord_codex_sessions[key] = session_id
    return session_id


def render_codex_result(result: dict[str, Any]) -> str:
    summary = compact_discord_text(result.get("reply") or result.get("summary") or "Codex completed without a useful summary.", 1200)
    lines = [summary]
    outcome = str(result.get("outcome") or result.get("status") or "").strip().lower()
    questions = [compact_discord_text(q, 220) for q in (result.get("result", {}).get("questions") or result.get("questions") or []) if str(q or "").strip()]
    files_touched = [str(p).strip() for p in (result.get("result", {}).get("files_touched") or result.get("files_touched") or []) if str(p).strip()]
    if questions and outcome in {"blocked", "failed", "needs_confirmation"}:
        lines.append("Need from you: " + " | ".join(questions[:3]))
    if files_touched:
        lines.append("Files: " + ", ".join(files_touched[:4]))
    return compact_discord_text("\n".join(lines), 1800)


def parse_csv_items(raw: str, max_items: int = 40, max_len: int = 64) -> list[str]:
    out = []
    for part in (raw or "").split(","):
        item = part.strip()
        if not item:
            continue
        clean = "".join(ch for ch in item if ch.isalnum() or ch in "-_ .:/").strip()
        if not clean:
            continue
        out.append(clean[:max_len])
    seen = set()
    uniq = []
    for item in out:
        k = item.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(item)
    return uniq[:max_items]


async def queue_project_task(goal: str, project_key: str, entails: str = "", source: str = "discord", requested_by: str = "discord-bot") -> dict:
    try:
        record_runtime_breadcrumb(
            "dispatch",
            severity="info",
            scene="project",
            route="project_task",
            connection=runtime_connection_summary(project_key=project_key, route_status="queued"),
            goal=compact_discord_text(goal, 140),
            source=source,
        )
        return await core_post(
            "/tasks/create",
            {
                "goal": goal,
                "project_key": project_key,
                "entails": entails or goal,
                "source": source,
                "requested_by": requested_by,
            },
        )
    except Exception as e:
        return {"status": "error", "detail": str(e), "goal": goal, "project_key": project_key}


def event_template_upsert(
    guild_id: int,
    template_key: str,
    name: str,
    description: str,
    frequency: str,
    start_hour: int,
    start_minute: int,
    duration_min: int,
    channel_name: str = "",
    weekday: Optional[int] = None,
    location: str = "",
    meta: Optional[dict[str, Any]] = None,
):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO discord_event_templates
          (guild_id, template_key, name, description, frequency, weekday, start_hour, start_minute, duration_min, channel_name, location, status, meta_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s)
        ON DUPLICATE KEY UPDATE
          name=VALUES(name),
          description=VALUES(description),
          frequency=VALUES(frequency),
          weekday=VALUES(weekday),
          start_hour=VALUES(start_hour),
          start_minute=VALUES(start_minute),
          duration_min=VALUES(duration_min),
          channel_name=VALUES(channel_name),
          location=VALUES(location),
          status='active',
          meta_json=VALUES(meta_json)
        """,
        (
            str(guild_id),
            template_key[:128],
            name[:255],
            description,
            frequency[:32],
            weekday,
            max(0, min(int(start_hour), 23)),
            max(0, min(int(start_minute), 59)),
            max(10, min(int(duration_min), 720)),
            channel_name[:128],
            location[:255],
            json.dumps(meta or {}),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()


def list_event_templates(guild_id: int) -> list[dict[str, Any]]:
    conn = db_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """
        SELECT template_key, name, description, frequency, weekday, start_hour, start_minute, duration_min, channel_name, location, status, meta_json
        FROM discord_event_templates
        WHERE guild_id=%s AND status='active'
        ORDER BY template_key ASC
        """,
        (str(guild_id),),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def discord_timezone():
    try:
        return ZoneInfo(DISCORD_EVENT_TIMEZONE)
    except Exception:
        return timezone.utc


def compute_template_start(template: dict[str, Any], now_local: datetime) -> datetime:
    start_hour = max(0, min(_safe_int(template.get("start_hour"), 12), 23))
    start_minute = max(0, min(_safe_int(template.get("start_minute"), 0), 59))
    candidate = now_local.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    frequency = str(template.get("frequency", "weekly") or "weekly").strip().lower()
    if frequency == "daily":
        if candidate <= now_local + timedelta(minutes=10):
            candidate += timedelta(days=1)
        return candidate
    weekday = _safe_int(template.get("weekday"), 2)
    delta = (weekday - candidate.weekday()) % 7
    candidate = candidate + timedelta(days=delta)
    if candidate <= now_local + timedelta(minutes=10):
        candidate += timedelta(days=7)
    return candidate


async def ensure_role(guild: discord.Guild, name: str, color: int = 0) -> discord.Role:
    role = discord.utils.get(guild.roles, name=name)
    if role is not None:
        return role
    kwargs: dict[str, Any] = {"name": name[:100], "reason": "Mount Olympus Discord bootstrap"}
    if color:
        kwargs["colour"] = discord.Colour(color)
    return await guild.create_role(**kwargs)


async def ensure_category(guild: discord.Guild, name: str, overwrites: Optional[dict[Any, discord.PermissionOverwrite]] = None) -> discord.CategoryChannel:
    category = discord.utils.get(guild.categories, name=name)
    if category is not None:
        return category
    kwargs: dict[str, Any] = {"reason": "Mount Olympus Discord bootstrap"}
    if overwrites:
        kwargs["overwrites"] = overwrites
    return await guild.create_category(name[:100], **kwargs)


async def ensure_text_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    topic: str = "",
    overwrites: Optional[dict[Any, discord.PermissionOverwrite]] = None,
) -> discord.TextChannel:
    channel_name = safe_channel_name(name)
    channel = discord.utils.get(guild.text_channels, name=channel_name)
    if channel is None:
        kwargs: dict[str, Any] = {
            "category": category,
            "topic": topic[:1024],
            "reason": "Mount Olympus Discord bootstrap",
        }
        if overwrites:
            kwargs["overwrites"] = overwrites
        channel = await guild.create_text_channel(channel_name, **kwargs)
        return channel
    updates = {}
    if channel.category_id != category.id:
        updates["category"] = category
    if topic and (channel.topic or "") != topic[:1024]:
        updates["topic"] = topic[:1024]
    if updates:
        await channel.edit(reason="Mount Olympus Discord bootstrap sync", **updates)
    return channel


async def ensure_voice_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    overwrites: Optional[dict[Any, discord.PermissionOverwrite]] = None,
) -> discord.VoiceChannel:
    channel_name = safe_channel_name(name)
    channel = discord.utils.get(guild.voice_channels, name=channel_name)
    if channel is None:
        kwargs: dict[str, Any] = {"category": category, "reason": "Mount Olympus Discord bootstrap"}
        if overwrites:
            kwargs["overwrites"] = overwrites
        return await guild.create_voice_channel(channel_name, **kwargs)
    if channel.category_id != category.id:
        await channel.edit(category=category, reason="Mount Olympus Discord bootstrap sync")
    return channel


async def ensure_pinned_post(
    channel: discord.TextChannel,
    marker: str,
    body: str,
    namespace: str = "Olympus",
) -> dict[str, Any]:
    safe_marker = safe_channel_name(marker) or "notice"
    safe_namespace = re.sub(r"[^A-Za-z0-9:_-]+", "-", str(namespace or "Olympus")).strip("-") or "Olympus"
    return await ensure_namespaced_pinned_post(channel, safe_namespace, safe_marker, body.strip())


async def ensure_namespaced_pinned_post(channel: discord.TextChannel, namespace: str, marker: str, body: str) -> dict[str, Any]:
    safe_namespace = re.sub(r"[^A-Za-z0-9:_-]+", "-", str(namespace or "Olympus")).strip("-") or "Olympus"
    safe_marker = re.sub(r"[^A-Za-z0-9:_-]+", "-", str(marker or "notice")).strip("-") or "notice"
    prefix = f"[{safe_namespace}:{safe_marker}]"
    content = f"{prefix}\n{body.strip()}"
    existing = None
    try:
        for msg in await channel.pins():
            if msg.author.id == channel.guild.me.id and (msg.content or "").startswith(prefix):
                existing = msg
                break
    except Exception:
        existing = None
    if existing is None:
        async for msg in channel.history(limit=50):
            if msg.author.id == channel.guild.me.id and (msg.content or "").startswith(prefix):
                existing = msg
                break
    if existing is None:
        msg = await channel.send(content)
        try:
            await msg.pin(reason="Mount Olympus pinned sync")
        except Exception:
            pass
        return {"channel": channel.name, "message_id": msg.id, "status": "created", "marker": safe_marker}
    if (existing.content or "") != content:
        await existing.edit(content=content)
    if not existing.pinned:
        try:
            await existing.pin(reason="Mount Olympus pinned sync")
        except Exception:
            pass
    return {"channel": channel.name, "message_id": existing.id, "status": "updated", "marker": safe_marker}


async def cleanup_legacy_papr_pins(channel: discord.TextChannel) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    me = channel.guild.me
    author_id = getattr(me, "id", 0)
    try:
        pins = list(await channel.pins())
    except Exception:
        pins = []
    for msg in pins:
        if author_id and getattr(msg.author, "id", 0) != author_id:
            continue
        content = str(msg.content or "").strip()
        if not (content.startswith("[PAPR FOUNDATION:") or content.startswith("[PAPR:")):
            continue
        actions: list[str] = []
        try:
            await msg.unpin(reason="Mount Olympus Discord sticky cleanup")
            actions.append("unpinned")
        except Exception:
            actions.append("unpin_skipped")
        try:
            await msg.delete()
            actions.append("deleted")
        except Exception:
            actions.append("delete_skipped")
        cleaned.append(
            {
                "channel": channel.name,
                "message_id": msg.id,
                "status": ",".join(actions),
            }
        )
    return cleaned


async def cleanup_legacy_papr_structure(
    guild: discord.Guild,
    desired_category_keys: set[str],
    desired_channel_keys: set[str],
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    me = guild.me
    if me is None or not me.guild_permissions.manage_channels:
        return cleaned
    legacy_category_keys = {
        "raffles",
        "community",
    }
    legacy_channel_keys = {
        "welcome",
        "announcements",
        "how-papr-works",
        "raffle-rules",
        "faq-support",
        "active-raffles",
        "draw-schedule",
        "winners-circle",
        "sold-out-raffles",
        "mission-impact",
        "general-chat",
        "volunteer-lounge",
        "papr-lobby",
        "public-draw-room",
    }
    for category in list(guild.categories):
        category_key = safe_channel_name(category.name)
        if category_key not in legacy_category_keys or category_key in desired_category_keys:
            continue
        for channel in list(category.channels):
            channel_key = safe_channel_name(getattr(channel, "name", ""))
            if channel_key in desired_channel_keys:
                continue
            try:
                await channel.delete(reason="Mount Olympus legacy PAPR cleanup")
                cleaned.append({"kind": "channel", "name": channel.name, "status": "deleted", "parent": category.name})
            except Exception as exc:
                cleaned.append({"kind": "channel", "name": channel.name, "status": "delete_skipped", "detail": compact_discord_text(str(exc), 180), "parent": category.name})
        try:
            await category.delete(reason="Mount Olympus legacy PAPR cleanup")
            cleaned.append({"kind": "category", "name": category.name, "status": "deleted"})
        except Exception as exc:
            cleaned.append({"kind": "category", "name": category.name, "status": "delete_skipped", "detail": compact_discord_text(str(exc), 180)})
    for channel in list(guild.channels):
        channel_key = safe_channel_name(getattr(channel, "name", ""))
        if channel_key not in legacy_channel_keys or channel_key in desired_channel_keys:
            continue
        parent = getattr(channel, "category", None)
        parent_key = safe_channel_name(getattr(parent, "name", "")) if parent else ""
        if parent_key in legacy_category_keys:
            continue
        try:
            await channel.delete(reason="Mount Olympus legacy PAPR cleanup")
            cleaned.append({"kind": "channel", "name": channel.name, "status": "deleted", "parent": getattr(parent, "name", "") or None})
        except Exception as exc:
            cleaned.append({"kind": "channel", "name": channel.name, "status": "delete_skipped", "detail": compact_discord_text(str(exc), 180), "parent": getattr(parent, "name", "") or None})
    return cleaned


def _blueprint_role_specs(blueprint: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in blueprint.get("roles", []) if isinstance(blueprint.get("roles"), list) else []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                rows.append({"name": name, "color": int(item.get("color") or 0)})
        else:
            name = str(item or "").strip()
            if name:
                rows.append({"name": name, "color": 0})
    return rows


def _blueprint_category_channels(category_spec: dict[str, Any]) -> tuple[list[tuple[str, str]], list[str]]:
    text_channels: list[tuple[str, str]] = []
    voice_channels: list[str] = []
    raw_channels = category_spec.get("channels") if isinstance(category_spec.get("channels"), list) else []
    for item in raw_channels:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            channel_type = str(item.get("type") or "text").strip().lower()
            if channel_type == "voice":
                voice_channels.append(name)
            else:
                text_channels.append((name, str(item.get("topic") or "").strip()))
            continue
        name = str(item or "").strip()
        if not name:
            continue
        if name.lower().startswith("voice:"):
            voice_channels.append(name.split(":", 1)[1].strip())
        else:
            text_channels.append((name, ""))
    if not text_channels and isinstance(category_spec.get("text"), list):
        text_channels.extend([(str(name or "").strip(), str(topic or "").strip()) for name, topic in category_spec.get("text", []) if str(name or "").strip()])
    if not voice_channels and isinstance(category_spec.get("voice"), list):
        voice_channels.extend([str(name or "").strip() for name in category_spec.get("voice", []) if str(name or "").strip()])
    return text_channels, voice_channels


def _candidate_asset_paths(asset_ref: str) -> list[Path]:
    cleaned = str(asset_ref or "").strip().replace("\\", "/")
    if not cleaned:
        return []
    variants = [cleaned]
    for prefix in ("payload/generated/", "payload/", "deploy/ubuntu/"):
        if cleaned.startswith(prefix):
            variants.append(cleaned[len(prefix):])
    roots = [Path.cwd(), WORKSPACE_ROOT_PATH, WORKSPACE_ROOT_PATH.parent]
    out: list[Path] = []
    seen: set[str] = set()
    raw_path = Path(cleaned)
    if raw_path.is_absolute():
        resolved = raw_path.resolve()
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            out.append(resolved)
    for variant in variants:
        for root in roots:
            resolved = (root / variant).resolve()
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(resolved)
    return out


def load_discord_asset_bytes(asset_ref: str) -> tuple[Optional[bytes], str]:
    for candidate in _candidate_asset_paths(asset_ref):
        if not candidate.exists() or not candidate.is_file():
            continue
        suffix = candidate.suffix.lower()
        if suffix == ".svg":
            return None, f"Skipped `{candidate.name}` because Discord uploads need PNG/JPG/GIF/WebP and SVG conversion is unavailable."
        if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            return None, f"Skipped `{candidate.name}` because Discord does not accept `{suffix}` for guild art."
        try:
            payload = candidate.read_bytes()
        except Exception as exc:
            return None, f"Failed to read `{candidate.name}`: {exc}"
        if not payload:
            return None, f"Skipped `{candidate.name}` because the file is empty."
        return payload, f"Loaded `{candidate.name}`."
    return None, f"Asset not found for `{asset_ref}`."


def render_discord_blueprint_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"Blueprint `{summary.get('blueprint_key', 'unknown')}` for `{summary.get('guild_name', 'guild')}`.",
        f"Project lane: {summary.get('project_key', 'olympus') or 'olympus'}",
        f"Dry run: {'yes' if summary.get('dry_run') else 'no'}",
    ]
    guild_updates = summary.get("guild_updates") if isinstance(summary.get("guild_updates"), list) else []
    if guild_updates:
        lines.append("Guild updates: " + ", ".join([str(item.get("status") or item) for item in guild_updates[:6]]))
    if summary.get("roles"):
        lines.append(f"Roles synced: {len(summary['roles'])}")
    if summary.get("categories"):
        lines.append(f"Categories synced: {len(summary['categories'])}")
    if summary.get("channels"):
        lines.append(f"Channels synced: {len(summary['channels'])}")
    if summary.get("pinned_posts"):
        lines.append(f"Sticky posts synced: {len(summary['pinned_posts'])}")
    asset_updates = summary.get("asset_updates") if isinstance(summary.get("asset_updates"), list) else []
    if asset_updates:
        lines.append("Asset notes: " + "; ".join([compact_discord_text(str(item.get("detail") or item), 220) for item in asset_updates[:3]]))
    return compact_discord_text("\n".join(lines), 1800)


async def sync_discord_blueprint(guild: discord.Guild, blueprint_key: str = "", requested_by: int = 0, dry_run: bool = False) -> dict[str, Any]:
    blueprint = discord_ops_blueprint(
        blueprint_key=blueprint_key,
        guild_name=guild.name,
        project_key=default_project_key_from_guild(guild),
    )
    if not blueprint:
        raise RuntimeError("No Olympus Discord blueprint matched this guild.")
    summary: dict[str, Any] = {
        "status": "ok",
        "dry_run": bool(dry_run),
        "blueprint_key": str(blueprint.get("key") or blueprint_key or "").strip(),
        "guild_id": guild.id,
        "guild_name": guild.name,
        "project_key": normalize_project_key_local(blueprint.get("project_key") or "") or default_project_key_from_guild(guild),
        "roles": [],
        "categories": [],
        "channels": [],
        "pinned_posts": [],
        "asset_updates": [],
        "guild_updates": [],
    }
    me = guild.me or guild.get_member(bot.user.id)
    role_map: dict[str, discord.Role] = {}
    role_specs = _blueprint_role_specs(blueprint)
    for role_spec in role_specs:
        if not dry_run:
            role = await ensure_role(guild, role_spec["name"], int(role_spec.get("color") or 0))
            role_map[role.name] = role
        summary["roles"].append(role_spec["name"])
    private_role_names = blueprint.get("private_roles") if isinstance(blueprint.get("private_roles"), list) else [row["name"] for row in role_specs]
    private_roles = [role_map[name] for name in private_role_names if name in role_map]
    private_overwrites = await build_private_overwrites(guild, private_roles) if private_roles and not dry_run else None
    text_channels: dict[str, discord.TextChannel] = {safe_channel_name(channel.name): channel for channel in guild.text_channels}
    voice_channels: dict[str, discord.VoiceChannel] = {safe_channel_name(channel.name): channel for channel in guild.voice_channels}
    desired_category_keys: set[str] = set()
    desired_channel_keys: set[str] = set()
    for category_spec in blueprint.get("categories", []) if isinstance(blueprint.get("categories"), list) else []:
        if not isinstance(category_spec, dict):
            continue
        category_name = str(category_spec.get("name") or "Olympus").strip()[:100] or "Olympus"
        text_specs, voice_specs = _blueprint_category_channels(category_spec)
        desired_category_keys.add(safe_channel_name(category_name))
        desired_channel_keys.update(safe_channel_name(channel_name) for channel_name, _topic in text_specs)
        desired_channel_keys.update(safe_channel_name(voice_name) for voice_name in voice_specs)
        summary["categories"].append(category_name)
        if dry_run:
            for channel_name, _topic in text_specs:
                summary["channels"].append(f"text:{safe_channel_name(channel_name)}")
            for voice_name in voice_specs:
                summary["channels"].append(f"voice:{safe_channel_name(voice_name)}")
            continue
        category = await ensure_category(guild, category_name, overwrites=private_overwrites if category_spec.get("private") else None)
        for channel_name, topic in text_specs:
            channel = await ensure_text_channel(
                guild,
                category,
                channel_name,
                topic=topic,
                overwrites=private_overwrites if category_spec.get("private") else None,
            )
            text_channels[safe_channel_name(channel.name)] = channel
            summary["channels"].append(f"text:{channel.name}")
        for voice_name in voice_specs:
            channel = await ensure_voice_channel(
                guild,
                category,
                voice_name,
                overwrites=private_overwrites if category_spec.get("private") else None,
            )
            voice_channels[safe_channel_name(channel.name)] = channel
            summary["channels"].append(f"voice:{channel.name}")
    sticky_rows = blueprint.get("sticky_messages") if isinstance(blueprint.get("sticky_messages"), list) else []
    sticky_namespace = str(blueprint.get("key") or blueprint.get("title") or "Olympus").strip().upper()
    sticky_channel_keys = {
        safe_channel_name(str(sticky.get("channel") or "").strip())
        for sticky in sticky_rows
        if isinstance(sticky, dict) and str(sticky.get("channel") or "").strip()
    }
    if not dry_run and sticky_namespace != "PAPR":
        legacy_structure_rows = await cleanup_legacy_papr_structure(guild, desired_category_keys, desired_channel_keys)
        if legacy_structure_rows:
            summary.setdefault("legacy_cleanup", []).extend(legacy_structure_rows)
        for channel_key in sorted(sticky_channel_keys):
            channel = text_channels.get(channel_key)
            if channel is None:
                continue
            legacy_rows = await cleanup_legacy_papr_pins(channel)
            if legacy_rows:
                summary.setdefault("legacy_cleanup", []).extend(legacy_rows)
    for sticky in sticky_rows:
        if not isinstance(sticky, dict):
            continue
        channel_key = safe_channel_name(str(sticky.get("channel") or "").strip())
        title = str(sticky.get("title") or "Olympus notice").strip()
        body = str(sticky.get("body") or "").strip()
        if not channel_key or not body:
            continue
        if dry_run:
            summary["pinned_posts"].append({"channel": channel_key, "status": "planned", "title": title})
            continue
        channel = text_channels.get(channel_key)
        if channel is None:
            summary["pinned_posts"].append({"channel": channel_key, "status": "missing_channel", "title": title})
            continue
        content = f"**{title}**\n{body}" if title else body
        marker = safe_channel_name(f"{channel_key}-{title or 'notice'}")
        summary["pinned_posts"].append(await ensure_namespaced_pinned_post(channel, sticky_namespace, marker, content))
    if not dry_run and me and me.guild_permissions.manage_guild:
        desired_name = str(blueprint.get("guild_name") or "").strip()
        desired_description = str(blueprint.get("description") or "").strip()
        if desired_name and guild.name != desired_name:
            await guild.edit(name=desired_name[:100], reason="Mount Olympus Discord sync")
            summary["guild_updates"].append({"status": f"name->{desired_name[:100]}"})
            summary["guild_name"] = desired_name[:100]
        if desired_description:
            try:
                await guild.edit(description=desired_description[:120], reason="Mount Olympus Discord sync")
                summary["guild_updates"].append({"status": "description-updated"})
            except Exception as exc:
                summary["guild_updates"].append({"status": "description-skipped", "detail": compact_discord_text(str(exc), 180)})
        for field_name, asset_ref in (("icon", blueprint.get("icon_asset")), ("banner", blueprint.get("banner_asset"))):
            raw_ref = str(asset_ref or "").strip()
            if not raw_ref:
                continue
            payload, detail = load_discord_asset_bytes(raw_ref)
            if not payload:
                summary["asset_updates"].append({"field": field_name, "status": "skipped", "detail": detail})
                continue
            try:
                await guild.edit(**{field_name: payload}, reason="Mount Olympus Discord sync")
                summary["asset_updates"].append({"field": field_name, "status": "updated", "detail": detail})
            except Exception as exc:
                summary["asset_updates"].append({"field": field_name, "status": "skipped", "detail": compact_discord_text(str(exc), 180)})
    elif dry_run:
        summary["guild_updates"].append({"status": "dry-run"})
    return summary


async def run_discord_blueprint_command(ctx, blueprint_key: str = "", *, spec: str = "", dry_run_default: bool = False) -> tuple[Optional[dict[str, Any]], str]:
    if not ctx.guild:
        return None, "This command only works inside a Discord server."
    parts = parse_pipe_parts(spec)
    requested_key = blueprint_key or (parts[0] if parts else "")
    requested_project_key = (parts[0] if blueprint_key else (parts[1] if len(parts) > 1 else "")).strip()
    mode = (parts[1] if blueprint_key and len(parts) > 1 else (parts[2] if len(parts) > 2 else (parts[1] if not blueprint_key and len(parts) > 1 else ""))).strip().lower()
    dry_run = dry_run_default or mode in {"dryrun", "dry-run", "plan", "preview"}
    policy = await fetch_discord_actor_policy(
        ctx.message,
        requested_project_key=requested_project_key,
        command_key="guildsync",
    )
    blueprint = discord_ops_blueprint(
        blueprint_key=requested_key,
        guild_name=ctx.guild.name,
        project_key=str(policy.get("project_key") or default_project_key_from_guild(ctx.guild)),
    )
    if not blueprint:
        return None, "I could not match this guild to an Olympus Discord blueprint."
    review = await review_discord_action(
        f"Sync Discord blueprint {str(blueprint.get('key') or '').strip()} for guild {ctx.guild.name}",
        policy,
        action_kind="branding_sync",
        allow_write=not dry_run,
        meta={"blueprint_key": str(blueprint.get("key") or "").strip(), "dry_run": dry_run, "source": "command"},
    )
    if not review.get("approved"):
        return None, compact_discord_text(str(review.get("detail") or "Oracle review blocked this blueprint sync."), 1800)
    summary = await sync_discord_blueprint(
        ctx.guild,
        blueprint_key=str(blueprint.get("key") or "").strip(),
        requested_by=ctx.author.id,
        dry_run=dry_run,
    )
    project_key = normalize_project_key_local(summary.get("project_key") or "")
    if project_key and not dry_run:
        await queue_project_task(
            goal=f"Sync Discord blueprint {summary.get('blueprint_key')} for guild {ctx.guild.name}",
            project_key=project_key,
            entails=json.dumps(
                {
                    "guild_id": ctx.guild.id,
                    "roles": len(summary.get("roles", [])),
                    "categories": len(summary.get("categories", [])),
                    "channels": len(summary.get("channels", [])),
                    "sticky_posts": len(summary.get("pinned_posts", [])),
                }
            ),
            source="discord_blueprint_sync",
            requested_by=f"discord:{ctx.author.id}",
    )
    return summary, render_discord_blueprint_summary(summary)

def render_community_packet_summary(packet: dict[str, Any]) -> str:
    owner_route = packet.get("owner_route") if isinstance(packet.get("owner_route"), dict) else {}
    mirror_ref = str(packet.get("olympus_mirror_channel") or "").strip() or "not set"
    mirror_id = str(packet.get("olympus_mirror_channel_id") or "").strip()
    if mirror_id:
        mirror_ref = f"{mirror_ref} (id={mirror_id})"
    return "\n".join(
        [
            f"Community packet for `{packet.get('project_key', 'unknown')}`.",
            f"- Blueprint: `{packet.get('community_guild_blueprint_key', 'unknown')}`",
            f"- Status: `{packet.get('community_guild_status', 'unknown')}`",
            f"- Welcome: `{packet.get('welcome_channel', 'welcome')}`",
            f"- Announcements: `{packet.get('announcement_channel', 'announcements')}`",
            f"- Patch notes: `{packet.get('patch_notes_channel', 'patch-notes')}`",
            f"- Feedback: `{packet.get('feedback_channel', 'feedback')}`",
            f"- Bugs: `{packet.get('bugs_channel', 'bug-reports')}`",
            f"- Events: `{packet.get('events_channel', 'events')}`",
            f"- Olympus mirror: `{mirror_ref}`",
            f"- the owner route: `{owner_route.get('owner_name') or 'the owner'}` via `{', '.join(owner_route.get('notify_via', [])) or 'owner_dm'}`",
        ]
    )

def render_community_validation_summary(validation: dict[str, Any]) -> str:
    summary = validation.get("summary") if isinstance(validation.get("summary"), dict) else {}
    checks = validation.get("checks") if isinstance(validation.get("checks"), list) else []
    failed = [row for row in checks if isinstance(row, dict) and row.get("required") and not row.get("ok")]
    lines = [
        f"Validation: `{validation.get('status', 'unknown')}`",
        f"- Required failures: `{summary.get('required_failed', len(failed))}`",
        f"- Mirror bound: `{summary.get('mirror_bound', False)}`",
        f"- the owner route bound: `{summary.get('owner_route_bound', False)}`",
        f"- Patch notes bound: `{summary.get('patch_notes_bound', False)}`",
    ]
    if failed:
        labels = [str(row.get("key") or "check") for row in failed[:4]]
        lines.append(f"- Needs attention: `{', '.join(labels)}`")
    return "\n".join(lines)

async def publish_community_announcement(
    ctx,
    *,
    spec: str,
    announcement_kind: str = "announcement",
) -> tuple[bool, str]:
    if not ctx.guild:
        return False, "This command only works inside a Discord server."
    policy = await ensure_ctx_policy(ctx, command_key="communityannounce")
    if not is_admin_ctx(ctx):
        return False, build_owner_access_denied_reply(normalize_project_key_local((policy or {}).get("project_key") or default_project_key_from_guild(ctx.guild)))
    project_key = normalize_project_key_local((policy or {}).get("project_key") or default_project_key_from_guild(ctx.guild))
    packet = await fetch_discord_community_packet(project_key, guild=ctx.guild, mode="apply")
    if not packet or not packet.get("community_guild_enabled"):
        return False, f"`{project_key or 'project'}` is not configured for a public community guild."
    parts = parse_pipe_parts(spec)
    if len(parts) < 2:
        return False, "Usage: !communityannounce <title|body> or !patchnotes <title|body>"
    target_channel = resolve_text_channel_from_packet(
        ctx.guild,
        packet,
        channel_id_key="patch_notes_channel_id" if announcement_kind == "patch_notes" else "announcement_channel_id",
        channel_name_key="patch_notes_channel" if announcement_kind == "patch_notes" else "announcement_channel",
    ) or ctx.channel
    if target_channel is None:
        target_channel = ctx.channel
    review = await review_discord_action(
        f"Publish {announcement_kind} in {ctx.guild.name}: {parts[0]}",
        policy,
        action_kind="branding_sync",
        allow_write=True,
        meta={"announcement_kind": announcement_kind, "project_key": project_key, "channel": getattr(target_channel, 'name', '')},
    )
    if not review.get("approved"):
        return False, compact_discord_text(str(review.get("detail") or "Zeus held that publish request."), 1800)
    title, body = parts[0], parts[1]
    content = f"**{title}**\n{body}"
    sent = await target_channel.send(compact_discord_text(content, 1800))
    await core_post(
        "/discord/community-publish/announcement",
        {
            "token": CONTROL_TOKEN,
            "project_key": project_key,
            "guild_id": str(ctx.guild.id),
            "channel_id": str(getattr(target_channel, "id", 0) or ""),
            "message_id": str(getattr(sent, "id", 0) or ""),
            "announcement_kind": announcement_kind,
            "status": "published",
            "title": title,
            "body": body,
            "mirror_channel_id": str(packet.get("olympus_mirror_channel_id") or packet.get("olympus_mirror_channel") or "").strip(),
            "meta": {"published_by": str(ctx.author.id), "channel_name": str(getattr(target_channel, "name", "") or "")},
        },
    )
    record_runtime_breadcrumb(
        "community_publish_result",
        severity="info",
        scene="discord",
        route=f"community_{announcement_kind}",
        connection=runtime_connection_summary(
            surface=str((policy or {}).get("surface") or "public_channel"),
            project_key=project_key,
            guild_id=str(ctx.guild.id),
            channel_id=str(getattr(target_channel, "id", 0) or ""),
            route_status="published",
        ),
        announcement_kind=announcement_kind,
        title=compact_discord_text(title, 140),
    )
    await route_public_community_owner_notice(
        policy,
        event_key=announcement_kind,
        summary=f"{pretty_project_label(project_key)} {announcement_kind.replace('_', ' ')} published in #{getattr(target_channel, 'name', 'channel')}: {compact_discord_text(title, 160)}",
        requested_by=f"discord:{ctx.author.id}",
    )
    return True, f"{pretty_project_label(project_key)} {announcement_kind.replace('_', ' ')} published in #{getattr(target_channel, 'name', 'channel')}."


def papr_blueprint() -> dict[str, Any]:
    return {
        "roles": [
            {"name": "PAPR Staff", "color": 0xC8A13A},
            {"name": "Draw Team", "color": 0x2F8FCE},
            {"name": "Volunteer", "color": 0x5DAA68},
            {"name": "Moderator", "color": 0x7A5BEF},
        ],
        "categories": [
            {
                "name": "Start Here",
                "text": [
                    ("welcome", "Nonprofit intro, house rules, and where to begin."),
                    ("announcements", "Official platform announcements, launch updates, and policy changes."),
                    ("how-papr-works", "How raffles lock, sell out, and move into draw scheduling."),
                    ("raffle-rules", "Official raffle rules, eligibility notes, and fairness guardrails."),
                    ("faq-support", "Support intake and common account, ticket, and draw questions."),
                ],
            },
            {
                "name": "Raffles",
                "text": [
                    ("active-raffles", "Current raffle drops, ticket windows, and status changes."),
                    ("draw-schedule", "Upcoming public draw times, sellout assignments, and event cadence."),
                    ("winners-circle", "Published winners, result summaries, and archive references."),
                    ("sold-out-raffles", "Locked raffles that are waiting for their assigned draw slot."),
                ],
            },
            {
                "name": "Community",
                "text": [
                    ("general-chat", "Community conversation, reminders, and event discussion."),
                    ("mission-impact", "Nonprofit mission, beneficiary impact, and transparency updates."),
                    ("volunteer-lounge", "Volunteer coordination and supporter check-ins."),
                ],
                "voice": [
                    "papr-lobby",
                    "public-draw-room",
                ],
            },
            {
                "name": "PAPR Ops",
                "private": True,
                "text": [
                    ("ops-war-room", "Live operator coordination for launches, issues, and follow-through."),
                    ("finance-compliance", "Payments, nonprofit controls, and compliance follow-up."),
                    ("broadcast-runbook", "Public draw prep, rundown notes, and winner publication staging."),
                    ("moderator-desk", "Moderation notes, support escalations, and staff-only coordination."),
                ],
            },
        ],
        "posts": {
            "welcome": (
                f"Welcome to PAPR.\n\n"
                f"PAPR is a nonprofit raffle platform built around fair inventory, visible status, and documented draws.\n"
                f"Start with {PAPR_SITE_URL} for the public experience and use this Discord for updates, support, and community touchpoints.\n\n"
                f"Key rooms:\n"
                f"- #how-papr-works for the raffle flow\n"
                f"- #raffle-rules for official guardrails\n"
                f"- #draw-schedule for upcoming public draw windows\n"
                f"- #winners-circle for published results"
            ),
            "announcements": (
                "This channel is reserved for official PAPR updates.\n\n"
                "Expect launch notes, live raffle changes, major transparency posts, and policy notices here.\n"
                "If a raffle changes state publicly, we announce it here and on the website."
            ),
            "how-papr-works": (
                "PAPR raffle flow:\n"
                "1. A raffle opens with a fixed ticket inventory.\n"
                "2. Ticket counts stay visible while the raffle is live.\n"
                "3. Once the final valid ticket is issued, the raffle is marked sold out and locked.\n"
                "4. The sold-out raffle is assigned to its draw mode: immediate or next scheduled public draw.\n"
                "5. The winner is selected from the valid issued-ticket pool and then published with the result summary.\n\n"
                "The website remains the source of truth for status, timing, and published winners."
            ),
            "raffle-rules": (
                "Core rules:\n"
                "- No early close unless an authorized admin stops a raffle for a valid documented reason.\n"
                "- Automatic close only happens at sellout.\n"
                "- Sold-out raffles are locked from further purchases.\n"
                "- Draw timing must be recorded and publicly communicated.\n"
                "- Any void, reroll, or exception requires a logged operator action."
            ),
            "faq-support": (
                "Support starts here.\n\n"
                "Use this channel for account help, purchase questions, draw timing questions, and policy clarifications.\n"
                "If a question affects the public record, we answer it here and mirror important clarifications into the website FAQ."
            ),
            "active-raffles": (
                "Live raffle posts belong here.\n\n"
                "Each raffle drop should include the prize, ticket count, status label, and a link to the live raffle page.\n"
                "When inventory changes materially or a raffle sells out, update here and in #announcements."
            ),
            "draw-schedule": (
                "Public draw schedule lives here.\n\n"
                "Daily: a short community and support check-in event keeps members oriented.\n"
                "Weekly: a public draw briefing and impact recap keep the room aligned on upcoming draws and published results.\n"
                "Use this room to announce when a sold-out raffle is assigned to a specific public draw slot."
            ),
            "winners-circle": (
                "Winner publication format:\n"
                "- raffle name\n"
                "- sellout timestamp\n"
                "- draw timestamp\n"
                "- public draw slot reference if applicable\n"
                "- winner display format consistent with privacy policy\n"
                "- link back to the result or archive page"
            ),
            "sold-out-raffles": (
                "Sold-out raffles wait here for draw assignment and outcome publication.\n\n"
                "Once a raffle sells out it should be moved into the sold-out state, linked to its upcoming draw slot, and left locked until the result is published."
            ),
            "mission-impact": (
                "PAPR exists to turn premium raffle energy into nonprofit impact.\n\n"
                "Use this room for beneficiary highlights, transparency notes, volunteer wins, and the story behind what funds are supporting."
            ),
            "general-chat": (
                "Community room.\n\n"
                "Use this channel for member conversation, public questions that do not belong in support, and live discussion around active raffles and draw nights."
            ),
            "volunteer-lounge": (
                "Volunteer coordination room.\n\n"
                "Use this space for volunteer availability, event support needs, and community follow-through that helps the public-facing raffle floor run smoothly."
            ),
            "ops-war-room": (
                "Staff-only room.\n\n"
                "Use this channel for launch checklists, incident handling, operator handoffs, and action tracking tied to the public raffle floor."
            ),
            "finance-compliance": (
                "Staff-only finance and compliance room.\n\n"
                "Track payment issues, nonprofit controls, disclosure updates, and anything that could affect the platform's audit trail or public trust posture."
            ),
            "broadcast-runbook": (
                "Staff-only draw production room.\n\n"
                "Use this channel to stage draw rundowns, sold-out raffle assignments, public readouts, and winner publication timing before a scheduled event goes live."
            ),
            "moderator-desk": (
                "Staff-only moderation room.\n\n"
                "Use this channel for support escalations, moderation notes, bad-actor handling, and any user-facing issue that needs internal coordination first."
            ),
        },
        "events": [
            {
                "template_key": "papr_daily_checkin",
                "name": "PAPR Daily Check-In",
                "description": "Daily community and support check-in covering active raffles, public questions, and the next operational window.",
                "frequency": "daily",
                "start_hour": 10,
                "start_minute": 0,
                "duration_min": 30,
                "channel_name": "papr-lobby",
            },
            {
                "template_key": "papr_weekly_draw_briefing",
                "name": "PAPR Weekly Draw Briefing",
                "description": "Weekly public briefing for sold-out raffle assignments, draw timing, and any upcoming public drawing slots.",
                "frequency": "weekly",
                "weekday": 2,
                "start_hour": 19,
                "start_minute": 0,
                "duration_min": 45,
                "channel_name": "public-draw-room",
            },
            {
                "template_key": "papr_weekly_impact_recap",
                "name": "PAPR Weekly Impact Recap",
                "description": "Weekly recap of winners, beneficiary impact, and the next nonprofit storytelling beat for the community.",
                "frequency": "weekly",
                "weekday": 6,
                "start_hour": 18,
                "start_minute": 0,
                "duration_min": 30,
                "channel_name": "papr-lobby",
            },
        ],
    }


def underfoot_blueprint() -> dict[str, Any]:
    return {
        "roles": [
            {"name": "Underfoot Staff", "color": 0x7D59FF},
            {"name": "Playtest Crew", "color": 0x44D17A},
            {"name": "Lore Keeper", "color": 0xFF9C48},
        ],
        "categories": [
            {
                "name": "Underfoot Start Here",
                "text": [
                    ("welcome-burrow", "New players start here."),
                    ("announcements", "Major build notes, patch drops, and playtest alerts."),
                    ("how-underfoot-works", "Guest-entry flow, map selection, and current prototype rules."),
                    ("playtest-faq", "Quick answers about the prototype and test rhythm."),
                ],
                "voice": ["burrow-lobby"],
            },
            {
                "name": "Underfoot World",
                "text": [
                    ("patch-notes", "Build notes and fixes."),
                    ("scene-flow", "Startup, title, character select, game list, world map, and minigame routing."),
                    ("map-design", "Map concepts, exits, hazards, and world layout."),
                    ("quest-board", "Quest ideas, mission hooks, and rescue loop tuning."),
                    ("bug-burrow", "Player bug reports and weird hazard clips."),
                    ("creature-lab", "Meshy creature generations, props, imports, and rigging notes."),
                    ("ui-foundry", "Menu art, title cards, generated UI images, and Unity presentation polish."),
                ],
                "voice": ["playtest-burrow", "hazard-chaos"],
            },
            {
                "name": "Underfoot Ops",
                "private": True,
                "text": [
                    ("underfoot-ops", "Internal operator lane for the project."),
                    ("build-pipeline", "Unity scene generation, Codex runs, deployment notes, and blockers."),
                    ("asset-ingest", "Model imports, texture drops, prefab hookups, and reference tracking."),
                    ("qa-ladder", "Level-1/2/3 test ladder output and retest notes."),
                ],
                "voice": ["ops-call"],
            },
        ],
        "posts": {
            "welcome-burrow": (
                "Welcome to Underfoot.\n\n"
                "This is the playtest burrow for the Unity 6 multiplayer prototype.\n"
                "Mount Olympus tracks the active build stack here: Unity scenes and C# gameplay, local/netcode match scaffolds, Meshy-generated creatures/props, and generated UI art.\n"
                "Current MVP rule: no registration. Enter a name, choose a map, pick a spawn preference, and jump in."
            ),
            "how-underfoot-works": (
                "Prototype flow:\n"
                "- startup -> title -> character select -> game list -> world map -> minigame\n"
                "- guest name entry only\n"
                "- choose a map and spawn preference\n"
                "- third-person Warble or Biggun play with proximity voice only\n"
                "- gather resources, rescue creatures, and bank points\n"
                "- if disaster hits, respawn nearby and re-enter fast"
            ),
            "bug-burrow": (
                "Bug report format:\n"
                "- what happened\n"
                "- where it happened\n"
                "- what you expected instead\n"
                "- screenshot or clip if possible"
            ),
            "underfoot-ops": (
                "Internal ops lane.\n\n"
                "Use this room for live deploy notes, Unity build routing, Codex runs, Meshy asset tracking, playtest scheduling, and issue ownership."
            ),
        },
        "events": [
            {
                "template_key": "underfoot_daily_burrow_check",
                "name": "Underfoot Daily Burrow Check",
                "description": "Short daily check-in for build status, blockers, and what changed in the prototype.",
                "frequency": "daily",
                "start_hour": 11,
                "start_minute": 0,
                "duration_min": 20,
                "channel_name": "burrow-lobby",
                "location": UNDERFOOT_SITE_URL,
            },
            {
                "template_key": "underfoot_weekly_playtest",
                "name": "Underfoot Weekly Playtest",
                "description": "Weekly multiplayer playtest session covering map flow, hazards, respawns, and rescue loops.",
                "frequency": "weekly",
                "weekday": 5,
                "start_hour": 20,
                "start_minute": 0,
                "duration_min": 60,
                "channel_name": "playtest-burrow",
                "location": UNDERFOOT_SITE_URL,
            },
        ],
    }


async def build_private_overwrites(guild: discord.Guild, staff_roles: list[discord.Role]) -> dict[Any, discord.PermissionOverwrite]:
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
    }
    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, manage_messages=True)
    for owner in await fetch_owner_members_for_guild(guild):
        overwrites[owner] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True, manage_channels=True)
    for role in staff_roles:
        overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True)
    return overwrites


async def ensure_template_events_for_guild(guild: discord.Guild, voice_channels: dict[str, discord.VoiceChannel]) -> list[dict[str, Any]]:
    templates = list_event_templates(guild.id)
    if not templates:
        return []
    tz = discord_timezone()
    now_local = datetime.now(tz)
    existing = await guild.fetch_scheduled_events(with_counts=False)
    created: list[dict[str, Any]] = []
    for template in templates:
        start_local = compute_template_start(template, now_local)
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = start_utc + timedelta(minutes=max(10, _safe_int(template.get("duration_min"), 30)))
        meta = template.get("meta") if isinstance(template.get("meta"), dict) else {}
        template_project_key = normalize_project_key_local((meta or {}).get("project_key") or "")
        project_key = template_project_key or default_project_key_from_guild(guild)
        name = str(template.get("name") or f"{project_event_label(project_key)} Event").strip()[:100]
        dupe = None
        for ev in existing:
            if ev.name != name:
                continue
            if not getattr(ev, "start_time", None):
                continue
            delta = abs((ev.start_time - start_utc).total_seconds())
            if delta <= 3600:
                dupe = ev
                break
        if dupe is not None:
            continue
        channel_name = safe_channel_name(str(template.get("channel_name") or ""))
        channel = voice_channels.get(channel_name)
        description = str(template.get("description") or "").strip()
        default_location = project_site_url(project_key)
        reason_label = project_event_label(project_key)
        kwargs: dict[str, Any] = {
            "name": name,
            "description": description[:1000] if description else None,
            "start_time": start_utc,
            "end_time": end_utc,
            "privacy_level": discord.PrivacyLevel.guild_only,
            "reason": f"{reason_label} event template sync",
        }
        if channel is not None:
            kwargs["entity_type"] = discord.EntityType.voice
            kwargs["channel"] = channel
        else:
            kwargs["entity_type"] = discord.EntityType.external
            kwargs["location"] = (str(template.get("location") or default_location)[:100] or default_location)
        event = await guild.create_scheduled_event(**kwargs)
        existing.append(event)
        created.append({"name": event.name, "start_time": str(event.start_time), "channel": channel_name or kwargs.get("location")})
    return created


async def provision_papr_guild(guild: discord.Guild, requested_by: int = 0) -> dict[str, Any]:
    blueprint = papr_blueprint()
    created_roles: list[str] = []
    created_categories: list[str] = []
    created_channels: list[str] = []
    pinned_posts: list[dict[str, Any]] = []
    role_map: dict[str, discord.Role] = {}
    for role_spec in blueprint.get("roles", []):
        role = await ensure_role(guild, str(role_spec.get("name") or "Role"), int(role_spec.get("color") or 0))
        role_map[role.name] = role
        created_roles.append(role.name)
    staff_roles = [role_map[name] for name in ["PAPR Staff", "Draw Team", "Moderator"] if name in role_map]
    private_overwrites = await build_private_overwrites(guild, staff_roles)
    text_channels: dict[str, discord.TextChannel] = {}
    voice_channels: dict[str, discord.VoiceChannel] = {}
    for category_spec in blueprint.get("categories", []):
        category_name = str(category_spec.get("name") or "PAPR").strip()[:100]
        category = await ensure_category(guild, category_name, overwrites=private_overwrites if category_spec.get("private") else None)
        created_categories.append(category.name)
        for chan_name, topic in category_spec.get("text", []):
            channel = await ensure_text_channel(
                guild,
                category,
                chan_name,
                topic=topic,
                overwrites=private_overwrites if category_spec.get("private") else None,
            )
            text_channels[channel.name] = channel
            created_channels.append(f"text:{channel.name}")
        for voice_name in category_spec.get("voice", []):
            channel = await ensure_voice_channel(
                guild,
                category,
                voice_name,
                overwrites=private_overwrites if category_spec.get("private") else None,
            )
            voice_channels[channel.name] = channel
            created_channels.append(f"voice:{channel.name}")
    for marker, body in blueprint.get("posts", {}).items():
        channel = text_channels.get(safe_channel_name(marker))
        if channel is None:
            continue
        pinned_posts.append(await ensure_pinned_post(channel, marker, body, namespace="PAPR"))
    for event in blueprint.get("events", []):
        event_template_upsert(
            guild.id,
            template_key=str(event.get("template_key") or "papr_event"),
            name=str(event.get("name") or "PAPR Event"),
            description=str(event.get("description") or ""),
            frequency=str(event.get("frequency") or "weekly"),
            weekday=event.get("weekday"),
            start_hour=_safe_int(event.get("start_hour"), 12),
            start_minute=_safe_int(event.get("start_minute"), 0),
            duration_min=_safe_int(event.get("duration_min"), 30),
            channel_name=safe_channel_name(str(event.get("channel_name") or "")),
            location=str(event.get("location") or PAPR_SITE_URL),
            meta={"project_key": "papr", "requested_by": requested_by or primary_owner_discord_id()},
        )
    created_events = await ensure_template_events_for_guild(guild, voice_channels)
    return {
        "status": "ok",
        "guild_id": guild.id,
        "guild_name": guild.name,
        "roles": sorted(set(created_roles)),
        "categories": sorted(set(created_categories)),
        "channels": sorted(set(created_channels)),
        "pinned_posts": pinned_posts,
        "events_created": created_events,
        "event_template_count": len(blueprint.get("events", [])),
    }


async def provision_underfoot_guild(guild: discord.Guild, requested_by: int = 0) -> dict[str, Any]:
    blueprint = underfoot_blueprint()
    created_roles: list[str] = []
    created_categories: list[str] = []
    created_channels: list[str] = []
    pinned_posts: list[dict[str, Any]] = []
    role_map: dict[str, discord.Role] = {}
    for role_spec in blueprint.get("roles", []):
        role = await ensure_role(guild, str(role_spec.get("name") or "Role"), int(role_spec.get("color") or 0))
        role_map[role.name] = role
        created_roles.append(role.name)
    staff_roles = [role_map[name] for name in ["Underfoot Staff", "Playtest Crew", "Lore Keeper"] if name in role_map]
    private_overwrites = await build_private_overwrites(guild, staff_roles)
    text_channels: dict[str, discord.TextChannel] = {}
    voice_channels: dict[str, discord.VoiceChannel] = {}
    for category_spec in blueprint.get("categories", []):
        category_name = str(category_spec.get("name") or "Underfoot").strip()[:100]
        category = await ensure_category(guild, category_name, overwrites=private_overwrites if category_spec.get("private") else None)
        created_categories.append(category.name)
        for chan_name, topic in category_spec.get("text", []):
            channel = await ensure_text_channel(
                guild,
                category,
                chan_name,
                topic=topic,
                overwrites=private_overwrites if category_spec.get("private") else None,
            )
            text_channels[channel.name] = channel
            created_channels.append(f"text:{channel.name}")
        for voice_name in category_spec.get("voice", []):
            channel = await ensure_voice_channel(
                guild,
                category,
                voice_name,
                overwrites=private_overwrites if category_spec.get("private") else None,
            )
            voice_channels[channel.name] = channel
            created_channels.append(f"voice:{channel.name}")
    for marker, body in blueprint.get("posts", {}).items():
        channel = text_channels.get(safe_channel_name(marker))
        if channel is None:
            continue
        pinned_posts.append(await ensure_pinned_post(channel, marker, body, namespace="UNDERFOOT"))
    for event in blueprint.get("events", []):
        event_template_upsert(
            guild.id,
            template_key=str(event.get("template_key") or "underfoot_event"),
            name=str(event.get("name") or "Underfoot Event"),
            description=str(event.get("description") or ""),
            frequency=str(event.get("frequency") or "weekly"),
            weekday=event.get("weekday"),
            start_hour=_safe_int(event.get("start_hour"), 12),
            start_minute=_safe_int(event.get("start_minute"), 0),
            duration_min=_safe_int(event.get("duration_min"), 30),
            channel_name=safe_channel_name(str(event.get("channel_name") or "")),
            location=str(event.get("location") or UNDERFOOT_SITE_URL),
            meta={"project_key": "underfoot", "requested_by": requested_by or primary_owner_discord_id()},
        )
    created_events = await ensure_template_events_for_guild(guild, voice_channels)
    return {
        "status": "ok",
        "guild_id": guild.id,
        "guild_name": guild.name,
        "roles": sorted(set(created_roles)),
        "categories": sorted(set(created_categories)),
        "channels": sorted(set(created_channels)),
        "pinned_posts": pinned_posts,
        "events_created": created_events,
        "event_template_count": len(blueprint.get("events", [])),
    }


async def sync_event_templates_for_guild(guild: discord.Guild) -> list[dict[str, Any]]:
    voice_channels = {safe_channel_name(channel.name): channel for channel in guild.voice_channels}
    return await ensure_template_events_for_guild(guild, voice_channels)


async def watch_darius_dispatch_and_report(
    channel: discord.abc.Messageable,
    requester: discord.abc.User,
    policy: dict[str, Any],
    intent: HermesIntent,
    route: HermesRouteDecision,
    project_key: str,
    dispatch_id: str,
) -> None:
    surface = intent.surface
    last_status = ""
    for _ in range(40):
        try:
            data = await core_get(f"/darius/projects/{project_key}/dispatch/{dispatch_id}", {"token": CONTROL_TOKEN})
        except Exception as exc:
            await upsert_hermes_job(
                policy,
                intent=intent,
                route=HermesRouteDecision(**{**asdict(route), "route_status": "dispatch_lookup_failed"}),
                status="failed",
                public_summary="Darius dispatch lookup failed",
                private_summary="",
                dm_status="error",
                detail_text=compact_discord_text(str(exc), 800),
                verification=HermesVerificationResult(status="failed", summary="Dispatch lookup failed.", details=compact_discord_text(str(exc), 800)),
                command_key="darius_project",
                extra_meta={"dispatch_id": dispatch_id},
            )
            if surface == "project_channel":
                await channel.send(_project_blocked_text("Darius dispatch lookup failed."))
            elif surface != "dm":
                await channel.send(_public_status_text("Darius dispatch lookup failed."))
            return
        dispatch = data.get("dispatch", {}) if isinstance(data.get("dispatch"), dict) else {}
        delivery = data.get("delivery", {}) if isinstance(data.get("delivery"), dict) else {}
        status = str(dispatch.get("status") or "").strip().lower()
        if not status or status == last_status:
            await asyncio.sleep(6)
            continue
        last_status = status
        verification = HermesVerificationResult(
            status="verified" if status == "verified" else ("failed" if status == "failed" else "pending"),
            summary=str(dispatch.get("verificationSummary") or dispatch.get("summary") or "").strip(),
            details=str(dispatch.get("error") or dispatch.get("summary") or "").strip(),
            artifact_paths=[str(item).strip() for item in (dispatch.get("artifactPaths") or []) if str(item).strip()],
            touched_targets=[str(item).strip() for item in (dispatch.get("touchedFiles") or []) if str(item).strip()],
        )
        await upsert_hermes_job(
            policy,
            intent=intent,
            route=HermesRouteDecision(**{**asdict(route), "route_status": str(delivery.get("routeStatus") or status)}),
            status="completed" if status == "verified" else ("failed" if status == "failed" else "running"),
            public_summary="Darius verified" if status == "verified" else ("Darius failed" if status == "failed" else "Darius running"),
            private_summary=verification.summary,
            dm_status="not_needed",
            detail_text=verification.details,
            verification=verification,
            command_key="darius_project",
            extra_meta={"dispatch_id": dispatch_id, "delivery": delivery, "dispatch": dispatch},
        )
        if status in {"thread_bound", "planning"} and "manual codex bind" in (str(dispatch.get("verificationSummary") or dispatch.get("summary") or "").lower()):
            detail = compact_discord_text(str(dispatch.get("summary") or "Darius staged this in the local thread and is waiting for Codex bind."), 1800)
            dm_sent, dm_status = await send_detail_dm(requester, detail + build_private_activity_footer(intent.job_id))
            await upsert_hermes_job(
                policy,
                intent=intent,
                route=HermesRouteDecision(**{**asdict(route), "route_status": "thread_bound"}),
                status="queued",
                public_summary="Darius awaiting local bind",
                private_summary=detail,
                dm_status=dm_status,
                detail_text=detail,
                verification=HermesVerificationResult(status="pending", summary="Awaiting local Codex bind."),
                command_key="darius_project",
                extra_meta={"dispatch_id": dispatch_id},
            )
            if surface == "project_channel":
                await channel.send(_project_blocked_text("Darius is waiting for the local Codex bind."))
            elif surface != "dm" and not dm_sent:
                await channel.send(_public_status_text("Darius is waiting for the local Codex bind."))
        elif status == "verified":
            detail = compact_discord_text(str(dispatch.get("verificationSummary") or dispatch.get("summary") or "Darius verified the task."), 1800)
            dm_sent, dm_status = await send_detail_dm(requester, detail + build_private_activity_footer(intent.job_id))
            if surface == "project_channel":
                await channel.send(_project_safe_completion_text(dm_sent=dm_sent).replace("Task completed", "Darius verified the task"))
            elif surface != "dm":
                await channel.send(_public_status_text("Darius verified the task." if dm_sent else "Darius verified the task, but DM delivery failed."))
            await upsert_hermes_job(
                policy,
                intent=intent,
                route=HermesRouteDecision(**{**asdict(route), "route_status": "verified"}),
                status="completed",
                public_summary="Darius verified",
                private_summary=detail,
                dm_status=dm_status,
                detail_text=detail,
                verification=verification,
                command_key="darius_project",
                extra_meta={"dispatch_id": dispatch_id, "delivery": delivery, "dispatch": dispatch},
            )
            return
        elif status == "failed":
            detail = compact_discord_text(str(dispatch.get("error") or dispatch.get("summary") or "Darius hit a failure."), 1800)
            dm_sent, dm_status = await send_detail_dm(requester, detail + build_private_activity_footer(intent.job_id))
            if surface == "project_channel":
                await channel.send(_project_blocked_text("Darius hit a failure."))
            elif surface != "dm":
                await channel.send(_public_status_text("Darius hit a failure." if dm_sent else "Darius hit a failure. DM delivery failed."))
            await upsert_hermes_job(
                policy,
                intent=intent,
                route=HermesRouteDecision(**{**asdict(route), "route_status": "failed"}),
                status="failed",
                public_summary="Darius failed",
                private_summary=detail,
                dm_status=dm_status,
                detail_text=detail,
                verification=verification,
                command_key="darius_project",
                extra_meta={"dispatch_id": dispatch_id, "delivery": delivery, "dispatch": dispatch},
            )
            return
        await asyncio.sleep(6)


async def run_codex_job_and_report(
    channel: discord.abc.Messageable,
    requester: discord.abc.User,
    guild: Optional[discord.Guild],
    goal: str,
    scope: str = "implement",
    allow_write: bool = False,
    policy: Optional[dict[str, Any]] = None,
    job_id: str = "",
    attachments: Optional[list[str]] = None,
    intent: Optional[HermesIntent] = None,
    route_decision: Optional[HermesRouteDecision] = None,
    plan: Optional[HermesActionPlan] = None,
):
    current_policy = policy if isinstance(policy, dict) else {}
    surface = str(current_policy.get("surface") or ("dm" if isinstance(channel, discord.DMChannel) else "public_channel")).strip() or "public_channel"
    record_runtime_breadcrumb(
        "dispatch",
        severity="info",
        scene="text",
        route=str(scope or "implement").strip(),
        connection=runtime_connection_summary(
            surface=surface,
            project_key=str(current_policy.get("project_key") or ""),
            guild_id=current_policy.get("guild_id") or "",
            channel_id=current_policy.get("channel_id") or "",
            route_status="running",
            speaker=getattr(requester, "display_name", getattr(requester, "name", "user")),
            request_kind="codex",
        ),
        job_id=job_id,
    )
    resolved_intent = intent if isinstance(intent, HermesIntent) else build_hermes_intent(
        message=type("HermesMessageProxy", (), {"content": goal, "attachments": [], "guild": guild, "channel": channel, "author": requester})(),  # type: ignore[arg-type]
        content=goal,
        policy=current_policy,
        priority="project_action",
    )
    if job_id:
        resolved_intent.job_id = job_id
    if attachments:
        resolved_intent.attachments = [str(item).strip() for item in attachments if str(item).strip()]
    route = route_decision if isinstance(route_decision, HermesRouteDecision) else build_hermes_route_decision(resolved_intent, current_policy)
    resolved_plan = plan if isinstance(plan, HermesActionPlan) else await build_zeus_execution_plan(resolved_intent, route, current_policy)
    route = apply_zeus_plan_to_route(resolved_intent, route, resolved_plan)
    if route.resolved_project_key and route.resolved_project_key != normalize_project_key_local(current_policy.get("project_key") or ""):
        current_policy = dict(current_policy)
        current_policy["project_key"] = route.resolved_project_key
    execution_scope = str(resolved_plan.requested_action or scope).strip() or scope
    if resolved_plan.requires_confirmation:
        confirmation_text = compact_discord_text(
            resolved_plan.public_ack or "Zeus wants explicit confirmation before Hermes applies that change.",
            1800,
        )
        if surface == "dm":
            await channel.send(confirmation_text)
        elif surface == "project_channel":
            await channel.send(_project_blocked_text("Zeus wants explicit confirmation before applying that change."))
        else:
            await channel.send(_public_status_text("Zeus wants confirmation before applying that change."))
        await upsert_hermes_job(
            current_policy,
            intent=resolved_intent,
            route=HermesRouteDecision(**{**asdict(route), "route_status": "confirmation_required"}),
            status="needs_confirmation",
            public_summary="Awaiting confirmation",
            private_summary=confirmation_text,
            dm_status="needs_confirmation",
            detail_text=confirmation_text,
            verification=HermesVerificationResult(status="needs_confirmation", summary="Zeus requested explicit confirmation before execution."),
            command_key=route.executor_kind or "project_write",
            extra_meta={"scope": execution_scope, "surface": surface, "zeus_plan": asdict(resolved_plan)},
        )
        return
    done_event = asyncio.Event()
    progress_state = {"sent": 0}
    progress_first = resolved_plan.public_ack or (
        f"{pretty_project_label(route.resolved_project_key or default_project_key_from_guild(guild))}: handing that to Darius now."
        if route.executor_kind == "darius_project"
        else build_codex_progress_update(goal)
    )
    progress_repeat = "Still in progress with Darius now." if route.executor_kind == "darius_project" else "Still in progress with Codex now."

    async def _send_progress(text: str):
        progress_state["sent"] += 1
        if surface == "dm":
            await channel.send(compact_discord_text(text, 260))
        elif surface == "project_channel":
            await channel.send(compact_discord_text(text, 260))
        else:
            await channel.send(_public_status_text(text))

    progress_task = asyncio.create_task(
        run_progress_notifier(
            _send_progress,
            done_event,
            [
                (DISCORD_PROGRESS_FIRST_SEC, progress_first),
                (DISCORD_PROGRESS_SECOND_SEC, progress_repeat),
            ],
            repeat_text=progress_repeat,
            repeat_every_sec=DISCORD_PROGRESS_REPEAT_SEC,
        )
    )
    try:
        review = await review_discord_action(
            goal,
            current_policy,
            action_kind=route.review_action_kind,
            allow_write=allow_write,
            meta={
                "scope": execution_scope,
                "surface": surface,
                "correlation_id": resolved_intent.correlation_id,
                "attachments": resolved_intent.attachments,
                "executor_kind": route.executor_kind,
                "resolved_project_key": route.resolved_project_key,
                "zeus_plan": asdict(resolved_plan),
            },
        )
        if not review.get("approved"):
            reason = compact_discord_text(str(review.get("detail") or "Oracle review blocked this action."), 1500)
            verification = HermesVerificationResult(status="failed", summary="Review blocked the action.", details=reason)
            if surface == "dm":
                await channel.send(reason)
            elif surface == "project_channel":
                await channel.send(_project_blocked_text(reason))
            else:
                await channel.send(_public_status_text("Oracle review blocked that action."))
            await upsert_hermes_job(
                current_policy,
                intent=resolved_intent,
                route=HermesRouteDecision(**{**asdict(route), "route_status": "review_blocked"}),
                status="blocked",
                public_summary="Oracle review blocked",
                private_summary="",
                dm_status="blocked",
                detail_text=reason,
                verification=verification,
                command_key=route.executor_kind or "project_write",
                extra_meta={"scope": execution_scope, "surface": surface, "review": review, "zeus_plan": asdict(resolved_plan)},
            )
            record_runtime_breadcrumb(
                "block",
                severity="warn",
                scene="text",
                route=str(route.executor_kind or execution_scope or "project_write").strip(),
                connection=runtime_connection_summary(
                    surface=surface,
                    project_key=str(current_policy.get("project_key") or ""),
                    guild_id=current_policy.get("guild_id") or "",
                    channel_id=current_policy.get("channel_id") or "",
                    route_status="review_blocked",
                ),
                reason=reason,
            )
            return
        allow_write = bool(review.get("allow_write", allow_write))
        project_key = normalize_project_key_local(route.resolved_project_key or current_policy.get("project_key") or default_project_key_from_guild(guild))
        if route.executor_kind == "darius_project":
            route_data = await get_darius_route(project_key)
            route_row = route_data.get("route", {}) if isinstance(route_data, dict) else {}
            route_status = str(route_row.get("routeStatus") or "").strip().lower()
            if not route_row or route_status in {"", "offline", "failed", "stale", "unassigned"}:
                raise RuntimeError(f"{pretty_project_label(project_key)} is local-only right now and the assigned Darius machine is offline or unassigned.")
            dispatch_result = await queue_darius_dispatch(
                project_key,
                text=goal,
                requested_action=execution_scope or "implement",
                attachments=resolved_intent.attachments,
                requested_by=f"discord:{getattr(requester, 'id', 0)}",
                source="discord_hermes",
                expect_verification=True,
                allow_write=allow_write,
            )
            dispatch = dispatch_result.get("dispatch", {}) if isinstance(dispatch_result, dict) else {}
            dispatch_id = str(dispatch.get("id") or "").strip()
            machine_label = str(route_row.get("machineLabel") or "project machine").strip() or "project machine"
            local_thread = str(route_row.get("activeLocalThreadId") or "awaiting-local-thread").strip() or "awaiting-local-thread"
            message = compact_discord_text(
                f"{pretty_project_label(project_key)}: Darius accepted this on {machine_label}. "
                f"Local thread: {local_thread}. Dispatch id: {dispatch_id or 'pending'}.",
                1800,
            )
            dm_status = "not_needed"
            if surface == "dm":
                await channel.send(message)
            else:
                dm_sent, dm_status = await send_detail_dm(requester, compact_discord_text(message + build_private_activity_footer(resolved_intent.job_id), 1900))
                if surface == "project_channel":
                    await channel.send(_project_safe_completion_text(dm_sent=dm_sent).replace("Task completed", "Darius accepted the task"))
                else:
                    await channel.send(_public_status_text("Darius accepted the task on the assigned project machine." if dm_sent else "Darius accepted the task, but DM delivery failed."))
            await upsert_hermes_job(
                current_policy,
                intent=resolved_intent,
                route=HermesRouteDecision(**{**asdict(route), "route_status": route_status or "queued"}),
                status="queued",
                public_summary="Queued to Darius",
                private_summary=message,
                dm_status=dm_status,
                detail_text=message,
                verification=HermesVerificationResult(status="pending", summary="Queued to Darius."),
                command_key="darius_project",
                extra_meta={"scope": execution_scope, "allow_write": bool(allow_write), "surface": surface, "dispatch_id": dispatch_id, "route": route_row, "zeus_plan": asdict(resolved_plan)},
            )
            asyncio.create_task(watch_darius_dispatch_and_report(channel, requester, current_policy, resolved_intent, route, project_key, dispatch_id))
            return
        session_id = await ensure_codex_session_binding(
            channel,
            guild,
            project_key=project_key,
            requester_id=int(getattr(requester, "id", 0) or 0),
            allow_write=allow_write,
        )
        result = await codex_session_message(session_id=session_id, prompt=goal, scope=execution_scope, allow_write=allow_write)
        message = render_codex_result(result)
        dm_status = "not_needed"
        if surface == "dm":
            if progress_state["sent"]:
                message = compact_discord_text(f"Done. {message}", 1800)
            await channel.send(message)
        else:
            dm_sent, dm_status = await send_detail_dm(requester, compact_discord_text(message + build_private_activity_footer(resolved_intent.job_id), 1900))
            if surface == "project_channel":
                await channel.send(_project_safe_completion_text(dm_sent=dm_sent))
            else:
                await channel.send(_public_status_text("Task completed. Detailed DM sent." if dm_sent else "Task completed. DM delivery failed."))
        await upsert_hermes_job(
            current_policy,
            intent=resolved_intent,
            route=HermesRouteDecision(**{**asdict(route), "route_status": "completed"}),
            status="completed",
            public_summary="Codex completed",
            private_summary=message,
            dm_status=dm_status,
            detail_text=message,
            verification=HermesVerificationResult(status="verified", summary="Codex completed."),
            command_key="server_codex",
            extra_meta={"scope": execution_scope, "allow_write": bool(allow_write), "surface": surface, "session_id": session_id, "zeus_plan": asdict(resolved_plan)},
        )
    except Exception as e:
        failure_text = compact_discord_text(str(e) or "Unknown execution failure", 1200)
        if route.executor_kind == "darius_project":
            failure = f"Darius dispatch failed: {failure_text}"
            if surface == "dm":
                await channel.send(failure)
            elif surface == "project_channel":
                await channel.send(_project_blocked_text("Darius hit a failure."))
            else:
                await channel.send(_public_status_text("Darius hit a failure."))
        else:
            failure = f"Codex dispatch failed: {failure_text}"
            if surface == "dm":
                await channel.send(failure)
            elif surface == "project_channel":
                await channel.send(_project_blocked_text("Codex hit a failure. Detailed status stays in DM/UI."))
            else:
                await channel.send(_public_status_text("Codex hit a failure."))
        await upsert_hermes_job(
            current_policy,
            intent=resolved_intent,
            route=HermesRouteDecision(**{**asdict(route), "route_status": "failed"}),
            status="failed",
            public_summary="Darius failed" if route.executor_kind == "darius_project" else "Codex failed",
            private_summary="",
            dm_status="error",
            detail_text=compact_discord_text(str(e), 800),
            verification=HermesVerificationResult(status="failed", summary="Execution failed.", details=compact_discord_text(str(e), 800)),
            command_key=route.executor_kind or "project_write",
            extra_meta={"scope": execution_scope, "allow_write": bool(allow_write), "surface": surface, "error": compact_discord_text(str(e), 300), "zeus_plan": asdict(resolved_plan)},
        )
        record_runtime_breadcrumb(
            "fallback",
            severity="error",
            scene="text",
            route=str(route.executor_kind or execution_scope or "project_write").strip(),
            connection=runtime_connection_summary(
                surface=surface,
                project_key=str(current_policy.get("project_key") or ""),
                guild_id=current_policy.get("guild_id") or "",
                channel_id=current_policy.get("channel_id") or "",
                route_status="failed",
            ),
            error=failure_text,
        )
        await submit_runtime_diagnostic_receipt(
            current_policy,
            stage="dispatch",
            severity="error",
            scene="text",
            route=str(route.executor_kind or execution_scope or "project_write").strip(),
            reason=str(e),
            meta={"scope": execution_scope, "surface": surface, "job_id": job_id},
        )
    finally:
        done_event.set()
        if not progress_task.done():
            progress_task.cancel()
def poll_store_open(guild_id: int, channel_id: int, message_id: int, question: str, options: list[str], closes_at: int | None):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO discord_polls (guild_id, channel_id, message_id, question, options_json, status, closes_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE question=VALUES(question), options_json=VALUES(options_json), status=VALUES(status), closes_at=VALUES(closes_at)
        """,
        (str(guild_id), str(channel_id), str(message_id), question, json.dumps(options), "open", closes_at),
    )
    conn.commit()
    cur.close()
    conn.close()


def poll_store_closed(message_id: int, result: dict[str, Any]):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE discord_polls SET status=%s, result_json=%s WHERE message_id=%s",
        ("closed", json.dumps(result), str(message_id)),
    )
    conn.commit()
    cur.close()
    conn.close()


def raffle_store_open(guild_id: int, channel_id: int, message_id: int, prize: str, winners: int, closes_at: int | None):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO discord_raffles (guild_id, channel_id, message_id, prize, winners, status, closes_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE prize=VALUES(prize), winners=VALUES(winners), status=VALUES(status), closes_at=VALUES(closes_at)
        """,
        (str(guild_id), str(channel_id), str(message_id), prize, winners, "open", closes_at),
    )
    conn.commit()
    cur.close()
    conn.close()


def raffle_store_closed(message_id: int, entrants: list[dict[str, Any]], winners: list[dict[str, Any]]):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE discord_raffles SET status=%s, entrants_json=%s, result_json=%s WHERE message_id=%s",
        ("closed", json.dumps(entrants), json.dumps({"winners": winners}), str(message_id)),
    )
    conn.commit()
    cur.close()
    conn.close()


def feedback_store(guild_id: Optional[int], channel_id: Optional[int], user_id: int, user_name: str, feedback_type: str, content: str, meta: Optional[dict[str, Any]] = None) -> int:
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO discord_feedback (guild_id, channel_id, user_id, user_name, feedback_type, content, status, meta_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            str(guild_id) if guild_id else None,
            str(channel_id) if channel_id else None,
            str(user_id),
            user_name[:255],
            (feedback_type or "general")[:32],
            content,
            "open",
            json.dumps(meta or {}),
        ),
    )
    conn.commit()
    row_id = cur.lastrowid
    cur.close()
    conn.close()
    return int(row_id)


def feedback_latest(limit: int = 10) -> list[dict[str, Any]]:
    conn = db_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT id, created_at, user_name, feedback_type, LEFT(content, 260) AS content, status FROM discord_feedback ORDER BY id DESC LIMIT %s",
        (max(1, min(limit, 50)),),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def interview_store_open(guild_id: int, channel_id: int, candidate_id: int, candidate_name: str, requested_by: int, role_name: str, notes: str):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO discord_interviews (guild_id, channel_id, candidate_id, candidate_name, requested_by, role_name, notes, status, decision, meta_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            str(guild_id),
            str(channel_id),
            str(candidate_id),
            candidate_name[:255],
            str(requested_by),
            role_name[:255],
            notes,
            "open",
            None,
            json.dumps({}),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()


def interview_store_close(channel_id: int, decision: str):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE discord_interviews SET status=%s, decision=%s WHERE channel_id=%s AND status='open'",
        ("closed", decision[:64], str(channel_id)),
    )
    conn.commit()
    cur.close()
    conn.close()


async def close_poll(channel: discord.abc.Messageable, message_id: int, closed_by: str = "manual") -> dict[str, Any]:
    state = active_polls.get(message_id)
    if not state:
        return {"status": "missing", "message_id": message_id}
    try:
        msg = await channel.fetch_message(message_id)  # type: ignore[attr-defined]
    except Exception as e:
        return {"status": "error", "detail": str(e), "message_id": message_id}
    votes: list[dict[str, Any]] = []
    for i, emoji in enumerate(state.get("emojis", [])):
        reaction = discord.utils.get(msg.reactions, emoji=emoji)
        count = max(0, (reaction.count - 1) if reaction else 0)
        votes.append({"option": state["options"][i], "votes": count, "emoji": emoji})
    winner = sorted(votes, key=lambda x: x["votes"], reverse=True)[0] if votes else {"option": "n/a", "votes": 0}
    result = {"status": "closed", "message_id": message_id, "question": state.get("question"), "votes": votes, "winner": winner, "closed_by": closed_by}
    poll_store_closed(message_id, result)
    active_polls.pop(message_id, None)
    lines = [f"Poll closed: {state.get('question', 'Poll')}", f"Winner: {winner.get('option')} ({winner.get('votes', 0)} votes)"]
    for v in votes:
        lines.append(f"{v['emoji']} {v['option']} - {v['votes']}")
    await channel.send("\n".join(lines))
    if bool(state.get("community_guild_enabled")):
        policy = {
            "project_key": str(state.get("project_key") or ""),
            "guild_id": str(state.get("guild_id") or ""),
            "channel_id": str(state.get("channel_id") or ""),
            "surface": "public_channel",
            "community_guild": {
                "olympus_mirror_channel_id": str(state.get("olympus_mirror_channel_id") or ""),
            },
        }
        await upsert_discord_community_feedback_receipt_core(
            policy,
            receipt_kind="poll_close",
            summary=f"Community poll closed: {state.get('question') or message_id}",
            detail_text=json.dumps(result, ensure_ascii=False),
            status="closed",
            meta={
                "poll_message_id": str(message_id),
                "closed_by": closed_by,
                "source": "poll_close_runtime",
            },
        )
        record_runtime_breadcrumb(
            "community_poll_close_result",
            severity="info",
            scene="discord",
            route="poll_close",
            connection=runtime_connection_summary(
                surface="public_channel",
                project_key=str(state.get("project_key") or ""),
                guild_id=str(state.get("guild_id") or ""),
                channel_id=str(state.get("channel_id") or ""),
                route_status="closed",
            ),
            message_id=str(message_id),
            closed_by=closed_by,
        )
        await route_public_community_owner_notice(
            policy,
            event_key="poll_close",
            summary=f"{pretty_project_label(str(state.get('project_key') or ''))} poll closed: {compact_discord_text(str(state.get('question') or message_id), 160)}",
            requested_by=closed_by,
        )
    return result


async def close_raffle(channel: discord.abc.Messageable, message_id: int, closed_by: str = "manual") -> dict[str, Any]:
    state = active_raffles.get(message_id)
    if not state:
        return {"status": "missing", "message_id": message_id}
    try:
        msg = await channel.fetch_message(message_id)  # type: ignore[attr-defined]
    except Exception as e:
        return {"status": "error", "detail": str(e), "message_id": message_id}
    reaction = discord.utils.get(msg.reactions, emoji=RAFFLE_EMOJI)
    entrants: list[dict[str, Any]] = []
    if reaction:
        async for u in reaction.users():
            if u.bot:
                continue
            entrants.append({"id": u.id, "name": u.display_name if hasattr(u, "display_name") else u.name})
    random.shuffle(entrants)
    winners_count = max(1, int(state.get("winners", 1)))
    winners = entrants[:winners_count]
    raffle_store_closed(message_id, entrants, winners)
    active_raffles.pop(message_id, None)
    if winners:
        mentions = " ".join([f"<@{w['id']}>" for w in winners])
        await channel.send(f"Raffle closed for **{state.get('prize','Prize')}**. Winners: {mentions}")
    else:
        await channel.send(f"Raffle closed for **{state.get('prize','Prize')}**. No valid entrants.")
    return {"status": "closed", "winners": winners, "entrants": entrants, "closed_by": closed_by}


async def admin_maintenance_loop():
    global last_event_template_sync_ts, last_archive_compaction_ts, last_continuous_steward_ts
    while True:
        now = int(time.time())
        try:
            for message_id, info in list(active_raffles.items()):
                closes_at = int(info.get("closes_at", 0) or 0)
                if closes_at and now >= closes_at:
                    channel = bot.get_channel(int(info.get("channel_id", 0) or 0))
                    if channel is not None:
                        await close_raffle(channel, message_id, closed_by="timer")
            for message_id, info in list(active_polls.items()):
                closes_at = int(info.get("closes_at", 0) or 0)
                if closes_at and now >= closes_at:
                    channel = bot.get_channel(int(info.get("channel_id", 0) or 0))
                    if channel is not None:
                        await close_poll(channel, message_id, closed_by="timer")
            now_ts = time.time()
            if now_ts - last_event_template_sync_ts >= max(60.0, DISCORD_EVENT_TEMPLATE_CHECK_INTERVAL_SEC):
                for guild in list(bot.guilds):
                    try:
                        created = await sync_event_templates_for_guild(guild)
                        if created:
                            print(f"[discord-admin] event template sync guild={guild.name} created={json.dumps(created)[:1000]}")
                    except Exception as sync_error:
                            print(f"[discord-admin] event sync failed for guild={getattr(guild, 'name', guild.id)}: {sync_error}")
                last_event_template_sync_ts = now_ts
            if now_ts - last_archive_compaction_ts >= DISCORD_ARCHIVE_COMPACT_INTERVAL_SEC:
                try:
                    trimmed = compact_discord_archive()
                    if trimmed.get("trimmed") or trimmed.get("quotes"):
                        print(f"[discord-archive] compacted trimmed={trimmed.get('trimmed')} quotes={trimmed.get('quotes')}")
                except Exception as archive_error:
                    print(f"[discord-archive] compaction failed: {archive_error}")
                last_archive_compaction_ts = now_ts
            if DISCORD_CONTINUOUS_STEWARD_ENABLED and now_ts - last_continuous_steward_ts >= DISCORD_CONTINUOUS_STEWARD_INTERVAL_SEC:
                for guild in list(bot.guilds):
                    try:
                        blueprint_key = default_blueprint_key_for_guild(guild)
                        if not blueprint_key:
                            continue
                        summary = await sync_discord_blueprint(guild, blueprint_key=blueprint_key, requested_by=primary_owner_discord_id(), dry_run=False)
                        print(f"[discord-steward] synced guild={guild.name} blueprint={blueprint_key} channels={len(summary.get('channels', []))} stickies={len(summary.get('pinned_posts', []))}")
                    except Exception as steward_error:
                        print(f"[discord-steward] sync failed for guild={getattr(guild, 'name', guild.id)}: {steward_error}")
                last_continuous_steward_ts = now_ts
            await sync_incident_notifications()
        except Exception as e:
            print(f"[discord-admin] maintenance loop error: {e}")
        await asyncio.sleep(10)

async def decide_discord_action(message: discord.Message, priority: str) -> dict:
    cleaned = (message.content or "").strip().lower()
    if any(marker in cleaned for marker in ["general channel", "general chat", "change the general", "name of the general", "channel header", "change the header", "rename the header"]):
        return {
            "action": "theme_channel",
            "reply": "Retheming the live channel and header now.",
            "target_name": "general",
        }
    heuristic_blueprint = {}
    if message.guild is not None:
        requested_key = ""
        if "revamp" in cleaned or "staging" in cleaned:
            requested_key = "revamp_staging"
        elif "world of epoch" in cleaned or "worldofepoch" in cleaned:
            requested_key = "world_of_epoch_realm"
        elif "underfoot" in cleaned:
            requested_key = "underfoot_unity_lane"
        heuristic_blueprint = discord_ops_blueprint(
            blueprint_key=requested_key,
            guild_name=message.guild.name,
            project_key=default_project_key_from_guild(message.guild),
        )
    if heuristic_blueprint and any(
        marker in cleaned
        for marker in [
            "sync branding",
            "sync channels",
            "change the name of the",
            "channel name",
            "general channel",
            "channel header",
            "change the header",
            "rename the header",
            "theme of the channels",
            "theme the channels",
            "sync sticky",
            "sticky message",
            "pinned message",
            "server icon",
            "server banner",
            "rename server",
            "rename guild",
            "rename channel",
            "revamp the discord",
            "fix the discord",
            "fix discord",
            "discord channel",
            "discord channels",
            "papr influence",
            "papr branding",
            "remove papr",
            "lose the papr influence",
            "raffles section",
            "rename the raffles",
            "change the raffles",
            "fix the raffles",
        ]
    ):
        if any(marker in cleaned for marker in ["general channel", "general chat", "channel header", "change the header", "rename the header", "change the name of the general", "change the general chat"]):
            return {
                "action": "theme_channel",
                "reply": "Retheming the live channel and header now.",
                "target_name": "general",
            }
        return {
            "action": "sync_blueprint",
            "reply": f"Applying the `{heuristic_blueprint.get('title') or heuristic_blueprint.get('key')}` Discord blueprint now.",
            "blueprint_key": str(heuristic_blueprint.get("key") or "").strip(),
        }
    prompt = (
        f"You are {olympus_discord_name()} relaying for {olympus_operator_name()} while deciding what to do inside Discord. Return ONLY strict JSON with keys: "
        "action, reply, channel_name, channel_topic, category_name, target_name, allow_names, deny_names, blueprint_key. "
        "Allowed action values: reply, create_channel, set_permissions, sync_blueprint, theme_channel, none. "
        "Use sync_blueprint when the user is asking for a Discord branding/template sync, guild rename, channel rename sweep, sticky post refresh, icon update, or banner update. "
        "Use theme_channel when the user wants a live channel rename, a better header/topic, or a cooler themed general chat. "
        "Only choose create_channel or set_permissions when the user is explicitly asking for a Discord server change. "
        "Never invent admin requests. Keep reply concise.\n\n"
        f"Priority: {priority}\n"
        f"Channel type: {'dm' if is_dm(message) else 'guild'}\n"
        f"Author: {getattr(message.author, 'display_name', message.author.name)}\n"
        f"Default blueprint: {str(heuristic_blueprint.get('key') or default_blueprint_key_for_guild(message.guild)).strip()}\n"
        f"Message: {(message.content or '').strip()}"
    )
    raw = await query_assistant(prompt)
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
    except Exception:
        pass
    return {"action": "reply", "reply": raw[:1900] if raw else "I'm here."}


def pcm_to_wav_bytes(pcm_data: bytes) -> bytes:
    bio = io.BytesIO()
    with wave.open(bio, "wb") as wf:
        wf.setnchannels(PCM_CHANNELS)
        wf.setsampwidth(PCM_SAMPLE_WIDTH)
        wf.setframerate(PCM_SAMPLE_RATE)
        wf.writeframes(pcm_data)
    return bio.getvalue()


def safe_channel_name(name: str) -> str:
    base = (name or "new-channel").strip().lower().replace(" ", "-")
    cleaned = "".join(ch for ch in base if ch.isalnum() or ch in "-_")
    return cleaned[:90] or "new-channel"


def _guild_theme_name_pool(guild: Optional[discord.Guild]) -> list[str]:
    project_key = default_project_key_from_guild(guild)
    if project_key == "underfoot":
        return ["burrow-square", "rootreach", "lantern-hollow", "moss-market", "warble-way"]
    if project_key == "mount-olympus-web" or "olympus" in str(getattr(guild, "name", "") or "").lower():
        return ["thunder-floor", "summit-forum", "oracle-square", "zeus-deck", "council-step"]
    return ["main-floor", "project-square", "signal-hall", "crew-lane", "guild-floor"]


def themed_channel_topic(guild: Optional[discord.Guild], request_text: str, channel_name: str) -> str:
    project_key = default_project_key_from_guild(guild)
    if project_key == "underfoot":
        return compact_discord_text("Warbles gather here for live chatter, quick calls, and current tunnel updates.", 900)
    if project_key == "mount-olympus-web" or "olympus" in str(getattr(guild, "name", "") or "").lower():
        return compact_discord_text("Live Olympus floor for current strategy, design direction, and real-time announcements.", 900)
    return compact_discord_text(f"Live project floor for {channel_name.replace('-', ' ')} discussion and updates.", 900)


def suggest_themed_channel_name(guild: Optional[discord.Guild], current_name: str, request_text: str) -> str:
    normalized_current = safe_channel_name(current_name)
    lowered = str(request_text or "").lower()
    candidates = _guild_theme_name_pool(guild)
    if "general" in lowered or normalized_current in {"general", "general-chat", "chat", "lobby"}:
        for candidate in candidates:
            if candidate != normalized_current:
                return candidate
    return candidates[0] if candidates else normalized_current


async def verify_channel_theme_change(target: discord.abc.GuildChannel, expected_name: str = "", expected_topic: str = "") -> HermesVerificationResult:
    try:
        refreshed = await target.guild.fetch_channel(target.id)  # type: ignore[attr-defined]
    except Exception:
        refreshed = target
    actual_name = str(getattr(refreshed, "name", "") or "").strip()
    actual_topic = str(getattr(refreshed, "topic", "") or "").strip()
    name_ok = not expected_name or actual_name == expected_name
    topic_ok = not expected_topic or actual_topic == expected_topic
    if name_ok and topic_ok:
        summary_parts = []
        if expected_name:
            summary_parts.append(f"name -> #{expected_name}")
        if expected_topic:
            summary_parts.append("header updated")
        return HermesVerificationResult(
            status="verified",
            summary=", ".join(summary_parts) or "Discord surface verified.",
            details=f"Verified live Discord state for {actual_name}.",
            touched_targets=[f"channel:{target.id}"],
        )
    mismatch = []
    if expected_name and not name_ok:
        mismatch.append(f"name stayed `{actual_name}`")
    if expected_topic and not topic_ok:
        mismatch.append("header/topic did not match")
    return HermesVerificationResult(
        status="failed",
        summary="Discord change did not read back correctly.",
        details=", ".join(mismatch) or "Verification failed.",
        touched_targets=[f"channel:{target.id}"],
    )


async def apply_theme_channel_action(message: discord.Message, decision: dict[str, Any]) -> dict[str, Any]:
    guild = message.guild
    if guild is None:
        reply = "I can only retheme channels inside a server."
        await message.reply(reply, mention_author=False)
        return {"action": "reply", "reply": reply, "verification": asdict(HermesVerificationResult(status="failed", summary=reply))}
    target_name = safe_channel_name(str(decision.get("target_name") or "general").strip() or "general")
    target = discord.utils.get(guild.text_channels, name=target_name)
    if target is None and isinstance(message.channel, discord.TextChannel):
        target = message.channel
    if target is None:
        reply = "I could not find the live channel to retheme."
        await message.reply(reply, mention_author=False)
        return {"action": "reply", "reply": reply, "verification": asdict(HermesVerificationResult(status="failed", summary=reply))}
    old_name = str(target.name)
    old_topic = str(getattr(target, "topic", "") or "")
    new_name = safe_channel_name(str(decision.get("channel_name") or suggest_themed_channel_name(guild, old_name, message.content or "")).strip() or old_name)
    new_topic = str(decision.get("channel_topic") or themed_channel_topic(guild, message.content or "", new_name)).strip()
    await target.edit(name=new_name, topic=new_topic or None, reason=f"{DISCORD_CONNECTOR_NAME} themed channel tune")
    verification = await verify_channel_theme_change(target, expected_name=new_name, expected_topic=new_topic)
    if verification.status != "verified":
        reply = compact_discord_text(f"Discord theme update needs attention: {verification.details}", 1800)
        await message.reply(reply, mention_author=False)
        return {"action": "theme_channel", "reply": reply, "verification": asdict(verification)}
    reply = compact_discord_text(f"Updated #{old_name} -> #{new_name}. Header refreshed." if old_name != new_name else f"Updated the header for #{new_name}.", 1800)
    await message.reply(reply, mention_author=False)
    return {
        "action": "theme_channel",
        "reply": reply,
        "summary": {
            "old_name": old_name,
            "new_name": new_name,
            "old_topic": old_topic,
            "new_topic": new_topic,
        },
        "verification": asdict(verification),
    }


def permission_role_lookup(guild: discord.Guild, names: list[str]):
    found = []
    for role_name in names or []:
        role = discord.utils.get(guild.roles, name=role_name)
        if role:
            found.append(role)
    return found


async def execute_discord_action(
    message: discord.Message,
    decision: dict,
    policy: Optional[dict[str, Any]] = None,
    *,
    intent: Optional[HermesIntent] = None,
    route: Optional[HermesRouteDecision] = None,
    plan: Optional[HermesActionPlan] = None,
    review: Optional[dict[str, Any]] = None,
):
    action = (decision.get("action") or "reply").strip().lower()
    if action == "reply" or is_dm(message) or not DISCORD_ACTIONS_ENABLED:
        reply = (decision.get("reply") or "I'm here.")[:1900]
        if not decision.get("_skip_typing") and os.getenv("DISCORD_TYPING_INDICATOR", "true").lower() == "true":
            async with message.channel.typing():
                await message.reply(reply, mention_author=False)
        else:
            await message.reply(reply, mention_author=False)
        return {"action": "reply", "reply": reply}

    guild = message.guild
    if guild is None:
        reply = (decision.get("reply") or "I can only change channels and permissions inside a server.")[:1900]
        await message.reply(reply, mention_author=False)
        return {"action": "reply", "reply": reply}

    me = guild.me or guild.get_member(bot.user.id)
    if not me or not me.guild_permissions.manage_channels:
        reply = "I do not have Manage Channels permission to do that here."
        await message.reply(reply, mention_author=False)
        return {"action": "reply", "reply": reply}
    current_policy = policy if isinstance(policy, dict) and policy else _cached_message_policy(message)
    resolved_intent = intent if isinstance(intent, HermesIntent) else build_hermes_intent(message, (message.content or "").strip(), current_policy)
    resolved_route = route if isinstance(route, HermesRouteDecision) else build_hermes_route_decision(resolved_intent, current_policy)
    resolved_plan = plan if isinstance(plan, HermesActionPlan) else await build_zeus_execution_plan(resolved_intent, resolved_route, current_policy)
    resolved_route = apply_zeus_plan_to_route(resolved_intent, resolved_route, resolved_plan)
    if resolved_plan.requires_confirmation:
        reply = compact_discord_text(resolved_plan.public_ack or "Zeus wants explicit confirmation before Hermes changes that live Discord structure.", 1800)
        await message.reply(reply, mention_author=False)
        return {
            "action": "reply",
            "reply": reply,
            "verification": asdict(HermesVerificationResult(status="needs_confirmation", summary="Zeus requested explicit confirmation before execution.")),
            "plan": asdict(resolved_plan),
        }
    review_kind = "branding_sync" if action == "sync_blueprint" else "discord_admin"
    if review is None:
        review = await review_discord_action(
            (message.content or "").strip() or f"Discord admin action: {action}",
            current_policy,
            action_kind=review_kind,
            allow_write=True,
            meta={"decision": action, "blueprint_key": decision.get("blueprint_key"), "zeus_plan": asdict(resolved_plan)},
        )
    if not review.get("approved"):
        reason = compact_discord_text(str(review.get("detail") or "Oracle review blocked this Discord action."), 1500)
        await message.reply(reason, mention_author=False)
        await log_discord_denial(current_policy, command_key=action or "discord_admin", reason=reason, goal=(message.content or "").strip(), meta={"review": review})
        record_runtime_breadcrumb(
            "block",
            severity="warn",
            scene=str(current_policy.get("surface") or "discord").strip(),
            route=action,
            connection=runtime_connection_summary(
                surface=str(current_policy.get("surface") or "discord").strip(),
                project_key=str(current_policy.get("project_key") or "").strip(),
                guild_id=current_policy.get("guild_id") or "",
                channel_id=current_policy.get("channel_id") or "",
                route_status="review_blocked",
            ),
            reason=reason,
        )
        return {"action": "reply", "reply": reason, "plan": asdict(resolved_plan)}

    if action == "theme_channel":
        themed_result = await apply_theme_channel_action(message, decision)
        themed_result["plan"] = asdict(resolved_plan)
        return themed_result

    if action == "sync_blueprint":
        blueprint_key = str(decision.get("blueprint_key") or default_blueprint_key_for_guild(guild) or "").strip()
        try:
            summary = await sync_discord_blueprint(guild, blueprint_key=blueprint_key, requested_by=message.author.id, dry_run=False)
        except Exception as exc:
            reply = compact_discord_text(f"Discord branding sync failed: {exc}", 1800)
            await message.reply(reply, mention_author=False)
            return {"action": "reply", "reply": reply}
        if summary.get("project_key"):
            await queue_project_task(
                goal=f"Sync Discord blueprint {summary.get('blueprint_key')} for guild {guild.name}",
                project_key=str(summary.get("project_key") or ""),
                entails=json.dumps(
                    {
                        "guild_id": guild.id,
                        "roles": len(summary.get("roles", [])),
                        "categories": len(summary.get("categories", [])),
                        "channels": len(summary.get("channels", [])),
                        "sticky_posts": len(summary.get("pinned_posts", [])),
                    }
                ),
                source="discord_blueprint_sync",
                requested_by=f"discord:{message.author.id}",
            )
        reply = render_discord_blueprint_summary(summary)
        await message.reply(reply, mention_author=False)
        return {"action": "sync_blueprint", "reply": reply, "summary": summary, "plan": asdict(resolved_plan)}

    if action == "create_channel":
        channel_name = safe_channel_name(decision.get("channel_name") or "new-channel")
        category_name = (decision.get("category_name") or "").strip()
        category = discord.utils.get(guild.categories, name=category_name) if category_name else None
        new_channel = await guild.create_text_channel(channel_name, category=category, reason=f"{DISCORD_CONNECTOR_NAME} connector action")
        reply = (decision.get("reply") or f"Created channel #{new_channel.name}.")[:1900]
        await message.reply(reply, mention_author=False)
        return {"action": "create_channel", "channel": new_channel.name, "reply": reply, "plan": asdict(resolved_plan)}

    if action == "set_permissions":
        target_name = (decision.get("target_name") or "").strip()
        target = discord.utils.get(guild.channels, name=target_name) if target_name else message.channel
        if target is None:
            reply = "I could not find the target channel to update permissions."
            await message.reply(reply, mention_author=False)
            return {"action": "reply", "reply": reply}
        for role in permission_role_lookup(guild, decision.get("allow_names") or []):
            await target.set_permissions(role, read_messages=True, send_messages=True, reason=f"{DISCORD_CONNECTOR_NAME} connector action allow")
        for role in permission_role_lookup(guild, decision.get("deny_names") or []):
            await target.set_permissions(role, read_messages=False, send_messages=False, reason=f"{DISCORD_CONNECTOR_NAME} connector action deny")
        reply = (decision.get("reply") or f"Updated permissions for {target.name}.")[:1900]
        await message.reply(reply, mention_author=False)
        return {"action": "set_permissions", "target": target.name, "reply": reply, "plan": asdict(resolved_plan)}

    reply = (decision.get("reply") or "I chose not to take action.")[:1900]
    await message.reply(reply, mention_author=False)
    return {"action": "reply", "reply": reply, "plan": asdict(resolved_plan)}


async def submit_bug(message: discord.Message, title: str, details: str, severity: str = "normal"):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO bug_reports (source, reporter_name, reporter_id, channel_ref, severity, title, details, meta_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            "discord",
            getattr(message.author, "display_name", message.author.name),
            str(message.author.id),
            channel_label(message),
            severity,
            title[:255],
            details,
            json.dumps({"guild": getattr(message.guild, 'name', None), "channel": getattr(message.channel, 'name', 'dm')}),
        ),
    )
    conn.commit()
    bug_id = cur.lastrowid
    cur.close()
    conn.close()
    if DISCORD_BUG_CHANNEL and message.guild:
        bug_channel = None
        if DISCORD_BUG_CHANNEL.isdigit():
            bug_channel = discord.utils.get(message.guild.text_channels, id=int(DISCORD_BUG_CHANNEL))
        if bug_channel is None:
            bug_channel = discord.utils.get(message.guild.text_channels, name=DISCORD_BUG_CHANNEL)
        if bug_channel:
            await bug_channel.send(
                f"[BUG] #{bug_id} | severity={severity}\n**{title}**\n{details[:1700]}"
            )
    return bug_id


@dataclass
class SpeakerBuffer:
    user_id: int
    display_name: str
    pcm: bytearray = field(default_factory=bytearray)
    first_audio_ts: float = 0.0
    last_audio_ts: float = field(default_factory=time.time)
    last_flush_ts: float = 0.0
    bytes_total: int = 0
    packets: int = 0
    transcripts: int = 0
    last_transcript_ts: float = 0.0
    last_transcript: str = ""
    dropped_bytes: int = 0
    processing: bool = False


class GuildVoiceSession:
    def __init__(self, guild, text_channel):
        self.guild = guild
        self.text_channel = text_channel
        self.voice_client = None
        self.buffers = {}
        self.listen_enabled = True
        self.flush_task = None
        self.started_at = time.time()
        self.last_debug_ts = 0.0
        self.received_frames = 0
        self.received_bytes = 0
        self.last_audio_any_ts = 0.0
        self.last_error = ""
        self.last_utterance_reason = ""

    def get_buffer(self, user):
        if user.id not in self.buffers:
            self.buffers[user.id] = SpeakerBuffer(user_id=user.id, display_name=getattr(user, "display_name", user.name))
        return self.buffers[user.id]

    def diagnostics(self):
        now = time.time()
        speakers = []
        for buf in self.buffers.values():
            pending = len(buf.pcm)
            speakers.append(
                {
                    "user_id": buf.user_id,
                    "display_name": buf.display_name,
                    "pending_bytes": pending,
                    "pending_sec_est": round(pending / PCM_BYTES_PER_SEC, 2),
                    "idle_sec": round(now - buf.last_audio_ts, 2),
                    "packets": buf.packets,
                    "bytes_total": buf.bytes_total,
                    "transcripts": buf.transcripts,
                    "last_transcript_age_sec": round(now - buf.last_transcript_ts, 2) if buf.last_transcript_ts else None,
                    "processing": bool(buf.processing),
                    "dropped_bytes": buf.dropped_bytes,
                }
            )
        return {
            "connected": bool(self.voice_client and self.voice_client.is_connected()),
            "listen_enabled": bool(self.listen_enabled),
            "uptime_sec": round(now - self.started_at, 1),
            "received_frames": self.received_frames,
            "received_bytes": self.received_bytes,
            "last_audio_any_age_sec": round(now - self.last_audio_any_ts, 2) if self.last_audio_any_ts else None,
            "last_error": self.last_error or None,
            "last_utterance_reason": self.last_utterance_reason or None,
            "thresholds": {
                "silence_sec": UTTERANCE_SILENCE_SEC,
                "min_pcm_bytes": MIN_PCM_BYTES,
                "max_buffer_sec": MAX_BUFFER_SEC,
                "max_buffer_bytes": MAX_BUFFER_BYTES,
                "min_flush_interval_sec": MIN_FLUSH_INTERVAL_SEC,
            },
            "speakers": speakers,
        }

    async def start_flush_loop(self):
        while self.listen_enabled:
            now = time.time()
            for _, buf in list(self.buffers.items()):
                if buf.processing:
                    continue
                pending = len(buf.pcm)
                if pending <= 0:
                    continue
                idle = now - buf.last_audio_ts
                age = now - buf.first_audio_ts if buf.first_audio_ts > 0 else 0.0
                time_since_flush = now - buf.last_flush_ts if buf.last_flush_ts > 0 else 999.0
                flush_reason = ""
                if pending >= MAX_BUFFER_BYTES:
                    flush_reason = "max_buffer"
                elif idle >= UTTERANCE_SILENCE_SEC and pending >= MIN_PCM_BYTES:
                    flush_reason = "silence"
                elif age >= MAX_BUFFER_SEC and pending >= MIN_PCM_BYTES:
                    flush_reason = "age_timeout"
                elif idle >= (UTTERANCE_SILENCE_SEC * 2.0) and pending >= max(2048, MIN_PCM_BYTES // 3):
                    flush_reason = "short_tail"
                if flush_reason and time_since_flush >= MIN_FLUSH_INTERVAL_SEC:
                    pcm_copy = bytes(buf.pcm)
                    buf.pcm.clear()
                    buf.processing = True
                    buf.first_audio_ts = 0.0
                    buf.last_flush_ts = now
                    self.last_utterance_reason = flush_reason
                    asyncio.create_task(self.process_utterance(buf, pcm_copy, flush_reason, idle))
            if now - self.last_debug_ts >= max(2.0, VOICE_DEBUG_EVERY_SEC):
                active = []
                for buf in self.buffers.values():
                    pending = len(buf.pcm)
                    if pending:
                        active.append(
                            {
                                "user": buf.display_name,
                                "pending_bytes": pending,
                                "pending_sec_est": round(pending / PCM_BYTES_PER_SEC, 2),
                                "idle_sec": round(now - buf.last_audio_ts, 2),
                                "processing": bool(buf.processing),
                            }
                        )
                if active:
                    print(f"[discord-voice] active buffers: {json.dumps(active)[:1200]}")
                self.last_debug_ts = now
            await asyncio.sleep(0.10)

    async def deliver_voice_reply(self, reply: str, speak: bool = True):
        text = compact_discord_text(reply, 1800)
        if not text:
            return
        await self.text_channel.send(f"{olympus_discord_name()}: {text}")
        if speak and DISCORD_VOICE_TTS_REPLY:
            await play_tts_in_voice(self, compact_discord_text(reply, DISCORD_VOICE_REPLY_MAX_CHARS))

    async def finish_background_lookup(self, speaker_name: str, transcript: str):
        done_event = asyncio.Event()
        progress_state = {"sent": 0}
        async def _send_progress(text: str):
            progress_state["sent"] += 1
            await self.deliver_voice_reply(text, speak=DISCORD_VOICE_STATUS_TTS)
        progress_task = asyncio.create_task(
            run_progress_notifier(
                _send_progress,
                done_event,
                [
                    (DISCORD_VOICE_PROGRESS_UPDATE_SEC, build_voice_progress_update(speaker_name)),
                    (DISCORD_VOICE_PROGRESS_UPDATE_SEC * 2.2, "Still working through it. I'll keep the final answer short."),
                ],
            )
        )
        try:
            reply = compact_discord_text((await query_assistant_lookup_deep(transcript, speaker=speaker_name)).strip(), DISCORD_VOICE_REPLY_MAX_CHARS)
            if reply:
                if progress_state["sent"]:
                    reply = compact_discord_text(f"Done. {reply}", DISCORD_VOICE_REPLY_MAX_CHARS)
                await self.deliver_voice_reply(reply, speak=True)
        except Exception as e:
            self.last_error = str(e)
            print(f"[discord-voice] background lookup failed: {e}")
        finally:
            done_event.set()
            if not progress_task.done():
                progress_task.cancel()

    async def process_utterance(self, buf, pcm_bytes, flush_reason: str, idle_sec: float):
        turn_started = time.perf_counter()
        try:
            if len(pcm_bytes) < 2048:
                return
            wav_bytes = pcm_to_wav_bytes(pcm_bytes)
            transcribe_started = time.perf_counter()
            try:
                transcript = await transcribe_wav_bytes(f"{buf.user_id}.wav", wav_bytes)
            except Exception as exc:
                self.last_error = str(exc)
                connection = runtime_connection_summary(
                    surface="voice",
                    project_key=default_project_key_from_guild(self.guild),
                    guild_id=getattr(self.guild, "id", ""),
                    channel_id=getattr(self.text_channel, "id", ""),
                    voice_connected=bool(self.voice_client and self.voice_client.is_connected()),
                    speaker=buf.display_name,
                    request_kind=flush_reason,
                )
                record_runtime_breadcrumb(
                    "transcribe",
                    severity="error",
                    scene="voice",
                    route=flush_reason,
                    connection=connection,
                    speaker=buf.display_name,
                    error=compact_discord_text(str(exc), 220),
                )
                await self.deliver_voice_reply(
                    "I captured the audio, but transcription failed. I left a text status instead.",
                    speak=False,
                )
                await submit_runtime_diagnostic_receipt(
                    {"project_key": default_project_key_from_guild(self.guild), "guild_id": getattr(self.guild, "id", ""), "channel_id": getattr(self.text_channel, "id", ""), "surface": "voice"},
                    stage="transcribe",
                    severity="error",
                    scene="voice",
                    route=flush_reason,
                    reason=str(exc),
                    meta={"speaker": buf.display_name, "request_kind": "voice_transcription", "flush_reason": flush_reason},
                )
                return
            stt_ms = (time.perf_counter() - transcribe_started) * 1000.0
            if not transcript:
                record_runtime_breadcrumb(
                    "transcribe",
                    severity="warn",
                    scene="voice",
                    route=flush_reason,
                    connection=runtime_connection_summary(
                        surface="voice",
                        project_key=default_project_key_from_guild(self.guild),
                        guild_id=getattr(self.guild, "id", ""),
                        channel_id=getattr(self.text_channel, "id", ""),
                        voice_connected=bool(self.voice_client and self.voice_client.is_connected()),
                        speaker=buf.display_name,
                        request_kind=flush_reason,
                    ),
                    speaker=buf.display_name,
                    error="empty transcript",
                )
                print(
                    f"[discord-voice] empty transcript for {buf.display_name} "
                    f"(reason={flush_reason}, bytes={len(pcm_bytes)}, idle={idle_sec:.2f}s)"
                )
                await self.deliver_voice_reply("I heard audio, but I couldn't transcribe it clearly. I left a text status instead.", speak=False)
                return
            self.last_error = ""
            buf.transcripts += 1
            buf.last_transcript_ts = time.time()
            buf.last_transcript = transcript[:500]
            if DISCORD_VOICE_TRANSCRIPT_ECHO:
                await self.text_channel.send(f"Voice {buf.display_name}: {compact_discord_text(transcript, 1500)}")
            print(
                f"[discord-voice] transcribed {buf.display_name} "
                f"(reason={flush_reason}, bytes={len(pcm_bytes)}, idle={idle_sec:.2f}s): {transcript[:160]}"
            )
            voice_author = type(
                "VoiceAuthor",
                (),
                {"id": buf.user_id, "display_name": buf.display_name, "name": buf.display_name},
            )()
            voice_message = type(
                "VoiceMessageProxy",
                (),
                {"content": transcript, "author": voice_author, "guild": self.guild, "channel": self.text_channel},
            )()
            current_policy = await fetch_discord_actor_policy(
                voice_message,
                requested_project_key=default_project_key_from_guild(self.guild),
                command_key="voice",
            )
            voice_review_kind = classify_zeus_review_request(transcript, infer_discord_media_request(transcript))
            async def _send_voice_status(text: str):
                await self.deliver_voice_reply(text, speak=DISCORD_VOICE_STATUS_TTS)
            if voice_review_kind == "suggestion":
                handled, review_result = await handle_zeus_suggestion_turn(
                    voice_message,
                    transcript,
                    current_policy,
                    surface="voice",
                    priority="immediate",
                    trace_meta={
                        "room_label": getattr(self.text_channel, "name", "voice"),
                        "room_kind": "voice",
                        "project_key": str(current_policy.get("project_key") or default_project_key_from_guild(self.guild)),
                        "channel_policy": "voice",
                        "actor_role": str(current_policy.get("actor_role") or "voice"),
                        "authorized_capabilities": current_policy.get("authorized_capabilities") or [],
                        "visibility_mode": str(current_policy.get("visibility_mode") or "private_detail"),
                        "request_source": "discord_voice",
                    },
                    send_reply=_send_voice_status,
                )
                if handled:
                    reply_text = compact_discord_text(str(review_result.get("reply") or ""), DISCORD_VOICE_REPLY_MAX_CHARS)
                    total_ms = (time.perf_counter() - turn_started) * 1000.0
                    print(f"[discord-voice] suggestion routed speaker={buf.display_name} stt_ms={stt_ms:.0f} total_ms={total_ms:.0f} reason={flush_reason}")
                    return
            elif voice_review_kind == "action":
                action_kind = "discord_admin" if (looks_like_discord_admin_request(transcript) or looks_like_message_delete_request(transcript)) else ("deploy" if "deploy" in transcript.lower() or "restart" in transcript.lower() else "implement")
                handled, review_result = await gate_zeus_action_turn(
                    voice_message,
                    transcript,
                    current_policy,
                    surface="voice",
                    priority="immediate",
                    action_kind=action_kind,
                    trace_meta={
                        "room_label": getattr(self.text_channel, "name", "voice"),
                        "room_kind": "voice",
                        "project_key": str(current_policy.get("project_key") or default_project_key_from_guild(self.guild)),
                        "channel_policy": "voice",
                        "actor_role": str(current_policy.get("actor_role") or "voice"),
                        "authorized_capabilities": current_policy.get("authorized_capabilities") or [],
                        "visibility_mode": str(current_policy.get("visibility_mode") or "private_detail"),
                        "request_source": "discord_voice",
                    },
                    send_reply=_send_voice_status,
                )
                if handled:
                    total_ms = (time.perf_counter() - turn_started) * 1000.0
                    print(f"[discord-voice] action blocked speaker={buf.display_name} stt_ms={stt_ms:.0f} total_ms={total_ms:.0f} reason={flush_reason}")
                    return
                await self.deliver_voice_reply("I routed that through Zeus and will keep the project lane updated in text.", speak=DISCORD_VOICE_STATUS_TTS)
                record_runtime_breadcrumb(
                    "dispatch",
                    severity="info",
                    scene="voice",
                    route=action_kind,
                    connection=runtime_connection_summary(
                        surface="voice",
                        project_key=str(current_policy.get("project_key") or default_project_key_from_guild(self.guild)),
                        guild_id=getattr(self.guild, "id", ""),
                        channel_id=getattr(self.text_channel, "id", ""),
                        route_status="approved",
                        voice_connected=bool(self.voice_client and self.voice_client.is_connected()),
                        speaker=buf.display_name,
                    ),
                    speaker=buf.display_name,
                    request_kind="action",
                )
                total_ms = (time.perf_counter() - turn_started) * 1000.0
                print(f"[discord-voice] action routed speaker={buf.display_name} stt_ms={stt_ms:.0f} total_ms={total_ms:.0f} reason={flush_reason}")
                return
            if DISCORD_VOICE_REPLY_ENABLED:
                slow_lookup = DISCORD_VOICE_FAST_ACK_ENABLED and looks_like_slow_lookup_request(transcript)
                if slow_lookup and DISCORD_VOICE_BACKGROUND_LOOKUPS:
                    ack = build_voice_lookup_ack(buf.display_name, transcript)
                    await self.deliver_voice_reply(ack, speak=DISCORD_VOICE_FAST_ACK_TTS)
                    total_ms = (time.perf_counter() - turn_started) * 1000.0
                    print(f"[discord-voice] acked speaker={buf.display_name} stt_ms={stt_ms:.0f} total_ms={total_ms:.0f} reason={flush_reason}")
                    asyncio.create_task(self.finish_background_lookup(buf.display_name, transcript))
                    return
                reply_started = time.perf_counter()
                fast_done = asyncio.Event()
                fast_ack_state = {"sent": 0}
                async def _send_fast_turn_ack(text: str):
                    fast_ack_state["sent"] += 1
                    await self.deliver_voice_reply(text, speak=True)
                fast_ack_task = asyncio.create_task(
                    run_progress_notifier(
                        _send_fast_turn_ack,
                        fast_done,
                        [(1.2, build_voice_turn_ack(buf.display_name))],
                    )
                )
                reply = ""
                try:
                    reply = compact_discord_text(
                        (await query_assistant_fast(
                            transcript,
                            mode="voice",
                            speaker=buf.display_name,
                            timeout_sec=DISCORD_VOICE_FAST_REPLY_TIMEOUT_SEC,
                        )).strip(),
                        DISCORD_VOICE_REPLY_MAX_CHARS,
                    )
                except Exception as fast_error:
                    self.last_error = str(fast_error)
                    print(f"[discord-voice] fast path failed speaker={buf.display_name}: {fast_error}")
                finally:
                    fast_done.set()
                    if not fast_ack_task.done():
                        fast_ack_task.cancel()
                if not reply and DISCORD_VOICE_BACKGROUND_LOOKUPS:
                    ack = build_voice_fallback_ack(buf.display_name)
                    await self.deliver_voice_reply(ack, speak=True)
                    asyncio.create_task(self.finish_background_lookup(buf.display_name, transcript))
                    total_ms = (time.perf_counter() - turn_started) * 1000.0
                    print(
                        f"[discord-voice] fallback lookup speaker={buf.display_name} "
                        f"stt_ms={stt_ms:.0f} total_ms={total_ms:.0f} reason={flush_reason}"
                    )
                    return
                if reply:
                    if fast_ack_state["sent"]:
                        reply = compact_discord_text(f"Done. {reply}", DISCORD_VOICE_REPLY_MAX_CHARS)
                    await self.deliver_voice_reply(reply, speak=True)
                reply_ms = (time.perf_counter() - reply_started) * 1000.0
                total_ms = (time.perf_counter() - turn_started) * 1000.0
                print(
                    f"[discord-voice] replied speaker={buf.display_name} stt_ms={stt_ms:.0f} "
                    f"reply_ms={reply_ms:.0f} total_ms={total_ms:.0f} reason={flush_reason}"
                )
        except Exception as e:
            self.last_error = str(e)
            print(f"[discord-voice] process failed: {e}")
        finally:
            buf.processing = False


class LiveTranscribeSink(voice_recv.AudioSink):
    def __init__(self, session):
        super().__init__()
        self.session = session

    def wants_opus(self):
        return False

    def cleanup(self):
        return None

    def write(self, user, data):
        if user is None or bot.user is None or user.id == bot.user.id:
            return
        pcm = getattr(data, "pcm", None)
        if not pcm:
            return
        now = time.time()
        buf = self.session.get_buffer(user)
        if buf.first_audio_ts <= 0:
            buf.first_audio_ts = now
            record_runtime_breadcrumb(
                "capture",
                severity="info",
                scene="voice",
                route="pcm",
                connection=runtime_connection_summary(
                    surface="voice",
                    project_key=default_project_key_from_guild(self.session.guild),
                    guild_id=getattr(self.session.guild, "id", ""),
                    channel_id=getattr(self.session.text_channel, "id", ""),
                    voice_connected=bool(self.session.voice_client and self.session.voice_client.is_connected()),
                    speaker=getattr(user, "display_name", getattr(user, "name", "user")),
                ),
                speaker=getattr(user, "display_name", getattr(user, "name", "user")),
            )
        buf.pcm.extend(pcm)
        if len(buf.pcm) > MAX_BUFFER_BYTES * 2:
            overflow = len(buf.pcm) - (MAX_BUFFER_BYTES * 2)
            if overflow > 0:
                del buf.pcm[:overflow]
                buf.dropped_bytes += overflow
        buf.packets += 1
        buf.bytes_total += len(pcm)
        buf.last_audio_ts = now
        self.session.received_frames += 1
        self.session.received_bytes += len(pcm)
        self.session.last_audio_any_ts = now


def message_mentions_hermes(message: discord.Message) -> bool:
    content = (message.content or "").lower()
    if bot.user and bot.user in message.mentions:
        return True
    return any(alias in content for alias in TEXT_NAME_ALIASES)


def message_is_reply_to_bot(message: discord.Message) -> bool:
    ref = message.reference
    if not ref or not bot.user:
        return False
    resolved = getattr(ref, "resolved", None)
    if resolved and getattr(resolved, "author", None):
        return resolved.author.id == bot.user.id
    return False


def message_seems_conversational(message: discord.Message) -> bool:
    content = (message.content or "").strip().lower()
    if not content or len(content) > 500:
        return False
    if content.endswith("?"):
        return True
    lead_ins = [
        "what do you think", "can you", "could you", "should i", "why ", "how ", "okay", "ok ",
        "lol", "haha", "that makes sense", "tell me", "help me", "i think", "i feel", "maybe",
        "let's", "lets ", "we should", "can we", "would you"
    ]
    return any(content.startswith(prefix) or prefix in content for prefix in lead_ins)


def message_is_ambient_candidate(message: discord.Message) -> bool:
    content = (message.content or "").strip().lower()
    if not content or len(content) < 4 or len(content) > 400:
        return False
    if content.startswith(("http://", "https://")):
        return False
    if content.count("\n") > 8:
        return False
    if is_dm(message) and DISCORD_AMBIENT_DMS:
        return True
    if message_seems_conversational(message):
        return True
    ambient_markers = [
        "anybody know", "anyone know", "thoughts", "opinion", "idea", "question", "stuck",
        "working on", "trying to", "need help", "does this", "is this", "will this"
    ]
    return any(marker in content for marker in ambient_markers)


def channel_allowed(message: discord.Message) -> bool:
    if is_dm(message):
        return DISCORD_AMBIENT_DMS
    configured_mode = _channel_mode_lookup(message)
    if configured_mode in {"ambient_allowed", "project_control", "ops_only", "mention_only"}:
        return True
    if not TEXT_CHANNEL_ALLOWLIST:
        return True
    channel_id = str(getattr(message.channel, 'id', ''))
    channel_name = getattr(message.channel, 'name', '') or ''
    return channel_id in TEXT_CHANNEL_ALLOWLIST or channel_name in TEXT_CHANNEL_ALLOWLIST


async def delete_recent_hermes_messages(channel: discord.abc.Messageable, limit: int = 20) -> int:
    target = max(1, min(int(limit or 20), 100))
    deleted = 0
    async for row in channel.history(limit=max(30, target * 4)):
        author = getattr(row, "author", None)
        if not author or not bot.user or getattr(author, "id", 0) != bot.user.id:
            continue
        try:
            await row.delete()
            deleted += 1
        except Exception:
            continue
        if deleted >= target:
            break
    return deleted


async def purge_recent_channel_messages(channel: discord.TextChannel, limit: int = 20, hermes_only: bool = False) -> int:
    target = max(1, min(int(limit or 20), 100))
    if hermes_only:
        return await delete_recent_hermes_messages(channel, limit=target)
    deleted_rows = await channel.purge(limit=target, reason="Hermes authorized purge")
    return len(deleted_rows or [])


async def handle_message_delete_request(message: discord.Message, policy: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    current_policy = policy if isinstance(policy, dict) else {}
    confirmed, count, hermes_only = parse_purge_confirmation(message.content or "")
    if is_dm(message):
        if confirmed:
            deleted = await delete_recent_hermes_messages(message.channel, limit=count)
            reply = f"Deleted {deleted} recent Hermes messages from this DM." if deleted else "I did not find any recent Hermes messages to delete in this DM."
            await message.reply(reply, mention_author=False)
            return {"action": "reply", "reply": reply}
        reply = "I cannot erase your side of a DM. I can delete my recent messages here if you want. Say `confirm purge 20 hermes` or run `!purgehermes 20`."
        await message.reply(reply, mention_author=False)
        return {"action": "reply", "reply": reply}
    if not (current_policy.get("can_command") or current_policy.get("server_operator") or is_owner_member(message.author)):
        reply = build_access_denied_reply(str(current_policy.get("project_key") or ""))
        await message.reply(reply, mention_author=False)
        await log_discord_denial(current_policy, command_key="purge_messages", reason=reply, goal=(message.content or "").strip(), meta={"surface": current_policy.get("surface") or "public_channel"})
        return {"action": "reply", "reply": reply}
    me = message.guild.me if message.guild else None
    if me is None or not getattr(getattr(me, "guild_permissions", None), "manage_messages", False):
        reply = "I do not have Manage Messages permission here, so I cannot purge this channel."
        await message.reply(reply, mention_author=False)
        return {"action": "reply", "reply": reply}
    if not confirmed:
        reply = "I can purge this channel, but destructive cleanup needs an explicit confirmation. Reply `confirm purge 20` or use `!purge 20`. Add `hermes` to limit it to my own messages."
        await message.reply(reply, mention_author=False)
        return {"action": "reply", "reply": reply}
    deleted = await purge_recent_channel_messages(message.channel, limit=count, hermes_only=hermes_only)
    scope_label = "Hermes messages" if hermes_only else "messages"
    reply = f"Purged {deleted} recent {scope_label} from this channel."
    await message.reply(reply, mention_author=False)
    return {"action": "reply", "reply": reply}


async def _legacy_maybe_reply_to_text(message: discord.Message, policy: Optional[dict[str, Any]] = None):
    if message.author.bot or not bot.user:
        return
    content = (message.content or "").strip()
    if not content:
        return
    if content.startswith(bot.command_prefix):
        print(f"[discord] skip command-like message in {channel_label(message)}")
        return
    if not channel_allowed(message):
        print(f"[discord] skip channel not allowlisted: {channel_label(message)}")
        return

    current_policy = policy if isinstance(policy, dict) and policy else getattr(message, "_luminax_policy", {})
    if is_dm(message):
        paired = await handle_dm_pairing(message, policy=current_policy)
        if paired:
            return

    state = channel_state(message.channel.id if not is_dm(message) else f"dm:{message.author.id}")
    now = time.time()
    mode = guild_mode(message.guild)
    immediate = message_directed_to_hermes(message) or is_dm(message)
    contextual = state["engaged_until"] > now and message_seems_conversational(message)
    ambient = is_dm(message) and message_is_ambient_candidate(message)
    surface = str(current_policy.get("surface") or ("dm" if is_dm(message) else "public_channel")).strip()
    project_action_trigger = (
        surface == "project_channel"
        and bool(current_policy.get("can_command") or current_policy.get("server_operator") or is_owner_member(message.author))
        and (looks_like_operational_change_request(content) or looks_like_discord_admin_request(content))
    )

    if surface == "public_channel" and not immediate:
        return
    if surface == "project_channel" and not (immediate or contextual or project_action_trigger):
        return
    if mode == "silent" and not immediate:
        return
    if mode == "focus" and not (immediate or contextual or project_action_trigger):
        return
    if not immediate and not contextual and not ambient and not project_action_trigger:
        print(f"[discord] skip no trigger: {content[:120]}")
        return

    min_interval = TEXT_REPLY_MIN_INTERVAL_SEC if (immediate or contextual) else TEXT_AMBIENT_MIN_INTERVAL_SEC
    if now - state["last_reply_ts"] < min_interval:
        print(f"[discord] skip cooldown active for {channel_label(message)}")
        return

    speaker = getattr(message.author, "display_name", getattr(message.author, "name", "user"))
    trace_meta = policy_channel_meta(message, current_policy)
    priority = "immediate" if immediate else ("contextual" if contextual else "ambient")
    started = time.perf_counter()
    fast_ack_sent = False
    path = "unknown"
    result = {"action": "skip", "reply": ""}
    try:
        fast_ack_sent = await maybe_send_fast_ack(message, state, priority)
    except Exception as e:
        print(f"[discord] fast ack failed: {e}")

    try:
        if looks_like_discord_admin_request(content):
            path = "planner"
            decision = await decide_discord_action(message, priority)
            if fast_ack_sent:
                decision["_skip_typing"] = True
            result = await execute_discord_action(message, decision)
        elif surface != "dm":
            if current_policy.get("project_key") and not current_policy.get("can_view") and not is_owner_member(message.author):
                reply = build_access_denied_reply(str(current_policy.get("project_key") or ""))
                await message.reply(reply, mention_author=False)
                await log_discord_denial(current_policy, command_key="project_lookup", reason=reply, goal=content, meta={"surface": surface})
                result = {"action": "reply", "reply": reply}
            elif looks_like_operational_change_request(content) and not (current_policy.get("can_command") or current_policy.get("server_operator") or is_owner_member(message.author)):
                reply = build_access_denied_reply(str(current_policy.get("project_key") or ""))
                await message.reply(reply, mention_author=False)
                await log_discord_denial(current_policy, command_key="codex", reason=reply, goal=content, meta={"surface": surface})
                result = {"action": "reply", "reply": reply}
            else:
                job_id = f"discord_job_{uuid.uuid4().hex[:12]}"
                public_ack = _public_status_text("Looking now.") if surface == "public_channel" else "Looking now."
                if looks_like_operational_change_request(content):
                    path = "codex_background"
                    if not fast_ack_sent:
                        await message.reply(public_ack, mention_author=False)
                    await upsert_discord_job_core(current_policy, job_id=job_id, status="running", public_summary="Running task", command_key="codex", meta={"surface": surface, "scope": "implement", "allow_write": bool(current_policy.get("can_write"))})
                    asyncio.create_task(run_codex_job_and_report(message.channel, message.author, message.guild, content, scope="implement", allow_write=bool(current_policy.get("can_write")), policy=current_policy, job_id=job_id))
                    result = {"action": "codex_background", "reply": public_ack}
                else:
                    path = "dm_background"
                    if not fast_ack_sent:
                        await message.reply(public_ack, mention_author=False)
                    request_kind = "lookup" if looks_like_slow_lookup_request(content) else "conversation"
                    await upsert_discord_job_core(current_policy, job_id=job_id, status="running", public_summary="Working", command_key=f"text_{request_kind}", meta={"surface": surface, "request_kind": request_kind})
                    asyncio.create_task(finish_text_background_lookup(message.channel, message.author, speaker, content, request_kind=request_kind, channel_meta=trace_meta, policy=current_policy, job_id=job_id))
                    result = {"action": "background_lookup", "reply": public_ack}
        else:
            if looks_like_operational_change_request(content) and not (current_policy.get("can_command") or current_policy.get("server_operator") or is_owner_member(message.author)):
                reply = build_operator_access_reply(current_policy, surface=surface)
                await message.reply(compact_discord_text(reply, 1800), mention_author=False)
                await log_discord_denial(current_policy, command_key="codex", reason=reply, goal=content, meta={"surface": surface})
                result = {"action": "reply", "reply": reply}
            elif looks_like_operational_change_request(content) and (current_policy.get("can_command") or current_policy.get("server_operator") or is_owner_member(message.author)):
                path = "codex_dm"
                reply = build_codex_dispatch_ack(content)
                await message.reply(reply, mention_author=False)
                asyncio.create_task(run_codex_job_and_report(message.channel, message.author, message.guild, content, scope="implement", allow_write=bool(current_policy.get("can_write") or is_owner_member(message.author)), policy=current_policy, job_id=f"discord_job_{uuid.uuid4().hex[:12]}"))
                result = {"action": "codex_background", "reply": reply}
            elif looks_like_slow_lookup_request(content):
                if DISCORD_TEXT_BACKGROUND_LOOKUPS:
                    path = "lookup_background"
                    if not fast_ack_sent:
                        reply = build_fast_lookup_ack(message)
                        await message.reply(reply, mention_author=False)
                    else:
                        reply = state.get("last_bot_reply", "")
                    asyncio.create_task(finish_text_background_lookup(message.channel, message.author, speaker, content, request_kind="lookup", channel_meta=trace_meta, policy=current_policy, job_id=f"discord_job_{uuid.uuid4().hex[:12]}"))
                    result = {"action": "background_lookup", "reply": reply}
                else:
                    path = "lookup"
                    reply = compact_discord_text((await query_assistant_lookup_deep(content, speaker=speaker, channel_meta=trace_meta)).strip(), 1900) or "I'm here."
                    await message.reply(reply, mention_author=False)
                    result = {"action": "reply", "reply": reply}
            else:
                path = "conversation"
                try:
                    reply = compact_discord_text((await query_assistant_fast(content, mode="conversation", speaker=speaker, channel_meta=trace_meta)).strip(), 1900)
                except Exception as fast_error:
                    print(f"[discord] fast conversation failed: {fast_error}")
                    reply = ""
                if not reply:
                    path = "conversation_background"
                    reply = "I heard you. I'm working through that now and I'll follow up here."
                    await message.reply(reply, mention_author=False)
                    asyncio.create_task(finish_text_background_lookup(message.channel, message.author, speaker, content, request_kind="conversation", channel_meta=trace_meta, policy=current_policy, job_id=f"discord_job_{uuid.uuid4().hex[:12]}"))
                    result = {"action": "background_lookup", "reply": reply}
                else:
                    await message.reply(reply, mention_author=False)
                    result = {"action": "reply", "reply": reply}
    except Exception as e:
        print(f"[discord] reply path failed: {e}")
        return

    state["last_reply_ts"] = now
    state["last_user_id"] = message.author.id
    state["last_topic"] = content[:300]
    state["last_bot_reply"] = (result.get("reply") or "")[:500]
    state["engaged_until"] = now + TEXT_CONTEXT_WINDOW_SEC
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    print(f"[discord] replied/action={result.get('action')} priority={priority} path={path} ms={elapsed_ms:.0f} in {channel_label(message)}")


async def maybe_reply_to_text(message: discord.Message, policy: Optional[dict[str, Any]] = None):
    if message.author.bot or not bot.user:
        return
    content = (message.content or "").strip()
    if not content:
        return
    summary_mode = infer_summary_mode_from_text(content)
    if content.startswith(bot.command_prefix):
        print(f"[discord] skip command-like message in {channel_label(message)}")
        return
    if not channel_allowed(message):
        print(f"[discord] skip channel not allowlisted: {channel_label(message)}")
        return

    current_policy = policy if isinstance(policy, dict) and policy else _cached_message_policy(message)
    if isinstance(current_policy, dict):
        current_policy = dict(current_policy)
        current_policy["summary_mode"] = summary_mode
    if is_dm(message):
        paired = await handle_dm_pairing(message, policy=current_policy)
        if paired:
            return

    state = channel_state(message.channel.id if not is_dm(message) else f"dm:{message.author.id}")
    now = time.time()
    mode = guild_mode(message.guild)
    immediate = message_directed_to_hermes(message) or is_dm(message)
    channel_mode = channel_mode_for_message(message, current_policy)
    contextual = state["engaged_until"] > now and message_seems_conversational(message)
    ambient_candidate = message_is_ambient_candidate(message)
    ambient = bool(is_dm(message) and ambient_candidate) or bool(channel_mode == "ambient_allowed" and ambient_candidate)
    surface = str(current_policy.get("surface") or ("dm" if is_dm(message) else "public_channel")).strip()
    authorized_operator = bool(current_policy.get("can_command") or current_policy.get("server_operator") or is_owner_member(message.author))
    media_request_preview = infer_discord_media_request(content)
    media_allowed_preview = bool(media_request_preview) and discord_media_access_allowed(current_policy, message.author, surface=surface)
    operational_request = looks_like_operational_change_request(content) or (
        looks_like_contextual_followthrough_request(content)
        and bool(
            current_policy.get("project_key")
            or current_policy.get("binding")
            or surface == "project_channel"
        )
    )
    project_action_trigger = (
        surface == "project_channel"
        and (authorized_operator or media_allowed_preview)
        and (
            operational_request
            or looks_like_discord_admin_request(content)
            or looks_like_message_delete_request(content)
            or bool(media_request_preview)
        )
    )

    if channel_mode == "ops_only" and not authorized_operator:
        return
    if surface == "public_channel" and channel_mode == "mention_only" and not immediate:
        return
    if surface == "project_channel" and channel_mode == "project_control" and not (immediate or contextual or project_action_trigger):
        return
    if mode == "silent" and not immediate and not project_action_trigger:
        return
    if mode == "focus" and not (immediate or contextual or project_action_trigger):
        return
    if not immediate and not contextual and not ambient and not project_action_trigger and channel_mode != "ambient_allowed":
        print(f"[discord] skip no trigger: {content[:120]}")
        return

    min_interval = TEXT_REPLY_MIN_INTERVAL_SEC if (immediate or contextual or project_action_trigger) else TEXT_AMBIENT_MIN_INTERVAL_SEC
    if now - state["last_reply_ts"] < min_interval:
        print(f"[discord] skip cooldown active for {channel_label(message)}")
        return

    speaker = getattr(message.author, "display_name", getattr(message.author, "name", "user"))
    trace_meta = policy_channel_meta(message, current_policy)
    trace_meta["summary_mode"] = summary_mode
    media_request = media_request_preview
    media_allowed = media_allowed_preview
    priority = "immediate" if immediate else ("project_action" if project_action_trigger else "ambient")
    async def _reply_text(text: str) -> None:
        await message.reply(compact_discord_text(text, 1800), mention_author=False)
    if await handle_severe_moderation(message, current_policy, surface=surface):
        state["last_reply_ts"] = now
        state["last_user_id"] = message.author.id
        state["last_topic"] = "[moderated]"
        state["last_bot_reply"] = ""
        state["engaged_until"] = now + TEXT_CONTEXT_WINDOW_SEC
        return
    handled_community, community_result = await maybe_handle_public_community_feedback_turn(
        message,
        content,
        current_policy,
        surface=surface,
        priority=priority,
        trace_meta=trace_meta,
        send_reply=_reply_text,
    )
    if handled_community:
        reply_text = compact_discord_text(str(community_result.get("reply") or ""), 500)
        state["last_reply_ts"] = now
        state["last_user_id"] = message.author.id
        state["last_topic"] = content[:300]
        state["last_bot_reply"] = reply_text[:500]
        state["engaged_until"] = now + TEXT_CONTEXT_WINDOW_SEC
        return
    review_kind = classify_zeus_review_request(content, media_request)
    if review_kind == "suggestion":
        handled, review_result = await handle_zeus_suggestion_turn(
            message,
            content,
            current_policy,
            surface=surface,
            priority=priority,
            trace_meta=trace_meta,
            send_reply=_reply_text,
        )
        if handled:
            reply_text = compact_discord_text(str(review_result.get("reply") or ""), 500)
            state["last_reply_ts"] = now
            state["last_user_id"] = message.author.id
            state["last_topic"] = content[:300]
            state["last_bot_reply"] = reply_text[:500]
            state["engaged_until"] = now + TEXT_CONTEXT_WINDOW_SEC
            return
    elif review_kind == "action":
        action_kind = "discord_admin" if (looks_like_discord_admin_request(content) or looks_like_message_delete_request(content)) else ("deploy" if "deploy" in content.lower() or "restart" in content.lower() else "implement")
        handled, review_result = await gate_zeus_action_turn(
            message,
            content,
            current_policy,
            surface=surface,
            priority=priority,
            action_kind=action_kind,
            trace_meta=trace_meta,
            send_reply=_reply_text,
        )
        if handled:
            reply_text = compact_discord_text(str(review_result.get("reply") or ""), 500)
            state["last_reply_ts"] = now
            state["last_user_id"] = message.author.id
            state["last_topic"] = content[:300]
            state["last_bot_reply"] = reply_text[:500]
            state["engaged_until"] = now + TEXT_CONTEXT_WINDOW_SEC
            return
    if DISCORD_PERSONA_MODE == "zeus":
        if (message_directed_to_hermes(message) or is_dm(message) or message_is_reply_to_bot(message)) and not bool(current_policy.get("council_allowed") or is_owner_member(message.author)):
            reply = build_owner_access_denied_reply(normalize_project_key_local(current_policy.get("project_key") or default_project_key_from_guild(message.guild)))
            await _reply_text(reply)
            await log_discord_denial(current_policy, command_key="zeus_presence", reason=reply, goal=content, meta={"surface": surface, "priority": priority, "stage": "council_gate"})
            state["last_reply_ts"] = now
            state["last_user_id"] = message.author.id
            state["last_topic"] = content[:300]
            state["last_bot_reply"] = reply[:500]
            state["engaged_until"] = now + TEXT_CONTEXT_WINDOW_SEC
            return
    started = time.perf_counter()
    path = "unknown"
    result = {"action": "skip", "reply": ""}
    intent = build_hermes_intent(message, content, current_policy, priority=priority, media_request=media_request)
    route = build_hermes_route_decision(intent, current_policy)
    zeus_plan: Optional[HermesActionPlan] = None
    if intent.intent_kind in {"discord_admin", "project_write", "media"}:
        zeus_plan = await build_zeus_execution_plan(intent, route, current_policy)
        route = apply_zeus_plan_to_route(intent, route, zeus_plan)
        if route.resolved_project_key and route.resolved_project_key != normalize_project_key_local(current_policy.get("project_key") or ""):
            current_policy = dict(current_policy)
            current_policy["project_key"] = route.resolved_project_key
        if zeus_plan.requires_confirmation:
            reply = compact_discord_text(
                zeus_plan.public_ack or "Zeus wants explicit confirmation before Hermes changes that live surface.",
                1800,
            )
            await message.reply(reply, mention_author=False)
            verification = HermesVerificationResult(status="needs_confirmation", summary="Zeus requested explicit confirmation before execution.")
            await upsert_hermes_job(
                current_policy,
                intent=intent,
                route=HermesRouteDecision(**{**asdict(route), "route_status": "confirmation_required"}),
                status="needs_confirmation",
                public_summary="Awaiting confirmation",
                private_summary=reply,
                dm_status="needs_confirmation",
                detail_text=reply,
                verification=verification,
                command_key=route.executor_kind or intent.action_kind,
                extra_meta={"path": "zeus_confirmation_gate", "zeus_plan": asdict(zeus_plan)},
            )
            state["last_reply_ts"] = now
            state["last_user_id"] = message.author.id
            state["last_topic"] = content[:300]
            state["last_bot_reply"] = reply[:500]
            state["engaged_until"] = now + TEXT_CONTEXT_WINDOW_SEC
            return
    try:
        if intent.intent_kind == "discord_admin" and intent.action_kind == "safe_cleanup":
            path = "message_delete"
            result = await handle_message_delete_request(message, policy=current_policy)
            verification = HermesVerificationResult(status="verified", summary=str(result.get("reply") or "").strip())
            await upsert_hermes_job(
                current_policy,
                intent=intent,
                route=route,
                status="completed",
                public_summary="Discord cleanup completed",
                private_summary=str(result.get("reply") or "").strip(),
                dm_status="not_needed",
                detail_text=str(result.get("reply") or "").strip(),
                verification=verification,
                command_key="discord_admin",
                extra_meta={"path": path, "zeus_plan": asdict(zeus_plan) if zeus_plan else {}},
            )
        elif intent.intent_kind == "discord_admin":
            path = "planner"
            decision = await decide_discord_action(message, priority)
            result = await execute_discord_action(message, decision, policy=current_policy, intent=intent, route=route, plan=zeus_plan)
            verification_data = result.get("verification") if isinstance(result.get("verification"), dict) else {}
            verification = HermesVerificationResult(
                status=str(verification_data.get("status") or ("verified" if result.get("action") != "reply" else "pending")).strip(),
                summary=str(verification_data.get("summary") or result.get("reply") or "").strip(),
                details=str(verification_data.get("details") or "").strip(),
                artifact_paths=[str(item).strip() for item in (verification_data.get("artifact_paths") or []) if str(item).strip()],
                touched_targets=[str(item).strip() for item in (verification_data.get("touched_targets") or []) if str(item).strip()],
            )
            final_status = "completed" if verification.status == "verified" else ("needs_confirmation" if verification.status == "needs_confirmation" else "failed")
            public_summary = "Discord admin completed" if verification.status == "verified" else ("Awaiting confirmation" if verification.status == "needs_confirmation" else "Discord admin needs attention")
            await upsert_hermes_job(
                current_policy,
                intent=intent,
                route=route,
                status=final_status,
                public_summary=public_summary,
                private_summary=str(result.get("reply") or "").strip(),
                dm_status="needs_confirmation" if verification.status == "needs_confirmation" else "not_needed",
                detail_text=str(result.get("reply") or "").strip(),
                verification=verification,
                command_key="discord_admin",
                extra_meta={"path": path, "decision": decision, "summary": result.get("summary", {}), "zeus_plan": asdict(zeus_plan) if zeus_plan else result.get("plan", {})},
            )
        elif intent.intent_kind == "media" and not media_allowed:
            reply = build_media_access_reply(current_policy, surface=surface)
            await message.reply(compact_discord_text(reply, 1800), mention_author=False)
            await log_discord_denial(current_policy, command_key="media_job", reason=reply, goal=content, meta={"surface": surface, "media_kind": media_request.get("kind")})
            result = {"action": "reply", "reply": reply}
        elif intent.intent_kind == "media":
            path = f"media_{media_request.get('kind', 'image')}"
            await upsert_hermes_job(
                current_policy,
                intent=intent,
                route=route,
                status="running",
                public_summary="Generating media",
                verification=HermesVerificationResult(status="pending", summary="Media job queued."),
                command_key="media_job",
                extra_meta={
                    "surface": surface,
                    "media_kind": media_request.get("kind"),
                    "style_key": str(((media_request.get("settings") or {}).get("style_key") or "")).strip(),
                    "model_key": str(media_request.get("model_key") or "").strip(),
                    "zeus_plan": asdict(zeus_plan) if zeus_plan else {},
                },
            )
            asyncio.create_task(
                run_media_job_and_report(
                    message.channel,
                    message.author,
                    str(media_request.get("prompt") or "").strip() or content,
                    media_kind=str(media_request.get("kind") or "image").strip(),
                    settings=media_request.get("settings") if isinstance(media_request.get("settings"), dict) else {},
                    output_target=str(media_request.get("output_target") or "").strip(),
                    model_key=str(media_request.get("model_key") or "").strip(),
                    policy=current_policy,
                    job_id=intent.job_id,
                    plan=zeus_plan,
                )
            )
            result = {"action": "media_background", "reply": ""}
        elif surface != "dm":
            if current_policy.get("project_key") and not current_policy.get("can_view") and not is_owner_member(message.author):
                reply = build_access_denied_reply(str(current_policy.get("project_key") or ""))
                await message.reply(reply, mention_author=False)
                await log_discord_denial(current_policy, command_key="project_lookup", reason=reply, goal=content, meta={"surface": surface})
                result = {"action": "reply", "reply": reply}
            elif operational_request and not authorized_operator:
                reply = build_access_denied_reply(str(current_policy.get("project_key") or ""))
                await message.reply(reply, mention_author=False)
                await log_discord_denial(current_policy, command_key="codex", reason=reply, goal=content, meta={"surface": surface})
                result = {"action": "reply", "reply": reply}
            elif intent.intent_kind == "project_write":
                path = route.executor_kind
                accepted_reply = compact_discord_text(
                    (zeus_plan.public_ack if zeus_plan else "") or build_codex_dispatch_ack(content),
                    1800,
                )
                await message.reply(accepted_reply, mention_author=False)
                await upsert_hermes_job(
                    current_policy,
                    intent=intent,
                    route=route,
                    status="running",
                    public_summary="Running task",
                    verification=HermesVerificationResult(status="pending", summary="Task accepted by Hermes."),
                    command_key=route.executor_kind or "project_write",
                    extra_meta={"surface": surface, "scope": "implement", "allow_write": bool(current_policy.get("can_write")), "zeus_plan": asdict(zeus_plan) if zeus_plan else {}},
                )
                asyncio.create_task(
                    run_codex_job_and_report(
                        message.channel,
                        message.author,
                        message.guild,
                        content,
                        scope="implement",
                        allow_write=bool(current_policy.get("can_write")),
                        policy=current_policy,
                        job_id=intent.job_id,
                        attachments=intent.attachments,
                        intent=intent,
                        route_decision=route,
                        plan=zeus_plan,
                    )
                )
                result = {"action": "project_background", "reply": accepted_reply}
            elif intent.intent_kind == "lookup":
                path = "lookup_background"
                await upsert_hermes_job(
                    current_policy,
                    intent=intent,
                    route=route,
                    status="running",
                    public_summary="Working",
                    verification=HermesVerificationResult(status="pending", summary="Lookup running."),
                    command_key="text_lookup",
                    extra_meta={"surface": surface, "request_kind": "lookup"},
                )
                asyncio.create_task(finish_text_background_lookup(message.channel, message.author, speaker, content, request_kind="lookup", channel_meta=trace_meta, policy=current_policy, job_id=intent.job_id))
                result = {"action": "background_lookup", "reply": ""}
            else:
                path = "conversation"
                try:
                    reply = compact_discord_text((await query_assistant_fast(content, mode="conversation", speaker=speaker, channel_meta=trace_meta, timeout_sec=DISCORD_RESPONSE_HOLD_SEC)).strip(), 1900)
                except Exception as fast_error:
                    print(f"[discord] fast conversation failed: {fast_error}")
                    reply = ""
                if reply:
                    await message.reply(reply, mention_author=False)
                    result = {"action": "reply", "reply": reply}
                elif DISCORD_TEXT_BACKGROUND_LOOKUPS:
                    path = "conversation_background"
                    await upsert_hermes_job(
                        current_policy,
                        intent=intent,
                        route=route,
                        status="running",
                        public_summary="Working",
                        verification=HermesVerificationResult(status="pending", summary="Conversation lookup running."),
                        command_key="text_conversation",
                        extra_meta={"surface": surface, "request_kind": "conversation"},
                    )
                    asyncio.create_task(finish_text_background_lookup(message.channel, message.author, speaker, content, request_kind="conversation", channel_meta=trace_meta, policy=current_policy, job_id=intent.job_id))
                    result = {"action": "background_lookup", "reply": ""}
                else:
                    reply = "I could not produce a clean reply right now."
                    await message.reply(reply, mention_author=False)
                    result = {"action": "reply", "reply": reply}
        else:
            if operational_request and not authorized_operator:
                reply = build_operator_access_reply(current_policy, surface=surface)
                await message.reply(compact_discord_text(reply, 1800), mention_author=False)
                await log_discord_denial(current_policy, command_key="codex", reason=reply, goal=content, meta={"surface": surface})
                result = {"action": "reply", "reply": reply}
            elif operational_request:
                path = route.executor_kind
                accepted_reply = compact_discord_text(
                    (zeus_plan.public_ack if zeus_plan else "") or build_codex_dispatch_ack(content),
                    1800,
                )
                await message.reply(accepted_reply, mention_author=False)
                await upsert_hermes_job(
                    current_policy,
                    intent=intent,
                    route=route,
                    status="running",
                    public_summary="Running task",
                    verification=HermesVerificationResult(status="pending", summary="Task accepted by Hermes."),
                    command_key=route.executor_kind or "project_write",
                    extra_meta={"surface": surface, "scope": "implement", "allow_write": bool(current_policy.get("can_write") or is_owner_member(message.author)), "zeus_plan": asdict(zeus_plan) if zeus_plan else {}},
                )
                asyncio.create_task(
                    run_codex_job_and_report(
                        message.channel,
                        message.author,
                        message.guild,
                        content,
                        scope="implement",
                        allow_write=bool(current_policy.get("can_write") or is_owner_member(message.author)),
                        policy=current_policy,
                        job_id=intent.job_id,
                        attachments=intent.attachments,
                        intent=intent,
                        route_decision=route,
                        plan=zeus_plan,
                    )
                )
                result = {"action": "project_background", "reply": accepted_reply}
            elif intent.intent_kind == "lookup":
                if DISCORD_TEXT_BACKGROUND_LOOKUPS:
                    path = "lookup_background"
                    await upsert_hermes_job(
                        current_policy,
                        intent=intent,
                        route=route,
                        status="running",
                        public_summary="Working",
                        verification=HermesVerificationResult(status="pending", summary="Lookup running."),
                        command_key="text_lookup",
                        extra_meta={"surface": surface, "request_kind": "lookup"},
                    )
                    asyncio.create_task(finish_text_background_lookup(message.channel, message.author, speaker, content, request_kind="lookup", channel_meta=trace_meta, policy=current_policy, job_id=intent.job_id))
                    result = {"action": "background_lookup", "reply": ""}
                else:
                    path = "lookup"
                    reply = compact_discord_text((await query_assistant_lookup_deep(content, speaker=speaker, channel_meta=trace_meta)).strip(), 1900) or "I'm here."
                    await message.reply(reply, mention_author=False)
                    result = {"action": "reply", "reply": reply}
            else:
                path = "conversation"
                try:
                    reply = compact_discord_text((await query_assistant_fast(content, mode="conversation", speaker=speaker, channel_meta=trace_meta, timeout_sec=DISCORD_RESPONSE_HOLD_SEC)).strip(), 1900)
                except Exception as fast_error:
                    print(f"[discord] fast conversation failed: {fast_error}")
                    reply = ""
                if reply:
                    await message.reply(reply, mention_author=False)
                    result = {"action": "reply", "reply": reply}
                elif DISCORD_TEXT_BACKGROUND_LOOKUPS:
                    path = "conversation_background"
                    await upsert_hermes_job(
                        current_policy,
                        intent=intent,
                        route=route,
                        status="running",
                        public_summary="Working",
                        verification=HermesVerificationResult(status="pending", summary="Conversation lookup running."),
                        command_key="text_conversation",
                        extra_meta={"surface": surface, "request_kind": "conversation"},
                    )
                    asyncio.create_task(finish_text_background_lookup(message.channel, message.author, speaker, content, request_kind="conversation", channel_meta=trace_meta, policy=current_policy, job_id=intent.job_id))
                    result = {"action": "background_lookup", "reply": ""}
                else:
                    reply = "I'm still working through that now."
                    await message.reply(reply, mention_author=False)
                    result = {"action": "reply", "reply": reply}
    except Exception as e:
        print(f"[discord] reply path failed: {e}")
        return

    state["last_reply_ts"] = now
    state["last_user_id"] = message.author.id
    state["last_topic"] = content[:300]
    state["last_bot_reply"] = (result.get("reply") or "")[:500]
    state["engaged_until"] = now + TEXT_CONTEXT_WINDOW_SEC
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    print(f"[discord] replied/action={result.get('action')} priority={priority} path={path} mode={channel_mode} ms={elapsed_ms:.0f} in {channel_label(message)}")


async def build_project_plan(topic: str) -> dict:
    prompt = (
        "Design a Discord server structure for this project. Return strict JSON with keys: summary, "
        "categories (array of {name, channels}). Keep channels practical.\n"
        f"Project: {topic}"
    )
    raw = await query_assistant(prompt)
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end + 1])
            if isinstance(parsed, dict) and isinstance(parsed.get("categories"), list):
                return parsed
    except Exception:
        pass
    return {
        "summary": "Fallback Discord structure plan.",
        "categories": [
            {"name": "Project Light", "channels": ["welcome", "announcements", "roadmap", "build-log"]},
            {"name": "Engineering", "channels": ["backend", "frontend", "infra", "qa"]},
            {"name": "Operations", "channels": ["incidents", "deployments", "telemetry"]},
        ],
    }


async def apply_project_plan(guild: discord.Guild, plan: dict) -> dict:
    created = {"categories": [], "channels": []}
    me = guild.me or guild.get_member(bot.user.id)
    if not me or not me.guild_permissions.manage_channels:
        return {"status": "blocked", "reason": "Manage Channels permission missing", "created": created}
    for cat in plan.get("categories", []):
        category_name = (cat.get("name") or "Project").strip()[:100]
        category = discord.utils.get(guild.categories, name=category_name)
        if category is None:
            category = await guild.create_category(category_name, reason="Hermes project bootstrap")
            created["categories"].append(category_name)
        for channel_name_raw in cat.get("channels", []):
            channel_name = safe_channel_name(channel_name_raw)
            if discord.utils.get(guild.text_channels, name=channel_name) is None:
                await guild.create_text_channel(channel_name, category=category, reason="Hermes project bootstrap")
                created["channels"].append(channel_name)
    return {"status": "ok", "created": created}


@bot.event
async def on_ready():
    global admin_loop_task, leader_lease_task, discord_runtime_is_leader
    if DISCORD_RUNTIME_ROLE != "primary":
        discord_runtime_is_leader = False
        print(f"[discord-runtime] connected in passive role={DISCORD_RUNTIME_ROLE}; message handling is disabled for this instance.")
        return
    discord_runtime_is_leader = refresh_discord_leader_lease()
    if leader_lease_task is None or leader_lease_task.done():
        leader_lease_task = asyncio.create_task(maintain_discord_leader_lease())
    try:
        ensure_discord_admin_tables()
    except Exception as e:
        print(f"[discord-admin] table init failed: {e}")
    if admin_loop_task is None or admin_loop_task.done():
        admin_loop_task = asyncio.create_task(admin_maintenance_loop())
    live_app_id = int(getattr(bot, "application_id", 0) or 0)
    if DISCORD_APPLICATION_ID and live_app_id and DISCORD_APPLICATION_ID != live_app_id:
        print(f"[discord-app] application id mismatch env={DISCORD_APPLICATION_ID} live={live_app_id}")
    if DISCORD_PUBLIC_KEY and (len(DISCORD_PUBLIC_KEY) != 64 or not all(ch in '0123456789abcdefABCDEF' for ch in DISCORD_PUBLIC_KEY)):
        print("[discord-app] public key format looks invalid")
    try:
        await maybe_sync_app_commands()
    except Exception as e:
        print(f"[discord-app] slash sync failed: {e}")
    try:
        await sync_live_discord_identity()
    except Exception as e:
        print(f"[discord-identity] sync failed: {e}")
    install_url = discord_install_url()
    if install_url:
        print(f"[discord-app] invite url: {install_url}")
    print(f"{DISCORD_CONNECTOR_NAME} Discord bot online as {bot.user} persona={DISCORD_PERSONA_MODE}")


@bot.check
async def enforce_command_policy(ctx):
    if not discord_runtime_allows_processing():
        return False
    if ctx.command is None:
        return True
    command_key = str(getattr(ctx.command, "name", "") or "").strip().lower()
    if not command_key:
        return True
    policy = await ensure_ctx_policy(ctx, command_key=command_key)
    if command_key in DM_ONLY_COMMANDS and not is_dm(ctx.message):
        await ctx.reply(f"For security, run this in DM with {DISCORD_CONNECTOR_NAME}.", mention_author=False)
        return False
    if command_key in OWNER_ONLY_COMMANDS and not is_owner(ctx):
        await ctx.reply("That command is owner-only.", mention_author=False)
        return False
    if command_key in SERVER_OPERATOR_COMMANDS and not (is_owner(ctx) or policy.get("server_operator")):
        await ctx.reply("You need server-operator access for that.", mention_author=False)
        return False
    if command_key in PROJECT_SCOPED_COMMANDS and not is_owner(ctx):
        project_key = normalize_project_key_local(policy.get("project_key") or "")
        if not project_key:
            await ctx.reply("Run that from the locked project channel for the target project, or ask the owner to bind one.", mention_author=False)
            return False
        if not policy.get("can_command"):
            await ctx.reply(build_access_denied_reply(project_key), mention_author=False)
            return False
    return True


@bot.event
async def on_member_join(member: discord.Member):
    if not discord_runtime_allows_processing():
        return
    if not DISCORD_WELCOME_ENABLED:
        return
    channel = member.guild.system_channel if member.guild else None
    if channel is None:
        return
    try:
        await channel.send(f"Welcome {member.mention}. I am {DISCORD_CONNECTOR_NAME} and I am actively watching this server.")
    except Exception as e:
        print(f"[discord] welcome failed: {e}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not discord_runtime_allows_processing():
        return
    if not claim_message_delivery_slot(message):
        print(f"[discord-runtime] duplicate message suppressed: {_message_runtime_key(message)}")
        return
    print(f"[discord] recv in {channel_label(message)} from {getattr(message.author, 'display_name', getattr(message.author, 'name', 'unknown'))}: {(message.content or '')[:160]}")
    policy = {}
    try:
        policy = await fetch_discord_actor_policy(message)
    except Exception as e:
        print(f"[discord-policy] message policy resolve failed: {e}")
    try:
        archive_discord_message(message, policy=policy)
    except Exception as e:
        print(f"[discord-archive] archive failed: {e}")
    await maybe_reply_to_text(message, policy=policy)
    if DISCORD_PERSONA_MODE != "zeus":
        await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        raw = (ctx.message.content or "").strip()
        if not raw.startswith(bot.command_prefix):
            return
        invoked = raw[len(bot.command_prefix):].split()[0].strip().lower()
        if not invoked:
            return
        policy = getattr(ctx, "_luminax_policy", {}) if ctx else {}
        if not policy:
            try:
                policy = await fetch_discord_actor_policy(ctx.message, command_key=invoked)
            except Exception:
                policy = {}
        authorized_builder = bool(is_owner(ctx) or policy.get("server_operator") or policy.get("can_command"))
        if not authorized_builder:
            await ctx.reply("I do not have that capability yet, and I will not queue new work from an unauthorized context.", mention_author=False)
            return
        goal = (
            f"Add or map Discord command capability `{invoked}` with safe, approved behavior. "
            f"Requested text: {raw}"
        )
        project_key = normalize_project_key_local(policy.get("project_key") or default_project_key_from_guild(getattr(ctx, "guild", None)))
        queued = {}
        try:
            queued = await core_post(
                "/capabilities/scan_and_plan",
                {
                    "goal": goal,
                    "project_key": project_key,
                    "create_tasks": True,
                    "max_tasks": 6,
                },
            )
        except Exception as e:
            print(f"[discord] unknown command planner failed: {e}")
        await queue_project_task(
            goal=f"Unknown Discord command requested: {invoked}",
            project_key=project_key,
            entails=f"Original command: {raw}",
            source="discord_unknown_command",
        )
        queued_count = len((queued or {}).get("queued_tasks", []) if isinstance(queued, dict) else [])
        await ctx.reply(
            f"I do not have `{invoked}` yet. I queued an ability-build plan and linked it to project `{project_key}` "
            f"(queued_tasks={queued_count}).",
            mention_author=False,
        )
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("Missing required argument. Run `!helpme` for command formats.", mention_author=False)
        return
    print(f"[discord] command error: {error}")


async def _pair_start_response_for_user(user: discord.abc.User) -> str:
    allowed, wait = consume_pairing_cooldown(int(getattr(user, "id", 0) or 0))
    if not allowed:
        return f"Hold one beat. Pairing is throttled for {wait:.1f} more seconds so we do not spam Discord or the core bridge."
    result = await start_pairing_session_via_core(user, reset=True)
    status = str(result.get("status") or "").strip().lower()
    if status == "already_linked":
        link = result.get("link", {}) if isinstance(result, dict) else {}
        portal_user_id = str(link.get("portal_user_id") or "").strip() or "your Mount Olympus identity"
        gmail = str(link.get("gmail") or "").strip()
        return compact_discord_text(
            f"You're already paired to {portal_user_id}" + (f" ({gmail})" if gmail else "") + ". If you need the link changed, ask the owner to rotate it from {UI_BRAND_NAME}.",
            1800,
        )
    if status == "locked":
        return f"Your last pairing session is still locked. Wait for the lock to clear or have the owner generate a fresh code from {UI_BRAND_NAME}."
    return compact_discord_text(
        f"Pairing session opened. {pairing_instruction_text()} You have {DISCORD_PAIRING_MAX_ATTEMPTS} tries before this session locks.",
        1800,
    )


@bot.tree.command(name="pair", description="Link your Discord account with Mount Olympus")
async def slash_pair(interaction: discord.Interaction):
    if interaction.guild is not None:
        await interaction.response.send_message(
            compact_discord_text(f"DM {DISCORD_CONNECTOR_NAME} and say `pair`. {pairing_instruction_text()}", 1800),
            ephemeral=True,
        )
        return
    try:
        reply = await _pair_start_response_for_user(interaction.user)
    except Exception as e:
        reply = f"I couldn't open a pairing session right now: {compact_discord_text(str(e), 400)}"
    await interaction.response.send_message(reply)


@bot.tree.command(name="olympus", description="Get the Mount Olympus access link")
async def slash_olympus(interaction: discord.Interaction):
    url = mount_olympus_url()
    install_url = discord_install_url()
    lines = [
        f"{UI_BRAND_NAME}: {url}" if url else f"{UI_BRAND_NAME} is online.",
        install_url and f"Discord install URL: {install_url}",
    ]
    await interaction.response.send_message(compact_discord_text("\n".join([line for line in lines if line]), 1800), ephemeral=interaction.guild is not None)


@bot.command()
async def pair(ctx):
    if not is_dm(ctx.message):
        await ctx.reply(compact_discord_text(f"DM {DISCORD_CONNECTOR_NAME} and say `pair`. {pairing_instruction_text()}", 1800), mention_author=False)
        return
    try:
        reply = await _pair_start_response_for_user(ctx.author)
    except Exception as e:
        reply = f"I couldn't open a pairing session right now: {compact_discord_text(str(e), 400)}"
    await ctx.reply(reply, mention_author=False)


@bot.command()
async def olympus(ctx):
    url = mount_olympus_url()
    install_url = discord_install_url()
    lines = [
        url and f"{UI_BRAND_NAME}: {url}",
        install_url and f"Discord install URL: {install_url}",
    ]
    await ctx.reply(compact_discord_text("\n".join([line for line in lines if line]), 1800) or f"{UI_BRAND_NAME} is online.", mention_author=False)


@bot.command()
async def helpme(ctx):
    await ctx.reply(
                "Commands: !pair, !olympus, !joinvc, !leavevc, !vcdiag, !vcclear, !voice <name>, !image <prompt|target|settings_json>, !logo <brand_or_prompt|target|settings_json>, !bug <title> | <details>, !discordmode <engaged|focus|silent>, !projectplan <topic>, !projectapply, !projectsync <goal>, !codex <goal>, !selfupdate <goal>, !websitecreate <project_key|title|goal>, !guildsync [blueprint|project|mode], !communitysync [project|mode], !communityannounce <title|body>, !patchnotes <title|body>, !olympussync [mode], !revampsync [mode], !revamptest [goal], !underfootsetup, !underfootevents, !underfootbridge, !underfootunity <tool|json_args>, !underfootdariusroute, !underfootdariussession [session_id|title|goal], !underfootscreenshot, !incidentack <key> [note], !incidentresolve <key> [note], !welcome <on|off>, !poll <q|o1|o2...>, !pollclose <message_id>, !raffle <prize|minutes|winners>, !raffleclose <message_id>, !trivia <start|score>, !answer <A|B|C|D>, !feedback <text>, !feedbackreview [limit], !interview @user | role | notes, !interviewclose <decision>, !onboard @user | role | project, !recruitpost <title|details>, !cmdmap, !purge <count>, !purgehermes [count], !testerlink <key|provider|display|project|tags>, !testertoken <key|token|expires_min|scopes>, !testerstatus <key|status|note>, !testerdelete <key|note>, !testerlist [provider|status|project], !testersummary. Slash commands: /pair and /olympus.",
        mention_author=False,
    )


@bot.command()
async def purge(ctx, count: int = 20):
    if not ctx.guild or not (is_owner(ctx) or is_admin_ctx(ctx)):
        await ctx.reply("You need server-operator access for that.", mention_author=False)
        return
    me = ctx.guild.me or ctx.guild.get_member(bot.user.id)
    if me is None or not getattr(getattr(me, "guild_permissions", None), "manage_messages", False):
        await ctx.reply("I do not have Manage Messages permission here.", mention_author=False)
        return
    deleted = await purge_recent_channel_messages(ctx.channel, limit=count, hermes_only=False)
    await ctx.reply(f"Purged {deleted} recent messages from this channel.", mention_author=False)


@bot.command()
async def purgehermes(ctx, count: int = 20):
    if ctx.guild and not (is_owner(ctx) or is_admin_ctx(ctx)):
        await ctx.reply("You need server-operator access for that.", mention_author=False)
        return
    deleted = await delete_recent_hermes_messages(ctx.channel, limit=count)
    await ctx.reply(f"Deleted {deleted} recent Hermes messages from this channel.", mention_author=False)


@bot.command()
async def bug(ctx, *, details: str):
    title, body = (details.split("|", 1) + [details])[:2]
    bug_id = await submit_bug(ctx.message, title.strip()[:255] or "Untitled bug", body.strip() or details.strip())
    await ctx.reply(f"Bug logged as #{bug_id}.", mention_author=False)


@bot.command()
async def discordmode(ctx, mode: str):
    if not is_owner(ctx) or not ctx.guild:
        return
    mode = (mode or "").strip().lower()
    if mode not in {"engaged", "focus", "silent"}:
        await ctx.reply("Use one of: engaged, focus, silent", mention_author=False)
        return
    guild_modes[ctx.guild.id] = mode
    await ctx.reply(f"Discord mode set to {mode}.", mention_author=False)


@bot.command()
async def welcome(ctx, state: str):
    if not is_owner(ctx):
        return
    desired = (state or "").strip().lower()
    global DISCORD_WELCOME_ENABLED
    if desired in {"on", "true", "1"}:
        DISCORD_WELCOME_ENABLED = True
    elif desired in {"off", "false", "0"}:
        DISCORD_WELCOME_ENABLED = False
    await ctx.reply(f"Welcome mode: {'on' if DISCORD_WELCOME_ENABLED else 'off'}", mention_author=False)


@bot.command()
async def projectplan(ctx, *, topic: str):
    if not is_owner(ctx) or not ctx.guild:
        return
    plan = await build_project_plan(topic)
    guild_project_plans[ctx.guild.id] = plan
    project_key = default_project_key_from_guild(ctx.guild)
    task = await queue_project_task(
        goal=f"Draft Discord project structure plan for topic: {topic}",
        project_key=project_key,
        entails="Produce categories/channels/operating lanes and queue follow-up apply step.",
        source="discord_projectplan",
    )
    preview = json.dumps(plan, indent=2)
    if len(preview) > 1800:
        preview = preview[:1800] + "\n..."
    await ctx.reply(
        f"Server plan ready:\n```json\n{preview}\n```\nRun !projectapply to create channels."
        + (f"\nTask queued: {task.get('task_uid')}" if task.get("task_uid") else ""),
        mention_author=False,
    )


@bot.command()
async def projectapply(ctx):
    if not is_owner(ctx) or not ctx.guild:
        return
    plan = guild_project_plans.get(ctx.guild.id)
    if not plan:
        await ctx.reply("No cached plan. Run !projectplan <topic> first.", mention_author=False)
        return
    result = await apply_project_plan(ctx.guild, plan)
    project_key = default_project_key_from_guild(ctx.guild)
    task = await queue_project_task(
        goal=f"Apply Discord project structure for guild {ctx.guild.name}",
        project_key=project_key,
        entails=f"Plan apply result: {json.dumps(result)[:1200]}",
        source="discord_projectapply",
    )
    await ctx.reply(
        f"Plan apply result: {json.dumps(result)}"
        + (f"\nTask queued: {task.get('task_uid')}" if task.get("task_uid") else ""),
        mention_author=False,
    )


@bot.command()
async def projectsync(ctx, *, goal: str = ""):
    if not is_owner(ctx) or not ctx.guild:
        return
    project_key = default_project_key_from_guild(ctx.guild)
    scan_goal = goal.strip() or f"Scan capabilities and plan execution tasks for Discord + project operations in {ctx.guild.name}"
    try:
        result = await core_post(
            "/capabilities/scan_and_plan",
            {
                "goal": scan_goal,
                "project_key": project_key,
                "create_tasks": True,
                "max_tasks": 8,
            },
        )
    except Exception as e:
        await ctx.reply(f"Project sync failed: {e}", mention_author=False)
        return
    queued = len(result.get("queued_tasks", []) if isinstance(result, dict) else [])
    await ctx.reply(
        f"Project sync complete for `{project_key}`. queued_tasks={queued}. "
        "Use the Mount Olympus Projects view to inspect and track task status.",
        mention_author=False,
    )


@bot.command()
async def codex(ctx, *, goal: str = ""):
    if not is_admin_ctx(ctx):
        return
    goal = (goal or "").strip()
    if not goal:
        await ctx.reply("Usage: !codex <goal>", mention_author=False)
        return
    await ctx.reply(build_codex_dispatch_ack(goal), mention_author=False)
    policy = getattr(ctx, "_luminax_policy", {}) if ctx else {}
    asyncio.create_task(run_codex_job_and_report(ctx.channel, ctx.author, ctx.guild, goal, scope="implement", allow_write=bool((policy or {}).get("can_write") or is_owner(ctx)), policy=policy, job_id=f"discord_job_{uuid.uuid4().hex[:12]}"))


@bot.command()
async def selfupdate(ctx, *, goal: str = ""):
    if not is_owner(ctx):
        return
    goal = (goal or "").strip()
    if not goal:
        await ctx.reply("Usage: !selfupdate <goal>", mention_author=False)
        return
    await ctx.reply(build_codex_dispatch_ack(goal), mention_author=False)
    asyncio.create_task(run_codex_job_and_report(ctx.channel, ctx.author, ctx.guild, goal, scope="implement", allow_write=True, policy=getattr(ctx, "_luminax_policy", {}), job_id=f"discord_job_{uuid.uuid4().hex[:12]}"))


@bot.command()
async def image(ctx, *, details: str = ""):
    if not is_owner(ctx):
        return
    try:
        prompt, output_target, settings = parse_image_request_parts(details)
    except Exception as e:
        await ctx.reply(f"Invalid image settings JSON: {e}", mention_author=False)
        return
    prompt = (prompt or "").strip()
    if not prompt:
        await ctx.reply("Usage: !image <prompt|optional output target|optional settings json>", mention_author=False)
        return
    if output_target.lower().startswith("site:") and "publish_site" not in settings:
        settings["publish_site"] = True
    await ctx.reply("Generating that image now. I will use the local 3090 image lane first and fall back to the premium cloud path if needed.", mention_author=False)
    try:
        result = await create_media_job("image", prompt, output_target=output_target, settings=settings, wait_for_completion=True)
        job = result.get("job", {}) if isinstance(result, dict) else {}
        if str(job.get("status", "")).lower() == "failed":
            raise RuntimeError(str(job.get("notes", "image generation failed")))
        await deliver_media_job_result(ctx.channel, ctx.author, job, intro="Generated image ready.", policy=getattr(ctx, "_luminax_policy", {}))
    except Exception as e:
        await ctx.reply(f"Image generation failed: {compact_discord_text(str(e), 1200)}", mention_author=False)


@bot.command()
async def logo(ctx, *, details: str = ""):
    if not is_owner(ctx):
        return
    try:
        prompt, output_target, settings = parse_image_request_parts(details)
    except Exception as e:
        await ctx.reply(f"Invalid logo settings JSON: {e}", mention_author=False)
        return
    prompt = (prompt or "").strip()
    if not prompt:
        await ctx.reply("Usage: !logo <brand_or_prompt|optional output target|optional settings json>", mention_author=False)
        return
    if "logo" not in prompt.lower() and "wordmark" not in prompt.lower() and "brand" not in prompt.lower():
        prompt = f"Create a clean, production-ready logo and wordmark for {prompt}. Transparent background. Strong silhouette. Nonprofit raffle platform tone. Modern, trustworthy, high-contrast."
    settings.setdefault("background", "transparent")
    settings.setdefault("output_format", "png")
    settings.setdefault("size", "1024x1024")
    if output_target.lower().startswith("site:") and "publish_site" not in settings:
        settings["publish_site"] = True
    await ctx.reply("Generating that logo now. I will use the premium cloud logo lane so transparent brand assets come back cleaner.", mention_author=False)
    try:
        result = await create_media_job("image", prompt, output_target=output_target, settings=settings, model_key="image_logo_fast", wait_for_completion=True)
        job = result.get("job", {}) if isinstance(result, dict) else {}
        if str(job.get("status", "")).lower() == "failed":
            raise RuntimeError(str(job.get("notes", "logo generation failed")))
        await deliver_media_job_result(ctx.channel, ctx.author, job, intro="Generated logo ready.", policy=getattr(ctx, "_luminax_policy", {}))
    except Exception as e:
        await ctx.reply(f"Logo generation failed: {compact_discord_text(str(e), 1200)}", mention_author=False)


@bot.command()
async def websitecreate(ctx, *, details: str):
    if not is_owner(ctx):
        return
    parts = [p.strip() for p in (details or "").split("|")]
    site_name = parts[0] if len(parts) > 0 else ""
    title = parts[1] if len(parts) > 1 else (site_name or "Project Site")
    goal = parts[2] if len(parts) > 2 else f"Create and operate website project {site_name or 'project'}"
    if not site_name:
        await ctx.reply("Usage: !websitecreate <project_key|title|goal>", mention_author=False)
        return
    try:
        scaffold = await core_post("/sites/create_scaffold", {"site_name": site_name, "title": title})
    except Exception as e:
        await ctx.reply(f"Website scaffold failed: {e}", mention_author=False)
        return
    try:
        published = await core_post("/sites/publish", {"site_name": site_name, "mode": "copy", "make_default": True})
    except Exception as e:
        published = {"status": "publish_failed", "detail": str(e)}
    discord_lanes = {}
    if ctx.guild:
        try:
            discord_lanes = await ensure_project_channels_for_guild(ctx.guild, site_name, title=title)
        except Exception as e:
            discord_lanes = {"status": "error", "detail": str(e)}
    task = await queue_project_task(
        goal=goal,
        project_key=site_name,
        entails=f"Website scaffold root: {scaffold.get('site_root', 'unknown')}",
        source="discord_websitecreate",
    )
    await ctx.reply(
        f"Website scaffold created for `{site_name}` (title: {title}). "
        + (f"Task queued: {task.get('task_uid')}. " if task.get("task_uid") else "Task queue failed. ")
        + (f"Preview: {published.get('preview_url')} " if published.get("preview_url") else "")
        + (f"Default: {published.get('default_url')} " if published.get("default_url") else "")
        + (f"Text lane: #{getattr(discord_lanes.get('text_channel'), 'name', '')} " if isinstance(discord_lanes, dict) and discord_lanes.get("text_channel") else "")
        + (f"Voice lane: {getattr(discord_lanes.get('voice_channel'), 'name', '')}" if isinstance(discord_lanes, dict) and discord_lanes.get("voice_channel") else ""),
        mention_author=False,
    )


@bot.command()
async def guildsync(ctx, *, spec: str = ""):
    if not is_admin_ctx(ctx) or not ctx.guild:
        return
    try:
        summary, reply = await run_discord_blueprint_command(ctx, spec=spec)
    except discord.Forbidden as exc:
        await ctx.reply(f"Discord blueprint sync is blocked by missing permissions: {exc}", mention_author=False)
        return
    except Exception as exc:
        await ctx.reply(f"Discord blueprint sync failed: {compact_discord_text(str(exc), 1500)}", mention_author=False)
        return
    await ctx.reply(reply, mention_author=False)


@bot.command()
async def communitysync(ctx, *, spec: str = ""):
    if not is_admin_ctx(ctx) or not ctx.guild:
        return
    parts = parse_pipe_parts(spec)
    requested_project = normalize_project_key_local(parts[0] if parts else "") or normalize_project_key_local(default_project_key_from_guild(ctx.guild))
    mode = (parts[1] if len(parts) > 1 else "plan").strip().lower()
    dry_run = mode in {"plan", "preview", "dryrun", "dry-run"}
    packet = await fetch_discord_community_packet(requested_project, guild=ctx.guild, mode="preview" if dry_run else "apply")
    if not packet:
        await ctx.reply(f"I could not build a community packet for `{requested_project or 'project'}`.", mention_author=False)
        return
    if not packet.get("community_guild_enabled"):
        await ctx.reply(f"`{requested_project}` is internal-only right now, so public community sync is disabled.", mention_author=False)
        return
    review = await review_discord_action(
        f"Sync public community guild for {requested_project} in {ctx.guild.name}",
        getattr(ctx, "_luminax_policy", {}),
        action_kind="branding_sync",
        allow_write=not dry_run,
        meta={"project_key": requested_project, "blueprint_key": packet.get("community_guild_blueprint_key"), "dry_run": dry_run, "source": "communitysync"},
    )
    if not review.get("approved"):
        await ctx.reply(compact_discord_text(str(review.get("detail") or "Zeus held that community sync."), 1800), mention_author=False)
        return
    record_runtime_breadcrumb(
        "community_blueprint_apply_start",
        severity="info",
        scene="discord",
        route="communitysync",
        connection=runtime_connection_summary(surface="public_channel", project_key=requested_project, guild_id=str(ctx.guild.id), route_status="preview" if dry_run else "apply"),
        blueprint_key=str(packet.get("community_guild_blueprint_key") or ""),
        guild_name=ctx.guild.name,
    )
    summary = await sync_discord_blueprint(
        ctx.guild,
        blueprint_key=str(packet.get("community_guild_blueprint_key") or "").strip(),
        requested_by=ctx.author.id,
        dry_run=dry_run,
    )
    registry = {}
    if not dry_run:
        registry = await upsert_discord_community_guild_core(ctx.guild, requested_project, packet)
    validation = await fetch_discord_community_validation(requested_project, guild=ctx.guild)
    record_runtime_breadcrumb(
        "community_blueprint_apply_result",
        severity="info",
        scene="discord",
        route="communitysync",
        connection=runtime_connection_summary(surface="public_channel", project_key=requested_project, guild_id=str(ctx.guild.id), route_status="dry_run" if dry_run else "live_public"),
        blueprint_key=str(packet.get("community_guild_blueprint_key") or ""),
        registry_status=str((registry or {}).get("community_guild_status") or packet.get("community_guild_status") or ""),
    )
    reply = render_discord_blueprint_summary(summary)
    reply += "\n\n" + render_community_packet_summary(packet)
    if registry:
        reply += f"\n\nCommunity registry bound for guild_id={registry.get('guild_id')}"
    if validation:
        reply += "\n\n" + render_community_validation_summary(validation)
    if not dry_run:
        await route_public_community_owner_notice(
            getattr(ctx, "_luminax_policy", {}),
            event_key="bootstrap",
            summary=(
                f"{pretty_project_label(requested_project)} public community bootstrap synced for {ctx.guild.name}. "
                f"mirror={str((validation.get('packet') or {}).get('olympus_mirror_channel_id') or (registry or {}).get('olympus_mirror_channel_id') or 'missing')}"
            ),
            requested_by=f"discord:{ctx.author.id}",
        )
    await ctx.reply(compact_discord_text(reply, 1800), mention_author=False)


@bot.command()
async def olympussync(ctx, *, spec: str = ""):
    if not is_admin_ctx(ctx) or not ctx.guild:
        return
    mode = spec.strip() or "mount-olympus-core|live"
    if "|" not in mode:
        mode = f"mount-olympus-core|{mode}"
    try:
        summary, reply = await run_discord_blueprint_command(
            ctx,
            blueprint_key="mount_olympus_control_guild",
            spec=mode,
        )
    except discord.Forbidden as exc:
        await ctx.reply(f"Olympus sync is blocked by missing permissions: {exc}", mention_author=False)
        return
    except Exception as exc:
        await ctx.reply(f"Olympus sync failed: {compact_discord_text(str(exc), 1500)}", mention_author=False)
        return
    await ctx.reply(reply, mention_author=False)


@bot.command()
async def revampsync(ctx, *, spec: str = ""):
    if not is_admin_ctx(ctx) or not ctx.guild:
        return
    mode = spec.strip() or "plan"
    try:
        summary, reply = await run_discord_blueprint_command(ctx, blueprint_key="revamp_staging", spec=f"mount-olympus-web|{mode}" if "|" not in mode else mode)
    except discord.Forbidden as exc:
        await ctx.reply(f"Revamp staging sync is blocked by missing permissions: {exc}", mention_author=False)
        return
    except Exception as exc:
        await ctx.reply(f"Revamp staging sync failed: {compact_discord_text(str(exc), 1500)}", mention_author=False)
        return
    await ctx.reply(reply, mention_author=False)


@bot.command()
async def revamptest(ctx, *, goal: str = ""):
    if not is_admin_ctx(ctx) or not ctx.guild:
        return
    prompt = (goal or "").strip() or "Change the olympus base landing page to a moving parralax banner with a sun moving across the sky and the tree layers moving"
    try:
        _summary, sync_reply = await run_discord_blueprint_command(
            ctx,
            blueprint_key="revamp_staging",
            spec="mount-olympus-web|live",
        )
    except Exception as exc:
        await ctx.reply(f"Revamp staging prep failed: {compact_discord_text(str(exc), 1500)}", mention_author=False)
        return
    await ctx.reply(compact_discord_text(f"{sync_reply}\nDispatching the Olympus web change through Codex now.", 1800), mention_author=False)
    policy = await fetch_discord_actor_policy(ctx.message, requested_project_key="mount-olympus-web", command_key="revamptest")
    asyncio.create_task(
        run_codex_job_and_report(
            ctx.channel,
            ctx.author,
            ctx.guild,
            prompt,
            scope="implement",
            allow_write=bool((policy or {}).get("can_write") or is_owner(ctx)),
            policy=policy,
            job_id=f"discord_job_{uuid.uuid4().hex[:12]}",
        )
    )


@bot.command()
async def paprsetup(ctx):
    if not is_admin_ctx(ctx) or not ctx.guild:
        return
    try:
        summary = await provision_papr_guild(ctx.guild, requested_by=ctx.author.id)
    except discord.Forbidden as e:
        await ctx.reply(f"PAPR Discord bootstrap is blocked by missing Discord permissions: {e}", mention_author=False)
        return
    except Exception as e:
        await ctx.reply(f"PAPR Discord bootstrap failed: {compact_discord_text(str(e), 1500)}", mention_author=False)
        return
    task = await queue_project_task(
        goal=f"Provision PAPR Discord server structure for guild {ctx.guild.name}",
        project_key="papr",
        entails=json.dumps(
            {
                "guild_id": ctx.guild.id,
                "categories": len(summary.get("categories", [])),
                "channels": len(summary.get("channels", [])),
                "pinned_posts": len(summary.get("pinned_posts", [])),
                "events_created": len(summary.get("events_created", [])),
            }
        ),
        source="discord_paprsetup",
        requested_by=f"discord:{ctx.author.id}",
    )
    await ctx.reply(
        f"PAPR Discord bootstrap complete. categories={len(summary.get('categories', []))} "
        f"channels={len(summary.get('channels', []))} pinned={len(summary.get('pinned_posts', []))} "
        f"events_created={len(summary.get('events_created', []))}"
        + (f" task={task.get('task_uid')}" if task.get("task_uid") else ""),
        mention_author=False,
    )


@bot.command()
async def paprevents(ctx):
    if not is_admin_ctx(ctx) or not ctx.guild:
        return
    try:
        created = await sync_event_templates_for_guild(ctx.guild)
        templates = list_event_templates(ctx.guild.id)
    except discord.Forbidden as e:
        await ctx.reply(f"PAPR event sync is blocked by missing Discord permissions: {e}", mention_author=False)
        return
    except Exception as e:
        await ctx.reply(f"PAPR event sync failed: {compact_discord_text(str(e), 1500)}", mention_author=False)
        return
    await queue_project_task(
        goal=f"Refresh PAPR Discord scheduled events for guild {ctx.guild.name}",
        project_key="papr",
        entails=json.dumps({"templates": len(templates), "events_created": len(created)}),
        source="discord_paprevents",
        requested_by=f"discord:{ctx.author.id}",
    )
    created_names = ", ".join(event.get("name", "event") for event in created[:4]) if created else "none"
    await ctx.reply(
        f"PAPR event sync complete. templates={len(templates)} created_now={len(created)} ({created_names}).",
        mention_author=False,
    )


@bot.command()
async def underfootsetup(ctx):
    if not is_admin_ctx(ctx) or not ctx.guild:
        return
    try:
        summary = await provision_underfoot_guild(ctx.guild, requested_by=ctx.author.id)
    except discord.Forbidden as e:
        await ctx.reply(f"Underfoot Discord bootstrap is blocked by missing Discord permissions: {e}", mention_author=False)
        return
    except Exception as e:
        await ctx.reply(f"Underfoot Discord bootstrap failed: {compact_discord_text(str(e), 1500)}", mention_author=False)
        return
    task = await queue_project_task(
        goal=f"Provision Underfoot Discord server structure for guild {ctx.guild.name}",
        project_key="underfoot",
        entails=json.dumps(
            {
                "guild_id": ctx.guild.id,
                "categories": len(summary.get("categories", [])),
                "channels": len(summary.get("channels", [])),
                "pinned_posts": len(summary.get("pinned_posts", [])),
                "events_created": len(summary.get("events_created", [])),
            }
        ),
        source="discord_underfootsetup",
        requested_by=f"discord:{ctx.author.id}",
    )
    await ctx.reply(
        f"Underfoot Discord bootstrap complete. categories={len(summary.get('categories', []))} "
        f"channels={len(summary.get('channels', []))} pinned={len(summary.get('pinned_posts', []))} "
        f"events_created={len(summary.get('events_created', []))}"
        + (f" task={task.get('task_uid')}" if task.get("task_uid") else ""),
        mention_author=False,
    )


@bot.command()
async def underfootevents(ctx):
    if not is_admin_ctx(ctx) or not ctx.guild:
        return
    try:
        created = await sync_event_templates_for_guild(ctx.guild)
        templates = list_event_templates(ctx.guild.id)
    except discord.Forbidden as e:
        await ctx.reply(f"Underfoot event sync is blocked by missing Discord permissions: {e}", mention_author=False)
        return
    except Exception as e:
        await ctx.reply(f"Underfoot event sync failed: {compact_discord_text(str(e), 1500)}", mention_author=False)
        return
    await queue_project_task(
        goal=f"Refresh Underfoot Discord scheduled events for guild {ctx.guild.name}",
        project_key="underfoot",
        entails=json.dumps({"templates": len(templates), "events_created": len(created)}),
        source="discord_underfootevents",
        requested_by=f"discord:{ctx.author.id}",
    )
    created_names = ", ".join(event.get("name", "event") for event in created[:4]) if created else "none"
    await ctx.reply(
        f"Underfoot event sync complete. templates={len(templates)} created_now={len(created)} ({created_names}).",
        mention_author=False,
    )


@bot.command()
async def underfootbridge(ctx):
    if not is_admin_ctx(ctx):
        return
    try:
        data = await underfoot_bridge_get(
            "/api/bridge/health",
            ctx.author.id,
            getattr(ctx.author, "display_name", ctx.author.name),
        )
    except Exception as e:
        await ctx.reply(f"Underfoot bridge check failed: {compact_discord_text(str(e), 1500)}", mention_author=False)
        return
    tools = data.get("tools", []) if isinstance(data, dict) else []
    tool_names = ", ".join([str(item.get("name")) for item in tools[:6] if isinstance(item, dict) and item.get("name")]) or "none"
    await ctx.reply(
        f"Underfoot bridge online. tools={len(tools)} [{tool_names}] execute_methods={len(data.get('allowed_execute_methods', []))}",
        mention_author=False,
    )


@bot.command()
async def underfootunity(ctx, *, spec: str = "list"):
    if not is_admin_ctx(ctx):
        return
    parts = parse_pipe_parts(spec)
    action = (parts[0] if parts else "list").strip().lower()
    user_name = getattr(ctx.author, "display_name", ctx.author.name)
    if action in {"list", "tools"}:
        try:
            data = await underfoot_bridge_get("/api/bridge/tools", ctx.author.id, user_name)
        except Exception as e:
            await ctx.reply(f"Underfoot tools lookup failed: {compact_discord_text(str(e), 1500)}", mention_author=False)
            return
        tools = data.get("tools", []) if isinstance(data, dict) else []
        lines = ["Underfoot bridge tools:"]
        for item in tools[:12]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('name')}: {item.get('description')}")
        await ctx.reply(compact_discord_text("\n".join(lines), 1800), mention_author=False)
        return

    args: dict[str, Any] = {}
    if len(parts) > 1:
        raw_args = parts[1]
        try:
            parsed = json.loads(raw_args)
            if isinstance(parsed, dict):
                args = parsed
        except Exception:
            if action == "unity_execute_method":
                args = {"method": raw_args}
            else:
                args = {"value": raw_args}

    try:
        data = await underfoot_bridge_post(
            f"/api/bridge/tools/{action}",
            ctx.author.id,
            user_name,
            {"args": args},
        )
    except Exception as e:
        await ctx.reply(f"Underfoot tool run failed: {compact_discord_text(str(e), 1500)}", mention_author=False)
        return

    if ctx.guild:
        await queue_project_task(
            goal=f"Run Underfoot bridge tool {action}",
            project_key="underfoot",
            entails=json.dumps({"args": args}),
            source="discord_underfootunity",
            requested_by=f"discord:{ctx.author.id}",
        )
    await ctx.reply(render_underfoot_bridge_result(action, data), mention_author=False)


@bot.command()
async def underfootdariusroute(ctx):
    if not is_admin_ctx(ctx):
        return
    try:
        data = await get_darius_route("underfoot")
    except Exception as e:
        await ctx.reply(f"Underfoot Darius route lookup failed: {compact_discord_text(str(e), 1500)}", mention_author=False)
        return
    route = data.get("route", {}) if isinstance(data, dict) else {}
    if not isinstance(route, dict) or not route:
        await ctx.reply("Underfoot Darius route is not assigned yet.", mention_author=False)
        return
    await ctx.reply(
        compact_discord_text(
            "Underfoot Darius route\n"
            f"- status: {route.get('routeStatus')}\n"
            f"- agent: {route.get('agentId')}\n"
            f"- machine: {route.get('machineLabel')}\n"
            f"- session: {route.get('activeCodexSessionId')}\n"
            f"- workspace: {route.get('workspaceRoot')}",
            1800,
        ),
        mention_author=False,
    )


@bot.command()
async def underfootdariussession(ctx, *, spec: str = ""):
    if not is_admin_ctx(ctx):
        return
    parts = parse_pipe_parts(spec)
    session_id = parts[0] if parts else ""
    title = parts[1] if len(parts) > 1 else "Underfoot Darius Session"
    goal = parts[2] if len(parts) > 2 else "Assigned Underfoot Darius project session."
    try:
        data = await assign_darius_session("underfoot", session_id=session_id, title=title, goal=goal, allow_write=True)
    except Exception as e:
        await ctx.reply(f"Underfoot Darius session assign failed: {compact_discord_text(str(e), 1500)}", mention_author=False)
        return
    route = data.get("route", {}) if isinstance(data, dict) else {}
    session = data.get("session", {}) if isinstance(data, dict) else {}
    await ctx.reply(
        compact_discord_text(
            "Underfoot Darius session assigned\n"
            f"- session: {session.get('session_id')}\n"
            f"- status: {route.get('routeStatus')}\n"
            f"- machine: {route.get('machineLabel')}\n"
            f"- agent: {route.get('agentId')}",
            1800,
        ),
        mention_author=False,
    )


@bot.command()
async def underfootscreenshot(ctx):
    if not is_admin_ctx(ctx):
        return
    user_name = getattr(ctx.author, "display_name", ctx.author.name)
    try:
        file_bytes, _, filename = await underfoot_bridge_get_file(
            "/api/bridge/desktop/screenshot",
            ctx.author.id,
            user_name,
        )
    except Exception as e:
        await ctx.reply(f"Underfoot screenshot failed: {compact_discord_text(str(e), 1500)}", mention_author=False)
        return

    try:
        await ctx.author.send(
            f"Underfoot desktop capture for {user_name}:",
            file=discord.File(io.BytesIO(file_bytes), filename=filename or "underfoot_capture.png"),
        )
    except Exception as e:
        await ctx.reply(f"Screenshot captured but DM delivery failed: {compact_discord_text(str(e), 1500)}", mention_author=False)
        return

    if ctx.guild:
        await ctx.reply("Underfoot desktop capture sent to your DMs.", mention_author=False)
    else:
        await ctx.reply("Underfoot desktop capture delivered.", mention_author=False)


@bot.command()
async def incidentack(ctx, incident_key: str, *, note: str = ""):
    if not is_admin_ctx(ctx):
        return
    if not CONTROL_TOKEN:
        await ctx.reply("CONTROL_TOKEN is not configured for incident ack.", mention_author=False)
        return
    try:
        await admin_api_post(
            "/incidents/ack",
            {
                "token": CONTROL_TOKEN,
                "incident_key": incident_key,
                "actor": f"discord:{ctx.author.id}",
                "note": note,
            },
            timeout_sec=20,
        )
        await ctx.reply(f"Incident `{incident_key}` acknowledged.", mention_author=False)
    except Exception as e:
        await ctx.reply(f"Incident ack failed: {compact_discord_text(str(e), 1500)}", mention_author=False)


@bot.command()
async def incidentresolve(ctx, incident_key: str, *, note: str = ""):
    if not is_admin_ctx(ctx):
        return
    if not CONTROL_TOKEN:
        await ctx.reply("CONTROL_TOKEN is not configured for incident resolve.", mention_author=False)
        return
    try:
        await admin_api_post(
            "/incidents/resolve",
            {
                "token": CONTROL_TOKEN,
                "incident_key": incident_key,
                "actor": f"discord:{ctx.author.id}",
                "note": note,
            },
            timeout_sec=20,
        )
        await ctx.reply(f"Incident `{incident_key}` resolved.", mention_author=False)
    except Exception as e:
        await ctx.reply(f"Incident resolve failed: {compact_discord_text(str(e), 1500)}", mention_author=False)


@bot.command()
async def poll(ctx, *, spec: str):
    if not is_admin_ctx(ctx) or not ctx.guild:
        return
    policy = await ensure_ctx_policy(ctx, command_key="poll")
    parts = parse_pipe_parts(spec)
    if len(parts) < 3:
        await ctx.reply("Usage: !poll <question|option1|option2|...|duration=15>", mention_author=False)
        return
    question = parts[0]
    options: list[str] = []
    duration_min = 15
    for p in parts[1:]:
        low = p.lower()
        if low.startswith("duration="):
            duration_min = max(1, min(_safe_int(p.split("=", 1)[1], 15), 1440))
            continue
        options.append(p[:120])
    options = options[: len(POLL_EMOJIS)]
    if len(options) < 2:
        await ctx.reply("Poll requires at least two options.", mention_author=False)
        return
    closes_at = int(time.time()) + (duration_min * 60)
    lines = [f"Poll: **{question}**", f"Closes in: {duration_min} minute(s)"]
    for i, opt in enumerate(options):
        lines.append(f"{POLL_EMOJIS[i]} {opt}")
    msg = await ctx.send("\n".join(lines))
    emojis = POLL_EMOJIS[: len(options)]
    for emoji in emojis:
        await msg.add_reaction(emoji)
    active_polls[msg.id] = {
        "guild_id": ctx.guild.id,
        "channel_id": ctx.channel.id,
        "question": question,
        "options": options,
        "emojis": emojis,
        "closes_at": closes_at,
        "project_key": normalize_project_key_local((policy or {}).get("project_key") or default_project_key_from_guild(ctx.guild)),
        "community_guild_enabled": bool((policy or {}).get("community_guild")),
        "olympus_mirror_channel_id": str((((policy or {}).get("community_guild") or {}).get("olympus_mirror_channel_id") or "")).strip(),
    }
    poll_store_open(ctx.guild.id, ctx.channel.id, msg.id, question, options, closes_at)
    if (policy or {}).get("community_guild"):
        await upsert_discord_community_feedback_receipt_core(
            policy,
            receipt_kind="poll_open",
            summary=f"Community poll opened: {question}",
            detail_text=json.dumps(
                {
                    "question": question,
                    "options": options,
                    "duration_min": duration_min,
                    "message_id": str(msg.id),
                    "channel": str(getattr(ctx.channel, "name", "") or ""),
                },
                ensure_ascii=False,
            ),
            message=ctx.message,
            status="open",
            meta={
                "poll_message_id": str(msg.id),
                "poll_channel_name": str(getattr(ctx.channel, "name", "") or ""),
                "duration_min": duration_min,
                "source": "poll_command",
            },
        )
        record_runtime_breadcrumb(
            "community_poll_publish_result",
            severity="info",
            scene="discord",
            route="poll_open",
            connection=runtime_connection_summary(
                surface=str((policy or {}).get("surface") or "public_channel"),
                project_key=str((active_polls[msg.id] or {}).get("project_key") or ""),
                guild_id=str(ctx.guild.id),
                channel_id=str(getattr(ctx.channel, "id", 0) or ""),
                route_status="opened",
            ),
            message_id=str(msg.id),
            question=compact_discord_text(question, 140),
        )
        await route_public_community_owner_notice(
            policy,
            event_key="poll_open",
            summary=f"{pretty_project_label(str((active_polls[msg.id] or {}).get('project_key') or ''))} poll opened in #{getattr(ctx.channel, 'name', 'channel')}: {compact_discord_text(question, 160)}",
            requested_by=f"discord:{ctx.author.id}",
        )
    await queue_project_task(
        goal=f"Run Discord poll: {question}",
        project_key=normalize_project_key_local((policy or {}).get("project_key") or default_project_key_from_guild(ctx.guild)),
        entails=f"Poll message={msg.id}, duration_min={duration_min}",
        source="discord_poll",
    )
    await ctx.reply(f"Poll created. message_id={msg.id}", mention_author=False)


@bot.command()
async def pollclose(ctx, message_id: int):
    if not is_admin_ctx(ctx):
        return
    result = await close_poll(ctx.channel, message_id, closed_by=f"user:{ctx.author.id}")
    await ctx.reply(f"Poll close: {result.get('status','unknown')}", mention_author=False)


@bot.command()
async def raffle(ctx, *, spec: str):
    if not is_admin_ctx(ctx) or not ctx.guild:
        return
    parts = parse_pipe_parts(spec)
    if len(parts) < 2:
        await ctx.reply("Usage: !raffle <prize|minutes|winners>", mention_author=False)
        return
    prize = parts[0][:255]
    duration_min = max(1, min(_safe_int(parts[1], 30), 1440))
    winners_count = max(1, min(_safe_int(parts[2], 1) if len(parts) > 2 else 1, 20))
    closes_at = int(time.time()) + (duration_min * 60)
    msg = await ctx.send(
        f"Raffle: **{prize}**\nReact with {RAFFLE_EMOJI} to enter.\n"
        f"Closes in {duration_min} minute(s). Winners: {winners_count}"
    )
    await msg.add_reaction(RAFFLE_EMOJI)
    active_raffles[msg.id] = {
        "guild_id": ctx.guild.id,
        "channel_id": ctx.channel.id,
        "prize": prize,
        "winners": winners_count,
        "closes_at": closes_at,
    }
    raffle_store_open(ctx.guild.id, ctx.channel.id, msg.id, prize, winners_count, closes_at)
    await queue_project_task(
        goal=f"Run raffle: {prize}",
        project_key=default_project_key_from_guild(ctx.guild),
        entails=f"Raffle message={msg.id}, winners={winners_count}, duration_min={duration_min}",
        source="discord_raffle",
    )
    await ctx.reply(f"Raffle created. message_id={msg.id}", mention_author=False)


@bot.command()
async def raffleclose(ctx, message_id: int):
    if not is_admin_ctx(ctx):
        return
    result = await close_raffle(ctx.channel, message_id, closed_by=f"user:{ctx.author.id}")
    await ctx.reply(f"Raffle close: {result.get('status','unknown')}", mention_author=False)


@bot.command()
async def trivia(ctx, mode: str = "start"):
    mode = (mode or "start").strip().lower()
    if mode in {"score", "scores", "leaderboard"}:
        if not ctx.guild:
            await ctx.reply("Leaderboard is guild-only.", mention_author=False)
            return
        rows = trivia_leaderboard(ctx.guild.id, 10)
        if not rows:
            await ctx.reply("No trivia scores yet. Start with `!trivia start`.", mention_author=False)
            return
        lines = ["Trivia leaderboard:"]
        for idx, row in enumerate(rows, start=1):
            lines.append(f"{idx}. {row.get('user_name') or row.get('user_id')} - {row.get('score',0)}")
        await ctx.reply("\n".join(lines), mention_author=False)
        return
    if not is_admin_ctx(ctx):
        return
    if ctx.channel.id in active_trivia:
        await ctx.reply("Trivia already active in this channel. Use `!answer <A|B|C|D>`.", mention_author=False)
        return
    card = random.choice(TRIVIA_BANK)
    active_trivia[ctx.channel.id] = {
        "question": card["q"],
        "options": card["options"],
        "answer": int(card["answer"]),
        "asked_at": int(time.time()),
        "asked_by": ctx.author.id,
        "answers": {},
    }
    lines = [f"Trivia: **{card['q']}**"]
    for i, opt in enumerate(card["options"]):
        lines.append(f"{TRIVIA_OPTIONS[i]}. {opt}")
    lines.append("Reply with `!answer <A|B|C|D>`")
    await ctx.send("\n".join(lines))


@bot.command()
async def answer(ctx, choice: str):
    state = active_trivia.get(ctx.channel.id)
    if not state:
        return
    pick = (choice or "").strip().upper()
    if pick not in TRIVIA_OPTIONS:
        await ctx.reply("Use one of: A, B, C, D", mention_author=False)
        return
    if ctx.author.id in state["answers"]:
        await ctx.reply("You already answered this question.", mention_author=False)
        return
    idx = TRIVIA_OPTIONS.index(pick)
    state["answers"][ctx.author.id] = idx
    if idx == state["answer"]:
        if ctx.guild:
            trivia_score_upsert(ctx.guild.id, ctx.author.id, getattr(ctx.author, "display_name", ctx.author.name))
            await queue_project_task(
                goal=f"Community trivia engagement: winner {getattr(ctx.author, 'display_name', ctx.author.name)}",
                project_key=default_project_key_from_guild(ctx.guild),
                entails=f"Question: {state['question']}",
                source="discord_trivia",
            )
        await ctx.send(f"Correct, {ctx.author.mention}. Answer: {state['options'][state['answer']]}")
        active_trivia.pop(ctx.channel.id, None)
    else:
        await ctx.reply("Not correct yet. Keep trying.", mention_author=False)


@bot.command()
async def feedback(ctx, *, text: str):
    content = (text or "").strip()
    if not content:
        return
    policy = await ensure_ctx_policy(ctx, command_key="feedback")
    ft = "general"
    lower = content.lower()
    if lower.startswith("bug:"):
        ft = "bug"
        content = content[4:].strip()
    elif lower.startswith("idea:"):
        ft = "idea"
        content = content[5:].strip()
    elif lower.startswith("application:"):
        ft = "application"
        content = content[12:].strip()
    row_id = feedback_store(
        guild_id=ctx.guild.id if ctx.guild else None,
        channel_id=ctx.channel.id if ctx.channel else None,
        user_id=ctx.author.id,
        user_name=getattr(ctx.author, "display_name", ctx.author.name),
        feedback_type=ft,
        content=content,
        meta={"channel": channel_label(ctx.message)},
    )
    if ctx.guild:
        await queue_project_task(
            goal=f"Review Discord feedback #{row_id}",
            project_key=normalize_project_key_local((policy or {}).get("project_key") or default_project_key_from_guild(ctx.guild)),
            entails=f"type={ft} from={getattr(ctx.author, 'display_name', ctx.author.name)}",
            source="discord_feedback",
        )
    if (policy or {}).get("community_guild"):
        receipt_kind = "feedback"
        if ft == "bug":
            receipt_kind = "bug_report"
        elif ft == "idea":
            receipt_kind = "feature_request"
        await upsert_discord_community_feedback_receipt_core(
            policy,
            receipt_kind=receipt_kind,
            summary=f"Community {ft} received: {compact_discord_text(content, 120)}",
            detail_text=content,
            message=ctx.message,
            status="open",
            meta={"feedback_row_id": row_id, "feedback_type": ft, "source": "feedback_command"},
        )
        record_runtime_breadcrumb(
            "community_feedback_receipt_created",
            severity="info",
            scene="discord",
            route="feedback",
            connection=runtime_connection_summary(
                surface=str((policy or {}).get("surface") or "public_channel"),
                project_key=str((policy or {}).get("project_key") or default_project_key_from_guild(ctx.guild)),
                guild_id=str(getattr(ctx.guild, "id", 0) or ""),
                channel_id=str(getattr(ctx.channel, "id", 0) or ""),
                route_status="mirrored",
            ),
            receipt_kind=receipt_kind,
            feedback_row_id=row_id,
        )
        await route_public_community_owner_notice(
            policy,
            event_key=receipt_kind,
            summary=f"Public {pretty_project_label(normalize_project_key_local((policy or {}).get('project_key') or default_project_key_from_guild(ctx.guild)))} {receipt_kind.replace('_', ' ')} receipt: {compact_discord_text(content, 160)}",
            requested_by=f"discord:{ctx.author.id}",
        )
    await ctx.reply(f"Feedback received. id={row_id} type={ft}", mention_author=False)


@bot.command()
async def communityannounce(ctx, *, spec: str):
    ok, reply = await publish_community_announcement(ctx, spec=spec, announcement_kind="announcement")
    await ctx.reply(compact_discord_text(reply, 1800), mention_author=False)


@bot.command()
async def patchnotes(ctx, *, spec: str):
    ok, reply = await publish_community_announcement(ctx, spec=spec, announcement_kind="patch_notes")
    await ctx.reply(compact_discord_text(reply, 1800), mention_author=False)


@bot.command()
async def feedbackreview(ctx, limit: int = 10):
    if not is_admin_ctx(ctx):
        return
    safe_limit = max(1, min(int(limit or 10), 20))
    policy = await ensure_ctx_policy(ctx, command_key="feedbackreview")
    project_key = normalize_project_key_local((policy or {}).get("project_key") or default_project_key_from_guild(ctx.guild))
    lines: list[str] = []
    if ctx.guild and policy_is_public_community(policy):
        rows = await list_discord_community_feedback_receipts_core(
            project_key=project_key,
            guild_id=str(ctx.guild.id),
            limit=safe_limit,
        )
        if not rows:
            await ctx.reply("No community receipts yet.", mention_author=False)
            return
        for row in rows:
            receipt_id = str(row.get("receipt_id") or "")[-8:]
            discord_name = str(row.get("discord_name") or row.get("discord_user_id") or "member")
            receipt_kind = str(row.get("receipt_kind") or "feedback").replace("_", " ")
            status = str(row.get("status") or "open")
            summary = compact_discord_text(str(row.get("summary") or row.get("detail_text") or ""), 140)
            lines.append(f"[{status}] {receipt_kind} {discord_name} ({receipt_id}): {summary}")
    else:
        rows = feedback_latest(safe_limit)
        if not rows:
            await ctx.reply("No feedback entries yet.", mention_author=False)
            return
        for row in rows:
            lines.append(
                f"#{row.get('id')} [{row.get('feedback_type')}] {row.get('user_name')}: {row.get('content')}"
            )
    out = "\n".join(lines)
    if len(out) > 1850:
        out = out[:1850] + "\n..."
    await ctx.reply(out, mention_author=False)


@bot.command()
async def interview(ctx, *, spec: str):
    if not is_admin_ctx(ctx) or not ctx.guild:
        return
    if not ctx.message.mentions:
        await ctx.reply("Usage: !interview @user | role | notes", mention_author=False)
        return
    candidate = ctx.message.mentions[0]
    raw = spec
    raw = raw.replace(f"<@{candidate.id}>", "").replace(f"<@!{candidate.id}>", "")
    parts = parse_pipe_parts(raw)
    role_name = parts[0] if parts else "General Contributor"
    notes = parts[1] if len(parts) > 1 else ""
    category = discord.utils.get(ctx.guild.categories, name="Interviews")
    if category is None:
        category = await ctx.guild.create_category("Interviews", reason="Hermes interview workflow")
    overwrites = {
        ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        candidate: discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True),
        ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True),
    }
    if bot.user:
        me = ctx.guild.get_member(bot.user.id)
        if me:
            overwrites[me] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, manage_messages=True)
    channel_name = safe_channel_name(f"interview-{candidate.display_name}")[:90]
    interview_channel = await ctx.guild.create_text_channel(channel_name, category=category, overwrites=overwrites, reason="Hermes interview workflow")
    await interview_channel.send(
        f"Interview opened for {candidate.mention}\nRole focus: **{role_name}**\n\n"
        "Interview prompts:\n"
        "1. Tell us your relevant experience.\n"
        "2. Why this project?\n"
        "3. Availability and commitment level?\n"
        "4. Show one example of problem-solving work."
    )
    interview_store_open(ctx.guild.id, interview_channel.id, candidate.id, candidate.display_name, ctx.author.id, role_name, notes)
    await queue_project_task(
        goal=f"Interview opened for {candidate.display_name}",
        project_key=default_project_key_from_guild(ctx.guild),
        entails=f"channel={interview_channel.name}, role={role_name}",
        source="discord_interview",
    )
    await ctx.reply(f"Interview channel created: {interview_channel.mention}", mention_author=False)


@bot.command()
async def interviewclose(ctx, *, decision: str = "pending"):
    if not is_admin_ctx(ctx) or not ctx.guild:
        return
    interview_store_close(ctx.channel.id, decision)
    await queue_project_task(
        goal=f"Interview closed in #{getattr(ctx.channel, 'name', 'unknown')}",
        project_key=default_project_key_from_guild(ctx.guild),
        entails=f"decision={decision}",
        source="discord_interview_close",
    )
    await ctx.reply(f"Interview marked closed with decision={decision}.", mention_author=False)


@bot.command()
async def onboard(ctx, *, spec: str):
    if not is_admin_ctx(ctx) or not ctx.guild:
        return
    if not ctx.message.mentions:
        await ctx.reply("Usage: !onboard @user | role | project_key", mention_author=False)
        return
    member = ctx.message.mentions[0]
    raw = spec.replace(f"<@{member.id}>", "").replace(f"<@!{member.id}>", "")
    parts = parse_pipe_parts(raw)
    role_name = parts[0] if parts else ""
    project_key = parts[1] if len(parts) > 1 else default_project_key_from_guild(ctx.guild)
    role_assigned = None
    if role_name:
        role = discord.utils.get(ctx.guild.roles, name=role_name)
        me = ctx.guild.me or ctx.guild.get_member(bot.user.id)
        if role and me and me.guild_permissions.manage_roles:
            try:
                await member.add_roles(role, reason="Hermes onboarding")
                role_assigned = role.name
            except Exception:
                role_assigned = None
    await ctx.send(
        f"Welcome {member.mention}. Onboarding started."
        + (f" Role assigned: {role_assigned}." if role_assigned else "")
        + " Please read #welcome and #roadmap first."
    )
    await queue_project_task(
        goal=f"Onboard member {member.display_name}",
        project_key=project_key,
        entails=f"discord_user={member.id}, role={role_assigned or role_name or 'none'}",
        source="discord_onboard",
    )
    await ctx.reply("Onboarding workflow created and linked to project tasks.", mention_author=False)


@bot.command()
async def recruitpost(ctx, *, spec: str):
    if not is_admin_ctx(ctx):
        return
    parts = parse_pipe_parts(spec)
    title = parts[0] if parts else "We're recruiting contributors"
    details = parts[1] if len(parts) > 1 else "Reply with `!feedback application: <your skills and availability>`."
    await ctx.send(f"**{title}**\n{details}\n\nApply with: `!feedback application: ...`")
    if ctx.guild:
        await queue_project_task(
            goal=f"Recruitment post published: {title}",
            project_key=default_project_key_from_guild(ctx.guild),
            entails=details,
            source="discord_recruit",
        )


@bot.command()
async def cmdmap(ctx):
    commands_map = [
        "projectplan", "projectapply", "projectsync", "websitecreate", "guildsync", "olympussync", "revampsync", "revamptest", "image", "logo", "paprsetup", "paprevents", "underfootsetup", "underfootevents",
        "underfootbridge", "underfootunity", "underfootdariusroute", "underfootdariussession", "underfootscreenshot", "incidentack", "incidentresolve",
        "poll", "pollclose", "raffle", "raffleclose", "trivia", "answer",
        "feedback", "feedbackreview", "interview", "interviewclose", "onboard", "recruitpost",
        "testerlink", "testertoken", "testerstatus", "testerlist", "testersummary",
        "testerdelete",
        "joinvc", "leavevc", "vcdiag", "vcclear", "voice", "discordmode", "welcome",
    ]
    await ctx.reply("Command map:\n" + ", ".join(sorted(commands_map)), mention_author=False)


@bot.command()
async def testerlink(ctx, *, spec: str):
    if not is_admin_ctx(ctx):
        return
    parts = parse_pipe_parts(spec)
    if len(parts) < 1:
        await ctx.reply("Usage: !testerlink <account_key|provider|display_name|project_key|tags_csv>", mention_author=False)
        return
    account_key = parts[0]
    provider = parts[1] if len(parts) > 1 else "discord"
    display_name = parts[2] if len(parts) > 2 else account_key
    project_key = parts[3] if len(parts) > 3 else default_project_key_from_guild(ctx.guild)
    tags = parse_csv_items(parts[4] if len(parts) > 4 else "tester,discord")
    try:
        data = await account_worker_post(
            "/tester_accounts/upsert",
            {
                "account_key": account_key,
                "provider": provider,
                "display_name": display_name,
                "project_key": project_key,
                "status": "active",
                "tags": tags,
                "scopes": ["discord:read", "discord:write"],
                "persona": {"origin": "discord-command", "managed_by": "hermes"},
                "note": f"Registered via Discord by {ctx.author.id}",
            },
        )
    except Exception as e:
        await ctx.reply(f"Tester link failed: {e}", mention_author=False)
        return
    await queue_project_task(
        goal=f"Register tester account {account_key}",
        project_key=project_key,
        entails=f"provider={provider}, tags={tags}",
        source="discord_tester_link",
    )
    await ctx.reply(
        f"Tester account linked: {data.get('account_key', account_key)} (provider={provider}, project={project_key})",
        mention_author=False,
    )


@bot.command()
async def testertoken(ctx, *, spec: str):
    if not is_owner(ctx):
        return
    if not is_dm(ctx.message):
        await ctx.reply(f"For security, run !testertoken in DM with {olympus_discord_name()}.", mention_author=False)
        return
    parts = parse_pipe_parts(spec)
    if len(parts) < 2:
        await ctx.reply("Usage: !testertoken <account_key|token|expires_min|scopes_csv>", mention_author=False)
        return
    account_key = parts[0]
    token_value = parts[1]
    expires_min = max(0, _safe_int(parts[2], 0)) if len(parts) > 2 else 0
    scopes = parse_csv_items(parts[3] if len(parts) > 3 else "discord:read,discord:write", max_items=80, max_len=120)
    expires_unix = int(time.time()) + (expires_min * 60) if expires_min > 0 else None
    try:
        data = await account_worker_post(
            "/tester_accounts/set_token",
            {
                "control_token": CONTROL_TOKEN,
                "account_key": account_key,
                "token": token_value,
                "expires_unix": expires_unix,
                "scopes": scopes,
                "note": f"Token set via DM by owner {ctx.author.id}",
            },
        )
    except Exception as e:
        await ctx.reply(f"Tester token set failed: {e}", mention_author=False)
        return
    await ctx.reply(
        f"Tester token stored for `{account_key}` (hint={data.get('token_hint', '***')}, expires_unix={data.get('expires_unix')}).",
        mention_author=False,
    )


@bot.command()
async def testerstatus(ctx, *, spec: str):
    if not is_admin_ctx(ctx):
        return
    parts = parse_pipe_parts(spec)
    if len(parts) < 2:
        await ctx.reply("Usage: !testerstatus <account_key|status|note>", mention_author=False)
        return
    account_key = parts[0]
    status = parts[1]
    note = parts[2] if len(parts) > 2 else ""
    try:
        data = await account_worker_post(
            "/tester_accounts/set_status",
            {"account_key": account_key, "status": status, "note": note},
        )
    except Exception as e:
        await ctx.reply(f"Tester status update failed: {e}", mention_author=False)
        return
    await queue_project_task(
        goal=f"Update tester account {account_key} status to {status}",
        project_key=default_project_key_from_guild(ctx.guild),
        entails=note or "status update",
        source="discord_tester_status",
    )
    await ctx.reply(f"Tester status updated: {data.get('account_key')} -> {data.get('updated_status')}", mention_author=False)


@bot.command()
async def testerdelete(ctx, *, spec: str):
    if not is_admin_ctx(ctx):
        return
    parts = parse_pipe_parts(spec)
    if len(parts) < 1:
        await ctx.reply("Usage: !testerdelete <account_key|note>", mention_author=False)
        return
    account_key = parts[0]
    note = parts[1] if len(parts) > 1 else ""
    try:
        data = await account_worker_post(
            "/tester_accounts/delete",
            {"account_key": account_key, "purge_tokens": True, "note": note},
        )
    except Exception as e:
        await ctx.reply(f"Tester delete failed: {e}", mention_author=False)
        return
    await queue_project_task(
        goal=f"Delete tester account {account_key}",
        project_key=default_project_key_from_guild(ctx.guild),
        entails=note or "tester account removed",
        source="discord_tester_delete",
    )
    await ctx.reply(
        f"Tester account deleted: {data.get('account_key')} (tokens purged={data.get('purged_tokens')}).",
        mention_author=False,
    )


@bot.command()
async def testerlist(ctx, *, spec: str = ""):
    if not is_admin_ctx(ctx):
        return
    parts = parse_pipe_parts(spec)
    provider = parts[0] if len(parts) > 0 else ""
    status = parts[1] if len(parts) > 1 else ""
    project_key = parts[2] if len(parts) > 2 else ""
    try:
        data = await account_worker_get(
            "/tester_accounts/list",
            {"provider": provider, "status": status, "project_key": project_key},
        )
    except Exception as e:
        await ctx.reply(f"Tester list failed: {e}", mention_author=False)
        return
    rows = data.get("rows", []) if isinstance(data, dict) else []
    if not rows:
        await ctx.reply("No tester accounts found for current filter.", mention_author=False)
        return
    lines = []
    for row in rows[:20]:
        lines.append(
            f"{row.get('account_key')} | {row.get('provider')} | {row.get('status')} | "
            f"token={'yes' if row.get('has_token') else 'no'} | project={row.get('project_key') or '-'}"
        )
    out = "\n".join(lines)
    if len(out) > 1800:
        out = out[:1800] + "\n..."
    await ctx.reply(out, mention_author=False)


@bot.command()
async def testersummary(ctx):
    if not is_admin_ctx(ctx):
        return
    try:
        data = await account_worker_get("/tester_accounts/summary", {})
    except Exception as e:
        await ctx.reply(f"Tester summary failed: {e}", mention_author=False)
        return
    totals = data.get("totals", {}) if isinstance(data, dict) else {}
    rows = data.get("rows", []) if isinstance(data, dict) else []
    lines = [
        "Tester account summary:",
        f"accounts={totals.get('accounts', 0)} with_token={totals.get('with_token', 0)} without_token={totals.get('without_token', 0)}",
    ]
    for row in rows[:16]:
        lines.append(
            f"{row.get('provider')}/{row.get('account_type')} [{row.get('status')}]: "
            f"{row.get('total')} (token={row.get('with_token')})"
        )
    out = "\n".join(lines)
    if len(out) > 1800:
        out = out[:1800] + "\n..."
    await ctx.reply(out, mention_author=False)


@bot.command()
async def joinvc(ctx):
    if not is_owner(ctx):
        return
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("Join a voice channel first.")
        return
    existing = guild_sessions.get(ctx.guild.id)
    if existing and existing.voice_client and existing.voice_client.is_connected():
        await ctx.send("Already connected to a voice channel.")
        return
    try:
        vc = await ctx.author.voice.channel.connect(cls=voice_recv.VoiceRecvClient, self_deaf=False, self_mute=False)
    except TypeError:
        vc = await ctx.author.voice.channel.connect(cls=voice_recv.VoiceRecvClient)
    session = GuildVoiceSession(ctx.guild, ctx.channel)
    session.voice_client = vc
    vc.listen(LiveTranscribeSink(session))
    session.flush_task = asyncio.create_task(session.start_flush_loop())
    guild_sessions[ctx.guild.id] = session
    await ctx.send(
        "Joined voice channel and started live transcription + voice reply mode. "
        f"(silence={UTTERANCE_SILENCE_SEC}s, min_bytes={MIN_PCM_BYTES}, max_buffer={MAX_BUFFER_SEC}s)"
    )


@bot.command()
async def leavevc(ctx):
    if not is_owner(ctx):
        return
    session = guild_sessions.get(ctx.guild.id)
    if session and session.voice_client and session.voice_client.is_connected():
        await session.voice_client.disconnect(force=True)
        guild_sessions.pop(ctx.guild.id, None)
    await ctx.send("Left voice channel.")


@bot.command()
async def vcdiag(ctx):
    if not is_owner(ctx):
        return
    session = guild_sessions.get(ctx.guild.id) if ctx.guild else None
    if not session:
        await ctx.reply(
            f"No active voice session. thresholds=silence:{UTTERANCE_SILENCE_SEC}s min_bytes:{MIN_PCM_BYTES} max_buffer:{MAX_BUFFER_SEC}s",
            mention_author=False,
        )
        return
    diag = session.diagnostics()
    preview = json.dumps(diag, indent=2)
    if len(preview) > 1800:
        preview = preview[:1800] + "\n..."
    await ctx.reply(f"```json\n{preview}\n```", mention_author=False)


@bot.command()
async def vcclear(ctx):
    if not is_owner(ctx):
        return
    session = guild_sessions.get(ctx.guild.id) if ctx.guild else None
    if not session:
        await ctx.reply("No active voice session.", mention_author=False)
        return
    for buf in session.buffers.values():
        buf.pcm.clear()
        buf.first_audio_ts = 0.0
        buf.processing = False
    await ctx.reply("Voice buffers cleared.", mention_author=False)


@bot.command()
async def voice(ctx, *, voice_name: str):
    if not is_owner(ctx):
        return
    if ctx.guild:
        guild_voice_prefs[ctx.guild.id] = voice_name
        await ctx.send(f"Voice set to {voice_name}")


if __name__ == "__main__":
    token = (os.getenv("DISCORD_TOKEN", "") or "").strip()
    if DISCORD_RUNTIME_ROLE == "disabled":
        print("[discord-runtime] role=disabled; Hermes will stay offline on this node.")
        sys.exit(0)
    if not token:
        print("[discord-runtime] DISCORD_TOKEN is missing; Hermes cannot start.")
        sys.exit(1)
    if len(CONTROL_TOKEN) < 32 or not CORE_URL:
        print("[discord-runtime] Configure CORE_URL and a unique CONTROL_TOKEN of at least 32 characters before starting.")
        sys.exit(1)
    bot.run(token)
