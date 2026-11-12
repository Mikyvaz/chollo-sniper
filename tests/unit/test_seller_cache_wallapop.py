import tests.unit._harness  # noqa: F401
import json
import sqlite3
import unittest


class FakeResp:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        if not self.responses:
            return FakeResp(500, {})
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class FakeHealthMonitor:
    def __init__(self):
        self.events = []

    def handle_http_response(self, status_code, url="", query="", retry_after=None):
        self.events.append((status_code, url, query, retry_after))
        return True, 420


class TestSellerCacheWallapop(unittest.TestCase):
    def setUp(self):
        import core_engine
        import fraud_signals
        self.conn = sqlite3.connect(":memory:")
        core_engine.init_db(self.conn)
        fraud_signals._SELLER_PROFILE_CACHE.clear()

    def tearDown(self):
        self.conn.close()

    def test_memory_and_sqlite_cache_reuse_without_network(self):
        import fraud_signals

        # Table seller_profile_cache must exist after init_db
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='seller_profile_cache'"
        ).fetchall()
        self.assertEqual(len(rows), 1)

        sess = FakeSession([
            FakeResp(200, {"scoring": 96, "register_date": 1680000000000, "stats": {"reviews": {"total": 25}}}),
        ])
        item1 = {"platform": "WALLAPOP", "seller": {"id": "wal_u_25"}}
        p1 = fraud_signals.enrich_seller_profile(item1, http_session=sess, platform="WALLAPOP", conn=self.conn)
        self.assertEqual(p1["seller_reviews_count"], 25)
        self.assertEqual(p1["seller_profile_status"], "ok")
        self.assertEqual(len(sess.calls), 1)

        # 2nd call with RAM cache populated -> 0 new network calls
        item2 = {"platform": "WALLAPOP", "seller": {"id": "wal_u_25"}}
        p2 = fraud_signals.enrich_seller_profile(item2, http_session=sess, platform="WALLAPOP", conn=self.conn)
        self.assertEqual(p2["seller_reviews_count"], 25)
        self.assertEqual(len(sess.calls), 1)

        # Clear RAM cache -> must come from SQLite seller_profile_cache with 0 new network calls
        fraud_signals._SELLER_PROFILE_CACHE.clear()
        item3 = {"platform": "WALLAPOP", "seller": {"id": "wal_u_25"}}
        p3 = fraud_signals.enrich_seller_profile(item3, http_session=sess, platform="WALLAPOP", conn=self.conn)
        self.assertEqual(p3["seller_reviews_count"], 25)
        self.assertAlmostEqual(p3["seller_rating"], 4.8)
        self.assertEqual(len(sess.calls), 1)

    def test_ttl_tiers_by_reviews_count(self):
        import fraud_signals

        # Insert 3 sellers into seller_profile_cache:
        # - u_zero (0 reviews, TTL 1h): at -30 min (valid) vs -90 min (expired)
        # - u_mid (5 reviews, TTL 6h): at -3 h (valid) vs -7 h (expired)
        # - u_high (30 reviews, TTL 24h): at -12 h (valid) vs -25 h (expired)
        cases = [
            ("u_zero", 0, None, "-30 minutes", "-90 minutes", 10),
            ("u_mid", 5, 4.5, "-3 hours", "-7 hours", 12),
            ("u_high", 30, 4.9, "-12 hours", "-25 hours", 35),
        ]
        for sid, rev, rat, valid_offset, expired_offset, refreshed_rev in cases:
            prof = {
                "seller_id": sid,
                "seller_name": sid,
                "seller_reviews_count": rev,
                "reviews_count": rev,
                "seller_rating": rat,
                "rating": rat,
                "seller_joined_days": 100.0,
                "seller_verified": True,
                "seller_trust_badge": "",
                "seller_profile_status": "ok",
                "platform": "WALLAPOP",
            }
            self.conn.execute(
                f"INSERT OR REPLACE INTO seller_profile_cache (platform, seller_id, profile_json, fetched_at) "
                f"VALUES ('WALLAPOP', ?, ?, datetime('now', '{valid_offset}'))",
                (sid, json.dumps(prof)),
            )
            self.conn.commit()
            fraud_signals._SELLER_PROFILE_CACHE.clear()

            sess_ok = FakeSession([
                FakeResp(200, {"scoring": 90, "stats": {"reviews": {"total": refreshed_rev}}})
            ])
            item = {"platform": "WALLAPOP", "seller": {"id": sid}}
            got = fraud_signals.enrich_seller_profile(item, http_session=sess_ok, platform="WALLAPOP", conn=self.conn)
            self.assertEqual(got["seller_reviews_count"], rev, f"{sid} within TTL ({valid_offset}) should hit SQLite cache")
            self.assertEqual(len(sess_ok.calls), 0)

            # Now age the row past its TTL tier
            self.conn.execute(
                f"UPDATE seller_profile_cache SET fetched_at = datetime('now', '{expired_offset}') "
                f"WHERE platform = 'WALLAPOP' AND seller_id = ?",
                (sid,),
            )
            self.conn.commit()
            fraud_signals._SELLER_PROFILE_CACHE.clear()

            item_exp = {"platform": "WALLAPOP", "seller": {"id": sid}}
            got_exp = fraud_signals.enrich_seller_profile(item_exp, http_session=sess_ok, platform="WALLAPOP", conn=self.conn)
            self.assertEqual(got_exp["seller_reviews_count"], refreshed_rev, f"{sid} past TTL ({expired_offset}) should refetch")
            self.assertEqual(len(sess_ok.calls), 1)

    def test_429_sets_unavailable_notifies_health_monitor_and_not_cached(self):
        import antiban
        import fraud_signals

        bucket = antiban.TokenBucket(1.0, burst=5, sleeper=lambda _: None)
        antiban.set_platform_bucket("WALLAPOP", bucket)
        try:
            hm = FakeHealthMonitor()
            sess = FakeSession([
                FakeResp(429, {}, headers={"Retry-After": "120"}),
                FakeResp(200, {"counters": [{"type": "reviews", "value": 9}]}),  # should NOT be called after 429 on /users
            ])
            item = {"platform": "WALLAPOP", "seller": {"id": "wal_u_429"}}
            prof = fraud_signals.enrich_seller_profile(
                item, http_session=sess, platform="WALLAPOP", conn=self.conn, health_monitor=hm
            )
            self.assertEqual(prof["seller_profile_status"], "unavailable")
            self.assertIsNone(prof["seller_reviews_count"])
            self.assertEqual(len(sess.calls), 1, "Should abort /stats when /users returns 429")
            self.assertEqual(len(hm.events), 1)
            self.assertEqual(hm.events[0][0], 429)
            self.assertEqual(hm.events[0][3], "120")
            self.assertLess(bucket.rate, 1.0)
            self.assertAlmostEqual(bucket.rate, 0.5)

            # Must not be cached in RAM or SQLite
            self.assertNotIn(("WALLAPOP", "wal_u_429"), fraud_signals._SELLER_PROFILE_CACHE)
            row = self.conn.execute(
                "SELECT 1 FROM seller_profile_cache WHERE platform='WALLAPOP' AND seller_id='wal_u_429'"
            ).fetchone()
            self.assertIsNone(row)
        finally:
            antiban.set_platform_bucket("WALLAPOP", None)

    def test_open_default_cache_db_uses_wal_and_busy_timeout_30000(self):
        import os
        import tempfile
        from unittest.mock import patch
        import fraud_signals

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_db = os.path.join(tmpdir, "cache_test.db")
            with patch.dict(os.environ, {"DB_PATH": tmp_db}):
                c = fraud_signals._open_default_cache_db()
                self.assertIsNotNone(c)
                try:
                    jm = c.execute("PRAGMA journal_mode;").fetchone()[0]
                    bt = c.execute("PRAGMA busy_timeout;").fetchone()[0]
                    self.assertEqual(str(jm).lower(), "wal")
                    self.assertEqual(int(bt), 30000)
                finally:
                    c.close()


if __name__ == "__main__":
    unittest.main()
