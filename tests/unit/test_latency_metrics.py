# -*- coding: utf-8 -*-
import tests.unit._harness  # noqa: F401 — debe ser la primera línea

import os
import json
import shutil
import sqlite3
import tempfile
import unittest
import multiprocessing as mp
from datetime import datetime

import logger
import core_engine
from tools import metrics_report


def _mp_worker(log_dir, service_tag, count):
    os.environ["LOG_DIR"] = log_dir
    os.environ["SERVICE_NAME"] = service_tag
    os.environ.pop("METRICS_FILE", None)
    filepath = logger.get_metrics_filepath(service=service_tag, log_dir=log_dir)
    m = logger.JSONMetricsLogger(filepath, max_bytes=150000, backup_count=3)
    for i in range(count):
        m.emit("evt", service=service_tag, i=i, pad="x" * 80)
    m.close()


class TestLatencyAndMetrics(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_lat_metrics_")
        self._orig_log_dir = os.environ.get("LOG_DIR")
        self._orig_svc = os.environ.get("SERVICE_NAME")
        self._orig_mf = os.environ.get("METRICS_FILE")
        self._orig_default_svc = logger._default_service

    def tearDown(self):
        logger._default_service = self._orig_default_svc
        if self._orig_log_dir is None:
            os.environ.pop("LOG_DIR", None)
        else:
            os.environ["LOG_DIR"] = self._orig_log_dir
        if self._orig_svc is None:
            os.environ.pop("SERVICE_NAME", None)
        else:
            os.environ["SERVICE_NAME"] = self._orig_svc
        if self._orig_mf is None:
            os.environ.pop("METRICS_FILE", None)
        else:
            os.environ["METRICS_FILE"] = self._orig_mf
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_service_metrics_file_naming(self):
        os.environ["LOG_DIR"] = self.tmp_dir
        os.environ.pop("METRICS_FILE", None)
        logger._default_service = ""
        os.environ["SERVICE_NAME"] = "wallapop-sniper"
        path = logger.get_metrics_filepath()
        self.assertEqual(os.path.basename(path), "metrics.wallapop-sniper.jsonl")

        os.environ.pop("SERVICE_NAME", None)
        logger.setup_logger("Vinted_Sniper", log_dir=self.tmp_dir, capture_stdout=False)
        path_svc = logger.get_metrics_filepath()
        self.assertEqual(os.path.basename(path_svc), "metrics.vinted_sniper.jsonl")

    def test_multiprocess_5x400_zero_loss(self):
        nproc, per = 5, 400
        ps = [mp.Process(target=_mp_worker, args=(self.tmp_dir, f"p{k}", per)) for k in range(nproc)]
        for p in ps:
            p.start()
        for p in ps:
            p.join()
            self.assertEqual(p.exitcode, 0)

        files = [f for f in os.listdir(self.tmp_dir) if f.startswith("metrics.")]
        self.assertTrue(len(files) >= 5, f"Se esperaban ficheros por servicio, encontrados: {files}")
        kept = 0
        for f in files:
            with open(os.path.join(self.tmp_dir, f), encoding="utf-8") as fh:
                kept += sum(1 for line in fh if line.strip())
        self.assertEqual(kept, nproc * per, f"Se perdieron líneas al rotar: {kept} != {nproc * per}")

    def test_compute_pipeline_ms_and_save_alert_context(self):
        t = {
            "fetch": 10.000,
            "filters": 10.005,
            "enrich": 10.010,
            "p1": 10.050,
            "p1_end": 10.200,
            "image": 10.210,
            "vision": 10.300,
            "vision_end": 11.100,
            "cex": 11.100,
            "cex_end": 11.250,
            "alert": 11.250,
            "alert_end": 11.400,
        }
        ms = logger.compute_pipeline_ms(t)
        self.assertAlmostEqual(ms["ms_fetch_to_p1"], 50.0, places=1)
        self.assertAlmostEqual(ms["ms_p1"], 150.0, places=1)
        self.assertAlmostEqual(ms["ms_vision"], 800.0, places=1)
        self.assertAlmostEqual(ms["ms_cex"], 150.0, places=1)
        self.assertAlmostEqual(ms["ms_alert"], 150.0, places=1)

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE deals (deal_id TEXT PRIMARY KEY, alert_context_json TEXT)")
        conn.execute("INSERT INTO deals (deal_id, alert_context_json) VALUES ('WAL_1', '')")
        core_engine.save_alert_context(
            conn, "WAL_1", seller={"name": "juan"}, ai_data={"_pipeline_ms": ms}
        )
        raw = conn.execute("SELECT alert_context_json FROM deals WHERE deal_id='WAL_1'").fetchone()[0]
        ctx = json.loads(raw)
        self.assertIn("pipeline_ms", ctx)
        self.assertEqual(ctx["pipeline_ms"]["ms_vision"], 800.0)
        conn.close()

    def test_metrics_report_reads_all_service_files(self):
        os.environ.pop("METRICS_FILE", None)
        os.environ["LOG_DIR"] = self.tmp_dir
        f1 = os.path.join(self.tmp_dir, "metrics.wallapop.jsonl")
        f2 = os.path.join(self.tmp_dir, "metrics.vinted.jsonl")
        now_iso = datetime.now().isoformat()
        with open(f1, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "timestamp": now_iso, "event": "round_completed",
                "service": "wallapop", "platform": "WALLAPOP",
                "items_new": 2, "items_evaluated": 1, "duration_s": 5.0, "interval_s": 8.0
            }) + "\n")
        with open(f2, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "timestamp": now_iso, "event": "round_completed",
                "service": "vinted", "platform": "VINTED",
                "items_new": 4, "items_evaluated": 2, "duration_s": 10.0, "interval_s": 45.0
            }) + "\n")

        paths = metrics_report.get_metrics_paths(self.tmp_dir)
        self.assertEqual(len(paths), 2)

    def test_latency_report_calculates_percentiles_and_delay(self):
        from tools import latency_report

        now_iso = datetime.now().isoformat()
        mfile = os.path.join(self.tmp_dir, "metrics.wallapop.jsonl")
        with open(mfile, "w", encoding="utf-8") as fh:
            for i, p1_ms in enumerate([100.0, 120.0, 140.0, 160.0, 300.0]):
                fh.write(json.dumps({
                    "timestamp": now_iso,
                    "event": "item_pipeline",
                    "service": "wallapop",
                    "platform": "WALLAPOP",
                    "radar_id": "switch_oled",
                    "outcome": "p1_reject" if i < 4 else "alerted",
                    "ms_fetch_to_p1": 20.0 + i,
                    "ms_p1": p1_ms,
                    "ms_vision": 0.0 if i < 4 else 850.0,
                    "ms_cex": 0.0 if i < 4 else 40.0,
                    "ms_alert": 0.0 if i < 4 else 110.0,
                }) + "\n")

        db_path = os.path.join(self.tmp_dir, "ro_test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE alert_outcomes (
                deal_id TEXT PRIMARY KEY,
                platform TEXT,
                alert_at DATETIME,
                alert_delay_min REAL
            )
        """)
        for idx, dmin in enumerate([2.0, 3.5, 4.0, 5.0, 9.0]):
            conn.execute(
                "INSERT INTO alert_outcomes (deal_id, platform, alert_at, alert_delay_min) VALUES (?, 'WALLAPOP', datetime('now'), ?)",
                (f"WAL_{idx}", dmin),
            )
        conn.commit()
        conn.close()

        summary = latency_report.run_latency_report(since="24h", log_dir=self.tmp_dir, db_path=db_path)
        self.assertIn("WALLAPOP", summary["by_platform"])
        walla = summary["by_platform"]["WALLAPOP"]
        self.assertAlmostEqual(walla["stages"]["ms_p1"]["p50"], 140.0, places=1)
        self.assertTrue(walla["stages"]["ms_p1"]["p90"] >= 160.0)
        self.assertIn("WALLAPOP", summary["alert_delay"])
        self.assertAlmostEqual(summary["alert_delay"]["WALLAPOP"]["p50"], 4.0, places=1)

    def test_scanners_instrumented_with_item_pipeline(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for fname in ("sniper.py", "vinted_sniper.py", "multi_sniper.py"):
            with open(os.path.join(root, fname), "r", encoding="utf-8") as fh:
                src = fh.read()
            self.assertIn("item_pipeline", src, f"{fname} debe emitir métrica item_pipeline")
            for stage in ("fetch", "filters", "enrich", "p1", "image", "vision", "cex", "alert"):
                self.assertIn(f'"{stage}"', src, f"{fname} debe marcar etapa '{stage}' en t")


if __name__ == "__main__":
    unittest.main()
