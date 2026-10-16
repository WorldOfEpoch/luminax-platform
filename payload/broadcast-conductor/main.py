from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Luminax Broadcast Conductor")

WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspace/luminax"))
BROADCAST_ROOT = Path(os.getenv("BROADCAST_ROOT", "/data/memory/broadcast"))
OVERLAY_OUTPUT_ROOT = Path(os.getenv("BROADCAST_OVERLAY_ROOT", "/workspace/luminax/generated_media/broadcast/overlays"))
VISION_AGENT_URL = os.getenv("VISION_AGENT_URL", "http://vision-agent:8020").rstrip("/")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
COMMENTARY_MODEL = os.getenv("BROADCAST_COMMENTARY_MODEL", os.getenv("OLLAMA_CHAT_MODEL_FAST", "qwen2.5:14b")).strip() or "qwen2.5:14b"
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg").strip() or "ffmpeg"

if runtime_enabled():
    for path in [
        BROADCAST_ROOT / "accounts",
        BROADCAST_ROOT / "overlays",
        BROADCAST_ROOT / "sessions",
        BROADCAST_ROOT / "commentary",
        BROADCAST_ROOT / "logs",
        OVERLAY_OUTPUT_ROOT,
    ]:
        path.mkdir(parents=True, exist_ok=True)


ACTIVE_SESSION_PROCS: dict[str, subprocess.Popen] = {}


class BroadcastAccountUpsertRequest(BaseModel):
    account_key: str
    provider: str
    display_name: str = ""
    channel_handle: str = ""
    managed_account_key: str = ""
    project_key: str = ""
    category_name: str = ""
    ingest_server: str = ""
    status: str = "planned"
    overlay_profile: str = "creator_neon"
    notes: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)


class OverlayPackCreateRequest(BaseModel):
    project_key: str = ""
    title: str = ""
    subtitle: str = ""
    theme: str = "creator_neon"
    accent_primary: str = ""
    accent_secondary: str = ""
    sponsor_text: str = ""
    lower_third_label: str = "LIVE"
    commentary_label: str = "Luminax Broadcast AI"
    notes: str = ""


class BroadcastSessionCreateRequest(BaseModel):
    project_key: str = ""
    account_key: str = ""
    provider: str = ""
    title: str = ""
    description: str = ""
    source_video: str = ""
    overlay_pack_id: str = ""
    output_mode: str = "obs_browser_source"
    commentary_mode: str = "creator_react"
    commentary_persona: str = ""
    rtmp_url: str = ""
    stream_key_ref: str = ""
    loop_video: bool = True
    scheduled_for: str = ""
    notes: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)


class BroadcastSessionLaunchRequest(BaseModel):
    rtmp_url: str = ""
    stream_key: str = ""
    loop_video: bool | None = None
    additional_ffmpeg_args: list[str] = Field(default_factory=list)


class CommentaryPlanCreateRequest(BaseModel):
    project_key: str = ""
    source_video: str
    persona: str = ""
    style: str = "playful influencer"
    sample_every_sec: int = 18
    max_samples: int = 8
    notes: str = ""


def _now() -> int:
    return int(time.time())


