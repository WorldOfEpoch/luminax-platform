from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import os, tempfile, edge_tts, subprocess
from pathlib import Path
import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="Luminax Voice Agent")
VOICE_REF_ROOT = Path(os.getenv("VOICE_REF_ROOT", "/data/tts_ref"))
DEFAULT_VOICE = os.getenv("EDGE_TTS_VOICE", "en-US-JennyNeural")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
XTTS_MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
AFFECT_CONTROLLER_URL = (os.getenv("AFFECT_CONTROLLER_URL", "") or "").strip().rstrip("/")
_xtts = None
_whisper = None
ALLOWED_UPLOAD_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac"}

class SpeakRequest(BaseModel):
    text: str
    voice: str | None = None
    speed: float = 1.02
    emotion: str = "neutral"
    format: str = "mp3"

class CloneRequest(BaseModel):
    text: str
    voice_ref: str

@app.get('/voices/list')
async def voices_list():
    VOICE_REF_ROOT.mkdir(parents=True, exist_ok=True)
    refs = []
    for p in sorted(VOICE_REF_ROOT.glob('*.wav')):
        try:
            refs.append({"name": p.name, "size": p.stat().st_size})
        except Exception:
            refs.append({"name": p.name, "size": None})
    return {"status": "ok", "default_voice": DEFAULT_VOICE, "voice_refs": refs, "accepted_upload_types": sorted(ALLOWED_UPLOAD_EXTS)}

@app.get('/voices/catalog')
async def voices_catalog():
    try:
        voices = await edge_tts.list_voices()
    except Exception as e:
        return {"status": "error", "detail": str(e), "default_voice": DEFAULT_VOICE, "voices": []}
    normalized = []
    for v in voices:
        normalized.append({
            "short_name": v.get("ShortName"),
            "friendly_name": v.get("FriendlyName"),
            "locale": v.get("Locale"),
            "gender": v.get("Gender"),
            "voice_type": v.get("VoiceType"),
            "style_list": v.get("StyleList") or [],
        })
    normalized.sort(key=lambda x: ((x.get("locale") or ""), (x.get("short_name") or "")))
    return {
        "status": "ok",
        "default_voice": DEFAULT_VOICE,
        "voice_count": len(normalized),
        "voices": normalized,
        "downloadable_preview_note": "Use /speak with a selected voice to download a preview clip.",
    }

@app.post('/voices/upload')
async def voices_upload(file: UploadFile = File(...)):
    VOICE_REF_ROOT.mkdir(parents=True, exist_ok=True)
    raw_name = Path(file.filename or 'voice.webm').name
    safe_name = ''.join(ch for ch in raw_name if ch.isalnum() or ch in '._-') or 'voice.webm'
    ext = Path(safe_name).suffix.lower() or ".webm"
    if ext not in ALLOWED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f'Unsupported file extension: {ext}')
    stem = ''.join(ch for ch in Path(safe_name).stem if ch.isalnum() or ch in '._-') or 'voice'
    target = (VOICE_REF_ROOT / f"{stem}.wav").resolve()
    if not str(target).startswith(str(VOICE_REF_ROOT.resolve())):
        raise HTTPException(status_code=400, detail='Invalid upload path')
    data = await file.read()
    fd_in, in_path = tempfile.mkstemp(suffix=ext)
    os.close(fd_in)
    with open(in_path, 'wb') as f:
        f.write(data)
    ff = subprocess.run(
        ["ffmpeg", "-y", "-i", in_path, "-ac", "1", "-ar", "24000", str(target)],
        capture_output=True,
        text=True,
    )
    try:
        os.remove(in_path)
    except Exception:
        pass
    if ff.returncode != 0:
        raise HTTPException(status_code=400, detail=f"Audio conversion failed: {(ff.stderr or ff.stdout)[-400:]}")
    return {"status": "uploaded", "voice_ref": target.name, "bytes": len(data), "converted_from": ext}

def get_xtts():
    global _xtts
    if _xtts is None:
        from TTS.api import TTS
        _xtts = TTS(XTTS_MODEL_NAME)
    return _xtts

def get_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _whisper

def emotion_profile(emotion: str) -> dict[str, str]:
    e = (emotion or "neutral").strip().lower()
    profiles = {
        "neutral": {"pitch": "+0Hz", "volume": "+0%"},
        "warm": {"pitch": "+12Hz", "volume": "+2%"},
        "happy": {"pitch": "+24Hz", "volume": "+4%"},
        "serious": {"pitch": "-8Hz", "volume": "+0%"},
        "calm": {"pitch": "-4Hz", "volume": "-2%"},
        "excited": {"pitch": "+32Hz", "volume": "+6%"},
    }
    return profiles.get(e, profiles["neutral"])

def _scale_to_percent_delta(value: float) -> str:
    pct = int(round((float(value) - 1.0) * 100))
    return f"{pct:+d}%"

