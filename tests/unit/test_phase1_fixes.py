"""Correcciones de cierre de la fase 1: submodelo con prefijo, limpieza CeX simracing,
duplicado Discord tras fallo ambiguo del bot y presupuesto de IA por día local (T1.8, solo código)."""
import contextlib
import io
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import _harness  # noqa: F401  (debe ir primero)
import core_engine as ce
from test_discord_send import DiscordTestBase, FakeResp


class TestSubmodelCapPrefix(unittest.TestCase):
    def test_token_in_text_prefix(self):
        self.assertTrue(ce._token_in_text("ipad 9", "apple ipad 9th gen 64gb", prefix=True))
        self.assertTrue(ce._token_in_text("ipad 10", "ipad 10th generation", prefix=True))

    def test_default_sigue_exigiendo_limite_derecho(self):
        self.assertFalse(ce._token_in_text("ipad 9", "apple ipad 9th gen 64gb"))

    def test_tope_por_submodelo_posterior_a_la_ia_usa_prefijo(self):
        src = open(os.path.join(os.path.dirname(ce.__file__), "core_engine.py"), encoding="utf-8").read()
        i = src.rindex('for cap_rule in target.get("submodel_price_caps", [])')
        block = src[i:i + 700]
        self.assertIn("_token_in_text(mt, id_text, prefix=True)", block)
        self.assertIn("_token_in_text(ut, id_text, prefix=True)", block)


class TestCexSimracingQuery(unittest.TestCase):
    def test_radar_id_limpia_volante_pedales(self):
        seen = {}

        def fake_hits(conn, clean_q):
            seen["q"] = clean_q
            return []
        with mock.patch.object(ce, "_algolia_hits_cached", side_effect=fake_hits), \
                mock.patch.object(ce, "_mirror_candidates", return_value=[]):
            ce.get_cex_Cached_or_live(None, "Volante Thrustmaster TX pedales", radar_id="simracing_wheels")
        self.assertEqual(seen.get("q"), "Thrustmaster TX")

    def test_otro_radar_no_se_limpia(self):
        seen = {}
        with mock.patch.object(ce, "_algolia_hits_cached", side_effect=lambda c, q: seen.setdefault("q", q) and []), \
                mock.patch.object(ce, "_mirror_candidates", return_value=[]):
            ce.get_cex_Cached_or_live(None, "Volante y pedales X", radar_id="otro_radar")
        self.assertEqual(seen.get("q"), "Volante y pedales X")


class TestDiscordAmbiguousBotThenWebhook(DiscordTestBase):
    def test_bot_timeouts_y_webhook_ok_avisa_posible_duplicado(self):
        buf = io.StringIO()
        with mock.patch.object(ce, "WEBHOOK_URL", "https://discord.com/api/webhooks/1/abc"), \
                mock.patch.object(ce.HTTP_SESSION, "post",
                                  side_effect=[TimeoutError("t"), TimeoutError("t"), TimeoutError("t"),
                                               FakeResp(200, {"id": "w1"})]) as post, \
                mock.patch("logger.log_metric") as metric, contextlib.redirect_stdout(buf):
            self.assertTrue(self.send())
        self.assertEqual(post.call_count, 4)
        self.assertIn("Posible duplicado", buf.getvalue())
        self.assertIn("discord_possible_duplicate", [c.args[0] for c in metric.call_args_list])

    def test_bot_403_y_webhook_ok_no_avisa(self):
        buf = io.StringIO()
        with mock.patch.object(ce, "WEBHOOK_URL", "https://discord.com/api/webhooks/1/abc"), \
                mock.patch.object(ce.HTTP_SESSION, "post",
                                  side_effect=[FakeResp(403, {"message": "x"}), FakeResp(200, {"id": "w1"})]), \
                mock.patch("logger.log_metric") as metric, contextlib.redirect_stdout(buf):
            self.assertTrue(self.send())
        self.assertNotIn("duplicado", buf.getvalue())
        self.assertNotIn("discord_possible_duplicate", [c.args[0] for c in metric.call_args_list])


