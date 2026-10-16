"""Offline boundary tests; no service imports, sockets, models, or databases."""
import ast
import copy
import json
import os
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from runtime_guard import RuntimeGuard, feature_enabled, require_feature, runtime_enabled, token_matches


PAYLOAD = Path(__file__).resolve().parents[1]
SYNTHETIC_TOKEN = "test-only-" + "x" * 40


def immediate(coroutine):
    """Run coroutines whose awaited test doubles complete immediately."""
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()
    raise AssertionError("test unexpectedly attempted asynchronous external work")


def owner_functions(service, names, namespace=None, filename="main.py"):
    tree = ast.parse((PAYLOAD / service / filename).read_text(encoding="utf-8"))
    selected = [copy.deepcopy(n) for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    if len(selected) != len(names):
        raise AssertionError(f"owner function missing in {service}: {names}")
    for node in selected:
        node.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = {"feature_enabled": feature_enabled, "runtime_enabled": runtime_enabled,
             "token_matches": token_matches, **(namespace or {})}
    exec(compile(module, str(PAYLOAD / service / filename), "exec"), scope)
    return scope


class Rejected(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def request(self, *, path="/run", method="POST", headers=(), events=None,
                require_auth=True, feature=None, kind="http"):
        original = list(events or [{"type": "http.request", "body": b""}])
        incoming = list(original)
        sent, delivered = [], []
        calls = []

        async def receive():
            if not incoming:
                raise AssertionError("unexpected additional body read")
            return incoming.pop(0)

        async def send(event):
            sent.append(event)

        async def app(scope, receive, send):
            calls.append(scope)
            for _ in original:
                delivered.append(await receive())
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        scope = {"type": kind, "path": path, "method": method, "headers": list(headers)}
        immediate(RuntimeGuard(app, service="synthetic", require_auth=require_auth,
                               required_feature=feature)(scope, receive, send))
        return sent, calls, delivered

    def enable(self):
        os.environ.update(LUMINAX_RUNTIME_ENABLED="true", CONTROL_TOKEN=SYNTHETIC_TOKEN)

    def test_only_explicit_true_enables_runtime(self):
        for value in ("", "false", "0", "1", "yes", "on"):
            with self.subTest(value=value), patch.dict(os.environ, {"LUMINAX_RUNTIME_ENABLED": value}):
                self.assertFalse(runtime_enabled())
        os.environ["LUMINAX_RUNTIME_ENABLED"] = " True "
        self.assertTrue(runtime_enabled())

    def test_disabled_operations_do_not_invoke_owner(self):
        sent, calls, _ = self.request()
        self.assertEqual(sent[0]["status"], 503)
        self.assertFalse(calls)

    def test_disabled_health_is_shallow(self):
        for path in ("/health", "/api/health"):
            sent, calls, _ = self.request(path=path, method="GET")
            self.assertEqual(sent[0]["status"], 200)
            self.assertIn(b"runtime_disabled", sent[1]["body"])
            self.assertFalse(calls)

    def test_post_health_is_not_public(self):
        self.enable()
        self.assertEqual(self.request(path="/health")[0][0]["status"], 401)

    def test_missing_or_short_operator_secret_fails_closed(self):
        os.environ["LUMINAX_RUNTIME_ENABLED"] = "true"
        for value in ("", "short"):
            os.environ["CONTROL_TOKEN"] = value
            sent, calls, _ = self.request()
            self.assertEqual(sent[0]["status"], 503)
            self.assertFalse(calls)

    def test_active_health_never_calls_heavy_handler(self):
        self.enable()
        sent, calls, _ = self.request(path="/health", method="GET")
        self.assertEqual(sent[0]["status"], 200)
        self.assertIn(b"enabled", sent[1]["body"])
        self.assertFalse(calls)

    def test_absent_wrong_and_weak_credentials_are_denied(self):
        self.enable()
        for headers in ([], [(b"authorization", b"Bearer wrong")], [(b"x-control-token", b"short")]):
            sent, calls, _ = self.request(headers=headers)
            self.assertEqual(sent[0]["status"], 401)
            self.assertNotIn(SYNTHETIC_TOKEN.encode(), sent[1]["body"])
            self.assertFalse(calls)

    def test_bearer_and_legacy_header_preserve_request(self):
        self.enable()
        for name, prefix in ((b"authorization", "Bearer "), (b"x-control-token", "")):
            events = [{"type": "http.request", "body": b"original request"}]
            sent, calls, delivered = self.request(headers=[(name, (prefix + SYNTHETIC_TOKEN).encode())], events=events)
            self.assertEqual(sent[0]["status"], 204)
            self.assertEqual(len(calls), 1)
            self.assertEqual(delivered, events)

    def test_chunked_legacy_json_token_replays_exact_events(self):
        self.enable()
        body = json.dumps({"token": SYNTHETIC_TOKEN, "goal": "synthetic"}).encode()
        events = [{"type": "http.request", "body": body[:13], "more_body": True},
                  {"type": "http.request", "body": body[13:]}]
        sent, calls, delivered = self.request(headers=[(b"content-type", b"application/json; charset=utf-8")], events=events)
        self.assertEqual(sent[0]["status"], 204)
        self.assertEqual(delivered, events)

    def test_malformed_nonobject_and_nonstring_json_tokens_denied(self):
        self.enable()
        for body in (b"{", b"[]", b'{"token":42}', b'{"token":null}',
                     b'{"token":"\\ud800"}', b"[" * 2000 + b"]" * 2000):
            sent, calls, _ = self.request(headers=[(b"content-type", b"application/json")], events=[{"type": "http.request", "body": body}])
            self.assertEqual(sent[0]["status"], 401)
            self.assertFalse(calls)

    def test_json_auth_body_limit(self):
        self.enable()
        sent, calls, _ = self.request(headers=[(b"content-type", b"application/json")], events=[{"type": "http.request", "body": b" " * 65537}])
        self.assertEqual(sent[0]["status"], 413)
        self.assertFalse(calls)

    def test_auth_body_cannot_consume_unbounded_empty_events(self):
        self.enable()
        events = [{"type": "http.request", "body": b"", "more_body": True} for _ in range(1025)]
        sent, calls, _ = self.request(headers=[(b"content-type", b"application/json")], events=events)
        self.assertEqual(sent[0]["status"], 413)
        self.assertFalse(calls)

    def test_header_auth_does_not_consume_or_limit_upload(self):
        self.enable()
        events = [{"type": "http.request", "body": b"x" * 70000}]
        sent, _, delivered = self.request(headers=[(b"authorization", ("Bearer " + SYNTHETIC_TOKEN).encode()), (b"content-type", b"application/json")], events=events)
        self.assertEqual(sent[0]["status"], 204)
        self.assertEqual(delivered, events)

    def test_disconnect_during_auth_rejects(self):
        self.enable()
        sent, calls, _ = self.request(headers=[(b"content-type", b"application/json")], events=[{"type": "http.disconnect"}])
        self.assertEqual(sent[0]["status"], 401)
        self.assertFalse(calls)

    def test_feature_requires_its_own_opt_in(self):
        self.enable()
        args = {"headers": [(b"authorization", ("Bearer " + SYNTHETIC_TOKEN).encode())], "feature": "LUMINAX_BROWSER_ACTIONS_ENABLED"}
        self.assertEqual(self.request(**args)[0][0]["status"], 503)
        os.environ["LUMINAX_BROWSER_ACTIONS_ENABLED"] = "true"
        self.assertEqual(self.request(**args)[0][0]["status"], 204)

    def test_operator_websocket_does_not_accept_unauthenticated_client(self):
        self.enable()
        sent, calls, _ = self.request(kind="websocket")
        self.assertEqual(sent, [{"type": "websocket.close", "code": 1008, "reason": "operator_auth_required"}])
        self.assertFalse(calls)

    def test_player_service_keeps_own_auth_path_after_runtime_gate(self):
        self.assertEqual(self.request(require_auth=False)[0][0]["status"], 503)
        os.environ["LUMINAX_RUNTIME_ENABLED"] = "true"
        self.assertEqual(self.request(require_auth=False)[0][0]["status"], 204)

    def test_comparison_is_unicode_safe_and_never_accepts_empty(self):
        self.assertFalse(token_matches("", ""))
        self.assertFalse(token_matches(None, SYNTHETIC_TOKEN))
        self.assertTrue(token_matches("é" * 40, "é" * 40))
        self.assertFalse(token_matches("é" * 40, "a" * 40))


class OwnerRegressionTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_account_empty_token_and_untrusted_proxy_header_are_rejected(self):
        ns = owner_functions("account-worker", {"request_has_trusted_web_control", "require_control_token"}, {
            "CONTROL_TOKEN": "", "TRUSTED_WEB_CONTROL_USER": "example-operator", "current_web_auth_user": lambda: "example-operator", "HTTPException": Rejected})
        with self.assertRaises(Rejected):
            ns["require_control_token"]("")
        ns["CONTROL_TOKEN"] = SYNTHETIC_TOKEN
        with self.assertRaises(Rejected):
            ns["require_control_token"]("incorrect")
        ns["require_control_token"](SYNTHETIC_TOKEN)
        os.environ["TRUST_PROXY_AUTH_HEADERS"] = "true"
        ns["require_control_token"]("")

    def test_admin_validators_fail_closed_and_preserve_active_lease(self):
        redis = Mock()
        redis.get.return_value = "true"
        ns = owner_functions("admin-api", {"validate", "validate_internal_service", "validate_watch_status", "validate_node_token"}, {
            "CONTROL_TOKEN": "", "request_has_trusted_web_control": lambda: False,
            "redis_client": redis, "HTTPException": Rejected})
        for name in ("validate", "validate_internal_service", "validate_watch_status", "validate_node_token"):
            with self.subTest(name=name), self.assertRaises(Rejected):
                ns[name]("")
        redis.get.assert_not_called()
        ns["CONTROL_TOKEN"] = SYNTHETIC_TOKEN
        for name in ("validate", "validate_internal_service", "validate_watch_status", "validate_node_token"):
            ns[name](SYNTHETIC_TOKEN)
        redis.get.return_value = "false"
        with self.assertRaises(Rejected):
            ns["validate"](SYNTHETIC_TOKEN)

    def test_upgrade_apply_empty_token_is_rejected_before_lease(self):
        lease = Mock(return_value=True)
        ns = owner_functions("lumi-upgrade-agent", {"validate_apply_control_or_403"}, {"CONTROL_TOKEN": "", "control_granted": lease, "HTTPException": Rejected})
        with self.assertRaises(Rejected):
            ns["validate_apply_control_or_403"]("")
        lease.assert_not_called()
        ns["CONTROL_TOKEN"] = SYNTHETIC_TOKEN
        ns["validate_apply_control_or_403"](SYNTHETIC_TOKEN)

    def test_disabled_brain_startup_never_creates_storage(self):
        storage = Mock()
        ns = owner_functions("luminax-brain", {"on_startup"}, {"ensure_storage": storage})
        ns["on_startup"]()
        storage.assert_not_called()
        os.environ["LUMINAX_RUNTIME_ENABLED"] = "true"
        ns["on_startup"]()
        storage.assert_called_once()

    def test_image_startup_requires_runtime_even_if_warmup_flag_is_set(self):
        scheduler = Mock()
        ns = owner_functions("image-agent", {"startup_event"}, {"HEALTH_WARMUP_ON_START": True, "asyncio": scheduler})
        immediate(ns["startup_event"]())
        scheduler.create_task.assert_not_called()

    def test_daemon_disabled_start_does_not_erase_logs_or_schedule_work(self):
        scheduler, filesystem, logger = Mock(), Mock(), Mock()
        ns = owner_functions("lumi-daemon", {"main"}, {"AUTOPILOT_ENABLED": False, "SELF_UPGRADE_ENABLED": False, "HIVE_DIRECTOR_ENABLED": False, "CONTROL_TOKEN": "", "asyncio": scheduler, "os": filesystem, "guardian_log": logger, "print": Mock()}, filename="daemon.py")
        for runtime in ("false", "true"):
            os.environ["LUMINAX_RUNTIME_ENABLED"] = runtime
            immediate(ns["main"]())
        filesystem.makedirs.assert_not_called()
        scheduler.create_task.assert_not_called()
        logger.assert_not_called()

    def test_world_and_allocator_disabled_construction_has_no_side_effect(self):
        for service, target, owner in (("underfoot-world", "world", "WorldState"), ("underfoot-match-allocator", "registry", "InstanceRegistry")):
            tree = ast.parse((PAYLOAD / service / "main.py").read_text())
            value = next(n.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == target)
            constructor = Mock()
            result = eval(compile(ast.Expression(value), "<owner construction>", "eval"), {"runtime_enabled": runtime_enabled, "feature_enabled": feature_enabled, owner: constructor})
            self.assertIsNone(result)
            constructor.assert_not_called()

    def test_optional_neweden_is_not_imported_unless_explicitly_enabled(self):
        tree = ast.parse((PAYLOAD / "underfoot-world/main.py").read_text())
        nodes = [copy.deepcopy(n) for n in tree.body if (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "neweden_router") or (isinstance(n, ast.If) and "UNDERFOOT_NEWEDEN_ENABLED" in ast.unparse(n.test))]
        code = compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), "<optional companion>", "exec")
        importer = Mock(side_effect=ImportError("synthetic missing companion"))
        ns = {"feature_enabled": feature_enabled, "__builtins__": {"__import__": importer, "ImportError": ImportError, "RuntimeError": RuntimeError}}
        exec(code, ns)
        self.assertIsNone(ns["neweden_router"])
        importer.assert_not_called()
        os.environ["UNDERFOOT_NEWEDEN_ENABLED"] = "true"
        with self.assertRaisesRegex(RuntimeError, "neweden_companion_source_required"):
            exec(code, ns)

    def test_python_voice_join_refuses_missing_signing_secret(self):
        ns = owner_functions("underfoot-voice-gateway", {"decode_voice_token"}, {"VOICE_JOIN_SECRET": "", "HTTPException": Rejected})
        with self.assertRaises(Rejected) as failure:
            ns["decode_voice_token"]("synthetic.token")
        self.assertEqual(failure.exception.status_code, 503)

    def test_world_never_signs_empty_secret_or_invents_turn_credentials(self):
        ns = owner_functions("underfoot-world", {"encode_voice_token", "build_turn_servers"}, {"VOICE_JOIN_SECRET": "", "VOICE_TURN_HOST": "", "VOICE_TURN_SHARED_SECRET": "", "HTTPException": Rejected})
        with self.assertRaises(Rejected):
            ns["encode_voice_token"]({})
        self.assertEqual(ns["build_turn_servers"]("example-player", "example-match"), [])

    def test_project_memory_write_requires_configured_strong_token(self):
        ns = owner_functions("underfoot-world", {"read_bearer_token", "require_project_memory_write_access"}, {"PROJECT_MEMORY_WRITE_TOKEN": "short", "HTTPException": Rejected})
        with self.assertRaises(Rejected):
            ns["require_project_memory_write_access"]("Bearer short", None)
        ns["PROJECT_MEMORY_WRITE_TOKEN"] = SYNTHETIC_TOKEN
        ns["require_project_memory_write_access"]("Bearer " + SYNTHETIC_TOKEN, None)

    def test_admin_shell_never_executes_without_explicit_host_action_flag(self):
        process = Mock()
        process.run.return_value = SimpleNamespace(returncode=0, stdout="synthetic", stderr="")
        ns = owner_functions("admin-api", {"sh"}, {"subprocess": process, "require_feature": require_feature})
        with self.assertRaises(Exception) as failure:
            ns["sh"]("synthetic command")
        self.assertIn("feature_disabled", str(failure.exception))
        process.run.assert_not_called()
        os.environ.update(LUMINAX_RUNTIME_ENABLED="true", LUMINAX_HOST_EXECUTION_ENABLED="true")
        self.assertEqual(ns["sh"]("synthetic command")["returncode"], 0)
        process.run.assert_called_once()

    def test_atlas_rebuild_uses_argv_and_explicit_apply(self):
        process = Mock()
        process.run.return_value = SimpleNamespace(returncode=0)
        builder = Mock()
        builder.exists.return_value = True
        ns = owner_functions("admin-api", {"rebuild_workspace_atlas"}, {"subprocess": process, "require_feature": Mock(), "WORKSPACE_ATLAS_BUILDER_PATH": builder, "sys": SimpleNamespace(executable="python-test"), "load_workspace_atlas": lambda: {"synthetic": True}, "HTTPException": Rejected})
        untrusted_key = "example'; unexpected-command"
        self.assertEqual(ns["rebuild_workspace_atlas"](untrusted_key), {"synthetic": True})
        args, kwargs = process.run.call_args
        self.assertEqual(args[0], ["python-test", str(builder), "--apply", "--project-key", untrusted_key])
        self.assertEqual(kwargs["timeout"], 120)
        self.assertNotIn("shell", kwargs)

    def test_upgrade_analysis_cannot_auto_apply_without_separate_opt_in(self):
        apply = AsyncMock(return_value={"status": "synthetic"})
        ns = owner_functions("lumi-upgrade-agent", {"analyze"}, {
            "SELF_UPGRADE_ENABLED": True, "service_file_hints": Mock(return_value=[]),
            "collect_file_contexts": Mock(return_value={}), "call_codex_json": AsyncMock(return_value=({}, {})),
            "build_error_analysis_messages": Mock(return_value=[]),
            "normalize_proposal": Mock(return_value={"proposal_id": "synthetic", "risk": "low", "confidence": 1, "auto_apply_eligible": True}),
            "persist_pending_proposal": Mock(), "agent_log": Mock(), "control_granted": Mock(return_value=True),
            "apply_proposal_internal": apply, "format_exc": str})
        req = SimpleNamespace(service="example", file_hints=[], trigger_kind="synthetic", error_excerpt="synthetic", log_path="", initiative_goal="", apply_if_safe=True)
        self.assertEqual(immediate(ns["analyze"](req))["status"], "ok")
        apply.assert_not_called()
        os.environ["LUMINAX_AUTO_APPLY_ENABLED"] = "true"
        self.assertEqual(immediate(ns["analyze"](req))["status"], "ok")
        apply.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
