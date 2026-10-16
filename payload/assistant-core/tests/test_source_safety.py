"""Offline regression tests for the public source's existing safety owners.

The platform modules have optional external services. Load only the selected
function/statement ASTs so these tests cannot import or start those services.
All tokens, identities, requests, and service objects below are synthetic.
"""

import ast
import copy
import hmac
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace


PAYLOAD = Path(__file__).resolve().parents[2]
CORE = ast.parse((PAYLOAD / "assistant-core" / "main.py").read_text(encoding="utf-8"))
BOT = ast.parse((PAYLOAD / "discord-bot" / "bot.py").read_text(encoding="utf-8"))
OPS = ast.parse((PAYLOAD / "discord-ops-bot" / "main.py").read_text(encoding="utf-8"))
PROVISION = ast.parse((PAYLOAD / "discord-bot" / "provision_papr.py").read_text(encoding="utf-8"))
TOKEN = "synthetic-public-source-test-" + "x" * 32


class HttpError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code


class FakeRedis:
    def __init__(self, granted=False):
        self.granted = granted
        self.writes = []

    def get(self, _key):
        return "true" if self.granted else None

    def set(self, *args, **kwargs):
        self.writes.append((args, kwargs))


def execute(nodes, namespace):
    selected = copy.deepcopy(list(nodes))
    for node in selected:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(module, "<selected-public-source>", "exec"), namespace)


def functions(tree, names, namespace):
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    if {node.name for node in selected} != set(names):
        raise AssertionError("Expected source owner is missing")
    execute(selected, namespace)


