from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import asyncio
import base64
import io
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import torch
from diffusers import AutoPipelineForText2Image
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from PIL import Image


app = FastAPI(title="Olympus Image Agent")

MODEL_ID = (os.getenv("EDGE_IMAGE_MODEL_ID", "stabilityai/sdxl-turbo") or "stabilityai/sdxl-turbo").strip()
MODEL_VARIANT = (os.getenv("EDGE_IMAGE_VARIANT", "fp16") or "fp16").strip()
MODEL_DEVICE = (os.getenv("EDGE_IMAGE_DEVICE", "cuda") or "cuda").strip().lower()
MODEL_LOAD_MODE = (os.getenv("EDGE_IMAGE_LOAD_MODE", "cpu_offload") or "cpu_offload").strip().lower()
STYLE_LIBRARY_PATH = Path(
    os.getenv("IMAGE_STYLE_LIBRARY_PATH", "/workspace/luminax/config/olympus-style-guides.json")
)
AGENT_VERSION = "2026-03-13"
HEALTH_WARMUP_ON_START = feature_enabled("EDGE_IMAGE_WARMUP_ON_START")
IMAGE_DIAGNOSTICS_PATH = Path(os.getenv("EDGE_IMAGE_DIAGNOSTICS_PATH", "/tmp/olympus-image-agent-diagnostics.jsonl"))
PIPELINE: Any = None
PIPELINE_LOCK = asyncio.Lock()
LAST_ERROR = ""
LAST_READY_TS = 0
LAST_RUNTIME_DEVICE = ""
LAST_WARMUP_TS = 0


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(min_length=3)
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1024
    steps: int = 4
    guidance_scale: float = 0.0
    seed: Optional[int] = None
    output_format: str = "png"
    style_key: str = ""


def _write_diag(status: str, summary: str, detail: str = "", stage: str = "image-health") -> None:
    try:
        IMAGE_DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": status,
            "service": "image-agent",
            "stage": stage,
            "summary": str(summary or "")[:240],
            "detail": str(detail or "")[:1000],
            "model_id": MODEL_ID,
            "requested_device": MODEL_DEVICE,
            "runtime_device": LAST_RUNTIME_DEVICE,
            "generated_at": int(time.time()),
        }
        with IMAGE_DIAGNOSTICS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _style_keys() -> list[str]:
    try:
        payload = json.loads(STYLE_LIBRARY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = payload.get("styles", []) if isinstance(payload, dict) else []
    return [
        str(row.get("style_key", "")).strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("style_key", "")).strip()
    ]


