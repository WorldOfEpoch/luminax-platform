from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import base64, io, os, time, httpx
from fastapi import FastAPI, File, UploadFile, HTTPException
from PIL import Image

app = FastAPI(title="Luminax Vision Agent")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "gemma3")
LATEST = {"ts": 0, "analysis": "", "meta": {}}

@app.get("/health")
async def health():
    return {"status": "ok", "latest_ts": LATEST.get("ts", 0), "latest_meta": LATEST.get("meta", {}), "vision_model": VISION_MODEL}

@app.get("/latest")
async def latest():
    return LATEST

@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    data = await file.read()
    try:
        img = Image.open(io.BytesIO(data))
        width, height = img.size
        mode = img.mode
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")
    img_b64 = base64.b64encode(data).decode("utf-8")
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{OLLAMA_HOST}/api/chat",
            json={"model": VISION_MODEL, "messages": [{"role":"user","content":"Describe this screenshot for desktop assistance. Mention the visible app, controls, errors, and next useful action.","images":[img_b64]}], "stream": False}
        )
        r.raise_for_status()
        payload = r.json()
    analysis = payload.get("message", {}).get("content", "No vision response")
    LATEST["ts"] = int(time.time())
    LATEST["analysis"] = analysis
    LATEST["meta"] = {"width": width, "height": height, "mode": mode}
    return {"width": width, "height": height, "mode": mode, "analysis": analysis}


install_runtime_guard(app, 'vision-agent', require_auth=True)
