"""T2.8: los avisos de salud no bloquean la captura y comparten estado protegido."""
import _harness  # noqa: F401 (antes de importar el proyecto)

from contextlib import contextmanager
import threading
import time
import unittest
from unittest import mock

import antiban
import core_engine


class HealthNotifierTests(unittest.TestCase):
    @contextmanager
    def monitor(self, sender):
        with mock.patch.object(antiban, "HEALTH_WEBHOOK_URL", "https://example.invalid/health"), \
                mock.patch.object(core_engine, "_discord_send", side_effect=sender), \
                mock.patch.object(antiban.requests, "post", side_effect=sender):
            monitor = antiban.AntiBanHealthMonitor("TEST")
            try:
                yield monitor
            finally:
                notifier = getattr(monitor, "_notifier", None)
                if notifier is not None:
                    notifier._queue.join()
                    notifier._queue.put_nowait(None)
                    notifier.join(3)
                    self.assertFalse(notifier.is_alive())

    def test_slow_post_does_not_block_send_health_embed(self):
        delivered = threading.Event()

        def slow_post(*args, **kwargs):
            time.sleep(2)
            delivered.set()
            return True, 200, "1"

        with self.monitor(slow_post) as monitor:
            started = time.perf_counter()
            monitor.send_health_embed("Incidencia", "Detalle", 0xE74C3C)
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.005, f"encolar tardó {elapsed:.6f}s")
            self.assertTrue(delivered.wait(3))
            self.assertTrue(monitor._notifier.daemon)
            self.assertEqual(monitor._notifier.name, "HealthNotifier")

    def test_five_equal_titles_send_once_and_expire_at_ten_minutes(self):
        sender = mock.Mock(return_value=(True, 200, "1"))
        now = [1000.0]
        with self.monitor(sender) as monitor:
            monitor._notifier._clock = lambda: now[0]
            for offset in (0, 10, 20, 40, 59):
                now[0] = 1000 + offset
                monitor.send_health_embed("  Incidencia  ", str(offset), 1, fields=[{"name": "n", "value": "v"}])
            monitor._notifier._queue.join()
            self.assertEqual(sender.call_count, 1)
            call = sender.call_args
            self.assertEqual(call.args, ("POST", "https://example.invalid/health"))
            self.assertEqual(call.kwargs["json_payload"]["embeds"][0]["fields"], [{"name": "n", "value": "v"}])
            now[0] = 1600
            monitor.send_health_embed("Incidencia", "Nuevo detalle", 1)
            monitor._notifier._queue.join()
            self.assertEqual(sender.call_count, 2)

    def test_no_endpoint_does_not_start_worker(self):
        with mock.patch.object(antiban, "HEALTH_WEBHOOK_URL", ""), \
                mock.patch.object(threading.Thread, "start") as start:
            monitor = antiban.AntiBanHealthMonitor("TEST")
            monitor.send_health_embed("Sin destino", "Detalle", 1)
            start.assert_not_called()

    def test_full_queue_drops_immediately_and_allows_retry(self):
        notifier = antiban.HealthNotifier("https://example.invalid/health", start_worker=False)
        self.assertEqual(notifier._queue.maxsize, 100)
        for i in range(100):
            self.assertTrue(notifier.enqueue({"title": str(i)}))
        started = time.perf_counter()
        self.assertFalse(notifier.enqueue({"title": "retry"}))
        self.assertLess(time.perf_counter() - started, 0.005)
        self.assertEqual(notifier._queue.qsize(), 100)
        notifier._queue.get_nowait()
        notifier._queue.task_done()
        self.assertTrue(notifier.enqueue({"title": "retry"}))

    def test_concurrent_duplicate_titles_enqueue_once(self):
        notifier = antiban.HealthNotifier("https://example.invalid/health", start_worker=False)
        barrier = threading.Barrier(16)

        def enqueue():
            barrier.wait()
            notifier.enqueue({"title": "Incidencia"})

        workers = [threading.Thread(target=enqueue) for _ in range(16)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(notifier._queue.qsize(), 1)

    def test_worker_survives_sender_exception(self):
        sender = mock.Mock(side_effect=[RuntimeError("fallo simulado"), (True, 200, "1")])
        with self.monitor(sender) as monitor:
            monitor.send_health_embed("Primero", "Detalle", 1)
            monitor.send_health_embed("Segundo", "Detalle", 1)
            monitor._notifier._queue.join()
            self.assertTrue(monitor._notifier.is_alive())
            self.assertEqual(sender.call_count, 2)

    def test_monitor_state_access_waits_for_lock_and_notifications_release_it(self):
        monitor = antiban.AntiBanHealthMonitor("TEST")
        self.assertTrue(hasattr(monitor, "_lock"), "el estado compartido necesita lock")
        with mock.patch.object(monitor, "send_health_embed") as send:
            send.side_effect = lambda *args, **kwargs: self.assertFalse(monitor._lock.locked())
            for action in (
                lambda: monitor.handle_http_response(429, retry_after=1200),
                monitor.is_in_cooldown,
                monitor.get_remaining_cooldown,
                monitor.reset_cooldown,
                lambda: monitor.notify_ai_error(500, "fallo"),
            ):
                entered = threading.Event()
                finished = threading.Event()
                errors = []

                def run_action():
                    entered.set()
                    try:
                        action()
                    except Exception as exc:
                        errors.append(exc)
                    finally:
                        finished.set()

                with monitor._lock:
                    worker = threading.Thread(target=run_action)
                    worker.start()
                    self.assertTrue(entered.wait(1))
                    self.assertFalse(finished.wait(0.02))
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
            monitor.handle_http_response(429)
            self.assertTrue(monitor.is_in_cooldown())
            monitor.handle_http_response(200)
            self.assertFalse(monitor.is_in_cooldown())


if __name__ == "__main__":
    unittest.main()