def inline(coroutine):
    """Drive a fully stubbed coroutine without an event loop or sockets."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("Unexpected asynchronous I/O in an offline test")


def core_namespace(token=TOKEN, granted=False):
    namespace = {
        "CONTROL_TOKEN": token,
        "TRUST_PROXY_AUTH_HEADERS": False,
        "TRUSTED_WEB_CONTROL_USER": "",
        "current_web_auth_user": lambda: "synthetic-operator",
        "redis_client": FakeRedis(granted),
        "hmac": hmac,
        "HTTPException": HttpError,
        "GrantRequest": SimpleNamespace,
    }
    functions(CORE, {
        "request_has_trusted_web_control", "request_has_connector_token",
        "request_has_operator_control", "grant_control",
    }, namespace)
    return namespace


class CredentialTests(unittest.TestCase):
    def test_unconfigured_short_wrong_and_unicode_tokens_fail_closed(self):
        for configured, supplied in [("", ""), ("short", "short"), (TOKEN, ""), (TOKEN, "wrong"), (TOKEN, "\u2603")]:
            with self.subTest(configured_length=len(configured), supplied_length=len(supplied)):
                namespace = core_namespace(configured, granted=True)
                self.assertFalse(namespace["request_has_connector_token"](supplied))
                self.assertFalse(namespace["request_has_operator_control"](supplied))

    def test_operator_requires_both_valid_token_and_active_control_lease(self):
        namespace = core_namespace()
        self.assertTrue(namespace["request_has_connector_token"](TOKEN))
        self.assertFalse(namespace["request_has_operator_control"](TOKEN))
        namespace["redis_client"].granted = True
        self.assertTrue(namespace["request_has_operator_control"](TOKEN))

    def test_proxy_identity_requires_explicit_opt_in_and_configured_identity(self):
        namespace = core_namespace("")
        namespace["TRUSTED_WEB_CONTROL_USER"] = "synthetic-operator"
        self.assertFalse(namespace["request_has_operator_control"](""))
        namespace["TRUST_PROXY_AUTH_HEADERS"] = True
        self.assertTrue(namespace["request_has_operator_control"](""))
        namespace["TRUSTED_WEB_CONTROL_USER"] = "another-synthetic-operator"
        self.assertFalse(namespace["request_has_operator_control"](""))

    def test_control_grant_rejects_empty_configuration_without_writing(self):
        namespace = core_namespace("")
        with self.assertRaises(HttpError) as failure:
            inline(namespace["grant_control"](SimpleNamespace(token="")))
        self.assertEqual(failure.exception.status_code, 403)
        self.assertEqual(namespace["redis_client"].writes, [])

    def test_valid_control_grant_keeps_bounded_lease(self):
        namespace = core_namespace()
        result = inline(namespace["grant_control"](SimpleNamespace(token=TOKEN)))
        self.assertEqual(result["seconds"], 600)
        self.assertEqual(namespace["redis_client"].writes, [(("luminax_control_granted", "true"), {"ex": 600})])

    def test_ops_relay_never_supplies_its_token_for_an_unauthenticated_request(self):
        calls = []

        async def post(path, payload):
            calls.append((path, payload))
            return {"approved": True}

        namespace = {"CONTROL_TOKEN": TOKEN, "hmac": hmac, "HTTPException": HttpError,
                     "RevampInjectionRequest": SimpleNamespace, "Any": object, "_post_core_json": post}
        functions(OPS, {"_review_discord_action"}, namespace)
        request = SimpleNamespace(token="", discord_user_id="synthetic-user")
        with self.assertRaises(HttpError) as failure:
            inline(namespace["_review_discord_action"](request, "implement"))
        self.assertEqual(failure.exception.status_code, 403)
        self.assertEqual(calls, [])

    def test_authenticated_ops_review_preserves_the_actor_and_requested_action(self):
        calls = []

        async def post(path, payload):
            calls.append((path, payload))
            return {"approved": False}

        namespace = {"CONTROL_TOKEN": TOKEN, "hmac": hmac, "HTTPException": HttpError,
                     "RevampInjectionRequest": SimpleNamespace, "Any": object, "_post_core_json": post}
        functions(OPS, {"_review_discord_action"}, namespace)
        request = SimpleNamespace(token=TOKEN, discord_user_id="synthetic-user", prompt="Synthetic review",
                                  guild_id="", channel_id="", project_key="sample", blueprint_key="sample")
        result = inline(namespace["_review_discord_action"](request, "implement"))
        self.assertFalse(result["approved"])
        self.assertEqual(calls[0][0], "/discord/action/review")
        self.assertEqual(calls[0][1]["discord_user_id"], "synthetic-user")
        self.assertEqual(calls[0][1]["action_kind"], "implement")

    def test_missing_ops_endpoint_stops_before_http_client_creation(self):
        namespace = {"CORE_URL": "", "HTTPException": HttpError, "Any": object}
        functions(OPS, {"_post_core_json"}, namespace)
        with self.assertRaises(HttpError) as failure:
            inline(namespace["_post_core_json"]("/synthetic", {}))
        self.assertEqual(failure.exception.status_code, 503)


class InactiveDefaultTests(unittest.TestCase):
    def test_runtime_and_discord_defaults_are_off(self):
        defaults = SimpleNamespace(getenv=lambda _key, default=None: default)
        namespace = {"os": defaults}
        expected = {"LUMINAX_RUNTIME_ENABLED": False, "TRUST_PROXY_AUTH_HEADERS": False,
                    "DISCORD_RUNTIME_ROLE": "disabled", "DISCORD_CONTINUOUS_STEWARD_ENABLED": False}
        for tree in (CORE, BOT):
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in expected for target in node.targets):
                    execute([node], namespace)
        for key, value in expected.items():
            self.assertEqual(namespace[key], value)

    def test_disabled_core_skips_external_initialization_and_background_work(self):
        namespace = {"LUMINAX_RUNTIME_ENABLED": False, "CONTROL_TOKEN": ""}
        exercised = set()
        for node in CORE.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            names = {ast.unparse(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)}
            relevant = names & {"QdrantClient", "p.mkdir", "qdrant.create_collection", "scheduler.start", "ensure_hive_employee_profiles"}
            if relevant:
                execute([node], namespace)
                exercised.update(relevant)
        self.assertEqual(exercised, {"QdrantClient", "p.mkdir", "qdrant.create_collection", "scheduler.start", "ensure_hive_employee_profiles"})
        self.assertIsNone(namespace["qdrant"])

    def test_disabled_discord_main_exits_without_connecting_or_sleeping(self):
        entry = next(node for node in BOT.body if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                     and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")
        namespace = {"__name__": "__main__", "os": SimpleNamespace(getenv=lambda _key, default=None: default),
                     "DISCORD_RUNTIME_ROLE": "disabled", "sys": sys}
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            execute([entry], namespace)
        self.assertEqual(stopped.exception.code, 0)

    def test_provisioning_needs_explicit_opt_in_and_target(self):
        namespace = {"os": SimpleNamespace(getenv=lambda _key, default=None: default), "TARGET_GUILD_ID": 0,
                     "json": json}
        functions(PROVISION, {"main"}, namespace)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(inline(namespace["main"]()), 1)


if __name__ == "__main__":
    unittest.main()
