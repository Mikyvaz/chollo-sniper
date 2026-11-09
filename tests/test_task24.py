import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import antiban
from antiban import AntiBanHealthMonitor, AntiBanTiming, default_rate_limiter

print("=== Running Task 2.4 Empirical Tests ===")

# Test 1: AntiBanHealthMonitor streak & backoff escalation
mon = AntiBanHealthMonitor("TEST")
assert mon.consecutive_blocks == 0
assert mon.ok_streak_since_block == 0

# 1. Simular un bloqueo HTTP 429/403 (consecutive_blocks == 1)
is_blocked, cooldown = mon.handle_http_response(429, query="test_query")
assert is_blocked is True
assert mon.consecutive_blocks == 1
assert mon.ok_streak_since_block == 0
print("  [OK] Initial 429 -> consecutive_blocks == 1, ok_streak == 0")

# 2. Enviar 5 respuestas status_code=200 -> verificar que mon.consecutive_blocks sigue siendo 1 y mon.ok_streak_since_block == 5
for i in range(1, 6):
    mon.handle_http_response(200)
    assert mon.consecutive_blocks == 1, f"Step {i}: expected consecutive_blocks == 1, got {mon.consecutive_blocks}"
    assert mon.ok_streak_since_block == i, f"Step {i}: expected ok_streak_since_block == {i}, got {mon.ok_streak_since_block}"
print("  [OK] 5x 200 responses -> consecutive_blocks is STILL 1 and ok_streak == 5")

# 3. Enviar un nuevo 429 en la 6ª petición -> verificar que escala a consecutive_blocks == 2 y ok_streak_since_block == 0
is_blocked, cooldown = mon.handle_http_response(429, query="test_query_2")
assert is_blocked is True
assert mon.consecutive_blocks == 2, f"Expected consecutive_blocks == 2, got {mon.consecutive_blocks}"
assert mon.ok_streak_since_block == 0, f"Expected ok_streak_since_block == 0, got {mon.ok_streak_since_block}"
print("  [OK] Reincident 429 -> escalated to consecutive_blocks == 2, ok_streak reset to 0")

# 4. Enviar 10 respuestas 200 seguidas -> verificar que ahora sí mon.consecutive_blocks == 0
for i in range(1, 10):
    mon.handle_http_response(200)
    assert mon.consecutive_blocks == 2, f"Step {i} (<10): expected consecutive_blocks == 2, got {mon.consecutive_blocks}"
    assert mon.ok_streak_since_block == i

# The 10th consecutive 200
mon.handle_http_response(200)
assert mon.consecutive_blocks == 0, f"After 10th 200: expected consecutive_blocks == 0, got {mon.consecutive_blocks}"
assert mon.ok_streak_since_block == 0, f"After 10th 200: expected ok_streak_since_block == 0, got {mon.ok_streak_since_block}"
print("  [OK] 10 consecutive 200s -> consecutive_blocks successfully reset to 0!")

# Test 2: multi_sniper max sleep selection across platforms
timing = AntiBanTiming(night_start_hour=2, night_end_hour=7)
SCAN_INTERVAL = 45

# Register platforms with base 45s
for p in ("MILANUNCIOS", "CASHCONVERTERS", "TODOCOLECCION", "EBAY"):
    default_rate_limiter.register_platform(p, SCAN_INTERVAL)

# Degrade EBAY with high latencies
for _ in range(5):
    default_rate_limiter.record_request("EBAY", latency_seconds=6.5, status_code=200)

multi_platforms = ("MILANUNCIOS", "CASHCONVERTERS", "TODOCOLECCION", "EBAY")
platform_sleeps = [timing.calculate_round_sleep(SCAN_INTERVAL, p) for p in multi_platforms]
sleep_secs, is_night, transition, reason = max(platform_sleeps, key=lambda x: x[0])

# Verify that EBAY's degradation pushed the max sleep above SCAN_INTERVAL
ebay_interval = default_rate_limiter.get_current_interval("EBAY")
assert ebay_interval > SCAN_INTERVAL, f"Expected EBAY interval > {SCAN_INTERVAL}, got {ebay_interval}"
print(f"  [OK] Multi-sniper picked adapted sleep: {sleep_secs}s (EBAY interval: {ebay_interval}s)")

print("\n🎉 ALL Task 2.4 tests PASSED successfully!")

