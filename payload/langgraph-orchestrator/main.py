from __future__ import annotations
from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)

import hashlib
import json
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="LangGraph Orchestrator")

GRAPH_CATALOG = [
    {
        "graph_id": "continuity_resume_graph",
        "summary": "Placeholder graph for continuity recovery.",
        "status": "placeholder",
    },
    {
        "graph_id": "affect_alignment_graph",
        "summary": "Placeholder graph for affect and voice coordination.",
        "status": "placeholder",
    },
    {
        "graph_id": "mcp_tool_dispatch_graph",
        "summary": "Placeholder graph for routed tool dispatch.",
        "status": "placeholder",
    },
]


class GraphRunRequest(BaseModel):
    graph_id: str
    input_payload: dict[str, Any] = Field(default_factory=dict)
    caller: str = "unknown"
    run_mode: str = "compatibility_stub"


def stable_hash(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def get_graph(graph_id: str) -> dict[str, Any] | None:
    for graph in GRAPH_CATALOG:
        if graph["graph_id"] == graph_id:
            return graph
    return None


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "langgraph-orchestrator",
        "runtime_mode": "compatibility_stub",
        "langgraph_available": False,
        "graph_count": len(GRAPH_CATALOG),
    }


@app.get("/graphs/list")
def graphs_list() -> dict[str, Any]:
    return {
        "graphs": GRAPH_CATALOG,
        "count": len(GRAPH_CATALOG),
        "runtime_mode": "compatibility_stub",
    }


@app.post("/graphs/run")
def graphs_run(request: GraphRunRequest) -> dict[str, Any]:
    graph = get_graph(request.graph_id)
    if not graph:
        raise HTTPException(status_code=404, detail="graph not found")

    fingerprint = stable_hash(
        {
            "graph_id": request.graph_id,
            "input_payload": request.input_payload,
            "caller": request.caller,
            "run_mode": request.run_mode,
        }
    )
    receipt = {
        "run_id": f"graph-{fingerprint}",
        "graph_id": request.graph_id,
        "status": "placeholder",
        "runtime_mode": "compatibility_stub",
        "deterministic_fingerprint": fingerprint,
    }
    return {
        "status": "accepted",
        "graph": graph,
        "receipt": receipt,
        "events": [
            {"step": "validate_graph", "status": "ok"},
            {"step": "compatibility_stub", "status": "placeholder"},
            {"step": "emit_receipt", "status": "ok"},
        ],
        "outputs": {
            "message": "LangGraph runtime is not installed in this scaffold yet.",
            "echo": request.input_payload,
        },
    }


install_runtime_guard(app, 'langgraph-orchestrator', require_auth=True)
