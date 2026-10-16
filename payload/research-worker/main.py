from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import asyncio
import json
import os
import time
import urllib.parse
import uuid

import httpx
from bs4 import BeautifulSoup
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="Luminax Research Worker")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL_FAST = os.getenv("OLLAMA_CHAT_MODEL_FAST", "qwen2.5:14b")
MODEL_REASONING = os.getenv("OLLAMA_CHAT_MODEL_REASONING", "deepseek-r1:32b")
OPENAI_ENABLE = os.getenv("OPENAI_ENABLE", "false").lower() == "true"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_CHAT_MODEL_FAST = os.getenv("OPENAI_CHAT_MODEL_FAST", "gpt-5.2")
OPENAI_CHAT_MODEL_REASONING = os.getenv("OPENAI_CHAT_MODEL_REASONING", "gpt-5.4")
BROWSER_AGENT_URL = os.getenv("BROWSER_AGENT_URL", "http://browser-agent:8010")
CORE_API_URL = os.getenv("CORE_API_URL", "http://assistant-core:8000/api")
JOBS: dict[str, dict] = {}

class ResearchRequest(BaseModel):
    topic: str
    max_sources: int = 6
    max_iterations: int = 3
    provider: str = "auto"
    reasoning_provider: str = "auto"
    model_fast: str = ""
    model_reasoning: str = ""

def parse_llm_json(cleaned: str) -> list[str]:
    cleaned = (cleaned or "").strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, list) else []
    except Exception as e:
        print(f"[Research] JSON parse failed on: {cleaned[:50]}... Error: {e}")
        return []

def resolve_provider(mode: str) -> str:
    wanted = (mode or "auto").strip().lower()
    if wanted in {"openai", "ollama"}:
        return wanted
    if OPENAI_ENABLE and OPENAI_API_KEY:
        return "openai"
    return "ollama"

async def openai_generate(prompt: str, model: str) -> str:
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=300) as client:
        rr = await client.post(
            f"{OPENAI_BASE_URL}/responses",
            headers=headers,
            json={"model": model, "input": [{"role": "user", "content": prompt}], "temperature": 0.3},
        )
        rr.raise_for_status()
        payload = rr.json()
        text = (payload.get("output_text") or "").strip()
        if text:
            return text
        chunks = []
        for item in payload.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            for part in item.get("content", []) or []:
                if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                    if part.get("text"):
                        chunks.append(str(part.get("text")))
        if chunks:
            return "\n".join(chunks).strip()
        # Compatibility fallback
        rc = await client.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers=headers,
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3},
        )
        rc.raise_for_status()
        return str(rc.json()["choices"][0]["message"]["content"]).strip()

async def llm_generate(prompt: str, model: str, format_json: bool = False, provider: str = "auto") -> str:
    resolved = resolve_provider(provider)
    if resolved == "openai" and OPENAI_ENABLE and OPENAI_API_KEY:
        picked_model = model or (OPENAI_CHAT_MODEL_FAST if not format_json else OPENAI_CHAT_MODEL_REASONING)
        return await openai_generate(prompt, picked_model)
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False}
    if format_json:
        payload["format"] = "json"
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(f"{OLLAMA_HOST}/api/chat", json=payload)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "")

async def browser_navigate(url: str) -> dict:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{BROWSER_AGENT_URL}/navigate", json={"url": url})
        r.raise_for_status()
        return r.json()

async def extract_facts_from_source(url: str, text: str, topic: str) -> str:
    prompt = f"""You are a research analyst. Extract all factual information, statistics, and key claims relevant to the topic: '{topic}'.
If the text is irrelevant, an error page, a captcha, or requires a login, simply reply with EXACTLY \"NO_RELEVANT_DATA\".

SOURCE URL: {url}
TEXT:
{text[:8000]}
"""
    try:
        return await llm_generate(prompt, MODEL_FAST)
    except Exception as e:
        return f"Error extracting: {e}"