def _slug(value: str, default: str = "item", max_len: int = 96) -> str:
    raw = (value or "").strip().lower()
    clean = "".join(ch for ch in raw if ch.isalnum() or ch in "-_").strip("-_")
    return clean[:max_len] or default


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists() and path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _write_json(path: Path, payload: dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _record_path(kind: str, key: str) -> Path:
    return BROADCAST_ROOT / kind / f"{_slug(key, default=kind)}.json"


def _list_records(kind: str, limit: int = 120) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = BROADCAST_ROOT / kind
    for path in sorted(root.glob("*.json"), reverse=True)[: max(1, min(limit, 500))]:
        data = _read_json(path)
        if data:
            rows.append(data)
    return rows


def _provider_catalog() -> dict[str, Any]:
    return {
        "twitch": {
            "label": "Twitch",
            "mode": "oauth_plus_rtmp",
            "ingest_server": "rtmp://live.twitch.tv/app",
            "scope_hints": [
                "channel:manage:broadcast",
                "channel:read:stream_key",
                "user:read:chat",
                "user:write:chat",
            ],
            "notes": "Use Helix/EventSub for chat, title/category changes, and channel state. RTMP is used for the actual video feed.",
        },
        "kick": {
            "label": "Kick",
            "mode": "oauth_plus_manual_ingest",
            "ingest_server": "",
            "scope_hints": [
                "creator oauth token",
                "chat send permissions",
                "ingest URL from dashboard",
                "stream key from dashboard",
            ],
            "notes": "Kick account control is supported through a provider adapter, but ingest URL and stream key should be supplied from the current creator dashboard or app configuration.",
        },
    }


def _theme_palette(theme: str, accent_primary: str = "", accent_secondary: str = "") -> dict[str, str]:
    base = {
        "creator_neon": {"primary": "#66f7ff", "secondary": "#7c5cff", "panel": "rgba(8,14,28,.78)", "text": "#ebfbff", "muted": "#9ac8d6"},
        "sunset_arcade": {"primary": "#ff9f43", "secondary": "#ff4d6d", "panel": "rgba(29,10,18,.78)", "text": "#fff2ea", "muted": "#f3c2b2"},
        "forest_myth": {"primary": "#87ff8e", "secondary": "#4ac0ff", "panel": "rgba(8,24,18,.78)", "text": "#f1fff1", "muted": "#bddfc2"},
    }.get((theme or "creator_neon").strip().lower())
    if not base:
        base = {"primary": "#66f7ff", "secondary": "#7c5cff", "panel": "rgba(8,14,28,.78)", "text": "#ebfbff", "muted": "#9ac8d6"}
    if accent_primary.strip():
        base["primary"] = accent_primary.strip()
    if accent_secondary.strip():
        base["secondary"] = accent_secondary.strip()
    return base


def _target_overlay_root(project_key: str, pack_id: str) -> Path:
    key = _slug(project_key, default="")
    if key:
        return WORKSPACE_ROOT / "projects" / key / "broadcast" / "overlays" / pack_id
    return OVERLAY_OUTPUT_ROOT / pack_id


def _overlay_pack_url(pack_id: str, filename: str = "index.html") -> str:
    return f"/api/broadcast/overlays/{pack_id}/files/{filename}"


def _render_overlay_files(pack_id: str, req: OverlayPackCreateRequest) -> dict[str, Any]:
    palette = _theme_palette(req.theme, req.accent_primary, req.accent_secondary)
    output_root = _target_overlay_root(req.project_key, pack_id)
    output_root.mkdir(parents=True, exist_ok=True)
    title = (req.title or "Luminax Live").strip() or "Luminax Live"
    subtitle = (req.subtitle or "Created overlays ready for OBS browser source").strip()
    sponsor = (req.sponsor_text or "Powered by Luminax").strip()
    lower = (req.lower_third_label or "LIVE").strip()
    commentator = (req.commentary_label or "Luminax Broadcast AI").strip()
    css = f""":root {{
  --primary: {palette['primary']};
  --secondary: {palette['secondary']};
  --panel: {palette['panel']};
  --text: {palette['text']};
  --muted: {palette['muted']};
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; width: 100%; height: 100%; background: transparent; overflow: hidden; font-family: Inter, Segoe UI, Arial, sans-serif; color: var(--text); }}
body {{
  background:
    radial-gradient(circle at 15% 20%, color-mix(in srgb, var(--primary) 26%, transparent), transparent 26%),
    radial-gradient(circle at 85% 25%, color-mix(in srgb, var(--secondary) 24%, transparent), transparent 24%),
    radial-gradient(circle at 50% 100%, rgba(255,255,255,.05), transparent 40%);
}}
.frame {{ position: absolute; inset: 0; border: 6px solid color-mix(in srgb, var(--primary) 42%, transparent); box-shadow: inset 0 0 0 1px rgba(255,255,255,.08), 0 0 30px color-mix(in srgb, var(--primary) 26%, transparent); border-radius: 28px; pointer-events: none; }}
.topbar {{ position: absolute; top: 24px; left: 28px; right: 28px; display: flex; justify-content: space-between; gap: 16px; align-items: center; }}
.chip {{ display: inline-flex; align-items: center; gap: 10px; padding: 12px 18px; border-radius: 999px; border: 1px solid color-mix(in srgb, var(--primary) 30%, transparent); background: linear-gradient(135deg, color-mix(in srgb, var(--panel) 84%, transparent), rgba(8,14,24,.44)); box-shadow: 0 0 26px color-mix(in srgb, var(--primary) 18%, transparent); backdrop-filter: blur(14px); }}
.live-dot {{ width: 12px; height: 12px; border-radius: 50%; background: #ff4d6d; box-shadow: 0 0 14px rgba(255,77,109,.7); }}
.headline {{ font-size: 28px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }}
.subline {{ color: var(--muted); font-size: 13px; letter-spacing: .14em; text-transform: uppercase; }}
.right-stack {{ display: flex; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }}
.lower-third {{ position: absolute; left: 34px; bottom: 36px; min-width: min(720px, calc(100% - 68px)); max-width: calc(100% - 68px); padding: 18px 22px; border-radius: 24px; border: 1px solid color-mix(in srgb, var(--secondary) 30%, transparent); background: linear-gradient(135deg, rgba(8,14,24,.9), color-mix(in srgb, var(--secondary) 18%, rgba(8,14,24,.86))); box-shadow: 0 0 30px color-mix(in srgb, var(--secondary) 22%, transparent); backdrop-filter: blur(14px); }}
.lower-kicker {{ display: inline-flex; align-items: center; padding: 8px 12px; margin-bottom: 10px; border-radius: 999px; background: linear-gradient(135deg, color-mix(in srgb, var(--primary) 68%, transparent), color-mix(in srgb, var(--secondary) 64%, transparent)); color: #031017; font-size: 12px; font-weight: 800; letter-spacing: .18em; text-transform: uppercase; }}
.commentary-box {{ position: absolute; right: 34px; bottom: 36px; width: min(360px, calc(100% - 68px)); padding: 16px 18px; border-radius: 22px; border: 1px solid color-mix(in srgb, var(--primary) 24%, transparent); background: linear-gradient(180deg, rgba(7,13,24,.82), rgba(7,13,24,.58)); backdrop-filter: blur(12px); }}
.commentary-title {{ font-size: 12px; letter-spacing: .18em; text-transform: uppercase; color: var(--muted); margin-bottom: 8px; }}
.ticker {{ position: absolute; left: 34px; right: 34px; bottom: 170px; padding: 10px 14px; border-radius: 16px; border: 1px solid rgba(255,255,255,.08); background: rgba(8,14,24,.54); color: var(--muted); font-size: 14px; backdrop-filter: blur(10px); }}
.glow {{ position: absolute; inset: auto auto 20% 70%; width: 280px; height: 280px; border-radius: 50%; background: radial-gradient(circle, color-mix(in srgb, var(--secondary) 26%, transparent), transparent 70%); filter: blur(16px); opacity: .9; pointer-events: none; }}
"""
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{title}</title><link rel="stylesheet" href="styles.css"></head><body><div class="frame"></div><div class="glow"></div><div class="topbar"><div><div class="chip"><span class="live-dot"></span><span class="headline" id="streamTitle">{title}</span></div><div class="subline" id="streamSubtitle">{subtitle}</div></div><div class="right-stack"><div class="chip" id="statusChip">Status: LIVE</div><div class="chip" id="sponsorChip">{sponsor}</div></div></div><div class="ticker" id="tickerLine">Drop this as an OBS browser source. Update query params like ?title=Underfoot&subtitle=Chaos beneath the roots&status=Going live soon.</div><div class="lower-third"><div class="lower-kicker" id="lowerTag">{lower}</div><div class="headline" style="font-size:22px" id="lowerHeadline">{title}</div><div class="subline" id="lowerSub">{subtitle}</div></div><div class="commentary-box"><div class="commentary-title">{commentator}</div><div id="commentaryText">Chat, keep your eyes on the run. This overlay pack is ready for countdowns, lower-thirds, and live reactions.</div></div><script>const params = new URLSearchParams(location.search); const apply = (id, key) => {{ const el = document.getElementById(id); if (el && params.get(key)) el.textContent = params.get(key); }}; [['streamTitle','title'],['streamSubtitle','subtitle'],['statusChip','status'],['sponsorChip','sponsor'],['lowerTag','tag'],['lowerHeadline','headline'],['lowerSub','lower'],['tickerLine','ticker'],['commentaryText','commentary']].forEach(([id,key]) => apply(id,key));</script></body></html>
"""
    scorebug = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><link rel="stylesheet" href="styles.css"></head><body style="display:flex;align-items:flex-start;justify-content:flex-start;padding:18px;background:transparent"><div class="chip">{title} | {lower}</div></body></html>"""
    manifest = {
        "pack_id": pack_id,
        "theme": req.theme,
        "title": title,
        "subtitle": subtitle,
        "files": ["index.html", "scorebug.html", "styles.css"],
        "browser_source_url": _overlay_pack_url(pack_id, "index.html"),
        "scorebug_url": _overlay_pack_url(pack_id, "scorebug.html"),
        "notes": [
            "Use the browser source URL in OBS for animated overlays.",
            "Pass dynamic text with query params like ?title=Underfoot&status=LIVE NOW.",
            "For prerecorded video restreams, pair this overlay with an OBS media source or a local camera/video capture source.",
        ],
    }
    (output_root / "styles.css").write_text(css, encoding="utf-8")
    (output_root / "index.html").write_text(html, encoding="utf-8")
    (output_root / "scorebug.html").write_text(scorebug, encoding="utf-8")
    (output_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    record = {
        "pack_id": pack_id,
        "project_key": _slug(req.project_key, default=""),
        "title": title,
        "subtitle": subtitle,
        "theme": req.theme,
        "palette": palette,
        "output_root": str(output_root),
        "browser_source_url": _overlay_pack_url(pack_id, "index.html"),
        "scorebug_url": _overlay_pack_url(pack_id, "scorebug.html"),
        "created_at": _now(),
        "notes": req.notes,
    }
    _write_json(_record_path("overlays", pack_id), record)
    return record


def _source_is_url(value: str) -> bool:
    lower = (value or "").strip().lower()
    return lower.startswith("http://") or lower.startswith("https://")


def _resolve_video_source(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="source_video is required")
    if _source_is_url(value):
        return value
    source = Path(value)
    if not source.is_absolute():
        source = (WORKSPACE_ROOT / value).resolve()
    else:
        source = source.resolve()
    allowed_roots = [WORKSPACE_ROOT.resolve()]
    data_root = Path("/data")
    if data_root.exists():
        allowed_roots.append(data_root.resolve())
    if not any(str(source).startswith(str(root)) for root in allowed_roots):
        raise HTTPException(status_code=400, detail="source_video must stay inside the workspace or /data")
    if not source.exists():
        raise HTTPException(status_code=404, detail="source_video not found")
    return str(source)


def _session_target(provider: str, rtmp_url: str, stream_key: str, account: dict[str, Any] | None = None) -> str:
    provider_key = _slug(provider, default="twitch")
    base = (rtmp_url or "").strip()
    if not base and provider_key == "twitch":
        base = "rtmp://live.twitch.tv/app"
    elif not base and account:
        base = str(account.get("ingest_server", "")).strip()
    key = (stream_key or "").strip()
    if not base:
        raise HTTPException(status_code=400, detail="An ingest/rtmp URL is required for this provider")
    if not key:
        raise HTTPException(status_code=400, detail="A stream key is required to launch a live session")
    return base.rstrip("/") + "/" + key.lstrip("/")


def _build_ffmpeg_command(source_video: str, target: str, loop_video: bool = True, additional_args: list[str] | None = None) -> list[str]:
    command = [FFMPEG_BIN, "-re"]
    if loop_video:
        command += ["-stream_loop", "-1"]
    command += [
        "-i",
        source_video,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "60",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "48000",
    ]
    for arg in additional_args or []:
        clean = str(arg or "").strip()
        if clean:
            command.append(clean)
    command += ["-f", "flv", target]
    return command


def _format_timecode(seconds: int) -> str:
    mins = max(0, int(seconds)) // 60
    secs = max(0, int(seconds)) % 60
    return f"{mins:02d}:{secs:02d}"


async def _vision_describe(image_path: Path) -> str:
    with image_path.open("rb") as fh:
        files = {"file": (image_path.name, fh, "image/jpeg")}
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{VISION_AGENT_URL}/analyze", files=files)
    response.raise_for_status()
    data = response.json()
    return str(data.get("analysis", "")).strip() or "No scene analysis returned."


def _fallback_commentary_segments(frame_descriptions: list[dict[str, Any]], persona: str, style: str) -> list[dict[str, Any]]:
    openers = [
        "Okay, look at this.",
        "This is exactly the kind of chaos we wanted.",
        "Right here is where the run gets interesting.",
        "Chat, this is the moment to pay attention.",
    ]
    boosters = [
        "That is a near miss and the crowd would be losing it.",
        "You can feel the pressure building in this scene.",
        "This is the kind of danger that makes the map memorable.",
        "That setup is begging for a clutch escape.",
    ]
    persona_line = (persona or "Bright, playful creator host").strip()
    style_line = (style or "playful influencer").strip()
    segments: list[dict[str, Any]] = []
    for idx, row in enumerate(frame_descriptions):
        desc = str(row.get("description", "")).strip()
        segments.append(
            {
                "timecode": row.get("timecode", _format_timecode(idx * 10)),
                "observation": desc,
                "line": f"{openers[idx % len(openers)]} {desc[:180]} {boosters[idx % len(boosters)]}",
                "persona": persona_line,
                "style": style_line,
            }
        )
    return segments


async def _model_commentary_segments(frame_descriptions: list[dict[str, Any]], persona: str, style: str) -> list[dict[str, Any]]:
    prompt = {
        "task": "Create lively live-stream commentary lines for a prerecorded video.",
        "persona": persona or "Bright, playful creator host who narrates danger, celebrates escapes, and sounds like a confident influencer without being mean.",
        "style": style or "playful influencer",
        "requirements": [
            "Return only valid JSON.",
            "Produce one commentary line per frame description.",
            "Each item must include timecode, observation, and line.",
            "Keep each line under 180 characters.",
            "Sound energetic, specific, and stream-ready.",
        ],
        "frames": frame_descriptions,
    }
    payload = {
        "model": COMMENTARY_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": "You are Luminax's creator commentary writer. Return strict JSON only."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
    }
    async with httpx.AsyncClient(timeout=240) as client:
        response = await client.post(f"{OLLAMA_HOST}/api/chat", json=payload)
    response.raise_for_status()
    data = response.json()
    content = str(((data.get("message") or {}).get("content")) or "").strip()
    if not content:
        raise ValueError("Empty commentary model output")
    parsed = json.loads(content)
    if isinstance(parsed, dict) and isinstance(parsed.get("segments"), list):
        parsed = parsed["segments"]
    if not isinstance(parsed, list):
        raise ValueError("Commentary output is not a list")
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(parsed):
        if not isinstance(item, dict):
            continue
        fallback_frame = frame_descriptions[min(idx, len(frame_descriptions) - 1)]
        rows.append(
            {
                "timecode": str(item.get("timecode") or fallback_frame.get("timecode") or ""),
                "observation": str(item.get("observation") or fallback_frame.get("description") or ""),
                "line": str(item.get("line") or "").strip(),
                "persona": persona or "",
                "style": style or "",
            }
        )
    if not rows:
        raise ValueError("No usable commentary lines returned")
    return rows


def _sample_video_frames(source_video: str, sample_every_sec: int, max_samples: int) -> list[dict[str, Any]]:
    require_feature("LUMINAX_HOST_EXECUTION_ENABLED")
    ffmpeg = shutil.which(FFMPEG_BIN)
    if not ffmpeg:
        raise HTTPException(status_code=503, detail="ffmpeg is not installed in broadcast-conductor")
    temp_dir = Path(tempfile.mkdtemp(prefix="luminax_broadcast_frames_"))
    pattern = temp_dir / "frame_%03d.jpg"
    command = [
        ffmpeg,
        "-y",
        "-i",
        source_video,
        "-vf",
        f"fps=1/{max(2, sample_every_sec)}",
        "-frames:v",
        str(max(1, max_samples)),
        str(pattern),
    ]
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0:
        raise HTTPException(status_code=400, detail=f"ffmpeg frame sampling failed: {(proc.stderr or proc.stdout)[-800:]}")
    frames = sorted(temp_dir.glob("frame_*.jpg"))
    return [{"frame_path": str(path), "timecode": _format_timecode(idx * max(2, sample_every_sec))} for idx, path in enumerate(frames)]


@app.get("/health")
async def health():
    ffmpeg = shutil.which(FFMPEG_BIN)
    return {
        "status": "ok",
        "ffmpeg_bin": ffmpeg or "",
        "commentary_model": COMMENTARY_MODEL,
        "counts": {
            "accounts": len(list((BROADCAST_ROOT / "accounts").glob("*.json"))),
            "overlays": len(list((BROADCAST_ROOT / "overlays").glob("*.json"))),
            "sessions": len(list((BROADCAST_ROOT / "sessions").glob("*.json"))),
            "commentary_jobs": len(list((BROADCAST_ROOT / "commentary").glob("*.json"))),
        },
    }


@app.get("/providers")
async def providers():
    catalog = _provider_catalog()
    return {"status": "ok", "providers": catalog, "count": len(catalog)}


@app.get("/overview")
async def overview():
    providers = _provider_catalog()
    accounts = _list_records("accounts", limit=240)
    overlays = _list_records("overlays", limit=120)
    sessions = _list_records("sessions", limit=120)
    commentary = _list_records("commentary", limit=120)
    active = []
    for session_id, proc in list(ACTIVE_SESSION_PROCS.items()):
        running = proc.poll() is None
        active.append({"session_id": session_id, "running": running, "pid": proc.pid if running else None})
        if not running:
            ACTIVE_SESSION_PROCS.pop(session_id, None)
    return {
        "status": "ok",
        "providers": providers,
        "summary": {
            "provider_count": len(providers),
            "account_count": len(accounts),
            "overlay_count": len(overlays),
            "session_count": len(sessions),
            "active_session_count": len([row for row in active if row["running"]]),
            "commentary_count": len(commentary),
        },
        "active_sessions": active,
        "recent_accounts": accounts[:10],
        "recent_overlays": overlays[:10],
        "recent_sessions": sessions[:10],
        "recent_commentary": commentary[:10],
    }


@app.get("/accounts")
async def accounts(provider: str = Query(default=""), project_key: str = Query(default=""), limit: int = Query(default=120)):
    rows = _list_records("accounts", limit=limit)
    p = _slug(provider, default="")
    project = _slug(project_key, default="")
    if p:
        rows = [row for row in rows if _slug(str(row.get("provider", "")), default="") == p]
    if project:
        rows = [row for row in rows if _slug(str(row.get("project_key", "")), default="") == project]
    return {"status": "ok", "rows": rows, "count": len(rows)}


@app.post("/accounts/upsert")
async def accounts_upsert(req: BroadcastAccountUpsertRequest):
    account_key = _slug(req.account_key, default="")
    if not account_key:
        raise HTTPException(status_code=400, detail="account_key is required")
    provider = _slug(req.provider, default="")
    if provider not in _provider_catalog():
        raise HTTPException(status_code=400, detail="provider must be twitch or kick")
    record = {
        "account_key": account_key,
        "provider": provider,
        "display_name": (req.display_name or account_key).strip() or account_key,
        "channel_handle": (req.channel_handle or "").strip(),
        "managed_account_key": _slug(req.managed_account_key, default=""),
        "project_key": _slug(req.project_key, default=""),
        "category_name": (req.category_name or "").strip(),
        "ingest_server": (req.ingest_server or "").strip(),
        "status": _slug(req.status, default="planned", max_len=32),
        "overlay_profile": _slug(req.overlay_profile, default="creator_neon", max_len=48),
        "notes": (req.notes or "").strip(),
        "settings": req.settings or {},
        "updated_at": _now(),
        "created_at": _read_json(_record_path("accounts", account_key)).get("created_at", _now()),
        "provider_doc": _provider_catalog()[provider],
    }
    _write_json(_record_path("accounts", account_key), record)
    return {"status": "ok", "account": record}


@app.get("/overlays")
async def overlays(project_key: str = Query(default=""), limit: int = Query(default=120)):
    rows = _list_records("overlays", limit=limit)
    key = _slug(project_key, default="")
    if key:
        rows = [row for row in rows if _slug(str(row.get("project_key", "")), default="") == key]
    return {"status": "ok", "rows": rows, "count": len(rows)}


@app.post("/overlays/create")
async def overlays_create(req: OverlayPackCreateRequest):
    pack_id = f"overlay_{_now()}_{uuid.uuid4().hex[:8]}"
    record = _render_overlay_files(pack_id, req)
    return {"status": "ok", "overlay": record}


@app.get("/overlays/{pack_id}")
async def overlay_detail(pack_id: str):
    record = _read_json(_record_path("overlays", pack_id))
    if not record:
        raise HTTPException(status_code=404, detail="Overlay pack not found")
    return {"status": "ok", "overlay": record}


@app.get("/overlays/{pack_id}/files/{file_path:path}")
async def overlay_file(pack_id: str, file_path: str):
    record = _read_json(_record_path("overlays", pack_id))
    if not record:
        raise HTTPException(status_code=404, detail="Overlay pack not found")
    root = Path(str(record.get("output_root", "")).strip())
    target = (root / file_path).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise HTTPException(status_code=400, detail="Invalid overlay file path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Overlay file not found")
    return FileResponse(target, filename=target.name)


@app.get("/sessions")
async def sessions(project_key: str = Query(default=""), limit: int = Query(default=120)):
    rows = _list_records("sessions", limit=limit)
    key = _slug(project_key, default="")
    if key:
        rows = [row for row in rows if _slug(str(row.get("project_key", "")), default="") == key]
    return {"status": "ok", "rows": rows, "count": len(rows)}


@app.post("/sessions/create")
async def sessions_create(req: BroadcastSessionCreateRequest):
    provider = _slug(req.provider, default="")
    account_key = _slug(req.account_key, default="")
    account = _read_json(_record_path("accounts", account_key)) if account_key else {}
    if not provider:
        provider = _slug(str(account.get("provider", "")), default="")
    if provider not in _provider_catalog():
        raise HTTPException(status_code=400, detail="provider must be twitch or kick")
    overlay_pack_id = _slug(req.overlay_pack_id, default="")
    overlay = _read_json(_record_path("overlays", overlay_pack_id)) if overlay_pack_id else {}
    source_video = _resolve_video_source(req.source_video)
    session_id = f"stream_{_now()}_{uuid.uuid4().hex[:8]}"
    session = {
        "session_id": session_id,
        "project_key": _slug(req.project_key, default=""),
        "account_key": account_key,
        "provider": provider,
        "title": (req.title or "Luminax Stream Session").strip(),
        "description": (req.description or "").strip(),
        "source_video": source_video,
        "overlay_pack_id": overlay_pack_id,
        "overlay_browser_source_url": overlay.get("browser_source_url", "") if overlay else "",
        "overlay_scorebug_url": overlay.get("scorebug_url", "") if overlay else "",
        "output_mode": _slug(req.output_mode, default="obs_browser_source", max_len=40),
        "commentary_mode": _slug(req.commentary_mode, default="creator_react", max_len=40),
        "commentary_persona": (req.commentary_persona or "Playful, fast-moving creator host who reacts with hype and clarity.").strip(),
        "rtmp_url": (req.rtmp_url or "").strip() or str(account.get("ingest_server", "")).strip(),
        "stream_key_ref": (req.stream_key_ref or "").strip(),
        "loop_video": bool(req.loop_video),
        "scheduled_for": (req.scheduled_for or "").strip(),
        "notes": (req.notes or "").strip(),
        "settings": req.settings or {},
        "status": "planned",
        "created_at": _now(),
        "updated_at": _now(),
        "provider_doc": _provider_catalog()[provider],
        "launch_plan": {
            "browser_source_url": overlay.get("browser_source_url", "") if overlay else "",
            "scorebug_url": overlay.get("scorebug_url", "") if overlay else "",
            "engine": "OBS browser source + media source" if overlay else "ffmpeg/OBS media source",
            "ffmpeg_preview": "Pending launch parameters",
            "notes": [
                "Use OBS with the generated overlay browser source for the full produced stream look.",
                "If you want unattended looping output, provide an RTMP URL and stream key and launch the ffmpeg session.",
            ],
        },
    }
    _write_json(_record_path("sessions", session_id), session)
    return {"status": "ok", "session": session}


@app.get("/sessions/{session_id}")
async def session_detail(session_id: str):
    record = _read_json(_record_path("sessions", session_id))
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    proc = ACTIVE_SESSION_PROCS.get(session_id)
    if proc and proc.poll() is None:
        record["status"] = "running"
        record["runtime"] = {"pid": proc.pid}
    return {"status": "ok", "session": record}


@app.post("/sessions/{session_id}/launch")
async def session_launch(session_id: str, req: BroadcastSessionLaunchRequest):
    require_feature("LUMINAX_HOST_EXECUTION_ENABLED")
    record = _read_json(_record_path("sessions", session_id))
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    source_video = _resolve_video_source(str(record.get("source_video", "")))
    account = _read_json(_record_path("accounts", str(record.get("account_key", ""))))
    target = _session_target(str(record.get("provider", "")), req.rtmp_url or str(record.get("rtmp_url", "")), req.stream_key, account if account else None)
    command = _build_ffmpeg_command(
        source_video,
        target,
        loop_video=record.get("loop_video", True) if req.loop_video is None else bool(req.loop_video),
        additional_args=req.additional_ffmpeg_args,
    )
    ffmpeg = shutil.which(FFMPEG_BIN)
    if not ffmpeg:
        raise HTTPException(status_code=503, detail="ffmpeg is not available")
    log_path = BROADCAST_ROOT / "logs" / f"{session_id}.log"
    log_fh = log_path.open("ab")
    proc = subprocess.Popen(command, stdout=log_fh, stderr=subprocess.STDOUT)
    ACTIVE_SESSION_PROCS[session_id] = proc
    record["status"] = "running"
    record["updated_at"] = _now()
    record["runtime"] = {"pid": proc.pid, "started_at": _now(), "log_path": str(log_path)}
    record["launch_plan"] = {**(record.get("launch_plan") or {}), "ffmpeg_preview": " ".join(command), "target": target}
    _write_json(_record_path("sessions", session_id), record)
    return {"status": "ok", "session": record, "pid": proc.pid, "command": command, "log_path": str(log_path)}


@app.post("/sessions/{session_id}/stop")
async def session_stop(session_id: str):
    record = _read_json(_record_path("sessions", session_id))
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    proc = ACTIVE_SESSION_PROCS.get(session_id)
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        ACTIVE_SESSION_PROCS.pop(session_id, None)
    record["status"] = "stopped"
    record["updated_at"] = _now()
    _write_json(_record_path("sessions", session_id), record)
    return {"status": "ok", "session": record}


@app.get("/commentary")
async def commentary(limit: int = Query(default=80), project_key: str = Query(default="")):
    rows = _list_records("commentary", limit=limit)
    key = _slug(project_key, default="")
    if key:
        rows = [row for row in rows if _slug(str(row.get("project_key", "")), default="") == key]
    return {"status": "ok", "rows": rows, "count": len(rows)}


@app.get("/commentary/{job_id}")
async def commentary_detail(job_id: str):
    record = _read_json(_record_path("commentary", job_id))
    if not record:
        raise HTTPException(status_code=404, detail="Commentary plan not found")
    return {"status": "ok", "commentary": record}


@app.post("/commentary/create")
async def commentary_create(req: CommentaryPlanCreateRequest):
    job_id = f"commentary_{_now()}_{uuid.uuid4().hex[:8]}"
    source_video = _resolve_video_source(req.source_video)
    sample_every_sec = max(4, min(int(req.sample_every_sec or 18), 120))
    max_samples = max(2, min(int(req.max_samples or 8), 24))
    frame_rows = _sample_video_frames(source_video, sample_every_sec=sample_every_sec, max_samples=max_samples)
    described: list[dict[str, Any]] = []
    for idx, row in enumerate(frame_rows):
        frame_path = Path(row["frame_path"])
        description = await _vision_describe(frame_path)
        described.append({"timecode": row["timecode"], "frame_path": str(frame_path), "description": description, "index": idx})
    try:
        segments = await _model_commentary_segments(described, req.persona, req.style)
        generation_mode = "model"
    except Exception:
        segments = _fallback_commentary_segments(described, req.persona, req.style)
        generation_mode = "fallback"
    record = {
        "job_id": job_id,
        "project_key": _slug(req.project_key, default=""),
        "source_video": source_video,
        "persona": (req.persona or "").strip(),
        "style": (req.style or "").strip(),
        "sample_every_sec": sample_every_sec,
        "max_samples": max_samples,
        "notes": (req.notes or "").strip(),
        "generated_at": _now(),
        "generation_mode": generation_mode,
        "frames": described,
        "segments": segments,
    }
    _write_json(_record_path("commentary", job_id), record)
    return {"status": "ok", "commentary": record}


install_runtime_guard(app, 'broadcast-conductor', require_auth=True)
