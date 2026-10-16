import argparse
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_request(request, *, timeout, context=None):
    handlers = [_NoRedirect()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers).open(request, timeout=timeout)

from datetime import datetime, timezone
from pathlib import Path


def load_local_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# Configuration is loaded only after explicit --apply.

DEFAULT_BASE_URL = os.getenv("LUMINAX_BASE_URL", "").strip()
DEFAULT_USERNAME = os.getenv("LUMINAX_WEB_USER", os.getenv("EXTERNAL_ACCESS_USER", "")).strip()
DEFAULT_PASSWORD = os.getenv("LUMINAX_WEB_PASSWORD", os.getenv("EXTERNAL_ACCESS_PASSWORD", "")).strip()


def auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def request_json(
    base_url: str,
    path: str,
    username: str,
    password: str,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 20.0,
) -> dict:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    data = None
    headers = dict(auth_headers(username, password))
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    ctx = ssl.create_default_context()
    started = time.perf_counter()
    try:
        with _open_request(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            text = raw.decode("utf-8", errors="replace")
            parsed = json.loads(text) if text.strip() else {}
            return {
                "ok": True,
                "status_code": getattr(resp, "status", 200),
                "latency_ms": round(elapsed_ms, 1),
                "payload": parsed,
            }
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "ok": False,
            "status_code": e.code,
            "latency_ms": round(elapsed_ms, 1),
            "error": raw[:1200],
        }
    except Exception as e:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": round(elapsed_ms, 1),
            "error": str(e),
        }


def request_text(
    base_url: str,
    path: str,
    username: str,
    password: str,
    timeout: float = 20.0,
) -> dict:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    req = urllib.request.Request(url, headers=auth_headers(username, password), method="GET")
    ctx = ssl.create_default_context()
    started = time.perf_counter()
    try:
        with _open_request(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return {
                "ok": True,
                "status_code": getattr(resp, "status", 200),
                "latency_ms": round(elapsed_ms, 1),
                "payload": raw[:2000],
            }
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "ok": False,
            "status_code": e.code,
            "latency_ms": round(elapsed_ms, 1),
            "error": raw[:1200],
        }
    except Exception as e:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": round(elapsed_ms, 1),
            "error": str(e),
        }


def evaluate_probe(name: str, category: str, result: dict, expect_key: str | None = None, max_latency_ms: float | None = None) -> dict:
    ok = bool(result.get("ok"))
    note = ""
    payload = result.get("payload")
    if ok and expect_key and not (isinstance(payload, dict) and str(payload.get(expect_key, "")).strip()):
        ok = False
        note = f"missing_key:{expect_key}"
    if ok and max_latency_ms is not None and float(result.get("latency_ms", 0.0)) > max_latency_ms:
        note = f"slow>{max_latency_ms:.0f}ms"
    if not ok and not note:
        note = str(result.get("error", ""))[:240]
    return {
        "name": name,
        "category": category,
        "ok": ok,
        "status_code": result.get("status_code", 0),
        "latency_ms": result.get("latency_ms", 0.0),
        "note": note,
    }


