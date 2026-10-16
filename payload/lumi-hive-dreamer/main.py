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

app = FastAPI(title="Luminax Hive Dreamer")
HIVE_CHANNEL = os.getenv("HIVE_CHANNEL", "hive-channel")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL_FAST = os.getenv("OLLAMA_CHAT_MODEL_FAST", "qwen2.5:14b")
BRAIN_KEY = "lumi-hive-dreamer"
BRAIN_ROLE = "dreamer"
redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)

class DebateRequest(BaseModel):
    debate_id: str = ""
    prompt: str
    context: dict[str, Any] = {}

ACTIONABLE_HINTS = ["build", "create", "fix", "launch", "deploy", "setup", "implement", "automate", "project"]
RISKY_HINTS = ["wire money", "billpay", "delete", "ban", "remove", "format", "transfer funds"]

def choose_vote(prompt: str) -> tuple[str, str]:
    p = (prompt or "").strip().lower()
    actionable = any(k in p for k in ACTIONABLE_HINTS)
    risky = any(k in p for k in RISKY_HINTS)
    if actionable and not risky:
        return "create_project", "Dreamer votes to act and spawn project flow."
    if risky:
        return "plan_only", "Dreamer detected risk keywords; requires gated plan."
    return "plan_only", "Dreamer suggests planning before project spawn."

async def maybe_enhance_summary(prompt: str, fallback: str) -> str:
    msg = [
        {"role": "system", "content": "You are Luminax hive dreamer. Reply in one sentence with imaginative but practical project direction."},
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



install_runtime_guard(app, 'lumi-hive-dreamer', require_auth=True)