def _utc(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class TestBudgetLocalDay(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("CREATE TABLE ai_cost_log (id INTEGER PRIMARY KEY, usd REAL, created_at TIMESTAMP)")
        ce._today_cost_cache.clear()
        for name, val in (("_budget_notified_80", False), ("_budget_notified_100", False),
                          ("_budget_notified_120", False), ("_last_budget_check_day", None)):
            p = mock.patch.object(ce, name, val)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        ce._today_cost_cache.clear()
        self.conn.close()
        os.unlink(self.path)

    def _local_midnight_utc(self):
        lt = time.localtime()
        local_midnight = datetime(lt.tm_year, lt.tm_mon, lt.tm_mday)  # naive, hora local
        return datetime.fromtimestamp(time.mktime(local_midnight.timetuple()), tz=timezone.utc)

    def _insert(self, usd, when):
        self.conn.execute("INSERT INTO ai_cost_log (usd, created_at) VALUES (?, ?)", (usd, _utc(when)))
        self.conn.commit()

    def test_tz_es_madrid(self):
        self.assertEqual(os.environ.get("TZ"), "Europe/Madrid")

    def test_coste_de_las_00_30_locales_cuenta_hoy_y_el_de_las_23_30_no(self):
        mid = self._local_midnight_utc()
        self._insert(0.03, mid + timedelta(minutes=30))   # 00:30 hora local (p. ej. 23:30 UTC del día anterior)
        self._insert(0.50, mid - timedelta(minutes=30))   # 23:30 locales de ayer
        self.assertAlmostEqual(ce.get_today_ai_cost(self.conn), 0.03, places=6)

    def test_check_budget_paused_al_superar_el_tope(self):
        mid = self._local_midnight_utc()
        self._insert(0.12, mid + timedelta(minutes=30))
        with mock.patch.object(ce, "AI_DAILY_BUDGET_USD", 0.10), contextlib.redirect_stdout(io.StringIO()):
            status, ratio = ce.check_ai_budget_status(self.conn)
        self.assertEqual(status, "PAUSED")
        self.assertGreaterEqual(ratio, 1.0)

    def test_aviso_al_80_por_ciento(self):
        mid = self._local_midnight_utc()
        self._insert(0.085, mid + timedelta(minutes=30))
        hm = mock.Mock()
        with mock.patch.object(ce, "AI_DAILY_BUDGET_USD", 0.10), contextlib.redirect_stdout(io.StringIO()):
            status, _ = ce.check_ai_budget_status(self.conn, hm)
            ce.check_ai_budget_status(self.conn, hm)
        self.assertEqual(status, "NORMAL")
        self.assertEqual(hm.send_health_embed.call_count, 1)

    def test_gasto_memorizado_60_segundos(self):
        mid = self._local_midnight_utc()
        self._insert(0.01, mid + timedelta(minutes=30))
        self.assertAlmostEqual(ce.get_today_ai_cost(self.conn), 0.01, places=6)
        self._insert(0.02, mid + timedelta(minutes=31))
        self.assertAlmostEqual(ce.get_today_ai_cost(self.conn), 0.01, places=6)  # aún en caché
        with mock.patch.object(ce.time, "monotonic", return_value=time.monotonic() + 61):
            self.assertAlmostEqual(ce.get_today_ai_cost(self.conn), 0.03, places=6)

    def test_log_ai_cost_actualiza_el_gasto_memorizado(self):
        c2 = sqlite3.connect(self.path)
        c2.execute("ALTER TABLE ai_cost_log ADD COLUMN platform TEXT")
        for col in ("radar_id TEXT", "model TEXT", "call_type TEXT", "tokens_in INT", "tokens_out INT",
                    "finish_reason TEXT", "tokens_thoughts INT", "tokens_img INT", "tokens_text INT"):
            c2.execute(f"ALTER TABLE ai_cost_log ADD COLUMN {col}")
        c2.commit()
        self.assertEqual(ce.get_today_ai_cost(c2), 0.0)
        with mock.patch.object(ce, "calculate_ai_cost_usd", return_value=0.04):
            ce.log_ai_cost(c2, "WALLAPOP", "r", "m", "vision", 10, 10)
        self.assertAlmostEqual(ce.get_today_ai_cost(c2), 0.04, places=6)
        c2.close()


if __name__ == "__main__":
    unittest.main()
