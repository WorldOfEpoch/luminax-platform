from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import json
import os
import time
import uuid
from typing import Any

import httpx
import redis
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Luminax Hive Critic")
HIVE_CHANNEL = os.getenv("HIVE_CHANNEL", "hive-channel")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL_FAST = os.getenv("OLLAMA_CHAT_MODEL_FAST", "qwen2.5:14b")
BRAIN_KEY = "lumi-hive-critic"
BRAIN_ROLE = "critic"
redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)

class DebateRequest(BaseModel):
    debate_id: str = ""
    prompt: str
    context: dict[str, Any] = {}

SAFE_TO_ACT_HINTS = ["project", "workflow", "plan", "task", "build", "create"]
HIGH_RISK_HINTS = ["fund transfer", "bill pay", "delete", "wipe", "mass message", "production db", "credentials"]

def choose_vote(prompt: str) -> tuple[str, str]:
    p = (prompt or "").strip().lower()
    risky = any(k in p for k in HIGH_RISK_HINTS)
    safe_intent = any(k in p for k in SAFE_TO_ACT_HINTS)
    if risky:
        return "plan_only", "Critic vetoes immediate execution until consent and risk checks pass."
    if safe_intent and len(p) >= 12:
        return "create_project", "Critic approves controlled project spawn with auditable tasks."
    return "plan_only", "Critic requests more specific objective before action."

async def maybe_enhance_summary(prompt: str, fallback: str) -> str:
    msg = [
        {"role": "system", "content": "You are Luminax hive critic. Reply in one sentence focused on risk, quality, and safeguards."},
        {"role": "user", "content": prompt[:1200]},
    ]
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(f"{OLLAMA_HOST}/api/chat", json={"model": MODEL_FAST, "messages": msg, "stream": False})
            r.raise_for_status()
            out = r.json().get("message", {}).get("content", "").strip()
            return out or fallback
    except Exception:
        return fallback

@app.get("/health")
async def health():
    return {"status": "ok", "brain": BRAIN_KEY, "role": BRAIN_ROLE, "channel": HIVE_CHANNEL}

@app.post("/evaluate")
@app.post("/debate")
async def debate(req: DebateRequest):
    debate_id = (req.debate_id or uuid.uuid4().hex[:12]).strip()[:64]
    vote, summary = choose_vote(req.prompt)
    summary = await maybe_enhance_summary(req.prompt, summary)
    payload = {
        "debate_id": debate_id,
        "brain": BRAIN_KEY,
        "role": BRAIN_ROLE,
        "vote": vote,
        "summary": summary,
        "prompt": req.prompt[:2000],
        "created_at": int(time.time()),
        "context_meta": {"project_key": (req.context or {}).get("project_key"), "task_count": len(((req.context or {}).get("recent_project_tasks") or [])), "memory_count": len(((req.context or {}).get("recent_memory") or []))},
    }
    try:
        redis_client.publish(HIVE_CHANNEL, json.dumps(payload))
    except Exception:
        pass
    return {"status": "ok", "entry": payload}



install_runtime_guard(app, 'lumi-hive-critic', require_auth=True)
