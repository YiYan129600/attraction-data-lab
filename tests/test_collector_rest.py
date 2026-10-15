import io
import json
import tempfile
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

from xhs_research.collector.config import Config
from xhs_research.collector.providers import RestProvider, ProviderError, NoRedirect
from xhs_research.collector.normalize import rest_note, rest_profile
from xhs_research.collector.runner import Runner
from xhs_research.collector.state import State


NOTE = "000000000000000000000064"


def detail():
    return {"success": True, "data": {"feed_id": NOTE, "data": {"note": {"noteId": NOTE, "title": "Fixture", "desc": "Fixture", "type": "normal", "user": {"userId": "author"},
        "interactInfo": {"commentCount": "50", "likedCount": "20", "collectedCount": "1"}}, "comments": {"hasMore": False, "list": [{"id": "root", "noteId": NOTE, "userInfo": {"userId": "user"}, "content": "Fixture", "subComments": [{"id": "reply", "userInfo": {"userId": "author"}, "content": "Fixture"}]}]}}}}


class RestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config("rest-test", "owner", provider="xhs-rest", state_root=str(Path(self.tmp.name)), queries={"fitness": "健身"}, request_interval=0)
        self.provider = RestProvider(self.config, live=True)

    def tearDown(self):
        self.tmp.cleanup()

    def reply(self, data):
        return io.BytesIO(json.dumps(data).encode())

    def test_bound_official_detail_and_reply_counts_partial(self):
        value = rest_note(detail(), NOTE, "2026-10-14T00:00:00Z")
        self.assertEqual(len(value["comments"]), 2)
        self.assertEqual(value["comments"][1]["level"], 2)
        self.assertTrue(value["comments"][1]["is_author"])
        self.assertEqual(value["note"]["coverage"]["status"], "partial")
        broken = detail(); broken["data"]["feed_id"] = "wrong"
        with self.assertRaises(ValueError):
            rest_note(broken, NOTE, "2026-10-14T00:00:00Z")

    def test_rest_parameter_binding_no_user_token_guess(self):
        self.provider.locators[NOTE] = "SYNTHETIC_SOURCE_LOCATOR"
        self.provider.opener = Mock()
        self.provider.opener.open.return_value = self.reply(detail())
        self.provider.note({"note_id": NOTE}, {"expand": True, "root_limit": 100, "replies": True})
        request = self.provider.opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["comment_config"]["max_comment_items"], 100)
        self.assertTrue(payload["comment_config"]["click_more_replies"])
        self.assertNotIn("limit", payload)
        self.assertNotIn("user", self.provider.locators)
        with self.assertRaises(ProviderError) as error:
            self.provider.profile({"user_id": "user"})
        self.assertEqual(error.exception.category, "needs_locator")

    def test_http_errors_never_become_empty_success(self):
        for code, expected in [(401, "login_required"), (403, "access_denied"), (429, "rate_limited"), (503, "transient")]:
            with self.subTest(code=code):
                self.provider.opener = Mock()
                self.provider.opener.open.side_effect = urllib.error.HTTPError("http://127.0.0.1", code, "DO_NOT_LOG", {}, io.BytesIO(b"SECRET"))
                with self.assertRaises(ProviderError) as error:
                    self.provider.request("/health")
                self.assertEqual(str(error.exception), expected)

    def test_captcha_login_schema_errors_stop(self):
        for data, expected in [({"success": False, "message": "验证码 SECRET"}, "captcha"), ({"success": False, "message": "登录 SECRET"}, "login_required"), ([], "schema")]:
            self.provider.opener = Mock()
            self.provider.opener.open.return_value = self.reply(data)
            with self.assertRaises(ProviderError) as error:
                self.provider.request("/health")
            self.assertEqual(error.exception.category, expected)

    def test_redirect_rejected_and_read_routes_only(self):
        with self.assertRaises(ProviderError):
            NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://elsewhere.invalid")
        with self.assertRaises(ValueError):
            self.provider.request("/api/v1/like")
        for origin in ["https://example.com", "http://127.0.0.1@evil.invalid", "http://127.0.0.1:18060/?token=SECRET"]:
            with self.assertRaises(ValueError):
                replace(self.config, base_url=origin).validate()

    def test_transient_retry_counted_within_budget_and_failure_report(self):
        cfg = replace(self.config, max_requests=2)
        state = State(cfg, "failure")
        try:
            provider = Mock(network=True, name="xhs-rest")
            provider.probe.side_effect = ProviderError("transient")
            result = Runner(cfg, state, provider, sleep=lambda _: None).run()
            self.assertEqual(result["provider_calls"], 2)
            self.assertEqual(result["stop_reason"], "transient")
            self.assertTrue((state.run_dir / "run-summary.json").exists())
            summary = json.loads((state.run_dir / "run-summary.json").read_text())
            self.assertFalse(summary["new_live_data"])
        finally:
            state.close()

    def test_local_http_service_probe_search_detail_and_redirect(self):
        seen = []
        redirect = [False]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                seen.append(self.path)
                if redirect[0]:
                    self.send_response(302)
                    self.send_header("Location", "/unexpected")
                    self.end_headers()
                    return
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({"success": True, "data": {"is_logged_in": True}}).encode())
            def do_POST(self):
                seen.append(self.path)
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                result = detail() if self.path.endswith("detail") else {"success": True, "data": {"feeds": [{"id": NOTE, "xsecToken": "SYNTHETIC", "noteCard": {"user": {"userId": "author"}, "interactInfo": {"commentCount": "50"}}}]}}
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps(result).encode())
        server = HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            cfg = replace(self.config, base_url=f"http://127.0.0.1:{server.server_port}")
            provider = RestProvider(cfg, live=True)
            self.assertTrue(provider.probe()["logged_in"])
            self.assertEqual(provider.search("健身", "最多评论")["candidates"][0]["platform_comments"]["value"], 50)
            self.assertEqual(len(provider.note({"note_id": NOTE}, {})["comments"]), 2)
            redirect[0] = True
            with self.assertRaises(ProviderError) as exc:
                provider.request("/health")
            self.assertEqual(exc.exception.category, "redirect_rejected")
            self.assertNotIn("/unexpected", seen)
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
