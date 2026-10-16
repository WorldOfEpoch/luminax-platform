"""Offline checks of preview behavior and credential redirect boundaries."""
from __future__ import annotations

import contextlib
from email.message import Message
import importlib.util
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
import urllib.response

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "tools/CodenameDarius/python"))


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublicToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.darius = load("public_darius", "tools/CodenameDarius/python/darius_agent_cli.py")
        cls.epoch = load("public_epoch", "tools/EpochPortal/python/epoch_portal_cli.py")
        cls.meshy = load("public_meshy", "tools/CodenameDarius/python/meshy_cli.py")
        cls.ladder = load("public_ladder", "tools/luminax_test_ladder.py")
        cls.sync = load("public_sync", "tools/sync_payload_blocks_to_installer.py")

    def test_model_clis_preview_before_loading_configuration(self):
        for module in (self.darius, self.epoch):
            with self.subTest(module=module.__name__), patch.object(sys, "argv", ["tool", "sync"]), \
                    patch.object(module, "load_env", side_effect=AssertionError("loaded configuration")), \
                    patch.object(urllib.request, "build_opener", side_effect=AssertionError("network attempted")), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(module.main(), 0)
                self.assertIn('"status": "plan"', output.getvalue())

    def test_ladder_preview_does_not_open_network(self):
        with patch.object(sys, "argv", ["ladder"]), \
                patch.object(urllib.request, "build_opener", side_effect=AssertionError("network attempted")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.ladder.main(), 0)
            self.assertIn('"status": "plan"', output.getvalue())

    def test_legacy_sync_preview_does_not_read_files(self):
        with patch.object(sys, "argv", ["sync", "--installer", "absent.sh", "--payload-root", "absent", "--file", "a.py"]), \
                patch.object(Path, "read_text", side_effect=AssertionError("file read")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.sync.main(), 0)

    def test_redirects_never_construct_a_followup_with_bearer(self):
        for module in (self.darius, self.epoch, self.meshy, self.ladder):
            for code in (301, 302, 303, 307, 308):
                with self.subTest(module=module.__name__, code=code):
                    request = urllib.request.Request("https://source.invalid/api", headers={"Authorization": "Bearer synthetic-test-token"})
                    self.assertIsNone(module._NoRedirect().redirect_request(request, None, code, "redirect", {}, "https://other.invalid/collect"))

    def test_open_request_installs_no_redirect_handler(self):
        for module in (self.darius, self.epoch, self.meshy, self.ladder):
            with self.subTest(module=module.__name__), patch.object(urllib.request, "build_opener") as build:
                request = urllib.request.Request("https://source.invalid/api")
                module._open_request(request, timeout=3)
                self.assertTrue(any(isinstance(item, module._NoRedirect) for item in build.call_args.args))
                build.return_value.open.assert_called_once_with(request, timeout=3)

    def test_opener_rejects_cross_origin_without_second_request(self):
        class SyntheticHTTP(urllib.request.HTTPHandler):
            def __init__(self):
                super().__init__()
                self.requests = []

            def http_open(self, request):
                self.requests.append(request)
                headers = Message()
                headers["Location"] = "http://other.invalid/collect"
                response = urllib.response.addinfourl(io.BytesIO(b""), headers, request.full_url, code=302)
                response.msg = "Found"
                return response

        for module in (self.darius, self.epoch, self.meshy, self.ladder):
            with self.subTest(module=module.__name__):
                fake = SyntheticHTTP()
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), fake, module._NoRedirect())
                request = urllib.request.Request("http://source.invalid/api", data=b"{}", headers={"Authorization": "Bearer synthetic-test-token"})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    opener.open(request, timeout=1)
                self.assertEqual(error.exception.code, 302)
                self.assertEqual(len(fake.requests), 1)
                self.assertEqual(fake.requests[0].full_url, "http://source.invalid/api")

    def test_missing_meshy_key_fails_before_request(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                self.meshy.api_headers()

    def test_no_implicit_environment_file_discovery(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, "read_text", side_effect=AssertionError("implicit env read")):
            self.meshy.load_env()


if __name__ == "__main__":
    unittest.main()
