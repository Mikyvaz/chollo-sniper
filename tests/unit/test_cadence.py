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
        return datetime(2026, 11, 17, self.hour, 0, 0)


def _pct(values, pct):
    s = sorted(values)
    if not s:
        return 0.0
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return float(s[f] + (s[c] - s[f]) * (k - f))


def _simulate(sched, clock, seconds, yields=None, fetch_s=0.0):
    """Ejecuta el planificador `seconds` segundos simulados; devuelve {clave: [instantes de sondeo]}."""
    execs = {}
    end = clock.now + seconds
    while clock.now < end:
        task = sched.next()
        if clock.now >= end:
            sched.done(task)
            break
        execs.setdefault((task.tier, task.radar_id), []).append(clock.now)
        if fetch_s:
            clock.sleep(fetch_s)
        y = 0
        if yields:
            y = yields.get(task.radar_id, 0)
            y = y(clock.now) if callable(y) else y
        sched.done(task, yield_count=y)
    return execs


def _gaps(times):
    return [b - a for a, b in zip(times, times[1:])]


class TestPriorityTokens(unittest.TestCase):
    def test_priority_acquire_does_not_wait_and_debt_is_repaid(self):
        from antiban import TokenBucket

        clock = FakeClock()
        bucket = TokenBucket(rate_per_s=1.0, burst=2, clock=clock, sleeper=clock.sleep, max_debt=3)
        bucket.acquire()
        bucket.acquire()                      # ráfaga inicial agotada: saldo 0
        self.assertEqual(clock.now, 0.0)

        for _ in range(3):                    # tres peticiones de ficha: inmediatas, a crédito
            self.assertEqual(bucket.acquire(priority=True), 0.0)
        self.assertEqual(clock.now, 0.0)
        self.assertEqual(bucket.priority_grants, 3)

        waited = bucket.acquire()             # la siguiente búsqueda paga la deuda: 3 + 1 tokens
        self.assertAlmostEqual(waited, 4.0, places=6)
        self.assertAlmostEqual(clock.now, 4.0, places=6)

    def test_priority_beyond_max_debt_waits_like_normal(self):
        from antiban import TokenBucket

        clock = FakeClock()
        bucket = TokenBucket(rate_per_s=1.0, burst=1, clock=clock, sleeper=clock.sleep, max_debt=2)
        bucket.acquire()
        self.assertEqual(bucket.acquire(priority=True), 0.0)
        self.assertEqual(bucket.acquire(priority=True), 0.0)
        waited = bucket.acquire(priority=True)   # crédito agotado
        self.assertGreater(waited, 0.0)
        # Sigue contando como token de anuncio (no como coste de la búsqueda) aunque haya tenido que esperar
        self.assertEqual(bucket.priority_grants, 3)
        self.assertEqual(bucket.grants, 1)

    def test_candidate_burst_does_not_inflate_query_cost(self):
        """Una búsqueda que trae 8 candidatos (fichas/perfiles > crédito) sigue costando 1 token."""
        from antiban import TokenBucket, QueryTask, QueryScheduler

        clock = FakeClock()
        bucket = TokenBucket(rate_per_s=0.3, burst=2, clock=clock, sleeper=clock.sleep, max_debt=3)
        task = QueryTask(0.0, "switch2", "nintendo switch 2", 1, 2, 60.0)
        sched = QueryScheduler([task, QueryTask(0.0, "otro", "otro", 1, 2, 60.0)], bucket=bucket, clock=clock,
                               sleeper=clock.sleep, now_fn=clock.now_dt, stagger_initial=False)
        for _ in range(6):
            t = sched.next()
            if t.radar_id == "switch2":
                for _ in range(8):
                    bucket.acquire(priority=True)
            sched.done(t, yield_count=8 if t.radar_id == "switch2" else 0)
        self.assertLess(task.cost, 1.05)
        self.assertGreaterEqual(bucket.priority_grants, 8)

    def test_max_debt_zero_disables_priority(self):
        from antiban import TokenBucket

        clock = FakeClock()
        bucket = TokenBucket(rate_per_s=1.0, burst=1, clock=clock, sleeper=clock.sleep, max_debt=0)
        bucket.acquire()
        self.assertGreater(bucket.acquire(priority=True), 0.0)

    def test_long_run_rate_is_not_exceeded_with_priority_requests(self):
        from antiban import TokenBucket

        clock = FakeClock()
        bucket = TokenBucket(rate_per_s=0.5, burst=2, clock=clock, sleeper=clock.sleep, max_debt=3)
        grants = 0
        rng = random.Random(3)
        while clock.now < 3600.0:
            bucket.acquire()
            grants += 1
            if rng.random() < 0.2:            # 1 de cada 5 búsquedas trae un anuncio: ficha + perfil
                bucket.acquire(priority=True)
                bucket.acquire(priority=True)
                grants += 2
        # 0,5 req/s durante 1 h = 1800 (+ ráfaga inicial y crédito pendiente al terminar)
        self.assertLessEqual(grants, 1800 + 2 + 3 + 1)

    def test_acquire_item_token_falls_back_for_buckets_without_priority(self):
        from antiban import acquire_item_token

        class OldBucket:
            def __init__(self):
                self.calls = 0

            def acquire(self):
                self.calls += 1
                return 0.25

        ob = OldBucket()
        self.assertEqual(acquire_item_token(ob), 0.25)
        self.assertEqual(ob.calls, 1)
        self.assertEqual(acquire_item_token(None), 0.0)

    def test_enrich_and_details_use_priority_tokens(self):
        import fraud_signals
        import vinted_sniper
        from antiban import TokenBucket, set_platform_bucket

        class RecordingBucket(TokenBucket):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.kinds = []

            def acquire(self, priority=False):
                self.kinds.append(bool(priority))
                return super().acquire(priority=priority)

        class Resp:
            status_code = 200
            headers = {}
            text = "<html></html>"

            def json(self):
                return {"scoring": 90, "register_date": 1700000000000, "stats": {"reviews": {"total": 12}}}

        class Sess:
            def get(self, url, **kw):
                return Resp()

        clock = FakeClock()
        wb = RecordingBucket(rate_per_s=1.0, burst=2, clock=clock, sleeper=clock.sleep)
        vb = RecordingBucket(rate_per_s=0.3, burst=2, clock=clock, sleeper=clock.sleep)
        fraud_signals._SELLER_PROFILE_CACHE.pop(("WALLAPOP", "prio_seller_1"), None)
        try:
            set_platform_bucket("WALLAPOP", wb)
            set_platform_bucket("VINTED", vb)
            fraud_signals.enrich_seller_profile(
                {"platform": "WALLAPOP", "seller": {"id": "prio_seller_1"}}, http_session=Sess(), platform="WALLAPOP")
            self.assertEqual(wb.kinds, [True])

            client = vinted_sniper.VintedClient.__new__(vinted_sniper.VintedClient)
            client.session = Sess()

            class _HM:
                def is_in_cooldown(self):
                    return False

                def handle_http_response(self, *a, **k):
                    return (False, 0)

            client.health_monitor = _HM()
            self.assertIsNotNone(client.fetch_item_details("https://www.vinted.es/items/1-x"))
            self.assertEqual(vb.kinds, [True])
        finally:
            set_platform_bucket("WALLAPOP", None)
            set_platform_bucket("VINTED", None)
            fraud_signals._SELLER_PROFILE_CACHE.pop(("WALLAPOP", "prio_seller_1"), None)


