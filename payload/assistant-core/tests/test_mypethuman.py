"""Offline tests of the actual MyPetHuman transport and existing auth owner.

Only FastAPI's route registration and HTTPX's network boundary are substituted.
The production reader, configuration, pagination, errors and route functions run
unchanged. No sockets, account, live provider or core startup are needed.
"""

import ast
import asyncio
import hmac
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


CORE_PATH = Path(__file__).resolve().parents[1]
OPERATOR_TOKEN = "synthetic-operator-" + "x" * 40
PROVIDER_TOKEN = "synthetic-provider-" + "y" * 40
REPOSITORY = "sample-owner/sample-project"


class HttpError(Exception):
    def __init__(self, status_code, detail, headers=None):
        super().__init__(str(detail))
        self.status_code, self.detail, self.headers = status_code, detail, headers


class Router:
    def __init__(self, **_kwargs):
        self.routes = {}

    def get(self, path):
        def register(function):
            self.routes[path] = function
            return function
        return register


class FakeNetworkError(Exception):
    pass


def forbidden_network(**_kwargs):
    raise AssertionError("Unmocked external HTTP client")


def load_owner():
    name = "mypethuman_offline_owner"
    spec = importlib.util.spec_from_file_location(name, CORE_PATH / "mypethuman.py")
    module = importlib.util.module_from_spec(spec)
    fastapi = types.SimpleNamespace(APIRouter=Router, HTTPException=HttpError,
                                    Request=object, Response=object, Query=lambda default, **_kw: default)
    httpx = types.SimpleNamespace(AsyncClient=forbidden_network, HTTPError=FakeNetworkError)
    with patch.dict(sys.modules, {name: module, "fastapi": fastapi, "httpx": httpx}):
        spec.loader.exec_module(module)
    return module


OWNER = load_owner()


def existing_authorizer(*, configured=OPERATOR_TOKEN, proxy=False, identity=""):
    names = {"request_has_trusted_web_control", "request_has_connector_token", "require_connector_token", "require_mypethuman_operator"}
    tree = ast.parse((CORE_PATH / "main.py").read_text(encoding="utf-8"))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in selected} != names:
        raise AssertionError("Missing existing authorization owner")
    namespace = {"CONTROL_TOKEN": configured, "TRUST_PROXY_AUTH_HEADERS": proxy,
                 "TRUSTED_WEB_CONTROL_USER": "sample-operator", "current_web_auth_user": lambda: identity,
                 "hmac": hmac, "HTTPException": HttpError, "Request": object}
    exec(compile(ast.Module(body=selected, type_ignores=[]), "<existing-auth-owner>", "exec"), namespace)
    return namespace["require_mypethuman_operator"]


class Response:
    def __init__(self, data=None, *, status=200, headers=None, raw=None, wait=None):
        self.status_code = status
        self.headers = headers or {}
        self.raw = raw if raw is not None else json.dumps(data).encode()
        self.closed = False
        self.wait = wait

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True

    async def aiter_bytes(self):
        if self.wait:
            self.wait[0].set()
            await self.wait[1].wait()
        for start in range(0, len(self.raw), 8192):
            yield self.raw[start:start + 8192]


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.created = []

    def __call__(self, **options):
        self.created.append(options)
        transport = self

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            def stream(self, method, url, *, params):
                transport.calls.append((method, url, params, options))
                if not transport.responses:
                    raise AssertionError("Unexpected extra upstream request")
                response = transport.responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response

        return Client()


def project_response(repository=REPOSITORY, **extra):
    return Response({"full_name": repository, "name": repository.split("/")[1],
                     "description": "A real provider description in this synthetic response.",
                     "default_branch": "main", "updated_at": "2026-01-01T00:00:00Z", **extra})


def issue(number, **extra):
    return {"number": number, "title": "Provider issue " + str(number), "state": "open",
            "updated_at": "2026-01-01T00:00:00Z", "labels": [{"name": "bug"}], **extra}


def request(headers=None):
    return types.SimpleNamespace(headers=headers if headers is not None else {"Authorization": "Bearer " + OPERATOR_TOKEN})


def response():
    return types.SimpleNamespace(headers={})


def router(transport=None, *, environment=None, enabled=True, authorize=None):
    environment = {"MYPETHUMAN_GITHUB_REPOSITORIES": REPOSITORY} if environment is None else environment
    return OWNER.create_mypethuman_router(runtime_is_enabled=lambda: enabled,
                                         authorize_operator=authorize or existing_authorizer(),
                                         environment=environment, client_factory=transport or Transport()).routes