async def evaluate_information_gap(topic: str, current_knowledge: str) -> list[str]:
    prompt = f"""You are reviewing the current gathered research for the topic: '{topic}'.

CURRENT KNOWLEDGE BASE:
{current_knowledge[-6000:]}

Analyze if this is sufficient to write a highly detailed, definitive, and well-rounded report.
If the information is complete and satisfactory, reply with EXACTLY an empty JSON array: []

If critical information, counter-arguments, recent developments, or specific data points are missing,
generate up to 2 highly specific new search engine queries to find that missing info.
Return ONLY a JSON array of strings. Example: [\"missing financial data query\", \"recent controversies query\"]
"""
    try:
        return parse_llm_json(await llm_generate(prompt, MODEL_FAST, format_json=True))
    except Exception as e:
        print(f"[Research] Gap evaluation failed: {e}")
        return []

async def execute_research(topic: str, max_sources: int, max_iterations: int, provider: str = "auto", reasoning_provider: str = "auto", model_fast: str = "", model_reasoning: str = "", job_id: str | None = None):
    print(f"[Research] Starting deep iterative research on: {topic}")
    job = JOBS.get(job_id or "", {})
    if job_id:
        JOBS[job_id] = {
            **job,
            "job_id": job_id,
            "status": "running",
            "topic": topic,
            "provider": resolve_provider(provider),
            "reasoning_provider": resolve_provider(reasoning_provider),
            "started_at": int(time.time()),
            "updated_at": int(time.time()),
            "progress": {"phase": "query_bootstrap", "iteration": 0, "iterations_total": max_iterations},
        }
    query_prompt = f"""I need to research: '{topic}'.
Generate 2 distinct search engine queries to gather foundational facts.
Return ONLY a JSON array of strings. Example: [\"query 1\", \"query 2\"]"""
    try:
        fast_model = (model_fast or OPENAI_CHAT_MODEL_FAST) if resolve_provider(provider) == "openai" else MODEL_FAST
        active_queries = parse_llm_json(await llm_generate(query_prompt, fast_model, format_json=True, provider=provider)) or [topic]
    except Exception:
        active_queries = [topic]
    accumulated_context = ""
    seen_domains = set()
    all_evaluated_links = []
    cognitive_trace = []
    for iteration in range(max_iterations):
        if not active_queries:
            break
        if job_id and job_id in JOBS:
            JOBS[job_id]["updated_at"] = int(time.time())
            JOBS[job_id]["progress"] = {"phase": "crawl_extract", "iteration": iteration + 1, "iterations_total": max_iterations, "queries": active_queries}
        cognitive_trace.append({"iteration": iteration + 1, "queries": active_queries})
        links = []
        for q in active_queries:
            search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(q)}"
            try:
                search_data = await browser_navigate(search_url)
                html = search_data.get("html_preview", "")
                soup = BeautifulSoup(html, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if href.startswith("http") and "duckduckgo.com" not in href:
                        domain = urllib.parse.urlparse(href).netloc
                        if domain not in seen_domains:
                            seen_domains.add(domain)
                            links.append(href)
            except Exception:
                pass
        target_links = links[:max_sources]
        all_evaluated_links.extend(target_links)

        async def process_link(url: str):
            try:
                res = await browser_navigate(url)
                if isinstance(res, dict) and "text_preview" in res and res["text_preview"].strip():
                    facts = await extract_facts_from_source(url, res["text_preview"], topic)
                    if "NO_RELEVANT_DATA" not in facts:
                        return f"\n\n--- Source: {url} ---\n{facts}\n"
            except Exception:
                pass
            return ""

        extracted_results = await asyncio.gather(*[process_link(url) for url in target_links])
        accumulated_context += "".join(filter(bool, extracted_results))
        if iteration < max_iterations - 1:
            new_queries = await evaluate_information_gap(topic, accumulated_context)
            if not new_queries:
                cognitive_trace.append({"action": "early_termination", "reason": "Sufficient knowledge gathered."})
                break
            active_queries = new_queries
            cognitive_trace.append({"action": "gap_identified", "new_queries": new_queries})

    if not accumulated_context.strip():
        accumulated_context = "No detailed external facts could be successfully extracted. Rely on internal knowledge."

    safe_context = accumulated_context[:30000]
    report_prompt = f"""You are Luminax's lead autonomous research analyst.
Write a comprehensive, highly detailed markdown report on the topic: '{topic}'.
Synthesize the facts from the following accumulated knowledge base.
- Use professional formatting, clear headings, and bullet points.
- Identify consensus, nuance, and conflicting information.
- Cite your sources directly based on the provided URLs.

ACCUMULATED KNOWLEDGE BASE:
{safe_context}
"""
    reasoning_model = (model_reasoning or OPENAI_CHAT_MODEL_REASONING) if resolve_provider(reasoning_provider) == "openai" else MODEL_REASONING
    report = await llm_generate(report_prompt, reasoning_model, provider=reasoning_provider)
    trace_markdown = "\n".join([
        f"- **Loop {t.get('iteration', 'N/A')}**: Searched {t.get('queries', [])}. "
        + (f"*(Gaps found: {t.get('new_queries', [])})*" if 'new_queries' in t else "")
        for t in cognitive_trace
        if 'queries' in t
    ])
    final_memory = f"""# Deep Research Report: {topic}

## Cognitive Trace
*How I learned this:*
{trace_markdown}
- **Unique Domains Evaluated**: {len(all_evaluated_links)}

## Final Report
{report}"""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            await client.post(f"{CORE_API_URL}/memory/save", json={"text": final_memory})
    except Exception as e:
        print(f"[Research] Failed to inject to memory: {e}")
    if job_id and job_id in JOBS:
        JOBS[job_id]["updated_at"] = int(time.time())
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["result_preview"] = report[:1200]
        JOBS[job_id]["sources_evaluated"] = len(all_evaluated_links)
        JOBS[job_id]["progress"] = {"phase": "completed", "iteration": max_iterations, "iterations_total": max_iterations}

@app.get("/health")
async def health():
    return {"status": "ok", "mode": "reflective_orchestrator", "jobs_tracked": len(JOBS)}

async def execute_research_job(job_id: str, req: ResearchRequest):
    try:
        await execute_research(
            topic=req.topic,
            max_sources=req.max_sources,
            max_iterations=req.max_iterations,
            provider=req.provider,
            reasoning_provider=req.reasoning_provider,
            model_fast=req.model_fast,
            model_reasoning=req.model_reasoning,
            job_id=job_id,
        )
    except Exception as e:
        JOBS[job_id] = {
            **JOBS.get(job_id, {}),
            "job_id": job_id,
            "status": "failed",
            "error": str(e),
            "updated_at": int(time.time()),
        }

@app.get("/jobs")
async def jobs():
    rows = sorted(JOBS.values(), key=lambda x: int(x.get("updated_at", 0)), reverse=True)
    return {"status": "ok", "rows": rows[:200]}

@app.get("/jobs/latest")
async def jobs_latest():
    rows = sorted(JOBS.values(), key=lambda x: int(x.get("updated_at", 0)), reverse=True)
    return {"status": "ok", "job": rows[0] if rows else None}

@app.get("/jobs/get")
async def jobs_get(job_id: str = Query(...)):
    row = JOBS.get(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "ok", "job": row}

@app.post("/run")
async def run(req: ResearchRequest, background_tasks: BackgroundTasks):
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "topic": req.topic,
        "provider": resolve_provider(req.provider),
        "reasoning_provider": resolve_provider(req.reasoning_provider),
        "model_fast": req.model_fast or (OPENAI_CHAT_MODEL_FAST if resolve_provider(req.provider) == "openai" else MODEL_FAST),
        "model_reasoning": req.model_reasoning or (OPENAI_CHAT_MODEL_REASONING if resolve_provider(req.reasoning_provider) == "openai" else MODEL_REASONING),
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    background_tasks.add_task(execute_research_job, job_id, req)
    return {
        "status": "started",
        "job_id": job_id,
        "topic": req.topic,
        "provider": resolve_provider(req.provider),
        "reasoning_provider": resolve_provider(req.reasoning_provider),
        "model_fast": req.model_fast or (OPENAI_CHAT_MODEL_FAST if resolve_provider(req.provider) == "openai" else MODEL_FAST),
        "model_reasoning": req.model_reasoning or (OPENAI_CHAT_MODEL_REASONING if resolve_provider(req.reasoning_provider) == "openai" else MODEL_REASONING),
        "message": f"Reflective deep research dispatched (max {req.max_iterations} loops). The final report will be injected into Luminax memory upon completion.",
    }


install_runtime_guard(app, 'research-worker', require_auth=True)
