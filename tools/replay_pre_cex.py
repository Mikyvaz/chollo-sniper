"""T3.2: replay de solo lectura sobre la copia aislada; sin API ni llamadas de IA."""
import os
import sys
import json
from collections import defaultdict

assert os.environ.get("SNIPER_ISOLATED") == "1", "Ejecuta con tools/run_isolated.sh"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core_engine as ce


def main():
    targets = {t["id"]: t for t in ce.load_config().get("targets", [])}
    conn = ce.get_db()
    try:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute("SELECT radar_id, title, description, cex_box_name FROM deals "
                            "WHERE COALESCE(cex_box_name,'') != ''").fetchall()
        stats = defaultdict(lambda: {"total": 0, "covered": 0, "agree": 0})
        for radar, title, desc, expected in rows:
            ref = ce.estimate_cex_from_title(conn, title, desc, targets.get(radar, {"id": radar}))
            covered = ref["confidence"] in ("media", "alta")
            family = ce.cex_product_family(expected)
            agree = bool(covered and family and ref.get("family") == family)
            for key in ("ALL", radar):
                stats[key]["total"] += 1
                stats[key]["covered"] += int(covered)
                stats[key]["agree"] += int(agree)
        for s in stats.values():
            s["agreement_pct_all"] = round(100 * s["agree"] / s["total"], 2) if s["total"] else 0
            s["agreement_pct_covered"] = round(100 * s["agree"] / s["covered"], 2) if s["covered"] else 0
        print(json.dumps({"denominator": "todos los deals con cex_box_name, incluidos sin estimación",
                          "fresh_mirror_hours": 36, "stats": dict(stats)}, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