class ConfigurationTests(unittest.TestCase):
    def test_invalid_paths_urls_and_allowlist_size_fail_closed(self):
        for value in ("https://github.com/owner/repo", "owner/../repo", "owner/..", "owner/repo?token=x",
                      "owner/repo#fragment", ",".join("owner/r" + str(i) for i in range(21))):
            with self.subTest(value=value), self.assertRaises(OWNER.ConnectionError):
                OWNER.read_configuration({"MYPETHUMAN_GITHUB_REPOSITORIES": value})

    def test_metadata_is_repo_scoped_and_provider_secret_is_not_represented(self):
        env = {"MYPETHUMAN_GITHUB_REPOSITORIES": REPOSITORY, "MYPETHUMAN_GITHUB_TOKEN": PROVIDER_TOKEN,
               "MYPETHUMAN_PROJECT_METADATA": json.dumps({REPOSITORY: {"kind": "business", "purpose": PROVIDER_TOKEN}})}
        config = OWNER.read_configuration(env)
        self.assertNotIn(PROVIDER_TOKEN, repr(config))
        self.assertEqual(config.metadata[REPOSITORY]["purpose"], "[redacted]")
        env["MYPETHUMAN_PROJECT_METADATA"] = json.dumps({"other/repo": {"purpose": "other"}})
        with self.assertRaises(OWNER.ConnectionError):
            OWNER.read_configuration(env)

    def test_provider_header_injection_is_rejected(self):
        for token in ("value\r\nAnother-Header: secret", "embedded space", "\u2603"):
            with self.assertRaises(OWNER.ConnectionError):
                OWNER.read_configuration({"MYPETHUMAN_GITHUB_TOKEN": token})


class RouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        OWNER._rate_limit_until = 0

    async def test_disabled_runtime_does_not_authorize_or_construct_http_client(self):
        transport = Transport()
        def forbidden(_request):
            raise AssertionError("Authorization should not run")
        routes = router(transport, enabled=False, authorize=forbidden)
        with self.assertRaises(HttpError) as result:
            await routes["/projects"](request(), response())
        self.assertEqual(result.exception.status_code, 503)
        self.assertEqual(result.exception.detail["code"], "runtime_disabled")
        self.assertEqual(transport.created, [])

    async def test_unauthorized_request_stops_before_configuration_or_provider(self):
        transport = Transport()
        routes = router(transport, environment={"MYPETHUMAN_GITHUB_REPOSITORIES": "invalid"})
        with self.assertRaises(HttpError) as result:
            await routes["/projects"](request({}), response())
        self.assertEqual(result.exception.status_code, 403)
        self.assertEqual(result.exception.headers["Cache-Control"], "no-store")
        self.assertEqual(transport.created, [])

    async def test_spoofed_proxy_identity_and_query_token_do_not_authenticate(self):
        authorize = existing_authorizer(proxy=False, identity="sample-operator")
        for headers in ({"X-Web-Auth-User": "sample-operator"}, {"token": OPERATOR_TOKEN}, {"Authorization": "Basic " + OPERATOR_TOKEN}):
            with self.subTest(headers=list(headers)), self.assertRaises(HttpError):
                await router(authorize=authorize)["/status"](request(headers), response())

    async def test_existing_trusted_proxy_or_connector_header_work_without_provider_call(self):
        transport = Transport()
        for authorizer, headers in ((existing_authorizer(proxy=True, identity="sample-operator"), {}),
                                    (existing_authorizer(), {"X-Control-Token": OPERATOR_TOKEN})):
            result = await router(transport, authorize=authorizer)["/status"](request(headers), response())
            self.assertEqual(result["status"], "ready")
            self.assertFalse(result["worker_running"])
        self.assertEqual(transport.created, [])

    async def test_unconfigured_is_explicit_and_does_not_fabricate_projects(self):
        transport = Transport()
        routes = router(transport, environment={})
        result = await routes["/projects"](request(), response())
        self.assertEqual(result["status"], "unconfigured")
        self.assertEqual(result["projects"], [])
        self.assertEqual(transport.created, [])

    async def test_provider_credentials_and_operator_credentials_are_separate(self):
        transport = Transport(project_response())
        env = {"MYPETHUMAN_GITHUB_REPOSITORIES": REPOSITORY, "MYPETHUMAN_GITHUB_TOKEN": PROVIDER_TOKEN,
               "MYPETHUMAN_PROJECT_METADATA": json.dumps({REPOSITORY: {"name": "Project name", "kind": "business", "business_name": "Business", "purpose": "Purpose"}})}
        resp = response()
        result = await router(transport, environment=env)["/projects"](request(), resp)
        self.assertEqual(result["projects"][0]["purpose"], "Purpose")
        self.assertEqual(result["projects"][0]["business_name"], "Business")
        self.assertEqual(result["projects"][0]["status"], "ok")
        options = transport.calls[0][3]
        self.assertEqual(options["headers"]["Authorization"], "Bearer " + PROVIDER_TOKEN)
        self.assertNotIn(OPERATOR_TOKEN, repr(options))
        self.assertNotIn("X-Web-Auth-User", options["headers"])
        self.assertFalse(options["trust_env"])
        self.assertFalse(options["follow_redirects"])
        self.assertEqual(resp.headers["Cache-Control"], "no-store")

    async def test_unknown_repo_does_not_contact_github(self):
        transport = Transport()
        with self.assertRaises(HttpError) as result:
            await router(transport)["/projects/{owner}/{repo}"](request(), response(), "other", "repo", 20)
        self.assertEqual(result.exception.status_code, 404)
        self.assertEqual(transport.calls, [])

    async def test_pagination_uses_numeric_fixed_host_pages_and_filters_prs(self):
        malicious_link = '<https://elsewhere.invalid/private>; rel="next"'
        transport = Transport(project_response(), Response([issue(1, pull_request={})], headers={"link": malicious_link}),
                              Response([issue(2), issue(2), issue(3)]))
        result = await router(transport)["/projects/{owner}/{repo}"](request(), response(), "sample-owner", "sample-project", 1)
        self.assertEqual([row["number"] for row in result["issues"]], [2])
        self.assertTrue(result["has_more"])
        self.assertEqual(result["issue_count_returned"], 1)
        self.assertEqual(transport.calls[-1][2]["page"], 2)
        self.assertTrue(all(call[1].startswith("https://api.github.com/repos/" + REPOSITORY) for call in transport.calls))
        self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    async def test_issue_page_cap_reports_incomplete_scan(self):
        pages = [Response([issue(n, pull_request={})], headers={"link": '<ignored>; rel="next"'}) for n in range(3)]
        transport = Transport(project_response(), *pages)
        result = await router(transport)["/projects/{owner}/{repo}"](request(), response(), "sample-owner", "sample-project", 20)
        self.assertEqual(result["issues"], [])
        self.assertTrue(result["has_more"])
        self.assertEqual(len(transport.calls), 4)

    async def test_redirect_is_refused_without_reading_body_or_following_location(self):
        transport = Transport(Response(raw=PROVIDER_TOKEN.encode(), status=302, headers={"location": "https://elsewhere.invalid/"}))
        result = await router(transport)["/projects"](request(), response())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["projects"][0]["error"]["code"], "github_redirect_refused")
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn(PROVIDER_TOKEN, json.dumps(result))

    async def test_provider_failures_cannot_be_reported_as_empty_success(self):
        transport = Transport(project_response(), Response(status=404, raw=b"sensitive provider body"))
        with self.assertRaises(HttpError) as result:
            await router(transport)["/projects/{owner}/{repo}"](request(), response(), "sample-owner", "sample-project", 20)
        self.assertEqual(result.exception.status_code, 502)
        self.assertNotIn("sensitive provider body", str(result.exception))

    async def test_network_exception_and_invalid_json_are_sanitized(self):
        for upstream in (FakeNetworkError(PROVIDER_TOKEN), Response(raw=PROVIDER_TOKEN.encode())):
            result = await router(Transport(upstream))["/projects"](request(), response())
            self.assertEqual(result["status"], "partial")
            self.assertNotIn(PROVIDER_TOKEN, json.dumps(result))

    async def test_response_size_limit_closes_stream_and_returns_failure(self):
        large = Response(raw=b"x" * (OWNER.MAX_RESPONSE_BYTES + 1))
        result = await router(Transport(large))["/projects"](request(), response())
        self.assertEqual(result["projects"][0]["error"]["code"], "github_response_too_large")
        self.assertTrue(large.closed)

    async def test_rate_limit_is_reported_and_next_request_waits_without_upstream(self):
        transport = Transport(Response(status=429, headers={"retry-after": "90"}))
        routes = router(transport)
        first = await routes["/projects"](request(), response())
        second = await routes["/projects"](request(), response())
        self.assertEqual(first["projects"][0]["error"]["code"], "github_rate_limited")
        self.assertGreater(second["projects"][0]["error"]["retry_after_seconds"], 0)
        self.assertEqual(len(transport.calls), 1)

    async def test_cancellation_closes_the_inflight_stream(self):
        started, unblock = asyncio.Event(), asyncio.Event()
        upstream = Response({}, wait=(started, unblock))
        task = asyncio.create_task(router(Transport(upstream))["/projects"](request(), response()))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(upstream.closed)


if __name__ == "__main__":
    unittest.main()
