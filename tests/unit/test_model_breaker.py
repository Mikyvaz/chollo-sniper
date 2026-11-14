"""T2.7: fallos por modelo, plazo compartido y fallback sin contaminar cachés."""
import _harness  # noqa: F401: seguridad antes de importar el proyecto

import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import core_engine as ce


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def response(payload=None, status=200, headers=None):
    return mock.Mock(status_code=status, headers=headers or {},
                     json=mock.Mock(return_value=payload or {}), text=json.dumps(payload or {}))


def good_gemini():
    return response({"candidates": [{"finishReason": "STOP", "content": {
        "parts": [{"text": '{"decision":"proceed"}'}]}}]})


def good_openrouter():
    return response({"choices": [{"message": {"content": '{"decision":"proceed"}'}}]})


class TestModelBreaker(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.conn = sqlite3.connect(":memory:")
        ce.init_db(self.conn)
        self.addCleanup(self.conn.close)
        for name, value in (("GEMINI_API_KEY", "test"), ("OPENROUTER_API_KEY", "test")):
            p = mock.patch.object(ce, name, value)
            p.start()
            self.addCleanup(p.stop)
        if hasattr(ce, "ModelBreaker"):
            p = mock.patch.object(ce, "_MODEL_BREAKER", ce.ModelBreaker())
            p.start()
            self.addCleanup(p.stop)
        self.serial = 0

    def call(self, **kw):
        self.serial += 1
        args = dict(prompt=f"T2.7 item {self.serial}", conn=self.conn,
                    candidate_models=["gemini-test-lite"], openrouter_models=["openrouter/test"],
                    call_type="prescreen", enforce_budget=False)
        args.update(kw)
        return ce.ai_gateway_call(**args)

    def test_fourth_item_skips_timed_out_gemini_keeps_fallback(self):
        gemini_calls = []

        def post(url, **kw):
            if "googleapis" in url:
                gemini_calls.append(url)
                raise ce.std_requests.Timeout("simulated timeout")
            return good_openrouter()

        with mock.patch.object(ce._AI_HTTP_SESSION, "post", side_effect=post), redirect_stdout(io.StringIO()):
            for _ in range(3):
                self.assertEqual(self.call(), {"decision": "proceed"})
            started = time.monotonic()
            self.assertEqual(self.call(), {"decision": "proceed"})
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(len(gemini_calls), 3)

    def test_breaker_expires_and_success_resets_consecutive_failures(self):
        breaker = ce.ModelBreaker()
        with mock.patch.object(ce.time, "monotonic", self.clock):
            for _ in range(2):
                breaker.record_failure("a")
            breaker.record_success("a")
            breaker.record_failure("a")
            breaker.record_failure("a")
            self.assertTrue(breaker.allow("a"))
            breaker.record_failure("a")
            self.assertFalse(breaker.allow("a"))
            self.assertTrue(breaker.allow("b"))
            self.clock.now += 299
            self.assertFalse(breaker.allow("a"))
            self.clock.now += 1
            self.assertTrue(breaker.allow("a"))
            breaker.record_success("a")
            breaker.record_failure("a")
            self.assertTrue(breaker.allow("a"))

    def test_breaker_concurrent_failure_updates(self):
        breaker = ce.ModelBreaker()
        gate = threading.Barrier(4)

        def fail():
            gate.wait(timeout=2)
            breaker.record_failure("a")

        workers = [threading.Thread(target=fail) for _ in range(3)]
        for worker in workers:
            worker.start()
        gate.wait(timeout=2)
        for worker in workers:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        self.assertFalse(breaker.allow("a"))

    def test_retry_after_on_429_controls_reopening(self):
        with mock.patch.object(ce.time, "monotonic", self.clock), \
                mock.patch.object(ce._AI_HTTP_SESSION, "post", return_value=response(status=429, headers={"Retry-After": "900"})) as post, \
                redirect_stdout(io.StringIO()):
            for _ in range(4):
                self.call(openrouter_models=[])
            self.assertEqual(post.call_count, 3)
            self.clock.now += 899
            self.call(openrouter_models=[])
            self.assertEqual(post.call_count, 3)
            self.clock.now += 1
            self.call(openrouter_models=[])
            self.assertEqual(post.call_count, 4)

    def test_5xx_and_max_tokens_open_individual_models(self):
        for result in (response(status=503), response({"candidates": [{"finishReason": "MAX_TOKENS"}]})):
            with self.subTest(result=result):
                if hasattr(ce, "ModelBreaker"):
                    ce._MODEL_BREAKER = ce.ModelBreaker()
                with mock.patch.object(ce._AI_HTTP_SESSION, "post", return_value=result) as post, redirect_stdout(io.StringIO()):
                    for _ in range(4):
                        self.call(openrouter_models=[])
                    self.assertEqual(post.call_count, 3)

    def test_404_does_not_open_model(self):
        with mock.patch.object(ce._AI_HTTP_SESSION, "post", return_value=response(status=404)) as post:
            for _ in range(4):
                self.call(openrouter_models=[])
            self.assertEqual(post.call_count, 4)

    def test_deadline_spans_gemini_and_openrouter_attempts(self):
        observed = []

        def slow(url, **kw):
            observed.append(kw["timeout"])
            self.clock.now += kw["timeout"]
            raise ce.std_requests.Timeout("slow")

        with mock.patch.object(ce.time, "monotonic", self.clock), \
                mock.patch.object(ce._AI_HTTP_SESSION, "post", side_effect=slow), redirect_stdout(io.StringIO()):
            self.assertIsNone(self.call(timeout=6, deadline_s=10,
                                        openrouter_models=["fallback-1", "fallback-2"]))
        self.assertEqual(observed, [6, 4])
        self.assertLessEqual(self.clock.now - 1000, 10.5)

    def test_no_attempt_started_with_less_than_three_seconds(self):
        def slow(url, **kw):
            self.clock.now += 7.1
            return response(status=503)

        with mock.patch.object(ce.time, "monotonic", self.clock), \
                mock.patch.object(ce._AI_HTTP_SESSION, "post", side_effect=slow) as post, redirect_stdout(io.StringIO()):
            self.assertIsNone(self.call(timeout=8, deadline_s=10))
            self.assertEqual(post.call_count, 1)

    def test_semaphore_wait_consumes_deadline_and_is_released(self):
        def acquire(**kw):
            self.assertLessEqual(kw["timeout"], 10)
            self.clock.now += 8
            return True

        sem = mock.Mock(acquire=mock.Mock(side_effect=acquire))
        with mock.patch.object(ce.time, "monotonic", self.clock), \
                mock.patch.object(ce, "_AI_GATEWAY_SEMAPHORE", sem), \
                mock.patch.object(ce._AI_HTTP_SESSION, "post") as post:
            self.assertIsNone(self.call(timeout=12, deadline_s=10))
        post.assert_not_called()
        sem.release.assert_called_once_with()

    def test_saturated_semaphore_honors_short_deadline(self):
        with mock.patch.object(ce, "_AI_GATEWAY_SEMAPHORE", threading.Semaphore(0)), \
                mock.patch.object(ce._AI_HTTP_SESSION, "post") as post, redirect_stdout(io.StringIO()):
            started = time.monotonic()
            self.assertIsNone(self.call(timeout=12, deadline_s=0.05))
            self.assertLess(time.monotonic() - started, 0.55)
        post.assert_not_called()

    def test_openrouter_200_error_is_explicit_redacted_and_counted(self):
        def post(url, json, **kw):
            if json["model"] == "bad":
                return response({"error": {"message": "quota key=SECRET_TOKEN"}})
            return good_openrouter()

        buf = io.StringIO()
        with mock.patch.object(ce, "GEMINI_API_KEY", ""), \
                mock.patch.object(ce._AI_HTTP_SESSION, "post", side_effect=post) as request, redirect_stdout(buf):
            for _ in range(4):
                self.assertEqual(self.call(openrouter_models=["bad", "good"]), {"decision": "proceed"})
        self.assertEqual([c.kwargs["json"]["model"] for c in request.call_args_list].count("bad"), 3)
        self.assertIn("OpenRouter error", buf.getvalue())
        self.assertIn("quota", buf.getvalue())
        self.assertNotIn("SECRET_TOKEN", buf.getvalue())
        self.assertNotIn("'choices'", buf.getvalue())

    def test_prescreen_fail_open_does_not_cache_emergency(self):
        with mock.patch.object(ce._AI_HTTP_SESSION, "post", side_effect=ce.std_requests.Timeout("down")), \
                redirect_stdout(io.StringIO()):
            result = ce.prescreen_item_text("WALLAPOP", "Nintendo Switch", "funciona", 80, 80,
                                           {"id": "switch", "name": "Switch"}, conn=self.conn)
        self.assertEqual(result, ("proceed", "ai_unavailable_fail_open", "baja", False))
        for table in ("ai_prompt_cache", "ai_prescreen_cache"):
            self.assertEqual(self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_successful_prompt_cache_still_works_with_open_breaker(self):
        with mock.patch.object(ce._AI_HTTP_SESSION, "post", return_value=good_gemini()) as post:
            self.assertEqual(self.call(prompt="cached"), {"decision": "proceed"})
            for _ in range(3):
                ce._MODEL_BREAKER.record_failure("gemini-test-lite")
            self.assertEqual(self.call(prompt="cached"), {"decision": "proceed"})
        post.assert_called_once()

    def test_prescreen_passes_ten_second_deadline_and_six_second_attempt(self):
        with mock.patch.object(ce, "ai_gateway_call", return_value=None) as call:
            ce.prescreen_item_text("WALLAPOP", "Nintendo Switch", "", 80, 80, {}, conn=self.conn)
        self.assertEqual(call.call_args.kwargs["timeout"], 6)
        self.assertEqual(call.call_args.kwargs["deadline_s"], 10)

    def test_vision_passes_twenty_second_deadline_and_twelve_second_attempt(self):
        with mock.patch.object(ce, "ai_gateway_call", return_value=None) as call, redirect_stdout(io.StringIO()):
            ce.analyze_and_generate_kit("WALLAPOP", "Switch OLED completa", "buen estado", 90, 90,
                                       False, {"id": "switch", "name": "Switch", "mode": "resale"},
                                       conn=self.conn)
        self.assertEqual(call.call_args.kwargs["timeout"], 12)
        self.assertEqual(call.call_args.kwargs["deadline_s"], 20)

    def test_budget_is_rechecked_after_waiting_for_semaphore(self):
        sem = mock.Mock(acquire=mock.Mock(return_value=True))
        with mock.patch.object(ce, "_AI_GATEWAY_SEMAPHORE", sem), \
                mock.patch.object(ce, "get_today_ai_cost", side_effect=[0, ce.AI_DAILY_BUDGET_USD]), \
                mock.patch.object(ce._AI_HTTP_SESSION, "post") as post:
            with self.assertRaises(ce.BudgetExceededError):
                self.call(enforce_budget=True)
        post.assert_not_called()
        sem.release.assert_called_once_with()

    def test_sqlite_writer_lock_respects_deadline_and_restores_timeout(self):
        # Cada hilo crea su conexión: bloqueo WAL real, sin compartir conexiones.
        with tempfile.TemporaryDirectory(dir="/work") as directory:
            path = directory + "/gateway.db"
            conn = sqlite3.connect(path)
            self.conn.backup(conn)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            ready = threading.Event()
            release = threading.Event()

            def hold_writer():
                blocker = sqlite3.connect(path)
                try:
                    blocker.execute("BEGIN IMMEDIATE")
                    ready.set()
                    release.wait(timeout=5)
                    blocker.rollback()
                finally:
                    blocker.close()

            worker = threading.Thread(target=hold_writer)
            worker.start()
            self.assertTrue(ready.wait(timeout=2))
            output = io.StringIO()
            truncated = response({"candidates": [{"finishReason": "MAX_TOKENS"}],
                                  "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 10}})
            try:
                with mock.patch.object(ce._AI_HTTP_SESSION, "post", return_value=truncated) as post, \
                        redirect_stdout(output):
                    started = time.monotonic()
                    result = self.call(conn=conn, timeout=6, deadline_s=3.2)
                    elapsed = time.monotonic() - started
                self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
                self.assertLessEqual(elapsed, 3.7, f"SQLite excedió el plazo: {elapsed:.3f}s")
                self.assertIsNone(result)
                post.assert_called_once()  # no fallback nuevo tras consumir el plazo en SQLite
                self.assertIn("Error registrando coste IA", output.getvalue())
                self.assertEqual(conn.execute("SELECT count(*) FROM ai_cost_log").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT count(*) FROM ai_prompt_cache").fetchone()[0], 0)
            finally:
                release.set()
                worker.join(timeout=2)
                conn.close()

    def test_sqlite_timeout_restored_after_success_cache_and_budget_exception(self):
        self.conn.execute("PRAGMA busy_timeout=30000")
        with mock.patch.object(ce._AI_HTTP_SESSION, "post", return_value=good_gemini()):
            self.call(prompt="restoration success")
        self.assertEqual(self.conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
        self.call(prompt="restoration success")  # retorno temprano por caché
        self.assertEqual(self.conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
        with mock.patch.object(ce, "get_today_ai_cost", return_value=ce.AI_DAILY_BUDGET_USD), \
                redirect_stdout(io.StringIO()), self.assertRaises(ce.BudgetExceededError):
            self.call(enforce_budget=True)
        self.assertEqual(self.conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)

    def test_real_transport_stops_a_dripping_body_within_deadline(self):
        class DrippingResponse(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                try:
                    for _ in range(100):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.1)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), DrippingResponse)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        real_post = ce._AI_HTTP_SESSION.post

        def local_post(url, **kw):
            return real_post(f"http://127.0.0.1:{server.server_port}/", **kw)

        try:
            with mock.patch.object(ce._AI_HTTP_SESSION, "post", side_effect=local_post) as post, \
                    redirect_stdout(io.StringIO()):
                started = time.monotonic()
                self.assertIsNone(self.call(timeout=3.1, deadline_s=3.3))
                elapsed = time.monotonic() - started
            self.assertEqual(post.call_count, 1)
            self.assertGreaterEqual(elapsed, 3)
            self.assertLessEqual(elapsed, 3.8)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
