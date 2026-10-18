"""On-demand GitHub project reads owned by assistant-core.

This module starts no worker, stores no provider response, and performs no
GitHub writes. The caller supplies assistant-core's existing operator check.
GitHub credentials are read only from the server process environment.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response


GITHUB_API = "https://api.github.com"
MAX_REPOSITORIES = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ISSUE_PAGES = 3
NO_STORE = {"Cache-Control": "no-store", "Vary": "Authorization, X-Control-Token"}
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z")
_rate_limit_until = 0.0


class ConnectionError(Exception):
    """Fixed public errors; never retain response bodies, URLs, or credentials."""

    def __init__(self, code: str, message: str, status_code: int = 502, retry_after: int = 0):
        super().__init__(code)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after

    def public(self) -> dict[str, Any]:
        value = {"code": self.code, "message": self.message}
        if self.retry_after:
            value["retry_after_seconds"] = self.retry_after
        return value


@dataclass(frozen=True)
class Configuration:
    repositories: tuple[str, ...]
    metadata: dict[str, dict[str, str]]
    provider_token: str = field(repr=False)


def checked_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def text_value(value: Any, limit: int, secret: str = "") -> str:
    if not isinstance(value, str):
        return ""
    if secret:
        value = value.replace(secret, "[redacted]")
    return "".join(ch for ch in value if ch >= " " and ch != "\x7f")[:limit].strip()


def repository_name(value: str) -> str:
    value = value.strip()
    if not REPOSITORY_PATTERN.fullmatch(value) or value.split("/")[-1] in {".", ".."}:
        raise ConnectionError("configuration_invalid", "The GitHub repository configuration is invalid.", 503)
    return value


def read_configuration(environment: Mapping[str, str]) -> Configuration:
    raw = environment.get("MYPETHUMAN_GITHUB_REPOSITORIES", "")
    token = environment.get("MYPETHUMAN_GITHUB_TOKEN", "").strip()
    metadata_raw = environment.get("MYPETHUMAN_PROJECT_METADATA", "")
    if len(raw) > 4096 or len(token) > 4096 or any(ord(ch) < 33 or ord(ch) > 126 for ch in token):
        raise ConnectionError("configuration_invalid", "The GitHub connection configuration is invalid.", 503)
    names: dict[str, str] = {}
    for value in raw.split(","):
        if value.strip():
            name = repository_name(value)
            names.setdefault(name.lower(), name)
    if len(names) > MAX_REPOSITORIES or len(metadata_raw) > 65536:
        raise ConnectionError("configuration_invalid", "The GitHub connection configuration exceeds its limits.", 503)
    metadata: dict[str, dict[str, str]] = {}
    try:
        supplied = json.loads(metadata_raw) if metadata_raw.strip() else {}
        if not isinstance(supplied, dict):
            raise ValueError
        for key, value in supplied.items():
            if not isinstance(key, str) or key.lower() not in names or not isinstance(value, dict):
                raise ValueError
            kind = value.get("kind", "project")
            if kind not in {"project", "business", "research"}:
                raise ValueError
            for field_name in ("name", "purpose", "business_name"):
                if field_name in value and not isinstance(value[field_name], str):
                    raise ValueError
            metadata[key.lower()] = {
                "kind": kind,
                "name": text_value(value.get("name", ""), 160, token),
                "purpose": text_value(value.get("purpose", ""), 2000, token),
                "business_name": text_value(value.get("business_name", ""), 160, token),
            }
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ConnectionError("configuration_invalid", "The project metadata configuration is invalid.", 503) from None
    return Configuration(tuple(names.values()), metadata, token)


def retry_seconds(headers: Mapping[str, str]) -> int:
    for name, absolute in (("retry-after", False), ("x-ratelimit-reset", True)):
        value = headers.get(name, "")
        try:
            seconds = int(value) - int(time.time()) if absolute else int(value)
            if seconds > 0:
                return min(seconds, 86400)
        except (TypeError, ValueError):
            pass
    return 60


class GitHubReader:
    def __init__(self, configuration: Configuration, client_factory: Callable[..., Any] | None = None):
        self.configuration = configuration
        self.client_factory = client_factory or httpx.AsyncClient

    async def get_json(self, repository: str, *, issues: bool = False, page: int = 1) -> tuple[Any, bool]:
        global _rate_limit_until
        if repository.lower() not in {name.lower() for name in self.configuration.repositories}:
            raise ConnectionError("repository_not_configured", "That repository is not enabled for this connection.", 404)
        repository_name(repository)
        delay = int(_rate_limit_until - time.time())
        if delay > 0:
            raise ConnectionError("github_rate_limited", "GitHub asked this connection to wait before refreshing.", 503, delay + 1)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Luminax-MyPetHuman",
        }
        if self.configuration.provider_token:
            headers["Authorization"] = "Bearer " + self.configuration.provider_token
        # Both origin and path are constructed here. Never accept an upstream
        # URL, follow Link URLs, or forward incoming operator request headers.
        url = GITHUB_API + "/repos/" + repository + ("/issues" if issues else "")
        params = {"state": "open", "sort": "updated", "direction": "desc", "per_page": 100, "page": page} if issues else {}
        try:
            async with asyncio.timeout(12.0), self.client_factory(timeout=8.0, follow_redirects=False, trust_env=False, headers=headers) as client:
                async with client.stream("GET", url, params=params) as response:
                    status = response.status_code
                    if status == 429 or (status == 403 and (response.headers.get("x-ratelimit-remaining") == "0" or response.headers.get("retry-after"))):
                        delay = retry_seconds(response.headers)
                        _rate_limit_until = max(_rate_limit_until, time.time() + delay)
                        raise ConnectionError("github_rate_limited", "GitHub asked this connection to wait before refreshing.", 503, delay)
                    if status in {401, 403}:
                        raise ConnectionError("github_access_denied", "GitHub did not allow this repository read.")
                    if status == 404:
                        raise ConnectionError("github_repository_unavailable", "The repository is unavailable to this connection.")
                    if 300 <= status < 400:
                        raise ConnectionError("github_redirect_refused", "The repository address changed. Review its server configuration.")
                    if status != 200:
                        raise ConnectionError("github_unavailable", "GitHub could not complete this read.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise ConnectionError("github_response_too_large", "This GitHub response exceeded the connection's read limit.")
                        body.extend(chunk)
                    more = bool(re.search(r'rel\s*=\s*[\"\']next[\"\']', response.headers.get("link", "")))
                    return json.loads(body), more
        except (httpx.HTTPError, TimeoutError):
            raise ConnectionError("github_unavailable", "The GitHub connection could not complete this read.") from None
        except (ValueError, UnicodeDecodeError):
            raise ConnectionError("github_invalid_response", "GitHub returned an unreadable response.") from None

    async def project(self, repository: str) -> dict[str, Any]:
        data, _ = await self.get_json(repository)
        if not isinstance(data, dict) or str(data.get("full_name", "")).lower() != repository.lower():
            raise ConnectionError("github_invalid_response", "GitHub returned an unexpected repository.")
        metadata = self.configuration.metadata.get(repository.lower(), {})
        token = self.configuration.provider_token
        return {
            "repository": repository,
            "name": metadata.get("name") or text_value(data.get("name"), 160, token),
            "purpose": metadata.get("purpose") or text_value(data.get("description"), 2000, token),
            "kind": metadata.get("kind", "project"),
            "business_name": metadata.get("business_name", ""),
            "url": "https://github.com/" + repository,
            "default_branch": text_value(data.get("default_branch"), 100, token),
            "updated_at": text_value(data.get("updated_at"), 64, token),
            "private": bool(data.get("private")),
            "archived": bool(data.get("archived")),
            "status": "ok",
        }

    async def open_issues(self, repository: str, limit: int) -> tuple[list[dict[str, Any]], bool]:
        rows: list[dict[str, Any]] = []
        seen: set[int] = set()
        more = False
        for page in range(1, MAX_ISSUE_PAGES + 1):
            data, more = await self.get_json(repository, issues=True, page=page)
            if not isinstance(data, list):
                raise ConnectionError("github_invalid_response", "GitHub returned an unexpected issue list.")
            for item in data:
                if not isinstance(item, dict) or "pull_request" in item:
                    continue
                number = item.get("number")
                if not isinstance(number, int) or isinstance(number, bool) or number < 1 or number in seen:
                    continue
                title = text_value(item.get("title"), 512, self.configuration.provider_token)
                if not title or item.get("state") != "open":
                    continue
                seen.add(number)
                labels = item.get("labels") if isinstance(item.get("labels"), list) else []
                rows.append({
                    "number": number, "title": title, "state": "open",
                    "url": "https://github.com/" + repository + "/issues/" + str(number),
                    "updated_at": text_value(item.get("updated_at"), 64, self.configuration.provider_token),
                    "labels": [text_value(label.get("name"), 80, self.configuration.provider_token) for label in labels[:10] if isinstance(label, dict)],
                })
            if len(rows) >= limit or not more:
                return rows[:limit], more or len(rows) > limit
        return rows[:limit], more or len(rows) > limit


def create_mypethuman_router(
    *, runtime_is_enabled: Callable[[], bool], authorize_operator: Callable[[Request], None],
    environment: Mapping[str, str] | None = None, client_factory: Callable[..., Any] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/mypethuman", tags=["MyPetHuman"])

    def configuration(request: Request, response: Response) -> Configuration:
        response.headers.update(NO_STORE)
        if not runtime_is_enabled():
            raise HTTPException(status_code=503, detail={"code": "runtime_disabled", "message": "The local platform is disabled."}, headers=NO_STORE)
        try:
            authorize_operator(request)
        except HTTPException as error:
            error.headers = {**(error.headers or {}), **NO_STORE}
            raise
        try:
            return read_configuration(os.environ if environment is None else environment)
        except ConnectionError as error:
            raise HTTPException(status_code=error.status_code, detail=error.public(), headers=NO_STORE) from None

    @router.get("/status")
    async def status(request: Request, response: Response):
        config = configuration(request, response)
        return {
            "status": "ready" if config.repositories else "unconfigured", "source": "github",
            "runtime_enabled": True, "configured_repository_count": len(config.repositories),
            "repository_limit": MAX_REPOSITORIES, "authentication_mode": "server_token" if config.provider_token else "public",
            "credential_configured": bool(config.provider_token), "worker_running": False,
            "message": "Repositories are read when you open or refresh this view." if config.repositories else "Choose repositories in the server configuration.",
        }

    @router.get("/projects")
    async def projects(request: Request, response: Response):
        config = configuration(request, response)
        reader = GitHubReader(config, client_factory)
        gate = asyncio.Semaphore(4)

        async def read_project(repository: str) -> dict[str, Any]:
            async with gate:
                try:
                    return await reader.project(repository)
                except ConnectionError as error:
                    metadata = config.metadata.get(repository.lower(), {})
                    return {"repository": repository, "name": metadata.get("name") or repository,
                            "purpose": metadata.get("purpose", ""), "kind": metadata.get("kind", "project"),
                            "business_name": metadata.get("business_name", ""), "url": "https://github.com/" + repository,
                            "default_branch": "", "updated_at": "", "status": "unavailable", "error": error.public()}

        rows = await asyncio.gather(*(read_project(repository) for repository in config.repositories))
        result_status = "unconfigured" if not config.repositories else ("partial" if any(row["status"] != "ok" for row in rows) else "ok")
        return {"status": result_status, "source": "github", "checked_at": checked_at(), "projects": rows, "worker_running": False}

    @router.get("/projects/{owner}/{repo}")
    async def project_detail(request: Request, response: Response, owner: str, repo: str, limit: int = Query(default=20, ge=1, le=50)):
        config = configuration(request, response)
        repository = next((name for name in config.repositories if name.lower() == (owner + "/" + repo).lower()), "")
        if not repository:
            raise HTTPException(status_code=404, detail={"code": "repository_not_configured", "message": "That repository is not enabled for this connection."}, headers=NO_STORE)
        reader = GitHubReader(config, client_factory)
        try:
            project = await reader.project(repository)
            rows, more = await reader.open_issues(repository, limit)
        except ConnectionError as error:
            headers = {**NO_STORE}
            if error.retry_after:
                headers["Retry-After"] = str(error.retry_after)
            raise HTTPException(status_code=error.status_code, detail=error.public(), headers=headers) from None
        return {"status": "ok", "source": "github", "checked_at": checked_at(), "project": project,
                "issues": rows, "issue_count_returned": len(rows), "has_more": more,
                "scope": "open_issues", "worker_running": False}

    return router
