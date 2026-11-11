"""T1.9: Wallapop no duplica la petición ante 401/403/429 y respeta Retry-After."""
import unittest
from unittest import mock

import _harness  # noqa: F401  (debe ir primero)
import sniper


class FakeResp:
    def __init__(self, status, headers=None, payload=None):
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class TestWallapopBlock(unittest.TestCase):
    def setUp(self):
        sniper.health_monitor.reset_cooldown()
        sniper.health_monitor.consecutive_blocks = 0
        p = mock.patch.object(sniper.health_monitor, "send_health_embed")
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(sniper.rotator, "rotate")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(sniper.health_monitor.reset_cooldown)

    def _run(self, responses):
        with mock.patch.object(sniper.requests, "get", side_effect=responses) as g:
            out = sniper.fetch_wallapop("switch", 10, 100, fetch_second_page_if_sparse=False)
        return out, g

    def test_429_single_call(self):
        out, g = self._run([FakeResp(429), FakeResp(200)])
        self.assertEqual(out, [])
        self.assertEqual(g.call_count, 1)
        self.assertTrue(sniper.health_monitor.is_in_cooldown())

    def test_403_and_401_single_call(self):
        for st in (403, 401):
            sniper.health_monitor.reset_cooldown()
            out, g = self._run([FakeResp(st), FakeResp(200)])
            self.assertEqual(out, [])
            self.assertEqual(g.call_count, 1, st)

    def test_404_uses_fallback(self):
        out, g = self._run([FakeResp(404), FakeResp(200, payload={"search_objects": []})])
        self.assertEqual(g.call_count, 2)
        self.assertIn("/general/search", g.call_args_list[1][0][0])

    def test_400_and_410_use_fallback(self):
        for st in (400, 410):
            out, g = self._run([FakeResp(st), FakeResp(200, payload={"search_objects": []})])
            self.assertEqual(g.call_count, 2, st)

    def test_500_no_fallback(self):
        out, g = self._run([FakeResp(500), FakeResp(200)])
        self.assertEqual(out, [])
        self.assertEqual(g.call_count, 1)

    def test_retry_after_seconds(self):
        out, g = self._run([FakeResp(429, headers={"Retry-After": "900"})])
        self.assertGreaterEqual(sniper.health_monitor.get_remaining_cooldown(), 890)

    def test_retry_after_does_not_shorten(self):
        out, g = self._run([FakeResp(429, headers={"Retry-After": "5"})])
        self.assertGreaterEqual(sniper.health_monitor.get_remaining_cooldown(), 400)

    def test_retry_after_invalid_ignored(self):
        for val in ("Wed, 21 Oct 2026 07:28:00 GMT", "abc", "-5", ""):
            sniper.health_monitor.reset_cooldown()
            sniper.health_monitor.consecutive_blocks = 0
            self._run([FakeResp(429, headers={"Retry-After": val})])
            self.assertLessEqual(sniper.health_monitor.get_remaining_cooldown(), 421, val)

    def test_handle_http_response_returns_effective_cooldown(self):
        m = sniper.health_monitor
        should, secs = m.handle_http_response(429, retry_after=1200)
        self.assertTrue(should)
        self.assertGreaterEqual(secs, 1190)


if __name__ == "__main__":
    unittest.main()
