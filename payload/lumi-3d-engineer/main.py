from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Luminax 3D Engineer")
GAMES_ROOT = Path(os.getenv("GAMES_ROOT", "/data/games"))
JOB_ROOT = GAMES_ROOT / "_jobs"
if runtime_enabled():
    JOB_ROOT.mkdir(parents=True, exist_ok=True)

class AssetRequest(BaseModel):
    project_key: str
    prompt: str
    provider: str = "triposr"
    fallback_provider: str = "meshy"

class UE5ScaffoldRequest(BaseModel):
    project_key: str
    title: str = "Unreal Project"
    use_cpp: bool = True
    template: str = "ThirdPerson"

class GameServerRequest(BaseModel):
    project_key: str
    engine: str = "unreal_dedicated"
    language: str = "cpp"

class DeployRequest(BaseModel):
    project_key: str
    target: str = "docker_local"

def safe_key(raw: str) -> str:
    key = "".join(ch for ch in (raw or "").strip().lower() if ch.isalnum() or ch in "-_").strip("-_")
    if not key:
        raise HTTPException(status_code=400, detail="project_key is required")
    return key[:128]

def project_root(project_key: str) -> Path:
    key = safe_key(project_key)
    root = (GAMES_ROOT / key).resolve()
    if not str(root).startswith(str(GAMES_ROOT.resolve())):
        raise HTTPException(status_code=400, detail="Invalid project path")
    root.mkdir(parents=True, exist_ok=True)
    return root

def write_job(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = f"{kind}_{uuid.uuid4().hex[:10]}"
    record = {"job_id": job_id, "kind": kind, "status": "planned", "created_at": int(time.time()), **payload}
    (JOB_ROOT / f"{job_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record

@app.get("/health")
async def health():
    return {"status": "ok", "service": "lumi-3d-engineer", "games_root": str(GAMES_ROOT)}

@app.get("/workflow/node_templates")
async def workflow_node_templates():
    return {"status": "ok", "node_types": ["Generate3DAsset", "BuildUE5Project", "SpinGameServer", "DeployGame"]}

@app.post("/assets/generate")
async def assets_generate(req: AssetRequest):
    root = project_root(req.project_key)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    job = write_job("Generate3DAsset", {"project_key": req.project_key, "prompt": req.prompt, "provider": req.provider, "fallback_provider": req.fallback_provider})
    preview = assets / f"{job['job_id']}.txt"
    preview.write_text(f"3D asset scaffold\nprompt={req.prompt}\nprovider={req.provider}\nfallback={req.fallback_provider}\n", encoding="utf-8")
    return {"status": "queued", "job": job, "preview_artifact": str(preview)}

@app.post("/ue5/scaffold")
async def ue5_scaffold(req: UE5ScaffoldRequest):
    root = project_root(req.project_key)
    for rel in ["Source", "Config", "Content/Maps", "Content/Materials", "BuildScripts", "Server"]:
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / f"{safe_key(req.project_key)}.uproject").write_text(
        json.dumps({"FileVersion": 3, "EngineAssociation": "5.x", "Category": "Games", "Description": req.title}, indent=2),
        encoding="utf-8",
    )
    job = write_job("BuildUE5Project", {"project_key": req.project_key, "title": req.title, "template": req.template, "use_cpp": bool(req.use_cpp)})
    return {"status": "scaffolded", "job": job, "project_root": str(root)}

@app.post("/servers/spin")
async def servers_spin(req: GameServerRequest):
    root = project_root(req.project_key)
    server_dir = root / "Server"
    server_dir.mkdir(parents=True, exist_ok=True)
    (server_dir / "README.md").write_text(
        f"# Dedicated Server Scaffold\nengine={req.engine}\nlanguage={req.language}\n",
        encoding="utf-8",
    )
    job = write_job("SpinGameServer", {"project_key": req.project_key, "engine": req.engine, "language": req.language})
    return {"status": "queued", "job": job, "server_root": str(server_dir)}

@app.post("/deploy")
async def deploy(req: DeployRequest):
    root = project_root(req.project_key)
    deploy_log = root / "deploy.log"
    deploy_log.write_text(f"[{int(time.time())}] deploy target={req.target}\n", encoding="utf-8")
    job = write_job("DeployGame", {"project_key": req.project_key, "target": req.target})
    return {"status": "planned", "job": job, "deploy_log": str(deploy_log)}

@app.get("/library/list")
async def library_list(limit: int = 200):
    rows = []
    for p in sorted(GAMES_ROOT.glob("*")):
        if p.name.startswith("_") or not p.is_dir():
            continue
        rows.append({"project_key": p.name, "path": str(p), "updated_at": int(p.stat().st_mtime)})
        if len(rows) >= max(1, min(int(limit or 200), 500)):
            break
    return {"status": "ok", "rows": rows}


install_runtime_guard(app, 'lumi-3d-engineer', require_auth=True)