def _normalize_dimension(value: int, fallback: int) -> int:
    try:
        clean = int(value)
    except Exception:
        clean = fallback
    clean = max(512, min(clean, 1536))
    clean = max(64, (clean // 64) * 64)
    return clean or fallback


def _normalize_output_format(value: str) -> str:
    clean = str(value or "png").strip().lower()
    return clean if clean in {"png", "jpeg", "webp"} else "png"


async def _load_pipeline() -> Any:
    global PIPELINE, LAST_ERROR, LAST_READY_TS, LAST_RUNTIME_DEVICE, LAST_WARMUP_TS
    if PIPELINE is not None:
        return PIPELINE
    async with PIPELINE_LOCK:
        if PIPELINE is not None:
            return PIPELINE
        try:
            pipe = AutoPipelineForText2Image.from_pretrained(
                MODEL_ID,
                torch_dtype=torch.float16,
                variant=MODEL_VARIANT,
                use_safetensors=True,
            )
            pipe.set_progress_bar_config(disable=True)
            pipe.enable_attention_slicing()
            if MODEL_DEVICE == "cuda" and torch.cuda.is_available():
                if MODEL_LOAD_MODE == "cpu_offload":
                    pipe.enable_model_cpu_offload()
                else:
                    pipe = pipe.to("cuda")
                LAST_RUNTIME_DEVICE = "cuda"
            else:
                pipe = pipe.to("cpu")
                LAST_RUNTIME_DEVICE = "cpu"
            PIPELINE = pipe
            LAST_ERROR = ""
            LAST_READY_TS = int(time.time())
            LAST_WARMUP_TS = LAST_READY_TS
            return PIPELINE
        except Exception as exc:
            LAST_ERROR = str(exc)
            LAST_RUNTIME_DEVICE = ""
            _write_diag("failed", "Image model failed to load.", str(exc), stage="image-load")
            raise HTTPException(status_code=503, detail=f"Image model failed to load: {exc}")


async def _warmup_pipeline() -> None:
    try:
        await _load_pipeline()
        snapshot = _health_snapshot()
        if snapshot["status"] != "ok":
            _write_diag(snapshot["status"], "Image warmup did not reach a ready state.", snapshot["reason"], stage="image-warmup")
    except Exception:
        pass


def _health_snapshot() -> dict[str, Any]:
    cuda_available = bool(torch.cuda.is_available())
    cuda_expected = MODEL_DEVICE == "cuda"
    pipeline_loaded = PIPELINE is not None
    runtime_device = LAST_RUNTIME_DEVICE or ("cuda" if cuda_expected and cuda_available and pipeline_loaded else "cpu" if pipeline_loaded else "")
    ready = pipeline_loaded and not LAST_ERROR and ((not cuda_expected) or runtime_device == "cuda")
    if cuda_expected and not cuda_available:
        status = "failed"
        reason = "CUDA requested but torch.cuda.is_available() is false."
    elif LAST_ERROR:
        status = "failed" if cuda_expected else "degraded"
        reason = LAST_ERROR
    elif HEALTH_WARMUP_ON_START and not pipeline_loaded:
        status = "degraded"
        reason = "Warmup is still pending; pipeline is not loaded yet."
    elif cuda_expected and runtime_device != "cuda":
        status = "failed"
        reason = "CUDA was requested but the active runtime device is not cuda."
    elif ready:
        status = "ok"
        reason = "Image pipeline loaded and ready."
    else:
        status = "degraded"
        reason = "Image pipeline has not reached a ready state yet."
    return {
        "status": status,
        "reason": reason,
        "cuda_available": cuda_available,
        "cuda_expected": cuda_expected,
        "pipeline_loaded": pipeline_loaded,
        "runtime_device": runtime_device,
        "ready": ready,
    }


@app.on_event("startup")
async def startup_event() -> None:
    if runtime_enabled() and HEALTH_WARMUP_ON_START:
        asyncio.create_task(_warmup_pipeline())


@app.get("/health")
async def health() -> dict[str, Any]:
    snapshot = _health_snapshot()
    return {
        "status": snapshot["status"],
        "service": "image-agent",
        "display_name": "Helios Forge",
        "version": AGENT_VERSION,
        "model_id": MODEL_ID,
        "model_variant": MODEL_VARIANT,
        "device": MODEL_DEVICE,
        "runtime_device": snapshot["runtime_device"],
        "load_mode": MODEL_LOAD_MODE,
        "pipeline_loaded": snapshot["pipeline_loaded"],
        "ready": snapshot["ready"],
        "warmup_on_start": HEALTH_WARMUP_ON_START,
        "ready_ts": LAST_READY_TS,
        "warmup_ts": LAST_WARMUP_TS,
        "style_keys": _style_keys(),
        "last_error": LAST_ERROR,
        "health_reason": snapshot["reason"],
        "cuda_available": snapshot["cuda_available"],
        "cuda_expected": snapshot["cuda_expected"],
        "diagnostic_path": str(IMAGE_DIAGNOSTICS_PATH),
    }


@app.get("/styles")
async def styles() -> dict[str, Any]:
    return {"status": "ok", "style_keys": _style_keys(), "path": str(STYLE_LIBRARY_PATH)}


@app.post("/generate")
async def generate(req: ImageGenerateRequest) -> dict[str, Any]:
    pipe = await _load_pipeline()
    width = _normalize_dimension(req.width, 1024)
    height = _normalize_dimension(req.height, 1024)
    steps = max(1, min(int(req.steps or 4), 32))
    guidance_scale = float(req.guidance_scale or 0.0)
    generator = None
    if req.seed is not None:
        generator = torch.Generator("cuda" if MODEL_DEVICE == "cuda" and torch.cuda.is_available() else "cpu").manual_seed(int(req.seed))
    started = time.time()
    try:
        result = pipe(
            prompt=req.prompt,
            negative_prompt=req.negative_prompt or None,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
            width=width,
            height=height,
            generator=generator,
        )
        image = result.images[0]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Local image generation failed: {exc}")
    output_format = _normalize_output_format(req.output_format)
    buf = io.BytesIO()
    save_format = "JPEG" if output_format == "jpeg" else output_format.upper()
    save_kwargs: dict[str, Any] = {}
    if output_format == "jpeg":
        if image.mode != "RGB":
            image = image.convert("RGB")
        save_kwargs["quality"] = 92
    image.save(buf, format=save_format, **save_kwargs)
    payload = base64.b64encode(buf.getvalue()).decode("utf-8")
    return {
        "status": "completed",
        "provider": "edge_local",
        "model": MODEL_ID,
        "image_b64": payload,
        "extension": "jpg" if output_format == "jpeg" else output_format,
        "mime": f"image/{output_format}",
        "width": image.width,
        "height": image.height,
        "elapsed_sec": round(time.time() - started, 3),
        "style_key": req.style_key,
    }


install_runtime_guard(app, 'image-agent', require_auth=True)
