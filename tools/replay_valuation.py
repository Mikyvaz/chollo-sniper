"""T3.3: comparación informativa de valoración sobre la copia aislada, sin red."""
import os
import sys
import json
from collections import Counter

assert os.environ.get("SNIPER_ISOLATED") == "1", "Ejecuta con tools/run_isolated.sh"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core_engine as ce


def main():
    cfg = ce.load_config()
    targets = {t["id"]: t for t in cfg.get("targets", [])}
    conn = ce.get_db()
    conn.row_factory = __import__("sqlite3").Row
    try:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute("SELECT * FROM deals WHERE mode = 'resale'").fetchall()
        counts = Counter(total=len(rows))
        transitions = Counter()
        for row in rows:
            data = {"is_valid_deal": True, "visual_confidence": "alta", "identified_model": row["identified_model"],
                    "estimated_resale_price": row["optimal_price"], "cex_search_query": row["cex_search_query"]}
            cex = {"cash": row["cex_cash"] or 0, "voucher": row["cex_voucher"] or 0,
                   "sell": row["cex_sell"] or 0, "box_name": row["cex_box_name"] or ""}
            target = targets.get(row["radar_id"], {"id": row["radar_id"], "mode": "resale"})
            result = ce.evaluate_deal_math_and_score(row["price"], row["landed_cost"], target, data, cex,
                                                   config=cfg, conn=conn)
            counts["optimal_changed"] += int(abs(result[2] - (row["optimal_price"] or 0)) > .01)
            counts["profit_changed"] += int(abs(result[3] - (row["net_profit"] or 0)) > .01)
            counts["now_rejected"] += int(not result[0])
            new_type = data.get("deal_type", "rejected") if result[0] else "rejected"
            if new_type != row["deal_type"]:
                transitions[f"{row['deal_type']} -> {new_type}"] += 1
        print(json.dumps({"limitation": "Reconstrucción con configuración actual: optimal_price histórico como proxy "
                           "de ai_est; el valor IA original y todas las señales visuales no se guardaron. "
                           "No atribuir todos los cambios exclusivamente al ancla.",
                          "counts": dict(counts), "type_transitions": dict(transitions)}, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
