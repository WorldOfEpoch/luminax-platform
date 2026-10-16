from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import json
import hashlib
import ipaddress
import hmac
import logging
import os
import secrets
import time
import base64
import threading
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
neweden_router = None
if feature_enabled("UNDERFOOT_NEWEDEN_ENABLED"):
    try:
        from neweden_api import neweden_router
    except ImportError as exc:
        raise RuntimeError("neweden_companion_source_required") from exc

try:
    import mysql.connector
    from mysql.connector import Error as MySQLError
except Exception:
    mysql = None

    class MySQLError(Exception):
        pass


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not (os.environ.get(key, "") or "").strip():
            os.environ[key] = value


def resolve_project_path(value: str | None, fallback: Path) -> Path:
    if not value:
        return fallback

    candidate = Path(value)
    return candidate if candidate.is_absolute() else DEFAULT_PROJECT_ROOT / candidate


def getenv_first(*names: str, default: str = "") -> str:
    for name in names:
        value = (os.getenv(name, "") or "").strip()
        if value:
            return value
    return default


if runtime_enabled() and feature_enabled("UNDERFOOT_LOAD_DOTENV"):
    load_dotenv_file(DEFAULT_PROJECT_ROOT / ".env")
logging.basicConfig(level=os.getenv("UNDERFOOT_LOG_LEVEL", "INFO").upper())
LOGGER = logging.getLogger("underfoot.world")

PROJECT_ROOT = resolve_project_path(os.getenv("UNDERFOOT_PROJECT_ROOT"), DEFAULT_PROJECT_ROOT)
WEB_ROOT = PROJECT_ROOT / "client" / "web"
RUNTIME_ROOT = PROJECT_ROOT / "server" / "runtime"
STATE_PATH = RUNTIME_ROOT / "underfoot_state.json"
REPORTS_ROOT = RUNTIME_ROOT / "reports"
SHARED_CONTRACT_PATH = PROJECT_ROOT / "shared" / "world_contract.json"
FRONTEND_MANIFEST_PATH = resolve_project_path(os.getenv("UNDERFOOT_FRONTEND_MANIFEST_PATH"), PROJECT_ROOT / "unity" / "Underfoot" / "Assets" / "Data" / "FrontendManifest.json")
VOICE_POLICY_PATH = resolve_project_path(os.getenv("UNDERFOOT_VOICE_POLICY_PATH"), PROJECT_ROOT / "unity" / "Underfoot" / "Assets" / "Data" / "VoicePolicy.json")
AI_ART_PACK_PATH = resolve_project_path(
    os.getenv("UNDERFOOT_AI_ART_PACK_PATH"),
    PROJECT_ROOT / "client" / "web" / "assets" / "data" / "ai-art-pack.json",
)
DARIUS_PROJECT_MEMORY_PATH = resolve_project_path(
    os.getenv("UNDERFOOT_DARIUS_PROJECT_MEMORY_PATH"),
    PROJECT_ROOT / "shared" / "darius_project_memory.json",
)
WORLD_HOST = os.getenv("UNDERFOOT_WORLD_HOST", "underfoot-world:8460")
PROJECT_NAME = os.getenv("UNDERFOOT_PROJECT_NAME", "underfoot")
SESSION_PREFIX = os.getenv("UNDERFOOT_QUICK_MATCH_PREFIX", "underfoot")
ROOTREACH_MAP_ID = os.getenv("UNDERFOOT_MINIGAME_1_MAP_ID", "rootreach")
GLASSGARDEN_MAP_ID = os.getenv("UNDERFOOT_MINIGAME_2_MAP_ID", "glassgarden")
HOLLOWHEARTH_MAP_ID = os.getenv("UNDERFOOT_MINIGAME_3_MAP_ID", "hollowhearth")

UNDERFOOT_DB_ENABLED = runtime_enabled() and feature_enabled("UNDERFOOT_DB_ENABLED")
UNDERFOOT_DB_HOST = getenv_first("UNDERFOOT_DB_HOST", "MYSQL_HOST")
UNDERFOOT_DB_PORT = int(getenv_first("UNDERFOOT_DB_PORT", "MYSQL_PORT", default="3306"))
UNDERFOOT_DB_NAME = getenv_first("UNDERFOOT_DB_NAME", "MYSQL_DATABASE", default="underfoot")
UNDERFOOT_DB_USER = getenv_first("UNDERFOOT_DB_USER", "MYSQL_USER")
UNDERFOOT_DB_PASSWORD = getenv_first("UNDERFOOT_DB_PASSWORD", "MYSQL_PASSWORD")
PROJECT_MEMORY_WRITE_TOKEN = (os.getenv("UNDERFOOT_DARIUS_MEMORY_TOKEN", os.getenv("UNDERFOOT_LUMINAX_BRIDGE_TOKEN", "")) or "").strip()


def now_ts() -> int:
    return int(time.time())


def parse_port_range(raw_value: str, fallback_start: int, fallback_end: int) -> tuple[int, int]:
    cleaned = str(raw_value or "").strip()
    if not cleaned:
        return fallback_start, fallback_end

    if "-" in cleaned:
        start_raw, end_raw = cleaned.split("-", 1)
        start_port = int(start_raw.strip() or fallback_start)
        end_port = int(end_raw.strip() or start_port)
    else:
        start_port = int(cleaned)
        end_port = start_port

    if start_port <= 0 or end_port < start_port:
        return fallback_start, fallback_end
    return start_port, end_port


def is_private_or_loopback_host(host: str | None) -> bool:
    clean = str(host or "").strip().lower()
    if not clean:
        return True
    if clean in {"localhost", "0.0.0.0"}:
        return True

    try:
        ip = ipaddress.ip_address(clean)
        return ip.is_private or ip.is_loopback or ip.is_unspecified
    except ValueError:
        return clean.endswith(".local") or clean.endswith(".lan")


def normalize_unreal_warble_color(value: str | None) -> str:
    clean = str(value or "").strip().lower()
    if clean in {"red", "yellow"}:
        return clean
    return "blue"


def normalize_unreal_body_type(value: str | None) -> str:
    clean = str(value or "").strip().lower()
    if clean == "male":
        return "male"
    return "female"


def normalize_unreal_spawn_placement(value: str | None) -> str:
    clean = str(value or "").strip().lower()
    if clean in {"left", "center", "right"}:
        return clean
    return "auto"


def build_unreal_public_display_name(value: str | None) -> str:
    clean = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()
    if not clean:
        return "Player"
    max_len = 16
    if len(clean) <= max_len:
        return clean
    trimmed = clean[: max_len - 3].rstrip()
    return (trimmed or clean[: max_len - 3]) + "..."


def decode_json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    try:
        return json.loads(value)
    except Exception:
        return fallback


def read_optional_json(path: Path, fallback: Any) -> Any:
    try:
        if path.exists():
            # Handle both UTF-8 and UTF-8 BOM files exported by Unity tools.
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        LOGGER.exception("Failed reading JSON file: %s", path)
    return fallback


def encode_voice_token(payload: dict[str, Any]) -> str:
    if len(VOICE_JOIN_SECRET) < 32:
        raise HTTPException(status_code=503, detail="voice_join_secret_not_configured")
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(VOICE_JOIN_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    encoded = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    return f"{encoded}.{signature}"


def normalize_voice_role(value: str | None) -> str:
    clean = str(value or "").strip().lower()
    if clean == "biggun":
        return "biggun"
    return "warble"


def compute_voice_penalty_stage(silence_seconds: float, has_input: bool, device_lost: bool) -> tuple[str, str]:
    if has_input and not device_lost:
        return "clear", "clear"
    if silence_seconds >= VOICE_SILENCE_REVEAL_SECONDS:
        return "reveal", "siren_reveal"
    if silence_seconds >= VOICE_SILENCE_WARNING_SECONDS:
        return "warning_cry", "warning_cry"
    if silence_seconds >= VOICE_SILENCE_GRACE_SECONDS:
        return "grace", "quiet_whimper"
    return "clear", "clear"


def build_turn_credential(username: str) -> str:
    if len(VOICE_TURN_SHARED_SECRET) < 32:
        raise HTTPException(status_code=503, detail="turn_secret_not_configured")
    digest = hmac.new(VOICE_TURN_SHARED_SECRET.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def build_turn_servers(player_id: str, match_id: str) -> list[dict[str, Any]]:
    if not VOICE_TURN_HOST or len(VOICE_TURN_SHARED_SECRET) < 32:
        return []
    expires_at = now_ts() + max(300, VOICE_TURN_TTL_SECONDS)
    username = f"{expires_at}:{(player_id or 'warble').strip()[:48]}:{(match_id or 'match').strip()[:48]}"
    credential = build_turn_credential(username)
    servers = [
        {
            "url": f"turn:{VOICE_TURN_HOST}:{VOICE_TURN_PORT}?transport=udp",
            "username": username,
            "credential": credential,
            "credentialType": "password",
            "isTls": False,
        },
        {
            "url": f"turn:{VOICE_TURN_HOST}:{VOICE_TURN_PORT}?transport=tcp",
            "username": username,
            "credential": credential,
            "credentialType": "password",
            "isTls": False,
        },
    ]
    if VOICE_TURN_TLS_PORT > 0:
        servers.append(
            {
                "url": f"turns:{VOICE_TURN_HOST}:{VOICE_TURN_TLS_PORT}?transport=tcp",
                "username": username,
                "credential": credential,
                "credentialType": "password",
                "isTls": True,
            }
        )
    return servers


def build_voice_bank_descriptors() -> list[dict[str, Any]]:
    banks = VOICE_POLICY.get("voiceBanks", [])
    if isinstance(banks, list) and banks:
        clean_banks: list[dict[str, Any]] = []
        for bank in banks:
            if not isinstance(bank, dict):
                continue
            clean_banks.append(
                {
                    "key": str(bank.get("key", "")).strip(),
                    "version": str(bank.get("version", "v1")).strip() or "v1",
                    "bankRole": str(bank.get("bankRole", "mixed")).strip() or "mixed",
                    "summary": str(bank.get("summary", "")).strip(),
                    "syllables": [str(item).strip() for item in bank.get("syllables", []) if str(item).strip()],
                }
            )
        if clean_banks:
            return clean_banks
    return [
        {
            "key": "warble_squeak_v1",
            "version": "v1",
            "bankRole": "warble",
            "summary": "Cute squeaks and chipper warble fake-language syllables for cross-role playback.",
            "syllables": ["chi", "wib", "pip", "twee", "mip", "bibi"],
        },
        {
            "key": "biggun_gutturals_v1",
            "version": "v1",
            "bankRole": "biggun",
            "summary": "Heavy giant fake-language syllables for echoing cross-role playback.",
            "syllables": ["grum", "thok", "orr", "vak", "dru", "mok"],
        },
    ]


def resolve_voice_runtime_mode(runtime: dict[str, Any] | None = None) -> str:
    runtime = runtime or {}
    listing_type = str(runtime.get("listing_type", "quickplay")).strip().lower()
    map_id = str(runtime.get("map_id", "")).strip().lower()
    if listing_type == "private_room" or map_id in {"burrowhub", "burrow_hub", "hub"}:
        return "hub_soft"
    if listing_type == "custom_hosted" and bool(runtime.get("custom_cross_role_intelligible", VOICE_CUSTOM_CROSS_ROLE_INTELLIGIBLE_ALLOWED)):
        return "custom_intelligible_filtered"
    return "match_strict_illusion"


def resolve_voice_cross_role_mode(runtime: dict[str, Any] | None = None) -> str:
    mode = resolve_voice_runtime_mode(runtime)
    if mode == "custom_intelligible_filtered":
        return "intelligible_filtered"
    return "contour_only"


def build_voice_config_payload(match_id: str, runtime: dict[str, Any] | None = None, player_id: str | None = None) -> dict[str, Any]:
    runtime = runtime or {}
    mode = resolve_voice_runtime_mode(runtime)
    cross_role_mode = resolve_voice_cross_role_mode(runtime)
    turn_servers = build_turn_servers(player_id or "listener", match_id)
    return {
        "matchId": match_id,
        "gatewayHttpUrl": VOICE_GATEWAY_PUBLIC_HTTP_URL or VOICE_GATEWAY_HTTP_URL,
        "gatewayWebSocketUrl": VOICE_GATEWAY_PUBLIC_WS_URL or VOICE_GATEWAY_WS_URL,
        "openMic": True,
        "proximityVoiceOnly": True,
        "enforcementMode": "soft_test" if mode == "hub_soft" else VOICE_ENFORCEMENT_MODE,
        "hubEnforcementMode": VOICE_HUB_ENFORCEMENT_MODE,
        "transportMode": VOICE_TRANSPORT_MODE,
        "mode": mode,
        "crossRoleMode": cross_role_mode,
        "textChatEnabled": False,
        "teamVoiceEnabled": False,
        "recordingEnabled": False,
        "tokenTtlSeconds": VOICE_JOIN_TTL_SECONDS,
        "silenceGraceSeconds": VOICE_SILENCE_GRACE_SECONDS,
        "silenceWarningSeconds": VOICE_SILENCE_WARNING_SECONDS,
        "silenceRevealSeconds": VOICE_SILENCE_REVEAL_SECONDS,
        "opusBitrateKbps": VOICE_BITRATE_KBPS,
        "contourHz": VOICE_CONTOUR_HZ,
        "qualityPreset": VOICE_QUALITY_PRESET,
        "fakeVoiceBankKey": VOICE_FAKE_VOICE_BANK_KEY,
        "customCrossRoleIntelligibleAllowed": VOICE_CUSTOM_CROSS_ROLE_INTELLIGIBLE_ALLOWED,
        "turnServers": turn_servers,
        "voiceBanks": build_voice_bank_descriptors(),
        "profiles": VOICE_POLICY.get("profiles", {}),
        "occlusion": VOICE_POLICY.get("occlusion", {}),
        "spatial": VOICE_POLICY.get("spatial", {}),
    }


CONTRACT = read_optional_json(SHARED_CONTRACT_PATH, {}) if runtime_enabled() else {}
FRONTEND_MANIFEST = read_optional_json(FRONTEND_MANIFEST_PATH, {}) if runtime_enabled() else {}
VOICE_POLICY = read_optional_json(VOICE_POLICY_PATH, {}) if runtime_enabled() else {}
AI_ART_PACK = read_optional_json(AI_ART_PACK_PATH, {}) if runtime_enabled() else {}
PROJECT_MEMORY_SEED = read_optional_json(DARIUS_PROJECT_MEMORY_PATH, {}) if runtime_enabled() else {}
VOICE_TRANSPORT_MODE = str(VOICE_POLICY.get("transportMode", "unity_webrtc_sfu")).strip() or "unity_webrtc_sfu"
VOICE_GATEWAY_HTTP_URL = (os.getenv("UNDERFOOT_VOICE_GATEWAY_HTTP_URL", str(VOICE_POLICY.get("gatewayHttpUrl", "http://underfoot-voice-gateway:8470"))).strip() or "http://underfoot-voice-gateway:8470").rstrip("/")
VOICE_GATEWAY_PUBLIC_HTTP_URL = os.getenv("UNDERFOOT_VOICE_PUBLIC_HTTP_URL", "").strip().rstrip("/")
VOICE_GATEWAY_WS_URL = (os.getenv("UNDERFOOT_VOICE_GATEWAY_WS_URL", str(VOICE_POLICY.get("gatewayWebSocketUrl", "ws://underfoot-voice-gateway:8470/ws/signaling"))).strip() or "ws://underfoot-voice-gateway:8470/ws/signaling").rstrip("/")
VOICE_GATEWAY_PUBLIC_WS_URL = os.getenv("UNDERFOOT_VOICE_PUBLIC_WS_URL", "").strip().rstrip("/")
VOICE_JOIN_SECRET = os.getenv("UNDERFOOT_VOICE_JOIN_SECRET", "")
VOICE_JOIN_TTL_SECONDS = int(os.getenv("UNDERFOOT_VOICE_JOIN_TTL_SECONDS", str(VOICE_POLICY.get("tokenTtlSeconds", 180) or 180)))
VOICE_BITRATE_KBPS = int(os.getenv("UNDERFOOT_VOICE_BITRATE_KBPS", str(VOICE_POLICY.get("opusBitrateKbps", 40) or 40)))
VOICE_QUALITY_PRESET = str(os.getenv("UNDERFOOT_VOICE_QUALITY_PRESET", str(VOICE_POLICY.get("qualityPreset", "strict_public")))).strip() or "strict_public"
VOICE_ENFORCEMENT_MODE = str(os.getenv("UNDERFOOT_VOICE_ENFORCEMENT_MODE", str(VOICE_POLICY.get("enforcementMode", "strict")))).strip().lower() or "strict"
VOICE_HUB_ENFORCEMENT_MODE = str(os.getenv("UNDERFOOT_VOICE_HUB_ENFORCEMENT_MODE", str((VOICE_POLICY.get("hub") or {}).get("enforcementMode", "soft_test")))).strip().lower() or "soft_test"
VOICE_CONTOUR_HZ = int(os.getenv("UNDERFOOT_VOICE_CONTOUR_HZ", str(VOICE_POLICY.get("contourHz", 20) or 20)))
VOICE_FAKE_VOICE_BANK_KEY = str(os.getenv("UNDERFOOT_VOICE_FAKE_BANK_KEY", str(VOICE_POLICY.get("fakeVoiceBankKey", "warble_squeak_v1")))).strip() or "warble_squeak_v1"
VOICE_CUSTOM_CROSS_ROLE_INTELLIGIBLE_ALLOWED = str(os.getenv("UNDERFOOT_VOICE_CUSTOM_CROSS_ROLE_INTELLIGIBLE_ALLOWED", str(VOICE_POLICY.get("customCrossRoleIntelligibleAllowed", True)))).strip().lower() in {"1", "true", "yes", "on"}
VOICE_TURN_HOST = os.getenv("UNDERFOOT_VOICE_TURN_HOST", "").strip()
VOICE_TURN_PORT = int(os.getenv("UNDERFOOT_VOICE_TURN_PORT", str(VOICE_POLICY.get("turnPort", 3478) or 3478)))
VOICE_TURN_TLS_PORT = int(os.getenv("UNDERFOOT_VOICE_TURN_TLS_PORT", str(VOICE_POLICY.get("turnTlsPort", 5349) or 5349)))
VOICE_TURN_SHARED_SECRET = os.getenv("UNDERFOOT_VOICE_TURN_SHARED_SECRET", "")
VOICE_TURN_TTL_SECONDS = int(os.getenv("UNDERFOOT_VOICE_TURN_TTL_SECONDS", str(VOICE_POLICY.get("turnCredentialTtlSeconds", 900) or 900)))
VOICE_SILENCE = VOICE_POLICY.get("silence", {}) if isinstance(VOICE_POLICY.get("silence"), dict) else {}
VOICE_SILENCE_GRACE_SECONDS = float(os.getenv("UNDERFOOT_VOICE_SILENCE_GRACE_SECONDS", str(VOICE_SILENCE.get("graceSeconds", 4.0) or 4.0)))
VOICE_SILENCE_WARNING_SECONDS = float(os.getenv("UNDERFOOT_VOICE_SILENCE_WARNING_SECONDS", str(VOICE_SILENCE.get("warningCrySeconds", 8.0) or 8.0)))
VOICE_SILENCE_REVEAL_SECONDS = float(os.getenv("UNDERFOOT_VOICE_SILENCE_REVEAL_SECONDS", str(VOICE_SILENCE.get("revealSeconds", 14.0) or 14.0)))
PUBLIC_ADMIN_FILL_ENABLED = os.getenv("UNDERFOOT_PUBLIC_ADMIN_FILL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
OWNER_QUEUE_FILL_LOGIN = os.getenv("UNDERFOOT_OWNER_QUEUE_FILL_LOGIN", "").strip().lower()
MATCH_SPAWN_SELECTION_SECONDS = int(os.getenv("UNDERFOOT_MATCH_SPAWN_SELECTION_SECONDS", "45"))
MATCH_SHROOM_RITUAL_SECONDS = int(os.getenv("UNDERFOOT_MATCH_SHROOM_RITUAL_SECONDS", "90"))
MATCH_SCOUT_SECONDS = int(os.getenv("UNDERFOOT_MATCH_SCOUT_SECONDS", "180"))
QUICKPLAY_AUTO_START_SECONDS = int(os.getenv("UNDERFOOT_QUICKPLAY_AUTO_START_SECONDS", "0"))
QUICKPLAY_SPAWN_SELECTION_SECONDS = int(os.getenv("UNDERFOOT_QUICKPLAY_SPAWN_SELECTION_SECONDS", "30"))
QUICKPLAY_SHROOM_RITUAL_SECONDS = int(os.getenv("UNDERFOOT_QUICKPLAY_SHROOM_RITUAL_SECONDS", "60"))
QUICKPLAY_SCOUT_SECONDS = int(os.getenv("UNDERFOOT_QUICKPLAY_SCOUT_SECONDS", "60"))
TEAM_LOBBY_COUNTDOWN_SECONDS = int(os.getenv("UNDERFOOT_TEAM_LOBBY_COUNTDOWN_SECONDS", "10"))
DEDICATED_ALLOW_DEV_FALLBACK = os.getenv("UNDERFOOT_DEDICATED_ALLOW_DEV_FALLBACK", "false").strip().lower() in {"1", "true", "yes", "on"}
NETWORK_PRIMARY_BACKEND = (os.getenv("UNDERFOOT_NETWORK_PRIMARY_BACKEND", "ngo") or "ngo").strip().lower() or "ngo"
NETWORK_FALLBACK_BACKEND = (os.getenv("UNDERFOOT_NETWORK_FALLBACK_BACKEND", "photon") or "photon").strip().lower() or "photon"
NETWORK_FALLBACK_ALLOWED = os.getenv("UNDERFOOT_NETWORK_FALLBACK_ALLOWED", "true").strip().lower() in {"1", "true", "yes", "on"}
PLACEHOLDER_PRESENTATION_ENABLED = os.getenv("UNDERFOOT_PLACEHOLDER_PRESENTATION_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
DISABLE_TEAM_TINTING = os.getenv("UNDERFOOT_DISABLE_TEAM_TINTING", "true").strip().lower() in {"1", "true", "yes", "on"}
WARBLE_SPEED_SCALE = float(os.getenv("UNDERFOOT_WARBLE_SPEED_SCALE", "0.75"))
SPAWN_SEPARATION_METERS = float(os.getenv("UNDERFOOT_SPAWN_SEPARATION_METERS", "2.4"))
STABILIZATION_PHASE_LABEL = (os.getenv("UNDERFOOT_STABILIZATION_PHASE_LABEL", "phase0_runtime_recovery") or "phase0_runtime_recovery").strip()
DEDICATED_DEFAULT_PUBLIC_HOST = (os.getenv("UNDERFOOT_DEDICATED_DEFAULT_PUBLIC_HOST", "127.0.0.1") or "127.0.0.1").strip()
DEDICATED_DEFAULT_MATCH_PORT = int(os.getenv("UNDERFOOT_DEDICATED_DEFAULT_MATCH_PORT", "7777"))
DEDICATED_DEFAULT_ROOM_PORT = int(os.getenv("UNDERFOOT_DEDICATED_DEFAULT_ROOM_PORT", "7778"))
DEDICATED_DEFAULT_MAX_ASSIGNMENTS = int(os.getenv("UNDERFOOT_DEDICATED_DEFAULT_MAX_ASSIGNMENTS", "12"))
DEDICATED_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("UNDERFOOT_DEDICATED_HEARTBEAT_TIMEOUT_SECONDS", "45"))
DEDICATED_ALLOCATOR_URL = (os.getenv("UNDERFOOT_ALLOCATOR_URL", "http://underfoot-match-allocator:8485") or "http://underfoot-match-allocator:8485").strip().rstrip("/")
DEDICATED_ALLOCATOR_TIMEOUT_SECONDS = float(os.getenv("UNDERFOOT_ALLOCATOR_TIMEOUT_SECONDS", "120"))
DEDICATED_ALLOCATOR_BOOT_WAIT_SECONDS = float(os.getenv("UNDERFOOT_ALLOCATOR_BOOT_WAIT_SECONDS", "120"))
DEDICATED_RECONNECT_GRACE_SECONDS = int(os.getenv("UNDERFOOT_DEDICATED_RECONNECT_GRACE_SECONDS", "300"))
DEDICATED_ROOM_IDLE_TIMEOUT_SECONDS = int(os.getenv("UNDERFOOT_DEDICATED_ROOM_IDLE_TIMEOUT_SECONDS", "300"))
DEDICATED_SETUP_IDLE_TIMEOUT_SECONDS = int(os.getenv("UNDERFOOT_DEDICATED_SETUP_IDLE_TIMEOUT_SECONDS", "900"))
DEDICATED_EMPTY_SETUP_IDLE_TIMEOUT_SECONDS = int(os.getenv("UNDERFOOT_DEDICATED_EMPTY_SETUP_IDLE_TIMEOUT_SECONDS", "120"))
DEDICATED_COMBAT_IDLE_TIMEOUT_SECONDS = int(os.getenv("UNDERFOOT_DEDICATED_COMBAT_IDLE_TIMEOUT_SECONDS", "300"))
DEDICATED_ALLOCATOR_MATCH_PORT_RANGE = (os.getenv("UNDERFOOT_ALLOCATOR_MATCH_PORT_RANGE", "27000-27063") or "27000-27063").strip()
DEDICATED_ALLOCATOR_ROOM_PORT_RANGE = (os.getenv("UNDERFOOT_ALLOCATOR_ROOM_PORT_RANGE", "27100-27163") or "27100-27163").strip()
DEDICATED_COMBAT_SCENE_KEY = (os.getenv("UNDERFOOT_DEDICATED_COMBAT_SCENE_KEY", "PotionHutPanic") or "PotionHutPanic").strip()
DEDICATED_ROOM_SCENE_KEY = (os.getenv("UNDERFOOT_DEDICATED_ROOM_SCENE_KEY", "BurrowHub") or "BurrowHub").strip()
DEDICATED_COMBAT_MODE_KEY = (os.getenv("UNDERFOOT_DEDICATED_COMBAT_MODE_KEY", "underfoot_full_test") or "underfoot_full_test").strip()
DEDICATED_ROOM_MODE_KEY = (os.getenv("UNDERFOOT_DEDICATED_ROOM_MODE_KEY", "underfoot_social_room") or "underfoot_social_room").strip()
MATCH_PUBLIC_PORT_RANGE = parse_port_range(DEDICATED_ALLOCATOR_MATCH_PORT_RANGE, 27000, 27063)
ROOM_PUBLIC_PORT_RANGE = parse_port_range(DEDICATED_ALLOCATOR_ROOM_PORT_RANGE, 27100, 27163)
SKILL_HIDE_IN_GRASS_COOLDOWN_SECONDS = int(os.getenv("UNDERFOOT_SKILL_HIDE_IN_GRASS_COOLDOWN_SECONDS", "40"))
SKILL_HOLE_DASH_COOLDOWN_SECONDS = int(os.getenv("UNDERFOOT_SKILL_HOLE_DASH_COOLDOWN_SECONDS", "25"))
SKILL_TINY_CARRY_BOOST_COOLDOWN_SECONDS = int(os.getenv("UNDERFOOT_SKILL_TINY_CARRY_BOOST_COOLDOWN_SECONDS", "35"))
SKILL_SHROOM_RITUAL_BOOST_COOLDOWN_SECONDS = int(os.getenv("UNDERFOOT_SKILL_SHROOM_RITUAL_BOOST_COOLDOWN_SECONDS", "50"))
SKILL_SOUND_BAIT_COOLDOWN_SECONDS = int(os.getenv("UNDERFOOT_SKILL_SOUND_BAIT_COOLDOWN_SECONDS", "30"))
SKILL_HIDE_IN_GRASS_DURATION_SECONDS = int(os.getenv("UNDERFOOT_SKILL_HIDE_IN_GRASS_DURATION_SECONDS", "12"))
SKILL_TINY_CARRY_BOOST_DURATION_SECONDS = int(os.getenv("UNDERFOOT_SKILL_TINY_CARRY_BOOST_DURATION_SECONDS", "15"))
BIGGUN_PLACEMENT_DURATION_SECONDS = int(os.getenv("UNDERFOOT_BIGGUN_PLACEMENT_DURATION_SECONDS", "240"))
AI_THREAT_DURATION_SECONDS = int(os.getenv("UNDERFOOT_AI_THREAT_DURATION_SECONDS", "180"))
REPORT_RECENT_LIMIT = int(os.getenv("UNDERFOOT_REPORT_RECENT_LIMIT", "50"))


def build_stabilization_profile_payload() -> dict[str, Any]:
    return {
        "phaseLabel": STABILIZATION_PHASE_LABEL,
        "preferredBackend": NETWORK_PRIMARY_BACKEND,
        "fallbackBackend": NETWORK_FALLBACK_BACKEND,
        "fallbackAllowed": NETWORK_FALLBACK_ALLOWED,
        "presentationLane": "placeholder_humanoid" if PLACEHOLDER_PRESENTATION_ENABLED else "final_art",
        "teamTintingDisabled": DISABLE_TEAM_TINTING,
        "warbleSpeedScale": WARBLE_SPEED_SCALE,
        "spawnSeparationMeters": SPAWN_SEPARATION_METERS,
    }


def build_network_decision_gate_payload(runtime_compatibility_state: str = "monitoring") -> dict[str, Any]:
    compatibility = (runtime_compatibility_state or "monitoring").strip() or "monitoring"
    presentation_lane = "placeholder_humanoid" if PLACEHOLDER_PRESENTATION_ENABLED else "final_art"
    return {
        "activeBackend": NETWORK_PRIMARY_BACKEND,
        "preferredBackend": NETWORK_PRIMARY_BACKEND,
        "fallbackBackend": NETWORK_FALLBACK_BACKEND,
        "fallbackAllowed": NETWORK_FALLBACK_ALLOWED,
        "joinBodyHandoffStatus": "stabilizing",
        "countdownStartStatus": "stabilizing",
        "matchReconnectStatus": "stabilizing",
        "replicationStatus": "monitoring",
        "runtimeCompatibilityStatus": compatibility,
        "summary": (
            f"active={NETWORK_PRIMARY_BACKEND}"
            f"|fallback={NETWORK_FALLBACK_BACKEND}"
            f"|fallbackAllowed={'yes' if NETWORK_FALLBACK_ALLOWED else 'no'}"
            f"|presentation={presentation_lane}"
            f"|teamTinting={'disabled' if DISABLE_TEAM_TINTING else 'authored'}"
            f"|warbleSpeedScale={WARBLE_SPEED_SCALE:.2f}"
            f"|spawnSeparation={SPAWN_SEPARATION_METERS:.2f}"
            f"|compatibility={compatibility}"
        ),
    }


DEFAULT_MAPS: list[dict[str, Any]] = [
    {
        "map_id": "blackpot_hollow",
        "title": "Blackpot Hollow",
        "biome": "forest + tunnels + giant hut",
        "summary": "A dark woodland wrapped around a potion hut, root tunnels, hidden wall paths, and Warble fast-travel holes.",
        "hazards": ["boots", "cats", "braziers", "rolling junk", "spider silk"],
        "quest_hooks": ["glowcap runs", "cage breaks", "gong bait", "pot stalls"],
        "spawns": [
            {"id": "burrow_hole", "label": "Burrow Hole", "type": "tunnel"},
            {"id": "forest_hole_north", "label": "North Forest Hole", "type": "forest hole"},
            {"id": "forest_hole_east", "label": "East Forest Hole", "type": "forest hole"},
            {"id": "hut_wall_hole", "label": "Hut Wall Hole", "type": "wall hole"},
            {"id": "tunnel_mouth_south", "label": "South Tunnel Mouth", "type": "tunnel mouth"},
        ],
    },
    {
        "map_id": ROOTREACH_MAP_ID,
        "title": "Rootreach Wilds",
        "biome": "forest floor",
        "summary": "A giant mossy woodland where potion carts grind across root bridges and snakes slide through the canopy roots.",
        "hazards": ["boots", "snakes", "spider silk", "axe swings"],
        "quest_hooks": ["rescue cages", "sap siphons", "mushroom clusters"],
        "spawns": [
            {"id": "root-arch", "label": "Root Arch Burrow", "type": "root tunnel"},
            {"id": "stone-crack", "label": "Stone Crack Exit", "type": "wall crack"},
            {"id": "stump-door", "label": "Stump Door", "type": "tree stump"},
        ],
    },
    {
        "map_id": GLASSGARDEN_MAP_ID,
        "title": "Glassgarden Pantry",
        "biome": "kitchen greenhouse",
        "summary": "Warm glass, bright herbs, boiling kettles, and predatory house creatures make this the fast-risk harvest route.",
        "hazards": ["burners", "steam", "cats", "knives"],
        "quest_hooks": ["herb theft", "cork caches", "lantern seeds"],
        "spawns": [
            {"id": "terracotta-hole", "label": "Terracotta Pot Hole", "type": "pot exit"},
            {"id": "wall-vent", "label": "Wall Vent Grate", "type": "vent"},
            {"id": "drain-rim", "label": "Drain Rim Tunnel", "type": "drain"},
        ],
    },
    {
        "map_id": HOLLOWHEARTH_MAP_ID,
        "title": "Hollowhearth Warren",
        "biome": "underground forge burrow",
        "summary": "The resistance camp lies here under lantern fungus and ember pits, with rescue quests, vendors, and fast return holes.",
        "hazards": ["ember bursts", "unstable carts", "shock roots"],
        "quest_hooks": ["quest board", "rescue triage", "upgrade benches"],
        "spawns": [
            {"id": "lantern-pit", "label": "Lantern Pit Hatch", "type": "burrow"},
            {"id": "forge-door", "label": "Forge Door Gap", "type": "wall crack"},
            {"id": "vine-drop", "label": "Vine Drop", "type": "tree root"},
        ],
    },
]


def _normalize_pack_maps(pack: Any) -> list[dict[str, Any]]:
    if not isinstance(pack, dict):
        return []
    raw_maps = pack.get("maps")
    if not isinstance(raw_maps, list):
        return []
    normalized: list[dict[str, Any]] = []
    for raw in raw_maps:
        if not isinstance(raw, dict):
            continue
        map_id = str(raw.get("map_id", "")).strip()
        title = str(raw.get("title", "")).strip()
        if not map_id or not title:
            continue
        spawns_raw = raw.get("spawns")
        spawns: list[dict[str, str]] = []
        if isinstance(spawns_raw, list):
            for spawn in spawns_raw:
                if not isinstance(spawn, dict):
                    continue
                spawn_id = str(spawn.get("id", "")).strip()
                spawn_label = str(spawn.get("label", "")).strip()
                spawn_type = str(spawn.get("type", "")).strip()
                if spawn_id and spawn_label:
                    spawns.append({"id": spawn_id, "label": spawn_label, "type": spawn_type or "burrow"})
        if not spawns:
            spawns = [{"id": "default", "label": "Default Burrow", "type": "burrow"}]

        row: dict[str, Any] = {
            "map_id": map_id,
            "title": title,
            "biome": str(raw.get("biome", "unknown")).strip() or "unknown",
            "summary": str(raw.get("summary", "")).strip(),
            "hazards": raw.get("hazards") if isinstance(raw.get("hazards"), list) else [],
            "quest_hooks": raw.get("quest_hooks") if isinstance(raw.get("quest_hooks"), list) else [],
            "spawns": spawns,
            "threat_level": str(raw.get("threat_level", "medium")).strip() or "medium",
            "route_links": raw.get("route_links") if isinstance(raw.get("route_links"), list) else [],
            "poi": raw.get("poi") if isinstance(raw.get("poi"), list) else [],
            "prompt": str(raw.get("prompt", "")).strip(),
            "palette": raw.get("palette") if isinstance(raw.get("palette"), list) else [],
            "art": raw.get("art") if isinstance(raw.get("art"), dict) else {},
        }
        normalized.append(row)
    return normalized


def _normalize_pack_routes(pack: Any) -> list[dict[str, Any]]:
    if not isinstance(pack, dict):
        return []
    routes = pack.get("routes")
    if not isinstance(routes, list):
        return []
    out: list[dict[str, Any]] = []
    for row in routes:
        if not isinstance(row, dict):
            continue
        from_id = str(row.get("from", "")).strip()
        to_id = str(row.get("to", "")).strip()
        if not from_id or not to_id:
            continue
        out.append(
            {
                "id": str(row.get("id", f"{from_id}_{to_id}")).strip(),
                "from": from_id,
                "to": to_id,
                "path_type": str(row.get("path_type", "route")).strip(),
                "distance": str(row.get("distance", "medium")).strip(),
                "risk": str(row.get("risk", "medium")).strip(),
                "notes": str(row.get("notes", "")).strip(),
            }
        )
    return out


MAPS: list[dict[str, Any]] = _normalize_pack_maps(AI_ART_PACK) or DEFAULT_MAPS
MAP_ROUTES: list[dict[str, Any]] = _normalize_pack_routes(AI_ART_PACK)

DEFAULT_MATCH_HOLES: dict[str, list[dict[str, Any]]] = {
    "blackpot_hollow": [
        {"hole_id": "burrow_hole", "label": "Burrow Hole", "type": "team_burrow", "is_safe": True, "team_id": 0},
        {"hole_id": "forest_hole_north", "label": "North Forest Hole", "type": "neutral_hole", "is_safe": True, "team_id": -1},
        {"hole_id": "forest_hole_east", "label": "East Forest Hole", "type": "neutral_hole", "is_safe": True, "team_id": -1},
        {"hole_id": "hut_wall_hole", "label": "Hut Wall Hole", "type": "neutral_hole", "is_safe": False, "team_id": -1},
        {"hole_id": "tunnel_mouth_south", "label": "South Tunnel Mouth", "type": "neutral_hole", "is_safe": True, "team_id": -1},
    ],
}

DEFAULT_FACTIONS: list[dict[str, str]] = [
    {"key": "blue", "label": "Blue Burrow", "summary": "River-route hoarders with cool tempers and tight tunnel discipline."},
    {"key": "yellow", "label": "Yellow Burrow", "summary": "Fast gatherers who treat every mission like a sprint race."},
    {"key": "red", "label": "Red Burrow", "summary": "Raid-first hotheads who rush rival shrooms and love open chaos."},
]

LIVE_FACTION_KEYS = ("red", "yellow", "blue")
LIVE_TEAM_FACTION_ORDER = ("red", "yellow", "blue")

DEFAULT_HAIR_STYLES: list[dict[str, str]] = [
    {"key": "wavy_crown", "label": "Wavy Crown", "summary": "Soft rolling waves for the core Warble silhouette."},
    {"key": "moss_swoop", "label": "Moss Swoop", "summary": "A drooping swoop that bounces like a forest cub's fringe."},
    {"key": "burrow_tuft", "label": "Burrow Tuft", "summary": "Short upright curls with a tiny, scrappy energy."},
    {"key": "lantern_mop", "label": "Lantern Mop", "summary": "A fluffy mop that shakes during waddly sprint cycles."},
]

DEFAULT_HAIR_TINTS: list[str] = ["cream", "chestnut", "midnight", "moss", "copper", "violet"]

DEFAULT_FEATURED_MISSIONS: list[dict[str, Any]] = [
    {"mission_id": "glowcap_run", "label": "Glowcap Run", "summary": "Steal glowcaps from the Blackpot Hut and bring them home.", "objective_kind": "resource_run", "reward_points": 120},
    {"mission_id": "cage_break", "label": "Cage Break", "summary": "Slip into the hut, crack a cage, and rescue a teammate before the boil.", "objective_kind": "rescue", "reward_points": 180},
    {"mission_id": "gong_bait", "label": "Gong Bait", "summary": "Use noise and junk to drag the Biggun toward a rival shroom zone.", "objective_kind": "sound_bait", "reward_points": 140},
    {"mission_id": "pot_stall", "label": "Pot Stall", "summary": "Sabotage potion prep tools and buy time for your surviving shrooms.", "objective_kind": "potion_pressure", "reward_points": 200},
]


def normalize_live_faction_key(value: str | None) -> str:
    clean = str(value or "").strip().lower()
    if clean in LIVE_FACTION_KEYS:
        return clean
    return "blue"


def resolve_live_team_for_faction(value: str | None) -> int:
    clean = normalize_live_faction_key(value)
    try:
        return LIVE_TEAM_FACTION_ORDER.index(clean)
    except ValueError:
        return LIVE_TEAM_FACTION_ORDER.index("blue")


class GuestSessionRequest(BaseModel):
    name: str
    map_id: str
    spawn_preference: str | None = None


class ActionRequest(BaseModel):
    action: str
    value: int | None = None


class AccountRegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    login_name: str = Field(alias="loginName")
    password: str
    display_name: str = Field(alias="displayName")
    faction_key: str = Field(alias="factionKey")
    body_type: str = Field(default="female", alias="bodyType")
    hair_style_key: str = Field(alias="hairStyleKey")
    hair_tint_key: str = Field(alias="hairTintKey")
    primary_color_key: str = Field(default="ocean_blue", alias="primaryColorKey")
    outfit_key: str = Field(default="scout_wrap", alias="outfitKey")
    accessory_key: str = Field(default="none", alias="accessoryKey")
    hat_key: str = Field(default="none", alias="hatKey")
    jewelry_key: str = Field(default="none", alias="jewelryKey")
    gloves_key: str = Field(default="none", alias="glovesKey")
    gear_skin_key: str = Field(default="standard_kit", alias="gearSkinKey")
    biggun_eligible: bool = Field(default=True, alias="biggunEligible")


class AccountLoginRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    login_name: str = Field(alias="loginName")
    password: str


class ProfileAppearanceUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    account_id: str = Field(alias="accountId")
    faction_key: str = Field(alias="factionKey")
    body_type: str = Field(default="female", alias="bodyType")
    hair_style_key: str = Field(alias="hairStyleKey")
    hair_tint_key: str = Field(alias="hairTintKey")
    primary_color_key: str = Field(default="ocean_blue", alias="primaryColorKey")
    outfit_key: str = Field(default="scout_wrap", alias="outfitKey")
    accessory_key: str = Field(default="none", alias="accessoryKey")
    hat_key: str = Field(default="none", alias="hatKey")
    jewelry_key: str = Field(default="none", alias="jewelryKey")
    gloves_key: str = Field(default="none", alias="glovesKey")
    gear_skin_key: str = Field(default="standard_kit", alias="gearSkinKey")
    display_name: str | None = Field(default=None, alias="displayName")
    public_display_name: str | None = Field(default=None, alias="publicDisplayName")
    preferred_role: str | None = Field(default=None, alias="preferredRole")
    warble_color: str | None = Field(default=None, alias="warbleColor")
    spawn_placement: str | None = Field(default=None, alias="spawnPlacement")
    customization_completed: bool | None = Field(default=None, alias="customizationCompleted")
    audio_device_state: str | None = Field(default=None, alias="audioDeviceState")


class SetupMatchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    host_account_id: str = Field(alias="hostAccountId")
    host_display_name: str = Field(alias="hostDisplayName")
    team_count: int = Field(default=3, alias="teamCount")
    team_size: int = Field(default=6, alias="teamSize")
    match_minutes: int = Field(default=35, alias="matchMinutes")
    queue_preset_key: str = Field(default="underfoot_setup", alias="queuePresetKey")
    biggun_override: int = Field(default=0, alias="biggunOverride")
    custom_cross_role_intelligible: bool = Field(default=False, alias="customCrossRoleIntelligible")


class QueueTicketRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    account_id: str = Field(alias="accountId")
    display_name: str = Field(alias="displayName")
    player_id: str = Field(alias="playerId")
    faction_key: str = Field(alias="factionKey")
    hair_style_key: str = Field(alias="hairStyleKey")
    hair_tint_key: str = Field(alias="hairTintKey")
    requested_role: str = Field(default="warble", alias="requestedRole")
    queue_preset_key: str = Field(default="underfoot_quickplay", alias="queuePresetKey")
    party_size: int = Field(default=1, alias="partySize")
    join_style: str = Field(default="solo", alias="joinStyle")
    auto_ready: bool = Field(default=False, alias="autoReady")


class SteamPlatformResolveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    steam_account_id: str = Field(alias="steamAccountId")
    display_name: str = Field(default="", alias="displayName")
    public_display_name: str = Field(default="", alias="publicDisplayName")
    session_ticket: str = Field(default="", alias="sessionTicket")
    preferred_role: str = Field(default="warble", alias="preferredRole")
    warble_color: str = Field(default="blue", alias="warbleColor")
    body_type: str = Field(default="female", alias="bodyType")
    spawn_placement: str = Field(default="auto", alias="spawnPlacement")
    customization_completed: bool = Field(default=False, alias="customizationCompleted")
    audio_device_state: str = Field(default="", alias="audioDeviceState")


class UnrealQueueEnqueueRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    account_id: str = Field(alias="accountId")
    player_id: str = Field(alias="playerId")
    display_name: str = Field(default="", alias="displayName")
    public_display_name: str = Field(default="", alias="publicDisplayName")
    requested_role: str = Field(default="warble", alias="requestedRole")
    warble_color: str = Field(default="blue", alias="warbleColor")
    spawn_placement: str = Field(default="auto", alias="spawnPlacement")
    region: str = Field(default="us-east")
    auto_ready: bool = Field(default=False, alias="autoReady")
    build_fingerprint: str = Field(default="", alias="buildFingerprint")


class AdminFillRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    requested_slots: int = Field(alias="requestedSlots")
    requested_by: str = Field(alias="requestedBy")
    auth_token: str = Field(default="", alias="authToken")
    target_team_id: int = Field(default=-1, alias="targetTeamId")
    target_biggun: bool = Field(default=False, alias="targetBiggun")
    mark_ready: bool = Field(default=True, alias="markReady")


class PrivateRoomCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    host_account_id: str = Field(alias="hostAccountId")
    host_display_name: str = Field(alias="hostDisplayName")
    room_code: str | None = Field(default=None, alias="roomCode")
    player_id: str | None = Field(default=None, alias="playerId")
    faction_key: str | None = Field(default=None, alias="factionKey")
    hair_style_key: str | None = Field(default=None, alias="hairStyleKey")
    hair_tint_key: str | None = Field(default=None, alias="hairTintKey")


class PrivateRoomJoinRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    room_code: str = Field(alias="roomCode")
    account_id: str = Field(alias="accountId")
    display_name: str = Field(alias="displayName")
    player_id: str | None = Field(default=None, alias="playerId")
    faction_key: str | None = Field(default=None, alias="factionKey")
    hair_style_key: str | None = Field(default=None, alias="hairStyleKey")
    hair_tint_key: str | None = Field(default=None, alias="hairTintKey")


class DedicatedServerRegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    server_id: str = Field(alias="serverId")
    label: str = Field(alias="label")
    public_host: str = Field(alias="publicHost")
    combat_port: int = Field(alias="combatPort")
    room_port: int = Field(alias="roomPort")
    supports_combat: bool = Field(default=True, alias="supportsCombat")
    supports_rooms: bool = Field(default=True, alias="supportsRooms")
    max_assignments: int = Field(default=12, alias="maxAssignments")
    runtime_build_fingerprint: str = Field(default="", alias="runtimeBuildFingerprint")
    runtime_build_timestamp_utc: str = Field(default="", alias="runtimeBuildTimestampUtc")
    runtime_package_version: str = Field(default="", alias="runtimePackageVersion")


class DedicatedServerHeartbeatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    active_assignments: int = Field(default=0, alias="activeAssignments")
    is_draining: bool = Field(default=False, alias="isDraining")
    runtime_build_fingerprint: str = Field(default="", alias="runtimeBuildFingerprint")
    runtime_build_timestamp_utc: str = Field(default="", alias="runtimeBuildTimestampUtc")
    runtime_package_version: str = Field(default="", alias="runtimePackageVersion")


class UnrealServerHeartbeatRequest(DedicatedServerHeartbeatRequest):
    server_id: str = Field(alias="serverId")


class DedicatedServerDrainRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    is_draining: bool = Field(default=True, alias="isDraining")


class PersonalShroomRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    team_id: int = Field(alias="teamId")
    cast_seconds: int = Field(default=30, alias="castSeconds")
    cooldown_seconds: int = Field(default=240, alias="cooldownSeconds")


class TeamShroomRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    owner_id: str = Field(alias="ownerId")
    team_id: int = Field(alias="teamId")
    helper_count: int = Field(default=1, alias="helperCount")
    cast_seconds: int = Field(default=90, alias="castSeconds")
    helper_reduction_seconds: int = Field(default=15, alias="helperReductionSeconds")
    floor_seconds: int = Field(default=30, alias="floorSeconds")


class DestroyShroomRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    shroom_type: str = Field(alias="shroomType")
    owner_id: str | None = Field(default=None, alias="ownerId")
    team_id: int | None = Field(default=None, alias="teamId")


class MatchJoinRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    account_id: str = Field(alias="accountId")
    display_name: str = Field(alias="displayName")
    player_id: str = Field(alias="playerId")
    faction_key: str = Field(alias="factionKey")
    hair_style_key: str = Field(alias="hairStyleKey")
    hair_tint_key: str = Field(alias="hairTintKey")
    role: str = "warble"
    team_id: int = Field(default=0, alias="teamId")
    slot_index: int = Field(default=-1, alias="slotIndex")
    is_host: bool = Field(default=False, alias="isHost")
    is_simulated: bool = Field(default=False, alias="isSimulated")


class MatchReadyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    account_id: str = Field(default="", alias="accountId")
    is_ready: bool = Field(default=True, alias="isReady")


class TeamReadyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(default="", alias="playerId")
    account_id: str = Field(default="", alias="accountId")
    is_ready: bool = Field(default=True, alias="isReady")


class MatchPhaseRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    phase: str


class HoleDiscoveryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    hole_id: str = Field(alias="holeId")


class HoleTravelRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    destination_hole_id: str = Field(alias="destinationHoleId")


class MissionActionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    mission_id: str = Field(alias="missionId")
    action: str
    amount: int = 1


class CaptureActionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    target_player_id: str = Field(alias="targetPlayerId")


class TraumaApplyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    distance_meters: float = Field(default=3.0, alias="distanceMeters")
    goo_covered: bool = Field(default=False, alias="gooCovered")


class SkillUseRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    skill_key: str = Field(alias="skillKey")
    target_team_id: int = Field(default=-1, alias="targetTeamId")
    target_anchor_id: str = Field(default="", alias="targetAnchorId")
    target_hole_id: str = Field(default="", alias="targetHoleId")


class BiggunPlacementRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    placement_type: str = Field(alias="placementType")
    anchor_id: str = Field(default="", alias="anchorId")
    target_team_id: int = Field(default=-1, alias="targetTeamId")


class VoiceJoinTokenRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    role: str | None = None


class VoiceNoiseEventRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    kind: str
    source_label: str = Field(default="voice_event", alias="sourceLabel")
    intensity: float = 1.0
    audible_to_bigguns: bool = Field(default=True, alias="audibleToBigguns")
    world_x: float = Field(default=0.0, alias="worldX")
    world_y: float = Field(default=0.0, alias="worldY")
    world_z: float = Field(default=0.0, alias="worldZ")


class VoiceMicStateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    speaking: bool = False
    has_input: bool = Field(default=False, alias="hasInput")
    device_lost: bool = Field(default=False, alias="deviceLost")
    rms_level: float = Field(default=0.0, alias="rmsLevel")
    packet_loss_percent: float = Field(default=0.0, alias="packetLossPercent")
    jitter_ms: float = Field(default=0.0, alias="jitterMs")


class VoiceContourStateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    sequence_id: int = Field(default=0, alias="sequenceId")
    is_active: bool = Field(default=False, alias="isActive")
    envelope: float = 0.0
    pitch_norm: float = Field(default=0.5, alias="pitchNorm")
    stress: float = 0.0
    burst: float = 0.0
    duration_ms: float = Field(default=0.0, alias="durationMs")
    bank_hint: str = Field(default="", alias="bankHint")


class VoiceGatewayStatsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    active_peers: int = Field(default=0, alias="activePeers")
    connected_peers: int = Field(default=0, alias="connectedPeers")
    speaking_peers: int = Field(default=0, alias="speakingPeers")
    live_speakers: int = Field(default=0, alias="liveSpeakers")
    dropped_peers: int = Field(default=0, alias="droppedPeers")
    average_packet_loss_percent: float = Field(default=0.0, alias="averagePacketLossPercent")
    average_jitter_ms: float = Field(default=0.0, alias="averageJitterMs")
    average_jitter_milliseconds: float = Field(default=0.0, alias="averageJitterMilliseconds")
    contour_packets_relayed: int = Field(default=0, alias="contourPacketsRelayed")
    audio_frames_relayed: int = Field(default=0, alias="audioFramesRelayed")
    audio_bytes_relayed: int = Field(default=0, alias="audioBytesRelayed")
    raw_track_subscriptions: int = Field(default=0, alias="rawTrackSubscriptions")
    contour_only_subscriptions: int = Field(default=0, alias="contourOnlySubscriptions")


class ReportAttachmentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    file_name: str = Field(alias="fileName")
    content_type: str = Field(default="application/octet-stream", alias="contentType")
    base64_data: str = Field(default="", alias="base64Data")


class PlayerFeedbackRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    account_id: str = Field(default="", alias="accountId")
    player_id: str = Field(default="", alias="playerId")
    display_name: str = Field(default="", alias="displayName")
    category: str = Field(default="other")
    text: str = Field(default="")
    scene_mode: str = Field(default="", alias="sceneMode")
    scene_name: str = Field(default="", alias="sceneName")
    faction_key: str = Field(default="", alias="factionKey")
    role: str = Field(default="warble")
    match_id: str = Field(default="", alias="matchId")
    room_code: str = Field(default="", alias="roomCode")
    assignment_id: str = Field(default="", alias="assignmentId")
    assignment_route: str = Field(default="", alias="assignmentRoute")
    build_stamp: str = Field(default="", alias="buildStamp")
    voice_mode: str = Field(default="", alias="voiceMode")
    voice_connection_state: str = Field(default="", alias="voiceConnectionState")
    runtime_summary: dict[str, Any] = Field(default_factory=dict, alias="runtimeSummary")
    screenshot: ReportAttachmentRequest | None = None
    created_at_epoch: int = Field(default=0, alias="createdAtEpoch")


class CrashReportRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    session_id: str = Field(default="", alias="sessionId")
    account_id: str = Field(default="", alias="accountId")
    player_id: str = Field(default="", alias="playerId")
    display_name: str = Field(default="", alias="displayName")
    scene_name: str = Field(default="", alias="sceneName")
    scene_mode: str = Field(default="", alias="sceneMode")
    match_id: str = Field(default="", alias="matchId")
    room_code: str = Field(default="", alias="roomCode")
    assignment_id: str = Field(default="", alias="assignmentId")
    assignment_route: str = Field(default="", alias="assignmentRoute")
    build_stamp: str = Field(default="", alias="buildStamp")
    voice_mode: str = Field(default="", alias="voiceMode")
    voice_connection_state: str = Field(default="", alias="voiceConnectionState")
    platform_info: dict[str, Any] = Field(default_factory=dict, alias="platformInfo")
    runtime_summary: dict[str, Any] = Field(default_factory=dict, alias="runtimeSummary")
    crash_marker: dict[str, Any] = Field(default_factory=dict, alias="crashMarker")
    error_buffer: list[str] = Field(default_factory=list, alias="errorBuffer")
    attachments: list[ReportAttachmentRequest] = Field(default_factory=list)
    created_at_epoch: int = Field(default=0, alias="createdAtEpoch")


class RuntimeDiagnosticEventRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    created_at_epoch: int = Field(default=0, alias="createdAtEpoch")
    stage: str = Field(default="")
    severity: str = Field(default="info")
    detail: str = Field(default="")
    scene_name: str = Field(default="", alias="sceneName")
    transport_mode: str = Field(default="", alias="transportMode")
    connection_state: str = Field(default="", alias="connectionState")


class RuntimeDiagnosticReportRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    session_id: str = Field(default="", alias="sessionId")
    account_id: str = Field(default="", alias="accountId")
    player_id: str = Field(default="", alias="playerId")
    display_name: str = Field(default="", alias="displayName")
    summary: str = Field(default="")
    severity: str = Field(default="warn")
    scene_name: str = Field(default="", alias="sceneName")
    scene_mode: str = Field(default="", alias="sceneMode")
    match_id: str = Field(default="", alias="matchId")
    room_code: str = Field(default="", alias="roomCode")
    assignment_id: str = Field(default="", alias="assignmentId")
    assignment_route: str = Field(default="", alias="assignmentRoute")
    build_stamp: str = Field(default="", alias="buildStamp")
    voice_mode: str = Field(default="", alias="voiceMode")
    voice_connection_state: str = Field(default="", alias="voiceConnectionState")
    runtime_summary: dict[str, Any] = Field(default_factory=dict, alias="runtimeSummary")
    events: list[RuntimeDiagnosticEventRequest] = Field(default_factory=list)
    created_at_epoch: int = Field(default=0, alias="createdAtEpoch")


class SessionReconnectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    auth_token: str = Field(alias="authToken")


class LeaveActiveMatchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    auth_token: str = Field(alias="authToken")


class PresenceHeartbeatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    assignment_id: str = Field(default="", alias="assignmentId")
    scene_name: str = Field(default="", alias="sceneName")
    voice_mode: str = Field(default="", alias="voiceMode")
    voice_connection_state: str = Field(default="", alias="voiceConnectionState")


class UnrealVoicePresenceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    account_id: str = Field(default="", alias="accountId")
    player_id: str = Field(alias="playerId")
    match_id: str = Field(default="", alias="matchId")
    assignment_id: str = Field(default="", alias="assignmentId")
    scene_name: str = Field(default="", alias="sceneName")
    voice_mode: str = Field(default="", alias="voiceMode")
    voice_connection_state: str = Field(default="", alias="voiceConnectionState")
    audio_device_state: str = Field(default="", alias="audioDeviceState")


class UnrealMatchResultPlayerRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    account_id: str = Field(default="", alias="accountId")
    player_id: str = Field(default="", alias="playerId")
    display_name: str = Field(default="", alias="displayName")
    role: str = Field(default="warble")
    team_id: int = Field(default=-1, alias="teamId")
    ready: bool = True
    xp_delta: int = Field(default=25, alias="xpDelta")
    coins_delta: int = Field(default=5, alias="coinsDelta")


class UnrealMatchResultRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    match_id: str = Field(alias="matchId")
    assignment_id: str = Field(default="", alias="assignmentId")
    map_id: str = Field(default="blackpot_hollow", alias="mapId")
    mode_key: str = Field(default="underfoot_prototype", alias="modeKey")
    winner_team_id: int = Field(default=-1, alias="winnerTeamId")
    duration_seconds: int = Field(default=0, alias="durationSeconds")
    build_fingerprint: str = Field(default="", alias="buildFingerprint")
    build_stamp: str = Field(default="", alias="buildStamp")
    players: list[UnrealMatchResultPlayerRequest] = Field(default_factory=list)


class ProjectMemoryUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    project_memory: dict[str, Any] = Field(default_factory=dict, alias="projectMemory")
    merge: bool = True
    updated_by: str = Field(default="system", alias="updatedBy")


def deep_merge_dict(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dict(merged.get(key, {}), value)
        else:
            merged[key] = value
    return merged


def clean_login_name(value: str) -> str:
    return "".join(
        ch for ch in (value or "").strip().lower()
        if ch.isalnum() or ch in {"_", "-", ".", "+", "@"}
    )[:48]


LOGIN_NAME_MIN_LENGTH = 3
LOGIN_NAME_MAX_LENGTH = 48
DISPLAY_NAME_MIN_LENGTH = 3
DISPLAY_NAME_MAX_LENGTH = 16
PROHIBITED_DISPLAY_NAME_FRAGMENTS = {
    "fuck",
    "shit",
    "bitch",
    "cunt",
    "whore",
    "slut",
    "nigg",
    "fagg",
    "kike",
    "spic",
    "chink",
    "rape",
    "hitler",
    "nazi",
}


def _auth_validation_sample(value: str | None) -> str:
    sample = " ".join(str(value or "").strip().split())
    if len(sample) > 24:
        return f"{sample[:24]}..."
    return sample


def log_auth_validation_rejection(route: str, field: str, reason: str, raw_value: str | None, normalized_value: str | None = None) -> None:
    LOGGER.warning(
        "auth_validation_rejected stage=auth severity=warn route=%s field=%s reason=%s raw=%r normalized=%r",
        route,
        field,
        reason,
        _auth_validation_sample(raw_value),
        _auth_validation_sample(normalized_value),
    )


def validate_login_name(value: str, route: str) -> str:
    raw = (value or "").strip()
    normalized = clean_login_name(raw)
    if len(normalized) < LOGIN_NAME_MIN_LENGTH:
        log_auth_validation_rejection(route, "login_name", "too_short", raw, normalized)
        raise HTTPException(status_code=400, detail="Login name must be at least 3 characters.")
    if len(raw) > LOGIN_NAME_MAX_LENGTH:
        log_auth_validation_rejection(route, "login_name", "too_long", raw, normalized)
        raise HTTPException(
            status_code=400,
            detail=f"Login name must be {LOGIN_NAME_MIN_LENGTH}-{LOGIN_NAME_MAX_LENGTH} characters using valid email/login characters.",
        )
    if raw.lower() != normalized:
        log_auth_validation_rejection(route, "login_name", "invalid_characters", raw, normalized)
        raise HTTPException(
            status_code=400,
            detail="Login name can only use letters, numbers, underscores, hyphens, periods, plus signs, and @.",
        )
    return normalized


def sanitize_display_name(value: str) -> str:
    trimmed = (value or "").strip()
    return "".join(ch for ch in trimmed if ch.isascii() and ch.isalnum())[:DISPLAY_NAME_MAX_LENGTH]


def derive_fallback_display_name(login_name: str) -> str:
    fallback = (login_name or "").strip()
    if "@" in fallback:
        fallback = fallback.split("@", 1)[0]
    if "+" in fallback:
        fallback = fallback.split("+", 1)[0]
    fallback = sanitize_display_name(fallback)
    return fallback if len(fallback) >= DISPLAY_NAME_MIN_LENGTH else "warble"


def _display_name_block_fragment(value: str) -> str:
    compact = "".join(ch for ch in (value or "").lower() if ch.isalnum())
    for fragment in PROHIBITED_DISPLAY_NAME_FRAGMENTS:
        if fragment in compact:
            return fragment
    return ""


def normalize_account_display_name(value: str | None, login_name: str, route: str = "auth_register", strict: bool = True) -> str:
    raw = (value or "").strip()
    if raw:
        if strict:
            if any(ch.isspace() for ch in raw):
                log_auth_validation_rejection(route, "display_name", "contains_spaces", raw, raw)
                raise HTTPException(status_code=400, detail="Warble name cannot contain spaces.")
            if len(raw) > DISPLAY_NAME_MAX_LENGTH:
                log_auth_validation_rejection(route, "display_name", "too_long", raw, raw[:DISPLAY_NAME_MAX_LENGTH])
                raise HTTPException(
                    status_code=400,
                    detail=f"Warble name must be {DISPLAY_NAME_MIN_LENGTH}-{DISPLAY_NAME_MAX_LENGTH} letters or numbers with no spaces.",
                )
            candidate = sanitize_display_name(raw)
            if candidate != raw:
                log_auth_validation_rejection(route, "display_name", "invalid_characters", raw, candidate)
                raise HTTPException(
                    status_code=400,
                    detail=f"Warble name must be {DISPLAY_NAME_MIN_LENGTH}-{DISPLAY_NAME_MAX_LENGTH} letters or numbers with no spaces.",
                )
        else:
            candidate = sanitize_display_name(raw)
    else:
        candidate = ""

    if not candidate:
        candidate = derive_fallback_display_name(login_name)
    if len(candidate) < DISPLAY_NAME_MIN_LENGTH:
        log_auth_validation_rejection(route, "display_name", "too_short", raw or login_name, candidate)
        raise HTTPException(
            status_code=400,
            detail=f"Warble name must be {DISPLAY_NAME_MIN_LENGTH}-{DISPLAY_NAME_MAX_LENGTH} letters or numbers with no spaces.",
        )
    blocked_fragment = _display_name_block_fragment(candidate)
    if blocked_fragment:
        log_auth_validation_rejection(route, "display_name", f"blocked_fragment:{blocked_fragment}", raw or candidate, candidate)
        raise HTTPException(status_code=400, detail="Warble name contains blocked language.")
    return candidate


def stable_player_id(login_name: str) -> str:
    return f"warble_{hashlib.sha256(login_name.encode('utf-8')).hexdigest()[:12]}"


def build_password_hash(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt_hex = salt_hex or secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), bytes.fromhex(salt_hex), 120_000)
    return salt_hex, derived.hex()


def verify_password(password: str, salt_hex: str, digest_hex: str) -> bool:
    _, comparison = build_password_hash(password, salt_hex)
    return secrets.compare_digest(comparison, digest_hex)


def normalize_room_code(raw_code: str | None = None) -> str:
    clean = "".join(ch for ch in (raw_code or "").upper().strip() if ch.isalnum())[:6]
    if len(clean) == 6:
        return clean
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(alphabet[secrets.randbelow(len(alphabet))] for _ in range(6))


def read_bearer_token(authorization: str | None, header_token: str | None) -> str:
    if header_token:
        return str(header_token).strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def require_project_memory_write_access(authorization: str | None, header_token: str | None) -> None:
    if len(PROJECT_MEMORY_WRITE_TOKEN) < 32:
        raise HTTPException(status_code=503, detail="Project memory write token is not configured.")
    if not token_matches(read_bearer_token(authorization, header_token), PROJECT_MEMORY_WRITE_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid project memory write token.")


class UnderfootMySQLStore:
    def __init__(self) -> None:
        self.enabled = bool(
            UNDERFOOT_DB_ENABLED
            and mysql is not None
            and UNDERFOOT_DB_HOST
            and UNDERFOOT_DB_NAME
            and UNDERFOOT_DB_USER
            and UNDERFOOT_DB_PASSWORD
        )

    def ensure_schema(self) -> None:
        if not self.enabled:
            return

        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS uf_profiles (
                  player_name VARCHAR(64) PRIMARY KEY,
                  display_name VARCHAR(64) NOT NULL,
                  player_rank INT NOT NULL DEFAULT 1,
                  xp INT NOT NULL DEFAULT 0,
                  coins INT NOT NULL DEFAULT 0,
                  total_points INT NOT NULL DEFAULT 0,
                  total_rescues INT NOT NULL DEFAULT 0,
                  total_resources INT NOT NULL DEFAULT 0,
                  total_deaths INT NOT NULL DEFAULT 0,
                  last_session_id VARCHAR(64) NULL,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS uf_sessions (
                  session_id VARCHAR(64) PRIMARY KEY,
                  player_name VARCHAR(64) NOT NULL,
                  map_id VARCHAR(64) NOT NULL,
                  map_title VARCHAR(128) NOT NULL,
                  spawn_json JSON NOT NULL,
                  stats_json JSON NOT NULL,
                  quest_state_json JSON NOT NULL,
                  status VARCHAR(32) NOT NULL,
                  last_seen BIGINT NOT NULL,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  KEY idx_uf_sessions_map_id (map_id),
                  KEY idx_uf_sessions_last_seen (last_seen)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS uf_events (
                  event_key VARCHAR(64) PRIMARY KEY,
                  session_id VARCHAR(64) NOT NULL,
                  map_id VARCHAR(64) NULL,
                  kind VARCHAR(32) NOT NULL,
                  action_name VARCHAR(64) NULL,
                  note TEXT NULL,
                  payload_json JSON NULL,
                  ts BIGINT NOT NULL,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  KEY idx_uf_events_session_id (session_id),
                  KEY idx_uf_events_ts (ts)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS uf_project_memory (
                  project_key VARCHAR(64) PRIMARY KEY,
                  game_title VARCHAR(128) NOT NULL,
                  summary TEXT NOT NULL,
                  memory_json JSON NOT NULL,
                  source_path VARCHAR(255) NULL,
                  updated_by VARCHAR(64) NOT NULL DEFAULT 'system',
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS uf_accounts (
                  login_name VARCHAR(64) PRIMARY KEY,
                  account_id VARCHAR(64) NOT NULL,
                  player_id VARCHAR(64) NOT NULL,
                  display_name VARCHAR(64) NOT NULL,
                  public_display_name VARCHAR(64) NOT NULL,
                  account_json JSON NOT NULL,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  UNIQUE KEY idx_uf_accounts_account_id (account_id),
                  UNIQUE KEY idx_uf_accounts_player_id (player_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS uf_auth_tokens (
                  auth_token VARCHAR(128) PRIMARY KEY,
                  login_name VARCHAR(64) NOT NULL,
                  account_id VARCHAR(64) NOT NULL,
                  issued_at BIGINT NOT NULL,
                  auth_json JSON NOT NULL,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  KEY idx_uf_auth_tokens_login_name (login_name),
                  KEY idx_uf_auth_tokens_account_id (account_id)
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def load_state(self) -> tuple[dict[str, dict[str, Any]], deque[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        if not self.enabled:
            return {}, deque(maxlen=200), {}, {}

        sessions: dict[str, dict[str, Any]] = {}
        events: deque[dict[str, Any]] = deque(maxlen=200)
        accounts: dict[str, dict[str, Any]] = {}
        auth_tokens: dict[str, dict[str, Any]] = {}
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT session_id, player_name, map_id, map_title, spawn_json, stats_json, quest_state_json, status, last_seen
                FROM uf_sessions
                """
            )
            for row in cursor.fetchall():
                session_id, player_name, map_id, map_title, spawn_json, stats_json, quest_state_json, status, last_seen = row
                sessions[session_id] = {
                    "session_id": session_id,
                    "player_name": player_name,
                    "map_id": map_id,
                    "map_title": map_title,
                    "spawn": decode_json(spawn_json, {}),
                    "stats": decode_json(stats_json, {}),
                    "quest_state": decode_json(quest_state_json, {}),
                    "status": status,
                    "last_seen": int(last_seen or 0),
                }

            cursor.execute(
                """
                SELECT event_key, session_id, map_id, kind, action_name, note, payload_json, ts
                FROM uf_events
                ORDER BY ts DESC
                LIMIT 200
                """
            )
            for row in cursor.fetchall():
                event_key, session_id, map_id, kind, action_name, note, payload_json, ts = row
                payload = decode_json(payload_json, {})
                event = {
                    "event_key": event_key,
                    "session_id": session_id,
                    "map_id": map_id,
                    "kind": kind,
                    "note": note or "",
                    "ts": int(ts or 0),
                }
                if action_name:
                    event["action"] = action_name
                if payload:
                    event.update(payload)
                events.append(event)

            cursor.execute(
                """
                SELECT login_name, account_json
                FROM uf_accounts
                """
            )
            for row in cursor.fetchall():
                login_name, account_json = row
                account = decode_json(account_json, {})
                if not isinstance(account, dict):
                    continue
                clean_login_name_value = clean_login_name(str(login_name or account.get("login_name", "")))
                if not clean_login_name_value:
                    continue
                account["login_name"] = clean_login_name_value
                accounts[clean_login_name_value] = account

            cursor.execute(
                """
                SELECT auth_token, auth_json
                FROM uf_auth_tokens
                """
            )
            for row in cursor.fetchall():
                auth_token, auth_json = row
                auth_row = decode_json(auth_json, {})
                if not isinstance(auth_row, dict):
                    continue
                clean_auth_token = str(auth_token or "").strip()
                if not clean_auth_token:
                    continue
                auth_row["auth_token"] = clean_auth_token
                auth_tokens[clean_auth_token] = auth_row
        finally:
            connection.close()

        return sessions, events, accounts, auth_tokens

    def replace_state(
        self,
        sessions: dict[str, dict[str, Any]],
        events: deque[dict[str, Any]],
        accounts: dict[str, dict[str, Any]],
        auth_tokens: dict[str, dict[str, Any]],
    ) -> None:
        if not self.enabled:
            return

        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute("DELETE FROM uf_sessions")
            cursor.execute("DELETE FROM uf_events")
            cursor.execute("DELETE FROM uf_accounts")
            cursor.execute("DELETE FROM uf_auth_tokens")

            for entry in sessions.values():
                cursor.execute(
                    """
                    INSERT INTO uf_sessions (
                      session_id, player_name, map_id, map_title, spawn_json, stats_json, quest_state_json, status, last_seen
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        entry.get("session_id"),
                        entry.get("player_name"),
                        entry.get("map_id"),
                        entry.get("map_title"),
                        json.dumps(entry.get("spawn", {})),
                        json.dumps(entry.get("stats", {})),
                        json.dumps(entry.get("quest_state", {})),
                        entry.get("status", "active"),
                        int(entry.get("last_seen", now_ts())),
                    ),
                )
                self._upsert_profile(cursor, entry)

            for event in list(events):
                payload = {
                    key: value
                    for key, value in event.items()
                    if key not in {"event_key", "session_id", "map_id", "kind", "action", "note", "ts"}
                }
                cursor.execute(
                    """
                    INSERT INTO uf_events (
                      event_key, session_id, map_id, kind, action_name, note, payload_json, ts
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event.get("event_key"),
                        event.get("session_id"),
                        event.get("map_id"),
                        event.get("kind"),
                        event.get("action"),
                        event.get("note"),
                        json.dumps(payload),
                        int(event.get("ts", now_ts())),
                    ),
                )

            for login_name, account in accounts.items():
                normalized_login_name = clean_login_name(str(login_name or account.get("login_name", "")))
                if not normalized_login_name:
                    continue
                payload = dict(account)
                payload["login_name"] = normalized_login_name
                cursor.execute(
                    """
                    INSERT INTO uf_accounts (
                      login_name, account_id, player_id, display_name, public_display_name, account_json
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        normalized_login_name,
                        str(payload.get("account_id", "")).strip(),
                        str(payload.get("player_id", "")).strip(),
                        normalize_account_display_name(payload.get("display_name"), normalized_login_name, route="mysql_replace_state", strict=False),
                        build_unreal_public_display_name(payload.get("public_display_name", payload.get("display_name", normalized_login_name))),
                        json.dumps(payload),
                    ),
                )

            for auth_token, auth_row in auth_tokens.items():
                clean_auth_token = str(auth_token or auth_row.get("auth_token", "")).strip()
                if not clean_auth_token:
                    continue
                payload = dict(auth_row)
                payload["auth_token"] = clean_auth_token
                cursor.execute(
                    """
                    INSERT INTO uf_auth_tokens (
                      auth_token, login_name, account_id, issued_at, auth_json
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        clean_auth_token,
                        clean_login_name(str(payload.get("login_name", ""))),
                        str(payload.get("account_id", "")).strip(),
                        int(payload.get("issued_at", now_ts()) or now_ts()),
                        json.dumps(payload),
                    ),
                )

            connection.commit()
        finally:
            connection.close()

    def get_profile(self, player_name: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None

        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT player_name, display_name, player_rank, xp, coins, total_points, total_rescues, total_resources, total_deaths, last_session_id
                FROM uf_profiles
                WHERE player_name = %s
                """,
                (player_name,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "player_name": row[0],
                "display_name": row[1],
                "rank": int(row[2]),
                "xp": int(row[3]),
                "coins": int(row[4]),
                "totals": {
                    "points": int(row[5]),
                    "rescues": int(row[6]),
                    "resources": int(row[7]),
                    "deaths": int(row[8]),
                },
                "last_session_id": row[9],
            }
        finally:
            connection.close()

    def load_project_memory(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None

        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT game_title, summary, memory_json, source_path, updated_by, UNIX_TIMESTAMP(updated_at)
                FROM uf_project_memory
                WHERE project_key = %s
                """,
                (PROJECT_NAME,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            payload = decode_json(row[2], {})
            if not isinstance(payload, dict):
                payload = {}
            payload.setdefault("projectKey", PROJECT_NAME)
            payload.setdefault("gameTitle", row[0] or PROJECT_NAME.upper())
            payload.setdefault("conceptSummary", row[1] or "")
            payload["sourcePath"] = row[3] or str(DARIUS_PROJECT_MEMORY_PATH)
            payload["updatedBy"] = row[4] or "system"
            payload["updatedAtEpoch"] = int(row[5] or 0)
            return payload
        finally:
            connection.close()

    def save_project_memory(self, memory: dict[str, Any], updated_by: str = "system") -> None:
        if not self.enabled:
            return

        payload = dict(memory or {})
        payload.setdefault("projectKey", PROJECT_NAME)
        payload.setdefault("gameTitle", str(payload.get("gameTitle", PROJECT_NAME.upper())).strip() or PROJECT_NAME.upper())
        payload.setdefault("conceptSummary", str(payload.get("conceptSummary", "")).strip())
        payload["updatedBy"] = updated_by or str(payload.get("updatedBy", "system")).strip() or "system"
        payload["updatedAtEpoch"] = now_ts()

        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO uf_project_memory (
                  project_key, game_title, summary, memory_json, source_path, updated_by
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  game_title = VALUES(game_title),
                  summary = VALUES(summary),
                  memory_json = VALUES(memory_json),
                  source_path = VALUES(source_path),
                  updated_by = VALUES(updated_by)
                """,
                (
                    PROJECT_NAME,
                    payload.get("gameTitle"),
                    payload.get("conceptSummary", ""),
                    json.dumps(payload),
                    str(DARIUS_PROJECT_MEMORY_PATH),
                    payload["updatedBy"],
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def _upsert_profile(self, cursor: Any, entry: dict[str, Any]) -> None:
        stats = entry.get("stats", {})
        total_points = int(stats.get("points", 0))
        total_rescues = int(stats.get("rescues", 0))
        total_resources = int(stats.get("resources", 0))
        total_deaths = int(stats.get("deaths", 0))
        rank = max(1, (total_points // 500) + 1)
        xp = total_points
        coins = total_points // 10

        cursor.execute(
            """
            INSERT INTO uf_profiles (
              player_name, display_name, player_rank, xp, coins, total_points, total_rescues, total_resources, total_deaths, last_session_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              display_name = VALUES(display_name),
              player_rank = GREATEST(player_rank, VALUES(player_rank)),
              xp = GREATEST(xp, VALUES(xp)),
              coins = GREATEST(coins, VALUES(coins)),
              total_points = GREATEST(total_points, VALUES(total_points)),
              total_rescues = GREATEST(total_rescues, VALUES(total_rescues)),
              total_resources = GREATEST(total_resources, VALUES(total_resources)),
              total_deaths = GREATEST(total_deaths, VALUES(total_deaths)),
              last_session_id = VALUES(last_session_id)
            """,
            (
                entry.get("player_name"),
                entry.get("player_name"),
                rank,
                xp,
                coins,
                total_points,
                total_rescues,
                total_resources,
                total_deaths,
                entry.get("session_id"),
            ),
        )

    def _connect(self) -> Any:
        return mysql.connector.connect(
            host=UNDERFOOT_DB_HOST,
            port=UNDERFOOT_DB_PORT,
            user=UNDERFOOT_DB_USER,
            password=UNDERFOOT_DB_PASSWORD,
            database=UNDERFOOT_DB_NAME,
            autocommit=False,
        )


class WorldState:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=200)
        self.connections: dict[str, set[WebSocket]] = defaultdict(set)
        self.accounts: dict[str, dict[str, Any]] = {}
        self.auth_tokens: dict[str, dict[str, Any]] = {}
        self.queue_tickets: dict[str, dict[str, Any]] = {}
        self.private_rooms: dict[str, dict[str, Any]] = {}
        self.setup_matches: dict[str, dict[str, Any]] = {}
        self.match_runtimes: dict[str, dict[str, Any]] = {}
        self.dedicated_servers: dict[str, dict[str, Any]] = {}
        self.dedicated_assignments: dict[str, dict[str, Any]] = {}
        self.feedback_reports: dict[str, dict[str, Any]] = {}
        self.crash_reports: dict[str, dict[str, Any]] = {}
        self.runtime_diagnostics: dict[str, dict[str, Any]] = {}
        self.project_memory: dict[str, Any] = {}
        self.store = UnderfootMySQLStore()
        self.load()

    @property
    def persistence_mode(self) -> str:
        return "mysql+json" if self.store.enabled else "json"

    def load(self) -> None:
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
        self.project_memory = dict(PROJECT_MEMORY_SEED) if isinstance(PROJECT_MEMORY_SEED, dict) else {}
        local_memory = dict(self.project_memory)

        if self.store.enabled:
            try:
                self.store.ensure_schema()
                project_memory = self.store.load_project_memory()
                if isinstance(project_memory, dict) and project_memory:
                    self.project_memory = project_memory
                    local_updated = int(local_memory.get("updatedAtEpoch", 0) or 0) if isinstance(local_memory, dict) else 0
                    remote_updated = int(project_memory.get("updatedAtEpoch", 0) or 0)
                    if not local_memory or remote_updated >= local_updated:
                        DARIUS_PROJECT_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
                        DARIUS_PROJECT_MEMORY_PATH.write_text(
                            json.dumps(self.project_memory, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
                elif self.project_memory:
                    self.persist_project_memory(updated_by="seed_sync")
                sessions, events, accounts, auth_tokens = self.store.load_state()
                if sessions:
                    self.sessions = sessions
                if events:
                    self.events = events
                if accounts:
                    self.accounts = accounts
                if auth_tokens:
                    self.auth_tokens = auth_tokens
            except MySQLError:
                LOGGER.exception("Failed loading Underfoot state from MySQL; falling back to JSON.")

        if not STATE_PATH.exists():
            self.persist()
            return

        try:
            payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if not self.sessions and isinstance(payload.get("sessions"), dict):
                self.sessions = payload["sessions"]
            if not self.events and isinstance(payload.get("events"), list):
                self.events = deque(payload["events"][-200:], maxlen=200)
            if not self.accounts and isinstance(payload.get("accounts"), dict):
                self.accounts = payload["accounts"]
            if not self.auth_tokens and isinstance(payload.get("auth_tokens"), dict):
                self.auth_tokens = payload["auth_tokens"]
            if isinstance(payload.get("queue_tickets"), dict):
                self.queue_tickets = payload["queue_tickets"]
            if isinstance(payload.get("private_rooms"), dict):
                self.private_rooms = payload["private_rooms"]
            if isinstance(payload.get("setup_matches"), dict):
                self.setup_matches = payload["setup_matches"]
            if isinstance(payload.get("match_runtimes"), dict):
                self.match_runtimes = payload["match_runtimes"]
            if isinstance(payload.get("dedicated_servers"), dict):
                self.dedicated_servers = payload["dedicated_servers"]
            if isinstance(payload.get("dedicated_assignments"), dict):
                self.dedicated_assignments = payload["dedicated_assignments"]
            if isinstance(payload.get("feedback_reports"), dict):
                self.feedback_reports = payload["feedback_reports"]
            if isinstance(payload.get("crash_reports"), dict):
                self.crash_reports = payload["crash_reports"]
            if isinstance(payload.get("runtime_diagnostics"), dict):
                self.runtime_diagnostics = payload["runtime_diagnostics"]
        except Exception:
            LOGGER.exception("Failed loading Underfoot state from JSON snapshot; resetting runtime state.")
            self.sessions = {}
            self.events = deque(maxlen=200)
            self.accounts = {}
            self.auth_tokens = {}
            self.queue_tickets = {}
            self.private_rooms = {}
            self.setup_matches = {}
            self.match_runtimes = {}
            self.dedicated_servers = {}
            self.dedicated_assignments = {}
            self.feedback_reports = {}
            self.crash_reports = {}
            self.runtime_diagnostics = {}

    def persist_project_memory(self, updated_by: str = "system") -> None:
        if not isinstance(self.project_memory, dict) or not self.project_memory:
            return

        DARIUS_PROJECT_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.project_memory["projectKey"] = PROJECT_NAME
        self.project_memory["gameTitle"] = str(self.project_memory.get("gameTitle", PROJECT_NAME.upper())).strip() or PROJECT_NAME.upper()
        self.project_memory["conceptSummary"] = str(self.project_memory.get("conceptSummary", "")).strip()
        self.project_memory["sourcePath"] = str(DARIUS_PROJECT_MEMORY_PATH)
        self.project_memory["updatedBy"] = updated_by or str(self.project_memory.get("updatedBy", "system")).strip() or "system"
        self.project_memory["updatedAtEpoch"] = now_ts()
        DARIUS_PROJECT_MEMORY_PATH.write_text(
            json.dumps(self.project_memory, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        if self.store.enabled:
            try:
                self.store.save_project_memory(self.project_memory, updated_by=self.project_memory["updatedBy"])
            except MySQLError:
                LOGGER.exception("Failed persisting Underfoot project memory to MySQL; JSON memory remains current.")

    def update_project_memory(self, incoming: dict[str, Any], *, updated_by: str = "system", merge: bool = True) -> dict[str, Any]:
        if not isinstance(incoming, dict):
            raise HTTPException(status_code=400, detail="Project memory update must be a JSON object.")

        if merge and isinstance(self.project_memory, dict) and self.project_memory:
            self.project_memory = deep_merge_dict(self.project_memory, incoming)
        else:
            self.project_memory = dict(incoming)

        self.project_memory["projectKey"] = PROJECT_NAME
        self.project_memory["gameTitle"] = str(self.project_memory.get("gameTitle", PROJECT_NAME.upper())).strip() or PROJECT_NAME.upper()
        self.project_memory["conceptSummary"] = str(self.project_memory.get("conceptSummary", "")).strip()
        self.persist_project_memory(updated_by=updated_by)
        return self.project_memory

    def persist(self) -> None:
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessions": self.sessions,
            "events": list(self.events)[-200:],
            "accounts": self.accounts,
            "auth_tokens": self.auth_tokens,
            "queue_tickets": self.queue_tickets,
            "private_rooms": self.private_rooms,
            "setup_matches": self.setup_matches,
            "match_runtimes": self.match_runtimes,
            "dedicated_servers": self.dedicated_servers,
            "dedicated_assignments": self.dedicated_assignments,
            "feedback_reports": self.feedback_reports,
            "crash_reports": self.crash_reports,
            "runtime_diagnostics": self.runtime_diagnostics,
            "updated_at": now_ts(),
        }
        STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        if self.store.enabled:
            try:
                self.store.replace_state(self.sessions, self.events, self.accounts, self.auth_tokens)
            except MySQLError:
                LOGGER.exception("Failed persisting Underfoot state to MySQL; JSON snapshot remains current.")

    def _resolve_account_from_token(self, token: str) -> dict[str, Any]:
        clean_token = (token or "").strip()
        if not clean_token:
            raise HTTPException(status_code=400, detail="Auth token is required.")

        auth_row = self.auth_tokens.get(clean_token)
        if not auth_row:
            raise HTTPException(status_code=404, detail="Unknown auth session.")

        account = self.accounts.get(str(auth_row.get("login_name", "")).strip())
        if not account:
            raise HTTPException(status_code=404, detail="The profile for this auth session no longer exists.")
        return account

    @staticmethod
    def _safe_report_filename(file_name: str, default_name: str) -> str:
        clean = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(file_name or "").strip())
        clean = clean.strip("._")
        return clean or default_name

    def _write_report_attachments(self, report_dir: Path, attachments: list[ReportAttachmentRequest]) -> list[dict[str, Any]]:
        descriptors: list[dict[str, Any]] = []
        for index, attachment in enumerate(attachments):
            payload = str(attachment.base64_data or "").strip()
            if not payload:
                continue

            try:
                raw_bytes = base64.b64decode(payload, validate=False)
            except Exception:
                LOGGER.warning("Underfoot report attachment decode failed for %s", attachment.file_name)
                continue

            if not raw_bytes:
                continue

            safe_name = self._safe_report_filename(attachment.file_name, f"attachment_{index + 1}.bin")
            target_path = report_dir / safe_name
            target_path.write_bytes(raw_bytes)
            descriptors.append(
                {
                    "fileName": safe_name,
                    "contentType": str(attachment.content_type or "application/octet-stream").strip() or "application/octet-stream",
                    "sizeBytes": len(raw_bytes),
                    "relativePath": str(target_path.relative_to(REPORTS_ROOT)).replace("\\", "/"),
                }
            )

        return descriptors

    def _store_report(self, report_type: str, summary: dict[str, Any], payload: dict[str, Any], attachments: list[ReportAttachmentRequest]) -> dict[str, Any]:
        created_at_epoch = int(summary.get("createdAtEpoch", 0) or now_ts())
        report_id = f"uf_{report_type}_{created_at_epoch}_{secrets.token_hex(4)}"
        report_dir = REPORTS_ROOT / report_id
        report_dir.mkdir(parents=True, exist_ok=True)

        stored_attachments = self._write_report_attachments(report_dir, attachments)
        stored_summary = dict(summary)
        stored_summary["reportId"] = report_id
        stored_summary["reportType"] = report_type
        stored_summary["createdAtEpoch"] = created_at_epoch
        stored_summary["attachmentCount"] = len(stored_attachments)
        stored_summary["attachments"] = stored_attachments
        stored_summary["storagePath"] = str(report_dir)

        payload_to_write = dict(payload)
        payload_to_write["reportId"] = report_id
        payload_to_write["attachments"] = stored_attachments
        payload_to_write["storedAtEpoch"] = now_ts()

        (report_dir / "summary.json").write_text(json.dumps(stored_summary, indent=2), encoding="utf-8")
        (report_dir / "payload.json").write_text(json.dumps(payload_to_write, indent=2), encoding="utf-8")

        if report_type == "crash":
            target_store = self.crash_reports
        elif report_type == "runtime_diagnostic":
            target_store = self.runtime_diagnostics
        else:
            target_store = self.feedback_reports
        target_store[report_id] = stored_summary
        self.persist()
        return {
            "status": "ok",
            "reportId": report_id,
            "receiptId": report_id,
            "reportType": report_type,
            "createdAtEpoch": created_at_epoch,
            "attachmentCount": len(stored_attachments),
        }

    def list_recent_reports(self, limit: int = REPORT_RECENT_LIMIT) -> dict[str, Any]:
        rows = (
            list(self.feedback_reports.values())
            + list(self.crash_reports.values())
            + list(self.runtime_diagnostics.values())
        )
        rows.sort(key=lambda row: (int(row.get("createdAtEpoch", 0)), str(row.get("reportId", ""))), reverse=True)
        clean_limit = max(1, min(int(limit or REPORT_RECENT_LIMIT), 200))
        return {
            "status": "ok",
            "reports": rows[:clean_limit],
            "feedbackCount": len(self.feedback_reports),
            "crashCount": len(self.crash_reports),
            "runtimeDiagnosticCount": len(self.runtime_diagnostics),
        }

    def submit_player_feedback(self, req: PlayerFeedbackRequest) -> dict[str, Any]:
        created_at_epoch = int(req.created_at_epoch or now_ts())
        preview = " ".join(str(req.text or "").split())[:220]
        attachments = [req.screenshot] if req.screenshot else []
        summary = {
            "category": str(req.category or "other").strip().lower() or "other",
            "playerDisplayName": str(req.display_name or "Burrow Ghost").strip() or "Burrow Ghost",
            "accountId": str(req.account_id or "").strip(),
            "playerId": str(req.player_id or "").strip(),
            "buildStamp": str(req.build_stamp or "dev-local").strip() or "dev-local",
            "sceneName": str(req.scene_name or "").strip(),
            "sceneMode": str(req.scene_mode or "").strip(),
            "matchId": str(req.match_id or "").strip(),
            "roomCode": str(req.room_code or "").strip().upper(),
            "assignmentId": str(req.assignment_id or "").strip(),
            "assignmentRoute": str(req.assignment_route or "").strip(),
            "voiceMode": str(req.voice_mode or "").strip(),
            "voiceConnectionState": str(req.voice_connection_state or "").strip(),
            "preview": preview,
            "severity": "medium" if str(req.category or "").strip().lower() in {"audio/voice", "performance", "bug"} else "low",
            "createdAtEpoch": created_at_epoch,
        }
        payload = {
            "accountId": req.account_id,
            "playerId": req.player_id,
            "displayName": req.display_name,
            "category": req.category,
            "text": req.text,
            "sceneMode": req.scene_mode,
            "sceneName": req.scene_name,
            "factionKey": req.faction_key,
            "role": req.role,
            "matchId": req.match_id,
            "roomCode": req.room_code,
            "assignmentId": req.assignment_id,
            "assignmentRoute": req.assignment_route,
            "buildStamp": req.build_stamp,
            "voiceMode": req.voice_mode,
            "voiceConnectionState": req.voice_connection_state,
            "runtimeSummary": req.runtime_summary,
            "createdAtEpoch": created_at_epoch,
        }
        return self._store_report("feedback", summary, payload, attachments)

    def submit_crash_report(self, req: CrashReportRequest) -> dict[str, Any]:
        created_at_epoch = int(req.created_at_epoch or now_ts())
        attachments = list(req.attachments)
        severity = "critical" if attachments else "high"
        summary = {
            "category": "crash",
            "playerDisplayName": str(req.display_name or "Burrow Ghost").strip() or "Burrow Ghost",
            "accountId": str(req.account_id or "").strip(),
            "playerId": str(req.player_id or "").strip(),
            "buildStamp": str(req.build_stamp or "dev-local").strip() or "dev-local",
            "sceneName": str(req.scene_name or "").strip(),
            "sceneMode": str(req.scene_mode or "").strip(),
            "matchId": str(req.match_id or "").strip(),
            "roomCode": str(req.room_code or "").strip().upper(),
            "assignmentId": str(req.assignment_id or "").strip(),
            "assignmentRoute": str(req.assignment_route or "").strip(),
            "voiceMode": str(req.voice_mode or "").strip(),
            "voiceConnectionState": str(req.voice_connection_state or "").strip(),
            "preview": "Crash auto-upload bundle captured on next launch.",
            "severity": severity,
            "sessionId": str(req.session_id or "").strip(),
            "createdAtEpoch": created_at_epoch,
        }
        payload = {
            "sessionId": req.session_id,
            "accountId": req.account_id,
            "playerId": req.player_id,
            "displayName": req.display_name,
            "sceneName": req.scene_name,
            "sceneMode": req.scene_mode,
            "matchId": req.match_id,
            "roomCode": req.room_code,
            "assignmentId": req.assignment_id,
            "assignmentRoute": req.assignment_route,
            "buildStamp": req.build_stamp,
            "voiceMode": req.voice_mode,
            "voiceConnectionState": req.voice_connection_state,
            "platformInfo": req.platform_info,
            "runtimeSummary": req.runtime_summary,
            "crashMarker": req.crash_marker,
            "errorBuffer": req.error_buffer,
            "createdAtEpoch": created_at_epoch,
        }
        return self._store_report("crash", summary, payload, attachments)

    def submit_runtime_diagnostic(self, req: RuntimeDiagnosticReportRequest) -> dict[str, Any]:
        created_at_epoch = int(req.created_at_epoch or now_ts())
        events = []
        for event in req.events:
            events.append(
                {
                    "createdAtEpoch": int(event.created_at_epoch or created_at_epoch),
                    "stage": str(event.stage or "").strip(),
                    "severity": str(event.severity or "info").strip().lower() or "info",
                    "detail": str(event.detail or "").strip(),
                    "sceneName": str(event.scene_name or "").strip(),
                    "transportMode": str(event.transport_mode or "").strip(),
                    "connectionState": str(event.connection_state or "").strip(),
                }
            )

        preview = " ".join(str(req.summary or "").split()).strip()
        if not preview and events:
            preview = " ".join(str(events[-1].get("detail", "")).split()).strip()
        preview = preview[:220]
        latest_stage = str(events[-1].get("stage", "")).strip() if events else ""

        summary = {
            "category": "runtime_diagnostic",
            "playerDisplayName": str(req.display_name or "Burrow Ghost").strip() or "Burrow Ghost",
            "accountId": str(req.account_id or "").strip(),
            "playerId": str(req.player_id or "").strip(),
            "buildStamp": str(req.build_stamp or "dev-local").strip() or "dev-local",
            "sceneName": str(req.scene_name or "").strip(),
            "sceneMode": str(req.scene_mode or "").strip(),
            "matchId": str(req.match_id or "").strip(),
            "roomCode": str(req.room_code or "").strip().upper(),
            "assignmentId": str(req.assignment_id or "").strip(),
            "assignmentRoute": str(req.assignment_route or "").strip(),
            "voiceMode": str(req.voice_mode or "").strip(),
            "voiceConnectionState": str(req.voice_connection_state or "").strip(),
            "preview": preview,
            "severity": str(req.severity or "warn").strip().lower() or "warn",
            "sessionId": str(req.session_id or "").strip(),
            "eventCount": len(events),
            "latestStage": latest_stage,
            "createdAtEpoch": created_at_epoch,
        }
        payload = {
            "sessionId": req.session_id,
            "accountId": req.account_id,
            "playerId": req.player_id,
            "displayName": req.display_name,
            "summary": req.summary,
            "severity": req.severity,
            "sceneName": req.scene_name,
            "sceneMode": req.scene_mode,
            "matchId": req.match_id,
            "roomCode": req.room_code,
            "assignmentId": req.assignment_id,
            "assignmentRoute": req.assignment_route,
            "buildStamp": req.build_stamp,
            "voiceMode": req.voice_mode,
            "voiceConnectionState": req.voice_connection_state,
            "runtimeSummary": req.runtime_summary,
            "events": events,
            "createdAtEpoch": created_at_epoch,
        }
        return self._store_report("runtime_diagnostic", summary, payload, [])

    @staticmethod
    def _player_last_presence_epoch(player: dict[str, Any]) -> int:
        return int(player.get("last_presence_epoch", player.get("updated_at", 0)) or 0)

    @staticmethod
    def _runtime_created_or_phase_epoch(runtime: dict[str, Any]) -> int:
        return max(
            int(runtime.get("created_at", 0) or 0),
            int(runtime.get("phase_started_at_epoch", 0) or 0),
        )

    def _runtime_last_meaningful_epoch(
        self,
        runtime: dict[str, Any],
        players: list[dict[str, Any]] | None = None,
        *,
        include_simulated: bool = False,
    ) -> int:
        runtime_players = players if players is not None else list(runtime.get("players", {}).values())
        presence_epochs = [
            self._player_last_presence_epoch(player)
            for player in runtime_players
            if include_simulated or not bool(player.get("is_simulated", False))
        ]
        return max([self._runtime_created_or_phase_epoch(runtime)] + presence_epochs)

    def _player_can_reconnect(self, runtime: dict[str, Any], player: dict[str, Any], now_epoch: int | None = None) -> bool:
        if bool(player.get("is_abandoned", False)) or bool(player.get("is_consumed", False)):
            return False
        if str(runtime.get("phase", "lobby")) == "resolution":
            return False
        now_epoch = now_epoch or now_ts()
        last_presence_epoch = self._player_last_presence_epoch(player)
        if last_presence_epoch <= 0:
            return False
        return now_epoch - last_presence_epoch < DEDICATED_RECONNECT_GRACE_SECONDS

    def _player_is_backfill_eligible(self, runtime: dict[str, Any], player: dict[str, Any], now_epoch: int | None = None) -> bool:
        if str(runtime.get("listing_type", "")).strip().lower() == "private_room":
            return False
        if bool(player.get("is_consumed", False)) or bool(player.get("is_simulated", False)):
            return False
        if bool(player.get("is_abandoned", False)):
            return True
        now_epoch = now_epoch or now_ts()
        last_presence_epoch = self._player_last_presence_epoch(player)
        if last_presence_epoch <= 0:
            return False
        return now_epoch - last_presence_epoch >= DEDICATED_RECONNECT_GRACE_SECONDS

    def _player_counts_for_team(self, runtime: dict[str, Any], player: dict[str, Any], now_epoch: int | None = None) -> bool:
        if bool(player.get("is_consumed", False)):
            return False
        return not self._player_is_backfill_eligible(runtime, player, now_epoch)

    def _refresh_runtime_presence_flags(self, runtime: dict[str, Any], now_epoch: int | None = None) -> None:
        now_epoch = now_epoch or now_ts()
        players = runtime.setdefault("players", {})
        for player in players.values():
            last_presence_epoch = self._player_last_presence_epoch(player)
            if last_presence_epoch <= 0:
                last_presence_epoch = now_epoch
                player["last_presence_epoch"] = last_presence_epoch

            if not bool(player.get("is_abandoned", False)) and now_epoch - last_presence_epoch >= DEDICATED_RECONNECT_GRACE_SECONDS:
                player["is_abandoned"] = True
                player["abandoned_at_epoch"] = now_epoch
                player["abandoned_reason"] = "presence_timeout"

            player["reconnect_grace_expires_at_epoch"] = int(player.get("reconnect_grace_expires_at_epoch", last_presence_epoch + DEDICATED_RECONNECT_GRACE_SECONDS) or (last_presence_epoch + DEDICATED_RECONNECT_GRACE_SECONDS))
            player["is_connected"] = self._player_can_reconnect(runtime, player, now_epoch)
            player["is_backfill_eligible"] = self._player_is_backfill_eligible(runtime, player, now_epoch)

    def _log_team_lobby_breadcrumb(
        self,
        *,
        stage: str,
        severity: str,
        route: str,
        match_id: str,
        reason: str,
        scene: str = "underfoot_team_lobby",
        connection: str = "",
        team_id: int = -1,
        player_id: str = "",
        account_id: str = "",
        extra: str = "",
    ) -> None:
        clean_severity = str(severity or "info").strip().lower()
        log_message = (
            "team_lobby_breadcrumb stage=%s severity=%s scene=%s route=%s match_id=%s team_id=%s "
            "player_id=%s account_id=%s reason=%s connection=%s extra=%s"
        )
        log_args = (
            stage,
            clean_severity,
            scene,
            route,
            match_id,
            team_id,
            player_id,
            account_id,
            reason,
            connection,
            extra,
        )
        if clean_severity in {"warn", "warning"}:
            LOGGER.warning(log_message, *log_args)
        elif clean_severity in {"error", "critical"}:
            LOGGER.error(log_message, *log_args)
        else:
            LOGGER.info(log_message, *log_args)

    def _resolve_runtime_player_for_ready(
        self,
        runtime: dict[str, Any],
        *,
        player_id: str = "",
        account_id: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        players = runtime.setdefault("players", {})
        clean_account_id = str(account_id or "").strip()
        if clean_account_id:
            account_matches = [
                player
                for player in players.values()
                if str(player.get("account_id", "")).strip() == clean_account_id
            ]
            if account_matches:
                account_matches.sort(key=lambda row: (int(row.get("updated_at", 0)), str(row.get("player_id", ""))), reverse=True)
                return account_matches[0], "account_id"

        clean_player_id = str(player_id or "").strip()
        if clean_player_id and clean_player_id in players:
            return players[clean_player_id], "player_id"
        return None, ""

    @staticmethod
    def _slot_sort_key(player: dict[str, Any]) -> tuple[int, str, str]:
        slot_index = int(player.get("slot_index", -1))
        if slot_index < 0:
            slot_index = 999
        return (
            slot_index,
            str(player.get("display_name", "")).strip().lower(),
            str(player.get("player_id", "")).strip(),
        )

    def _get_active_warble_players_for_team(self, runtime: dict[str, Any], team_id: int, now_epoch: int | None = None) -> list[dict[str, Any]]:
        now_epoch = now_epoch or now_ts()
        players = runtime.setdefault("players", {})
        team_players = [
            player
            for player in players.values()
            if str(player.get("role", "warble")).strip() == "warble"
            and int(player.get("team_id", -1)) == int(team_id)
            and self._player_counts_for_team(runtime, player, now_epoch)
        ]
        team_players.sort(key=self._slot_sort_key)
        return team_players

    def _get_active_biggun_players(self, runtime: dict[str, Any]) -> list[dict[str, Any]]:
        players = runtime.setdefault("players", {})
        bigguns = [
            player
            for player in players.values()
            if str(player.get("role", "")).strip() == "biggun"
            and not bool(player.get("is_backfill_eligible", False))
            and not bool(player.get("is_consumed", False))
            and not bool(player.get("is_abandoned", False))
        ]
        bigguns.sort(key=self._slot_sort_key)
        return bigguns

    def _build_team_slot_rows(
        self,
        runtime: dict[str, Any],
        team_id: int,
        *,
        current_account_id: str = "",
        current_player_id: str = "",
        now_epoch: int | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
        now_epoch = now_epoch or now_ts()
        max_team_size = max(3, min(int(runtime.get("max_team_size", 6)), 6))
        team_players = self._get_active_warble_players_for_team(runtime, team_id, now_epoch)
        normalized_players: list[dict[str, Any]] = []
        open_slot_indices = list(range(max_team_size))
        for player in team_players:
            slot_index = int(player.get("slot_index", -1))
            if slot_index < 0 or slot_index >= max_team_size or slot_index not in open_slot_indices:
                slot_index = open_slot_indices[0] if open_slot_indices else max_team_size - 1
                player["slot_index"] = slot_index
            if slot_index in open_slot_indices:
                open_slot_indices.remove(slot_index)
            normalized_players.append(player)
        normalized_players.sort(key=self._slot_sort_key)
        captain = normalized_players[0] if normalized_players else None
        ready_players = [player for player in normalized_players if bool(player.get("is_ready", False))]
        slot_by_index = {int(player.get("slot_index", -1)): player for player in normalized_players if int(player.get("slot_index", -1)) >= 0}
        slots: list[dict[str, Any]] = []
        required_slots = max(3, int(runtime.get("min_team_size", 3)))
        for slot_index in range(max_team_size):
            player = slot_by_index.get(slot_index)
            if player:
                player_id = str(player.get("player_id", "")).strip()
                account_id = str(player.get("account_id", "")).strip()
                is_ready = bool(player.get("is_ready", False))
                slots.append(
                    {
                        "slotId": f"team_{team_id}_slot_{slot_index}",
                        "role": "warble",
                        "teamId": team_id,
                        "slotIndex": slot_index,
                        "status": "ready" if is_ready else "filled",
                        "isOpen": False,
                        "isFilled": True,
                        "isReady": is_ready,
                        "isCaptain": captain is not None and str(captain.get("player_id", "")) == player_id,
                        "isRequired": slot_index < required_slots,
                        "isSimulated": bool(player.get("is_simulated", False)),
                        "isHost": bool(player.get("is_host", False)),
                        "isCurrentPlayer": (current_player_id and player_id == current_player_id) or (current_account_id and account_id == current_account_id),
                        "statusLabel": "READY" if is_ready else "FILLED",
                        "playerId": player_id,
                        "accountId": account_id,
                        "displayName": str(player.get("display_name", "")).strip(),
                        "factionKey": str(player.get("faction_key", "")).strip(),
                    }
                )
            else:
                slots.append(
                    {
                        "slotId": f"team_{team_id}_slot_{slot_index}",
                        "role": "warble",
                        "teamId": team_id,
                        "slotIndex": slot_index,
                        "status": "open",
                        "isOpen": True,
                        "isFilled": False,
                        "isReady": False,
                        "isCaptain": False,
                        "isRequired": slot_index < required_slots,
                        "isSimulated": False,
                        "isHost": False,
                        "isCurrentPlayer": False,
                        "statusLabel": "OPEN",
                        "playerId": "",
                        "accountId": "",
                        "displayName": "",
                        "factionKey": LIVE_TEAM_FACTION_ORDER[team_id % len(LIVE_TEAM_FACTION_ORDER)],
                    }
                )
        return slots, captain, normalized_players, ready_players

    def _build_biggun_slot_rows(
        self,
        runtime: dict[str, Any],
        *,
        current_account_id: str = "",
        current_player_id: str = "",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        required_slots = self.compute_required_biggun_slots(runtime)
        biggun_players = self._get_active_biggun_players(runtime)
        ready_biggun_players = [player for player in biggun_players if bool(player.get("is_ready", False))]
        pending_biggun_players = [player for player in biggun_players if not bool(player.get("is_ready", False))]
        slot_by_index: dict[int, dict[str, Any]] = {}
        next_slot_index = 0
        for player in biggun_players:
            slot_index = int(player.get("slot_index", -1))
            if slot_index < 0 or slot_index >= required_slots or slot_index in slot_by_index:
                while next_slot_index in slot_by_index and next_slot_index < required_slots:
                    next_slot_index += 1
                slot_index = min(next_slot_index, max(required_slots - 1, 0))
                player["slot_index"] = slot_index
            slot_by_index[slot_index] = player
        slots: list[dict[str, Any]] = []
        for slot_index in range(required_slots):
            player = slot_by_index.get(slot_index)
            if player:
                player_id = str(player.get("player_id", "")).strip()
                account_id = str(player.get("account_id", "")).strip()
                is_ready = bool(player.get("is_ready", False))
                slots.append(
                    {
                        "slotId": f"biggun_slot_{slot_index}",
                        "role": "biggun",
                        "teamId": -1,
                        "slotIndex": slot_index,
                        "status": "ready" if is_ready else "filled",
                        "isOpen": False,
                        "isFilled": True,
                        "isReady": is_ready,
                        "isCaptain": False,
                        "isRequired": True,
                        "isSimulated": bool(player.get("is_simulated", False)),
                        "isHost": bool(player.get("is_host", False)),
                        "isCurrentPlayer": (current_player_id and player_id == current_player_id) or (current_account_id and account_id == current_account_id),
                        "statusLabel": "READY" if is_ready else "FILLED",
                        "playerId": player_id,
                        "accountId": account_id,
                        "displayName": str(player.get("display_name", "")).strip(),
                        "factionKey": str(player.get("faction_key", "giant")).strip() or "giant",
                    }
                )
            else:
                slots.append(
                    {
                        "slotId": f"biggun_slot_{slot_index}",
                        "role": "biggun",
                        "teamId": -1,
                        "slotIndex": slot_index,
                        "status": "open",
                        "isOpen": True,
                        "isFilled": False,
                        "isReady": False,
                        "isCaptain": False,
                        "isRequired": True,
                        "isSimulated": False,
                        "isHost": False,
                        "isCurrentPlayer": False,
                        "statusLabel": "OPEN",
                        "playerId": "",
                        "accountId": "",
                        "displayName": "",
                        "factionKey": "giant",
                    }
                )
        return slots, ready_biggun_players, pending_biggun_players

    def _clear_team_ready_confirmation(self, runtime: dict[str, Any], team_id: int) -> None:
        confirmations = runtime.setdefault("team_ready_confirmations", {})
        confirmations.pop(str(team_id), None)

    def _find_first_open_team_slot(self, runtime: dict[str, Any], team_id: int, *, exclude_player_id: str = "") -> int:
        max_team_size = max(3, min(int(runtime.get("max_team_size", 6)), 6))
        occupied: set[int] = set()
        for player in runtime.setdefault("players", {}).values():
            if str(player.get("role", "warble")).strip() != "warble":
                continue
            if int(player.get("team_id", -1)) != int(team_id):
                continue
            if exclude_player_id and str(player.get("player_id", "")).strip() == exclude_player_id:
                continue
            if not self._player_counts_for_team(runtime, player):
                continue
            slot_index = int(player.get("slot_index", -1))
            if 0 <= slot_index < max_team_size:
                occupied.add(slot_index)
        for slot_index in range(max_team_size):
            if slot_index not in occupied:
                return slot_index
        return -1

    def _find_first_open_biggun_slot(self, runtime: dict[str, Any], *, exclude_player_id: str = "") -> int:
        required_slots = self.compute_required_biggun_slots(runtime)
        occupied: set[int] = set()
        for player in runtime.setdefault("players", {}).values():
            if str(player.get("role", "")).strip() != "biggun":
                continue
            if exclude_player_id and str(player.get("player_id", "")).strip() == exclude_player_id:
                continue
            if bool(player.get("is_backfill_eligible", False)) or bool(player.get("is_consumed", False)):
                continue
            slot_index = int(player.get("slot_index", -1))
            if 0 <= slot_index < required_slots:
                occupied.add(slot_index)
        for slot_index in range(required_slots):
            if slot_index not in occupied:
                return slot_index
        return -1

    def _build_team_lobby_contract_state(self, runtime: dict[str, Any], now_epoch: int | None = None) -> dict[str, Any]:
        now_epoch = now_epoch or now_ts()
        team_count = max(3, int(runtime.get("team_count", 3)))
        min_team_size = max(3, int(runtime.get("min_team_size", 3)))
        max_team_size = max(3, min(int(runtime.get("max_team_size", 6)), 6))
        required_team_count = 3 if team_count >= 3 else team_count
        team_rows: list[dict[str, Any]] = []
        team_ready_flags: dict[str, bool] = {}
        ready_team_ids: list[int] = []
        confirmed_team_ids: list[int] = []
        active_team_ids: list[int] = []
        confirmations = runtime.setdefault("team_ready_confirmations", {})

        for team_id in range(team_count):
            slots, captain, team_players, ready_players = self._build_team_slot_rows(runtime, team_id, now_epoch=now_epoch)
            if team_players:
                active_team_ids.append(team_id)
            roster_ready = len(team_players) >= min_team_size and len(ready_players) >= min_team_size
            if roster_ready:
                ready_team_ids.append(team_id)
            captain_player_id = str(captain.get("player_id", "")).strip() if captain else ""
            captain_account_id = str(captain.get("account_id", "")).strip() if captain else ""
            ready_player_ids = {
                str(player.get("player_id", "")).strip()
                for player in ready_players
                if str(player.get("player_id", "")).strip()
            }
            captain_ready = bool(captain_player_id) and captain_player_id in ready_player_ids
            confirmation_key = str(confirmations.get(str(team_id), "")).strip()
            if roster_ready and captain_ready and captain_player_id:
                confirmations[str(team_id)] = captain_player_id
                confirmation_key = captain_player_id
            else:
                confirmations.pop(str(team_id), None)
                confirmation_key = ""
            team_confirmed = bool(captain_player_id) and captain_ready and roster_ready
            if team_confirmed:
                confirmed_team_ids.append(team_id)
            else:
                confirmations.pop(str(team_id), None)
            team_ready_flags[str(team_id)] = team_confirmed
            team_rows.append(
                {
                    "teamId": team_id,
                    "label": f"Team {LIVE_TEAM_FACTION_ORDER[team_id % len(LIVE_TEAM_FACTION_ORDER)].capitalize()}",
                    "factionKey": LIVE_TEAM_FACTION_ORDER[team_id % len(LIVE_TEAM_FACTION_ORDER)],
                    "playerCount": len(team_players),
                    "readyCount": len(ready_players),
                    "humanPlayerCount": sum(1 for player in team_players if not bool(player.get("is_simulated", False))),
                    "simulatedPlayerCount": sum(1 for player in team_players if bool(player.get("is_simulated", False))),
                    "isReady": roster_ready,
                    "isActive": bool(team_players),
                    "isFull": len(team_players) >= max_team_size,
                    "minTeamSize": min_team_size,
                    "maxTeamSize": max_team_size,
                    "captainPlayerId": captain_player_id,
                    "captainAccountId": captain_account_id,
                    "teamReadyConfirmed": team_confirmed,
                    "slots": slots,
                    "playerIds": sorted(str(player.get("player_id", "")) for player in team_players),
                    "readyPlayerIds": sorted(str(player.get("player_id", "")) for player in ready_players),
                }
            )

        required_biggun_slots = self.compute_required_biggun_slots(runtime)
        biggun_slots, ready_biggun_players, pending_biggun_players = self._build_biggun_slot_rows(runtime)
        biggun_state = {
            "requiredSlots": required_biggun_slots,
            "activeCount": len(ready_biggun_players) + len(pending_biggun_players),
            "readyCount": len(ready_biggun_players),
            "pendingCount": len(pending_biggun_players),
            "isReady": len(ready_biggun_players) >= required_biggun_slots,
            "readyPlayerIds": sorted(str(player.get("player_id", "")) for player in ready_biggun_players),
            "pendingPlayerIds": sorted(str(player.get("player_id", "")) for player in pending_biggun_players),
            "slots": biggun_slots,
        }

        countdown_seconds = max(0, int(runtime.get("team_lobby_countdown_seconds", TEAM_LOBBY_COUNTDOWN_SECONDS)))
        countdown_started_at = int(runtime.get("team_lobby_countdown_started_at_epoch", 0))
        countdown_completed_at = int(runtime.get("team_lobby_countdown_completed_at_epoch", 0))
        countdown_remaining = 0
        countdown_active = countdown_started_at > 0 and str(runtime.get("phase", "lobby")) == "lobby"
        if countdown_active and countdown_seconds > 0:
            countdown_remaining = max(0, countdown_started_at + countdown_seconds - now_epoch)
        start_blockers: list[str] = []
        if team_count < 3:
            start_blockers.append("team_count_below_three")
        if len(ready_team_ids) < required_team_count:
            start_blockers.append(f"teams_ready:{len(ready_team_ids)}/{required_team_count}")
        if len(confirmed_team_ids) < required_team_count:
            start_blockers.append(f"teams_confirmed:{len(confirmed_team_ids)}/{required_team_count}")
        if not biggun_state["isReady"]:
            start_blockers.append(f"biggun_ready:{biggun_state['readyCount']}/{required_biggun_slots}")
        if countdown_started_at <= 0 and not start_blockers:
            countdown_state = {
                "state": "armed",
                "isActive": False,
                "isComplete": False,
                "durationSeconds": countdown_seconds,
                "startedAtEpoch": 0,
                "endsAtEpoch": 0,
                "remainingSeconds": countdown_seconds,
                "completedAtEpoch": countdown_completed_at,
                "reason": "armed_for_auto_start",
            }
        elif countdown_active:
            countdown_state = {
                "state": "countdown",
                "isActive": True,
                "isComplete": countdown_remaining <= 0,
                "durationSeconds": countdown_seconds,
                "startedAtEpoch": countdown_started_at,
                "endsAtEpoch": countdown_started_at + countdown_seconds,
                "remainingSeconds": countdown_remaining,
                "completedAtEpoch": countdown_completed_at,
                "reason": str(runtime.get("team_lobby_countdown_reason", "")),
            }
        else:
            countdown_state = {
                "state": "blocked",
                "isActive": False,
                "isComplete": False,
                "durationSeconds": countdown_seconds,
                "startedAtEpoch": countdown_started_at,
                "endsAtEpoch": 0,
                "remainingSeconds": 0,
                "completedAtEpoch": countdown_completed_at,
                "reason": str(runtime.get("team_lobby_countdown_reason", "")) or ";".join(start_blockers),
            }

        start_gate_ready = not start_blockers and countdown_seconds >= 0
        lobby_state = {
            "teamCount": team_count,
            "minTeamSize": min_team_size,
            "maxTeamSize": max_team_size,
            "slotsPerTeam": max_team_size,
            "requiredTeamCount": required_team_count,
            "activeTeamCount": len(active_team_ids),
            "readyTeamCount": len(ready_team_ids),
            "confirmedTeamCount": len(confirmed_team_ids),
            "readyTeamIds": ready_team_ids,
            "confirmedTeamIds": confirmed_team_ids,
            "teamReadyFlags": team_ready_flags,
            "teams": team_rows,
            "biggunState": biggun_state,
            "countdownState": countdown_state,
            "startGateReady": start_gate_ready,
            "startGateBlockers": start_blockers,
            "countdownSeconds": countdown_seconds,
        }
        return {
            "team_lobby_state": lobby_state,
            "team_ready_flags": team_ready_flags,
            "biggun_state": biggun_state,
            "countdown_state": countdown_state,
            "start_gate_ready": start_gate_ready,
            "start_gate_blockers": start_blockers,
            "ready_team_ids": ready_team_ids,
            "confirmed_team_ids": confirmed_team_ids,
            "required_team_count": required_team_count,
            "required_biggun_slots": required_biggun_slots,
        }

    def _advance_team_lobby_start_gate(self, runtime: dict[str, Any], *, route: str, now_epoch: int | None = None) -> dict[str, Any]:
        now_epoch = now_epoch or now_ts()
        contract_state = self._build_team_lobby_contract_state(runtime, now_epoch)
        start_gate_ready = bool(contract_state["start_gate_ready"])
        start_gate_blockers = list(contract_state["start_gate_blockers"])
        countdown_state = dict(contract_state["countdown_state"])
        countdown_seconds = int(countdown_state.get("durationSeconds", 0))
        previous_started_at = int(runtime.get("team_lobby_countdown_started_at_epoch", 0))
        previous_reason = str(runtime.get("team_lobby_countdown_reason", ""))
        phase = str(runtime.get("phase", "lobby")).strip().lower() or "lobby"

        if phase != "lobby":
            runtime["team_lobby_countdown_started_at_epoch"] = 0
            runtime["team_lobby_countdown_reason"] = ""
            runtime["team_lobby_countdown_completed_at_epoch"] = 0
            return contract_state

        if start_gate_ready:
            if previous_started_at <= 0:
                runtime["team_lobby_countdown_started_at_epoch"] = now_epoch
                runtime["team_lobby_countdown_completed_at_epoch"] = 0
                runtime["team_lobby_countdown_reason"] = "all_three_teams_ready_and_biggun_ready"
                self._log_team_lobby_breadcrumb(
                    stage="team_lobby_countdown_start",
                    severity="info",
                    route=route,
                    match_id=str(runtime.get("match_id", "")),
                    reason="countdown_started",
                    connection=f"teams={contract_state['team_lobby_state']['readyTeamCount']}/{contract_state['team_lobby_state']['requiredTeamCount']} biggun={contract_state['biggun_state']['readyCount']}/{contract_state['biggun_state']['requiredSlots']}",
                )
                if countdown_seconds <= 0:
                    runtime["phase"] = "spawn_selection"
                    runtime["phase_started_at_epoch"] = now_epoch
                    runtime["team_lobby_countdown_started_at_epoch"] = 0
                    runtime["team_lobby_countdown_completed_at_epoch"] = now_epoch
                    runtime["team_lobby_countdown_reason"] = "countdown_elapsed"
                    self._log_team_lobby_breadcrumb(
                        stage="team_lobby_start",
                        severity="info",
                        route=route,
                        match_id=str(runtime.get("match_id", "")),
                        reason="lobby_countdown_completed",
                        connection=f"phase=spawn_selection teams={contract_state['team_lobby_state']['readyTeamCount']}/{contract_state['team_lobby_state']['requiredTeamCount']} biggun={contract_state['biggun_state']['readyCount']}/{contract_state['biggun_state']['requiredSlots']}",
                    )
            elif countdown_seconds <= 0 or now_epoch - previous_started_at >= countdown_seconds:
                runtime["phase"] = "spawn_selection"
                runtime["phase_started_at_epoch"] = now_epoch
                runtime["team_lobby_countdown_started_at_epoch"] = 0
                runtime["team_lobby_countdown_completed_at_epoch"] = now_epoch
                runtime["team_lobby_countdown_reason"] = "countdown_elapsed"
                self._log_team_lobby_breadcrumb(
                    stage="team_lobby_start",
                    severity="info",
                    route=route,
                    match_id=str(runtime.get("match_id", "")),
                    reason="lobby_countdown_completed",
                    connection=f"phase=spawn_selection teams={contract_state['team_lobby_state']['readyTeamCount']}/{contract_state['team_lobby_state']['requiredTeamCount']} biggun={contract_state['biggun_state']['readyCount']}/{contract_state['biggun_state']['requiredSlots']}",
                )
        else:
            if previous_started_at > 0 or previous_reason != ";".join(start_gate_blockers):
                runtime["team_lobby_countdown_started_at_epoch"] = 0
                runtime["team_lobby_countdown_completed_at_epoch"] = 0
                runtime["team_lobby_countdown_reason"] = ";".join(start_gate_blockers)
                self._log_team_lobby_breadcrumb(
                    stage="team_lobby_countdown_reset",
                    severity="warning",
                    route=route,
                    match_id=str(runtime.get("match_id", "")),
                    reason="countdown_blocked",
                    connection=f"blockers={';'.join(start_gate_blockers) or 'none'}",
                )

        return self._build_team_lobby_contract_state(runtime, now_epoch)

    def _find_active_memberships(self, account: dict[str, Any]) -> list[dict[str, Any]]:
        account_id = str(account.get("account_id", "")).strip()
        player_id = str(account.get("player_id", "")).strip()
        memberships: list[dict[str, Any]] = []
        for runtime in self.match_runtimes.values():
            self._refresh_runtime_presence_flags(runtime)
            for runtime_player in runtime.get("players", {}).values():
                if account_id and str(runtime_player.get("account_id", "")).strip() == account_id:
                    memberships.append({"runtime": runtime, "player": runtime_player})
                    continue
                if player_id and str(runtime_player.get("player_id", "")).strip() == player_id:
                    memberships.append({"runtime": runtime, "player": runtime_player})
        memberships.sort(
            key=lambda row: (
                0 if str(row["runtime"].get("listing_type", "")).strip().lower() != "private_room" else 1,
                -int(row["runtime"].get("updated_at", 0)),
            )
        )
        return memberships

    def _build_active_assignment_resume_state(self, account: dict[str, Any]) -> dict[str, Any]:
        memberships = self._find_active_memberships(account)
        if not memberships:
            return {
                "hasActiveAssignment": False,
                "canReconnect": False,
                "isCombatMatch": False,
                "isPrivateRoom": False,
                "matchId": "",
                "roomCode": "",
                "reconnectGraceExpiresAtEpoch": 0,
                "abandonmentState": "none",
                "assignment": None,
                "promptTitle": "",
                "promptBody": "",
            }

        membership = memberships[0]
        runtime = membership["runtime"]
        player = membership["player"]
        is_room = str(runtime.get("listing_type", "")).strip().lower() == "private_room"
        room_code = ""
        if is_room:
            for code, room in self.private_rooms.items():
                if str(room.get("match_id", "")).strip() == str(runtime.get("match_id", "")).strip():
                    room_code = code
                    break
        can_reconnect = self._player_can_reconnect(runtime, player)
        room_joinable = bool(self.private_rooms.get(room_code, {}).get("joinable", False)) if is_room and room_code else False
        assignment = None
        if can_reconnect and (not is_room or room_joinable):
            assignment = self.get_room_assignment(room_code) if is_room and room_code else self.get_match_assignment(str(runtime.get("match_id", "")))

        abandonment_state = "abandoned" if bool(player.get("is_abandoned", False)) else "active"
        return {
            "hasActiveAssignment": can_reconnect if not is_room else (can_reconnect and room_joinable),
            "canReconnect": can_reconnect if not is_room else (can_reconnect and room_joinable),
            "isCombatMatch": not is_room,
            "isPrivateRoom": is_room,
            "matchId": str(runtime.get("match_id", "")).strip(),
            "roomCode": room_code,
            "reconnectGraceExpiresAtEpoch": int(player.get("reconnect_grace_expires_at_epoch", 0) or 0),
            "abandonmentState": abandonment_state,
            "assignment": assignment,
            "promptTitle": "You are currently in a game" if not is_room else "You are currently in a room",
            "promptBody": "Reconnect to the live match or leave it so the slot can be backfilled." if not is_room else "Reconnect to the active room or leave it and clear the attachment.",
            "phase": str(runtime.get("phase", "lobby")).strip(),
        }

    def get_active_assignment_resume_state(self, auth_token: str) -> dict[str, Any]:
        account = self._resolve_account_from_token(auth_token)
        return {"status": "ok", **self._build_active_assignment_resume_state(account)}

    def reconnect_active_assignment(self, req: SessionReconnectRequest) -> dict[str, Any]:
        account = self._resolve_account_from_token(req.auth_token)
        memberships = self._find_active_memberships(account)
        if not memberships:
            raise HTTPException(status_code=404, detail="No active Underfoot assignment was found for this session.")

        membership = memberships[0]
        runtime = membership["runtime"]
        player = membership["player"]
        if not self._player_can_reconnect(runtime, player):
            raise HTTPException(status_code=409, detail="The reconnect grace window has expired for this Underfoot assignment.")

        player["last_presence_epoch"] = now_ts()
        player["is_connected"] = True
        player["reconnect_grace_expires_at_epoch"] = now_ts() + DEDICATED_RECONNECT_GRACE_SECONDS
        runtime["updated_at"] = now_ts()
        self.persist()
        state = self._build_active_assignment_resume_state(account)
        return {"status": "ok", **state, "runtime": self.build_match_runtime_snapshot(str(runtime.get("match_id", "")))}

    def leave_active_match(self, req: LeaveActiveMatchRequest) -> dict[str, Any]:
        account = self._resolve_account_from_token(req.auth_token)
        memberships = self._find_active_memberships(account)
        if not memberships:
            return {
                "status": "ok",
                "cleared": True,
                "matchId": "",
                "roomCode": "",
                "backfillEligible": False,
                "abandonmentState": "none",
                "detail": "No active assignment remained.",
            }

        membership = memberships[0]
        runtime = membership["runtime"]
        player = membership["player"]
        is_room = str(runtime.get("listing_type", "")).strip().lower() == "private_room"
        room_code = ""
        if is_room:
            for code, room in self.private_rooms.items():
                if str(room.get("match_id", "")).strip() == str(runtime.get("match_id", "")).strip():
                    room_code = code
                    room["occupancy"] = max(0, int(room.get("occupancy", 0)) - 1)
                    break
            runtime.setdefault("players", {}).pop(str(player.get("player_id", "")).strip(), None)
            detail = "Private room attachment cleared."
            backfill_eligible = False
            abandonment_state = "left_room"
        else:
            player["is_abandoned"] = True
            player["abandoned_at_epoch"] = now_ts()
            player["abandoned_reason"] = "player_left"
            player["is_connected"] = False
            player["is_backfill_eligible"] = True
            player["reconnect_grace_expires_at_epoch"] = now_ts()
            detail = "Match slot marked abandoned and backfillable."
            backfill_eligible = True
            abandonment_state = "abandoned"

        runtime["updated_at"] = now_ts()
        self.persist()
        return {
            "status": "ok",
            "cleared": True,
            "matchId": str(runtime.get("match_id", "")).strip(),
            "roomCode": room_code,
            "backfillEligible": backfill_eligible,
            "abandonmentState": abandonment_state,
            "detail": detail,
        }

    def heartbeat_match_presence(self, match_id: str, req: PresenceHeartbeatRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        player = runtime.setdefault("players", {}).get(req.player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Unknown runtime player. Rejoin the roster and try again.")

        if str(player.get("abandoned_reason", "")).strip() == "player_left":
            raise HTTPException(status_code=409, detail="This player left the active match and can no longer reconnect.")

        now_epoch = now_ts()
        player["last_presence_epoch"] = now_epoch
        player["is_connected"] = True
        player["is_abandoned"] = False
        player["abandoned_at_epoch"] = 0
        player["abandoned_reason"] = ""
        player["is_backfill_eligible"] = False
        player["reconnect_grace_expires_at_epoch"] = now_epoch + DEDICATED_RECONNECT_GRACE_SECONDS
        if req.scene_name:
            player["last_scene_name"] = str(req.scene_name).strip()
        if req.voice_mode:
            player["last_voice_mode"] = str(req.voice_mode).strip()
        if req.voice_connection_state:
            player["last_voice_connection_state"] = str(req.voice_connection_state).strip()
        runtime["updated_at"] = now_epoch
        self.persist()
        return {
            "status": "ok",
            "playerId": req.player_id,
            "matchId": match_id,
            "reconnectGraceExpiresAtEpoch": player["reconnect_grace_expires_at_epoch"],
        }

    def map_by_id(self, map_id: str) -> dict[str, Any]:
        for row in MAPS:
            if row["map_id"] == map_id:
                return row
        raise HTTPException(status_code=404, detail="Unknown map")

    def choose_spawn(self, map_row: dict[str, Any], preferred: str | None) -> dict[str, Any]:
        if preferred:
            for spawn in map_row.get("spawns", []):
                if spawn.get("id") == preferred:
                    return spawn
        return dict((map_row.get("spawns") or [{}])[0])

    def make_event(
        self,
        kind: str,
        session_id: str,
        map_id: str | None,
        note: str,
        action: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_key": f"evt_{now_ts()}_{secrets.token_hex(4)}",
            "kind": kind,
            "session_id": session_id,
            "map_id": map_id,
            "note": note,
            "ts": now_ts(),
        }
        if action:
            event["action"] = action
        if payload:
            event.update(payload)
        return event

    def resolve_match_listing(self, match_id: str) -> dict[str, Any]:
        clean_match_id = (match_id or "").strip()
        if not clean_match_id:
            raise HTTPException(status_code=400, detail="Match id is required.")

        listing = self.setup_matches.get(clean_match_id)
        if listing:
            return dict(listing)

        for row in self.list_quickplay():
            if str(row.get("match_id", "")).strip() == clean_match_id:
                return dict(row)

        runtime = self.match_runtimes.get(clean_match_id)
        if runtime:
            required_biggun_slots = self.compute_required_biggun_slots(runtime)
            return {
                "match_id": clean_match_id,
                "map_id": runtime.get("map_id", "blackpot_hollow"),
                "mode_key": runtime.get("mode_key", "underfoot_full_test"),
                "team_count": int(runtime.get("team_count", 3)),
                "min_team_size": int(runtime.get("min_team_size", 3)),
                "max_team_size": int(runtime.get("max_team_size", 6)),
                "max_players": int(runtime.get("max_players", 20)),
                "open_biggun_slots": required_biggun_slots,
                "display_label": runtime.get("display_label", clean_match_id),
                "listing_type": runtime.get("listing_type", "quickplay"),
            }

        raise HTTPException(status_code=404, detail="Unknown match.")

    def build_dedicated_server_snapshot(self, server: dict[str, Any]) -> dict[str, Any]:
        return {
            "server_id": str(server.get("server_id", "")).strip(),
            "label": str(server.get("label", "Olympus Underfoot DS")).strip() or "Olympus Underfoot DS",
            "public_host": str(server.get("public_host", DEDICATED_DEFAULT_PUBLIC_HOST)).strip() or DEDICATED_DEFAULT_PUBLIC_HOST,
            "combat_port": int(server.get("combat_port", DEDICATED_DEFAULT_MATCH_PORT)),
            "room_port": int(server.get("room_port", DEDICATED_DEFAULT_ROOM_PORT)),
            "supports_combat": bool(server.get("supports_combat", True)),
            "supports_rooms": bool(server.get("supports_rooms", True)),
            "is_draining": bool(server.get("is_draining", False)),
            "active_assignments": int(server.get("active_assignments", 0)),
            "max_assignments": max(1, int(server.get("max_assignments", DEDICATED_DEFAULT_MAX_ASSIGNMENTS))),
            "last_heartbeat_epoch": int(server.get("last_heartbeat_epoch", 0)),
            "runtime_build_fingerprint": str(server.get("runtime_build_fingerprint", "")).strip(),
            "runtime_build_timestamp_utc": str(server.get("runtime_build_timestamp_utc", "")).strip(),
            "runtime_compatibility_state": str(server.get("runtime_compatibility_state", "unknown")).strip() or "unknown",
        }

    @staticmethod
    def _resolve_runtime_compatibility_state(server: dict[str, Any]) -> str:
        fingerprint = str(server.get("runtime_build_fingerprint", "")).strip()
        explicit = str(server.get("runtime_compatibility_state", "")).strip()
        if explicit:
            return explicit
        return "ready" if fingerprint else "unknown"

    def _summarize_dedicated_runtime_health(self) -> dict[str, Any]:
        fingerprints: dict[str, int] = {}
        latest_timestamp = ""
        stale_assignments = 0
        active_instances = 0

        now_epoch = now_ts()
        for server in self.dedicated_servers.values():
            fingerprint = str(server.get("runtime_build_fingerprint", "")).strip()
            if fingerprint:
                fingerprints[fingerprint] = fingerprints.get(fingerprint, 0) + 1

            timestamp = str(server.get("runtime_build_timestamp_utc", "")).strip()
            if timestamp and (not latest_timestamp or timestamp > latest_timestamp):
                latest_timestamp = timestamp

            if not bool(server.get("is_draining", False)):
                last_heartbeat = int(server.get("last_heartbeat_epoch", 0))
                if last_heartbeat <= 0 or now_epoch - last_heartbeat <= DEDICATED_HEARTBEAT_TIMEOUT_SECONDS:
                    active_instances += 1

        for assignment in self.dedicated_assignments.values():
            if not bool(assignment.get("is_ready", False)) or not self._assignment_route_is_valid(assignment):
                stale_assignments += 1

        if not fingerprints:
            compatibility = "unknown"
            fingerprint = ""
        elif len(fingerprints) == 1:
            compatibility = "aligned"
            fingerprint = next(iter(fingerprints))
        else:
            compatibility = "mixed"
            fingerprint = max(fingerprints.items(), key=lambda item: item[1])[0]

        return {
            "runtimeBuildFingerprint": fingerprint,
            "runtimeBuildTimestampUtc": latest_timestamp,
            "runtimeCompatibilityState": compatibility,
            "activeInstanceCount": active_instances,
            "staleDedicatedAssignmentCount": stale_assignments,
        }

    def _allocator_request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not DEDICATED_ALLOCATOR_URL:
            raise HTTPException(status_code=503, detail="Underfoot allocator URL is not configured.")

        url = f"{DEDICATED_ALLOCATOR_URL}{path}"
        body = None
        headers = {"accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["content-type"] = "application/json"

        request = urllib_request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urllib_request.urlopen(request, timeout=DEDICATED_ALLOCATOR_TIMEOUT_SECONDS) as response:
                raw_body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore").strip()
            raise HTTPException(status_code=503, detail=f"Underfoot allocator returned HTTP {exc.code}: {detail or 'request failed'}") from exc
        except urllib_error.URLError as exc:
            raise HTTPException(status_code=503, detail=f"Underfoot allocator is unreachable: {exc.reason}") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Underfoot allocator request failed: {exc}") from exc

        try:
            decoded = json.loads(raw_body or "{}")
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Underfoot allocator returned invalid JSON: {exc}") from exc

        if not isinstance(decoded, dict):
            raise HTTPException(status_code=503, detail="Underfoot allocator returned an unexpected payload.")
        return decoded

    def _stop_allocator_instance(self, assignment: dict[str, Any]) -> None:
        clean_instance_id = str(assignment.get("instance_id", "")).strip()
        if not clean_instance_id or not DEDICATED_ALLOCATOR_URL:
            return

        try:
            self._allocator_request("POST", f"/api/instances/{clean_instance_id}/stop", {"remove": True})
        except HTTPException as exc:
            LOGGER.warning("Underfoot could not stop allocator instance %s during cleanup: %s", clean_instance_id, exc.detail)
        except Exception as exc:
            LOGGER.warning("Underfoot could not stop allocator instance %s during cleanup: %s", clean_instance_id, exc)

    def _stop_allocator_instance_by_id(self, instance_id: str) -> None:
        clean_instance_id = str(instance_id or "").strip()
        if not clean_instance_id or not DEDICATED_ALLOCATOR_URL:
            return

        try:
            self._allocator_request("POST", f"/api/instances/{clean_instance_id}/stop", {"remove": True})
        except HTTPException as exc:
            LOGGER.warning("Underfoot could not stop allocator instance %s during orphan cleanup: %s", clean_instance_id, exc.detail)
        except Exception as exc:
            LOGGER.warning("Underfoot could not stop allocator instance %s during orphan cleanup: %s", clean_instance_id, exc)

    def _stop_allocator_instances_async(self, assignments: list[dict[str, Any]]) -> None:
        pending = [dict(assignment) for assignment in assignments if str(assignment.get("instance_id", "")).strip()]
        if not pending or not DEDICATED_ALLOCATOR_URL:
            return

        def worker() -> None:
            for assignment in pending:
                self._stop_allocator_instance(assignment)

        threading.Thread(
            target=worker,
            name="underfoot-idle-room-stop",
            daemon=True,
        ).start()

    def _stop_allocator_instance_ids_async(self, instance_ids: list[str]) -> None:
        pending = [str(instance_id or "").strip() for instance_id in instance_ids if str(instance_id or "").strip()]
        if not pending or not DEDICATED_ALLOCATOR_URL:
            return

        def worker() -> None:
            for instance_id in pending:
                self._stop_allocator_instance_by_id(instance_id)

        threading.Thread(
            target=worker,
            name="underfoot-orphan-instance-stop",
            daemon=True,
        ).start()

    def _drop_queue_tickets_for_match_ids(self, match_ids: set[str]) -> None:
        if not match_ids or not self.queue_tickets:
            return

        stale_ticket_ids = [
            ticket_id
            for ticket_id, ticket in self.queue_tickets.items()
            if str(ticket.get("match_id", "")).strip() in match_ids
        ]
        for ticket_id in stale_ticket_ids:
            self.queue_tickets.pop(ticket_id, None)

    def _cleanup_idle_private_rooms(self) -> None:
        if not self.private_rooms:
            return

        now_epoch = now_ts()
        removed_any = False
        stale_room_codes: list[str] = []
        assignments_to_stop: list[dict[str, Any]] = []
        for room_code, room in list(self.private_rooms.items()):
            match_id = str(room.get("match_id", f"{SESSION_PREFIX}_room_{room_code.lower()}")).strip()
            runtime = self.match_runtimes.get(match_id)
            if runtime:
                self._refresh_runtime_presence_flags(runtime, now_epoch)
                runtime_players = list(runtime.get("players", {}).values())
                connected_players = [
                    player
                    for player in runtime_players
                    if self._player_can_reconnect(runtime, player, now_epoch)
                ]
                room["occupancy"] = min(int(room.get("capacity", 8) or 8), len(connected_players))
                room["joinable"] = int(room.get("occupancy", 0)) < int(room.get("capacity", 8) or 8)
                last_presence_epoch = max(
                    [int(room.get("updated_at", room.get("created_at", 0)) or 0)]
                    + [self._runtime_last_meaningful_epoch(runtime, runtime_players, include_simulated=True)]
                )
            else:
                room["occupancy"] = max(0, int(room.get("occupancy", 0) or 0))
                room["joinable"] = int(room.get("occupancy", 0)) < int(room.get("capacity", 8) or 8)
                last_presence_epoch = int(room.get("updated_at", room.get("created_at", 0)) or 0)

            if int(room.get("occupancy", 0) or 0) > 0:
                continue
            if last_presence_epoch <= 0:
                last_presence_epoch = now_epoch
            if now_epoch - last_presence_epoch < DEDICATED_ROOM_IDLE_TIMEOUT_SECONDS:
                continue
            stale_room_codes.append(room_code)

        for room_code in stale_room_codes:
            room = self.private_rooms.pop(room_code, None)
            if not room:
                continue
            match_id = str(room.get("match_id", f"{SESSION_PREFIX}_room_{room_code.lower()}")).strip()
            assignment = self.dedicated_assignments.get(room_code)
            if assignment:
                assignments_to_stop.append(dict(assignment))
                self._drop_stale_assignment(room_code, assignment)
            self.match_runtimes.pop(match_id, None)
            removed_any = True
            LOGGER.info("Closed idle Underfoot private room %s after %ss of inactivity.", room_code, DEDICATED_ROOM_IDLE_TIMEOUT_SECONDS)

        if removed_any:
            self.persist()
            self._stop_allocator_instances_async(assignments_to_stop)

    def _cleanup_idle_setup_matches(self) -> None:
        if not self.setup_matches:
            return

        now_epoch = now_ts()
        removed_any = False
        stale_match_ids: list[str] = []
        assignments_to_stop: list[dict[str, Any]] = []
        for match_id, listing in list(self.setup_matches.items()):
            runtime = self.match_runtimes.get(match_id)
            phase = "lobby"
            last_presence_epoch = int(listing.get("updated_at", listing.get("created_at", 0)) or 0)
            reconnectable_players: list[dict[str, Any]] = []
            if runtime:
                self._refresh_runtime_presence_flags(runtime, now_epoch)
                runtime_players = list(runtime.get("players", {}).values())
                reconnectable_players = [
                    player
                    for player in runtime_players
                    if not bool(player.get("is_simulated", False)) and self._player_can_reconnect(runtime, player, now_epoch)
                ]
                listing["current_players"] = min(
                    int(listing.get("max_players", len(runtime_players)) or len(runtime_players)),
                    len(reconnectable_players),
                )
                phase = str(runtime.get("phase", "lobby")).strip().lower() or "lobby"
                last_presence_epoch = max(last_presence_epoch, self._runtime_last_meaningful_epoch(runtime, runtime_players))
            if reconnectable_players:
                listing["updated_at"] = max(int(listing.get("updated_at", 0) or 0), last_presence_epoch)
                continue

            idle_timeout_seconds = (
                min(DEDICATED_EMPTY_SETUP_IDLE_TIMEOUT_SECONDS, DEDICATED_SETUP_IDLE_TIMEOUT_SECONDS)
                if int(listing.get("current_players", 0) or 0) <= 0
                else (300 if phase in {"resolution", "returning"} else DEDICATED_SETUP_IDLE_TIMEOUT_SECONDS)
            )
            if now_epoch - last_presence_epoch < idle_timeout_seconds:
                continue
            stale_match_ids.append(match_id)

        for match_id in stale_match_ids:
            listing = self.setup_matches.pop(match_id, None)
            if not listing:
                continue
            assignment = self.dedicated_assignments.get(match_id)
            if assignment:
                assignments_to_stop.append(dict(assignment))
                self._drop_stale_assignment(match_id, assignment)
            self.match_runtimes.pop(match_id, None)
            removed_any = True
            LOGGER.info(
                "Closed idle Underfoot setup match %s after %ss of inactivity.",
                match_id,
                DEDICATED_SETUP_IDLE_TIMEOUT_SECONDS,
            )

        if removed_any:
            self._drop_queue_tickets_for_match_ids(set(stale_match_ids))
            self.persist()
            self._stop_allocator_instances_async(assignments_to_stop)

    def _cleanup_stale_combat_matches(self) -> None:
        if not self.match_runtimes:
            return

        now_epoch = now_ts()
        removed_any = False
        stale_rows: list[tuple[str, str, int]] = []
        assignments_to_stop: list[dict[str, Any]] = []
        for match_id, runtime in list(self.match_runtimes.items()):
            listing_type = str(runtime.get("listing_type", "")).strip().lower()
            if listing_type == "private_room":
                continue
            if match_id in self.setup_matches:
                continue

            self._refresh_runtime_presence_flags(runtime, now_epoch)
            runtime_players = list(runtime.get("players", {}).values())
            human_players = [
                player
                for player in runtime_players
                if not bool(player.get("is_simulated", False))
            ]
            reconnectable_humans = [
                player
                for player in runtime_players
                if not bool(player.get("is_simulated", False)) and self._player_can_reconnect(runtime, player, now_epoch)
            ]
            if reconnectable_humans:
                continue

            connected_humans = [
                player
                for player in human_players
                if bool(player.get("is_connected", False))
            ]
            if not human_players or (not connected_humans and all(self._player_is_backfill_eligible(runtime, player, now_epoch) for player in human_players)):
                stale_rows.append((match_id, "empty", 0))
                continue

            phase = str(runtime.get("phase", "lobby")).strip().lower() or "lobby"
            last_presence_epoch = self._runtime_last_meaningful_epoch(runtime, runtime_players)
            idle_timeout_seconds = 300 if phase in {"resolution", "returning"} else DEDICATED_COMBAT_IDLE_TIMEOUT_SECONDS
            if now_epoch - last_presence_epoch < idle_timeout_seconds:
                continue
            stale_rows.append((match_id, phase, idle_timeout_seconds))

        for match_id, phase, idle_timeout_seconds in stale_rows:
            self.match_runtimes.pop(match_id, None)
            assignment = self.dedicated_assignments.get(match_id)
            if assignment:
                assignments_to_stop.append(dict(assignment))
                self._drop_stale_assignment(match_id, assignment)
            removed_any = True
            LOGGER.info(
                "Closed stale Underfoot combat runtime %s (phase=%s) after %ss without reconnectable human players.",
                match_id,
                phase,
                idle_timeout_seconds,
            )

        if removed_any:
            self._drop_queue_tickets_for_match_ids({match_id for match_id, _, _ in stale_rows})
            self.persist()
            self._stop_allocator_instances_async(assignments_to_stop)

    def _cleanup_stale_dedicated_servers(self) -> None:
        if not self.dedicated_servers:
            return

        now_epoch = now_ts()
        removed_any = False
        stale_server_ids: list[str] = []
        orphan_instance_ids: list[str] = []
        active_server_ids = {
            str(assignment.get("server_id", "")).strip()
            for assignment in self.dedicated_assignments.values()
            if str(assignment.get("server_id", "")).strip()
        }

        for server_id, server in list(self.dedicated_servers.items()):
            last_heartbeat_epoch = int(server.get("last_heartbeat_epoch", 0) or 0)
            if last_heartbeat_epoch <= 0:
                continue
            max_assignments = max(1, int(server.get("max_assignments", DEDICATED_DEFAULT_MAX_ASSIGNMENTS) or DEDICATED_DEFAULT_MAX_ASSIGNMENTS))
            active_assignments = max(0, int(server.get("active_assignments", 0) or 0))
            looks_like_dynamic_instance = server_id.startswith("uf_combat_match_") or server_id.startswith("uf_private_room_")
            if looks_like_dynamic_instance and max_assignments == 1 and server_id not in active_server_ids and active_assignments > 0:
                stale_server_ids.append(server_id)
                orphan_instance_ids.append(server_id)
                continue
            if now_epoch - last_heartbeat_epoch <= DEDICATED_HEARTBEAT_TIMEOUT_SECONDS:
                continue
            if server_id in active_server_ids:
                continue
            stale_server_ids.append(server_id)

        for server_id in stale_server_ids:
            self.dedicated_servers.pop(server_id, None)
            removed_any = True
            LOGGER.info(
                "Dropped stale dedicated server registration %s after %ss without heartbeat or active assignments.",
                server_id,
                DEDICATED_HEARTBEAT_TIMEOUT_SECONDS,
            )

        if removed_any:
            self.persist()
            self._stop_allocator_instance_ids_async(orphan_instance_ids)

    def run_maintenance_cleanup(self) -> None:
        self._cleanup_idle_private_rooms()
        self._cleanup_idle_setup_matches()
        self._cleanup_stale_combat_matches()
        self._cleanup_stale_dedicated_servers()

    def list_private_rooms(self) -> list[dict[str, Any]]:
        self.run_maintenance_cleanup()
        rooms = [dict(room) for room in self.private_rooms.values() if bool(room.get("joinable", True))]
        rooms.sort(key=lambda row: (str(row.get("label", "")).lower(), str(row.get("room_code", ""))))
        return rooms

    def list_setup_matches(self) -> list[dict[str, Any]]:
        self.run_maintenance_cleanup()
        visible: list[dict[str, Any]] = []
        for match_id, listing in self.setup_matches.items():
            runtime = self.match_runtimes.get(match_id)
            phase = str(runtime.get("phase", "lobby")).strip().lower() if runtime else "lobby"
            if phase != "lobby":
                continue
            if not bool(listing.get("joinable", True)):
                continue
            if int(listing.get("current_players", 0) or 0) <= 0:
                continue
            enriched_listing = dict(listing)
            snapshot = self.build_match_runtime_snapshot(match_id) if runtime else None
            if snapshot:
                enriched_listing["team_ready_flags"] = dict(snapshot.get("team_ready_flags", {}))
                enriched_listing["countdown_state"] = dict(snapshot.get("countdown_state", {}))
                enriched_listing["biggun_state"] = dict(snapshot.get("biggun_state", {}))
                enriched_listing["lobby_start_gate"] = dict(snapshot.get("lobby_start_gate", {}))
            visible.append(enriched_listing)

        visible.sort(
            key=lambda row: (
                int(row.get("created_at", 0) or 0),
                str(row.get("host_name", "")).lower(),
                str(row.get("match_id", "")),
            )
            )
        return visible

    @staticmethod
    def _browser_state_label(state_key: str) -> str:
        label_map = {
            "forming": "Forming",
            "ready_check": "Ready Check",
            "in_match": "In Match",
            "full": "Full",
        }
        return label_map.get((state_key or "").strip().lower(), "Forming")

    def _estimate_browser_ping_ms(self, listing: dict[str, Any], runtime_snapshot: dict[str, Any] | None = None) -> int:
        runtime_snapshot = runtime_snapshot or {}
        region = str(
            listing.get(
                "region",
                runtime_snapshot.get("region", runtime_snapshot.get("preferred_region", "us-east")),
            )
        ).strip().lower() or "us-east"
        public_host = str(
            runtime_snapshot.get(
                "public_host",
                listing.get("public_host", listing.get("host_name", "")),
            )
        ).strip()

        if is_private_or_loopback_host(public_host):
            base_ping = 18
        elif region in {"us-east", "useast", "na-east"}:
            base_ping = 42
        elif region in {"us-west", "uswest", "na-west"}:
            base_ping = 68
        elif region.startswith("eu"):
            base_ping = 94
        else:
            base_ping = 60

        phase = str(runtime_snapshot.get("phase", listing.get("phase", "lobby"))).strip().lower() or "lobby"
        if phase != "lobby":
            base_ping += 8
        if not bool(listing.get("joinable", True)):
            base_ping += 10
        return max(12, min(base_ping, 180))

    def _build_match_browser_card(
        self,
        listing: dict[str, Any],
        *,
        source_kind: str,
        runtime_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        runtime_snapshot = runtime_snapshot or {}
        phase = str(runtime_snapshot.get("phase", listing.get("phase", "lobby"))).strip().lower() or "lobby"
        current_players = int(runtime_snapshot.get("active_player_count", listing.get("current_players", 0)) or 0)
        max_players = max(1, int(runtime_snapshot.get("max_players", listing.get("max_players", 1)) or 1))
        ready_teams = int(runtime_snapshot.get("ready_teams", listing.get("ready_teams", 0)) or 0)
        active_teams = int(runtime_snapshot.get("active_teams", listing.get("active_teams", 0)) or 0)
        joinable_listing = bool(listing.get("joinable", True))
        countdown_state = dict(runtime_snapshot.get("countdown_state", listing.get("countdown_state", {})))
        start_gate = dict(runtime_snapshot.get("lobby_start_gate", listing.get("lobby_start_gate", {})))
        in_match_phase = phase in {"spawn_selection", "shroom_ritual", "scout", "live", "resolution", "returning"}

        if current_players >= max_players or not joinable_listing:
            state_key = "full"
            joinable = False
            join_lock_reason = "session_full" if current_players >= max_players else "session_locked"
        elif in_match_phase:
            state_key = "in_match"
            joinable = False
            join_lock_reason = "match_in_progress"
        elif bool(countdown_state.get("isActive", False)) or str(countdown_state.get("state", "")).strip().lower() in {"armed", "countdown"} or bool(start_gate.get("can_start", False)):
            state_key = "ready_check"
            joinable = True
            join_lock_reason = ""
        else:
            state_key = "forming"
            joinable = True
            join_lock_reason = ""

        team_summaries = []
        for team in list(dict(runtime_snapshot.get("team_lobby_state", {})).get("teams", [])):
            team_summaries.append(
                {
                    "team_id": int(team.get("teamId", -1)),
                    "label": str(team.get("label", "")),
                    "faction_key": str(team.get("factionKey", "")),
                    "player_count": int(team.get("playerCount", 0)),
                    "ready_count": int(team.get("readyCount", 0)),
                    "is_ready": bool(team.get("isReady", False)),
                    "is_active": bool(team.get("isActive", False)),
                    "is_full": bool(team.get("isFull", False)),
                }
            )

        biggun_state = dict(runtime_snapshot.get("biggun_state", listing.get("biggun_state", {})))
        browser_card = {
            "match_id": str(listing.get("match_id", "")).strip(),
            "source_kind": str(source_kind or "quickplay").strip().lower() or "quickplay",
            "listing_type": str(listing.get("listing_type", "")).strip().lower(),
            "display_label": str(listing.get("display_label", listing.get("host_name", ""))).strip(),
            "host_name": str(listing.get("host_name", "")).strip(),
            "map_id": str(listing.get("map_id", runtime_snapshot.get("map_id", ""))).strip(),
            "mode_key": str(listing.get("mode_key", runtime_snapshot.get("mode_key", ""))).strip(),
            "stage_key": str(listing.get("stage_key", "")).strip(),
            "stage_label": str(listing.get("stage_label", "")).strip(),
            "queue_preset_key": str(listing.get("queue_preset_key", "")).strip(),
            "queue_preset_label": str(listing.get("queue_preset_label", "")).strip(),
            "team_setup_label": str(listing.get("team_setup_label", "")).strip(),
            "queue_destination_label": str(listing.get("queue_destination_label", "")).strip(),
            "region": str(
                listing.get(
                    "region",
                    runtime_snapshot.get("region", runtime_snapshot.get("preferred_region", "us-east")),
                )
            ).strip().lower() or "us-east",
            "ping_estimate_ms": self._estimate_browser_ping_ms(listing, runtime_snapshot),
            "state": state_key,
            "state_label": self._browser_state_label(state_key),
            "joinable": joinable,
            "join_lock_reason": join_lock_reason,
            "current_players": current_players,
            "max_players": max_players,
            "simulated_players": int(listing.get("simulated_players", runtime_snapshot.get("simulated_players", 0)) or 0),
            "team_count": int(listing.get("team_count", runtime_snapshot.get("team_count", 3)) or 3),
            "min_team_size": int(listing.get("min_team_size", runtime_snapshot.get("min_team_size", 3)) or 3),
            "max_team_size": int(listing.get("max_team_size", runtime_snapshot.get("max_team_size", 6)) or 6),
            "active_teams": active_teams,
            "ready_teams": ready_teams,
            "open_biggun_slots": max(0, int(listing.get("open_biggun_slots", runtime_snapshot.get("open_biggun_slots", 1)) or 0)),
            "requires_biggun_ready": bool(listing.get("requires_biggun_ready", True)),
            "runtime_phase": phase,
            "runtime_build_fingerprint": str(runtime_snapshot.get("runtime_build_fingerprint", "")),
            "runtime_build_timestamp_utc": str(runtime_snapshot.get("runtime_build_timestamp_utc", "")),
            "runtime_compatibility_state": str(runtime_snapshot.get("network_decision_gate", {}).get("runtimeCompatibilityState", runtime_snapshot.get("runtime_compatibility_state", "unknown"))).strip() or "unknown",
            "team_summaries": team_summaries,
            "biggun_summary": {
                "required_slots": int(biggun_state.get("requiredSlots", listing.get("open_biggun_slots", 1)) or 1),
                "active_count": int(biggun_state.get("activeCount", 0) or 0),
                "ready_count": int(biggun_state.get("readyCount", 0) or 0),
                "pending_count": int(biggun_state.get("pendingCount", 0) or 0),
                "is_ready": bool(biggun_state.get("isReady", False)),
            },
            "team_ready_flags": dict(runtime_snapshot.get("team_ready_flags", listing.get("team_ready_flags", {}))),
            "countdown_state": countdown_state,
            "lobby_start_gate": start_gate,
            "updated_at_epoch": int(runtime_snapshot.get("updated_at", listing.get("updated_at", now_ts())) or now_ts()),
        }
        return browser_card

    def list_match_browser(self) -> dict[str, Any]:
        self.run_maintenance_cleanup()
        quickplay_listings = self.list_quickplay()
        setup_listings = self.list_setup_matches()

        cards_by_match_id: dict[str, dict[str, Any]] = {}
        for listing in quickplay_listings:
            match_id = str(listing.get("match_id", "")).strip()
            if not match_id:
                continue
            runtime_snapshot = self.build_match_runtime_snapshot(match_id) if self.match_runtimes.get(match_id) else {}
            cards_by_match_id[match_id] = self._build_match_browser_card(listing, source_kind="quickplay", runtime_snapshot=runtime_snapshot)

        for listing in setup_listings:
            match_id = str(listing.get("match_id", "")).strip()
            if not match_id:
                continue
            runtime_snapshot = self.build_match_runtime_snapshot(match_id) if self.match_runtimes.get(match_id) else {}
            cards_by_match_id[match_id] = self._build_match_browser_card(listing, source_kind="setup", runtime_snapshot=runtime_snapshot)

        state_priority = {"forming": 0, "ready_check": 1, "in_match": 2, "full": 3}
        cards = sorted(
            cards_by_match_id.values(),
            key=lambda row: (
                state_priority.get(str(row.get("state", "")).strip().lower(), 99),
                -int(row.get("ready_teams", 0) or 0),
                -int(row.get("current_players", 0) or 0),
                str(row.get("display_label", row.get("match_id", ""))).lower(),
            ),
        )
        state_counts: dict[str, int] = defaultdict(int)
        joinable_count = 0
        for card in cards:
            state_key = str(card.get("state", "forming")).strip().lower() or "forming"
            state_counts[state_key] += 1
            if bool(card.get("joinable", False)):
                joinable_count += 1

        browser = {
            "cards": cards,
            "summary": {
                "card_count": len(cards),
                "joinable_count": joinable_count,
                "state_counts": dict(state_counts),
                "generated_at_epoch": now_ts(),
            },
            "source_counts": {
                "quickplay": len(quickplay_listings),
                "setup_matches": len(setup_listings),
            },
        }
        LOGGER.info(
            "browser_snapshot_received stage=browser_snapshot_received severity=info scene=world_service route=GET /api/matches/browser card_count=%s joinable_count=%s states=%s",
            browser["summary"]["card_count"],
            browser["summary"]["joinable_count"],
            browser["summary"]["state_counts"],
        )
        return browser

    def _build_allocator_boot_payload(
        self,
        *,
        match_id: str,
        map_id: str,
        listing_type: str,
        room_code: str,
        join_ticket: str,
    ) -> dict[str, Any]:
        clean_listing_type = str(listing_type or "").strip().lower()
        is_room = clean_listing_type == "private_room" or bool(room_code.strip())
        runtime = self.match_runtimes.get(match_id.strip(), {}) if match_id.strip() else {}
        team_count = 1 if is_room else max(3, int(runtime.get("team_count", 3)))
        team_size = max(1, int(runtime.get("max_team_size", 6 if not is_room else 20)))
        max_players = max(6 if not is_room else 12, int(runtime.get("max_players", team_count * team_size)))
        voice_mode = resolve_voice_runtime_mode(runtime or {"listing_type": clean_listing_type, "map_id": map_id})
        biggun_slots = 0 if is_room else max(1, int(runtime.get("max_biggun_slots", runtime.get("base_biggun_slots", 1) or 1)))
        return {
            "instanceKind": "private_room" if is_room else "combat_match",
            "matchId": match_id.strip(),
            "roomCode": room_code.strip().upper(),
            "mapId": (map_id.strip() or ("burrow_hub" if is_room else "blackpot_hollow")),
            "listingType": clean_listing_type or ("private_room" if is_room else "quickplay"),
            "bootSceneKey": DEDICATED_ROOM_SCENE_KEY if is_room else DEDICATED_COMBAT_SCENE_KEY,
            "bootModeKey": DEDICATED_ROOM_MODE_KEY if is_room else str(runtime.get("mode_key", DEDICATED_COMBAT_MODE_KEY) or DEDICATED_COMBAT_MODE_KEY),
            "voiceMode": voice_mode,
            "teamCount": team_count,
            "teamSize": team_size,
            "maxPlayers": max_players,
            "biggunSlots": biggun_slots,
            "requestedPort": DEDICATED_DEFAULT_ROOM_PORT if is_room else DEDICATED_DEFAULT_MATCH_PORT,
            "publicHost": DEDICATED_DEFAULT_PUBLIC_HOST,
            "joinTicket": join_ticket.strip(),
            "reconnectGraceSeconds": DEDICATED_RECONNECT_GRACE_SECONDS,
            "idleTimeoutSeconds": DEDICATED_ROOM_IDLE_TIMEOUT_SECONDS if is_room else DEDICATED_RECONNECT_GRACE_SECONDS,
        }

    def _assignment_route_is_valid(self, assignment: dict[str, Any]) -> bool:
        host = str(assignment.get("public_host", "")).strip()
        port = int(assignment.get("port", 0) or 0)
        if not host or port <= 0:
            return False
        if is_private_or_loopback_host(host):
            return True

        instance_kind = str(assignment.get("instance_kind", "combat_match")).strip().lower()
        start_port, end_port = ROOM_PUBLIC_PORT_RANGE if instance_kind == "private_room" else MATCH_PUBLIC_PORT_RANGE
        return start_port <= port <= end_port

    def _sync_assignment_from_allocator_instance(self, assignment: dict[str, Any]) -> bool:
        clean_instance_id = str(assignment.get("instance_id", "")).strip()
        if not clean_instance_id or not DEDICATED_ALLOCATOR_URL:
            return False

        try:
            allocator_payload = self._allocator_request("GET", f"/api/instances/{clean_instance_id}")
        except HTTPException:
            return False

        instance = allocator_payload.get("instance", allocator_payload)
        if not isinstance(instance, dict):
            return False

        public_host = str(instance.get("publicHost", assignment.get("public_host", DEDICATED_DEFAULT_PUBLIC_HOST))).strip() or DEDICATED_DEFAULT_PUBLIC_HOST
        public_port = int(instance.get("publicPort", 0) or 0)
        if public_port <= 0:
            return False

        assignment["server_id"] = str(instance.get("serverId", assignment.get("server_id", clean_instance_id))).strip() or clean_instance_id
        assignment["instance_id"] = str(instance.get("instanceId", clean_instance_id)).strip() or clean_instance_id
        assignment["public_host"] = public_host
        assignment["port"] = public_port
        assignment["is_ready"] = True
        assignment["allocator_status"] = str(instance.get("status", "allocator_synced")).strip() or "allocator_synced"
        assignment["updated_at_epoch"] = now_ts()
        assignment["runtime_build_fingerprint"] = str(instance.get("runtimeBuildFingerprint", assignment.get("runtime_build_fingerprint", ""))).strip()
        assignment["runtime_build_timestamp_utc"] = str(instance.get("runtimeBuildTimestampUtc", assignment.get("runtime_build_timestamp_utc", ""))).strip()
        assignment["runtime_compatibility_state"] = str(instance.get("runtimeCompatibilityState", assignment.get("runtime_compatibility_state", "unknown"))).strip() or "unknown"
        assignment["summary"] = str(
            instance.get(
                "summary",
                f"{'Room' if str(assignment.get('instance_kind', '')).strip().lower() == 'private_room' else 'Match'} route via {public_host}:{public_port}",
            )
        ).strip() or assignment.get("summary", "")
        assignment["reconnect_grace_expires_at_epoch"] = now_ts() + DEDICATED_RECONNECT_GRACE_SECONDS

        server_id = str(assignment.get("server_id", "")).strip()
        if server_id:
            server = self.dedicated_servers.setdefault(
                server_id,
                {
                    "server_id": server_id,
                    "label": str(instance.get("instanceId", server_id)).strip() or server_id,
                    "supports_combat": True,
                    "supports_rooms": True,
                    "is_draining": False,
                    "max_assignments": 1,
                    "active_assignments": 0,
                },
            )
            server["public_host"] = public_host
            server["last_heartbeat_epoch"] = now_ts()
            server["runtime_build_fingerprint"] = str(instance.get("runtimeBuildFingerprint", server.get("runtime_build_fingerprint", ""))).strip()
            server["runtime_build_timestamp_utc"] = str(instance.get("runtimeBuildTimestampUtc", server.get("runtime_build_timestamp_utc", ""))).strip()
            server["runtime_compatibility_state"] = str(instance.get("runtimeCompatibilityState", server.get("runtime_compatibility_state", "unknown"))).strip() or "unknown"
            if str(assignment.get("instance_kind", "")).strip().lower() == "private_room":
                server["room_port"] = public_port
                server.setdefault("combat_port", DEDICATED_DEFAULT_MATCH_PORT)
            else:
                server["combat_port"] = public_port
                server.setdefault("room_port", DEDICATED_DEFAULT_ROOM_PORT)
        return True

    def _refresh_assignment_from_registered_server(self, assignment: dict[str, Any]) -> bool:
        clean_server_id = str(assignment.get("server_id", "")).strip()
        if not clean_server_id:
            return self._sync_assignment_from_allocator_instance(assignment)

        server = self.dedicated_servers.get(clean_server_id)
        if not server or bool(server.get("is_draining", False)):
            return self._sync_assignment_from_allocator_instance(assignment)

        port_key = "room_port" if str(assignment.get("instance_kind", "")).strip().lower() == "private_room" else "combat_port"
        assignment["public_host"] = str(server.get("public_host", assignment.get("public_host", DEDICATED_DEFAULT_PUBLIC_HOST))).strip() or DEDICATED_DEFAULT_PUBLIC_HOST
        assignment["port"] = int(server.get(port_key, assignment.get("port", DEDICATED_DEFAULT_ROOM_PORT if port_key == "room_port" else DEDICATED_DEFAULT_MATCH_PORT)))
        if not self._assignment_route_is_valid(assignment):
            if self._sync_assignment_from_allocator_instance(assignment):
                return True
            return False
        assignment["is_ready"] = True
        assignment["updated_at_epoch"] = now_ts()
        assignment["runtime_build_fingerprint"] = str(server.get("runtime_build_fingerprint", assignment.get("runtime_build_fingerprint", ""))).strip()
        assignment["runtime_build_timestamp_utc"] = str(server.get("runtime_build_timestamp_utc", assignment.get("runtime_build_timestamp_utc", ""))).strip()
        assignment["runtime_compatibility_state"] = self._resolve_runtime_compatibility_state(server)
        assignment["summary"] = f"{'Room' if port_key == 'room_port' else 'Match'} route via {assignment['public_host']}:{assignment['port']}"
        assignment["allocator_status"] = "registered"
        assignment["reconnect_grace_expires_at_epoch"] = now_ts() + DEDICATED_RECONNECT_GRACE_SECONDS
        return True

    def _wait_for_assignment_registration(self, assignment: dict[str, Any]) -> bool:
        deadline = time.time() + max(1.0, DEDICATED_ALLOCATOR_BOOT_WAIT_SECONDS)
        while time.time() < deadline:
            if self._refresh_assignment_from_registered_server(assignment):
                return True
            time.sleep(0.5)
        return self._refresh_assignment_from_registered_server(assignment)

    def _drop_stale_assignment(self, assignment_key: str, assignment: dict[str, Any]) -> None:
        clean_key = assignment_key.strip().upper() if assignment_key.strip().upper() in self.private_rooms else assignment_key.strip()
        clean_server_id = str(assignment.get("server_id", "")).strip()
        if clean_server_id:
            server = self.dedicated_servers.get(clean_server_id)
            if server:
                server["active_assignments"] = max(0, int(server.get("active_assignments", 0)) - 1)

        self.dedicated_assignments.pop(clean_key, None)
        LOGGER.warning("Dropped stale Underfoot dedicated assignment for %s.", clean_key or assignment_key)
        self.persist()

    def _allocate_via_allocator(
        self,
        *,
        match_id: str,
        map_id: str,
        listing_type: str,
        room_code: str,
    ) -> dict[str, Any]:
        join_ticket = f"join_{secrets.token_hex(16)}"
        allocator_payload = self._build_allocator_boot_payload(
            match_id=match_id,
            map_id=map_id,
            listing_type=listing_type,
            room_code=room_code,
            join_ticket=join_ticket,
        )
        allocator_response = self._allocator_request("POST", "/api/instances", allocator_payload)
        instance = allocator_response.get("instance")
        if not isinstance(instance, dict):
            raise HTTPException(status_code=503, detail="Underfoot allocator did not return an instance payload.")

        is_room = allocator_payload["instanceKind"] == "private_room"
        assignment = {
            "assignment_id": f"assign_{secrets.token_hex(6)}",
            "server_id": str(instance.get("serverId", instance.get("instanceId", ""))).strip(),
            "instance_id": str(instance.get("instanceId", "")).strip(),
            "instance_kind": allocator_payload["instanceKind"],
            "match_id": match_id.strip(),
            "room_code": room_code.strip().upper(),
            "map_id": allocator_payload["mapId"],
            "listing_type": allocator_payload["listingType"],
            "public_host": str(instance.get("publicHost", DEDICATED_DEFAULT_PUBLIC_HOST)).strip() or DEDICATED_DEFAULT_PUBLIC_HOST,
            "port": int(instance.get("publicPort", allocator_payload["requestedPort"])),
            "join_ticket": join_ticket,
            "transport": "ngo_utp",
            "runtime_join_url": self._build_runtime_join_url(match_id.strip()),
            "runtime_web_socket_url": f"ws://{WORLD_HOST}/ws/world/{match_id.strip()}",
            "summary": str(instance.get("summary", f"{'Room' if is_room else 'Match'} is provisioning on Olympus.")).strip() or f"{'Room' if is_room else 'Match'} is provisioning on Olympus.",
            "is_ready": bool(instance.get("isReady", False)),
            "allocator_status": str(instance.get("status", "provisioning")).strip() or "provisioning",
            "reconnect_grace_expires_at_epoch": int(instance.get("reconnectGraceExpiresAtEpoch", now_ts() + DEDICATED_RECONNECT_GRACE_SECONDS)),
            "runtime_build_fingerprint": str(instance.get("runtimeBuildFingerprint", "")).strip(),
            "runtime_build_timestamp_utc": str(instance.get("runtimeBuildTimestampUtc", "")).strip(),
            "runtime_compatibility_state": str(instance.get("runtimeCompatibilityState", "unknown")).strip() or "unknown",
            "boot_context": {
                "boot_scene_key": allocator_payload["bootSceneKey"],
                "boot_mode_key": allocator_payload["bootModeKey"],
                "voice_mode": allocator_payload["voiceMode"],
            },
            "created_at_epoch": now_ts(),
            "updated_at_epoch": now_ts(),
        }
        if not assignment["is_ready"]:
            assignment["is_ready"] = self._refresh_assignment_from_registered_server(assignment)
        if assignment["is_ready"]:
            self._refresh_assignment_from_registered_server(assignment)
        return assignment

    def _ensure_dev_fallback_dedicated_server(self) -> None:
        if not DEDICATED_ALLOW_DEV_FALLBACK or self.dedicated_servers:
            return

        server_id = "underfoot_dev_fallback"
        self.dedicated_servers[server_id] = {
            "server_id": server_id,
            "label": "Underfoot Dev Fallback",
            "public_host": DEDICATED_DEFAULT_PUBLIC_HOST,
            "combat_port": DEDICATED_DEFAULT_MATCH_PORT,
            "room_port": DEDICATED_DEFAULT_ROOM_PORT,
            "supports_combat": True,
            "supports_rooms": True,
            "max_assignments": DEDICATED_DEFAULT_MAX_ASSIGNMENTS,
            "active_assignments": 0,
            "is_draining": False,
            "last_heartbeat_epoch": now_ts(),
            "build_label": "dev-fallback",
            "runtime_build_fingerprint": "",
            "runtime_build_timestamp_utc": "",
            "runtime_package_version": "",
            "runtime_compatibility_state": "unknown",
        }

    def list_dedicated_servers(self) -> list[dict[str, Any]]:
        self.run_maintenance_cleanup()
        self._ensure_dev_fallback_dedicated_server()
        rows = [self.build_dedicated_server_snapshot(server) for server in self.dedicated_servers.values()]
        rows.sort(key=lambda row: (row["is_draining"], row["active_assignments"], row["server_id"]))
        return rows

    def register_dedicated_server(self, req: DedicatedServerRegisterRequest) -> dict[str, Any]:
        clean_server_id = str(req.server_id or "").strip()
        if not clean_server_id:
            raise HTTPException(status_code=400, detail="Server id is required.")

        server = {
            "server_id": clean_server_id,
            "label": str(req.label or clean_server_id).strip() or clean_server_id,
            "public_host": str(req.public_host or DEDICATED_DEFAULT_PUBLIC_HOST).strip() or DEDICATED_DEFAULT_PUBLIC_HOST,
            "combat_port": max(1, int(req.combat_port)),
            "room_port": max(1, int(req.room_port)),
            "supports_combat": bool(req.supports_combat),
            "supports_rooms": bool(req.supports_rooms),
            "max_assignments": max(1, int(req.max_assignments)),
            "active_assignments": min(max(0, int(self.dedicated_servers.get(clean_server_id, {}).get("active_assignments", 0))), max(1, int(req.max_assignments))),
            "is_draining": False,
            "last_heartbeat_epoch": now_ts(),
            "runtime_build_fingerprint": str(req.runtime_build_fingerprint or "").strip(),
            "runtime_build_timestamp_utc": str(req.runtime_build_timestamp_utc or "").strip(),
            "runtime_package_version": str(req.runtime_package_version or "").strip(),
            "runtime_compatibility_state": "ready" if str(req.runtime_build_fingerprint or "").strip() else "unknown",
        }
        self.dedicated_servers[clean_server_id] = server
        for assignment in self.dedicated_assignments.values():
            if str(assignment.get("server_id", "")).strip() == clean_server_id:
                self._refresh_assignment_from_registered_server(assignment)
        self.persist()
        return self.build_dedicated_server_snapshot(server)

    def heartbeat_dedicated_server(self, server_id: str, req: DedicatedServerHeartbeatRequest) -> dict[str, Any]:
        clean_server_id = (server_id or "").strip()
        server = self.dedicated_servers.get(clean_server_id)
        if not server:
            raise HTTPException(status_code=404, detail="Unknown dedicated server.")

        server["active_assignments"] = max(0, int(req.active_assignments))
        server["is_draining"] = bool(req.is_draining)
        server["last_heartbeat_epoch"] = now_ts()
        server["runtime_build_fingerprint"] = str(req.runtime_build_fingerprint or server.get("runtime_build_fingerprint", "")).strip()
        server["runtime_build_timestamp_utc"] = str(req.runtime_build_timestamp_utc or server.get("runtime_build_timestamp_utc", "")).strip()
        server["runtime_package_version"] = str(req.runtime_package_version or server.get("runtime_package_version", "")).strip()
        server["runtime_compatibility_state"] = self._resolve_runtime_compatibility_state(server)
        for assignment in self.dedicated_assignments.values():
            if str(assignment.get("server_id", "")).strip() == clean_server_id:
                self._refresh_assignment_from_registered_server(assignment)
        self.persist()
        return self.build_dedicated_server_snapshot(server)

    def drain_dedicated_server(self, server_id: str, req: DedicatedServerDrainRequest) -> dict[str, Any]:
        clean_server_id = (server_id or "").strip()
        server = self.dedicated_servers.get(clean_server_id)
        if not server:
            raise HTTPException(status_code=404, detail="Unknown dedicated server.")

        server["is_draining"] = bool(req.is_draining)
        server["last_heartbeat_epoch"] = now_ts()
        if bool(req.is_draining):
            for assignment in self.dedicated_assignments.values():
                if str(assignment.get("server_id", "")).strip() == clean_server_id:
                    assignment["is_ready"] = False
                    assignment["allocator_status"] = "draining"
                    assignment["updated_at_epoch"] = now_ts()
        self.persist()
        return self.build_dedicated_server_snapshot(server)

    def _select_dedicated_server(self, *, supports_rooms: bool, supports_combat: bool) -> dict[str, Any]:
        self._ensure_dev_fallback_dedicated_server()
        now_epoch = now_ts()
        candidates: list[dict[str, Any]] = []
        for server in self.dedicated_servers.values():
            if bool(server.get("is_draining", False)):
                continue
            last_heartbeat = int(server.get("last_heartbeat_epoch", 0))
            if last_heartbeat > 0 and now_epoch - last_heartbeat > DEDICATED_HEARTBEAT_TIMEOUT_SECONDS:
                continue
            if supports_combat and not bool(server.get("supports_combat", True)):
                continue
            if supports_rooms and not bool(server.get("supports_rooms", True)):
                continue
            active_assignments = int(server.get("active_assignments", 0))
            max_assignments = max(1, int(server.get("max_assignments", DEDICATED_DEFAULT_MAX_ASSIGNMENTS)))
            if active_assignments >= max_assignments:
                continue
            candidates.append(server)

        if not candidates:
            raise HTTPException(status_code=503, detail="No Olympus dedicated server is currently available for this Underfoot route.")

        candidates.sort(key=lambda row: (int(row.get("active_assignments", 0)), str(row.get("server_id", ""))))
        return candidates[0]

    def _build_runtime_join_url(self, match_id: str) -> str:
        return f"{VOICE_GATEWAY_PUBLIC_HTTP_URL.rsplit('/underfoot/voice', 1)[0]}/api/matches/{match_id}/runtime"

    def allocate_dedicated_assignment(
        self,
        *,
        match_id: str,
        map_id: str,
        listing_type: str,
        room_code: str = "",
        force_reallocate: bool = False,
    ) -> dict[str, Any]:
        assignment_key = room_code.strip().upper() if room_code.strip() else match_id.strip()
        if not assignment_key:
            raise HTTPException(status_code=400, detail="Assignment key is required.")

        existing = self.dedicated_assignments.get(assignment_key)
        if existing and not force_reallocate:
            server = self.dedicated_servers.get(str(existing.get("server_id", "")).strip())
            server_is_healthy = False
            if server and not bool(server.get("is_draining", False)):
                last_heartbeat = int(server.get("last_heartbeat_epoch", 0))
                server_is_healthy = last_heartbeat <= 0 or now_ts() - last_heartbeat <= DEDICATED_HEARTBEAT_TIMEOUT_SECONDS
                if server_is_healthy:
                    self._refresh_assignment_from_registered_server(existing)
                    existing["updated_at_epoch"] = now_ts()
                    self.persist()
                    return dict(existing)

            if DEDICATED_ALLOCATOR_URL and server_is_healthy and not bool(existing.get("is_ready", False)):
                existing["is_ready"] = self._refresh_assignment_from_registered_server(existing)
                existing["updated_at_epoch"] = now_ts()
                self.persist()
                return dict(existing)

            self._drop_stale_assignment(assignment_key, existing)
            existing = None

        is_room = str(listing_type or "").strip().lower() == "private_room" or bool(room_code.strip())
        allocator_error: HTTPException | None = None
        assignment: dict[str, Any] | None = None
        if DEDICATED_ALLOCATOR_URL:
            try:
                assignment = self._allocate_via_allocator(
                    match_id=match_id,
                    map_id=map_id,
                    listing_type=listing_type,
                    room_code=room_code,
                )
            except HTTPException as exc:
                allocator_error = exc
                LOGGER.warning("Underfoot allocator assignment failed for %s: %s", assignment_key, exc.detail)

        if assignment is None:
            if allocator_error and not DEDICATED_ALLOW_DEV_FALLBACK:
                raise allocator_error
            server = self._select_dedicated_server(supports_rooms=is_room, supports_combat=not is_room)
            instance_id = f"inst_{secrets.token_hex(6)}"
            assignment = {
                "assignment_id": f"assign_{secrets.token_hex(6)}",
                "server_id": str(server.get("server_id", "")).strip(),
                "instance_id": instance_id,
                "instance_kind": "private_room" if is_room else "combat_match",
                "match_id": match_id.strip(),
                "room_code": room_code.strip().upper(),
                "map_id": map_id.strip() or "blackpot_hollow",
                "listing_type": listing_type.strip() or ("private_room" if is_room else "quickplay"),
                "public_host": str(server.get("public_host", DEDICATED_DEFAULT_PUBLIC_HOST)).strip() or DEDICATED_DEFAULT_PUBLIC_HOST,
                "port": int(server.get("room_port" if is_room else "combat_port", DEDICATED_DEFAULT_ROOM_PORT if is_room else DEDICATED_DEFAULT_MATCH_PORT)),
                "join_ticket": f"join_{secrets.token_hex(16)}",
                "transport": "ngo_utp",
                "runtime_join_url": self._build_runtime_join_url(match_id.strip()),
                "runtime_web_socket_url": f"ws://{WORLD_HOST}/ws/world/{match_id.strip()}",
                "summary": f"{'Room' if is_room else 'Match'} route via {server.get('public_host', DEDICATED_DEFAULT_PUBLIC_HOST)}:{int(server.get('room_port' if is_room else 'combat_port', 0))}",
                "is_ready": True,
                "allocator_status": "legacy_pool",
                "reconnect_grace_expires_at_epoch": now_ts() + DEDICATED_RECONNECT_GRACE_SECONDS,
                "runtime_build_fingerprint": str(server.get("runtime_build_fingerprint", "")).strip(),
                "runtime_build_timestamp_utc": str(server.get("runtime_build_timestamp_utc", "")).strip(),
                "runtime_compatibility_state": self._resolve_runtime_compatibility_state(server),
                "created_at_epoch": now_ts(),
                "updated_at_epoch": now_ts(),
            }
            server["active_assignments"] = int(server.get("active_assignments", 0)) + 1
            server["last_heartbeat_epoch"] = now_ts()
        self.dedicated_assignments[assignment_key] = assignment
        self.persist()
        return dict(assignment)

    def get_match_assignment(self, match_id: str) -> dict[str, Any]:
        clean_match_id = (match_id or "").strip()
        if not clean_match_id:
            raise HTTPException(status_code=400, detail="Match id is required.")
        runtime = self.ensure_match_runtime(clean_match_id)
        return self.allocate_dedicated_assignment(
            match_id=clean_match_id,
            map_id=str(runtime.get("map_id", "blackpot_hollow")),
            listing_type=str(runtime.get("listing_type", "quickplay")),
        )

    def get_room_assignment(self, room_code: str) -> dict[str, Any]:
        clean_room_code = normalize_room_code(room_code)
        room = self.private_rooms.get(clean_room_code)
        if not room:
            raise HTTPException(status_code=404, detail="Unknown private room.")
        match_id = str(room.get("match_id", f"{SESSION_PREFIX}_room_{clean_room_code.lower()}")).strip()
        room["match_id"] = match_id
        self.ensure_private_room_runtime(room)
        return self.allocate_dedicated_assignment(
            match_id=match_id,
            map_id="burrow_hub",
            listing_type="private_room",
            room_code=clean_room_code,
        )

    def compute_required_biggun_slots(self, runtime: dict[str, Any]) -> int:
        if str(runtime.get("listing_type", "")).strip().lower() == "private_room":
            return 0
        players = list(runtime.get("players", {}).values())
        active_warbles = sum(
            1
            for player in players
            if str(player.get("role", "warble")) == "warble" and not bool(player.get("is_consumed", False))
        )
        base_slots = max(1, int(runtime.get("base_biggun_slots", 1)))
        max_slots = max(base_slots, int(runtime.get("max_biggun_slots", 2)))
        second_unlock = max(1, int(runtime.get("second_biggun_unlock_warble_count", runtime.get("warble_slots", 18))))
        if max_slots > base_slots and active_warbles >= second_unlock:
            return max_slots
        return base_slots

    def build_default_holes(self, map_id: str) -> list[dict[str, Any]]:
        seeded = DEFAULT_MATCH_HOLES.get(map_id)
        if seeded:
            return [dict(row) for row in seeded]

        try:
            map_row = self.map_by_id(map_id)
        except HTTPException:
            map_row = {"spawns": []}

        holes: list[dict[str, Any]] = []
        for spawn in map_row.get("spawns", []):
            if not isinstance(spawn, dict):
                continue
            hole_id = str(spawn.get("id", "")).strip()
            if not hole_id:
                continue
            holes.append(
                {
                    "hole_id": hole_id,
                    "label": str(spawn.get("label", hole_id)).strip() or hole_id,
                    "type": str(spawn.get("type", "neutral_hole")).strip() or "neutral_hole",
                    "is_safe": True,
                    "team_id": -1,
                }
            )
        return holes

    def ensure_voice_runtime(self, runtime: dict[str, Any]) -> dict[str, Any]:
        voice = runtime.setdefault(
            "voice",
            {
                "transportMode": VOICE_TRANSPORT_MODE,
                "gatewayHttpUrl": VOICE_GATEWAY_HTTP_URL,
                "gatewayWebSocketUrl": VOICE_GATEWAY_WS_URL,
                "tokenTtlSeconds": VOICE_JOIN_TTL_SECONDS,
                "qualityPreset": VOICE_QUALITY_PRESET,
                "opusBitrateKbps": VOICE_BITRATE_KBPS,
                "enforcementMode": VOICE_ENFORCEMENT_MODE,
                "profiles": VOICE_POLICY.get("profiles", {}),
                "occlusion": VOICE_POLICY.get("occlusion", {}),
                "spatial": VOICE_POLICY.get("spatial", {}),
                "silence": {
                    "graceSeconds": VOICE_SILENCE_GRACE_SECONDS,
                    "warningSeconds": VOICE_SILENCE_WARNING_SECONDS,
                    "revealSeconds": VOICE_SILENCE_REVEAL_SECONDS,
                },
                "peers": {},
                "penalties": {},
                "noise_events": [],
                "contour_states": {},
                "gateway_stats": {
                    "matchId": runtime.get("match_id"),
                    "activePeers": 0,
                    "connectedPeers": 0,
                    "speakingPeers": 0,
                    "liveSpeakers": 0,
                    "droppedPeers": 0,
                    "averagePacketLossPercent": 0.0,
                    "averageJitterMilliseconds": 0.0,
                    "contourPacketsRelayed": 0,
                    "audioFramesRelayed": 0,
                    "audioBytesRelayed": 0,
                    "rawTrackSubscriptions": 0,
                    "contourOnlySubscriptions": 0,
                    "updatedAtEpoch": now_ts(),
                },
            },
        )
        voice.setdefault("peers", {})
        voice.setdefault("penalties", {})
        voice.setdefault("noise_events", [])
        voice.setdefault("contour_states", {})
        voice.setdefault("gateway_stats", {})
        return voice

    def sync_voice_peers_from_players(self, runtime: dict[str, Any]) -> None:
        voice = self.ensure_voice_runtime(runtime)
        players = runtime.setdefault("players", {})
        peers = voice.setdefault("peers", {})
        active_player_ids = set(players.keys())
        stale_player_ids = [player_id for player_id in peers.keys() if player_id not in active_player_ids]
        for stale_player_id in stale_player_ids:
            peers.pop(stale_player_id, None)
            voice.get("penalties", {}).pop(stale_player_id, None)

        for player_id, player in players.items():
            role = normalize_voice_role(str(player.get("role", "warble")))
            is_captured = bool(player.get("is_captured", False))
            is_consumed = bool(player.get("is_consumed", False))
            life_state = "consumed" if is_consumed else "captured" if is_captured else "active"
            existing = peers.get(player_id, {})
            peers[player_id] = {
                "playerId": player_id,
                "displayName": player.get("display_name", player_id),
                "role": role,
                "teamId": int(player.get("team_id", -1)),
                "isConnected": bool(existing.get("isConnected", True)),
                "isSpeaking": bool(existing.get("isSpeaking", False)),
                "hasMicInput": bool(existing.get("hasMicInput", False)),
                "deviceLost": bool(existing.get("deviceLost", False)),
                "packetLossPercent": float(existing.get("packetLossPercent", 0.0)),
                "jitterMilliseconds": float(existing.get("jitterMilliseconds", 0.0)),
                "lifeState": life_state,
                "lastVoiceEpoch": int(existing.get("lastVoiceEpoch", 0)),
                "lastStateEpoch": int(existing.get("lastStateEpoch", now_ts())),
                "silentSinceEpoch": int(existing.get("silentSinceEpoch", 0)),
                "lastInputEpoch": int(existing.get("lastInputEpoch", 0)),
            }

        gateway_stats = voice.setdefault("gateway_stats", {})
        peer_rows = list(peers.values())
        connected_rows = [row for row in peer_rows if bool(row.get("isConnected", False))]
        speaking_rows = [row for row in peer_rows if bool(row.get("isSpeaking", False))]
        gateway_stats["matchId"] = runtime.get("match_id")
        gateway_stats["activePeers"] = max(len(peer_rows), int(gateway_stats.get("activePeers", 0) or 0))
        gateway_stats["connectedPeers"] = max(len(connected_rows), int(gateway_stats.get("connectedPeers", 0) or 0))
        gateway_stats["speakingPeers"] = max(len(speaking_rows), int(gateway_stats.get("speakingPeers", 0) or 0))
        gateway_stats["liveSpeakers"] = max(len(speaking_rows), int(gateway_stats.get("liveSpeakers", 0) or 0))
        gateway_stats.setdefault("droppedPeers", 0)
        gateway_stats.setdefault("averagePacketLossPercent", 0.0)
        gateway_stats.setdefault("averageJitterMilliseconds", 0.0)
        gateway_stats.setdefault("contourPacketsRelayed", 0)
        gateway_stats.setdefault("audioFramesRelayed", 0)
        gateway_stats.setdefault("audioBytesRelayed", 0)
        gateway_stats.setdefault("rawTrackSubscriptions", 0)
        gateway_stats.setdefault("contourOnlySubscriptions", 0)
        gateway_stats["updatedAtEpoch"] = now_ts()

    def build_match_voice_config(self, match_id: str) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        self.sync_voice_peers_from_players(runtime)
        return build_voice_config_payload(str(runtime.get("match_id", match_id)), runtime=runtime)

    def build_match_voice_state(self, match_id: str) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        self.sync_voice_peers_from_players(runtime)
        voice = self.ensure_voice_runtime(runtime)
        peers = list(voice.get("peers", {}).values())
        penalties = list(voice.get("penalties", {}).values())
        noise_events = list(voice.get("noise_events", []))[-24:]
        return {
            "config": self.build_match_voice_config(match_id),
            "peers": sorted(peers, key=lambda row: (int(row.get("teamId", -1)), str(row.get("displayName", "")))),
            "penalties": sorted(penalties, key=lambda row: str(row.get("playerId", ""))),
            "noiseEvents": list(reversed(noise_events)),
            "contourStates": list(voice.get("contour_states", {}).values()),
            "gatewayStats": dict(voice.get("gateway_stats", {})),
        }

    def create_match_voice_join_token(self, match_id: str, req: VoiceJoinTokenRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        self.sync_voice_peers_from_players(runtime)
        players = runtime.get("players", {})
        player = players.get(req.player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Only joined match members may request a voice session token.")

        role = normalize_voice_role(req.role or str(player.get("role", "warble")))
        if role != normalize_voice_role(str(player.get("role", "warble"))):
            raise HTTPException(status_code=403, detail="Voice role mismatch for this player.")

        issued_at = now_ts()
        expires_at = issued_at + max(30, VOICE_JOIN_TTL_SECONDS)
        payload = {
            "matchId": runtime.get("match_id", match_id),
            "playerId": req.player_id,
            "displayName": player.get("display_name", req.player_id),
            "role": role,
            "teamId": int(player.get("team_id", -1)),
            "sessionId": f"voice_{runtime.get('match_id', match_id)}_{req.player_id}",
            "issuedAtEpoch": issued_at,
            "expiresAtEpoch": expires_at,
            "gatewayHttpUrl": VOICE_GATEWAY_PUBLIC_HTTP_URL or VOICE_GATEWAY_HTTP_URL,
            "gatewayWebSocketUrl": VOICE_GATEWAY_PUBLIC_WS_URL or VOICE_GATEWAY_WS_URL,
            "mode": resolve_voice_runtime_mode(runtime),
            "crossRoleMode": resolve_voice_cross_role_mode(runtime),
            "contourHz": VOICE_CONTOUR_HZ,
            "fakeVoiceBankKey": VOICE_FAKE_VOICE_BANK_KEY,
            "turnServers": build_turn_servers(req.player_id, match_id),
        }
        token = encode_voice_token(payload)
        voice = self.ensure_voice_runtime(runtime)
        peer = voice.setdefault("peers", {}).get(req.player_id, {})
        peer["lastStateEpoch"] = issued_at
        voice["peers"][req.player_id] = peer
        runtime["updated_at"] = now_ts()
        self.persist()
        return {
            "matchId": payload["matchId"],
            "playerId": req.player_id,
            "role": role,
            "teamId": payload["teamId"],
            "token": token,
            "gatewayHttpUrl": VOICE_GATEWAY_PUBLIC_HTTP_URL or VOICE_GATEWAY_HTTP_URL,
            "gatewayWebSocketUrl": VOICE_GATEWAY_PUBLIC_WS_URL or VOICE_GATEWAY_WS_URL,
            "issuedAtEpoch": issued_at,
            "expiresAtEpoch": expires_at,
            "sessionId": payload["sessionId"],
            "mode": payload["mode"],
            "crossRoleMode": payload["crossRoleMode"],
            "contourHz": payload["contourHz"],
            "fakeVoiceBankKey": payload["fakeVoiceBankKey"],
            "turnServers": payload["turnServers"],
        }

    def apply_voice_noise_event(self, match_id: str, req: VoiceNoiseEventRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        self.sync_voice_peers_from_players(runtime)
        voice = self.ensure_voice_runtime(runtime)
        if req.player_id and req.player_id not in runtime.get("players", {}):
            raise HTTPException(status_code=404, detail="Unknown player for voice noise event.")

        event = {
            "eventId": f"voice_evt_{now_ts()}_{secrets.token_hex(4)}",
            "playerId": req.player_id,
            "kind": str(req.kind or "speech_activity").strip().lower() or "speech_activity",
            "sourceLabel": str(req.source_label or "voice_event").strip() or "voice_event",
            "intensity": max(0.0, min(float(req.intensity), 1.0)),
            "audibleToBigguns": bool(req.audible_to_bigguns),
            "worldX": float(req.world_x),
            "worldY": float(req.world_y),
            "worldZ": float(req.world_z),
            "createdAtEpoch": now_ts(),
        }
        noise_events = voice.setdefault("noise_events", [])
        noise_events.append(event)
        if len(noise_events) > 40:
            del noise_events[:-40]

        peer = voice.setdefault("peers", {}).get(req.player_id)
        if peer and event["kind"] in {"speech_activity", "speechactivity"}:
            peer["isSpeaking"] = True
            peer["lastVoiceEpoch"] = now_ts()
            peer["lastStateEpoch"] = now_ts()

        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_voice_state(match_id)

    def update_match_mic_state(self, match_id: str, req: VoiceMicStateRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        self.sync_voice_peers_from_players(runtime)
        voice = self.ensure_voice_runtime(runtime)
        peer = voice.setdefault("peers", {}).get(req.player_id)
        if not peer:
            raise HTTPException(status_code=404, detail="Unknown player for voice mic state.")

        ts = now_ts()
        if req.has_input and not req.device_lost:
            peer["lastInputEpoch"] = ts
            peer["silentSinceEpoch"] = 0
        else:
            peer["silentSinceEpoch"] = int(peer.get("silentSinceEpoch", 0) or ts)

        silent_since = int(peer.get("silentSinceEpoch", 0) or 0)
        silence_seconds = 0.0 if req.has_input and not req.device_lost else max(0.0, float(ts - silent_since))
        mode = resolve_voice_runtime_mode(runtime)
        if mode == "hub_soft":
            stage, audible_cue = "clear", "clear"
        else:
            stage, audible_cue = compute_voice_penalty_stage(silence_seconds, bool(req.has_input), bool(req.device_lost))
        peer["isSpeaking"] = bool(req.speaking)
        peer["hasMicInput"] = bool(req.has_input)
        peer["deviceLost"] = bool(req.device_lost)
        peer["packetLossPercent"] = max(0.0, float(req.packet_loss_percent))
        peer["jitterMilliseconds"] = max(0.0, float(req.jitter_ms))
        peer["lastStateEpoch"] = ts
        if req.speaking:
            peer["lastVoiceEpoch"] = ts

        penalty = {
            "playerId": req.player_id,
            "penaltyStage": stage,
            "deviceLost": bool(req.device_lost),
            "hasMicInput": bool(req.has_input),
            "silenceSeconds": silence_seconds,
            "audibleCue": audible_cue,
            "updatedAtEpoch": ts,
        }
        voice.setdefault("penalties", {})[req.player_id] = penalty

        gateway_stats = voice.setdefault("gateway_stats", {})
        peer_rows = list(voice.get("peers", {}).values())
        connected_rows = [row for row in peer_rows if bool(row.get("isConnected", False))]
        gateway_stats["activePeers"] = len(peer_rows)
        gateway_stats["connectedPeers"] = len(connected_rows)
        gateway_stats["speakingPeers"] = sum(1 for row in peer_rows if bool(row.get("isSpeaking", False)))
        gateway_stats["liveSpeakers"] = gateway_stats["speakingPeers"]
        if peer_rows:
            gateway_stats["averagePacketLossPercent"] = round(sum(float(row.get("packetLossPercent", 0.0)) for row in peer_rows) / len(peer_rows), 2)
            gateway_stats["averageJitterMilliseconds"] = round(sum(float(row.get("jitterMilliseconds", 0.0)) for row in peer_rows) / len(peer_rows), 2)
        gateway_stats["updatedAtEpoch"] = ts

        runtime["updated_at"] = ts
        self.persist()
        return {"penalty": penalty, "voice": self.build_match_voice_state(match_id)}

    def update_match_contour_state(self, match_id: str, req: VoiceContourStateRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        self.sync_voice_peers_from_players(runtime)
        voice = self.ensure_voice_runtime(runtime)
        if req.player_id not in voice.setdefault("peers", {}):
            raise HTTPException(status_code=404, detail="Unknown player for voice contour state.")

        contour_state = {
            "playerId": req.player_id,
            "sequenceId": max(0, int(req.sequence_id)),
            "isActive": bool(req.is_active),
            "envelope": max(0.0, min(float(req.envelope), 1.0)),
            "pitchNorm": max(0.0, min(float(req.pitch_norm), 1.0)),
            "stress": max(0.0, min(float(req.stress), 1.0)),
            "burst": max(0.0, min(float(req.burst), 1.0)),
            "durationMs": max(0.0, float(req.duration_ms)),
            "bankHint": str(req.bank_hint or "").strip() or VOICE_FAKE_VOICE_BANK_KEY,
            "updatedAtEpoch": now_ts(),
        }
        voice.setdefault("contour_states", {})[req.player_id] = contour_state
        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_voice_state(match_id)

    def update_match_voice_gateway_stats(self, match_id: str, req: VoiceGatewayStatsRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        self.sync_voice_peers_from_players(runtime)
        voice = self.ensure_voice_runtime(runtime)
        voice["gateway_stats"] = {
            "matchId": runtime.get("match_id", match_id),
            "activePeers": max(0, int(req.active_peers)),
            "connectedPeers": max(0, int(req.connected_peers)),
            "speakingPeers": max(0, int(req.speaking_peers)),
            "liveSpeakers": max(0, int(req.live_speakers or req.speaking_peers)),
            "droppedPeers": max(0, int(req.dropped_peers)),
            "averagePacketLossPercent": max(0.0, float(req.average_packet_loss_percent)),
            "averageJitterMilliseconds": max(0.0, float(req.average_jitter_milliseconds or req.average_jitter_ms)),
            "contourPacketsRelayed": max(0, int(req.contour_packets_relayed)),
            "audioFramesRelayed": max(0, int(req.audio_frames_relayed)),
            "audioBytesRelayed": max(0, int(req.audio_bytes_relayed)),
            "rawTrackSubscriptions": max(0, int(req.raw_track_subscriptions)),
            "contourOnlySubscriptions": max(0, int(req.contour_only_subscriptions)),
            "updatedAtEpoch": now_ts(),
        }
        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_voice_state(match_id)

    def ensure_match_runtime(self, match_id: str) -> dict[str, Any]:
        clean_match_id = (match_id or "").strip()
        runtime = self.match_runtimes.get(clean_match_id)
        if runtime:
            return runtime

        for room in self.private_rooms.values():
            if str(room.get("match_id", "")).strip() == clean_match_id:
                return self.ensure_private_room_runtime(room)

        listing = self.resolve_match_listing(clean_match_id)
        team_count = max(3, min(int(listing.get("team_count", 3)), 3))
        max_team_size = max(3, min(int(listing.get("max_team_size", 6)), 6))
        warble_slots = team_count * max_team_size
        max_biggun_slots = max(1, min(int(listing.get("max_biggun_slots", 2)), 2))
        max_players = max(warble_slots + max_biggun_slots, int(listing.get("max_players", warble_slots + max_biggun_slots)))
        listing_type = str(listing.get("listing_type", "quickplay")).strip() or "quickplay"
        quickplay_runtime = listing_type == "quickplay"
        runtime = {
            "match_id": clean_match_id,
            "map_id": str(listing.get("map_id", "blackpot_hollow")).strip() or "blackpot_hollow",
            "mode_key": str(listing.get("mode_key", "underfoot_full_test")).strip() or "underfoot_full_test",
            "phase": "lobby",
            "team_count": team_count,
            "min_team_size": max(3, min(int(listing.get("min_team_size", 3)), max_team_size)),
            "max_team_size": max_team_size,
            "max_players": max_players,
            "warble_slots": warble_slots,
            "base_biggun_slots": max(1, int(listing.get("base_biggun_slots", 1))),
            "max_biggun_slots": max_biggun_slots,
            "second_biggun_unlock_warble_count": max(1, int(listing.get("second_biggun_unlock_warble_count", warble_slots))),
            "display_label": str(listing.get("display_label", clean_match_id)).strip() or clean_match_id,
            "listing_type": listing_type,
            "custom_cross_role_intelligible": bool(listing.get("custom_cross_role_intelligible", listing.get("listing_type", "quickplay") == "custom_hosted")),
            "phase_started_at_epoch": now_ts(),
            "phase_durations_seconds": {
                "spawn_selection": QUICKPLAY_SPAWN_SELECTION_SECONDS if quickplay_runtime else MATCH_SPAWN_SELECTION_SECONDS,
                "shroom_ritual": QUICKPLAY_SHROOM_RITUAL_SECONDS if quickplay_runtime else MATCH_SHROOM_RITUAL_SECONDS,
                "scout": QUICKPLAY_SCOUT_SECONDS if quickplay_runtime else MATCH_SCOUT_SECONDS,
            },
            "quickplay_auto_start_seconds": QUICKPLAY_AUTO_START_SECONDS if quickplay_runtime else 0,
            "quickplay_auto_start_started_at_epoch": 0,
            "team_lobby_countdown_seconds": TEAM_LOBBY_COUNTDOWN_SECONDS,
            "team_lobby_countdown_started_at_epoch": 0,
            "team_lobby_countdown_completed_at_epoch": 0,
            "team_lobby_countdown_reason": "",
            "players": {},
            "shrooms": {"personal": {}, "team": {}},
            "missions": [dict(row) for row in DEFAULT_FEATURED_MISSIONS],
            "mission_progress": {},
            "trauma_states": {},
            "skill_states": {},
            "biggun_placements": {},
            "ai_threats": {},
            "holes": {row["hole_id"]: row for row in self.build_default_holes(str(listing.get("map_id", "blackpot_hollow")))},
            "travel_log": [],
            "winner_team_id": -1,
            "updated_at": now_ts(),
        }
        self.ensure_voice_runtime(runtime)
        self.match_runtimes[clean_match_id] = runtime
        self.persist()
        return runtime

    def ensure_private_room_runtime(self, room: dict[str, Any]) -> dict[str, Any]:
        match_id = str(room.get("match_id", "")).strip()
        if not match_id:
            raise HTTPException(status_code=400, detail="Private room match id is required.")

        runtime = self.match_runtimes.get(match_id)
        if runtime:
            return runtime

        runtime = {
            "match_id": match_id,
            "map_id": "burrow_hub",
            "mode_key": "underfoot_social_room",
            "phase": "lobby",
            "team_count": 1,
            "min_team_size": 1,
            "max_team_size": int(room.get("capacity", 8) or 8),
            "max_players": int(room.get("capacity", 8) or 8),
            "warble_slots": int(room.get("capacity", 8) or 8),
            "base_biggun_slots": 0,
            "max_biggun_slots": 0,
            "second_biggun_unlock_warble_count": 999,
            "display_label": str(room.get("label", match_id)).strip() or match_id,
            "listing_type": "private_room",
            "custom_cross_role_intelligible": False,
            "phase_started_at_epoch": now_ts(),
            "phase_durations_seconds": {},
            "team_lobby_countdown_seconds": 0,
            "team_lobby_countdown_started_at_epoch": 0,
            "team_lobby_countdown_completed_at_epoch": 0,
            "team_lobby_countdown_reason": "",
            "players": {},
            "shrooms": {"personal": {}, "team": {}},
            "missions": [],
            "mission_progress": {},
            "trauma_states": {},
            "skill_states": {},
            "biggun_placements": {},
            "ai_threats": {},
            "holes": {row["hole_id"]: row for row in self.build_default_holes("burrow_hub")},
            "travel_log": [],
            "winner_team_id": -1,
            "updated_at": now_ts(),
        }
        self.ensure_voice_runtime(runtime)
        self.match_runtimes[match_id] = runtime
        self.persist()
        return runtime

    def upsert_private_room_player(
        self,
        runtime: dict[str, Any],
        *,
        account_id: str,
        player_id: str,
        display_name: str,
        faction_key: str,
        hair_style_key: str,
        hair_tint_key: str,
        is_host: bool,
    ) -> dict[str, Any]:
        clean_player_id = (player_id or "").strip() or f"room_{(account_id or 'guest').strip()}"
        player = {
            "player_id": clean_player_id,
            "account_id": (account_id or "").strip(),
            "display_name": (display_name or "Burrow Guest").strip()[:32] or "Burrow Guest",
            "faction_key": normalize_live_faction_key(faction_key or "blue"),
            "hair_style_key": (hair_style_key or "wavy_crown").strip() or "wavy_crown",
            "hair_tint_key": (hair_tint_key or "cream").strip() or "cream",
            "role": "warble",
            "team_id": 0,
            "is_host": bool(is_host),
            "is_ready": True,
            "is_simulated": False,
            "is_captured": False,
            "is_consumed": False,
            "is_connected": True,
            "is_abandoned": False,
            "is_backfill_eligible": False,
            "abandoned_at_epoch": 0,
            "abandoned_reason": "",
            "last_presence_epoch": now_ts(),
            "reconnect_grace_expires_at_epoch": now_ts() + DEDICATED_RECONNECT_GRACE_SECONDS,
            "discovered_holes": [],
            "resources": 0,
            "rescues": 0,
            "sound_baits": 0,
            "sabotages": 0,
            "updated_at": now_ts(),
        }
        runtime.setdefault("players", {})[clean_player_id] = player
        self.ensure_player_skill_state(runtime, clean_player_id)
        self.sync_voice_peers_from_players(runtime)
        runtime["updated_at"] = now_ts()
        return player

    def ensure_player_skill_state(self, runtime: dict[str, Any], player_id: str) -> dict[str, Any]:
        skill_states = runtime.setdefault("skill_states", {})
        state = skill_states.get(player_id)
        if state:
            return state

        state = {
            "player_id": player_id,
            "hidden_until_epoch": 0,
            "carry_boost_until_epoch": 0,
            "hide_in_grass_ready_at_epoch": 0,
            "hole_dash_ready_at_epoch": 0,
            "tiny_carry_boost_ready_at_epoch": 0,
            "shroom_ritual_boost_ready_at_epoch": 0,
            "sound_bait_ready_at_epoch": 0,
            "updated_at": now_ts(),
        }
        skill_states[player_id] = state
        return state

    def maybe_start_match_flow(self, runtime: dict[str, Any]) -> None:
        phase = str(runtime.get("phase", "lobby"))
        if phase not in {"lobby", "spawn_selection"}:
            return

        if phase == "lobby" and bool(runtime.get("dev_auto_seed_enabled", False)) and str(runtime.get("listing_type", "")).strip().lower() == "quickplay":
            human_count = self._count_human_runtime_players(runtime)
            if human_count <= 0:
                runtime["quickplay_auto_start_started_at_epoch"] = 0
                return

            self._seed_quickplay_runtime(runtime)

        if str(runtime.get("listing_type", "")).strip().lower() != "private_room":
            self._advance_team_lobby_start_gate(runtime, route="runtime_tick")

    def _compute_runtime_start_state(self, runtime: dict[str, Any]) -> tuple[int, int, int]:
        players = list(runtime.get("players", {}).values())
        active_team_ids = sorted(
            {
                int(player.get("team_id", -1))
                for player in players
                if str(player.get("role", "warble")).strip() == "warble"
                and int(player.get("team_id", -1)) >= 0
                and self._player_counts_for_team(runtime, player)
            }
        )
        ready_teams = 0
        min_team_size = int(runtime.get("min_team_size", 3))
        for team_id in active_team_ids:
            ready_count = sum(
                1
                for player in players
                if str(player.get("role", "warble")).strip() == "warble"
                and int(player.get("team_id", -1)) == team_id
                and bool(player.get("is_ready", False))
                and self._player_counts_for_team(runtime, player)
            )
            if ready_count >= min_team_size:
                ready_teams += 1

        biggun_count = sum(
            1
            for player in players
            if str(player.get("role", "")) == "biggun"
            and not bool(player.get("is_backfill_eligible", False))
            and not bool(player.get("is_consumed", False))
        )
        return len(active_team_ids), ready_teams, biggun_count

    def _count_human_runtime_players(self, runtime: dict[str, Any]) -> int:
        return sum(
            1
            for player in runtime.get("players", {}).values()
            if not bool(player.get("is_simulated", False))
            and not bool(player.get("is_backfill_eligible", False))
            and not bool(player.get("is_consumed", False))
        )

    def _seed_quickplay_runtime(self, runtime: dict[str, Any]) -> None:
        players = runtime.setdefault("players", {})
        team_count = max(3, int(runtime.get("team_count", 3)))
        min_team_size = max(3, min(int(runtime.get("min_team_size", 3)), int(runtime.get("max_team_size", 6))))
        factions = [row.get("key", "blue") for row in DEFAULT_FACTIONS] or ["blue"]
        now_epoch = now_ts()

        for player in players.values():
            if str(player.get("role", "warble")) == "warble" and not bool(player.get("is_backfill_eligible", False)):
                player["is_ready"] = True
                player["updated_at"] = now_epoch

        next_index = 0
        while f"sim_quick_fill_{next_index:03d}" in players:
            next_index += 1

        for team_id in range(team_count):
            active_team_members = [
                row
                for row in players.values()
                if str(row.get("role", "warble")) == "warble"
                and int(row.get("team_id", -1)) == team_id
                and not bool(row.get("is_backfill_eligible", False))
                and not bool(row.get("is_consumed", False))
            ]
            while len(active_team_members) < min_team_size:
                player_id = f"sim_quick_fill_{next_index:03d}"
                next_index += 1
                filler = {
                    "player_id": player_id,
                    "account_id": player_id,
                    "display_name": f"Quick Crew {next_index:03d}",
                    "faction_key": factions[team_id % len(factions)],
                    "hair_style_key": "wavy_crown",
                    "hair_tint_key": "cream",
                    "role": "warble",
                    "team_id": team_id,
                    "is_host": False,
                    "is_ready": True,
                    "is_simulated": True,
                    "is_captured": False,
                    "is_consumed": False,
                    "is_connected": True,
                    "is_abandoned": False,
                    "is_backfill_eligible": False,
                    "abandoned_at_epoch": 0,
                    "abandoned_reason": "",
                    "last_presence_epoch": now_epoch,
                    "reconnect_grace_expires_at_epoch": now_epoch + DEDICATED_RECONNECT_GRACE_SECONDS,
                    "discovered_holes": [],
                    "resources": 0,
                    "rescues": 0,
                    "sound_baits": 0,
                    "sabotages": 0,
                    "updated_at": now_epoch,
                }
                players[player_id] = filler
                self.ensure_player_skill_state(runtime, player_id)
                active_team_members.append(filler)

        required_bigguns = max(1, int(self.compute_required_biggun_slots(runtime)))
        current_bigguns = sum(
            1
            for row in players.values()
            if str(row.get("role", "")) == "biggun" and not bool(row.get("is_backfill_eligible", False))
        )
        while current_bigguns < required_bigguns:
            biggun_id = f"sim_quick_biggun_{current_bigguns + 1}"
            players[biggun_id] = {
                "player_id": biggun_id,
                "account_id": biggun_id,
                "display_name": f"Hidden Biggun {current_bigguns + 1}",
                "faction_key": "giant",
                "hair_style_key": "none",
                "hair_tint_key": "none",
                "role": "biggun",
                "team_id": -1,
                "is_host": False,
                "is_ready": True,
                "is_simulated": True,
                "is_captured": False,
                "is_consumed": False,
                "is_connected": True,
                "is_abandoned": False,
                "is_backfill_eligible": False,
                "abandoned_at_epoch": 0,
                "abandoned_reason": "",
                "last_presence_epoch": now_epoch,
                "reconnect_grace_expires_at_epoch": now_epoch + DEDICATED_RECONNECT_GRACE_SECONDS,
                "discovered_holes": [],
                "resources": 0,
                "rescues": 0,
                "sound_baits": 0,
                "sabotages": 0,
                "updated_at": now_epoch,
            }
            self.ensure_player_skill_state(runtime, biggun_id)
            current_bigguns += 1

        runtime["updated_at"] = now_epoch

    def choose_quickplay_listing(self, req: QueueTicketRequest) -> dict[str, Any]:
        requested_role = "biggun" if str(req.requested_role or "").strip().lower() == "biggun" else "warble"
        requested_preset = str(req.queue_preset_key or "underfoot_quickplay").strip() or "underfoot_quickplay"
        requested_faction = normalize_live_faction_key(req.faction_key)
        join_style = str(req.join_style or "solo").strip().lower()
        fill_any_open_slot = join_style in {"fill", "fill_any", "fill_any_open", "fill_open", "open_slot", "fast_play", "fast"}
        candidates: list[tuple[int, int, int, dict[str, Any]]] = []

        for listing in self.list_quickplay():
            if not bool(listing.get("joinable", True)):
                continue
            if str(listing.get("queue_preset_key", "")).strip() != requested_preset:
                continue

            match_id = str(listing.get("match_id", "")).strip()
            snapshot = self.build_match_runtime_snapshot(match_id)
            biggun_count = sum(
                1
                for player in snapshot.get("players", [])
                if str(player.get("role", "")) == "biggun" and not bool(player.get("is_backfill_eligible", False))
            )
            if requested_role == "biggun":
                if biggun_count >= int(snapshot.get("biggun_slots_required", 1)):
                    continue
                candidates.append((biggun_count, int(snapshot.get("active_player_count", len(snapshot.get("players", [])))), -1, listing))
                continue

            team_count = max(3, int(snapshot.get("team_count", listing.get("team_count", 3))))
            max_team_size = max(3, int(snapshot.get("max_team_size", listing.get("max_team_size", 6))))
            team_sizes = [
                sum(
                    1
                    for player in snapshot.get("players", [])
                    if str(player.get("role", "")) == "warble"
                    and int(player.get("team_id", -1)) == team_id
                    and not bool(player.get("is_consumed", False))
                    and not bool(player.get("is_backfill_eligible", False))
                )
                for team_id in range(team_count)
            ]
            if fill_any_open_slot:
                open_team_candidates = [
                    (team_size, team_id)
                    for team_id, team_size in enumerate(team_sizes)
                    if team_size < max_team_size
                ]
                if not open_team_candidates:
                    continue
                open_team_candidates.sort(key=lambda row: (row[0], row[1]))
                selected_team_id = open_team_candidates[0][1]
                candidates.append((open_team_candidates[0][0], int(snapshot.get("active_player_count", len(snapshot.get("players", [])))), selected_team_id, listing))
                continue

            preferred_team_id = resolve_live_team_for_faction(requested_faction)
            if preferred_team_id >= len(team_sizes) or team_sizes[preferred_team_id] >= max_team_size:
                continue
            candidates.append((team_sizes[preferred_team_id], int(snapshot.get("active_player_count", len(snapshot.get("players", [])))), preferred_team_id, listing))

        if not candidates:
            raise HTTPException(status_code=409, detail="No compatible quickplay listing is available right now.")

        candidates.sort(key=lambda row: (row[0], row[1], str(row[3].get("match_id", ""))))
        selected_listing = dict(candidates[0][3])
        selected_listing["selected_team_id"] = int(candidates[0][2])
        return selected_listing

    def tick_match_runtime(self, runtime: dict[str, Any]) -> None:
        now_epoch = now_ts()
        last_tick = int(runtime.get("last_tick_epoch", runtime.get("updated_at", now_epoch)))
        delta = max(0, min(now_epoch - last_tick, 30))
        runtime["last_tick_epoch"] = now_epoch
        self._refresh_runtime_presence_flags(runtime, now_epoch)
        if str(runtime.get("phase", "lobby")) == "lobby":
            self.maybe_start_match_flow(runtime)
        if delta <= 0:
            return

        trauma_states = runtime.setdefault("trauma_states", {})
        stale_trauma: list[str] = []
        for player_id, trauma in trauma_states.items():
            remaining = max(0.0, float(trauma.get("recover_in_seconds", 0.0)) - float(delta))
            if remaining <= 0.0:
                stale_trauma.append(player_id)
                continue
            trauma["recover_in_seconds"] = remaining

        for player_id in stale_trauma:
            trauma_states.pop(player_id, None)

        for skill_state in runtime.setdefault("skill_states", {}).values():
            skill_state["updated_at"] = now_epoch
            if int(skill_state.get("hidden_until_epoch", 0)) <= now_epoch:
                skill_state["hidden_until_epoch"] = 0
            if int(skill_state.get("carry_boost_until_epoch", 0)) <= now_epoch:
                skill_state["carry_boost_until_epoch"] = 0

        for shroom in runtime.setdefault("shrooms", {}).setdefault("personal", {}).values():
            if str(shroom.get("state", "active")) != "cooldown":
                continue
            remaining_cooldown = max(0.0, float(shroom.get("remaining_cooldown_seconds", shroom.get("cooldown_seconds", 240.0))) - float(delta))
            shroom["remaining_cooldown_seconds"] = remaining_cooldown
            if remaining_cooldown <= 0.0:
                shroom["state"] = "dormant"
                shroom["is_destroyed"] = False
                shroom["remaining_cooldown_seconds"] = 0.0

        stale_placements: list[str] = []
        for placement_id, placement in runtime.setdefault("biggun_placements", {}).items():
            remaining_seconds = max(0.0, float(placement.get("remaining_seconds", BIGGUN_PLACEMENT_DURATION_SECONDS)) - float(delta))
            placement["remaining_seconds"] = remaining_seconds
            placement["updated_at"] = now_epoch
            if remaining_seconds <= 0.0:
                stale_placements.append(placement_id)

        for placement_id in stale_placements:
            runtime.setdefault("biggun_placements", {}).pop(placement_id, None)

        stale_threats: list[str] = []
        for threat_id, threat in runtime.setdefault("ai_threats", {}).items():
            remaining_seconds = max(0.0, float(threat.get("remaining_seconds", AI_THREAT_DURATION_SECONDS)) - float(delta))
            threat["remaining_seconds"] = remaining_seconds
            if remaining_seconds <= 0.0:
                stale_threats.append(threat_id)
                continue
            if str(threat.get("state", "patrol")) == "baited" and remaining_seconds < max(15.0, AI_THREAT_DURATION_SECONDS / 2):
                threat["state"] = "hunting"
            threat["updated_at"] = now_epoch

        for threat_id in stale_threats:
            runtime.setdefault("ai_threats", {}).pop(threat_id, None)

        phase = str(runtime.get("phase", "lobby"))
        phase_started_at = int(runtime.get("phase_started_at_epoch", now_epoch))
        phase_elapsed = max(0, now_epoch - phase_started_at)
        phase_durations = runtime.get("phase_durations_seconds", {})
        if phase == "spawn_selection" and phase_elapsed >= int(phase_durations.get("spawn_selection", MATCH_SPAWN_SELECTION_SECONDS)):
            runtime["phase"] = "shroom_ritual"
            runtime["phase_started_at_epoch"] = now_epoch
        elif phase == "shroom_ritual" and phase_elapsed >= int(phase_durations.get("shroom_ritual", MATCH_SHROOM_RITUAL_SECONDS)):
            runtime["phase"] = "scout"
            runtime["phase_started_at_epoch"] = now_epoch
        elif phase == "scout" and phase_elapsed >= int(phase_durations.get("scout", MATCH_SCOUT_SECONDS)):
            runtime["phase"] = "live"
            runtime["phase_started_at_epoch"] = now_epoch

        surviving_team_ids: set[int] = set()
        players = runtime.setdefault("players", {})
        personal_shrooms = runtime.setdefault("shrooms", {}).setdefault("personal", {})
        team_shrooms = runtime.setdefault("shrooms", {}).setdefault("team", {})
        for player in players.values():
            if str(player.get("role", "warble")) != "warble" or bool(player.get("is_consumed", False)):
                continue
            if not self._player_counts_for_team(runtime, player, now_epoch):
                continue
            team_id = int(player.get("team_id", -1))
            if team_id < 0:
                continue
            team_shroom = team_shrooms.get(str(team_id))
            if team_shroom and str(team_shroom.get("state", "active")) == "active" and not bool(team_shroom.get("is_destroyed", False)):
                surviving_team_ids.add(team_id)
                continue
            personal_shroom = personal_shrooms.get(str(player.get("player_id", "")))
            if personal_shroom and str(personal_shroom.get("state", "dormant")) in {"active", "dormant"} and not bool(personal_shroom.get("is_destroyed", False)):
                surviving_team_ids.add(team_id)

        if str(runtime.get("phase", "lobby")) == "live" and len(surviving_team_ids) <= 1:
            runtime["phase"] = "resolution"
            runtime["phase_started_at_epoch"] = now_epoch
            runtime["winner_team_id"] = next(iter(surviving_team_ids), -1)

        runtime["updated_at"] = now_epoch

    def build_match_runtime_snapshot(self, match_id: str) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        self.tick_match_runtime(runtime)
        self.sync_voice_peers_from_players(runtime)
        lobby_contract_state = self._build_team_lobby_contract_state(runtime)
        players = list(runtime.get("players", {}).values())
        personal_shrooms = list(runtime.get("shrooms", {}).get("personal", {}).values())
        team_shrooms = list(runtime.get("shrooms", {}).get("team", {}).values())
        active_player_count = sum(1 for player in players if self._player_counts_for_team(runtime, player))
        backfill_eligible_count = sum(1 for player in players if bool(player.get("is_backfill_eligible", False)))
        captured_count = sum(1 for player in players if bool(player.get("is_captured", False)) and not bool(player.get("is_consumed", False)))
        consumed_count = sum(1 for player in players if bool(player.get("is_consumed", False)))
        surviving_team_shrooms = sum(1 for shroom in team_shrooms if str(shroom.get("state", "active")) == "active" and not bool(shroom.get("is_destroyed", False)))
        holes = list(runtime.get("holes", {}).values())
        skill_states = list(runtime.get("skill_states", {}).values())
        placements = list(runtime.get("biggun_placements", {}).values())
        ai_threats = list(runtime.get("ai_threats", {}).values())
        return {
            "match_id": runtime.get("match_id"),
            "map_id": runtime.get("map_id"),
            "mode_key": runtime.get("mode_key"),
            "phase": runtime.get("phase", "lobby"),
            "team_count": int(runtime.get("team_count", 3)),
            "min_team_size": int(runtime.get("min_team_size", 3)),
            "max_team_size": int(runtime.get("max_team_size", 6)),
            "max_players": int(runtime.get("max_players", 20)),
            "biggun_slots_required": int(lobby_contract_state["required_biggun_slots"]),
            "ready_teams": int(lobby_contract_state["team_lobby_state"]["confirmedTeamCount"]),
            "active_teams": int(lobby_contract_state["team_lobby_state"]["activeTeamCount"]),
            "team_lobby_state": lobby_contract_state["team_lobby_state"],
            "team_ready_flags": lobby_contract_state["team_ready_flags"],
            "biggun_state": lobby_contract_state["biggun_state"],
            "countdown_state": lobby_contract_state["countdown_state"],
            "lobby_start_gate": {
                "can_start": bool(lobby_contract_state["start_gate_ready"]),
                "blockers": list(lobby_contract_state["start_gate_blockers"]),
                "required_team_count": int(lobby_contract_state["required_team_count"]),
                "ready_team_count": int(lobby_contract_state["team_lobby_state"]["readyTeamCount"]),
                "required_biggun_slots": int(lobby_contract_state["required_biggun_slots"]),
                "biggun_ready": bool(lobby_contract_state["biggun_state"]["isReady"]),
                "countdown_active": bool(lobby_contract_state["countdown_state"]["isActive"]),
                "countdown_remaining_seconds": int(lobby_contract_state["countdown_state"]["remainingSeconds"]),
            },
            "captured_warbles": captured_count,
            "consumed_warbles": consumed_count,
            "surviving_team_shrooms": surviving_team_shrooms,
            "destroyed_team_shrooms": max(0, len(team_shrooms) - surviving_team_shrooms),
            "active_player_count": active_player_count,
            "backfill_eligible_count": backfill_eligible_count,
            "players": sorted(
                players,
                key=lambda row: (
                    0 if str(row.get("role", "warble")).strip() == "warble" else 1,
                    int(row.get("team_id", -1)),
                    int(row.get("slot_index", -1)) if int(row.get("slot_index", -1)) >= 0 else 999,
                    str(row.get("display_name", "")).strip().lower(),
                ),
            ),
            "shrooms": {
                "personal": sorted(personal_shrooms, key=lambda row: str(row.get("owner_id", ""))),
                "team": sorted(team_shrooms, key=lambda row: int(row.get("team_id", -1))),
            },
            "missions": runtime.get("missions", []),
            "mission_progress": runtime.get("mission_progress", {}),
            "trauma_states": list(runtime.get("trauma_states", {}).values()),
            "voice": self.build_match_voice_state(match_id),
            "holes": sorted(holes, key=lambda row: str(row.get("hole_id", ""))),
            "skill_states": sorted(skill_states, key=lambda row: str(row.get("player_id", ""))),
            "biggun_placements": sorted(placements, key=lambda row: str(row.get("placement_id", ""))),
            "ai_threats": sorted(ai_threats, key=lambda row: str(row.get("threat_id", ""))),
            "stabilization_profile": build_stabilization_profile_payload(),
            "network_decision_gate": build_network_decision_gate_payload(),
            "travel_log": list(runtime.get("travel_log", []))[-12:],
            "updated_at": int(runtime.get("updated_at", now_ts())),
            "winner_team_id": int(runtime.get("winner_team_id", -1)),
        }

    def build_match_team_lobby_snapshot(self, match_id: str, account_id: str = "", player_id: str = "") -> dict[str, Any]:
        runtime_snapshot = self.build_match_runtime_snapshot(match_id)
        current_player = None
        clean_account_id = str(account_id or "").strip()
        clean_player_id = str(player_id or "").strip()
        for player in runtime_snapshot.get("players", []):
            if clean_player_id and str(player.get("player_id", "")).strip() == clean_player_id:
                current_player = player
                break
            if clean_account_id and str(player.get("account_id", "")).strip() == clean_account_id:
                current_player = player
                break

        lobby_state = dict(runtime_snapshot.get("team_lobby_state", {}))
        teams = list(lobby_state.get("teams", []))
        for team in teams:
            slots = list(team.get("slots", []))
            for slot in slots:
                slot["isCurrentPlayer"] = bool(
                    (clean_player_id and str(slot.get("playerId", "")).strip() == clean_player_id)
                    or (clean_account_id and str(slot.get("accountId", "")).strip() == clean_account_id)
                )
            team["slots"] = slots
        biggun_state = dict(runtime_snapshot.get("biggun_state", {}))
        biggun_slots = list(biggun_state.get("slots", []))
        for slot in biggun_slots:
            slot["isCurrentPlayer"] = bool(
                (clean_player_id and str(slot.get("playerId", "")).strip() == clean_player_id)
                or (clean_account_id and str(slot.get("accountId", "")).strip() == clean_account_id)
            )
        biggun_state["slots"] = biggun_slots
        countdown_state = dict(runtime_snapshot.get("countdown_state", {}))
        start_gate = dict(runtime_snapshot.get("lobby_start_gate", {}))

        return {
            "match_id": runtime_snapshot.get("match_id", match_id),
            "map_id": runtime_snapshot.get("map_id", "blackpot_hollow"),
            "mode_key": runtime_snapshot.get("mode_key", "underfoot_full_test"),
            "phase": runtime_snapshot.get("phase", "lobby"),
            "current_players": int(runtime_snapshot.get("active_player_count", len(runtime_snapshot.get("players", [])))),
            "max_players": int(runtime_snapshot.get("max_players", 0)),
            "simulated_players": sum(1 for player in runtime_snapshot.get("players", []) if bool(player.get("is_simulated", False))),
            "open_biggun_slots": max(0, int(runtime_snapshot.get("biggun_slots_required", 1)) - int(biggun_state.get("activeCount", 0))),
            "team_count": int(runtime_snapshot.get("team_count", 3)),
            "min_team_size": int(runtime_snapshot.get("min_team_size", 3)),
            "max_team_size": int(runtime_snapshot.get("max_team_size", 6)),
            "active_teams": int(lobby_state.get("activeTeamCount", 0)),
            "ready_teams": int(lobby_state.get("confirmedTeamCount", 0)),
            "requires_biggun_ready": True,
            "admin_fill_enabled": bool(self.setup_matches.get(match_id, {}).get("admin_fill_enabled", False)),
            "joinable": True,
            "is_private_room": str(runtime_snapshot.get("listing_type", "")).strip().lower() == "private_room",
            "host_account_id": str(next((player.get("account_id", "") for player in runtime_snapshot.get("players", []) if bool(player.get("is_host", False))), "")),
            "host_player_id": str(next((player.get("player_id", "") for player in runtime_snapshot.get("players", []) if bool(player.get("is_host", False))), "")),
            "private_room_code": "",
            "queue_preset_key": str(self.setup_matches.get(match_id, {}).get("queue_preset_key", "underfoot_quickplay")),
            "queue_preset_label": str(self.setup_matches.get(match_id, {}).get("queue_preset_label", "Quickplay: Warble Found")),
            "stage_key": str(self.setup_matches.get(match_id, {}).get("stage_key", "potion_hut_panic")),
            "stage_label": str(self.setup_matches.get(match_id, {}).get("stage_label", "Blackpot Hut")),
            "display_label": str(self.setup_matches.get(match_id, {}).get("display_label", "Warble Found | 3 Team Live Slice")),
            "team_setup_label": str(self.setup_matches.get(match_id, {}).get("team_setup_label", "Red vs Yellow vs Blue | 3-6 warbles")),
            "queue_destination_label": str(self.setup_matches.get(match_id, {}).get("queue_destination_label", "Team Lobby")),
            "local_shroom_rule_label": str(self.setup_matches.get(match_id, {}).get("local_shroom_rule_label", "Personal 240s | Team 90s")),
            "current_account_id": str(current_player.get("account_id", clean_account_id) if current_player else clean_account_id),
            "current_player_id": str(current_player.get("player_id", clean_player_id) if current_player else clean_player_id),
            "current_display_name": str(current_player.get("display_name", "") if current_player else ""),
            "current_role": str(current_player.get("role", "warble") if current_player else "warble"),
            "current_team_id": int(current_player.get("team_id", -1) if current_player else -1),
            "current_ready": bool(current_player.get("is_ready", False) if current_player else False),
            "current_faction_key": str(current_player.get("faction_key", "blue") if current_player else "blue"),
            "current_hair_style_key": str(current_player.get("hair_style_key", "wavy_crown") if current_player else "wavy_crown"),
            "current_hair_tint_key": str(current_player.get("hair_tint_key", "cream") if current_player else "cream"),
            "teams": teams,
            "players": runtime_snapshot.get("players", []),
            "state": lobby_state,
            "biggun_state": biggun_state,
            "countdown_state": countdown_state,
            "lobby_start_gate": start_gate,
            "runtime": runtime_snapshot,
            "detail": "; ".join(start_gate.get("blockers", [])) if start_gate.get("blockers") else str(countdown_state.get("reason", "")).strip(),
        }

    def join_match(self, match_id: str, req: MatchJoinRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        players = runtime.setdefault("players", {})
        requested_role = "biggun" if str(req.role).strip().lower() == "biggun" else "warble"
        target_team_id = int(req.team_id)
        requested_slot_index = int(req.slot_index)
        previous_player = players.get(req.player_id)
        previous_team_id = int(previous_player.get("team_id", -1)) if previous_player else -1
        previous_role = str(previous_player.get("role", "warble")).strip() if previous_player else ""
        backfill_team_id = -1
        backfill_replaced_player_id = ""
        backfill_slot_index = -1
        for existing_player_id, existing_player in list(players.items()):
            if str(existing_player.get("role", "warble")) != requested_role:
                continue
            if not self._player_is_backfill_eligible(runtime, existing_player):
                continue
            existing_team_id = int(existing_player.get("team_id", -1))
            if requested_role == "warble" and target_team_id >= 0 and existing_team_id != target_team_id:
                continue
            backfill_team_id = int(existing_player.get("team_id", -1))
            backfill_replaced_player_id = existing_player_id
            backfill_slot_index = int(existing_player.get("slot_index", -1))
            break

        if backfill_replaced_player_id:
            players.pop(backfill_replaced_player_id, None)
            if requested_role == "warble" and backfill_team_id >= 0:
                target_team_id = backfill_team_id
            if requested_slot_index < 0 and backfill_slot_index >= 0:
                requested_slot_index = backfill_slot_index

        if requested_role == "warble" and target_team_id < 0:
            team_count = max(3, int(runtime.get("team_count", 3)))
            max_team_size = max(3, int(runtime.get("max_team_size", 6)))
            preferred_team_id = resolve_live_team_for_faction(req.faction_key)
            team_sizes = [
                (
                    team_id,
                    sum(
                        1
                        for row in players.values()
                        if str(row.get("role", "warble")) == "warble"
                        and int(row.get("team_id", -1)) == team_id
                        and self._player_counts_for_team(runtime, row)
                    ),
                )
                for team_id in range(team_count)
            ]
            team_sizes.sort(key=lambda row: (row[1], row[0]))
            if preferred_team_id < team_count and next((size for team_id, size in team_sizes if team_id == preferred_team_id), max_team_size) < max_team_size:
                target_team_id = preferred_team_id
            else:
                target_team_id = next((team_id for team_id, size in team_sizes if size < max_team_size), team_sizes[0][0] if team_sizes else 0)

        assigned_team_id = -1 if requested_role == "biggun" else max(0, min(target_team_id, int(runtime.get("team_count", 3)) - 1))
        assigned_faction_key = normalize_live_faction_key(req.faction_key)
        if requested_role == "warble" and assigned_team_id >= 0:
            assigned_faction_key = LIVE_TEAM_FACTION_ORDER[assigned_team_id % len(LIVE_TEAM_FACTION_ORDER)]
        if requested_role == "warble":
            max_team_size = max(3, min(int(runtime.get("max_team_size", 6)), 6))
            if requested_slot_index >= max_team_size:
                raise HTTPException(status_code=409, detail="Requested Warble slot is outside the visible team roster.")
            if requested_slot_index < 0:
                requested_slot_index = self._find_first_open_team_slot(runtime, assigned_team_id, exclude_player_id=req.player_id)
            elif any(
                str(player.get("role", "warble")).strip() == "warble"
                and int(player.get("team_id", -1)) == assigned_team_id
                and int(player.get("slot_index", -1)) == requested_slot_index
                and str(player.get("player_id", "")).strip() != str(req.player_id).strip()
                and self._player_counts_for_team(runtime, player)
                for player in players.values()
            ):
                raise HTTPException(status_code=409, detail="Requested Warble slot is already occupied.")
            if requested_slot_index < 0:
                raise HTTPException(status_code=409, detail="No open Warble slots remain on that team.")
        else:
            required_biggun_slots = self.compute_required_biggun_slots(runtime)
            if requested_slot_index >= required_biggun_slots:
                raise HTTPException(status_code=409, detail="Requested Biggun slot is unavailable.")
            if requested_slot_index < 0:
                requested_slot_index = self._find_first_open_biggun_slot(runtime, exclude_player_id=req.player_id)
            elif any(
                str(player.get("role", "")).strip() == "biggun"
                and int(player.get("slot_index", -1)) == requested_slot_index
                and str(player.get("player_id", "")).strip() != str(req.player_id).strip()
                and not bool(player.get("is_backfill_eligible", False))
                and not bool(player.get("is_consumed", False))
                for player in players.values()
            ):
                raise HTTPException(status_code=409, detail="Requested Biggun slot is already occupied.")
            if requested_slot_index < 0:
                raise HTTPException(status_code=409, detail="No open Biggun slots remain.")

        player_row = {
            "player_id": req.player_id,
            "account_id": req.account_id,
            "display_name": req.display_name.strip()[:32] or req.player_id,
            "faction_key": assigned_faction_key,
            "hair_style_key": (req.hair_style_key or "wavy_crown").strip() or "wavy_crown",
            "hair_tint_key": (req.hair_tint_key or "cream").strip() or "cream",
            "role": requested_role,
            "team_id": assigned_team_id,
            "slot_index": requested_slot_index,
            "is_host": bool(req.is_host),
            "is_ready": False,
            "is_simulated": bool(req.is_simulated),
            "is_captured": False,
            "is_consumed": False,
            "is_connected": True,
            "is_abandoned": False,
            "is_backfill_eligible": False,
            "abandoned_at_epoch": 0,
            "abandoned_reason": "",
            "last_presence_epoch": now_ts(),
            "reconnect_grace_expires_at_epoch": now_ts() + DEDICATED_RECONNECT_GRACE_SECONDS,
            "replaced_player_id": backfill_replaced_player_id,
            "discovered_holes": [],
            "resources": 0,
            "rescues": 0,
            "sound_baits": 0,
            "sabotages": 0,
            "updated_at": now_ts(),
        }
        players[req.player_id] = player_row
        if previous_role == "warble" and previous_team_id >= 0:
            self._clear_team_ready_confirmation(runtime, previous_team_id)
        if requested_role == "warble" and assigned_team_id >= 0:
            self._clear_team_ready_confirmation(runtime, assigned_team_id)
        self.ensure_player_skill_state(runtime, req.player_id)

        setup_listing = self.setup_matches.get((match_id or "").strip())
        if setup_listing:
            setup_listing["current_players"] = min(int(setup_listing.get("max_players", 0)), len(players))
            setup_listing["updated_at"] = now_ts()

        self._advance_team_lobby_start_gate(runtime, route=f"POST /api/matches/{match_id}/join")
        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def set_match_ready(self, match_id: str, req: MatchReadyRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        player, resolved_by = self._resolve_runtime_player_for_ready(runtime, player_id=req.player_id, account_id=req.account_id)
        if not player:
            self._log_team_lobby_breadcrumb(
                stage="team_ready_rejected",
                severity="warning",
                route=f"POST /api/matches/{match_id}/ready",
                match_id=match_id,
                reason="unknown_player",
                connection=f"players={len(runtime.setdefault('players', {}))}",
                player_id=req.player_id,
                account_id=req.account_id,
            )
            raise HTTPException(status_code=404, detail="Unknown runtime player. Rejoin the roster and try again.")

        clean_player_id = str(player.get("player_id", "")).strip()
        clean_account_id = str(player.get("account_id", "")).strip()
        if resolved_by == "account_id" and req.player_id and clean_player_id != str(req.player_id).strip():
            self._log_team_lobby_breadcrumb(
                stage="team_ready_reconciled",
                severity="info",
                route=f"POST /api/matches/{match_id}/ready",
                match_id=match_id,
                reason="stale_player_id_reconciled",
                connection=f"resolved_player_id={clean_player_id}",
                player_id=req.player_id,
                account_id=req.account_id or clean_account_id,
            )

        previous_ready = bool(player.get("is_ready", False))
        player["is_ready"] = bool(req.is_ready)
        player["updated_at"] = now_ts()
        if str(player.get("role", "warble")).strip() == "warble" and int(player.get("team_id", -1)) >= 0:
            self._clear_team_ready_confirmation(runtime, int(player.get("team_id", -1)))
        if previous_ready != bool(req.is_ready):
            self._log_team_lobby_breadcrumb(
                stage="team_ready_mutation",
                severity="info",
                route=f"POST /api/matches/{match_id}/ready",
                match_id=match_id,
                reason="player_ready_updated",
                connection=f"ready={bool(req.is_ready)}",
                player_id=clean_player_id,
                account_id=clean_account_id or req.account_id,
            )
        self._advance_team_lobby_start_gate(runtime, route=f"POST /api/matches/{match_id}/ready")
        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def set_team_ready(self, match_id: str, team_id: int, req: TeamReadyRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        if str(runtime.get("listing_type", "")).strip().lower() == "private_room":
            self._log_team_lobby_breadcrumb(
                stage="team_ready_rejected",
                severity="warning",
                route=f"POST /api/matches/{match_id}/teams/{team_id}/ready",
                match_id=match_id,
                team_id=team_id,
                reason="team_ready_not_supported_for_private_room",
            )
            raise HTTPException(status_code=409, detail="Team readiness is only available for combat matches.")

        actor, resolved_by = self._resolve_runtime_player_for_ready(runtime, player_id=req.player_id, account_id=req.account_id)
        now_epoch = now_ts()
        slots, captain, team_players, ready_players = self._build_team_slot_rows(runtime, team_id, now_epoch=now_epoch)
        if not team_players:
            self._log_team_lobby_breadcrumb(
                stage="team_ready_rejected",
                severity="warning",
                route=f"POST /api/matches/{match_id}/teams/{team_id}/ready",
                match_id=match_id,
                team_id=team_id,
                reason="no_active_team_members",
                connection=f"actor_resolved={resolved_by or 'none'}",
            )
            raise HTTPException(status_code=404, detail="No active Warbles are available on that team.")

        roster_ready = len(team_players) >= max(3, int(runtime.get("min_team_size", 3))) and len(ready_players) >= max(3, int(runtime.get("min_team_size", 3)))
        if not roster_ready:
            self._clear_team_ready_confirmation(runtime, int(team_id))
            self._advance_team_lobby_start_gate(runtime, route=f"POST /api/matches/{match_id}/teams/{team_id}/ready", now_epoch=now_epoch)
            runtime["updated_at"] = now_epoch
            self.persist()
            raise HTTPException(status_code=409, detail="That team needs at least 3 ready Warbles before TEAM READY can be confirmed.")

        if actor is None:
            raise HTTPException(status_code=404, detail="Unknown runtime player.")

        actor_team_id = int(actor.get("team_id", -1))
        actor_is_host = bool(actor.get("is_host", False))
        captain_player_id = str(captain.get("player_id", "")).strip() if captain else ""
        actor_player_id = str(actor.get("player_id", "")).strip()
        actor_account_id = str(actor.get("account_id", "")).strip()
        if not actor_is_host and (actor_team_id != int(team_id) or not captain_player_id or actor_player_id != captain_player_id):
            self._log_team_lobby_breadcrumb(
                stage="team_ready_rejected",
                severity="warning",
                route=f"POST /api/matches/{match_id}/teams/{team_id}/ready",
                match_id=match_id,
                team_id=team_id,
                reason="captain_required",
                connection=f"actor_team={actor_team_id}|captain={captain_player_id or 'none'}|host={actor_is_host}",
                player_id=actor_player_id,
                account_id=actor_account_id,
            )
            raise HTTPException(status_code=403, detail="Only the current team captain or host can confirm TEAM READY.")

        confirmations = runtime.setdefault("team_ready_confirmations", {})
        ready_player_ids = {
            str(player.get("player_id", "")).strip()
            for player in ready_players
            if str(player.get("player_id", "")).strip()
        }
        captain_ready = bool(captain_player_id) and captain_player_id in ready_player_ids
        if not captain_ready:
            self._clear_team_ready_confirmation(runtime, int(team_id))
            self._log_team_lobby_breadcrumb(
                stage="team_ready_rejected",
                severity="warning",
                route=f"POST /api/matches/{match_id}/teams/{team_id}/ready",
                match_id=match_id,
                team_id=team_id,
                reason="captain_not_ready",
                connection=f"captain={captain_player_id or 'none'}|ready_count={len(ready_players)}",
                player_id=actor_player_id,
                account_id=actor_account_id,
            )
            self._advance_team_lobby_start_gate(runtime, route=f"POST /api/matches/{match_id}/teams/{team_id}/ready", now_epoch=now_epoch)
            runtime["updated_at"] = now_epoch
            self.persist()
            raise HTTPException(status_code=409, detail="The current team captain must be READY before TEAM READY can be confirmed.")

        effective_ready = bool(req.is_ready) and roster_ready and captain_ready and bool(captain_player_id)
        if effective_ready:
            confirmations[str(team_id)] = captain_player_id
        else:
            confirmations.pop(str(team_id), None)

        self._log_team_lobby_breadcrumb(
            stage="team_ready_toggled",
            severity="info",
            route=f"POST /api/matches/{match_id}/teams/{team_id}/ready",
            match_id=match_id,
            team_id=team_id,
            reason="captain_confirmation_set" if effective_ready else "captain_confirmation_cleared",
            connection=f"requested={bool(req.is_ready)}|effective={effective_ready}|captain={captain_player_id or 'none'}|captain_ready={captain_ready}|host={actor_is_host}",
            player_id=actor_player_id,
            account_id=actor_account_id,
        )

        self._advance_team_lobby_start_gate(runtime, route=f"POST /api/matches/{match_id}/teams/{team_id}/ready", now_epoch=now_epoch)
        runtime["updated_at"] = now_epoch
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def set_match_phase(self, match_id: str, req: MatchPhaseRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        phase = str(req.phase or "").strip().lower()
        valid_phases = {"lobby", "spawn_selection", "shroom_ritual", "scout", "live", "resolution", "returning"}
        if phase not in valid_phases:
            raise HTTPException(status_code=400, detail="Unknown match phase.")

        current_phase = str(runtime.get("phase", "lobby")).strip().lower() or "lobby"
        if current_phase == "lobby" and phase in {"spawn_selection", "live"}:
            snapshot = self.build_match_runtime_snapshot(match_id)
            start_gate = snapshot.get("lobby_start_gate", {})
            countdown_state = snapshot.get("countdown_state", {})
            blockers = list(start_gate.get("blockers", []))
            if not bool(start_gate.get("can_start", False)) or int(countdown_state.get("remainingSeconds", 0)) > 0:
                blocker_text = "; ".join(blockers) if blockers else str(countdown_state.get("reason", "")).strip() or "countdown_not_complete"
                self._log_team_lobby_breadcrumb(
                    stage="team_lobby_start_denied",
                    severity="warning",
                    route=f"POST /api/matches/{match_id}/phase",
                    match_id=match_id,
                    reason=blocker_text,
                    connection=f"phase={snapshot.get('phase', 'lobby')} countdown={countdown_state.get('state', 'unknown')} remaining={countdown_state.get('remainingSeconds', 0)}",
                )
                raise HTTPException(status_code=409, detail=f"Team lobby cannot start yet: {blocker_text}.")

        runtime["phase"] = phase
        if phase == "lobby":
            runtime["team_lobby_countdown_started_at_epoch"] = 0
            runtime["team_lobby_countdown_completed_at_epoch"] = 0
            runtime["team_lobby_countdown_reason"] = "phase_reset_to_lobby"
        elif phase in {"spawn_selection", "shroom_ritual", "scout", "live"}:
            runtime["team_lobby_countdown_started_at_epoch"] = 0
            runtime["team_lobby_countdown_completed_at_epoch"] = now_ts()
            runtime["team_lobby_countdown_reason"] = "phase_advanced"
        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def raise_personal_shroom(self, match_id: str, req: PersonalShroomRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        player = runtime.setdefault("players", {}).get(req.player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Unknown runtime player.")

        shroom = {
            "shroom_id": f"personal_{req.player_id}",
            "owner_id": req.player_id,
            "team_id": int(req.team_id),
            "shroom_type": "personal",
            "state": "active",
            "cast_seconds": max(1, int(req.cast_seconds)),
            "cooldown_seconds": max(180, min(int(req.cooldown_seconds), 300)),
            "helper_count": 0,
            "is_destroyed": False,
            "is_consumed_locked": bool(player.get("is_consumed", False)),
            "remaining_cooldown_seconds": 0,
            "updated_at": now_ts(),
        }
        runtime.setdefault("shrooms", {}).setdefault("personal", {})[req.player_id] = shroom
        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def raise_team_shroom(self, match_id: str, req: TeamShroomRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        adjusted_cast = max(
            int(req.floor_seconds),
            int(req.cast_seconds) - max(0, int(req.helper_count) - 1) * int(req.helper_reduction_seconds),
        )
        shroom = {
            "shroom_id": f"team_{req.team_id}",
            "owner_id": req.owner_id,
            "team_id": int(req.team_id),
            "shroom_type": "team",
            "state": "active",
            "cast_seconds": adjusted_cast,
            "cooldown_seconds": 0,
            "helper_count": max(1, int(req.helper_count)),
            "is_destroyed": False,
            "is_consumed_locked": False,
            "updated_at": now_ts(),
        }
        runtime.setdefault("shrooms", {}).setdefault("team", {})[str(req.team_id)] = shroom
        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def destroy_shroom(self, match_id: str, req: DestroyShroomRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        shroom_type = str(req.shroom_type or "").strip().lower()
        if shroom_type == "team":
            team_key = str(int(req.team_id or -1))
            shroom = runtime.setdefault("shrooms", {}).setdefault("team", {}).get(team_key)
            if not shroom:
                raise HTTPException(status_code=404, detail="Unknown team shroom.")
            shroom["state"] = "destroyed"
            shroom["is_destroyed"] = True
            shroom["updated_at"] = now_ts()
        elif shroom_type == "personal":
            owner_id = str(req.owner_id or "").strip()
            shroom = runtime.setdefault("shrooms", {}).setdefault("personal", {}).get(owner_id)
            if not shroom:
                raise HTTPException(status_code=404, detail="Unknown personal shroom.")
            shroom["state"] = "cooldown"
            shroom["is_destroyed"] = True
            shroom["remaining_cooldown_seconds"] = float(shroom.get("cooldown_seconds", 240))
            shroom["updated_at"] = now_ts()
        else:
            raise HTTPException(status_code=400, detail="Unknown shroom type.")

        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def discover_hole(self, match_id: str, req: HoleDiscoveryRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        player = runtime.setdefault("players", {}).get(req.player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Unknown runtime player.")
        holes = runtime.setdefault("holes", {})
        if req.hole_id not in holes:
            raise HTTPException(status_code=404, detail="Unknown hole.")
        discovered = player.setdefault("discovered_holes", [])
        if req.hole_id not in discovered:
            discovered.append(req.hole_id)
        player["updated_at"] = now_ts()
        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def travel_hole(self, match_id: str, req: HoleTravelRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        player = runtime.setdefault("players", {}).get(req.player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Unknown runtime player.")
        if str(player.get("role", "warble")) == "biggun":
            raise HTTPException(status_code=403, detail="Bigguns cannot use the Warble hole network.")
        holes = runtime.setdefault("holes", {})
        hole = holes.get(req.destination_hole_id)
        if not hole:
            raise HTTPException(status_code=404, detail="Unknown destination hole.")
        if req.destination_hole_id not in player.setdefault("discovered_holes", []):
            raise HTTPException(status_code=403, detail="That hole has not been discovered yet.")

        player["last_hole_id"] = req.destination_hole_id
        runtime.setdefault("travel_log", []).append(
            {
                "player_id": req.player_id,
                "destination_hole_id": req.destination_hole_id,
                "label": hole.get("label", req.destination_hole_id),
                "ts": now_ts(),
            }
        )
        player["updated_at"] = now_ts()
        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def apply_mission_action(self, match_id: str, req: MissionActionRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        player = runtime.setdefault("players", {}).get(req.player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Unknown runtime player.")

        mission_ids = {str(row.get("mission_id", "")) for row in runtime.get("missions", [])}
        if req.mission_id not in mission_ids:
            raise HTTPException(status_code=404, detail="Unknown mission.")

        action = str(req.action or "").strip().lower()
        amount = max(1, int(req.amount))
        if action == "collect":
            player["resources"] = int(player.get("resources", 0)) + amount
        elif action == "rescue":
            player["rescues"] = int(player.get("rescues", 0)) + amount
        elif action == "bait":
            player["sound_baits"] = int(player.get("sound_baits", 0)) + amount
        elif action == "sabotage":
            player["sabotages"] = int(player.get("sabotages", 0)) + amount
        else:
            raise HTTPException(status_code=400, detail="Unknown mission action.")

        progress = runtime.setdefault("mission_progress", {})
        progress[req.mission_id] = int(progress.get(req.mission_id, 0)) + amount
        player["updated_at"] = now_ts()
        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def capture_warble(self, match_id: str, req: CaptureActionRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        target = runtime.setdefault("players", {}).get(req.target_player_id)
        if not target:
            raise HTTPException(status_code=404, detail="Unknown target player.")
        target["is_captured"] = True
        target["is_consumed"] = False
        target["updated_at"] = now_ts()
        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def rescue_warble(self, match_id: str, req: CaptureActionRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        target = runtime.setdefault("players", {}).get(req.target_player_id)
        if not target:
            raise HTTPException(status_code=404, detail="Unknown target player.")
        target["is_captured"] = False
        target["updated_at"] = now_ts()
        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def consume_warble(self, match_id: str, req: CaptureActionRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        target = runtime.setdefault("players", {}).get(req.target_player_id)
        if not target:
            raise HTTPException(status_code=404, detail="Unknown target player.")
        target["is_captured"] = False
        target["is_consumed"] = True
        target["updated_at"] = now_ts()
        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def apply_trauma(self, match_id: str, req: TraumaApplyRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        player = runtime.setdefault("players", {}).get(req.player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Unknown runtime player.")

        recover_seconds = 10.0 if req.goo_covered else max(2.0, min(8.0, 8.0 - float(req.distance_meters) * 0.15))
        runtime.setdefault("trauma_states", {})[req.player_id] = {
            "player_id": req.player_id,
            "is_witness_shocked": True,
            "is_goo_covered": bool(req.goo_covered),
            "recover_in_seconds": recover_seconds,
            "source": "goo_contact" if req.goo_covered else "witness_shock",
        }
        runtime["updated_at"] = now_ts()
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def create_guest(self, req: GuestSessionRequest) -> dict[str, Any]:
        clean_name = (req.name or "").strip()[:20]
        if not clean_name:
            raise HTTPException(status_code=400, detail="Name is required")

        map_row = self.map_by_id(req.map_id)
        spawn = self.choose_spawn(map_row, req.spawn_preference)
        session_id = f"{SESSION_PREFIX}_{secrets.token_hex(6)}"
        entry = {
            "session_id": session_id,
            "player_name": clean_name,
            "map_id": map_row["map_id"],
            "map_title": map_row["title"],
            "spawn": spawn,
            "stats": {"points": 0, "rescues": 0, "resources": 0, "deaths": 0},
            "quest_state": {
                "active": "Rescue two burrowfolk cages and bring back three glowcaps.",
                "progress": {"rescues_needed": 2, "rescues_done": 0, "glowcaps_needed": 3, "glowcaps_found": 0},
            },
            "status": "active",
            "last_seen": now_ts(),
        }
        self.sessions[session_id] = entry
        self.events.appendleft(
            self.make_event(
                kind="guest_join",
                session_id=session_id,
                map_id=map_row["map_id"],
                note=f"{clean_name} joined {map_row['title']}.",
                payload={"player_name": clean_name, "spawn": spawn.get("id")},
            )
        )
        self.persist()
        return entry

    def snapshot(self, session_id: str) -> dict[str, Any]:
        entry = self.sessions.get(session_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Unknown session")

        previous_seen = int(entry.get("last_seen", 0))
        entry["last_seen"] = now_ts()
        if entry["last_seen"] - previous_seen >= 10:
            self.persist()

        map_id = entry.get("map_id", "")
        presence = [
            {
                "player_name": row.get("player_name"),
                "points": (row.get("stats") or {}).get("points", 0),
                "rescues": (row.get("stats") or {}).get("rescues", 0),
            }
            for row in self.sessions.values()
            if row.get("map_id") == map_id
        ]
        return {
            "session": entry,
            "presence": presence,
            "recent_events": [row for row in list(self.events)[:12] if row.get("map_id") in {"", map_id} or row.get("session_id") == session_id],
        }

    def apply_action(self, session_id: str, action: str, value: int | None = None) -> dict[str, Any]:
        entry = self.sessions.get(session_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Unknown session")

        stats = entry.setdefault("stats", {})
        progress = entry.setdefault("quest_state", {}).setdefault("progress", {})
        clean = (action or "").strip().lower()
        note = ""

        if clean == "forage":
            delta = max(1, min(int(value or 1), 5))
            stats["resources"] = int(stats.get("resources", 0)) + delta
            stats["points"] = int(stats.get("points", 0)) + (delta * 10)
            progress["glowcaps_found"] = min(int(progress.get("glowcaps_found", 0)) + delta, int(progress.get("glowcaps_needed", 3)))
            note = f"Foraged {delta} resources."
        elif clean == "rescue":
            stats["rescues"] = int(stats.get("rescues", 0)) + 1
            stats["points"] = int(stats.get("points", 0)) + 75
            progress["rescues_done"] = min(int(progress.get("rescues_done", 0)) + 1, int(progress.get("rescues_needed", 2)))
            note = "Rescue completed."
        elif clean == "respawn":
            stats["deaths"] = int(stats.get("deaths", 0)) + 1
            map_row = self.map_by_id(str(entry.get("map_id", "")))
            entry["spawn"] = self.choose_spawn(map_row, None)
            note = f"Respawned at {entry['spawn'].get('label', 'safe burrow')}."
        elif clean == "quest_turn_in":
            stats["points"] = int(stats.get("points", 0)) + 150
            entry["quest_state"] = {
                "active": "Scout the sorcerer's wagon and recover two ember flasks.",
                "progress": {"ember_flasks_needed": 2, "ember_flasks_found": 0},
            }
            note = "Quest turned in. New objective assigned."
        else:
            raise HTTPException(status_code=400, detail="Unknown action")

        event = self.make_event(kind="action", session_id=session_id, map_id=entry.get("map_id"), note=note, action=clean)
        self.events.appendleft(event)
        entry["last_seen"] = now_ts()
        self.persist()
        return {"status": "ok", "note": note, "snapshot": self.snapshot(session_id)}

    def profile_snapshot(self, player_name: str) -> dict[str, Any]:
        clean_name = (player_name or "").strip()[:20]
        if not clean_name:
            raise HTTPException(status_code=400, detail="Player name is required")

        account = self.accounts.get(clean_login_name(clean_name))
        if account:
            return self._account_profile_payload(account)

        if self.store.enabled:
            try:
                profile = self.store.get_profile(clean_name)
                if profile:
                    return profile
            except MySQLError:
                LOGGER.exception("Failed loading Underfoot profile from MySQL.")

        live_session = next((row for row in self.sessions.values() if row.get("player_name") == clean_name), None)
        if not live_session:
            raise HTTPException(status_code=404, detail="Unknown player profile")

        stats = live_session.get("stats", {})
        points = int(stats.get("points", 0))
        return {
            "player_name": clean_name,
            "display_name": clean_name,
            "rank": max(1, (points // 500) + 1),
            "xp": points,
            "coins": points // 10,
            "totals": {
                "points": points,
                "rescues": int(stats.get("rescues", 0)),
                "resources": int(stats.get("resources", 0)),
                "deaths": int(stats.get("deaths", 0)),
            },
            "last_session_id": live_session.get("session_id"),
        }

    def register_account(self, req: AccountRegisterRequest) -> dict[str, Any]:
        login_name = validate_login_name(req.login_name, "auth_register")
        if len((req.password or "").strip()) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
        if login_name in self.accounts:
            log_auth_validation_rejection("auth_register", "login_name", "claimed", login_name, login_name)
            raise HTTPException(status_code=409, detail="That login name is already claimed.")

        account_id = f"acct_{secrets.token_hex(6)}"
        display_name = normalize_account_display_name(req.display_name, login_name, route="auth_register", strict=True)
        salt_hex, digest_hex = build_password_hash(req.password)
        account = {
            "account_id": account_id,
            "player_id": stable_player_id(login_name),
            "login_name": login_name,
            "display_name": display_name,
            "body_type": normalize_unreal_body_type(req.body_type),
            "password_salt": salt_hex,
            "password_hash": digest_hex,
            "faction_key": normalize_live_faction_key(req.faction_key),
            "hair_style_key": (req.hair_style_key or "wavy_crown").strip() or "wavy_crown",
            "hair_tint_key": (req.hair_tint_key or "cream").strip() or "cream",
            "primary_color_key": (req.primary_color_key or "ocean_blue").strip() or "ocean_blue",
            "outfit_key": (req.outfit_key or "scout_wrap").strip() or "scout_wrap",
            "accessory_key": (req.accessory_key or "none").strip() or "none",
            "hat_key": (req.hat_key or "none").strip() or "none",
            "jewelry_key": (req.jewelry_key or "none").strip() or "none",
            "gloves_key": (req.gloves_key or "none").strip() or "none",
            "gear_skin_key": (req.gear_skin_key or "standard_kit").strip() or "standard_kit",
            "biggun_eligible": bool(req.biggun_eligible),
            "profile_level": 1,
            "warble_rank": 1,
            "biggun_rank": 1,
            "xp": 0,
            "coins": 0,
            "created_at": now_ts(),
            "updated_at": now_ts(),
        }
        self.accounts[login_name] = account
        token = self._issue_auth_token(account)
        self.persist()
        return {"status": "ok", "auth_token": token, "authToken": token, "profile": self._account_profile_payload(account)}

    def login_account(self, req: AccountLoginRequest) -> dict[str, Any]:
        login_name = validate_login_name(req.login_name, "auth_login")
        account = self.accounts.get(login_name)
        if not account:
            raise HTTPException(status_code=404, detail="Unknown account.")
        if not verify_password(req.password, str(account.get("password_salt", "")), str(account.get("password_hash", ""))):
            raise HTTPException(status_code=403, detail="Invalid login credentials.")
        token = self._issue_auth_token(account)
        self.persist()
        return {"status": "ok", "auth_token": token, "authToken": token, "profile": self._account_profile_payload(account)}

    def auth_session(self, token: str) -> dict[str, Any]:
        clean_token = (token or "").strip()
        auth_row = self.auth_tokens.get(clean_token)
        if not auth_row:
            raise HTTPException(status_code=404, detail="Unknown auth session.")
        account = self.accounts.get(str(auth_row.get("login_name", "")))
        if not account:
            raise HTTPException(status_code=404, detail="The profile for this auth session no longer exists.")
        return {"status": "ok", "auth_token": clean_token, "authToken": clean_token, "profile": self._account_profile_payload(account)}

    def resolve_steam_platform_profile(self, req: SteamPlatformResolveRequest) -> dict[str, Any]:
        steam_account_id = str(req.steam_account_id or "").strip()
        if not steam_account_id:
            raise HTTPException(status_code=400, detail="steamAccountId is required.")

        account = next(
            (row for row in self.accounts.values() if str(row.get("steam_account_id", "")).strip() == steam_account_id),
            None,
        )
        public_display_name = build_unreal_public_display_name(req.public_display_name or req.display_name or "Player")
        display_name = normalize_account_display_name(req.display_name or public_display_name, public_display_name, route="steam_resolve", strict=False)
        warble_color = normalize_unreal_warble_color(req.warble_color)
        body_type = normalize_unreal_body_type(req.body_type)
        spawn_placement = normalize_unreal_spawn_placement(req.spawn_placement)
        preferred_role = "biggun" if str(req.preferred_role or "").strip().lower() == "biggun" else "warble"

        if not account:
            login_name = clean_login_name(f"steam_{steam_account_id}")
            if not login_name or login_name in self.accounts:
                login_name = f"steam_{hashlib.sha1(steam_account_id.encode('utf-8')).hexdigest()[:12]}"
            account = {
                "account_id": f"acct_{secrets.token_hex(6)}",
                "player_id": stable_player_id(login_name),
                "login_name": login_name,
                "display_name": display_name,
                "public_display_name": public_display_name,
                "steam_account_id": steam_account_id,
                "password_salt": "",
                "password_hash": "",
                "faction_key": normalize_live_faction_key(warble_color),
                "body_type": body_type,
                "hair_style_key": "wavy_crown",
                "hair_tint_key": "cream",
                "primary_color_key": "ocean_blue",
                "outfit_key": "scout_wrap",
                "accessory_key": "none",
                "hat_key": "none",
                "jewelry_key": "none",
                "gloves_key": "none",
                "gear_skin_key": "standard_kit",
                "biggun_eligible": True,
                "preferred_role": preferred_role,
                "warble_color": warble_color,
                "spawn_placement": spawn_placement,
                "customization_completed": bool(req.customization_completed),
                "audio_device_state": str(req.audio_device_state or "").strip(),
                "profile_level": 1,
                "warble_rank": 1,
                "biggun_rank": 1,
                "xp": 0,
                "coins": 0,
                "created_at": now_ts(),
                "updated_at": now_ts(),
            }
            self.accounts[login_name] = account
        else:
            account["display_name"] = display_name
            account["public_display_name"] = public_display_name
            account["steam_account_id"] = steam_account_id
            account["preferred_role"] = preferred_role
            account["warble_color"] = warble_color
            account["body_type"] = body_type
            account["spawn_placement"] = spawn_placement
            account["customization_completed"] = bool(req.customization_completed)
            account["audio_device_state"] = str(req.audio_device_state or "").strip()
            account["faction_key"] = normalize_live_faction_key(warble_color)
            account["updated_at"] = now_ts()

        token = self._issue_auth_token(account)
        self.persist()
        return {"status": "ok", "authToken": token, "profile": self._account_profile_payload(account)}

    def _resolve_account_from_auth_token(self, token: str) -> dict[str, Any] | None:
        clean_token = (token or "").strip()
        if not clean_token:
            return None
        auth_row = self.auth_tokens.get(clean_token)
        if not auth_row:
            return None
        login_name = clean_login_name(str(auth_row.get("login_name", "")))
        if not login_name:
            return None
        return self.accounts.get(login_name)

    def _can_use_owner_queue_fill(self, token: str) -> bool:
        account = self._resolve_account_from_auth_token(token)
        if not account:
            return False
        login_name = clean_login_name(str(account.get("login_name", "")))
        if not login_name:
            return False
        allowlist = {OWNER_QUEUE_FILL_LOGIN, "alex", "alexc"}
        allowlist_raw = str(os.getenv("UNDERFOOT_OWNER_QUEUE_FILL_LOGINS", "") or "").replace("|", ",")
        for raw_entry in allowlist_raw.split(","):
            clean_entry = clean_login_name(raw_entry)
            if clean_entry:
                allowlist.add(clean_entry)
        return login_name in allowlist

    def update_profile_appearance(self, req: ProfileAppearanceUpdateRequest) -> dict[str, Any]:
        account_id = (req.account_id or "").strip()
        if not account_id:
            raise HTTPException(status_code=400, detail="Account id is required.")

        account = next((row for row in self.accounts.values() if str(row.get("account_id", "")) == account_id), None)
        if not account:
            raise HTTPException(status_code=404, detail="Unknown account.")

        account["faction_key"] = normalize_live_faction_key(req.faction_key)
        account["body_type"] = normalize_unreal_body_type(req.body_type)
        account["hair_style_key"] = (req.hair_style_key or "wavy_crown").strip() or "wavy_crown"
        account["hair_tint_key"] = (req.hair_tint_key or "cream").strip() or "cream"
        account["primary_color_key"] = (req.primary_color_key or "ocean_blue").strip() or "ocean_blue"
        account["outfit_key"] = (req.outfit_key or "scout_wrap").strip() or "scout_wrap"
        account["accessory_key"] = (req.accessory_key or "none").strip() or "none"
        account["hat_key"] = (req.hat_key or "none").strip() or "none"
        account["jewelry_key"] = (req.jewelry_key or "none").strip() or "none"
        account["gloves_key"] = (req.gloves_key or "none").strip() or "none"
        account["gear_skin_key"] = (req.gear_skin_key or "standard_kit").strip() or "standard_kit"
        if req.display_name is not None:
            account["display_name"] = normalize_account_display_name(req.display_name, str(account.get("login_name", "")), route="auth_profile_update", strict=False)
        if req.public_display_name is not None:
            account["public_display_name"] = build_unreal_public_display_name(req.public_display_name or account.get("display_name", "Player"))
        if req.preferred_role is not None:
            account["preferred_role"] = "biggun" if str(req.preferred_role or "").strip().lower() == "biggun" else "warble"
        if req.warble_color is not None:
            account["warble_color"] = normalize_unreal_warble_color(req.warble_color)
            account["faction_key"] = normalize_live_faction_key(account["warble_color"])
        if req.spawn_placement is not None:
            account["spawn_placement"] = normalize_unreal_spawn_placement(req.spawn_placement)
        if req.customization_completed is not None:
            account["customization_completed"] = bool(req.customization_completed)
        if req.audio_device_state is not None:
            account["audio_device_state"] = str(req.audio_device_state or "").strip()
        account["updated_at"] = now_ts()
        self.persist()
        return {"status": "ok", "profile": self._account_profile_payload(account)}

    def queue_unreal_client(self, req: UnrealQueueEnqueueRequest) -> dict[str, Any]:
        public_display_name = build_unreal_public_display_name(req.public_display_name or req.display_name or "Player")
        warble_color = normalize_unreal_warble_color(req.warble_color)
        spawn_placement = normalize_unreal_spawn_placement(req.spawn_placement)
        account = next((row for row in self.accounts.values() if str(row.get("account_id", "")).strip() == str(req.account_id or "").strip()), None)
        hair_style_key = str((account or {}).get("hair_style_key", "wavy_crown")).strip() or "wavy_crown"
        hair_tint_key = str((account or {}).get("hair_tint_key", "cream")).strip() or "cream"
        queue_result = self.queue_quickplay(
            QueueTicketRequest(
                accountId=str(req.account_id or "").strip(),
                displayName=public_display_name,
                playerId=str(req.player_id or "").strip(),
                factionKey=warble_color,
                hairStyleKey=hair_style_key,
                hairTintKey=hair_tint_key,
                requestedRole=req.requested_role,
                queuePresetKey="underfoot_quickplay",
                partySize=1,
                joinStyle="solo",
                autoReady=bool(req.auto_ready),
            )
        )

        ticket = dict(queue_result.get("ticket", {}))
        assignment = dict(queue_result.get("assignment", {}))
        runtime = dict(queue_result.get("runtime", {}))
        match_id = str(ticket.get("match_id", "")).strip()
        if match_id:
            live_runtime = self.match_runtimes.get(match_id)
            if isinstance(live_runtime, dict):
                player = live_runtime.setdefault("players", {}).get(str(req.player_id or "").strip())
                if isinstance(player, dict):
                    player["public_display_name"] = public_display_name
                    player["spawn_placement"] = spawn_placement
                    player["body_type"] = normalize_unreal_body_type((account or {}).get("body_type", "female"))
                    player["hair_style_key"] = hair_style_key
                    player["accessory_key"] = str((account or {}).get("accessory_key", "none")).strip() or "none"
                    player["build_fingerprint"] = str(req.build_fingerprint or "").strip()
                    player["updated_at"] = now_ts()
                    live_runtime["updated_at"] = now_ts()
                    runtime = self.build_match_runtime_snapshot(match_id)
                    self.persist()

        ticket["buildFingerprint"] = str(req.build_fingerprint or "").strip()
        ticket["spawnPlacement"] = spawn_placement
        ticket["publicDisplayName"] = public_display_name
        return {"ticket": ticket, "assignment": assignment, "runtime": runtime}

    def get_unreal_queue_assignment(self, ticket_id: str) -> dict[str, Any]:
        ticket = self.get_queue_ticket(ticket_id)
        match_id = str(ticket.get("match_id", "")).strip()
        assignment = self.get_match_assignment(match_id) if match_id else {}
        runtime = self.build_match_runtime_snapshot(match_id) if match_id else {}
        return {"ticket": ticket, "assignment": assignment, "runtime": runtime}

    def submit_unreal_match_result(self, req: UnrealMatchResultRequest) -> dict[str, Any]:
        applied_rewards = []
        for player in req.players:
            account_id = str(player.account_id or "").strip()
            if not account_id:
                continue
            account = next((row for row in self.accounts.values() if str(row.get("account_id", "")).strip() == account_id), None)
            if not account:
                continue
            xp_delta = max(0, int(player.xp_delta))
            coins_delta = max(0, int(player.coins_delta))
            account["xp"] = int(account.get("xp", 0)) + xp_delta
            account["coins"] = int(account.get("coins", 0)) + coins_delta
            account["warble_rank"] = max(1, (int(account.get("xp", 0)) // 500) + 1)
            account["profile_level"] = max(1, (int(account.get("xp", 0)) // 1000) + 1)
            account["updated_at"] = now_ts()
            applied_rewards.append(
                {
                    "accountId": account_id,
                    "playerId": str(player.player_id or "").strip(),
                    "xpDelta": xp_delta,
                    "coinsDelta": coins_delta,
                }
            )

        summary = {
            "category": "unreal_match_result",
            "matchId": str(req.match_id or "").strip(),
            "assignmentId": str(req.assignment_id or "").strip(),
            "mapId": str(req.map_id or "").strip(),
            "modeKey": str(req.mode_key or "").strip(),
            "winnerTeamId": int(req.winner_team_id),
            "durationSeconds": int(req.duration_seconds),
            "buildFingerprint": str(req.build_fingerprint or "").strip(),
            "buildStamp": str(req.build_stamp or "").strip(),
            "playerCount": len(req.players),
            "createdAtEpoch": now_ts(),
        }
        payload = {
            "matchId": req.match_id,
            "assignmentId": req.assignment_id,
            "mapId": req.map_id,
            "modeKey": req.mode_key,
            "winnerTeamId": req.winner_team_id,
            "durationSeconds": req.duration_seconds,
            "buildFingerprint": req.build_fingerprint,
            "buildStamp": req.build_stamp,
            "players": [player.model_dump(by_alias=True) for player in req.players],
            "appliedRewards": applied_rewards,
        }
        self.persist()
        return self._store_report("runtime_diagnostic", summary, payload, [])

    def update_unreal_voice_presence(self, req: UnrealVoicePresenceRequest) -> dict[str, Any]:
        clean_match_id = str(req.match_id or "").strip()
        if clean_match_id:
            runtime = self.ensure_match_runtime(clean_match_id)
            player = runtime.setdefault("players", {}).get(req.player_id)
            if player:
                player["last_voice_mode"] = str(req.voice_mode or "").strip()
                player["last_voice_connection_state"] = str(req.voice_connection_state or "").strip()
                player["audio_device_state"] = str(req.audio_device_state or "").strip()
                player["last_presence_epoch"] = now_ts()
                player["updated_at"] = now_ts()
                runtime["updated_at"] = now_ts()
                self.persist()
                return {"status": "ok", "voice": self.build_match_voice_state(clean_match_id)}

        if req.account_id:
            account = next((row for row in self.accounts.values() if str(row.get("account_id", "")).strip() == str(req.account_id).strip()), None)
            if account:
                account["audio_device_state"] = str(req.audio_device_state or "").strip()
                account["updated_at"] = now_ts()
                self.persist()
        return {"status": "ok", "voice": {"voiceMode": str(req.voice_mode or "").strip(), "voiceConnectionState": str(req.voice_connection_state or "").strip()}}

    def build_shell_snapshot(self) -> dict[str, Any]:
        self.run_maintenance_cleanup()
        runtime_health = self._summarize_dedicated_runtime_health()
        return {
            "project": PROJECT_NAME,
            "factions": DEFAULT_FACTIONS,
            "hair_styles": DEFAULT_HAIR_STYLES,
            "hair_tints": DEFAULT_HAIR_TINTS,
            "featured_missions": DEFAULT_FEATURED_MISSIONS,
            "quickplay": self.list_quickplay(),
            "setup_matches": self.list_setup_matches(),
            "browser": self.list_match_browser(),
            "match_runtimes": [
                {
                    "match_id": snapshot["match_id"],
                    "phase": snapshot["phase"],
                    "ready_teams": snapshot["ready_teams"],
                    "captured_warbles": snapshot["captured_warbles"],
                    "consumed_warbles": snapshot["consumed_warbles"],
                    "surviving_team_shrooms": snapshot["surviving_team_shrooms"],
                }
                for snapshot in [self.build_match_runtime_snapshot(match_id) for match_id in sorted(self.match_runtimes.keys(), key=str)]
            ],
            "private_rooms": self.list_private_rooms(),
            "dedicated_servers": self.list_dedicated_servers(),
            "voice_policy": {
                "open_mic": True,
                "proximity_voice_only": True,
                "enforcement_mode": VOICE_ENFORCEMENT_MODE,
                "text_chat": "disabled",
                "nameplates": "disabled",
                "gateway_http_url": VOICE_GATEWAY_HTTP_URL,
                "gateway_web_socket_url": VOICE_GATEWAY_WS_URL,
                "transport_mode": VOICE_TRANSPORT_MODE,
                "token_ttl_seconds": VOICE_JOIN_TTL_SECONDS,
                "silence_grace_seconds": VOICE_SILENCE_GRACE_SECONDS,
                "silence_warning_seconds": VOICE_SILENCE_WARNING_SECONDS,
                "silence_reveal_seconds": VOICE_SILENCE_REVEAL_SECONDS,
            },
            "stabilization_profile": build_stabilization_profile_payload(),
            "network_decision_gate": build_network_decision_gate_payload(runtime_health.get("runtimeCompatibilityState", "monitoring")),
            "shroom_rules": {
                "personal_cast_seconds": 30,
                "personal_cooldown_seconds": 240,
                "team_cast_seconds": 90,
                "team_helper_reduction_seconds": 15,
                "team_cast_floor_seconds": 30,
            },
        }

    def list_quickplay(self) -> list[dict[str, Any]]:
        self.run_maintenance_cleanup()
        listings = [
            {
                "match_id": f"{SESSION_PREFIX}_quick_3x6",
                "host_name": "Underfoot Found",
                "map_id": "blackpot_hollow",
                "mode_key": "underfoot_full_test",
                "current_players": 0,
                "max_players": 20,
                "open_biggun_slots": 1,
                "joinable": True,
                "simulated_players": 0,
                "queue_preset_key": "underfoot_quickplay",
                "queue_preset_label": "Quickplay: Warble Found",
                "stage_key": "potion_hut_panic",
                "stage_label": "Blackpot Hut",
                "hidden_biggun_queued": True,
                "public_biggun_label": "Unknown Biggun",
                "warble_color_key": "red",
                "party_size": 1,
                "queue_join_style": "solo",
                "listing_type": "quickplay",
                "display_label": "Warble Found | 3 Team Live Slice",
                "team_setup_label": "Red vs Yellow vs Blue | 3-6 warbles",
                "queue_destination_label": "Team Lobby",
                "team_count": 3,
                "min_team_size": 3,
                "max_team_size": 6,
                "ready_teams": 0,
                "requires_biggun_ready": True,
                "admin_fill_enabled": False,
                "local_shroom_rule_label": "Personal 240s | Team 90s",
            },
        ]

        for listing in listings:
            runtime = self.match_runtimes.get(str(listing.get("match_id", "")))
            if not runtime:
                continue
            snapshot = self.build_match_runtime_snapshot(str(listing.get("match_id", "")))
            listing["current_players"] = int(snapshot.get("active_player_count", len(snapshot.get("players", []))))
            listing["ready_teams"] = int(snapshot.get("ready_teams", 0))
            biggun_count = sum(
                1
                for player in snapshot.get("players", [])
                if str(player.get("role", "")) == "biggun" and not bool(player.get("is_backfill_eligible", False))
            )
            listing["open_biggun_slots"] = max(0, int(snapshot.get("biggun_slots_required", 1)) - biggun_count)
            listing["warble_color_key"] = LIVE_TEAM_FACTION_ORDER[0]
            listing["team_ready_flags"] = dict(snapshot.get("team_ready_flags", {}))
            listing["countdown_state"] = dict(snapshot.get("countdown_state", {}))
            listing["biggun_state"] = dict(snapshot.get("biggun_state", {}))
            listing["lobby_start_gate"] = dict(snapshot.get("lobby_start_gate", {}))

        return listings

    def create_setup_match(self, req: SetupMatchRequest) -> dict[str, Any]:
        self.run_maintenance_cleanup()
        match_id = f"{SESSION_PREFIX}_setup_{secrets.token_hex(5)}"
        team_count = 3
        team_size = max(3, min(int(req.team_size), 6))
        warble_slots = team_count * team_size
        max_biggun_slots = 1 if int(req.biggun_override) == 1 else 2
        max_players = warble_slots + max_biggun_slots
        listing = {
            "match_id": match_id,
            "host_name": req.host_display_name.strip()[:32] or "Host",
            "host_account_id": req.host_account_id,
            "map_id": "blackpot_hollow",
            "mode_key": "underfoot_full_test",
            "current_players": 1,
            "max_players": max_players,
            "open_biggun_slots": 1,
            "joinable": True,
            "simulated_players": 0,
            "queue_preset_key": req.queue_preset_key,
            "queue_preset_label": "Setup Game",
            "stage_key": "potion_hut_panic",
            "stage_label": "Blackpot Hut",
            "hidden_biggun_queued": True,
            "public_biggun_label": "Unknown Biggun",
            "warble_color_key": "red",
            "party_size": 1,
            "queue_join_style": "solo",
            "listing_type": "custom_hosted",
            "display_label": req.host_display_name.strip()[:32] or "Host",
            "team_setup_label": f"Red vs Yellow vs Blue | 3-{team_size} warbles",
            "queue_destination_label": "Team Lobby",
            "team_count": team_count,
            "min_team_size": 3,
            "max_team_size": team_size,
            "ready_teams": 0,
            "requires_biggun_ready": True,
            "admin_fill_enabled": False,
            "local_shroom_rule_label": "Personal 240s | Team 90s",
            "match_minutes": max(30, min(int(req.match_minutes), 45)),
            "base_biggun_slots": 1,
            "max_biggun_slots": max_biggun_slots,
            "second_biggun_unlock_warble_count": warble_slots,
            "custom_cross_role_intelligible": bool(req.custom_cross_role_intelligible),
        }
        self.setup_matches[match_id] = listing
        self.ensure_match_runtime(match_id)
        assignment = self.get_match_assignment(match_id)
        self.persist()
        return {"match": listing, "assignment": assignment}

    def queue_quickplay(self, req: QueueTicketRequest) -> dict[str, Any]:
        listing = self.choose_quickplay_listing(req)
        match_id = str(listing.get("match_id", "")).strip()
        ticket_id = f"{SESSION_PREFIX}_queue_{secrets.token_hex(6)}"
        requested_role = "biggun" if str(req.requested_role or "").strip().lower() == "biggun" else "warble"
        selected_team_id = int(listing.get("selected_team_id", -1) or -1)
        runtime = self.join_match(
            match_id,
            MatchJoinRequest(
                accountId=req.account_id,
                displayName=req.display_name,
                playerId=req.player_id,
                factionKey=req.faction_key,
                hairStyleKey=req.hair_style_key,
                hairTintKey=req.hair_tint_key,
                role=requested_role,
                teamId=selected_team_id,
                isHost=False,
                isSimulated=False,
            ),
        )
        if bool(req.auto_ready):
            runtime = self.set_match_ready(match_id, MatchReadyRequest(playerId=req.player_id, accountId=req.account_id, isReady=True))

        ticket = {
            "ticket_id": ticket_id,
            "ticketId": ticket_id,
            "status": "assigned",
            "match_id": match_id,
            "queue_preset_key": str(req.queue_preset_key or "underfoot_quickplay").strip() or "underfoot_quickplay",
            "requested_role": requested_role,
            "player_id": req.player_id,
            "account_id": req.account_id,
            "display_label": str(listing.get("display_label", match_id)),
            "auto_ready": bool(req.auto_ready),
            "assignment_id": "",
            "created_at": now_ts(),
            "updated_at": now_ts(),
        }
        assignment = self.get_match_assignment(match_id)
        ticket["assignment_id"] = assignment.get("assignment_id", "")
        self.queue_tickets[ticket_id] = ticket
        self.persist()
        return {"ticket": ticket, "runtime": runtime, "assignment": assignment}

    def get_queue_ticket(self, ticket_id: str) -> dict[str, Any]:
        ticket = self.queue_tickets.get((ticket_id or "").strip())
        if not ticket:
            raise HTTPException(status_code=404, detail="Unknown queue ticket.")
        ticket_copy = dict(ticket)
        ticket_copy.setdefault("ticketId", str(ticket_copy.get("ticket_id", "")).strip())
        return ticket_copy

    def use_skill(self, match_id: str, req: SkillUseRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        player = runtime.setdefault("players", {}).get(req.player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Unknown runtime player.")
        if str(player.get("role", "warble")) != "warble":
            raise HTTPException(status_code=403, detail="Only Warbles can use the starter skill kit.")

        skill_key = str(req.skill_key or "").strip().lower()
        now_epoch = now_ts()
        skill_state = self.ensure_player_skill_state(runtime, req.player_id)
        cooldown_field_map = {
            "hide_in_grass": "hide_in_grass_ready_at_epoch",
            "hole_dash": "hole_dash_ready_at_epoch",
            "tiny_carry_boost": "tiny_carry_boost_ready_at_epoch",
            "shroom_ritual_boost": "shroom_ritual_boost_ready_at_epoch",
            "sound_bait": "sound_bait_ready_at_epoch",
        }
        cooldown_seconds_map = {
            "hide_in_grass": SKILL_HIDE_IN_GRASS_COOLDOWN_SECONDS,
            "hole_dash": SKILL_HOLE_DASH_COOLDOWN_SECONDS,
            "tiny_carry_boost": SKILL_TINY_CARRY_BOOST_COOLDOWN_SECONDS,
            "shroom_ritual_boost": SKILL_SHROOM_RITUAL_BOOST_COOLDOWN_SECONDS,
            "sound_bait": SKILL_SOUND_BAIT_COOLDOWN_SECONDS,
        }
        if skill_key not in cooldown_field_map:
            raise HTTPException(status_code=400, detail="Unknown Warble skill.")

        ready_at_field = cooldown_field_map[skill_key]
        if int(skill_state.get(ready_at_field, 0)) > now_epoch:
            remaining = int(skill_state.get(ready_at_field, 0)) - now_epoch
            raise HTTPException(status_code=409, detail=f"{skill_key} is on cooldown for {remaining}s.")

        skill_state[ready_at_field] = now_epoch + cooldown_seconds_map[skill_key]
        skill_state["updated_at"] = now_epoch

        if skill_key == "hide_in_grass":
            skill_state["hidden_until_epoch"] = now_epoch + SKILL_HIDE_IN_GRASS_DURATION_SECONDS
        elif skill_key == "hole_dash":
            destination_hole_id = str(req.target_hole_id or player.get("last_hole_id", "")).strip()
            if destination_hole_id:
                discovered = player.setdefault("discovered_holes", [])
                if destination_hole_id not in discovered:
                    discovered.append(destination_hole_id)
                runtime.setdefault("travel_log", []).append(
                    {
                        "player_id": req.player_id,
                        "destination_hole_id": destination_hole_id,
                        "label": f"Dash to {destination_hole_id}",
                        "ts": now_epoch,
                        "travel_style": "hole_dash",
                    }
                )
                player["last_hole_id"] = destination_hole_id
        elif skill_key == "tiny_carry_boost":
            skill_state["carry_boost_until_epoch"] = now_epoch + SKILL_TINY_CARRY_BOOST_DURATION_SECONDS
            player["resources"] = int(player.get("resources", 0)) + 1
        elif skill_key == "shroom_ritual_boost":
            team_key = str(int(player.get("team_id", -1)))
            team_shroom = runtime.setdefault("shrooms", {}).setdefault("team", {}).get(team_key)
            if team_shroom:
                team_shroom["helper_count"] = int(team_shroom.get("helper_count", 1)) + 1
                team_shroom["cast_seconds"] = max(30, int(team_shroom.get("cast_seconds", 90)) - 15)
                team_shroom["updated_at"] = now_epoch
        elif skill_key == "sound_bait":
            player["sound_baits"] = int(player.get("sound_baits", 0)) + 1
            rival_team_id = int(req.target_team_id)
            if rival_team_id < 0:
                rival_team_id = next(
                    (
                        int(row.get("team_id", -1))
                        for row in runtime.setdefault("players", {}).values()
                        if str(row.get("role", "warble")) == "warble" and int(row.get("team_id", -1)) != int(player.get("team_id", -1))
                    ),
                    0,
                )
            threat_id = f"bait_{secrets.token_hex(4)}"
            runtime.setdefault("ai_threats", {})[threat_id] = {
                "threat_id": threat_id,
                "threat_type": "snake",
                "state": "baited",
                "alert_source": str(req.target_anchor_id or "sound_bait").strip() or "sound_bait",
                "target_team_id": rival_team_id,
                "target_player_id": "",
                "target_shroom_id": f"team_{rival_team_id}",
                "remaining_seconds": AI_THREAT_DURATION_SECONDS,
                "updated_at": now_epoch,
            }

        player["updated_at"] = now_epoch
        runtime["updated_at"] = now_epoch
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def place_biggun_tool(self, match_id: str, req: BiggunPlacementRequest) -> dict[str, Any]:
        runtime = self.ensure_match_runtime(match_id)
        player = runtime.setdefault("players", {}).get(req.player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Unknown runtime player.")
        if str(player.get("role", "biggun")) != "biggun":
            raise HTTPException(status_code=403, detail="Only Bigguns can place prep tools.")

        placement_type = str(req.placement_type or "").strip().lower()
        if placement_type not in {"trap", "gong_bait", "cat", "spider", "snake"}:
            raise HTTPException(status_code=400, detail="Unknown Biggun placement type.")

        now_epoch = now_ts()
        placement_id = f"placement_{secrets.token_hex(5)}"
        placement = {
            "placement_id": placement_id,
            "player_id": req.player_id,
            "placement_type": placement_type,
            "anchor_id": str(req.anchor_id or "").strip(),
            "target_team_id": int(req.target_team_id),
            "is_armed": True,
            "remaining_seconds": BIGGUN_PLACEMENT_DURATION_SECONDS,
            "updated_at": now_epoch,
        }
        runtime.setdefault("biggun_placements", {})[placement_id] = placement

        if placement_type in {"cat", "spider", "snake"}:
            threat_id = f"threat_{secrets.token_hex(5)}"
            runtime.setdefault("ai_threats", {})[threat_id] = {
                "threat_id": threat_id,
                "threat_type": placement_type,
                "state": "hunting",
                "alert_source": placement["anchor_id"] or placement_type,
                "target_team_id": int(req.target_team_id),
                "target_player_id": "",
                "target_shroom_id": f"team_{int(req.target_team_id)}" if int(req.target_team_id) >= 0 else "",
                "remaining_seconds": AI_THREAT_DURATION_SECONDS,
                "updated_at": now_epoch,
            }

        runtime["updated_at"] = now_epoch
        self.persist()
        return self.build_match_runtime_snapshot(match_id)

    def admin_fill_match(self, match_id: str, req: AdminFillRequest) -> dict[str, Any]:
        listing = self.setup_matches.get(match_id)
        runtime = self.ensure_match_runtime(match_id)
        owner_fill_allowed = self._can_use_owner_queue_fill(req.auth_token)
        listing_fill_allowed = bool(listing) and PUBLIC_ADMIN_FILL_ENABLED and bool(listing.get("admin_fill_enabled", False))
        if not (owner_fill_allowed or listing_fill_allowed):
            raise HTTPException(status_code=403, detail="Admin slot fill is disabled for public testing.")
        requested_slots = max(1, min(int(req.requested_slots), 12))
        if listing:
            listing["simulated_players"] = int(listing.get("simulated_players", 0)) + requested_slots
            listing["current_players"] = min(int(listing.get("max_players", 0)), int(listing.get("current_players", 0)) + requested_slots)
            listing["ready_teams"] = min(int(listing.get("team_count", 0)), int(listing.get("ready_teams", 0)) + max(1, requested_slots // 3))
            listing["last_fill_by"] = req.requested_by
            listing["updated_at"] = now_ts()

        players = runtime.setdefault("players", {})
        team_count = max(3, int(runtime.get("team_count", 3)))
        max_team_size = max(3, min(int(runtime.get("max_team_size", 6)), 6))
        factions = [row.get("key", "blue") for row in DEFAULT_FACTIONS] or ["blue"]
        next_index = 0
        while f"sim_fill_{next_index:03d}" in players:
            next_index += 1
        now_epoch = now_ts()

        if bool(req.target_biggun):
            for _ in range(requested_slots):
                slot_index = self._find_first_open_biggun_slot(runtime)
                if slot_index < 0:
                    break
                biggun_id = f"sim_biggun_{slot_index + 1}"
                while biggun_id in players:
                    slot_index += 1
                    biggun_id = f"sim_biggun_{slot_index + 1}"
                players[biggun_id] = {
                    "player_id": biggun_id,
                    "account_id": biggun_id,
                    "display_name": f"Hidden Biggun {slot_index + 1}",
                    "faction_key": "giant",
                    "hair_style_key": "none",
                    "hair_tint_key": "none",
                    "role": "biggun",
                    "team_id": -1,
                    "slot_index": slot_index,
                    "is_host": False,
                    "is_ready": bool(req.mark_ready),
                    "is_simulated": True,
                    "is_captured": False,
                    "is_consumed": False,
                    "is_connected": True,
                    "is_abandoned": False,
                    "is_backfill_eligible": False,
                    "abandoned_at_epoch": 0,
                    "abandoned_reason": "",
                    "last_presence_epoch": now_epoch,
                    "reconnect_grace_expires_at_epoch": now_epoch + DEDICATED_RECONNECT_GRACE_SECONDS,
                    "discovered_holes": [],
                    "resources": 0,
                    "rescues": 0,
                    "sound_baits": 0,
                    "sabotages": 0,
                    "updated_at": now_epoch,
                }
                self.ensure_player_skill_state(runtime, biggun_id)
        else:
            for _ in range(requested_slots):
                if req.target_team_id >= 0:
                    target_team = max(0, min(int(req.target_team_id), team_count - 1))
                else:
                    team_sizes = [
                        (
                            team_id,
                            sum(
                                1
                                for row in players.values()
                                if str(row.get("role", "warble")).strip() == "warble"
                                and int(row.get("team_id", -1)) == team_id
                                and self._player_counts_for_team(runtime, row)
                            ),
                        )
                        for team_id in range(team_count)
                    ]
                    team_sizes.sort(key=lambda row: (row[1], row[0]))
                    target_team = next((team_id for team_id, size in team_sizes if size < max_team_size), -1)
                    if target_team < 0:
                        break

                slot_index = self._find_first_open_team_slot(runtime, target_team)
                if slot_index < 0:
                    if req.target_team_id >= 0:
                        break
                    continue

                player_id = f"sim_fill_{next_index:03d}"
                next_index += 1
                players[player_id] = {
                    "player_id": player_id,
                    "account_id": player_id,
                    "display_name": f"Slot Filler {next_index:03d}",
                    "faction_key": factions[target_team % len(factions)],
                    "hair_style_key": "wavy_crown",
                    "hair_tint_key": "cream",
                    "role": "warble",
                    "team_id": target_team,
                    "slot_index": slot_index,
                    "is_host": False,
                    "is_ready": bool(req.mark_ready),
                    "is_simulated": True,
                    "is_captured": False,
                    "is_consumed": False,
                    "is_connected": True,
                    "is_abandoned": False,
                    "is_backfill_eligible": False,
                    "abandoned_at_epoch": 0,
                    "abandoned_reason": "",
                    "last_presence_epoch": now_epoch,
                    "reconnect_grace_expires_at_epoch": now_epoch + DEDICATED_RECONNECT_GRACE_SECONDS,
                    "discovered_holes": [],
                    "resources": 0,
                    "rescues": 0,
                    "sound_baits": 0,
                    "sabotages": 0,
                    "updated_at": now_epoch,
                }
                self._clear_team_ready_confirmation(runtime, target_team)
                self.ensure_player_skill_state(runtime, player_id)

        if req.target_team_id >= 0:
            self._clear_team_ready_confirmation(runtime, int(req.target_team_id))

        self._advance_team_lobby_start_gate(runtime, route=f"POST /api/matches/{match_id}/fill", now_epoch=now_epoch)
        self.maybe_start_match_flow(runtime)
        runtime["updated_at"] = now_epoch
        snapshot = self.build_match_runtime_snapshot(match_id)
        if listing:
            listing["current_players"] = min(int(listing.get("max_players", 0)), len(snapshot["players"]))
            listing["ready_teams"] = snapshot["ready_teams"]
        self.persist()
        match_payload = dict(listing) if listing else {
            "match_id": match_id,
            "listing_type": str(runtime.get("listing_type", "quickplay")),
            "current_players": len(snapshot["players"]),
            "ready_teams": snapshot["ready_teams"],
            "team_count": int(runtime.get("team_count", 3)),
            "simulated_players": sum(1 for player in snapshot["players"] if bool(player.get("is_simulated", False))),
            "updated_at": now_epoch,
        }
        return {
            "match": match_payload,
            "runtime": snapshot,
        }

    def create_private_room(self, req: PrivateRoomCreateRequest) -> dict[str, Any]:
        self.run_maintenance_cleanup()
        room_code = normalize_room_code(req.room_code)
        room = self.private_rooms.get(room_code)
        if room is None:
            room = {
                "room_code": room_code,
                "host_account_id": req.host_account_id,
                "host_display_name": req.host_display_name.strip()[:32] or "Host",
                "label": f"{req.host_display_name.strip()[:32] or 'Host'}'s Underground",
                "joinable": True,
                "is_owner": True,
                "occupancy": 1,
                "capacity": 8,
                "match_id": f"{SESSION_PREFIX}_room_{room_code.lower()}",
                "created_at": now_ts(),
                "updated_at": now_ts(),
            }
        else:
            room["occupancy"] = min(int(room.get("capacity", 8)), int(room.get("occupancy", 0)) + 1)
            room["match_id"] = str(room.get("match_id", f"{SESSION_PREFIX}_room_{room_code.lower()}")).strip() or f"{SESSION_PREFIX}_room_{room_code.lower()}"
            room["updated_at"] = now_ts()
        self.private_rooms[room_code] = room
        runtime = self.ensure_private_room_runtime(room)
        self.upsert_private_room_player(
            runtime,
            account_id=req.host_account_id,
            player_id=req.player_id or "",
            display_name=req.host_display_name,
            faction_key=req.faction_key or "blue",
            hair_style_key=req.hair_style_key or "wavy_crown",
            hair_tint_key=req.hair_tint_key or "cream",
            is_host=True,
        )
        room["occupancy"] = min(int(room.get("capacity", 8) or 8), len(runtime.get("players", {})))
        room["joinable"] = int(room.get("occupancy", 0)) < int(room.get("capacity", 8) or 8)
        room["updated_at"] = now_ts()
        assignment = self.get_room_assignment(room_code)
        self.persist()
        return {"room": room, "assignment": assignment}

    def join_private_room(self, req: PrivateRoomJoinRequest) -> dict[str, Any]:
        self.run_maintenance_cleanup()
        room_code = normalize_room_code(req.room_code)
        room = self.private_rooms.get(room_code)
        if not room:
            raise HTTPException(status_code=404, detail="Unknown private room.")
        room["occupancy"] = min(int(room.get("capacity", 8)), int(room.get("occupancy", 0)) + 1)
        room["last_joined_by"] = req.display_name.strip()[:32] or req.account_id
        room["match_id"] = str(room.get("match_id", f"{SESSION_PREFIX}_room_{room_code.lower()}")).strip() or f"{SESSION_PREFIX}_room_{room_code.lower()}"
        room["updated_at"] = now_ts()
        runtime = self.ensure_private_room_runtime(room)
        self.upsert_private_room_player(
            runtime,
            account_id=req.account_id,
            player_id=req.player_id or "",
            display_name=req.display_name,
            faction_key=req.faction_key or "blue",
            hair_style_key=req.hair_style_key or "wavy_crown",
            hair_tint_key=req.hair_tint_key or "cream",
            is_host=str(room.get("host_account_id", "")).strip() == str(req.account_id or "").strip(),
        )
        room["occupancy"] = min(int(room.get("capacity", 8) or 8), len(runtime.get("players", {})))
        room["joinable"] = int(room.get("occupancy", 0)) < int(room.get("capacity", 8) or 8)
        room["updated_at"] = now_ts()
        assignment = self.get_room_assignment(room_code)
        self.persist()
        return {"room": room, "assignment": assignment}

    def _issue_auth_token(self, account: dict[str, Any]) -> str:
        token = f"uf_auth_{secrets.token_hex(16)}"
        self.auth_tokens[token] = {
            "account_id": account.get("account_id"),
            "login_name": account.get("login_name"),
            "issued_at": now_ts(),
        }
        account["updated_at"] = now_ts()
        return token

    def _account_profile_payload(self, account: dict[str, Any]) -> dict[str, Any]:
        display_name = normalize_account_display_name(
            account.get("display_name"),
            str(account.get("login_name", "")),
            route="auth_profile_payload",
            strict=False,
        )
        return {
            "account_id": account.get("account_id"),
            "player_id": account.get("player_id"),
            "login_name": account.get("login_name"),
            "steam_account_id": account.get("steam_account_id", ""),
            "display_name": display_name,
            "public_display_name": build_unreal_public_display_name(account.get("public_display_name", display_name)),
            "preferred_role": str(account.get("preferred_role", "warble")).strip() or "warble",
            "warble_color": normalize_unreal_warble_color(account.get("warble_color", account.get("faction_key", "blue"))),
            "body_type": normalize_unreal_body_type(account.get("body_type", "female")),
            "spawn_placement": normalize_unreal_spawn_placement(account.get("spawn_placement", "auto")),
            "customization_completed": bool(account.get("customization_completed", False)),
            "audio_device_state": str(account.get("audio_device_state", "")).strip(),
            "profile_level": int(account.get("profile_level", 1)),
            "warble_rank": int(account.get("warble_rank", 1)),
            "biggun_rank": int(account.get("biggun_rank", 1)),
            "xp": int(account.get("xp", 0)),
            "coins": int(account.get("coins", 0)),
            "biggun_eligible": bool(account.get("biggun_eligible", True)),
            "appearance": {
                "faction_key": normalize_live_faction_key(account.get("faction_key", "blue")),
                "body_type": normalize_unreal_body_type(account.get("body_type", "female")),
                "hair_style_key": account.get("hair_style_key", "wavy_crown"),
                "hair_tint_key": account.get("hair_tint_key", "cream"),
                "primary_color_key": account.get("primary_color_key", "ocean_blue"),
                "outfit_key": account.get("outfit_key", "scout_wrap"),
                "accessory_key": account.get("accessory_key", "none"),
                "hat_key": account.get("hat_key", "none"),
                "jewelry_key": account.get("jewelry_key", "none"),
                "gloves_key": account.get("gloves_key", "none"),
                "gear_skin_key": account.get("gear_skin_key", "standard_kit"),
            },
            "updated_at": int(account.get("updated_at", 0)),
        }


world = WorldState() if runtime_enabled() else None

app = FastAPI(title="Underfoot World", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
if neweden_router is not None:
    app.include_router(neweden_router)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    world.run_maintenance_cleanup()
    active_voice_matches = sum(1 for runtime in world.match_runtimes.values() if isinstance(runtime.get("voice"), dict))
    runtime_health = world._summarize_dedicated_runtime_health()
    return {
        "status": "ok",
        "project": PROJECT_NAME,
        "sessions": len(world.sessions),
        "accounts": len(world.accounts),
        "queue_tickets": len(world.queue_tickets),
        "private_rooms": len(world.private_rooms),
        "setup_matches": len(world.setup_matches),
        "dedicated_servers": len(world.dedicated_servers),
        "dedicated_assignments": len(world.dedicated_assignments),
        "dedicated_allocator_url": DEDICATED_ALLOCATOR_URL,
        "dedicated_allocator_configured": bool(DEDICATED_ALLOCATOR_URL),
        "voice_matches": active_voice_matches,
        "voice_gateway_http_url": VOICE_GATEWAY_HTTP_URL,
        "public_admin_fill_enabled": PUBLIC_ADMIN_FILL_ENABLED,
        "maps": len(MAPS),
        "persistence": world.persistence_mode,
        "runtimeBuildFingerprint": runtime_health["runtimeBuildFingerprint"],
        "runtimeBuildTimestampUtc": runtime_health["runtimeBuildTimestampUtc"],
        "runtimeCompatibilityState": runtime_health["runtimeCompatibilityState"],
        "activeInstanceCount": runtime_health["activeInstanceCount"],
        "staleDedicatedAssignmentCount": runtime_health["staleDedicatedAssignmentCount"],
        "stabilizationProfile": build_stabilization_profile_payload(),
        "networkDecisionGate": build_network_decision_gate_payload(runtime_health["runtimeCompatibilityState"]),
    }


@app.get("/api/config")
async def config() -> dict[str, Any]:
    return {
        "project": PROJECT_NAME,
        "mode": "account_shell",
        "world_host": WORLD_HOST,
        "persistence": world.persistence_mode,
        "engine": CONTRACT.get("engine", {"client": "Unity 6", "scripting": "C#"}),
        "stabilizationProfile": build_stabilization_profile_payload(),
        "networkDecisionGate": build_network_decision_gate_payload(),
    }


@app.get("/api/shell")
async def shell() -> dict[str, Any]:
    return {"status": "ok", "shell": world.build_shell_snapshot()}


@app.post("/api/auth/register")
async def register_account(req: AccountRegisterRequest) -> dict[str, Any]:
    return world.register_account(req)


@app.post("/api/auth/login")
async def login_account(req: AccountLoginRequest) -> dict[str, Any]:
    return world.login_account(req)


@app.get("/api/auth/session/{token}")
async def auth_session(token: str) -> dict[str, Any]:
    return world.auth_session(token)


@app.put("/api/auth/profile/appearance")
async def auth_profile_appearance(req: ProfileAppearanceUpdateRequest) -> dict[str, Any]:
    return world.update_profile_appearance(req)


@app.get("/api/maps")
async def list_maps() -> dict[str, Any]:
    return {"status": "ok", "maps": MAPS, "routes": MAP_ROUTES}


@app.get("/api/project-memory")
async def project_memory() -> dict[str, Any]:
    return {"status": "ok", "project_memory": world.project_memory, "source_path": str(DARIUS_PROJECT_MEMORY_PATH)}


@app.put("/api/project-memory")
async def update_project_memory(
    req: ProjectMemoryUpdateRequest,
    authorization: str | None = Header(default=None),
    x_underfoot_memory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_project_memory_write_access(authorization, x_underfoot_memory_token)
    updated = world.update_project_memory(req.project_memory, updated_by=req.updated_by, merge=req.merge)
    return {
        "status": "ok",
        "project_memory": updated,
        "source_path": str(DARIUS_PROJECT_MEMORY_PATH),
        "persistence": world.persistence_mode,
    }


@app.get("/api/frontend")
async def frontend() -> dict[str, Any]:
    return {
        "status": "ok",
        "manifest": FRONTEND_MANIFEST,
        "voice_policy": VOICE_POLICY,
    }


@app.get("/api/matches")
async def list_match_shell() -> dict[str, Any]:
    return {
        "status": "ok",
        "quickplay": world.list_quickplay(),
        "setup_matches": world.list_setup_matches(),
        "browser": world.list_match_browser(),
    }


@app.get("/api/matches/browser")
async def list_match_browser() -> dict[str, Any]:
    return {"status": "ok", "browser": world.list_match_browser()}


@app.get("/api/dedicated/servers")
async def list_dedicated_servers() -> dict[str, Any]:
    return {"status": "ok", "servers": world.list_dedicated_servers()}


@app.post("/api/dedicated/servers/register")
async def register_dedicated_server(req: DedicatedServerRegisterRequest) -> dict[str, Any]:
    return {"status": "ok", "server": world.register_dedicated_server(req)}


@app.post("/api/dedicated/servers/{server_id}/heartbeat")
async def dedicated_server_heartbeat(server_id: str, req: DedicatedServerHeartbeatRequest) -> dict[str, Any]:
    return {"status": "ok", "server": world.heartbeat_dedicated_server(server_id, req)}


@app.post("/api/dedicated/servers/{server_id}/drain")
async def dedicated_server_drain(server_id: str, req: DedicatedServerDrainRequest) -> dict[str, Any]:
    return {"status": "ok", "server": world.drain_dedicated_server(server_id, req)}


@app.post("/api/server/register")
async def register_unreal_server(req: DedicatedServerRegisterRequest) -> dict[str, Any]:
    return {"status": "ok", "server": world.register_dedicated_server(req)}


@app.post("/api/server/heartbeat")
async def unreal_server_heartbeat(req: UnrealServerHeartbeatRequest) -> dict[str, Any]:
    return {"status": "ok", "server": world.heartbeat_dedicated_server(req.server_id, req)}


@app.post("/api/queue/tickets")
async def create_queue_ticket(req: QueueTicketRequest) -> dict[str, Any]:
    return {"status": "ok", **world.queue_quickplay(req)}


@app.post("/api/queue/enqueue")
async def enqueue_unreal_queue(req: UnrealQueueEnqueueRequest) -> dict[str, Any]:
    return {"status": "ok", **world.queue_unreal_client(req)}


@app.get("/api/queue/tickets/{ticket_id}")
async def get_queue_ticket(ticket_id: str) -> dict[str, Any]:
    return {"status": "ok", "ticket": world.get_queue_ticket(ticket_id)}


@app.get("/api/queue/assignment")
async def get_unreal_queue_assignment(ticket_id: str = Query(alias="ticketId")) -> dict[str, Any]:
    return {"status": "ok", **world.get_unreal_queue_assignment(ticket_id)}


@app.post("/api/matches/setup")
async def create_setup_match(req: SetupMatchRequest) -> dict[str, Any]:
    return {"status": "ok", **world.create_setup_match(req)}


@app.get("/api/matches/{match_id}/assignment")
async def get_match_assignment(match_id: str) -> dict[str, Any]:
    return {"status": "ok", "assignment": world.get_match_assignment(match_id)}


@app.post("/api/matches/{match_id}/fill")
async def admin_fill_match(match_id: str, req: AdminFillRequest) -> dict[str, Any]:
    return {"status": "ok", **world.admin_fill_match(match_id, req)}


@app.get("/api/matches/{match_id}/runtime")
async def get_match_runtime(match_id: str) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.build_match_runtime_snapshot(match_id)}


@app.get("/api/matches/{match_id}/team-lobby")
async def get_match_team_lobby(match_id: str, accountId: str = "", playerId: str = "") -> dict[str, Any]:
    return {
        "status": "ok",
        "team_lobby": world.build_match_team_lobby_snapshot(match_id, accountId, playerId),
        "runtime": world.build_match_runtime_snapshot(match_id),
    }


@app.post("/api/matches/{match_id}/join")
async def join_match_runtime(match_id: str, req: MatchJoinRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.join_match(match_id, req)}


@app.post("/api/matches/{match_id}/ready")
async def ready_match_runtime(match_id: str, req: MatchReadyRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.set_match_ready(match_id, req)}


@app.post("/api/matches/{match_id}/teams/{team_id}/ready")
async def ready_match_team_runtime(match_id: str, team_id: int, req: TeamReadyRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.set_team_ready(match_id, team_id, req)}


@app.post("/api/matches/{match_id}/phase")
async def set_match_phase(match_id: str, req: MatchPhaseRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.set_match_phase(match_id, req)}


@app.post("/api/matches/{match_id}/shrooms/personal")
async def raise_personal_shroom(match_id: str, req: PersonalShroomRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.raise_personal_shroom(match_id, req)}


@app.post("/api/matches/{match_id}/shrooms/team")
async def raise_team_shroom(match_id: str, req: TeamShroomRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.raise_team_shroom(match_id, req)}


@app.post("/api/matches/{match_id}/shrooms/destroy")
async def destroy_match_shroom(match_id: str, req: DestroyShroomRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.destroy_shroom(match_id, req)}


@app.post("/api/matches/{match_id}/holes/discover")
async def discover_match_hole(match_id: str, req: HoleDiscoveryRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.discover_hole(match_id, req)}


@app.post("/api/matches/{match_id}/holes/travel")
async def travel_match_hole(match_id: str, req: HoleTravelRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.travel_hole(match_id, req)}


@app.post("/api/matches/{match_id}/missions/action")
async def apply_match_mission_action(match_id: str, req: MissionActionRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.apply_mission_action(match_id, req)}


@app.post("/api/matches/{match_id}/warbles/capture")
async def capture_match_warble(match_id: str, req: CaptureActionRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.capture_warble(match_id, req)}


@app.post("/api/matches/{match_id}/warbles/rescue")
async def rescue_match_warble(match_id: str, req: CaptureActionRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.rescue_warble(match_id, req)}


@app.post("/api/matches/{match_id}/warbles/consume")
async def consume_match_warble(match_id: str, req: CaptureActionRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.consume_warble(match_id, req)}


@app.post("/api/matches/{match_id}/trauma/apply")
async def apply_match_trauma(match_id: str, req: TraumaApplyRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.apply_trauma(match_id, req)}


@app.post("/api/matches/{match_id}/skills/use")
async def use_match_skill(match_id: str, req: SkillUseRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.use_skill(match_id, req)}


@app.post("/api/matches/{match_id}/placements")
async def place_match_biggun_tool(match_id: str, req: BiggunPlacementRequest) -> dict[str, Any]:
    return {"status": "ok", "runtime": world.place_biggun_tool(match_id, req)}


@app.get("/api/matches/{match_id}/voice/config")
async def match_voice_config(match_id: str) -> dict[str, Any]:
    return {"status": "ok", "voiceConfig": world.build_match_voice_config(match_id)}


@app.post("/api/matches/{match_id}/voice/join-token")
async def match_voice_join_token(match_id: str, req: VoiceJoinTokenRequest) -> dict[str, Any]:
    return {"status": "ok", "voiceJoin": world.create_match_voice_join_token(match_id, req)}


@app.get("/api/matches/{match_id}/voice/state")
async def match_voice_state(match_id: str) -> dict[str, Any]:
    return {"status": "ok", "voice": world.build_match_voice_state(match_id)}


@app.post("/api/matches/{match_id}/voice/noise-event")
async def match_voice_noise_event(match_id: str, req: VoiceNoiseEventRequest) -> dict[str, Any]:
    return {"status": "ok", "voice": world.apply_voice_noise_event(match_id, req)}


@app.post("/api/matches/{match_id}/voice/mic-state")
async def match_voice_mic_state(match_id: str, req: VoiceMicStateRequest) -> dict[str, Any]:
    result = world.update_match_mic_state(match_id, req)
    return {"status": "ok", "penalty": result["penalty"], "voice": result["voice"]}


@app.post("/api/matches/{match_id}/voice/contour-state")
async def match_voice_contour_state(match_id: str, req: VoiceContourStateRequest) -> dict[str, Any]:
    return {"status": "ok", "voice": world.update_match_contour_state(match_id, req)}


@app.post("/api/matches/{match_id}/voice/gateway-stats")
async def match_voice_gateway_stats(match_id: str, req: VoiceGatewayStatsRequest) -> dict[str, Any]:
    return {"status": "ok", "voice": world.update_match_voice_gateway_stats(match_id, req)}


@app.post("/api/player-feedback")
async def submit_player_feedback(req: PlayerFeedbackRequest) -> dict[str, Any]:
    return world.submit_player_feedback(req)


@app.post("/api/crash-reports")
async def submit_crash_report(req: CrashReportRequest) -> dict[str, Any]:
    return world.submit_crash_report(req)


@app.post("/api/runtime-diagnostics")
async def submit_runtime_diagnostic(req: RuntimeDiagnosticReportRequest) -> dict[str, Any]:
    return world.submit_runtime_diagnostic(req)


@app.post("/api/diagnostics/runtime")
async def submit_runtime_diagnostic_alias(req: RuntimeDiagnosticReportRequest) -> dict[str, Any]:
    return world.submit_runtime_diagnostic(req)


@app.post("/api/platform/steam/resolve")
async def resolve_steam_platform(req: SteamPlatformResolveRequest) -> dict[str, Any]:
    return world.resolve_steam_platform_profile(req)


@app.post("/api/match/result")
async def submit_unreal_match_result(req: UnrealMatchResultRequest) -> dict[str, Any]:
    return world.submit_unreal_match_result(req)


@app.post("/api/voice/presence")
async def submit_unreal_voice_presence(req: UnrealVoicePresenceRequest) -> dict[str, Any]:
    return world.update_unreal_voice_presence(req)


@app.get("/api/reports/recent")
async def recent_reports(limit: int = Query(default=20)) -> dict[str, Any]:
    return world.list_recent_reports(limit)


@app.get("/api/session/active-assignment")
async def session_active_assignment(auth_token: str = Query(alias="authToken")) -> dict[str, Any]:
    return world.get_active_assignment_resume_state(auth_token)


@app.get("/api/queue/listings")
async def queue_listings() -> dict[str, Any]:
    return {
        "status": "ok",
        "quickplay": world.list_quickplay(),
        "setup_matches": world.list_setup_matches(),
        "private_rooms": world.list_private_rooms(),
        "browser": world.list_match_browser(),
    }


@app.post("/api/session/reconnect")
async def session_reconnect(req: SessionReconnectRequest) -> dict[str, Any]:
    return world.reconnect_active_assignment(req)


@app.post("/api/session/leave-active-match")
async def session_leave_active_match(req: LeaveActiveMatchRequest) -> dict[str, Any]:
    return world.leave_active_match(req)


@app.post("/api/matches/{match_id}/presence/heartbeat")
async def match_presence_heartbeat(match_id: str, req: PresenceHeartbeatRequest) -> dict[str, Any]:
    return world.heartbeat_match_presence(match_id, req)


@app.get("/api/rooms")
async def list_private_rooms() -> dict[str, Any]:
    return {"status": "ok", "rooms": world.list_private_rooms()}


@app.post("/api/rooms")
async def create_private_room(req: PrivateRoomCreateRequest) -> dict[str, Any]:
    return {"status": "ok", **world.create_private_room(req)}


@app.post("/api/rooms/join")
async def join_private_room(req: PrivateRoomJoinRequest) -> dict[str, Any]:
    return {"status": "ok", **world.join_private_room(req)}


@app.get("/api/rooms/{room_code}/assignment")
async def get_room_assignment(room_code: str) -> dict[str, Any]:
    return {"status": "ok", "assignment": world.get_room_assignment(room_code)}


@app.get("/api/profile/{player_name}")
async def get_profile(player_name: str) -> dict[str, Any]:
    return world.profile_snapshot(player_name)


@app.post("/api/session/guest")
async def create_guest_session(req: GuestSessionRequest) -> dict[str, Any]:
    return world.create_guest(req)


@app.get("/api/session/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    return world.snapshot(session_id)


@app.post("/api/session/{session_id}/action")
async def session_action(session_id: str, req: ActionRequest) -> dict[str, Any]:
    return world.apply_action(session_id, req.action, req.value)


@app.websocket("/ws/world/{session_id}")
async def world_socket(ws: WebSocket, session_id: str) -> None:
    await ws.accept()
    try:
        snapshot = world.snapshot(session_id)
    except HTTPException:
        await ws.send_json({"status": "error", "detail": "Unknown session"})
        await ws.close()
        return

    map_id = str(snapshot.get("session", {}).get("map_id", ""))
    world.connections[map_id].add(ws)
    await ws.send_json({"kind": "snapshot", **snapshot})
    try:
        while True:
            payload = await ws.receive_json()
            action = str(payload.get("action", "")).strip()
            if action:
                result = world.apply_action(session_id, action, payload.get("value"))
                update = {"kind": "update", **result}
            else:
                update = {"kind": "heartbeat", **world.snapshot(session_id)}

            stale: list[WebSocket] = []
            for conn in world.connections.get(map_id, set()):
                try:
                    await conn.send_json(update)
                except Exception:
                    stale.append(conn)
            for conn in stale:
                world.connections[map_id].discard(conn)
    except WebSocketDisconnect:
        world.connections[map_id].discard(ws)


if runtime_enabled() and WEB_ROOT.exists():
    app.mount("/assets", StaticFiles(directory=str(WEB_ROOT / "assets")), name="underfoot-assets")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/{path:path}")
async def site(path: str) -> FileResponse:
    clean = (path or "").strip().lstrip("/")
    target = (WEB_ROOT / clean).resolve() if clean else (WEB_ROOT / "index.html").resolve()
    if WEB_ROOT.exists() and str(target).startswith(str(WEB_ROOT.resolve())) and target.exists() and target.is_file():
        return FileResponse(target)
    return FileResponse(WEB_ROOT / "index.html")


install_runtime_guard(app, 'underfoot-world', require_auth=False)
