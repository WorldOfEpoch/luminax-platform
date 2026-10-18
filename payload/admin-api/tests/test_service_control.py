"""Offline checks of the actual admin owner functions; no services are started."""
import ast
import asyncio
import contextvars
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


PAYLOAD = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PAYLOAD / "service-runtime"))
from runtime_guard import feature_enabled, runtime_enabled, token_matches


SOURCE = PAYLOAD / "admin-api" / "main.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
SYNTHETIC_TOKEN = "test-only-" + "x" * 40
FUNCTION_NAMES = {
    "valid_service_name", "current_web_auth_user", "request_has_trusted_web_control",
    "validate_watch_status", "_maintenance_mode_key", "_maintenance_policy",
    "load_maintenance_mode", "_service_control_text", "load_service_control_profiles",
    "services_control",
}


class Rejected(Exception):
    def __init__(self, status_code, detail, headers=None):
        self.status_code = status_code
        self.detail = detail
        self.headers = headers or {}


def owner_functions(namespace):
    selected = [copy.deepcopy(node) for node in TREE.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in FUNCTION_NAMES]
    if len(selected) != len(FUNCTION_NAMES):
        raise AssertionError("admin service control owner function missing")
    for node in selected:
        node.decorator_list = []
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        *selected,
    ], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace


class ServiceControlTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"LUMINAX_RUNTIME_ENABLED": "true"}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.web_user = contextvars.ContextVar("test_web_user", default="")
        self.redis = Mock()
        self.redis.get.return_value = "false"
        self.namespace = owner_functions({
            "HTTPException": Rejected,
            "CONTROL_TOKEN": SYNTHETIC_TOKEN,
            "TRUSTED_WEB_CONTROL_USER": "test-operator",
            "web_auth_user_ctx": self.web_user,
            "runtime_enabled": runtime_enabled,
            "feature_enabled": feature_enabled,
            "token_matches": token_matches,
            "redis_client": self.redis,
            "MAINTENANCE_MODE_PATH": self.root / "maintenance.json",
            "OLYMPUS_PROFILE_ROOT": self.root / "profiles",
            "SERVICE_WATCH_CATALOG": {
                "assistant-core": {"probe": "http"},
                "plugin-agent": {"probe": "docker"},
                "vision-agent": {"external": True, "probe": "http"},
                "docker": {"probe": "platform"},
                "nginx": {"probe": "docker"},
                "other-platform": {"probe": "platform"},
                "../escape": {"probe": "docker"},
            },
            "json": json,
            "time": time,
        })

    def request(self, headers=None):
        response = SimpleNamespace(headers={})
        payload = asyncio.run(self.namespace["services_control"](
            SimpleNamespace(headers=headers or {}), response))
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        return payload

    def operator_headers(self):
        return {"Authorization": "Bearer " + SYNTHETIC_TOKEN}

    def enable_trusted_operator(self):
        os.environ["TRUST_PROXY_AUTH_HEADERS"] = "true"
        self.web_user.set("test-operator")

    def write_profile(self, name="plugin-agent", **overrides):
        directory = self.root / "profiles" / "services"
        directory.mkdir(parents=True, exist_ok=True)
        data = {"key": name, "display_name": "Test service", "role": "Test role", **overrides}
        path = directory / f"{name}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_disabled_runtime_rejects_before_auth_or_storage(self):
        os.environ["LUMINAX_RUNTIME_ENABLED"] = "false"
        self.namespace["load_service_control_profiles"] = Mock(side_effect=AssertionError("unexpected profile read"))
        with self.assertRaises(Rejected) as result:
            self.request(self.operator_headers())
        self.assertEqual(result.exception.status_code, 503)
        self.assertEqual(result.exception.detail, "runtime_disabled")
        self.redis.get.assert_not_called()

    def test_absent_wrong_and_body_only_credentials_are_denied(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"token": SYNTHETIC_TOKEN}):
            with self.subTest(headers=list(headers)), self.assertRaises(Rejected) as result:
                self.request(headers)
            self.assertEqual(result.exception.status_code, 403)
            self.assertEqual(result.exception.detail, "operator_auth_required")
            self.assertEqual(result.exception.headers.get("Cache-Control"), "no-store")
        self.redis.get.assert_not_called()

    def test_spoofed_web_identity_without_opt_in_is_denied(self):
        self.web_user.set("test-operator")
        with self.assertRaises(Rejected):
            self.request({"X-Web-Auth-User": "test-operator"})
        os.environ["TRUST_PROXY_AUTH_HEADERS"] = "true"
        self.web_user.set("another-user")
        with self.assertRaises(Rejected):
            self.request()

    def test_header_credentials_read_without_granting_control(self):
        for headers in (self.operator_headers(), {"X-Control-Token": SYNTHETIC_TOKEN}):
            payload = self.request(headers)
            self.assertTrue(payload["operator_authenticated"])
            self.assertEqual(payload["authentication_mode"], "operator_token")
            self.assertFalse(payload["control_granted"])
            self.assertFalse(payload["actions_enabled"])
            self.assertEqual(payload["control_state"], "not_granted")
        self.redis.set.assert_not_called()
        self.redis.setex.assert_not_called()
        self.assertFalse((self.root / "maintenance.json").exists())
        self.assertFalse((self.root / "profiles").exists())

    def test_invalid_bearer_does_not_fall_back_to_valid_header(self):
        with self.assertRaises(Rejected):
            self.request({"Authorization": "Bearer invalid", "X-Control-Token": SYNTHETIC_TOKEN})

    def test_basic_ingress_auth_preserves_explicit_operator_header(self):
        payload = self.request({"Authorization": "Basic ingress-value", "X-Control-Token": SYNTHETIC_TOKEN})
        self.assertEqual(payload["authentication_mode"], "operator_token")

    def test_short_configured_token_does_not_authenticate(self):
        self.namespace["CONTROL_TOKEN"] = "short"
        with self.assertRaises(Rejected):
            self.request({"X-Control-Token": "short"})

    def test_trusted_ingress_matches_existing_owner_without_redis_lease(self):
        self.enable_trusted_operator()
        os.environ["LUMINAX_HOST_EXECUTION_ENABLED"] = "true"
        self.redis.get.side_effect = AssertionError("trusted ingress must not inspect lease")
        payload = self.request()
        self.assertEqual(payload["authentication_mode"], "trusted_ingress")
        self.assertTrue(payload["control_granted"])
        self.assertTrue(payload["actions_enabled"])
        self.redis.get.assert_not_called()

    def test_host_feature_is_required_even_with_control_grant(self):
        self.enable_trusted_operator()
        payload = self.request()
        self.assertFalse(payload["host_execution_enabled"])
        self.assertFalse(payload["actions_enabled"])
        self.assertTrue(all(not row["allowed_actions"] for row in payload["allowed_services"]))

    def test_live_lease_can_enable_existing_operator_controls(self):
        os.environ["LUMINAX_HOST_EXECUTION_ENABLED"] = "true"
        self.redis.get.return_value = "true"
        payload = self.request(self.operator_headers())
        self.assertTrue(payload["actions_enabled"])
        self.redis.get.assert_called_once_with("luminax_control_granted")
        self.assertEqual({row["name"] for row in payload["allowed_services"]}, {"assistant-core", "plugin-agent"})
        self.assertTrue(all(row["allowed_actions"] == ["start", "stop", "restart"] for row in payload["allowed_services"]))

    def test_lease_lookup_failure_is_unavailable_and_never_leaked(self):
        os.environ["LUMINAX_HOST_EXECUTION_ENABLED"] = "true"
        self.redis.get.side_effect = RuntimeError("secret detail " + SYNTHETIC_TOKEN)
        payload = self.request(self.operator_headers())
        self.assertEqual(payload["control_state"], "unavailable")
        self.assertFalse(payload["control_granted"])
        self.assertFalse(payload["actions_enabled"])
        self.assertNotIn(SYNTHETIC_TOKEN, json.dumps(payload))
        self.assertNotIn("secret detail", json.dumps(payload))

    def test_diagnose_and_freeze_disable_actions_using_existing_policy(self):
        self.enable_trusted_operator()
        os.environ["LUMINAX_HOST_EXECUTION_ENABLED"] = "true"
        for mode in ("normal", "repair_safe", "diagnose", "freeze"):
            with self.subTest(mode=mode):
                self.namespace["MAINTENANCE_MODE_PATH"].write_text(json.dumps({"mode": mode}))
                payload = self.request()
                self.assertEqual(payload["maintenance"]["mode"], mode)
                self.assertEqual(payload["actions_enabled"], mode in {"normal", "repair_safe"})

    def test_profile_catalog_contains_only_declared_display_fields(self):
        self.write_profile(
            capabilities=["test.read", "test.route"],
            validation_routes={"core": "/api/test/status", "admin": "/test/status"},
            private_config={"token": "must-not-be-returned"},
            reads=["private/runtime/config"],
            project_overrides={"private": {"owner": "not-for-display"}},
        )
        payload = self.request(self.operator_headers())
        self.assertEqual(payload["profiles_status"], "ok")
        row = payload["profiles"][0]
        self.assertEqual(row["availability"], "declared")
        self.assertEqual(row["capabilities"], ["test.read", "test.route"])
        self.assertEqual(row["routes"], [{"owner": "core", "path": "/api/test/status"}, {"owner": "admin", "path": "/test/status"}])
        for forbidden in ("private_config", "profile_path", "reads", "project_overrides", "must-not-be-returned", "not-for-display"):
            self.assertNotIn(forbidden, json.dumps(payload))

    def test_routes_cannot_return_urls_queries_or_control_credentials(self):
        self.write_profile(
            role="value " + SYNTHETIC_TOKEN,
            capabilities=["read", SYNTHETIC_TOKEN, {"ignored": "object"}],
            validation_routes={
                "valid": "/api/test/{key}", "url": "https://invalid.example/path",
                "relative": "//invalid.example/path", "query": "/test?secret=value",
                "fragment": "/test#fragment", "backslash": "/test\\path",
            },
        )
        payload = self.request(self.operator_headers())
        self.assertNotIn(SYNTHETIC_TOKEN, json.dumps(payload))
        self.assertEqual(payload["profiles"][0]["routes"], [{"owner": "valid", "path": "/api/test/{key}"}])

    def test_invalid_oversized_and_linked_profiles_are_partial_without_writes(self):
        valid = self.write_profile()
        directory = valid.parent
        (directory / "broken.json").write_text("{")
        (directory / "oversized.json").write_bytes(b" " * 65537)
        (directory / "linked.json").symlink_to(valid)
        before = {path.name: path.stat().st_size for path in directory.iterdir()}
        payload = self.request(self.operator_headers())
        self.assertEqual(payload["profiles_status"], "partial")
        self.assertEqual(len(payload["profiles"]), 1)
        self.assertEqual(before, {path.name: path.stat().st_size for path in directory.iterdir()})

    def test_profile_display_fields_and_catalog_are_bounded(self):
        self.write_profile(display_name="n" * 1000, role="r" * 1000, capabilities=["c" * 200] * 60)
        payload = self.request(self.operator_headers())
        row = payload["profiles"][0]
        self.assertEqual(len(row["display_name"]), 120)
        self.assertEqual(len(row["role"]), 400)
        self.assertEqual(len(row["capabilities"]), 40)
        self.assertEqual(len(row["capabilities"][0]), 120)
        self.assertEqual(payload["catalog_scope"], "configured_services")
        self.assertNotIn("worker_running", payload)


if __name__ == "__main__":
    unittest.main()
