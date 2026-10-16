from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import base64
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import redis
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect

app = FastAPI(title="Luminax Realtime Gateway")
redis_client = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)
CORE_API_URL = os.getenv("CORE_API_URL", "http://assistant-core:8000/api")
VOICE_AGENT_URL = os.getenv("VOICE_AGENT_URL", os.getenv("EDGE_VOICE_URL", "http://voice-agent:8030"))
REALTIME_PROVIDER = os.getenv("REALTIME_PROVIDER", "local")
REALTIME_MODEL = os.getenv("REALTIME_MODEL", "gpt-realtime")
REALTIME_MAX_TURNS = int(os.getenv("REALTIME_MAX_TURNS", "40"))
REALTIME_TIMEOUT_SEC = int(os.getenv("REALTIME_TIMEOUT_SEC", "900"))
REALTIME_TTS_DEFAULT = os.getenv("REALTIME_TTS_DEFAULT", "true").lower() == "true"
REALTIME_SENTENCE_AUDIO = os.getenv("REALTIME_SENTENCE_AUDIO", "true").lower() == "true"
VOICE_DIAGNOSTICS_PATH = Path(os.getenv("VOICE_DIAGNOSTICS_PATH", "/data/logs/olympus-realtime-gateway-diagnostics.jsonl"))

ACTIVE = {
    "enabled": False,
    "session_id": None,
    "provider": REALTIME_PROVIDER,
    "model": REALTIME_MODEL,
    "started_at": None,
    "notes": "Realtime conversational path active: streaming tokens + sentence-level speech chunks + websocket turn loop.",
    "latency_mode": "sentence_stream",
    "barge_in": False,
    "clients": 0,
    "turn_count": 0,
    "last_turn_ms": None,
    "last_error": None,
    "turns": [],
}


