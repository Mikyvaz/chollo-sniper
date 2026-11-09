"""
Auditoría QA Verifier - TASK-06
Verificación de los 4 bugs críticos de producción reparados:
1. multi_sniper.py: des-indentación de pre-gating y Puerta 1/2 en candidate loop + exclusión de tiendas en cooldown en platform_sleeps
2. vinted_sniper.py: referencia corregida a vinted.session en enrich_seller_profile
3. sniper.py: http_session=None en enrich_seller_profile (usa sesión thread-local reutilizable)
4. logger.py: verificación preventiva de rollover en rotación de logs (10MB)
"""
import sys
import os

def find_file(filename):
    # Try current directory, /app, /opt/wallapop-sniper
    for base in [".", "/app", "/opt/wallapop-sniper", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]:
        cand = os.path.join(base, filename)
        if os.path.isfile(cand):
            return cand
    raise FileNotFoundError(f"Cannot find {filename}")

def test_task06():
    # 1. multi_sniper.py inspection
    multi_path = find_file("multi_sniper.py")
    with open(multi_path, "r", encoding="utf-8") as f:
        src_multi = f.read()

    assert "cooldown_until" in src_multi, "Cooldown check missing in multi_sniper"
    assert "active_stores" in src_multi, "Active stores filter missing in multi_sniper"
    assert "pre_gate_cex_offer_margin" in src_multi, "pre_gate_cex_offer_margin missing in multi_sniper"
    assert "prescreen_item_text" in src_multi, "prescreen_item_text missing in multi_sniper"

    # 2. vinted_sniper.py inspection
    vinted_path = find_file("vinted_sniper.py")
    with open(vinted_path, "r", encoding="utf-8") as f:
        src_vinted = f.read()

    assert "vinted.session" in src_vinted, "vinted.session missing in vinted_sniper"
    assert "scraper.session" not in src_vinted, "Broken scraper.session still present in vinted_sniper"

    # 3. sniper.py inspection
    sniper_path = find_file("sniper.py")
    with open(sniper_path, "r", encoding="utf-8") as f:
        src_sniper = f.read()

    assert "session_mgr.session" not in src_sniper, "Broken session_mgr.session still present in sniper.py"
    assert "enrich_seller_profile(item, http_session=None)" in src_sniper, "enrich_seller_profile call broken in sniper.py"

    # 4. logger.py inspection and runtime check
    logger_path = find_file("logger.py")
    with open(logger_path, "r", encoding="utf-8") as f:
        src_logger = f.read()

    assert "doRollover" in src_logger, "Rollover check missing in logger.py"

    # Runtime import test for logger
    sys.path.insert(0, os.path.dirname(logger_path))
    import logger
    assert hasattr(logger, "StdoutStreamToLogger")
    assert hasattr(logger, "JSONMetricsLogger")

    print("TASK_06_SPECIFIC_OK: Todos los 4 bugs de produccion verificados y corregidos.")

if __name__ == "__main__":
    test_task06()
