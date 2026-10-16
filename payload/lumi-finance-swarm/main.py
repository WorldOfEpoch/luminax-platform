from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Luminax Finance Swarm")
MONEY_MODE = os.getenv("FINANCE_MONEY_MODE", "false").lower() == "true"
DAILY_CAP_USD = float(os.getenv("FINANCE_DAILY_CAP_USD", "250"))
MEMORY_ROOT = Path(os.getenv("MEMORY_ROOT", "/data/memory"))
FIN_ROOT = MEMORY_ROOT / "finance"
if runtime_enabled():
    FIN_ROOT.mkdir(parents=True, exist_ok=True)

AGENTS = [
    {"key": "market-watcher", "role": "market_intel"},
    {"key": "politician-tracker", "role": "sec_watch"},
    {"key": "world-event-analyzer", "role": "macro_events"},
    {"key": "portfolio-manager", "role": "portfolio"},
    {"key": "billpay-agent", "role": "billpay"},
    {"key": "insurance-registration-agent", "role": "compliance"},
]

class FinancePlanRequest(BaseModel):
    goal: str
    amount_usd: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    consent_token: str = ""
    mode: Literal["dry_run", "simulate"] = "dry_run"

def safe_write(name: str, payload: dict[str, Any]) -> Path:
    safe = "".join(ch for ch in (name or "").strip().lower() if ch.isalnum() or ch in "-_").strip("-_")
    if not safe:
        safe = uuid.uuid4().hex[:10]
    path = FIN_ROOT / f"{safe}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "lumi-finance-swarm",
        "money_mode": MONEY_MODE,
        "daily_cap_usd": DAILY_CAP_USD,
        "agents": AGENTS,
    }

@app.get("/agents/list")
async def agents_list():
    return {"status": "ok", "rows": AGENTS}

@app.get("/briefing/daily")
async def briefing_daily():
    payload = {
        "status": "ok",
        "generated_at": int(time.time()),
        "headline": "Finance swarm daily executive briefing scaffold",
        "sections": [
            "Market snapshot (planned provider: Polygon/Alpaca)",
            "SEC/politician filing watch (planned)",
            "World event risk monitor (planned)",
            "Portfolio and billpay recommendations (consent-gated)",
        ],
        "money_mode": MONEY_MODE,
        "daily_cap_usd": DAILY_CAP_USD,
    }
    safe_write(f"briefing_{payload['generated_at']}", payload)
    return payload

@app.get("/overview")
async def overview():
    accounts = (await accounts_list()).get("rows", [])
    upcoming = (await upcoming_payments()).get("rows", [])
    cash = round(sum(float(r.get("balance_usd") or 0.0) for r in accounts if str(r.get("account_type", "")).lower() in {"bank", "cash"}), 2)
    retirement = round(sum(float(r.get("balance_usd") or 0.0) for r in accounts if str(r.get("account_type", "")).lower() == "retirement"), 2)
    investments = round(sum(float(r.get("balance_usd") or 0.0) for r in accounts if str(r.get("account_type", "")).lower() == "investment"), 2)
    debt = round(sum(float(r.get("balance_usd") or 0.0) for r in accounts if str(r.get("account_type", "")).lower() == "credit"), 2)
    net_worth = round(cash + retirement + investments + debt, 2)
    return {
        "status": "ok",
        "money_mode": MONEY_MODE,
        "daily_cap_usd": DAILY_CAP_USD,
        "summary": {
            "net_worth_usd": net_worth,
            "cash_usd": cash,
            "retirement_usd": retirement,
            "investments_usd": investments,
            "credit_debt_usd": debt,
            "upcoming_due_usd": round(sum(float(r.get("amount_usd") or 0.0) for r in upcoming), 2),
        },
        "cards": AGENTS,
    }

