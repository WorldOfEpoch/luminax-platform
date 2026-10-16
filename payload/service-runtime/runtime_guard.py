"""Shared opt-in boundary for the source-preview operator services.

This is not a command dispatcher. Service owners still enforce their own action,
player-session, consent, and project-access rules after this boundary.
"""
import hmac
import json
import os


def feature_enabled(name):
    return os.getenv(name, "false").strip().lower() == "true"


def runtime_enabled():
    return feature_enabled("LUMINAX_RUNTIME_ENABLED")


def token_matches(supplied, expected):
    if not isinstance(expected, str) or len(expected) < 32 or not isinstance(supplied, str):
        return False
    try:
        return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
    except UnicodeError:
        return False


def require_feature(name):
    if not runtime_enabled() or not feature_enabled(name):
        try:
            from fastapi import HTTPException
        except ImportError:
            # The foreground daemon shares the guard but does not depend on ASGI.
            raise RuntimeError("feature_disabled") from None
        raise HTTPException(status_code=503, detail="feature_disabled")


class RuntimeGuard:
    """Fail closed without calling the application when disabled/unconfigured.

    Only the two shallow health paths bypass operator authentication. Legacy
    JSON body token callers remain supported up to 64 KiB. Larger bodies must
    authenticate with a header, so uploads are never consumed just for auth.
    WebSocket operator clients must authenticate in headers; player services
    opt out of this layer and retain their existing session/token checks.
    """
    def __init__(self, app, *, service, require_auth=True, required_feature=None):
        self.app = app
        self.service = service
        self.require_auth = require_auth
        self.required_feature = required_feature

    async def reject(self, scope, send, status, code):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": code})
            return
        body = json.dumps({"status": code, "service": self.service}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        health = (scope["type"] == "http" and scope.get("method") in {"GET", "HEAD"}
                  and scope.get("path") in {"/health", "/api/health"})
        if not runtime_enabled():
            await self.reject(scope, send, 200 if health else 503, "runtime_disabled")
            return
        if self.required_feature and not feature_enabled(self.required_feature):
            await self.reject(scope, send, 200 if health else 503, "feature_disabled")
            return
        if self.require_auth:
            expected = os.getenv("CONTROL_TOKEN", "")
            if len(expected) < 32:
                await self.reject(scope, send, 503, "operator_token_not_configured")
                return
            # Public health is shallow and never invokes a service handler that
            # might contact a model, database, CLI, or private infrastructure.
            if health:
                await self.reject(scope, send, 200, "enabled")
                return
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            supplied = headers.get(b"x-control-token", b"").decode("utf-8", "replace")
            auth = headers.get(b"authorization", b"").decode("utf-8", "replace")
            if auth.startswith("Bearer "):
                supplied = auth[7:]
            replay = []
            if not supplied and scope["type"] == "http" and headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower() == b"application/json":
                body = bytearray()
                while True:
                    event = await receive()
                    if event.get("type") != "http.request":
                        await self.reject(scope, send, 401, "operator_auth_required")
                        return
                    chunk = event.get("body", b"")
                    if len(body) + len(chunk) > 65536 or len(replay) >= 1024:
                        await self.reject(scope, send, 413, "auth_body_too_large_use_header")
                        return
                    replay.append(event)
                    body.extend(chunk)
                    if not event.get("more_body", False):
                        break
                try:
                    data = json.loads(body)
                    supplied = data.get("token", "") if isinstance(data, dict) else ""
                except (ValueError, UnicodeDecodeError, RecursionError):
                    supplied = ""
            if not token_matches(supplied, expected):
                await self.reject(scope, send, 401, "operator_auth_required")
                return
            if replay:
                original_receive = receive

                async def receive():
                    return replay.pop(0) if replay else await original_receive()
        await self.app(scope, receive, send)


def install_runtime_guard(app, service, *, require_auth=True, required_feature=None):
    app.add_middleware(RuntimeGuard, service=service, require_auth=require_auth,
                       required_feature=required_feature)
