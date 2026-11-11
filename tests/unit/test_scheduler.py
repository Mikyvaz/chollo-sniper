import tests.unit._harness  # noqa: F401
import json
import os
import random
import tempfile
import unittest
from datetime import datetime


class FakeClock:
    def __init__(self, start=0.0, hour=12):
        self.now = float(start)
        self.hour = int(hour)
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        dt = max(0.0, float(seconds))
        self.sleeps.append(dt)
        self.now += dt

    def now_dt(self):
        return datetime(2026, 11, 11, self.hour, 0, 0)


def _percentile(values, pct):
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return float(s[f])
    return float(s[f] + (s[c] - s[f]) * (k - f))


class TestScheduler(unittest.TestCase):
    def test_one_hour_simulation_hot_and_standard(self):
        from antiban import TokenBucket, QueryTask, QueryScheduler

        clock = FakeClock(start=0.0, hour=12)
        rng = random.Random(42)
        bucket = TokenBucket(rate_per_s=1.0, burst=2, clock=clock, sleeper=clock.sleep)

        tasks = [
            QueryTask(0.0, f"hot_{i}", f"switch_{i}", 50.0, 200.0, 20.0, tier="hot")
            for i in range(16)
        ] + [
            QueryTask(0.0, f"std_{j}", f"retro_{j}", 20.0, 150.0, 80.0, tier="standard")
            for j in range(15)
        ]

        heartbeats = []
        sched = QueryScheduler(
            tasks,
            bucket=bucket,
            clock=clock,
            sleeper=clock.sleep,
            now_fn=clock.now_dt,
            heartbeat=lambda: heartbeats.append(clock.now),
            rng=rng,
        )

        exec_times = {}
        all_execs = []
        while clock.now < 3600.0:
            task = sched.next()
            if clock.now >= 3600.0:
                break
            t_now = clock.now
            exec_times.setdefault(task.radar_id, []).append(t_now)
            all_execs.append(t_now)
            sched.done(task)

        hot_revisits = []
        std_revisits = []
        for rid, times in exec_times.items():
            deltas = [times[k] - times[k - 1] for k in range(1, len(times))]
            if rid.startswith("hot_"):
                hot_revisits.extend(deltas)
            else:
                std_revisits.extend(deltas)

        p95_hot = _percentile(hot_revisits, 95)
        p95_std = _percentile(std_revisits, 95)
        self.assertLessEqual(p95_hot, 25.0, f"p95 hot revisit too high: {p95_hot:.2f}s")
        self.assertLessEqual(p95_std, 100.0, f"p95 std revisit too high: {p95_std:.2f}s")

        per_minute = [0] * 60
        for t_ex in all_execs:
            m = int(t_ex // 60.0)
            if 0 <= m < 60:
                per_minute[m] += 1
        self.assertLessEqual(max(per_minute), 60, f"Exceeded 60 req/min: max={max(per_minute)}")

    def test_token_bucket_penalize_and_recover(self):
        from antiban import TokenBucket

        clock = FakeClock(start=0.0)
        bucket = TokenBucket(rate_per_s=1.0, burst=2, clock=clock, sleeper=clock.sleep)
        self.assertAlmostEqual(bucket.rate, 1.0)

        bucket.penalize()
        self.assertAlmostEqual(bucket.rate, 0.5)
        bucket.penalize()
        self.assertAlmostEqual(bucket.rate, 0.25)
        bucket.penalize()
        self.assertAlmostEqual(bucket.rate, 0.25)

        for _ in range(49):
            bucket.recover()
        self.assertAlmostEqual(bucket.rate, 0.25)
        bucket.recover()
        self.assertAlmostEqual(bucket.rate, 0.275)

        # Penalize resets consecutive streak
        for _ in range(30):
            bucket.recover()
        bucket.penalize(factor=1.0)
        for _ in range(49):
            bucket.recover()
        self.assertAlmostEqual(bucket.rate, 0.275)
        bucket.recover()
        self.assertAlmostEqual(bucket.rate, 0.275 * 1.1)

        # Recover caps at base_rate
        for _ in range(50 * 30):
            bucket.recover()
        self.assertAlmostEqual(bucket.rate, 1.0)

    def test_night_multiplier(self):
        from antiban import TokenBucket, QueryTask, QueryScheduler

        clock = FakeClock(start=100.0, hour=3)  # 03:00 (between 02:00 and 07:00)
        rng = random.Random(7)
        bucket = TokenBucket(rate_per_s=1.0, burst=2, clock=clock, sleeper=clock.sleep)
        t1 = QueryTask(100.0, "switch_oled", "switch oled", 50.0, 200.0, 20.0)
        sched = QueryScheduler(
            [t1],
            bucket=bucket,
            night_multiplier=3,
            clock=clock,
            sleeper=clock.sleep,
            now_fn=clock.now_dt,
            rng=rng,
        )
        task = sched.next()
        sched.done(task)
        delay_night = task.next_due - clock.now
        self.assertGreaterEqual(delay_night, 20.0 * 3 * 0.85)
        self.assertLessEqual(delay_night, 20.0 * 3 * 1.15)

        clock.hour = 14  # 14:00 (daytime)
        clock.now = task.next_due
        task = sched.next()
        sched.done(task)
        delay_day = task.next_due - clock.now
        self.assertGreaterEqual(delay_day, 20.0 * 0.85)
        self.assertLessEqual(delay_day, 20.0 * 1.15)

    def test_reload_if_changed(self):
        from antiban import TokenBucket, QueryScheduler

        clock = FakeClock(start=10.0, hour=12)
        bucket = TokenBucket(rate_per_s=1.0, burst=2, clock=clock, sleeper=clock.sleep)

        cfg1 = {
            "scheduling": {
                "wallapop": {"hot_interval_s": 20, "standard_interval_s": 80, "rate_per_s": 1.0},
                "night_multiplier": 3,
            },
            "targets": [
                {
                    "id": "r1",
                    "name": "Radar 1",
                    "enabled": True,
                    "tier": "hot",
                    "platforms": ["wallapop"],
                    "min_price": 50,
                    "max_price": 150,
                    "wallapop_keywords": ["consola 1"],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg1, f)
            os.utime(cfg_path, (1000, 1000))

            sched = QueryScheduler.from_config(
                cfg1,
                platform="wallapop",
                bucket=bucket,
                config_path=cfg_path,
                clock=clock,
                sleeper=clock.sleep,
                now_fn=clock.now_dt,
            )
            self.assertEqual(len(sched.tasks), 1)
            self.assertFalse(sched.reload_if_changed())

            cfg2 = dict(cfg1)
            cfg2["targets"] = list(cfg1["targets"]) + [
                {
                    "id": "r2",
                    "name": "Radar 2",
                    "enabled": True,
                    "tier": "standard",
                    "platforms": ["wallapop"],
                    "min_price": 30,
                    "max_price": 120,
                    "wallapop_keywords": ["consola 2"],
                }
            ]
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg2, f)
            os.utime(cfg_path, (2000, 2000))

            self.assertTrue(sched.reload_if_changed())
            self.assertEqual(len(sched.tasks), 2)
            rids = {t.radar_id for t in sched.tasks}
            self.assertEqual(rids, {"r1", "r2"})

    def test_config_and_scanner_integration(self):
        import inspect
        import sniper
        import vinted_sniper
        from core_engine import load_config
        from antiban import TokenBucket, set_platform_bucket, get_platform_bucket

        cfg = load_config()
        sched = cfg.get("scheduling", {})
        self.assertEqual(sched.get("night_multiplier"), 3)
        self.assertEqual(sched.get("wallapop", {}).get("hot_interval_s"), 20)
        self.assertEqual(sched.get("wallapop", {}).get("standard_interval_s"), 80)
        self.assertAlmostEqual(float(sched.get("wallapop", {}).get("rate_per_s", 0)), 1.0)
        self.assertEqual(sched.get("vinted", {}).get("hot_interval_s"), 60)
        self.assertEqual(sched.get("vinted", {}).get("standard_interval_s"), 240)
        self.assertAlmostEqual(float(sched.get("vinted", {}).get("rate_per_s", 0)), 0.30)

        sniper_main_src = inspect.getsource(sniper.main)
        vinted_main_src = inspect.getsource(vinted_sniper.main)
        self.assertNotIn("inter_query_sleep", sniper_main_src)
        self.assertNotIn("item_processing_sleep", sniper_main_src)
        self.assertNotIn("inter_query_sleep", vinted_main_src)
        self.assertNotIn("item_processing_sleep", vinted_main_src)
        self.assertIn("QueryScheduler", sniper_main_src)
        self.assertIn("QueryScheduler", vinted_main_src)
        self.assertIn("query_completed", sniper_main_src)
        self.assertIn("query_completed", vinted_main_src)

        clock = FakeClock(start=0.0)
        b = TokenBucket(rate_per_s=2.0, burst=2, clock=clock, sleeper=clock.sleep)
        try:
            set_platform_bucket("WALLAPOP", b)
            self.assertIs(get_platform_bucket("WALLAPOP"), b)
        finally:
            set_platform_bucket("WALLAPOP", None)

    def test_enrich_seller_profile_consumes_bucket_tokens(self):
        import fraud_signals
        from antiban import TokenBucket, set_platform_bucket

        class CountingBucket(TokenBucket):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.acquires = 0

            def acquire(self) -> float:
                self.acquires += 1
                return super().acquire()

        class FakeResp:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class FakeSession:
            def __init__(self, resp):
                self.resp = resp
                self.calls = 0

            def get(self, url, **kw):
                self.calls += 1
                return self.resp

        clock = FakeClock(start=0.0)
        vinted_bucket = CountingBucket(rate_per_s=1.0, burst=2, clock=clock, sleeper=clock.sleep)
        walla_bucket = CountingBucket(rate_per_s=1.0, burst=2, clock=clock, sleeper=clock.sleep)
        fraud_signals._SELLER_PROFILE_CACHE.clear()
        try:
            set_platform_bucket("VINTED", vinted_bucket)
            set_platform_bucket("WALLAPOP", walla_bucket)

            v_sess = FakeSession(
                FakeResp(200, {"user": {"feedback_count": 5, "feedback_reputation": 0.9, "created_at": "2024-01-01T00:00:00Z"}})
            )
            item_v = {"platform": "VINTED", "seller": {"id": "vin_seller_99"}}
            fraud_signals.enrich_seller_profile(item_v, http_session=v_sess, platform="VINTED")
            self.assertEqual(v_sess.calls, 1)
            self.assertEqual(vinted_bucket.acquires, 1)

            w_sess = FakeSession(
                FakeResp(200, {"scoring": 90, "register_date": 1700000000000, "stats": {"reviews": {"total": 12}}})
            )
            item_w = {"platform": "WALLAPOP", "seller": {"id": "wal_seller_99"}}
            fraud_signals.enrich_seller_profile(item_w, http_session=w_sess, platform="WALLAPOP")
            self.assertEqual(w_sess.calls, 1)
            self.assertEqual(walla_bucket.acquires, 1)
        finally:
            set_platform_bucket("VINTED", None)
            set_platform_bucket("WALLAPOP", None)
            fraud_signals._SELLER_PROFILE_CACHE.clear()


if __name__ == "__main__":
    unittest.main()