def _append_voice_breadcrumb(stage: str, severity: str, summary: str, details: str, *, surface: str = "web_voice", request_kind: str = "conversation", project: str = "", status: str = "", meta: dict[str, Any] | None = None) -> None:
    entry = {
        "ts": int(time.time()),
        "stage": stage,
        "severity": severity,
        "scene": "olympus-web-voice",
        "route": "realtime-gateway",
        "connection": {
            "surface": (surface or "web_voice").strip(),
            "request_kind": (request_kind or "conversation").strip(),
            "project": (project or "").strip(),
            "status": (status or "").strip(),
        },
        "summary": summary,
        "details": details,
        "meta": meta or {},
    }
    ACTIVE["last_breadcrumb"] = entry
    try:
        VOICE_DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with VOICE_DIAGNOSTICS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _coerce_json_like(raw: Any, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return dict(fallback or {})
    return dict(fallback or {})


def _voice_reply_payload(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "classification": str(review.get("classification") or "conversation").strip().lower() or "conversation",
        "plan_summary": str(review.get("plan_summary") or "").strip(),
        "requires_confirmation": bool(review.get("requires_confirmation")),
        "disclosure_mode": str(review.get("disclosure_mode") or "").strip(),
        "notify_targets": review.get("notify_targets") if isinstance(review.get("notify_targets"), list) else [],
        "user_reply": str(review.get("user_reply") or "").strip(),
    }


def _safe_float(value: Any, fallback: float = 1.0) -> float:
    try:
        return float(value)
    except Exception:
        return fallback


def _safe_bool(value: Any, fallback: bool) -> bool:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _split_complete_sentences(pending: str) -> tuple[list[str], str]:
    matches = list(re.finditer(r"[^.!?\n]+[.!?\n]+", pending or ""))
    if not matches:
        return [], pending
    sentences = [m.group(0).strip() for m in matches if m.group(0).strip()]
    remainder = pending[matches[-1].end():]
    return sentences, remainder


def _record_turn(turn: dict):
    ACTIVE["turns"] = (ACTIVE.get("turns") or [])[-(REALTIME_MAX_TURNS - 1):] + [turn]
    ACTIVE["turn_count"] = int(ACTIVE.get("turn_count") or 0) + 1
    try:
        redis_client.set("luminax_realtime_last_turn", json.dumps(turn), ex=3600)
    except Exception:
        pass


async def _speak_base64(text: str, voice: str | None = None, speed: float = 1.0, emotion: str = "warm") -> str | None:
    clean = (text or "").strip()
    if not clean:
        return None
    payload = {"text": clean[:3000], "speed": max(0.8, min(_safe_float(speed, 1.0), 1.3)), "emotion": (emotion or "warm").strip().lower()}
    if voice:
        payload["voice"] = voice
    async with httpx.AsyncClient(timeout=REALTIME_TIMEOUT_SEC) as client:
        r = await client.post(f"{VOICE_AGENT_URL}/speak", json=payload)
        r.raise_for_status()
        return base64.b64encode(r.content).decode("utf-8")


async def _transcribe_bytes(filename: str, data: bytes, content_type: str = "audio/wav") -> str:
    files = {"file": (filename, data, content_type)}
    async with httpx.AsyncClient(timeout=REALTIME_TIMEOUT_SEC) as client:
        r = await client.post(f"{VOICE_AGENT_URL}/transcribe", files=files)
        r.raise_for_status()
        return (r.json().get("text") or "").strip()


async def _review_zeus_turn(prompt: str, *, surface: str, project: str = "", actor: dict[str, Any] | None = None, request_kind: str = "conversation", meta: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "surface": (surface or "web_voice").strip(),
        "actor": actor or {},
        "project": (project or "").strip(),
        "utterance": (prompt or "").strip(),
        "request_kind": (request_kind or "conversation").strip(),
        "meta": meta or {},
    }
    _append_voice_breadcrumb(
        "zeus_review_request",
        "info",
        "Realtime turn sent to Zeus review",
        (prompt or "").strip()[:240],
        surface=surface,
        request_kind=request_kind,
        project=project,
        status="pending",
        meta={"actor": actor or {}},
    )
    async with httpx.AsyncClient(timeout=REALTIME_TIMEOUT_SEC) as client:
        r = await client.post(f"{CORE_API_URL}/zeus/review", json=payload)
        r.raise_for_status()
        result = r.json()
    if not isinstance(result, dict):
        result = {"status": "error", "classification": "blocked", "user_reply": "Zeus review returned an invalid payload."}
    _append_voice_breadcrumb(
        "route_plan_returned",
        "ok" if str(result.get("status") or "ok").lower() == "ok" else "warn",
        "Zeus review returned a plan",
        str(result.get("plan_summary") or result.get("user_reply") or "No plan summary")[:320],
        surface=surface,
        request_kind=request_kind,
        project=project,
        status=str(result.get("classification") or result.get("status") or "").strip(),
        meta={"notify_targets": result.get("notify_targets") if isinstance(result.get("notify_targets"), list) else []},
    )
    return result


async def _iter_stream_tokens(prompt: str):
    async with httpx.AsyncClient(timeout=REALTIME_TIMEOUT_SEC) as client:
        async with client.stream("POST", f"{CORE_API_URL}/chat/stream", json={"prompt": prompt}) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    d = json.loads(payload)
                except Exception:
                    continue
                token = d.get("token")
                if token:
                    yield token


async def _chat_fallback(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=REALTIME_TIMEOUT_SEC) as client:
        r = await client.post(f"{CORE_API_URL}/chat", json={"prompt": prompt})
        r.raise_for_status()
        return (r.json().get("response") or "").strip()


async def _run_turn(prompt: str, auto_tts: bool, voice: str | None, speed: float, emotion: str = "warm", ws: WebSocket | None = None, *, surface: str = "web_text", project: str = "", actor: dict[str, Any] | None = None, request_kind: str = "conversation", meta: dict[str, Any] | None = None):
    start = time.time()
    assistant_text = ""
    pending = ""
    audio_chunks = []
    review = await _review_zeus_turn(prompt, surface=surface, project=project, actor=actor, request_kind=request_kind, meta=meta)
    classification = str(review.get("classification") or "conversation").strip().lower() or "conversation"
    if classification in {"blocked", "actionable", "suggestion"}:
        assistant_text = str(review.get("user_reply") or review.get("plan_summary") or "Zeus reviewed that request.").strip()
        if auto_tts and assistant_text:
            audio_b64 = await _speak_base64(assistant_text, voice=voice, speed=speed, emotion=emotion)
            if audio_b64:
                chunk = {"type": "audio_chunk", "text": assistant_text, "format": "audio/mpeg", "audio_base64": audio_b64}
                audio_chunks.append(chunk)
                if ws:
                    await ws.send_json(chunk)
                _append_voice_breadcrumb(
                    "tts_reply",
                    "info",
                    "Realtime review reply synthesized",
                    assistant_text[:240],
                    surface=surface,
                    request_kind=request_kind,
                    project=project,
                    status=classification,
                )
        latency_ms = int((time.time() - start) * 1000)
        ACTIVE["last_turn_ms"] = latency_ms
        return {"assistant_text": assistant_text, "latency_ms": latency_ms, "audio_chunks": audio_chunks, "review": review}
    try:
        async for token in _iter_stream_tokens(prompt):
            assistant_text += token
            if ws:
                await ws.send_json({"type": "token", "token": token})
            if auto_tts and REALTIME_SENTENCE_AUDIO:
                pending += token
                ready, pending = _split_complete_sentences(pending)
                for sentence in ready:
                    audio_b64 = await _speak_base64(sentence, voice=voice, speed=speed, emotion=emotion)
                    if not audio_b64:
                        continue
                    chunk = {"type": "audio_chunk", "text": sentence, "format": "audio/mpeg", "audio_base64": audio_b64}
                    audio_chunks.append(chunk)
                    if ws:
                        await ws.send_json(chunk)
                    _append_voice_breadcrumb(
                        "tts_reply",
                        "info",
                        "Realtime sentence audio chunk synthesized",
                        sentence[:240],
                        surface=surface,
                        request_kind=request_kind,
                        project=project,
                        status="streaming",
                    )
        if not assistant_text.strip():
            assistant_text = await _chat_fallback(prompt)
            if ws and assistant_text:
                for piece in assistant_text.split():
                    await ws.send_json({"type": "token", "token": piece + " "})
            _append_voice_breadcrumb(
                "fallback_chosen",
                "warn",
                "Realtime stream fell back to chat endpoint",
                assistant_text[:240],
                surface=surface,
                request_kind=request_kind,
                project=project,
                status="chat_fallback",
            )
        if auto_tts and pending.strip():
            audio_b64 = await _speak_base64(pending.strip(), voice=voice, speed=speed, emotion=emotion)
            if audio_b64:
                chunk = {"type": "audio_chunk", "text": pending.strip(), "format": "audio/mpeg", "audio_base64": audio_b64}
                audio_chunks.append(chunk)
                if ws:
                    await ws.send_json(chunk)
                _append_voice_breadcrumb(
                    "tts_reply",
                    "info",
                    "Realtime final audio chunk synthesized",
                    pending.strip()[:240],
                    surface=surface,
                    request_kind=request_kind,
                    project=project,
                    status="complete",
                )
    except Exception:
        if not assistant_text.strip():
            assistant_text = await _chat_fallback(prompt)
            _append_voice_breadcrumb(
                "fallback_chosen",
                "error",
                "Realtime stream failed and fell back to chat endpoint",
                assistant_text[:240],
                surface=surface,
                request_kind=request_kind,
                project=project,
                status="chat_fallback",
            )
    latency_ms = int((time.time() - start) * 1000)
    ACTIVE["last_turn_ms"] = latency_ms
    return {"assistant_text": (assistant_text or "").strip(), "latency_ms": latency_ms, "audio_chunks": audio_chunks, "review": review}


def _ensure_session_enabled():
    if not ACTIVE.get("enabled"):
        raise HTTPException(status_code=409, detail="Realtime session is not active")


@app.get("/session/info")
async def session_info():
    return ACTIVE


@app.post("/session/start")
async def session_start(payload: dict | None = None):
    payload = payload or {}
    ACTIVE["enabled"] = True
    ACTIVE["session_id"] = uuid.uuid4().hex[:12]
    ACTIVE["started_at"] = int(time.time())
    ACTIVE["turns"] = []
    ACTIVE["turn_count"] = 0
    ACTIVE["last_turn_ms"] = None
    ACTIVE["last_error"] = None
    ACTIVE["barge_in"] = _safe_bool(payload.get("barge_in"), False)
    ACTIVE["latency_mode"] = str(payload.get("latency_mode") or "sentence_stream")
    redis_client.set("luminax_realtime_session", ACTIVE["session_id"], ex=3600)
    return ACTIVE


@app.post("/session/turn")
async def session_turn(payload: dict):
    _ensure_session_enabled()
    text = (payload.get("text") or payload.get("prompt") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing text/prompt")
    auto_tts = _safe_bool(payload.get("auto_tts"), REALTIME_TTS_DEFAULT)
    include_audio = _safe_bool(payload.get("include_audio"), True)
    voice = (payload.get("voice") or "").strip() or None
    speed = _safe_float(payload.get("speed"), 1.0)
    emotion = str(payload.get("emotion") or "warm").strip().lower() or "warm"
    surface = str(payload.get("surface") or "web_text").strip() or "web_text"
    project = str(payload.get("project") or "").strip()
    actor = _coerce_json_like(payload.get("actor"))
    request_kind = str(payload.get("request_kind") or "conversation").strip().lower() or "conversation"
    meta = _coerce_json_like(payload.get("meta"))
    result = await _run_turn(text, auto_tts=auto_tts, voice=voice, speed=speed, emotion=emotion, surface=surface, project=project, actor=actor, request_kind=request_kind, meta=meta)
    turn = {
        "ts": int(time.time()),
        "kind": "text",
        "user_text": text,
        "assistant_text": result["assistant_text"],
        "latency_ms": result["latency_ms"],
    }
    _record_turn(turn)
    response = {"status": "ok", **turn}
    if include_audio:
        response["audio_chunks"] = result["audio_chunks"]
    response.update(_voice_reply_payload(result.get("review") if isinstance(result.get("review"), dict) else {}))
    return response


@app.post("/session/turn_audio")
async def session_turn_audio(
    file: UploadFile = File(...),
    auto_tts: bool = REALTIME_TTS_DEFAULT,
    include_audio: bool = True,
    voice: str | None = None,
    speed: float = 1.0,
    emotion: str = "warm",
    surface: str = Form("web_voice"),
    project: str = Form(""),
    actor_json: str = Form("{}"),
    request_kind: str = Form("conversation"),
    meta_json: str = Form("{}"),
):
    _ensure_session_enabled()
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty audio file")
    filename = file.filename or "turn.wav"
    actor = _coerce_json_like(actor_json)
    meta = _coerce_json_like(meta_json)
    _append_voice_breadcrumb(
        "transcription_request",
        "info",
        "Realtime audio received for transcription",
        filename,
        surface=surface,
        request_kind=request_kind,
        project=project,
        status="pending",
        meta={"content_type": file.content_type or "audio/wav", "bytes": len(raw)},
    )
    transcript = await _transcribe_bytes(filename, raw, file.content_type or "audio/wav")
    if not transcript:
        _append_voice_breadcrumb(
            "fallback_chosen",
            "warn",
            "Realtime audio produced no transcript",
            filename,
            surface=surface,
            request_kind=request_kind,
            project=project,
            status="no_transcript",
        )
        return {"status": "ok", "user_text": "", "assistant_text": "", "audio_chunks": [], "latency_ms": 0, "note": "No transcript"}
    _append_voice_breadcrumb(
        "transcription_complete",
        "ok",
        "Realtime audio transcribed",
        transcript[:240],
        surface=surface,
        request_kind=request_kind,
        project=project,
        status="ok",
    )
    result = await _run_turn(transcript, auto_tts=bool(auto_tts), voice=voice, speed=speed, emotion=(emotion or "warm"), surface=surface, project=project, actor=actor, request_kind=request_kind, meta=meta)
    turn = {
        "ts": int(time.time()),
        "kind": "audio",
        "user_text": transcript,
        "assistant_text": result["assistant_text"],
        "latency_ms": result["latency_ms"],
    }
    _record_turn(turn)
    response = {"status": "ok", **turn}
    if include_audio:
        response["audio_chunks"] = result["audio_chunks"]
    response.update(_voice_reply_payload(result.get("review") if isinstance(result.get("review"), dict) else {}))
    return response


@app.websocket("/session/ws")
async def session_ws(ws: WebSocket):
    await ws.accept()
    ACTIVE["clients"] = int(ACTIVE.get("clients") or 0) + 1
    await ws.send_json({"type": "ready", "session": ACTIVE})
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw or "{}")
            except Exception:
                await ws.send_json({"type": "error", "detail": "Invalid JSON payload"})
                continue
            msg_type = (msg.get("type") or "").strip().lower()
            if msg_type == "ping":
                await ws.send_json({"type": "pong", "ts": int(time.time())})
                continue
            if msg_type == "start":
                await session_start(msg if isinstance(msg, dict) else {})
                await ws.send_json({"type": "session", "session": ACTIVE})
                continue
            if msg_type == "stop":
                await session_stop()
                await ws.send_json({"type": "session", "session": ACTIVE})
                continue
            _ensure_session_enabled()
            if msg_type == "turn_text":
                prompt = (msg.get("text") or msg.get("prompt") or "").strip()
                if not prompt:
                    await ws.send_json({"type": "error", "detail": "Missing turn text"})
                    continue
                auto_tts = _safe_bool(msg.get("auto_tts"), REALTIME_TTS_DEFAULT)
                voice = (msg.get("voice") or "").strip() or None
                speed = _safe_float(msg.get("speed"), 1.0)
                emotion = str(msg.get("emotion") or "warm").strip().lower() or "warm"
                surface = str(msg.get("surface") or "web_text").strip() or "web_text"
                project = str(msg.get("project") or "").strip()
                actor = _coerce_json_like(msg.get("actor"))
                request_kind = str(msg.get("request_kind") or "conversation").strip().lower() or "conversation"
                meta = _coerce_json_like(msg.get("meta"))
                await ws.send_json({"type": "turn_start", "kind": "text", "text": prompt})
                result = await _run_turn(prompt, auto_tts=auto_tts, voice=voice, speed=speed, emotion=emotion, ws=ws, surface=surface, project=project, actor=actor, request_kind=request_kind, meta=meta)
                turn = {
                    "ts": int(time.time()),
                    "kind": "text",
                    "user_text": prompt,
                    "assistant_text": result["assistant_text"],
                    "latency_ms": result["latency_ms"],
                }
                _record_turn(turn)
                await ws.send_json({"type": "turn_complete", **turn, **_voice_reply_payload(result.get("review") if isinstance(result.get("review"), dict) else {})})
                continue
            if msg_type == "turn_audio":
                audio_b64 = (msg.get("audio_base64") or "").strip()
                if not audio_b64:
                    await ws.send_json({"type": "error", "detail": "Missing audio_base64"})
                    continue
                mime = (msg.get("mime") or "audio/wav").strip()
                ext = ".wav"
                if "webm" in mime:
                    ext = ".webm"
                elif "mp3" in mime or "mpeg" in mime:
                    ext = ".mp3"
                try:
                    data = base64.b64decode(audio_b64)
                except Exception:
                    await ws.send_json({"type": "error", "detail": "Invalid audio_base64"})
                    continue
                transcript = await _transcribe_bytes(f"turn{ext}", data, mime)
                if not transcript:
                    await ws.send_json({"type": "turn_complete", "kind": "audio", "user_text": "", "assistant_text": "", "latency_ms": 0, "note": "No transcript"})
                    continue
                auto_tts = _safe_bool(msg.get("auto_tts"), REALTIME_TTS_DEFAULT)
                voice = (msg.get("voice") or "").strip() or None
                speed = _safe_float(msg.get("speed"), 1.0)
                emotion = str(msg.get("emotion") or "warm").strip().lower() or "warm"
                surface = str(msg.get("surface") or "web_voice").strip() or "web_voice"
                project = str(msg.get("project") or "").strip()
                actor = _coerce_json_like(msg.get("actor"))
                request_kind = str(msg.get("request_kind") or "conversation").strip().lower() or "conversation"
                meta = _coerce_json_like(msg.get("meta"))
                await ws.send_json({"type": "turn_start", "kind": "audio", "text": transcript})
                result = await _run_turn(transcript, auto_tts=auto_tts, voice=voice, speed=speed, emotion=emotion, ws=ws, surface=surface, project=project, actor=actor, request_kind=request_kind, meta=meta)
                turn = {
                    "ts": int(time.time()),
                    "kind": "audio",
                    "user_text": transcript,
                    "assistant_text": result["assistant_text"],
                    "latency_ms": result["latency_ms"],
                }
                _record_turn(turn)
                await ws.send_json({"type": "turn_complete", **turn, **_voice_reply_payload(result.get("review") if isinstance(result.get("review"), dict) else {})})
                continue
            await ws.send_json({"type": "error", "detail": f"Unsupported type: {msg_type or 'unknown'}"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        ACTIVE["last_error"] = str(e)
        try:
            await ws.send_json({"type": "error", "detail": str(e)})
        except Exception:
            pass
    finally:
        ACTIVE["clients"] = max(0, int(ACTIVE.get("clients") or 1) - 1)


@app.post("/session/stop")
async def session_stop():
    ACTIVE["enabled"] = False
    ACTIVE["session_id"] = None
    ACTIVE["started_at"] = None
    redis_client.delete("luminax_realtime_session")
    return ACTIVE


install_runtime_guard(app, 'realtime-gateway', require_auth=True)