async def resolve_voice_profile(emotion: str, context: str = "default") -> dict[str, str]:
    fallback = emotion_profile(emotion)
    if not AFFECT_CONTROLLER_URL:
        return fallback
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            response = await client.post(
                f"{AFFECT_CONTROLLER_URL}/voice/style",
                json={
                    "mood": (emotion or "neutral").strip().lower() or "neutral",
                    "context": (context or "default").strip().lower() or "default",
                    "energy": 0.5,
                    "urgency": 0.0,
                    "clarity_bias": 0.6,
                },
            )
            response.raise_for_status()
            payload = response.json()
        style = payload.get("voice_hints") if isinstance(payload, dict) else {}
        if not isinstance(style, dict):
            return fallback
        return {
            "pitch": _scale_to_percent_delta(style.get("pitch_scale", 1.0)),
            "volume": _scale_to_percent_delta(style.get("volume_scale", 1.0)),
            "rate": _scale_to_percent_delta(style.get("rate_scale", 1.0)),
        }
    except Exception:
        return fallback

async def edge_speak_to_file(text: str, voice: str, out_path: str, rate: str = "+15%", pitch: str = "+0Hz", volume: str = "+0%"):
    await edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch, volume=volume).save(out_path)

@app.get("/health")
async def health():
    VOICE_REF_ROOT.mkdir(parents=True, exist_ok=True)
    refs = sorted([p.name for p in VOICE_REF_ROOT.glob('*.wav')])[:50]
    return {"status": "ok", "default_voice": DEFAULT_VOICE, "whisper_model": WHISPER_MODEL, "voice_refs": refs, "engines": ["edge_tts", "xtts_v2", "faster_whisper"], "emotions": ["neutral", "warm", "happy", "serious", "calm", "excited"], "affect_controller": AFFECT_CONTROLLER_URL or ""}

@app.post("/speak")
async def speak(req: SpeakRequest):
    output_format = (req.format or "mp3").strip().lower()
    if output_format not in {"mp3", "wav"}:
        raise HTTPException(status_code=400, detail="Unsupported speak format. Allowed: mp3, wav")
    speed = max(0.75, min(req.speed, 1.5))
    pct = int(round((speed - 1.0) * 100))
    rate = f"{pct:+d}%"
    prof = await resolve_voice_profile(req.emotion)
    fd_mp3, mp3_path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd_mp3)
    await edge_speak_to_file(req.text[:3000], req.voice or DEFAULT_VOICE, mp3_path, rate=rate, pitch=prof["pitch"], volume=prof["volume"])
    if output_format == "mp3":
        return FileResponse(mp3_path, media_type="audio/mpeg", filename="speak.mp3")
    fd_wav, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd_wav)
    ff = subprocess.run(
        ["ffmpeg", "-y", "-i", mp3_path, "-ac", "1", "-ar", "24000", wav_path],
        capture_output=True,
        text=True,
    )
    if ff.returncode != 0:
        raise HTTPException(status_code=400, detail=f"Speak WAV conversion failed: {(ff.stderr or ff.stdout)[-400:]}")
    return FileResponse(wav_path, media_type="audio/wav", filename="speak.wav")

@app.post("/clone")
async def clone(req: CloneRequest):
    ref = (VOICE_REF_ROOT / req.voice_ref).resolve()
    if not str(ref).startswith(str(VOICE_REF_ROOT.resolve())):
        raise HTTPException(status_code=400, detail="Invalid voice ref path")
    if not ref.exists():
        raise HTTPException(status_code=404, detail="Voice reference file not found")
    fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    get_xtts().tts_to_file(text=req.text[:2500], speaker_wav=str(ref), language="en", file_path=out_path)
    return FileResponse(out_path, media_type="audio/wav", filename="clone.wav")

@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    suffix = Path(file.filename or "audio.bin").suffix or ".wav"
    fd, temp_in = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    with open(temp_in, "wb") as f:
        f.write(await file.read())
    segments, info = get_whisper().transcribe(temp_in, vad_filter=True)
    text = " ".join([seg.text.strip() for seg in segments]).strip()
    return {"text": text, "language": getattr(info, "language", None), "duration": getattr(info, "duration", None)}

@app.post("/talk_turn")
async def talk_turn(file: UploadFile = File(...), voice: str | None = None, speed: float = 1.02):
    suffix = Path(file.filename or "audio.bin").suffix or ".wav"
    fd, temp_in = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    with open(temp_in, "wb") as f:
        f.write(await file.read())
    segments, info = get_whisper().transcribe(temp_in, vad_filter=True)
    text = " ".join([seg.text.strip() for seg in segments]).strip()
    return {"text": text, "language": getattr(info, "language", None), "duration": getattr(info, "duration", None), "voice": voice or DEFAULT_VOICE, "speed": speed}


install_runtime_guard(app, 'voice-agent', require_auth=True)
