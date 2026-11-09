import os
import sys
import json
import shutil
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core_engine
import bot

print("=== Running Task 3.3 Empirical Tests (Atomic Write & Concurrent Reads) ===")

# Backup config.json
config_path = core_engine.CONFIG_PATH
backup_path = f"{config_path}.bak_test33"
shutil.copy2(config_path, backup_path)

try:
    initial_cfg = core_engine.load_config(expand_for_offers=False)
    read_errors = []
    write_errors = []
    read_successes = [0]
    write_successes = [0]

    def writer_worker(iterations):
        for i in range(iterations):
            try:
                # Alternate slightly to create dynamic payload
                cfg_copy = json.loads(json.dumps(initial_cfg))
                cfg_copy["_test_iteration"] = i
                bot.save_config(cfg_copy)
                write_successes[0] += 1
            except Exception as e:
                write_errors.append(f"Writer error at iter {i}: {type(e).__name__}: {e}")
            time.sleep(0.002)

    def reader_worker(iterations):
        for i in range(iterations):
            try:
                loaded = core_engine.load_config()
                assert isinstance(loaded, dict)
                read_successes[0] += 1
            except Exception as e:
                read_errors.append(f"Reader error at iter {i}: {type(e).__name__}: {e}")
            time.sleep(0.002)

    # Launch concurrent threads
    t_writer1 = threading.Thread(target=writer_worker, args=(50,))
    t_writer2 = threading.Thread(target=writer_worker, args=(50,))
    t_reader1 = threading.Thread(target=reader_worker, args=(50,))
    t_reader2 = threading.Thread(target=reader_worker, args=(50,))

    threads = [t_writer1, t_writer2, t_reader1, t_reader2]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"  [OK] Writes completed: {write_successes[0]}, Write errors: {len(write_errors)}")
    print(f"  [OK] Reads completed: {read_successes[0]}, Read errors: {len(read_errors)}")

    if write_errors:
        print(f"  [ERROR] Write error details: {write_errors[:5]}")
    if read_errors:
        print(f"  [ERROR] Read error details: {read_errors[:5]}")

    assert len(write_errors) == 0, f"Expected 0 write errors, got {len(write_errors)}"
    assert len(read_errors) == 0, f"Expected 0 read errors (0 JSONDecodeError), got {len(read_errors)}"
    assert write_successes[0] == 100, f"Expected 100 successful writes, got {write_successes[0]}"
    assert read_successes[0] == 100, f"Expected 100 successful reads, got {read_successes[0]}"

    # Verify final file integrity
    final_cfg = core_engine.load_config()
    assert isinstance(final_cfg, dict)
    print("  [OK] Final config integrity verified: valid JSON structure")

finally:
    # Restore original config.json
    if os.path.exists(backup_path):
        shutil.copy2(backup_path, config_path)
        os.remove(backup_path)
    print("  [OK] Backup restored cleanly.")

print("\n🎉 ALL Task 3.3 tests PASSED successfully!")