def build_probe_matrix(level: int, focus: str) -> list[tuple[str, str, str, dict | None, str | None, float | None, str]]:
    focus = (focus or "full").strip().lower()
    broad = [
        ("ui_home", "web", "GET /", None, None, 3000.0, "text"),
        ("health", "core", "GET /api/health", None, "status", 4000.0, "json"),
        ("readiness", "admin", "GET /admin/readiness/aggregate", None, "status", 6000.0, "json"),
        ("telemetry", "admin", "GET /admin/telemetry/latest", None, "status", 8000.0, "json"),
        ("capabilities", "core", "GET /api/capabilities", None, "skills_active", 5000.0, "json"),
        ("projects_overview", "projects", "GET /api/projects/overview", None, None, 5000.0, "json"),
        ("hive_status", "hive", "GET /api/hive/status", None, None, 5000.0, "json"),
        ("media_tools", "media", "GET /api/media/tools", None, "tools", 5000.0, "json"),
        ("codex_dashboard", "codex", "GET /api/codex/bridge/dashboard", None, "status", 7000.0, "json"),
        ("discord_chat_fast", "discord", "POST /api/chat/discord", {"prompt": "Say hello in one short sentence.", "mode": "conversation", "speaker": "Probe"}, "response", 6000.0, "json"),
    ]
    targeted = {
        "discord": [
            ("discord_chat_lookup", "discord", "POST /api/chat/discord", {"prompt": "What can you do here right now? Keep it short.", "mode": "lookup", "speaker": "Probe"}, "response", 9000.0, "json"),
            ("discord_chat_deep", "discord", "POST /api/chat/discord", {"prompt": "Summarize your current Discord/operator role in two sentences.", "mode": "lookup_deep", "speaker": "Probe"}, "response", 14000.0, "json"),
            ("discord_chat_voice", "discord", "POST /api/chat/discord", {"prompt": "Tell me what you are doing in one short sentence.", "mode": "voice", "speaker": "Probe"}, "response", 5000.0, "json"),
            ("voice_list", "voice", "GET /voice/voices/list", None, "status", 6000.0, "json"),
            ("voice_catalog", "voice", "GET /voice/voices/catalog", None, "status", 12000.0, "json"),
            ("realtime_info", "realtime", "GET /realtime/session/info", None, None, 8000.0, "json"),
        ],
        "voice": [
            ("voice_list", "voice", "GET /voice/voices/list", None, "status", 6000.0, "json"),
            ("voice_catalog", "voice", "GET /voice/voices/catalog", None, "status", 12000.0, "json"),
            ("discord_chat_voice", "discord", "POST /api/chat/discord", {"prompt": "Say hello in one short sentence.", "mode": "voice", "speaker": "Probe"}, "response", 5000.0, "json"),
        ],
        "codex": [
            ("codex_dashboard", "codex", "GET /api/codex/bridge/dashboard", None, "status", 7000.0, "json"),
            ("codex_sessions", "codex", "GET /api/codex/bridge/sessions", None, None, 7000.0, "json"),
        ],
        "media": [
            ("media_tools", "media", "GET /api/media/tools", None, "tools", 5000.0, "json"),
            ("media_jobs", "media", "GET /api/media/jobs", None, None, 7000.0, "json"),
        ],
        "web": [
            ("ui_home", "web", "GET /", None, None, 3000.0, "text"),
            ("default_site", "web", "GET /default-site/", None, None, 4000.0, "text"),
            ("projects_overview", "projects", "GET /api/projects/overview", None, None, 5000.0, "json"),
        ],
    }
    pinpoint = {
        "discord": [
            ("discord_voice_role", "discord", "POST /api/chat/discord", {"prompt": "In one short sentence, what is your role in this Discord server?", "mode": "voice", "speaker": "Probe"}, "response", 4500.0, "json"),
            ("discord_conversation_action", "discord", "POST /api/chat/discord", {"prompt": "Can you update the website theme here if I ask you to?", "mode": "conversation", "speaker": "Probe"}, "response", 6000.0, "json"),
            ("discord_capability_identity", "discord", "POST /api/chat/discord", {"prompt": "What can you directly do from this stack right now?", "mode": "lookup_deep", "speaker": "Probe"}, "response", 14000.0, "json"),
        ],
        "voice": [
            ("voice_list", "voice", "GET /voice/voices/list", None, "status", 6000.0, "json"),
            ("voice_catalog", "voice", "GET /voice/voices/catalog", None, "status", 12000.0, "json"),
            ("voice_turn_reply", "discord", "POST /api/chat/discord", {"prompt": "Tell me what you are doing right now.", "mode": "voice", "speaker": "Probe"}, "response", 4500.0, "json"),
        ],
        "codex": [
            ("codex_dashboard", "codex", "GET /api/codex/bridge/dashboard", None, "status", 7000.0, "json"),
            ("self_upgrade_latest", "codex", "GET /admin/self-upgrade/latest-proposal", None, None, 8000.0, "json"),
        ],
    }
    if level <= 1:
        return broad
    if level == 2:
        return targeted.get(focus, targeted["discord"])
    return pinpoint.get(focus, pinpoint["discord"])


def run_probe(base_url: str, username: str, password: str, probe: tuple[str, str, str, dict | None, str | None, float | None, str]) -> dict:
    name, category, spec, payload, expect_key, max_latency_ms, response_kind = probe
    method, path = spec.split(" ", 1)
    if response_kind == "text":
        result = request_text(base_url, path, username, password)
    else:
        result = request_json(base_url, path, username, password, method=method, payload=payload)
    return evaluate_probe(name, category, result, expect_key=expect_key, max_latency_ms=max_latency_ms)


def summarize(results: list[dict]) -> dict:
    failed = [r for r in results if not r["ok"]]
    slow = [r for r in results if r["ok"] and str(r.get("note", "")).startswith("slow>")]
    categories = sorted({r["category"] for r in failed}) or sorted({r["category"] for r in slow})
    return {
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "slow": len(slow),
        "max_latency_ms": round(max([float(r.get("latency_ms", 0.0)) for r in results] or [0.0]), 1),
        "next_focus": categories,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Luminax tiered probes against the live web stack.")
    parser.add_argument("--level", type=int, default=1, choices=[1, 2, 3])
    parser.add_argument("--focus", default="full")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--apply", action="store_true", help="Allow configured live HTTP probes")
    args = parser.parse_args()
    if not args.apply:
        print(json.dumps({"status": "plan", "detail": "No requests sent. Configure a test endpoint and add --apply."}))
        return 0
    if not args.base_url:
        parser.error("--base-url or LUMINAX_BASE_URL is required")

    probes = build_probe_matrix(args.level, args.focus)
    results = [run_probe(args.base_url, args.username, args.password, probe) for probe in probes]
    summary = summarize(results)
    status = "ok" if summary["failed"] == 0 else "degraded"
    report = {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "level": args.level,
        "focus": args.focus,
        "base_url": args.base_url,
        "summary": summary,
        "results": results,
    }
    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