@app.post("/actions/plan")
async def actions_plan(req: FinancePlanRequest):
    action_id = f"fin_{uuid.uuid4().hex[:10]}"
    over_cap = float(req.amount_usd or 0.0) > DAILY_CAP_USD
    plan = {
        "action_id": action_id,
        "goal": req.goal,
        "amount_usd": float(req.amount_usd or 0.0),
        "mode": req.mode,
        "consent_required": True,
        "money_mode_required": True,
        "blocked_reason": "amount_over_daily_cap" if over_cap else "",
        "status": "planned" if not over_cap else "blocked",
        "created_at": int(time.time()),
    }
    safe_write(action_id, plan)
    return {"status": "ok", "plan": plan}

@app.post("/actions/execute")
async def actions_execute(req: FinancePlanRequest):
    if not MONEY_MODE:
        raise HTTPException(status_code=403, detail="Money mode disabled")
    if (req.consent_token or "").strip().lower() != "approved":
        raise HTTPException(status_code=403, detail="Consent token required")
    if float(req.amount_usd or 0.0) > DAILY_CAP_USD:
        raise HTTPException(status_code=400, detail="Amount exceeds daily cap")
    action_id = f"fin_exec_{uuid.uuid4().hex[:10]}"
    record = {
        "action_id": action_id,
        "goal": req.goal,
        "amount_usd": float(req.amount_usd or 0.0),
        "status": "executed_simulated",
        "created_at": int(time.time()),
        "notes": "Simulation only. No banking, brokerage, or payment adapter is implemented.",
    }
    safe_write(action_id, record)
    return {"status": "ok", "record": record}

@app.get("/accounts/list")
async def accounts_list():
    rows = [
        {"account_id": "checking-main", "provider": "plaid", "name": "Example Checking", "account_type": "bank", "balance_usd": 0.0, "status": "connected_simulated"},
        {"account_id": "savings-hysa", "provider": "plaid", "name": "Example Savings", "account_type": "bank", "balance_usd": 0.0, "status": "connected_simulated"},
        {"account_id": "retirement-401k", "provider": "plaid", "name": "Example Retirement", "account_type": "retirement", "balance_usd": 0.0, "status": "connected_simulated"},
        {"account_id": "brokerage-main", "provider": "alpaca", "name": "Example Brokerage", "account_type": "investment", "balance_usd": 0.0, "status": "connected_simulated"},
        {"account_id": "credit-main", "provider": "plaid", "name": "Example Credit Card", "account_type": "credit", "balance_usd": 0.0, "status": "connected_simulated"},
    ]
    return {
        "status": "ok",
        "rows": rows,
        "notes": "Synthetic demonstration accounts; live keys do not connect these fixtures to a provider.",
    }

@app.get("/upcoming/payments")
async def upcoming_payments(window: int = 30):
    days = window if window in {30, 60, 90} else 30
    template = [
        ("Example Housing", 0.0, 5),
        ("Example Utilities", 0.0, 9),
        ("Example Insurance", 0.0, 16),
        ("Example Credit Card", 0.0, 22),
        ("Example Registration", 0.0, 41),
    ]
    rows = []
    for title, amount, due_in_days in template:
        if due_in_days > days:
            continue
        rows.append(
            {
                "payment_id": f"{title.lower().replace(' ', '_')}-{due_in_days}",
                "title": title,
                "amount_usd": amount,
                "due_in_days": due_in_days,
                "status": "upcoming",
            }
        )
    return {"status": "ok", "window_days": days, "rows": rows}

@app.get("/statements/list")
async def statements_list():
    return {
        "status": "ok",
        "rows": [
            {"statement_id": "stmt-checking-latest", "account_id": "checking-main", "period": "latest", "status": "available_simulated"},
            {"statement_id": "stmt-credit-latest", "account_id": "credit-main", "period": "latest", "status": "available_simulated"},
        ],
    }


install_runtime_guard(app, 'lumi-finance-swarm', require_auth=True, required_feature='LUMINAX_FINANCE_SIMULATION_ENABLED')