class TestNoStarvation(unittest.TestCase):
    def _production_like_tasks(self, QueryTask, hot_n, std_n, hot_s, std_s):
        return ([QueryTask(0.0, f"hot_{i}", f"q{i}", 1, 2, hot_s, tier="hot") for i in range(hot_n)]
                + [QueryTask(0.0, f"std_{j}", f"s{j}", 1, 2, std_s, tier="standard") for j in range(std_n)])

    def test_oversubscribed_wallapop_polls_every_tier_in_daytime(self):
        """21 hot @20 s + 10 standard @80 s piden 70,5 req/min contra 60: nadie se queda sin sondear."""
        from antiban import TokenBucket, QueryTask, QueryScheduler

        clock = FakeClock()
        bucket = TokenBucket(rate_per_s=1.0, burst=2, clock=clock, sleeper=clock.sleep)
        sched = QueryScheduler(self._production_like_tasks(QueryTask, 21, 10, 20.0, 80.0), bucket=bucket,
                               clock=clock, sleeper=clock.sleep, now_fn=clock.now_dt, rng=random.Random(1))
        self.assertTrue(sched.plan["oversubscribed"])
        execs = _simulate(sched, clock, 3600.0)
        hot = [g for k, v in execs.items() if k[0] == "hot" for g in _gaps(v)]
        std = [g for k, v in execs.items() if k[0] == "standard" for g in _gaps(v)]
        self.assertEqual(len([k for k in execs if k[0] == "standard"]), 10, "todas las standard deben sondearse")
        # Reparto proporcional: ~x1,21 sobre la cadencia deseada (antes: hot 32 s y standard nunca)
        self.assertLessEqual(_pct(hot, 50), 26.0)
        self.assertLessEqual(_pct(hot, 95), 30.0)
        self.assertLessEqual(_pct(std, 50), 104.0)
        self.assertLessEqual(_pct(std, 95), 120.0)
        self.assertGreaterEqual(_pct(std, 50) / _pct(hot, 50), 3.3)   # se conserva la relación 4:1 de los tiers
        per_minute = {}
        for v in execs.values():
            for t in v:
                per_minute[int(t // 60)] = per_minute.get(int(t // 60), 0) + 1
        self.assertLessEqual(max(per_minute.values()), 60)

    def test_oversubscribed_vinted_polls_every_tier_in_daytime(self):
        from antiban import TokenBucket, QueryTask, QueryScheduler

        clock = FakeClock()
        bucket = TokenBucket(rate_per_s=0.30, burst=2, clock=clock, sleeper=clock.sleep)
        sched = QueryScheduler(self._production_like_tasks(QueryTask, 22, 8, 60.0, 240.0), bucket=bucket,
                               clock=clock, sleeper=clock.sleep, now_fn=clock.now_dt, rng=random.Random(2))
        execs = _simulate(sched, clock, 4 * 3600.0, fetch_s=0.7)
        std_keys = [k for k in execs if k[0] == "standard"]
        self.assertEqual(len(std_keys), 8)
        std = [g for k in std_keys for g in _gaps(execs[k])]
        hot = [g for k, v in execs.items() if k[0] == "hot" for g in _gaps(v)]
        self.assertLessEqual(_pct(std, 95), 420.0)
        self.assertLessEqual(_pct(hot, 95), 105.0)

    def test_relative_lateness_picks_most_overdue_task(self):
        from antiban import QueryTask, QueryScheduler

        clock = FakeClock(start=1000.0)
        hot = QueryTask(995.0, "hot", "h", 1, 2, 20.0, tier="hot")        # 5 s tarde: 25 % de su cadencia
        std = QueryTask(940.0, "std", "s", 1, 2, 80.0, tier="standard")   # 60 s tarde: 75 % de su cadencia
        sched = QueryScheduler([hot, std], bucket=None, clock=clock, sleeper=clock.sleep, now_fn=clock.now_dt,
                               stagger_initial=False)
        self.assertEqual(sched.next().radar_id, "std")
        self.assertEqual(sched.next().radar_id, "hot")

    def test_feasible_config_keeps_configured_intervals(self):
        from antiban import TokenBucket, QueryTask, QueryScheduler

        clock = FakeClock()
        bucket = TokenBucket(rate_per_s=1.0, burst=2, clock=clock, sleeper=clock.sleep)
        tasks = self._production_like_tasks(QueryTask, 5, 5, 20.0, 80.0)
        sched = QueryScheduler(tasks, bucket=bucket, clock=clock, sleeper=clock.sleep, now_fn=clock.now_dt)
        self.assertFalse(sched.plan["oversubscribed"])
        for t in sched.all_tasks:
            self.assertAlmostEqual(t.effective_interval, t.interval_s, places=6)
        self.assertIn("cabe en la cuota", sched.describe())


class TestCostAwarePlan(unittest.TestCase):
    """El plan descuenta lo que se gasta de verdad: 2.ª página (token normal extra) y fichas/perfiles."""

    def _run(self, extra_normal, prio_per_poll, seconds=3 * 3600.0):
        from antiban import TokenBucket, QueryTask, QueryScheduler

        clock = FakeClock()
        bucket = TokenBucket(rate_per_s=1.0, burst=2, clock=clock, sleeper=clock.sleep, max_debt=3)
        tasks = [QueryTask(0.0, f"hot_{i}", f"q{i}", 1, 2, 20.0, tier="hot") for i in range(21)]
        sched = QueryScheduler(tasks, bucket=bucket, clock=clock, sleeper=clock.sleep, now_fn=clock.now_dt,
                               rng=random.Random(4))
        rng = random.Random(9)
        execs = {}
        grants0 = 0
        while clock.now < seconds:
            task = sched.next()
            execs.setdefault(task.radar_id, []).append(clock.now)
            for _ in range(extra_normal.get(task.radar_id, 0)):   # 2.ª página
                bucket.acquire()
            if rng.random() < prio_per_poll:                      # ficha + perfil de un candidato
                bucket.acquire(priority=True)
            sched.done(task)
            if clock.now < 600.0:
                grants0 = bucket.grants + bucket.priority_grants
        total = bucket.grants + bucket.priority_grants - grants0
        planned = {t.radar_id: t.effective_interval for t in sched.all_tasks}
        real = {rid: _pct(_gaps(v[len(v) // 3:]), 50) for rid, v in execs.items()}
        return sched, planned, real, total / ((seconds - 600.0) / 60.0)

    def test_second_page_cost_is_learned_and_plan_matches_reality(self):
        extra = {f"hot_{i}": 1 for i in range(11)}                # 11 de 21 consultas piden 2.ª página
        sched, planned, real, tokens_min = self._run(extra, 0.0)
        costs = {t.radar_id: t.cost for t in sched.all_tasks}
        self.assertGreater(costs["hot_0"], 1.9)
        self.assertLess(costs["hot_20"], 1.1)
        for rid in ("hot_0", "hot_20"):
            self.assertLess(abs(real[rid] - planned[rid]) / planned[rid], 0.12,
                            f"{rid}: real {real[rid]:.1f}s vs plan {planned[rid]:.1f}s")
        self.assertLessEqual(tokens_min, 60.5)
        self.assertIn("cuota", sched.describe())

    def test_item_tokens_reduce_the_search_budget(self):
        sched, planned, real, tokens_min = self._run({}, 0.3)
        self.assertGreater(sched.plan["item_tokens_per_min"], 10.0)
        self.assertLess(sched.plan["budget_per_min"], 58.2 - 10.0)
        self.assertLess(abs(real["hot_5"] - planned["hot_5"]) / planned["hot_5"], 0.12)
        self.assertLessEqual(tokens_min, 60.5)

    def test_cost_survives_config_reload(self):
        from antiban import TokenBucket, QueryScheduler

        clock = FakeClock(start=10.0)
        bucket = TokenBucket(rate_per_s=1.0, burst=2, clock=clock, sleeper=clock.sleep)
        cfg = {"scheduling": {"wallapop": {"hot_interval_s": 20, "rate_per_s": 1.0}},
               "targets": [{"id": "r", "enabled": True, "tier": "hot", "platforms": ["wallapop"],
                            "wallapop_keywords": ["uno"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            os.utime(path, (1000, 1000))
            sched = QueryScheduler.from_config(cfg, platform="wallapop", bucket=bucket, config_path=path,
                                               clock=clock, sleeper=clock.sleep, now_fn=clock.now_dt)
            sched.all_tasks[0].cost = 2.0
            os.utime(path, (2000, 2000))
            self.assertTrue(sched.reload_if_changed())
            self.assertEqual(sched.all_tasks[0].cost, 2.0)


class TestAdaptiveCadence(unittest.TestCase):
    def test_balanced_multipliers_conserve_budget_and_respect_clamps(self):
        from antiban import balanced_multipliers

        freqs = [1 / 20.0] * 6 + [1 / 80.0] * 4
        weights = [9.0, 4.0, 1.0, 1.0, 0.7, 0.7, 3.0, 0.7, 0.7, 0.7]
        budget = sum(freqs)
        lo, hi = 1 / 1.6, 1 / 0.5
        m = balanced_multipliers(freqs, weights, budget, lo, hi)
        self.assertEqual(len(m), len(freqs))
        self.assertTrue(all(lo - 1e-9 <= g <= hi + 1e-9 for g in m))
        used = sum(f * g for f, g in zip(freqs, m))
        self.assertLessEqual(used, budget + 1e-9)
        self.assertGreaterEqual(used, budget * 0.995)
        self.assertGreater(m[0], m[1])
        self.assertGreater(m[1], m[2])
        self.assertEqual(balanced_multipliers(freqs, [1.0] * len(freqs), budget, lo, hi), [1.0] * len(freqs))

    def test_disabled_by_default_and_parse_clamps(self):
        from antiban import parse_adaptive_config, QueryScheduler, QueryTask

        self.assertFalse(parse_adaptive_config({}, {})["enabled"])
        cfg = parse_adaptive_config({"adaptive": {"enabled": True, "min_factor": 0.01, "max_factor": 99, "tau_s": 1}},
                                    {"adaptive": {"max_factor": 2}})
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["min_factor"], 0.1)
        self.assertEqual(cfg["max_factor"], 2.0)
        self.assertEqual(cfg["tau_s"], 300.0)

        clock = FakeClock()
        sched = QueryScheduler([QueryTask(0.0, "a", "a", 1, 2, 20.0), QueryTask(0.0, "b", "b", 1, 2, 20.0)],
                               bucket=None, clock=clock, sleeper=clock.sleep, now_fn=clock.now_dt)
        self.assertFalse(sched.plan["adaptive"])

    def test_productive_queries_get_polled_more_often_with_same_request_budget(self):
        from antiban import TokenBucket, QueryTask, QueryScheduler, parse_adaptive_config

        def run(adaptive_enabled):
            clock = FakeClock()
            bucket = TokenBucket(rate_per_s=0.30, burst=2, clock=clock, sleeper=clock.sleep)
            tasks = ([QueryTask(0.0, f"hot_{i}", f"q{i}", 1, 2, 60.0, tier="hot") for i in range(22)]
                     + [QueryTask(0.0, f"std_{j}", f"s{j}", 1, 2, 240.0, tier="standard") for j in range(8)])
            adaptive = parse_adaptive_config({"adaptive": {"enabled": adaptive_enabled, "tau_s": 1800}})
            sched = QueryScheduler(tasks, bucket=bucket, clock=clock, sleeper=clock.sleep, now_fn=clock.now_dt,
                                   rng=random.Random(5), adaptive=adaptive)
            rng = random.Random(11)
            # hot_0 y hot_1 traen candidatos a menudo (PS5, 3DS); el resto casi nunca
            yields = {"hot_0": lambda _t: 1 if rng.random() < 0.5 else 0,
                      "hot_1": lambda _t: 1 if rng.random() < 0.3 else 0}
            _simulate(sched, clock, 3 * 3600.0, yields=yields)           # aprendizaje
            execs = _simulate(sched, clock, 3 * 3600.0, yields=yields)   # medición
            total = sum(len(v) for v in execs.values())
            med = {k[1]: _pct(_gaps(v), 50) for k, v in execs.items()}
            return med, total, sched

        base_med, base_total, _ = run(False)
        ad_med, ad_total, sched = run(True)
        self.assertTrue(sched.plan["adaptive"])
        # Las productivas bajan de forma clara; las demás suben, pero nunca más del tope configurado
        self.assertLess(ad_med["hot_0"], base_med["hot_0"] * 0.75)
        self.assertLess(ad_med["hot_1"], base_med["hot_1"] * 0.85)
        self.assertGreater(ad_med["hot_0"], base_med["hot_0"] * 0.45)
        self.assertLessEqual(ad_med["hot_10"], base_med["hot_10"] * 1.75)
        self.assertLessEqual(ad_med["std_3"], base_med["std_3"] * 1.75)
        # Mismo presupuesto de peticiones (el cubo no deja pasar más)
        self.assertLessEqual(ad_total, base_total * 1.03 + 5)
        self.assertLessEqual(ad_total, int(3 * 3600 * 0.30) + 5)

    def test_yield_score_weights_alerts_over_plain_candidates(self):
        from antiban import QueryTask, QueryScheduler, parse_adaptive_config

        clock = FakeClock()
        sched = QueryScheduler([QueryTask(0.0, "a", "a", 1, 2, 30.0)], bucket=None, clock=clock,
                               sleeper=clock.sleep, now_fn=clock.now_dt,
                               adaptive=parse_adaptive_config({"adaptive": {"enabled": True, "alert_bonus": 6}}))
        self.assertEqual(sched.yield_score(0, 0), 0.0)
        self.assertEqual(sched.yield_score(3, 0), 3.0)
        self.assertEqual(sched.yield_score(1, 1), 7.0)
        self.assertEqual(sched.yield_score(-2, -1), 0.0)
        self.assertEqual(parse_adaptive_config({"adaptive": {"alert_bonus": 999}})["alert_bonus"], 50.0)
        for fname in ("sniper.py", "vinted_sniper.py"):
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            with open(os.path.join(root, fname), "r", encoding="utf-8") as fh:
                src = fh.read()
            self.assertIn("scheduler.yield_score(target_candidates, target_alerts)", src, fname)

    def test_yield_estimate_decays_when_a_query_stops_producing(self):
        from antiban import QueryTask, QueryScheduler, parse_adaptive_config

        clock = FakeClock()
        t1 = QueryTask(0.0, "a", "a", 1, 2, 30.0)
        sched = QueryScheduler([t1, QueryTask(0.0, "b", "b", 1, 2, 30.0)], bucket=None, clock=clock,
                               sleeper=clock.sleep, now_fn=clock.now_dt,
                               adaptive=parse_adaptive_config({"adaptive": {"enabled": True, "tau_s": 600}}))
        for _ in range(100):
            clock.sleep(30.0)
            sched._observe_yield(t1, 1, clock.now)
        peak = t1.yield_rate
        self.assertGreater(peak * 3600.0, 60.0)       # ~120 candidatos/h
        for _ in range(100):
            clock.sleep(30.0)
            sched._observe_yield(t1, 0, clock.now)
        self.assertLess(t1.yield_rate, peak * 0.05)

    def test_state_persists_across_restart_and_stale_state_is_ignored(self):
        from antiban import QueryTask, QueryScheduler, parse_adaptive_config

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "scheduler_state.test.json")
            wall = [1_000_000.0]
            clock = FakeClock()
            ad = parse_adaptive_config({"adaptive": {"enabled": True}})

            def build():
                return QueryScheduler(
                    [QueryTask(0.0, "ps5", "playstation 5", 1, 2, 60.0), QueryTask(0.0, "cam", "sony a6000", 1, 2, 60.0)],
                    bucket=None, clock=clock, sleeper=clock.sleep, now_fn=clock.now_dt, adaptive=ad,
                    state_path=path, wall_clock=lambda: wall[0])

            s1 = build()
            ps5 = [t for t in s1.all_tasks if t.radar_id == "ps5"][0]
            ps5.yield_rate = 5.0 / 3600.0
            self.assertTrue(s1.save_state())
            with open(path, "r", encoding="utf-8") as f:
                self.assertIn("ps5|playstation 5", json.load(f)["rates"])

            wall[0] += 3600.0
            s2 = build()
            got = {t.radar_id: t for t in s2.all_tasks}
            self.assertAlmostEqual(got["ps5"].yield_rate, 5.0 / 3600.0, places=9)
            self.assertLess(got["ps5"].effective_interval, got["cam"].effective_interval)

            wall[0] += 13 * 3600.0                   # estado de hace >12 h: no se reutiliza
            s3 = build()
            self.assertEqual({t.yield_rate for t in s3.all_tasks}, {0.0})

    def test_reload_keeps_learned_yield_and_reads_adaptive_from_config(self):
        from antiban import TokenBucket, QueryScheduler

        clock = FakeClock(start=10.0)
        bucket = TokenBucket(rate_per_s=1.0, burst=2, clock=clock, sleeper=clock.sleep)
        target = {"id": "r1", "enabled": True, "tier": "hot", "platforms": ["wallapop"], "min_price": 1,
                  "max_price": 9, "wallapop_keywords": ["uno", "dos"]}
        cfg1 = {"scheduling": {"wallapop": {"hot_interval_s": 20, "standard_interval_s": 80, "rate_per_s": 1.0}},
                "targets": [target]}
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg1, f)
            os.utime(cfg_path, (1000, 1000))
            sched = QueryScheduler.from_config(cfg1, platform="wallapop", bucket=bucket, config_path=cfg_path,
                                               clock=clock, sleeper=clock.sleep, now_fn=clock.now_dt)
            self.assertFalse(sched.plan["adaptive"])
            for t in sched.all_tasks:
                if t.query == "uno":
                    t.yield_rate = 0.01
            cfg2 = dict(cfg1)
            cfg2["scheduling"] = dict(cfg1["scheduling"], adaptive={"enabled": True})
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg2, f)
            os.utime(cfg_path, (2000, 2000))
            self.assertTrue(sched.reload_if_changed())
            self.assertTrue(sched.plan["adaptive"])
            by_q = {t.query: t for t in sched.all_tasks}
            self.assertAlmostEqual(by_q["uno"].yield_rate, 0.01)
            self.assertLess(by_q["uno"].effective_interval, by_q["dos"].effective_interval)

    def test_repo_config_enables_adaptive_within_safe_bounds(self):
        from core_engine import load_config
        from antiban import parse_adaptive_config

        sched = load_config().get("scheduling", {})
        ad = parse_adaptive_config(sched, sched.get("wallapop"))
        self.assertTrue(ad["enabled"])
        self.assertGreaterEqual(ad["min_factor"], 0.4)
        self.assertLessEqual(ad["max_factor"], 2.0)


class TestSecondPagePolicy(unittest.TestCase):
    def test_skips_after_repeated_empty_pages_and_rechecks_later(self):
        import sniper

        pol = sniper.SecondPagePolicy(empty_streak=2, skip_polls=5)
        self.assertTrue(pol.should_fetch("canon g7x"))
        pol.record("canon g7x", 0)
        self.assertTrue(pol.should_fetch("canon g7x"))
        pol.record("canon g7x", 0)
        self.assertEqual([pol.should_fetch("canon g7x") for _ in range(6)], [False] * 5 + [True])
        pol.record("canon g7x", 0)                      # sigue vacía: otro tramo sin pedirla
        self.assertFalse(pol.should_fetch("canon g7x"))
        self.assertTrue(pol.should_fetch("otra consulta"))

    def test_page_with_new_items_keeps_being_fetched(self):
        import sniper

        pol = sniper.SecondPagePolicy(empty_streak=2, skip_polls=5)
        for _ in range(10):
            self.assertTrue(pol.should_fetch("wd red"))
            pol.record("wd red", 3)

    def test_fetch_wallapop_stops_requesting_empty_second_page(self):
        import sniper
        from unittest import mock

        calls = []

        class Resp:
            def __init__(self, payload):
                self.status_code = 200
                self.headers = {}
                self._payload = payload

            def json(self):
                return self._payload

        def fake_get(url, headers, proxies, impersonate, timeout):
            calls.append("p2" if "next_page=" in url else "p1")
            if "next_page=" in url:
                return Resp({"data": {"section": {"payload": {"items": []}}}})
            return Resp({"data": {"section": {"payload": {"items": [{"id": "a1", "price": {"amount": 50}}]}}},
                         "meta": {"next_page": "tok"}})

        with mock.patch.object(sniper, "_wallapop_http_get", side_effect=fake_get), \
             mock.patch.object(sniper, "second_page_policy", sniper.SecondPagePolicy(empty_streak=2, skip_polls=10)), \
             mock.patch.object(sniper.health_monitor, "is_in_cooldown", return_value=False), \
             mock.patch.object(sniper.health_monitor, "handle_http_response", return_value=(False, 0)):
            for _ in range(8):
                items = sniper.fetch_wallapop("canon g7x", 45, 234)
                self.assertEqual([i["id"] for i in items], ["a1"])
        self.assertEqual(calls.count("p1"), 8)
        self.assertEqual(calls.count("p2"), 2, "tras 2 páginas vacías deja de gastar el token de la 2.ª página")


class TestSecondPageOnlyOlderItems(unittest.TestCase):
    """Con order_by=newest la 2.ª página solo trae anuncios más antiguos: no cuenta como útil."""

    def test_count_newer_second_page_items(self):
        import sniper

        p1 = [{"id": "a", "created_at": 3000}, {"id": "b", "created_at": 2000}]
        self.assertEqual(sniper.count_newer_second_page_items(p1, [{"id": "c", "created_at": 1000}]), 0)
        self.assertEqual(sniper.count_newer_second_page_items(p1, [{"id": "a", "created_at": 3000}]), 0)
        self.assertEqual(sniper.count_newer_second_page_items(p1, [{"id": "d", "created_at": 2500}]), 1)
        self.assertEqual(sniper.count_newer_second_page_items(p1, [{"id": "e"}]), 1)          # sin fecha: se cuenta
        self.assertEqual(sniper.count_newer_second_page_items([], [{"id": "f", "created_at": 10}]), 1)
        self.assertEqual(sniper.count_newer_second_page_items(p1, []), 0)

    def test_fetch_wallapop_stops_when_second_page_is_only_older_items(self):
        import sniper
        from unittest import mock

        calls = []

        class Resp:
            def __init__(self, payload):
                self.status_code = 200
                self.headers = {}
                self._payload = payload

            def json(self):
                return self._payload

        def fake_get(url, headers, proxies, impersonate, timeout):
            calls.append("p2" if "next_page=" in url else "p1")
            if "next_page=" in url:   # el mismo anuncio antiguo de siempre (no está en la 1.ª página)
                return Resp({"data": {"section": {"payload": {"items": [{"id": "viejo", "created_at": 1000}]}}}})
            return Resp({"data": {"section": {"payload": {"items": [{"id": "a1", "created_at": 5000,
                                                                     "price": {"amount": 50}}]}}},
                         "meta": {"next_page": "tok"}})

        with mock.patch.object(sniper, "_wallapop_http_get", side_effect=fake_get), \
             mock.patch.object(sniper, "second_page_policy", sniper.SecondPagePolicy(empty_streak=2, skip_polls=10)), \
             mock.patch.object(sniper.health_monitor, "is_in_cooldown", return_value=False), \
             mock.patch.object(sniper.health_monitor, "handle_http_response", return_value=(False, 0)):
            for _ in range(8):
                sniper.fetch_wallapop("sony a6000", 100, 338)
        self.assertEqual(calls.count("p2"), 2)


class TestListingAge(unittest.TestCase):
    def test_listing_age_seconds_handles_ms_seconds_and_garbage(self):
        import sniper

        now = 1_800_000_000.0
        self.assertAlmostEqual(sniper.listing_age_seconds({"created_at": (now - 90) * 1000}, now=now), 90.0)
        self.assertAlmostEqual(sniper.listing_age_seconds({"creation_date": now - 30}, now=now), 30.0)
        self.assertIsNone(sniper.listing_age_seconds({"created_at": "x"}, now=now))
        self.assertIsNone(sniper.listing_age_seconds({}, now=now))
        self.assertIsNone(sniper.listing_age_seconds({"created_at": (now + 500) * 1000}, now=now))
        self.assertEqual(sniper.summarize_ages([]), (0, None, None))
        self.assertEqual(sniper.summarize_ages([30, 10, 20]), (3, 10.0, 20.0))


if __name__ == "__main__":
    unittest.main()
