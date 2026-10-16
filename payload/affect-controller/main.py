from __future__ import annotations
from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Affect Controller")

MOOD_BASELINES = {
    "neutral": {"pitch": 1.00, "volume": 1.00, "rate": 1.00, "style": "steady"},
    "calm": {"pitch": 0.96, "volume": 0.92, "rate": 0.90, "style": "grounded"},
    "focused": {"pitch": 0.99, "volume": 1.00, "rate": 0.97, "style": "clear"},
    "empathetic": {"pitch": 1.03, "volume": 0.95, "rate": 0.94, "style": "warm"},
    "reassuring": {"pitch": 0.98, "volume": 0.94, "rate": 0.91, "style": "soft"},
    "excited": {"pitch": 1.09, "volume": 1.12, "rate": 1.11, "style": "bright"},
    "urgent": {"pitch": 1.05, "volume": 1.15, "rate": 1.18, "style": "direct"},
    "stern": {"pitch": 0.93, "volume": 1.08, "rate": 0.92, "style": "firm"},
}

CONTEXT_ADJUSTMENTS = {
    "default": {"pitch": 0.00, "volume": 0.00, "rate": 0.00, "style_hint": "balanced"},
    "briefing": {"pitch": 0.00, "volume": 0.03, "rate": -0.02, "style_hint": "structured"},
    "combat": {"pitch": 0.02, "volume": 0.12, "rate": 0.10, "style_hint": "high_clarity"},
    "social": {"pitch": 0.03, "volume": 0.00, "rate": 0.04, "style_hint": "natural"},
    "recovery": {"pitch": -0.02, "volume": -0.05, "rate": -0.08, "style_hint": "gentle"},
    "stealth": {"pitch": -0.03, "volume": -0.12, "rate": -0.05, "style_hint": "quiet"},
    "celebration": {"pitch": 0.05, "volume": 0.08, "rate": 0.06, "style_hint": "upbeat"},
}

VOICE_HINT_BOUNDS = {
    "pitch_scale": {"min": 0.85, "max": 1.15},
    "volume_scale": {"min": 0.80, "max": 1.20},
    "rate_scale": {"min": 0.85, "max": 1.20},
}


class AffectRequest(BaseModel):
    mood: str = "neutral"
    context: str = "default"
    energy: float = 0.5
    urgency: float = 0.0
    clarity_bias: float = 0.5


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def resolve_affect(request: AffectRequest) -> dict[str, Any]:
    mood_key = request.mood.strip().lower() or "neutral"
    context_key = request.context.strip().lower() or "default"
    mood = MOOD_BASELINES.get(mood_key, MOOD_BASELINES["neutral"])
    context = CONTEXT_ADJUSTMENTS.get(context_key, CONTEXT_ADJUSTMENTS["default"])
    energy = clamp(request.energy, 0.0, 1.0)
    urgency = clamp(request.urgency, 0.0, 1.0)
    clarity_bias = clamp(request.clarity_bias, 0.0, 1.0)

    pitch_scale = clamp(
        mood["pitch"] + context["pitch"] + ((energy - 0.5) * 0.08) + ((urgency - 0.5) * 0.04),
        VOICE_HINT_BOUNDS["pitch_scale"]["min"],
        VOICE_HINT_BOUNDS["pitch_scale"]["max"],
    )
    volume_scale = clamp(
        mood["volume"] + context["volume"] + (urgency * 0.08) + ((clarity_bias - 0.5) * 0.04),
        VOICE_HINT_BOUNDS["volume_scale"]["min"],
        VOICE_HINT_BOUNDS["volume_scale"]["max"],
    )
    rate_scale = clamp(
        mood["rate"] + context["rate"] + ((energy - 0.5) * 0.10) + (urgency * 0.08) + ((clarity_bias - 0.5) * 0.04),
        VOICE_HINT_BOUNDS["rate_scale"]["min"],
        VOICE_HINT_BOUNDS["rate_scale"]["max"],
    )

    return {
        "mood": mood_key if mood_key in MOOD_BASELINES else "neutral",
        "context": context_key if context_key in CONTEXT_ADJUSTMENTS else "default",
        "style_name": f"{mood['style']}_{context['style_hint']}",
        "voice_hints": {
            "pitch_scale": round(pitch_scale, 3),
            "volume_scale": round(volume_scale, 3),
            "rate_scale": round(rate_scale, 3),
        },
        "signals": {
            "energy": round(energy, 3),
            "urgency": round(urgency, 3),
            "clarity_bias": round(clarity_bias, 3),
        },
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "affect-controller",
        "bounded_hints": VOICE_HINT_BOUNDS,
    }


@app.post("/affect/resolve")
def affect_resolve(request: AffectRequest) -> dict[str, Any]:
    resolved = resolve_affect(request)
    return {"status": "ok", "affect": resolved}


@app.post("/voice/style")
def voice_style(request: AffectRequest) -> dict[str, Any]:
    resolved = resolve_affect(request)
    return {
        "status": "ok",
        "style_name": resolved["style_name"],
        "voice_hints": resolved["voice_hints"],
        "bounds": VOICE_HINT_BOUNDS,
    }


install_runtime_guard(app, 'affect-controller', require_auth=True)
