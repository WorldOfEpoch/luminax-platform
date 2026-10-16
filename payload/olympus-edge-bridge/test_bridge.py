"""Offline checks: no listeners, services, credentials, or model requests."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx


SOURCE = Path(__file__).with_name("main.py")


def load_bridge(**environment):
    spec = importlib.util.spec_from_file_location("bridge_under_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, environment, clear=True):
        spec.loader.exec_module(module)
    return module


class ConfigurationTests(unittest.TestCase):
    def test_service_urls_are_explicit_and_admin_health_routes_are_derived(self):
        bridge = load_bridge(ADMIN_API_URL="http://admin.invalid:18040/", OLLAMA_HOST="http://model.invalid:11434/", VOICE_HEALTH_URL="")
        self.assertEqual(bridge.ADMIN_API_URL, "http://admin.invalid:18040")
        self.assertEqual(bridge.OLLAMA_HOST, "http://model.invalid:11434")
        self.assertEqual(bridge.VOICE_HEALTH_URL, "http://admin.invalid:18040/voice/health?skip_edge_bridge=1&edge_safe=1")
        self.assertEqual(bridge.EDGE_VOICE_URL, "")
        self.assertFalse(bridge.EDGE_BRIDGE_HEARTBEAT_ENABLED)

    def test_invalid_urls_report_only_configuration_name(self):
        secret = "synthetic-private-value"
        invalid = [
            "https://" + "user:" + secret + "@example.invalid",
            "https://example.invalid/?token=" + secret,
            "https://example.invalid/#" + secret,
            "file:///" + secret,
            "https://example.invalid:99999",
            "https://example.invalid/invalid path",
            "https://example.invalid\\" + secret,
        ]
        for value in invalid:
            with self.subTest(value_index=invalid.index(value)):
                with self.assertRaisesRegex(ValueError, "^Invalid URL configuration: ADMIN_API_URL$") as error:
                    load_bridge(ADMIN_API_URL=value)
                self.assertNotIn(secret, str(error.exception))

    def test_health_overrides_allow_only_noncredential_routing_parameters(self):
        bridge = load_bridge(VOICE_HEALTH_URL="https://voice.invalid/health?edge_safe=1")
        self.assertEqual(bridge.VOICE_HEALTH_URL, "https://voice.invalid/health?edge_safe=1")
        with self.assertRaisesRegex(ValueError, "Invalid URL configuration: VOICE_HEALTH_URL"):
            load_bridge(VOICE_HEALTH_URL="https://voice.invalid/health?token=synthetic-private-value")

    def test_boolean_opt_in_is_exact_and_intervals_are_bounded(self):
        for name in ["EDGE_BRIDGE_HEARTBEAT_ENABLED", "EDGE_GPU_PROBE_ENABLED"]:
            for value in ["yes", "1", "", "truthy"]:
                with self.subTest(name=name, value=value):
                    with self.assertRaisesRegex(ValueError, "Invalid boolean configuration"):
                        load_bridge(**{name: value})
        for value in ["0", "19", "86401", "1.5", "invalid"]:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "Invalid interval configuration"):
                    load_bridge(EDGE_HEARTBEAT_INTERVAL_SEC=value)
        self.assertEqual(load_bridge(EDGE_HEARTBEAT_INTERVAL_SEC="20").EDGE_HEARTBEAT_INTERVAL_SEC, 20)
        self.assertEqual(load_bridge(EDGE_HEARTBEAT_INTERVAL_SEC="86400").EDGE_HEARTBEAT_INTERVAL_SEC, 86400)

    def test_diagnostic_urls_drop_all_queries_userinfo_and_fragments(self):
        bridge = load_bridge(CONTROL_TOKEN="synthetic control/value")
        url = "https://" + "user:synthetic-private-value@" + "example.invalid/path?token=synthetic+control%2Fvalue#hidden"
        self.assertEqual(bridge._safe_url(url), "https://example.invalid/path")
        self.assertEqual(bridge._safe_url("http://[::1]:8150/health?anything=hidden"), "http://[::1]:8150/health")

    def test_diagnostic_files_redact_credentials(self):
        bridge = load_bridge(CONTROL_TOKEN="synthetic-control-value")
        with tempfile.TemporaryDirectory() as directory:
            bridge.EDGE_DIAGNOSTICS_PATH = Path(directory) / "diagnostic.jsonl"
            bridge._write_diag({"detail": "failure with synthetic-control-value", "nested": {"authorization": "synthetic-other-value"}})
            content = bridge.EDGE_DIAGNOSTICS_PATH.read_text(encoding="utf-8")
            self.assertNotIn("synthetic-control-value", content)
            self.assertNotIn("synthetic-other-value", content)
            self.assertEqual(json.loads(content)["nested"]["authorization"], "[redacted]")


class AsyncBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_startup_health_and_heartbeat_are_inert(self):
        bridge = load_bridge()
        with patch.object(bridge.httpx, "AsyncClient", side_effect=AssertionError("unexpected network client")), patch.object(
            bridge.subprocess, "run", side_effect=AssertionError("unexpected process")
        ), patch.object(bridge.asyncio, "create_task", side_effect=AssertionError("unexpected background task")):
            await bridge.startup_event()
            result = await bridge.health()
            runtime = await bridge.local_runtime()
            heartbeat = await bridge.heartbeat_now()
        self.assertEqual(result["workers"]["ollama"]["status"], "disabled")
        self.assertEqual(result["workers"]["admin"]["status"], "disabled")
        self.assertEqual(runtime["services"]["voice"]["status"], "disabled")
        self.assertEqual(heartbeat["status"], "skipped")
        self.assertIsNone(bridge.EDGE_DIAGNOSTICS_PATH)
        self.assertIsNone(bridge.HEARTBEAT_TASK)

    async def test_authority_probe_keeps_wire_contract_without_returning_token(self):
        secret = "synthetic control/value"
        bridge = load_bridge(ADMIN_API_URL="http://admin.invalid", CONTROL_TOKEN=secret)
        probe = AsyncMock(return_value={"row": {"token": secret, "meta_json": {"inventory": {"gpus": [{"name": "test GPU"}]}}}})
        with patch.object(bridge, "_probe_json", probe):
            result = await bridge._probe_windows_node_authority()
        wire_url = probe.await_args.args[0]
        self.assertIn("token=synthetic+control%2Fvalue", wire_url)
        self.assertEqual(result["url"], "http://admin.invalid/windows-node/latest")
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(bridge._windows_gpu_rows(result), [{"name": "test GPU"}])

    async def test_probe_failures_do_not_echo_url_or_exception_contents(self):
        secret = "synthetic-control-value"
        bridge = load_bridge(CONTROL_TOKEN=secret)
        with patch.object(bridge, "_probe_json", AsyncMock(side_effect=RuntimeError("request failed: " + secret))):
            result = await bridge._probe_status("https://example.invalid/health?token=" + secret)
        self.assertEqual(result, {"status": "error", "url": "https://example.invalid/health", "detail": "RuntimeError"})

    async def test_heartbeat_requires_both_admin_url_and_token_before_any_probe(self):
        for configuration in [{}, {"ADMIN_API_URL": "http://admin.invalid"}, {"CONTROL_TOKEN": "synthetic-control-value"}]:
            bridge = load_bridge(**configuration)
            with patch.object(bridge, "collect_edge_status", AsyncMock(side_effect=AssertionError("unexpected probe"))):
                result = await bridge.send_heartbeat()
            self.assertEqual(result["status"], "skipped")

    async def test_enabled_heartbeat_rejects_missing_configuration(self):
        bridge = load_bridge(EDGE_BRIDGE_HEARTBEAT_ENABLED="true")
        with patch.object(bridge.asyncio, "create_task", side_effect=AssertionError("unexpected task")):
            with self.assertRaisesRegex(RuntimeError, "Heartbeat requires ADMIN_API_URL and CONTROL_TOKEN"):
                await bridge.startup_event()

    async def test_mock_heartbeat_task_is_single_owned_and_cancelled_on_shutdown(self):
        bridge = load_bridge(EDGE_BRIDGE_HEARTBEAT_ENABLED="true", ADMIN_API_URL="http://admin.invalid", CONTROL_TOKEN="synthetic-control-value")
        entered = asyncio.Event()

        async def mock_loop():
            entered.set()
            await asyncio.Event().wait()

        with patch.object(bridge, "_heartbeat_loop", mock_loop):
            await bridge.startup_event()
            task = bridge.HEARTBEAT_TASK
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                await bridge.startup_event()
                self.assertIs(bridge.HEARTBEAT_TASK, task)
            finally:
                await bridge.shutdown_event()
        self.assertTrue(task.cancelled())
        self.assertIsNone(bridge.HEARTBEAT_TASK)

    async def test_unconfigured_proxy_reports_503_without_reading_or_forwarding(self):
        bridge = load_bridge()
        request = type("RequestStub", (), {"body": AsyncMock(side_effect=AssertionError("unexpected body read"))})()
        with patch.object(bridge.httpx, "AsyncClient", side_effect=AssertionError("unexpected network client")):
            result = await bridge.proxy_luminax_brain(request, "chat")
        self.assertEqual(result.status_code, 503)

    async def test_configured_proxy_uses_mock_upstream_and_ignores_ambient_proxy_settings(self):
        bridge = load_bridge(EDGE_LUMINAX_BRAIN_LOCAL_URL="http://brain.invalid:8501")
        original_client = httpx.AsyncClient
        seen = []

        def upstream(request):
            seen.append(request)
            return httpx.Response(200, json={"status": "mock-only"})

        def client_factory(**kwargs):
            self.assertFalse(kwargs["trust_env"])
            self.assertFalse(kwargs["follow_redirects"])
            return original_client(transport=httpx.MockTransport(upstream), **kwargs)

        async with original_client(transport=httpx.ASGITransport(app=bridge.app), base_url="http://testserver") as client:
            with patch.object(bridge.httpx, "AsyncClient", client_factory):
                response = await client.post("/luminax-brain/chat?mode=test", json={"text": "offline"})
        self.assertEqual(response.json(), {"status": "mock-only"})
        self.assertEqual(str(seen[0].url), "http://brain.invalid:8501/chat?mode=test")
        self.assertEqual(json.loads(seen[0].content), {"text": "offline"})

    async def test_successful_heartbeat_does_not_echo_returned_control_token(self):
        secret = "synthetic-control-value"
        bridge = load_bridge(ADMIN_API_URL="http://admin.invalid", CONTROL_TOKEN=secret)
        original_client = httpx.AsyncClient

        def upstream(request):
            self.assertEqual(json.loads(request.content)["token"], secret)
            return httpx.Response(200, json={"status": "ok", "token": secret, "hostname": "server echoed " + secret})

        def client_factory(**kwargs):
            self.assertFalse(kwargs["trust_env"])
            self.assertFalse(kwargs["follow_redirects"])
            return original_client(transport=httpx.MockTransport(upstream), **kwargs)

        with patch.object(bridge, "collect_edge_status", AsyncMock(return_value={})), patch.object(bridge.httpx, "AsyncClient", client_factory):
            result = await bridge.send_heartbeat()
        self.assertEqual(result["status"], "ok")
        self.assertNotIn(secret, json.dumps([result, bridge.LAST_HEARTBEAT]))


if __name__ == "__main__":
    unittest.main()
